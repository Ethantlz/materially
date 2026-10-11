"""Projects and their alternates (Base bid, Alternate 1, ...)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from projects import alternates as alt

router = APIRouter(prefix="/api", tags=["projects"])


class AlternateIn(BaseModel):
    name: str
    description: str | None = None


@router.get("/projects")
def projects():
    return alt.project_rows()


@router.get("/jobs/{job_id}/alternates")
def list_alternates(job_id: str):
    return alt.list_alternates(job_id)


@router.post("/jobs/{job_id}/alternates")
def create_alternate(job_id: str, body: AlternateIn):
    try:
        return {"id": alt.create_alternate(job_id, body.name, body.description)}
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.patch("/alternates/{alt_id}")
def rename_alternate(alt_id: str, body: AlternateIn):
    try:
        alt.rename_alternate(alt_id, body.name)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"id": alt_id}


@router.delete("/alternates/{alt_id}")
def delete_alternate(alt_id: str):
    try:
        alt.delete_alternate(alt_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except alt.InUse as e:
        raise HTTPException(409, str(e))
    return {"deleted": alt_id}
