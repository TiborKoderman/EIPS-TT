using ManagerApp.Models;

namespace ManagerApp.Services;

/// <summary>
/// Service for retrieving crawler statistics from the database
/// </summary>
public interface IStatisticsService
{
    /// <summary>
    /// Get comprehensive statistics for the dashboard
    /// </summary>
    Task<StatisticsViewModel> GetStatisticsAsync();

    /// <summary>
    /// Get count of pages by page type (HTML, BINARY, DUPLICATE, FRONTIER)
    /// </summary>
    Task<Dictionary<string, int>> GetPageTypeCountsAsync();

    /// <summary>
    /// Get total count of duplicate pages
    /// </summary>
    Task<int> GetDuplicateCountAsync();

    /// <summary>
    /// Get total count of images in the database
    /// </summary>
    Task<int> GetImageCountAsync();

    /// <summary>
    /// Get count of binary files by type (PDF, DOC, DOCX, PPT, PPTX)
    /// </summary>
    Task<Dictionary<string, int>> GetBinaryFileCountsAsync();

    /// <summary>
    /// Get count of binary image files by type (JPG, PNG, WEBP, GIF, SVG, BMP, TIFF, ICO, AVIF, OTHER)
    /// </summary>
    Task<Dictionary<string, int>> GetImageFileCountsAsync();

    /// <summary>
    /// Calculate average number of images per HTML page
    /// </summary>
    Task<double> GetAverageImagesPerPageAsync();
}
