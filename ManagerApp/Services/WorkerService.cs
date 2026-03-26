using ManagerApp.Models;
using Npgsql;
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
    private readonly IConfiguration _configuration;
    private readonly ILogger<WorkerService> _logger;
    private readonly bool _useReverseChannelCommands;
    public string? LastError { get; private set; }
    private static readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNameCaseInsensitive = true
    };

    public WorkerService(HttpClient httpClient, IConfiguration configuration, ILogger<WorkerService> logger)
    {
        _httpClient = httpClient;
        _configuration = configuration;
        _logger = logger;
        _useReverseChannelCommands = configuration.GetValue("CrawlerApi:UseReverseChannelCommands", true);
    }

    public async Task<DaemonStatusViewModel?> GetDaemonStatusAsync()
    {
        LastError = null;
        var envelope = await GetAsync<DaemonStatusViewModel>("api/daemon");
        return envelope?.Data;
    }

    public async Task<bool> StartDaemonAsync()
    {
        LastError = null;
        if (_useReverseChannelCommands && await EnqueueCommandAsync("start-daemon", null))
        {
            return true;
        }
        var response = await PostAsync("api/daemon/start", new { });
        return response?.Ok == true;
    }

    public async Task<bool> StopDaemonAsync()
    {
        LastError = null;
        if (_useReverseChannelCommands && await EnqueueCommandAsync("stop-daemon", null))
        {
            return true;
        }
        var response = await PostAsync("api/daemon/stop", new { });
        return response?.Ok == true;
    }

    public async Task<bool> ReloadDaemonAsync()
    {
        LastError = null;
        if (_useReverseChannelCommands && await EnqueueCommandAsync("reload-daemon", null))
        {
            return true;
        }
        var response = await PostAsync("api/daemon/reload", new { });
        return response?.Ok == true;
    }

    public async Task<WorkerViewModel?> SpawnWorkerAsync(string? name = null, int? daemonGroupId = null)
    {
        LastError = null;
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
        LastError = null;
        var envelope = await GetAsync<List<WorkerViewModel>>("api/workers");
        return envelope?.Data ?? new List<WorkerViewModel>();
    }

    public async Task<WorkerViewModel?> GetWorkerAsync(int id)
    {
        LastError = null;
        var envelope = await GetAsync<WorkerViewModel>($"api/workers/{id}/status");
        return envelope?.Data;
    }

    public async Task<bool> StartWorkerAsync(int id)
    {
        LastError = null;
        if (_useReverseChannelCommands && await EnqueueCommandAsync("start-worker", id))
        {
            return true;
        }
        var response = await PostAsync($"api/workers/{id}/start", new { });
        return response?.Ok == true;
    }

    public async Task<bool> StopWorkerAsync(int id)
    {
        LastError = null;
        if (_useReverseChannelCommands && await EnqueueCommandAsync("stop-worker", id))
        {
            return true;
        }
        var response = await PostAsync($"api/workers/{id}/stop", new { });
        return response?.Ok == true;
    }

    public async Task<bool> PauseWorkerAsync(int id)
    {
        LastError = null;
        if (_useReverseChannelCommands && await EnqueueCommandAsync("pause-worker", id))
        {
            return true;
        }
        var response = await PostAsync($"api/workers/{id}/pause", new { });
        return response?.Ok == true;
    }

    private async Task<bool> EnqueueCommandAsync(string commandType, int? workerId)
    {
        try
        {
            var connectionString = _configuration.GetConnectionString("CrawldbConnection");
            if (string.IsNullOrWhiteSpace(connectionString))
            {
                LastError = "Missing CrawldbConnection for command queue.";
                return false;
            }

            var daemonDbId = _configuration.GetValue("CrawlerApi:DefaultDaemonDbId", 1);
            var payload = JsonSerializer.Serialize(new
            {
                command = commandType,
                workerId,
            });

            await using var connection = new NpgsqlConnection(connectionString);
            await connection.OpenAsync();

            const string insertSql = """
                INSERT INTO manager.command (daemon_id, command_type, payload, status)
                VALUES (@daemon_id, @command_type, @payload::jsonb, 'queued');
                """;

            await using var cmd = new NpgsqlCommand(insertSql, connection);
            cmd.Parameters.AddWithValue("daemon_id", daemonDbId);
            cmd.Parameters.AddWithValue("command_type", commandType);
            cmd.Parameters.AddWithValue("payload", payload);
            await cmd.ExecuteNonQueryAsync();

            return true;
        }
        catch (Exception ex)
        {
            LastError = $"Failed to enqueue command '{commandType}': {ex.Message}";
            _logger.LogWarning(ex, "Failed to enqueue command {CommandType}", commandType);
            return false;
        }
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
        LastError = null;
        var envelope = await GetAsync<WorkerDetailViewModel>($"api/workers/{id}/detail");
        return envelope?.Data;
    }

    public async Task<WorkerGlobalConfigViewModel> GetGlobalConfigAsync()
    {
        LastError = null;
        var envelope = await GetAsync<WorkerGlobalConfigViewModel>("api/config/global");
        return envelope?.Data ?? new WorkerGlobalConfigViewModel();
    }

    public async Task SaveGlobalConfigAsync(WorkerGlobalConfigViewModel config)
    {
        LastError = null;
        _ = await PutAsync("api/config/global", config);
    }

    public async Task<List<WorkerGroupSettingsViewModel>> GetWorkerGroupsAsync()
    {
        LastError = null;
        var envelope = await GetAsync<List<WorkerGroupSettingsViewModel>>("api/config/groups");
        return envelope?.Data ?? new List<WorkerGroupSettingsViewModel>();
    }

    public async Task<bool> SaveWorkerGroupAsync(WorkerGroupSettingsViewModel group)
    {
        LastError = null;
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
                LastError = await ReadErrorAsync(response);
                return default;
            }

            return await response.Content.ReadFromJsonAsync<ApiEnvelope<T>>(_jsonOptions);
        }
        catch
        {
            LastError = "Crawler API is unreachable. Start the API server and verify CrawlerApi:BaseUrl.";
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
                LastError = await ReadErrorAsync(response);
                return default;
            }

            return await response.Content.ReadFromJsonAsync<ApiResponseEnvelope>(_jsonOptions);
        }
        catch
        {
            LastError = "Crawler API is unreachable. Start the API server and verify CrawlerApi:BaseUrl.";
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
                LastError = await ReadErrorAsync(response);
                return default;
            }

            return await response.Content.ReadFromJsonAsync<ApiResponseEnvelope<T>>(_jsonOptions);
        }
        catch
        {
            LastError = "Crawler API is unreachable. Start the API server and verify CrawlerApi:BaseUrl.";
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
                LastError = await ReadErrorAsync(response);
                return default;
            }

            return await response.Content.ReadFromJsonAsync<ApiResponseEnvelope>(_jsonOptions);
        }
        catch
        {
            LastError = "Crawler API is unreachable. Start the API server and verify CrawlerApi:BaseUrl.";
            return default;
        }
    }

    private static async Task<string> ReadErrorAsync(HttpResponseMessage response)
    {
        try
        {
            var envelope = await response.Content.ReadFromJsonAsync<ApiErrorEnvelope>(_jsonOptions);
            if (!string.IsNullOrWhiteSpace(envelope?.Error))
            {
                return envelope.Error;
            }
        }
        catch
        {
            // Ignore deserialization errors and fall back to status text.
        }

        return $"Crawler API request failed: {(int)response.StatusCode} {response.ReasonPhrase}";
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

    private sealed class ApiErrorEnvelope
    {
        public bool Ok { get; set; }
        public string? Error { get; set; }
    }
}
