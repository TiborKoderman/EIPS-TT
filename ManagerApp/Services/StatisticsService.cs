using Microsoft.EntityFrameworkCore;
using ManagerApp.Data;
using ManagerApp.Models;

namespace ManagerApp.Services;

/// <summary>
/// Implementation of statistics service that queries the database for crawler metrics
/// </summary>
public class StatisticsService : IStatisticsService
{
    private readonly IDbContextFactory<CrawldbContext> _dbContextFactory;
    private static readonly string[] BinaryTypeOrder = ["PDF", "DOC", "DOCX", "PPT", "PPTX"];
    private static readonly string[] ImageTypeOrder = ["JPG", "PNG", "WEBP", "GIF", "SVG", "BMP", "TIFF", "ICO", "AVIF", "OTHER"];
    private static readonly Dictionary<string, string> BinaryTypeByExtension = new(StringComparer.OrdinalIgnoreCase)
    {
        [".pdf"] = "PDF",
        [".docx"] = "DOCX",
        [".pptx"] = "PPTX",
        [".doc"] = "DOC",
        [".ppt"] = "PPT",
    };
    private static readonly Dictionary<string, string> ImageTypeByExtension = new(StringComparer.OrdinalIgnoreCase)
    {
        [".jpeg"] = "JPG",
        [".jpg"] = "JPG",
        [".png"] = "PNG",
        [".webp"] = "WEBP",
        [".gif"] = "GIF",
        [".svg"] = "SVG",
        [".bmp"] = "BMP",
        [".tiff"] = "TIFF",
        [".tif"] = "TIFF",
        [".ico"] = "ICO",
        [".avif"] = "AVIF",
    };
    private static readonly char[] CandidateTrimChars = ['"', '\'', ')', ']', '}', '>', ';'];

    public StatisticsService(IDbContextFactory<CrawldbContext> dbContextFactory)
    {
        _dbContextFactory = dbContextFactory;
    }

    /// <summary>
    /// Get comprehensive statistics for the dashboard
    /// Queries run in parallel using separate DbContext instances for thread safety
    /// </summary>
    public async Task<StatisticsViewModel> GetStatisticsAsync()
    {
        var totalSitesTask = GetTotalSitesAsync();
        var totalPagesTask = GetTotalPagesAsync();
        var htmlPagesTask = GetHtmlPagesAsync();
        var duplicatesTask = GetDuplicateCountAsync();
        var imagesTask = GetImageCountAsync();
        var avgImagesTask = GetAverageImagesPerPageAsync();
        var binaryCountsTask = GetBinaryFileCountsAsync();

        await Task.WhenAll(
            totalSitesTask, totalPagesTask, htmlPagesTask,
            duplicatesTask, imagesTask, avgImagesTask, binaryCountsTask
        );

        return new StatisticsViewModel
        {
            TotalSites = await totalSitesTask,
            TotalPages = await totalPagesTask,
            HtmlPages = await htmlPagesTask,
            DuplicatePages = await duplicatesTask,
            TotalImages = await imagesTask,
            AverageImagesPerPage = await avgImagesTask,
            BinaryFileCounts = await binaryCountsTask,
            LastUpdated = DateTime.UtcNow
        };
    }

    public async Task<Dictionary<string, int>> GetPageTypeCountsAsync()
    {
        await using var context = await _dbContextFactory.CreateDbContextAsync();

        return await context.Pages
            .GroupBy(p => p.PageTypeCode)
            .Select(g => new { Type = g.Key ?? "Unknown", Count = g.Count() })
            .ToDictionaryAsync(x => x.Type, x => x.Count);
    }

    public async Task<int> GetDuplicateCountAsync()
    {
        await using var context = await _dbContextFactory.CreateDbContextAsync();
        return await context.Pages.CountAsync(p => p.PageTypeCode == "DUPLICATE");
    }

    public async Task<int> GetImageCountAsync()
    {
        await using var context = await _dbContextFactory.CreateDbContextAsync();
        return await context.Images.CountAsync();
    }

    public async Task<Dictionary<string, int>> GetBinaryFileCountsAsync()
    {
        await using var context = await _dbContextFactory.CreateDbContextAsync();

        var counts = BinaryTypeOrder.ToDictionary(type => type, _ => 0, StringComparer.OrdinalIgnoreCase);

        var binaryPages = await context.Pages
            .Where(page => page.PageTypeCode == "BINARY")
            .Select(page => new { page.Id, page.Url })
            .ToListAsync();

        var explicitTypesByPageId = await (
            from pageData in context.PageData
            join page in context.Pages on pageData.PageId equals page.Id
            where page.PageTypeCode == "BINARY" && pageData.DataTypeCode != null
            select new { page.Id, pageData.DataTypeCode })
            .GroupBy(item => item.Id)
            .Select(group => new
            {
                PageId = group.Key,
                DataTypeCode = group.Select(item => item.DataTypeCode!).FirstOrDefault(),
            })
            .ToDictionaryAsync(
                item => item.PageId,
                item => NormalizeBinaryType(item.DataTypeCode),
                cancellationToken: CancellationToken.None);

        foreach (var page in binaryPages)
        {
            if (explicitTypesByPageId.TryGetValue(page.Id, out var explicitType)
                && !string.IsNullOrWhiteSpace(explicitType)
                && counts.ContainsKey(explicitType))
            {
                counts[explicitType]++;
                continue;
            }

            var inferredType = InferBinaryTypeFromUrl(page.Url);
            if (!string.IsNullOrWhiteSpace(inferredType) && counts.ContainsKey(inferredType))
            {
                counts[inferredType]++;
            }
        }

        return BinaryTypeOrder.ToDictionary(type => type, type => counts[type]);
    }

    public async Task<Dictionary<string, int>> GetImageFileCountsAsync()
    {
        await using var context = await _dbContextFactory.CreateDbContextAsync();

        var counts = ImageTypeOrder.ToDictionary(type => type, _ => 0, StringComparer.OrdinalIgnoreCase);

        var binaryUrls = await context.Pages
            .Where(page => page.PageTypeCode == "BINARY" && page.Url != null)
            .Select(page => page.Url!)
            .ToListAsync();

        foreach (var url in binaryUrls)
        {
            var inferredType = InferImageTypeFromUrl(url);
            if (!string.IsNullOrWhiteSpace(inferredType) && counts.ContainsKey(inferredType))
            {
                counts[inferredType]++;
            }
            else
            {
                counts["OTHER"]++;
            }
        }

        return ImageTypeOrder.ToDictionary(type => type, type => counts[type]);
    }

    public async Task<double> GetAverageImagesPerPageAsync()
    {
        await using var context = await _dbContextFactory.CreateDbContextAsync();

        var totalPages = await context.Pages.CountAsync(p => p.PageTypeCode == "HTML");
        if (totalPages == 0) return 0;

        var totalImages = await context.Images.CountAsync();
        return Math.Round((double)totalImages / totalPages, 2);
    }

    private async Task<int> GetTotalSitesAsync()
    {
        await using var context = await _dbContextFactory.CreateDbContextAsync();
        return await context.Sites.CountAsync();
    }

    private async Task<int> GetTotalPagesAsync()
    {
        await using var context = await _dbContextFactory.CreateDbContextAsync();
        return await context.Pages.CountAsync();
    }

    private async Task<int> GetHtmlPagesAsync()
    {
        await using var context = await _dbContextFactory.CreateDbContextAsync();
        return await context.Pages.CountAsync(p => p.PageTypeCode == "HTML");
    }

    private static string? NormalizeBinaryType(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return null;
        }

        var normalized = value.Trim().ToUpperInvariant();
        return BinaryTypeOrder.Contains(normalized, StringComparer.OrdinalIgnoreCase)
            ? normalized
            : null;
    }

    private static string? InferBinaryTypeFromUrl(string? url)
    {
        return InferTypeFromUrl(url, BinaryTypeByExtension);
    }

    private static string? InferImageTypeFromUrl(string? url)
    {
        return InferTypeFromUrl(url, ImageTypeByExtension);
    }

    private static string? InferTypeFromUrl(string? url, IReadOnlyDictionary<string, string> typeMap)
    {
        if (string.IsNullOrWhiteSpace(url))
        {
            return null;
        }

        foreach (var candidate in ExtractUrlCandidates(url))
        {
            var normalizedCandidate = NormalizeCandidate(candidate);
            if (string.IsNullOrWhiteSpace(normalizedCandidate))
            {
                continue;
            }

            foreach (var (extension, fileType) in typeMap.OrderByDescending(item => item.Key.Length))
            {
                if (normalizedCandidate.EndsWith(extension, StringComparison.OrdinalIgnoreCase))
                {
                    return fileType;
                }
            }
        }

        return null;
    }

    private static IEnumerable<string> ExtractUrlCandidates(string url)
    {
        if (Uri.TryCreate(url, UriKind.Absolute, out var absoluteUri))
        {
            var candidates = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            AddCandidate(candidates, absoluteUri.AbsolutePath);
            AddCandidate(candidates, GetLastPathSegment(absoluteUri.AbsolutePath));

            var query = absoluteUri.Query.TrimStart('?');
            if (!string.IsNullOrWhiteSpace(query))
            {
                foreach (var pair in query.Split('&', StringSplitOptions.RemoveEmptyEntries))
                {
                    var separatorIndex = pair.IndexOf('=');
                    if (separatorIndex >= 0 && separatorIndex < pair.Length - 1)
                    {
                        AddCandidate(candidates, DecodeUrlFragment(pair[(separatorIndex + 1)..]));
                    }
                    else
                    {
                        AddCandidate(candidates, DecodeUrlFragment(pair));
                    }
                }
            }

            return candidates;
        }

        var withoutFragment = url.Split('#', 2, StringSplitOptions.None)[0];
        var withoutQuery = withoutFragment.Split('?', 2, StringSplitOptions.None)[0];
        return [withoutFragment, withoutQuery, GetLastPathSegment(withoutQuery)];
    }

    private static void AddCandidate(ISet<string> candidates, string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return;
        }

        candidates.Add(value);
        candidates.Add(GetLastPathSegment(value));
    }

    private static string GetLastPathSegment(string value)
    {
        var normalized = value.Replace('\\', '/');
        var slashIndex = normalized.LastIndexOf('/');
        return slashIndex >= 0 && slashIndex < normalized.Length - 1
            ? normalized[(slashIndex + 1)..]
            : normalized;
    }

    private static string DecodeUrlFragment(string value)
    {
        try
        {
            return Uri.UnescapeDataString(value.Replace('+', ' '));
        }
        catch
        {
            return value;
        }
    }

    private static string NormalizeCandidate(string value)
    {
        var trimmed = value.Trim().Trim(CandidateTrimChars);
        var withoutFragment = trimmed.Split('#', 2, StringSplitOptions.None)[0];
        return withoutFragment.Split('?', 2, StringSplitOptions.None)[0];
    }
}
