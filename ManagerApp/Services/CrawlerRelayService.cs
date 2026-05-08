using System.Security.Cryptography;
using System.Data;
using System.Text;
using System.Text.Json;
using System.Xml.Linq;
using ManagerApp.Data;
using ManagerApp.Models;
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
    private readonly bool _imageStorageEnabled;
    private readonly int _imageFetchTimeoutSeconds;
    private readonly int _imageMaxBytes;
    private readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
    };
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
        _imageStorageEnabled = _configuration.GetValue("CrawlerApi:ImageStorageEnabled", true);
        _imageFetchTimeoutSeconds = Math.Clamp(_configuration.GetValue("CrawlerApi:ImageFetchTimeoutSeconds", 10), 2, 60);
        _imageMaxBytes = Math.Clamp(_configuration.GetValue("CrawlerApi:ImageMaxBytes", 2_000_000), 1_024, 25_000_000);
    }

    public async Task<CrawlerIngestResponse> IngestAsync(CrawlerIngestRequest request, CancellationToken cancellationToken)
    {
        var accessedTime = DateTime.SpecifyKind(DateTime.UtcNow, DateTimeKind.Unspecified);
        var rawUrl = NormalizeUrl(request.RawUrl);
        var finalUrl = NormalizeUrl(request.DownloadResult?.FinalUrl);
        LogCanonicalRewrite("crawler.rawUrl", request.RawUrl, rawUrl);
        LogCanonicalRewrite("crawler.finalUrl", request.DownloadResult?.FinalUrl, finalUrl);
        var url = string.IsNullOrWhiteSpace(finalUrl) ? rawUrl : finalUrl;
        if (string.IsNullOrWhiteSpace(url))
        {
            throw new InvalidOperationException("rawUrl/finalUrl must be provided.");
        }

        await _frontierService.ReportObservedDelayAsync(
            request.DaemonId,
            url,
            request.DownloadResult?.EffectiveDelaySeconds,
            request.DownloadResult?.RobotsCrawlDelaySeconds,
            cancellationToken);

        var pageTypeCode = string.IsNullOrWhiteSpace(request.DownloadResult?.PageTypeCode)
            ? "HTML"
            : request.DownloadResult!.PageTypeCode!.Trim().ToUpperInvariant();
        var html = request.DownloadResult?.HtmlContent;
        var contentHash = string.IsNullOrWhiteSpace(html) ? null : Sha256Hex(html);
        var binaryPayloadBytes = DecodeBinaryPayload(request.DownloadResult?.BinaryContentBase64);
        var parsedPayloadBytes = SerializeParsedPayload(request.DownloadResult?.ParsedPayload);
        var pageDataBytes = binaryPayloadBytes ?? parsedPayloadBytes;

        await using var context = await _contextFactory.CreateDbContextAsync(cancellationToken);
        var allowedScopeHosts = await ResolveAllowedScopeHostsAsync(context, url, cancellationToken);

        var existingPage = await context.Pages
            .FirstOrDefaultAsync(page => page.Url == url, cancellationToken);

        var duplicateOfPageId = string.Equals(pageTypeCode, "HTML", StringComparison.OrdinalIgnoreCase)
            && !string.IsNullOrWhiteSpace(contentHash)
            ? await context.Pages
                .Where(page =>
                    page.Url != url &&
                    page.PageTypeCode == "HTML" &&
                    page.ContentHash == contentHash)
                .OrderBy(page => page.Id)
                .Select(page => (int?)page.Id)
                .FirstOrDefaultAsync(cancellationToken)
            : null;

        var sitemapCandidates = new List<string>();
        var siteId = await ResolveSiteIdAsync(context, url, request.SiteId, cancellationToken);
        if (siteId.HasValue)
        {
            sitemapCandidates = await UpdateSitePolicyAsync(context, siteId.Value, request.DownloadResult, cancellationToken);
        }
        else if (request.DownloadResult?.RobotsSitemaps is { Count: > 0 })
        {
            var canonicalizedSitemaps = 0;
            var normalizedSitemaps = new List<string>();
            foreach (var sitemap in request.DownloadResult.RobotsSitemaps)
            {
                var normalized = NormalizeUrl(sitemap);
                if (string.IsNullOrWhiteSpace(normalized))
                {
                    continue;
                }

                var original = (sitemap ?? string.Empty).Trim();
                if (!string.IsNullOrWhiteSpace(original)
                    && !string.Equals(original, normalized, StringComparison.Ordinal))
                {
                    canonicalizedSitemaps += 1;
                }

                normalizedSitemaps.Add(normalized);
            }

            sitemapCandidates = normalizedSitemaps
                .Distinct(StringComparer.Ordinal)
                .ToList();

            if (canonicalizedSitemaps > 0)
            {
                _logger.LogInformation(
                    "Canonicalized {Count} robots sitemap URLs during ingest for {Url}.",
                    canonicalizedSitemaps,
                    url);
            }
        }
        var status = "inserted";
        Page targetPage;

        if (existingPage != null && !string.Equals(existingPage.PageTypeCode, "FRONTIER", StringComparison.OrdinalIgnoreCase))
        {
            existingPage.SiteId ??= siteId;
            existingPage.AccessedTime = accessedTime;
            existingPage.HttpStatusCode = request.DownloadResult?.StatusCode;
            existingPage.ContentHash = pageTypeCode == "HTML" ? contentHash : null;
            existingPage.DuplicateOfPageId = duplicateOfPageId;

            if (string.Equals(pageTypeCode, "HTML", StringComparison.OrdinalIgnoreCase) && duplicateOfPageId.HasValue)
            {
                existingPage.PageTypeCode = "DUPLICATE";
                existingPage.HtmlContent = null;
                status = "updated_duplicate";
            }
            else
            {
                existingPage.PageTypeCode = pageTypeCode;
                existingPage.HtmlContent = pageTypeCode == "HTML" ? html : null;
                status = "updated";
            }

            targetPage = existingPage;
        }
        else
        {
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
            racedPage.AccessedTime = accessedTime;
            racedPage.HttpStatusCode = request.DownloadResult?.StatusCode;
            racedPage.ContentHash = pageTypeCode == "HTML" ? contentHash : null;

            var racedDuplicateOfPageId = string.Equals(pageTypeCode, "HTML", StringComparison.OrdinalIgnoreCase)
                && !string.IsNullOrWhiteSpace(contentHash)
                ? await context.Pages
                    .Where(page =>
                        page.Url != url &&
                        page.PageTypeCode == "HTML" &&
                        page.ContentHash == contentHash)
                    .OrderBy(page => page.Id)
                    .Select(page => (int?)page.Id)
                    .FirstOrDefaultAsync(cancellationToken)
                : null;

            racedPage.DuplicateOfPageId = racedDuplicateOfPageId;
            if (string.Equals(pageTypeCode, "HTML", StringComparison.OrdinalIgnoreCase) && racedDuplicateOfPageId.HasValue)
            {
                racedPage.PageTypeCode = "DUPLICATE";
                racedPage.HtmlContent = null;
                status = "updated_duplicate";
            }
            else
            {
                racedPage.PageTypeCode = pageTypeCode;
                racedPage.HtmlContent = pageTypeCode == "HTML" ? html : null;
                status = "updated";
            }

            targetPage = racedPage;
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
                    Data = pageDataBytes,
                });
                await context.SaveChangesAsync(cancellationToken);
            }
            else if (pageDataBytes is not null)
            {
                existingPageData.Data = pageDataBytes;
                await context.SaveChangesAsync(cancellationToken);
            }
        }

        var discoveredImageUrls = new List<string>();
        if (request.DiscoveredImageUrls is { Count: > 0 })
        {
            discoveredImageUrls.AddRange(request.DiscoveredImageUrls);
        }
        discoveredImageUrls.AddRange(ExtractParsedImageUrls(request.DownloadResult?.ParsedPayload));

        var canonicalizedDiscoveredImages = CountCanonicalRewrites(discoveredImageUrls);
        if (canonicalizedDiscoveredImages > 0)
        {
            _logger.LogInformation(
                "Canonicalized {Count} discovered image URLs during ingest for {Url}.",
                canonicalizedDiscoveredImages,
                url);
        }

        var inScopeImageUrls = FilterUrlsByScope(discoveredImageUrls, allowedScopeHosts);
        var droppedImageCount = Math.Max(0, discoveredImageUrls.Count - inScopeImageUrls.Count);
        if (droppedImageCount > 0)
        {
            _logger.LogInformation(
                "Dropped {Count} out-of-scope discovered image URLs during ingest for {Url}.",
                droppedImageCount,
                url);
        }

        if (inScopeImageUrls.Count > 0)
        {
            await UpsertDiscoveredImagesAsync(
                context,
                targetPage,
                inScopeImageUrls,
                accessedTime,
                cancellationToken,
                allowedScopeHosts);

            await StoreDiscoveredImageDataAsync(
                context,
                targetPage,
                inScopeImageUrls,
                cancellationToken);
        }

        var canonicalizedDiscoveredPages = CountCanonicalRewrites(request.DiscoveredUrls);
        if (canonicalizedDiscoveredPages > 0)
        {
            _logger.LogInformation(
                "Canonicalized {Count} discovered page URLs during ingest for {Url}.",
                canonicalizedDiscoveredPages,
                url);
        }

        var inScopeDiscoveredUrls = FilterUrlsByScope(request.DiscoveredUrls ?? new List<string>(), allowedScopeHosts);
        var droppedDiscoveredCount = Math.Max(0, (request.DiscoveredUrls?.Count ?? 0) - inScopeDiscoveredUrls.Count);
        if (droppedDiscoveredCount > 0)
        {
            _logger.LogInformation(
                "Dropped {Count} out-of-scope discovered page URLs during ingest for {Url}.",
                droppedDiscoveredCount,
                url);
        }

        var queueEligibilityInput = request.QueueEligibleDiscoveredUrls is { Count: > 0 }
            ? request.QueueEligibleDiscoveredUrls
            : request.DiscoveredUrls;
        var canonicalizedQueueEligiblePages = CountCanonicalRewrites(queueEligibilityInput);
        if (canonicalizedQueueEligiblePages > 0)
        {
            _logger.LogInformation(
                "Canonicalized {Count} queue-eligible discovered URLs during ingest for {Url}.",
                canonicalizedQueueEligiblePages,
                url);
        }

        var normalizedQueueEligibilityInput = (queueEligibilityInput ?? new List<string>())
            .Select(NormalizeUrl)
            .Where(queueUrl => !string.IsNullOrWhiteSpace(queueUrl))
            .Distinct(StringComparer.Ordinal)
            .ToList();

        var inScopeQueueEligibleDiscoveredUrls = FilterUrlsByScope(
            normalizedQueueEligibilityInput,
            allowedScopeHosts);
        var droppedQueueEligibleCount = Math.Max(0, normalizedQueueEligibilityInput.Count - inScopeQueueEligibleDiscoveredUrls.Count);
        if (droppedQueueEligibleCount > 0)
        {
            _logger.LogInformation(
                "Dropped {Count} out-of-scope queue-eligible discovered URLs during ingest for {Url}.",
                droppedQueueEligibleCount,
                url);
        }

        var discoveredQueueCandidates = await UpsertDiscoveredLinksAsync(
            context,
            targetPage.DuplicateOfPageId.HasValue
                ? await context.Pages.FirstAsync(page => page.Id == targetPage.DuplicateOfPageId.Value, cancellationToken)
                : targetPage,
            inScopeDiscoveredUrls,
            cancellationToken,
            allowedScopeHosts: allowedScopeHosts);

        var queueEligibleSet = new HashSet<string>(inScopeQueueEligibleDiscoveredUrls, StringComparer.Ordinal);
        var queueCandidates = discoveredQueueCandidates
            .Where(discoveredUrl => queueEligibleSet.Contains(discoveredUrl))
            .ToList();

        var filteredOutQueueCandidates = discoveredQueueCandidates.Count - queueCandidates.Count;
        if (filteredOutQueueCandidates > 0)
        {
            _logger.LogInformation(
                "Skipped queueing {Count} discovered URLs because they were not queue-eligible for {Url}.",
                filteredOutQueueCandidates,
                url);
        }

        if (queueCandidates.Count > 0)
        {
            var enqueueCandidates = queueCandidates
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
            _ = await EnqueueSitemapDiscoveredUrlsAsync(sitemapCandidates, url, allowedScopeHosts, cancellationToken);
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

    private static byte[]? DecodeBinaryPayload(string? binaryContentBase64)
    {
        if (string.IsNullOrWhiteSpace(binaryContentBase64))
        {
            return null;
        }

        try
        {
            return Convert.FromBase64String(binaryContentBase64);
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
        var isRemovedEvent = string.Equals(envelope.Type, "worker-removed", StringComparison.OrdinalIgnoreCase);

        if (isRemovedEvent)
        {
            const string deleteSql = """
                DELETE FROM manager.worker
                WHERE daemon_id = @daemon_id
                  AND external_worker_id = @external_worker_id;
                """;

            await using var deleteCmd = new NpgsqlCommand(deleteSql, connection);
            deleteCmd.Parameters.AddWithValue("daemon_id", daemonDbId.Value);
            deleteCmd.Parameters.AddWithValue("external_worker_id", workerId);
            await deleteCmd.ExecuteNonQueryAsync();
            return;
        }

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
                    var normalizedCurrentUrl = NormalizeUrl(currentUrlNode.GetString());
                    currentUrl = string.IsNullOrWhiteSpace(normalizedCurrentUrl)
                        ? null
                        : normalizedCurrentUrl;
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
        return NormalizeUrl(value, baseUrl: null);
    }

    private static string NormalizeUrl(string? value, string? baseUrl)
    {
        return CanonicalUrlNormalizer.Normalize(value, baseUrl) ?? string.Empty;
    }

    private int CountCanonicalRewrites(IEnumerable<string>? urls)
    {
        if (urls is null)
        {
            return 0;
        }

        var rewritten = 0;
        foreach (var candidate in urls)
        {
            var original = (candidate ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(original))
            {
                continue;
            }

            var normalized = NormalizeUrl(original);
            if (!string.IsNullOrWhiteSpace(normalized)
                && !string.Equals(original, normalized, StringComparison.Ordinal))
            {
                rewritten += 1;
            }
        }

        return rewritten;
    }

    private void LogCanonicalRewrite(string source, string? original, string normalized)
    {
        var raw = (original ?? string.Empty).Trim();
        if (string.IsNullOrWhiteSpace(raw)
            || string.IsNullOrWhiteSpace(normalized)
            || string.Equals(raw, normalized, StringComparison.Ordinal))
        {
            return;
        }

        _logger.LogDebug(
            "Canonicalized {Source} URL from {OriginalUrl} to {CanonicalUrl}.",
            source,
            raw,
            normalized);
    }

    private static List<string> ExtractParsedImageUrls(JsonElement? parsedPayload)
    {
        if (parsedPayload is null)
        {
            return new List<string>();
        }

        var payload = parsedPayload.Value;
        if (payload.ValueKind != JsonValueKind.Object
            || !payload.TryGetProperty("images", out var imagesNode)
            || imagesNode.ValueKind != JsonValueKind.Array)
        {
            return new List<string>();
        }

        var discovered = new List<string>();
        foreach (var item in imagesNode.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.String)
            {
                continue;
            }

            var normalized = NormalizeUrl(item.GetString());
            if (string.IsNullOrWhiteSpace(normalized))
            {
                continue;
            }

            discovered.Add(normalized);
        }

        return discovered;
    }

    private static async Task UpsertDiscoveredImagesAsync(
        CrawldbContext context,
        Page sourcePage,
        IReadOnlyCollection<string> discoveredImageUrls,
        DateTime accessedTime,
        CancellationToken cancellationToken,
        IReadOnlySet<string>? allowedScopeHosts = null)
    {
        if (discoveredImageUrls.Count == 0)
        {
            return;
        }

        await UpsertDiscoveredLinksAsync(
            context,
            sourcePage,
            discoveredImageUrls,
            cancellationToken,
            queueFrontierOnly: false,
            allowedScopeHosts: allowedScopeHosts);

        var normalizedImages = discoveredImageUrls
            .Select(NormalizeUrl)
            .Where(url => !string.IsNullOrWhiteSpace(url))
            .Distinct(StringComparer.Ordinal)
            .ToList();
        if (normalizedImages.Count == 0)
        {
            return;
        }

        var existingNames = await context.Images
            .Where(image => image.PageId == sourcePage.Id && image.Filename != null)
            .Select(image => image.Filename!)
            .ToHashSetAsync(StringComparer.Ordinal, cancellationToken);

        foreach (var imageUrl in normalizedImages)
        {
            var filename = BuildImageRecordName(imageUrl);
            if (existingNames.Contains(filename))
            {
                continue;
            }

            context.Images.Add(new Image
            {
                PageId = sourcePage.Id,
                Filename = filename,
                ContentType = GuessImageContentType(imageUrl),
                Data = null,
                AccessedTime = accessedTime,
            });
            existingNames.Add(filename);
        }

        await context.SaveChangesAsync(cancellationToken);
    }

    private static string BuildImageRecordName(string imageUrl)
    {
        var normalized = (imageUrl ?? string.Empty).Trim();
        if (normalized.Length <= 255)
        {
            return normalized;
        }

        var hashSuffix = Sha256Hex(normalized)[..12];
        return normalized[..Math.Max(0, 255 - hashSuffix.Length - 1)] + "-" + hashSuffix;
    }

    private static string? GuessImageContentType(string imageUrl)
    {
        if (!Uri.TryCreate(imageUrl, UriKind.Absolute, out var uri))
        {
            return null;
        }

        var path = uri.AbsolutePath.ToLowerInvariant();
        return path switch
        {
            var value when value.EndsWith(".png") => "image/png",
            var value when value.EndsWith(".jpg") || value.EndsWith(".jpeg") => "image/jpeg",
            var value when value.EndsWith(".gif") => "image/gif",
            var value when value.EndsWith(".bmp") => "image/bmp",
            var value when value.EndsWith(".webp") => "image/webp",
            var value when value.EndsWith(".svg") => "image/svg+xml",
            var value when value.EndsWith(".ico") => "image/x-icon",
            var value when value.EndsWith(".tif") || value.EndsWith(".tiff") => "image/tiff",
            var value when value.EndsWith(".avif") => "image/avif",
            _ => null,
        };
    }

    private async Task StoreDiscoveredImageDataAsync(
        CrawldbContext context,
        Page sourcePage,
        IReadOnlyCollection<string> discoveredImageUrls,
        CancellationToken cancellationToken)
    {
        if (!_imageStorageEnabled || discoveredImageUrls.Count == 0)
        {
            return;
        }

        var normalizedImages = discoveredImageUrls
            .Select(NormalizeUrl)
            .Where(url => !string.IsNullOrWhiteSpace(url))
            .Distinct(StringComparer.Ordinal)
            .ToList();
        if (normalizedImages.Count == 0)
        {
            return;
        }

        var imageByFilename = await context.Images
            .Where(image => image.PageId == sourcePage.Id && image.Filename != null)
            .ToDictionaryAsync(image => image.Filename!, cancellationToken);

        var changed = false;
        foreach (var imageUrl in normalizedImages)
        {
            var filename = BuildImageRecordName(imageUrl);
            if (!imageByFilename.TryGetValue(filename, out var image) || image.Data is not null)
            {
                continue;
            }

            var payload = await TryDownloadImagePayloadAsync(imageUrl, cancellationToken);
            if (payload is null)
            {
                continue;
            }

            image.Data = payload;
            image.AccessedTime = DateTime.SpecifyKind(DateTime.UtcNow, DateTimeKind.Unspecified);
            changed = true;
        }

        if (changed)
        {
            await context.SaveChangesAsync(cancellationToken);
        }
    }

    private async Task<byte[]?> TryDownloadImagePayloadAsync(string url, CancellationToken cancellationToken)
    {
        try
        {
            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeoutCts.CancelAfter(TimeSpan.FromSeconds(_imageFetchTimeoutSeconds));

            using var request = new HttpRequestMessage(HttpMethod.Get, url);
            request.Headers.UserAgent.ParseAdd("EIPS-TT-Manager/1.0");
            using var response = await _httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, timeoutCts.Token);
            if (!response.IsSuccessStatusCode)
            {
                return null;
            }

            var mediaType = response.Content.Headers.ContentType?.MediaType;
            if (string.IsNullOrWhiteSpace(mediaType)
                || !mediaType.StartsWith("image/", StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }

            var contentLength = response.Content.Headers.ContentLength;
            if (contentLength.HasValue && contentLength.Value > _imageMaxBytes)
            {
                return null;
            }

            var data = await response.Content.ReadAsByteArrayAsync(timeoutCts.Token);
            if (data.Length == 0 || data.Length > _imageMaxBytes)
            {
                return null;
            }

            return data;
        }
        catch
        {
            return null;
        }
    }

    private static async Task<List<string>> UpsertDiscoveredLinksAsync(
        CrawldbContext context,
        Page sourcePage,
        IReadOnlyCollection<string>? discoveredUrls,
        CancellationToken cancellationToken,
        bool queueFrontierOnly = true,
        IReadOnlySet<string>? allowedScopeHosts = null)
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

        var scopedUrls = FilterUrlsByScope(filteredUrls, allowedScopeHosts);
        if (scopedUrls.Count == 0)
        {
            return new List<string>();
        }

        var knownTargets = await context.Pages
            .Where(page => page.Url != null && scopedUrls.Contains(page.Url))
            .ToDictionaryAsync(page => page.Url!, cancellationToken);

        var targetPages = knownTargets.Values
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

        if (!queueFrontierOnly)
        {
            return new List<string>();
        }

        return scopedUrls
            .Where(discoveredUrl =>
            {
                if (!knownTargets.TryGetValue(discoveredUrl, out var targetPage))
                {
                    return true;
                }

                return string.Equals(targetPage.PageTypeCode, "FRONTIER", StringComparison.OrdinalIgnoreCase);
            })
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
        IReadOnlySet<string>? allowedScopeHosts,
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

        var visitedSitemaps = new HashSet<string>(StringComparer.Ordinal);
        var discoveredUrls = new HashSet<string>(StringComparer.Ordinal);
        var droppedOutOfScopeCount = 0;
        var canonicalizedSitemapChildCount = 0;
        var canonicalizedSitemapPageCount = 0;
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
                var normalizedChild = NormalizeUrl(child, sitemapUrl);
                if (string.IsNullOrWhiteSpace(normalizedChild)
                    || visitedSitemaps.Contains(normalizedChild))
                {
                    continue;
                }

                var originalChild = (child ?? string.Empty).Trim();
                if (!string.IsNullOrWhiteSpace(originalChild)
                    && !string.Equals(originalChild, normalizedChild, StringComparison.Ordinal))
                {
                    canonicalizedSitemapChildCount += 1;
                }

                pending.Enqueue(normalizedChild);
            }

            foreach (var pageUrl in pageUrls)
            {
                if (discoveredUrls.Count >= _sitemapIngestMaxUrls)
                {
                    break;
                }

                var normalizedPageUrl = NormalizeUrl(pageUrl, sitemapUrl);
                if (string.IsNullOrWhiteSpace(normalizedPageUrl))
                {
                    continue;
                }

                var originalPageUrl = (pageUrl ?? string.Empty).Trim();
                if (!string.IsNullOrWhiteSpace(originalPageUrl)
                    && !string.Equals(originalPageUrl, normalizedPageUrl, StringComparison.Ordinal))
                {
                    canonicalizedSitemapPageCount += 1;
                }

                if (!IsUrlWithinScope(normalizedPageUrl, allowedScopeHosts))
                {
                    droppedOutOfScopeCount += 1;
                    continue;
                }

                discoveredUrls.Add(normalizedPageUrl);
            }
        }

        if (discoveredUrls.Count == 0)
        {
            if (canonicalizedSitemapChildCount > 0 || canonicalizedSitemapPageCount > 0)
            {
                _logger.LogInformation(
                    "Canonicalized sitemap URLs for source {SourceUrl}: childSitemaps={ChildCount}, pageUrls={PageCount}.",
                    sourceUrl,
                    canonicalizedSitemapChildCount,
                    canonicalizedSitemapPageCount);
            }

            if (droppedOutOfScopeCount > 0)
            {
                _logger.LogInformation(
                    "Dropped {Count} out-of-scope sitemap URLs for source {SourceUrl}.",
                    droppedOutOfScopeCount,
                    sourceUrl);
            }
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
        if (canonicalizedSitemapChildCount > 0 || canonicalizedSitemapPageCount > 0)
        {
            _logger.LogInformation(
                "Canonicalized sitemap URLs for source {SourceUrl}: childSitemaps={ChildCount}, pageUrls={PageCount}.",
                sourceUrl,
                canonicalizedSitemapChildCount,
                canonicalizedSitemapPageCount);
        }
        if (droppedOutOfScopeCount > 0)
        {
            _logger.LogInformation(
                "Dropped {Count} out-of-scope sitemap URLs for source {SourceUrl}.",
                droppedOutOfScopeCount,
                sourceUrl);
        }
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

    private async Task<HashSet<string>> ResolveAllowedScopeHostsAsync(
        CrawldbContext context,
        string sourceUrl,
        CancellationToken cancellationToken)
    {
        var scopeHosts = await LoadConfiguredSeedHostsAsync(context, cancellationToken);
        if (scopeHosts.Count > 0)
        {
            return scopeHosts;
        }

        var sourceHost = NormalizeHost(GetHost(sourceUrl));
        if (!string.IsNullOrWhiteSpace(sourceHost))
        {
            scopeHosts.Add(sourceHost);
        }

        return scopeHosts;
    }

    private async Task<HashSet<string>> LoadConfiguredSeedHostsAsync(
        CrawldbContext context,
        CancellationToken cancellationToken)
    {
        var hosts = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var connection = context.Database.GetDbConnection() as NpgsqlConnection;
        if (connection is null)
        {
            return hosts;
        }

        if (connection.State != ConnectionState.Open)
        {
            await connection.OpenAsync(cancellationToken);
        }

        const string globalConfigSql = """
            SELECT value::text
            FROM manager.global_setting
            WHERE key = 'crawler.global_config'
            LIMIT 1;
            """;

        await using (var globalConfigCmd = new NpgsqlCommand(globalConfigSql, connection))
        {
            var rawConfig = await globalConfigCmd.ExecuteScalarAsync(cancellationToken) as string;
            if (!string.IsNullOrWhiteSpace(rawConfig))
            {
                var config = JsonSerializer.Deserialize<WorkerGlobalConfigViewModel>(rawConfig, _jsonOptions);
                if (config is not null)
                {
                    foreach (var host in ExtractSeedHosts(config))
                    {
                        hosts.Add(host);
                    }
                }
            }
        }

        if (hosts.Count > 0)
        {
            return hosts;
        }

        const string seedUrlSql = """
            SELECT DISTINCT url
            FROM manager.seed_url
            WHERE url IS NOT NULL
              AND btrim(url) <> '';
            """;

        await using var seedCmd = new NpgsqlCommand(seedUrlSql, connection);
        await using var reader = await seedCmd.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            var url = reader.IsDBNull(0) ? null : reader.GetString(0);
            var host = NormalizeHost(GetHost(url));
            if (!string.IsNullOrWhiteSpace(host))
            {
                hosts.Add(host);
            }
        }

        return hosts;
    }

    private static IEnumerable<string> ExtractSeedHosts(WorkerGlobalConfigViewModel config)
    {
        var hosts = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        foreach (var entry in config.SeedEntries)
        {
            if (!entry.Enabled)
            {
                continue;
            }

            var host = NormalizeHost(GetHost(entry.Url));
            if (!string.IsNullOrWhiteSpace(host))
            {
                hosts.Add(host);
            }
        }

        foreach (var rawLine in (config.SeedUrlsText ?? string.Empty)
            .Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
        {
            var host = NormalizeHost(GetHost(rawLine));
            if (!string.IsNullOrWhiteSpace(host))
            {
                hosts.Add(host);
            }
        }

        return hosts;
    }

    private static List<string> FilterUrlsByScope(
        IEnumerable<string> urls,
        IReadOnlySet<string>? allowedScopeHosts)
    {
        if (allowedScopeHosts is null || allowedScopeHosts.Count == 0)
        {
            return urls.Distinct(StringComparer.Ordinal).ToList();
        }

        return urls
            .Where(url => IsUrlWithinScope(url, allowedScopeHosts))
            .Distinct(StringComparer.Ordinal)
            .ToList();
    }

    private static bool IsUrlWithinScope(string? url, IReadOnlySet<string>? allowedScopeHosts)
    {
        if (allowedScopeHosts is null || allowedScopeHosts.Count == 0)
        {
            return true;
        }

        var host = NormalizeHost(GetHost(url));
        if (string.IsNullOrWhiteSpace(host))
        {
            return false;
        }

        return allowedScopeHosts
            .Select(NormalizeHost)
            .Any(normalizedAllowed =>
                !string.IsNullOrWhiteSpace(normalizedAllowed)
                && string.Equals(host, normalizedAllowed, StringComparison.OrdinalIgnoreCase));
    }

    private static string? NormalizeHost(string? host)
    {
        if (string.IsNullOrWhiteSpace(host))
        {
            return null;
        }

        return host.Trim().Trim('.').ToLowerInvariant();
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
    public string? DaemonId { get; set; }
    public int? SiteId { get; set; }
    public int? SourcePageId { get; set; }
    public List<string>? DiscoveredUrls { get; set; }
    public List<string>? QueueEligibleDiscoveredUrls { get; set; }
    public List<string>? DiscoveredImageUrls { get; set; }
    public CrawlerDownloadResult? DownloadResult { get; set; }
}

public sealed class CrawlerDownloadResult
{
    public string? RequestedUrl { get; set; }
    public string? FinalUrl { get; set; }
    public int? StatusCode { get; set; }
    public string? ContentType { get; set; }
    public string? DataTypeCode { get; set; }
    public string? BinaryContentBase64 { get; set; }
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
