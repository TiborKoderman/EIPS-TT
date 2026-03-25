namespace ManagerApp.Models;

/// <summary>
/// View model for displaying comprehensive statistics on the dashboard
/// </summary>
public class StatisticsViewModel
{
    public int TotalSites { get; set; }
    public int TotalPages { get; set; }
    public int HtmlPages { get; set; }
    public int DuplicatePages { get; set; }
    public int TotalImages { get; set; }
    public double AverageImagesPerPage { get; set; }
    public Dictionary<string, int> BinaryFileCounts { get; set; } = new();
    public DateTime LastUpdated { get; set; }
}

/// <summary>
/// DTO for D3.js graph node visualization
/// Represents a single page in the graph
/// </summary>
public class GraphNodeDto
{
    public int Id { get; set; }
    public string Url { get; set; } = "";
    public string Domain { get; set; } = "";
    public string PageType { get; set; } = "HTML";
    public int Size { get; set; } = 1; // Based on incoming links count
}

/// <summary>
/// DTO for D3.js graph edge/link visualization
/// Represents a link from one page to another
/// </summary>
public class GraphLinkDto
{
    public int Source { get; set; }  // FromPage ID
    public int Target { get; set; }  // ToPage ID
}

/// <summary>
/// Container for complete graph data (nodes + links)
/// Used by D3.js graph visualization component
/// </summary>
public class GraphDataDto
{
    public List<GraphNodeDto> Nodes { get; set; } = new();
    public List<GraphLinkDto> Links { get; set; } = new();
}

/// <summary>
/// DTO for page search results
/// Lightweight representation of a page for list display
/// </summary>
public class PageSearchDto
{
    public int Id { get; set; }
    public string Url { get; set; } = "";
    public string PageType { get; set; } = "";
    public int? HttpStatus { get; set; }
    public DateTime? AccessedTime { get; set; }
    public string? SiteDomain { get; set; }
    public bool IsDuplicate { get; set; }
}

/// <summary>
/// View model for worker status and metrics
/// Currently uses mock data - will be populated from Python API when implemented
/// </summary>
public class WorkerViewModel
{
    public int Id { get; set; }
    public string Name { get; set; } = "";
    public string Status { get; set; } = "Idle"; // Active, Idle, Paused, Stopped, Error
    public string? CurrentUrl { get; set; }
    public int PagesProcessed { get; set; }
    public int ErrorCount { get; set; }
    public DateTime? StartedAt { get; set; }
}
