using Microsoft.EntityFrameworkCore;
using ManagerApp.Components;
using ManagerApp.Data;
using ManagerApp.Services;
using ManagerApp.Hubs;
using System.Net.Http.Headers;

var builder = WebApplication.CreateBuilder(args);

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
builder.Services.AddHostedService<LocalDaemonHostedService>();
builder.Services.AddHostedService<CommandDispatchHostedService>();
builder.Services.AddHttpClient<IWorkerService, WorkerService>((sp, client) =>
{
    var configuration = sp.GetRequiredService<IConfiguration>();
    var baseUrl = configuration["CrawlerApi:BaseUrl"] ?? "http://127.0.0.1:8090";
    client.BaseAddress = new Uri(baseUrl.TrimEnd('/') + "/");

    var token = configuration["CrawlerApi:Token"];
    if (!string.IsNullOrWhiteSpace(token))
    {
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
    }
});

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

app.Run();
