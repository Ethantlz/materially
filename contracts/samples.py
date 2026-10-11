"""One valid sample doc per collection, all on one job, so UI and tests can work before other lanes merge.

Case A1 is the build spec's hand-worked case: 100 LF, 10 ft, 3-5/8" 20 ga at 16", one layer 5/8" Type X
each side. Quantities in the sample order are illustrative; orders.math owns the real ones.
References are _id hex strings (contracts/schemas.py conventions).
"""
from datetime import datetime, timezone

from bson import ObjectId


def _oid(suffix: str) -> ObjectId:
    return ObjectId(suffix.rjust(24, "0"))


AT = datetime(2026, 10, 11, 16, 0, tzinfo=timezone.utc)
JOB_ID = _oid("a1")
SHEET_PT_ID = _oid("b1")  # partition-types sheet
SHEET_FP_ID = _oid("b2")  # floor plan
CONDITION_ID = _oid("c1")
ROOM_ID = _oid("d1")
EWO_ID = _oid("e1")
DRAFT_ID = _oid("e2")
ORDER_ID = _oid("f1")
PRODUCT_ID = _oid("f2")
ALT_ID = _oid("a2")

JEV_ROUTING = {
    "choice": "partition_types",
    "agreed": True,
    "probabilities": {"partition_types": 0.93, "details": 0.05, "other": 0.02},
    "model": "typesafe/jev-1.13",
    "ms": 840.0,
    "usage": {"input_tokens": 612, "output_tokens": 40, "cost_usd": 0.0004},
}

SAMPLES: dict[str, dict] = {
    "jobs": {"_id": JOB_ID, "name": "Sample Clinic Fit-Out", "created_at": AT, "status": "ready"},
    "sheets": {
        "_id": SHEET_PT_ID,
        "job_id": str(JOB_ID),
        "page": 4,
        "file": "sample-set.pdf",
        "file_page": 4,
        "sheet_no": "A-601",
        "title": "PARTITION TYPES",
        "name_source": "title_block",
        "kind": "partition_types",
        "routing": JEV_ROUTING,
        "size_pt": [3024.0, 2160.0],
        "scale": {"label": "NTS", "pt_per_ft": None, "source": "title_block", "confidence": 0.9},
        "image_path": f"/data/{JOB_ID}/pages/0004.png",
        "created_at": AT,
    },
    "conditions": {
        "_id": CONDITION_ID,
        "job_id": str(JOB_ID),
        "type_tag": "A1",
        "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16},
        "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}],
        "sides": 2,
        "insulation": '3-1/2" sound attenuation batts',
        "fire_rating": 1,
        "ul_assembly": "U419",
        "stc": 50,
        "height_ft": 10,
        "source": {"sheet": str(SHEET_PT_ID), "page": 4, "bbox": [612.0, 140.5, 790.2, 260.0]},
        "routing": {"task": "parse_partition", "model": "deepseek-ai/DeepSeek-V4.1-Flash", "escalated": False},
        "materials": [
            {"key": "stud", "role": "stud", "desc": '3-5/8" 20 ga metal studs @ 16" o.c.', "size_in": 3.625, "gauge": 20, "spacing_in": 16, "per": "lf", "source": "drawing"},
            {"key": "track", "role": "track", "desc": '3-5/8" 20 ga track, top and bottom', "size_in": 3.625, "gauge": 20, "per": "lf", "source": "derived"},
            {"key": "board_0.625_type_x", "role": "board", "desc": '5/8" Type X gypsum board, 1 layer each side', "thickness_in": 0.625, "board_type": "type_x", "sides": 2, "per": "sf", "source": "drawing"},
            {"key": "insulation", "role": "insulation", "desc": '3-1/2" sound attenuation batts', "size_in": 3.625, "per": "sf", "source": "drawing"},
        ],
    },
    "rooms": {
        "_id": ROOM_ID,
        "job_id": str(JOB_ID),
        "sheet": str(SHEET_FP_ID),
        "name": "EXAM",
        "number": "104",
        "label_pt": [1312.5, 884.0],
    },
    "ewos": {
        "_id": EWO_ID,
        "job_id": str(JOB_ID),
        "sheet": str(SHEET_FP_ID),
        "pin": [1290.0, 902.0],
        "room_id": str(ROOM_ID),
        "description": "Owner added a 4 ft wall to close in the alcove, full height, same as A1.",
        "submitted_by": "foreman, synthetic",
        "status": "drafted",
        "created_at": AT,
    },
    "ewo_drafts": {
        "_id": DRAFT_ID,
        "ewo_id": str(EWO_ID),
        "task": "price_extra",
        "model": "deepseek-ai/DeepSeek-V4.1-Flash",
        "line_items": [
            {"desc": '3-5/8" 20 ga studs, 10 ft', "qty": 5, "unit": "ea", "unit_cost": 10.15},
            {"desc": '5/8" Type X board, 4x10', "qty": 3, "unit": "ea", "unit_cost": 21.6},
            {"desc": "Framing and hanging labor", "qty": 6, "unit": "hr", "unit_cost": 85.0},
        ],
        "total": 625.55,
        "explanation": "New 4 ft A1 wall at the exam alcove, not on the bid drawings; priced as an extra.",
        "routing": {
            **JEV_ROUTING,
            "choice": "price_extra",
            "probabilities": {"price_extra": 0.9, "base_scope_check": 0.08, "backcharge": 0.02},
        },
        "escalated": False,
    },
    "orders": {
        "_id": ORDER_ID,
        "job_id": str(JOB_ID),
        "condition_id": str(CONDITION_ID),
        "lf": 100,
        "height_ft": 10,
        "lines": [
            {"item": "stud_3.625_20ga_10ft", "qty": 80, "unit": "ea", "unit_cost": 10.15, "computed_qty": 76, "waste": 0.05},
            {"item": "track_3.625_20ga_10ft", "qty": 21, "unit": "ea", "unit_cost": 10.15, "computed_qty": 20, "waste": 0.05},
            {"item": "board_0.625_type_x_4x10", "qty": 55, "unit": "ea", "unit_cost": 21.6, "computed_qty": 50, "waste": 0.1},
        ],
        "total": 2213.15,
        "guard": {"over": False, "items_over": []},
        "supplier_email": None,
        "sent_at": None,
    },
    "product_data": {
        "_id": PRODUCT_ID,
        "query_hash": "sample-5f8-type-x-board",
        "condition_id": str(CONDITION_ID),
        "component": "board",
        "url": "https://example.com/5-8-type-x-data-sheet.pdf",
        "title": '5/8" Type X Gypsum Board - Product Data',
        "attrs": {"thickness_in": 0.625, "board_type": "type_x", "ul": ["U419"], "fire_rated": True},
        "spec_check": {"thickness_in": 0.03, "board_type": 0.05},
        "domains": ["sample-supply.example.com"],
        "checked": True,
        "fetched_at": AT,
    },
    "product_sources": {"url": "https://sample-supply.example.com", "domain": "sample-supply.example.com", "name": "Sample Supply Co", "created_at": AT},
    "price_book": {"item": "board_0.625_type_x_4x10", "unit": "ea", "unit_cost": 21.6, "supplier": "Sample Supply Co"},
    "model_policy": {
        "task": "parse_partition",
        "tier": "quick",
        "model": "deepseek-ai/DeepSeek-V4.1-Flash",
        "retry_model": "claude-sonnet-5-5",
        "enabled": True,
    },
    "usage": {
        "job_id": str(JOB_ID),
        "task": "parse_partition",
        "model": "deepseek-ai/DeepSeek-V4.1-Flash",
        "input_tokens": 1450,
        "output_tokens": 210,
        "cost_usd": 0.00023,
        "ms": 1830.0,
        "at": AT,
        "ok": True,
    },
    "measurements": {
        "job_id": str(JOB_ID),
        "sheet": str(SHEET_FP_ID),
        "condition_id": str(CONDITION_ID),
        "kind": "linear",
        "points": [[400.0, 500.0], [1264.0, 500.0]],  # 864 pt at 1/8" = 1'-0" (9 pt/ft) -> 96 LF
        "quantity": 96.0,
        "unit": "lf",
        "pt_per_ft": 9.0,
        "height_ft": 10.0,
        "label": None,
        "created_at": AT,
    },
    "review_queue": {
        "source": "parse_partition",
        "doc": {"type_tag": "B4", "studs": {"size_in": 3.625, "gauge": 19, "spacing_in": 16}},
        "reason": "studs.gauge: 19 not one of [12, 14, 16, 18, 20, 22, 25, 30]",
        "at": AT,
    },
    "alternates": {"_id": ALT_ID, "job_id": str(JOB_ID), "name": "Base bid", "kind": "base", "description": None, "order": 0, "created_at": AT},
}

# A parse the validator must reject (bad gauge, no layers): used by setup's self-check and tests.
BAD_CONDITION = {"job_id": str(JOB_ID), "type_tag": "B4", "studs": {"size_in": 3.625, "gauge": 19, "spacing_in": 16}}
