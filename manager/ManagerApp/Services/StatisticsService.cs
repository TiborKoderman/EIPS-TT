using Microsoft.EntityFrameworkCore;
using ManagerApp.Data;
using ManagerApp.Models;

namespace ManagerApp.Services;

/// <summary>
/// Implementation of statistics service that queries the database for crawler metrics
/// </summary>
public class StatisticsService : IStatisticsService
{
    private readonly CrawldbContext _context;

    public StatisticsService(CrawldbContext context)
    {
        _context = context;
    }

    /// <summary>
    /// Get comprehensive statistics for the dashboard
    /// Queries run in parallel for optimal performance
    /// </summary>
    public async Task<StatisticsViewModel> GetStatisticsAsync()
    {
        // Run queries in parallel for performance
        var totalSitesTask = _context.Sites.CountAsync();
        var totalPagesTask = _context.Pages.CountAsync();
        var htmlPagesTask = _context.Pages.CountAsync(p => p.PageTypeCode == "HTML");
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
        return await _context.Pages
            .GroupBy(p => p.PageTypeCode)
            .Select(g => new { Type = g.Key ?? "Unknown", Count = g.Count() })
            .ToDictionaryAsync(x => x.Type, x => x.Count);
    }

    public async Task<int> GetDuplicateCountAsync()
    {
        return await _context.Pages.CountAsync(p => p.PageTypeCode == "DUPLICATE");
    }

    public async Task<int> GetImageCountAsync()
    {
        return await _context.Images.CountAsync();
    }

    public async Task<Dictionary<string, int>> GetBinaryFileCountsAsync()
    {
        return await _context.PageData
            .GroupBy(pd => pd.DataTypeCode)
            .Select(g => new { Type = g.Key ?? "Unknown", Count = g.Count() })
            .ToDictionaryAsync(x => x.Type, x => x.Count);
    }

    public async Task<double> GetAverageImagesPerPageAsync()
    {
        var totalPages = await _context.Pages.CountAsync(p => p.PageTypeCode == "HTML");
        if (totalPages == 0) return 0;

        var totalImages = await _context.Images.CountAsync();
        return Math.Round((double)totalImages / totalPages, 2);
    }
}
