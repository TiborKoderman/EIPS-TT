using System;
using System.Collections.Generic;
using Microsoft.EntityFrameworkCore;

namespace ManagerApp.Data;

public partial class CrawldbContext : DbContext
{
    public CrawldbContext(DbContextOptions<CrawldbContext> options)
        : base(options)
    {
    }

    public virtual DbSet<DataType> DataTypes { get; set; }

    public virtual DbSet<Image> Images { get; set; }

    public virtual DbSet<Page> Pages { get; set; }

    public virtual DbSet<PageDatum> PageData { get; set; }

    public virtual DbSet<PageType> PageTypes { get; set; }

    public virtual DbSet<Site> Sites { get; set; }

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<DataType>(entity =>
        {
            entity.HasKey(e => e.Code).HasName("pk_data_type_code");

            entity.ToTable("data_type", "crawldb");

            entity.Property(e => e.Code)
                .HasMaxLength(20)
                .HasColumnName("code");
        });

        modelBuilder.Entity<Image>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("pk_image_id");

            entity.ToTable("image", "crawldb");

            entity.HasIndex(e => e.PageId, "idx_image_page_id");

            entity.Property(e => e.Id).HasColumnName("id");
            entity.Property(e => e.AccessedTime)
                .HasColumnType("timestamp without time zone")
                .HasColumnName("accessed_time");
            entity.Property(e => e.ContentType)
                .HasMaxLength(50)
                .HasColumnName("content_type");
            entity.Property(e => e.Data).HasColumnName("data");
            entity.Property(e => e.Filename)
                .HasMaxLength(255)
                .HasColumnName("filename");
            entity.Property(e => e.PageId).HasColumnName("page_id");

            entity.HasOne(d => d.Page).WithMany(p => p.Images)
                .HasForeignKey(d => d.PageId)
                .OnDelete(DeleteBehavior.Restrict)
                .HasConstraintName("fk_image_page_data");
        });

        modelBuilder.Entity<Page>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("pk_page_id");

            entity.ToTable("page", "crawldb");

            entity.HasIndex(e => e.ContentHash, "idx_page_content_hash");

            entity.HasIndex(e => e.DuplicateOfPageId, "idx_page_duplicate_of_page_id");

            entity.HasIndex(e => e.PageTypeCode, "idx_page_page_type_code");

            entity.HasIndex(e => e.SiteId, "idx_page_site_id");

            entity.HasIndex(e => e.CanonicalUrl, "unq_page_canonical_url").IsUnique();

            entity.HasIndex(e => e.Url, "unq_url_idx").IsUnique();

            entity.Property(e => e.Id).HasColumnName("id");
            entity.Property(e => e.AccessedTime)
                .HasColumnType("timestamp without time zone")
                .HasColumnName("accessed_time");
            entity.Property(e => e.CanonicalUrl)
                .HasMaxLength(3000)
                .HasColumnName("canonical_url");
            entity.Property(e => e.ContentHash)
                .HasMaxLength(64)
                .HasColumnName("content_hash");
            entity.Property(e => e.DuplicateOf).HasColumnName("duplicate_of");
            entity.Property(e => e.DuplicateOfPageId).HasColumnName("duplicate_of_page_id");
            entity.Property(e => e.HtmlContent).HasColumnName("html_content");
            entity.Property(e => e.HttpStatusCode).HasColumnName("http_status_code");
            entity.Property(e => e.PageTypeCode)
                .HasMaxLength(20)
                .HasColumnName("page_type_code");
            entity.Property(e => e.SiteId).HasColumnName("site_id");
            entity.Property(e => e.Url)
                .HasMaxLength(3000)
                .HasColumnName("url");

            entity.HasOne(d => d.DuplicateOfNavigation).WithMany(p => p.InverseDuplicateOfNavigation)
                .HasForeignKey(d => d.DuplicateOf)
                .HasConstraintName("page_duplicate_of_fkey");

            entity.HasOne(d => d.DuplicateOfPage).WithMany(p => p.InverseDuplicateOfPage)
                .HasForeignKey(d => d.DuplicateOfPageId)
                .OnDelete(DeleteBehavior.SetNull)
                .HasConstraintName("fk_page_duplicate_of_page");

            entity.HasOne(d => d.PageTypeCodeNavigation).WithMany(p => p.Pages)
                .HasForeignKey(d => d.PageTypeCode)
                .OnDelete(DeleteBehavior.Restrict)
                .HasConstraintName("fk_page_page_type");

            entity.HasOne(d => d.Site).WithMany(p => p.Pages)
                .HasForeignKey(d => d.SiteId)
                .OnDelete(DeleteBehavior.Restrict)
                .HasConstraintName("fk_page_site");

            entity.HasMany(d => d.FromPages).WithMany(p => p.ToPages)
                .UsingEntity<Dictionary<string, object>>(
                    "Link",
                    r => r.HasOne<Page>().WithMany()
                        .HasForeignKey("FromPage")
                        .OnDelete(DeleteBehavior.Restrict)
                        .HasConstraintName("fk_link_page"),
                    l => l.HasOne<Page>().WithMany()
                        .HasForeignKey("ToPage")
                        .OnDelete(DeleteBehavior.Restrict)
                        .HasConstraintName("fk_link_page_1"),
                    j =>
                    {
                        j.HasKey("FromPage", "ToPage").HasName("_0");
                        j.ToTable("link", "crawldb");
                        j.HasIndex(new[] { "FromPage" }, "idx_link_from_page");
                        j.HasIndex(new[] { "ToPage" }, "idx_link_to_page");
                        j.IndexerProperty<int>("FromPage").HasColumnName("from_page");
                        j.IndexerProperty<int>("ToPage").HasColumnName("to_page");
                    });

            entity.HasMany(d => d.ToPages).WithMany(p => p.FromPages)
                .UsingEntity<Dictionary<string, object>>(
                    "Link",
                    r => r.HasOne<Page>().WithMany()
                        .HasForeignKey("ToPage")
                        .OnDelete(DeleteBehavior.Restrict)
                        .HasConstraintName("fk_link_page_1"),
                    l => l.HasOne<Page>().WithMany()
                        .HasForeignKey("FromPage")
                        .OnDelete(DeleteBehavior.Restrict)
                        .HasConstraintName("fk_link_page"),
                    j =>
                    {
                        j.HasKey("FromPage", "ToPage").HasName("_0");
                        j.ToTable("link", "crawldb");
                        j.HasIndex(new[] { "FromPage" }, "idx_link_from_page");
                        j.HasIndex(new[] { "ToPage" }, "idx_link_to_page");
                        j.IndexerProperty<int>("FromPage").HasColumnName("from_page");
                        j.IndexerProperty<int>("ToPage").HasColumnName("to_page");
                    });
        });

        modelBuilder.Entity<PageDatum>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("pk_page_data_id");

            entity.ToTable("page_data", "crawldb");

            entity.HasIndex(e => e.DataTypeCode, "idx_page_data_data_type_code");

            entity.HasIndex(e => e.PageId, "idx_page_data_page_id");

            entity.Property(e => e.Id).HasColumnName("id");
            entity.Property(e => e.Data).HasColumnName("data");
            entity.Property(e => e.DataTypeCode)
                .HasMaxLength(20)
                .HasColumnName("data_type_code");
            entity.Property(e => e.PageId).HasColumnName("page_id");

            entity.HasOne(d => d.DataTypeCodeNavigation).WithMany(p => p.PageData)
                .HasForeignKey(d => d.DataTypeCode)
                .OnDelete(DeleteBehavior.Restrict)
                .HasConstraintName("fk_page_data_data_type");

            entity.HasOne(d => d.Page).WithMany(p => p.PageData)
                .HasForeignKey(d => d.PageId)
                .OnDelete(DeleteBehavior.Restrict)
                .HasConstraintName("fk_page_data_page");
        });

        modelBuilder.Entity<PageType>(entity =>
        {
            entity.HasKey(e => e.Code).HasName("pk_page_type_code");

            entity.ToTable("page_type", "crawldb");

            entity.Property(e => e.Code)
                .HasMaxLength(20)
                .HasColumnName("code");
        });

        modelBuilder.Entity<Site>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("pk_site_id");

            entity.ToTable("site", "crawldb");

            entity.Property(e => e.Id).HasColumnName("id");
            entity.Property(e => e.Domain)
                .HasMaxLength(500)
                .HasColumnName("domain");
            entity.Property(e => e.RobotsContent).HasColumnName("robots_content");
            entity.Property(e => e.SitemapContent).HasColumnName("sitemap_content");
        });

        OnModelCreatingPartial(modelBuilder);
    }

    partial void OnModelCreatingPartial(ModelBuilder modelBuilder);
}
