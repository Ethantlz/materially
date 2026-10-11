"""The takeoff popups' JS: the screen's shortcuts and Create order while a dialog is open, and modals.js itself.

takeoff.js is one script bound to the page, so the handlers under test are cut out of it and run in Node. Every bare
name such a handler uses resolves through a proxy: a stub when the test gives one, else a recorder that logs the call.
modals.js runs whole against a stub dialog."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ui" / "static"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node isn't installed")


def _node(script: str):
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _handler(js: str, start: str) -> str:
    """The source of the arrow function passed to `start`(...), up to its closing `});` at column 0."""
    i = js.index(start) + len(start)
    return js[i:js.index("\n});", i) + 2]


def _run_handler(src: str, stubs_js: str, body_js: str):
    """Evaluate `src` (an arrow function) inside the recorder scope, then run body_js, which prints a JSON result."""
    return _node(f"""
const calls = [];
const stubs = {{ vp: {{ classList: {{ add: () => calls.push('vp.add'), remove: () => calls.push('vp.remove') }} }}, ...({stubs_js}) }};
const scope = new Proxy({{}}, {{
  has: (_, n) => typeof n === 'string' && (n in stubs || !(n in globalThis)),
  get: (_, n) => (n in stubs ? stubs[n] : () => {{ calls.push(n); }}),
}});
const handler = new Function('scope', {json.dumps('with (scope) { return (' + src + '); }')})(scope);
{body_js}
""")


def _keydown(dialog_open: bool) -> dict:
    src = _handler((STATIC / "takeoff.js").read_text(encoding="utf-8"), 'document.addEventListener("keydown", ')
    stubs = f"""{{
      S: {{ draft: null, conds: [{{ id: 'c1' }}], shift: false, space: false, cur: null, k: 1 }},
      $: () => ({{ hidden: true }}), vpRect: () => ({{ width: 100, height: 100 }}), snap: (p) => p,
      document: {{ querySelector: (s) => ({str(dialog_open).lower()} && s === 'dialog[open]' ? {{}} : null) }},
    }}"""
    return _run_handler(src, stubs, """
const press = (key, over = {}, tag = 'BODY') => {
  calls.length = 0;
  handler({ key, ctrlKey: false, metaKey: false, altKey: false, preventDefault: () => calls.push('preventDefault'),
    target: { tagName: tag, isContentEditable: false, classList: { contains: () => false }, blur: () => calls.push('blur') }, ...over });
  return calls.slice();
};
const out = {};
for (const k of ['Delete', 'Backspace', 'Escape', 'Enter', ' ', 'l', 'a', 'c', 'v', 'k', 'f', 'q', '?', '+', '-', '1']) out[k] = press(k);
out['space on a button'] = press(' ', {}, 'BUTTON');
out['ctrl+z'] = press('z', { ctrlKey: true });
out.shift = press('Shift');
out.shiftHeld = stubs.S.shift;
console.log(JSON.stringify(out));
""")


def test_shortcuts_pause_while_a_dialog_is_open():
    out = _keydown(dialog_open=True)
    shift_held = out.pop("shiftHeld")
    assert {key: calls for key, calls in out.items() if calls} == {}, "no shortcut may act, or swallow a key, behind a dialog"
    assert shift_held is True  # the Shift bookkeeping still runs, so a draft's snapping isn't left stale


def test_shortcuts_still_work_with_no_dialog():
    out = _keydown(dialog_open=False)
    assert out["Delete"] == ["preventDefault", "deleteSelected"]
    assert out["Escape"] == ["onEscape"] and out["Enter"] == ["finishDraft"]
    assert out[" "] == ["vp.add", "preventDefault"] and out["space on a button"] == ["blur", "vp.add", "preventDefault"]
    assert out["l"] == ["setTool"] and out["f"] == ["fit"] and out["1"] == ["setCond"]
    assert out["ctrl+z"] == ["preventDefault", "undo"]


@pytest.mark.parametrize("dialog_open,expected", [(True, ["vp.remove"]), (False, ["vp.remove", "preventDefault"])])
def test_space_keyup_does_not_cancel_a_dialog_button(dialog_open, expected):
    """A button is pressed with Space on keyup; the screen's keyup handler preventDefaults that, which would cancel Close / Send PO."""
    src = _handler((STATIC / "takeoff.js").read_text(encoding="utf-8"), 'document.addEventListener("keyup", ')
    stubs = f"""{{ S: {{ shift: false, space: true }},
      document: {{ activeElement: {{ tagName: 'BUTTON' }}, querySelector: (s) => ({str(dialog_open).lower()} && s === 'dialog[open]' ? {{}} : null) }} }}"""
    out = _run_handler(src, stubs, """
handler({ key: ' ', preventDefault: () => calls.push('preventDefault') });
console.log(JSON.stringify(calls));
""")
    assert out == expected  # space-pan state is always cleared; the key is only swallowed when no dialog is open


def _create_order(modal: bool, result: dict) -> dict:
    src = _handler((STATIC / "takeoff.js").read_text(encoding="utf-8"), '$("#qty-body").addEventListener("click", ')
    stubs = f"""{{
      S: {{ job: {{ id: 'j1' }}, altId: 'a1', qty: new Map() }}, cond: () => ({{ height_ft: 10 }}), running: () => ({{ lf: 12.34 }}),
      Takeoff: {{ createOrder: async () => {{ seen.push([btn.disabled, btn.textContent]); return {json.dumps(result)}; }} }},
      errText: () => 'nope', toast: (m) => calls.push('toast:' + m), location: loc,
      window: {{ Modal: {"modal" if modal else "undefined"} }}, Modal: {"modal" if modal else "undefined"},
    }}"""
    return _run_with_btn(src, stubs)


def _run_with_btn(src: str, stubs: str) -> dict:
    # unlike _run_handler, the stubs here refer to btn / seen / loc / modal, so those are declared ahead of them
    return _node(f"""
const calls = [], seen = [], loc = {{ href: '' }};
const btn = {{ disabled: false, textContent: 'Create order', dataset: {{ order: 'c1' }} }};
const modal = {{ open: (...a) => calls.push(['open', ...a]) }};
const stubs = {stubs};
const scope = new Proxy({{}}, {{
  has: (_, n) => typeof n === 'string' && (n in stubs || !(n in globalThis)),
  get: (_, n) => (n in stubs ? stubs[n] : () => {{ calls.push(n); }}),
}});
const handler = new Function('scope', {json.dumps('with (scope) { return (' + src + '); }')})(scope);
(async () => {{
  await handler({{ target: {{ closest: () => btn }} }});
  console.log(JSON.stringify({{ seen, calls, loc: loc.href, disabled: btn.disabled, text: btn.textContent }}));
}})();
""")


def test_create_order_restores_the_button_when_the_popup_opens():
    out = _create_order(modal=True, result={"ok": True, "orderId": "o1"})
    assert out["seen"] == [[True, "Creating…"]]  # it is disabled while the order is built
    assert ["open", "Order", "/ui/orders/o1/panel"] in out["calls"] and out["loc"] == ""
    assert out["disabled"] is False and out["text"] == "Create order"  # and usable again behind the popup


def test_create_order_without_modals_js_falls_back_to_the_order_page():
    out = _create_order(modal=False, result={"ok": True, "orderId": "o1"})
    assert out["loc"] == "/orders/o1" and not any(isinstance(c, list) and c[0] == "open" for c in out["calls"])


def test_create_order_restores_the_button_after_a_failure():
    out = _create_order(modal=True, result={"ok": False, "status": 500, "data": {}})
    assert "toast:nope" in out["calls"] and out["disabled"] is False and out["text"] == "Create order"


# ---- modals.js ----
MODALS = """
const listeners = {}, doc = {};
const h2 = { textContent: '' };
const body = { innerHTML: '', inits: 0, querySelectorAll: (sel) => { if (sel === 'script[data-init]') body.inits++; return []; } };
const dlg = { open: false, showModal() { this.open = true; },
  close() { this.open = false; (listeners.close || []).forEach((f) => f()); },
  addEventListener: (ev, fn) => (listeners[ev] = listeners[ev] || []).push(fn),
  querySelector: (s) => ({ 'h2': h2, '.modal-body': body, '[data-testid=modal-close]': { addEventListener() {} } }[s] || null) };
global.window = global;
global.document = { getElementById: (id) => (id === 'modal' ? dlg : null), addEventListener: (ev, fn) => (doc[ev] = doc[ev] || []).push(fn), dispatchEvent() {} };
global.CustomEvent = class { constructor(t) { this.type = t; } };
global.JOB_ID = 'j1';
const deferred = () => { let resolve; const p = new Promise((r) => { resolve = r; }); return { p, resolve }; };
const page = (html, ok = true, status = 200) => ({ ok, status, text: async () => html });
require(MODALS_PATH);
"""


def _modals(scenario: str):
    return _node(MODALS.replace("MODALS_PATH", json.dumps(str(STATIC / "modals.js"))) + "(async () => {\n" + scenario + "\n})();")


def test_modal_shows_a_message_when_the_network_fails():
    out = _modals("""
      global.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
      await Modal.open('Tracking', '/ui/jobs/j1/panel/tracking');
      console.log(JSON.stringify({ html: body.innerHTML, open: dlg.open }));
    """)
    assert "Couldn't load this" in out["html"] and out["open"] is True  # not left saying "Loading…"


def test_modal_keeps_the_http_status_in_its_message():
    out = _modals("""
      global.fetch = async () => page('', false, 404);
      await Modal.open('Order', '/ui/orders/zzz/panel');
      console.log(JSON.stringify({ html: body.innerHTML }));
    """)
    assert "Couldn't load this" in out["html"] and "404" in out["html"]


def test_modal_ignores_a_response_once_a_newer_open_has_started():
    out = _modals("""
      const slow = deferred();
      global.fetch = (url) => (url === '/slow' ? slow.p : Promise.resolve(page('<p>second</p>')));
      const first = Modal.open('First', '/slow');
      await Modal.open('Second', '/fast');
      slow.resolve(page('<p>first</p>'));
      await first;
      console.log(JSON.stringify({ html: body.innerHTML, title: h2.textContent, inits: body.inits }));
    """)
    assert out["html"] == "<p>second</p>" and out["title"] == "Second"
    assert out["inits"] == 1  # only the second panel's scripts ran


def test_modal_ignores_a_response_that_arrives_after_it_was_closed():
    out = _modals("""
      const slow = deferred();
      global.fetch = () => slow.p;
      const p = Modal.open('EWOs', '/slow');
      Modal.close();
      slow.resolve(page('<p>late</p>'));
      await p;
      console.log(JSON.stringify({ html: body.innerHTML, inits: body.inits }));
    """)
    assert "late" not in out["html"] and out["inits"] == 0  # a closed dialog starts no live feed


def test_modal_link_with_data_modal_open_opens_in_the_dialog():
    out = _modals("""
      const urls = [];
      global.fetch = async (url) => { urls.push(url); return page('<p>list</p>'); };
      const link = { dataset: { modalOpen: '/ui/jobs/j1/panel/orders?alt=a1', modalTitle: 'Material orders' } };
      let prevented = false;
      const e = { target: { closest: (sel) => (sel === '[data-modal-open]' ? link : null) }, preventDefault: () => { prevented = true; } };
      doc.click.forEach((f) => f(e));
      await new Promise((r) => setTimeout(r, 0));
      console.log(JSON.stringify({ urls, title: h2.textContent, html: body.innerHTML, prevented }));
    """)
    assert out["urls"] == ["/ui/jobs/j1/panel/orders?alt=a1"] and out["title"] == "Material orders"
    assert out["html"] == "<p>list</p>" and out["prevented"] is True
