using ManagerApp.Models;

namespace ManagerApp.Services;

public interface IAssignment2Service
{
    Task<Assignment2OverviewDto> GetAssignment2OverviewAsync();
    Task<List<Assignment2DocumentSummaryDto>> SearchAssignment2DocumentsAsync(string? searchTerm, string? contentType, bool? hasCleanedText, int skip = 0, int take = 100);
    Task<Assignment2DocumentDetailDto?> GetAssignment2DocumentAsync(int pageId);
    Task<Assignment2DemoRunResultDto> RunAssignment2DemoAsync(string? query = null, bool rerank = false, bool useOfficialQueries = true);
    Task<List<Assignment2QueryDefinitionDto>> GetAssignment2QueriesAsync();
    Task<Assignment2SiteMetricsDto> GetAssignment2SiteMetricsAsync(int siteId);
}
