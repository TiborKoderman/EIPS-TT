using System.Collections.Concurrent;
using System.Globalization;
using System.Net;
using ManagerApp.Models;
using Npgsql;

namespace ManagerApp.Services;

public sealed class FrontierService
{
    private const string EnqueueSql = """
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

    private readonly IConfiguration _configuration;
    private readonly ILogger<FrontierService> _logger;
    private readonly string? _connectionString;
    private readonly ConcurrentDictionary<string, DateTime> _crawlerSiteNextAllowedUtc = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, (string SiteIpKey, DateTime ExpiresAtUtc)> _resolvedSiteIdentityCache = new(StringComparer.OrdinalIgnoreCase);
    private readonly int _leaseTtlSeconds;
    private readonly int _politenessDelayMilliseconds;
    private readonly TimeSpan _resolvedSiteIdentityTtl = TimeSpan.FromMinutes(10);

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
        var normalizedUrl = normalized.Trim();

        var normalizedSource = NormalizeUrl(sourceUrl);

        await using var connection = await OpenConnectionAsync(cancellationToken);
        if (connection is null)
        {
            return false;
        }
        return await EnqueueCoreAsync(connection, normalizedUrl, priority, depth, normalizedSource, cancellationToken);
    }

    public async Task<int> EnqueueBatchAsync(
        IReadOnlyCollection<FrontierEnqueueCandidate>? candidates,
        CancellationToken cancellationToken)
    {
        if (candidates is null || candidates.Count == 0)
        {
            return 0;
        }

        var mergedByUrl = new Dictionary<string, FrontierEnqueueCandidate>(StringComparer.Ordinal);
        foreach (var candidate in candidates)
        {
            var normalized = NormalizeUrl(candidate.Url);
            if (string.IsNullOrWhiteSpace(normalized))
            {
                continue;
            }

            var normalizedSource = NormalizeUrl(candidate.SourceUrl);
            var normalizedDepth = Math.Max(0, candidate.Depth);

            if (mergedByUrl.TryGetValue(normalized, out var existing))
            {
                mergedByUrl[normalized] = new FrontierEnqueueCandidate
                {
                    Url = normalized,
                    Priority = Math.Max(existing.Priority, candidate.Priority),
                    Depth = Math.Min(existing.Depth, normalizedDepth),
                    SourceUrl = existing.SourceUrl ?? normalizedSource,
                };
            }
            else
            {
                mergedByUrl[normalized] = new FrontierEnqueueCandidate
                {
                    Url = normalized,
                    Priority = candidate.Priority,
                    Depth = normalizedDepth,
                    SourceUrl = normalizedSource,
                };
            }
        }

        if (mergedByUrl.Count == 0)
        {
            return 0;
        }

        await using var connection = await OpenConnectionAsync(cancellationToken);
        if (connection is null)
        {
            return 0;
        }

        var inserted = 0;
        foreach (var candidate in mergedByUrl.Values)
        {
            if (string.IsNullOrWhiteSpace(candidate.Url))
            {
                continue;
            }

            var queued = await EnqueueCoreAsync(
                connection,
                candidate.Url,
                candidate.Priority,
                candidate.Depth,
                candidate.SourceUrl,
                cancellationToken);
            if (queued)
            {
                inserted += 1;
            }
        }

        return inserted;
    }

    private static async Task<bool> EnqueueCoreAsync(
        NpgsqlConnection connection,
        string? normalizedUrl,
        int priority,
        int depth,
        string? normalizedSource,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(normalizedUrl))
        {
            return false;
        }

        await using var cmd = new NpgsqlCommand(EnqueueSql, connection);
        cmd.Parameters.AddWithValue("url", normalizedUrl);
        cmd.Parameters.AddWithValue("priority", priority);
        cmd.Parameters.AddWithValue("source_url", (object?)normalizedSource ?? DBNull.Value);
        cmd.Parameters.AddWithValue("depth", Math.Max(0, depth));
        await cmd.ExecuteNonQueryAsync(cancellationToken);
        return true;
    }

    public async Task<FrontierClaimViewModel> ClaimAsync(int workerId, string? daemonId, CancellationToken cancellationToken)
    {
        if (workerId <= 0)
        {
            return new FrontierClaimViewModel { Claimed = false, WorkerId = workerId };
        }

        var crawlerKey = NormalizeCrawlerKey(daemonId);

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
            (long Id, string Url, int Priority, string? SourceUrl, int Depth)? selected = null;
            foreach (var item in candidates)
            {
                if (await IsSiteReadyAsync(crawlerKey, item.Url, now, cancellationToken))
                {
                    selected = item;
                    break;
                }
            }

            if (selected is null)
            {
                await tx.CommitAsync(cancellationToken);
                return new FrontierClaimViewModel
                {
                    Claimed = false,
                    WorkerId = workerId,
                };
            }

            var candidate = selected.Value;

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
            await ReserveSiteAsync(crawlerKey, candidate.Url, now, cancellationToken);

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
        string? daemonId,
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
            await ReserveSiteAsync(NormalizeCrawlerKey(daemonId), normalizedUrl, DateTime.UtcNow, cancellationToken);
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

            var claim = await ClaimAsync(workerId, batch.DaemonId, cancellationToken);
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

    private async Task<bool> IsSiteReadyAsync(
        string crawlerKey,
        string url,
        DateTime nowUtc,
        CancellationToken cancellationToken)
    {
        var siteIpKey = await ResolveSiteIpKeyAsync(url, nowUtc, cancellationToken);
        if (string.IsNullOrWhiteSpace(siteIpKey))
        {
            return true;
        }

        var scopedKey = ComposeCrawlerSiteKey(crawlerKey, siteIpKey);
        if (!_crawlerSiteNextAllowedUtc.TryGetValue(scopedKey, out var readyAtUtc))
        {
            return true;
        }

        if (nowUtc >= readyAtUtc)
        {
            _crawlerSiteNextAllowedUtc.TryRemove(scopedKey, out _);
            return true;
        }

        return nowUtc >= readyAtUtc;
    }

    private async Task ReserveSiteAsync(
        string crawlerKey,
        string url,
        DateTime nowUtc,
        CancellationToken cancellationToken)
    {
        if (_politenessDelayMilliseconds <= 0)
        {
            return;
        }

        var siteIpKey = await ResolveSiteIpKeyAsync(url, nowUtc, cancellationToken);
        if (string.IsNullOrWhiteSpace(siteIpKey))
        {
            return;
        }

        var scopedKey = ComposeCrawlerSiteKey(crawlerKey, siteIpKey);
        _crawlerSiteNextAllowedUtc[scopedKey] = nowUtc.AddMilliseconds(_politenessDelayMilliseconds);
        PruneExpiredPolitenessEntries(nowUtc);
    }

    private void PruneExpiredPolitenessEntries(DateTime nowUtc)
    {
        foreach (var entry in _crawlerSiteNextAllowedUtc)
        {
            if (entry.Value <= nowUtc)
            {
                _crawlerSiteNextAllowedUtc.TryRemove(entry.Key, out _);
            }
        }

        foreach (var entry in _resolvedSiteIdentityCache)
        {
            if (entry.Value.ExpiresAtUtc <= nowUtc)
            {
                _resolvedSiteIdentityCache.TryRemove(entry.Key, out _);
            }
        }
    }

    private async Task<string> ResolveSiteIpKeyAsync(string url, DateTime nowUtc, CancellationToken cancellationToken)
    {
        if (cancellationToken.IsCancellationRequested)
        {
            return string.Empty;
        }

        var host = ExtractHost(url);
        if (string.IsNullOrWhiteSpace(host))
        {
            return string.Empty;
        }

        if (_resolvedSiteIdentityCache.TryGetValue(host, out var cached) && cached.ExpiresAtUtc > nowUtc)
        {
            return cached.SiteIpKey;
        }

        if (IPAddress.TryParse(host, out var parsedIpAddress))
        {
            var literalIp = NormalizeIpAddress(parsedIpAddress);
            _resolvedSiteIdentityCache[host] = (literalIp, nowUtc.Add(_resolvedSiteIdentityTtl));
            return literalIp;
        }

        string resolvedSiteKey;
        try
        {
            var addresses = await Dns.GetHostAddressesAsync(host);
            var resolvedAddress = addresses.FirstOrDefault(address => address.AddressFamily == System.Net.Sockets.AddressFamily.InterNetwork)
                ?? addresses.FirstOrDefault();

            resolvedSiteKey = resolvedAddress is null
                ? host.ToLowerInvariant()
                : NormalizeIpAddress(resolvedAddress);
        }
        catch (Exception)
        {
            resolvedSiteKey = host.ToLowerInvariant();
        }

        _resolvedSiteIdentityCache[host] = (resolvedSiteKey, nowUtc.Add(_resolvedSiteIdentityTtl));
        return resolvedSiteKey;
    }

    private static string NormalizeCrawlerKey(string? daemonId)
    {
        if (string.IsNullOrWhiteSpace(daemonId))
        {
            return "local-default";
        }

        return daemonId.Trim().ToLowerInvariant();
    }

    private static string ComposeCrawlerSiteKey(string crawlerKey, string siteIpKey)
    {
        return $"{crawlerKey}|{siteIpKey}";
    }

    private static string ExtractHost(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var parsed))
        {
            return string.Empty;
        }

        return parsed.Host.Trim().ToLowerInvariant();
    }

    private static string NormalizeIpAddress(IPAddress address)
    {
        if (address.IsIPv4MappedToIPv6)
        {
            return address.MapToIPv4().ToString();
        }

        return address.ToString();
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

}

public sealed class FrontierSeedRequest
{
    public string? Url { get; set; }
    public int? Priority { get; set; }
    public int? Depth { get; set; }
    public string? SourceUrl { get; set; }
}

public sealed class FrontierEnqueueCandidate
{
    public string Url { get; set; } = string.Empty;
    public int Priority { get; set; }
    public int Depth { get; set; }
    public string? SourceUrl { get; set; }
}

public sealed class FrontierClaimRequest
{
    public int WorkerId { get; set; }
    public string? DaemonId { get; set; }
}

public sealed class FrontierCompleteRequest
{
    public int WorkerId { get; set; }
    public string? Url { get; set; }
    public string? LeaseToken { get; set; }
    public string? Status { get; set; }
    public string? DaemonId { get; set; }
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
