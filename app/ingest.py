"""文档摄取路由：pypdf 逐页解析 PDF，分块写入 PowerContext 记忆库。"""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pypdf import PdfReader
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .config import get_settings

router = APIRouter(prefix="/ingest", tags=["ingest"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = PROJECT_ROOT / "samples" / "manifest.json"

CHUNK_LIMIT_BYTES = 7000
REMEMBER_PATH = "/v1/memory/remember"
REMEMBER_KIND = "pdf-page"
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 2
BACKOFF_BASE_SECONDS = 1.0
PREVIEW_PAGES = 3
PREVIEW_CHARS = 120


class IngestRequest(BaseModel):
    source: str = Field(description="PDF 路径；相对路径按项目根目录解析，例如 samples/sample_doc.pdf")


def _resolve_source(source: str) -> Path:
    path = Path(source)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"找不到文件：{path}")
    if path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail=f"只接受 PDF，收到 {path.suffix or '无扩展名'}：{path}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _chunk_by_utf8_bytes(text: str, limit: int) -> list[str]:
    """按字符累加 UTF-8 字节数切块：整块不超 limit，且绝不切断多字节字符。"""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for char in text:
        char_size = len(char.encode("utf-8"))
        if current and size + char_size > limit:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += char_size
    if current:
        chunks.append("".join(current))
    return chunks


def _extract_pages(path: Path) -> list[tuple[int, str]]:
    """返回 [(页码从 1 开始, 去首尾空白的文本)]。空白文本在这里统一收敛，
    下游据此判定"空页"；解析失败转成 400。"""
    try:
        reader = PdfReader(str(path))
        pages = [(page_no, (page.extract_text() or "").strip()) for page_no, page in enumerate(reader.pages, start=1)]
    except Exception as exc:  # pypdf 对损坏文件抛的异常类型很杂，统一转成可读的 400
        raise HTTPException(status_code=400, detail=f"pypdf 无法解析 {path.name}：{type(exc).__name__}: {exc}") from exc
    return pages


def _build_chunks(document_id: str, pages: list[tuple[int, str]]) -> tuple[list[tuple[int, str]], list[int]]:
    """入参需为 _extract_pages 的输出（已 strip）；空串即视为空页，只计数不入库。"""
    chunks: list[tuple[int, str]] = []
    empty_pages: list[int] = []
    for page_no, text in pages:
        if not text:
            empty_pages.append(page_no)
            continue
        locator = f"[{document_id} p{page_no}]"
        # 定位符也计入预算，保证发出去的每一块整体 ≤ CHUNK_LIMIT_BYTES。
        budget = CHUNK_LIMIT_BYTES - len(locator.encode("utf-8"))
        for piece in _chunk_by_utf8_bytes(text, budget):
            chunks.append((page_no, locator + piece))
    return chunks, empty_pages


def _read_manifest() -> dict[str, object]:
    if not MANIFEST_PATH.is_file():
        return {"documents": {}}
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"{MANIFEST_PATH} 不是合法 JSON：{exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("documents"), dict):
        raise HTTPException(status_code=500, detail=f"{MANIFEST_PATH} 缺少 documents 对象，请检查或删掉重建。")
    return manifest


def _record_ingest(sha256: str, document_id: str, source: Path, pages: int, chunks: int, skipped_pages: int) -> None:
    manifest = _read_manifest()
    try:
        relative_source = source.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        relative_source = source.as_posix()
    manifest["documents"][sha256] = {
        "document_id": document_id,
        "source": relative_source,
        "pages": pages,
        "chunks": chunks,
        "skipped_pages": skipped_pages,
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _remember(client: httpx.AsyncClient, url: str, payload: dict[str, str], index: int, total: int) -> None:
    """单块写入：4xx 立即中止并回传上游原文，其余失败按 1s、2s 指数退避重试 2 次。"""
    label = f"第 {index}/{total} 块"
    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code < 400:
                return
            if 400 <= response.status_code < 500:
                raise HTTPException(
                    status_code=502,
                    detail=f"PowerContext 拒绝了{label}（上游 {response.status_code}），已中止：{response.text}",
                )
            last_error = f"HTTP {response.status_code}: {response.text}"
        if attempt < MAX_RETRIES:
            await asyncio.sleep(BACKOFF_BASE_SECONDS * 2**attempt)
    raise HTTPException(
        status_code=503,
        detail=f"{label} 重试 {MAX_RETRIES} 次后仍未写入成功：{last_error}",
    )


@router.post("")
async def ingest_document(request: IngestRequest) -> dict[str, object]:
    path = _resolve_source(request.source)
    document_id = path.stem
    sha256 = await run_in_threadpool(_sha256, path)

    if sha256 in _read_manifest()["documents"]:
        return {"status": "skipped", "document_id": document_id, "sha256": sha256}

    settings = get_settings()
    pages = await run_in_threadpool(_extract_pages, path)
    chunks, empty_pages = _build_chunks(document_id, pages)

    url = settings.powercontext_url + REMEMBER_PATH
    # trust_env=False：PowerContext 常部署在 localhost，而 Windows 系统代理会把发往
    # 127.0.0.1 的请求也接管掉（表现为空 body 的 502），这里强制直连。
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
        for index, (_, text) in enumerate(chunks, start=1):
            payload = {"scope_id": settings.scope_id, "kind": REMEMBER_KIND, "text": text}
            await _remember(client, url, payload, index, len(chunks))

    _record_ingest(
        sha256=sha256,
        document_id=document_id,
        source=path,
        pages=len(pages),
        chunks=len(chunks),
        skipped_pages=len(empty_pages),
    )
    return {
        "status": "ok",
        "document_id": document_id,
        "pages": len(pages),
        "chunks": len(chunks),
        "skipped_pages": len(empty_pages),
    }


@router.post("/preview")
async def preview_document(request: IngestRequest) -> dict[str, object]:
    """只解析不写库，返回前 3 页各自前 120 字符，供评审快速确认解析质量。"""
    path = _resolve_source(request.source)
    pages = await run_in_threadpool(_extract_pages, path)
    preview = [
        {
            "page": page_no,
            "text": text[:PREVIEW_CHARS],
            "empty": not text,
            "truncated": len(text) > PREVIEW_CHARS,
        }
        for page_no, text in pages[:PREVIEW_PAGES]
    ]
    return {"status": "ok", "document_id": path.stem, "pages": len(pages), "preview": preview}
