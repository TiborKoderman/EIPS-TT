using ManagerApp.Models;

namespace ManagerApp.Services;

/// <summary>
/// Service for managing crawler workers
/// Daemon-based control surface backed by Python crawler API
/// </summary>
public interface IWorkerService
{
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
    Task<WorkerViewModel?> SpawnWorkerAsync(string? name = null, int? daemonGroupId = null);

    /// <summary>
    /// Get list of all workers with their current status
    /// </summary>
    Task<List<WorkerViewModel>> GetAllWorkersAsync();

    /// <summary>
    /// Get details for a specific worker
    /// </summary>
    Task<WorkerViewModel?> GetWorkerAsync(int id);

    /// <summary>
    /// Start a worker
    /// </summary>
    /// <returns>True if successful, false otherwise</returns>
    Task<bool> StartWorkerAsync(int id);

    /// <summary>
    /// Stop a worker
    /// </summary>
    /// <returns>True if successful, false otherwise</returns>
    Task<bool> StopWorkerAsync(int id);

    /// <summary>
    /// Pause a worker
    /// </summary>
    /// <returns>True if successful, false otherwise</returns>
    Task<bool> PauseWorkerAsync(int id);

    /// <summary>
    /// Get worker status counts for pie chart display
    /// Returns dictionary with status names as keys and counts as values
    /// </summary>
    Task<Dictionary<string, int>> GetWorkerStatusCountsAsync();

    /// <summary>
    /// Get full details for a single worker.
    /// </summary>
    Task<WorkerDetailViewModel?> GetWorkerDetailAsync(int id);

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
}
