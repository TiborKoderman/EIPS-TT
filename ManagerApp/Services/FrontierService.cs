using System.Collections.Concurrent;
using System.Globalization;
using ManagerApp.Models;
using Npgsql;

namespace ManagerApp.Services;

public sealed class FrontierService
{
    private readonly IConfiguration _configuration;
    private readonly ILogger<FrontierService> _logger;
    private readonly string? _connectionString;
    private readonly ConcurrentDictionary<string, DateTime> _siteNextAllowedUtc = new(StringComparer.OrdinalIgnoreCase);
    private readonly int _leaseTtlSeconds;
    private readonly int _politenessDelayMilliseconds;

    public FrontierService(IConfiguration configuration, ILogger<FrontierService> logger)
    {
        _configuration = configuration;
        _logger = logger;
        _connectionString = _configuration.GetConnectionString("CrawldbConnection");
        _leaseTtlSeconds = Math.Clamp(_configuration.GetValue("CrawlerApi:FrontierLeaseTtlSeconds", 30), 5, 3600);
        _politenessDelayMilliseconds = Math.Clamp(_configuration.GetValue("CrawlerApi:FrontierPolitenessDelayMilliseconds", 500), 0, 60000);
    }

    public async Task<bool> EnqueueAsync(string? url, int priority, int depth, string? sourceUrl, CancellationToken cancellationToken)
    {
        var normalized = NormalizeUrl(url);
        if (string.IsNullOrWhiteSpace(normalized))
        {
            return false;
        }

        var normalizedSource = NormalizeUrl(sourceUrl);

        await using var connection = await OpenConnectionAsync(cancellationToken);
        if (connection is null)
        {
            return false;
        }

        const string sql = """
            INSERT INTO crawldb.frontier_queue (
                url,
                priority,
                source_url,
                depth,
                state,
                discovered_at,
                memory_cached,
                locked_at,
                locked_by_worker_id,
                finished_at
            )
            VALUES (
                @url,
                @priority,
                @source_url,
                @depth,
                'QUEUED'::crawldb.frontier_queue_state,
                NOW(),
                false,
                NULL,
                NULL,
                NULL
            )
            ON CONFLICT (url)
            DO UPDATE
               SET priority = GREATEST(crawldb.frontier_queue.priority, EXCLUDED.priority),
                   source_url = COALESCE(crawldb.frontier_queue.source_url, EXCLUDED.source_url),
                   depth = LEAST(crawldb.frontier_queue.depth, EXCLUDED.depth),
                   discovered_at = LEAST(crawldb.frontier_queue.discovered_at, EXCLUDED.discovered_at),
                   state = CASE
                       WHEN crawldb.frontier_queue.state IN (
                           'COMPLETED'::crawldb.frontier_queue_state,
                           'FAILED'::crawldb.frontier_queue_state,
                           'DUPLICATE'::crawldb.frontier_queue_state
                       ) THEN 'QUEUED'::crawldb.frontier_queue_state
                       ELSE crawldb.frontier_queue.state
                   END,
                   finished_at = CASE
                       WHEN crawldb.frontier_queue.state IN (
                           'COMPLETED'::crawldb.frontier_queue_state,
                           'FAILED'::crawldb.frontier_queue_state,
                           'DUPLICATE'::crawldb.frontier_queue_state
                       ) THEN NULL
                       ELSE crawldb.frontier_queue.finished_at
                   END,
                   locked_at = CASE
                       WHEN crawldb.frontier_queue.state IN (
                           'COMPLETED'::crawldb.frontier_queue_state,
                           'FAILED'::crawldb.frontier_queue_state,
                           'DUPLICATE'::crawldb.frontier_queue_state
                       ) THEN NULL
                       ELSE crawldb.frontier_queue.locked_at
                   END,
                   locked_by_worker_id = CASE
                       WHEN crawldb.frontier_queue.state IN (
                           'COMPLETED'::crawldb.frontier_queue_state,
                           'FAILED'::crawldb.frontier_queue_state,
                           'DUPLICATE'::crawldb.frontier_queue_state
                       ) THEN NULL
                       ELSE crawldb.frontier_queue.locked_by_worker_id
                   END;
            """;

        await using var cmd = new NpgsqlCommand(sql, connection);
        cmd.Parameters.AddWithValue("url", normalized);
        cmd.Parameters.AddWithValue("priority", priority);
        cmd.Parameters.AddWithValue("source_url", (object?)normalizedSource ?? DBNull.Value);
        cmd.Parameters.AddWithValue("depth", Math.Max(0, depth));
        await cmd.ExecuteNonQueryAsync(cancellationToken);
        return true;
    }

    public async Task<FrontierClaimViewModel> ClaimAsync(int workerId, CancellationToken cancellationToken)
    {
        if (workerId <= 0)
        {
            return new FrontierClaimViewModel { Claimed = false, WorkerId = workerId };
        }

        await using var connection = await OpenConnectionAsync(cancellationToken);
        if (connection is null)
        {
            return new FrontierClaimViewModel { Claimed = false, WorkerId = workerId };
        }

        await RequeueExpiredLeasesAsync(connection, cancellationToken);

        await using var tx = await connection.BeginTransactionAsync(cancellationToken);
        try
        {
            const string selectSql = """
                SELECT id, url, priority, source_url, depth
                FROM crawldb.frontier_queue
                WHERE state = 'QUEUED'::crawldb.frontier_queue_state
                ORDER BY priority DESC, discovered_at ASC
                LIMIT 64
                FOR UPDATE SKIP LOCKED;
                """;

            var candidates = new List<(long Id, string Url, int Priority, string? SourceUrl, int Depth)>();
            await using (var selectCmd = new NpgsqlCommand(selectSql, connection, tx))
            await using (var reader = await selectCmd.ExecuteReaderAsync(cancellationToken))
            {
                while (await reader.ReadAsync(cancellationToken))
                {
                    candidates.Add((
                        reader.GetInt64(0),
                        reader.GetString(1),
                        reader.GetInt32(2),
                        reader.IsDBNull(3) ? null : reader.GetString(3),
                        reader.GetInt32(4)));
                }
            }

            var now = DateTime.UtcNow;
            var candidate = candidates.FirstOrDefault(item => IsSiteReady(item.Url, now));
            if (candidate == default)
            {
                await tx.CommitAsync(cancellationToken);
                return new FrontierClaimViewModel
                {
                    Claimed = false,
                    WorkerId = workerId,
                };
            }

            const string lockSql = """
                UPDATE crawldb.frontier_queue
                SET state = 'LOCKED'::crawldb.frontier_queue_state,
                    locked_at = NOW(),
                    locked_by_worker_id = @worker_id,
                    dequeued_at = NOW()
                WHERE id = @id
                  AND state = 'QUEUED'::crawldb.frontier_queue_state;
                """;

            await using var lockCmd = new NpgsqlCommand(lockSql, connection, tx);
            lockCmd.Parameters.AddWithValue("worker_id", workerId);
            lockCmd.Parameters.AddWithValue("id", candidate.Id);
            var updated = await lockCmd.ExecuteNonQueryAsync(cancellationToken);
            if (updated <= 0)
            {
                await tx.RollbackAsync(cancellationToken);
                return new FrontierClaimViewModel { Claimed = false, WorkerId = workerId };
            }

            await tx.CommitAsync(cancellationToken);
            ReserveSite(candidate.Url, now);

            return new FrontierClaimViewModel
            {
                Claimed = true,
                WorkerId = workerId,
                Url = candidate.Url,
                LeaseToken = candidate.Id.ToString(CultureInfo.InvariantCulture),
                LeaseTtlSeconds = _leaseTtlSeconds,
                Source = "server",
                SourceUrl = candidate.SourceUrl,
            };
        }
        catch
        {
            await tx.RollbackAsync(cancellationToken);
            throw;
        }
    }

    public async Task<bool> CompleteAsync(
        int workerId,
        string? url,
        string? leaseToken,
        string? status,
        CancellationToken cancellationToken)
    {
        var normalizedUrl = NormalizeUrl(url);
        if (workerId <= 0 || string.IsNullOrWhiteSpace(normalizedUrl))
        {
            return false;
        }

        await using var connection = await OpenConnectionAsync(cancellationToken);
        if (connection is null)
        {
            return false;
        }

        await RequeueExpiredLeasesAsync(connection, cancellationToken);

        var normalizedState = NormalizeTerminalState(status);
        const string updateByIdSql = """
            UPDATE crawldb.frontier_queue
            SET state = @state::crawldb.frontier_queue_state,
                finished_at = CASE
                    WHEN @state::crawldb.frontier_queue_state = 'QUEUED'::crawldb.frontier_queue_state THEN NULL
                    ELSE NOW()
                END,
                locked_at = NULL,
                locked_by_worker_id = NULL,
                dequeued_at = CASE
                    WHEN @state::crawldb.frontier_queue_state = 'QUEUED'::crawldb.frontier_queue_state THEN NULL
                    ELSE dequeued_at
                END
            WHERE id = @id
              AND url = @url
              AND locked_by_worker_id = @worker_id
              AND state IN (
                  'LOCKED'::crawldb.frontier_queue_state,
                  'PROCESSING'::crawldb.frontier_queue_state
              );
            """;

        if (!long.TryParse(leaseToken, NumberStyles.Integer, CultureInfo.InvariantCulture, out var leaseId))
        {
            return false;
        }

        await using var updateByIdCmd = new NpgsqlCommand(updateByIdSql, connection);
        updateByIdCmd.Parameters.AddWithValue("state", normalizedState);
        updateByIdCmd.Parameters.AddWithValue("id", leaseId);
        updateByIdCmd.Parameters.AddWithValue("url", normalizedUrl);
        updateByIdCmd.Parameters.AddWithValue("worker_id", workerId);
        var updated = await updateByIdCmd.ExecuteNonQueryAsync(cancellationToken);

        if (updated > 0)
        {
            ReserveSite(normalizedUrl, DateTime.UtcNow);
            return true;
        }

        return false;
    }

    public async Task<bool> PruneAsync(int workerId, string? url, CancellationToken cancellationToken)
    {
        var normalizedUrl = NormalizeUrl(url);
        if (workerId <= 0 || string.IsNullOrWhiteSpace(normalizedUrl))
        {
            return false;
        }

        await using var connection = await OpenConnectionAsync(cancellationToken);
        if (connection is null)
        {
            return false;
        }

        const string sql = """
            UPDATE crawldb.frontier_queue
            SET state = 'FAILED'::crawldb.frontier_queue_state,
                finished_at = NOW(),
                locked_at = NULL,
                locked_by_worker_id = NULL
            WHERE url = @url
              AND (
                  state = 'QUEUED'::crawldb.frontier_queue_state
                  OR (state = 'LOCKED'::crawldb.frontier_queue_state AND locked_by_worker_id = @worker_id)
              );
            """;

        await using var cmd = new NpgsqlCommand(sql, connection);
        cmd.Parameters.AddWithValue("url", normalizedUrl);
        cmd.Parameters.AddWithValue("worker_id", workerId);
        var updated = await cmd.ExecuteNonQueryAsync(cancellationToken);
        return updated > 0;
    }

    public async Task<FrontierStatusViewModel> GetStatusAsync(CancellationToken cancellationToken)
    {
        var status = new FrontierStatusViewModel
        {
            LeaseTtlSeconds = _leaseTtlSeconds,
            RelayEnabled = true,
        };

        await using var connection = await OpenConnectionAsync(cancellationToken);
        if (connection is null)
        {
            return status;
        }

        await RequeueExpiredLeasesAsync(connection, cancellationToken);

        const string sql = """
            SELECT state::text, COUNT(*)
            FROM crawldb.frontier_queue
            GROUP BY state;
            """;

        var counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        await using (var cmd = new NpgsqlCommand(sql, connection))
        await using (var reader = await cmd.ExecuteReaderAsync(cancellationToken))
        {
            while (await reader.ReadAsync(cancellationToken))
            {
                counts[reader.GetString(0)] = reader.GetInt32(1);
            }
        }

        var queued = counts.GetValueOrDefault("QUEUED");
        var locked = counts.GetValueOrDefault("LOCKED");
        var processing = counts.GetValueOrDefault("PROCESSING");
        var completed = counts.GetValueOrDefault("COMPLETED");
        var duplicate = counts.GetValueOrDefault("DUPLICATE");
        var failed = counts.GetValueOrDefault("FAILED");

        status.InMemoryQueued = queued;
        status.KnownUrls = queued + locked + processing + completed + duplicate + failed;
        status.LocalQueued = 0;
        status.ActiveLeases = locked + processing;
        status.Tombstones = completed + duplicate + failed;

        return status;
    }

    public async Task<FrontierDequeueBatchViewModel> DequeueAsync(
        IReadOnlyList<int>? workerIds,
        int limit,
        string? daemonId,
        CancellationToken cancellationToken)
    {
        var boundedLimit = Math.Clamp(limit, 1, 100);
        var ids = (workerIds ?? Array.Empty<int>())
            .Where(id => id > 0)
            .Distinct()
            .Take(boundedLimit)
            .ToList();

        var batch = new FrontierDequeueBatchViewModel
        {
            DaemonId = string.IsNullOrWhiteSpace(daemonId) ? "local-default" : daemonId.Trim(),
            RequestedWorkerIds = ids,
            Items = new List<FrontierDequeueItemViewModel>(),
        };

        foreach (var workerId in ids)
        {
            if (batch.Items.Count >= boundedLimit)
            {
                break;
            }

            var claim = await ClaimAsync(workerId, cancellationToken);
            if (!claim.Claimed || string.IsNullOrWhiteSpace(claim.Url))
            {
                continue;
            }

            batch.Items.Add(new FrontierDequeueItemViewModel
            {
                WorkerId = workerId,
                Url = claim.Url,
                LeaseToken = claim.LeaseToken,
                LeaseTtlSeconds = claim.LeaseTtlSeconds,
                Source = claim.Source,
                SourceUrl = claim.SourceUrl,
            });
        }

        var status = await GetStatusAsync(cancellationToken);
        batch.RemainingInMemory = status.InMemoryQueued;
        batch.ActiveLeases = status.ActiveLeases;
        return batch;
    }

    private async Task<NpgsqlConnection?> OpenConnectionAsync(CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(_connectionString))
        {
            return null;
        }

        try
        {
            var connection = new NpgsqlConnection(_connectionString);
            await connection.OpenAsync(cancellationToken);
            return connection;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Frontier service failed to open DB connection.");
            return null;
        }
    }

    private async Task RequeueExpiredLeasesAsync(NpgsqlConnection connection, CancellationToken cancellationToken)
    {
        const string sql = """
            UPDATE crawldb.frontier_queue
            SET state = 'QUEUED'::crawldb.frontier_queue_state,
                locked_at = NULL,
                locked_by_worker_id = NULL
            WHERE state = 'LOCKED'::crawldb.frontier_queue_state
              AND locked_at IS NOT NULL
              AND locked_at < NOW() - make_interval(secs => @ttl_seconds);
            """;

        await using var cmd = new NpgsqlCommand(sql, connection);
        cmd.Parameters.AddWithValue("ttl_seconds", _leaseTtlSeconds);
        await cmd.ExecuteNonQueryAsync(cancellationToken);
    }

    private bool IsSiteReady(string url, DateTime nowUtc)
    {
        var siteKey = ExtractSiteKey(url);
        if (string.IsNullOrWhiteSpace(siteKey))
        {
            return true;
        }

        if (!_siteNextAllowedUtc.TryGetValue(siteKey, out var readyAtUtc))
        {
            return true;
        }

        return nowUtc >= readyAtUtc;
    }

    private void ReserveSite(string url, DateTime nowUtc)
    {
        if (_politenessDelayMilliseconds <= 0)
        {
            return;
        }

        var siteKey = ExtractSiteKey(url);
        if (string.IsNullOrWhiteSpace(siteKey))
        {
            return;
        }

        _siteNextAllowedUtc[siteKey] = nowUtc.AddMilliseconds(_politenessDelayMilliseconds);
    }

    private static string NormalizeTerminalState(string? status)
    {
        var normalized = (status ?? string.Empty).Trim().ToLowerInvariant();
        return normalized switch
        {
            "queued" or "requeue" or "retry" => "QUEUED",
            "completed" or "done" or "success" => "COMPLETED",
            "duplicate" => "DUPLICATE",
            _ => "FAILED",
        };
    }

    private static string? NormalizeUrl(string? url)
    {
        if (string.IsNullOrWhiteSpace(url))
        {
            return null;
        }

        var trimmed = url.Trim();
        if (!Uri.TryCreate(trimmed, UriKind.Absolute, out var parsed))
        {
            return trimmed;
        }

        var builder = new UriBuilder(parsed)
        {
            Fragment = string.Empty,
        };
        return builder.Uri.AbsoluteUri;
    }

    private static string ExtractSiteKey(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var parsed))
        {
            return string.Empty;
        }

        return parsed.Host.ToLowerInvariant();
    }
}

public sealed class FrontierSeedRequest
{
    public string? Url { get; set; }
    public int? Priority { get; set; }
    public int? Depth { get; set; }
    public string? SourceUrl { get; set; }
}

public sealed class FrontierClaimRequest
{
    public int WorkerId { get; set; }
}

public sealed class FrontierCompleteRequest
{
    public int WorkerId { get; set; }
    public string? Url { get; set; }
    public string? LeaseToken { get; set; }
    public string? Status { get; set; }
}

public sealed class FrontierPruneRequest
{
    public int WorkerId { get; set; }
    public string? Url { get; set; }
}

public sealed class FrontierDequeueRequest
{
    public List<int>? WorkerIds { get; set; }
    public int Limit { get; set; } = 20;
    public string? DaemonId { get; set; }
}
