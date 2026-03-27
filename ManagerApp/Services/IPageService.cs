using ManagerApp.Data;
using ManagerApp.Models;

namespace ManagerApp.Services;

/// <summary>
/// Service for page search, filtering, and details retrieval
/// </summary>
public interface IPageService
{
    /// <summary>
    /// Search pages with fuzzy matching and filtering
    /// </summary>
    /// <param name="searchTerm">Search term to match against URL and content</param>
    /// <param name="pageType">Filter by page type (HTML, BINARY, DUPLICATE, FRONTIER, or ALL)</param>
    /// <param name="skip">Number of records to skip (for pagination)</param>
    /// <param name="take">Number of records to take (page size)</param>
    Task<List<PageSearchDto>> SearchPagesAsync(string? searchTerm, string? pageType, string? orderBy = null, int skip = 0, int take = 50);

    /// <summary>
    /// Get total count of search results (for pagination)
    /// </summary>
    Task<int> GetSearchResultsCountAsync(string? searchTerm, string? pageType);

    /// <summary>
    /// Compute a classifier-free relevance evaluation summary over the full collected database.
    /// </summary>
    Task<PageEvaluationSummaryDto> GetEvaluationSummaryAsync();

    /// <summary>
    /// Return top queue URLs that are not currently claimed/processing, enriched with relevance score.
    /// </summary>
    Task<List<QueueTopItemViewModel>> GetTopQueueItemsAsync(int take = 20);

    /// <summary>
    /// Return a relevance-focused report over collected pages.
    /// </summary>
    Task<List<RelevanceReportRowDto>> GetRelevanceReportAsync(string? searchTerm, int skip = 0, int take = 100);

    /// <summary>
    /// Get full page details including site, images, and binary data
    /// </summary>
    Task<Page?> GetPageDetailsAsync(int pageId);

    /// <summary>
    /// Get backlink and outgoing link stats for a page.
    /// </summary>
    Task<PageBacklinkStatsDto> GetPageBacklinkStatsAsync(int pageId);

    /// <summary>
    /// Get collected sites with aggregate crawl/link/relevance metrics.
    /// </summary>
    Task<List<CollectedSiteSummaryDto>> GetCollectedSitesAsync(string? searchTerm, string? orderBy = null, int skip = 0, int take = 100);

    /// <summary>
    /// Get details for a specific collected site.
    /// </summary>
    Task<CollectedSiteSummaryDto?> GetCollectedSiteDetailsAsync(int siteId);
}
