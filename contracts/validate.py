"""Check a doc against a contracts schema in Python, with the same rules MongoDB applies.

    errors = validate("conditions", doc)            # [] when valid
    errors = validate("conditions", doc, ai=True)   # model output: caller fields not required

Covers the $jsonSchema keywords contracts/schemas.py uses: bsonType, required, properties,
items, enum, minimum/maximum, minLength/maxLength, minItems/maxItems, additionalProperties.
"""
from datetime import datetime

from bson import Decimal128, ObjectId

from contracts.schemas import CALLER_FIELDS, SCHEMAS


def _is_num(v) -> bool:
    return isinstance(v, (int, float, Decimal128)) and not isinstance(v, bool)


_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, (list, tuple)),
    "string": lambda v: isinstance(v, str),
    "bool": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "objectId": lambda v: isinstance(v, ObjectId),
    "date": lambda v: isinstance(v, datetime),
    "number": _is_num,
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool) and -(2**31) <= v < 2**31,
    "long": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "double": lambda v: isinstance(v, float),
    "decimal": lambda v: isinstance(v, Decimal128),
}


def errors_for(schema: dict, value, path: str = "") -> list[str]:
    where = path or "(doc)"
    types = schema.get("bsonType")
    if types is not None:
        types = types if isinstance(types, list) else [types]
        if not any(_TYPES[t](value) for t in types):
            return [f"{where}: expected {'|'.join(types)}, got {type(value).__name__} {value!r:.60}"]
    if "enum" in schema:
        same_kind = [e for e in schema["enum"] if isinstance(e, bool) == isinstance(value, bool)]
        if value not in same_kind:
            return [f"{where}: {value!r:.60} not one of {schema['enum']}"]
    errs: list[str] = []
    if _is_num(value):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{where}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{where}: {value} > maximum {schema['maximum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errs.append(f"{where}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{where}: longer than {schema['maxLength']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errs.append(f"{path + '.' if path else ''}{key}: required")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                errs += errors_for(sub, value[key], f"{path}.{key}" if path else key)
        if schema.get("additionalProperties") is False:
            errs += [f"{where}: unexpected field {k}" for k in value if k not in props and k != "_id"]
    if isinstance(value, (list, tuple)):
        if len(value) < schema.get("minItems", 0):
            errs.append(f"{where}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{where}: at most {schema['maxItems']} items")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                errs += errors_for(schema["items"], item, f"{where}[{i}]" if path else f"[{i}]")
    return errs


def ai_schema(name: str) -> dict:
    """The schema a model must satisfy: caller-filled fields removed from required and properties."""
    schema = SCHEMAS[name]
    return {
        **schema,
        "required": [k for k in schema.get("required", []) if k not in CALLER_FIELDS],
        "properties": {k: v for k, v in schema.get("properties", {}).items() if k not in CALLER_FIELDS},
    }


def validate(name: str, doc, *, ai: bool = False) -> list[str]:
    if name not in SCHEMAS:
        raise KeyError(f"no contract schema named {name!r}; have {sorted(SCHEMAS)}")
    return errors_for(ai_schema(name) if ai else SCHEMAS[name], doc)
