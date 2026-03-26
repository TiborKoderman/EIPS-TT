using Npgsql;
using System.Text.Json;

namespace ManagerApp.Services;

public sealed class CommandDispatchHostedService : BackgroundService
{
    private readonly IConfiguration _configuration;
    private readonly DaemonChannelService _daemonChannel;
    private readonly ILogger<CommandDispatchHostedService> _logger;
    private string? _lastConnectionWarningKey;
    private DateTime _lastConnectionWarningUtc = DateTime.MinValue;
    private static readonly TimeSpan WarningThrottle = TimeSpan.FromSeconds(30);

    public CommandDispatchHostedService(
        IConfiguration configuration,
        DaemonChannelService daemonChannel,
        ILogger<CommandDispatchHostedService> logger)
    {
        _configuration = configuration;
        _daemonChannel = daemonChannel;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var enabled = _configuration.GetValue("CrawlerApi:EnableCommandDispatch", true);
        if (!enabled)
        {
            _logger.LogInformation("Command dispatch hosted service is disabled.");
            return;
        }

        var pollIntervalMs = Math.Max(250, _configuration.GetValue("CrawlerApi:CommandDispatchPollMs", 1000));

        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await DispatchQueuedCommandsAsync(stoppingToken);
            }
            catch (Exception ex)
            {
                if (!TryLogConnectionWarning(ex))
                {
                    _logger.LogWarning(ex, "Failed to dispatch queued daemon commands.");
                }
            }

            await Task.Delay(pollIntervalMs, stoppingToken);
        }
    }

    private async Task DispatchQueuedCommandsAsync(CancellationToken cancellationToken)
    {
        var connectionString = _configuration.GetConnectionString("CrawldbConnection");
        if (string.IsNullOrWhiteSpace(connectionString))
        {
            return;
        }

        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(cancellationToken);

        var commands = await LoadQueuedCommandsAsync(connection, cancellationToken);
        if (commands.Count == 0)
        {
            return;
        }

        foreach (var command in commands)
        {
            var payload = BuildOutgoingPayload(command);
            var sent = await _daemonChannel.SendCommandAsync(command.DaemonIdentifier, command.Id, command.CommandType, payload);

            if (sent)
            {
                await UpdateCommandStatusAsync(
                    connection,
                    command.Id,
                    status: "dispatched",
                    errorMessage: null,
                    setDispatchedAt: true,
                    cancellationToken);
                continue;
            }

            await UpdateCommandStatusAsync(
                connection,
                command.Id,
                status: "queued",
                errorMessage: $"Daemon '{command.DaemonIdentifier}' is not connected.",
                setDispatchedAt: false,
                cancellationToken);
        }
    }

    private static async Task<List<QueuedCommand>> LoadQueuedCommandsAsync(
        NpgsqlConnection connection,
        CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT c.id,
                   c.command_type,
                   c.payload::text,
                   COALESCE(d.metadata->>'daemonId', 'local-default') AS daemon_identifier
            FROM manager.command c
            JOIN manager.daemon d ON d.id = c.daemon_id
            WHERE c.status = 'queued'
            ORDER BY c.created_at ASC
            LIMIT 100;
            """;

        var result = new List<QueuedCommand>();
        await using var cmd = new NpgsqlCommand(sql, connection);
        await using var reader = await cmd.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            result.Add(new QueuedCommand
            {
                Id = reader.GetInt64(0),
                CommandType = reader.GetString(1),
                PayloadJson = reader.IsDBNull(2) ? "{}" : reader.GetString(2),
                DaemonIdentifier = reader.GetString(3),
            });
        }

        return result;
    }

    private static object BuildOutgoingPayload(QueuedCommand command)
    {
        try
        {
            using var json = JsonDocument.Parse(command.PayloadJson);
            var root = json.RootElement;

            int? workerId = null;
            if (root.ValueKind == JsonValueKind.Object && root.TryGetProperty("workerId", out var workerIdNode))
            {
                if (workerIdNode.ValueKind == JsonValueKind.Number && workerIdNode.TryGetInt32(out var parsed))
                {
                    workerId = parsed;
                }
                else if (workerIdNode.ValueKind == JsonValueKind.String && int.TryParse(workerIdNode.GetString(), out var parsedString))
                {
                    workerId = parsedString;
                }
            }

            return new
            {
                command = command.CommandType,
                workerId,
                payload = JsonSerializer.Deserialize<object>(command.PayloadJson),
            };
        }
        catch
        {
            return new
            {
                command = command.CommandType,
                payload = new { }
            };
        }
    }

    private static async Task UpdateCommandStatusAsync(
        NpgsqlConnection connection,
        long commandId,
        string status,
        string? errorMessage,
        bool setDispatchedAt,
        CancellationToken cancellationToken)
    {
        const string sql = """
            UPDATE manager.command
            SET status = @status,
                error_message = @error_message,
                dispatched_at = CASE WHEN @set_dispatched_at THEN now() ELSE dispatched_at END
            WHERE id = @id;
            """;

        await using var cmd = new NpgsqlCommand(sql, connection);
        cmd.Parameters.AddWithValue("status", status);
        cmd.Parameters.AddWithValue("error_message", (object?)errorMessage ?? DBNull.Value);
        cmd.Parameters.AddWithValue("set_dispatched_at", setDispatchedAt);
        cmd.Parameters.AddWithValue("id", commandId);
        await cmd.ExecuteNonQueryAsync(cancellationToken);
    }

    private sealed class QueuedCommand
    {
        public long Id { get; set; }
        public required string CommandType { get; set; }
        public required string PayloadJson { get; set; }
        public required string DaemonIdentifier { get; set; }
    }

    private bool TryLogConnectionWarning(Exception ex)
    {
        var postgres = ex as PostgresException ?? ex.InnerException as PostgresException;
        if (postgres?.SqlState == PostgresErrorCodes.InvalidPassword)
        {
            return LogConnectionWarning(
                $"auth:{postgres.SqlState}:{postgres.MessageText}",
                $"Command dispatch DB auth failed for {DescribeConnectionTarget()}. Check ConnectionStrings:CrawldbConnection or DB_/PG_ environment variables.");
        }

        if (ex is NpgsqlException npgsql && ex.InnerException is TimeoutException)
        {
            return LogConnectionWarning(
                "timeout",
                $"Command dispatch could not reach the manager DB at {DescribeConnectionTarget()}. Check the DB host/port and whether PostgreSQL is reachable from this process.");
        }

        return false;
    }

    private bool LogConnectionWarning(string key, string message)
    {
        var now = DateTime.UtcNow;
        if (string.Equals(_lastConnectionWarningKey, key, StringComparison.Ordinal)
            && now - _lastConnectionWarningUtc < WarningThrottle)
        {
            return true;
        }

        _lastConnectionWarningKey = key;
        _lastConnectionWarningUtc = now;
        _logger.LogWarning("{Message}", message);
        return true;
    }

    private string DescribeConnectionTarget()
    {
        var connectionString = _configuration.GetConnectionString("CrawldbConnection");
        if (string.IsNullOrWhiteSpace(connectionString))
        {
            return "an unspecified PostgreSQL target";
        }

        try
        {
            var builder = new NpgsqlConnectionStringBuilder(connectionString);
            var host = string.IsNullOrWhiteSpace(builder.Host) ? "localhost" : builder.Host;
            var database = string.IsNullOrWhiteSpace(builder.Database) ? "crawldb" : builder.Database;
            var username = string.IsNullOrWhiteSpace(builder.Username) ? "postgres" : builder.Username;
            return $"{host}:{builder.Port}/{database} as {username}";
        }
        catch
        {
            return "the configured PostgreSQL target";
        }
    }
}
