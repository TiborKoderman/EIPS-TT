using Microsoft.EntityFrameworkCore;
using ManagerApp.Components;
using ManagerApp.Data;
using ManagerApp.Services;
using ManagerApp.Hubs;

var builder = WebApplication.CreateBuilder(args);

// Add services to the container.
builder.Services.AddRazorComponents()
    .AddInteractiveServerComponents();

// Register DbContext with PostgreSQL
builder.Services.AddDbContext<CrawldbContext>(options =>
    options.UseNpgsql(builder.Configuration.GetConnectionString("CrawldbConnection")));

// Register application services
builder.Services.AddScoped<IStatisticsService, StatisticsService>();
builder.Services.AddScoped<IGraphService, GraphService>();
builder.Services.AddScoped<IPageService, PageService>();
builder.Services.AddScoped<IWorkerService, WorkerService>(); // Mock for now

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
app.UseHttpsRedirection();

app.UseAntiforgery();

app.MapStaticAssets();
app.MapRazorComponents<App>()
    .AddInteractiveServerRenderMode();

// Map SignalR hub for real-time updates
app.MapHub<CrawlerHub>("/crawlerhub");

app.Run();
