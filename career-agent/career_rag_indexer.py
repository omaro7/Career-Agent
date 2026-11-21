"""BWG-Career-Agent Stage 03: conservative, project-scoped JSON -> Qdrant indexer.

Does NOT modify the original parser, PostgreSQL, PPTX generator, or web/API.
Requires: qdrant-client, langchain-ollama, langchain-text-splitters.

Usage:
  python career_rag_indexer.py preview --input /tmp/profile.json --profile-id TEST-001
  python career_rag_indexer.py index   --input /tmp/profile.json --profile-id TEST-001 --revision v1
  python career_rag_indexer.py search  --profile-id TEST-001 --query '금융 PL 경험'
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

class EmptyProjectsError(ValueError):
    """Recognized project array exists but has no project rows."""
    pass


COLLECTION = os.getenv("CAREER_RAG_COLLECTION", "career_profiles_v1")
EMBED_MODEL = os.getenv("CAREER_EMBED_MODEL", "embeddinggemma")
VECTOR_SIZE = 768  # Verified by user: Ollama embeddinggemma /api/embed
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://career-ollama:11434")
QDRANT_URL = os.getenv("QDRANT_URL", "http://career-qdrant:6333")

# Explicitly limited to project experience, not personal details.
PROJECT_CONTAINERS = ("projects", "careerProjects", "project_history", "projectHistory",
                      "project_experiences", "experiences", "careers", "프로젝트", "프로젝트경력")
WRAPPERS = ("profile", "data", "result", "parsed", "career")
FIELDS = {
    "project_id": ("project_id", "projectId", "career_id", "careerId", "careerProjectId", "id", "프로젝트ID"),
    "project_name": ("project_name", "projectName", "name", "title", "project", "프로젝트명", "사업명"),
    "client": ("client", "clientName", "customer", "customerName", "company", "client_name", "고객사", "발주처"),
    "role": ("role", "submittedRole", "position", "responsibility", "담당역할", "역할"),
    "period": ("period", "periodText", "duration", "projectPeriod", "참여기간", "기간"),
    "start_date": ("start_date", "startDate", "start", "시작일"),
    "end_date": ("end_date", "endDate", "end", "종료일"),
    "technologies": ("technologies", "technologiesText", "techStack", "skills", "tech_stack", "기술", "사용기술"),
    "description": ("description", "workDescription", "summary", "details", "tasks", "responsibilities",
                    "achievements", "work", "업무내용", "수행업무", "주요업무", "성과"),
}
LABELS = {
    "project_name": "프로젝트", "client": "고객사", "role": "담당 역할", "period": "참여기간",
    "start_date": "시작일", "end_date": "종료일", "technologies": "사용 기술", "description": "업무 및 성과",
}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
EMAIL = re.compile(r"(?<!\w)[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}(?!\w)")
KOREAN_PHONE = re.compile(r"(?<!\d)(?:\+?82[- .]?)?0?1[016789][-. ]?\d{3,4}[-. ]?\d{4}(?!\d)")
RRN = re.compile(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)")


@dataclass(frozen=True)
class Chunk:
    point_id: str
    payload: dict[str, Any]


def _first(obj: dict[str, Any], names: tuple[str, ...]):
    for name in names:
        val = obj.get(name)
        if val is not None and val != "" and val != []:
            return val
    return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, (tuple, list)):
        return ", ".join(s for x in value if (s := _as_text(x)))
    # Never stringify raw nested dictionaries: potentially personal/private fields.
    return ""


def redact(text: str) -> str:
    """Best-effort PII redaction; upstream data-governance review still required."""
    return RRN.sub("[주민번호 제거]", KOREAN_PHONE.sub("[전화번호 제거]", EMAIL.sub("[이메일 제거]", text)))


def extract_projects(parser_result: Any) -> tuple[list[dict[str, Any]], str]:
    """Strict adapter: only recognized project containers; unknown schema raises."""
    if isinstance(parser_result, list) and parser_result and all(isinstance(p, dict) for p in parser_result):
        return parser_result, "root"
    if not isinstance(parser_result, dict):
        raise ValueError("Parser 결과는 객체(dict) 또는 프로젝트 객체 목록(list)이어야 합니다.")
    queue = [(parser_result, "root")]
    while queue:
        item, path = queue.pop(0)
        for key in PROJECT_CONTAINERS:
            projects = item.get(key)
            if isinstance(projects, list):
                if not projects:
                    raise EmptyProjectsError(f"{path}.{key}가 비어 있어 색인하지 않습니다.")
                if not all(isinstance(p, dict) for p in projects):
                    raise ValueError(f"{path}.{key}는 프로젝트 객체 배열이어야 합니다.")
                return projects, f"{path}.{key}"
        for wrapper in WRAPPERS:
            nested = item.get(wrapper)
            if isinstance(nested, dict):
                queue.append((nested, f"{path}.{wrapper}"))
    raise ValueError(
        "프로젝트 배열을 찾지 못했습니다. 지원 필드: " + ", ".join(PROJECT_CONTAINERS)
        + "; 실제 Parser JSON 구조에 맞춰 PROJECT_CONTAINERS/FIELDS를 수정하세요. "
        + "최상위 키: " + ", ".join(map(str, parser_result.keys()))
    )


def _split(text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    """Whitespace/paragraph aware fallback splitter, no external import for preview/tests."""
    if chunk_size < 200 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size>=200, 0<=overlap<chunk_size 이어야 합니다.")
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    step = chunk_size - overlap
    for start in range(0, len(text), step):
        segment = text[start:start+chunk_size]
        if segment:
            chunks.append(segment)
        if start+chunk_size >= len(text):
            break
    return chunks


def prepare_chunks(profile_id: str, parser_result: Any, revision: str = "v1") -> tuple[list[Chunk], list[str]]:
    if not ID_PATTERN.fullmatch(profile_id):
        raise ValueError("profile_id는 1~128자의 영문/숫자/._:-만 허용합니다.")
    if not revision.strip():
        raise ValueError("revision은 빈 값일 수 없습니다.")
    projects, container_path = extract_projects(parser_result)
    chunks: list[Chunk] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    for index, project in enumerate(projects):
        raw = {name: _as_text(_first(project, keys)) for name, keys in FIELDS.items()}
        project_id = raw.pop("project_id")
        if not project_id:
            project_id = f"row-{index+1:04d}"
            warnings.append(f"프로젝트 #{index+1}: 고유 project_id 없음. 행 순서 기반 ID 사용(순서 변경 시 확인 필요)")
        if project_id in seen_ids:
            raise ValueError(f"project_id 중복: {project_id}")
        seen_ids.add(project_id)
        lines = [f"{LABELS[key]}: {value}" for key, value in raw.items() if value and key in LABELS]
        if not lines:
            raise ValueError(f"프로젝트 #{index+1}에 인식 가능한 경력 필드가 없습니다. 키: {list(project.keys())}")
        content = redact("\n".join(lines))
        for n, section in enumerate(_split(content)):
            pid = str(uuid5(NAMESPACE_URL, f"bwg-career-rag:{profile_id}:{project_id}:{n}"))
            payload = {
                "profile_id": profile_id, "project_id": project_id,
                "source": "python-parser", "source_path": f"{container_path}[{index}]",
                "section": "project", "chunk_index": n, "revision": revision,
                "project_name": redact(raw["project_name"]),
                "client": redact(raw["client"]), "role": redact(raw["role"]),
                "technologies": redact(raw["technologies"]),
                "content": section,
            }
            chunks.append(Chunk(pid, payload))
    return chunks, warnings


def make_client():
    from qdrant_client import QdrantClient
    api_key = os.getenv("QDRANT_API_KEY")
    return QdrantClient(url=QDRANT_URL, api_key=api_key if api_key else None, timeout=60)


def make_embeddings():
    from langchain_ollama import OllamaEmbeddings
    return OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_URL)


def verify_dimension(embeddings) -> None:
    vector = embeddings.embed_query("경력 검색 차원 검증")
    if len(vector) != VECTOR_SIZE:
        raise RuntimeError(f"임베딩 차원 불일치: expected={VECTOR_SIZE}, actual={len(vector)}")


def ensure_collection(client) -> None:
    from qdrant_client import models
    if client.collection_exists(COLLECTION):
        vectors = client.get_collection(COLLECTION).config.params.vectors
        if isinstance(vectors, dict):
            raise RuntimeError("동일 이름의 named-vector collection이 있습니다. 별도 collection 이름을 사용하세요.")
        if vectors.size != VECTOR_SIZE or vectors.distance != models.Distance.COSINE:
            raise RuntimeError(f"기존 Collection 설정 불일치: size={vectors.size}, distance={vectors.distance}")
        return
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=models.VectorParams(size=VECTOR_SIZE, distance=models.Distance.COSINE),
    )
    client.create_payload_index(
        collection_name=COLLECTION, field_name="profile_id",
        field_schema=models.PayloadSchemaType.KEYWORD, wait=True,
    )


def _profile_filter(profile_id: str):
    from qdrant_client import models
    return models.Filter(must=[models.FieldCondition(
        key="profile_id", match=models.MatchValue(value=profile_id))])


def _existing_point_ids(client, profile_id: str) -> set[str]:
    ids: set[str] = set()
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=COLLECTION, scroll_filter=_profile_filter(profile_id),
            limit=100, offset=offset, with_payload=False, with_vectors=False,
        )
        ids.update(str(p.id) for p in records)
        if offset is None:
            break
    return ids


def index_profile(profile_id: str, parser_result: Any, revision: str = "v1", *,
                  embeddings=None, client=None) -> dict[str, Any]:
    """Index full snapshot of ONE profile. Replaces stale points only after successful upsert.

    Qdrant ops are not one atomic transaction; rerun to recover from partial failures.
    """
    from qdrant_client import models
    chunks, warnings = prepare_chunks(profile_id, parser_result, revision)
    embeddings = embeddings or make_embeddings()
    client = client or make_client()
    verify_dimension(embeddings)
    ensure_collection(client)
    old_ids = _existing_point_ids(client, profile_id)
    points: list[Any] = []
    for i in range(0, len(chunks), 2):
        batch = chunks[i:i+2]  # CPU-friendly for local Ollama
        vectors = embeddings.embed_documents([p.payload["content"] for p in batch])
        if len(vectors) != len(batch) or any(len(v) != VECTOR_SIZE for v in vectors):
            raise RuntimeError("Ollama embedding 결과 수 또는 벡터 차원 오류")
        points.extend(models.PointStruct(id=p.point_id, vector=v, payload=p.payload)
                      for p, v in zip(batch, vectors))
    # All embeddings are ready before the first write.
    for i in range(0, len(points), 16):
        client.upsert(collection_name=COLLECTION, points=points[i:i+16], wait=True)
    new_ids = {p.point_id for p in chunks}
    stale_ids = old_ids - new_ids
    if stale_ids:
        client.delete(
            collection_name=COLLECTION,
            points_selector=models.PointIdsList(points=sorted(stale_ids)), wait=True,
        )
    return {
        "collection": COLLECTION, "profile_id": profile_id, "revision": revision,
        "projects": len({c.payload['project_id'] for c in chunks}),
        "chunks_upserted": len(chunks), "stale_chunks_removed": len(stale_ids),
        "warnings": warnings,
    }


def search_profile(profile_id: str, query: str, limit: int = 5) -> dict[str, Any]:
    if not ID_PATTERN.fullmatch(profile_id):
        raise ValueError("잘못된 profile_id 형식")
    if not query.strip():
        raise ValueError("검색어를 입력하세요")
    client, embeddings = make_client(), make_embeddings()
    if not client.collection_exists(COLLECTION):
        raise RuntimeError("Collection이 없습니다. 먼저 index 명령을 실행하세요.")
    verify_dimension(embeddings)
    result = client.query_points(
        collection_name=COLLECTION, query=embeddings.embed_query(query),
        query_filter=_profile_filter(profile_id), limit=limit,
        with_payload=True, with_vectors=False,
    )
    return {"profile_id": profile_id, "query": query, "hits": [
        {"score": round(float(hit.score), 5),
         "project_id": (hit.payload or {}).get("project_id"),
         "project_name": (hit.payload or {}).get("project_name"),
         "source_path": (hit.payload or {}).get("source_path"),
         "revision": (hit.payload or {}).get("revision"),
         "content": (hit.payload or {}).get("content")}
        for hit in result.points
    ]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="BWG Career RAG: parser JSON -> Qdrant (768D)")
    sub = ap.add_subparsers(dest="command", required=True)
    for action in ("preview", "index"):
        p = sub.add_parser(action)
        p.add_argument("--input", required=True, help="Parser output JSON file inside container")
        p.add_argument("--profile-id", required=True, help="Unique stable profile ID")
        p.add_argument("--revision", default="v1")
    p = sub.add_parser("search")
    p.add_argument("--profile-id", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=5)
    args = ap.parse_args(argv)
    try:
        if args.command in ("preview", "index"):
            raw = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
            if args.command == "preview":
                chunks, warnings = prepare_chunks(args.profile_id, raw, args.revision)
                result = {"mode": "preview_only_no_write", "profile_id": args.profile_id,
                          "projects": len({c.payload['project_id'] for c in chunks}),
                          "chunks": len(chunks), "warnings": warnings,
                          "sample": [c.payload for c in chunks[:2]]}
            else:
                result = index_profile(args.profile_id, raw, args.revision)
        else:
            if not (1 <= args.limit <= 20):
                raise ValueError("--limit은 1~20 사이여야 합니다.")
            result = search_profile(args.profile_id, args.query, args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
