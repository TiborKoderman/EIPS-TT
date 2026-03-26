using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;

namespace ManagerApp.Services;

public sealed class DaemonChannelService
{
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

    public async Task<bool> SendCommandAsync(string daemonId, string command, object? payload = null)
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

                // Minimal command/heartbeat protocol hook; extend with durable command queue later.
                if (payload.Contains("\"type\":\"heartbeat\"", StringComparison.OrdinalIgnoreCase))
                {
                    await SendAsync(socket, new
                    {
                        type = "ack",
                        daemonId,
                        timestampUtc = DateTime.UtcNow,
                    });
                }
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

    private static async Task SendAsync(WebSocket socket, object payload)
    {
        var bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload));
        await socket.SendAsync(bytes, WebSocketMessageType.Text, true, CancellationToken.None);
    }
}
