"""Independent internal endpoint for Word career documents (no Excel routes)."""
import os
from fastapi import FastAPI, HTTPException, Request
from .parser import MAX_BYTES, WordInputError
from .graph import build_graph

app = FastAPI(title="BWG Career Word Agent")
workflow = build_graph()


@app.post("/internal/word/extract")
async def extract_word(request: Request):
    token = os.getenv("CAREER_AGENT_INTERNAL_TOKEN")
    if not token or request.headers.get("X-Internal-Token") != token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        raise HTTPException(status_code=415, detail="Only DOCX is supported")
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Maximum DOCX size is 10 MB")
    try:
        state = await workflow.ainvoke({"content": raw})
    except WordInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Word analysis service failed") from exc
    return {"document_type": "career_word", "review_status": "needs_review", "blocks": state["parsed"]["blocks"], "extraction": state["extraction"]}
