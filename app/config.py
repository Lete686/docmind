"""从环境变量读取配置。凭据一律不写进代码库。"""

import os
from dataclasses import dataclass
from functools import lru_cache

POWERCONTEXT_URL_ENV = "POWERCONTEXT_URL"
POWERCONTEXT_SCOPE_ENV = "POWERCONTEXT_SCOPE"
POWERCONTEXT_MEMORY_SCOPE_ENV = "POWERCONTEXT_MEMORY_SCOPE"

_SETUP_STEPS = (
    "  1) cp .env.example .env 并填入真实值（scripts/run.sh 会自动加载 .env）\n"
    "  2) 或 export POWERCONTEXT_URL=http://127.0.0.1:8787 POWERCONTEXT_SCOPE=<你的 scope>\n"
    "  3) 重新运行 scripts/run.sh"
)


class PowerContextNotConfiguredError(RuntimeError):
    """PowerContext 地址或 scope 缺失、不合法时抛出。"""


@dataclass(frozen=True)
class Settings:
    powercontext_url: str
    scope_id: str
    memory_scope: str | None = None


def load_settings() -> Settings:
    url = os.getenv(POWERCONTEXT_URL_ENV, "").strip().rstrip("/")
    scope = os.getenv(POWERCONTEXT_SCOPE_ENV, "").strip()

    missing = [name for name, value in ((POWERCONTEXT_URL_ENV, url), (POWERCONTEXT_SCOPE_ENV, scope)) if not value]
    if missing:
        raise PowerContextNotConfiguredError(
            f"未设置环境变量 {', '.join(missing)}，无法访问 PowerContext。\n{_SETUP_STEPS}"
        )
    if not url.startswith(("http://", "https://")):
        raise PowerContextNotConfiguredError(
            f"{POWERCONTEXT_URL_ENV}={url!r} 不是一个 http/https 地址。\n{_SETUP_STEPS}"
        )
    memory_scope = os.getenv(POWERCONTEXT_MEMORY_SCOPE_ENV, "").strip() or None
    return Settings(powercontext_url=url, scope_id=scope, memory_scope=memory_scope)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """业务代码用这个入口拿配置；未配置时抛清晰错误，而不是静默用默认值。"""
    return load_settings()
