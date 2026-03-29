using Microsoft.AspNetCore.SignalR;
using ManagerApp.Models;

namespace ManagerApp.Hubs;

/// <summary>
/// SignalR hub for real-time crawler updates and statistics
/// This hub will be used to push updates from the Python crawler API to all connected clients
/// </summary>
public class CrawlerHub : Hub
{
    /// <summary>
    /// Broadcast worker status update to all connected clients
    /// Called by Python API when a worker's status changes
    /// </summary>
    /// <param name="worker">Updated worker data</param>
    public async Task BroadcastWorkerStatus(WorkerViewModel worker)
    {
        await Clients.All.SendAsync("WorkerStatusUpdated", worker);
    }

    /// <summary>
    /// Broadcast new page crawled event to all connected clients
    /// </summary>
    /// <param name="page">Newly crawled page data</param>
    public async Task BroadcastPageCrawled(PageSearchDto page)
    {
        await Clients.All.SendAsync("PageCrawled", page);
    }

    /// <summary>
    /// Broadcast statistics update to all connected clients
    /// Can be called periodically to update dashboard in real-time
    /// </summary>
    /// <param name="stats">Updated statistics</param>
    public async Task BroadcastStatisticsUpdate(StatisticsViewModel stats)
    {
        await Clients.All.SendAsync("StatisticsUpdated", stats);
    }

    /// <summary>
    /// Called when a client connects to the hub
    /// </summary>
    public override async Task OnConnectedAsync()
    {
        await base.OnConnectedAsync();
        // Log connection for monitoring (in production, use proper logging)
        Console.WriteLine($"[CrawlerHub] Client connected: {Context.ConnectionId}");
    }

    /// <summary>
    /// Called when a client disconnects from the hub
    /// </summary>
    public override async Task OnDisconnectedAsync(Exception? exception)
    {
        await base.OnDisconnectedAsync(exception);
        Console.WriteLine($"[CrawlerHub] Client disconnected: {Context.ConnectionId}");

        if (exception != null)
        {
            Console.WriteLine($"[CrawlerHub] Disconnect reason: {exception.Message}");
        }
    }
}
