using System.Diagnostics;
using System.Text.Json;
using ManagerApp.Models;
using Npgsql;
using NpgsqlTypes;

namespace ManagerApp.Services;

/// <summary>
/// Worker service backed by manager-owned daemon websocket channels and persisted manager state.
/// </summary>
public sealed class ReverseChannelWorkerService : IWorkerService
{
    private readonly IConfiguration _configuration;
    private readonly ILogger<ReverseChannelWorkerService> _logger;
    private readonly DaemonChannelService _daemonChannel;
    private readonly CrawlerRelayService _crawlerRelay;
    private static readonly SemaphoreSlim _daemonStartGate = new(1, 1);
    private static DateTime _lastLocalDaemonLaunchAttemptUtc = DateTime.MinValue;
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    public ReverseChannelWorkerService(
        IConfiguration configuration,
        ILogger<ReverseChannelWorkerService> logger,
        DaemonChannelService daemonChannel,
        CrawlerRelayService crawlerRelay)
    {
        _configuration = configuration;
        _logger = logger;
        _daemonChannel = daemonChannel;
        _crawlerRelay = crawlerRelay;
    }

    public string? LastError { get; private set; }

    public Task<DaemonStatusViewModel?> GetDaemonStatusAsync()
    {
        LastError = null;
        var snapshot = GetSnapshot();
        if (snapshot is not null)
        {
            var daemon = CloneDaemonStatus(snapshot.Daemon);
            daemon.WorkerCount = snapshot.Workers.Count;
            daemon.ActiveWorkers = snapshot.Workers.Count(worker => string.Equals(worker.Status, "Active", StringComparison.OrdinalIgnoreCase));
            return Task.FromResult<DaemonStatusViewModel?>(daemon);
        }

        return Task.FromResult<DaemonStatusViewModel?>(new DaemonStatusViewModel
        {
            Running = false,
            StartedAt = null,
            Mode = "single-instance",
            WorkerCount = 0,
            ActiveWorkers = 0,
            LocalProcessCount = 0,
            Frontier = new DaemonFrontierSnapshotViewModel(),
        });
    }

    public async Task<bool> StartDaemonAsync()
    {
        LastError = null;
        if (!await EnsureDaemonConnectedAsync())
        {
            return false;
        }

        await PushPersistedStateToDaemonAsync();
        var direct = await RequestAsync<object>("start-daemon", payload: null, ensureConnected: false);
        if (direct is not null)
        {
            LastError = null;
            return true;
        }

        return await EnqueueCommandAsync("start-daemon", null);
    }

    public Task<bool> StopDaemonAsync()
    {
        LastError = null;
        return ExecuteCommandAsync("stop-daemon", null);
    }

    public Task<bool> ReloadDaemonAsync()
    {
        LastError = null;
        return ExecuteCommandAsync("reload-daemon", null);
    }

    public async Task<WorkerViewModel?> SpawnWorkerAsync(
        string? name = null,
        int? daemonGroupId = null,
        string? mode = null,
        IReadOnlyList<string>? seedUrls = null)
    {
        LastError = null;
        if (!await EnsureDaemonConnectedAsync())
        {
            return null;
        }

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

        var response = await RequestAsync<WorkerViewModel>("spawn-worker", payload);
        if (response is null)
        {
            return null;
        }

        if (normalizedSeedUrls.Count > 0)
        {
            await PersistSeedUrlsAsync(response.Id, normalizedSeedUrls);
        }

        return response;
    }

    public Task<List<WorkerViewModel>> GetAllWorkersAsync()
    {
        LastError = null;
        var snapshot = GetSnapshot();
        var workers = snapshot?.Workers.Select(CloneWorker).ToList() ?? new List<WorkerViewModel>();
        return Task.FromResult(workers);
    }

    public Task<WorkerViewModel?> GetWorkerAsync(int id)
    {
        LastError = null;
        var snapshot = GetSnapshot();
        var worker = snapshot?.Workers.FirstOrDefault(item => item.Id == id);
        return Task.FromResult(worker is null ? null : CloneWorker(worker));
    }

    public Task<bool> StartWorkerAsync(int id)
    {
        LastError = null;
        return ExecuteCommandAsync("start-worker", id);
    }

    public Task<bool> StopWorkerAsync(int id)
    {
        LastError = null;
        return ExecuteCommandAsync("stop-worker", id);
    }

    public Task<bool> PauseWorkerAsync(int id)
    {
        LastError = null;
        return ExecuteCommandAsync("pause-worker", id);
    }

    public async Task<Dictionary<string, int>> GetWorkerStatusCountsAsync()
    {
        var workers = await GetAllWorkersAsync();
        return workers
            .Where(worker => !string.IsNullOrWhiteSpace(worker.Status))
            .GroupBy(worker => NormalizeStatus(worker.Status))
            .ToDictionary(group => group.Key, group => group.Count());
    }

    public async Task<WorkerDetailViewModel?> GetWorkerDetailAsync(int id)
    {
        LastError = null;
        var snapshot = GetSnapshot();
        var worker = snapshot?.Workers.FirstOrDefault(item => item.Id == id);
        if (worker is null)
        {
            return null;
        }

        var groupName = snapshot?.Groups.FirstOrDefault(group => group.WorkerIds.Contains(id))?.Name;
        return new WorkerDetailViewModel
        {
            Worker = CloneWorker(worker),
            GroupName = groupName,
            RuntimeConfig = new Dictionary<string, string>(worker.RuntimeConfig, StringComparer.OrdinalIgnoreCase),
        };
    }

    public async Task<WorkerGlobalConfigViewModel> GetGlobalConfigAsync()
    {
        LastError = null;
        var snapshot = GetSnapshot();
        if (snapshot is not null)
        {
            return CloneGlobalConfig(snapshot.GlobalConfig);
        }

        return await LoadGlobalConfigAsync() ?? new WorkerGlobalConfigViewModel();
    }

    public async Task SaveGlobalConfigAsync(WorkerGlobalConfigViewModel config)
    {
        LastError = null;
        NormalizeGlobalConfig(config);
        await SaveJsonGlobalSettingAsync("crawler.global_config", config, updatedBy: "manager-ui");

        if (_daemonChannel.IsConnected(GetDaemonId()))
        {
            var result = await RequestAsync<WorkerGlobalConfigViewModel>("save-global-config", config, ensureConnected: false);
            if (result is null && string.IsNullOrWhiteSpace(LastError))
            {
                LastError = "Failed to push global config to daemon.";
            }
        }
    }

    public async Task<List<WorkerGroupSettingsViewModel>> GetWorkerGroupsAsync()
    {
        LastError = null;
        var snapshot = GetSnapshot();
        if (snapshot is not null && snapshot.Groups.Count > 0)
        {
            return snapshot.Groups.Select(CloneGroup).ToList();
        }

        var persisted = await LoadWorkerGroupsAsync();
        if (persisted.Count > 0)
        {
            return persisted;
        }

        return new List<WorkerGroupSettingsViewModel>
        {
            new()
            {
                Id = 1,
                Name = "Local Daemon",
                Description = "Default local crawler daemon instance.",
                Enabled = true,
                MaxPagesPerWorker = 5000,
                RateLimitPerMinute = 240,
                QueueMode = "both",
                StrategyMode = "balanced",
                TopicKeywords = new List<string> { "medicine", "health", "clinic" },
                TopicKeywordsText = "medicine\nhealth\nclinic",
                AvoidDuplicatePathsAcrossDaemons = true,
                WorkerIds = new List<int>(),
            },
        };
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

        var groups = await GetWorkerGroupsAsync();
        var existingIndex = groups.FindIndex(item => item.Id == group.Id);
        if (existingIndex >= 0)
        {
            groups[existingIndex] = CloneGroup(group);
        }
        else
        {
            groups.Add(CloneGroup(group));
        }

        await SaveJsonGlobalSettingAsync("crawler.worker_groups", groups, updatedBy: "manager-ui");

        if (_daemonChannel.IsConnected(GetDaemonId()))
        {
            var response = await RequestAsync<WorkerGroupSettingsViewModel>("save-group", group, ensureConnected: false);
            return response is not null;
        }

        return true;
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

        var response = await RequestAsync<object>("add-seed", new
        {
            url = normalized,
            workerId,
        });
        return response is not null;
    }

    public Task<FrontierClaimViewModel?> ClaimFrontierUrlAsync(int workerId)
    {
        LastError = null;
        return RequestAsync<FrontierClaimViewModel>("claim-frontier", new
        {
            workerId,
        });
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

        var response = await RequestAsync<object>("complete-frontier", new
        {
            workerId,
            url = normalizedUrl,
            leaseToken,
            status = string.IsNullOrWhiteSpace(status) ? "completed" : status.Trim().ToLowerInvariant(),
        });
        return response is not null;
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

        var response = await RequestAsync<object>("prune-frontier", new
        {
            workerId,
            url = normalizedUrl,
            reason = string.IsNullOrWhiteSpace(reason) ? "server-conflict" : reason.Trim(),
        });
        return response is not null;
    }

    public async Task<FrontierStatusViewModel?> GetFrontierStatusAsync()
    {
        LastError = null;
        var snapshot = GetSnapshot();
        if (snapshot is not null)
        {
            return CloneFrontierStatus(snapshot.FrontierStatus);
        }

        return await RequestAsync<FrontierStatusViewModel>("get-frontier-status", payload: null, ensureConnected: false);
    }

    public Task<FrontierDequeueBatchViewModel?> DequeueFrontierAsync(
        IReadOnlyList<int>? workerIds = null,
        int limit = 20,
        string? daemonId = null)
    {
        LastError = null;
        return RequestAsync<FrontierDequeueBatchViewModel>("dequeue-frontier", new
        {
            workerIds = workerIds?.Distinct().ToArray() ?? Array.Empty<int>(),
            limit = Math.Clamp(limit, 1, 100),
            daemonId,
        });
    }

    public Task<List<CrawlerEventViewModel>> GetRecentCrawlerEventsAsync(int limit = 40)
    {
        LastError = null;
        var boundedLimit = Math.Clamp(limit, 1, 200);
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
                await using var reader = await cmd.ExecuteReaderAsync();
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
            diagnostics.LastFailure = await failureCmd.ExecuteScalarAsync() as string;
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
                    DaemonId = reader.IsDBNull(3) ? GetDaemonId() : reader.GetString(3),
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

    private async Task<T?> RequestAsync<T>(string action, object? payload, bool ensureConnected = true)
    {
        if (ensureConnected && !await EnsureDaemonConnectedAsync())
        {
            return default;
        }

        var daemonId = GetDaemonId();
        var response = await _daemonChannel.SendRequestAsync<T>(daemonId, action, payload);
        if (!response.Ok)
        {
            LastError = response.Error;
            return default;
        }

        LastError = null;
        return response.Data;
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

            if (string.Equals(commandType, "start-daemon", StringComparison.OrdinalIgnoreCase)
                && !await EnsureDaemonConnectedAsync())
            {
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

            LastError = null;
            return true;
        }
        catch (Exception ex)
        {
            LastError = $"Failed to enqueue command '{commandType}': {ex.Message}";
            _logger.LogWarning(ex, "Failed to enqueue command {CommandType}", commandType);
            return false;
        }
    }

    private async Task<bool> ExecuteCommandAsync(string commandType, int? workerId)
    {
        var payload = workerId.HasValue
            ? new { workerId }
            : null;

        var action = commandType switch
        {
            "start-daemon" => "start-daemon",
            "stop-daemon" => "stop-daemon",
            "reload-daemon" => "reload-daemon",
            "start-worker" => "start-worker",
            "stop-worker" => "stop-worker",
            "pause-worker" => "pause-worker",
            _ => null,
        };

        if (string.Equals(commandType, "start-worker", StringComparison.OrdinalIgnoreCase)
            || string.Equals(commandType, "stop-worker", StringComparison.OrdinalIgnoreCase)
            || string.Equals(commandType, "pause-worker", StringComparison.OrdinalIgnoreCase)
            || string.Equals(commandType, "reload-daemon", StringComparison.OrdinalIgnoreCase)
            || string.Equals(commandType, "stop-daemon", StringComparison.OrdinalIgnoreCase))
        {
            if (!await EnsureDaemonConnectedAsync())
            {
                return false;
            }
        }

        if (!string.IsNullOrWhiteSpace(action))
        {
            var daemonId = GetDaemonId();
            var direct = await _daemonChannel.SendRequestAsync<JsonElement>(daemonId, action, payload);
            if (direct.Ok)
            {
                LastError = null;
                return true;
            }

            if (IsTransientRequestFailure(direct.Error))
            {
                await Task.Delay(300);
                if (await EnsureDaemonConnectedAsync())
                {
                    var retry = await _daemonChannel.SendRequestAsync<JsonElement>(daemonId, action, payload);
                    if (retry.Ok)
                    {
                        LastError = null;
                        return true;
                    }

                    LastError = retry.Error;
                    return false;
                }
            }

            LastError = direct.Error;
            return false;
        }

        return await EnqueueCommandAsync(commandType, workerId);
    }

    private static bool IsTransientRequestFailure(string? error)
    {
        if (string.IsNullOrWhiteSpace(error))
        {
            return false;
        }

        return error.Contains("Timed out", StringComparison.OrdinalIgnoreCase)
            || error.Contains("not connected", StringComparison.OrdinalIgnoreCase)
            || error.Contains("disconnected", StringComparison.OrdinalIgnoreCase);
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

    private async Task<bool> EnsureDaemonConnectedAsync()
    {
        var daemonId = GetDaemonId();
        if (_daemonChannel.IsConnected(daemonId))
        {
            return true;
        }

        if (!IsLocalDaemon())
        {
            LastError = $"Daemon '{daemonId}' is not connected.";
            return false;
        }

        await _daemonStartGate.WaitAsync();
        try
        {
            if (_daemonChannel.IsConnected(daemonId))
            {
                return true;
            }

            // Prevent repeated button clicks/refreshes from spawning many local daemon processes.
            if (DateTime.UtcNow - _lastLocalDaemonLaunchAttemptUtc < TimeSpan.FromSeconds(10))
            {
                if (await _daemonChannel.WaitForConnectionAsync(daemonId, TimeSpan.FromSeconds(2), CancellationToken.None))
                {
                    LastError = null;
                    return true;
                }

                LastError = $"Daemon '{daemonId}' is still starting. Please retry in a few seconds.";
                return false;
            }

            _lastLocalDaemonLaunchAttemptUtc = DateTime.UtcNow;

            var managerDir = Directory.GetCurrentDirectory();
            var repoRoot = Path.GetFullPath(Path.Combine(managerDir, ".."));
            var daemonArgs = _configuration["CrawlerApi:LocalDaemonArgs"] ?? "pa1/crawler/src/main.py";
            var managerHttpBaseUrl = ResolveManagerHttpBaseUrl();
            var wsUrl = ResolveManagerSocketUrl(managerHttpBaseUrl, daemonId);

            foreach (var pythonExe in ResolvePythonCandidates(repoRoot))
            {
                try
                {
                    var startInfo = new ProcessStartInfo
                    {
                        FileName = pythonExe,
                        Arguments = daemonArgs,
                        WorkingDirectory = repoRoot,
                        UseShellExecute = false,
                        RedirectStandardOutput = false,
                        RedirectStandardError = false,
                    };
                    startInfo.Environment["CRAWLER_DAEMON_ID"] = daemonId;
                    startInfo.Environment["MANAGER_DAEMON_WS_URL"] = wsUrl;
                    var daemonChannelToken = (_configuration["CrawlerApi:DaemonChannelToken"] ?? string.Empty).Trim();
                    if (!string.IsNullOrWhiteSpace(daemonChannelToken))
                    {
                        startInfo.Environment["MANAGER_DAEMON_WS_TOKEN"] = daemonChannelToken;
                    }
                    startInfo.Environment["MANAGER_INGEST_API_URL"] = managerHttpBaseUrl.TrimEnd('/') + "/api/crawler/ingest";
                    startInfo.Environment["MANAGER_EVENT_API_URL"] = managerHttpBaseUrl.TrimEnd('/') + "/api/crawler/events";
                    startInfo.Environment["MANAGER_PARENT_PID"] = Environment.ProcessId.ToString();

                    Process.Start(startInfo);
                    _logger.LogInformation("Triggered on-demand daemon startup using {PythonExe}", pythonExe);

                    if (await _daemonChannel.WaitForConnectionAsync(daemonId, TimeSpan.FromSeconds(12), CancellationToken.None))
                    {
                        LastError = null;
                        return true;
                    }

                    LastError = $"Daemon '{daemonId}' launched but did not connect to manager websocket in time.";
                    _logger.LogWarning(
                        "Daemon launch did not connect in time. daemonId={DaemonId} wsUrl={WsUrl} executable={PythonExe}",
                        daemonId,
                        wsUrl,
                        pythonExe);
                    return false;
                }
                catch (Exception ex)
                {
                    _logger.LogDebug(ex, "Failed to trigger on-demand daemon startup using {PythonExe}", pythonExe);
                }
            }
        }
        finally
        {
            _daemonStartGate.Release();
        }

        LastError = $"Daemon '{daemonId}' is not connected.";
        return false;
    }

    private async Task PushPersistedStateToDaemonAsync()
    {
        var daemonId = GetDaemonId();
        if (!_daemonChannel.IsConnected(daemonId))
        {
            return;
        }

        var globalConfig = await LoadGlobalConfigAsync();
        if (globalConfig is not null)
        {
            var response = await _daemonChannel.SendRequestAsync<WorkerGlobalConfigViewModel>(daemonId, "save-global-config", globalConfig);
            if (!response.Ok)
            {
                _logger.LogDebug("Failed to sync persisted global config to daemon {DaemonId}: {Error}", daemonId, response.Error);
            }
        }

        var groups = await LoadWorkerGroupsAsync();
        foreach (var group in groups)
        {
            var response = await _daemonChannel.SendRequestAsync<WorkerGroupSettingsViewModel>(daemonId, "save-group", group);
            if (!response.Ok)
            {
                _logger.LogDebug("Failed to sync persisted group {GroupId} to daemon {DaemonId}: {Error}", group.Id, daemonId, response.Error);
            }
        }
    }

    private async Task<WorkerGlobalConfigViewModel?> LoadGlobalConfigAsync()
    {
        return await LoadJsonGlobalSettingAsync<WorkerGlobalConfigViewModel>("crawler.global_config");
    }

    private async Task<List<WorkerGroupSettingsViewModel>> LoadWorkerGroupsAsync()
    {
        return await LoadJsonGlobalSettingAsync<List<WorkerGroupSettingsViewModel>>("crawler.worker_groups")
            ?? new List<WorkerGroupSettingsViewModel>();
    }

    private async Task<T?> LoadJsonGlobalSettingAsync<T>(string key)
    {
        try
        {
            var connectionString = _configuration.GetConnectionString("CrawldbConnection");
            if (string.IsNullOrWhiteSpace(connectionString))
            {
                return default;
            }

            await using var connection = new NpgsqlConnection(connectionString);
            await connection.OpenAsync();

            const string sql = """
                SELECT value::text
                FROM manager.global_setting
                WHERE key = @key;
                """;

            await using var cmd = new NpgsqlCommand(sql, connection);
            cmd.Parameters.AddWithValue("key", key);
            var raw = await cmd.ExecuteScalarAsync() as string;
            if (string.IsNullOrWhiteSpace(raw))
            {
                return default;
            }

            return JsonSerializer.Deserialize<T>(raw, JsonOptions);
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Failed to load manager global setting {Key}", key);
            return default;
        }
    }

    private async Task SaveJsonGlobalSettingAsync<T>(string key, T value, string updatedBy)
    {
        var connectionString = _configuration.GetConnectionString("CrawldbConnection");
        if (string.IsNullOrWhiteSpace(connectionString))
        {
            return;
        }

        var json = JsonSerializer.Serialize(value);
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync();

        const string sql = """
            INSERT INTO manager.global_setting (key, value, updated_at, updated_by)
            VALUES (@key, @value::jsonb, now(), @updated_by)
            ON CONFLICT (key)
            DO UPDATE SET value = EXCLUDED.value,
                          updated_at = now(),
                          updated_by = EXCLUDED.updated_by;
            """;

        await using var cmd = new NpgsqlCommand(sql, connection);
        cmd.Parameters.AddWithValue("key", key);
        cmd.Parameters.AddWithValue("value", json);
        cmd.Parameters.AddWithValue("updated_by", updatedBy);
        await cmd.ExecuteNonQueryAsync();
    }

    private DaemonChannelService.DaemonSnapshot? GetSnapshot()
    {
        var daemonId = GetDaemonId();
        return _daemonChannel.GetSnapshot(daemonId) ?? _daemonChannel.GetLatestSnapshot();
    }

    private string GetDaemonId()
    {
        var configured = _configuration["CrawlerApi:LocalDaemonId"];
        return string.IsNullOrWhiteSpace(configured) ? "local-default" : configured.Trim();
    }

    private bool IsLocalDaemon()
    {
        var baseUrl = _configuration["CrawlerApi:BaseUrl"] ?? "http://127.0.0.1:8090";
        return baseUrl.Contains("127.0.0.1", StringComparison.OrdinalIgnoreCase)
            || baseUrl.Contains("localhost", StringComparison.OrdinalIgnoreCase);
    }

    private IEnumerable<string> ResolvePythonCandidates(string repoRoot)
    {
        var emitted = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        static bool TryAdd(HashSet<string> set, string? candidate)
        {
            return !string.IsNullOrWhiteSpace(candidate) && set.Add(candidate);
        }

        var venvPython = Path.Combine(repoRoot, ".venv", "bin", "python");
        var hasVenvPython = File.Exists(venvPython);
        if (hasVenvPython && TryAdd(emitted, venvPython))
        {
            yield return venvPython;
        }

        var configured = _configuration["CrawlerApi:PythonExecutable"];
        if (!string.IsNullOrWhiteSpace(configured))
        {
            string candidate;
            if (Path.IsPathRooted(configured))
            {
                candidate = configured;
            }
            else
            {
                var configuredRelative = Path.Combine(repoRoot, configured);
                if (File.Exists(configuredRelative))
                {
                    candidate = configuredRelative;
                }
                else
                {
                    candidate = configured;
                }
            }

            if (TryAdd(emitted, candidate))
            {
                yield return candidate;
            }
        }

        if (!hasVenvPython)
        {
            if (TryAdd(emitted, "python3"))
            {
                yield return "python3";
            }

            if (TryAdd(emitted, "python"))
            {
                yield return "python";
            }
        }
    }

    private string ResolveManagerHttpBaseUrl()
    {
        var configuredHttp = _configuration["CrawlerApi:ManagerBaseUrl"];
        if (!string.IsNullOrWhiteSpace(configuredHttp))
        {
            return NormalizeHttpUrlCandidate(configuredHttp) ?? configuredHttp.TrimEnd('/');
        }

        var configuredSocket = _configuration["CrawlerApi:ManagerSocketUrl"];
        if (!string.IsNullOrWhiteSpace(configuredSocket))
        {
            if (configuredSocket.StartsWith("ws://", StringComparison.OrdinalIgnoreCase))
            {
                return "http://" + configuredSocket[5..].TrimEnd('/').Replace("/api/daemon-channel", string.Empty);
            }

            if (configuredSocket.StartsWith("wss://", StringComparison.OrdinalIgnoreCase))
            {
                return "https://" + configuredSocket[6..].TrimEnd('/').Replace("/api/daemon-channel", string.Empty);
            }
        }

        var urls = Environment.GetEnvironmentVariable("ASPNETCORE_URLS");
        if (string.IsNullOrWhiteSpace(urls))
        {
            // `dotnet run --urls ...` may be available only through configuration keys.
            urls = _configuration["ASPNETCORE_URLS"]
                ?? _configuration["URLS"]
                ?? _configuration["urls"];
        }
        if (!string.IsNullOrWhiteSpace(urls))
        {
            var candidates = urls.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
            var normalized = candidates
                .Select(NormalizeHttpUrlCandidate)
                .Where(url => !string.IsNullOrWhiteSpace(url))
                .ToList();

            var httpUrl = normalized.FirstOrDefault(url => url!.StartsWith("http://", StringComparison.OrdinalIgnoreCase));
            if (!string.IsNullOrWhiteSpace(httpUrl))
            {
                return httpUrl!;
            }

            var httpsUrl = normalized.FirstOrDefault(url => url!.StartsWith("https://", StringComparison.OrdinalIgnoreCase));
            if (!string.IsNullOrWhiteSpace(httpsUrl))
            {
                return httpsUrl!;
            }
        }

        return "http://127.0.0.1:5000";
    }

    private string ResolveManagerSocketUrl(string managerHttpBaseUrl, string daemonId)
    {
        var normalizedBaseUrl = NormalizeHttpUrlCandidate(managerHttpBaseUrl) ?? managerHttpBaseUrl.TrimEnd('/');

        if (normalizedBaseUrl.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
        {
            return $"wss://{normalizedBaseUrl[8..].TrimEnd('/')}/api/daemon-channel?daemonId={daemonId}";
        }

        if (normalizedBaseUrl.StartsWith("http://", StringComparison.OrdinalIgnoreCase))
        {
            return $"ws://{normalizedBaseUrl[7..].TrimEnd('/')}/api/daemon-channel?daemonId={daemonId}";
        }

        return $"ws://127.0.0.1:5000/api/daemon-channel?daemonId={daemonId}";
    }

    private static string? NormalizeHttpUrlCandidate(string? candidate)
    {
        if (string.IsNullOrWhiteSpace(candidate))
        {
            return null;
        }

        var value = candidate.Trim().TrimEnd('/');
        if (value.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
            || value.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
        {
            return value;
        }

        if (value.StartsWith("localhost", StringComparison.OrdinalIgnoreCase)
            || value.StartsWith("127.0.0.1", StringComparison.OrdinalIgnoreCase)
            || value.StartsWith("0.0.0.0", StringComparison.OrdinalIgnoreCase))
        {
            return "http://" + value;
        }

        return null;
    }

    private static void NormalizeGlobalConfig(WorkerGlobalConfigViewModel config)
    {
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

    private static WorkerViewModel CloneWorker(WorkerViewModel worker)
    {
        return new WorkerViewModel
        {
            Id = worker.Id,
            Name = worker.Name,
            Status = worker.Status,
            StatusReason = worker.StatusReason,
            CurrentUrl = worker.CurrentUrl,
            PagesProcessed = worker.PagesProcessed,
            ErrorCount = worker.ErrorCount,
            StartedAt = worker.StartedAt,
            Mode = worker.Mode,
            Pid = worker.Pid,
            RuntimeConfig = new Dictionary<string, string>(worker.RuntimeConfig, StringComparer.OrdinalIgnoreCase),
        };
    }

    private static WorkerGlobalConfigViewModel CloneGlobalConfig(WorkerGlobalConfigViewModel config)
    {
        return new WorkerGlobalConfigViewModel
        {
            MaxConcurrentWorkers = config.MaxConcurrentWorkers,
            RequestTimeoutSeconds = config.RequestTimeoutSeconds,
            CrawlDelayMilliseconds = config.CrawlDelayMilliseconds,
            RespectRobotsTxt = config.RespectRobotsTxt,
            UserAgent = config.UserAgent,
            SeedUrlsText = config.SeedUrlsText,
            SeedEntries = config.SeedEntries.Select(entry => new SeedEntryViewModel
            {
                Url = entry.Url,
                Enabled = entry.Enabled,
                Label = entry.Label,
            }).ToList(),
            QueueMode = config.QueueMode,
            StrategyMode = config.StrategyMode,
            ScoreFunction = config.ScoreFunction,
            ScoreWeightPages = config.ScoreWeightPages,
            ScoreWeightErrors = config.ScoreWeightErrors,
            TopicKeywords = new List<string>(config.TopicKeywords),
            TopicKeywordsText = config.TopicKeywordsText,
            RelevanceAllowedDomainSuffixes = new List<string>(config.RelevanceAllowedDomainSuffixes),
            RelevanceAllowedDomainSuffixesText = config.RelevanceAllowedDomainSuffixesText,
            RelevanceSameHostBoost = config.RelevanceSameHostBoost,
            RelevanceAllowedSuffixBoost = config.RelevanceAllowedSuffixBoost,
            RelevanceKeywordBoost = config.RelevanceKeywordBoost,
            RelevanceDepthPenalty = config.RelevanceDepthPenalty,
            MaxFrontierInMemory = config.MaxFrontierInMemory,
            AvoidDuplicatePathsAcrossDaemons = config.AvoidDuplicatePathsAcrossDaemons,
        };
    }

    private static WorkerGroupSettingsViewModel CloneGroup(WorkerGroupSettingsViewModel group)
    {
        return new WorkerGroupSettingsViewModel
        {
            Id = group.Id,
            Name = group.Name,
            Description = group.Description,
            Enabled = group.Enabled,
            MaxPagesPerWorker = group.MaxPagesPerWorker,
            RateLimitPerMinute = group.RateLimitPerMinute,
            QueueMode = group.QueueMode,
            StrategyMode = group.StrategyMode,
            TopicKeywords = new List<string>(group.TopicKeywords),
            TopicKeywordsText = group.TopicKeywordsText,
            AvoidDuplicatePathsAcrossDaemons = group.AvoidDuplicatePathsAcrossDaemons,
            WorkerIds = new List<int>(group.WorkerIds),
        };
    }

    private static FrontierStatusViewModel CloneFrontierStatus(FrontierStatusViewModel status)
    {
        return new FrontierStatusViewModel
        {
            InMemoryQueued = status.InMemoryQueued,
            KnownUrls = status.KnownUrls,
            LocalQueued = status.LocalQueued,
            ActiveLeases = status.ActiveLeases,
            Tombstones = status.Tombstones,
            LeaseTtlSeconds = status.LeaseTtlSeconds,
            RelayEnabled = status.RelayEnabled,
        };
    }

    private static DaemonStatusViewModel CloneDaemonStatus(DaemonStatusViewModel status)
    {
        return new DaemonStatusViewModel
        {
            Running = status.Running,
            StartedAt = status.StartedAt,
            Mode = status.Mode,
            WorkerCount = status.WorkerCount,
            ActiveWorkers = status.ActiveWorkers,
            LocalProcessCount = status.LocalProcessCount,
            Frontier = new DaemonFrontierSnapshotViewModel
            {
                InMemoryQueued = status.Frontier.InMemoryQueued,
                KnownUrls = status.Frontier.KnownUrls,
                LocalQueued = status.Frontier.LocalQueued,
                ActiveLeases = status.Frontier.ActiveLeases,
                Tombstones = status.Frontier.Tombstones,
            },
        };
    }
}
