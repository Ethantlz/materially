"""$jsonSchema validators, one per collection. MongoDB enforces these with validationAction "error".

Conventions:
- Cross-collection references (job_id, ...) are strings holding the referenced doc's _id hex.
- Drawing coordinates are PDF points (72 per paper inch), origin top-left, y down.
- Numbers use bsonType "number" (int or double): inches as decimals (3-5/8" -> 3.625), fire_rating in
  hours, gauge as an integer.
- contracts/validate.py checks these same schemas in Python, so a model's output is checked before a write.
"""

SHEET_KINDS = [
    "partition_types",
    "floor_plan",
    "reflected_ceiling",
    "elevations",
    "details",
    "schedules",
    "other",
]

SCALE_SOURCES = ["title_block", "view_title", "bookmark", "manual"]
NAME_SOURCES = ["bookmark", "title_block", "none"]

GAUGES = [12, 14, 16, 18, 20, 22, 25, 30]
BOARD_TYPES = [
    "regular",
    "type_x",
    "type_c",
    "moisture_resistant",
    "mold_resistant",
    "type_x_mold_resistant",
    "abuse_resistant",
    "impact_resistant",
    "shaftliner",
    "cement_board",
    "glass_mat_sheathing",
    "sound_dampening",
]
TIERS = ["decide", "quick", "expert"]
EWO_STATUSES = ["new", "routed", "drafted", "review", "approved", "rejected"]

_NUM = {"bsonType": "number"}
_MONEY = {"bsonType": "number", "minimum": 0}
_STR = {"bsonType": "string"}
_STR_OR_NULL = {"bsonType": ["string", "null"]}
POINT = {
    "bsonType": "array",
    "minItems": 2,
    "maxItems": 2,
    "items": {"bsonType": "number", "minimum": 0, "maximum": 5000},
}

SCALE = {
    "bsonType": ["object", "null"],
    "required": ["label", "pt_per_ft", "source"],
    "properties": {
        "label": _STR,  # canonical, e.g. 1/8" = 1'-0"  or  NTS
        "pt_per_ft": {"bsonType": ["number", "null"], "minimum": 0.01, "maximum": 1000},  # null = not to scale
        "ratio": {"bsonType": ["number", "null"]},  # real length / paper length (96 for 1/8" = 1'-0")
        "source": {"enum": SCALE_SOURCES},
        "confidence": {"bsonType": "number", "minimum": 0, "maximum": 1},
        "candidates": {"bsonType": "array", "items": _STR},
    },
}

VALIDATORS: dict[str, dict] = {
    "jobs": {
        "bsonType": "object",
        "required": ["name", "created_at", "status"],
        "properties": {
            "name": {"bsonType": "string", "minLength": 1, "maxLength": 200},
            "created_at": {"bsonType": "date"},
            "status": {"enum": ["uploading", "processing", "ready", "error"]},
            "files": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["name", "pages"],
                    "properties": {"name": _STR, "pages": {"bsonType": "number", "minimum": 0}},
                },
            },
            "pages_total": {"bsonType": "number", "minimum": 0},
            "pages_named": {"bsonType": "number", "minimum": 0},
            "pages_rendered": {"bsonType": "number", "minimum": 0},
            "error": _STR_OR_NULL,
        },
    },
    "sheets": {
        "bsonType": "object",
        "required": ["job_id", "page", "file", "file_page", "size_pt", "kind"],
        "properties": {
            "job_id": _STR,
            "page": {"bsonType": "number", "minimum": 1},  # 1-based across the whole job
            "file": _STR,
            "file_page": {"bsonType": "number", "minimum": 1},
            "sheet_no": {"bsonType": ["string", "null"], "maxLength": 20},
            "title": {"bsonType": ["string", "null"], "maxLength": 200},
            "name_source": {"enum": NAME_SOURCES},
            "kind": {"enum": SHEET_KINDS},
            "routing": {"bsonType": "object"},
            "size_pt": {"bsonType": "array", "minItems": 2, "maxItems": 2, "items": _NUM},
            "scale": SCALE,
            "image_path": _STR_OR_NULL,  # URL path under /data, rendered at image_scale px per pt
            "thumb_path": _STR_OR_NULL,
            "image_scale": _NUM,
            "created_at": {"bsonType": "date"},
        },
    },
    "conditions": {
        "bsonType": "object",
        "required": ["type_tag", "studs", "layers_each_side"],
        "properties": {
            "job_id": _STR,
            "type_tag": {"bsonType": "string", "minLength": 1, "maxLength": 12},
            "studs": {
                "bsonType": "object",
                "required": ["size_in", "gauge", "spacing_in"],
                "properties": {
                    "size_in": {"bsonType": "number", "minimum": 1, "maximum": 14},
                    # gauge / spacing are often only in the specs (09 22 16): present but null when the
                    # drawing doesn't say. size_in stays non-null, so "SEE UL DET." lands in review_queue.
                    "gauge": {"enum": GAUGES + [None]},
                    "spacing_in": {"bsonType": ["number", "null"], "minimum": 4, "maximum": 48},
                },
            },
            "layers_each_side": {
                "bsonType": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "bsonType": "object",
                    "required": ["thickness_in", "board_type"],
                    "properties": {
                        "thickness_in": {"bsonType": "number", "minimum": 0.25, "maximum": 1.25},
                        "board_type": {"enum": BOARD_TYPES},
                    },
                },
            },
            "sides": {"enum": [1, 2]},
            "insulation": _STR_OR_NULL,
            "fire_rating": {"bsonType": ["number", "null"], "minimum": 0, "maximum": 4},  # hours
            "ul_assembly": _STR_OR_NULL,
            "stc": {"bsonType": ["number", "null"], "minimum": 0, "maximum": 90},
            "height_ft": {"bsonType": ["number", "null"], "minimum": 1, "maximum": 60},
            "source": {
                "bsonType": "object",
                "properties": {"sheet": _STR, "page": _NUM, "bbox": {"bsonType": "array", "items": _NUM}},
            },
            "routing": {"bsonType": "object"},
        },
    },
    "rooms": {
        "bsonType": "object",
        "required": ["job_id", "sheet", "label_pt"],
        "properties": {
            "job_id": _STR,
            "sheet": _STR,  # sheets._id hex
            "name": _STR_OR_NULL,
            "number": _STR_OR_NULL,
            "label_pt": POINT,
        },
    },
    "ewos": {
        "bsonType": "object",
        "required": ["job_id", "sheet", "pin", "description", "status"],
        "properties": {
            "job_id": _STR,
            "sheet": _STR,
            "pin": POINT,
            "room_id": _STR_OR_NULL,
            "description": {"bsonType": "string", "minLength": 1},
            "submitted_by": _STR,
            "status": {"enum": EWO_STATUSES},
            "created_at": {"bsonType": "date"},
        },
    },
    "ewo_drafts": {
        "bsonType": "object",
        "required": ["ewo_id", "task", "line_items", "total"],
        "properties": {
            "ewo_id": _STR,
            "task": _STR,
            "model": _STR,
            "line_items": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["desc", "qty", "unit", "unit_cost"],
                    "properties": {
                        "desc": {"bsonType": "string", "minLength": 1},
                        "qty": _MONEY,
                        "unit": {"bsonType": "string", "minLength": 1},
                        "unit_cost": _MONEY,
                    },
                },
            },
            "total": _MONEY,
            "explanation": _STR,
            "routing": {"bsonType": "object"},
            "escalated": {"bsonType": "bool"},
        },
    },
    "orders": {
        "bsonType": "object",
        "required": ["job_id", "condition_id", "lf", "lines"],
        "properties": {
            "job_id": _STR,
            "condition_id": _STR,
            "lf": _MONEY,
            "height_ft": _MONEY,
            "lines": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["item", "qty", "unit"],
                    "properties": {
                        "item": {"bsonType": "string", "minLength": 1},
                        "qty": _MONEY,
                        "unit": _STR,
                        "unit_cost": {"bsonType": ["number", "null"], "minimum": 0},
                        "computed_qty": _MONEY,
                        "waste": {"bsonType": "number", "minimum": 0, "maximum": 1},
                    },
                },
            },
            "total": {"bsonType": ["number", "null"], "minimum": 0},
            "guard": {
                "bsonType": "object",
                "required": ["over", "items_over"],
                "properties": {"over": {"bsonType": "bool"}, "items_over": {"bsonType": "array"}},
            },
            "supplier_email": _STR_OR_NULL,
            "sent_at": {"bsonType": ["date", "null"]},
        },
    },
    "product_data": {
        "bsonType": "object",
        "required": ["query_hash"],
        "properties": {
            "query_hash": {"bsonType": "string", "minLength": 1},
            "condition_id": _STR_OR_NULL,
            "component": _STR,
            "url": _STR_OR_NULL,
            "title": _STR_OR_NULL,
            "attrs": {"bsonType": "object"},
            "spec_check": {"bsonType": "object"},  # {attr: p_mismatch}
            "fetched_at": {"bsonType": "date"},
        },
    },
    "price_book": {
        "bsonType": "object",
        "required": ["item", "unit", "unit_cost", "supplier"],
        "properties": {
            "item": {"bsonType": "string", "minLength": 1},
            "unit": {"bsonType": "string", "minLength": 1},
            "unit_cost": _MONEY,
            "supplier": _STR,
        },
    },
    "model_policy": {
        "bsonType": "object",
        "required": ["task", "tier", "model", "enabled"],
        "properties": {
            "task": {"bsonType": "string", "minLength": 1},
            "tier": {"enum": TIERS},
            "model": {"bsonType": "string", "minLength": 1},
            "retry_model": _STR_OR_NULL,
            "enabled": {"bsonType": "bool"},
            "prompt": _STR_OR_NULL,  # overrides app/prompts.py for this task
            # Call options for `model` (not the retry model), read live by the router. Example: EWO drafting turns
            # DeepSeek's reasoning off: {"extra_body": {"chat_template_kwargs": {"thinking": false}}, "timeout_s": 30}
            "params": {
                "bsonType": ["object", "null"],
                "properties": {
                    "extra_body": {"bsonType": "object"},
                    "timeout_s": {"bsonType": "number", "minimum": 1, "maximum": 600},
                    "max_tokens": {"bsonType": "number", "minimum": 16, "maximum": 32000},
                },
            },
        },
    },
    "usage": {
        "bsonType": "object",
        "required": ["task", "model", "at"],
        "properties": {
            "job_id": _STR_OR_NULL,
            "task": _STR,
            "model": _STR,
            "input_tokens": _MONEY,
            "output_tokens": _MONEY,
            "cost_usd": _MONEY,
            "ms": _MONEY,
            "at": {"bsonType": "date"},
            "ok": {"bsonType": "bool"},
            "error": _STR,
        },
    },
    # One takeoff line drawn on a sheet. quantity is LF / SF / EA from the points and the sheet's scale.
    "measurements": {
        "bsonType": "object",
        "required": ["job_id", "sheet", "condition_id", "kind", "points", "quantity", "unit"],
        "properties": {
            "job_id": _STR,
            "sheet": _STR,  # sheets._id hex
            "condition_id": _STR,
            "kind": {"enum": ["linear", "area", "count"]},
            "points": {"bsonType": "array", "minItems": 1, "items": POINT},  # PDF points, top-left origin
            "quantity": {"bsonType": "number", "minimum": 0},
            "unit": {"enum": ["lf", "sf", "ea"]},
            "pt_per_ft": {"bsonType": ["number", "null"], "minimum": 0.01},  # the sheet scale it was measured at
            "height_ft": {"bsonType": ["number", "null"], "minimum": 0, "maximum": 60},
            "label": _STR_OR_NULL,
            "created_at": {"bsonType": "date"},
        },
    },
    "review_queue": {
        "bsonType": "object",
        "required": ["source", "reason", "at"],
        "properties": {"source": _STR, "collection": _STR, "reason": _STR, "at": {"bsonType": "date"}},
    },
}

# Model output that is not a whole collection doc (a run_task validator_name).
EXTRA: dict[str, dict] = {
    "product_attrs": {
        "bsonType": "object",
        "properties": {
            "manufacturer": _STR_OR_NULL,
            "product_name": _STR_OR_NULL,
            "thickness_in": {"bsonType": ["number", "null"], "minimum": 0, "maximum": 2},
            "board_type": {"enum": BOARD_TYPES + [None]},
            "size_in": {"bsonType": ["number", "null"]},
            "gauge": {"enum": GAUGES + [None]},
            "fire_rated": {"bsonType": ["bool", "null"]},
            "ul": {"bsonType": ["array", "null"], "items": _STR},
            "astm": {"bsonType": ["array", "null"], "items": _STR},
        },
    },
}

# Fields the caller fills, not the model: run_task drops them from `required` and from the schema the
# model is shown, so a model only produces content fields.
CALLER_FIELDS = {
    "_id", "job_id", "ewo_id", "condition_id", "task", "model", "escalated",
    "routing", "source", "created_at", "at", "fetched_at", "query_hash",
}

SCHEMAS = {**VALIDATORS, **EXTRA}

# (keys, options) per collection
INDEXES: dict[str, list[tuple[list, dict]]] = {
    "jobs": [([("created_at", -1)], {})],
    "sheets": [([("job_id", 1), ("page", 1)], {"unique": True})],
    "review_queue": [([("at", -1)], {})],
    # PDF points run to ~3000, past the 2d default bounds of +-180.
    "rooms": [([("label_pt", "2d"), ("sheet", 1)], {"min": 0, "max": 5000, "name": "label_pt_2d_sheet"})],
    "conditions": [([("job_id", 1), ("type_tag", 1)], {})],
    "ewos": [([("job_id", 1)], {})],
    "ewo_drafts": [([("ewo_id", 1)], {})],
    "orders": [([("job_id", 1)], {})],
    "measurements": [([("job_id", 1), ("condition_id", 1)], {}), ([("sheet", 1)], {})],
    "usage": [([("job_id", 1)], {})],
    "product_data": [([("query_hash", 1)], {"unique": True})],
    "model_policy": [([("task", 1)], {"unique": True})],
    "price_book": [([("item", 1), ("supplier", 1)], {"unique": True})],
}
