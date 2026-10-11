"""Server-rendered pages. Data comes from the JSON API; job.js renders and live-polls the sheet grid."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.db import get_db, jsonable, oid
from extract.scale import COMMON_LABELS

router = APIRouter(tags=["ui"])
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@router.get("/")
def index(request: Request):
    jobs = jsonable(list(get_db().jobs.find().sort("created_at", -1).limit(50)))
    return templates.TemplateResponse(request, "index.html", {"jobs": jobs})


@router.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str):
    job = get_db().jobs.find_one({"_id": oid(job_id)})
    if not job:
        raise HTTPException(404, "No such job.")
    return templates.TemplateResponse(
        request, "job.html", {"job": jsonable(job), "scale_labels": json.dumps(COMMON_LABELS)}
    )
