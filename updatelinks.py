import sqlite3
import json
import os
from link import expand_discourse_link

DB_PATH = "knowledge_base.db"
URL_CACHE_PATH = "url_cache.json"

# Load cache if available
url_cache = {}
if os.path.exists(URL_CACHE_PATH):
    with open(URL_CACHE_PATH, "r") as f:
        url_cache = json.load(f)

def save_url_cache():
    with open(URL_CACHE_PATH, "w") as f:
        json.dump(url_cache, f, indent=2)

def fix_forum_urls():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT chunk_id, url FROM forum_chunks")
    rows = cursor.fetchall()

    for chunk_id, old_url in rows:
        try:
            # Skip if already a full URL (has a dash/slug)
            if "-" in old_url.split("/t/")[1]:
                continue

            # Extract topic_id and post_number
            parts = old_url.strip("/").split("/")
            topic_id = parts[-2]
            post_number = parts[-1]
            topic_base_url = f"https://discourse.onlinedegree.iitm.ac.in/t/{topic_id}/1"

            # Expand only once per topic
            if topic_base_url in url_cache:
                expanded_base = url_cache[topic_base_url]
            else:
                expanded_full_url = expand_discourse_link(topic_base_url)
                expanded_base = expanded_full_url.rsplit("/", 1)[0]
                url_cache[topic_base_url] = expanded_base

            # Build full correct URL
            new_url = f"{expanded_base}/{post_number}"

            # Update DB if changed
            if new_url != old_url:
                cursor.execute("UPDATE forum_chunks SET url = ? WHERE chunk_id = ?", (new_url, chunk_id))
                print(f"✅ Updated: {old_url} → {new_url}")

        except Exception as e:
            print(f"❌ Failed to fix {old_url}: {e}")

    conn.commit()
    conn.close()
    save_url_cache()
    print("🔁 All forum URLs checked and updated.")

if __name__ == "__main__":
    fix_forum_urls()

