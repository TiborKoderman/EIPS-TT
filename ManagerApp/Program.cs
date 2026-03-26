using Microsoft.EntityFrameworkCore;
using ManagerApp.Components;
using ManagerApp.Data;
using ManagerApp.Services;
using ManagerApp.Hubs;
using Npgsql;

var builder = WebApplication.CreateBuilder(args);
LoadProjectEnvFile();
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

app.MapGet("/api/pages/{pageId:int}/images/{imageId:int}", async (
    int pageId,
    int imageId,
    bool? download,
    IDbContextFactory<CrawldbContext> contextFactory) =>
{
    await using var db = await contextFactory.CreateDbContextAsync();
    var image = await db.Images
        .AsNoTracking()
        .FirstOrDefaultAsync(item => item.Id == imageId && item.PageId == pageId);

    if (image?.Data is null)
    {
        return Results.NotFound();
    }

    var contentType = string.IsNullOrWhiteSpace(image.ContentType)
        ? DetectContentType(image.Data, fallback: "application/octet-stream")
        : image.ContentType;
    var fileName = string.IsNullOrWhiteSpace(image.Filename)
        ? $"image-{image.Id}{ResolveFileExtension(contentType)}"
        : SanitizeFileName(image.Filename);

    if (download == true)
    {
        return Results.File(image.Data, contentType, fileName, enableRangeProcessing: true);
    }

    return Results.File(image.Data, contentType, enableRangeProcessing: true);
});

app.MapGet("/api/pages/{pageId:int}/page-data/{pageDataId:int}", async (
    int pageId,
    int pageDataId,
    bool? download,
    IDbContextFactory<CrawldbContext> contextFactory) =>
{
    await using var db = await contextFactory.CreateDbContextAsync();
    var pageData = await db.PageData
        .AsNoTracking()
        .FirstOrDefaultAsync(item => item.Id == pageDataId && item.PageId == pageId);

    if (pageData?.Data is null)
    {
        return Results.NotFound();
    }

    var contentType = ResolveDataContentType(pageData.DataTypeCode, pageData.Data);
    var fileName = $"page-{pageId}-blob-{pageData.Id}{ResolveFileExtension(contentType)}";

    if (download == true)
    {
        return Results.File(pageData.Data, contentType, fileName, enableRangeProcessing: true);
    }

    return Results.File(pageData.Data, contentType, enableRangeProcessing: true);
});

app.Run();

static string ResolveDataContentType(string? dataTypeCode, byte[] data)
{
    var normalized = dataTypeCode?.Trim().ToUpperInvariant();
    return normalized switch
    {
        "PDF" => "application/pdf",
        "DOC" => "application/msword",
        "DOCX" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "PPT" => "application/vnd.ms-powerpoint",
        "PPTX" => "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        _ => DetectContentType(data, fallback: "application/octet-stream"),
    };
}

static string DetectContentType(byte[] data, string fallback)
{
    if (data.Length >= 8
        && data[0] == 0x89
        && data[1] == 0x50
        && data[2] == 0x4E
        && data[3] == 0x47
        && data[4] == 0x0D
        && data[5] == 0x0A
        && data[6] == 0x1A
        && data[7] == 0x0A)
    {
        return "image/png";
    }

    if (data.Length >= 3 && data[0] == 0xFF && data[1] == 0xD8 && data[2] == 0xFF)
    {
        return "image/jpeg";
    }

    if (data.Length >= 6
        && data[0] == 0x47
        && data[1] == 0x49
        && data[2] == 0x46
        && data[3] == 0x38
        && (data[4] == 0x37 || data[4] == 0x39)
        && data[5] == 0x61)
    {
        return "image/gif";
    }

    if (data.Length >= 12
        && data[0] == 0x52
        && data[1] == 0x49
        && data[2] == 0x46
        && data[3] == 0x46
        && data[8] == 0x57
        && data[9] == 0x45
        && data[10] == 0x42
        && data[11] == 0x50)
    {
        return "image/webp";
    }

    if (data.Length >= 5
        && data[0] == 0x25
        && data[1] == 0x50
        && data[2] == 0x44
        && data[3] == 0x46
        && data[4] == 0x2D)
    {
        return "application/pdf";
    }

    return fallback;
}

static string ResolveFileExtension(string contentType)
{
    return contentType.ToLowerInvariant() switch
    {
        "image/png" => ".png",
        "image/jpeg" => ".jpg",
        "image/gif" => ".gif",
        "image/webp" => ".webp",
        "application/pdf" => ".pdf",
        "application/msword" => ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document" => ".docx",
        "application/vnd.ms-powerpoint" => ".ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation" => ".pptx",
        _ => ".bin",
    };
}

static string SanitizeFileName(string fileName)
{
    var invalidChars = Path.GetInvalidFileNameChars();
    var sanitized = new string(fileName.Select(ch => invalidChars.Contains(ch) ? '_' : ch).ToArray());
    return string.IsNullOrWhiteSpace(sanitized) ? "download.bin" : sanitized;
}

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

static void LoadProjectEnvFile()
{
    var current = new DirectoryInfo(Directory.GetCurrentDirectory());
    while (current is not null)
    {
        var envFile = Path.Combine(current.FullName, ".env.local");
        if (File.Exists(envFile))
        {
            ApplyEnvFile(envFile);
            return;
        }

        current = current.Parent;
    }
}

static void ApplyEnvFile(string filePath)
{
    foreach (var rawLine in File.ReadLines(filePath))
    {
        var line = rawLine.Trim();
        if (line.Length == 0 || line.StartsWith("#", StringComparison.Ordinal))
        {
            continue;
        }

        var separator = line.IndexOf('=');
        if (separator <= 0)
        {
            continue;
        }

        var key = line[..separator].Trim();
        if (key.Length == 0 || Environment.GetEnvironmentVariable(key) is not null)
        {
            continue;
        }

        var value = line[(separator + 1)..].Trim();
        Environment.SetEnvironmentVariable(key, value);
    }
}

static int ParsePort(string? rawPort, int fallback)
{
    return int.TryParse(rawPort, out var parsed) ? parsed : fallback;
}
