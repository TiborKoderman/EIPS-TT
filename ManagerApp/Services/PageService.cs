using Microsoft.EntityFrameworkCore;
using ManagerApp.Data;
using ManagerApp.Models;

namespace ManagerApp.Services;

/// <summary>
/// Implementation of page service for searching and retrieving page data
/// </summary>
public class PageService : IPageService
{
    private readonly CrawldbContext _context;

    public PageService(CrawldbContext context)
    {
        _context = context;
    }

    /// <summary>
    /// Search pages with fuzzy matching on URL and HTML content
    /// Supports filtering by page type and pagination
    /// </summary>
    public async Task<List<PageSearchDto>> SearchPagesAsync(
        string? searchTerm,
        string? pageType,
        int skip = 0,
        int take = 50)
    {
        var query = _context.Pages.AsQueryable();

        // Filter by page type if specified
        if (!string.IsNullOrWhiteSpace(pageType) && pageType != "ALL")
        {
            var normalizedType = pageType.Trim().ToUpperInvariant();
            if (normalizedType == "DUPLICATE")
            {
                query = query.Where(p => p.DuplicateOfPageId != null);
            }
            else
            {
                query = query.Where(p => p.PageTypeCode != null && p.PageTypeCode.ToUpper() == normalizedType);
            }
        }

        // Fuzzy search on URL and HTML content (case-insensitive)
        if (!string.IsNullOrWhiteSpace(searchTerm))
        {
            var pattern = $"%{searchTerm.Trim()}%";
            query = query.Where(p =>
                (p.Url != null && EF.Functions.ILike(p.Url, pattern)) ||
                (p.HtmlContent != null && EF.Functions.ILike(p.HtmlContent, pattern))
            );
        }

        // Execute query with pagination, ordered by most recently accessed
        return await query
            .Include(p => p.Site)
            .OrderByDescending(p => p.AccessedTime)
            .Skip(skip)
            .Take(take)
            .Select(p => new PageSearchDto
            {
                Id = p.Id,
                Url = p.Url ?? "",
                PageType = p.PageTypeCode ?? "HTML",
                HttpStatus = p.HttpStatusCode,
                AccessedTime = p.AccessedTime,
                SiteDomain = p.Site != null ? p.Site.Domain : null,
                IsDuplicate = p.DuplicateOfPageId != null
            })
            .ToListAsync();
    }

    /// <summary>
    /// Get total count of search results for pagination
    /// Uses same filtering logic as SearchPagesAsync
    /// </summary>
    public async Task<int> GetSearchResultsCountAsync(string? searchTerm, string? pageType)
    {
        var query = _context.Pages.AsQueryable();

        if (!string.IsNullOrWhiteSpace(pageType) && pageType != "ALL")
        {
            var normalizedType = pageType.Trim().ToUpperInvariant();
            if (normalizedType == "DUPLICATE")
            {
                query = query.Where(p => p.DuplicateOfPageId != null);
            }
            else
            {
                query = query.Where(p => p.PageTypeCode != null && p.PageTypeCode.ToUpper() == normalizedType);
            }
        }

        if (!string.IsNullOrWhiteSpace(searchTerm))
        {
            var pattern = $"%{searchTerm.Trim()}%";
            query = query.Where(p =>
                (p.Url != null && EF.Functions.ILike(p.Url, pattern)) ||
                (p.HtmlContent != null && EF.Functions.ILike(p.HtmlContent, pattern))
            );
        }

        return await query.CountAsync();
    }

    /// <summary>
    /// Get complete page details with all related data
    /// Includes site, images, and binary data (page_data table)
    /// </summary>
    public async Task<Page?> GetPageDetailsAsync(int pageId)
    {
        return await _context.Pages
            .Include(p => p.Site)
            .Include(p => p.Images)
            .Include(p => p.PageData)
            .Include(p => p.PageTypeCodeNavigation)
            .FirstOrDefaultAsync(p => p.Id == pageId);
    }
}
