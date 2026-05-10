using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using ManagerApp.Data;
using ManagerApp.Models;
using Microsoft.EntityFrameworkCore;
using Npgsql;
using NpgsqlTypes;

namespace ManagerApp.Services;

public sealed class Assignment2Service : IAssignment2Service
{
    private sealed record DemoRunCacheEntry(string CorpusFingerprint, Assignment2DemoRunResultDto Result);

    private const string ContentTypeCaseSql = """
        CASE
            WHEN COALESCE(stats.has_forum, 0) = 1 THEN 'forum'
            WHEN COALESCE(stats.has_article, 0) = 1 THEN 'article'
            WHEN lower(COALESCE(p.url, '')) LIKE '%/forum/%'
              OR lower(COALESCE(p.url, '')) LIKE '%/kategorija/%'
              OR lower(COALESCE(p.url, '')) LIKE '%/tag/%'
              OR lower(COALESCE(p.url, '')) LIKE '%/search%'
              OR lower(COALESCE(p.url, '')) LIKE '%/feed%'
              OR lower(COALESCE(p.url, '')) LIKE '%/prijava%'
              OR lower(COALESCE(p.url, '')) LIKE '%/registracija%'
              THEN 'listing'
            WHEN COALESCE(length(nullif(p.cleaned_content, '')), 0) > 0 THEN 'article'
            ELSE 'unknown'
        END
        """;

    private const string SegmentStatsCteSql = """
        WITH short_stats AS (
            SELECT
                page_id,
                COUNT(*)::int AS short_count,
                MAX(CASE WHEN page_type = 'article' THEN 1 ELSE 0 END)::int AS has_article,
                MAX(CASE WHEN page_type = 'forum' THEN 1 ELSE 0 END)::int AS has_forum
            FROM crawldb.page_segment_short
            GROUP BY page_id
        ),
        long_stats AS (
            SELECT
                page_id,
                COUNT(*)::int AS long_count,
                MAX(CASE WHEN page_type = 'article' THEN 1 ELSE 0 END)::int AS has_article,
                MAX(CASE WHEN page_type = 'forum' THEN 1 ELSE 0 END)::int AS has_forum
            FROM crawldb.page_segment_long
            GROUP BY page_id
        ),
        stats AS (
            SELECT
                COALESCE(short_stats.page_id, long_stats.page_id) AS page_id,
                COALESCE(short_stats.short_count, 0) AS short_count,
                COALESCE(long_stats.long_count, 0) AS long_count,
                GREATEST(COALESCE(short_stats.has_article, 0), COALESCE(long_stats.has_article, 0)) AS has_article,
                GREATEST(COALESCE(short_stats.has_forum, 0), COALESCE(long_stats.has_forum, 0)) AS has_forum
            FROM short_stats
            FULL OUTER JOIN long_stats
                ON long_stats.page_id = short_stats.page_id
        )
        """;

    private static readonly Regex SavedRunRegex = new(@"\[demo\] saved run:\s*(?<path>.+)", RegexOptions.Compiled);
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = true,
    };
    private static readonly ConcurrentDictionary<string, DemoRunCacheEntry> DemoRunCache = new(StringComparer.Ordinal);

    private readonly CrawldbContext _context;
    private readonly IConfiguration _configuration;
    private readonly IHostEnvironment _environment;
    private readonly ILogger<Assignment2Service> _logger;

    public Assignment2Service(
        CrawldbContext context,
        IConfiguration configuration,
        IHostEnvironment environment,
        ILogger<Assignment2Service> logger)
    {
        _context = context;
        _configuration = configuration;
        _environment = environment;
        _logger = logger;
    }

    public async Task<Assignment2OverviewDto> GetAssignment2OverviewAsync()
    {
        await using var connection = await OpenConnectionAsync();
        if (connection is null)
        {
            return new Assignment2OverviewDto();
        }

        var overview = new Assignment2OverviewDto
        {
            ExtractedArticlePages = await ExecuteScalarIntAsync(connection, """
                SELECT COUNT(DISTINCT page_id)::int
                FROM crawldb.page_segment_long
                WHERE page_type = 'article';
                """),
            ExtractedForumPages = await ExecuteScalarIntAsync(connection, """
                SELECT COUNT(DISTINCT page_id)::int
                FROM crawldb.page_segment_long
                WHERE page_type = 'forum';
                """),
            CleanedPagesTotal = await ExecuteScalarIntAsync(connection, """
                SELECT COUNT(*)::int
                FROM crawldb.page
                WHERE page_type_code = 'HTML'
                  AND cleaned_content IS NOT NULL
                  AND length(cleaned_content) > 0;
                """),
            ShortSegmentTotal = await ExecuteScalarIntAsync(connection, "SELECT COUNT(*)::int FROM crawldb.page_segment_short;"),
            LongSegmentTotal = await ExecuteScalarIntAsync(connection, "SELECT COUNT(*)::int FROM crawldb.page_segment_long;"),
            EmbeddedShortSegments = await ExecuteScalarIntAsync(connection, """
                SELECT COUNT(*)::int
                FROM crawldb.page_segment_short
                WHERE embedding IS NOT NULL;
                """),
            EmbeddedLongSegments = await ExecuteScalarIntAsync(connection, """
                SELECT COUNT(*)::int
                FROM crawldb.page_segment_long
                WHERE embedding IS NOT NULL;
                """),
            ActiveEmbeddingModel = await ExecuteScalarStringAsync(connection, """
                SELECT embedding_model
                FROM (
                    SELECT embedding_model, COUNT(*) AS c
                    FROM crawldb.page_segment_long
                    GROUP BY embedding_model
                    ORDER BY c DESC, embedding_model ASC
                    LIMIT 1
                ) AS ranked;
                """) ?? "unknown",
            RerankerModel = "BAAI/bge-reranker-v2-m3",
        };

        var indexDefinition = await ExecuteScalarStringAsync(connection, """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = 'crawldb'
              AND tablename = 'page_segment_long'
              AND indexname LIKE '%embedding%'
            ORDER BY CASE
                WHEN indexname LIKE '%ivfflat%' THEN 0
                WHEN indexname LIKE '%hnsw%' THEN 1
                ELSE 2
            END
            LIMIT 1;
            """);

        overview.AnnIndex = ParseAnnIndex(indexDefinition);
        overview.SimilarityMetric = ParseSimilarityMetric(indexDefinition);
        return overview;
    }

    public async Task<List<Assignment2DocumentSummaryDto>> SearchAssignment2DocumentsAsync(
        string? searchTerm,
        string? contentType,
        bool? hasCleanedText,
        int skip = 0,
        int take = 100)
    {
        await using var connection = await OpenConnectionAsync();
        if (connection is null)
        {
            return new List<Assignment2DocumentSummaryDto>();
        }

        var normalizedSearch = string.IsNullOrWhiteSpace(searchTerm) ? null : searchTerm.Trim();
        var normalizedType = NormalizeContentType(contentType);
        var results = new List<Assignment2DocumentSummaryDto>();

        var sql = $"""
            {SegmentStatsCteSql}
            SELECT
                p.id,
                p.url,
                p.accessed_time,
                COALESCE(length(nullif(p.cleaned_content, '')), 0)::int AS cleaned_length,
                COALESCE(stats.short_count, 0)::int AS short_count,
                COALESCE(stats.long_count, 0)::int AS long_count,
                {ContentTypeCaseSql} AS content_type
            FROM crawldb.page p
            LEFT JOIN stats ON stats.page_id = p.id
            WHERE p.page_type_code = 'HTML'
              AND (@search IS NULL OR p.url ILIKE ('%' || @search || '%') OR COALESCE(p.cleaned_content, '') ILIKE ('%' || @search || '%'))
              AND (@has_cleaned IS NULL OR (COALESCE(length(nullif(p.cleaned_content, '')), 0) > 0) = @has_cleaned)
              AND (@content_type IS NULL OR {ContentTypeCaseSql} = @content_type)
            ORDER BY
                CASE {ContentTypeCaseSql}
                    WHEN 'article' THEN 0
                    WHEN 'forum' THEN 1
                    WHEN 'listing' THEN 2
                    ELSE 3
                END,
                COALESCE(length(nullif(p.cleaned_content, '')), 0) DESC,
                p.id ASC
            OFFSET @skip
            LIMIT @take;
            """;

        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.Add("search", NpgsqlDbType.Text).Value = (object?)normalizedSearch ?? DBNull.Value;
        command.Parameters.Add("has_cleaned", NpgsqlDbType.Boolean).Value = (object?)hasCleanedText ?? DBNull.Value;
        command.Parameters.Add("content_type", NpgsqlDbType.Text).Value = string.IsNullOrWhiteSpace(normalizedType)
            ? DBNull.Value
            : normalizedType;
        command.Parameters.AddWithValue("skip", Math.Max(0, skip));
        command.Parameters.AddWithValue("take", Math.Clamp(take, 1, 500));

        await using var reader = await command.ExecuteReaderAsync();
        while (await reader.ReadAsync())
        {
            results.Add(new Assignment2DocumentSummaryDto
            {
                PageId = reader.GetInt32(0),
                Url = reader.IsDBNull(1) ? string.Empty : reader.GetString(1),
                AccessedTime = reader.IsDBNull(2) ? null : reader.GetDateTime(2),
                HasCleanedContent = !reader.IsDBNull(3) && reader.GetInt32(3) > 0,
                CleanedContentLength = reader.IsDBNull(3) ? 0 : reader.GetInt32(3),
                ShortSegmentCount = reader.IsDBNull(4) ? 0 : reader.GetInt32(4),
                LongSegmentCount = reader.IsDBNull(5) ? 0 : reader.GetInt32(5),
                ContentType = reader.IsDBNull(6) ? "unknown" : reader.GetString(6),
            });
        }

        return results;
    }

    public async Task<Assignment2DocumentDetailDto?> GetAssignment2DocumentAsync(int pageId)
    {
        await using var connection = await OpenConnectionAsync();
        if (connection is null)
        {
            return null;
        }

        var sql = $"""
            {SegmentStatsCteSql}
            SELECT
                p.id,
                p.url,
                p.cleaned_content,
                COALESCE(length(nullif(p.cleaned_content, '')), 0)::int AS cleaned_length,
                COALESCE(stats.short_count, 0)::int AS short_count,
                COALESCE(stats.long_count, 0)::int AS long_count,
                s.domain,
                p.accessed_time,
                {ContentTypeCaseSql} AS content_type
            FROM crawldb.page p
            LEFT JOIN stats ON stats.page_id = p.id
            LEFT JOIN crawldb.site s ON s.id = p.site_id
            WHERE p.id = @page_id
            LIMIT 1;
            """;

        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("page_id", pageId);
        await using var reader = await command.ExecuteReaderAsync();
        if (!await reader.ReadAsync())
        {
            return null;
        }

        return new Assignment2DocumentDetailDto
        {
            PageId = reader.GetInt32(0),
            Url = reader.IsDBNull(1) ? string.Empty : reader.GetString(1),
            CleanedContent = reader.IsDBNull(2) ? null : reader.GetString(2),
            CleanedContentLength = reader.IsDBNull(3) ? 0 : reader.GetInt32(3),
            ShortSegmentCount = reader.IsDBNull(4) ? 0 : reader.GetInt32(4),
            LongSegmentCount = reader.IsDBNull(5) ? 0 : reader.GetInt32(5),
            SiteDomain = reader.IsDBNull(6) ? null : reader.GetString(6),
            AccessedTime = reader.IsDBNull(7) ? null : reader.GetDateTime(7),
            ContentType = reader.IsDBNull(8) ? "unknown" : reader.GetString(8),
        };
    }

    public async Task<Assignment2DemoRunResultDto> RunAssignment2DemoAsync(string? query = null, bool rerank = false, bool useOfficialQueries = true)
    {
        var repositoryRoot = GetRepositoryRoot();
        var pythonExecutable = ResolvePythonExecutable(repositoryRoot);
        var demoScriptPath = Path.Combine(repositoryRoot, "pa2", "implementation-extraction", "demo.py");
        var queriesFilePath = Path.Combine(repositoryRoot, "pa2", "implementation-extraction", "eval", "queries.json");
        var runsDirectory = Path.Combine(repositoryRoot, "pa2", "implementation-extraction", "eval", "runs");
        var normalizedQuery = string.IsNullOrWhiteSpace(query) ? null : query.Trim();
        var corpusFingerprint = await ComputeCorpusFingerprintAsync();
        var cacheKey = BuildDemoCacheKey(rerank, useOfficialQueries, normalizedQuery);

        if (DemoRunCache.TryGetValue(cacheKey, out var cached)
            && string.Equals(cached.CorpusFingerprint, corpusFingerprint, StringComparison.Ordinal))
        {
            return cached.Result;
        }

        var latestSaved = await TryLoadLatestMatchingRunAsync(runsDirectory, rerank, useOfficialQueries, normalizedQuery, corpusFingerprint);
        if (latestSaved is not null)
        {
            DemoRunCache[cacheKey] = new DemoRunCacheEntry(corpusFingerprint, latestSaved);
            return latestSaved;
        }

        var processInfo = new ProcessStartInfo
        {
            FileName = pythonExecutable,
            WorkingDirectory = repositoryRoot,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
        };

        processInfo.ArgumentList.Add(demoScriptPath);
        if (useOfficialQueries || string.IsNullOrWhiteSpace(normalizedQuery))
        {
            processInfo.ArgumentList.Add("--queries-file");
            processInfo.ArgumentList.Add(queriesFilePath);
            if (rerank)
            {
                processInfo.ArgumentList.Add("--intent-filter");
                processInfo.ArgumentList.Add("bad");
            }
        }
        else
        {
            processInfo.ArgumentList.Add("--query");
            processInfo.ArgumentList.Add(normalizedQuery);
        }

        processInfo.ArgumentList.Add("--top-k");
        processInfo.ArgumentList.Add("5");
        processInfo.ArgumentList.Add("--device");
        processInfo.ArgumentList.Add("cpu");
        processInfo.ArgumentList.Add("--corpus-fingerprint");
        processInfo.ArgumentList.Add(corpusFingerprint);
        if (rerank)
        {
            processInfo.ArgumentList.Add("--rerank");
            processInfo.ArgumentList.Add("--rerank-candidates");
            processInfo.ArgumentList.Add("10");
        }

        ApplyDatabaseEnvironment(processInfo);

        var startedAtUtc = DateTime.UtcNow;
        using var process = Process.Start(processInfo)
            ?? throw new InvalidOperationException($"Failed to start demo process '{pythonExecutable}'.");
        var stdoutTask = process.StandardOutput.ReadToEndAsync();
        var stderrTask = process.StandardError.ReadToEndAsync();

        await process.WaitForExitAsync();
        var stdout = await stdoutTask;
        var stderr = await stderrTask;

        if (process.ExitCode != 0)
        {
            throw new InvalidOperationException($"Assignment 2 demo failed with exit code {process.ExitCode}: {stderr}");
        }

        var runPath = ResolveRunPath(stderr, runsDirectory, rerank, startedAtUtc);
        await AnnotateRunPayloadAsync(runPath, rerank, useOfficialQueries, normalizedQuery, corpusFingerprint);
        var result = await LoadRunPayloadAsync(runPath);
        result.Stdout = stdout;
        result.Stderr = stderr;
        result.RunPath = runPath;
        DemoRunCache[cacheKey] = new DemoRunCacheEntry(corpusFingerprint, result);
        return result;
    }

    public async Task<Assignment2DemoRunResultDto?> GetLatestAssignment2DemoRunAsync(bool rerank)
    {
        var repositoryRoot = GetRepositoryRoot();
        var runsDirectory = Path.Combine(repositoryRoot, "pa2", "implementation-extraction", "eval", "runs");
        var corpusFingerprint = await ComputeCorpusFingerprintAsync();
        return await TryLoadLatestMatchingRunAsync(
            runsDirectory,
            rerank,
            useOfficialQueries: true,
            normalizedQuery: null,
            corpusFingerprint: corpusFingerprint);
    }

    public async Task<List<Assignment2QueryDefinitionDto>> GetAssignment2QueriesAsync()
    {
        var filePath = Path.Combine(GetRepositoryRoot(), "pa2", "implementation-extraction", "eval", "queries.json");
        if (!File.Exists(filePath))
        {
            return new List<Assignment2QueryDefinitionDto>();
        }

        using var stream = File.OpenRead(filePath);
        using var document = await JsonDocument.ParseAsync(stream);
        if (!document.RootElement.TryGetProperty("queries", out var queriesNode)
            || queriesNode.ValueKind != JsonValueKind.Array)
        {
            return new List<Assignment2QueryDefinitionDto>();
        }

        var queries = new List<Assignment2QueryDefinitionDto>();
        foreach (var item in queriesNode.EnumerateArray())
        {
            queries.Add(new Assignment2QueryDefinitionDto
            {
                Label = GetString(item, "label") ?? string.Empty,
                Query = GetString(item, "query") ?? string.Empty,
                Intent = GetString(item, "intent") ?? string.Empty,
                Expected = GetString(item, "expected") ?? string.Empty,
            });
        }

        return queries;
    }

    public async Task<Assignment2SiteMetricsDto> GetAssignment2SiteMetricsAsync(int siteId)
    {
        await using var connection = await OpenConnectionAsync();
        if (connection is null)
        {
            return new Assignment2SiteMetricsDto { SiteId = siteId };
        }

        var sql = $"""
            {SegmentStatsCteSql}
            SELECT
                COUNT(*) FILTER (
                    WHERE p.page_type_code = 'HTML'
                      AND p.cleaned_content IS NOT NULL
                      AND length(p.cleaned_content) > 0
                )::int AS cleaned_pages,
                COUNT(*) FILTER (
                    WHERE {ContentTypeCaseSql} = 'article'
                      AND COALESCE(stats.long_count, 0) > 0
                )::int AS article_pages,
                COUNT(*) FILTER (
                    WHERE {ContentTypeCaseSql} = 'forum'
                      AND COALESCE(stats.long_count, 0) > 0
                )::int AS forum_pages,
                COALESCE(SUM(COALESCE(stats.short_count, 0)), 0)::int AS short_segments,
                COALESCE(SUM(COALESCE(stats.long_count, 0)), 0)::int AS long_segments
            FROM crawldb.page p
            LEFT JOIN stats ON stats.page_id = p.id
            WHERE p.site_id = @site_id;
            """;

        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("site_id", siteId);
        await using var reader = await command.ExecuteReaderAsync();
        if (!await reader.ReadAsync())
        {
            return new Assignment2SiteMetricsDto { SiteId = siteId };
        }

        return new Assignment2SiteMetricsDto
        {
            SiteId = siteId,
            CleanedPages = reader.IsDBNull(0) ? 0 : reader.GetInt32(0),
            ArticlePages = reader.IsDBNull(1) ? 0 : reader.GetInt32(1),
            ForumPages = reader.IsDBNull(2) ? 0 : reader.GetInt32(2),
            ShortSegments = reader.IsDBNull(3) ? 0 : reader.GetInt32(3),
            LongSegments = reader.IsDBNull(4) ? 0 : reader.GetInt32(4),
        };
    }

    private async Task<NpgsqlConnection?> OpenConnectionAsync()
    {
        var connectionString = _context.Database.GetConnectionString();
        if (string.IsNullOrWhiteSpace(connectionString))
        {
            return null;
        }

        var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync();
        return connection;
    }

    private static async Task<int> ExecuteScalarIntAsync(NpgsqlConnection connection, string sql)
    {
        await using var command = new NpgsqlCommand(sql, connection);
        var scalar = await command.ExecuteScalarAsync();
        return scalar is null || scalar == DBNull.Value ? 0 : Convert.ToInt32(scalar, CultureInfo.InvariantCulture);
    }

    private static async Task<string?> ExecuteScalarStringAsync(NpgsqlConnection connection, string sql)
    {
        await using var command = new NpgsqlCommand(sql, connection);
        return await command.ExecuteScalarAsync() as string;
    }

    private static string NormalizeContentType(string? contentType)
    {
        var normalized = (contentType ?? string.Empty).Trim().ToLowerInvariant();
        return normalized switch
        {
            "article" => "article",
            "forum" => "forum",
            "listing" => "listing",
            _ => string.Empty,
        };
    }

    private string GetRepositoryRoot()
    {
        return Path.GetFullPath(Path.Combine(_environment.ContentRootPath, ".."));
    }

    private async Task<string> ComputeCorpusFingerprintAsync()
    {
        await using var connection = await OpenConnectionAsync();
        if (connection is null)
        {
            return "no-db";
        }

        const string sql = """
            SELECT
                (SELECT COUNT(*)::bigint FROM crawldb.page WHERE page_type_code = 'HTML' AND cleaned_content IS NOT NULL AND length(cleaned_content) > 0) AS cleaned_pages,
                (SELECT COUNT(*)::bigint FROM crawldb.page_segment_long) AS long_segments,
                (SELECT COUNT(*)::bigint FROM crawldb.page_segment_short) AS short_segments,
                (SELECT COUNT(DISTINCT page_id)::bigint FROM crawldb.page_segment_long WHERE page_type = 'article') AS article_pages,
                (SELECT COUNT(DISTINCT page_id)::bigint FROM crawldb.page_segment_long WHERE page_type = 'forum') AS forum_pages,
                (SELECT COALESCE(MAX(id), 0)::bigint FROM crawldb.page) AS max_page_id,
                (SELECT COALESCE(MAX(id), 0)::bigint FROM crawldb.page_segment_long) AS max_long_segment_id,
                (SELECT COALESCE(MAX(id), 0)::bigint FROM crawldb.page_segment_short) AS max_short_segment_id;
            """;

        await using var command = new NpgsqlCommand(sql, connection);
        await using var reader = await command.ExecuteReaderAsync();
        if (!await reader.ReadAsync())
        {
            return "unknown";
        }

        var raw = string.Join("|", Enumerable.Range(0, reader.FieldCount).Select(index => reader.GetInt64(index).ToString(CultureInfo.InvariantCulture)));
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(raw));
        return Convert.ToHexString(hash);
    }

    private string ResolvePythonExecutable(string repositoryRoot)
    {
        var configured = (_configuration["CrawlerApi:PythonExecutable"] ?? string.Empty).Trim();
        if (!string.IsNullOrWhiteSpace(configured))
        {
            if (Path.IsPathRooted(configured) && File.Exists(configured))
            {
                return configured;
            }

            var relativeCandidate = Path.GetFullPath(Path.Combine(repositoryRoot, configured));
            if (File.Exists(relativeCandidate))
            {
                return relativeCandidate;
            }

            if (!configured.Contains(Path.DirectorySeparatorChar) && !configured.Contains(Path.AltDirectorySeparatorChar))
            {
                return configured;
            }
        }

        return "python3";
    }

    private void ApplyDatabaseEnvironment(ProcessStartInfo processInfo)
    {
        var builder = new NpgsqlConnectionStringBuilder(_context.Database.GetConnectionString());
        processInfo.Environment["PGHOST"] = builder.Host;
        processInfo.Environment["PGPORT"] = builder.Port.ToString(CultureInfo.InvariantCulture);
        processInfo.Environment["PGDATABASE"] = builder.Database;
        processInfo.Environment["PGUSER"] = builder.Username;
        processInfo.Environment["PGPASSWORD"] = builder.Password;
    }

    private static string ResolveRunPath(string stderr, string runsDirectory, bool rerank, DateTime startedAtUtc)
    {
        var match = SavedRunRegex.Match(stderr ?? string.Empty);
        if (match.Success)
        {
            return match.Groups["path"].Value.Trim();
        }

        var suffix = rerank ? "_rerank.json" : "_baseline.json";
        var candidate = new DirectoryInfo(runsDirectory)
            .EnumerateFiles($"*{suffix}", SearchOption.TopDirectoryOnly)
            .Where(file => file.LastWriteTimeUtc >= startedAtUtc.AddSeconds(-5))
            .OrderByDescending(file => file.LastWriteTimeUtc)
            .FirstOrDefault();

        if (candidate is null)
        {
            throw new FileNotFoundException("Demo completed but the saved run JSON could not be located.", runsDirectory);
        }

        return candidate.FullName;
    }

    private static string BuildDemoCacheKey(bool rerank, bool useOfficialQueries, string? normalizedQuery)
    {
        return string.Join(
            "|",
            rerank ? "rerank" : "baseline",
            useOfficialQueries ? "official" : "adhoc",
            normalizedQuery ?? string.Empty);
    }

    private async Task<Assignment2DemoRunResultDto?> TryLoadLatestMatchingRunAsync(
        string runsDirectory,
        bool rerank,
        bool useOfficialQueries,
        string? normalizedQuery,
        string corpusFingerprint)
    {
        if (!Directory.Exists(runsDirectory))
        {
            return null;
        }

        var suffix = rerank ? "_rerank.json" : "_baseline.json";
        var files = new DirectoryInfo(runsDirectory)
            .EnumerateFiles($"*{suffix}", SearchOption.TopDirectoryOnly)
            .OrderByDescending(file => file.LastWriteTimeUtc)
            .Take(20);

        foreach (var file in files)
        {
            if (!await RunPayloadMatchesAsync(file.FullName, rerank, useOfficialQueries, normalizedQuery, corpusFingerprint))
            {
                continue;
            }

            var result = await LoadRunPayloadAsync(file.FullName);
            result.RunPath = file.FullName;
            return result;
        }

        return null;
    }

    private async Task<bool> RunPayloadMatchesAsync(
        string runPath,
        bool rerank,
        bool useOfficialQueries,
        string? normalizedQuery,
        string corpusFingerprint)
    {
        await using var stream = File.OpenRead(runPath);
        using var document = await JsonDocument.ParseAsync(stream);
        var root = document.RootElement;

        if (GetBool(root, "rerank") != rerank)
        {
            return false;
        }

        if (!string.Equals(GetString(root, "manager_corpus_fingerprint"), corpusFingerprint, StringComparison.Ordinal))
        {
            return false;
        }

        var expectedMode = useOfficialQueries ? "official" : "adhoc";
        if (!string.Equals(GetString(root, "manager_request_mode"), expectedMode, StringComparison.Ordinal))
        {
            return false;
        }

        if (useOfficialQueries)
        {
            var expectedIntentFilter = rerank ? "bad" : "all";
            return string.Equals(GetString(root, "intent_filter") ?? "all", expectedIntentFilter, StringComparison.OrdinalIgnoreCase);
        }

        return string.Equals(GetString(root, "manager_query") ?? string.Empty, normalizedQuery ?? string.Empty, StringComparison.Ordinal);
    }

    private static async Task AnnotateRunPayloadAsync(
        string runPath,
        bool rerank,
        bool useOfficialQueries,
        string? normalizedQuery,
        string corpusFingerprint)
    {
        if (!File.Exists(runPath))
        {
            return;
        }

        var payload = JsonNode.Parse(await File.ReadAllTextAsync(runPath, Encoding.UTF8)) as JsonObject;
        if (payload is null)
        {
            return;
        }

        payload["manager_request_mode"] = useOfficialQueries ? "official" : "adhoc";
        payload["manager_query"] = normalizedQuery;
        payload["manager_corpus_fingerprint"] = corpusFingerprint;
        payload["manager_cached_at_utc"] = DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture);

        await File.WriteAllTextAsync(runPath, payload.ToJsonString(new JsonSerializerOptions(JsonSerializerDefaults.Web)
        {
            WriteIndented = true,
        }), Encoding.UTF8);
    }

    private async Task<Assignment2DemoRunResultDto> LoadRunPayloadAsync(string runPath)
    {
        using var stream = File.OpenRead(runPath);
        using var document = await JsonDocument.ParseAsync(stream);
        var root = document.RootElement;

        var result = new Assignment2DemoRunResultDto
        {
            RunPath = runPath,
            EmbeddingModel = GetString(root, "embedding_model") ?? "unknown",
            Metric = GetString(root, "metric") ?? "unknown",
            Table = GetString(root, "table") ?? "page_segment_long",
            TopK = GetInt(root, "top_k"),
            Rerank = GetBool(root, "rerank"),
            RerankModel = GetString(root, "rerank_model"),
            TimestampUtc = ParseUtcTimestamp(GetString(root, "timestamp")),
        };

        if (root.TryGetProperty("queries", out var queriesNode) && queriesNode.ValueKind == JsonValueKind.Array)
        {
            foreach (var queryNode in queriesNode.EnumerateArray())
            {
                result.Queries.Add(new Assignment2DemoQueryResultDto
                {
                    Label = GetString(queryNode, "label") ?? string.Empty,
                    Query = GetString(queryNode, "query") ?? string.Empty,
                    Expected = GetString(queryNode, "expected") ?? string.Empty,
                    Initial = ParseHits(queryNode, "initial"),
                    Rerank = ParseHits(queryNode, "rerank"),
                });
            }
        }

        return result;
    }

    private static List<Assignment2DemoHitDto> ParseHits(JsonElement queryNode, string propertyName)
    {
        if (!queryNode.TryGetProperty(propertyName, out var hitsNode) || hitsNode.ValueKind != JsonValueKind.Array)
        {
            return new List<Assignment2DemoHitDto>();
        }

        var hits = new List<Assignment2DemoHitDto>();
        foreach (var hitNode in hitsNode.EnumerateArray())
        {
            hits.Add(new Assignment2DemoHitDto
            {
                PageId = GetNullableInt(hitNode, "page_id"),
                Url = GetString(hitNode, "url"),
                Score = GetNullableDouble(hitNode, "score"),
                RerankScore = GetNullableDouble(hitNode, "rerank_score"),
                Preview = GetString(hitNode, "preview") ?? string.Empty,
            });
        }

        return hits;
    }

    private static string ParseAnnIndex(string? indexDefinition)
    {
        if (string.IsNullOrWhiteSpace(indexDefinition))
        {
            return "unknown";
        }

        if (indexDefinition.Contains("ivfflat", StringComparison.OrdinalIgnoreCase))
        {
            return "IVFFlat";
        }

        if (indexDefinition.Contains("hnsw", StringComparison.OrdinalIgnoreCase))
        {
            return "HNSW";
        }

        return "unknown";
    }

    private static string ParseSimilarityMetric(string? indexDefinition)
    {
        if (string.IsNullOrWhiteSpace(indexDefinition))
        {
            return "unknown";
        }

        if (indexDefinition.Contains("vector_cosine_ops", StringComparison.OrdinalIgnoreCase))
        {
            return "cosine";
        }

        if (indexDefinition.Contains("vector_l2_ops", StringComparison.OrdinalIgnoreCase))
        {
            return "l2";
        }

        if (indexDefinition.Contains("vector_ip_ops", StringComparison.OrdinalIgnoreCase))
        {
            return "inner-product";
        }

        return "unknown";
    }

    private static string? GetString(JsonElement element, string propertyName)
    {
        return element.TryGetProperty(propertyName, out var node) && node.ValueKind == JsonValueKind.String
            ? node.GetString()
            : null;
    }

    private static int GetInt(JsonElement element, string propertyName)
    {
        return element.TryGetProperty(propertyName, out var node) && node.TryGetInt32(out var value)
            ? value
            : 0;
    }

    private static int? GetNullableInt(JsonElement element, string propertyName)
    {
        return element.TryGetProperty(propertyName, out var node) && node.TryGetInt32(out var value)
            ? value
            : null;
    }

    private static bool GetBool(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var node))
        {
            return false;
        }

        return node.ValueKind == JsonValueKind.True
            || (node.ValueKind == JsonValueKind.String && bool.TryParse(node.GetString(), out var value) && value);
    }

    private static double? GetNullableDouble(JsonElement element, string propertyName)
    {
        return element.TryGetProperty(propertyName, out var node) && node.TryGetDouble(out var value)
            ? value
            : null;
    }

    private static DateTime? ParseUtcTimestamp(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
        {
            return null;
        }

        return DateTime.TryParse(raw, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out var value)
            ? value
            : null;
    }
}
