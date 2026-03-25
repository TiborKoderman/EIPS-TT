using System;
using System.Collections.Generic;

namespace ManagerApp.Data;

public partial class Page
{
    public int Id { get; set; }

    public int? SiteId { get; set; }

    public string? PageTypeCode { get; set; }

    public string? Url { get; set; }

    public string? HtmlContent { get; set; }

    public int? HttpStatusCode { get; set; }

    public DateTime? AccessedTime { get; set; }

    public string? ContentHash { get; set; }

    public int? DuplicateOf { get; set; }

    public string? CanonicalUrl { get; set; }

    public int? DuplicateOfPageId { get; set; }

    public virtual Page? DuplicateOfNavigation { get; set; }

    public virtual Page? DuplicateOfPage { get; set; }

    public virtual ICollection<Image> Images { get; set; } = new List<Image>();

    public virtual ICollection<Page> InverseDuplicateOfNavigation { get; set; } = new List<Page>();

    public virtual ICollection<Page> InverseDuplicateOfPage { get; set; } = new List<Page>();

    public virtual ICollection<PageDatum> PageData { get; set; } = new List<PageDatum>();

    public virtual PageType? PageTypeCodeNavigation { get; set; }

    public virtual Site? Site { get; set; }

    public virtual ICollection<Page> FromPages { get; set; } = new List<Page>();

    public virtual ICollection<Page> ToPages { get; set; } = new List<Page>();
}
