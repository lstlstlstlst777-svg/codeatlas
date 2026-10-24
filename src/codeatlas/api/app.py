"""FastAPI 入口：lifespan 里装配 Runtime，退出时统一释放。

启动时会校验索引一致性（manifest + Qdrant 点数）：不一致直接拒绝启动。
让服务带着错位的索引上线，比启动失败糟糕得多——它只会产出"看起来有出处"的错误答案。
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from codeatlas import __version__
from codeatlas.api.models import HealthResponse
from codeatlas.api.routes import router
from codeatlas.api.runtime import Runtime
from codeatlas.config import load_config
from codeatlas.settings import Settings

# 同 runtime.project_root 的理由：容器里包在 site-packages，__file__ 推不出项目根
WEB_DIR = Path(os.environ.get("CODEATLAS_ROOT") or Path.cwd()) / "web"


def create_app(
    settings: Settings | None = None,
    *,
    use_cache: bool = True,
    checkpoint_db: str | None = None,
) -> FastAPI:
    # 必须走 load_config：裸 Settings() 会得到 refuse_tau=None，
    # 导致每个请求都在拒答门抛 "未标定"，而 yaml 里其实已经标好了。
    settings = settings or load_config()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = await Runtime.create(settings, use_cache=use_cache, checkpoint_db=checkpoint_db)
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.aclose()

    app = FastAPI(title="CodeAtlas", version=__version__, lifespan=lifespan)
    app.include_router(router)

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        runtime: Runtime | None = getattr(app.state, "runtime", None)
        if runtime is None:
            return HealthResponse(
                status="starting",
                version=__version__,
                mode=settings.provider_mode,
                index_fingerprint=settings.index_fingerprint(),
                chunks=0,
                bm25_docs=0,
                dense_points=0,
                llm=settings.llm_model,
                refuse_tau=settings.refuse_tau,
            )
        return HealthResponse(
            status="ok",
            version=__version__,
            mode=settings.provider_mode,
            **runtime.stats(),  # type: ignore[arg-type]
        )

    mount_web(app)
    return app


def mount_web(app: FastAPI, web_dir: Path | None = None) -> None:
    """挂前端静态文件。web 目录不存在时静默跳过（只跑 API 的场景不该因此崩）。"""
    directory = web_dir or WEB_DIR
    if not directory.is_dir():
        return

    app.mount("/static", StaticFiles(directory=str(directory)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(directory / "index.html")

    # 这些前缀属于 API：404 必须回 JSON，否则前端会拿到一整页 HTML 去 JSON.parse
    API_PREFIXES = ("/ask", "/code", "/healthz")

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Exception) -> JSONResponse | FileResponse:
        # API 路径返回 JSON，浏览器路径回落到首页（单页应用的路由由前端处理）
        if request.url.path.startswith(API_PREFIXES):
            detail = getattr(exc, "detail", "Not Found")
            return JSONResponse({"detail": detail}, status_code=404)
        return FileResponse(directory / "index.html")


def main() -> None:
    import uvicorn

    uvicorn.run("codeatlas.api.app:create_app", factory=True, host="127.0.0.1", port=8000)


app = create_app()
