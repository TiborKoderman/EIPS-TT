using ManagerApp.Models;

namespace ManagerApp.Services;

/// <summary>
/// Service for retrieving graph data for D3.js visualization
/// </summary>
public interface IGraphService
{
    /// <summary>
    /// Get complete graph data (nodes and links) formatted for D3.js
    /// </summary>
    /// <param name="limit">Optional limit on number of nodes to retrieve (for performance)</param>
    Task<GraphDataDto> GetGraphDataAsync(int? limit = null);

    /// <summary>
    /// Get subgraph centered on a specific page
    /// </summary>
    /// <param name="pageId">ID of the central page</param>
    /// <param name="depth">Depth of traversal (number of hops from central node)</param>
    Task<GraphDataDto> GetGraphDataForPageAsync(int pageId, int depth = 2);

    /// <summary>
    /// Get incoming link counts for all pages (used for node sizing)
    /// </summary>
    Task<Dictionary<int, int>> GetIncomingLinkCountsAsync();

    /// <summary>
    /// Get aggregated site graph data with per-site score/page metrics and inter-site edge counts.
    /// </summary>
    Task<SiteGraphDataDto> GetSiteGraphDataAsync();
}
