"""The conditions panel's "Pull product data": one pass over every material of an expanded condition.

There is no JS runtime harness for the page, so the wiring is checked in the source and the two pieces of
behaviour under test (materialsHtml and the #clist click handler) are cut out of takeoff.js and run in Node.
"""
import html
import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.main import app
from contracts.samples import CONDITION_ID, SAMPLES

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ui" / "static"
NODE = shutil.which("node")


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _fn(js: str, sig: str) -> str:
    """The source of a top-level function, up to its closing `}` at column 0."""
    i = js.index(sig)
    return js[i:js.index("\n}", i) + 2]


def _handler(js: str, start: str) -> str:
    """The source of the arrow function passed to `start`(...), up to its closing `});` at column 0."""
    i = js.index(start) + len(start)
    return js[i:js.index("\n});", i) + 2]


# ---- wiring, read out of the shipped sources ----
def test_api_js_has_the_procure_route():
    js = _read("api.js")
    assert 'procure: (cid) => route("procure", "POST", [`/api/conditions/${cid}/procure`])' in js


def test_materials_html_renders_the_pull_header():
    fn = _fn(_read("takeoff.js"), "function materialsHtml(c) {")
    assert 'data-pull="${esc(c.id)}"' in fn and 'data-testid="cond-pull-products"' in fn
    assert "Pull product data" in fn and "Suppliers" in fn  # the muted note points at the top bar
    assert "No materials listed" in fn  # the empty list still renders for a type with no materials


def test_product_links_are_only_rendered_for_http_urls():
    fn = _fn(_read("takeoff.js"), "function materialsHtml(c) {")
    assert "/^https?:\\/\\//" in fn
    assert fn.index("/^https?:\\/\\//") < fn.index("data-testid=\"material-product\"")


def test_the_pull_branch_comes_before_the_other_clist_branches():
    handler = _handler(_read("takeoff.js"), '$("#clist").addEventListener("click", ')
    assert "Takeoff.procure(" in handler
    assert handler.index('closest("[data-pull]")') < handler.index('closest("[data-expand]")')
    assert handler.index('closest("[data-pull]")') < handler.index('closest("[data-find]")')
    assert "productErrors" in handler and "Found " in handler and "reloadData(true)" in handler


def test_the_products_state_map_is_declared():
    js = _read("takeoff.js")
    state = js[js.index("const S = {"): js.index("const SPEC_FLAG")]
    assert "productErrors" in state and "pulling: new Set()" in state


# ---- behaviour, run under Node ----
def _node(script: str):
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _materials(c: dict, products: dict = None, errors: dict = None, pulling: list = None) -> str:
    """Run takeoff.js's materialsHtml over these condition/products/errors (both maps keyed by condition id).

    `pulling` is the list of condition ids with a Pull product data request in flight (S.pulling)."""
    src = _fn(_read("takeoff.js"), "function materialsHtml(c) {")
    return _node(f"""
const S = {{ products: {json.dumps(products or {})}, productErrors: {json.dumps(errors or {})}, pulling: new Set({json.dumps(pulling or [])}) }};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (ch) => ({{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }}[ch]));
const SPEC_FLAG = 0.7;
const fn = new Function('S', 'esc', 'SPEC_FLAG', {json.dumps("return (" + src + ");")})(S, esc, SPEC_FLAG);
console.log(JSON.stringify({{ html: fn({json.dumps(c)}) }}));
""")["html"]


COND = {"id": "c1", "type_tag": "A1", "materials": [{"key": "studs", "desc": '3-5/8" studs'},
                                                    {"key": "board", "desc": '5/8" Type X'}]}


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_materials_html_shows_the_pull_button_and_the_supplier_note():
    html = _materials(COND)
    assert 'data-pull="c1"' in html and 'data-testid="cond-pull-products"' in html
    assert "Pull product data" in html and "Suppliers" in html
    assert html.index("cond-pull-products") < html.index('data-testid="cond-materials"')


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_a_type_without_materials_gets_no_pull_button():
    html = _materials({"id": "c9", "type_tag": "Z9", "materials": []})
    assert "cond-pull-products" not in html and "No materials listed" in html


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_a_guarded_product_link_falls_back_to_plain_text():
    cond = {"id": "c1", "type_tag": "A1", "materials": [{"key": "studs", "desc": "studs", "product_id": "p1"}]}
    safe = _materials(cond, products={"c1": {"p1": {"id": "p1", "url": "https://acme.test/sheet.pdf", "title": "Acme 362", "spec_check": {}}}})
    assert '<a href="https://acme.test/sheet.pdf"' in safe and "Acme 362" in safe
    bad = _materials(cond, products={"c1": {"p1": {"id": "p1", "url": "javascript:alert(1)", "title": "Acme 362"}}})
    assert "<a " not in bad and "javascript:" not in bad and "Acme 362" in bad


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_an_unchecked_product_is_pilled_and_a_pull_error_sits_under_its_row():
    cond = {"id": "c1", "type_tag": "A1", "materials": [{"key": "studs", "desc": "studs", "product_id": "p1"},
                                                        {"key": "board", "desc": "board"}]}
    html = _materials(cond, products={"c1": {"p1": {"id": "p1", "url": "https://a.test/x", "title": "Acme", "checked": False}}},
                      errors={"c1": {"board": "No supplier matched this board."}})
    assert "unchecked" in html
    assert html.index('data-testid="material-error">No supplier matched this board.') > html.index("board")  # under its own row
    assert html.count('data-testid="material-error"') == 1  # not on the row that found a product


def _pull(result: dict, saved: dict = None) -> dict:
    """Run the #clist click handler against the Pull product data button and report what it did.

    `saved` is what GET /api/conditions/{cid}/products answers (the refetch after a stopped pull)."""
    src = _handler(_read("takeoff.js"), '$("#clist").addEventListener("click", ')
    return _node(f"""
const calls = [], seen = [];
const btn = {{ disabled: false, textContent: 'Pull product data', dataset: {{ pull: 'c1' }} }};
const S = {{ products: {{}}, productErrors: {{}}, pulling: new Set() }};
const stubs = {{
  S, btn,
  Takeoff: {{
    procure: async (cid) => {{ seen.push([cid, btn.disabled, btn.textContent, [...S.pulling]]); return {json.dumps(result)}; }},
    products: async (cid) => {{ calls.push(['products', cid]); return {json.dumps(saved or {"ok": True, "data": []})}; }},
  }},
  cond: () => ({{ id: 'c1', materials: [{{ key: 'studs' }}, {{ key: 'board' }}, {{ key: 'tape' }}] }}),
  errText: (r, fallback) => (r.data && r.data.detail) || fallback,
  toast: (m, bad) => calls.push(['toast', m, !!bad]),
  reloadData: async (force) => {{ calls.push(['reload', force]); return true; }},
}};
const scope = new Proxy({{}}, {{
  has: (_, n) => typeof n === 'string' && (n in stubs || !(n in globalThis)),
  get: (_, n) => (n in stubs ? stubs[n] : () => {{ calls.push(n); }}),
}});
const handler = new Function('scope', {json.dumps('with (scope) { return (' + src + '); }')})(scope);
const e = {{ target: {{ closest: (sel) => (sel === '[data-pull]' ? btn : null) }} }};
(async () => {{
  await handler(e);
  console.log(JSON.stringify({{ seen, calls, products: S.products, errors: S.productErrors,
                               pulling: [...S.pulling], disabled: btn.disabled, text: btn.textContent }}));
}})();
""")


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_a_successful_pull_merges_products_and_counts_them_against_the_materials():
    out = _pull({"ok": True, "data": {"products": [{"id": "p1", "url": "https://a.test/1", "title": "One"},
                                                   {"id": "p2", "url": "https://a.test/2", "title": "Two"}],
                                      "errors": {"tape": "No supplier matched this tape."}}})
    assert out["seen"] == [["c1", True, "Pulling…", ["c1"]]]  # off, renamed and flagged while the search runs
    assert out["pulling"] == []  # and the flag is cleared again once it answers
    assert sorted(out["products"]["c1"]) == ["p1", "p2"]  # keyed by product id
    assert out["errors"]["c1"] == {"tape": "No supplier matched this tape."}
    assert ["toast", "Found 2 of 3 products", False] in out["calls"]
    assert out["calls"][-1] == ["reload", True]  # the materials list is re-read from the server


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_a_refused_pull_says_why_and_gives_the_button_back():
    out = _pull({"ok": False, "status": 409, "data": {"detail": "Product search needs a Firecrawl key (FIRECRAWL_API_KEY in Infisical)."}})
    assert ["toast", "Product search needs a Firecrawl key (FIRECRAWL_API_KEY in Infisical).", True] in out["calls"]
    assert out["disabled"] is False and out["text"] == "Pull product data"
    assert out["products"] == {"c1": {}} and out["errors"] == {}  # nothing was saved, nothing is flagged under a material
    assert out["pulling"] == []


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_a_stopped_pull_still_shows_what_it_saved():
    # a 502 (Firecrawl blocked) stops the search, but the products found before it are saved and linked to their materials
    out = _pull({"ok": False, "status": 502, "data": {"detail": "Firecrawl refused the request (out of credits)."}},
                saved={"ok": True, "data": [{"id": "p1", "url": "https://a.test/1", "title": "One"}]})
    assert ["toast", "Firecrawl refused the request (out of credits).", True] in out["calls"]
    assert ["products", "c1"] in out["calls"]  # refetched ...
    assert list(out["products"]["c1"]) == ["p1"] and out["products"]["c1"]["p1"]["title"] == "One"  # ... keyed by id
    assert ["reload", True] in out["calls"]  # the materials now point at what was saved
    toast_at, products_at, reload_at = (out["calls"].index(c) for c in (
        ["toast", "Firecrawl refused the request (out of credits).", True], ["products", "c1"], ["reload", True]))
    assert toast_at < products_at < reload_at  # say why first, then show what there is
    assert out["disabled"] is False and out["text"] == "Pull product data"  # and the button comes back
    assert out["pulling"] == []


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_a_stopped_pull_keeps_the_products_it_already_had_if_the_refetch_fails():
    out = _pull({"ok": False, "status": 502, "data": {"detail": "rate limited"}}, saved={"ok": False, "status": 0, "data": {}})
    assert out["products"] == {} and ["reload", True] in out["calls"]
    assert out["disabled"] is False and out["pulling"] == []


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_one_pull_per_condition_at_a_time():
    """While a pull is in flight a re-render (the 6 s poll) keeps the button off and a second click never reaches the server."""
    mat_src = _fn(_read("takeoff.js"), "function materialsHtml(c) {")
    src = _handler(_read("takeoff.js"), '$("#clist").addEventListener("click", ')
    out = _node(f"""
const calls = [], rendered = [];
const btn = {{ disabled: false, textContent: 'Pull product data', dataset: {{ pull: 'c1' }} }};
const S = {{ products: {{}}, productErrors: {{}}, pulling: new Set() }};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (ch) => ({{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }}[ch]));
const materialsHtml = new Function('S', 'esc', 'SPEC_FLAG', {json.dumps("return (" + mat_src + ");")})(S, esc, 0.7);
const COND = {{ id: 'c1', materials: [{{ key: 'studs', desc: 'studs' }}] }};
let finish;
const stubs = {{
  S, btn,
  Takeoff: {{
    procure: (cid) => {{ calls.push(['procure', cid]); return new Promise((res) => {{ finish = res; }}); }},
    products: async () => ({{ ok: true, data: [] }}),
  }},
  cond: () => COND,
  errText: (r, fallback) => fallback,
  toast: () => {{}},
  reloadData: async () => true,
}};
const scope = new Proxy({{}}, {{
  has: (_, n) => typeof n === 'string' && (n in stubs || !(n in globalThis)),
  get: (_, n) => (n in stubs ? stubs[n] : () => {{}}),
}});
const handler = new Function('scope', {json.dumps('with (scope) { return (' + src + '); }')})(scope);
const e = {{ target: {{ closest: (sel) => (sel === '[data-pull]' ? btn : null) }} }};
(async () => {{
  const first = handler(e);                 // pull starts, never answers until `finish`
  rendered.push(materialsHtml(COND));       // the poll re-renders mid-pull
  await handler(e);                         // a second click on the (re-rendered) button
  await handler(e);
  const during = [...calls];
  finish({{ ok: true, data: {{ products: [], errors: {{}} }} }});
  await first;
  rendered.push(materialsHtml(COND));       // and after it answered
  const again = handler(e);                 // a new pull is allowed again
  const next = {{ calls: [...calls], pulling: [...S.pulling] }};
  finish({{ ok: true, data: {{ products: [], errors: {{}} }} }});
  await again;
  console.log(JSON.stringify({{ during, rendered, next, pulling: [...S.pulling] }}));
}})();
""")
    assert out["during"] == [["procure", "c1"]]  # the second and third click never called procure
    mid, after = out["rendered"]
    assert 'data-pull="c1"' in mid and " disabled" in mid and ">Pulling…</button>" in mid
    assert "Pull product data</button>" not in mid
    assert " disabled" not in after and ">Pull product data</button>" in after
    assert out["next"] == {"calls": [["procure", "c1"], ["procure", "c1"]], "pulling": ["c1"]}  # once the first answered, a new pull goes out
    assert out["pulling"] == []


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_materials_html_disables_the_button_only_for_the_condition_being_pulled():
    html = _materials(COND, pulling=["c1"])
    assert "disabled" in html and "Pulling…" in html and "Pull product data" not in html
    other = _materials(COND, pulling=["c2"])
    assert "disabled" not in other and "Pull product data" in other


# ---- the condition page (server-rendered) ----
@pytest.fixture
def client(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    for name, doc in SAMPLES.items():
        if name not in ("price_book", "model_policy"):  # mock_db is already seeded with these
            mock_db[name].insert_one(deepcopy(doc))
    return TestClient(app)


def test_the_condition_page_only_links_http_product_urls(client, mock_db):
    # product urls are scraped from supplier pages: an href must never carry a javascript:/data: scheme
    for component, url, title in (("stud", "javascript:alert(1)", "Evil sheet"), ("track", "data:text/html,<b>x</b>", "Data sheet"),
                                  ("screw", "javascript:alert(2)", None), ("tape", "http://plain.test/tape.pdf", "Plain tape")):
        mock_db.product_data.insert_one(deepcopy(SAMPLES["product_data"]) | {
            "_id": ObjectId(), "query_hash": f"q-{component}", "component": component, "url": url, "title": title})
    page = html.unescape(client.get(f"/conditions/{CONDITION_ID}").text)
    assert "javascript:" not in page and 'href="data:' not in page  # not as a link, and not echoed as text either
    assert "Evil sheet" in page and "Data sheet" in page  # the title still shows, as plain text
    assert '<a href="http://plain.test/tape.pdf"' in page and "Plain tape" in page
    assert '<a href="https://example.com/5-8-type-x-data-sheet.pdf"' in page  # the seeded https product keeps its link
    assert page.count('data-testid="product-row"') == 5
