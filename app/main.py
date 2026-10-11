"""materially web app: `uv run uvicorn app.main:app --reload`."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import get_db
from app.settings import ROOT, get_settings
from contracts.setup_db import ensure_schema
from eval.api import router as eval_router
from extract.api import router as extract_router
from orders.api import router as orders_router
from pipeline.api import router as pipeline_router
from po.api import router as po_router
from procure.api import router as procure_router
from ui.api import router as ui_router

log = logging.getLogger("materially")


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        ensure_schema(get_db())
    except Exception as e:  # keep serving so /health can show what's wrong
        log.error("MongoDB setup failed: %s", e)
    yield


app = FastAPI(title="materially", lifespan=lifespan)
data_dir = get_settings().data_dir
data_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=ROOT / "ui" / "static"), name="static")
app.mount("/data", StaticFiles(directory=data_dir), name="data")
# One router per lane folder; each lane adds endpoints in its own <lane>/api.py.
for router in (extract_router, pipeline_router, ui_router, procure_router, po_router, orders_router, eval_router):
    app.include_router(router)


@app.get("/health")
def health():
    try:
        db = get_db()
        db.command("ping")
        return {"ok": True, "db": db.name}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
