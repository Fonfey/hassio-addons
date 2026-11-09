"""
Gedeelde basis voor de Home Assistant-apps van Fonfey: instellingen, toegang tot
Home Assistant (REST en WebSocket, alleen standaardbibliotheek), toegangscontrole
en een eenvoudige webserver met gedeelde opmaak.
"""

import base64
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html import escape as html_escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------- instellingen
HA_OPTIONS_FILE = "/data/options.json"
HA_APP = os.path.exists(HA_OPTIONS_FILE)
OPTS = {}
if HA_APP:
    try:
        with open(HA_OPTIONS_FILE, encoding="utf-8") as _f:
            OPTS = json.load(_f)
    except Exception:
        OPTS = {}


def _read_token():
    """De Supervisor-token staat in de omgeving, of (onder s6-overlay) in de containeromgeving op schijf."""
    tok = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") or ""
    for path in ("/run/s6/container_environment/SUPERVISOR_TOKEN", "/var/run/s6/container_environment/SUPERVISOR_TOKEN",
                 "/run/s6/container_environment/HASSIO_TOKEN"):
        if tok:
            break
        try:
            with open(path, encoding="utf-8") as f:
                tok = f.read().strip()
        except OSError:
            pass
    return tok


TOKEN = _read_token()
DEMO = "--demo" in sys.argv or (not TOKEN and not HA_APP)
NO_TOKEN = HA_APP and not TOKEN
ALLOWED_USERS = [str(u).strip().lower() for u in (OPTS.get("allowed_users") or []) if str(u).strip()]
LISTEN = "0.0.0.0" if HA_APP else "127.0.0.1"
CORE = "http://supervisor/core/api"


def data_dir(fallback_file):
    d = "/data" if HA_APP else os.path.join(os.path.dirname(os.path.abspath(fallback_file)), "data")
    os.makedirs(d, exist_ok=True)
    return d


def opt_int(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(OPTS.get(name, default))))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- Home Assistant
def http_json(method, url, body=None, timeout=30):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8")
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return raw


def core(method, path, body=None, timeout=30):
    return http_json(method, CORE + path, body, timeout)


class WebSocket:
    """Minimale WebSocket-client voor de Home Assistant-API."""

    def __init__(self, host="supervisor", port=80, path="/core/websocket", timeout=30):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Geen antwoord op de WebSocket-verbinding.")
            buf += chunk
        head, self.rest = buf.split(b"\r\n\r\n", 1)
        if b" 101" not in head.split(b"\r\n")[0]:
            raise ConnectionError("WebSocket-verbinding geweigerd.")
        self.next_id = 1
        self.events = []

    def _read(self, n):
        while len(self.rest) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("WebSocket-verbinding verbroken.")
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def _send(self, opcode, payload):
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = bytes([0x80 | opcode, 0x80 | n])
        elif n < 65536:
            head = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", n)
        else:
            head = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, obj):
        self._send(1, json.dumps(obj).encode("utf-8"))

    def recv(self):
        msg = b""
        while True:
            b1, b2 = self._read(2)
            fin, opcode, length = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(length)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 9:
                self._send(10, data)
                continue
            if opcode == 8:
                raise ConnectionError("WebSocket gesloten door Home Assistant.")
            if opcode in (0, 1, 2):
                msg += data
            if fin and opcode != 10:
                return json.loads(msg.decode("utf-8"))

    def auth(self, token=None):
        first = self.recv()
        if first.get("type") == "auth_required":
            self.send({"type": "auth", "access_token": token or TOKEN})
            res = self.recv()
            if res.get("type") != "auth_ok":
                raise PermissionError("Home Assistant weigerde de toegang van de app.")
        return self

    def call(self, command, **kw):
        cid = self.next_id
        self.next_id += 1
        self.send(dict(kw, id=cid, type=command))
        while True:
            res = self.recv()
            if res.get("type") == "event":
                self.events.append(res)
                continue
            if res.get("id") == cid and res.get("type") == "result":
                if not res.get("success"):
                    raise RuntimeError((res.get("error") or {}).get("message") or command)
                return res.get("result")

    def next_event(self):
        """Volgende gebeurtenis; stuurt een ping als het lang stil is."""
        while True:
            if self.events:
                return self.events.pop(0)
            try:
                msg = self.recv()
            except socket.timeout:
                self.send({"id": self.next_id, "type": "ping"})
                self.next_id += 1
                continue
            if msg.get("type") == "event":
                return msg

    def close(self):
        try:
            self._send(8, b"")
            self.sock.close()
        except Exception:
            pass


def ws_call(command, **kw):
    ws = WebSocket().auth()
    try:
        return ws.call(command, **kw)
    finally:
        ws.close()


def listen_forever(subscriptions, on_event, on_connect=None):
    """Blijft verbonden met Home Assistant, abonneert op gebeurtenissen en roept on_event aan.
    subscriptions: lijst met event_types (None = alle gebeurtenissen)."""
    first = True
    while True:
        ws = None
        try:
            ws = WebSocket(timeout=60).auth()
            for et in subscriptions:
                ws.call("subscribe_events", **({"event_type": et} if et else {}))
            if on_connect:
                on_connect(first)
            first = False
            while True:
                ev = ws.next_event()
                try:
                    on_event(ev.get("event") or {})
                except Exception as e:
                    print(f"Gebeurtenis verwerken mislukt: {e}", flush=True)
        except Exception as e:
            if not first:
                print(f"Verbinding met Home Assistant weg ({type(e).__name__}), opnieuw verbinden.", flush=True)
            first = first and not isinstance(e, PermissionError)
        finally:
            if ws:
                ws.close()
        time.sleep(10)


# ---------------------------------------------------------------- opslag
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------- toegang
_SEEN_DENIED = set()


def user_label(headers):
    nm = (headers.get("X-Remote-User-Name") or "").strip()
    disp = (headers.get("X-Remote-User-Display-Name") or "").strip()
    if nm and disp and disp.lower() != nm.lower():
        return f"{nm} ({disp})"
    return nm or disp


def user_allowed(headers):
    if not ALLOWED_USERS:
        return True
    names = {(headers.get(h) or "").strip().lower() for h in ("X-Remote-User-Name", "X-Remote-User-Display-Name", "X-Remote-User-Id")}
    if names & set(ALLOWED_USERS):
        return True
    who = user_label(headers) or "onbekende gebruiker"
    if who not in _SEEN_DENIED:
        _SEEN_DENIED.add(who)
        print(f"Toegang geweigerd voor gebruikersnaam '{who}'.", flush=True)
    return False


# ---------------------------------------------------------------- taal (Nederlands of Engels, volgt Home Assistant)
_LANG = threading.local()


def lang():
    """Taal van het huidige verzoek: 'nl' of 'en'. De pagina stuurt die mee in de header X-Lang."""
    return getattr(_LANG, "v", "nl")


def tx(nl, en):
    """Tekst in de taal van het huidige verzoek."""
    return en if lang() == "en" else nl


def L(nl, en):
    """Tekst in beide talen, voor gegevens die op de achtergrond worden gemaakt. Wordt bij het versturen omgezet."""
    return {"__l": 1, "nl": nl, "en": en}


def resolve(obj, lg=None):
    lg = lg or lang()
    if isinstance(obj, dict):
        if obj.get("__l") == 1:
            return obj.get(lg) if obj.get(lg) is not None else obj.get("nl")
        return {k: resolve(v, lg) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve(v, lg) for v in obj]
    return obj


def _set_lang(headers, query=""):
    v = (headers.get("X-Lang") or "").lower()
    if not v and "lang=" in query:
        v = urllib.parse.parse_qs(query).get("lang", [""])[0].lower()
    _LANG.v = "en" if v and not v.startswith("nl") else "nl"


# ---------------------------------------------------------------- webserver
class BaseHandler(BaseHTTPRequestHandler):
    """Subklassen zetten PAGE, APP (voor de X-Requested-With-controle) en implementeren get_api/post_api."""
    PAGE = "<!doctype html><title>App</title>"
    APP = "app"

    def send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def font(self, name):
        """Lettertypen worden met de app meegeleverd, zodat de pagina niets van internet laadt."""
        # alleen een bestandsnaam uit de vaste lijst gebruiken, nooit de opgevraagde tekst zelf
        fname = next((f for f in sorted(FONT_FILES) if f == name), None)
        if fname is None:
            self.send(404, "", "text/plain")
            return
        with open(os.path.join(FONT_DIR, fname), "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "font/woff2")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    def json(self, obj, code=200):
        self.send(code, json.dumps(resolve(obj), ensure_ascii=False))

    def do_GET(self):
        _set_lang(self.headers, urllib.parse.urlparse(self.path).query)
        if not user_allowed(self.headers):
            api = "/api/" in self.path
            self.send(403, json.dumps({"error": tx("Geen toegang.", "No access.")}) if api else "<h1>Geen toegang / No access</h1>",
                      "application/json" if api else "text/html; charset=utf-8")
            return
        url = urllib.parse.urlparse(self.path)
        if url.path.startswith("/fonts/"):
            self.font(url.path[len("/fonts/"):])
            return
        if url.path in ("/", "/index.html"):
            who = user_label(self.headers)
            foot = f'<p class="who"><span data-en="Signed in as">Ingelogd als</span> <b>{html_escape(who)}</b></p>' if who else ""
            self.send(200, self.PAGE.replace("<!--FOOT-->", foot), "text/html; charset=utf-8")
            return
        try:
            self.get_api(url.path, urllib.parse.parse_qs(url.query))
        except Exception as e:
            self.json({"error": str(e)}, 500)

    def do_POST(self):
        _set_lang(self.headers)
        if not user_allowed(self.headers):
            self.json({"error": tx("Geen toegang.", "No access.")}, 403)
            return
        if self.headers.get("X-Requested-With") != self.APP:
            self.json({"error": tx("Ongeldig verzoek.", "Invalid request.")}, 400)
            return
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 2_000_000)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            body = {}
        try:
            self.post_api(urllib.parse.urlparse(self.path).path, body)
        except Exception as e:
            self.json({"error": str(e)}, 500)

    def get_api(self, path, query):
        self.json({"error": tx("Niet gevonden.", "Not found.")}, 404)

    def post_api(self, path, body):
        self.json({"error": tx("Niet gevonden.", "Not found.")}, 404)

    def log_message(self, *args):
        pass


def serve(handler, port_ha=8099, port_local=8090):
    port = port_ha if HA_APP else int(os.environ.get("PORT", str(port_local)))
    srv = ThreadingHTTPServer((LISTEN, port), handler)
    print(f"Luistert op http://{LISTEN}:{port}" + (" (voorbeeldgegevens)" if DEMO else ""), flush=True)
    srv.serve_forever()


def start_thread(fn, *args):
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    return t


# Lettertype IBM Plex (SIL Open Font License 1.1), meegeleverd in de map fonts/.
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_FILES = {"ibm-plex-sans-400.woff2", "ibm-plex-sans-500.woff2", "ibm-plex-sans-600.woff2",
              "ibm-plex-mono-400.woff2", "ibm-plex-mono-500.woff2"}
FONT_CSS = "".join(
    f'@font-face{{font-family:"IBM Plex {fam.title()}";font-style:normal;font-weight:{w};font-display:swap;src:url("fonts/ibm-plex-{fam}-{w}.woff2") format("woff2")}}\n'
    for fam, ws in (("sans", (400, 500, 600)), ("mono", (400, 500))) for w in ws)

# Gedeelde opmaak: dezelfde kleuren als Onderhoud, licht en donker.
CSS = r"""
:root{--bg:#EDF1F6;--surface:#fff;--sunk:#F5F7FA;--line:#D5DDE8;--line2:#E4E9F0;--text:#14213A;--muted:#5C6C84;--faint:#7D8CA1;
  --accent:#0B978B;--accentText:#fff;--sel:#E4F3F1;--selLine:#0B978B;--ok:#1F8A57;--okbg:#E7F6EE;--warn:#9A5A10;--warnbg:#FCEFDF;
  --bad:#B33232;--badbg:#FBE7E7;--info:#2B6BA6;--infobg:#E6EFF8;--purple:#6B4BB8;--purplebg:#EEE8FA;--link:#2F7CC0;
  --addbg:rgba(31,138,87,.10);--addtx:#17663F;--delbg:rgba(179,50,50,.10);--deltx:#8E2626}
@media (prefers-color-scheme: dark){:root{--bg:#0E1622;--surface:#152030;--sunk:#0E1622;--line:#233249;--line2:#1C293C;--text:#E4EBF5;--muted:#93A3BA;--faint:#7486A0;
  --accent:#3CC5B6;--accentText:#0E1622;--sel:#1B2A3E;--selLine:#3CC5B6;--ok:#4CC787;--okbg:#12241F;--warn:#F0A257;--warnbg:#3A2A1E;
  --bad:#F58A8A;--badbg:#3D2324;--info:#9BC8F0;--infobg:#1E2D45;--purple:#CDB8F5;--purplebg:#2E2640;--link:#69ACE6;
  --addbg:rgba(76,199,135,.10);--addtx:#A8E6C4;--delbg:rgba(245,138,138,.10);--deltx:#F7B5B5}}
*{box-sizing:border-box}
html{color-scheme:light dark;scrollbar-color:var(--line) transparent}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
button,input,select{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--link);outline-offset:2px;border-radius:6px}
a{color:var(--link)}
.wrap{max-width:1160px;margin:0 auto;padding:32px 16px 48px;display:flex;flex-direction:column;gap:18px}
h1{margin:0;font-size:28px;font-weight:600;letter-spacing:-.01em}
h2{margin:0;font-size:17px;font-weight:600}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap}
.sum{margin:6px 0 0;color:var(--muted);font-size:15px}
.meta{color:var(--faint);font-size:13px;text-align:right}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px}
.pad{padding:16px 18px}
.note{border:1px solid var(--line);background:var(--surface);border-radius:12px;padding:12px 16px;font-size:14px;color:var(--muted)}
.note.err{border-color:var(--bad);color:var(--text)}
[hidden]{display:none!important}
.row2{display:flex;flex-wrap:wrap;gap:18px;align-items:flex-start}
.side{flex:1 1 320px;max-width:420px;min-width:0}
.main{flex:999 1 560px;min-width:0;display:flex;flex-direction:column;gap:16px}
.field{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--muted)}
.field input,.field select{font-size:14px;background:var(--sunk);border:1px solid var(--line);border-radius:9px;padding:9px 12px;color:var(--text)}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{border:1px solid var(--line);background:transparent;color:var(--muted);border-radius:999px;padding:6px 12px;font-size:13px;cursor:pointer}
.chip[aria-pressed="true"]{border-color:var(--selLine);background:var(--sel);color:var(--text)}
.list{list-style:none;margin:0;padding:0}
.list>li{border-top:1px solid var(--line2)}
.list>li:first-child{border-top:0}
.pick{width:100%;text-align:left;border:0;background:transparent;cursor:pointer;padding:12px 16px;display:block}
.pick:hover{background:var(--sel)}
.pick[aria-current="true"]{background:var(--sel);box-shadow:inset 3px 0 0 var(--selLine)}
.tag{display:inline-block;font-size:12px;font-weight:600;padding:2px 9px;border-radius:999px;white-space:nowrap}
.t-info{background:var(--infobg);color:var(--info)} .t-ok{background:var(--okbg);color:var(--ok)} .t-warn{background:var(--warnbg);color:var(--warn)}
.t-bad{background:var(--badbg);color:var(--bad)} .t-muted{background:var(--line2);color:var(--muted)} .t-purple{background:var(--purplebg);color:var(--purple)}
.btn{border:1px solid var(--line);background:transparent;border-radius:9px;padding:9px 14px;font-size:14px;cursor:pointer;text-decoration:none;color:var(--text);display:inline-block}
.btn:hover{border-color:var(--muted)}
.btn.primary{border:0;background:var(--accent);color:var(--accentText);font-weight:600}
.btn:disabled{opacity:.5;cursor:default}
.muted{color:var(--muted)} .faint{color:var(--faint)} .small{font-size:13px}
.kv{display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px 14px;margin:0;font-size:14px}
.kv dt{color:var(--muted)} .kv dd{margin:0;overflow-wrap:anywhere}
.empty{padding:28px 18px;text-align:center;color:var(--muted)}
.who{color:var(--faint);font-size:13px;margin:8px 0 0}
.toast{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--ok);border-radius:12px;padding:10px 16px;font-size:14px;z-index:6;max-width:calc(100% - 32px)}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:grid;place-items:center;padding:16px;z-index:5}
.modal .box{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px;max-width:440px;width:100%}
.modal h3{margin:0 0 6px;font-size:17px}
.modal p{margin:0 0 16px;color:var(--muted);font-size:14px}
.modal .acts{display:flex;gap:10px;justify-content:flex-end}
"""

JS_COMMON = r"""
// Taal volgt Home Assistant: Nederlands als HA Nederlands is, anders Engels. ?lang=en of ?lang=nl overschrijft.
const LANG = (() => { const q = new URLSearchParams(location.search).get('lang'); if (q) return q.toLowerCase().startsWith('nl') ? 'nl' : 'en';
  let l = ''; try { if (window.top !== window) l = window.top.document.documentElement.lang || ''; } catch (e) {}
  if (!l) { try { l = JSON.parse(window.top.localStorage.getItem('selectedLanguage') || 'null') || ''; } catch (e) {} }
  if (!l) l = navigator.language || 'en';
  return String(l).toLowerCase().startsWith('nl') ? 'nl' : 'en'; })();
const t = (nl, en) => LANG === 'en' ? en : nl;
const LOCALE = t('nl-NL', 'en-GB');
document.documentElement.lang = LANG;
function i18n(root){ if (LANG !== 'en') return; const r = root || document;
  r.querySelectorAll('[data-en]').forEach(el => { el.textContent = el.dataset.en; });
  r.querySelectorAll('[data-en-html]').forEach(el => { el.innerHTML = el.dataset.enHtml; });
  for (const a of ['placeholder', 'aria-label', 'title']) r.querySelectorAll(`[data-en-${a}]`).forEach(el => el.setAttribute(a, el.getAttribute(`data-en-${a}`))); }
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let toastT;
function toast(msg, bad){ const t = $('#toast'); t.textContent = msg; t.style.borderLeftColor = bad ? 'var(--bad)' : 'var(--ok)'; t.hidden = false; clearTimeout(toastT); toastT = setTimeout(() => t.hidden = true, 3500); }
async function api(path){ const r = await fetch(path, {headers: {'X-Lang': LANG}}); const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); return j; }
async function post(path, body){ const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': APP, 'X-Lang': LANG}, body: JSON.stringify(body || {})});
  const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || r.statusText); return j; }
function confirmBox(title, text, ok){ return new Promise(res => { const m = $('#modal'); $('#mt').textContent = title; $('#mx').textContent = text; $('#mok').textContent = ok || t('Doorgaan', 'Continue'); m.hidden = false;
  const done = v => { m.hidden = true; res(v); }; $('#mok').onclick = () => done(true); $('#mno').onclick = () => done(false); m.onclick = e => { if (e.target === m) done(false); }; $('#mok').focus(); }); }
const HA = p => { try { return (window.top !== window ? window.top.location.origin : '') + p; } catch (e) { return p; } };
function when(ts){ const d = new Date(ts * 1000), now = new Date(), y = new Date(now); y.setDate(now.getDate() - 1);
  const hm = d.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'});
  if (d.toDateString() === now.toDateString()) return t('vandaag ', 'today ') + hm;
  if (d.toDateString() === y.toDateString()) return t('gisteren ', 'yesterday ') + hm;
  return d.toLocaleDateString(LOCALE, {day: 'numeric', month: 'long'}) + ' ' + hm; }
i18n();
"""

SHELL_TAIL = r"""<div class="toast" id="toast" role="status" hidden></div>
<div class="modal" id="modal" hidden><div class="box" role="dialog" aria-modal="true" aria-labelledby="mt"><h3 id="mt"></h3><p id="mx"></p>
<div class="acts"><button class="btn" id="mno" data-en="Cancel">Annuleren</button><button class="btn primary" id="mok" data-en="Continue">Doorgaan</button></div></div></div>"""


def page(title, body_html, css="", js=""):
    return ("<!doctype html>\n<html lang=\"nl\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
            f"<title>{title}</title>\n"
            f"<style>{FONT_CSS}{CSS}{css}</style>\n</head>\n<body>\n{body_html}\n{SHELL_TAIL}\n"
            f"<script>{JS_COMMON}{js}</script>\n</body>\n</html>")
