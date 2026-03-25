using System;
using System.Collections.Generic;

namespace ManagerApp.Data;

public partial class Image
{
    public int Id { get; set; }

    public int? PageId { get; set; }

    public string? Filename { get; set; }

    public string? ContentType { get; set; }

    public byte[]? Data { get; set; }

    public DateTime? AccessedTime { get; set; }

    public virtual Page? Page { get; set; }
}
