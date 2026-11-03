"""记忆路由：偏好 scope 自动创建（幂等）+ 用户偏好的读写。"""

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from .config import get_settings

router = APIRouter(prefix="/memory", tags=["memory"])

SCOPES_PATH = "/v1/scopes"
REMEMBER_PATH = "/v1/memory/remember"
ENTRIES_LIST_PATH = "/v1/memory/entries/list"
PREFERENCE_KIND = "user-preference"
REQUEST_TIMEOUT_SECONDS = 30.0
TEXT_MAX_CHARS = 2000
SCOPE_PAYLOAD = {
    "title": "DocMind Preferences",
    "summary": "User preferences for DocMind",
    "idempotency_key": "docmind-pref-scope-v1",
}

# 模块级缓存：配了 POWERCONTEXT_MEMORY_SCOPE 就用它，否则向 PowerContext 申请。
# 幂等键固定，重复启动拿回的是同一个 scope，不会重复创建。
_preference_scope_id: str | None = None


class MemoryWriteRequest(BaseModel):
    text: str = Field(max_length=TEXT_MAX_CHARS, description=f"要长期保留的用户偏好，1-{TEXT_MAX_CHARS} 字符")

    @field_validator("text")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text 不能是空白字符")
        return value


async def _post_json(url: str, body: dict, label: str) -> object:
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
            response = await client.post(url, json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"连不上 PowerContext（{url}）：{type(exc).__name__}: {exc}。请检查 POWERCONTEXT_URL 是否指向已启动的服务。",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"PowerContext {label}调用失败（上游 {response.status_code}）：{response.text}")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"PowerContext {label}返回的不是合法 JSON：{response.text[:200]}") from exc


async def ensure_preference_scope() -> str:
    """拿到偏好 scope_id；已缓存或已配置则不发请求。"""
    global _preference_scope_id
    settings = get_settings()
    if _preference_scope_id:
        return _preference_scope_id
    if settings.memory_scope:
        _preference_scope_id = settings.memory_scope
        return _preference_scope_id

    payload = await _post_json(settings.powercontext_url + SCOPES_PATH, SCOPE_PAYLOAD, "偏好 scope 创建")
    scope_id = payload.get("scope_id") if isinstance(payload, dict) else None
    if not scope_id:
        raise HTTPException(status_code=502, detail=f"PowerContext 创建偏好 scope 的响应缺少 scope_id：{str(payload)[:200]}")
    _preference_scope_id = str(scope_id)
    return _preference_scope_id


async def fetch_preferences() -> list[dict]:
    """只保留 kind == user-preference 的条目。"""
    scope_id = await ensure_preference_scope()
    payload = await _post_json(get_settings().powercontext_url + ENTRIES_LIST_PATH, {"scope_id": scope_id}, "偏好列表读取")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail=f"PowerContext 偏好列表响应结构异常：{str(payload)[:200]}")
    entries = payload.get("entries") or []
    if not isinstance(entries, list):
        raise HTTPException(status_code=502, detail=f"PowerContext 偏好列表的 entries 不是数组：{str(entries)[:200]}")

    preferences: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("kind") != PREFERENCE_KIND:
            continue
        artifact = entry.get("artifact") or {}
        preferences.append(
            {
                "text": entry.get("text") or "",
                "artifact_id": artifact.get("artifact_id"),
                "revision": artifact.get("revision"),
            }
        )
    return preferences


@router.get("")
async def read_memory() -> dict[str, object]:
    return {"status": "ok", "preferences": await fetch_preferences()}


@router.post("")
async def write_memory(request: MemoryWriteRequest) -> dict[str, object]:
    scope_id = await ensure_preference_scope()
    await _post_json(
        get_settings().powercontext_url + REMEMBER_PATH,
        {"scope_id": scope_id, "kind": PREFERENCE_KIND, "text": request.text},
        "偏好写入",
    )
    return {"status": "ok"}
