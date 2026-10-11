"""Upload a drawing set and read back its sheets."""
import re
import shutil

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.db import get_db, jsonable, now, oid
from app.settings import get_settings
from extract.ingest import ingest
from extract.scale import parse_label

router = APIRouter(prefix="/api", tags=["extract"])


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "drawings.pdf"


@router.post("/jobs")
def create_job(background: BackgroundTasks, name: str = Form(...), files: list[UploadFile] = File(...)):
    pdfs = [f for f in files if (f.filename or "").lower().endswith(".pdf")]
    if not pdfs:
        raise HTTPException(400, "Upload at least one PDF.")
    db = get_db()
    job_id = str(db.jobs.insert_one({"name": name.strip() or "Untitled", "created_at": now(), "status": "uploading",
                                     "pages_total": 0, "pages_named": 0, "pages_rendered": 0}).inserted_id)
    src = get_settings().data_dir / job_id / "source"
    src.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in pdfs:
        path = src / _safe(f.filename)
        with path.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        paths.append(path)
    background.add_task(ingest, db, job_id, paths, get_settings().data_dir)
    return {"job_id": job_id}


@router.get("/jobs")
def list_jobs():
    return [jsonable(j) for j in get_db().jobs.find().sort("created_at", -1).limit(50)]


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    db = get_db()
    job = db.jobs.find_one({"_id": oid(job_id)})
    if not job:
        raise HTTPException(404, "No such job.")
    sheets = list(db.sheets.find({"job_id": job_id}).sort("page", 1))
    return {"job": jsonable(job), "sheets": jsonable(sheets)}


class ScaleIn(BaseModel):
    label: str


@router.patch("/sheets/{sheet_id}/scale")
def set_scale(sheet_id: str, body: ScaleIn):
    scale = parse_label(body.label)
    if scale is None:
        raise HTTPException(422, f"Can't read scale {body.label!r}.")
    doc = scale.as_doc("manual", 1.0)
    res = get_db().sheets.update_one({"_id": oid(sheet_id)}, {"$set": {"scale": doc}})
    if not res.matched_count:
        raise HTTPException(404, "No such sheet.")
    return doc
