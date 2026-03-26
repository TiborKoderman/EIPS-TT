using System.Diagnostics;

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

        StartProcessDaemon();
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

    private void StartProcessDaemon()
    {
        var pythonExe = _configuration["CrawlerApi:PythonExecutable"] ?? "python";
        var daemonArgs = _configuration["CrawlerApi:LocalDaemonArgs"]
            ?? "pa1/crawler/src/main.py --run-api --api-host 127.0.0.1 --api-port 8090";

        var managerDir = Directory.GetCurrentDirectory();
        var repoRoot = Path.GetFullPath(Path.Combine(managerDir, ".."));
        var wsUrl = _configuration["CrawlerApi:ManagerSocketUrl"]
            ?? "ws://127.0.0.1:5150/api/daemon-channel?daemonId=local-default";

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
        _logger.LogInformation("Started local crawler daemon process. PID={Pid}", _process.Id);
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
