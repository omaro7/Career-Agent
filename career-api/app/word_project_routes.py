"""Read-only project proposals from the already reviewed Word text blocks.

Only changes happen when the user subsequently saves the structured profile
through the existing PUT /word-imports/{id}/structured API.
"""
from __future__ import annotations

import uuid
from fastapi import APIRouter, Response

from .word_imports import _load
from .word_project_extractor import extract_projects

router = APIRouter(prefix='/api/v1/career/word-imports', tags=['word-project-extraction'])


@router.get('/{import_id}/projects/extract')
def extract_word_projects(import_id: uuid.UUID, response: Response):
    """Rule-based extraction only; does not invoke Ollama or write to DB."""
    row = _load(import_id)  # Includes tenant isolation check from existing API.
    response.headers['Cache-Control'] = 'no-store'
    return extract_projects(row['reviewed'] or {})
