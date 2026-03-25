using System;
using System.Collections.Generic;

namespace ManagerApp.Data;

public partial class PageType
{
    public string Code { get; set; } = null!;

    public virtual ICollection<Page> Pages { get; set; } = new List<Page>();
}
