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

public class GraphWorkerDto
{
    public int Id { get; set; }
    public string Name { get; set; } = "";
    public string Status { get; set; } = "idle";
    public string? CurrentUrl { get; set; }
    public int? CurrentNodeId { get; set; }
}

/// <summary>
/// Container for complete graph data (nodes + links)
/// Used by D3.js graph visualization component
/// </summary>
public class GraphDataDto
{
    public List<GraphNodeDto> Nodes { get; set; } = new();
    public List<GraphLinkDto> Links { get; set; } = new();
    public List<GraphWorkerDto> Workers { get; set; } = new();
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
    public double RelevanceScore { get; set; }
    public bool HasKeywordEvidence { get; set; }
    public bool HasAllowedSuffixEvidence { get; set; }
    public bool HasSameHostEvidence { get; set; }
    public int? FrontierDepth { get; set; }
}

public class PageEvaluationSummaryDto
{
    public int TargetPageCount { get; set; } = 5000;
    public int TotalPages { get; set; }
    public int EvaluatedPages { get; set; }
    public int PositiveScorePages { get; set; }
    public int EstimatedRelevantPages { get; set; }
    public int KeywordEvidencePages { get; set; }
    public int AllowedSuffixEvidencePages { get; set; }
    public int SameHostEvidencePages { get; set; }
    public double AverageScore { get; set; }
    public double MedianScore { get; set; }
    public double TopScore { get; set; }
}

public class QueueTopItemViewModel
{
    public string Url { get; set; } = "";
    public string? SourceUrl { get; set; }
    public string State { get; set; } = "QUEUED";
    public int Priority { get; set; }
    public int Depth { get; set; }
    public DateTime DiscoveredAt { get; set; }
    public double RelevanceScore { get; set; }
    public bool HasKeywordEvidence { get; set; }
    public bool HasAllowedSuffixEvidence { get; set; }
    public bool HasSameHostEvidence { get; set; }
}

public class RelevanceReportRowDto
{
    public int Id { get; set; }
    public string Url { get; set; } = "";
    public string PageType { get; set; } = "";
    public int? HttpStatus { get; set; }
    public DateTime? AccessedTime { get; set; }
    public string? SiteDomain { get; set; }
    public double RelevanceScore { get; set; }
    public bool HasKeywordEvidence { get; set; }
    public bool HasAllowedSuffixEvidence { get; set; }
    public bool HasSameHostEvidence { get; set; }
    public int? FrontierDepth { get; set; }
}

public class PageBacklinkStatsDto
{
    public int InboundLinks { get; set; }
    public int OutboundLinks { get; set; }
    public double BacklinkScore { get; set; }
}

public class CollectedSiteSummaryDto
{
    public int SiteId { get; set; }
    public string Domain { get; set; } = "";
    public int PagesCollected { get; set; }
    public int OutboundLinks { get; set; }
    public int Backlinks { get; set; }
    public double RelevanceScore { get; set; }
    public double AveragePageScore { get; set; }
    public double MedianPageScore { get; set; }
    public double TopPageScore { get; set; }
    public DateTime? LastPageAccessed { get; set; }
    public string? RobotsContent { get; set; }
    public string? SitemapContent { get; set; }
}

/// <summary>
/// View model for worker status and metrics
/// Populated from crawler daemon API via manager service layer
/// </summary>
public class WorkerViewModel
{
    public int Id { get; set; }
    public string Name { get; set; } = "";
    public string Status { get; set; } = "Idle"; // Active, Idle, Paused, Stopped, Error
    public string? StatusReason { get; set; }
    public string? CurrentUrl { get; set; }
    public int PagesProcessed { get; set; }
    public int ErrorCount { get; set; }
    public DateTime? StartedAt { get; set; }
    public string Mode { get; set; } = "thread";
    public int? Pid { get; set; }
    public Dictionary<string, string> RuntimeConfig { get; set; } = new();
}

/// <summary>
/// Log entry for worker diagnostics and activity timeline.
/// </summary>
public class WorkerLogEntryViewModel
{
    public DateTime TimestampUtc { get; set; }
    public string Level { get; set; } = "Info";
    public string Message { get; set; } = "";
    public string DaemonId { get; set; } = "local-default";
    public int? WorkerId { get; set; }
}

public class WorkerThroughputPointViewModel
{
    public DateTime TimestampUtc { get; set; }
    public double Value { get; set; }
}

/// <summary>
/// Detailed worker view model used by the worker details page.
/// </summary>
public class WorkerDetailViewModel
{
    public WorkerViewModel Worker { get; set; } = new();
    public string? GroupName { get; set; }
    public Dictionary<string, string> RuntimeConfig { get; set; } = new();
    public List<WorkerLogEntryViewModel> RecentLogs { get; set; } = new();
}

/// <summary>
/// Global worker settings shared across worker instances.
/// </summary>
public class WorkerGlobalConfigViewModel
{
    public int MaxConcurrentWorkers { get; set; } = 4;
    public int RequestTimeoutSeconds { get; set; } = 20;
    public int CrawlDelayMilliseconds { get; set; } = 300;
    public bool RespectRobotsTxt { get; set; } = true;
    public string UserAgent { get; set; } = "EIPS-TT-Crawler/1.0";
    public string SeedUrlsText { get; set; } = "";
    public List<SeedEntryViewModel> SeedEntries { get; set; } = new();
    public string QueueMode { get; set; } = "both";
    public string StrategyMode { get; set; } = "balanced";
    public string ScoreFunction { get; set; } = "rendezvous";
    public double ScoreWeightPages { get; set; } = 1.0;
    public double ScoreWeightErrors { get; set; } = 1.0;
    public List<string> TopicKeywords { get; set; } = new();
    public string TopicKeywordsText { get; set; } = "";
    public List<string> RelevanceAllowedDomainSuffixes { get; set; } = new();
    public string RelevanceAllowedDomainSuffixesText { get; set; } = "";
    public double RelevanceSameHostBoost { get; set; } = 10.0;
    public double RelevanceAllowedSuffixBoost { get; set; } = 20.0;
    public double RelevanceKeywordBoost { get; set; } = 5.0;
    public double RelevanceDepthPenalty { get; set; } = 0.2;
    public int MaxFrontierInMemory { get; set; } = 50000;
    public bool AvoidDuplicatePathsAcrossDaemons { get; set; } = true;
}

/// <summary>
/// Seed URL configuration item.
/// </summary>
public class SeedEntryViewModel
{
    public string Url { get; set; } = "";
    public bool Enabled { get; set; } = true;
    public string Label { get; set; } = "";
}

/// <summary>
/// Group-level worker settings for segmented crawling behavior.
/// </summary>
public class WorkerGroupSettingsViewModel
{
    public int Id { get; set; }
    public string Name { get; set; } = "";
    public string Description { get; set; } = "";
    public bool Enabled { get; set; } = true;
    public int? MaxPagesPerWorker { get; set; }
    public int? RateLimitPerMinute { get; set; }
    public string? QueueMode { get; set; }
    public string? StrategyMode { get; set; }
    public List<string> TopicKeywords { get; set; } = new();
    public string TopicKeywordsText { get; set; } = "";
    public bool? AvoidDuplicatePathsAcrossDaemons { get; set; }
    public List<int> WorkerIds { get; set; } = new();
}

/// <summary>
/// Daemon status model exposed by crawler control API.
/// </summary>
public class DaemonStatusViewModel
{
    public bool Running { get; set; }
    public DateTime? StartedAt { get; set; }
    public string Mode { get; set; } = "single-instance";
    public int WorkerCount { get; set; }
    public int ActiveWorkers { get; set; }
    public int LocalProcessCount { get; set; }
    public DaemonFrontierSnapshotViewModel Frontier { get; set; } = new();
}

public class DaemonFrontierSnapshotViewModel
{
    public int InMemoryQueued { get; set; }
    public int KnownUrls { get; set; }
    public int LocalQueued { get; set; }
    public int ActiveLeases { get; set; }
    public int Tombstones { get; set; }
}

/// <summary>
/// Queue diagnostics for manager command dispatch pipeline.
/// </summary>
public class CommandQueueDiagnosticsViewModel
{
    public int RecentWindowMinutes { get; set; }
    public int Queued { get; set; }
    public int Dispatched { get; set; }
    public int Acknowledged { get; set; }
    public int Completed { get; set; }
    public int Failed { get; set; }
    public string? LastFailure { get; set; }
}

/// <summary>
/// Frontier claim response model.
/// </summary>
public class FrontierClaimViewModel
{
    public bool Claimed { get; set; }
    public int WorkerId { get; set; }
    public string? Url { get; set; }
    public string? LeaseToken { get; set; }
    public int LeaseTtlSeconds { get; set; }
    public string? Source { get; set; }
    public string? SourceUrl { get; set; }
}

/// <summary>
/// Frontier queue diagnostics returned by daemon API.
/// </summary>
public class FrontierStatusViewModel
{
    public int InMemoryQueued { get; set; }
    public int KnownUrls { get; set; }
    public int LocalQueued { get; set; }
    public int ActiveLeases { get; set; }
    public int Tombstones { get; set; }
    public int LeaseTtlSeconds { get; set; }
    public bool RelayEnabled { get; set; }
}

public class FrontierDequeueItemViewModel
{
    public int WorkerId { get; set; }
    public string? Url { get; set; }
    public string? LeaseToken { get; set; }
    public int LeaseTtlSeconds { get; set; }
    public string? Source { get; set; }
    public string? SourceUrl { get; set; }
}

public class FrontierDequeueBatchViewModel
{
    public string DaemonId { get; set; } = "local-default";
    public List<int> RequestedWorkerIds { get; set; } = new();
    public List<FrontierDequeueItemViewModel> Items { get; set; } = new();
    public int RemainingInMemory { get; set; }
    public int ActiveLeases { get; set; }
}

/// <summary>
/// Manager-relayed crawler telemetry event.
/// </summary>
public class CrawlerEventViewModel
{
    public DateTime TimestampUtc { get; set; }
    public string Type { get; set; } = "info";
    public string DaemonId { get; set; } = "local-default";
    public int? WorkerId { get; set; }
    public string PayloadJson { get; set; } = "{}";
}
