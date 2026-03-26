using ManagerApp.Models;
using System.Net.Http.Json;
using System.Text.Json;

namespace ManagerApp.Services;

/// <summary>
/// API-backed implementation of worker service.
/// Uses crawler daemon control endpoints exposed by Python API.
/// </summary>
public class WorkerService : IWorkerService
{
    private readonly HttpClient _httpClient;
    private static readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNameCaseInsensitive = true
    };

    public WorkerService(HttpClient httpClient)
    {
        _httpClient = httpClient;
    }

    public async Task<DaemonStatusViewModel?> GetDaemonStatusAsync()
    {
        var envelope = await GetAsync<DaemonStatusViewModel>("api/daemon");
        return envelope?.Data;
    }

    public async Task<bool> StartDaemonAsync()
    {
        var response = await PostAsync("api/daemon/start", new { });
        return response?.Ok == true;
    }

    public async Task<bool> StopDaemonAsync()
    {
        var response = await PostAsync("api/daemon/stop", new { });
        return response?.Ok == true;
    }

    public async Task<bool> ReloadDaemonAsync()
    {
        var response = await PostAsync("api/daemon/reload", new { });
        return response?.Ok == true;
    }

    public async Task<WorkerViewModel?> SpawnWorkerAsync(string? name = null, int? daemonGroupId = null)
    {
        var payload = new
        {
            name,
            mode = "mock",
            groupId = daemonGroupId
        };
        var response = await PostAsync<WorkerViewModel>("api/workers/spawn", payload);
        return response?.Data;
    }

    public async Task<List<WorkerViewModel>> GetAllWorkersAsync()
    {
        var envelope = await GetAsync<List<WorkerViewModel>>("api/workers");
        return envelope?.Data ?? new List<WorkerViewModel>();
    }

    public async Task<WorkerViewModel?> GetWorkerAsync(int id)
    {
        var envelope = await GetAsync<WorkerViewModel>($"api/workers/{id}/status");
        return envelope?.Data;
    }

    public async Task<bool> StartWorkerAsync(int id)
    {
        var response = await PostAsync($"api/workers/{id}/start", new { });
        return response?.Ok == true;
    }

    public async Task<bool> StopWorkerAsync(int id)
    {
        var response = await PostAsync($"api/workers/{id}/stop", new { });
        return response?.Ok == true;
    }

    public async Task<bool> PauseWorkerAsync(int id)
    {
        var response = await PostAsync($"api/workers/{id}/pause", new { });
        return response?.Ok == true;
    }

    public async Task<Dictionary<string, int>> GetWorkerStatusCountsAsync()
    {
        var workers = await GetAllWorkersAsync();
        return workers
            .GroupBy(w => w.Status)
            .ToDictionary(g => g.Key, g => g.Count());
    }

    public async Task<WorkerDetailViewModel?> GetWorkerDetailAsync(int id)
    {
        var envelope = await GetAsync<WorkerDetailViewModel>($"api/workers/{id}/detail");
        return envelope?.Data;
    }

    public async Task<WorkerGlobalConfigViewModel> GetGlobalConfigAsync()
    {
        var envelope = await GetAsync<WorkerGlobalConfigViewModel>("api/config/global");
        return envelope?.Data ?? new WorkerGlobalConfigViewModel();
    }

    public async Task SaveGlobalConfigAsync(WorkerGlobalConfigViewModel config)
    {
        _ = await PutAsync("api/config/global", config);
    }

    public async Task<List<WorkerGroupSettingsViewModel>> GetWorkerGroupsAsync()
    {
        var envelope = await GetAsync<List<WorkerGroupSettingsViewModel>>("api/config/groups");
        return envelope?.Data ?? new List<WorkerGroupSettingsViewModel>();
    }

    public async Task<bool> SaveWorkerGroupAsync(WorkerGroupSettingsViewModel group)
    {
        var response = await PutAsync($"api/config/groups/{group.Id}", group);
        return response?.Ok == true;
    }

    private async Task<ApiEnvelope<T>?> GetAsync<T>(string path)
    {
        try
        {
            var response = await _httpClient.GetAsync(path);
            if (!response.IsSuccessStatusCode)
            {
                return default;
            }

            return await response.Content.ReadFromJsonAsync<ApiEnvelope<T>>(_jsonOptions);
        }
        catch
        {
            return default;
        }
    }

    private async Task<ApiResponseEnvelope?> PostAsync(string path, object payload)
    {
        try
        {
            var response = await _httpClient.PostAsJsonAsync(path, payload);
            if (!response.IsSuccessStatusCode)
            {
                return default;
            }

            return await response.Content.ReadFromJsonAsync<ApiResponseEnvelope>(_jsonOptions);
        }
        catch
        {
            return default;
        }
    }

    private async Task<ApiResponseEnvelope<T>?> PostAsync<T>(string path, object payload)
    {
        try
        {
            var response = await _httpClient.PostAsJsonAsync(path, payload);
            if (!response.IsSuccessStatusCode)
            {
                return default;
            }

            return await response.Content.ReadFromJsonAsync<ApiResponseEnvelope<T>>(_jsonOptions);
        }
        catch
        {
            return default;
        }
    }

    private async Task<ApiResponseEnvelope?> PutAsync(string path, object payload)
    {
        try
        {
            var response = await _httpClient.PutAsJsonAsync(path, payload);
            if (!response.IsSuccessStatusCode)
            {
                return default;
            }

            return await response.Content.ReadFromJsonAsync<ApiResponseEnvelope>(_jsonOptions);
        }
        catch
        {
            return default;
        }
    }

    private sealed class ApiEnvelope<T>
    {
        public bool Ok { get; set; }
        public T? Data { get; set; }
    }

    private sealed class ApiResponseEnvelope
    {
        public bool Ok { get; set; }
        public JsonElement Data { get; set; }
    }

    private sealed class ApiResponseEnvelope<T>
    {
        public bool Ok { get; set; }
        public T? Data { get; set; }
    }
}
