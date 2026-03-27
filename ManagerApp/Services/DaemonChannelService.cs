using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using ManagerApp.Models;
using Npgsql;

namespace ManagerApp.Services;

public sealed class DaemonChannelService
{
    private readonly IConfiguration _configuration;
    private readonly ILogger<DaemonChannelService> _logger;
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    public DaemonChannelService(IConfiguration configuration, ILogger<DaemonChannelService> logger)
    {
        _configuration = configuration;
        _logger = logger;
    }

    private sealed class DaemonConnection
    {
        public required string DaemonId { get; init; }
        public required WebSocket Socket { get; init; }
        public required SemaphoreSlim SendLock { get; init; }
        public DateTime ConnectedAtUtc { get; init; }
        public DateTime LastSeenUtc { get; set; }
    }

    private sealed class RpcResponseEnvelope
    {
        public bool Ok { get; set; }
        public JsonElement Data { get; set; }
        public string? Error { get; set; }
    }

    private sealed class DaemonSnapshotPayload
    {
        public DaemonStatusViewModel Daemon { get; set; } = new();
        public List<WorkerViewModel> Workers { get; set; } = new();
        public WorkerGlobalConfigViewModel GlobalConfig { get; set; } = new();
        public List<WorkerGroupSettingsViewModel> Groups { get; set; } = new();
        public FrontierStatusViewModel FrontierStatus { get; set; } = new();
    }

    public sealed class DaemonConnectionInfo
    {
        public required string DaemonId { get; init; }
        public DateTime ConnectedAtUtc { get; init; }
        public DateTime LastSeenUtc { get; init; }
        public bool IsOpen { get; init; }
    }

    public sealed class DaemonSnapshot
    {
        public required string DaemonId { get; init; }
        public DateTime ReceivedAtUtc { get; init; }
        public DaemonStatusViewModel Daemon { get; init; } = new();
        public List<WorkerViewModel> Workers { get; init; } = new();
        public WorkerGlobalConfigViewModel GlobalConfig { get; init; } = new();
        public List<WorkerGroupSettingsViewModel> Groups { get; init; } = new();
        public FrontierStatusViewModel FrontierStatus { get; init; } = new();
    }

    private readonly ConcurrentDictionary<string, DaemonConnection> _connections = new();
    private readonly ConcurrentDictionary<string, DaemonSnapshot> _snapshots = new();
    private readonly ConcurrentDictionary<string, TaskCompletionSource<RpcResponseEnvelope>> _pendingRequests = new();

    public IReadOnlyCollection<string> ConnectedDaemonIds => _connections.Keys.ToArray();

    public IReadOnlyList<DaemonConnectionInfo> GetConnectionInfos()
    {
        return _connections.Values
            .Select(connection => new DaemonConnectionInfo
            {
                DaemonId = connection.DaemonId,
                ConnectedAtUtc = connection.ConnectedAtUtc,
                LastSeenUtc = connection.LastSeenUtc,
                IsOpen = connection.Socket.State == WebSocketState.Open,
            })
            .OrderBy(info => info.DaemonId)
            .ToList();
    }

    public bool IsConnected(string daemonId)
    {
        return _connections.TryGetValue(daemonId, out var connection)
            && connection.Socket.State == WebSocketState.Open;
    }

    public DaemonSnapshot? GetSnapshot(string daemonId)
    {
        return _snapshots.TryGetValue(daemonId, out var snapshot) ? snapshot : null;
    }

    public DaemonSnapshot? GetLatestSnapshot()
    {
        return _snapshots.Values
            .OrderByDescending(snapshot => snapshot.ReceivedAtUtc)
            .FirstOrDefault();
    }

    public async Task<bool> WaitForConnectionAsync(string daemonId, TimeSpan timeout, CancellationToken cancellationToken)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline && !cancellationToken.IsCancellationRequested)
        {
            if (IsConnected(daemonId))
            {
                return true;
            }

            await Task.Delay(200, cancellationToken);
        }

        return IsConnected(daemonId);
    }

    public async Task<bool> SendCommandAsync(string daemonId, long commandId, string command, object? payload = null)
    {
        if (!_connections.TryGetValue(daemonId, out var connection) || connection.Socket.State != WebSocketState.Open)
        {
            return false;
        }

        await connection.SendLock.WaitAsync();
        try
        {
            if (connection.Socket.State != WebSocketState.Open)
            {
                return false;
            }

            await SendAsync(connection.Socket, new
            {
                type = "command",
                commandId,
                command,
                payload,
                timestampUtc = DateTime.UtcNow,
            });
            return true;
        }
        finally
        {
            connection.SendLock.Release();
        }
    }

    public async Task<(bool Ok, T? Data, string? Error)> SendRequestAsync<T>(
        string daemonId,
        string action,
        object? payload = null,
        TimeSpan? timeout = null)
    {
        if (!_connections.TryGetValue(daemonId, out var connection) || connection.Socket.State != WebSocketState.Open)
        {
            return (false, default, $"Daemon '{daemonId}' is not connected.");
        }

        var requestId = Guid.NewGuid().ToString("N");
        var completion = new TaskCompletionSource<RpcResponseEnvelope>(TaskCreationOptions.RunContinuationsAsynchronously);
        if (!_pendingRequests.TryAdd(requestId, completion))
        {
            return (false, default, "Failed to register daemon request.");
        }

        try
        {
            await connection.SendLock.WaitAsync();
            try
            {
                if (connection.Socket.State != WebSocketState.Open)
                {
                    return (false, default, $"Daemon '{daemonId}' disconnected before request could be sent.");
                }

                await SendAsync(connection.Socket, new
                {
                    type = "request",
                    requestId,
                    action,
                    payload,
                    timestampUtc = DateTime.UtcNow,
                });
            }
            finally
            {
                connection.SendLock.Release();
            }

            using var timeoutCts = new CancellationTokenSource(timeout ?? TimeSpan.FromSeconds(8));
            var response = await completion.Task.WaitAsync(timeoutCts.Token);
            if (!response.Ok)
            {
                return (false, default, response.Error ?? $"Daemon request '{action}' failed.");
            }

            if (response.Data.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null)
            {
                return (true, default, null);
            }

            var data = response.Data.Deserialize<T>(JsonOptions);
            return (true, data, null);
        }
        catch (OperationCanceledException)
        {
            return (false, default, $"Timed out waiting for daemon response to '{action}'.");
        }
        finally
        {
            _pendingRequests.TryRemove(requestId, out _);
        }
    }

    public async Task HandleSocketAsync(HttpContext context)
    {
        if (!context.WebSockets.IsWebSocketRequest)
        {
            context.Response.StatusCode = StatusCodes.Status400BadRequest;
            await context.Response.WriteAsync("Expected websocket request");
            return;
        }

        var daemonId = context.Request.Query["daemonId"].ToString();
        if (string.IsNullOrWhiteSpace(daemonId))
        {
            daemonId = $"daemon-{Guid.NewGuid():N}";
        }

        using var socket = await context.WebSockets.AcceptWebSocketAsync();
        var connection = new DaemonConnection
        {
            DaemonId = daemonId,
            Socket = socket,
            SendLock = new SemaphoreSlim(1, 1),
            ConnectedAtUtc = DateTime.UtcNow,
            LastSeenUtc = DateTime.UtcNow,
        };

        _connections[daemonId] = connection;
        _logger.LogInformation("Daemon websocket connected: {DaemonId}", daemonId);
        await SendAsync(socket, new
        {
            type = "registered",
            daemonId,
            timestampUtc = DateTime.UtcNow,
        });

        try
        {
            while (socket.State == WebSocketState.Open)
            {
                var payload = await ReceiveTextMessageAsync(socket, CancellationToken.None);
                if (payload is null)
                {
                    break;
                }

                connection.LastSeenUtc = DateTime.UtcNow;
                await ProcessIncomingMessageAsync(connection.DaemonId, socket, payload);
            }
        }
        catch (WebSocketException ex)
        {
            _logger.LogDebug(ex, "Daemon websocket closed unexpectedly for {DaemonId}", daemonId);
        }
        finally
        {
            if (_connections.TryGetValue(daemonId, out var current) && ReferenceEquals(current, connection))
            {
                _connections.TryRemove(daemonId, out _);
            }
            connection.SendLock.Dispose();
            _logger.LogInformation("Daemon websocket disconnected: {DaemonId}", daemonId);

            if (socket.State is WebSocketState.Open or WebSocketState.CloseReceived)
            {
                try
                {
                    await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "closed", CancellationToken.None);
                }
                catch (WebSocketException)
                {
                    // Remote peer already dropped the socket.
                }
            }
        }
    }

    private async Task ProcessIncomingMessageAsync(string fallbackDaemonId, WebSocket socket, string payload)
    {
        try
        {
            using var json = JsonDocument.Parse(payload);
            var root = json.RootElement;
            if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("type", out var typeNode))
            {
                return;
            }

            var daemonId = root.TryGetProperty("daemonId", out var daemonIdNode) && daemonIdNode.ValueKind == JsonValueKind.String
                ? daemonIdNode.GetString() ?? fallbackDaemonId
                : fallbackDaemonId;

            var messageType = typeNode.GetString();
            if (string.IsNullOrWhiteSpace(messageType))
            {
                return;
            }

            if (string.Equals(messageType, "register", StringComparison.OrdinalIgnoreCase)
                || string.Equals(messageType, "heartbeat", StringComparison.OrdinalIgnoreCase))
            {
                UpdateSnapshot(daemonId, root);
                await SendAsync(socket, new
                {
                    type = "ack",
                    daemonId,
                    timestampUtc = DateTime.UtcNow,
                });
                return;
            }

            if (string.Equals(messageType, "response", StringComparison.OrdinalIgnoreCase))
            {
                if (!root.TryGetProperty("requestId", out var requestIdNode) || requestIdNode.ValueKind != JsonValueKind.String)
                {
                    return;
                }

                var requestId = requestIdNode.GetString();
                if (string.IsNullOrWhiteSpace(requestId) || !_pendingRequests.TryRemove(requestId, out var completion))
                {
                    return;
                }

                var response = new RpcResponseEnvelope
                {
                    Ok = !root.TryGetProperty("ok", out var okNode) || okNode.ValueKind != JsonValueKind.False,
                    Error = root.TryGetProperty("error", out var errorNode) && errorNode.ValueKind == JsonValueKind.String
                        ? errorNode.GetString()
                        : null,
                    Data = root.TryGetProperty("data", out var dataNode) ? dataNode.Clone() : default,
                };
                completion.TrySetResult(response);
                return;
            }

            if (!root.TryGetProperty("commandId", out var commandIdNode) || !commandIdNode.TryGetInt64(out var commandId))
            {
                return;
            }

            if (string.Equals(messageType, "command-ack", StringComparison.OrdinalIgnoreCase))
            {
                _logger.LogInformation("Daemon command acknowledged: daemon={DaemonId} commandId={CommandId}", daemonId, commandId);
                await UpdateCommandStatusAsync(commandId, "acknowledged", null, setAcknowledgedAt: true, setCompletedAt: false);
                return;
            }

            if (string.Equals(messageType, "command-result", StringComparison.OrdinalIgnoreCase))
            {
                var status = root.TryGetProperty("status", out var statusNode)
                    ? (statusNode.GetString() ?? "completed")
                    : "completed";
                var error = root.TryGetProperty("error", out var errorNode)
                    ? errorNode.GetString()
                    : null;

                var mappedStatus = string.Equals(status, "failed", StringComparison.OrdinalIgnoreCase)
                    ? "failed"
                    : "completed";
                _logger.LogInformation(
                    "Daemon command result: daemon={DaemonId} commandId={CommandId} status={Status} error={Error}",
                    daemonId,
                    commandId,
                    mappedStatus,
                    error);
                await UpdateCommandStatusAsync(commandId, mappedStatus, error, setAcknowledgedAt: false, setCompletedAt: true);
            }
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Failed to process daemon channel message: {Payload}", payload);
        }
    }

    private void UpdateSnapshot(string daemonId, JsonElement root)
    {
        JsonElement snapshotNode;
        if (root.TryGetProperty("snapshot", out var explicitSnapshot))
        {
            snapshotNode = explicitSnapshot;
        }
        else if (root.TryGetProperty("status", out var legacyStatus))
        {
            snapshotNode = legacyStatus;
        }
        else
        {
            return;
        }

        try
        {
            var payload = snapshotNode.Deserialize<DaemonSnapshotPayload>(JsonOptions);
            if (payload is null)
            {
                return;
            }

            _snapshots[daemonId] = new DaemonSnapshot
            {
                DaemonId = daemonId,
                ReceivedAtUtc = DateTime.UtcNow,
                Daemon = payload.Daemon,
                Workers = payload.Workers,
                GlobalConfig = payload.GlobalConfig,
                Groups = payload.Groups,
                FrontierStatus = payload.FrontierStatus,
            };
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Failed to deserialize daemon snapshot for {DaemonId}", daemonId);
        }
    }

    private async Task UpdateCommandStatusAsync(
        long commandId,
        string status,
        string? error,
        bool setAcknowledgedAt,
        bool setCompletedAt)
    {
        var connectionString = _configuration.GetConnectionString("CrawldbConnection");
        if (string.IsNullOrWhiteSpace(connectionString))
        {
            return;
        }

        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync();

        const string sql = """
            UPDATE manager.command
            SET status = @status,
                error_message = @error_message,
                acknowledged_at = CASE WHEN @set_ack THEN now() ELSE acknowledged_at END,
                completed_at = CASE WHEN @set_done THEN now() ELSE completed_at END
            WHERE id = @id;
            """;

        await using var cmd = new NpgsqlCommand(sql, connection);
        cmd.Parameters.AddWithValue("status", status);
        cmd.Parameters.AddWithValue("error_message", (object?)error ?? DBNull.Value);
        cmd.Parameters.AddWithValue("set_ack", setAcknowledgedAt);
        cmd.Parameters.AddWithValue("set_done", setCompletedAt);
        cmd.Parameters.AddWithValue("id", commandId);
        await cmd.ExecuteNonQueryAsync();

        const string commandMetaSql = """
            SELECT c.command_type,
                   c.payload::text,
                   COALESCE(d.metadata->>'daemonId', 'local-default') AS daemon_identifier
            FROM manager.command c
            JOIN manager.daemon d ON d.id = c.daemon_id
            WHERE c.id = @id;
            """;

        string commandType = "unknown";
        string payloadJson = "{}";
        string daemonIdentifier = "local-default";

        await using (var metaCmd = new NpgsqlCommand(commandMetaSql, connection))
        {
            metaCmd.Parameters.AddWithValue("id", commandId);
            await using var reader = await metaCmd.ExecuteReaderAsync();
            if (await reader.ReadAsync())
            {
                commandType = reader.IsDBNull(0) ? "unknown" : reader.GetString(0);
                payloadJson = reader.IsDBNull(1) ? "{}" : reader.GetString(1);
                daemonIdentifier = reader.IsDBNull(2) ? "local-default" : reader.GetString(2);
            }
        }

        var level = string.Equals(status, "failed", StringComparison.OrdinalIgnoreCase) ? "Error" : "Info";
        var message = string.IsNullOrWhiteSpace(error)
            ? $"[command-result] commandId={commandId} type={commandType} status={status}"
            : $"[command-result] commandId={commandId} type={commandType} status={status} error={error}";

        const string logSql = """
            INSERT INTO manager.worker_log(daemon_identifier, external_worker_id, level, message, payload)
            VALUES (@daemon_identifier, NULL, @level, @message, @payload::jsonb);
            """;

        await using var logCmd = new NpgsqlCommand(logSql, connection);
        logCmd.Parameters.AddWithValue("daemon_identifier", daemonIdentifier);
        logCmd.Parameters.AddWithValue("level", level);
        logCmd.Parameters.AddWithValue("message", message);
        logCmd.Parameters.AddWithValue("payload", string.IsNullOrWhiteSpace(payloadJson) ? "{}" : payloadJson);
        await logCmd.ExecuteNonQueryAsync();
    }

    private static async Task<string?> ReceiveTextMessageAsync(WebSocket socket, CancellationToken cancellationToken)
    {
        var buffer = new byte[8192];
        using var ms = new MemoryStream();

        while (true)
        {
            var result = await socket.ReceiveAsync(buffer.AsMemory(), cancellationToken);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                return null;
            }

            if (result.MessageType != WebSocketMessageType.Text)
            {
                if (result.EndOfMessage)
                {
                    return null;
                }

                continue;
            }

            ms.Write(buffer, 0, result.Count);
            if (result.EndOfMessage)
            {
                return Encoding.UTF8.GetString(ms.ToArray());
            }
        }
    }

    private static async Task SendAsync(WebSocket socket, object payload)
    {
        var bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload));
        await socket.SendAsync(bytes, WebSocketMessageType.Text, true, CancellationToken.None);
    }
}
