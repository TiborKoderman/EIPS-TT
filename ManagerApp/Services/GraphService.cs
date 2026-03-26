using Microsoft.EntityFrameworkCore;
using ManagerApp.Data;
using ManagerApp.Models;

namespace ManagerApp.Services;

/// <summary>
/// Implementation of graph service that transforms database link data into D3.js format
/// </summary>
public class GraphService : IGraphService
{
    private readonly CrawldbContext _context;

    public GraphService(CrawldbContext context)
    {
        _context = context;
    }

    /// <summary>
    /// Get graph data formatted for D3.js visualization
    /// Nodes are pages, edges are links between pages
    /// Node size is determined by incoming link count
    /// </summary>
    public async Task<GraphDataDto> GetGraphDataAsync(int? limit = null)
    {
        // Get incoming link counts for node sizing
        var incomingCounts = await GetIncomingLinkCountsAsync();

        // Get pages with stable URLs (or limited set for performance).
        var query = _context.Pages
            .Where(p => p.Url != null)
            .OrderByDescending(p => p.Id)
            .Include(p => p.Site)
            .Select(p => new GraphNodeDto
            {
                Id = p.Id,
                Url = p.Url ?? "",
                Domain = p.Site != null ? p.Site.Domain ?? "unknown" : "unknown",
                PageType = p.PageTypeCode ?? "HTML",
                Size = 1 // Will be updated with incoming count below
            });

        if (limit.HasValue)
        {
            query = query.Take(limit.Value);
        }

        var nodes = await query.ToListAsync();

        // Update node sizes based on incoming links (more incoming links = bigger node)
        foreach (var node in nodes)
        {
            node.Size = incomingCounts.GetValueOrDefault(node.Id, 1);
        }

        // Get node IDs for filtering links
        var nodeIds = nodes.Select(n => n.Id).ToHashSet();

        // Get links between these nodes
        // Use the many-to-many relationship via FromPages/ToPages
        var links = await _context.Pages
            .Where(p => nodeIds.Contains(p.Id))
            .SelectMany(p => p.ToPages
                .Where(tp => nodeIds.Contains(tp.Id))
                .Select(tp => new GraphLinkDto
                {
                    Source = p.Id,
                    Target = tp.Id
                }))
            .Distinct()
            .ToListAsync();

        return new GraphDataDto
        {
            Nodes = nodes,
            Links = links
        };
    }

    /// <summary>
    /// Get subgraph centered on a specific page
    /// TODO: Implement breadth-first traversal from page_id up to specified depth
    /// For now, returns all connected nodes (limited to 100)
    /// </summary>
    public async Task<GraphDataDto> GetGraphDataForPageAsync(int pageId, int depth = 2)
    {
        // TODO: Implement BFS traversal
        // For now, return limited graph
        return await GetGraphDataAsync(100);
    }

    /// <summary>
    /// Calculate incoming link count for each page
    /// Used to size nodes proportionally to their importance (more incoming links = bigger node)
    /// </summary>
    public async Task<Dictionary<int, int>> GetIncomingLinkCountsAsync()
    {
        // Count how many pages link TO each page (incoming links)
        var counts = await _context.Pages
            .SelectMany(p => p.ToPages.Select(tp => tp.Id))
            .GroupBy(pageId => pageId)
            .Select(g => new { PageId = g.Key, Count = g.Count() })
            .ToDictionaryAsync(x => x.PageId, x => x.Count);

        return counts;
    }

}
