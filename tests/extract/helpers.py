from extract.pdftext import Seg


def seg(text: str, x0: float, top: float, size: float = 10.0) -> Seg:
    """A segment with a Helvetica-ish width (0.6 em per character)."""
    return Seg(text, x0, top, x0 + 0.6 * size * len(text), top + size, size)


def forget_job(db, job_id: str) -> None:
    """Delete everything a test wrote for one job."""
    from app.db import oid

    db.jobs.delete_one({"_id": oid(job_id)})
    for name in ("sheets", "conditions", "rooms", "usage"):
        db[name].delete_many({"job_id": job_id})
    db.review_queue.delete_many({"doc.job_id": job_id})
