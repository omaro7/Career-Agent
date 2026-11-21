"""Stage 02 agent boot scaffold; replace with migrated worker logic."""
import os
from fastapi import FastAPI, HTTPException

app = FastAPI(title="BWG Career Agent", version="stage02-scaffold")

@app.get("/health")
def health():
    return {"status": "ok", "service": "career-agent", "mode": "scaffold"}

@app.get("/config")
def config():
    return {"ollama_url": os.getenv("OLLAMA_BASE_URL"), "model": os.getenv("OLLAMA_MODEL"), "qdrant_url": os.getenv("QDRANT_URL")}

@app.post("/analyze")
def analyze():
    raise HTTPException(status_code=501, detail="Legacy worker/Agent processing is not migrated yet")
