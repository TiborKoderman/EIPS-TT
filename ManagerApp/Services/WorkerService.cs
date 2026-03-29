using ManagerApp.Models;
using Npgsql;
using NpgsqlTypes;
using System.Diagnostics;
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
    private readonly CrawlerRelayService _crawlerRelay;
    private readonly bool _useReverseChannelCommands;
    private static readonly SemaphoreSlim _daemonStartGate = new(1, 1);
    public string? LastError { get; private set; }
    private static readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNameCaseInsensitive = true
    };

    public WorkerService(
        HttpClient httpClient,
        IConfiguration configuration,
        ILogger<WorkerService> logger,
        CrawlerRelayService crawlerRelay)
    {
        _httpClient = httpClient;
        _configuration = configuration;
        _logger = logger;
        _crawlerRelay = crawlerRelay;
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
        var response = await PostAsync("api/daemon/start", new { }, allowAutoStartOnFailure: true);
        if (response?.Ok == true)
        {
            return true;
        }

        // Reverse channel requires an already running daemon websocket,
        // so enqueueing start-daemon commands only creates guaranteed failures.
        if (string.IsNullOrWhiteSpace(LastError))
        {
            LastError = "Daemon start must go through API/process launcher; reverse-channel start is disabled.";
        }

        return false;
    }

    public async Task<bool> StopDaemonAsync()
    {
        LastError = null;
        var response = await PostAsync("api/daemon/stop", new { });
        if (response?.Ok == true)
        {
            return true;
        }

        if (_useReverseChannelCommands && await EnqueueCommandAsync("stop-daemon", null))
        {
            LastError = null;
            return true;
        }

        return false;
    }

    public async Task<bool> ReloadDaemonAsync()
    {
        LastError = null;
        var response = await PostAsync("api/daemon/reload", new { });
        if (response?.Ok == true)
        {
            return true;
        }

        if (_useReverseChannelCommands && await EnqueueCommandAsync("reload-daemon", null))
        {
            LastError = null;
            return true;
        }

        return false;
    }

    public async Task<WorkerViewModel?> SpawnWorkerAsync(
        string? name = null,
        int? daemonGroupId = null,
        string? mode = null,
        IReadOnlyList<string>? seedUrls = null,
        string? daemonId = null)
    {
        LastError = null;
        var normalizedSeedUrls = (seedUrls ?? Array.Empty<string>())
            .Select(url => url.Trim())
            .Where(url => !string.IsNullOrWhiteSpace(url))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        var payload = new
        {
            name,
            mode = string.IsNullOrWhiteSpace(mode) ? "thread" : mode,
            groupId = daemonGroupId,
            seedUrl = normalizedSeedUrls.FirstOrDefault(),
            seedUrls = normalizedSeedUrls,
        };
        var response = await PostAsync<WorkerViewModel>("api/workers/spawn", payload);
        var spawned = response?.Data;
        if (spawned is null)
        {
            return null;
        }

        if (normalizedSeedUrls.Count > 0)
        {
            await PersistSeedUrlsAsync(spawned.Id, normalizedSeedUrls);
        }

        return spawned;
    }

    public async Task<List<WorkerViewModel>> GetAllWorkersAsync()
    {
        LastError = null;
        var envelope = await GetAsync<List<WorkerViewModel>>("api/workers");
        return envelope?.Data ?? new List<WorkerViewModel>();
    }

    public async Task<WorkerViewModel?> GetWorkerAsync(int id, string? daemonId = null)
    {
        LastError = null;
        var envelope = await GetAsync<WorkerViewModel>($"api/workers/{id}/status");
        return envelope?.Data;
    }

    public async Task<bool> StartWorkerAsync(int id, string? daemonId = null)
    {
        LastError = null;
        var response = await PostAsync($"api/workers/{id}/start", new { });
        if (response?.Ok == true)
        {
            return true;
        }

        if (_useReverseChannelCommands && await EnqueueCommandAsync("start-worker", id))
        {
            LastError = null;
            return true;
        }

        return false;
    }

    public async Task<bool> StopWorkerAsync(int id, string? daemonId = null)
    {
        LastError = null;
        var response = await PostAsync($"api/workers/{id}/stop", new { });
        if (response?.Ok == true)
        {
            return true;
        }

        if (_useReverseChannelCommands && await EnqueueCommandAsync("stop-worker", id))
        {
            LastError = null;
            return true;
        }

        return false;
    }

    public async Task<bool> PauseWorkerAsync(int id, string? daemonId = null)
    {
        LastError = null;
        var response = await PostAsync($"api/workers/{id}/pause", new { });
        if (response?.Ok == true)
        {
            return true;
        }

        if (_useReverseChannelCommands && await EnqueueCommandAsync("pause-worker", id))
        {
            LastError = null;
            return true;
        }

        return false;
    }

    private async Task PersistSeedUrlsAsync(int externalWorkerId, IReadOnlyList<string> seedUrls)
    {
        try
        {
            var connectionString = _configuration.GetConnectionString("CrawldbConnection");
            if (string.IsNullOrWhiteSpace(connectionString))
            {
                return;
            }

            var daemonDbId = _configuration.GetValue("CrawlerApi:DefaultDaemonDbId", 1);

            await using var connection = new NpgsqlConnection(connectionString);
            await connection.OpenAsync();

            const string insertSql = """
                INSERT INTO manager.seed_url (daemon_id, external_worker_id, url)
                VALUES (@daemon_id, @external_worker_id, @url)
                ON CONFLICT (daemon_id, external_worker_id, url)
                DO NOTHING;
                """;

            foreach (var url in seedUrls)
            {
                await using var cmd = new NpgsqlCommand(insertSql, connection);
                cmd.Parameters.AddWithValue("daemon_id", daemonDbId);
                cmd.Parameters.AddWithValue("external_worker_id", externalWorkerId);
                cmd.Parameters.AddWithValue("url", url);
                await cmd.ExecuteNonQueryAsync();
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to persist seed URLs for worker {WorkerId}", externalWorkerId);
        }
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
            .Where(w => !string.IsNullOrWhiteSpace(w.Status))
            .GroupBy(w => NormalizeStatus(w.Status))
            .ToDictionary(g => g.Key, g => g.Count());
    }

    private static string NormalizeStatus(string? status)
    {
        if (string.IsNullOrWhiteSpace(status))
        {
            return "Unknown";
        }

        var normalized = status.Trim();
        return char.ToUpperInvariant(normalized[0]) + normalized[1..].ToLowerInvariant();
    }

    public async Task<WorkerDetailViewModel?> GetWorkerDetailAsync(int id, string? daemonId = null)
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
        config.SeedEntries = config.SeedEntries
            .Where(entry => !string.IsNullOrWhiteSpace(entry.Url))
            .Select(entry => new SeedEntryViewModel
            {
                Url = entry.Url.Trim(),
                Enabled = entry.Enabled,
                Label = entry.Label?.Trim() ?? string.Empty,
            })
            .ToList();

        config.SeedUrlsText = string.Join("\n", config.SeedEntries
            .Where(entry => entry.Enabled)
            .Select(entry => entry.Url));

        config.TopicKeywords = config.TopicKeywordsText
            .Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries)
            .Select(value => value.Trim())
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        config.RelevanceAllowedDomainSuffixes = config.RelevanceAllowedDomainSuffixesText
            .Split(new[] { '\r', '\n', ',', ';' }, StringSplitOptions.RemoveEmptyEntries)
            .Select(value => value.Trim().TrimStart('.').ToLowerInvariant())
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        config.RelevanceSameHostBoost = Math.Max(0, config.RelevanceSameHostBoost);
        config.RelevanceAllowedSuffixBoost = Math.Max(0, config.RelevanceAllowedSuffixBoost);
        config.RelevanceKeywordBoost = Math.Max(0, config.RelevanceKeywordBoost);
        config.RelevanceDepthPenalty = Math.Max(0, config.RelevanceDepthPenalty);

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
        group.TopicKeywords = group.TopicKeywordsText
            .Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries)
            .Select(value => value.Trim())
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        var response = await PutAsync($"api/config/groups/{group.Id}", group);
        return response?.Ok == true;
    }

    public async Task<bool> AddSeedAsync(string url, int? workerId = null)
    {
        LastError = null;
        var normalized = (url ?? string.Empty).Trim();
        if (string.IsNullOrWhiteSpace(normalized))
        {
            LastError = "Seed URL must not be empty.";
            return false;
        }

        var payload = new
        {
            url = normalized,
            workerId,
        };

        var response = await PostAsync("api/frontier/seed", payload);
        return response?.Ok == true;
    }

    public async Task<FrontierClaimViewModel?> ClaimFrontierUrlAsync(int workerId)
    {
        LastError = null;
        var response = await PostAsync<FrontierClaimViewModel>("api/frontier/claim", new
        {
            workerId,
        });
        return response?.Data;
    }

    public async Task<bool> CompleteFrontierUrlAsync(int workerId, string url, string? leaseToken, string status = "completed")
    {
        LastError = null;
        var normalizedUrl = (url ?? string.Empty).Trim();
        if (string.IsNullOrWhiteSpace(normalizedUrl))
        {
            LastError = "Frontier completion requires a non-empty URL.";
            return false;
        }

        var normalizedStatus = string.IsNullOrWhiteSpace(status) ? "completed" : status.Trim().ToLowerInvariant();
        var response = await PostAsync("api/frontier/complete", new
        {
            workerId,
            url = normalizedUrl,
            leaseToken,
            status = normalizedStatus,
        });
        return response?.Ok == true;
    }

    public async Task<bool> PruneFrontierUrlAsync(int workerId, string url, string reason = "server-conflict")
    {
        LastError = null;
        var normalizedUrl = (url ?? string.Empty).Trim();
        if (string.IsNullOrWhiteSpace(normalizedUrl))
        {
            LastError = "Frontier prune requires a non-empty URL.";
            return false;
        }

        var normalizedReason = string.IsNullOrWhiteSpace(reason) ? "server-conflict" : reason.Trim();
        var response = await PostAsync("api/frontier/prune", new
        {
            workerId,
            url = normalizedUrl,
            reason = normalizedReason,
        });
        return response?.Ok == true;
    }

    public async Task<FrontierStatusViewModel?> GetFrontierStatusAsync()
    {
        LastError = null;
        var response = await GetAsync<FrontierStatusViewModel>("api/frontier/status");
        return response?.Data;
    }

    public async Task<FrontierDequeueBatchViewModel?> DequeueFrontierAsync(
        IReadOnlyList<int>? workerIds = null,
        int limit = 20,
        string? daemonId = null)
    {
        LastError = null;
        var boundedLimit = Math.Clamp(limit, 1, 100);
        var payload = new
        {
            workerIds = workerIds?.Distinct().ToArray() ?? Array.Empty<int>(),
            limit = boundedLimit,
            daemonId,
        };

        var response = await PostAsync<FrontierDequeueBatchViewModel>("api/frontier/dequeue", payload);
        return response?.Data;
    }

    public Task<List<CrawlerEventViewModel>> GetRecentCrawlerEventsAsync(int limit = 40)
    {
        LastError = null;
        var boundedLimit = Math.Clamp(limit, 1, 5000);
        var events = _crawlerRelay
            .GetRecentEvents(boundedLimit)
            .Select(evt => new CrawlerEventViewModel
            {
                TimestampUtc = evt.TimestampUtc,
                Type = evt.Type,
                DaemonId = evt.DaemonId,
                WorkerId = evt.WorkerId,
                PayloadJson = evt.PayloadJson,
            })
            .ToList();

        return Task.FromResult(events);
    }

    public async Task<CommandQueueDiagnosticsViewModel> GetCommandQueueDiagnosticsAsync()
    {
        var diagnostics = new CommandQueueDiagnosticsViewModel();
        var lookbackMinutes = Math.Clamp(_configuration.GetValue("CrawlerApi:DiagnosticsLookbackMinutes", 60), 5, 24 * 60);
        diagnostics.RecentWindowMinutes = lookbackMinutes;

        try
        {
            var connectionString = _configuration.GetConnectionString("CrawldbConnection");
            if (string.IsNullOrWhiteSpace(connectionString))
            {
                return diagnostics;
            }

            await using var connection = new NpgsqlConnection(connectionString);
            await connection.OpenAsync();

            const string countsSql = """
                SELECT status, COUNT(*)
                FROM manager.command
                WHERE COALESCE(completed_at, created_at) >= NOW() - make_interval(mins => @lookback_minutes)
                  AND NOT (command_type = 'start-daemon' AND status = 'failed')
                GROUP BY status;
                """;

            await using (var cmd = new NpgsqlCommand(countsSql, connection))
            {
                cmd.Parameters.AddWithValue("lookback_minutes", lookbackMinutes);
            await using (var reader = await cmd.ExecuteReaderAsync())
            {
                while (await reader.ReadAsync())
                {
                    var status = reader.GetString(0);
                    var count = reader.GetInt32(1);
                    switch (status)
                    {
                        case "queued":
                            diagnostics.Queued = count;
                            break;
                        case "dispatched":
                            diagnostics.Dispatched = count;
                            break;
                        case "acknowledged":
                            diagnostics.Acknowledged = count;
                            break;
                        case "completed":
                            diagnostics.Completed = count;
                            break;
                        case "failed":
                            diagnostics.Failed = count;
                            break;
                    }
                }
            }
            }

            const string failureSql = """
                SELECT error_message
                FROM manager.command
                WHERE status = 'failed'
                  AND error_message IS NOT NULL
                  AND COALESCE(completed_at, created_at) >= NOW() - make_interval(mins => @lookback_minutes)
                ORDER BY COALESCE(completed_at, created_at) DESC
                LIMIT 1;
                """;

            await using var failureCmd = new NpgsqlCommand(failureSql, connection);
            failureCmd.Parameters.AddWithValue("lookback_minutes", lookbackMinutes);
            var failure = await failureCmd.ExecuteScalarAsync();
            diagnostics.LastFailure = failure as string;
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Failed to load command queue diagnostics.");
        }

        return diagnostics;
    }

    public async Task<List<WorkerLogEntryViewModel>> GetPersistedWorkerLogsAsync(
        int? workerId,
        int limit = 120,
        string? severity = null,
        string? search = null)
    {
        var result = new List<WorkerLogEntryViewModel>();
        var boundedLimit = Math.Clamp(limit, 1, 400);
        var normalizedSeverity = string.IsNullOrWhiteSpace(severity) || string.Equals(severity, "all", StringComparison.OrdinalIgnoreCase)
            ? null
            : severity.Trim();
        var normalizedSearch = string.IsNullOrWhiteSpace(search) ? null : search.Trim();

        try
        {
            var connectionString = _configuration.GetConnectionString("CrawldbConnection");
            if (string.IsNullOrWhiteSpace(connectionString))
            {
                return result;
            }

            await using var connection = new NpgsqlConnection(connectionString);
            await connection.OpenAsync();

            const string sql = """
                SELECT created_at, level, message, daemon_identifier, external_worker_id
                FROM manager.worker_log
                WHERE (@worker_id IS NULL OR external_worker_id = @worker_id)
                  AND (@severity IS NULL OR lower(level) = lower(@severity))
                  AND (@search IS NULL OR message ILIKE ('%' || @search || '%'))
                ORDER BY created_at DESC
                LIMIT @limit;
                """;

            await using var cmd = new NpgsqlCommand(sql, connection);
            cmd.Parameters.Add("worker_id", NpgsqlDbType.Integer).Value = (object?)workerId ?? DBNull.Value;
            cmd.Parameters.Add("severity", NpgsqlDbType.Text).Value = (object?)normalizedSeverity ?? DBNull.Value;
            cmd.Parameters.Add("search", NpgsqlDbType.Text).Value = (object?)normalizedSearch ?? DBNull.Value;
            cmd.Parameters.Add("limit", NpgsqlDbType.Integer).Value = boundedLimit;

            await using var reader = await cmd.ExecuteReaderAsync();
            while (await reader.ReadAsync())
            {
                result.Add(new WorkerLogEntryViewModel
                {
                    TimestampUtc = DateTime.SpecifyKind(reader.GetDateTime(0), DateTimeKind.Utc),
                    Level = reader.GetString(1),
                    Message = reader.GetString(2),
                    DaemonId = reader.IsDBNull(3) ? "local-default" : reader.GetString(3),
                    WorkerId = reader.IsDBNull(4) ? null : reader.GetInt32(4),
                });
            }
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Failed to load persisted worker logs.");
        }

        return result;
    }

    public async Task<List<WorkerThroughputPointViewModel>> GetThroughputSeriesAsync(
        int? workerId,
        int windowMinutes = 60,
        int bucketSeconds = 30)
    {
        var result = new List<WorkerThroughputPointViewModel>();
        var boundedWindow = Math.Clamp(windowMinutes, 5, 24 * 60);
        var boundedBucket = Math.Clamp(bucketSeconds, 10, 600);

        try
        {
            var connectionString = _configuration.GetConnectionString("CrawldbConnection");
            if (string.IsNullOrWhiteSpace(connectionString))
            {
                return result;
            }

            await using var connection = new NpgsqlConnection(connectionString);
            await connection.OpenAsync();

            const string sql = """
                SELECT
                    date_bin(make_interval(secs => @bucket_seconds), created_at, timestamp '2001-01-01') AS bucket_time,
                    COALESCE(SUM(metric_value), 0)
                FROM manager.worker_metric
                WHERE metric_name = 'page_processed'
                  AND created_at >= NOW() - make_interval(mins => @window_minutes)
                  AND (@worker_id IS NULL OR external_worker_id = @worker_id)
                GROUP BY bucket_time
                ORDER BY bucket_time;
                """;

            await using var cmd = new NpgsqlCommand(sql, connection);
            cmd.Parameters.Add("bucket_seconds", NpgsqlDbType.Integer).Value = boundedBucket;
            cmd.Parameters.Add("window_minutes", NpgsqlDbType.Integer).Value = boundedWindow;
            cmd.Parameters.Add("worker_id", NpgsqlDbType.Integer).Value = (object?)workerId ?? DBNull.Value;

            await using var reader = await cmd.ExecuteReaderAsync();
            while (await reader.ReadAsync())
            {
                result.Add(new WorkerThroughputPointViewModel
                {
                    TimestampUtc = DateTime.SpecifyKind(reader.GetDateTime(0), DateTimeKind.Utc),
                    Value = reader.GetDouble(1),
                });
            }
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Failed to load throughput series.");
        }

        return result;
    }

    private async Task<ApiEnvelope<T>?> GetAsync<T>(string path)
    {
        try
        {
            var response = await _httpClient.GetAsync(path);
            if (!response.IsSuccessStatusCode && await TryAutoStartDaemonAndRetryAsync())
            {
                response = await _httpClient.GetAsync(path);
            }
            if (!response.IsSuccessStatusCode)
            {
                LastError = await ReadErrorAsync(response);
                return default;
            }

            return await response.Content.ReadFromJsonAsync<ApiEnvelope<T>>(_jsonOptions);
        }
        catch
        {
            if (await TryAutoStartDaemonAndRetryAsync())
            {
                try
                {
                    var retryResponse = await _httpClient.GetAsync(path);
                    if (retryResponse.IsSuccessStatusCode)
                    {
                        LastError = null;
                        return await retryResponse.Content.ReadFromJsonAsync<ApiEnvelope<T>>(_jsonOptions);
                    }

                    LastError = await ReadErrorAsync(retryResponse);
                    return default;
                }
                catch
                {
                    // keep default unreachable message below
                }
            }
            LastError = "Crawler API is unreachable. Start the API server and verify CrawlerApi:BaseUrl.";
            return default;
        }
    }

    private async Task<bool> TryAutoStartDaemonAndRetryAsync()
    {
        var baseUrl = _configuration["CrawlerApi:BaseUrl"] ?? "http://127.0.0.1:8090";
        if (!baseUrl.Contains("127.0.0.1") && !baseUrl.Contains("localhost"))
        {
            return false;
        }

        await _daemonStartGate.WaitAsync();
        try
        {
            if (await IsApiReachableAsync())
            {
                return true;
            }

            var managerDir = Directory.GetCurrentDirectory();
            var repoRoot = Path.GetFullPath(Path.Combine(managerDir, ".."));
            var daemonArgs = _configuration["CrawlerApi:LocalDaemonArgs"]
                ?? "pa1/crawler/src/daemon/main.py";

            var candidates = new List<string>();
            var configured = _configuration["CrawlerApi:PythonExecutable"];
            var venvPython = Path.Combine(repoRoot, ".venv", "bin", "python");
            if (File.Exists(venvPython))
            {
                candidates.Add(venvPython);
            }
            candidates.Add(".venv/bin/python");
            if (!string.IsNullOrWhiteSpace(configured))
            {
                candidates.Add(configured);
            }
            candidates.Add("python3");
            candidates.Add("python");

            foreach (var pythonExe in candidates.Distinct(StringComparer.OrdinalIgnoreCase))
            {
                try
                {
                    var process = Process.Start(new ProcessStartInfo
                    {
                        FileName = pythonExe,
                        Arguments = daemonArgs,
                        WorkingDirectory = repoRoot,
                        UseShellExecute = false,
                    });

                    _logger.LogInformation("Triggered on-demand daemon startup using {PythonExe}", pythonExe);
                    for (var i = 0; i < 10; i++)
                    {
                        if (await IsApiReachableAsync())
                        {
                            LastError = null;
                            return true;
                        }

                        await Task.Delay(300);
                    }

                    if (process?.HasExited == true)
                    {
                        continue;
                    }
                }
                catch
                {
                    // try next candidate
                }
            }

            for (var i = 0; i < 10; i++)
            {
                if (await IsApiReachableAsync())
                {
                    LastError = null;
                    return true;
                }

                await Task.Delay(400);
            }

            return false;
        }
        finally
        {
            _daemonStartGate.Release();
        }
    }

    private async Task<bool> IsApiReachableAsync()
    {
        try
        {
            using var response = await _httpClient.GetAsync("api/health");
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    private async Task<ApiResponseEnvelope?> PostAsync(string path, object payload, bool allowAutoStartOnFailure = false)
    {
        try
        {
            var response = await _httpClient.PostAsJsonAsync(path, payload);
            if (!response.IsSuccessStatusCode && allowAutoStartOnFailure && await TryAutoStartDaemonAndRetryAsync())
            {
                response = await _httpClient.PostAsJsonAsync(path, payload);
            }
            if (!response.IsSuccessStatusCode)
            {
                LastError = await ReadErrorAsync(response);
                return default;
            }

            return await response.Content.ReadFromJsonAsync<ApiResponseEnvelope>(_jsonOptions);
        }
        catch
        {
            if (allowAutoStartOnFailure && await TryAutoStartDaemonAndRetryAsync())
            {
                try
                {
                    var retryResponse = await _httpClient.PostAsJsonAsync(path, payload);
                    if (retryResponse.IsSuccessStatusCode)
                    {
                        LastError = null;
                        return await retryResponse.Content.ReadFromJsonAsync<ApiResponseEnvelope>(_jsonOptions);
                    }

                    LastError = await ReadErrorAsync(retryResponse);
                    return default;
                }
                catch
                {
                    // keep default unreachable message below
                }
            }
            LastError = "Crawler API is unreachable. Start the API server and verify CrawlerApi:BaseUrl.";
            return default;
        }
    }

    private async Task<ApiResponseEnvelope<T>?> PostAsync<T>(string path, object payload, bool allowAutoStartOnFailure = false)
    {
        try
        {
            var response = await _httpClient.PostAsJsonAsync(path, payload);
            if (!response.IsSuccessStatusCode && allowAutoStartOnFailure && await TryAutoStartDaemonAndRetryAsync())
            {
                response = await _httpClient.PostAsJsonAsync(path, payload);
            }
            if (!response.IsSuccessStatusCode)
            {
                LastError = await ReadErrorAsync(response);
                return default;
            }

            return await response.Content.ReadFromJsonAsync<ApiResponseEnvelope<T>>(_jsonOptions);
        }
        catch
        {
            if (allowAutoStartOnFailure && await TryAutoStartDaemonAndRetryAsync())
            {
                try
                {
                    var retryResponse = await _httpClient.PostAsJsonAsync(path, payload);
                    if (retryResponse.IsSuccessStatusCode)
                    {
                        LastError = null;
                        return await retryResponse.Content.ReadFromJsonAsync<ApiResponseEnvelope<T>>(_jsonOptions);
                    }

                    LastError = await ReadErrorAsync(retryResponse);
                    return default;
                }
                catch
                {
                    // keep default unreachable message below
                }
            }
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
