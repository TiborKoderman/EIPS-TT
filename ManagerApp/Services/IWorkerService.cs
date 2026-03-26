using ManagerApp.Models;

namespace ManagerApp.Services;

/// <summary>
/// Service for managing crawler workers
/// Currently uses mock data - will integrate with Python crawler API when available
/// </summary>
public interface IWorkerService
{
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
