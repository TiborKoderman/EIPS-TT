import argparse
import psycopg2
import psycopg2.extras
from sentence_transformers import SentenceTransformer
import logging
from device_utils import resolve_torch_device

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def process_table(cur, model, table_name, batch_size=100, worker_id=0, num_workers=1):
    logging.info(f"Processing table {table_name} [worker {worker_id}/{num_workers}]")

    shard_filter = f"AND (id % {num_workers}) = {worker_id}" if num_workers > 1 else ""
    cur.execute(f"SELECT COUNT(*) FROM crawldb.{table_name} WHERE embedding IS NULL {shard_filter};")
    count = cur.fetchone()[0]
    logging.info(f"Found {count} rows without embedding")

    if count == 0:
        return

    processed = 0
    while True:
        cur.execute(f"SELECT id, segment_text FROM crawldb.{table_name} WHERE embedding IS NULL {shard_filter} ORDER BY id LIMIT {batch_size};")
        rows = cur.fetchall()
        if not rows:
            break

        ids = [r[0] for r in rows]
        texts = [r[1] for r in rows]

        # Calculate embeddings
        embeddings = model.encode(texts, show_progress_bar=False)

        # Batch update via execute_values (one round-trip per batch)
        psycopg2.extras.execute_values(
            cur,
            f"UPDATE crawldb.{table_name} AS t "
            f"SET embedding = d.emb::vector, embedded_at = NOW() "
            f"FROM (VALUES %s) AS d(id, emb) WHERE t.id = d.id::int",
            [(i, str(e.tolist())) for i, e in zip(ids, embeddings)],
        )

        processed += len(rows)
        logging.info(f"Updated {processed}/{count} in {table_name}")

    logging.info(f"Finished {table_name}")

def main():
    parser = argparse.ArgumentParser(description="Compute embeddings for text segments")
    parser.add_argument("--db-name", default="crawldb")
    parser.add_argument("--db-user", default="postgres")
    parser.add_argument("--db-pass", default="postgres")
    parser.add_argument("--db-host", default="localhost")
    parser.add_argument("--model-name", default="sentence-transformers/LaBSE")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--device", default=None, help="Torch device override (e.g. cpu, cuda)")
    parser.add_argument("--worker-id", type=int, default=0, help="Worker shard index (0-based)")
    parser.add_argument("--num-workers", type=int, default=1, help="Total number of parallel workers")
    parser.add_argument(
        "--table",
        choices=["page_segment_short", "page_segment_long", "both"],
        default="both",
        help="Which segment table to embed",
    )
    args = parser.parse_args()

    device = resolve_torch_device(args.device)
    logging.info(f"Loading model: {args.model_name} on device={device}")
    model = SentenceTransformer(args.model_name, device=device)

    conn = psycopg2.connect(
        dbname=args.db_name,
        user=args.db_user,
        password=args.db_pass,
        host=args.db_host
    )
    conn.autocommit = True
    cur = conn.cursor()

    from pgvector.psycopg2 import register_vector
    register_vector(conn)

    if args.table in {"page_segment_short", "both"}:
        process_table(cur, model, 'page_segment_short', args.batch_size, args.worker_id, args.num_workers)
    if args.table in {"page_segment_long", "both"}:
        process_table(cur, model, 'page_segment_long', args.batch_size, args.worker_id, args.num_workers)

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
