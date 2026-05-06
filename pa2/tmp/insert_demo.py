import json
import requests
import psycopg2
from urllib.parse import urlparse

def main():
    conn = psycopg2.connect('dbname=crawldb user=postgres password=postgres host=localhost')
    conn.autocommit = True
    cur = conn.cursor()

    with open('pa2/tmp/extraction_sample/validation_report.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    for res in data.get('results', [])[:3]:
        url = res['url']
        domain = urlparse(url).netloc
        print(f"Fetching {url}...")
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                html = resp.text

                # Insert site if not exists
                cur.execute("INSERT INTO crawldb.site (domain) VALUES (%s) ON CONFLICT (domain) DO NOTHING RETURNING id;", (domain,))
                site_id_row = cur.fetchone()
                if site_id_row:
                    site_id = site_id_row[0]
                else:
                    cur.execute("SELECT id FROM crawldb.site WHERE domain = %s;", (domain,))
                    site_id = cur.fetchone()[0]

                # Insert page
                # page_type_code required? page_type_code 'HTML'
                cur.execute("""
                    INSERT INTO crawldb.page (site_id, page_type_code, url, html_content, http_status_code, accessed_time)
                    VALUES (%s, 'HTML', %s, %s, %s, NOW())
                    ON CONFLICT (url) DO UPDATE SET html_content = EXCLUDED.html_content;
                """, (site_id, url, html, resp.status_code))
                print(f"Inserted {url} successfully.")
        except Exception as e:
            print(f"Failed to process {url}: {e}")

if __name__ == "__main__":
    main()

