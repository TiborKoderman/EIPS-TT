using System.Collections.Concurrent;
using Npgsql;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;

namespace ManagerApp.Services;

public sealed class DaemonChannelService
{
    private readonly IConfiguration _configuration;
    private readonly ILogger<DaemonChannelService> _logger;

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

    public sealed class DaemonConnectionInfo
    {
        public required string DaemonId { get; init; }
        public DateTime ConnectedAtUtc { get; init; }
        public DateTime LastSeenUtc { get; init; }
        public bool IsOpen { get; init; }
    }

    private readonly ConcurrentDictionary<string, DaemonConnection> _connections = new();

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

    public async Task<bool> SendCommandAsync(string daemonId, long commandId, string command, object? payload = null)
    {
        if (!_connections.TryGetValue(daemonId, out var connection))
        {
            return false;
        }

        if (connection.Socket.State != WebSocketState.Open)
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
        await SendAsync(socket, new
        {
            type = "registered",
            daemonId,
            timestampUtc = DateTime.UtcNow,
        });

        var buffer = new byte[8192];
        try
        {
            while (socket.State == WebSocketState.Open)
            {
                var result = await socket.ReceiveAsync(buffer, CancellationToken.None);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    break;
                }

                if (result.MessageType != WebSocketMessageType.Text)
                {
                    continue;
                }

                var payload = Encoding.UTF8.GetString(buffer, 0, result.Count);
                connection.LastSeenUtc = DateTime.UtcNow;

                if (payload.Contains("\"type\":\"heartbeat\"", StringComparison.OrdinalIgnoreCase))
                {
                    await SendAsync(socket, new
                    {
                        type = "ack",
                        daemonId,
                        timestampUtc = DateTime.UtcNow,
                    });
                    continue;
                }

                await ProcessIncomingMessageAsync(payload);
            }
        }
        finally
        {
            _connections.TryRemove(daemonId, out _);
            connection.SendLock.Dispose();
            if (socket.State is WebSocketState.Open or WebSocketState.CloseReceived)
            {
                await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "closed", CancellationToken.None);
            }
        }
    }

    private async Task ProcessIncomingMessageAsync(string payload)
    {
        try
        {
            using var json = JsonDocument.Parse(payload);
            var root = json.RootElement;
            if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("type", out var typeNode))
            {
                return;
            }

            var messageType = typeNode.GetString();
            if (string.IsNullOrWhiteSpace(messageType))
            {
                return;
            }

            if (!root.TryGetProperty("commandId", out var commandIdNode) || !commandIdNode.TryGetInt64(out var commandId))
            {
                return;
            }

            if (string.Equals(messageType, "command-ack", StringComparison.OrdinalIgnoreCase))
            {
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
                await UpdateCommandStatusAsync(commandId, mappedStatus, error, setAcknowledgedAt: false, setCompletedAt: true);
            }
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Failed to process daemon channel message: {Payload}", payload);
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
    }

    private static async Task SendAsync(WebSocket socket, object payload)
    {
        var bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload));
        await socket.SendAsync(bytes, WebSocketMessageType.Text, true, CancellationToken.None);
    }
}
