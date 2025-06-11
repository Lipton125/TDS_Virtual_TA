import os
import json
import sqlite3
import uuid
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from bs4 import BeautifulSoup
import logging

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# === Config ===
FORUM_DIR = "downloaded_threads"
COURSE_DIR = "markdown_files"
DB_PATH = "knowledge_base.db"
CHUNK_SIZE = 750
CHUNK_OVERLAP = 70

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    chunks, start = [], 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks

def embed(texts):
    response = client.embeddings.create(model="text-embedding-3-small", input=texts)
    return [item.embedding for item in response.data]

def create_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS forum_chunks")
    cur.execute("DROP TABLE IF EXISTS course_chunks")
    cur.execute('''
        CREATE TABLE forum_chunks (
            chunk_id TEXT PRIMARY KEY,
            post_id INTEGER,
            post_number INTEGER,
            topic_id INTEGER,
            topic_title TEXT,
            author TEXT,
            url TEXT,
            text TEXT,
            embedding BLOB
        )
    ''')
    cur.execute('''
        CREATE TABLE course_chunks (
            chunk_id TEXT PRIMARY KEY,
            source_file TEXT,
            section_title TEXT,
            url TEXT,
            text TEXT,
            embedding BLOB
        )
    ''')
    conn.commit()
    return conn

def process_forum_json(filepath, conn):
    logger.info(f"📁 Processing forum JSON file: {filepath}")
    with open(filepath, 'r', encoding='utf-8') as f:
        posts = json.load(f)

    count = 0
    for post in posts:
        chunks = chunk_text(post["content"])
        if not chunks:
            continue
        embeddings = embed(chunks)
        for chunk, emb in zip(chunks, embeddings):
            conn.execute(
                '''INSERT INTO forum_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    str(uuid.uuid4()),
                    post["post_id"],
                    post["post_number"],
                    post["topic_id"],
                    post["topic_title"],
                    post["author"],
                    f"https://discourse.onlinedegree.iitm.ac.in/t/{post['topic_id']}/{post['post_number']}",
                    chunk,
                    json.dumps(emb)
                )
            )
            count += 1
    logger.info(f"✅ Inserted {count} chunks from {filepath}")

def process_course_md(filepath, conn):
    logger.info(f"📁 Processing course file: {filepath}")
    content = Path(filepath).read_text(encoding='utf-8')
    lines = content.splitlines()

    url = None
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip().startswith("original_url:"):
                url = lines[i].split(":", 1)[1].strip().strip('"')
            elif lines[i].strip() == "---":
                lines = lines[i+1:]
                break

    section_title = ""
    buffer = ""
    count = 0
    for line in lines:
        if line.strip().startswith("#"):
            if buffer.strip():
                chunks = chunk_text(buffer)
                embeddings = embed(chunks)
                for chunk, emb in zip(chunks, embeddings):
                    conn.execute(
                        '''INSERT INTO course_chunks VALUES (?, ?, ?, ?, ?, ?)''',
                        (
                            str(uuid.uuid4()),
                            os.path.basename(filepath),
                            section_title,
                            url,
                            chunk,
                            json.dumps(emb)
                        )
                    )
                    count += 1
                buffer = ""
            section_title = line.strip("# ").strip()
        else:
            buffer += line + "\n"

    if buffer.strip():
        chunks = chunk_text(buffer)
        embeddings = embed(chunks)
        for chunk, emb in zip(chunks, embeddings):
            conn.execute(
                '''INSERT INTO course_chunks VALUES (?, ?, ?, ?, ?, ?)''',
                (
                    str(uuid.uuid4()),
                    os.path.basename(filepath),
                    section_title,
                    url,
                    chunk,
                    json.dumps(emb)
                )
            )
            count += 1

    logger.info(f"✅ Inserted {count} chunks from {filepath}")

def main():
    conn = create_db()

    logger.info("🔎 Processing forum JSON files...")
    for file in os.listdir(FORUM_DIR):
        if file.endswith(".json"):
            process_forum_json(os.path.join(FORUM_DIR, file), conn)

    logger.info("🔎 Processing course markdown files...")
    for file in os.listdir(COURSE_DIR):
        if file.endswith(".md"):
            process_course_md(os.path.join(COURSE_DIR, file), conn)

    conn.commit()
    conn.close()
    logger.info(f"🎉 Knowledge base rebuilt and stored in {DB_PATH}")

if __name__ == "__main__":
    main()

