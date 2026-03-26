using Microsoft.EntityFrameworkCore;
using ManagerApp.Components;
using ManagerApp.Data;
using ManagerApp.Services;
using ManagerApp.Hubs;
using Npgsql;

var builder = WebApplication.CreateBuilder(args);
ApplyDbEnvironmentOverrides(builder.Configuration);

// Add services to the container.
builder.Services.AddRazorComponents()
    .AddInteractiveServerComponents();

// Register DbContext with PostgreSQL
builder.Services.AddDbContextFactory<CrawldbContext>(options =>
    options.UseNpgsql(builder.Configuration.GetConnectionString("CrawldbConnection")));
// Bridge existing services that request CrawldbContext directly.
builder.Services.AddScoped(sp =>
    sp.GetRequiredService<IDbContextFactory<CrawldbContext>>().CreateDbContext());

// Register application services
builder.Services.AddScoped<IStatisticsService, StatisticsService>();
builder.Services.AddScoped<IGraphService, GraphService>();
builder.Services.AddScoped<IPageService, PageService>();
builder.Services.AddSingleton<DaemonChannelService>();
builder.Services.AddSingleton<CrawlerRelayService>();
builder.Services.AddHostedService<LocalDaemonHostedService>();
builder.Services.AddHostedService<CommandDispatchHostedService>();
builder.Services.AddScoped<IWorkerService, ReverseChannelWorkerService>();

// SignalR for real-time updates
builder.Services.AddSignalR();

var app = builder.Build();

// Configure the HTTP request pipeline.
if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Error", createScopeForErrors: true);
    // The default HSTS value is 30 days. You may want to change this for production scenarios, see https://aka.ms/aspnetcore-hsts.
    app.UseHsts();
}
app.UseStatusCodePagesWithReExecute("/not-found", createScopeForStatusCodePages: true);
if (!app.Environment.IsDevelopment())
{
    app.UseHttpsRedirection();
}
app.UseWebSockets();

app.UseAntiforgery();

app.MapStaticAssets();
app.MapRazorComponents<App>()
    .AddInteractiveServerRenderMode();

// Map SignalR hub for real-time updates
app.MapHub<CrawlerHub>("/crawlerhub");
app.Map("/api/daemon-channel", async (HttpContext context, DaemonChannelService daemonChannel) =>
{
    await daemonChannel.HandleSocketAsync(context);
});

app.MapPost("/api/crawler/ingest", async (
    CrawlerIngestRequest request,
    CrawlerRelayService relay,
    CancellationToken cancellationToken) =>
{
    var result = await relay.IngestAsync(request, cancellationToken);
    return Results.Ok(new
    {
        ok = true,
        data = new
        {
            pageId = result.PageId,
            status = result.Status,
            url = result.Url,
            duplicateOfPageId = result.DuplicateOfPageId,
            contentHash = result.ContentHash,
        }
    });
});

app.MapPost("/api/crawler/events", async (CrawlerEventMessage message, CrawlerRelayService relay) =>
{
    await relay.IngestEventAsync(message);
    return Results.Ok(new { ok = true });
});

app.MapGet("/api/crawler/events", (int? limit, CrawlerRelayService relay) =>
{
    var events = relay.GetRecentEvents(limit ?? 80);
    return Results.Ok(new
    {
        ok = true,
        data = events.Select(evt => new
        {
            timestampUtc = evt.TimestampUtc,
            type = evt.Type,
            daemonId = evt.DaemonId,
            workerId = evt.WorkerId,
            payloadJson = evt.PayloadJson,
        })
    });
});

app.Run();

static void ApplyDbEnvironmentOverrides(ConfigurationManager configuration)
{
    var hasDbOverride =
        !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("DB_HOST")) ||
        !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("DB_PORT")) ||
        !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("DB_USER")) ||
        !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("DB_PASSWORD")) ||
        !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("DB_NAME")) ||
        !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("PGHOST")) ||
        !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("PGPORT")) ||
        !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("PGUSER")) ||
        !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("PGPASSWORD")) ||
        !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("PGDATABASE"));

    if (!hasDbOverride)
    {
        return;
    }

    var existing = configuration.GetConnectionString("CrawldbConnection");
    var builder = new NpgsqlConnectionStringBuilder(existing ?? string.Empty)
    {
        Host = Environment.GetEnvironmentVariable("DB_HOST")
            ?? Environment.GetEnvironmentVariable("PGHOST")
            ?? "localhost",
        Port = ParsePort(
            Environment.GetEnvironmentVariable("DB_PORT")
            ?? Environment.GetEnvironmentVariable("PGPORT"),
            fallback: 5432),
        Username = Environment.GetEnvironmentVariable("DB_USER")
            ?? Environment.GetEnvironmentVariable("PGUSER")
            ?? "postgres",
        Password = Environment.GetEnvironmentVariable("DB_PASSWORD")
            ?? Environment.GetEnvironmentVariable("PGPASSWORD")
            ?? "postgres",
        Database = Environment.GetEnvironmentVariable("DB_NAME")
            ?? Environment.GetEnvironmentVariable("PGDATABASE")
            ?? "crawldb",
    };

    configuration["ConnectionStrings:CrawldbConnection"] = builder.ConnectionString;
}

static int ParsePort(string? rawPort, int fallback)
{
    return int.TryParse(rawPort, out var parsed) ? parsed : fallback;
}
