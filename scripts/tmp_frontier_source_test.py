from uuid import uuid4
from db.pg_connect import get_connection, load_db_config
from db.frontier_store import PostgresFrontierStore
cfg = load_db_config()
url = "https://example.com/test-frontier-source"
dummy_src = f"src-{uuid4()}"
with get_connection(cfg) as conn:
    f = PostgresFrontierStore(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM crawldb.page WHERE canonical_url = %s;", (url,))
        cur.execute(
            """
            INSERT INTO crawldb.page (site_id, page_type_code, url, canonical_url, html_content)
            VALUES (NULL, 'HTML', %s, %s, NULL)
            RETURNING id;
            """,
            (dummy_src, dummy_src),
        )
        source_id = cur.fetchone()[0]
    conn.commit()
    f.enqueue(url, source_page_id=source_id, depth=1, priority=42.0)
    claimed = f.claim_next(worker_id="t")
    print("claimed", claimed.canonical_url, "prio", claimed.priority, "source", claimed.source_page_id)
    assert claimed.source_page_id == source_id
    f.mark_done(claimed.frontier_page_id)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM crawldb.page WHERE id = %s;", (source_id,))
    conn.commit()
print("ok")
