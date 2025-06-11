import os
import json
import sqlite3
import logging
import re
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
import base64
from io import BytesIO
from PIL import Image
import pytesseract
from openai import OpenAI
import numpy as np
import aiohttp

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("uvicorn.error")

load_dotenv()
API_KEY = os.getenv("API_KEY")

if not API_KEY:
    raise RuntimeError("API_KEY  not set in .env")

openai_client = OpenAI(api_key=API_KEY)
conn = sqlite3.connect("knowledge_base.db")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

SIMILARITY_THRESHOLD = 0.4
MAX_RESULTS = 50

class QueryRequest(BaseModel):
    question: str
    image: Optional[str] = None

class Link(BaseModel):
    url: str
    text: str

class QueryResponse(BaseModel):
    answer: str
    links: List[Link]

def extract_text_from_base64_image(image_base64: str) -> str:
    try:
        image_data = base64.b64decode(image_base64)
        image = Image.open(BytesIO(image_data))
        text = pytesseract.image_to_string(image).strip()
        logger.info(f"OCR extracted text: '{text[:80]}...' ({len(text)} chars)")
        return text
    except Exception as e:
        logger.warning(f"Failed to extract text from image: {e}")
        return ""

def get_embedding(text: str) -> List[float]:
    response = openai_client.embeddings.create(model="text-embedding-3-small", input=[text])
    return response.data[0].embedding

def cosine_similarity(a, b):
    a, b = np.array(a), np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

def retrieve_similar_chunks(question: str, top_k=MAX_RESULTS):
    logger.info(f"Embedding query text...")
    question_embedding = get_embedding(question)

    all_chunks = []
    for table in ["forum_chunks", "course_chunks"]:
        logger.info(f"Checking {table} for similar chunks...")
        cursor = conn.execute(f"SELECT url, text, embedding FROM {table}")
        for url, text, emb_json in cursor.fetchall():
            try:
                emb = json.loads(emb_json)
                if len(emb) != len(question_embedding):
                    continue
                similarity = cosine_similarity(question_embedding, emb)
                if similarity >= SIMILARITY_THRESHOLD:
                    all_chunks.append({
                        "source": table.replace("_chunks", ""),
                        "text": text,
                        "url": url,
                        "similarity": similarity,
                        "post_number": int(url.rstrip("/").split("/")[-1]) if url.rstrip("/").split("/")[-1].isdigit() else 0
                    })
            except Exception as e:
                logger.warning(f"Skipping row → {e}")

    logger.info(f"✅ Retrieved {len(all_chunks)} matching chunks.")
    return sorted(all_chunks, key=lambda x: (-x["similarity"], -x["post_number"]))[:top_k]

async def generate_llm_answer(question: str, chunks: List[dict], extracted_text: Optional[str] = None) -> str:
    context = "\n\n".join([
        f"{chunk['source'].capitalize()} (URL: {chunk['url']}): {chunk['text'][:1500]}"
        for chunk in chunks
    ])

    formatted_extracted_text = f"OCR-extracted Text:\n{extracted_text}\n\n" if extracted_text else ""

    prompt = f"""
You are not a conversational assistant. You are a compliance checker.

DO NOT answer based on general knowledge or what the user wants to hear.

ONLY answer based on the course materials provided below. Do NOT infer or inject anything. If the material does not clearly answer the question, reply with:

"I don't have enough information to answer this question."

---

{formatted_extracted_text}Context:
{context}

---

User Question:
{question}

Based ONLY on the context above, what does the course recommend?

Format your response like this:

1. [Factual answer only]

Sources:
1. URL: [exact URL], Text: [short quote from that page]
2. URL: [exact URL], Text: [short quote from that page]
"""

    headers = {"Authorization": API_KEY, "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant that provides accurate answers based only on the provided context. Always include sources in your response with exact URLs."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1
    }

    logger.info("Sending prompt to LLM...")
    async with aiohttp.ClientSession() as session:
        async with session.post("https://aipipe.org/openai/v1/chat/completions", headers=headers, json=payload) as resp:
            if resp.status != 200:
                logger.error(await resp.text())
                raise HTTPException(status_code=resp.status, detail=await resp.text())
            result = await resp.json()
            return result["choices"][0]["message"]["content"]

@app.post("/query", response_model=QueryResponse)
async def query_virtual_ta(req: QueryRequest, request: Request):
    logger.info(f"Incoming request from IP: {request.client.host}")
    logger.info(f"Request headers: {dict(request.headers)}")

    body_bytes = await request.body()
    try:
        body_json = json.loads(body_bytes.decode("utf-8"))
        if "image" in body_json:
            image_size = len(body_json["image"])
            body_json["image"] = f"<base64, {image_size} bytes>"
        logger.info(f"Request body: {json.dumps(body_json)}")
    except Exception as e:
        logger.warning(f"Failed to decode request body for logging: {e}")

    extracted_text = None
    if req.image:
        extracted_text = extract_text_from_base64_image(req.image)

    chunks = retrieve_similar_chunks(req.question)

    if not chunks:
        logger.warning("No relevant content found for query.")
        return QueryResponse(answer="I couldn't find relevant content.", links=[])

    llm_output = await generate_llm_answer(req.question, chunks, extracted_text)

    if "Sources:" in llm_output:
        answer_part, sources_part = llm_output.split("Sources:", 1)
    else:
        answer_part, sources_part = llm_output, ""

    links = []
    for match in re.finditer(r"URL:\s*(\S+),\s*Text:\s*(.*)", sources_part):
        url, text = match.groups()
        links.append(Link(url=url.strip(), text=text.strip()))

    return QueryResponse(answer=answer_part.strip(), links=links)

