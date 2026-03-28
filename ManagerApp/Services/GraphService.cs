using Microsoft.EntityFrameworkCore;
using ManagerApp.Data;
using ManagerApp.Models;
using Npgsql;

namespace ManagerApp.Services;

/// <summary>
/// Implementation of graph service that transforms database link data into D3.js format
/// </summary>
public class GraphService : IGraphService
{
    private readonly CrawldbContext _context;

    public GraphService(CrawldbContext context)
    {
        _context = context;
    }

    /// <summary>
    /// Get graph data formatted for D3.js visualization
    /// Nodes are pages, edges are links between pages
    /// Node size is determined by incoming link count
    /// </summary>
    public async Task<GraphDataDto> GetGraphDataAsync(int? limit = null)
    {
        var effectiveLimit = limit.GetValueOrDefault(500);
        if (effectiveLimit <= 0)
        {
            effectiveLimit = 500;
        }

        // Get incoming link counts for node sizing
        var incomingCounts = await GetIncomingLinkCountsAsync();

        var linkRows = new List<(int Source, int Target)>();
        var nodeIdsInOrder = new List<int>();
        var nodeIdSet = new HashSet<int>();

        await using (var connection = new NpgsqlConnection(_context.Database.GetConnectionString()))
        {
            await connection.OpenAsync();

            const string recentLinksSql = """
                SELECT l.from_page, l.to_page
                FROM crawldb.link l
                JOIN crawldb.page p_from ON p_from.id = l.from_page AND p_from.url IS NOT NULL
                JOIN crawldb.page p_to ON p_to.id = l.to_page AND p_to.url IS NOT NULL
                ORDER BY GREATEST(l.from_page, l.to_page) DESC
                LIMIT @link_limit;
                """;

            await using (var linkCmd = new NpgsqlCommand(recentLinksSql, connection))
            {
                linkCmd.Parameters.AddWithValue("link_limit", Math.Max(effectiveLimit * 8, 2000));
                await using var linkReader = await linkCmd.ExecuteReaderAsync();
                while (await linkReader.ReadAsync())
                {
                    var source = linkReader.GetInt32(0);
                    var target = linkReader.GetInt32(1);
                    linkRows.Add((source, target));

                    if (nodeIdSet.Count < effectiveLimit && nodeIdSet.Add(source))
                    {
                        nodeIdsInOrder.Add(source);
                    }

                    if (nodeIdSet.Count < effectiveLimit && nodeIdSet.Add(target))
                    {
                        nodeIdsInOrder.Add(target);
                    }

                    if (nodeIdSet.Count >= effectiveLimit)
                    {
                        break;
                    }
                }
            }

            if (nodeIdSet.Count == 0)
            {
                const string fallbackNodesSql = """
                    SELECT p.id
                    FROM crawldb.page p
                    WHERE p.url IS NOT NULL
                    ORDER BY p.id DESC
                    LIMIT @limit;
                    """;

                await using var fallbackCmd = new NpgsqlCommand(fallbackNodesSql, connection);
                fallbackCmd.Parameters.AddWithValue("limit", effectiveLimit);
                await using var fallbackReader = await fallbackCmd.ExecuteReaderAsync();
                while (await fallbackReader.ReadAsync())
                {
                    var id = fallbackReader.GetInt32(0);
                    if (nodeIdSet.Add(id))
                    {
                        nodeIdsInOrder.Add(id);
                    }
                }
            }

            var idListSql = string.Join(",", nodeIdsInOrder.OrderBy(id => id));
            var nodesSql = $"""
                SELECT p.id,
                       COALESCE(p.url, '') AS url,
                       COALESCE(s.domain, 'unknown') AS domain,
                       COALESCE(p.page_type_code, 'HTML') AS page_type
                FROM crawldb.page p
                LEFT JOIN crawldb.site s ON s.id = p.site_id
                WHERE p.id IN ({idListSql})
                ORDER BY p.id DESC;
                """;

            var nodes = new List<GraphNodeDto>();
            await using (var nodeCmd = new NpgsqlCommand(nodesSql, connection))
            await using (var nodeReader = await nodeCmd.ExecuteReaderAsync())
            {
                while (await nodeReader.ReadAsync())
                {
                    var id = nodeReader.GetInt32(0);
                    var url = nodeReader.GetString(1);
                    var domain = nodeReader.GetString(2);
                    nodes.Add(new GraphNodeDto
                    {
                        Id = id,
                        Url = url,
                        Domain = domain,
                        PageType = nodeReader.GetString(3),
                        Size = incomingCounts.GetValueOrDefault(id, 1),
                        RelevanceScore = EstimateGraphRelevanceScore(url, domain),
                    });
                }
            }

            var nodeIds = nodes.Select(n => n.Id).ToHashSet();
            var nodeIdByUrl = nodes
                .Where(n => !string.IsNullOrWhiteSpace(n.Url))
                .GroupBy(n => n.Url, StringComparer.Ordinal)
                .ToDictionary(group => group.Key, group => group.First().Id, StringComparer.Ordinal);

            var links = linkRows
                .Where(link => nodeIds.Contains(link.Source) && nodeIds.Contains(link.Target))
                .Distinct()
                .Select(link => new GraphLinkDto
                {
                    Source = link.Source,
                    Target = link.Target,
                })
                .ToList();

            var workers = await GetActiveWorkersAsync(connection, nodeIdByUrl);

            return new GraphDataDto
            {
                Nodes = nodes,
                Links = links,
                Workers = workers,
            };
        }
    }

    private static async Task<List<GraphWorkerDto>> GetActiveWorkersAsync(
        NpgsqlConnection connection,
        IReadOnlyDictionary<string, int> nodeIdByUrl)
    {
        const string workerSql = """
                        SELECT COALESCE(w.external_worker_id, w.id::int) AS worker_id,
                 COALESCE(w.name, '') AS worker_name,
                 COALESCE(w.status, 'idle') AS status,
                 w.current_url
                        FROM manager.worker w
                        JOIN manager.daemon d ON d.id = w.daemon_id
             WHERE lower(COALESCE(w.status, '')) = 'active'
                            AND lower(COALESCE(d.status, '')) IN ('running', 'active')
                        ORDER BY COALESCE(w.external_worker_id, w.id::int);
            """;

        var workers = new List<GraphWorkerDto>();
        await using var workerCmd = new NpgsqlCommand(workerSql, connection);
        await using var workerReader = await workerCmd.ExecuteReaderAsync();
        while (await workerReader.ReadAsync())
        {
            var workerId = workerReader.GetInt32(0);
            var currentUrl = workerReader.IsDBNull(3) ? null : workerReader.GetString(3);
            int? nodeId = null;
            if (!string.IsNullOrWhiteSpace(currentUrl) && nodeIdByUrl.TryGetValue(currentUrl, out var matchedNodeId))
            {
                nodeId = matchedNodeId;
            }

            workers.Add(new GraphWorkerDto
            {
                Id = workerId,
                Name = workerReader.GetString(1),
                Status = workerReader.GetString(2),
                CurrentUrl = currentUrl,
                CurrentNodeId = nodeId,
            });
        }

        return workers;
    }

    /// <summary>
    /// Get subgraph centered on a specific page
    /// TODO: Implement breadth-first traversal from page_id up to specified depth
    /// For now, returns all connected nodes (limited to 100)
    /// </summary>
    public async Task<GraphDataDto> GetGraphDataForPageAsync(int pageId, int depth = 2)
    {
        // TODO: Implement BFS traversal
        // For now, return limited graph
        return await GetGraphDataAsync(100);
    }

    /// <summary>
    /// Calculate incoming link count for each page
    /// Used to size nodes proportionally to their importance (more incoming links = bigger node)
    /// </summary>
    public async Task<Dictionary<int, int>> GetIncomingLinkCountsAsync()
    {
        const string sql = """
            SELECT l.to_page, COUNT(*)::int
            FROM crawldb.link l
            GROUP BY l.to_page;
            """;

        var counts = new Dictionary<int, int>();
        await using var connection = new NpgsqlConnection(_context.Database.GetConnectionString());
        await connection.OpenAsync();
        await using var cmd = new NpgsqlCommand(sql, connection);
        await using var reader = await cmd.ExecuteReaderAsync();
        while (await reader.ReadAsync())
        {
            counts[reader.GetInt32(0)] = reader.GetInt32(1);
        }

        return counts;
    }

    public async Task<SiteGraphDataDto> GetSiteGraphDataAsync()
    {
        var result = new SiteGraphDataDto();

        await using var connection = new NpgsqlConnection(_context.Database.GetConnectionString());
        await connection.OpenAsync();

        const string sitesSql = """
            SELECT
                s.id,
                COALESCE(s.domain, 'unknown') AS domain,
                COALESCE(p.url, '') AS url
            FROM crawldb.site s
            JOIN crawldb.page p ON p.site_id = s.id
            ORDER BY s.id ASC;
            """;

        var siteAggregates = new Dictionary<int, SiteAggregate>();

        await using (var sitesCmd = new NpgsqlCommand(sitesSql, connection))
        await using (var reader = await sitesCmd.ExecuteReaderAsync())
        {
            while (await reader.ReadAsync())
            {
                var siteId = reader.GetInt32(0);
                var domain = reader.GetString(1);
                var url = reader.GetString(2);
                var score = EstimateGraphRelevanceScore(url, domain);

                if (!siteAggregates.TryGetValue(siteId, out var aggregate))
                {
                    aggregate = new SiteAggregate
                    {
                        SiteId = siteId,
                        Domain = domain,
                    };
                    siteAggregates[siteId] = aggregate;
                }

                if (string.IsNullOrWhiteSpace(aggregate.Domain) || string.Equals(aggregate.Domain, "unknown", StringComparison.OrdinalIgnoreCase))
                {
                    aggregate.Domain = domain;
                }

                aggregate.PagesCount += 1;
                aggregate.ScoreSum += score;
                aggregate.TopScore = Math.Max(aggregate.TopScore, score);
            }
        }

        result.Nodes = siteAggregates
            .Values
            .OrderByDescending(item => item.PagesCount)
            .ThenBy(item => item.Domain, StringComparer.OrdinalIgnoreCase)
            .Select(item => new SiteGraphNodeDto
            {
                SiteId = item.SiteId,
                Domain = item.Domain,
                PagesCount = item.PagesCount,
                AverageScore = item.PagesCount > 0 ? item.ScoreSum / item.PagesCount : 0,
                TopScore = item.TopScore,
            })
            .ToList();

        const string edgesSql = """
            SELECT
                LEAST(from_page.site_id, to_page.site_id)::int AS source_site_id,
                GREATEST(from_page.site_id, to_page.site_id)::int AS target_site_id,
                COUNT(*)::int AS edge_count
            FROM crawldb.link l
            JOIN crawldb.page from_page ON from_page.id = l.from_page
            JOIN crawldb.page to_page ON to_page.id = l.to_page
            WHERE from_page.site_id IS NOT NULL
              AND to_page.site_id IS NOT NULL
              AND from_page.site_id <> to_page.site_id
            GROUP BY LEAST(from_page.site_id, to_page.site_id), GREATEST(from_page.site_id, to_page.site_id)
            ORDER BY edge_count DESC, source_site_id ASC, target_site_id ASC;
            """;

        await using (var edgesCmd = new NpgsqlCommand(edgesSql, connection))
        await using (var reader = await edgesCmd.ExecuteReaderAsync())
        {
            while (await reader.ReadAsync())
            {
                result.Edges.Add(new SiteGraphEdgeDto
                {
                    SourceSiteId = reader.GetInt32(0),
                    TargetSiteId = reader.GetInt32(1),
                    EdgeCount = reader.GetInt32(2),
                });
            }
        }

        return result;
    }

    private static double EstimateGraphRelevanceScore(string? url, string? domain)
    {
        var urlValue = (url ?? string.Empty).Trim().ToLowerInvariant();
        var domainValue = (domain ?? string.Empty).Trim().ToLowerInvariant();
        if (string.IsNullOrWhiteSpace(urlValue) && string.IsNullOrWhiteSpace(domainValue))
        {
            return 0;
        }

        var combined = $"{urlValue} {domainValue}";
        var score = 0.05;

        if (combined.Contains(".gov") || combined.Contains("government") || combined.Contains("ministr") || combined.Contains("uprava"))
        {
            score += 0.18;
        }

        if (combined.Contains("health") || combined.Contains("medic") || combined.Contains("hospital") || combined.Contains("clinic") || combined.Contains("doctor") || combined.Contains("disease") || combined.Contains("nijz") || combined.Contains("who.int"))
        {
            score += 0.24;
        }

        if (combined.Contains("research") || combined.Contains("science") || combined.Contains("university") || combined.Contains("faculty") || combined.Contains("edu"))
        {
            score += 0.14;
        }

        if (combined.Contains("news") || combined.Contains("blog") || combined.Contains("article"))
        {
            score += 0.1;
        }

        if (combined.Contains("covid") || combined.Contains("epidem") || combined.Contains("pandem") || combined.Contains("vaccine") || combined.Contains("virus"))
        {
            score += 0.18;
        }

        return Math.Clamp(score, 0, 1);
    }

    private sealed class SiteAggregate
    {
        public int SiteId { get; set; }
        public string Domain { get; set; } = "unknown";
        public int PagesCount { get; set; }
        public double ScoreSum { get; set; }
        public double TopScore { get; set; }
    }

}
