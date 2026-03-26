using ManagerApp.Models;

namespace ManagerApp.Services;

/// <summary>
/// Mock implementation of worker service
/// TODO: Replace with real implementation that calls Python crawler API
/// </summary>
public class WorkerService : IWorkerService
{
    // MOCK DATA - In-memory list of workers for testing
    // This will be replaced with API calls to Python crawler when implemented
    private static readonly List<WorkerViewModel> _mockWorkers = new()
    {
        new WorkerViewModel
        {
            Id = 1,
            Name = "Worker-1",
            Status = "Active",
            CurrentUrl = "https://example.com/page1",
            PagesProcessed = 142,
            ErrorCount = 2,
            StartedAt = DateTime.UtcNow.AddHours(-2)
        },
        new WorkerViewModel
        {
            Id = 2,
            Name = "Worker-2",
            Status = "Idle",
            CurrentUrl = null,
            PagesProcessed = 89,
            ErrorCount = 0,
            StartedAt = DateTime.UtcNow.AddHours(-1)
        },
        new WorkerViewModel
        {
            Id = 3,
            Name = "Worker-3",
            Status = "Error",
            CurrentUrl = "https://example.com/error-page",
            PagesProcessed = 45,
            ErrorCount = 15,
            StartedAt = DateTime.UtcNow.AddMinutes(-30)
        },
        new WorkerViewModel
        {
            Id = 4,
            Name = "Worker-4",
            Status = "Paused",
            CurrentUrl = "https://example.com/paused-at",
            PagesProcessed = 203,
            ErrorCount = 5,
            StartedAt = DateTime.UtcNow.AddHours(-3)
        }
    };

    private static WorkerGlobalConfigViewModel _globalConfig = new()
    {
        MaxConcurrentWorkers = 8,
        RequestTimeoutSeconds = 25,
        CrawlDelayMilliseconds = 250,
        RespectRobotsTxt = true,
        UserAgent = "EIPS-TT-Manager-Crawler/10.0"
    };

    private static readonly List<WorkerGroupSettingsViewModel> _workerGroups = new()
    {
        new WorkerGroupSettingsViewModel
        {
            Id = 1,
            Name = "Core Sites",
            Description = "Primary trusted domains with broader crawl depth",
            Enabled = true,
            MaxPagesPerWorker = 5000,
            RateLimitPerMinute = 240,
            WorkerIds = [1, 2]
        },
        new WorkerGroupSettingsViewModel
        {
            Id = 2,
            Name = "Experimental",
            Description = "Sandbox group for new parsing strategies",
            Enabled = true,
            MaxPagesPerWorker = 1200,
            RateLimitPerMinute = 90,
            WorkerIds = [3, 4]
        }
    };

    public Task<List<WorkerViewModel>> GetAllWorkersAsync()
    {
        // Return copy of mock workers
        return Task.FromResult(_mockWorkers.ToList());
    }

    public Task<WorkerViewModel?> GetWorkerAsync(int id)
    {
        var worker = _mockWorkers.FirstOrDefault(w => w.Id == id);
        return Task.FromResult(worker);
    }

    public Task<bool> StartWorkerAsync(int id)
    {
        // TODO: Call Python API endpoint POST /api/workers/{id}/start
        var worker = _mockWorkers.FirstOrDefault(w => w.Id == id);
        if (worker != null)
        {
            worker.Status = "Active";
            worker.StartedAt = DateTime.UtcNow;
            return Task.FromResult(true);
        }
        return Task.FromResult(false);
    }

    public Task<bool> StopWorkerAsync(int id)
    {
        // TODO: Call Python API endpoint POST /api/workers/{id}/stop
        var worker = _mockWorkers.FirstOrDefault(w => w.Id == id);
        if (worker != null)
        {
            worker.Status = "Stopped";
            worker.CurrentUrl = null;
            return Task.FromResult(true);
        }
        return Task.FromResult(false);
    }

    public Task<bool> PauseWorkerAsync(int id)
    {
        // TODO: Call Python API endpoint POST /api/workers/{id}/pause
        var worker = _mockWorkers.FirstOrDefault(w => w.Id == id);
        if (worker != null)
        {
            worker.Status = "Paused";
            return Task.FromResult(true);
        }
        return Task.FromResult(false);
    }

    public Task<Dictionary<string, int>> GetWorkerStatusCountsAsync()
    {
        // Group workers by status and count them
        var counts = _mockWorkers
            .GroupBy(w => w.Status)
            .ToDictionary(g => g.Key, g => g.Count());

        return Task.FromResult(counts);
    }

    public Task<WorkerDetailViewModel?> GetWorkerDetailAsync(int id)
    {
        var worker = _mockWorkers.FirstOrDefault(w => w.Id == id);
        if (worker == null)
        {
            return Task.FromResult<WorkerDetailViewModel?>(null);
        }

        var group = _workerGroups.FirstOrDefault(g => g.WorkerIds.Contains(id));

        var detail = new WorkerDetailViewModel
        {
            Worker = worker,
            GroupName = group?.Name,
            RuntimeConfig = new Dictionary<string, string>
            {
                ["Retries"] = "3",
                ["Render JS"] = (worker.Id % 2 == 0 ? "false" : "true"),
                ["Canonicalization"] = "strict",
                ["Depth Limit"] = (worker.Id % 2 == 0 ? "4" : "6")
            },
            RecentLogs =
            [
                new WorkerLogEntryViewModel
                {
                    TimestampUtc = DateTime.UtcNow.AddSeconds(-12),
                    Level = "Info",
                    Message = $"Worker {worker.Name} fetched {(worker.CurrentUrl ?? "frontier URL")}."
                },
                new WorkerLogEntryViewModel
                {
                    TimestampUtc = DateTime.UtcNow.AddSeconds(-33),
                    Level = worker.Status == "Error" ? "Error" : "Info",
                    Message = worker.Status == "Error"
                        ? "Parser pipeline raised a transient extraction error."
                        : "Link extraction and dedup checks completed."
                },
                new WorkerLogEntryViewModel
                {
                    TimestampUtc = DateTime.UtcNow.AddMinutes(-1),
                    Level = "Debug",
                    Message = "Heartbeat and metrics snapshot sent to manager hub."
                }
            ]
        };

        return Task.FromResult<WorkerDetailViewModel?>(detail);
    }

    public Task<WorkerGlobalConfigViewModel> GetGlobalConfigAsync()
    {
        return Task.FromResult(new WorkerGlobalConfigViewModel
        {
            MaxConcurrentWorkers = _globalConfig.MaxConcurrentWorkers,
            RequestTimeoutSeconds = _globalConfig.RequestTimeoutSeconds,
            CrawlDelayMilliseconds = _globalConfig.CrawlDelayMilliseconds,
            RespectRobotsTxt = _globalConfig.RespectRobotsTxt,
            UserAgent = _globalConfig.UserAgent
        });
    }

    public Task SaveGlobalConfigAsync(WorkerGlobalConfigViewModel config)
    {
        _globalConfig = new WorkerGlobalConfigViewModel
        {
            MaxConcurrentWorkers = config.MaxConcurrentWorkers,
            RequestTimeoutSeconds = config.RequestTimeoutSeconds,
            CrawlDelayMilliseconds = config.CrawlDelayMilliseconds,
            RespectRobotsTxt = config.RespectRobotsTxt,
            UserAgent = config.UserAgent
        };

        return Task.CompletedTask;
    }

    public Task<List<WorkerGroupSettingsViewModel>> GetWorkerGroupsAsync()
    {
        var copy = _workerGroups.Select(g => new WorkerGroupSettingsViewModel
        {
            Id = g.Id,
            Name = g.Name,
            Description = g.Description,
            Enabled = g.Enabled,
            MaxPagesPerWorker = g.MaxPagesPerWorker,
            RateLimitPerMinute = g.RateLimitPerMinute,
            WorkerIds = [.. g.WorkerIds]
        }).ToList();

        return Task.FromResult(copy);
    }

    public Task<bool> SaveWorkerGroupAsync(WorkerGroupSettingsViewModel group)
    {
        var existing = _workerGroups.FirstOrDefault(g => g.Id == group.Id);
        if (existing == null)
        {
            return Task.FromResult(false);
        }

        existing.Name = group.Name;
        existing.Description = group.Description;
        existing.Enabled = group.Enabled;
        existing.MaxPagesPerWorker = group.MaxPagesPerWorker;
        existing.RateLimitPerMinute = group.RateLimitPerMinute;
        existing.WorkerIds = [.. group.WorkerIds.Distinct()];
        return Task.FromResult(true);
    }
}
