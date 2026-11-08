#!/usr/bin/env python3
"""
Onderhoud voor Home Assistant
=============================

Controleert elk uur wat aandacht nodig heeft in je Home Assistant en zet het in
één lijst, op volgorde van belang: back-ups, reparaties, updates, offline
apparaten, lege batterijen, gestopte apps, automations die elkaar in de weg
zitten of nooit draaien, en verweesde entiteiten.

Draait als Home Assistant-app (gebruikt de Supervisor-token, geen API-key nodig).
Los starten zonder Home Assistant toont voorbeeldgegevens:  python3 maintenance.py --demo
"""

import base64
import hashlib
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
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------- instellingen
HA_OPTIONS_FILE = "/data/options.json"
HA_APP = os.path.exists(HA_OPTIONS_FILE)
_OPTS = {}
if HA_APP:
    try:
        with open(HA_OPTIONS_FILE, encoding="utf-8") as _f:
            _OPTS = json.load(_f)
    except Exception:
        _OPTS = {}

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
INTERVAL = int(_OPTS.get("check_interval_minutes", 60)) * 60
BATTERY_MIN = int(_OPTS.get("battery_threshold", 20))
DISABLED_DAYS = int(_OPTS.get("disabled_days", 30))
STALE_DAYS = int(_OPTS.get("stale_days", 7))
ALLOWED_USERS = [str(u).strip().lower() for u in (_OPTS.get("allowed_users") or []) if str(u).strip()]
LISTEN = "0.0.0.0" if HA_APP else "127.0.0.1"
PORT = 8099 if HA_APP else int(os.environ.get("PORT", "8082"))
DATA_DIR = "/data" if HA_APP else os.path.dirname(os.path.abspath(__file__))
IGNORE_FILE = os.path.join(DATA_DIR, "onderhoud_genegeerd.json")

CORE = "http://supervisor/core/api"
SUPERVISOR = "http://supervisor"
CONFLICT_DOMAINS = {"climate", "cover", "fan", "light", "media_player", "switch", "lock",
                    "water_heater", "humidifier", "vacuum", "valve"}
LOCAL_AGENTS = {"hassio.local", "backup.local"}

LOCK = threading.Lock()
STATE = {"items": [], "ok": [], "checked": None, "attempt": 0, "error": None, "running": False, "demo": DEMO,
         "tijdmachine": None, "tmChecked": False}
SETTLE = 120  # seconden wachten na het opstarten van Home Assistant, zodat apparaten weer verbinding hebben
OPS = set()  # acties die de laatste controle heeft toegestaan, bv. ("remove_entity", "sensor.x")
IGNORED = set()


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


def supervisor(path, method="GET"):
    res = http_json(method, SUPERVISOR + path)
    return res.get("data", res) if isinstance(res, dict) else res


class WebSocket:
    """Minimale WebSocket-client (alleen standaardbibliotheek) voor de Home Assistant-API."""

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
        self.events = []  # gebeurtenissen die binnenkwamen terwijl call() op een antwoord wachtte

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

    def auth(self, token):
        first = self.recv()
        if first.get("type") == "auth_required":
            self.send({"type": "auth", "access_token": token})
            res = self.recv()
            if res.get("type") != "auth_ok":
                raise PermissionError("Home Assistant weigerde de toegang van de app.")

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

    def close(self):
        try:
            self._send(8, b"")
            self.sock.close()
        except Exception:
            pass


TEMPLATE = r"""{%- set mob = integration_entities('mobile_app') -%}
{%- set ns = namespace(e=[], b=[]) -%}
{%- for s in states -%}
{%- set d = device_id(s.entity_id) -%}
{%- set r = (s.attributes.restored is defined and s.attributes.restored == true) -%}
{%- if d or r -%}{%- set ns.e = ns.e + [[s.entity_id, d, s.state, r, s.name]] -%}{%- endif -%}
{%- if s.domain == 'sensor' and s.attributes.device_class is defined and s.attributes.device_class == 'battery' and is_number(s.state) and s.entity_id not in mob -%}{%- set ns.b = ns.b + [[s.entity_id, s.name, s.state | float, d]] -%}{%- endif -%}
{%- endfor -%}
{%- set devs = ns.e | map(attribute='1') | reject('none') | unique | list -%}
{%- set nd = namespace(m={}) -%}
{%- for d in devs -%}{%- set nd.m = dict(nd.m, **{d: [device_attr(d, 'name_by_user') or device_attr(d, 'name'), area_name(d)]}) -%}{%- endfor -%}
{{ {'e': ns.e, 'b': ns.b, 'd': nd.m} | tojson }}"""


# ---------------------------------------------------------------- hulpjes
def short_hash(values):
    return hashlib.sha1("|".join(sorted(map(str, values))).encode()).hexdigest()[:10]


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def L(nl, en):
    """Tekst in beide talen; wordt bij het versturen omgezet naar de taal van de gebruiker."""
    return {"__l": 1, "nl": nl, "en": en}


def resolve(obj, lg):
    if isinstance(obj, dict):
        if obj.get("__l") == 1:
            return obj.get(lg) if obj.get(lg) is not None else obj.get("nl")
        return {k: resolve(v, lg) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve(v, lg) for v in obj]
    return obj


def nfmt(tpl, n):
    """Vul {n} in, ook in tekst met twee talen."""
    if isinstance(tpl, dict) and tpl.get("__l") == 1:
        return L(tpl["nl"].replace("{n}", str(n)), tpl["en"].replace("{n}", str(n)))
    return str(tpl).replace("{n}", str(n))


def join_l(parts, sep=", "):
    return L(sep.join(str(resolve(p, "nl")) for p in parts), sep.join(str(resolve(p, "en")) for p in parts))


def ago(dt):
    if not dt:
        return L("nooit", "never")
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 3600:
        m = max(1, int(secs // 60))
        return L(f"{m} min geleden", f"{m} min ago")
    if secs < 86400:
        h = int(secs // 3600)
        return L(f"{h} uur geleden", f"{h} {'hour' if h == 1 else 'hours'} ago")
    days = int(secs // 86400)
    return L("gisteren", "yesterday") if days == 1 else L(f"{days} dagen geleden", f"{days} days ago")


FLIP_DAYS = 7
FLIP_MIN = 90
STATE_NL = {"on": "aan", "off": "uit", "heat": "verwarmen", "cool": "koelen", "auto": "automatisch", "heat_cool": "verwarmen/koelen",
            "dry": "drogen", "fan_only": "ventileren", "open": "open", "closed": "dicht", "locked": "op slot", "unlocked": "van het slot",
            "playing": "speelt", "paused": "gepauzeerd", "idle": "inactief"}
STATE_EN = {"on": "on", "off": "off", "heat": "heat", "cool": "cool", "auto": "auto", "heat_cool": "heat/cool", "dry": "dry",
            "fan_only": "fan only", "open": "open", "closed": "closed", "locked": "locked", "unlocked": "unlocked",
            "playing": "playing", "paused": "paused", "idle": "idle"}


def flip_flops(target, automations):
    """Momenten waarop de ene automation het apparaat schakelde en kort daarna een andere het terugdraaide."""
    start = (datetime.now(timezone.utc) - timedelta(days=FLIP_DAYS)).isoformat()
    try:
        rows = http_json("GET", f"{CORE}/logbook/{urllib.parse.quote(start)}?entity={urllib.parse.quote(target)}", timeout=40) or []
    except Exception as e:
        print(f"Logboek van {target} niet te lezen: {e}", flush=True)
        return []
    seq = []
    for r in rows:
        if r.get("entity_id") != target or r.get("state") in (None, "unavailable", "unknown"):
            continue
        src = r.get("context_entity_id") if r.get("context_event_type") == "automation_triggered" else None
        t = parse_time(r.get("when"))
        if t:
            seq.append((t, r["state"], src))
    seq.sort(key=lambda x: x[0])
    out = []
    for (ta, sa, a), (tb, sb, b) in zip(seq, seq[1:]):
        if a in automations and b in automations and a != b and sa != sb and (tb - ta).total_seconds() <= FLIP_MIN * 60:
            out.append({"a": a, "b": b, "ta": ta, "tb": tb, "sa": L(STATE_NL.get(sa, sa), STATE_EN.get(sa, sa)), "sb": L(STATE_NL.get(sb, sb), STATE_EN.get(sb, sb))})
    return sorted(out, key=lambda f: f["ta"], reverse=True)


def local(dt):
    return dt.astimezone().strftime("%d-%m %H:%M") if dt else "–"


def link(label, path):
    return {"label": label, "kind": "link", "href": path}


def item(iid, sev, title, why, details=None, actions=None, one=None, many=None):
    it = {"id": iid, "sev": sev, "title": title, "why": why, "details": details or [], "actions": actions or []}
    if one and many:
        it.update(one=one, many=many)
    return it


def counted(iid, sev, one, many, why, rows, actions):
    """Een punt waarvan de titel het aantal regels noemt; regels zijn los te negeren."""
    n = len(rows)
    return item(iid, sev, nfmt(one if n == 1 else many, n), why, rows, actions, one, many)


def row(k, v, rid=None, href=None, acts=None):
    r = {"k": k, "v": v}
    if rid:
        r["rid"] = rid
    if href:
        r["href"] = href
    if acts:
        r["acts"] = acts
    return r


def ent_href(eid):
    return "/history?entity_id=" + urllib.parse.quote(eid)


def visible_items(items, ignored):
    """Wat de gebruiker nog ziet: zonder genegeerde punten en genegeerde regels."""
    out = []
    for it in items:
        if it["id"] in ignored:
            continue
        rows = it["details"]
        if any(r.get("rid") for r in rows):
            rows = [r for r in rows if r.get("rid") not in ignored]
            if not rows:
                continue
            if it.get("many"):
                it = dict(it, details=rows, title=nfmt(it["one"] if len(rows) == 1 else it["many"], len(rows)))
        out.append(it)
    return out


def find_targets(node, out):
    """Alle entity_id's die acties in een automation aansturen."""
    if isinstance(node, dict):
        is_action = "action" in node or "service" in node
        for key, val in node.items():
            if is_action and key in ("target", "data") and isinstance(val, dict):
                ids = val.get("entity_id")
                if isinstance(ids, str):
                    out.update(x.strip() for x in ids.split(","))
                elif isinstance(ids, list):
                    out.update(str(x) for x in ids)
            if is_action and key == "entity_id" and isinstance(val, (str, list)):
                out.update([val] if isinstance(val, str) else map(str, val))
            find_targets(val, out)
    elif isinstance(node, list):
        for x in node:
            find_targets(x, out)


# ---------------------------------------------------------------- controles
def check_live():
    items, ok = [], []
    states = http_json("GET", CORE + "/states")
    by_id = {s["entity_id"]: s for s in states}
    name = lambda eid: (by_id.get(eid, {}).get("attributes", {}) or {}).get("friendly_name") or eid

    # Back-ups en reparaties via de WebSocket-API
    backup_info, issues = None, None
    try:
        ws = WebSocket()
        try:
            ws.auth(TOKEN)
            backup_info = ws.call("backup/info")
            try:
                issues = (ws.call("repairs/list_issues") or {}).get("issues", [])
            except Exception:
                issues = None
        finally:
            ws.close()
    except Exception as e:
        print(f"WebSocket niet beschikbaar: {e}", flush=True)

    if backup_info is not None:
        backups = sorted(backup_info.get("backups") or [], key=lambda b: b.get("date") or "", reverse=True)
        if not backups:
            items.append(item("backup:geen", "hoog", L("Er zijn geen back-ups", "There are no backups"),
                              L("Gaat er iets mis, dan kun je niets terugzetten.", "If something goes wrong, there is nothing to restore."),
                              actions=[link(L("Back-ups instellen", "Set up backups"), "/config/backup/settings")]))
        else:
            newest = parse_time(backups[0].get("date"))
            agents = set()
            for b in backups:
                agents.update((b.get("agents") or {}).keys())
            size = sum(((v or {}).get("size") or 0) for v in (backups[0].get("agents") or {}).values())
            ag = ago(newest)
            det = [{"k": L("Laatste back-up", "Latest backup"), "v": L(f"{local(newest)}, {ag['nl']}", f"{local(newest)}, {ag['en']}")},
                   {"k": L("Bewaard", "Kept"), "v": L(f"{len(backups)} back-ups", f"{len(backups)} backups")},
                   {"k": L("Locaties", "Locations"), "v": ", ".join(sorted(agents)) or L("onbekend", "unknown")}]
            if size:
                det.insert(1, {"k": L("Grootte", "Size"), "v": f"{size / 1048576:.0f} MB"})
            if newest and (datetime.now(timezone.utc) - newest).total_seconds() > 2 * 86400:
                items.append(item("backup:oud:" + backups[0].get("backup_id", ""), "hoog",
                                  L(f"De laatste back-up is van {ag['nl']}", f"The latest backup is from {ag['en']}"),
                                  L("Automatische back-ups lijken niet meer te draaien.", "Automatic backups no longer seem to run."), det,
                                  [link(L("Back-ups bekijken", "View backups"), "/config/backup")]))
            elif agents and agents <= LOCAL_AGENTS:
                items.append(item("backup:lokaal", "hoog", L("Back-ups staan alleen op Home Assistant zelf", "Backups are only stored on Home Assistant itself"),
                                  L("Gaat de opslag kapot, dan ben je ook al je back-ups kwijt.", "If the storage breaks, you lose all your backups too."), det,
                                  [link(L("Tweede locatie instellen", "Set up a second location"), "/config/backup/settings")]))
            else:
                ok.append(L(f"back-up {ag['nl']}", f"backup {ag['en']}"))

    if issues is not None:
        open_issues = [i for i in issues if not i.get("ignored") and not i.get("dismissed_version")]
        if open_issues:
            sev = "hoog" if any(i.get("severity") in ("critical", "error") for i in open_issues) else "na"
            det = [{"k": i.get("translation_key") or i.get("issue_id"), "v": i.get("domain") or ""} for i in open_issues]
            items.append(item("repairs:" + short_hash(i.get("issue_id") for i in open_issues), sev,
                              L(f"{len(open_issues)} {'reparatie' if len(open_issues) == 1 else 'reparaties'} open",
                                f"{len(open_issues)} {'repair' if len(open_issues) == 1 else 'repairs'} open"),
                              L("Home Assistant meldt zelf iets dat aandacht nodig heeft.", "Home Assistant itself reports something that needs attention."), det,
                              [link(L("Reparaties openen", "Open repairs"), "/config/repairs")]))
        else:
            ok.append(L("geen reparaties open", "no open repairs"))

    # Updates
    upd = [s for s in states if s["entity_id"].startswith("update.") and s["state"] == "on"]
    if upd:
        det = [row(name(s["entity_id"]).replace(" Update", ""),
                   f"{s['attributes'].get('installed_version') or '?'} → {s['attributes'].get('latest_version') or '?'}",
                   rid=f"upd:{s['entity_id']}={s['attributes'].get('latest_version')}", href="/config/updates") for s in upd]
        items.append(counted("updates", "na", L("{n} update beschikbaar", "{n} update available"), L("{n} updates beschikbaar", "{n} updates available"),
                             L("Bijwerken houdt Home Assistant veilig en stabiel.", "Updating keeps Home Assistant safe and stable."), det,
                             [link(L("Updates openen", "Open updates"), "/config/updates")]))
    else:
        ok.append(L("alles bijgewerkt", "everything up to date"))

    # Apparaten, verweesde entiteiten en batterijen via één template
    tpl = http_json("POST", CORE + "/template", {"template": TEMPLATE}, timeout=60)
    tpl = json.loads(tpl) if isinstance(tpl, str) else tpl
    devices = tpl.get("d") or {}
    per_dev = {}
    orphans = []
    for eid, dev, st, restored, nm in tpl.get("e") or []:
        if dev:
            per_dev.setdefault(dev, []).append((eid, st, restored))
        elif restored:
            orphans.append((eid, nm))
    offline, gone = [], []
    for dev, ents in per_dev.items():
        if not ents or any(st not in ("unavailable",) for _, st, _ in ents):
            continue
        (gone if all(r for _, _, r in ents) else offline).append(dev)
    dev_det = lambda ids, pre: [row((devices.get(d) or [d])[0] or d, (devices.get(d) or [None, None])[1] or L("geen ruimte", "no area"),
                                    rid=f"{pre}:{d}", href=f"/config/devices/device/{d}")
                                for d in sorted(ids, key=lambda d: str((devices.get(d) or [d])[0]).lower())]
    if offline:
        items.append(counted("offline", "na", OFF_ONE, OFF_MANY, OFF_WHY, dev_det(offline, "dev"), [link(OPEN_DEV, "/config/devices/dashboard")]))
    if gone:
        items.append(counted("gone", "op", L("{n} apparaat bestaat niet meer", "{n} device no longer exists"), L("{n} apparaten bestaan niet meer", "{n} devices no longer exist"),
                             L("De integratie levert ze niet meer; Home Assistant onthoudt ze nog wel. Open een apparaat om het te verwijderen.",
                               "The integration no longer provides them, but Home Assistant still remembers them. Open a device to remove it."),
                             dev_det(gone, "gone"), [link(OPEN_DEV, "/config/devices/dashboard")]))
    if orphans:
        rows = []
        for e, nm in sorted(orphans, key=lambda x: str(x[1] or x[0]).lower()):
            OPS.add(("remove_entity", e))
            rows.append(row(nm or e, e, rid=f"ent:{e}", href=ent_href(e),
                            acts=[{"label": DEL, "kind": "remove_entity", "entity_id": e,
                                   "confirm": L(f"{nm or e} verwijderen uit Home Assistant?", f"Remove {nm or e} from Home Assistant?")}]))
        items.append(counted("orphans", "op", ORPH_ONE, ORPH_MANY, ORPH_WHY, rows, [link(OPEN_ENT, "/config/entities")]))
    low = [b for b in tpl.get("b") or [] if b[2] < BATTERY_MIN and "_soc" not in b[0]]
    if low:
        items.append(counted("battery", "na", BAT_ONE, BAT_MANY, bat_why(BATTERY_MIN),
                             [row(b[1], f"{b[2]:.0f}%", rid=f"bat:{b[0]}",
                                  href=f"/config/devices/device/{b[3]}" if b[3] else ent_href(b[0]))
                              for b in sorted(low, key=lambda b: b[2])],
                             [link(OPEN_ENT, "/config/entities")]))
    elif tpl.get("b"):
        ok.append(L("batterijen in orde", "batteries fine"))

    # Sensoren zonder data en statistieken die niet meer kloppen
    skip = {eid for eid, dev, st, r, nm in tpl.get("e") or [] if r or dev in offline or dev in gone}
    try:
        check_statistics(items, ok, by_id, skip)
    except Exception as e:
        print(f"Statistieken niet te controleren: {e}", flush=True)

    # Apps die zouden moeten draaien
    try:
        addons = (supervisor("/addons") or {}).get("addons", [])
        tm = next((a for a in addons if a.get("slug") == "tijdmachine" or str(a.get("slug", "")).endswith("_tijdmachine")), None)
        if tm:
            # verwijderde automations zijn terug te zetten in de Tijdmachine; de link gaat naar zijn paneel
            tinfo = supervisor(f"/addons/{tm['slug']}/info") or {}
            STATE["tijdmachine"] = {"url": f"/{tm['slug']}" if tinfo.get("ingress_panel") else f"/hassio/ingress/{tm['slug']}",
                                    "running": tm.get("state") == "started"}
        else:
            STATE["tijdmachine"] = None
        STATE["tmChecked"] = True
        stopped = []
        for a in addons:
            if a.get("state") == "stopped":
                info = supervisor(f"/addons/{a['slug']}/info") or {}
                if info.get("boot") == "auto":
                    stopped.append(a)
        for a in stopped:
            items.append(item(f"addon:{a['slug']}", "na", L(f"{a.get('name')} staat uit", f"{a.get('name')} is stopped"), ADDON_WHY,
                              [{"k": "App", "v": a.get("name")}, {"k": L("Versie", "Version"), "v": a.get("version") or ""}],
                              [{"label": L("Starten", "Start"), "kind": "start_addon", "slug": a["slug"],
                                "confirm": L(f"{a.get('name')} starten?", f"Start {a.get('name')}?")},
                               link(L("App openen", "Open app"), f"/hassio/addon/{a['slug']}/info")]))
    except Exception as e:
        print(f"Apps niet op te halen: {e}", flush=True)

    # Automations
    autos = [s for s in states if s["entity_id"].startswith("automation.")]
    broken = [s for s in autos if s["state"] == "unavailable"]
    if broken:
        items.append(counted("auto:kapot", "hoog", L("{n} automation laadt niet", "{n} automation does not load"), L("{n} automations laden niet", "{n} automations do not load"),
                             L("Home Assistant kon ze niet inlezen, dus ze doen niets.", "Home Assistant could not read them, so they do nothing."),
                             [row(name(s["entity_id"]), s["entity_id"], rid=f"kapot:{s['entity_id']}") for s in broken],
                             [link(OPEN_AUTO, "/config/automation/dashboard")]))
    never = [s for s in autos if s["state"] == "on" and not s["attributes"].get("last_triggered")]
    if never:
        items.append(counted("auto:nooit", "op", NEVER_ONE, NEVER_MANY, NEVER_WHY,
                             [row(name(s["entity_id"]), L("aan", "on"), rid=f"nooit:{s['entity_id']}",
                                  href=f"/config/automation/edit/{s['attributes']['id']}" if s["attributes"].get("id") else None)
                              for s in never],
                             [link(OPEN_AUTO, "/config/automation/dashboard")]))
    cutoff = time.time() - DISABLED_DAYS * 86400
    def _old(s):
        lt = parse_time(s["attributes"].get("last_triggered"))
        return lt is None or lt.timestamp() < cutoff
    old_off = [s for s in autos if s["state"] == "off" and _old(s)]
    if old_off:
        rows = []
        for s in old_off:
            aid = s["attributes"].get("id")
            acts = None
            if aid:
                OPS.add(("delete_automation", str(aid)))
                acts = [{"label": DEL, "kind": "delete_automation", "automation_id": str(aid),
                         "confirm": L(f"Automation '{name(s['entity_id'])}' verwijderen?", f"Delete automation '{name(s['entity_id'])}'?")}]
            rows.append(row(name(s["entity_id"]), ago(parse_time(s["attributes"].get("last_triggered"))),
                            rid=f"uit:{s['entity_id']}", href=f"/config/automation/edit/{aid}" if aid else None, acts=acts))
        items.append(counted("auto:uit", "op", OFFA_ONE, OFFA_MANY, offa_why(DISABLED_DAYS), rows,
                             [link(OPEN_AUTO, "/config/automation/dashboard")]))

    # Conflicten: actieve automations met triggers die hetzelfde apparaat aansturen
    users = {}
    for s in autos:
        aid = s["attributes"].get("id")
        if s["state"] != "on" or not aid:
            continue
        try:
            cfg = http_json("GET", f"{CORE}/config/automation/config/{urllib.parse.quote(str(aid))}")
        except Exception:
            continue
        if not isinstance(cfg, dict) or not (cfg.get("triggers") or cfg.get("trigger")):
            continue
        targets = set()
        find_targets(cfg.get("actions") or cfg.get("action") or [], targets)
        for t in targets:
            if t.split(".")[0] in CONFLICT_DOMAINS:
                users.setdefault(t, []).append((s["entity_id"], aid))
    for target, autolist in sorted(users.items()):
        if len(autolist) < 2:
            continue
        ids = {e: a for e, a in autolist}
        flips = flip_flops(target, set(ids))
        hid = "conflict:" + target + ":" + short_hash(e for e, _ in autolist)
        if flips:
            per = {}
            for f in flips:
                per[(f["a"], f["b"])] = per.get((f["a"], f["b"]), 0) + 1
            (pa, pb), n = max(per.items(), key=lambda x: x[1])
            rows = [row(L(f"{local(f['ta'])} en {f['tb'].astimezone().strftime('%H:%M')}", f"{local(f['ta'])} and {f['tb'].astimezone().strftime('%H:%M')}"),
                        L(f"{name(f['a'])}: {f['sa']['nl']}, daarna {name(f['b'])}: {f['sb']['nl']}", f"{name(f['a'])}: {f['sa']['en']}, then {name(f['b'])}: {f['sb']['en']}"),
                        rid=None, href=f"/history?entity_id={target}") for f in flips[:6]]
            rows += [row(name(e), CONTROLS, href=f"/config/automation/edit/{a}") for e, a in autolist]
            items.append(item(hid, "na", L(f"Automations werken {name(target)} tegen", f"Automations work against {name(target)}"),
                              L(f"{name(pa)} en {name(pb)} zetten {name(target)} {n} keer binnen {FLIP_MIN} minuten de andere kant op, "
                                f"in de afgelopen {FLIP_DAYS} dagen. Laat ze op verschillende momenten draaien, of voeg ze samen.",
                                f"{name(pa)} and {name(pb)} switched {name(target)} the other way {n} times within {FLIP_MIN} minutes "
                                f"in the last {FLIP_DAYS} days. Let them run at different times, or merge them."),
                              rows, [link(L("Geschiedenis openen", "Open history"), f"/history?entity_id={target}"),
                                     link(OPEN_AUTO, "/config/automation/dashboard")]))
        else:
            names = [name(e) for e, _ in autolist]
            items.append(item(hid, "na", L(f"{len(autolist)} automations sturen {name(target)}", f"{len(autolist)} automations control {name(target)}"),
                              L(f"Ze kunnen elkaar in de weg zitten. In de afgelopen {FLIP_DAYS} dagen werkten ze elkaar niet tegen; "
                                "kijk of dat ook geldt als ze op hetzelfde moment draaien.",
                                f"They can get in each other's way. In the last {FLIP_DAYS} days they did not work against each other; "
                                "check whether that also holds when they run at the same time."),
                              [row(n, CONTROLS, href=f"/config/automation/edit/{a}") for n, (_, a) in zip(names, autolist)],
                              [link(OPEN_AUTO, "/config/automation/dashboard")]))

    return items, ok


STAT_ISSUES = {
    "no_state": (L("De sensor bestaat niet meer", "The sensor no longer exists"), True),
    "state_class_removed": (L("Heeft geen statistiek-type meer", "No longer has a state class"), True),
    "entity_no_longer_recorded": (L("Wordt niet meer opgeslagen", "No longer recorded"), False),
    "entity_not_recorded": (L("Wordt niet opgeslagen", "Not recorded"), False),
    "units_changed": (L("Eenheid is veranderd", "Unit has changed"), False),
    "mean_type_changed": (L("Soort gemiddelde is veranderd", "Mean type has changed"), False),
}

# Vaste teksten die op meer plekken (ook in de voorbeeldgegevens) voorkomen
DEL = L("Verwijderen", "Delete")
OPEN_ENT, OPEN_DEV, OPEN_AUTO = L("Entiteiten openen", "Open entities"), L("Apparaten openen", "Open devices"), L("Automations openen", "Open automations")
CONTROLS = L("stuurt aan", "controls")
OFF_ONE, OFF_MANY = L("{n} apparaat is offline", "{n} device is offline"), L("{n} apparaten zijn offline", "{n} devices are offline")
OFF_WHY = L("Alle entiteiten van deze apparaten zijn onbeschikbaar. Vaak geen stroom, buiten bereik of een lege batterij.",
            "All entities of these devices are unavailable. Often no power, out of range or an empty battery.")
ORPH_ONE, ORPH_MANY = L("{n} entiteit wordt nergens meer aangemaakt", "{n} entity is no longer provided"), L("{n} entiteiten worden nergens meer aangemaakt", "{n} entities are no longer provided")
ORPH_WHY = L("Ze staan nog in Home Assistant, maar niets geeft ze een waarde. Vaak oude sensoren die je hebt vervangen.",
             "They are still in Home Assistant, but nothing gives them a value. Often old sensors you have replaced.")
BAT_ONE, BAT_MANY = L("{n} batterij is bijna leeg", "{n} battery is almost empty"), L("{n} batterijen zijn bijna leeg", "{n} batteries are almost empty")


def bat_why(pct):
    return L(f"Onder de {pct}%. Vervang ze voordat de sensoren uitvallen.", f"Below {pct}%. Replace them before the sensors stop working.")


ADDON_WHY = L("Deze app start normaal vanzelf, maar draait nu niet.", "This app normally starts by itself, but is not running now.")
NEVER_ONE, NEVER_MANY = L("{n} automation is nooit gestart", "{n} automation has never run"), L("{n} automations zijn nooit gestart", "{n} automations have never run")
NEVER_WHY = L("Ze staan aan, maar hebben nog nooit iets gedaan.", "They are on, but have never done anything.")
OFFA_ONE, OFFA_MANY = L("{n} automation staat al lang uit", "{n} automation has been off for a long time"), L("{n} automations staan al lang uit", "{n} automations have been off for a long time")


def offa_why(days):
    return L(f"Langer dan {days} dagen niet gedraaid. Heb je ze nog nodig?", f"Not run for more than {days} days. Do you still need them?")


STALE_ONE, STALE_MANY = L("{n} sensor geeft al lang geen data", "{n} sensor has not given data for a long time"), L("{n} sensoren geven al lang geen data", "{n} sensors have not given data for a long time")


def stale_why(days):
    return L(f"Al minstens {days} dagen geen nieuwe of veranderende waarde. Kapot, losgekoppeld, of niet meer nodig?",
             f"No new or changing value for at least {days} days. Broken, disconnected, or no longer needed?")


STAT_ONE, STAT_MANY = L("{n} statistiek heeft een probleem", "{n} statistic has a problem"), L("{n} statistieken hebben een probleem", "{n} statistics have a problem")
STAT_WHY = L("Home Assistant bewaart statistieken die niet meer kloppen of bij geen sensor meer horen. Je vindt ze ook bij Ontwikkelhulpmiddelen, Statistieken.",
             "Home Assistant keeps statistics that are no longer correct or no longer belong to a sensor. You also find them under Developer tools, Statistics.")
OPEN_STAT = L("Statistieken openen", "Open statistics")


def stat_confirm(n):
    return L(f"Alle opgeslagen statistieken van {n} verwijderen? Dit kan niet ongedaan worden.", f"Delete all stored statistics of {n}? This cannot be undone.")


def fmt_num(v, unit):
    txt = f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{txt} {unit}".strip()


def check_statistics(items, ok, by_id, skip):
    """Sensoren die al lang geen (veranderende) data geven, en statistieken met een probleem."""
    ws = WebSocket(timeout=60)
    try:
        ws.auth(TOKEN)
        meta = ws.call("recorder/list_statistic_ids") or []
        problems = ws.call("recorder/validate_statistics") or {}
        cands = [m for m in meta if m.get("source") == "recorder" and m.get("statistic_id") in by_id
                 and m["statistic_id"].split(".")[0] == "sensor" and m["statistic_id"] not in skip]
        start = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)
        stats = ws.call("recorder/statistics_during_period", start_time=start.isoformat(), period="day",
                        statistic_ids=[m["statistic_id"] for m in cands],
                        types=["min", "max", "change"]) if cands else {}
    finally:
        ws.close()

    now = datetime.now(timezone.utc)
    stale, seen = [], set()
    for m in cands:
        sid = m["statistic_id"]
        rows = (stats or {}).get(sid) or []
        unit = m.get("statistics_unit_of_measurement") or m.get("display_unit_of_measurement") or ""
        has_sum = m.get("has_sum")
        covered = len(rows) >= STALE_DAYS - 1
        if not rows:
            stale.append((sid, L(f"geen data in {STALE_DAYS} dagen", f"no data in {STALE_DAYS} days")))
        elif has_sum and covered and all(not (r.get("change") or 0) for r in rows):
            stale.append((sid, L(f"telt al {STALE_DAYS} dagen niet op", f"has not counted up for {STALE_DAYS} days")))
        elif not has_sum and covered:
            lo = [r["min"] for r in rows if r.get("min") is not None]
            hi = [r["max"] for r in rows if r.get("max") is not None]
            if lo and hi and min(lo) == max(hi):
                stale.append((sid, L(f"staat {STALE_DAYS} dagen stil op {fmt_num(min(lo), unit)}", f"stuck at {fmt_num(min(lo), unit).replace(',', '.')} for {STALE_DAYS} days")))
        seen.add(sid)
    # Sensoren zonder statistieken die al lang onbeschikbaar of onbekend zijn
    cutoff = now - timedelta(days=STALE_DAYS)
    for eid, st in by_id.items():
        if eid.split(".")[0] not in ("sensor", "binary_sensor") or eid in seen or eid in skip:
            continue
        if st.get("state") in ("unavailable", "unknown"):
            lc = parse_time(st.get("last_changed"))
            if lc and lc < cutoff:
                ag = ago(lc)
                stale.append((eid, L(f"{'onbeschikbaar' if st['state'] == 'unavailable' else 'onbekend'} sinds {ag['nl']}",
                                     f"{'unavailable' if st['state'] == 'unavailable' else 'unknown'} since {ag['en']}")))
    nm = lambda e: ((by_id.get(e) or {}).get("attributes") or {}).get("friendly_name") or e
    if stale:
        items.append(counted("stale", "na", STALE_ONE, STALE_MANY, stale_why(STALE_DAYS),
                             [row(nm(e), why, rid=f"stil:{e}", href=ent_href(e))
                              for e, why in sorted(stale, key=lambda x: str(nm(x[0])).lower())],
                             [link(OPEN_ENT, "/config/entities")]))
    elif cands:
        ok.append(L("sensoren leveren data", "sensors deliver data"))

    rows = []
    for sid, lst in sorted((problems or {}).items()):
        for p in lst or []:
            label, clearable = STAT_ISSUES.get(p.get("type"), (p.get("type") or L("Onbekend probleem", "Unknown problem"), False))
            acts = None
            if clearable:
                OPS.add(("clear_statistics", sid))
                acts = [{"label": DEL, "kind": "clear_statistics", "statistic_id": sid, "confirm": stat_confirm(nm(sid))}]
            rows.append(row(nm(sid), label, rid=f"stat:{sid}:{p.get('type')}", href="/developer-tools/statistics", acts=acts))
    if rows:
        items.append(counted("statistics", "op", STAT_ONE, STAT_MANY, STAT_WHY, rows, [link(OPEN_STAT, "/developer-tools/statistics")]))


def check_demo():
    STATE.update(tijdmachine={"url": "/demo_tijdmachine", "running": True}, tmChecked=True)
    ent = lambda nm, e: row(nm, e, rid=f"ent:{e}", href=ent_href(e),
                            acts=[{"label": DEL, "kind": "remove_entity", "entity_id": e,
                                   "confirm": L(f"{nm} verwijderen uit Home Assistant?", f"Remove {nm} from Home Assistant?")}])
    off = lambda nm, a, last: row(nm, last, rid=f"uit:{nm}", href="/config/automation/dashboard",
                                  acts=[{"label": DEL, "kind": "delete_automation", "automation_id": nm,
                                         "confirm": L(f"Automation '{nm}' verwijderen?", f"Delete automation '{nm}'?")}])
    dev = lambda nm, area: row(nm, area, rid=f"dev:{nm['nl']}", href="/config/devices/dashboard")
    living, garden, kitchen, noarea = L("Woonkamer", "Living room"), L("Tuin", "Garden"), L("Keuken", "Kitchen"), L("geen ruimte", "no area")
    items = [
        item("backup:lokaal", "hoog", L("Back-ups staan alleen op Home Assistant zelf", "Backups are only stored on Home Assistant itself"),
             L("Gaat de opslag kapot, dan ben je ook al je back-ups kwijt.", "If the storage breaks, you lose all your backups too."),
             [row(L("Laatste back-up", "Latest backup"), L("vandaag 05:06, 6 uur geleden", "today 05:06, 6 hours ago")), row(L("Grootte", "Size"), "507 MB"),
              row(L("Bewaard", "Kept"), L("5 back-ups", "5 backups")), row(L("Locaties", "Locations"), "hassio.local")],
             [link(L("Tweede locatie instellen", "Set up a second location"), "/config/backup/settings")]),
        counted("battery", "na", BAT_ONE, BAT_MANY, bat_why(20),
                [row(n, "0%", rid=f"bat:{n['nl']}", href="/config/devices/dashboard") for n in
                 (L("Sensor Badkamer Batterij", "Bathroom sensor battery"), L("Bewegingssensor Hal Batterij", "Hall motion sensor battery"),
                  L("Deursensor Achterdeur Batterij", "Back door sensor battery"))],
                [link(OPEN_ENT, "/config/entities")]),
        counted("offline", "na", OFF_ONE, OFF_MANY, OFF_WHY,
                [dev(L("Lamp Trap", "Stairs lamp"), living), dev(L("Plantbak 2", "Planter 2"), garden), dev(L("Plantbak 3", "Planter 3"), garden),
                 dev(L("Remote keuken", "Kitchen remote"), kitchen), dev(L("Schakelaar 4", "Switch 4"), noarea), dev(L("Schakelaar 5", "Switch 5"), noarea),
                 dev(L("Schakelaar 6", "Switch 6"), noarea), dev(L("Schakelaar_2", "Switch_2"), garden)],
                [link(OPEN_DEV, "/config/devices/dashboard")]),
        item("conflict:demo", "na", L("Automations werken Airco woonkamer tegen", "Automations work against Living room air con"),
             L("Airco - Woonkamer Ochtend opwarmen en Verwarming woonkamer regelen zetten Airco woonkamer 4 keer binnen 90 minuten "
               "de andere kant op, in de afgelopen 7 dagen. Laat ze op verschillende momenten draaien, of voeg ze samen.",
               "Air con - Living room morning warm-up and Living room heating control switched Living room air con the other way "
               "4 times within 90 minutes in the last 7 days. Let them run at different times, or merge them."),
             [row(L(f"{d} 06:00 en 06:30", f"{d} 06:00 and 06:30"),
                  L("Ochtend opwarmen: verwarmen, daarna Verwarming regelen: uit", "Morning warm-up: heat, then Heating control: off"), href="/history")
              for d in ("06-11", "05-11", "04-11", "03-11")] +
             [row(L("Airco - Woonkamer Ochtend opwarmen", "Air con - Living room morning warm-up"), CONTROLS, href="/config/automation/dashboard"),
              row(L("Verwarming woonkamer regelen (Nest + airco)", "Living room heating control (Nest + air con)"), CONTROLS, href="/config/automation/dashboard")],
             [link(L("Geschiedenis openen", "Open history"), "/history"), link(OPEN_AUTO, "/config/automation/dashboard")]),
        item("addon:demo", "na", L("Mosquitto broker staat uit", "Mosquitto broker is stopped"), ADDON_WHY,
             [row("App", "Mosquitto broker")],
             [{"label": L("Starten", "Start"), "kind": "start_addon", "slug": "core_mosquitto", "confirm": L("Mosquitto broker starten?", "Start Mosquitto broker?")}]),
        counted("stale", "na", STALE_ONE, STALE_MANY, stale_why(7),
                [row(L("Bodemvocht Plantbak 1", "Planter 1 soil moisture"), L("geen data in 7 dagen", "no data in 7 days"), rid="stil:sensor.bodemvocht_plantbak_1", href=ent_href("sensor.bodemvocht_plantbak_1")),
                 row(L("Temperatuur Zolder", "Attic temperature"), L("staat 7 dagen stil op 18,5 °C", "stuck at 18.5 °C for 7 days"), rid="stil:sensor.temperatuur_zolder", href=ent_href("sensor.temperatuur_zolder")),
                 row(L("Waterverbruik Totaal", "Total water usage"), L("telt al 7 dagen niet op", "has not counted up for 7 days"), rid="stil:sensor.waterverbruik_totaal", href=ent_href("sensor.waterverbruik_totaal"))],
                [link(OPEN_ENT, "/config/entities")]),
        counted("statistics", "op", STAT_ONE, STAT_MANY, STAT_WHY,
                [row(n, STAT_ISSUES["no_state"][0], rid=f"stat:{n}:no_state", href="/developer-tools/statistics",
                     acts=[{"label": DEL, "kind": "clear_statistics", "statistic_id": n, "confirm": stat_confirm(n)}])
                 for n in ("sensor.marstek_totale_stored_energy", "sensor.netto_energie_verbruik")],
                [link(OPEN_STAT, "/developer-tools/statistics")]),
        counted("orphans", "op", ORPH_ONE, ORPH_MANY, ORPH_WHY,
                [ent("Batterij Besparing Actueel", "sensor.batterij_besparing_actueel"),
                 ent("Marstek Totale AC Power", "sensor.marstek_totale_ac_power"),
                 ent("Netto Energiekosten Actueel", "sensor.netto_energiekosten_actueel"),
                 ent("Stroomkosten Actueel", "sensor.stroomkosten_actueel")],
                [link(OPEN_ENT, "/config/entities")]),
        counted("auto:nooit", "op", NEVER_ONE, NEVER_MANY, NEVER_WHY,
                [row(n, L("aan", "on"), rid=f"nooit:{n['nl']}", href="/config/automation/dashboard") for n in
                 (L("Airco - Luchten stoppen", "Air con - Stop airing"), L("Kiosk: Start chrome", "Kiosk: Start Chrome"),
                  L("Nest - Vaste stand 17,5 graden", "Nest - Fixed at 17.5 degrees"))],
                [link(OPEN_AUTO, "/config/automation/dashboard")]),
        counted("auto:uit", "op", OFFA_ONE, OFFA_MANY, offa_why(30),
                [off("Airco - Luchtvochtigheid Woonkamer", "", L("77 dagen geleden", "77 days ago")), off("Lampen Kerst", "", L("nooit", "never")),
                 off("Marstek - Hoge stroomprijs", "", L("nooit", "never"))],
                [link(OPEN_AUTO, "/config/automation/dashboard")]),
    ]
    return items, [L("alles bijgewerkt", "everything up to date"), L("geen reparaties open", "no open repairs")]


ORDER = {"hoog": 0, "na": 1, "op": 2}


def publish_sensor(items):
    visible = visible_items(items, IGNORED)
    cnt = {k: sum(1 for i in visible if i["sev"] == k) for k in ORDER}
    try:  # de sensor volgt de taal van Home Assistant; de namen van de attributen blijven gelijk voor bestaande automations
        lg = "nl" if str((http_json("GET", CORE + "/config") or {}).get("language") or "nl").lower().startswith("nl") else "en"
    except Exception:
        lg = "nl"
    try:
        http_json("POST", CORE + "/states/sensor.onderhoud_punten", {
            "state": len(visible),
            "attributes": {"friendly_name": "Onderhoud" if lg == "nl" else "Maintenance", "icon": "mdi:wrench-check",
                           "unit_of_measurement": "punten" if lg == "nl" else "items",
                           "belangrijk": cnt["hoog"], "nalopen": cnt["na"], "opruimen": cnt["op"],
                           "punten": [resolve(i["title"], lg) for i in visible]}})
    except Exception as e:
        print(f"Sensor bijwerken mislukt: {e}", flush=True)


def run_checks():
    with LOCK:
        if STATE["running"]:
            return
        STATE["running"] = True
        STATE["attempt"] = time.time()
    try:
        if NO_TOKEN:
            raise PermissionError("geen Supervisor-token")
        OPS.clear()
        items, ok = check_demo() if DEMO else check_live()
        items.sort(key=lambda i: ORDER.get(i["sev"], 9))
        with LOCK:
            STATE.update(items=items, ok=ok, checked=time.time(), error=None)
        if not DEMO:
            publish_sensor(items)
        print(f"Controle klaar: {len(items)} punten.", flush=True)
    except Exception as e:
        with LOCK:
            if NO_TOKEN:
                STATE["error"] = L("De app krijgt geen toegang tot Home Assistant (geen Supervisor-token). Herstart de app; helpt dat niet, installeer hem opnieuw.",
                                   "The app has no access to Home Assistant (no Supervisor token). Restart the app; if that does not help, reinstall it.")
            else:
                STATE["error"] = L(f"De controle is mislukt: {type(e).__name__}: {e}", f"The check failed: {type(e).__name__}: {e}")
        print(resolve(STATE["error"], "nl"), flush=True)
    finally:
        with LOCK:
            STATE["running"] = False


def loop():
    """Vaste controle volgens het interval, gerekend vanaf de laatste controle."""
    if not DEMO and not NO_TOKEN:
        time.sleep(SETTLE + 30)  # de eerste controle komt van watch_core; dit is een vangnet
    while True:
        if time.time() - STATE["attempt"] >= INTERVAL:
            run_checks()
        time.sleep(30)


def check_later(reason, delay):
    def go():
        time.sleep(delay)
        print(f"Controle na: {reason}.", flush=True)
        run_checks()
    threading.Thread(target=go, daemon=True).start()


def watch_core():
    """Houdt een verbinding met Home Assistant open en controleert meteen zodra Home Assistant (opnieuw) is opgestart."""
    first = True
    while True:
        ws = None
        try:
            ws = WebSocket(timeout=60)
            ws.auth(TOKEN)
            ws.call("subscribe_events", event_type="homeassistant_started")
            running = (ws.call("get_config") or {}).get("state") == "RUNNING"
            if running:
                # Bij de eerste verbinding draait Home Assistant meestal al een tijd: dan direct controleren.
                check_later("start van de app" if first else "herstart van Home Assistant", 5 if first else SETTLE)
            first = False
            while True:
                try:
                    msg = ws.events.pop(0) if ws.events else ws.recv()
                except socket.timeout:
                    ws.send({"id": ws.next_id, "type": "ping"})
                    ws.next_id += 1
                    continue
                if msg.get("type") == "event" and (msg.get("event") or {}).get("event_type") == "homeassistant_started":
                    check_later("opstarten van Home Assistant", SETTLE)
        except Exception as e:
            if not first:
                print(f"Verbinding met Home Assistant weg ({type(e).__name__}), opnieuw verbinden.", flush=True)
            first = first and not isinstance(e, PermissionError)
        finally:
            if ws:
                ws.close()
        time.sleep(10)


def load_ignored():
    try:
        with open(IGNORE_FILE, encoding="utf-8") as f:
            IGNORED.update(json.load(f))
    except Exception:
        pass


def save_ignored():
    try:
        tmp = IGNORE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(IGNORED), f)
        os.replace(tmp, IGNORE_FILE)
    except Exception as e:
        print(f"Negeerlijst opslaan mislukt: {e}", flush=True)


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


# ---------------------------------------------------------------- webserver
class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    @property
    def lg(self):
        v = (self.headers.get("X-Lang") or "").lower()
        return "en" if v and not v.startswith("nl") else "nl"

    def tx(self, nl, en):
        return en if self.lg == "en" else nl

    def j(self, code, obj):
        """JSON versturen in de taal van de gebruiker (de pagina stuurt die mee in X-Lang)."""
        self.send(code, json.dumps(resolve(obj, self.lg), ensure_ascii=False))

    def do_GET(self):
        if not user_allowed(self.headers):
            self.send(403, "<h1>Geen toegang / No access</h1>" if "/api/" not in self.path else json.dumps({"error": self.tx("Geen toegang.", "No access.")}),
                      "application/json" if "/api/" in self.path else "text/html; charset=utf-8")
            return
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/fonts/"):
            # lettertype IBM Plex (SIL Open Font License) wordt meegeleverd: de pagina laadt niets van internet
            name = path[len("/fonts/"):]
            if name not in ("ibm-plex-sans-400.woff2", "ibm-plex-sans-500.woff2", "ibm-plex-sans-600.woff2"):
                self.send(404, "", "text/plain")
                return
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", name), "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "font/woff2")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(data)
            return
        if path in ("/", "/index.html"):
            who = user_label(self.headers)
            foot = (f'<p class="who"><span data-en="Signed in as">Ingelogd als</span> <b>{html_escape(who)}</b></p>' if who else "")
            self.send(200, PAGE.replace("<!--FOOT-->", foot), "text/html; charset=utf-8")
        elif path == "/api/status":
            with LOCK:
                payload = dict(STATE, ignored=sorted(IGNORED), interval=INTERVAL // 60)
            self.j(200, payload)
        else:
            self.j(404, {"error": self.tx("Niet gevonden.", "Not found.")})

    def do_POST(self):
        if not user_allowed(self.headers):
            self.j(403, {"error": self.tx("Geen toegang.", "No access.")})
            return
        if self.headers.get("X-Requested-With") != "onderhoud":
            self.j(400, {"error": self.tx("Ongeldig verzoek.", "Invalid request.")})
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 20000)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            body = {}
        if path == "/api/check":
            threading.Thread(target=run_checks, daemon=True).start()
            self.j(200, {"ok": True})
        elif path in ("/api/ignore", "/api/unignore"):
            ids = body.get("ids") or ([body["id"]] if body.get("id") else [])
            with LOCK:
                for i in ids:
                    (IGNORED.add if path == "/api/ignore" else IGNORED.discard)(str(i)[:200])
                save_ignored()
                items = list(STATE["items"])
            if not DEMO:
                publish_sensor(items)
            self.j(200, {"ok": True})
        elif path in ("/api/remove_entity", "/api/delete_automation", "/api/clear_statistics"):
            kind = path.rsplit("/", 1)[1]
            key = str({"remove_entity": body.get("entity_id"), "delete_automation": body.get("automation_id"),
                       "clear_statistics": body.get("statistic_id")}[kind] or "")
            if DEMO:
                self.j(200, {"ok": True, "message": self.tx("In de demo wordt niets verwijderd.", "Nothing is deleted in the demo.")})
                return
            if (kind, key) not in OPS:
                self.j(400, {"error": self.tx("Dit staat niet (meer) in de lijst. Controleer opnieuw.", "This is not (or no longer) in the list. Check again.")})
                return
            try:
                if kind in ("remove_entity", "clear_statistics"):
                    ws = WebSocket()
                    try:
                        ws.auth(TOKEN)
                        if kind == "remove_entity":
                            ws.call("config/entity_registry/remove", entity_id=key)
                        else:
                            ws.call("recorder/clear_statistics", statistic_ids=[key])
                    finally:
                        ws.close()
                    msg = self.tx("Entiteit verwijderd.", "Entity removed.") if kind == "remove_entity" else self.tx("Statistieken verwijderd.", "Statistics deleted.")
                else:
                    http_json("DELETE", f"{CORE}/config/automation/config/{urllib.parse.quote(key)}")
                    msg = self.tx("Automation verwijderd.", "Automation deleted.")
                OPS.discard((kind, key))
                threading.Thread(target=run_checks, daemon=True).start()
                self.j(200, {"ok": True, "message": msg})
            except Exception as e:
                self.j(502, {"error": self.tx(f"Verwijderen mislukt: {e}", f"Deleting failed: {e}")})
        elif path == "/api/start_addon":
            slug = str(body.get("slug") or "")
            if not slug or "/" in slug or ".." in slug:
                self.j(400, {"error": self.tx("Onbekende app.", "Unknown app.")})
                return
            if DEMO:
                self.j(200, {"ok": True, "message": self.tx("In de demo wordt niets gestart.", "Nothing is started in the demo.")})
                return
            try:
                supervisor(f"/addons/{slug}/start", "POST")
                threading.Thread(target=lambda: (time.sleep(5), run_checks()), daemon=True).start()
                self.j(200, {"ok": True, "message": self.tx("De app wordt gestart.", "The app is starting.")})
            except urllib.error.HTTPError as e:
                self.j(502, {"error": self.tx(f"Starten mislukt (HTTP {e.code}).", f"Starting failed (HTTP {e.code}).")})
        else:
            self.j(404, {"error": self.tx("Niet gevonden.", "Not found.")})

    def log_message(self, *args):
        pass


PAGE = r"""<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Maintenance</title>
<style>
@font-face{font-family:"IBM Plex Sans";font-style:normal;font-weight:400;font-display:swap;src:url("fonts/ibm-plex-sans-400.woff2") format("woff2")}
@font-face{font-family:"IBM Plex Sans";font-style:normal;font-weight:500;font-display:swap;src:url("fonts/ibm-plex-sans-500.woff2") format("woff2")}
@font-face{font-family:"IBM Plex Sans";font-style:normal;font-weight:600;font-display:swap;src:url("fonts/ibm-plex-sans-600.woff2") format("woff2")}
:root{--bg:#EDF1F6;--surface:#fff;--line:#D5DDE8;--line2:#E4E9F0;--text:#14213A;--muted:#5C6C84;--faint:#93A1B5;
  --accent:#0B978B;--accentText:#fff;--okbg:#E7F6EE;--okline:#BFE5CF;--oktext:#1C6B45;--ok:#24945F;
  --hoogbg:#FBE7E7;--hoog:#B33232;--nabg:#FCEFDF;--na:#9A5A10;--opbg:#E9EDF3;--op:#4A5A70;--link:#2F7CC0}
@media (prefers-color-scheme: dark){:root{--bg:#0E1622;--surface:#152030;--line:#233249;--line2:#1C293C;--text:#E4EBF5;--muted:#93A3BA;--faint:#5E6F88;
  --accent:#3CC5B6;--accentText:#0E1622;--okbg:#12241F;--okline:#1F4636;--oktext:#A8D9BF;--ok:#4CC787;
  --hoogbg:#3D2324;--hoog:#F58A8A;--nabg:#3A2A1E;--na:#F0A257;--opbg:#26303E;--op:#B4C0D0;--link:#69ACE6}}
*{box-sizing:border-box}
html{color-scheme:light dark;scrollbar-color:var(--line) transparent}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
button{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--link);outline-offset:2px;border-radius:6px}
.wrap{max-width:780px;margin:0 auto;padding:36px 16px 48px;display:flex;flex-direction:column;gap:16px}
h1{margin:0;font-size:28px;font-weight:600;letter-spacing:-.01em}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap}
.sum{margin:6px 0 0;color:var(--muted);font-size:16px}
.meta{color:var(--faint);font-size:13px;text-align:right}
.meta button{background:none;border:0;padding:0;color:var(--link);text-decoration:underline;cursor:pointer;font-size:13px}
.ok{display:flex;gap:10px;align-items:center;background:var(--okbg);border:1px solid var(--okline);border-radius:12px;padding:12px 16px;font-size:14px;color:var(--oktext)}
.ok svg{flex:none;color:var(--ok)}
.note{border:1px solid var(--line);background:var(--surface);border-radius:12px;padding:12px 16px;font-size:14px;color:var(--muted)}
.note.err{border-color:var(--hoog);color:var(--text)}
.note[hidden]{display:none}
.undo{display:flex;align-items:center;gap:10px 14px;flex-wrap:wrap;color:var(--text);border-color:var(--accent);position:sticky;bottom:16px;z-index:4;box-shadow:0 6px 24px rgba(0,0,0,.18)}
.undo span{flex:1 1 220px}
.tmtip{margin:4px 0 0;font-size:13px;color:var(--muted)}
.tmtip[hidden]{display:none}
ol{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
li.it{background:var(--surface);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.head{width:100%;background:none;border:0;padding:16px 18px;display:flex;align-items:flex-start;gap:14px;text-align:left;cursor:pointer}
.tag{flex:none;margin-top:2px;font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;min-width:84px;text-align:center}
.tag.hoog{background:var(--hoogbg);color:var(--hoog)} .tag.na{background:var(--nabg);color:var(--na)} .tag.op{background:var(--opbg);color:var(--op)}
.tx{flex:1;min-width:0} .tt{display:block;font-size:16px;font-weight:600;line-height:1.35} .tw{display:block;margin-top:3px;font-size:14px;color:var(--muted)}
.chev{flex:none;margin-top:4px;color:var(--faint);transition:transform .15s}
.it.open .chev{transform:rotate(180deg)}
.body{padding:0 18px 16px 116px}
.it:not(.open) .body{display:none}
.rows{list-style:none;margin:0 0 14px;padding:0;font-size:14px}
.rw{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:4px 14px;align-items:center;padding:8px 0;border-top:1px solid var(--line2)}
.rw .k a{color:var(--text);text-decoration:underline;text-decoration-color:var(--faint);text-underline-offset:2px}
.rw .k a:hover{text-decoration-color:var(--text)}
.rw .v{color:var(--muted);text-align:right;word-break:break-all;font-size:13px}
.rw .ra{display:flex;gap:4px;justify-content:flex-end}
.mini{background:none;border:1px solid transparent;border-radius:7px;padding:4px 8px;font-size:13px;color:var(--muted);cursor:pointer}
.mini:hover{border-color:var(--line);color:var(--text)}
.mini.del{color:var(--hoog)}
.mini.del:hover{border-color:var(--hoog)}
.acts{display:flex;gap:10px;flex-wrap:wrap}
.btn{border:0;border-radius:9px;padding:9px 16px;cursor:pointer;font-size:14px;text-decoration:none;display:inline-block}
.btn.pri{background:var(--accent);color:var(--accentText);font-weight:600}
.btn.sec{background:transparent;border:1px solid var(--line);color:var(--text)}
.btn.lnk{background:transparent;color:var(--muted);padding:9px 6px}
.done{text-align:center;padding:36px 16px;color:var(--muted)}
.restore{background:none;border:0;padding:0;color:var(--link);text-decoration:underline;cursor:pointer;font-size:14px}
.ign{background:var(--surface);border:1px dashed var(--line);border-radius:14px;padding:4px 18px}
.ignhead{width:100%;background:none;border:0;padding:12px 0;text-align:left;cursor:pointer;color:var(--muted);font-size:14px;display:flex;justify-content:space-between;align-items:center}
.ignhead svg{transition:transform .15s}
.ignhead[aria-expanded="true"] svg{transform:rotate(180deg)}
#ignbody{padding-bottom:14px}
#ignbody .rows{margin-bottom:12px}
.rw .cat{color:var(--faint);font-size:12px;display:block;margin-top:1px}
.who{max-width:780px;margin:0 auto;padding:0 16px 24px;font-size:13px;color:var(--faint)}
.modal{position:fixed;inset:0;background:rgb(10 14 20 / .55);display:grid;place-items:center;padding:16px;z-index:5}
.box{background:var(--surface);border-radius:16px;padding:20px 22px;width:min(420px,100%)}
.box h3{margin:0 0 8px;font-size:18px} .box p{margin:0 0 16px;color:var(--muted)}
.box .acts{justify-content:flex-end}
.toast{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--ok);border-radius:12px;padding:10px 16px;font-size:14px;z-index:6}
.toast[hidden]{display:none}
@media (max-width:600px){
  h1{font-size:24px}
  .body{padding:0 16px 14px}
  .head{padding:14px 16px;gap:10px;flex-wrap:wrap}
  .tag{min-width:0}
  .tx{flex-basis:100%;order:3}
  .chev{margin-left:auto}
  .meta{text-align:left}
  .rw{grid-template-columns:minmax(0,1fr) auto}
  .rw .v{grid-column:1;grid-row:2;text-align:left}
  .rw .ra{grid-column:2;grid-row:1 / span 2}
}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div><h1 data-en="Maintenance">Onderhoud</h1><p class="sum" id="sum"></p></div>
    <div class="meta" id="meta"></div>
  </header>
  <p class="note" id="note" hidden></p>
  <div class="ok" id="ok" hidden><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg><span id="oktext"></span></div>
  <ol id="list"></ol>
  <div class="note undo" id="undo" role="status" hidden></div>
  <p class="done" id="done" hidden data-en="Everything has been checked. New items appear here by themselves as soon as there is something.">Alles is nagelopen. Je ziet hier vanzelf nieuwe punten zodra er iets is.</p>
  <section class="ign" id="ign" hidden>
    <button class="ignhead" id="ignhead" aria-expanded="false"></button>
    <div id="ignbody" hidden>
      <ul class="rows" id="ignlist"></ul>
      <button class="restore" id="restoreall" data-en="Restore all">Alles terugzetten</button>
    </div>
  </section>
  <p class="tmtip" id="tmtip" hidden><span data-en="Tip: with the Time Machine app you restore a deleted automation, script or helper with one click. You find it in the same store as Maintenance.">Tip: met de app Time Machine (Tijdmachine) zet je een verwijderde automation, script of helper met één klik terug. Je vindt hem in dezelfde store als Onderhoud.</span> <button class="mini" id="tmtipx" data-en="Hide">Verbergen</button></p>
</div>
<!--FOOT-->
<div class="toast" id="toast" role="status" hidden></div>
<script>
// Taal volgt Home Assistant: Nederlands als HA Nederlands is, anders Engels. ?lang=en of ?lang=nl overschrijft.
const LANG = (() => { const q = new URLSearchParams(location.search).get('lang'); if (q) return q.toLowerCase().startsWith('nl') ? 'nl' : 'en';
  let l = ''; try { if (window.top !== window) l = window.top.document.documentElement.lang || ''; } catch (e) {}
  if (!l) { try { l = JSON.parse(window.top.localStorage.getItem('selectedLanguage') || 'null') || ''; } catch (e) {} }
  if (!l) l = navigator.language || 'en';
  return String(l).toLowerCase().startsWith('nl') ? 'nl' : 'en'; })();
const t = (nl, en) => LANG === 'en' ? en : nl;
document.documentElement.lang = LANG;
if (LANG === 'en') document.querySelectorAll('[data-en]').forEach(el => { el.textContent = el.dataset.en; });
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const SEV = {hoog: t('Belangrijk', 'Important'), na: t('Nalopen', 'Check'), op: t('Opruimen', 'Clean up')};
let data = null, open = null, polling = null, ignOpen = false; const removed = new Set();
const CAT = {stale: t('Geen data', 'No data'), statistics: t('Statistieken', 'Statistics'), offline: t('Offline apparaten', 'Offline devices'), gone: t('Bestaan niet meer', 'No longer exist'),
  orphans: t('Nergens meer aangemaakt', 'No longer provided'), battery: t('Batterijen', 'Batteries'), updates: 'Updates', 'auto:nooit': t('Nooit gestart', 'Never run'),
  'auto:uit': t('Al lang uit', 'Off for a long time'), 'auto:kapot': t('Laden niet', 'Do not load')};
function ignoredEntries(all, ign){
  const out = [];
  for (const it of all){
    const cat = CAT[it.id] || it.title;
    if (ign.has(it.id)) { out.push({ids: [it.id], name: it.title, cat: t('Hele punt', 'Whole item')}); continue; }
    for (const r of it.details) if (r.rid && ign.has(r.rid)) out.push({ids: [r.rid], name: r.k, cat});
  }
  return out;
}
async function unignore(ids, msg){
  try { await post('api/unignore', {ids}); const set = new Set(ids); data.ignored = (data.ignored || []).filter(x => !set.has(x)); render(); toast(msg); }
  catch (e) { toast(e.message); }
}

async function post(path, body){
  const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'onderhoud', 'X-Lang': LANG}, body: JSON.stringify(body || {})});
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) throw new Error(j.error || t('Er ging iets mis.', 'Something went wrong.'));
  return j;
}
let tt; function toast(m){ const el = $('#toast'); el.textContent = m; el.hidden = false; clearTimeout(tt); tt = setTimeout(() => el.hidden = true, 4000); }
function confirmBox(text, sub){
  return new Promise(res => {
    const m = document.createElement('div'); m.className = 'modal';
    m.innerHTML = `<div class="box" role="dialog" aria-modal="true"><h3>${esc(text)}</h3><p>${esc(sub || t('Dit voert de actie meteen uit in Home Assistant.', 'This carries out the action in Home Assistant right away.'))}</p><div class="acts"><button class="btn sec" data-no>${t('Annuleren', 'Cancel')}</button><button class="btn pri" data-yes>${t('Doorgaan', 'Continue')}</button></div></div>`;
    document.body.appendChild(m); m.querySelector('[data-yes]').focus();
    const close = v => { m.remove(); res(v); };
    m.addEventListener('click', e => { if (e.target === m || e.target.closest('[data-no]')) close(false); if (e.target.closest('[data-yes]')) close(true); });
    m.addEventListener('keydown', e => { if (e.key === 'Escape') close(false); });
  });
}
const haLink = href => href ? (href.startsWith('/') ? href : '/' + href) : null;

function visible(all, ign){
  const out = [];
  for (const it of all){
    if (ign.has(it.id)) continue;
    let rows = it.details;
    if (rows.some(r => r.rid)){
      rows = rows.filter(r => !ign.has(r.rid) && !removed.has(r.rid));
      if (!rows.length) continue;
      if (it.many) { out.push({...it, details: rows, title: (rows.length === 1 ? it.one : it.many).replace('{n}', rows.length)}); continue; }
    }
    out.push(it);
  }
  return out;
}
async function ignore(ids, msg){
  try { await post('api/ignore', {ids}); data.ignored = [...new Set([...(data.ignored || []), ...ids])]; render(); toast(msg); }
  catch (e) { toast(e.message); }
}
function render(){
  const d = data, ign = new Set(d.ignored || []);
  const items = visible(d.items || [], ign);
  const ignList = ignoredEntries(d.items || [], ign);
  const cnt = k => items.filter(i => i.sev === k).length;
  const parts = [['hoog', t('belangrijk', 'important')], ['na', t('nalopen', 'to check')], ['op', t('opruimen', 'to clean up')]].filter(([k]) => cnt(k)).map(([k, l]) => `${cnt(k)} ${l}`);
  $('#sum').textContent = !d.checked ? (d.error ? t('De controle is niet gelukt.', 'The check did not succeed.') : t('Eerste controle loopt…', 'First check running…')) : items.length ? `${items.length} ${items.length === 1 ? t('punt', 'item') : t('punten', 'items')}: ${parts.join(', ')}.` : t('Niets te doen.', 'Nothing to do.');
  const when = d.checked ? new Date(d.checked * 1000).toLocaleTimeString(t('nl-NL', 'en-GB'), {hour: '2-digit', minute: '2-digit'}) : '';
  $('#meta').innerHTML = (d.running ? t('Bezig met controleren…', 'Checking…') : when ? t(`Gecontroleerd om ${when}`, `Checked at ${when}`) : '') + `<br><button id="again">${t('Opnieuw controleren', 'Check again')}</button>`;
  $('#again').onclick = async () => { try { await post('api/check'); data.running = true; render(); poll(); } catch (e) { toast(e.message); } };
  const n = $('#note');
  if (d.error) { n.hidden = false; n.className = 'note err'; n.textContent = d.error; }
  else if (d.demo) { n.hidden = false; n.className = 'note'; n.textContent = t('Je bekijkt voorbeeldgegevens. Als Home Assistant-app controleert Onderhoud je eigen installatie.', 'You are looking at sample data. As a Home Assistant app, Maintenance checks your own installation.'); }
  else n.hidden = true;
  $('#ok').hidden = !(d.ok || []).length; $('#oktext').textContent = t('In orde: ', 'All good: ') + (d.ok || []).join(', ') + '.';
  $('#list').innerHTML = items.map(i => `<li class="it ${open === i.id ? 'open' : ''}" data-id="${esc(i.id)}">
    <button class="head" aria-expanded="${open === i.id}"><span class="tag ${i.sev}">${SEV[i.sev] || ''}</span>
      <span class="tx"><span class="tt">${esc(i.title)}</span><span class="tw">${esc(i.why)}</span></span>
      <svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></button>
    <div class="body">${i.details.length ? `<ul class="rows">${i.details.map((x, r) => `<li class="rw"><span class="k">${x.href ? `<a href="${esc(haLink(x.href))}" target="_top" title="${t('Openen in Home Assistant', 'Open in Home Assistant')}">${esc(x.k)}</a>` : esc(x.k)}</span>
        <span class="v">${esc(x.v)}</span><span class="ra">${(x.acts || []).map((a, k) => `<button class="mini del" data-row="${r}" data-ract="${k}">${esc(a.label)}</button>`).join('')}${x.rid ? `<button class="mini" data-rign="${r}" aria-label="${esc(x.k)} ${t('negeren', 'ignore')}">${t('Negeren', 'Ignore')}</button>` : ''}</span></li>`).join('')}</ul>` : ''}
      <div class="acts">${i.actions.map((a, k) => a.kind === 'link' ? `<a class="btn ${k ? 'sec' : 'pri'}" href="${esc(haLink(a.href))}" target="_top">${esc(a.label)}</a>`
        : `<button class="btn ${k ? 'sec' : 'pri'}" data-act="${k}">${esc(a.label)}</button>`).join('')}
        <button class="btn lnk" data-ignore>${i.details.some(x => x.rid) ? t('Alles negeren', 'Ignore all') : t('Negeren', 'Ignore')}</button></div></div></li>`).join('');
  $('#done').hidden = !(d.checked && !items.length);
  tmTip();
  const sec = $('#ign'); sec.hidden = !ignList.length;
  if (!ignList.length) ignOpen = false;
  $('#ignhead').setAttribute('aria-expanded', ignOpen);
  $('#ignhead').innerHTML = `<span>${t('Genegeerd', 'Ignored')} (${ignList.length})</span><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>`;
  $('#ignbody').hidden = !ignOpen;
  $('#ignlist').innerHTML = ignList.map((g, k) => `<li class="rw"><span class="k">${esc(g.name)}<span class="cat">${esc(g.cat)}</span></span><span class="v"></span>
    <span class="ra"><button class="mini" data-unign="${k}">${t('Terugzetten', 'Restore')}</button></span></li>`).join('');
  document.querySelectorAll('[data-unign]').forEach(b => b.onclick = () => unignore(ignList[+b.dataset.unign].ids, t('Teruggezet in de lijst.', 'Back in the list.')));
  $('#restoreall').onclick = () => unignore(ignList.flatMap(g => g.ids), t('Alles staat weer in de lijst.', 'Everything is back in the list.'));
  document.querySelectorAll('li.it').forEach(li => {
    const it = items.find(x => x.id === li.dataset.id);
    li.querySelector('.head').onclick = () => { open = open === it.id ? null : it.id; render(); };
    li.querySelector('[data-ignore]').onclick = () => { const rids = it.details.filter(x => x.rid).map(x => x.rid); if (open === it.id) open = null;
      ignore(rids.length ? rids : [it.id], rids.length ? t('Genegeerd. Nieuwe gevallen zie je wel weer verschijnen.', 'Ignored. New cases will still show up.') : t('Genegeerd. Je ziet dit punt pas weer als er iets verandert.', 'Ignored. You only see this item again when something changes.')); };
    li.querySelectorAll('[data-rign]').forEach(b => b.onclick = () => ignore([it.details[+b.dataset.rign].rid], t('Regel genegeerd.', 'Row ignored.')));
    li.querySelectorAll('[data-ract]').forEach(b => b.onclick = async () => {
      const a = it.details[+b.dataset.row].acts[+b.dataset.ract];
      const tm = data.tijdmachine && data.tijdmachine.running;
      const sub = a.kind !== 'delete_automation' ? null : tm ? t('De Tijdmachine bewaart hem. Daar kun je hem later terugzetten.', 'Time Machine keeps it. You can restore it there later.') : t('Dit voert de actie meteen uit in Home Assistant en is niet ongedaan te maken.', 'This carries out the action in Home Assistant right away and cannot be undone.');
      if (a.confirm && !(await confirmBox(a.confirm, sub))) return;
      b.disabled = true;
      try { const res = await post('api/' + a.kind, a);
        if (a.kind === 'delete_automation') undone(it.details[+b.dataset.row].k, a.automation_id); else toast(res.message || t('Gedaan.', 'Done.'));
        if (it.details[+b.dataset.row].rid) removed.add(it.details[+b.dataset.row].rid); render(); setTimeout(load, 4000); }
      catch (e) { b.disabled = false; toast(e.message); }
    });
    li.querySelectorAll('[data-act]').forEach(b => b.onclick = async () => {
      const a = it.actions[+b.dataset.act];
      if (a.confirm && !(await confirmBox(a.confirm))) return;
      try { const res = await post('api/' + a.kind, a); toast(res.message || t('Gedaan.', 'Done.')); setTimeout(load, 6000); } catch (e) { toast(e.message); }
    });
  });
}
$('#ignhead').onclick = () => { ignOpen = !ignOpen; render(); };
// Na het verwijderen van een automation: terugzetten via de Tijdmachine (als die er is).
function undone(name, id){
  const u = $('#undo'), tm = data.tijdmachine && data.tijdmachine.running ? data.tijdmachine : null;
  u.innerHTML = `<span><b>${esc(name)}</b> ${t('is verwijderd.', 'has been deleted.')}${tm ? t(' Per ongeluk?', ' By accident?') : ''}</span>`
    + (tm ? `<a class="btn sec" id="totm" href="${esc(haLink(tm.url))}" target="_top">${t('Terugzetten in de Tijdmachine', 'Restore in Time Machine')}</a>` : '')
    + `<button class="mini" id="undox">${t('Sluiten', 'Close')}</button>`;
  u.hidden = false;
  $('#undox').onclick = () => { u.hidden = true; };
  if (tm) $('#totm').onclick = () => { try { localStorage.setItem('fonfey.tijdmachine.open', JSON.stringify({key: 'automation:' + id, t: Date.now()})); } catch (e) {} };
}
// Eén rustige tip onderaan als de Tijdmachine niet is geïnstalleerd.
function tmTip(){
  let hide = false; try { hide = localStorage.getItem('onderhoud.tmtip') === 'weg'; } catch (e) {}
  const tip = $('#tmtip'); tip.hidden = hide || !data.tmChecked || !!data.tijdmachine;
  $('#tmtipx').onclick = () => { try { localStorage.setItem('onderhoud.tmtip', 'weg'); } catch (e) {} tip.hidden = true; };
}
async function load(){
  try { data = await (await fetch('api/status', {cache: 'no-store', headers: {'X-Lang': LANG}})).json(); if (open === null) open = (visible(data.items || [], new Set(data.ignored || []))[0] || {}).id || null; render(); }
  catch (e) { $('#sum').textContent = t('Geen verbinding met de app. Draait hij nog?', 'No connection with the app. Is it still running?'); }
  return data;
}
function poll(){ clearInterval(polling); polling = setInterval(async () => { const d = await load(); if (d && !d.running) clearInterval(polling); }, 2000); }
load().then(d => { if (d && (d.running || !d.checked)) poll(); });
setInterval(load, 5 * 60 * 1000);
</script>
</body>
</html>
"""


def main():
    load_ignored()
    threading.Thread(target=loop, daemon=True).start()
    if not DEMO and not NO_TOKEN:
        threading.Thread(target=watch_core, daemon=True).start()
    server = ThreadingHTTPServer((LISTEN, PORT), Handler)
    print(f"Onderhoud draait op poort {PORT}. {'Voorbeeldgegevens.' if DEMO else 'Controleert elke ' + str(INTERVAL // 60) + ' minuten.'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
