"""问答路由：先取用户偏好扩展查询，再向 PowerContext 全文检索，用命中原文片段抽取式作答。"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .memory import fetch_preferences

router = APIRouter(prefix="/ask", tags=["ask"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUERY_LOG_PATH = PROJECT_ROOT / "samples" / "queries.log"

SEARCH_PATH = "/v1/memory/search"
SEARCH_MODE = "fts"
REQUEST_TIMEOUT_SECONDS = 30.0
SNIPPET_CHARS = 300
ANSWER_SNIPPETS = 3
PREFERENCE_EXPANSION = 2
ANSWER_HEADER = "以下为命中原文片段（抽样式，非生成式）："
NO_HIT_ANSWER = "未在已入库文档中找到相关内容"
MEMORY_KIND = "pdf-page"
LOCATOR_RE = re.compile(r"^\[(?P<doc>[^\]]+)\s+p(?P<page>\d+)\]\s*")


class AskRequest(BaseModel):
    question: str = Field(description="自然语言问题")
    top_k: int = Field(default=5, ge=1, le=50, description="召回的文档片段数量")


def _limit_for(top_k: int) -> int:
    """上游按 top_k 的 3 倍召回以保证重排有余量，返回给前端的再截到 top_k。"""
    return min(max(top_k * 3, top_k), 50)


def _collect_hits(payload: dict) -> list[dict]:
    """只留 kind=pdf-page 且定位符可解析的命中；同 (doc,page) 保最高分；结果按分数降序。"""
    best: dict[tuple[str, int], dict] = {}
    for hit in payload.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        memory = hit.get("memory") or {}
        if memory.get("kind") != MEMORY_KIND:
            continue
        text = memory.get("text") or ""
        match = LOCATOR_RE.match(text)
        if not match:
            continue
        artifact = memory.get("artifact") or {}
        citation = {
            "document_id": match.group("doc"),
            "page": int(match.group("page")),
            "score": float(hit.get("score") or 0.0),
            "artifact_id": artifact.get("artifact_id"),
            "revision": artifact.get("revision"),
            "snippet": text[match.end() :][:SNIPPET_CHARS],
        }
        key = (citation["document_id"], citation["page"])
        if key not in best or citation["score"] > best[key]["score"]:
            best[key] = citation
    return sorted(best.values(), key=lambda item: item["score"], reverse=True)


async def _search(settings: Settings, query: str, limit: int) -> dict:
    body = {"scope_id": settings.scope_id, "query": query, "limit": limit, "mode": SEARCH_MODE}
    url = settings.powercontext_url + SEARCH_PATH
    # trust_env=False：本机系统代理会劫持发往 127.0.0.1 的请求，这里强制直连。
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
            response = await client.post(url, json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"连不上 PowerContext（{url}）：{type(exc).__name__}: {exc}。请检查 POWERCONTEXT_URL 是否指向已启动的服务。",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"PowerContext 检索失败（上游 {response.status_code}）：{response.text}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"PowerContext 返回的不是合法 JSON：{response.text[:200]}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail=f"PowerContext 响应结构异常，顶层不是对象：{str(payload)[:200]}")
    return payload


def _log_query(
    question: str,
    expanded_query: str,
    applied: list[str],
    used_query: str,
    citations: list[dict],
    preferences_error: str | None,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": question,
        "hits": [[item["document_id"], item["page"]] for item in citations],
        "expanded_query": expanded_query,
        "applied_preferences": applied,
        "used_query": used_query,
        "fallback_used": bool(applied) and used_query != expanded_query,
    }
    if preferences_error:
        entry["preferences_error"] = preferences_error
    QUERY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with QUERY_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


@router.post("")
async def ask_question(request: AskRequest) -> dict[str, object]:
    settings = get_settings()
    limit = _limit_for(request.top_k)

    # 偏好只是增强项：读不到就退化成"无偏好"，不该让整条问答挂掉。
    preferences: list[dict] = []
    preferences_error: str | None = None
    try:
        preferences = await fetch_preferences()
    except HTTPException as exc:
        preferences_error = f"{exc.status_code}: {exc.detail}"

    applied = [item["text"].strip() for item in preferences[:PREFERENCE_EXPANSION] if item["text"].strip()]
    expanded_query = " ".join([request.question, *applied]) if applied else request.question

    payload = await _search(settings, expanded_query, limit)
    citations = _collect_hits(payload)
    used_query = expanded_query
    if not citations and applied:
        # 扩展查询反而把召回带偏时，退回原 query 再检一次。
        payload = await _search(settings, request.question, limit)
        citations = _collect_hits(payload)
        used_query = request.question

    citations = citations[: request.top_k]
    _log_query(request.question, expanded_query, applied, used_query, citations, preferences_error)

    common: dict[str, object] = {
        "mode": payload.get("mode"),
        "original_query": request.question,
        "expanded_query": expanded_query,
        "applied_preferences": applied,
    }
    if not citations:
        return {"status": "no_hit", "answer": NO_HIT_ANSWER, "citations": [], **common}

    answer = ANSWER_HEADER + "\n" + "\n---\n".join(item["snippet"] for item in citations[:ANSWER_SNIPPETS])
    return {"status": "ok", "answer": answer, "citations": citations, **common}
