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
}
