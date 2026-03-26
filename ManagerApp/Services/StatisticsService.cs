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

        return await context.PageData
            .GroupBy(pd => pd.DataTypeCode)
            .Select(g => new { Type = g.Key ?? "Unknown", Count = g.Count() })
            .ToDictionaryAsync(x => x.Type, x => x.Count);
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
}
