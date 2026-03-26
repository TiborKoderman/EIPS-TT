using System.Security.Cryptography;
using System.Text;
using ManagerApp.Data;
using Microsoft.EntityFrameworkCore;

namespace ManagerApp.Services;

public sealed class CrawlerRelayService
{
    private readonly IDbContextFactory<CrawldbContext> _contextFactory;
    private readonly ILogger<CrawlerRelayService> _logger;

    public CrawlerRelayService(
        IDbContextFactory<CrawldbContext> contextFactory,
        ILogger<CrawlerRelayService> logger)
    {
        _contextFactory = contextFactory;
        _logger = logger;
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

    public Task IngestEventAsync(CrawlerEventMessage message)
    {
        _logger.LogInformation(
            "[crawler-event] type={Type} daemon={DaemonId} worker={WorkerId} payload={Payload}",
            message.Type,
            message.DaemonId,
            message.WorkerId,
            message.Payload);
        return Task.CompletedTask;
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