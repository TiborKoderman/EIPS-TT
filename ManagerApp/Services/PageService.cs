using Microsoft.EntityFrameworkCore;
using ManagerApp.Data;
using ManagerApp.Models;
using Npgsql;
using NpgsqlTypes;
using System.Net;
using System.Text.Json;

namespace ManagerApp.Services;

/// <summary>
/// Implementation of page service for searching and retrieving page data
/// </summary>
public class PageService : IPageService
{
    private readonly CrawldbContext _context;
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public PageService(CrawldbContext context)
    {
        _context = context;
    }

    /// <summary>
    /// Search pages with fuzzy matching on URL and HTML content
    /// Supports filtering by page type and pagination
    /// </summary>
    public async Task<List<PageSearchDto>> SearchPagesAsync(
        string? searchTerm,
        string? pageType,
        string? orderBy = null,
        int skip = 0,
        int take = 50)
    {
        var query = _context.Pages.AsQueryable();

        // Filter by page type if specified
        if (!string.IsNullOrWhiteSpace(pageType) && pageType != "ALL")
        {
            var normalizedType = pageType.Trim().ToUpperInvariant();
            if (normalizedType == "DUPLICATE")
            {
                query = query.Where(p => p.DuplicateOfPageId != null);
            }
            else
            {
                query = query.Where(p => p.PageTypeCode != null && p.PageTypeCode.ToUpper() == normalizedType);
            }
        }

        // Fuzzy search on URL and HTML content (case-insensitive)
        if (!string.IsNullOrWhiteSpace(searchTerm))
        {
            var pattern = $"%{searchTerm.Trim()}%";
            query = query.Where(p =>
                (p.Url != null && EF.Functions.ILike(p.Url, pattern)) ||
                (p.HtmlContent != null && EF.Functions.ILike(p.HtmlContent, pattern))
            );
        }

        var candidates = await query
            .Include(p => p.Site)
            .Select(p => new PageSearchDto
            {
                Id = p.Id,
                Url = p.Url ?? "",
                PageType = p.PageTypeCode ?? "HTML",
                HttpStatus = p.HttpStatusCode,
                AccessedTime = p.AccessedTime,
                SiteDomain = p.Site != null ? p.Site.Domain : null,
                IsDuplicate = p.DuplicateOfPageId != null
            })
            .ToListAsync();

        if (candidates.Count == 0)
        {
            return candidates;
        }

        var policy = await LoadRelevancePolicyAsync();
        var frontierHints = await LoadFrontierHintsAsync(candidates.Select(row => row.Url));

        foreach (var row in candidates)
        {
            var hint = frontierHints.GetValueOrDefault(row.Url);
            var score = ScorePageUrl(
                row.Url,
                hint.SourceUrl,
                hint.Depth,
                policy,
                out var hasKeyword,
                out var hasAllowedSuffix,
                out var hasSameHost);

            row.RelevanceScore = score;
            row.HasKeywordEvidence = hasKeyword;
            row.HasAllowedSuffixEvidence = hasAllowedSuffix;
            row.HasSameHostEvidence = hasSameHost;
            row.FrontierDepth = hint.Depth;
        }

        var normalizedOrder = (orderBy ?? "latest").Trim().ToLowerInvariant();
        IEnumerable<PageSearchDto> ordered = normalizedOrder == "best_score"
            ? candidates
                .OrderByDescending(row => row.RelevanceScore)
                .ThenByDescending(row => row.AccessedTime)
                .ThenBy(row => row.Id)
            : candidates
                .OrderByDescending(row => row.AccessedTime)
                .ThenBy(row => row.Id);

        return ordered
            .Skip(skip)
            .Take(take)
            .ToList();
    }

    public async Task<List<RelevanceReportRowDto>> GetRelevanceReportAsync(string? searchTerm, int skip = 0, int take = 100)
    {
        var query = _context.Pages
            .Where(page => page.Url != null && page.PageTypeCode != "FRONTIER");

        if (!string.IsNullOrWhiteSpace(searchTerm))
        {
            var pattern = $"%{searchTerm.Trim()}%";
            query = query.Where(p =>
                (p.Url != null && EF.Functions.ILike(p.Url, pattern)) ||
                (p.HtmlContent != null && EF.Functions.ILike(p.HtmlContent, pattern))
            );
        }

        var rows = await query
            .Include(page => page.Site)
            .Select(page => new PageSearchDto
            {
                Id = page.Id,
                Url = page.Url ?? string.Empty,
                PageType = page.PageTypeCode ?? "HTML",
                HttpStatus = page.HttpStatusCode,
                AccessedTime = page.AccessedTime,
                SiteDomain = page.Site != null ? page.Site.Domain : null,
                IsDuplicate = page.DuplicateOfPageId != null,
            })
            .ToListAsync();

        if (rows.Count == 0)
        {
            return new List<RelevanceReportRowDto>();
        }

        var policy = await LoadRelevancePolicyAsync();
        var hints = await LoadFrontierHintsAsync(rows.Select(row => row.Url));
        foreach (var row in rows)
        {
            var hint = hints.GetValueOrDefault(row.Url);
            var score = ScorePageUrl(
                row.Url,
                hint.SourceUrl,
                hint.Depth,
                policy,
                out var hasKeyword,
                out var hasAllowedSuffix,
                out var hasSameHost);

            row.RelevanceScore = score;
            row.HasKeywordEvidence = hasKeyword;
            row.HasAllowedSuffixEvidence = hasAllowedSuffix;
            row.HasSameHostEvidence = hasSameHost;
            row.FrontierDepth = hint.Depth;
        }

        return rows
            .OrderByDescending(row => row.RelevanceScore)
            .ThenByDescending(row => row.AccessedTime)
            .ThenBy(row => row.Id)
            .Skip(Math.Max(0, skip))
            .Take(Math.Clamp(take, 1, 500))
            .Select(row => new RelevanceReportRowDto
            {
                Id = row.Id,
                Url = row.Url,
                PageType = row.PageType,
                HttpStatus = row.HttpStatus,
                AccessedTime = row.AccessedTime,
                SiteDomain = row.SiteDomain,
                RelevanceScore = row.RelevanceScore,
                HasKeywordEvidence = row.HasKeywordEvidence,
                HasAllowedSuffixEvidence = row.HasAllowedSuffixEvidence,
                HasSameHostEvidence = row.HasSameHostEvidence,
                FrontierDepth = row.FrontierDepth,
            })
            .ToList();
    }

    public async Task<List<QueueTopItemViewModel>> GetTopQueueItemsAsync(int take = 20)
    {
        var boundedTake = Math.Clamp(take, 1, 100);
        var items = new List<QueueTopItemViewModel>();

        await using var connection = new NpgsqlConnection(_context.Database.GetConnectionString());
        await connection.OpenAsync();

        const string sql = """
            SELECT
                url,
                source_url,
                priority,
                depth,
                discovered_at,
                state
            FROM crawldb.frontier_queue
            WHERE
                (
                    state::text IN ('QUEUED', 'queued', 'in_memory')
                    OR state::text = 'LOCKED'
                )
                AND (
                    locked_by_worker_id IS NULL
                    OR state::text IN ('QUEUED', 'queued', 'in_memory')
                )
            ORDER BY priority DESC, discovered_at ASC
            LIMIT @take;
            """;

        await using (var command = new NpgsqlCommand(sql, connection))
        {
            command.Parameters.AddWithValue("take", boundedTake);
            await using var reader = await command.ExecuteReaderAsync();
            while (await reader.ReadAsync())
            {
                items.Add(new QueueTopItemViewModel
                {
                    Url = reader.IsDBNull(0) ? string.Empty : reader.GetString(0),
                    SourceUrl = reader.IsDBNull(1) ? null : reader.GetString(1),
                    Priority = reader.IsDBNull(2) ? 0 : reader.GetInt32(2),
                    Depth = reader.IsDBNull(3) ? 0 : reader.GetInt32(3),
                    DiscoveredAt = reader.IsDBNull(4) ? DateTime.UtcNow : reader.GetDateTime(4),
                    State = reader.IsDBNull(5) ? "QUEUED" : reader.GetString(5),
                });
            }
        }

        if (items.Count == 0)
        {
            return items;
        }

        var policy = await LoadRelevancePolicyAsync();
        foreach (var row in items)
        {
            row.RelevanceScore = ScorePageUrl(
                row.Url,
                row.SourceUrl,
                row.Depth,
                policy,
                out var hasKeyword,
                out var hasAllowedSuffix,
                out var hasSameHost);
            row.HasKeywordEvidence = hasKeyword;
            row.HasAllowedSuffixEvidence = hasAllowedSuffix;
            row.HasSameHostEvidence = hasSameHost;
        }

        return items;
    }

    /// <summary>
    /// Get total count of search results for pagination
    /// Uses same filtering logic as SearchPagesAsync
    /// </summary>
    public async Task<int> GetSearchResultsCountAsync(string? searchTerm, string? pageType)
    {
        var query = _context.Pages.AsQueryable();

        if (!string.IsNullOrWhiteSpace(pageType) && pageType != "ALL")
        {
            var normalizedType = pageType.Trim().ToUpperInvariant();
            if (normalizedType == "DUPLICATE")
            {
                query = query.Where(p => p.DuplicateOfPageId != null);
            }
            else
            {
                query = query.Where(p => p.PageTypeCode != null && p.PageTypeCode.ToUpper() == normalizedType);
            }
        }

        if (!string.IsNullOrWhiteSpace(searchTerm))
        {
            var pattern = $"%{searchTerm.Trim()}%";
            query = query.Where(p =>
                (p.Url != null && EF.Functions.ILike(p.Url, pattern)) ||
                (p.HtmlContent != null && EF.Functions.ILike(p.HtmlContent, pattern))
            );
        }

        return await query.CountAsync();
    }

    public async Task<PageEvaluationSummaryDto> GetEvaluationSummaryAsync()
    {
        var rows = await _context.Pages
            .Where(page => page.Url != null && page.PageTypeCode != "FRONTIER")
            .Select(page => new { Url = page.Url! })
            .ToListAsync();

        var summary = new PageEvaluationSummaryDto
        {
            TotalPages = rows.Count,
        };

        if (rows.Count == 0)
        {
            return summary;
        }

        var policy = await LoadRelevancePolicyAsync();
        var hints = await LoadFrontierHintsAsync(rows.Select(row => row.Url));
        var scores = new List<double>(rows.Count);

        foreach (var row in rows)
        {
            var hint = hints.GetValueOrDefault(row.Url);
            var score = ScorePageUrl(
                row.Url,
                hint.SourceUrl,
                hint.Depth,
                policy,
                out var hasKeyword,
                out var hasAllowedSuffix,
                out var hasSameHost);

            scores.Add(score);
            if (score > 0)
            {
                summary.PositiveScorePages += 1;
            }

            if (hasKeyword)
            {
                summary.KeywordEvidencePages += 1;
            }

            if (hasAllowedSuffix)
            {
                summary.AllowedSuffixEvidencePages += 1;
            }

            if (hasSameHost)
            {
                summary.SameHostEvidencePages += 1;
            }

            // Classifier-free proxy for likely relevance:
            // explicit topical signals from configured keywords and scope suffixes.
            if (hasKeyword || hasAllowedSuffix)
            {
                summary.EstimatedRelevantPages += 1;
            }
        }

        summary.EvaluatedPages = scores.Count;
        if (scores.Count > 0)
        {
            scores.Sort();
            summary.AverageScore = scores.Average();
            summary.TopScore = scores[^1];
            summary.MedianScore = scores[scores.Count / 2];
        }

        return summary;
    }

    /// <summary>
    /// Get complete page details with all related data
    /// Includes site, images, and binary data (page_data table)
    /// </summary>
    public async Task<Page?> GetPageDetailsAsync(int pageId)
    {
        return await _context.Pages
            .Include(p => p.Site)
            .Include(p => p.Images)
            .Include(p => p.PageData)
            .Include(p => p.PageTypeCodeNavigation)
            .FirstOrDefaultAsync(p => p.Id == pageId);
    }

    private readonly record struct FrontierHint(string? SourceUrl, int Depth);

    private sealed class RelevancePolicySnapshot
    {
        public List<string> Keywords { get; init; } = new();
        public List<string> AllowedSuffixes { get; init; } = new();
        public double SameHostBoost { get; init; } = 10.0;
        public double AllowedSuffixBoost { get; init; } = 20.0;
        public double KeywordBoost { get; init; } = 5.0;
        public double DepthPenalty { get; init; } = 0.2;
    }

    private async Task<Dictionary<string, FrontierHint>> LoadFrontierHintsAsync(IEnumerable<string> urls)
    {
        var unique = urls
            .Where(url => !string.IsNullOrWhiteSpace(url))
            .Select(url => url.Trim())
            .Distinct(StringComparer.Ordinal)
            .ToArray();

        var hints = new Dictionary<string, FrontierHint>(StringComparer.Ordinal);
        if (unique.Length == 0)
        {
            return hints;
        }

        await using var connection = new NpgsqlConnection(_context.Database.GetConnectionString());
        await connection.OpenAsync();

        const string sql = """
            SELECT url, source_url, depth
            FROM crawldb.frontier_queue
            WHERE url = ANY(@urls)
            ORDER BY discovered_at DESC;
            """;

        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.Add("urls", NpgsqlDbType.Array | NpgsqlDbType.Text).Value = unique;

        await using var reader = await command.ExecuteReaderAsync();
        while (await reader.ReadAsync())
        {
            var url = reader.GetString(0);
            if (hints.ContainsKey(url))
            {
                continue;
            }

            var sourceUrl = reader.IsDBNull(1) ? null : reader.GetString(1);
            var depth = reader.IsDBNull(2) ? 0 : reader.GetInt32(2);
            hints[url] = new FrontierHint(sourceUrl, depth);
        }

        return hints;
    }

    private async Task<RelevancePolicySnapshot> LoadRelevancePolicyAsync()
    {
        var fallback = new RelevancePolicySnapshot
        {
            Keywords =
            [
                "medicine", "medic", "health", "doctor", "clinic", "hospital", "treatment", "disease",
                "zdrav", "zdravje", "bolnis", "ambul", "cepl", "preven", "higi"
            ],
            AllowedSuffixes = ["gov.si", "nijz.si", "kclj.si", "zdravljenjenadom.si"],
        };

        try
        {
            await using var connection = new NpgsqlConnection(_context.Database.GetConnectionString());
            await connection.OpenAsync();

            const string sql = """
                SELECT value::text
                FROM manager.global_setting
                WHERE key = 'crawler.global_config';
                """;

            await using var cmd = new NpgsqlCommand(sql, connection);
            var raw = await cmd.ExecuteScalarAsync() as string;
            if (string.IsNullOrWhiteSpace(raw))
            {
                return fallback;
            }

            var config = JsonSerializer.Deserialize<WorkerGlobalConfigViewModel>(raw, JsonOptions);
            if (config is null)
            {
                return fallback;
            }

            var keywords = (config.TopicKeywords.Count > 0 ? config.TopicKeywords : config.TopicKeywordsText
                    .Split(new[] { '\r', '\n', ',', ';' }, StringSplitOptions.RemoveEmptyEntries)
                    .Select(value => value.Trim())
                    .Where(value => !string.IsNullOrWhiteSpace(value))
                    .ToList())
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();

            var suffixes = (config.RelevanceAllowedDomainSuffixes.Count > 0
                    ? config.RelevanceAllowedDomainSuffixes
                    : config.RelevanceAllowedDomainSuffixesText
                        .Split(new[] { '\r', '\n', ',', ';' }, StringSplitOptions.RemoveEmptyEntries)
                        .Select(value => value.Trim().TrimStart('.').ToLowerInvariant())
                        .Where(value => !string.IsNullOrWhiteSpace(value))
                        .ToList())
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();

            if (suffixes.Count == 0)
            {
                var seedHosts = (config.SeedEntries ?? new List<SeedEntryViewModel>())
                    .Where(entry => entry.Enabled && !string.IsNullOrWhiteSpace(entry.Url))
                    .Select(entry => entry.Url.Trim())
                    .Concat((config.SeedUrlsText ?? string.Empty)
                        .Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries)
                        .Select(url => url.Trim()))
                    .Select(TryExtractHost)
                    .Where(host => !string.IsNullOrWhiteSpace(host))
                    .Select(host => host!)
                    .Distinct(StringComparer.OrdinalIgnoreCase)
                    .ToList();

                suffixes = seedHosts;
            }

            return new RelevancePolicySnapshot
            {
                Keywords = keywords.Count > 0 ? keywords : fallback.Keywords,
                AllowedSuffixes = suffixes,
                SameHostBoost = Math.Max(0, config.RelevanceSameHostBoost),
                AllowedSuffixBoost = Math.Max(0, config.RelevanceAllowedSuffixBoost),
                KeywordBoost = Math.Max(0, config.RelevanceKeywordBoost),
                DepthPenalty = Math.Max(0, config.RelevanceDepthPenalty),
            };
        }
        catch
        {
            return fallback;
        }
    }

    private static double ScorePageUrl(
        string url,
        string? sourceUrl,
        int depth,
        RelevancePolicySnapshot policy,
        out bool hasKeyword,
        out bool hasAllowedSuffix,
        out bool hasSameHost)
    {
        hasKeyword = false;
        hasAllowedSuffix = false;
        hasSameHost = false;

        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)
            || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps)
            || string.IsNullOrWhiteSpace(uri.Host))
        {
            return 0;
        }

        var host = uri.Host.ToLowerInvariant();
        var score = 0.0;

        if (!string.IsNullOrWhiteSpace(sourceUrl)
            && Uri.TryCreate(sourceUrl, UriKind.Absolute, out var sourceUri)
            && string.Equals(sourceUri.Host, uri.Host, StringComparison.OrdinalIgnoreCase))
        {
            hasSameHost = true;
            score += policy.SameHostBoost;
        }

        foreach (var suffix in policy.AllowedSuffixes)
        {
            var normalized = suffix.Trim().TrimStart('.').ToLowerInvariant();
            if (string.IsNullOrWhiteSpace(normalized))
            {
                continue;
            }

            if (host == normalized || host.EndsWith($".{normalized}", StringComparison.Ordinal))
            {
                hasAllowedSuffix = true;
                score += policy.AllowedSuffixBoost;
                break;
            }
        }

        var decodedPath = WebUtility.UrlDecode(uri.AbsolutePath) ?? uri.AbsolutePath;
        var decodedQuery = WebUtility.UrlDecode(uri.Query) ?? uri.Query;
        var haystack = (host + " " + decodedPath + " " + decodedQuery).ToLowerInvariant();
        foreach (var keyword in policy.Keywords)
        {
            var normalized = keyword.Trim().ToLowerInvariant();
            if (string.IsNullOrWhiteSpace(normalized))
            {
                continue;
            }

            if (haystack.Contains(normalized, StringComparison.Ordinal))
            {
                hasKeyword = true;
                score += policy.KeywordBoost;
            }
        }

        // Small non-zero baseline for valid URLs on known hosts to avoid all-zero reports.
        if (!hasKeyword && !hasAllowedSuffix && !hasSameHost)
        {
            score += 0.1;
        }

        score -= policy.DepthPenalty * Math.Max(0, depth);
        return Math.Max(0, score);
    }

    private static string? TryExtractHost(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var parsed))
        {
            return null;
        }

        return string.IsNullOrWhiteSpace(parsed.Host) ? null : parsed.Host.Trim().ToLowerInvariant();
    }
}
