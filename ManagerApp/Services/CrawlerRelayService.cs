using System.Security.Cryptography;
using System.Data;
using System.Text;
using System.Text.Json;
using System.Xml.Linq;
using ManagerApp.Data;
using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace ManagerApp.Services;

public sealed class CrawlerRelayService
{
    private readonly IDbContextFactory<CrawldbContext> _contextFactory;
    private readonly FrontierService _frontierService;
    private readonly IConfiguration _configuration;
    private readonly ILogger<CrawlerRelayService> _logger;
    private readonly HttpClient _httpClient = new();
    private readonly object _eventLock = new();
    private readonly LinkedList<CrawlerEventEnvelope> _recentEvents = new();
    private readonly string? _connectionString;
    private readonly int _workerLogRetentionDays;
    private readonly int _workerMetricRetentionDays;
    private readonly TimeSpan _cleanupInterval;
    private readonly bool _sitemapIngestEnabled;
    private readonly int _sitemapIngestMaxUrls;
    private readonly int _sitemapIngestMaxDocuments;
    private readonly int _sitemapFetchTimeoutSeconds;
    private DateTime _lastCleanupUtc = DateTime.MinValue;
    private const int MaxRecentEvents = 5000;

    public CrawlerRelayService(
        IDbContextFactory<CrawldbContext> contextFactory,
        FrontierService frontierService,
        IConfiguration configuration,
        ILogger<CrawlerRelayService> logger)
    {
        _contextFactory = contextFactory;
        _frontierService = frontierService;
        _configuration = configuration;
        _logger = logger;
        _connectionString = _configuration.GetConnectionString("CrawldbConnection");
        _workerLogRetentionDays = Math.Clamp(_configuration.GetValue("CrawlerApi:WorkerLogRetentionDays", 14), 1, 365);
        _workerMetricRetentionDays = Math.Clamp(_configuration.GetValue("CrawlerApi:WorkerMetricRetentionDays", 30), 1, 365);
        _cleanupInterval = TimeSpan.FromMinutes(Math.Clamp(_configuration.GetValue("CrawlerApi:ObservabilityCleanupMinutes", 30), 5, 24 * 60));
        _sitemapIngestEnabled = _configuration.GetValue("CrawlerApi:SitemapIngestEnabled", true);
        _sitemapIngestMaxUrls = Math.Clamp(_configuration.GetValue("CrawlerApi:SitemapIngestMaxUrls", 1000), 20, 20000);
        _sitemapIngestMaxDocuments = Math.Clamp(_configuration.GetValue("CrawlerApi:SitemapIngestMaxDocuments", 30), 1, 200);
        _sitemapFetchTimeoutSeconds = Math.Clamp(_configuration.GetValue("CrawlerApi:SitemapFetchTimeoutSeconds", 10), 2, 60);
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

        var sitemapCandidates = new List<string>();
        var siteId = await ResolveSiteIdAsync(context, url, request.SiteId, cancellationToken);
        if (siteId.HasValue)
        {
            sitemapCandidates = await UpdateSitePolicyAsync(context, siteId.Value, request.DownloadResult, cancellationToken);
        }
        else if (request.DownloadResult?.RobotsSitemaps is { Count: > 0 })
        {
            sitemapCandidates = request.DownloadResult.RobotsSitemaps
                .Select(NormalizeUrl)
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .Distinct(StringComparer.Ordinal)
                .ToList();
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

        try
        {
            await context.SaveChangesAsync(cancellationToken);
        }
        catch (DbUpdateException)
        {
            if (targetPage.Id == 0)
            {
                var targetEntry = context.Entry(targetPage);
                if (targetEntry.State != EntityState.Detached)
                {
                    targetEntry.State = EntityState.Detached;
                }
            }

            var racedPage = await context.Pages
                .FirstOrDefaultAsync(page => page.Url == url, cancellationToken);
            if (racedPage is null)
            {
                throw;
            }

            racedPage.SiteId ??= siteId;
            racedPage.PageTypeCode = pageTypeCode;
            racedPage.AccessedTime = accessedTime;
            racedPage.HttpStatusCode = request.DownloadResult?.StatusCode;
            racedPage.HtmlContent = pageTypeCode == "HTML" ? html : null;
            racedPage.ContentHash = pageTypeCode == "HTML" ? contentHash : null;
            racedPage.DuplicateOfPageId = null;

            targetPage = racedPage;
            status = "updated";
            await context.SaveChangesAsync(cancellationToken);
        }

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

        var discoveredQueueCandidates = await UpsertDiscoveredLinksAsync(
            context,
            targetPage.DuplicateOfPageId.HasValue
                ? await context.Pages.FirstAsync(page => page.Id == targetPage.DuplicateOfPageId.Value, cancellationToken)
                : targetPage,
            request.DiscoveredUrls,
            cancellationToken);

        if (discoveredQueueCandidates.Count > 0)
        {
            var enqueueCandidates = discoveredQueueCandidates
                .Select(discoveredUrl => new FrontierEnqueueCandidate
                {
                    Url = discoveredUrl,
                    Priority = 0,
                    Depth = 1,
                    SourceUrl = url,
                })
                .ToList();

            _ = await _frontierService.EnqueueBatchAsync(enqueueCandidates, cancellationToken);
        }

        if (sitemapCandidates.Count > 0)
        {
            _ = await EnqueueSitemapDiscoveredUrlsAsync(sitemapCandidates, url, cancellationToken);
        }

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

    private static async Task<List<string>> UpsertDiscoveredLinksAsync(
        CrawldbContext context,
        Page sourcePage,
        IReadOnlyCollection<string>? discoveredUrls,
        CancellationToken cancellationToken)
    {
        if (discoveredUrls is null || discoveredUrls.Count == 0)
        {
            return new List<string>();
        }

        var normalizedUrls = discoveredUrls
            .Select(NormalizeUrl)
            .Where(url => !string.IsNullOrWhiteSpace(url))
            .Distinct(StringComparer.Ordinal)
            .ToList();
        if (normalizedUrls.Count == 0)
        {
            return new List<string>();
        }

        var filteredUrls = normalizedUrls
            .Where(discoveredUrl => !string.Equals(discoveredUrl, sourcePage.Url, StringComparison.Ordinal))
            .ToList();
        if (filteredUrls.Count == 0)
        {
            return new List<string>();
        }

        var knownTargets = await context.Pages
            .Where(page => page.Url != null && filteredUrls.Contains(page.Url))
            .ToDictionaryAsync(page => page.Url!, cancellationToken);

        var missingUrls = filteredUrls
            .Where(discoveredUrl => !knownTargets.ContainsKey(discoveredUrl))
            .ToList();

        if (missingUrls.Count > 0)
        {
            var siteIdCache = new Dictionary<string, int?>(StringComparer.OrdinalIgnoreCase);
            var insertedTargets = new List<Page>(missingUrls.Count);
            foreach (var discoveredUrl in missingUrls)
            {
                var siteId = await ResolveSiteIdAsync(context, discoveredUrl, null, cancellationToken, siteIdCache);
                insertedTargets.Add(new Page
                {
                    SiteId = siteId,
                    PageTypeCode = "FRONTIER",
                    Url = discoveredUrl,
                    HtmlContent = null,
                    HttpStatusCode = null,
                    AccessedTime = null,
                    ContentHash = null,
                    DuplicateOfPageId = null,
                });
            }

            context.Pages.AddRange(insertedTargets);
            try
            {
                await context.SaveChangesAsync(cancellationToken);
            }
            catch (DbUpdateException)
            {
                // Another ingest worker inserted some of the same URLs concurrently.
                foreach (var insertedTarget in insertedTargets)
                {
                    var entry = context.Entry(insertedTarget);
                    if (entry.State != EntityState.Detached)
                    {
                        entry.State = EntityState.Detached;
                    }
                }
            }

            knownTargets = await context.Pages
                .Where(page => page.Url != null && filteredUrls.Contains(page.Url))
                .ToDictionaryAsync(page => page.Url!, cancellationToken);

            var unresolvedUrls = missingUrls
                .Where(discoveredUrl => !knownTargets.ContainsKey(discoveredUrl))
                .ToList();
            if (unresolvedUrls.Count > 0)
            {
                throw new InvalidOperationException(
                    $"Failed to upsert discovered frontier URLs: {string.Join(", ", unresolvedUrls.Take(5))}");
            }
        }

        var targetPages = filteredUrls
            .Where(knownTargets.ContainsKey)
            .Select(discoveredUrl => knownTargets[discoveredUrl])
            .ToList();

        var targetIds = targetPages
            .Select(targetPage => targetPage.Id)
            .Where(id => id > 0)
            .Distinct()
            .ToArray();
        if (targetIds.Length > 0)
        {
            var connection = context.Database.GetDbConnection() as NpgsqlConnection;
            if (connection is null)
            {
                throw new InvalidOperationException("Expected Npgsql connection for crawldb context.");
            }

            if (connection.State != ConnectionState.Open)
            {
                await connection.OpenAsync(cancellationToken);
            }

            const string linkSql = """
                INSERT INTO crawldb.link(from_page, to_page)
                SELECT @from_page, target_id
                FROM unnest(@target_ids) AS target_id
                ON CONFLICT DO NOTHING;
                """;

            await using var linkCmd = new NpgsqlCommand(linkSql, connection);
            linkCmd.Parameters.AddWithValue("from_page", sourcePage.Id);
            linkCmd.Parameters.AddWithValue("target_ids", targetIds);
            await linkCmd.ExecuteNonQueryAsync(cancellationToken);
        }

        return targetPages
            .Where(targetPage =>
                string.Equals(targetPage.PageTypeCode, "FRONTIER", StringComparison.OrdinalIgnoreCase)
                && !string.IsNullOrWhiteSpace(targetPage.Url))
            .Select(targetPage => targetPage.Url!)
            .Distinct(StringComparer.Ordinal)
            .ToList();
    }

    private static async Task<int?> ResolveSiteIdAsync(
        CrawldbContext context,
        string url,
        int? explicitSiteId,
        CancellationToken cancellationToken,
        Dictionary<string, int?>? siteIdCache = null)
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

        if (siteIdCache is not null && siteIdCache.TryGetValue(host, out var cachedSiteId))
        {
            return cachedSiteId;
        }

        var site = await context.Sites.FirstOrDefaultAsync(s => s.Domain == host, cancellationToken);
        if (site != null)
        {
            siteIdCache?[host] = site.Id;
            return site.Id;
        }

        site = new Site
        {
            Domain = host,
            RobotsContent = string.Empty,
            SitemapContent = string.Empty,
        };
        context.Sites.Add(site);
        try
        {
            await context.SaveChangesAsync(cancellationToken);
            siteIdCache?[host] = site.Id;
            return site.Id;
        }
        catch (DbUpdateException)
        {
            context.Entry(site).State = EntityState.Detached;
            site = await context.Sites.FirstOrDefaultAsync(s => s.Domain == host, cancellationToken);
            if (site != null)
            {
                siteIdCache?[host] = site.Id;
                return site.Id;
            }

            throw;
        }
    }

    private async Task<List<string>> EnqueueSitemapDiscoveredUrlsAsync(
        IReadOnlyCollection<string> sitemapUrls,
        string sourceUrl,
        CancellationToken cancellationToken)
    {
        if (!_sitemapIngestEnabled || sitemapUrls.Count == 0)
        {
            return new List<string>();
        }

        var normalizedSitemaps = sitemapUrls
            .Select(NormalizeUrl)
            .Where(url => !string.IsNullOrWhiteSpace(url))
            .Distinct(StringComparer.Ordinal)
            .ToList();
        if (normalizedSitemaps.Count == 0)
        {
            return new List<string>();
        }

        var sourceHost = GetHost(sourceUrl);
        var visitedSitemaps = new HashSet<string>(StringComparer.Ordinal);
        var discoveredUrls = new HashSet<string>(StringComparer.Ordinal);
        var pending = new Queue<string>(normalizedSitemaps);

        while (pending.Count > 0
               && visitedSitemaps.Count < _sitemapIngestMaxDocuments
               && discoveredUrls.Count < _sitemapIngestMaxUrls)
        {
            var sitemapUrl = pending.Dequeue();
            if (!visitedSitemaps.Add(sitemapUrl))
            {
                continue;
            }

            var xml = await TryFetchTextAsync(sitemapUrl, cancellationToken);
            if (string.IsNullOrWhiteSpace(xml))
            {
                continue;
            }

            ParseSitemap(xml, out var childSitemaps, out var pageUrls);

            foreach (var child in childSitemaps)
            {
                var normalizedChild = NormalizeUrl(child);
                if (string.IsNullOrWhiteSpace(normalizedChild)
                    || visitedSitemaps.Contains(normalizedChild))
                {
                    continue;
                }

                pending.Enqueue(normalizedChild);
            }

            foreach (var pageUrl in pageUrls)
            {
                if (discoveredUrls.Count >= _sitemapIngestMaxUrls)
                {
                    break;
                }

                var normalizedPageUrl = NormalizeUrl(pageUrl);
                if (string.IsNullOrWhiteSpace(normalizedPageUrl))
                {
                    continue;
                }

                if (!string.IsNullOrWhiteSpace(sourceHost)
                    && !string.Equals(GetHost(normalizedPageUrl), sourceHost, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                discoveredUrls.Add(normalizedPageUrl);
            }
        }

        if (discoveredUrls.Count == 0)
        {
            return new List<string>();
        }

        var enqueueCandidates = discoveredUrls
            .Select(url => new FrontierEnqueueCandidate
            {
                Url = url,
                Priority = 0,
                Depth = 1,
                SourceUrl = sourceUrl,
            })
            .ToList();

        _ = await _frontierService.EnqueueBatchAsync(enqueueCandidates, cancellationToken);
        return discoveredUrls.ToList();
    }

    private async Task<string?> TryFetchTextAsync(string url, CancellationToken cancellationToken)
    {
        try
        {
            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeoutCts.CancelAfter(TimeSpan.FromSeconds(_sitemapFetchTimeoutSeconds));

            using var request = new HttpRequestMessage(HttpMethod.Get, url);
            request.Headers.UserAgent.ParseAdd("EIPS-TT-Manager/1.0");
            using var response = await _httpClient.SendAsync(request, HttpCompletionOption.ResponseContentRead, timeoutCts.Token);
            if (!response.IsSuccessStatusCode)
            {
                return null;
            }

            return await response.Content.ReadAsStringAsync(timeoutCts.Token);
        }
        catch
        {
            return null;
        }
    }

    private static void ParseSitemap(string xmlContent, out List<string> childSitemaps, out List<string> pageUrls)
    {
        childSitemaps = new List<string>();
        pageUrls = new List<string>();

        try
        {
            var document = XDocument.Parse(xmlContent);
            var rootName = document.Root?.Name.LocalName?.ToLowerInvariant() ?? string.Empty;
            var locValues = document
                .Descendants()
                .Where(node => string.Equals(node.Name.LocalName, "loc", StringComparison.OrdinalIgnoreCase))
                .Select(node => node.Value?.Trim())
                .Where(value => !string.IsNullOrWhiteSpace(value))
                .Cast<string>()
                .Distinct(StringComparer.Ordinal)
                .ToList();

            if (rootName == "sitemapindex")
            {
                childSitemaps.AddRange(locValues);
                return;
            }

            pageUrls.AddRange(locValues);
        }
        catch
        {
            // Ignore malformed sitemap payload.
        }
    }

    private static string? GetHost(string? url)
    {
        if (string.IsNullOrWhiteSpace(url))
        {
            return null;
        }

        return Uri.TryCreate(url, UriKind.Absolute, out var uri)
            ? uri.Host
            : null;
    }

    private static async Task<List<string>> UpdateSitePolicyAsync(
        CrawldbContext context,
        int siteId,
        CrawlerDownloadResult? downloadResult,
        CancellationToken cancellationToken)
    {
        var updatedSitemaps = new List<string>();
        if (downloadResult is null)
        {
            return updatedSitemaps;
        }

        var site = await context.Sites.FirstOrDefaultAsync(item => item.Id == siteId, cancellationToken);
        if (site is null)
        {
            return updatedSitemaps;
        }

        var changed = false;
        if (!string.IsNullOrWhiteSpace(downloadResult.RobotsContent))
        {
            if (!string.Equals(site.RobotsContent, downloadResult.RobotsContent, StringComparison.Ordinal))
            {
                site.RobotsContent = downloadResult.RobotsContent;
                changed = true;
            }
        }

        if (downloadResult.RobotsSitemaps is { Count: > 0 })
        {
            var normalizedSitemaps = downloadResult.RobotsSitemaps
                .Select(NormalizeUrl)
                .Where(url => !string.IsNullOrWhiteSpace(url))
                .Distinct(StringComparer.Ordinal)
                .ToList();
            var sitemapContent = string.Join('\n', normalizedSitemaps);
            if (!string.Equals(site.SitemapContent, sitemapContent, StringComparison.Ordinal))
            {
                site.SitemapContent = sitemapContent;
                changed = true;
                updatedSitemaps = normalizedSitemaps;
            }
        }

        if (changed)
        {
            await context.SaveChangesAsync(cancellationToken);
        }

        return updatedSitemaps;
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
