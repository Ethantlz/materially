"""Each screen renders from the contracts' sample docs, and every testid in contracts/TESTIDS.md exists on its page."""
import html
import re
from copy import deepcopy
from pathlib import Path

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.main import app
from contracts.samples import CONDITION_ID, DRAFT_ID, JOB_ID, ORDER_ID, SAMPLES, SHEET_PT_ID
from ui.api import inches

ROOT = Path(__file__).resolve().parents[2]
EMPTY_JOB = ObjectId()
SENT_ORDER = ObjectId()


@pytest.fixture
def client(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    for name, doc in SAMPLES.items():
        if name not in ("price_book", "model_policy"):  # mock_db is already seeded with these
            mock_db[name].insert_one(deepcopy(doc))
    mock_db.conditions.update_one({"_id": CONDITION_ID}, {"$set": {"routing.escalated": True}})
    mock_db.ewo_drafts.update_one({"_id": DRAFT_ID}, {"$set": {"escalated": True, "job_id": str(JOB_ID)}})
    mock_db.orders.update_one({"_id": ORDER_ID}, {"$set": {"guard": {"over": True, "items_over": ["board_0.625_type_x_4x10"]}}})
    sent = deepcopy(SAMPLES["orders"]) | {"_id": SENT_ORDER, "sent_at": SAMPLES["jobs"]["created_at"], "guard": {"over": False, "items_over": []}}
    mock_db.orders.insert_one(sent)
    mock_db.review_queue.insert_one({
        "source": "extract.partition", "collection": "conditions", "at": SAMPLES["jobs"]["created_at"],
        "doc": {"job_id": str(JOB_ID), "type_tag": "B4", "studs": {"size_in": 3.625, "gauge": 19, "spacing_in": 16}},
        "reason": "Document failed validation: {'details': [{'propertyName': 'gauge', 'reason': 'value was not found in enum', 'consideredValue': 19}]}",
    })
    mock_db.review_queue.insert_one({  # a parse that failed before any write has no `collection`
        "source": "extract.partition", "at": SAMPLES["jobs"]["created_at"], "reason": "studs.size_in: expected number, got NoneType None",
        "doc": {"job_id": str(JOB_ID), "sheet": str(SHEET_PT_ID), "type_tag": "SB2"},
    })
    mock_db.jobs.insert_one({"_id": EMPTY_JOB, "name": "Empty", "status": "ready", "created_at": SAMPLES["jobs"]["created_at"]})
    return TestClient(app)


def _pages(client) -> dict[str, list[str]]:
    """Each TESTIDS page pattern -> rendered HTML in the full state and the empty state.

    A pattern may be a full page, a popup partial (`/ui/jobs/{job}/panel/<name>`, `/ui/orders/{order}/panel`)
    or the projects page; every one of them formats the same `{job}` / `{condition}` / `{order}` ids."""
    ids = {"full": {"job": JOB_ID, "condition": CONDITION_ID, "order": ORDER_ID},
           "empty": {"job": EMPTY_JOB, "condition": CONDITION_ID, "order": SENT_ORDER}}
    out = {}
    for pattern in {line.split("|")[1].strip() for line in _testid_lines()}:
        htmls = []
        for state, vals in ids.items():
            url = pattern.format(**{k: str(v) for k, v in vals.items()})
            if pattern == "/review" and state == "empty":
                url += f"?job_id={EMPTY_JOB}"
            r = client.get(url)
            assert r.status_code == 200, (url, r.text[:300])
            htmls.append(r.text)
        out[pattern] = htmls
    return out


def _testid_lines() -> list[str]:
    """The live contract lines: a testid whose element the app no longer has is marked `(retired)` and skipped."""
    text = (ROOT / "contracts" / "TESTIDS.md").read_text(encoding="utf-8")
    return [line[2:] for line in text.splitlines()
            if line.startswith("- ") and line.count("|") == 2 and not line.rstrip().endswith("(retired)")]


def test_every_screen_renders(client):
    for url in (f"/jobs/{JOB_ID}/conditions", f"/conditions/{CONDITION_ID}", f"/jobs/{JOB_ID}/orders",
                f"/orders/{ORDER_ID}", f"/jobs/{JOB_ID}/ewo", f"/jobs/{JOB_ID}/pm", "/review", f"/jobs/{JOB_ID}", f"/jobs/{JOB_ID}/takeoff",
                f"/jobs/{EMPTY_JOB}/takeoff", "/"):
        r = client.get(url)
        assert r.status_code == 200, url


def test_sheet_select_has_names_not_scales(client):
    page = client.get(f"/jobs/{JOB_ID}/takeoff").text
    sel = page[page.index('data-testid="sheet-select"'):]
    sel = sel[: sel.index("</select>")]
    assert "A-601" in sel and "PARTITION TYPES" in sel
    assert "NTS" not in sel and "= 1'" not in sel  # no scale in the dropdown
    i_undo, i_scale = page.index('data-testid="tool-undo"'), page.index('data-testid="canvas-scale"')
    assert 0 < i_scale - i_undo < 400  # the scale chip sits right after Undo
    assert page.index('data-testid="cond-panel"') < page.index('data-testid="takeoff-canvas"')  # panel on the left
    for tid in ("alt-select", "scan-partitions", "open-orders", "open-ewos", "open-tracking", "open-review"):
        assert f'data-testid="{tid}"' in page


def test_job_url_redirects_to_takeoff(client):
    r = client.get(f"/jobs/{JOB_ID}", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"].endswith(f"/jobs/{JOB_ID}/takeoff")


def test_takeoff_copy_says_left_and_refresh_guards_the_alternate():
    js = (ROOT / "ui" / "static" / "takeoff.js").read_text(encoding="utf-8")
    assert "on the right" not in js  # the conditions panel moved to the left
    assert "alt !== S.altId" in js or "alt === S.altId" in js  # a stale answer for another alternate is dropped


def test_every_testid_exists_on_its_page(client):
    pages = _pages(client)
    js = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "ui" / "static").glob("*.js"))
    missing = []
    for line in _testid_lines():
        testid, page, what = (part.strip() for part in line.split("|"))
        found = any(f'data-testid="{testid}"' in page_html for page_html in pages[page])
        if "(js)" in what:  # data-testid="x" in a template string, el.dataset.testid = "x", or {"data-testid": "x"}
            found = found or re.search(r"""testid["']?\s*[:=]\s*["']""" + re.escape(testid) + r"""["']""", js)
        if not found:
            missing.append(f"{testid} on {page}")
    assert not missing, missing


def test_projects_page_lists_alternates_and_links_to_takeoff(client, mock_db):
    from projects import alternates as alt
    a1 = alt.create_alternate(str(JOB_ID), "Alternate 1")
    page = client.get("/").text
    assert 'data-testid="project-row"' in page and "Alternate 1" in page
    assert f'/jobs/{JOB_ID}/takeoff?alt={a1}' in page


def test_conditions_page_lists_both_kinds_of_rejected_parse(client):
    html = client.get(f"/jobs/{JOB_ID}/conditions").text
    assert html.count('data-testid="condition-rejected"') == 2
    assert "SB2" in html and "gauge: 19 not allowed" in html and "expected number" in html


def test_pages_show_the_key_numbers(client):
    order = client.get(f"/orders/{ORDER_ID}").text
    assert "Over-purchase guard tripped" in order and "$2,213.15" in order
    cond = html.unescape(client.get(f"/conditions/{CONDITION_ID}").text)
    assert '3-5/8"' in cond and "Type X" in cond and "U419" in cond
    pm = client.get(f"/jobs/{JOB_ID}/pm").text
    assert "$625.55" in pm and "escalated" in pm
    cost = client.get(f"/ui/jobs/{JOB_ID}/cost").json()
    assert cost["calls"] == 1 and cost["cost_usd"] == 0.00023
    assert cost["by_task"] == [{"task": "parse_partition", "model": "deepseek-ai/DeepSeek-V4.1-Flash", "calls": 1, "cost_usd": 0.00023}]
    assert client.get(f"/ui/jobs/{JOB_ID}/drafts").json() == [str(DRAFT_ID)]
    assert client.get(f"/ui/drafts/{DRAFT_ID}/card").status_code == 200


@pytest.mark.parametrize("value,text", [(3.625, '3-5/8"'), (0.625, '5/8"'), (6, '6"'), (2.5, '2-1/2"'), (None, "—")])
def test_inches(value, text):
    assert inches(value) == text


@pytest.mark.parametrize("path,tid", [
    ("orders", "orders-panel"), ("ewos", "ewos-panel"), ("tracking", "tracking-panel"), ("review", "review-panel")])
def test_panels_render(client, path, tid):
    r = client.get(f"/ui/jobs/{JOB_ID}/panel/{path}")
    assert r.status_code == 200 and f'data-testid="{tid}"' in r.text


def test_order_panel_has_lines_guard_and_send(client):
    html = client.get(f"/ui/orders/{ORDER_ID}/panel").text
    for tid in ("order-panel", "order-line-qty", "guard-warning", "send-po"):
        assert f'data-testid="{tid}"' in html


def test_tracking_lists_orders_and_ewos(client):
    page = client.get(f"/ui/jobs/{JOB_ID}/panel/tracking").text
    rows = page.split('data-testid="tracking-row"')[1:]
    assert len(rows) == 3  # the sample order, the sent copy of it, and the sample EWO
    ewo_rows = [r for r in rows if '<span class="badge">EWO</span>' in r]
    assert len(ewo_rows) == 1 and html.escape(SAMPLES["ewos"]["description"][:70]) in ewo_rows[0]
    assert sum('<span class="badge">Order</span>' in r for r in rows) == 2


def test_orders_and_tracking_panels_follow_the_alternate(client, mock_db):
    from contracts.samples import ALT_ID
    from projects.alternates import create_alternate

    alt = create_alternate(str(JOB_ID), "Add alt 1")
    mock_db.orders.insert_one(deepcopy(SAMPLES["orders"]) | {"_id": ObjectId(), "alternate_id": alt, "lf": 77.7})
    base_orders = {"": 2, f"?alt={ALT_ID}": 2, f"?alt={alt}": 1}  # no alt = the Base bid, which owns orders made before alternates
    for query, n in base_orders.items():
        page = client.get(f"/ui/jobs/{JOB_ID}/panel/orders{query}").text
        assert page.count('data-testid="order-row"') == n and ("77.7" in page) == (query == f"?alt={alt}"), query
    for query, n in {"": 3, f"?alt={ALT_ID}": 3, f"?alt={alt}": 2}.items():  # the job's one EWO shows under every alternate
        page = client.get(f"/ui/jobs/{JOB_ID}/panel/tracking{query}").text
        assert page.count('data-testid="tracking-row"') == n and ("77.7 LF" in page) == (query == f"?alt={alt}"), query


def test_order_popup_links_stay_in_the_takeoff(client, mock_db):
    popup = client.get(f"/ui/orders/{ORDER_ID}/panel").text
    assert f'href="/jobs/{JOB_ID}/orders"' in popup  # still a plain link where nothing opens popups
    assert f'data-modal-open="/ui/jobs/{JOB_ID}/panel/orders"' in popup and 'data-modal-title="Material orders"' in popup
    assert 'href="/conditions/' not in popup  # the condition tag is plain text inside the popup
    page = client.get(f"/orders/{ORDER_ID}").text
    assert f'href="/conditions/{CONDITION_ID}"' in page  # and a link on the full page

    alt = ObjectId()
    mock_db.orders.insert_one(deepcopy(SAMPLES["orders"]) | {"_id": alt, "alternate_id": "alt-1"})
    assert 'data-modal-open="/ui/jobs/%s/panel/orders?alt=alt-1"' % JOB_ID in client.get(f"/ui/orders/{alt}/panel").text  # back to its own list


def test_empty_orders_popup_does_not_link_to_the_takeoff(client):
    popup = client.get(f"/ui/jobs/{EMPTY_JOB}/panel/orders").text
    assert 'data-testid="orders-empty"' in popup and "/takeoff" not in popup
    page = client.get(f"/jobs/{EMPTY_JOB}/orders").text
    assert f'href="/jobs/{EMPTY_JOB}/takeoff"' in page  # the old page keeps its link


def test_reason_summary_reads_mongo_rejection():
    from ui.api import reason_summary

    raw = ("Document failed validation: {'operatorName': '$jsonSchema', 'schemaRulesNotSatisfied': [{'operatorName': "
           "'properties', 'propertiesNotSatisfied': [{'propertyName': 'studs', 'details': [{'operatorName': 'properties', "
           "'propertiesNotSatisfied': [{'propertyName': 'gauge', 'details': [{'operatorName': 'enum', 'specifiedAs': "
           "{'enum': [12, 14, 16, 18, 20, 22, 25, 30]}, 'reason': 'value was not found in enum', 'consideredValue': 19}]}]}]}]}, "
           "{'operatorName': 'required', 'specifiedAs': {'required': ['type_tag', 'studs', 'layers_each_side']}, "
           "'missingProperties': ['layers_each_side']}]}")
    assert reason_summary(raw) == "Rejected by MongoDB's validator · gauge: 19 not allowed · missing: layers_each_side"
    assert reason_summary("Jev's two answers disagreed") == "Jev's two answers disagreed"


def test_light_tokens_and_no_tab_nav(client):
    css = (ROOT / "ui" / "static" / "app.css").read_text(encoding="utf-8")
    for token in ("--ink: #111827", "--line: #e5e7eb", "--stage: #eef0f3", "--accent: #3b82f6"):
        assert token in css
    page = client.get(f"/jobs/{JOB_ID}/takeoff").text
    assert 'data-testid="nav-tabs"' not in page and 'data-testid="nav-home"' in page


def test_css_uses_font_longhands_and_zero_specificity_button_defaults():
    """`font: 500 13px/1 inherit` is invalid (`inherit` is not a family) and a bare `button` rule outranks
    class rules, so the design system must spell fonts out and keep its button defaults at zero specificity."""
    css = (ROOT / "ui" / "static" / "app.css").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # strip comments before looking at selectors
    assert "13px/1 inherit" not in css and "1 inherit" not in css
    assert not re.search(r"font:\s*\S[^;{}]*\s+inherit", css)  # no `font` shorthand with inherit as the family
    assert ":where(button)" in css and ":where(button:hover)" in css
    assert not re.search(r"^\s*button[\s,{:.>]", code, re.M)  # no bare button rules left
    assert ".tbl th.num" in css
    assert ".tbl tfoot td" in css and "border-top: 1px solid var(--line); }" in css


def test_takeoff_js_renders_materials_and_scan():
    js = (ROOT / "ui" / "static" / "takeoff.js").read_text(encoding="utf-8")
    for tid in ("cond-materials", "material-row", "material-find", "material-product"):
        assert f'data-testid="{tid}"' in js
    assert "Takeoff.scan(" in js


def test_canvas_and_undo_follow_the_alternate():
    js = (ROOT / "ui" / "static" / "takeoff.js").read_text(encoding="utf-8")
    assert "Takeoff.undo(S.sheetId, S.altId)" in js
    calls = re.findall(r"Takeoff\.measurements\(([^)]*)\)", js)
    assert calls and all("S.altId" in c for c in calls)


def test_a_draft_is_saved_under_the_alternate_it_was_drawn_in():
    js = (ROOT / "ui" / "static" / "takeoff.js").read_text(encoding="utf-8")
    assert "alternate_id: d.alt" in js  # the draft carries its own alternate, not whatever is selected now
    assert "alt: S.altId" in js         # tagged where the draft is created
    handler = js[js.index('$("#alt-select").addEventListener'):]
    handler = handler[: handler.index("\n});")]  # switching alternates drops a draft that couldn't be saved
    assert "cancelDraft()" in handler
    assert "S.meas = []" in handler


# ---- the canvas: walls drawn at their real width, and press-drag-release to draw one ----
def _takeoff_js() -> str:
    return (ROOT / "ui" / "static" / "takeoff.js").read_text(encoding="utf-8")


def _balanced(js: str, start: int) -> str:
    """js from the bracket at `start` through the bracket that closes it; string literals and // comments are skipped."""
    depth, i = 0, start
    while True:
        ch = js[i]
        if ch in "'\"`":
            i += 1
            while js[i] != ch:
                i += 2 if js[i] == "\\" else 1
        elif js.startswith("//", i):
            i = js.index("\n", i)
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return js[start:i + 1]
        i += 1


def _function_src(js: str, name: str) -> str:
    """`function name(...) { ... }` cut out of takeoff.js."""
    i = js.index(f"function {name}(")
    brace = js.index("{", js.index(")", i))
    return js[i:brace] + _balanced(js, brace)


def _listener_src(js: str, target: str, event: str) -> str:
    """The handler passed to `target.addEventListener("event", ...)`."""
    call = _balanced(js, js.index("(", js.index(f'{target}.addEventListener("{event}"')))
    return call[len(f'("{event}", '):-1]


def test_linear_shapes_are_drawn_at_the_walls_real_width():
    js = _takeoff_js()
    assert "function wallThicknessIn(" in js
    assert "measNode(" in _function_src(js, "renderMeas")  # the saved shapes
    for fn in ("measNode", "renderDraft"):  # saved shapes and the live preview both stroke the band
        assert "wallPx(" in _function_src(js, fn), fn
    wall_px = _function_src(js, "wallPx")
    assert "wallThicknessIn(" in wall_px and "ptPerFt(" in wall_px and "S.ips" in wall_px  # inches -> pt -> image px, like the points
    css = (ROOT / "ui" / "static" / "takeoff.css").read_text(encoding="utf-8")
    band = css[css.index(".band"):]
    band = band[: band.index("}")]
    assert "stroke-opacity: .35" in band and "stroke-linecap: butt" in band  # a translucent band with square ends
    assert ".banded .vis" in css and "1.5px" in css  # and a thin centreline in the full colour


def test_a_wall_is_drawn_by_pressing_dragging_and_releasing():
    js = _takeoff_js()
    assert "endDrag(" in _listener_src(js, "vp", "pointerup")  # the release saves the wall
    assert "startDrag(" in _listener_src(js, "vp", "pointermove")  # moving past the click slop starts the preview
    assert "Drag along a wall to draw it; click to chain points." in js
    assert "click along the wall" not in js and "click along a wall" not in js  # no copy still describing the old mode


def _run_canvas(scenario: str) -> dict:
    """Run takeoff.js's real wall math and pointer handlers in Node, with every other name stubbed or recorded."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't installed")
    js = _takeoff_js()
    funcs = "\n".join(_function_src(js, n) for n in ("wallThicknessIn", "wallPx", "measNode", "renderDraft", "toPt", "clampPt", "snap", "pointAt", "startDrag", "endDrag"))
    handlers = ", ".join(f"{ev}: ({_listener_src(js, 'vp', ev)})" for ev in ("pointerdown", "pointermove", "pointerup", "pointercancel"))
    script = """
const calls = [];
const S = { loaded: true, tool: 'linear', condId: 'c1', sheetId: 's1', altId: 'a1', draft: null, cur: null, cal: null, down: null,
  shift: false, space: false, cursor: null, tx: 10, ty: 20, k: 2, ips: 1.5, fitted: true, ppf: 9, noCond: false, sel: null,
  condObj: { id: 'c1', studs: { size_in: 6 }, layers_each_side: [{ thickness_in: 0.625 }], sides: 2 } };  // the SA6 wall: 7.25 in thick
const stubs = {
  S, vp: { classList: { add() {}, remove() {} }, setPointerCapture() {} },
  vpRect: () => ({ left: 0, top: 0, width: 1000, height: 800 }), sizePt: () => [5000, 5000],
  clamp: (v, lo, hi) => Math.min(Math.max(v, lo), hi), dist: (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]),
  cond: () => (S.noCond ? null : S.condObj), ptPerFt: () => S.ppf, applyView() {}, renderCal() {},
  svgEl: (tag, attrs = {}, parent) => { const e = { tag, attrs, children: [], textContent: '' }; if (parent) parent.children.push(e); return e; },
  toImg: (p) => [p[0] * S.ips, p[1] * S.ips], colorOf: () => '#e8590c', labelFor: () => "10'", esc: (v) => String(v),
  gDraft: { children: [], replaceChildren() { this.children = []; } }, readout: { hidden: false, innerHTML: '', style: {} },
  draftMetrics: () => ({ unit: 'lf', qty: 8, text: "8'", segText: "8'" }), updateNumbers() {}, renderQty() {}, updateHint() {},
  finishDraft: () => { calls.push(['finishDraft', JSON.parse(JSON.stringify(S.draft))]); S.draft = null; S.cur = null; },  // the real one clears the draft before it saves
  cancelDraft: () => { calls.push('cancelDraft'); S.draft = null; S.cur = null; },
  addPoint: (p) => calls.push(['addPoint', p]),
};
const scope = new Proxy({}, {
  has: (_, n) => typeof n === 'string' && (n in stubs || !(n in globalThis)),
  get: (_, n) => (n in stubs ? stubs[n] : () => { calls.push(n); }),
});
const lib = new Function('scope', 'with (scope) { ' + FUNCS + '\\n return { wallThicknessIn, wallPx, measNode, renderDraft, ' + HANDLERS + ' }; }')(scope);
const ev = (x, y, o = {}) => ({ clientX: x, clientY: y, button: 0, pointerId: 1, shiftKey: false, target: { closest: () => null }, preventDefault() {}, ...o });
const snapshot = () => JSON.parse(JSON.stringify(S.draft));
""".replace("FUNCS", json.dumps(funcs)).replace("HANDLERS", json.dumps(handlers)) + scenario
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_wall_thickness_is_the_studs_plus_the_board_on_each_side():
    out = _run_canvas("""
const t = lib.wallThicknessIn, c = (o) => ({ studs: { size_in: 6 }, layers_each_side: [{ thickness_in: 0.625 }], sides: 2, ...o });
const mats = (n) => [{ role: 'stud', size_in: 3.625 }, { role: 'track', size_in: 3.625 },
  { role: 'board', thickness_in: 0.625, sides: 2, desc: '5/8" Type X gypsum board, ' + n + ' each side' }];
S.ppf = 9;
console.log(JSON.stringify({
  sa6: t(c({})), oneSide: t(c({ sides: 1 })), sidesDefault: t(c({ sides: null })), twoLayers: t(c({ layers_each_side: [{ thickness_in: 0.625 }, { thickness_in: 0.625 }] })),
  noLayers: t(c({ layers_each_side: [] })), noStuds: t(c({ studs: null })), nothing: t({ type_tag: 'A1' }), noCond: t(null),
  badStud: t(c({ studs: { size_in: null } })), fromMaterials: t({ sides: 2, materials: mats('1 layer') }), fromMaterials2: t({ sides: 2, materials: mats('2 layers') }),
  noStudMaterial: t({ sides: 2, materials: mats('1 layer').slice(2) }), emptyMaterials: t({ materials: [] }),
  px: lib.wallPx(c({}), 's1'), pxNoScale: (S.ppf = null, lib.wallPx(c({}), 's1')), pxNoCond: lib.wallPx(null, 's1'),
}));
""")
    assert out["sa6"] == 7.25 and out["oneSide"] == 6.625 and out["sidesDefault"] == 7.25  # 6" studs + 2 x 5/8" board
    assert out["twoLayers"] == 8.5 and out["noLayers"] == 6
    assert out["noStuds"] is None and out["nothing"] is None and out["noCond"] is None and out["badStud"] is None  # unknown -> the thin line
    # the page ships a condition's materials, so a view without studs / layers is read from those
    assert out["fromMaterials"] == 3.625 + 2 * 0.625 and out["fromMaterials2"] == 3.625 + 2 * 1.25
    assert out["noStudMaterial"] is None and out["emptyMaterials"] is None
    assert out["px"] == pytest.approx(7.25 / 12 * 9 * 1.5)  # inches -> feet -> PDF points at the sheet's scale -> image px (S.ips)
    assert out["pxNoScale"] is None and out["pxNoCond"] is None


def test_a_wall_is_stroked_as_a_band_under_a_thin_centreline_and_a_bare_line_without_a_scale():
    out = _run_canvas("""
const m = { id: 'm1', kind: 'linear', condition_id: 'c1', sheet: 's1', points: [[10, 10], [110, 10]] };
const kids = (g) => g.children.map((e) => [e.tag, e.attrs.class, e.attrs['stroke-width'] ?? null, e.attrs.style ?? null]);
const saved = lib.measNode(m);
S.draft = { kind: 'linear', points: [[10, 10]], cond: 'c1', sheet: 's1', alt: 'a1', drag: true }; S.cur = [60, 10];
lib.renderDraft();
const draft = kids(stubs.gDraft);
S.sel = 'm1'; const selected = lib.measNode(m).attrs.class; S.sel = null;
S.ppf = null;
const bare = lib.measNode(m);
lib.renderDraft();
S.ppf = 9; S.condObj = { id: 'c1', studs: null };  // a condition that doesn't say how thick it is
const unknown = lib.measNode(m);
console.log(JSON.stringify({ cls: saved.attrs.class, saved: kids(saved), draft, selected, bareCls: bare.attrs.class, bare: kids(bare), bareDraft: kids(stubs.gDraft), unknownCls: unknown.attrs.class }));
""")
    band = 7.25 / 12 * 9 * 1.5  # 6" studs + 2 x 5/8" board, at 9 pt per foot, 1.5 image px per pt
    assert out["cls"] == "m linear banded"
    hit, wall, line = out["saved"][:3]
    assert wall == ["polyline", "band", pytest.approx(band), None]  # the translucent band ...
    assert line[:2] == ["polyline", "vis"] and out["saved"].index(wall) < out["saved"].index(line)  # ... under the centreline
    assert hit[1] == "hit" and f"max(calc(16px*var(--inv)),{band}px)" in hit[3]  # and the whole width is clickable
    assert [k[1] for k in out["draft"]][:2] == ["band", "vis rubber banded"] and out["draft"][0][2] == pytest.approx(band)  # the live preview has the band too
    assert "banded" in out["selected"] and "sel" in out["selected"]  # selected shapes keep their selected styling
    for cls in (out["bareCls"], out["unknownCls"]):  # no scale, or an unknown build: the thin line as before
        assert cls == "m linear"
    assert all(k[1] != "band" for k in out["bare"]) and [k[1] for k in out["bareDraft"]][0] == "vis rubber"


def test_press_drag_release_saves_one_two_point_wall():
    out = _run_canvas("""
lib.pointerdown(ev(100, 100));
lib.pointermove(ev(130, 100));
const mid = { draft: snapshot(), cur: S.cur };
lib.pointermove(ev(220, 100));
lib.pointerup(ev(220, 100));
console.log(JSON.stringify({ mid, calls, down: S.down }));
""")
    x0, x1, y = (100 - 10) / 3, (220 - 10) / 3, (100 - 20) / 3  # screen -> pt: (screen - translate) / k / ips
    assert out["mid"]["draft"] == {"kind": "linear", "points": [pytest.approx([x0, y])], "cond": "c1", "sheet": "s1", "alt": "a1", "drag": True}
    assert out["mid"]["cur"] == pytest.approx([(130 - 10) / 3, y])  # the live preview follows the pointer
    assert len(out["calls"]) == 1 and out["calls"][0][0] == "finishDraft"  # one measurement, through finishDraft's save path
    saved = out["calls"][0][1]
    assert saved["points"] == [pytest.approx([x0, y]), pytest.approx([x1, y])]
    assert (saved["kind"], saved["cond"], saved["sheet"], saved["alt"], saved["drag"]) == ("linear", "c1", "s1", "a1", False)
    assert out["down"] is None


def test_a_click_still_chains_points():
    out = _run_canvas("""
lib.pointerdown(ev(100, 100)); lib.pointermove(ev(102, 101)); lib.pointerup(ev(102, 101));
const click = { calls: calls.splice(0), draft: S.draft };
S.draft = { kind: 'linear', points: [[5, 5]], cond: 'c1', sheet: 's1', alt: 'a1' };  // a chain is in progress
lib.pointerdown(ev(100, 100)); lib.pointermove(ev(200, 100)); lib.pointerup(ev(200, 100));
const dragInChain = { calls: calls.splice(0), points: S.draft.points.length };
lib.pointerdown(ev(100, 100)); lib.pointerup(ev(103, 100));
console.log(JSON.stringify({ click, dragInChain, chained: calls }));
""")
    assert out["click"]["calls"][0][0] == "addPoint" and len(out["click"]["calls"]) == 1 and out["click"]["draft"] is None  # <= 4 px: a click
    assert out["dragInChain"] == {"calls": [], "points": 1}  # mid-chain, a drag does what it always did: nothing
    assert out["chained"][0][0] == "addPoint"


def test_shift_snaps_a_dragged_wall_to_horizontal_or_vertical():
    out = _run_canvas("""
const run = (x, y) => {
  lib.pointerdown(ev(100, 100)); lib.pointermove(ev(x, y, { shiftKey: true })); lib.pointerup(ev(x, y, { shiftKey: true }));
  const saved = calls.splice(0)[0][1].points; S.shift = false; return saved;
};
console.log(JSON.stringify({ horizontal: run(200, 112), vertical: run(112, 200) }));
""")
    x0, y0 = (100 - 10) / 3, (100 - 20) / 3
    assert out["horizontal"] == [pytest.approx([x0, y0]), pytest.approx([(200 - 10) / 3, y0])]  # y held, x follows the pointer
    assert out["vertical"] == [pytest.approx([x0, y0]), pytest.approx([x0, (200 - 20) / 3])]


def test_panning_does_not_draw_and_a_drag_does_not_pan():
    out = _run_canvas("""
const run = (setup, down = {}) => {
  Object.assign(S, { tx: 10, ty: 20, tool: 'linear', space: false, draft: null }); setup();
  lib.pointerdown(ev(100, 100, down)); lib.pointermove(ev(160, 130)); lib.pointerup(ev(160, 130));
  const r = { tx: S.tx, ty: S.ty, draft: S.draft, calls: calls.splice(0) }; S.space = false; return r;
};
const space = run(() => { S.space = true; }), middle = run(() => {}, { button: 1 }), select = run(() => { S.tool = 'select'; });
const draw = run(() => {});
console.log(JSON.stringify({ space, middle, select, draw }));
""")
    for name in ("space", "middle", "select"):  # the existing pans: space-drag, middle-drag, the select tool
        assert (out[name]["tx"], out[name]["ty"]) == (70, 50) and out[name]["draft"] is None and out[name]["calls"] == [], name
    assert (out["draw"]["tx"], out["draw"]["ty"]) == (10, 20) and out["draw"]["calls"][0][0] == "finishDraft"  # the drawing drag did not pan


def test_a_cancelled_or_aborted_drag_saves_nothing():
    out = _run_canvas("""
lib.pointerdown(ev(100, 100)); lib.pointermove(ev(160, 100));
S.draft = null; S.cur = null;                         // Esc / right-click / undo cancelled it mid-drag
lib.pointermove(ev(200, 100)); lib.pointerup(ev(200, 100));
const cancelled = { calls: calls.splice(0), draft: S.draft };
lib.pointerdown(ev(100, 100)); lib.pointermove(ev(160, 100)); lib.pointermove(ev(101, 100)); lib.pointerup(ev(101, 100));
const backAtStart = { calls: calls.splice(0), draft: S.draft };
lib.pointerdown(ev(100, 100)); lib.pointermove(ev(160, 100)); lib.pointercancel();
const interrupted = { calls: calls.splice(0), draft: S.draft, down: S.down };
console.log(JSON.stringify({ cancelled, backAtStart, interrupted }));
""")
    assert out["cancelled"] == {"calls": [], "draft": None}  # releasing after Esc neither saves nor starts a new preview
    assert out["backAtStart"] == {"calls": ["cancelDraft"], "draft": None}  # dragged back to where it started: no wall
    assert out["interrupted"] == {"calls": ["cancelDraft"], "draft": None, "down": None}


def test_a_drag_without_a_condition_or_scale_explains_instead_of_drawing():
    out = _run_canvas("""
const run = (setup) => {
  Object.assign(S, { ppf: 9, noCond: false, draft: null }); setup();
  lib.pointerdown(ev(100, 100)); lib.pointermove(ev(160, 100)); lib.pointermove(ev(180, 100)); lib.pointerup(ev(180, 100));
  return { calls: calls.splice(0), draft: S.draft };
};
console.log(JSON.stringify({ noScale: run(() => { S.ppf = null; }), noCond: run(() => { S.noCond = true; }) }));
""")
    assert out["noScale"] == {"calls": ["needScale"], "draft": None}  # said once, drew nothing, and the release adds no stray point
    assert out["noCond"] == {"calls": ["needCondition"], "draft": None}


def test_the_other_tools_keep_their_clicks():
    out = _run_canvas("""
S.tool = 'calibrate';
lib.pointerdown(ev(100, 100)); lib.pointerup(ev(100, 100));
const cal = calls.splice(0);
S.tool = 'area';
lib.pointerdown(ev(100, 100)); lib.pointermove(ev(200, 100)); lib.pointerup(ev(200, 100));  // a drag with the area tool is not a click, as before
const areaDrag = { calls: calls.splice(0), draft: S.draft };
console.log(JSON.stringify({ cal, areaDrag }));
""")
    assert out["cal"] == ["calPoint"] and out["areaDrag"] == {"calls": [], "draft": None}


def test_condition_views_pass_materials_through():
    from ui.common import condition_views

    mats = [{"key": "stud_0", "role": "stud", "desc": '3-5/8" 20ga stud @ 16" o.c.', "source": "derived", "product_id": None}]
    c = {"id": "c1", "type_tag": "A1", "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16},
         "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}], "materials": mats}
    assert condition_views([c])[0]["materials"] == mats
    assert condition_views([{**c, "materials": None}])[0]["materials"] == []
