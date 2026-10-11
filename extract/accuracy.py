"""Field-level partition accuracy against hand-written labels (fixtures/expected/<set>.json, SPEC section 10).

    scores = score(expected_conditions, parsed_conditions)   # {type_tag: {field: right?}}
    print(report(scores))
"""

FIELDS = {
    "stud size": lambda c: (c.get("studs") or {}).get("size_in"),
    "gauge": lambda c: (c.get("studs") or {}).get("gauge"),
    "spacing": lambda c: (c.get("studs") or {}).get("spacing_in"),
    "layers": lambda c: [layer.get("thickness_in") for layer in c.get("layers_each_side") or []],
    "board type": lambda c: [layer.get("board_type") for layer in c.get("layers_each_side") or []],
    "fire rating": lambda c: c.get("fire_rating"),
}


def score(expected: list[dict], got: list[dict]) -> dict[str, dict[str, bool]]:
    """Per expected type tag, whether each field matches. A tag that was never parsed gets every field wrong."""
    by_tag = {c.get("type_tag"): c for c in got}
    out = {}
    for e in expected:
        g = by_tag.get(e["type_tag"])
        out[e["type_tag"]] = {f: g is not None and read(g) == read(e) for f, read in FIELDS.items()}
    return out


def report(scores: dict[str, dict[str, bool]]) -> str:
    width = max([len("type"), *map(len, scores)])
    rows = [f"{'type':<{width}}  " + "  ".join(FIELDS) + "  right"]
    for tag, fields in scores.items():
        cells = "  ".join(f"{'ok' if fields[f] else 'X':^{len(f)}}" for f in FIELDS)
        rows.append(f"{tag:<{width}}  {cells}  {sum(fields.values())}/{len(fields)}")
    per_field = "  ".join(f"{sum(s[f] for s in scores.values()):^{len(f)}}" for f in FIELDS)
    right = sum(sum(s.values()) for s in scores.values())
    rows.append(f"{'total':<{width}}  {per_field}  {right}/{len(FIELDS) * len(scores)}")
    return "\n".join(rows)
