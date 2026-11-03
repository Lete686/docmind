"""服务入口：挂载 /ingest、/ask、/memory 三个路由前缀与静态首页。"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .ask import router as ask_router
from .config import PowerContextNotConfiguredError
from .ingest import router as ingest_router
from .memory import ensure_preference_scope, router as memory_router

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
logger = logging.getLogger("docmind")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时预热偏好 scope。配置缺失或 PowerContext 还没起来都不该阻止启动，
    # 首次用到时 ensure_preference_scope() 会再试一次。
    try:
        await ensure_preference_scope()
    except Exception as exc:  # noqa: BLE001 - 预热失败只降级，不影响服务可用
        logger.warning("偏好 scope 预建失败，将在首次使用时重试：%s", exc)
    yield


app = FastAPI(
    title="docmind",
    version="0.1.0",
    description="文档摄取、问答与记忆服务，底层对接 PowerContext。",
    lifespan=lifespan,
)

app.include_router(ingest_router)
app.include_router(ask_router)
app.include_router(memory_router)


@app.exception_handler(PowerContextNotConfiguredError)
async def handle_unconfigured(request: Request, exc: PowerContextNotConfiguredError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"status": "error", "detail": str(exc)})


# 根路径挂载必须排在最后，否则 StaticFiles 会吃掉 /ingest、/ask、/memory。
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
