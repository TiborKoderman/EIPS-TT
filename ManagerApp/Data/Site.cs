using System;
using System.Collections.Generic;

namespace ManagerApp.Data;

public partial class Site
{
    public int Id { get; set; }

    public string? Domain { get; set; }

    public string? RobotsContent { get; set; }

    public string? SitemapContent { get; set; }

    public virtual ICollection<Page> Pages { get; set; } = new List<Page>();
}
