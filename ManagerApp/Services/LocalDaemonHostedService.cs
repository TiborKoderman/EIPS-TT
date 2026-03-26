using System.Diagnostics;
using System.Net.Http;

namespace ManagerApp.Services;

public sealed class LocalDaemonHostedService : IHostedService
{
    private readonly IConfiguration _configuration;
    private readonly ILogger<LocalDaemonHostedService> _logger;
    private Process? _process;
    private Process? _dockerStartProcess;

    public LocalDaemonHostedService(IConfiguration configuration, ILogger<LocalDaemonHostedService> logger)
    {
        _configuration = configuration;
        _logger = logger;
    }

    public async Task StartAsync(CancellationToken cancellationToken)
    {
        var autoStart = _configuration.GetValue("CrawlerApi:AutoStartLocalDaemon", true);
        if (!autoStart)
        {
            _logger.LogInformation("Local daemon autostart disabled.");
            return;
        }

        var baseUrl = _configuration["CrawlerApi:BaseUrl"] ?? "http://127.0.0.1:8090";
        if (!baseUrl.Contains("127.0.0.1") && !baseUrl.Contains("localhost"))
        {
            _logger.LogInformation("CrawlerApi BaseUrl is not local ({BaseUrl}); skipping local daemon autostart.", baseUrl);
            return;
        }

        var launcherMode = (_configuration["CrawlerApi:LauncherMode"] ?? "Process").Trim().ToLowerInvariant();
        if (launcherMode == "docker")
        {
            await StartDockerDaemonAsync(cancellationToken);
            return;
        }

        await StartProcessDaemonAsync(cancellationToken);
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
            ?? "pa1/crawler/src/daemon/main.py";
        var baseUrl = _configuration["CrawlerApi:BaseUrl"] ?? "http://127.0.0.1:8090";

        var managerDir = Directory.GetCurrentDirectory();
        var repoRoot = Path.GetFullPath(Path.Combine(managerDir, ".."));
        var wsUrl = ResolveManagerSocketUrl();

        foreach (var pythonExe in ResolvePythonCandidates(repoRoot))
        {
            if (TryStartDaemonProcess(pythonExe, daemonArgs, repoRoot, wsUrl))
            {
                var ready = await WaitForApiReadyAsync(baseUrl, cancellationToken);
                if (ready)
                {
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
                    _logger.LogWarning("Daemon process started but API did not become reachable at {BaseUrl}.", baseUrl);
                }
                return;
            }
        }

        _logger.LogWarning("Failed to start local crawler daemon process with all configured Python candidates.");
    }

    private bool TryStartDaemonProcess(string pythonExe, string daemonArgs, string repoRoot, string wsUrl)
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
            startInfo.Environment["MANAGER_DAEMON_WS_URL"] = wsUrl;
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

    private string ResolveManagerSocketUrl()
    {
        var configured = _configuration["CrawlerApi:ManagerSocketUrl"];
        if (!string.IsNullOrWhiteSpace(configured))
        {
            return configured;
        }

        var urls = Environment.GetEnvironmentVariable("ASPNETCORE_URLS");
        if (!string.IsNullOrWhiteSpace(urls))
        {
            var first = urls.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .FirstOrDefault();
            if (!string.IsNullOrWhiteSpace(first))
            {
                if (first.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                {
                    return "wss://" + first[8..].TrimEnd('/') + "/api/daemon-channel?daemonId=local-default";
                }

                if (first.StartsWith("http://", StringComparison.OrdinalIgnoreCase))
                {
                    return "ws://" + first[7..].TrimEnd('/') + "/api/daemon-channel?daemonId=local-default";
                }
            }
        }

        return "ws://127.0.0.1:5160/api/daemon-channel?daemonId=local-default";
    }

    private static async Task<bool> WaitForApiReadyAsync(string baseUrl, CancellationToken cancellationToken)
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
        var healthUrl = baseUrl.TrimEnd('/') + "/api/health";
        for (var i = 0; i < 20; i++)
        {
            try
            {
                using var response = await client.GetAsync(healthUrl, cancellationToken);
                if (response.IsSuccessStatusCode)
                {
                    return true;
                }
            }
            catch
            {
                // Keep polling until timeout.
            }

            await Task.Delay(400, cancellationToken);
        }

        return false;
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
}
