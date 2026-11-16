import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import auth, batches, catalogs, evidence, reports, spacetime
from .config import settings
from .db import init_db

app = FastAPI(
    title="收运批次重量一致性与计费差异数据服务（含时空证据状态）",
    version="1.1.0",
    description=(
        "以来源证据库、批次关系册、计量裁定器、费率编译器与差异发布器，"
        "发布可审计的收运重量、计费依据与差异范围。本服务不派车、不卸料、不审批付款。"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(evidence.router)
app.include_router(batches.router)
app.include_router(catalogs.router)
app.include_router(reports.router)
app.include_router(spacetime.router)


@app.on_event("startup")
def startup():
    init_db()
    if settings.SEED_DEMO:
        import seed  # backend/seed.py（从 backend/ 目录启动时可导入）

        seed.seed()


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 生产模式：若前端构建产物存在，由后端直接托管（本地单端口验证用）
_dist = os.path.abspath(settings.FRONTEND_DIST)
if os.path.isdir(_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(_dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return {"detail": "Not Found"}
        target = os.path.join(_dist, full_path)
        if full_path and os.path.isfile(target):
            return FileResponse(target)
        return FileResponse(os.path.join(_dist, "index.html"))
