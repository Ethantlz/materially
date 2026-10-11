"""Orders: orders.math.lines_for -> $lookup price_book -> $merge into orders -> over-purchase guard.
Lane 2 owns this file; the signatures are the contract other lanes call."""


def build(job_id: str, condition_id: str, lf: float, height_ft: float) -> str:
    """Lines from orders.math.lines_for(condition, lf, height_ft), priced by an aggregation that $lookups
    price_book and $merges into orders. Return the order _id hex."""
    raise NotImplementedError


def guard(order_id: str) -> dict:
    """Compare each line's qty to computed_qty x (1 + waste); set and return guard {over, items_over}."""
    raise NotImplementedError
