using System;
using System.Collections.Generic;

namespace ManagerApp.Data;

public partial class PageDatum
{
    public int Id { get; set; }

    public int? PageId { get; set; }

    public string? DataTypeCode { get; set; }

    public byte[]? Data { get; set; }

    public virtual DataType? DataTypeCodeNavigation { get; set; }

    public virtual Page? Page { get; set; }
}
