"""Upload a drawing set; read back its sheets, conditions and rooms; re-parse a sheet picked by hand."""
import re
import shutil

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.db import get_db, jsonable, now, oid
from app.settings import get_settings
from extract.analyze import parse_blocks
from extract.ingest import ingest, read_sheet_page
from extract.partitions import partition_blocks
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


def _job_or_404(db, job_id: str) -> None:
    if not db.jobs.find_one({"_id": oid(job_id)}, {"_id": 1}):
        raise HTTPException(404, "No such job.")


@router.get("/jobs/{job_id}/sheets")
def job_sheets(job_id: str):
    db = get_db()
    _job_or_404(db, job_id)
    return jsonable(list(db.sheets.find({"job_id": job_id}).sort("page", 1)))


@router.get("/jobs/{job_id}/conditions")
def job_conditions(job_id: str):
    db = get_db()
    _job_or_404(db, job_id)
    return jsonable(list(db.conditions.find({"job_id": job_id}).sort([("type_tag", 1), ("_id", 1)])))


@router.get("/jobs/{job_id}/rooms")
def job_rooms(job_id: str, sheet: str | None = None):
    db = get_db()
    _job_or_404(db, job_id)
    query = {"job_id": job_id, **({"sheet": sheet} if sheet else {})}
    return jsonable(list(db.rooms.find(query).sort([("sheet", 1), ("number", 1)])))


@router.post("/sheets/{sheet_id}/parse")
def parse_picked_sheet(sheet_id: str, background: BackgroundTasks):
    """Checkpoint A fallback: parse partition types on a sheet a person picked. Conditions already parsed from
    this sheet are updated in place (same `_id`, so measurements keep them); new ones arrive at
    GET /api/jobs/{job_id}/conditions as each block is parsed."""
    db = get_db()
    sheet = db.sheets.find_one({"_id": oid(sheet_id)})
    if not sheet:
        raise HTTPException(404, "No such sheet.")
    job_id = sheet["job_id"]
    page = read_sheet_page(get_settings().data_dir / job_id / "source" / sheet["file"], sheet["file_page"])
    blocks = partition_blocks(page)
    if not blocks:
        raise HTTPException(422, "No partition type tags found on this sheet.")
    if sheet["kind"] != "partition_types":
        routing = {"method": "manual", "previous_kind": sheet["kind"], "previous": sheet.get("routing")}
        db.sheets.update_one({"_id": sheet["_id"]}, {"$set": {"kind": "partition_types", "routing": routing}})
    background.add_task(parse_blocks, db, job_id, sheet, blocks)
    return {"sheet_id": sheet_id, "tags": [b.tag for b in blocks]}


@router.post("/jobs/{job_id}/conditions/scan")
def scan_partitions(job_id: str, background: BackgroundTasks):
    """The conditions panel's "Scan partitions" button: parse every partition-type sheet of the project.
    Conditions arrive at GET /api/jobs/{job_id}/conditions as each block is parsed; a re-scan updates them in place."""
    db = get_db()
    _job_or_404(db, job_id)
    sheets = list(db.sheets.find({"job_id": job_id, "kind": "partition_types"}).sort("page", 1))
    if not sheets:
        raise HTTPException(422, "No partition-type sheet in this project. Open the partition sheet and use Parse this sheet.")
    found = []
    for sheet in sheets:
        page = read_sheet_page(get_settings().data_dir / job_id / "source" / sheet["file"], sheet["file_page"])
        blocks = partition_blocks(page)
        if blocks:
            background.add_task(parse_blocks, db, job_id, sheet, blocks)
            found.append({"sheet_id": str(sheet["_id"]), "sheet_no": sheet.get("sheet_no"), "tags": [b.tag for b in blocks]})
    if not found:
        raise HTTPException(422, "The partition sheets have no type tags this reader recognises.")
    return {"sheets": found, "tags": sum(len(f["tags"]) for f in found)}
