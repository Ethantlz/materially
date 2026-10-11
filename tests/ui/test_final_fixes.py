"""Static checks for the final review fix wave: JS-only behaviour that has no route to call, so the source is read."""
import json
import re
from pathlib import Path

import pytest

from tests.ui.test_popups_js import MODALS, NODE, _node

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ui" / "static"


def _read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_popups_read_the_alternate_from_the_dropdown_not_from_takeoff_js_S():
    """takeoff.js's `S` is a top-level const of another classic script: modals.js can't see it, so the popups
    were always asked for ?alt= empty (= Base bid) and ignored the selected alternate."""
    js = _read("ui", "static", "modals.js")
    code = re.sub(r"//[^\n]*", "", js)
    assert 'getElementById("alt-select")' in code
    assert "typeof S" not in code and "S.altId" not in code
    assert re.search(r'const alt = \(\) => document\.getElementById\("alt-select"\)\?\.value \|\| ""', code)


def test_order_popup_lines_table_gets_the_full_dialog_width():
    css = _read("ui", "static", "app.css")
    assert re.search(r"dialog\.modal \.split\s*\{[^}]*grid-template-columns:\s*1fr", css)
    assert re.search(r"dialog\.modal #lines\s*\{[^}]*overflow-x:\s*auto", css)


def test_the_selects_release_focus_after_a_change():
    """A <select> that keeps focus swallows the L/A/C/V, Ctrl+Z and Esc shortcuts and type-aheads into itself."""
    js = _read("ui", "static", "takeoff.js")
    for sel in ('$("#sheet-select").addEventListener("change", ', '$("#alt-select").addEventListener("change", '):
        i = js.index(sel)
        head = js[i + len(sel): i + len(sel) + 200]
        assert "e.target.blur()" in head, f"{sel} must blur the select first"


def test_scale_menu_is_positioned_against_the_viewport_not_the_hidden_menus_offset_parent():
    js = _read("ui", "static", "takeoff.js")
    body = js[js.index("function showScaleMenu()"): js.index("async function chooseScale")]
    assert "offsetParent" not in body
    assert "vp.getBoundingClientRect()" in body
    assert "m.offsetWidth" in body  # clamped by the menu's real width, measured once it is shown


def test_empty_state_points_at_the_sheet_dropdown_not_a_sheets_tab():
    js = _read("ui", "static", "takeoff.js")
    assert "Sheets tab" not in js


def test_drawer_orders_link_opens_the_popup_when_there_is_one():
    html = _read("ui", "templates", "takeoff.html")
    m = re.search(r'<a [^>]*id="drawer-orders"[^>]*>', html)
    assert m and 'href="/jobs/{{ job.id }}/orders"' in m.group(0)  # the full-page link stays as the fallback
    js = _read("ui", "static", "modals.js")
    assert "drawer-orders" in js and "open-orders" in js


def test_a_failed_product_search_is_written_into_the_material_row():
    js = _read("ui", "static", "takeoff.js")
    assert 'data-testid="material-error"' in js
    handler = js[js.index('const f = e.target.closest("[data-find]")'):]
    handler = handler[: handler.index("const src = e.target.closest")]
    fail = handler[handler.index("if (!r.ok)"): handler.index("S.products[cid]")]
    assert "material-error" in fail and "toast(" in fail  # the row says it, and the toast still fires
    # a new attempt clears the old message before it runs
    assert "material-error" in handler[: handler.index("if (!r.ok)")]


def test_toast_lands_inside_an_open_dialog():
    js = _read("ui", "static", "api.js")
    body = js[js.index("window.toast = "): js.index("// ---- the job-cost badge")]
    assert 'document.querySelector("dialog[open]") || document.body' in body
    assert "appendChild" in body


def test_no_unused_accent_text_alias():
    for p in STATIC.iterdir():
        if p.suffix in (".css", ".js"):
            assert "accent-text" not in p.read_text(encoding="utf-8"), p.name
    for p in (ROOT / "ui" / "templates").iterdir():
        assert "accent-text" not in p.read_text(encoding="utf-8"), p.name


# ---- modals.js run for real (Node): the popups follow the dropdown, and the drawer link opens the Orders popup ----
@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_orders_and_tracking_popups_ask_for_the_selected_alternate():
    stub = MODALS.replace("getElementById: (id) => (id === 'modal' ? dlg : null)",
                          "getElementById: (id) => (id === 'modal' ? dlg : (els[id] || null))")
    assert stub != MODALS
    script = ("const els = { 'alt-select': { value: 'alt42' }, 'open-orders': {}, 'open-tracking': {}, 'drawer-orders': {} };\n"
              + stub.replace("MODALS_PATH", json.dumps(str(STATIC / "modals.js")))
              + """(async () => {
      const urls = [];
      global.fetch = async (url) => { urls.push(url); return page('<p>x</p>'); };
      await els['open-orders'].onclick();
      await els['open-tracking'].onclick();
      els['alt-select'].value = '';  // Base bid
      await els['open-orders'].onclick();
      let prevented = false;
      els['open-orders'].click = () => els['open-orders'].onclick();
      await els['drawer-orders'].onclick({ preventDefault: () => { prevented = true; } });
      console.log(JSON.stringify({ urls, prevented }));
    })();""")
    out = _node(script)
    assert out["urls"] == ["/ui/jobs/j1/panel/orders?alt=alt42", "/ui/jobs/j1/panel/tracking?alt=alt42",
                           "/ui/jobs/j1/panel/orders?alt=", "/ui/jobs/j1/panel/orders?alt="]
    assert out["prevented"] is True
