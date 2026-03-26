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
    private const int MaxRecentEvents = 400;

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
        var rawUrl = (request.RawUrl ?? string.Empty).Trim();
        var finalUrl = (request.DownloadResult?.FinalUrl ?? rawUrl).Trim();
        var canonicalUrl = string.IsNullOrWhiteSpace(finalUrl) ? rawUrl : finalUrl;
        if (string.IsNullOrWhiteSpace(canonicalUrl))
        {
            throw new InvalidOperationException("rawUrl/finalUrl must be provided.");
        }

        var pageTypeCode = string.IsNullOrWhiteSpace(request.DownloadResult?.PageTypeCode)
            ? "HTML"
            : request.DownloadResult!.PageTypeCode!.Trim().ToUpperInvariant();
        var html = request.DownloadResult?.HtmlContent;
        var contentHash = string.IsNullOrWhiteSpace(html) ? null : Sha256Hex(html);

        await using var context = await _contextFactory.CreateDbContextAsync(cancellationToken);

        var existingPage = await context.Pages
            .FirstOrDefaultAsync(
                page => page.CanonicalUrl == canonicalUrl || page.Url == canonicalUrl,
                cancellationToken);

        var status = "inserted";
        Page targetPage;

        if (existingPage != null)
        {
            existingPage.AccessedTime = accessedTime;
            existingPage.HttpStatusCode = request.DownloadResult?.StatusCode;
            if (pageTypeCode == "HTML" && !string.IsNullOrWhiteSpace(html))
            {
                existingPage.HtmlContent = html;
                existingPage.ContentHash = contentHash;
            }
            targetPage = existingPage;
            status = "updated";
        }
        else
        {
            var siteId = await ResolveSiteIdAsync(context, canonicalUrl, request.SiteId, cancellationToken);
            targetPage = new Page
            {
                SiteId = siteId,
                PageTypeCode = pageTypeCode,
                Url = canonicalUrl,
                CanonicalUrl = canonicalUrl,
                HtmlContent = pageTypeCode == "HTML" ? html : null,
                HttpStatusCode = request.DownloadResult?.StatusCode,
                AccessedTime = accessedTime,
                ContentHash = pageTypeCode == "HTML" ? contentHash : null,
            };
            context.Pages.Add(targetPage);
            status = "inserted";
        }

        await context.SaveChangesAsync(cancellationToken);

        if (request.SourcePageId.HasValue && request.SourcePageId.Value > 0)
        {
            await context.Database.ExecuteSqlInterpolatedAsync(
                $"INSERT INTO crawldb.link(from_page, to_page) VALUES ({request.SourcePageId.Value}, {targetPage.Id}) ON CONFLICT DO NOTHING",
                cancellationToken);
        }

        if (!string.IsNullOrWhiteSpace(request.DownloadResult?.DataTypeCode))
        {
            var hasPageData = await context.PageData
                .AnyAsync(pd => pd.PageId == targetPage.Id && pd.DataTypeCode == request.DownloadResult!.DataTypeCode, cancellationToken);
            if (!hasPageData)
            {
                context.PageData.Add(new PageDatum
                {
                    PageId = targetPage.Id,
                    DataTypeCode = request.DownloadResult!.DataTypeCode,
                    Data = null,
                });
                await context.SaveChangesAsync(cancellationToken);
            }
        }

        return new CrawlerIngestResponse
        {
            PageId = targetPage.Id,
            Status = status,
            CanonicalUrl = canonicalUrl,
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

            await PersistLogEntryIfApplicableAsync(connection, envelope);
            await PersistMetricEntriesIfApplicableAsync(connection, envelope);
            await RunRetentionCleanupIfNeededAsync(connection);
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Failed to persist crawler observability event.");
        }
    }

    private static async Task PersistLogEntryIfApplicableAsync(NpgsqlConnection connection, CrawlerEventEnvelope envelope)
    {
        if (!string.Equals(envelope.Type, "worker-log", StringComparison.OrdinalIgnoreCase)
            && !string.Equals(envelope.Type, "error", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        var level = "Info";
        var message = envelope.PayloadJson;

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
            }
        }
        catch
        {
            // Keep defaults from payload JSON.
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

    private static async Task<int?> ResolveSiteIdAsync(
        CrawldbContext context,
        string canonicalUrl,
        int? explicitSiteId,
        CancellationToken cancellationToken)
    {
        if (explicitSiteId.HasValue && explicitSiteId.Value > 0)
        {
            return explicitSiteId;
        }

        if (!Uri.TryCreate(canonicalUrl, UriKind.Absolute, out var uri) || string.IsNullOrWhiteSpace(uri.Host))
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
}

public sealed class CrawlerIngestRequest
{
    public string? RawUrl { get; set; }
    public int? SiteId { get; set; }
    public int? SourcePageId { get; set; }
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
}

public sealed class CrawlerIngestResponse
{
    public int PageId { get; set; }
    public string Status { get; set; } = "inserted";
    public string CanonicalUrl { get; set; } = string.Empty;
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