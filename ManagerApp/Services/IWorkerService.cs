using ManagerApp.Models;

namespace ManagerApp.Services;

/// <summary>
/// Service for managing crawler workers
/// Daemon-based control surface backed by Python crawler API
/// </summary>
public interface IWorkerService
{
    /// <summary>
    /// Last API error captured by worker service calls.
    /// </summary>
    string? LastError { get; }

    /// <summary>
    /// Get current daemon status.
    /// </summary>
    Task<DaemonStatusViewModel?> GetDaemonStatusAsync();

    /// <summary>
    /// Start crawler daemon process.
    /// </summary>
    Task<bool> StartDaemonAsync();

    /// <summary>
    /// Stop crawler daemon process.
    /// </summary>
    Task<bool> StopDaemonAsync();

    /// <summary>
    /// Reload daemon workers.
    /// </summary>
    Task<bool> ReloadDaemonAsync();

    /// <summary>
    /// Spawn a new worker inside the daemon.
    /// </summary>
    Task<WorkerViewModel?> SpawnWorkerAsync(
        string? name = null,
        int? daemonGroupId = null,
        string? mode = null,
        IReadOnlyList<string>? seedUrls = null,
        string? daemonId = null);

    /// <summary>
    /// Get list of all workers with their current status
    /// </summary>
    Task<List<WorkerViewModel>> GetAllWorkersAsync();

    /// <summary>
    /// Get details for a specific worker
    /// </summary>
    Task<WorkerViewModel?> GetWorkerAsync(int id, string? daemonId = null);

    /// <summary>
    /// Start a worker
    /// </summary>
    /// <returns>True if successful, false otherwise</returns>
    Task<bool> StartWorkerAsync(int id, string? daemonId = null);

    /// <summary>
    /// Stop a worker
    /// </summary>
    /// <returns>True if successful, false otherwise</returns>
    Task<bool> StopWorkerAsync(int id, string? daemonId = null);

    /// <summary>
    /// Pause a worker
    /// </summary>
    /// <returns>True if successful, false otherwise</returns>
    Task<bool> PauseWorkerAsync(int id, string? daemonId = null);

    /// <summary>
    /// Get worker status counts for pie chart display
    /// Returns dictionary with status names as keys and counts as values
    /// </summary>
    Task<Dictionary<string, int>> GetWorkerStatusCountsAsync();

    /// <summary>
    /// Get full details for a single worker.
    /// </summary>
    Task<WorkerDetailViewModel?> GetWorkerDetailAsync(int id, string? daemonId = null);

    /// <summary>
    /// Get global worker configuration.
    /// </summary>
    Task<WorkerGlobalConfigViewModel> GetGlobalConfigAsync();

    /// <summary>
    /// Save global worker configuration.
    /// </summary>
    Task SaveGlobalConfigAsync(WorkerGlobalConfigViewModel config);

    /// <summary>
    /// Get all worker groups and their settings.
    /// </summary>
    Task<List<WorkerGroupSettingsViewModel>> GetWorkerGroupsAsync();

    /// <summary>
    /// Save a worker group's settings.
    /// </summary>
    Task<bool> SaveWorkerGroupAsync(WorkerGroupSettingsViewModel group);

    /// <summary>
    /// Add one seed URL to daemon/worker frontier.
    /// </summary>
    Task<bool> AddSeedAsync(string url, int? workerId = null);

    /// <summary>
    /// Claim one URL from frontier for a worker.
    /// </summary>
    Task<FrontierClaimViewModel?> ClaimFrontierUrlAsync(int workerId);

    /// <summary>
    /// Complete claimed frontier URL processing.
    /// </summary>
    Task<bool> CompleteFrontierUrlAsync(int workerId, string url, string? leaseToken, string status = "completed");

    /// <summary>
    /// Prune URL from worker-local frontier queue.
    /// </summary>
    Task<bool> PruneFrontierUrlAsync(int workerId, string url, string reason = "server-conflict");

    /// <summary>
    /// Get frontier diagnostics snapshot.
    /// </summary>
    Task<FrontierStatusViewModel?> GetFrontierStatusAsync();

    /// <summary>
    /// Dequeue a chunk of frontier claims for specific workers.
    /// </summary>
    Task<FrontierDequeueBatchViewModel?> DequeueFrontierAsync(
        IReadOnlyList<int>? workerIds = null,
        int limit = 20,
        string? daemonId = null);

    /// <summary>
    /// Get recent daemon crawler telemetry events relayed by manager.
    /// </summary>
    Task<List<CrawlerEventViewModel>> GetRecentCrawlerEventsAsync(int limit = 40);

    /// <summary>
    /// Get command queue dispatch diagnostics.
    /// </summary>
    Task<CommandQueueDiagnosticsViewModel> GetCommandQueueDiagnosticsAsync();

    /// <summary>
    /// Query persisted worker logs (worker-scoped or daemon-combined when workerId is null).
    /// </summary>
    Task<List<WorkerLogEntryViewModel>> GetPersistedWorkerLogsAsync(
        int? workerId,
        int limit = 120,
        string? severity = null,
        string? search = null);

    /// <summary>
    /// Query persisted throughput buckets for worker or all daemons when workerId is null.
    /// </summary>
    Task<List<WorkerThroughputPointViewModel>> GetThroughputSeriesAsync(
        int? workerId,
        int windowMinutes = 60,
        int bucketSeconds = 30);
}
