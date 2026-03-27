using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using ManagerApp.Data;
using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace ManagerApp.Services;

public sealed class CrawlerRelayService
{
    private readonly IDbContextFactory<CrawldbContext> _contextFactory;
    private readonly IConfiguration _configuration;
    private readonly ILogger<CrawlerRelayService> _logger;
    private readonly object _eventLock = new();
    private readonly LinkedList<CrawlerEventEnvelope> _recentEvents = new();
    private readonly string? _connectionString;
    private readonly int _workerLogRetentionDays;
    private readonly int _workerMetricRetentionDays;
    private readonly TimeSpan _cleanupInterval;
    private DateTime _lastCleanupUtc = DateTime.MinValue;
    private const int MaxRecentEvents = 5000;

    public CrawlerRelayService(
        IDbContextFactory<CrawldbContext> contextFactory,
        IConfiguration configuration,
        ILogger<CrawlerRelayService> logger)
    {
        _contextFactory = contextFactory;
        _configuration = configuration;
        _logger = logger;
        _connectionString = _configuration.GetConnectionString("CrawldbConnection");
        _workerLogRetentionDays = Math.Clamp(_configuration.GetValue("CrawlerApi:WorkerLogRetentionDays", 14), 1, 365);
        _workerMetricRetentionDays = Math.Clamp(_configuration.GetValue("CrawlerApi:WorkerMetricRetentionDays", 30), 1, 365);
        _cleanupInterval = TimeSpan.FromMinutes(Math.Clamp(_configuration.GetValue("CrawlerApi:ObservabilityCleanupMinutes", 30), 5, 24 * 60));
    }

    public async Task<CrawlerIngestResponse> IngestAsync(CrawlerIngestRequest request, CancellationToken cancellationToken)
    {
        var accessedTime = DateTime.SpecifyKind(DateTime.UtcNow, DateTimeKind.Unspecified);
        var rawUrl = NormalizeUrl(request.RawUrl);
        var finalUrl = NormalizeUrl(request.DownloadResult?.FinalUrl);
        var url = string.IsNullOrWhiteSpace(finalUrl) ? rawUrl : finalUrl;
        if (string.IsNullOrWhiteSpace(url))
        {
            throw new InvalidOperationException("rawUrl/finalUrl must be provided.");
        }

        var pageTypeCode = string.IsNullOrWhiteSpace(request.DownloadResult?.PageTypeCode)
            ? "HTML"
            : request.DownloadResult!.PageTypeCode!.Trim().ToUpperInvariant();
        var html = request.DownloadResult?.HtmlContent;
        var contentHash = string.IsNullOrWhiteSpace(html) ? null : Sha256Hex(html);
        var parsedPayloadBytes = SerializeParsedPayload(request.DownloadResult?.ParsedPayload);

        await using var context = await _contextFactory.CreateDbContextAsync(cancellationToken);

        var existingPage = await context.Pages
            .FirstOrDefaultAsync(page => page.Url == url, cancellationToken);

        var siteId = await ResolveSiteIdAsync(context, url, request.SiteId, cancellationToken);
        if (siteId.HasValue)
        {
            await UpdateSitePolicyAsync(context, siteId.Value, request.DownloadResult, cancellationToken);
        }
        var status = "inserted";
        Page targetPage;

        if (existingPage != null && !string.Equals(existingPage.PageTypeCode, "FRONTIER", StringComparison.OrdinalIgnoreCase))
        {
            existingPage.SiteId ??= siteId;
            existingPage.PageTypeCode = pageTypeCode;
            existingPage.AccessedTime = accessedTime;
            existingPage.HttpStatusCode = request.DownloadResult?.StatusCode;
            existingPage.HtmlContent = pageTypeCode == "HTML" ? html : null;
            existingPage.ContentHash = pageTypeCode == "HTML" ? contentHash : null;
            existingPage.DuplicateOfPageId = null;
            targetPage = existingPage;
            status = "updated";
        }
        else
        {
            var duplicateOfPageId = pageTypeCode == "HTML" && !string.IsNullOrWhiteSpace(contentHash)
                ? await context.Pages
                    .Where(page =>
                        page.Url != url &&
                        page.PageTypeCode == "HTML" &&
                        page.ContentHash == contentHash)
                    .OrderBy(page => page.Id)
                    .Select(page => (int?)page.Id)
                    .FirstOrDefaultAsync(cancellationToken)
                : null;

            if (existingPage != null)
            {
                var existingPageType = existingPage.PageTypeCode;
                existingPage.SiteId ??= siteId;
                existingPage.AccessedTime = accessedTime;
                existingPage.HttpStatusCode = request.DownloadResult?.StatusCode;
                existingPage.ContentHash = pageTypeCode == "HTML" ? contentHash : null;
                existingPage.DuplicateOfPageId = duplicateOfPageId;

                if (pageTypeCode == "HTML" && duplicateOfPageId.HasValue)
                {
                    existingPage.PageTypeCode = "DUPLICATE";
                    existingPage.HtmlContent = null;
                    status = "promoted_duplicate";
                }
                else
                {
                    existingPage.PageTypeCode = pageTypeCode;
                    existingPage.HtmlContent = pageTypeCode == "HTML" ? html : null;
                    status = string.Equals(existingPageType, "FRONTIER", StringComparison.OrdinalIgnoreCase)
                        ? "promoted"
                        : "updated";
                }

                targetPage = existingPage;
            }
            else
            {
                targetPage = new Page
                {
                    SiteId = siteId,
                    PageTypeCode = pageTypeCode == "HTML" && duplicateOfPageId.HasValue ? "DUPLICATE" : pageTypeCode,
                    Url = url,
                    HtmlContent = pageTypeCode == "HTML" && !duplicateOfPageId.HasValue ? html : null,
                    HttpStatusCode = request.DownloadResult?.StatusCode,
                    AccessedTime = accessedTime,
                    ContentHash = pageTypeCode == "HTML" ? contentHash : null,
                    DuplicateOfPageId = duplicateOfPageId,
                };
                context.Pages.Add(targetPage);
                status = pageTypeCode == "HTML" && duplicateOfPageId.HasValue
                    ? "duplicate_content"
                    : "inserted";
            }
        }

        await context.SaveChangesAsync(cancellationToken);

        var canonicalTargetPageId = targetPage.DuplicateOfPageId ?? targetPage.Id;

        if (request.SourcePageId.HasValue && request.SourcePageId.Value > 0)
        {
            await context.Database.ExecuteSqlInterpolatedAsync(
            $"INSERT INTO crawldb.link(from_page, to_page) VALUES ({request.SourcePageId.Value}, {canonicalTargetPageId}) ON CONFLICT DO NOTHING",
                cancellationToken);
        }

        if (!string.IsNullOrWhiteSpace(request.DownloadResult?.DataTypeCode))
        {
            var existingPageData = await context.PageData
                .FirstOrDefaultAsync(pd => pd.PageId == targetPage.Id && pd.DataTypeCode == request.DownloadResult!.DataTypeCode, cancellationToken);
            if (existingPageData is null)
            {
                context.PageData.Add(new PageDatum
                {
                    PageId = targetPage.Id,
                    DataTypeCode = request.DownloadResult!.DataTypeCode,
                    Data = parsedPayloadBytes,
                });
                await context.SaveChangesAsync(cancellationToken);
            }
            else if (parsedPayloadBytes is not null)
            {
                existingPageData.Data = parsedPayloadBytes;
                await context.SaveChangesAsync(cancellationToken);
            }
        }

        await UpsertDiscoveredLinksAsync(
            context,
            targetPage.DuplicateOfPageId.HasValue
                ? await context.Pages.FirstAsync(page => page.Id == targetPage.DuplicateOfPageId.Value, cancellationToken)
                : targetPage,
            request.DiscoveredUrls,
            cancellationToken);

        return new CrawlerIngestResponse
        {
            PageId = targetPage.Id,
            Status = status,
            Url = url,
            DuplicateOfPageId = targetPage.DuplicateOfPageId,
            ContentHash = targetPage.ContentHash,
        };
    }

    public async Task IngestEventAsync(CrawlerEventMessage message)
    {
        var envelope = new CrawlerEventEnvelope
        {
            TimestampUtc = DateTime.UtcNow,
            Type = string.IsNullOrWhiteSpace(message.Type) ? "info" : message.Type,
            DaemonId = string.IsNullOrWhiteSpace(message.DaemonId) ? "local-default" : message.DaemonId,
            WorkerId = message.WorkerId,
            PayloadJson = SerializePayload(message.Payload),
        };

        lock (_eventLock)
        {
            _recentEvents.AddFirst(envelope);
            while (_recentEvents.Count > MaxRecentEvents)
            {
                _recentEvents.RemoveLast();
            }
        }

        _logger.LogInformation(
            "[crawler-event] type={Type} daemon={DaemonId} worker={WorkerId} payload={Payload}",
            envelope.Type,
            envelope.DaemonId,
            envelope.WorkerId,
            envelope.PayloadJson);

        await PersistObservabilityEventAsync(envelope);
    }

    public IReadOnlyList<CrawlerEventEnvelope> GetRecentEvents(int limit = 80)
    {
        var capped = Math.Clamp(limit, 1, MaxRecentEvents);
        lock (_eventLock)
        {
            return _recentEvents.Take(capped).ToList();
        }
    }

    private static string SerializePayload(object? payload)
    {
        if (payload is null)
        {
            return "{}";
        }

        try
        {
            return JsonSerializer.Serialize(payload);
        }
        catch
        {
            return payload.ToString() ?? "{}";
        }
    }

    private static byte[]? SerializeParsedPayload(JsonElement? parsedPayload)
    {
        if (parsedPayload is null)
        {
            return null;
        }

        var payload = parsedPayload.Value;
        if (payload.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null)
        {
            return null;
        }

        try
        {
            return Encoding.UTF8.GetBytes(payload.GetRawText());
        }
        catch
        {
            return null;
        }
    }

    private async Task PersistObservabilityEventAsync(CrawlerEventEnvelope envelope)
    {
        if (string.IsNullOrWhiteSpace(_connectionString))
        {
            return;
        }

        try
        {
            await using var connection = new NpgsqlConnection(_connectionString);
            await connection.OpenAsync();

            await PersistWorkerStateIfApplicableAsync(connection, envelope);
            await PersistLogEntryIfApplicableAsync(connection, envelope);
            await PersistMetricEntriesIfApplicableAsync(connection, envelope);
            await RunRetentionCleanupIfNeededAsync(connection);
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Failed to persist crawler observability event.");
        }
    }

    private static async Task PersistWorkerStateIfApplicableAsync(NpgsqlConnection connection, CrawlerEventEnvelope envelope)
    {
        if (!envelope.WorkerId.HasValue)
        {
            return;
        }

        var daemonDbId = await ResolveDaemonDbIdAsync(connection, envelope.DaemonId);
        if (!daemonDbId.HasValue)
        {
            return;
        }

        var workerId = envelope.WorkerId.Value;
        var workerName = NormalizeWorkerName(workerId, null);
        string? status = null;
        string? currentUrl = null;
        int? pagesProcessed = null;
        int? errorCount = null;
        string metadataJson = envelope.PayloadJson;

        var isStatusEvent = string.Equals(envelope.Type, "status-change", StringComparison.OrdinalIgnoreCase);
        var isSpawnEvent = string.Equals(envelope.Type, "worker-spawned", StringComparison.OrdinalIgnoreCase);

        try
        {
            using var payloadDoc = JsonDocument.Parse(envelope.PayloadJson);
            if (payloadDoc.RootElement.ValueKind == JsonValueKind.Object)
            {
                if (payloadDoc.RootElement.TryGetProperty("name", out var nameNode)
                    && nameNode.ValueKind == JsonValueKind.String)
                {
                    workerName = NormalizeWorkerName(workerId, nameNode.GetString());
                }

                if (isStatusEvent
                    && payloadDoc.RootElement.TryGetProperty("status", out var statusNode)
                    && statusNode.ValueKind == JsonValueKind.String)
                {
                    status = NormalizeWorkerStatus(statusNode.GetString());
                }

                if (payloadDoc.RootElement.TryGetProperty("currentUrl", out var currentUrlNode)
                    && currentUrlNode.ValueKind == JsonValueKind.String)
                {
                    currentUrl = currentUrlNode.GetString();
                }

                if (payloadDoc.RootElement.TryGetProperty("pagesProcessed", out var pagesNode)
                    && pagesNode.ValueKind == JsonValueKind.Number
                    && pagesNode.TryGetInt32(out var pagesValue))
                {
                    pagesProcessed = pagesValue;
                }

                if (payloadDoc.RootElement.TryGetProperty("pages_processed_total", out var pagesTotalNode)
                    && pagesTotalNode.ValueKind == JsonValueKind.Number
                    && pagesTotalNode.TryGetInt32(out var pagesTotalValue))
                {
                    pagesProcessed ??= pagesTotalValue;
                }

                if (payloadDoc.RootElement.TryGetProperty("errorCount", out var errorsNode)
                    && errorsNode.ValueKind == JsonValueKind.Number
                    && errorsNode.TryGetInt32(out var errorsValue))
                {
                    errorCount = errorsValue;
                }

                if (payloadDoc.RootElement.TryGetProperty("errors_total", out var errorsTotalNode)
                    && errorsTotalNode.ValueKind == JsonValueKind.Number
                    && errorsTotalNode.TryGetInt32(out var errorsTotalValue))
                {
                    errorCount ??= errorsTotalValue;
                }
            }
        }
        catch
        {
            // Keep defaults when payload is not JSON object.
        }

        if (isSpawnEvent)
        {
            status = "idle";
        }

        const string sql = """
            INSERT INTO manager.worker(
                daemon_id,
                external_worker_id,
                name,
                status,
                current_url,
                pages_processed,
                error_count,
                last_heartbeat_at,
                metadata,
                updated_at
            )
            VALUES (
                @daemon_id,
                @external_worker_id,
                @name,
                COALESCE(@status, 'idle'),
                @current_url,
                COALESCE(@pages_processed, 0),
                COALESCE(@error_count, 0),
                NOW(),
                @metadata::jsonb,
                NOW()
            )
            ON CONFLICT (daemon_id, external_worker_id)
            DO UPDATE
               SET name = COALESCE(NULLIF(EXCLUDED.name, ''), manager.worker.name),
                   status = COALESCE(NULLIF(@status, ''), manager.worker.status),
                   current_url = COALESCE(EXCLUDED.current_url, manager.worker.current_url),
                   pages_processed = COALESCE(@pages_processed, manager.worker.pages_processed),
                   error_count = COALESCE(@error_count, manager.worker.error_count),
                   last_heartbeat_at = NOW(),
                   metadata = EXCLUDED.metadata,
                   updated_at = NOW();
            """;

        await using var cmd = new NpgsqlCommand(sql, connection);
        cmd.Parameters.AddWithValue("daemon_id", daemonDbId.Value);
        cmd.Parameters.AddWithValue("external_worker_id", workerId);
        cmd.Parameters.AddWithValue("name", workerName);
        cmd.Parameters.AddWithValue("status", (object?)status ?? DBNull.Value);
        cmd.Parameters.AddWithValue("current_url", (object?)currentUrl ?? DBNull.Value);
        cmd.Parameters.AddWithValue("pages_processed", (object?)pagesProcessed ?? DBNull.Value);
        cmd.Parameters.AddWithValue("error_count", (object?)errorCount ?? DBNull.Value);
        cmd.Parameters.AddWithValue("metadata", metadataJson);
        await cmd.ExecuteNonQueryAsync();
    }

    private static string? NormalizeWorkerStatus(string? raw)
    {
        var normalized = (raw ?? string.Empty).Trim().ToLowerInvariant();
        return normalized switch
        {
            "active" => "active",
            "idle" => "idle",
            "paused" => "paused",
            "stopped" => "stopped",
            "error" => "error",
            _ => null,
        };
    }

    private static string NormalizeWorkerName(int workerId, string? raw)
    {
        var canonical = $"Worker-{workerId}";
        if (string.IsNullOrWhiteSpace(raw))
        {
            return canonical;
        }

        return raw.Trim().Equals(canonical, StringComparison.OrdinalIgnoreCase)
            ? canonical
            : canonical;
    }

    private static async Task<int?> ResolveDaemonDbIdAsync(NpgsqlConnection connection, string daemonIdentifier)
    {
        const string sql = """
            SELECT id
            FROM manager.daemon
            WHERE COALESCE(metadata->>'daemonId', '') = @daemon_identifier
               OR lower(name) = lower(@daemon_name)
            ORDER BY CASE WHEN COALESCE(metadata->>'daemonId', '') = @daemon_identifier THEN 0 ELSE 1 END
            LIMIT 1;
            """;

        await using var cmd = new NpgsqlCommand(sql, connection);
        cmd.Parameters.AddWithValue("daemon_identifier", daemonIdentifier ?? string.Empty);
        cmd.Parameters.AddWithValue("daemon_name", daemonIdentifier == "local-default" ? "Local Daemon" : daemonIdentifier ?? string.Empty);
        var scalar = await cmd.ExecuteScalarAsync();
        if (scalar is null || scalar == DBNull.Value)
        {
            return null;
        }

        return Convert.ToInt32(scalar);
    }

    private static async Task PersistLogEntryIfApplicableAsync(NpgsqlConnection connection, CrawlerEventEnvelope envelope)
    {
        var eventType = envelope.Type?.Trim() ?? string.Empty;
        var shouldPersist = string.Equals(eventType, "worker-log", StringComparison.OrdinalIgnoreCase)
            || string.Equals(eventType, "error", StringComparison.OrdinalIgnoreCase)
            || string.Equals(eventType, "status-change", StringComparison.OrdinalIgnoreCase)
            || string.Equals(eventType, "worker-spawned", StringComparison.OrdinalIgnoreCase)
            || string.Equals(eventType, "queue-change", StringComparison.OrdinalIgnoreCase)
            || string.Equals(eventType, "frontier-lease", StringComparison.OrdinalIgnoreCase)
            || string.Equals(eventType, "frontier-release", StringComparison.OrdinalIgnoreCase)
            || string.Equals(eventType, "frontier-complete", StringComparison.OrdinalIgnoreCase)
            || string.Equals(eventType, "frontier-prune", StringComparison.OrdinalIgnoreCase)
            || string.Equals(eventType, "frontier-lease-expired", StringComparison.OrdinalIgnoreCase)
            || string.Equals(eventType, "page-reported", StringComparison.OrdinalIgnoreCase);

        if (!shouldPersist)
        {
            return;
        }

        var level = "Info";
        var message = $"[{eventType}] {envelope.PayloadJson}";

        try
        {
            using var payloadDoc = JsonDocument.Parse(envelope.PayloadJson);
            if (payloadDoc.RootElement.ValueKind == JsonValueKind.Object)
            {
                if (payloadDoc.RootElement.TryGetProperty("level", out var levelNode)
                    && levelNode.ValueKind == JsonValueKind.String)
                {
                    level = levelNode.GetString() ?? "Info";
                }

                if (payloadDoc.RootElement.TryGetProperty("message", out var messageNode)
                    && messageNode.ValueKind == JsonValueKind.String)
                {
                    message = messageNode.GetString() ?? message;
                }

                if (!string.Equals(eventType, "worker-log", StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(eventType, "error", StringComparison.OrdinalIgnoreCase))
                {
                    if (payloadDoc.RootElement.TryGetProperty("status", out var statusNode)
                        && statusNode.ValueKind == JsonValueKind.String)
                    {
                        var status = statusNode.GetString() ?? "unknown";
                        var reason = payloadDoc.RootElement.TryGetProperty("reason", out var reasonNode)
                            && reasonNode.ValueKind == JsonValueKind.String
                            ? reasonNode.GetString()
                            : null;
                        message = string.IsNullOrWhiteSpace(reason)
                            ? $"[{eventType}] status={status}"
                            : $"[{eventType}] status={status} reason={reason}";
                    }
                    else if (payloadDoc.RootElement.TryGetProperty("action", out var actionNode)
                        && actionNode.ValueKind == JsonValueKind.String)
                    {
                        message = $"[{eventType}] action={actionNode.GetString()}";
                    }
                    else if (payloadDoc.RootElement.TryGetProperty("url", out var urlNode)
                        && urlNode.ValueKind == JsonValueKind.String)
                    {
                        message = $"[{eventType}] url={urlNode.GetString()}";
                    }
                    else if (payloadDoc.RootElement.TryGetProperty("name", out var nameNode)
                        && nameNode.ValueKind == JsonValueKind.String)
                    {
                        message = $"[{eventType}] name={nameNode.GetString()}";
                    }

                    if (payloadDoc.RootElement.TryGetProperty("queueOrder", out var orderNode)
                        && orderNode.ValueKind == JsonValueKind.Number
                        && orderNode.TryGetInt64(out var queueOrder))
                    {
                        message = $"{message} order={queueOrder}";
                    }
                }
            }
        }
        catch
        {
            // Keep defaults from payload JSON.
        }

        if (string.Equals(eventType, "error", StringComparison.OrdinalIgnoreCase)
            || message.Contains("failed", StringComparison.OrdinalIgnoreCase)
            || message.Contains("error", StringComparison.OrdinalIgnoreCase))
        {
            level = "Error";
        }
        else if (string.Equals(eventType, "frontier-prune", StringComparison.OrdinalIgnoreCase))
        {
            level = "Warning";
        }

        const string sql = """
            INSERT INTO manager.worker_log(daemon_identifier, external_worker_id, level, message, payload)
            VALUES (@daemon_identifier, @external_worker_id, @level, @message, @payload::jsonb);
            """;
        await using var cmd = new NpgsqlCommand(sql, connection);
        cmd.Parameters.AddWithValue("daemon_identifier", envelope.DaemonId);
        cmd.Parameters.AddWithValue("external_worker_id", (object?)envelope.WorkerId ?? DBNull.Value);
        cmd.Parameters.AddWithValue("level", level);
        cmd.Parameters.AddWithValue("message", message);
        cmd.Parameters.AddWithValue("payload", envelope.PayloadJson);
        await cmd.ExecuteNonQueryAsync();
    }

    private static async Task PersistMetricEntriesIfApplicableAsync(NpgsqlConnection connection, CrawlerEventEnvelope envelope)
    {
        var metrics = new List<(string Name, double Value)>();

        if (string.Equals(envelope.Type, "page-reported", StringComparison.OrdinalIgnoreCase))
        {
            metrics.Add(("page_processed", 1));
        }
        else if (string.Equals(envelope.Type, "worker-metric", StringComparison.OrdinalIgnoreCase))
        {
            try
            {
                using var payloadDoc = JsonDocument.Parse(envelope.PayloadJson);
                if (payloadDoc.RootElement.ValueKind == JsonValueKind.Object)
                {
                    foreach (var item in payloadDoc.RootElement.EnumerateObject())
                    {
                        if (item.Value.ValueKind == JsonValueKind.Number && item.Value.TryGetDouble(out var value))
                        {
                            metrics.Add((item.Name, value));
                        }
                    }
                }
            }
            catch
            {
                // Ignore malformed metric payloads.
            }
        }

        if (metrics.Count == 0)
        {
            return;
        }

        const string sql = """
            INSERT INTO manager.worker_metric(daemon_identifier, external_worker_id, metric_name, metric_value, payload)
            VALUES (@daemon_identifier, @external_worker_id, @metric_name, @metric_value, @payload::jsonb);
            """;

        foreach (var metric in metrics)
        {
            await using var cmd = new NpgsqlCommand(sql, connection);
            cmd.Parameters.AddWithValue("daemon_identifier", envelope.DaemonId);
            cmd.Parameters.AddWithValue("external_worker_id", (object?)envelope.WorkerId ?? DBNull.Value);
            cmd.Parameters.AddWithValue("metric_name", metric.Name);
            cmd.Parameters.AddWithValue("metric_value", metric.Value);
            cmd.Parameters.AddWithValue("payload", envelope.PayloadJson);
            await cmd.ExecuteNonQueryAsync();
        }
    }

    private async Task RunRetentionCleanupIfNeededAsync(NpgsqlConnection connection)
    {
        var now = DateTime.UtcNow;
        if (now - _lastCleanupUtc < _cleanupInterval)
        {
            return;
        }

        _lastCleanupUtc = now;

        const string deleteLogsSql = """
            DELETE FROM manager.worker_log
            WHERE created_at < NOW() - make_interval(days => @days);
            """;
        await using (var logCmd = new NpgsqlCommand(deleteLogsSql, connection))
        {
            logCmd.Parameters.AddWithValue("days", _workerLogRetentionDays);
            await logCmd.ExecuteNonQueryAsync();
        }

        const string deleteMetricsSql = """
            DELETE FROM manager.worker_metric
            WHERE created_at < NOW() - make_interval(days => @days);
            """;
        await using var metricCmd = new NpgsqlCommand(deleteMetricsSql, connection);
        metricCmd.Parameters.AddWithValue("days", _workerMetricRetentionDays);
        await metricCmd.ExecuteNonQueryAsync();
    }

    private static string Sha256Hex(string input)
    {
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(input));
        return Convert.ToHexString(bytes).ToLowerInvariant();
    }

    private static string NormalizeUrl(string? value)
    {
        var trimmed = (value ?? string.Empty).Trim();
        if (string.IsNullOrWhiteSpace(trimmed))
        {
            return string.Empty;
        }

        if (!Uri.TryCreate(trimmed, UriKind.Absolute, out var uri))
        {
            return trimmed;
        }

        var builder = new UriBuilder(uri)
        {
            Fragment = string.Empty,
        };
        return builder.Uri.AbsoluteUri;
    }

    private static async Task UpsertDiscoveredLinksAsync(
        CrawldbContext context,
        Page sourcePage,
        IReadOnlyCollection<string>? discoveredUrls,
        CancellationToken cancellationToken)
    {
        if (discoveredUrls is null || discoveredUrls.Count == 0)
        {
            return;
        }

        var normalizedUrls = discoveredUrls
            .Select(NormalizeUrl)
            .Where(url => !string.IsNullOrWhiteSpace(url))
            .Distinct(StringComparer.Ordinal)
            .ToList();
        if (normalizedUrls.Count == 0)
        {
            return;
        }

        var knownTargets = await context.Pages
            .Where(page => page.Url != null && normalizedUrls.Contains(page.Url))
            .ToDictionaryAsync(page => page.Url!, cancellationToken);

        var targetPages = new List<Page>();
        foreach (var discoveredUrl in normalizedUrls)
        {
            if (string.Equals(discoveredUrl, sourcePage.Url, StringComparison.Ordinal))
            {
                continue;
            }

            if (!knownTargets.TryGetValue(discoveredUrl, out var targetPage))
            {
                var siteId = await ResolveSiteIdAsync(context, discoveredUrl, null, cancellationToken);
                targetPage = new Page
                {
                    SiteId = siteId,
                    PageTypeCode = "FRONTIER",
                    Url = discoveredUrl,
                    HtmlContent = null,
                    HttpStatusCode = null,
                    AccessedTime = null,
                    ContentHash = null,
                    DuplicateOfPageId = null,
                };
                context.Pages.Add(targetPage);
                knownTargets[discoveredUrl] = targetPage;
            }

            targetPages.Add(targetPage);
        }

        await context.SaveChangesAsync(cancellationToken);

        foreach (var targetPage in targetPages)
        {
            await context.Database.ExecuteSqlInterpolatedAsync(
                $"INSERT INTO crawldb.link(from_page, to_page) VALUES ({sourcePage.Id}, {targetPage.Id}) ON CONFLICT DO NOTHING",
                cancellationToken);
        }
    }

    private static async Task<int?> ResolveSiteIdAsync(
        CrawldbContext context,
        string url,
        int? explicitSiteId,
        CancellationToken cancellationToken)
    {
        if (explicitSiteId.HasValue && explicitSiteId.Value > 0)
        {
            return explicitSiteId;
        }

        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || string.IsNullOrWhiteSpace(uri.Host))
        {
            return null;
        }

        var host = uri.Host.ToLowerInvariant();
        var site = await context.Sites.FirstOrDefaultAsync(s => s.Domain == host, cancellationToken);
        if (site != null)
        {
            return site.Id;
        }

        site = new Site
        {
            Domain = host,
            RobotsContent = string.Empty,
            SitemapContent = string.Empty,
        };
        context.Sites.Add(site);
        await context.SaveChangesAsync(cancellationToken);
        return site.Id;
    }

    private static async Task UpdateSitePolicyAsync(
        CrawldbContext context,
        int siteId,
        CrawlerDownloadResult? downloadResult,
        CancellationToken cancellationToken)
    {
        if (downloadResult is null)
        {
            return;
        }

        var site = await context.Sites.FirstOrDefaultAsync(item => item.Id == siteId, cancellationToken);
        if (site is null)
        {
            return;
        }

        var changed = false;
        if (!string.IsNullOrWhiteSpace(downloadResult.RobotsContent))
        {
            site.RobotsContent = downloadResult.RobotsContent;
            changed = true;
        }

        if (downloadResult.RobotsSitemaps is { Count: > 0 })
        {
            site.SitemapContent = string.Join('\n', downloadResult.RobotsSitemaps.Distinct(StringComparer.Ordinal));
            changed = true;
        }

        if (changed)
        {
            await context.SaveChangesAsync(cancellationToken);
        }
    }
}

public sealed class CrawlerIngestRequest
{
    public string? RawUrl { get; set; }
    public int? SiteId { get; set; }
    public int? SourcePageId { get; set; }
    public List<string>? DiscoveredUrls { get; set; }
    public CrawlerDownloadResult? DownloadResult { get; set; }
}

public sealed class CrawlerDownloadResult
{
    public string? RequestedUrl { get; set; }
    public string? FinalUrl { get; set; }
    public int? StatusCode { get; set; }
    public string? ContentType { get; set; }
    public string? DataTypeCode { get; set; }
    public string? PageTypeCode { get; set; }
    public string? HtmlContent { get; set; }
    public bool? UsedRenderer { get; set; }
    public int? ContentLength { get; set; }
    public JsonElement? ParsedPayload { get; set; }
    public bool? RobotsAllowed { get; set; }
    public string? RobotsUrl { get; set; }
    public bool? RobotsFetched { get; set; }
    public double? RobotsCrawlDelaySeconds { get; set; }
    public List<string>? RobotsSitemaps { get; set; }
    public string? RobotsContent { get; set; }
    public double? EffectiveDelaySeconds { get; set; }
}

public sealed class CrawlerIngestResponse
{
    public int PageId { get; set; }
    public string Status { get; set; } = "inserted";
    public string Url { get; set; } = string.Empty;
    public int? DuplicateOfPageId { get; set; }
    public string? ContentHash { get; set; }
}

public sealed class CrawlerEventMessage
{
    public string Type { get; set; } = "info";
    public string DaemonId { get; set; } = "local-default";
    public int? WorkerId { get; set; }
    public object? Payload { get; set; }
}

public sealed class CrawlerEventEnvelope
{
    public DateTime TimestampUtc { get; set; }
    public string Type { get; set; } = "info";
    public string DaemonId { get; set; } = "local-default";
    public int? WorkerId { get; set; }
    public string PayloadJson { get; set; } = "{}";
}
