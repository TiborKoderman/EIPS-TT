using System.Diagnostics;
using System.Net;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;

namespace ManagerApp.Services;

public sealed class LocalDaemonHostedService : IHostedService
{
    private readonly IConfiguration _configuration;
    private readonly ILogger<LocalDaemonHostedService> _logger;
    private readonly DaemonChannelService _daemonChannel;
    private readonly IHostApplicationLifetime _applicationLifetime;
    private readonly IServer _server;
    private Process? _process;
    private Process? _dockerStartProcess;

    public LocalDaemonHostedService(
        IConfiguration configuration,
        ILogger<LocalDaemonHostedService> logger,
        DaemonChannelService daemonChannel,
        IHostApplicationLifetime applicationLifetime,
        IServer server)
    {
        _configuration = configuration;
        _logger = logger;
        _daemonChannel = daemonChannel;
        _applicationLifetime = applicationLifetime;
        _server = server;
    }

    public Task StartAsync(CancellationToken cancellationToken)
    {
        var autoStart = _configuration.GetValue("CrawlerApi:AutoStartLocalDaemon", true);
        if (!autoStart)
        {
            _logger.LogInformation("Local daemon autostart disabled.");
            return Task.CompletedTask;
        }

        var baseUrl = _configuration["CrawlerApi:BaseUrl"] ?? "http://127.0.0.1:8090";
        if (!baseUrl.Contains("127.0.0.1") && !baseUrl.Contains("localhost"))
        {
            _logger.LogInformation("CrawlerApi BaseUrl is not local ({BaseUrl}); skipping local daemon autostart.", baseUrl);
            return Task.CompletedTask;
        }

        _applicationLifetime.ApplicationStarted.Register(() =>
        {
            _ = Task.Run(async () =>
            {
                try
                {
                    var launcherMode = (_configuration["CrawlerApi:LauncherMode"] ?? "Process").Trim().ToLowerInvariant();
                    if (launcherMode == "docker")
                    {
                        await StartDockerDaemonAsync(CancellationToken.None);
                        return;
                    }

                    await StartProcessDaemonAsync(CancellationToken.None);
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(ex, "Failed to autostart local daemon after application startup.");
                }
            });
        });

        return Task.CompletedTask;
    }

    public Task StopAsync(CancellationToken cancellationToken)
    {
        StopProcess(_process, "python daemon");
        StopProcess(_dockerStartProcess, "docker daemon launcher");

        var launcherMode = (_configuration["CrawlerApi:LauncherMode"] ?? "Process").Trim().ToLowerInvariant();
        if (launcherMode == "docker")
        {
            var stopCommand = _configuration["CrawlerApi:DockerStopCommand"]
                ?? "docker compose --profile daemon stop crawler-daemon";
            TryRunShell(stopCommand);
        }

        return Task.CompletedTask;
    }

    private async Task StartProcessDaemonAsync(CancellationToken cancellationToken)
    {
        var daemonArgs = _configuration["CrawlerApi:LocalDaemonArgs"]
            ?? "pa1/crawler/src/main.py";
        var daemonId = ResolveLocalDaemonId();

        var managerDir = Directory.GetCurrentDirectory();
        var repoRoot = Path.GetFullPath(Path.Combine(managerDir, ".."));
        var managerHttpBaseUrl = ResolveManagerHttpBaseUrl();
        var wsUrl = ResolveManagerSocketUrl(managerHttpBaseUrl, daemonId);

        foreach (var pythonExe in ResolvePythonCandidates(repoRoot))
        {
            if (TryStartDaemonProcess(pythonExe, daemonArgs, repoRoot, wsUrl, managerHttpBaseUrl, daemonId))
            {
                var ready = await _daemonChannel.WaitForConnectionAsync(daemonId, TimeSpan.FromSeconds(15), cancellationToken);
                if (ready)
                {
                    await EnsureDaemonInitializedWithWorkerAsync(daemonId);
                    return;
                }

                var exited = _process?.HasExited == true;
                if (exited)
                {
                    _logger.LogWarning("Daemon process exited early using executable {PythonExe}; trying next candidate.", pythonExe);
                    _process?.Dispose();
                    _process = null;
                    continue;
                }

                if (!ready)
                {
                    _logger.LogWarning("Daemon process started but did not establish websocket connection as {DaemonId}.", daemonId);
                }
                return;
            }
        }

        _logger.LogWarning("Failed to start local crawler daemon process with all configured Python candidates.");
    }

    private bool TryStartDaemonProcess(
        string pythonExe,
        string daemonArgs,
        string repoRoot,
        string wsUrl,
        string managerHttpBaseUrl,
        string daemonId)
    {
        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = pythonExe,
                Arguments = daemonArgs,
                WorkingDirectory = repoRoot,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            startInfo.Environment["CRAWLER_DAEMON_ID"] = daemonId;
            startInfo.Environment["MANAGER_DAEMON_WS_URL"] = wsUrl;
            var daemonChannelToken = (_configuration["CrawlerApi:DaemonChannelToken"] ?? string.Empty).Trim();
            var frontierApiToken = (_configuration["CrawlerApi:FrontierApiToken"] ?? daemonChannelToken).Trim();
            if (!string.IsNullOrWhiteSpace(daemonChannelToken))
            {
                startInfo.Environment["MANAGER_DAEMON_WS_TOKEN"] = daemonChannelToken;
            }
            startInfo.Environment["MANAGER_INGEST_API_URL"] = managerHttpBaseUrl.TrimEnd('/') + "/api/crawler/ingest";
            startInfo.Environment["MANAGER_EVENT_API_URL"] = managerHttpBaseUrl.TrimEnd('/') + "/api/crawler/events";
            startInfo.Environment["MANAGER_FRONTIER_INGEST_URL"] = managerHttpBaseUrl.TrimEnd('/') + "/api/frontier/seed";
            startInfo.Environment["MANAGER_FRONTIER_CLAIM_URL"] = managerHttpBaseUrl.TrimEnd('/') + "/api/frontier/claim";
            startInfo.Environment["MANAGER_FRONTIER_COMPLETE_URL"] = managerHttpBaseUrl.TrimEnd('/') + "/api/frontier/complete";
            startInfo.Environment["MANAGER_FRONTIER_STATUS_URL"] = managerHttpBaseUrl.TrimEnd('/') + "/api/frontier/status";
            startInfo.Environment["CRAWLER_DAEMON_ALLOW_LOCAL_FALLBACK"] = "false";
            if (!string.IsNullOrWhiteSpace(frontierApiToken))
            {
                startInfo.Environment["MANAGER_FRONTIER_INGEST_TOKEN"] = frontierApiToken;
                startInfo.Environment["MANAGER_INGEST_API_TOKEN"] = frontierApiToken;
            }
            startInfo.Environment["MANAGER_PARENT_PID"] = Environment.ProcessId.ToString();

            _process = new Process
            {
                StartInfo = startInfo,
                EnableRaisingEvents = true,
            };
            _process.OutputDataReceived += (_, e) =>
            {
                if (!string.IsNullOrWhiteSpace(e.Data))
                {
                    _logger.LogInformation("[daemon] {Line}", e.Data);
                }
            };
            _process.ErrorDataReceived += (_, e) =>
            {
                if (!string.IsNullOrWhiteSpace(e.Data))
                {
                    _logger.LogWarning("[daemon] {Line}", e.Data);
                }
            };

            _process.Start();
            _process.BeginOutputReadLine();
            _process.BeginErrorReadLine();
            _logger.LogInformation("Started local crawler daemon process with executable {PythonExe}. PID={Pid}", pythonExe, _process.Id);
            return true;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to start daemon using executable {PythonExe}", pythonExe);
            return false;
        }
    }

    private IEnumerable<string> ResolvePythonCandidates(string repoRoot)
    {
        var configured = _configuration["CrawlerApi:PythonExecutable"];
        if (!string.IsNullOrWhiteSpace(configured))
        {
            if (Path.IsPathRooted(configured))
            {
                yield return configured;
            }
            else
            {
                var configuredRelative = Path.Combine(repoRoot, configured);
                if (File.Exists(configuredRelative))
                {
                    yield return configuredRelative;
                }
                else
                {
                    yield return configured;
                }
            }
        }

        var venvPython = Path.Combine(repoRoot, ".venv", "bin", "python");
        if (File.Exists(venvPython))
        {
            yield return venvPython;
        }

        var relativeVenvPython = Path.Combine(repoRoot, ".venv", "bin", "python");
        if (File.Exists(relativeVenvPython))
        {
            yield return relativeVenvPython;
        }

        yield return "python3";
        yield return "python";
    }

    private string ResolveManagerHttpBaseUrl()
    {
        var configuredHttp = _configuration["CrawlerApi:ManagerBaseUrl"];
        if (!string.IsNullOrWhiteSpace(configuredHttp))
        {
            return NormalizeHttpUrlCandidate(configuredHttp) ?? configuredHttp.TrimEnd('/');
        }

        var addressesFeature = _server.Features.Get<IServerAddressesFeature>();
        if (addressesFeature is not null)
        {
            var runtimeAddress = addressesFeature.Addresses
                .Select(NormalizeHttpUrlCandidate)
                .FirstOrDefault(url => !string.IsNullOrWhiteSpace(url));
            if (!string.IsNullOrWhiteSpace(runtimeAddress))
            {
                return runtimeAddress!;
            }
        }

        var configured = _configuration["CrawlerApi:ManagerSocketUrl"];
        if (!string.IsNullOrWhiteSpace(configured))
        {
            if (configured.StartsWith("ws://", StringComparison.OrdinalIgnoreCase))
            {
                return "http://" + configured[5..].TrimEnd('/').Replace("/api/daemon-channel", string.Empty);
            }

            if (configured.StartsWith("wss://", StringComparison.OrdinalIgnoreCase))
            {
                return "https://" + configured[6..].TrimEnd('/').Replace("/api/daemon-channel", string.Empty);
            }
        }

        var urls = Environment.GetEnvironmentVariable("ASPNETCORE_URLS");
        if (string.IsNullOrWhiteSpace(urls))
        {
            // `dotnet run --urls ...` is exposed via configuration even when env var is unset.
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

        // Fallback to the default `dotnet run` Kestrel HTTP port.
        return "http://127.0.0.1:5000";
    }

    private string ResolveManagerSocketUrl(string managerHttpBaseUrl, string daemonId)
    {
        var configuredSocket = _configuration["CrawlerApi:ManagerSocketUrl"];
        if (!string.IsNullOrWhiteSpace(configuredSocket))
        {
            return configuredSocket;
        }

        var normalizedBaseUrl = NormalizeHttpUrlCandidate(managerHttpBaseUrl) ?? managerHttpBaseUrl.TrimEnd('/');

        if (normalizedBaseUrl.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
        {
            var encodedDaemonId = WebUtility.UrlEncode(daemonId);
            return "wss://" + normalizedBaseUrl[8..].TrimEnd('/') + $"/api/daemon-channel?daemonId={encodedDaemonId}";
        }

        if (normalizedBaseUrl.StartsWith("http://", StringComparison.OrdinalIgnoreCase))
        {
            var encodedDaemonId = WebUtility.UrlEncode(daemonId);
            return "ws://" + normalizedBaseUrl[7..].TrimEnd('/') + $"/api/daemon-channel?daemonId={encodedDaemonId}";
        }

        return "ws://127.0.0.1:5000/api/daemon-channel?daemonId=local-default";
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

    private string ResolveLocalDaemonId()
    {
        var configured = _configuration["CrawlerApi:LocalDaemonId"];
        return string.IsNullOrWhiteSpace(configured) ? "local-default" : configured.Trim();
    }

    private async Task StartDockerDaemonAsync(CancellationToken cancellationToken)
    {
        var managerDir = Directory.GetCurrentDirectory();
        var repoRoot = Path.GetFullPath(Path.Combine(managerDir, ".."));
        var command = _configuration["CrawlerApi:DockerStartCommand"]
            ?? "docker compose --profile daemon up -d crawler-daemon";

        _dockerStartProcess = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = "bash",
                Arguments = $"-lc \"{command}\"",
                WorkingDirectory = repoRoot,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            }
        };

        _dockerStartProcess.Start();
        await _dockerStartProcess.WaitForExitAsync(cancellationToken);
        if (_dockerStartProcess.ExitCode == 0)
        {
            _logger.LogInformation("Started docker daemon using command: {Command}", command);
            var daemonId = ResolveLocalDaemonId();
            var ready = await _daemonChannel.WaitForConnectionAsync(daemonId, TimeSpan.FromSeconds(20), cancellationToken);
            if (ready)
            {
                await EnsureDaemonInitializedWithWorkerAsync(daemonId);
            }
            else
            {
                _logger.LogWarning("Docker daemon started but did not connect to manager websocket as {DaemonId}.", daemonId);
            }
        }
        else
        {
            _logger.LogWarning("Docker daemon start command failed with exit code {ExitCode}: {Command}", _dockerStartProcess.ExitCode, command);
        }
    }

    private void TryRunShell(string command)
    {
        try
        {
            var managerDir = Directory.GetCurrentDirectory();
            var repoRoot = Path.GetFullPath(Path.Combine(managerDir, ".."));
            using var process = Process.Start(new ProcessStartInfo
            {
                FileName = "bash",
                Arguments = $"-lc \"{command}\"",
                WorkingDirectory = repoRoot,
                UseShellExecute = false,
            });
            process?.WaitForExit(5000);
            _logger.LogInformation("Ran shell command: {Command}", command);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to run shell command: {Command}", command);
        }
    }

    private void StopProcess(Process? process, string label)
    {
        if (process == null)
        {
            return;
        }

        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                process.WaitForExit(5000);
                _logger.LogInformation("Stopped {Label} process.", label);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to stop {Label} process cleanly.", label);
        }
    }

    private async Task EnsureDaemonInitializedWithWorkerAsync(string daemonId)
    {
        var shouldEnsure = _configuration.GetValue("CrawlerApi:EnsureLocalDaemonWorkerOnStartup", true);
        if (!shouldEnsure)
        {
            return;
        }

        var daemonStart = await _daemonChannel.SendRequestAsync<object>(daemonId, "start-daemon", payload: null, timeout: TimeSpan.FromSeconds(12));
        if (!daemonStart.Ok)
        {
            _logger.LogWarning("Failed to start daemon '{DaemonId}' during initialization: {Error}", daemonId, daemonStart.Error);
            return;
        }

        var workerStart = await _daemonChannel.SendRequestAsync<object>(daemonId, "start-worker", new { workerId = 1 }, timeout: TimeSpan.FromSeconds(12));
        if (!workerStart.Ok)
        {
            _logger.LogWarning(
                "Daemon '{DaemonId}' initialized but worker 1 start failed during bootstrap: {Error}",
                daemonId,
                workerStart.Error);
            return;
        }

        _logger.LogInformation("Local daemon '{DaemonId}' initialized with worker 1 active.", daemonId);
    }
}
