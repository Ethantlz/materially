"""The conditions panel's "Pull product data": one pass over every material of an expanded condition.

There is no JS runtime harness for the page, so the wiring is checked in the source and the two pieces of
behaviour under test (materialsHtml and the #clist click handler) are cut out of takeoff.js and run in Node.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

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
    assert "productErrors" in js[js.index("const S = {"): js.index("const SPEC_FLAG")]


# ---- behaviour, run under Node ----
def _node(script: str):
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _materials(c: dict, products: dict = None, errors: dict = None) -> str:
    """Run takeoff.js's materialsHtml over these condition/products/errors (both maps keyed by condition id)."""
    src = _fn(_read("takeoff.js"), "function materialsHtml(c) {")
    return _node(f"""
const S = {{ products: {json.dumps(products or {})}, productErrors: {json.dumps(errors or {})} }};
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


def _pull(result: dict) -> dict:
    """Run the #clist click handler against the Pull product data button and report what it did."""
    src = _handler(_read("takeoff.js"), '$("#clist").addEventListener("click", ')
    return _node(f"""
const calls = [], seen = [];
const btn = {{ disabled: false, textContent: 'Pull product data', dataset: {{ pull: 'c1' }} }};
const S = {{ products: {{}}, productErrors: {{}} }};
const stubs = {{
  S, btn,
  Takeoff: {{ procure: async (cid) => {{ seen.push([cid, btn.disabled, btn.textContent]); return {json.dumps(result)}; }} }},
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
                               disabled: btn.disabled, text: btn.textContent }}));
}})();
""")


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_a_successful_pull_merges_products_and_counts_them_against_the_materials():
    out = _pull({"ok": True, "data": {"products": [{"id": "p1", "url": "https://a.test/1", "title": "One"},
                                                   {"id": "p2", "url": "https://a.test/2", "title": "Two"}],
                                      "errors": {"tape": "No supplier matched this tape."}}})
    assert out["seen"] == [["c1", True, "Pulling…"]]  # off and renamed while the search runs
    assert sorted(out["products"]["c1"]) == ["p1", "p2"]  # keyed by product id
    assert out["errors"]["c1"] == {"tape": "No supplier matched this tape."}
    assert ["toast", "Found 2 of 3 products", False] in out["calls"]
    assert out["calls"][-1] == ["reload", True]  # the materials list is re-read from the server


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_a_refused_pull_says_why_and_gives_the_button_back():
    out = _pull({"ok": False, "status": 409, "data": {"detail": "Product search needs a Firecrawl key (FIRECRAWL_API_KEY in Infisical)."}})
    assert ["toast", "Product search needs a Firecrawl key (FIRECRAWL_API_KEY in Infisical).", True] in out["calls"]
    assert out["disabled"] is False and out["text"] == "Pull product data"
    assert ["reload", True] not in out["calls"]
