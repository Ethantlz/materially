"""EWOs: a foreman's pin on a sheet -> room ($near on rooms) -> Jev picks the task -> run_task drafts it.
Lane 2 owns this file; the signatures are the contract other lanes call."""


def create(job_id: str, sheet: str, x: float, y: float, description: str) -> str:
    """Insert into ewos with room_id from $near on rooms of the same sheet; return the ewo _id hex."""
    raise NotImplementedError


def route(ewo_id: str) -> str | None:
    """app.jev.choice over the enabled model_policy tasks (price_extra, base_scope_check, backcharge).
    Not agreed -> review_queue. Agreed -> run_task(task, {...}, "ewo_drafts") -> insert into ewo_drafts.
    Return the draft _id hex, or None when it went to review_queue."""
    raise NotImplementedError
