using System;
using System.Collections.Generic;

namespace ManagerApp.Data;

public partial class DataType
{
    public string Code { get; set; } = null!;

    public virtual ICollection<PageDatum> PageData { get; set; } = new List<PageDatum>();
}
