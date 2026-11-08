#!/usr/bin/env python3
"""
UniFi Dashboard
===============

Live overzicht van je UniFi-netwerk: netwerkkaart, internetverkeer (tot 24 uur
terug), switchpoorten, wifi-radio's en clients. Werkt als Home Assistant-app
of los op je computer.

Los gebruiken (Python 3.8+, geen extra pakketten):
1. Maak in UniFi Network een API-key aan:
   Instellingen -> Control Plane -> Integraties -> "Create API Key".
2. Vul hieronder UNIFI_HOST en UNIFI_API_KEY in (of zet ze als omgevingsvariabelen).
3. Start:  py unifi_dashboard.py   (Windows)   of   python3 unifi_dashboard.py
   Open:   http://localhost:8080
   Demo:   py unifi_dashboard.py --demo

Als Home Assistant-app komen de instellingen uit het tabblad Configuratie.
De API-key gaat nooit naar de browser.
"""

import ipaddress
import json
from html import escape as html_escape
import math
import os
import random
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
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


def _opt(key, default):
    val = _OPTS.get(key.lower())
    if val in (None, ""):
        val = os.environ.get(key, default)
    return val


UNIFI_HOST = _opt("UNIFI_HOST", "192.168.1.1")             # IP of hostnaam van je console
UNIFI_API_KEY = _opt("UNIFI_API_KEY", "")                  # API-key uit UniFi Network
UNIFI_SITE = _opt("UNIFI_SITE", "")                        # leeg = eerste site
LISTEN = "0.0.0.0" if HA_APP else os.environ.get("LISTEN", "127.0.0.1")  # "0.0.0.0" = ook vanaf tablet/telefoon
PORT = 8099 if HA_APP else int(os.environ.get("PORT", "8080"))
REFRESH_SECONDS = int(_opt("REFRESH_SECONDS", "10"))

DEMO = "--demo" in sys.argv or not UNIFI_API_KEY
DATA_DIR = "/data" if HA_APP else os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(DATA_DIR, "unifi_history.json")

GATEWAY_MODELS = ("UDM", "UDR", "UCG", "UXG", "USG", "UX", "EFG", "UDW")
AP_MODELS = ("U6", "U7", "UAP", "UAL", "E7", "UWB", "UMA")
SWITCH_MODELS = ("USW", "US-", "USL", "ECS")

CTX = ssl.create_default_context()   # consoles gebruiken een self-signed certificaat
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

LOCK = threading.Lock()
STATE = {"ok": False, "error": None, "updated": None, "demo": DEMO,
         "site": None, "version": None, "devices": [], "clients": [], "wan": None}

# Geschiedenis: elke meting van de laatste 30 minuten, plus minuutgemiddelden voor 24 uur.
RAW = deque(maxlen=max(30, 1800 // max(1, REFRESH_SECONDS)))
MINUTES = deque(maxlen=1440)
_CUR = {"m": None, "rx": 0.0, "tx": 0.0, "max": 0.0, "n": 0}


# ---------------------------------------------------------------- UniFi API
def api(path, params=None):
    url = f"https://{UNIFI_HOST}/proxy/network/integration/v1{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-API-KEY": UNIFI_API_KEY,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, context=CTX, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def legacy(path):
    """Klassieke Network-API (zelfde API-key); levert o.a. op welke switchpoort een client zit."""
    ref = _site_cache.get("ref") or "default"
    url = f"https://{UNIFI_HOST}/proxy/network/api/s/{urllib.parse.quote(ref)}{path}"
    req = urllib.request.Request(url, headers={"X-API-KEY": UNIFI_API_KEY, "Accept": "application/json"})
    with urllib.request.urlopen(req, context=CTX, timeout=10) as r:
        res = json.loads(r.read().decode("utf-8"))
    return res.get("data", []) if isinstance(res, dict) else res


# ---------------------------------------------------------------- productafbeeldingen
# Ubiquiti publiceert een openbare productcatalogus met afbeeldingen. De app zoekt daarin je modellen op,
# haalt de afbeelding één keer op en bewaart die in /data; de browser krijgt hem via de app.
UIDB_URL = "https://static.ui.com/fingerprint/ui/public.json"
ICON_URL = "https://static.ui.com/fingerprint/ui/icons/{id}_{w}x{h}.png"
IMG_DIR = os.path.join(DATA_DIR, "productafbeeldingen")
_uidb = {"index": None, "loaded": 0, "tried": 0}
LEGACY_MODEL = {}  # {apparaat-mac: modelcode uit de klassieke API}
LEGACY_LAN_IP = {}  # {apparaat-mac: intern IP-adres}, voor gateways waarvan de API het publieke adres geeft
_NO_IMG = set()
CLIENT_FP = {}  # {client-mac: UniFi-apparaatherkenning (icoon-id)}
CLIENT_EXTRA = {}  # {client-mac: {"ssid", "network", "vlan", "signal", ...}}
NAMES = {}  # {mac: naam} om MAC-adressen in het logboek te vervangen
STORAGE = {}  # {apparaat-mac: [{"name", "size", "used"}]}
BACKUPS = {"items": None, "at": 0, "error": None}


def backups():
    """Back-ups van de Network-app op de console, elk half uur ververst."""
    if time.time() - BACKUPS["at"] < 1800:
        return BACKUPS
    BACKUPS["at"] = time.time()
    ref = urllib.parse.quote(_site_cache.get("ref") or "default")
    try:
        req = urllib.request.Request(f"https://{UNIFI_HOST}/proxy/network/api/s/{ref}/cmd/backup", data=json.dumps({"cmd": "list-backups"}).encode(),
                                     method="POST", headers={"X-API-KEY": UNIFI_API_KEY, "Accept": "application/json", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
            res = json.loads(r.read().decode("utf-8"))
        rows = res.get("data", []) if isinstance(res, dict) else res
        items = []
        for b in rows or []:
            t = b.get("time") or b.get("datetime")
            if isinstance(t, str):
                try:
                    t = datetime_from_iso(t)
                except Exception:
                    t = None
            elif isinstance(t, (int, float)):
                t = int(t // 1000 if t > 1e11 else t)
            items.append({"t": t, "size": b.get("size"), "version": b.get("version"), "kind": b.get("type") or ""})
        BACKUPS.update(items=sorted(items, key=lambda x: -(x["t"] or 0))[:10], error=None)
        DIAG["backups"] = {"ok": True, "info": _shape(rows[0] if rows else {})}
    except Exception as e:
        BACKUPS["error"] = str(e)
        DIAG["backups"] = {"ok": False, "info": str(e)}
        print(f"Back-ups niet op te halen ({e}).", flush=True)
    return BACKUPS


def datetime_from_iso(v):
    import datetime as _dt
    return int(_dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())

# ---------------------------------------------------------------- flows
FLOWS = {"data": None, "at": 0, "error": None}
DIAG = {}  # {onderdeel: {"ok": bool, "info": str}} voor de technische controle (alleen veldnamen, geen gegevens)


def _shape(v, depth=0):
    """Beschrijft de opbouw van een antwoord met alleen veldnamen, zonder waarden."""
    if isinstance(v, dict) and depth < 3:
        return "{" + ", ".join(f"{k}{(':' + _shape(x, depth + 1)) if isinstance(x, (dict, list)) else ''}" for k, x in list(v.items())[:25]) + "}"
    if isinstance(v, list) and depth < 3:
        return "[" + (_shape(v[0], depth + 1) if v else "") + "]"
    return ""


def _v2(path, body=None):
    ref = urllib.parse.quote(_site_cache.get("ref") or "default")
    req = urllib.request.Request(f"https://{UNIFI_HOST}/proxy/network/v2/api/site/{ref}{path}",
                                 data=json.dumps(body).encode() if body is not None else None, method="POST" if body is not None else "GET",
                                 headers={"X-API-KEY": UNIFI_API_KEY, "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=CTX, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _pick(d, *paths):
    for p in paths:
        cur = d
        for k in p.split("."):
            cur = cur.get(k) if isinstance(cur, dict) else None
        if cur not in (None, "", []):
            return cur
    return None


def flows():
    """Verbindingen (flows) van de afgelopen 24 uur: totalen, geblokkeerd, risico, topbestemmingen en -clients."""
    if time.time() - FLOWS["at"] < 300:
        return FLOWS
    FLOWS["at"] = time.time()
    end = int(time.time() * 1000)
    base = {"timestampFrom": end - 86400000, "timestampTo": end, "pageNumber": 0}
    try:
        res = _v2("/traffic-flows", dict(base, pageSize=500))
        items = res.get("data") if isinstance(res, dict) else res
        items = items or []
        DIAG["flows"] = {"ok": True, "info": _shape({k: v for k, v in res.items() if k != "data"} if isinstance(res, dict) else {}) + " item " + _shape(items[0] if items else {})}
        total = res.get("total_element_count") if isinstance(res, dict) else None

        def count(extra):
            try:
                r = _v2("/traffic-flows", dict(base, pageSize=1, **extra))
                return r.get("total_element_count") if isinstance(r, dict) else None
            except Exception:
                return None
        blocked = count({"action": ["blocked"]})
        risky = count({"risk": ["medium", "high"]})
        dests, srcs, n_block, n_risk = {}, {}, 0, 0
        for f in items:
            dest = _pick(f, "destination.domain_name", "destination.domain", "destination.hostname", "destination.client_name", "destination.ip", "dst.domain", "dst.ip", "destination_ip")
            country = _pick(f, "destination.region", "destination.country", "destination.country_code", "dst.country")
            src = _pick(f, "source.client_name", "source.name", "source.hostname", "src.name")
            mac = str(_pick(f, "source.mac", "source.client_mac", "src.mac") or "").lower()
            src = src or NAMES.get(mac) or _pick(f, "source.ip", "src.ip")
            act = str(_pick(f, "action", "verdict") or "").lower()
            risk = str(_pick(f, "risk", "risk_level") or "").lower()
            n_block += act.startswith("block")
            n_risk += risk in ("medium", "high", "suspicious")
            if dest:
                e = dests.setdefault(str(dest), {"name": str(dest), "country": str(country or "")[:2].upper() if country and len(str(country)) <= 3 else "", "n": 0, "blocked": 0})
                e["n"] += 1
                e["blocked"] += act.startswith("block")
            if src:
                srcs[str(src)] = srcs.get(str(src), 0) + 1
        FLOWS.update(error=None, data={
            "total": total if isinstance(total, int) else len(items), "sample": len(items),
            "blocked": blocked if isinstance(blocked, int) else n_block, "risky": risky if isinstance(risky, int) else n_risk,
            "exact": isinstance(total, int),
            "dests": sorted(dests.values(), key=lambda x: -x["n"])[:10],
            "clients": [{"name": k, "n": v} for k, v in sorted(srcs.items(), key=lambda x: -x[1])[:10]]})
    except Exception as e:
        FLOWS["error"] = str(e)
        DIAG["flows"] = {"ok": False, "info": str(e)}
        print(f"Flows niet op te halen ({e}).", flush=True)
    return FLOWS


# ---------------------------------------------------------------- logboek
EVENTS = {"items": None, "at": 0, "error": None, "source": None}
EVENT_KIND = [("EVT_WU", "Wifi"), ("EVT_WG", "Wifi"), ("EVT_LU", "Client"), ("EVT_LG", "Client"), ("EVT_AP", "Toegangspunt"),
              ("EVT_SW", "Switch"), ("EVT_GW", "Gateway"), ("EVT_IPS", "Beveiliging"), ("EVT_FW", "Firewall"), ("EVT_AD", "Beheer")]
_MAC_TAG = __import__("re").compile(r"(\w+)\[([0-9a-fA-F:]{17})\]")


def nice_msg(msg):
    def sub(m):
        return NAMES.get(m.group(2).lower()) or m.group(1)
    return _MAC_TAG.sub(sub, str(msg or "")).strip()


_PH = __import__("re").compile(r"\{\s*([A-Za-z0-9_]+)\s*\}|<([A-Z0-9_]+)>|\$\{([A-Za-z0-9_]+)\}")
SYSLOG_CAT = {"SECURITY": "Beveiliging", "AUDIT": "Beheer", "CLIENT": "Clients", "CLIENTS": "Clients", "DEVICE": "Apparaten",
              "DEVICES": "Apparaten", "INTERNET": "Internet", "VPN": "VPN", "UPDATE": "Updates", "SYSTEM": "Systeem",
              "MONITORING": "Bewaking", "POWER": "Stroom", "CRITICAL": "Kritiek"}
SEVERITY = {"VERY_LOW": 1, "LOW": 1, "INFO": 1, "MEDIUM": 2, "WARNING": 2, "HIGH": 3, "VERY_HIGH": 4, "CRITICAL": 4}


def fill(template, params):
    """Vult de plaatshouders in een systeemlogbericht met namen uit de parameters."""
    params = params or {}

    def sub(m):
        key = m.group(1) or m.group(2) or m.group(3)
        p = params.get(key) if isinstance(params, dict) else None
        if p is None and isinstance(params, dict):
            p = next((v for k, v in params.items() if str(k).upper() == key.upper()), None)
        if isinstance(p, dict):
            return str(p.get("name") or p.get("display_name") or p.get("hostname") or p.get("value") or p.get("id") or key)
        return str(p) if p not in (None, "") else m.group(0)
    return _PH.sub(sub, str(template or "")).strip()


def events():
    """Systeemlogboek van de console (elke minuut ververst); oudere consoles: de klassieke gebeurtenissen."""
    if time.time() - EVENTS["at"] < 60:
        return EVENTS
    EVENTS["at"] = time.time()
    items, err = [], None
    try:
        end = int(time.time() * 1000)
        res = _v2("/system-log/all", {"timestampFrom": end - 2 * 86400000, "timestampTo": end, "pageNumber": 0, "pageSize": 200})
        rows = (res.get("data") if isinstance(res, dict) else res) or []
        DIAG["logboek"] = {"ok": True, "info": "system-log " + _shape(rows[0] if rows else {})}
        for e in rows:
            params = e.get("parameters") or {}
            msg = fill(e.get("message_raw") or e.get("message") or e.get("msg"), params)
            title = fill(e.get("title_raw") or e.get("title"), params)
            if not msg:
                msg = title or str(e.get("key") or "")
            ts = e.get("timestamp") or e.get("time") or 0
            cat = str(e.get("category") or "").upper()
            items.append({"t": int(ts) // 1000 if ts > 1e11 else int(ts), "msg": nice_msg(msg), "title": title if title != msg else "",
                          "kind": SYSLOG_CAT.get(cat, cat.replace("_", " ").title() or "Systeem"),
                          "sev": SEVERITY.get(str(e.get("severity") or "").upper(), 0), "key": e.get("key") or ""})
        EVENTS["source"] = "system-log"
    except Exception as e:
        err = e
    if not items and err is not None:
        try:
            for e in legacy("/stat/event?within=48&_limit=150&_sort=-time"):
                key = str(e.get("key") or "")
                items.append({"t": int(e.get("time") or 0) // 1000, "msg": nice_msg(e.get("msg") or key), "title": "", "sev": 0,
                              "kind": next((lbl for pre, lbl in EVENT_KIND if key.startswith(pre)), "Systeem"), "key": key})
            EVENTS["source"] = "event"
            DIAG["logboek"] = {"ok": True, "info": "klassieke gebeurtenissen"}
            err = None
        except Exception as e2:
            DIAG["logboek"] = {"ok": False, "info": f"{err} / {e2}"}
    if err is None:
        EVENTS.update(items=sorted(items, key=lambda x: -x["t"])[:200], error=None)
    else:
        EVENTS["error"] = str(err)
        print(f"Logboek niet op te halen ({err}).", flush=True)
    return EVENTS


# ---------------------------------------------------------------- inzichten (gebruik per app)
INSIGHTS = {"data": None, "at": 0, "error": None}
DPI_NAMES = {"apps": {}, "cats": {}, "at": 0}


def dpi_names():
    if time.time() - DPI_NAMES["at"] < 86400 and DPI_NAMES["apps"]:
        return DPI_NAMES
    try:
        DPI_NAMES["apps"] = {int(a["id"]): a.get("name") for a in api_all("/dpi/applications") if str(a.get("id", "")).isdigit()}
        DPI_NAMES["cats"] = {int(a["id"]): a.get("name") for a in api_all("/dpi/categories") if str(a.get("id", "")).isdigit()}
        DPI_NAMES["at"] = time.time()
    except Exception as e:
        print(f"Namen van apps niet op te halen ({e}).", flush=True)
    return DPI_NAMES


def app_name(app, cat):
    n = dpi_names()
    try:
        app, cat = int(app), int(cat)
    except (TypeError, ValueError):
        return str(app), str(cat)
    name = n["apps"].get((cat << 16) | app) or n["apps"].get(app)
    if app == 65535 or cat == 255:
        name = name or "Niet herkend"
    return name or f"App {app}", n["cats"].get(cat) or ""


def insights():
    """Dataverbruik per app over de afgelopen 24 uur, elke 10 minuten ververst."""
    if time.time() - INSIGHTS["at"] < 600:
        return INSIGHTS
    INSIGHTS["at"] = time.time()
    end = int(time.time() * 1000)
    ref = urllib.parse.quote(_site_cache.get("ref") or "default")
    url = (f"https://{UNIFI_HOST}/proxy/network/v2/api/site/{ref}/traffic?start={end - 86400000}&end={end}"
           f"&includeTrafficUsage=true&includeUnidentified=true")
    try:
        req = urllib.request.Request(url, headers={"X-API-KEY": UNIFI_API_KEY, "Accept": "application/json"})
        with urllib.request.urlopen(req, context=CTX, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8"))
        rows = []
        for a in res.get("total_usage_by_app") or []:
            name, cat = app_name(a.get("application"), a.get("category"))
            rx, tx = int(a.get("bytes_received") or 0), int(a.get("bytes_transmitted") or 0)
            rows.append({"name": name, "cat": cat, "rx": rx, "tx": tx, "total": int(a.get("total_bytes") or rx + tx)})
        rows.sort(key=lambda x: -x["total"])
        per_client = {}
        for c in res.get("client_usage_by_app") or []:
            mac = str((c.get("client") or {}).get("mac") or "").lower()
            apps = []
            for a in c.get("usage_by_app") or []:
                name, cat = app_name(a.get("application"), a.get("category"))
                apps.append({"name": name, "cat": cat, "total": int(a.get("total_bytes") or 0)})
            per_client[mac] = sorted(apps, key=lambda x: -x["total"])[:6]
        DIAG["apps"] = {"ok": True, "info": _shape({k: v for k, v in res.items() if k != "client_usage_by_app"})}
        INSIGHTS.update(error=None, data={"apps": rows[:40], "count": len(rows), "perClient": per_client,
                                          "rx": sum(r["rx"] for r in rows), "tx": sum(r["tx"] for r in rows)})
    except Exception as e:
        INSIGHTS["error"] = str(e)
        DIAG["apps"] = {"ok": False, "info": str(e)}
        print(f"Gebruik per app niet op te halen ({e}).", flush=True)
    return INSIGHTS
FP_URL = "https://static.ui.com/fingerprint/0/{id}_{s}x{s}.png"
_fp_fail = {"logged": False}


def fp_file(fp_id, want):
    """Bewaard client-icoon (zelfde als in UniFi); None als het niet bestaat."""
    if not str(fp_id).isdigit() or len(str(fp_id)) > 9:
        return None
    s = 257 if want > 129 else 129
    os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, f"fp{fp_id}_{s}.png")
    if os.path.exists(path + ".none"):
        return None
    if not os.path.exists(path):
        try:
            req = urllib.request.Request(FP_URL.format(id=fp_id, s=s), headers={"User-Agent": "unifi-dashboard-ha-app"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                open(path + ".none", "w").close()
            return None
        except Exception as e:
            if not _fp_fail["logged"]:
                _fp_fail["logged"] = True
                print(f"Client-iconen niet op te halen ({e}); de app gebruikt wifi- en kabelpictogrammen.", flush=True)
            return None
        if not data.startswith(b"\x89PNG"):
            return None
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    return path


def is_public(ip):
    try:
        return ipaddress.ip_address(str(ip).strip()).is_global
    except ValueError:
        return False


def _norm(v):
    return "".join(ch for ch in str(v or "").lower() if ch.isalnum())


def uidb_index():
    """{genormaliseerde naam: (icon-id, [[b, h], ...])}, één keer per dag ververst."""
    now = time.time()
    if _uidb["index"] is not None and now - _uidb["loaded"] < 86400:
        return _uidb["index"]
    if now - _uidb["tried"] < 900:
        return _uidb["index"] or {}
    _uidb["tried"] = now
    try:
        req = urllib.request.Request(UIDB_URL, headers={"User-Agent": "unifi-dashboard-ha-app", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            feed = json.loads(r.read().decode("utf-8"))
        items = (feed.get("devices") or feed.get("products") or []) if isinstance(feed, dict) else feed
        index = {}
        for e in items:
            icon = e.get("icon") or {}
            if not icon.get("id"):
                continue
            line = str((e.get("line") or {}).get("id") or "")
            prod = e.get("product") or {}
            names = [e.get("sku"), e.get("name"), e.get("abbrev"), prod.get("name"), prod.get("abbrev"), e.get("sysid")]
            names += list(e.get("shortnames") or []) + list(e.get("sysids") or [])
            entry = (str(icon["id"]), icon.get("resolutions") or [[129, 129], [257, 257]])
            for n in names:
                k = _norm(n)
                if k and (k not in index or "network" in line):
                    index[k] = entry
        _uidb.update(index=index, loaded=now)
        print(f"Productcatalogus geladen: {len(index)} namen.", flush=True)
    except Exception as e:
        print(f"Productcatalogus van Ubiquiti niet op te halen ({e}); de app gebruikt eenvoudige iconen.", flush=True)
    return _uidb["index"] or {}


def image_for(*names):
    index = uidb_index()
    for n in names:
        k = _norm(n)
        if not k:
            continue
        if k in index:
            return index[k][0]
        for alt in (k.replace("poe", ""), k + "poe"):
            if alt in index:
                return index[alt][0]
    return None


def icon_file(icon_id, want):
    """Pad naar de bewaarde afbeelding; haalt hem op als hij er nog niet is."""
    if not all(c in "0123456789abcdef-" for c in icon_id.lower()) or len(icon_id) > 64:
        return None
    res = next((r for k, r in (_uidb["index"] or {}).items() if r[0] == icon_id), (icon_id, [[129, 129], [257, 257]]))[1]
    sizes = sorted((int(w), int(h)) for w, h in res if int(w) == int(h))
    w, h = next(((a, b) for a, b in sizes if a >= want), sizes[-1] if sizes else (129, 129))
    os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, f"{icon_id}_{w}.png")
    if not os.path.exists(path):
        req = urllib.request.Request(ICON_URL.format(id=icon_id, w=w, h=h), headers={"User-Agent": "unifi-dashboard-ha-app"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        if not data.startswith(b"\x89PNG"):
            return None
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    return path


_port_state = {"ok": None, "retry": 0}
ISP = {"name": None}  # naam van de internetprovider, uit de klassieke API
WAN_PORTS = {}  # {apparaat-mac: {poortnummer, ...}} poorten die naar internet gaan


def port_map():
    """{mac: (switch-mac, poort)} voor bekabelde clients en apparaten. Leeg als de console dit niet toestaat."""
    if _port_state["ok"] is False and time.time() < _port_state["retry"]:
        return {}
    try:
        out = {}
        nets = {}
        try:
            for n in legacy("/rest/networkconf"):
                vlan = n.get("vlan") if n.get("vlan_enabled") or n.get("vlan") else None
                nets[n.get("_id")] = (n.get("name"), int(vlan) if str(vlan or "").isdigit() else None)
        except Exception:
            pass
        CLIENT_EXTRA.clear()
        for c in legacy("/stat/sta"):
            net = nets.get(c.get("network_id")) or (c.get("network"), None)
            vlan = c.get("vlan") if str(c.get("vlan") or "").isdigit() and int(c.get("vlan")) > 0 else net[1]
            wifi = not c.get("is_wired")
            CLIENT_EXTRA[str(c.get("mac", "")).lower()] = {
                "ssid": c.get("essid") if wifi else None,
                "network": c.get("network") or net[0], "vlan": int(vlan) if vlan else None,
                "signal": c.get("signal") if wifi and isinstance(c.get("signal"), (int, float)) else None,
                "band": {"ng": 2.4, "na": 5, "6e": 6}.get(c.get("radio")) if wifi else None,
                "channel": c.get("channel") if wifi else None,
                "experience": c.get("satisfaction") if isinstance(c.get("satisfaction"), (int, float)) and c.get("satisfaction") >= 0 else None,
                "txRate": c.get("tx_rate") if wifi else None, "rxRate": c.get("rx_rate") if wifi else None}
            NAMES[str(c.get("mac", "")).lower()] = c.get("name") or c.get("hostname")
            fp = c.get("dev_id_override") or c.get("dev_id")
            if fp and str(fp).isdigit():
                CLIENT_FP[str(c.get("mac", "")).lower()] = int(fp)
            if c.get("sw_mac") and c.get("sw_port") is not None and c.get("is_wired", True):
                out[str(c.get("mac", "")).lower()] = (str(c["sw_mac"]).lower(), int(c["sw_port"]), None)
        WAN_PORTS.clear()
        isp = None
        for d in legacy("/stat/device"):
            for g in (d.get("geo_info") or {}).values():
                if isinstance(g, dict) and not isp:
                    isp = g.get("isp_name") or g.get("isp_organization")
            if d.get("model"):
                LEGACY_MODEL[str(d.get("mac", "")).lower()] = d.get("model")
            st = [{"name": x.get("name") or x.get("mount_point") or "Opslag", "size": x.get("size"), "used": x.get("used")}
                  for x in d.get("storage") or [] if isinstance(x, dict) and x.get("size")]
            if st:
                STORAGE[str(d.get("mac", "")).lower()] = st
            for k in ("lan_ip", "connect_request_ip", "ip"):
                if d.get(k) and not is_public(d[k]):
                    LEGACY_LAN_IP[str(d.get("mac", "")).lower()] = d[k]
                    break
            for pt in d.get("port_table") or []:
                if str(pt.get("network_name") or "").lower().startswith("wan") and pt.get("port_idx") is not None:
                    WAN_PORTS.setdefault(str(d.get("mac", "")).lower(), set()).add(int(pt["port_idx"]))
            up = d.get("uplink") or d.get("last_uplink") or {}
            if up.get("uplink_mac") and up.get("uplink_remote_port") is not None:
                local = up.get("port_idx")
                out[str(d.get("mac", "")).lower()] = (str(up["uplink_mac"]).lower(), int(up["uplink_remote_port"]),
                                                      int(local) if local is not None else None)
        if not isp:
            try:
                for h in legacy("/stat/health"):
                    if h.get("subsystem") in ("wan", "www") and not isp:
                        isp = h.get("isp_name") or h.get("isp_organization")
            except Exception:
                pass
        ISP["name"] = str(isp).strip() if isp else None
        _port_state.update(ok=True)
        return out
    except Exception as e:
        if _port_state["ok"] is not False:
            print(f"Poortinformatie niet beschikbaar ({e}); probeer het over 10 minuten opnieuw.", flush=True)
        _port_state.update(ok=False, retry=time.time() + 600)
        return {}


def api_all(path):
    items, offset = [], 0
    while True:
        page = api(path, {"offset": offset, "limit": 200})
        data = page.get("data", []) if isinstance(page, dict) else page
        items.extend(data)
        total = page.get("totalCount", len(items)) if isinstance(page, dict) else len(items)
        offset += len(data)
        if not data or offset >= total:
            return items


def safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def kind_of(model, features):
    m = str(model or "").upper()
    if m.startswith(GATEWAY_MODELS):
        return "gateway"
    if "accessPoint" in features or m.startswith(AP_MODELS):
        return "ap"
    if "switching" in features or m.startswith(SWITCH_MODELS):
        return "switch"
    return "other"


_site_cache = {}


def collect_live():
    info = safe(lambda: api("/info"), {})
    if "id" not in _site_cache:
        sites = api_all("/sites")
        if not sites:
            raise RuntimeError("Geen sites gevonden op de console.")
        site = next((s for s in sites if UNIFI_SITE and UNIFI_SITE.lower() in
                     (str(s.get("name", "")).lower(), str(s.get("internalReference", "")).lower())),
                    sites[0])
        _site_cache.update(id=site["id"], name=site.get("name") or "Standaard",
                           ref=site.get("internalReference") or "default")
    sid = _site_cache["id"]

    raw_devices = api_all(f"/sites/{sid}/devices")

    def enrich(d):
        detail = safe(lambda: api(f"/sites/{sid}/devices/{d['id']}"), {})
        stats = safe(lambda: api(f"/sites/{sid}/devices/{d['id']}/statistics/latest"), {})
        feats = d.get("features") or detail.get("features") or []
        if isinstance(feats, dict):
            feats = list(feats.keys())
        ifs = detail.get("interfaces") or {}
        ports = []
        for p in ifs.get("ports") or []:
            poe = p.get("poe") or {}
            ports.append({"idx": p.get("idx"), "state": p.get("state"), "speed": p.get("speedMbps"),
                          "max": p.get("maxSpeedMbps"), "connector": p.get("connector"),
                          "poe": poe.get("state") == "UP" or (poe.get("enabled") and p.get("state") == "UP"
                                                              and poe.get("state") is None)})
        rstats = {r.get("frequencyGHz"): r for r in ((stats.get("interfaces") or {}).get("radios") or [])}
        radios = [{"band": r.get("frequencyGHz"), "channel": r.get("channel"),
                   "width": r.get("channelWidthMHz"), "standard": r.get("wlanStandard"),
                   "retries": (rstats.get(r.get("frequencyGHz")) or {}).get("txRetriesPct")}
                  for r in ifs.get("radios") or []]
        uplink = stats.get("uplink") or {}
        return {
            "id": d.get("id"),
            "name": d.get("name") or d.get("model") or d.get("macAddress"),
            "model": d.get("model"),
            "ip": d.get("ipAddress"),
            "mac": d.get("macAddress"),
            "state": d.get("state"),
            "kind": kind_of(d.get("model"), feats),
            "uplinkId": (detail.get("uplink") or {}).get("deviceId"),
            "firmware": detail.get("firmwareVersion"),
            "updatable": bool(detail.get("firmwareUpdatable")),
            "uptime": stats.get("uptimeSec"),
            "cpu": stats.get("cpuUtilizationPct"),
            "mem": stats.get("memoryUtilizationPct"),
            "load": stats.get("loadAverage1Min"),
            "tx": uplink.get("txRateBps"),
            "rx": uplink.get("rxRateBps"),
            "ports": ports,
            "radios": radios,
        }

    with ThreadPoolExecutor(max_workers=8) as ex:
        devices = list(ex.map(enrich, raw_devices))

    clients = [{
        "name": c.get("name") or c.get("macAddress") or c.get("ipAddress") or "Onbekend",
        "ip": c.get("ipAddress"),
        "mac": c.get("macAddress"),
        "type": c.get("type"),
        "uplink": c.get("uplinkDeviceId"),
        "since": c.get("connectedAt"),
    } for c in api_all(f"/sites/{sid}/clients")]

    ports = port_map()
    by_mac = {str(d.get("mac") or "").lower(): d for d in devices}
    ins = insights()
    for c in clients:
        m = str(c.get("mac") or "").lower()
        c["fp"] = CLIENT_FP.get(m)
        c.update(CLIENT_EXTRA.get(m) or {})
        if ins["data"]:
            c["apps"] = ins["data"]["perClient"].get(m) or []
    for c in clients:
        hit = ports.get(str(c.get("mac") or "").lower())
        if hit and hit[0] in by_mac:
            c["port"] = hit[1]
    for d in devices:
        code = LEGACY_MODEL.get(str(d.get("mac") or "").lower())
        d["img"] = image_for(d.get("model"), code)
        if not d["img"] and _uidb["index"] and (d.get("model"), code) not in _NO_IMG:
            _NO_IMG.add((d.get("model"), code))
            print(f"Geen productafbeelding gevonden voor {d.get('model')} ({code or 'geen modelcode'}).", flush=True)
        d["wanPorts"] = sorted(WAN_PORTS.get(str(d.get("mac") or "").lower(), ()))
        hit = ports.get(str(d.get("mac") or "").lower())
        if hit and hit[0] in by_mac:
            d["uplinkPort"] = hit[1]
            d["localUplinkPort"] = hit[2]
            d["uplinkId"] = d.get("uplinkId") or by_mac[hit[0]]["id"]

    gw = next((d for d in devices if d["kind"] == "gateway"), None)
    wan = {"rx": gw["rx"], "tx": gw["tx"], "gateway": gw["name"], "isp": ISP["name"]} if gw and gw["rx"] is not None else None
    snap_ins = {k: v for k, v in (ins["data"] or {}).items() if k != "perClient"} if ins["data"] else None
    out = finish(_site_cache["name"], info.get("applicationVersion"), devices, clients, wan)
    for d in devices:
        if d.get("mac"):
            NAMES[str(d["mac"]).lower()] = d["name"]
    ev = events()
    fl = flows()
    bk = backups()
    for d in devices:
        st = STORAGE.get(str(d.get("mac") or "").lower())
        if st:
            d["storage"] = st
    DIAG["poorten"] = {"ok": bool(_port_state["ok"]), "info": "klassieke API" if _port_state["ok"] else "niet beschikbaar"}
    DIAG["productcatalogus"] = {"ok": bool(_uidb["index"]), "info": f"{len(_uidb['index'] or {})} namen"}
    out.update(insights=snap_ins, insightsError=None if ins["data"] else ins["error"],
               events=ev["items"], eventsError=None if ev["items"] is not None else ev["error"],
               flows=fl["data"], flowsError=None if fl["data"] else fl["error"], diag=DIAG,
               backups=bk["items"], backupsError=None if bk["items"] is not None else bk["error"])
    return out


def finish(site, version, devices, clients, wan):
    # De API geeft bij de gateway het internetadres; toon dat apart en gebruik het interne adres als IP-adres.
    for d in devices:
        if d.get("ip") and is_public(d["ip"]):
            d["wanIp"] = d["ip"]
            d["ip"] = LEGACY_LAN_IP.get(str(d.get("mac") or "").lower()) or d["ip"]
    names = {d["id"]: d["name"] for d in devices}
    counts = {}
    for c in clients:
        c["uplinkName"] = names.get(c["uplink"])
        c["key"] = str(c.get("mac") or c.get("name") or "").lower()
        counts[c["uplink"]] = counts.get(c["uplink"], 0) + 1
    order = {"gateway": 0, "switch": 1, "ap": 2, "other": 3}
    attached = {}
    for c in clients:
        if c.get("port") is not None:
            attached.setdefault((c["uplink"], c["port"]), []).append({"name": c["name"], "kind": "client",
                                                                      "key": str(c.get("mac") or c.get("name") or "").lower(),
                                                                      "fp": c.get("fp"), "type": c.get("type")})
    for d in devices:
        if d.get("uplinkPort") is not None:
            attached.setdefault((d["uplinkId"], d["uplinkPort"]), []).append({"name": d["name"], "kind": d["kind"], "id": d["id"]})
            parent = next((x for x in devices if x["id"] == d["uplinkId"]), None)
            if parent and d.get("localUplinkPort") is not None:
                attached.setdefault((d["id"], d["localUplinkPort"]), []).append(
                    {"name": parent["name"], "kind": parent["kind"], "id": parent["id"], "uplink": True})
    for d in devices:
        for idx in d.get("wanPorts") or []:
            attached.setdefault((d["id"], idx), []).insert(0, {"name": "Internet (WAN)", "kind": "wan"})
    for d in devices:
        d["gateway"] = d["kind"] == "gateway"
        d["clients"] = counts.get(d["id"], 0)
        d["ports"] = [dict(p, who=sorted(attached.get((d["id"], p.get("idx")), []), key=lambda w: (w["kind"] == "client", w["name"].lower())))
                      for p in d.get("ports") or []]
    devices.sort(key=lambda d: (order.get(d["kind"], 9), str(d["name"]).lower()))
    return {"site": site, "version": version, "devices": devices, "clients": clients, "wan": wan}


# ---------------------------------------------------------------- demo-data
_rng = random.Random(7)


def _demo_ports(n, used, poe, sfp=0, fast=()):
    ports = []
    for i in range(1, n + 1):
        up = i in used
        speed = 2500 if i in fast else (100 if up and _rng.random() < 0.15 else 1000)
        ports.append({"idx": i, "state": "UP" if up else "DOWN", "speed": speed if up else None,
                      "max": 2500 if i in fast else 1000, "connector": "RJ45", "poe": up and i in poe})
    for j in range(sfp):
        idx = n + j + 1
        ports.append({"idx": idx, "state": "UP" if j == 0 else "DOWN", "speed": 10000 if j == 0 else None,
                      "max": 10000, "connector": "SFPPLUS", "poe": False})
    return ports


_DEMO_DEVICES = [
    dict(id="gw", name="Dream Machine Pro", model="UDM-Pro", ip="192.168.1.1", state="ONLINE", firmware="4.1.13",
         updatable=False, kind="gateway", uplinkId=None, ports=_demo_ports(9, {3, 9}, set(), sfp=2), wanPorts=[9], radios=[]),
    dict(id="sw1", name="Switch meterkast", model="USW-Pro-24-PoE", ip="192.168.1.2", state="ONLINE",
         firmware="7.1.26", updatable=True, kind="switch", uplinkId="gw",
         ports=_demo_ports(24, {1, 2, 3, 5, 6, 9, 10, 13, 17, 18, 21, 24}, {2, 3, 5, 9, 17}, sfp=2, fast={1}),
         radios=[]),
    dict(id="sw2", name="Switch woonkamer", model="USW-Lite-8-PoE", ip="192.168.1.3", state="ONLINE",
         firmware="7.1.26", updatable=False, kind="switch", uplinkId="sw1",
         ports=_demo_ports(8, {1, 2, 4, 5, 7}, {2}), radios=[]),
    dict(id="ap1", name="AP woonkamer", model="U7-Pro", ip="192.168.1.10", state="ONLINE", firmware="8.0.24",
         updatable=False, kind="ap", uplinkId="sw2", ports=[],
         radios=[{"band": 2.4, "channel": 6, "width": 20, "standard": "802.11be"},
                 {"band": 5, "channel": 36, "width": 80, "standard": "802.11be"},
                 {"band": 6, "channel": 37, "width": 160, "standard": "802.11be"}]),
    dict(id="ap2", name="AP zolder", model="U6-Lite", ip="192.168.1.11", state="ONLINE", firmware="6.7.31",
         updatable=True, kind="ap", uplinkId="sw1", ports=[],
         radios=[{"band": 2.4, "channel": 1, "width": 20, "standard": "802.11ax"},
                 {"band": 5, "channel": 100, "width": 80, "standard": "802.11ax"}]),
    dict(id="ap3", name="AP tuin", model="U6-Mesh", ip="192.168.1.12", state="OFFLINE", firmware="6.7.31",
         updatable=False, kind="ap", uplinkId="ap1", ports=[],
         radios=[{"band": 2.4, "channel": 11, "width": 20, "standard": "802.11ax"},
                 {"band": 5, "channel": 44, "width": 40, "standard": "802.11ax"}]),
]
_DEMO_CLIENTS = []
for _i, (_n, _t, _u) in enumerate([
    ("iPhone Sanne", "WIRELESS", "ap1"), ("iPhone Mark", "WIRELESS", "ap1"),
    ("MacBook Pro", "WIRELESS", "ap2"), ("iPad keuken", "WIRELESS", "ap1"),
    ("Sonos woonkamer", "WIRELESS", "ap1"), ("Sonos badkamer", "WIRELESS", "ap2"),
    ("Philips Hue Bridge", "WIRED", "sw2"), ("Home Assistant", "WIRED", "sw1"),
    ("Synology NAS", "WIRED", "sw1"), ("Apple TV", "WIRED", "sw2"),
    ("PlayStation 5", "WIRED", "sw2"), ("Werklaptop", "WIRELESS", "ap2"),
    ("Galaxy Tab", "WIRELESS", "ap1"), ("Thermostaat", "WIRELESS", "ap1"),
    ("Robotstofzuiger", "WIRELESS", "ap1"), ("Printer", "WIRELESS", "ap2"),
    ("Desktop zolder", "WIRED", "sw1"), ("Deurbel", "WIRELESS", "ap1"),
    ("Slimme meter P1", "WIRELESS", "ap1"), ("Kindle", "WIRELESS", "ap2"),
    ("Pixel 8", "WIRELESS", "ap2"), ("Wandtablet", "WIRELESS", "ap1"),
    ("Camera oprit", "WIRED", "sw1"), ("Camera achtertuin", "WIRED", "sw1"), ("Camera voordeur", "WIRED", "sw1"),
    ("Werkplek kantoor", "WIRED", "sw1"), ("Sonos Port", "WIRED", "sw1"), ("Server meterkast", "WIRED", "gw"),
]):
    _DEMO_CLIENTS.append({"name": _n, "ip": f"192.168.1.{100 + _i}", "type": _t, "uplink": _u,
                          "mac": "a4:83:e7:%02x:%02x:%02x" % (_rng.randrange(256), _rng.randrange(256), _i),
                          "since_offset": 1500 if _n == "Pixel 8" else _rng.randint(4000, 9 * 86400)})
_DEMO_START = time.time()


def _demo_curve(t):
    """Dagpatroon in bit/s: rustig 's nachts, piek in de avond."""
    h = time.localtime(t).tm_hour + time.localtime(t).tm_min / 60
    dist = lambda a: min(abs(h - a), 24 - abs(h - a))
    evening = math.exp(-(dist(21) ** 2) / 6) * 210e6
    day = math.exp(-(dist(13) ** 2) / 18) * 70e6
    return 12e6 + evening + day


_DEMO_PORTS = {"Home Assistant": 6, "Synology NAS": 10, "Desktop zolder": 13, "Philips Hue Bridge": 4,
               "Apple TV": 5, "PlayStation 5": 7, "Camera oprit": 2, "Camera achtertuin": 9, "Camera voordeur": 17,
               "Werkplek kantoor": 18, "Sonos Port": 21, "Server meterkast": 3}
_DEMO_UPLINK_PORTS = {"sw1": (10, 25), "sw2": (1, 1), "ap1": (2, None), "ap2": (3, None)}  # (poort boven, eigen poort)


def collect_demo():
    t = time.time()
    rx = _demo_curve(t) * (0.7 + random.random() * 0.6)
    if random.random() < 0.06:
        rx += 300e6 + random.random() * 250e6
    tx = rx * 0.08 + random.random() * 6e6
    up = 12 * 86400 + (t - _DEMO_START)
    devices = []
    for i, base in enumerate(_DEMO_DEVICES):
        online = base["state"] == "ONLINE"
        d = dict(base, mac="f4:e2:c6:00:00:%02x" % i,
                 uptime=up - i * 3600 if online else None,
                 cpu=round(5 + random.random() * 25, 1) if online else None,
                 mem=round(35 + random.random() * 30, 1) if online else None,
                 load=round(0.3 + random.random() * 1.2, 2) if online and base["kind"] == "gateway" else None,
                 tx=(tx if base["kind"] == "gateway" else random.random() * 30e6) if online else None,
                 rx=(rx if base["kind"] == "gateway" else random.random() * 90e6) if online else None)
        d["radios"] = [dict(r, retries=round(2 + random.random() * 9, 1)) if online else dict(r)
                       for r in base["radios"]]
        if base["id"] in _DEMO_UPLINK_PORTS:
            d["uplinkPort"], d["localUplinkPort"] = _DEMO_UPLINK_PORTS[base["id"]]
        devices.append(d)
    clients = [{**{k: v for k, v in c.items() if k != "since_offset"},
                **({"port": _DEMO_PORTS[c["name"]]} if c["name"] in _DEMO_PORTS else {}),
                "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t - c["since_offset"]))}
               for c in _DEMO_CLIENTS]
    iot = {"Philips Hue Bridge", "Thermostaat", "Robotstofzuiger", "Deurbel", "Slimme meter P1", "Camera oprit",
           "Camera achtertuin", "Camera voordeur", "Sonos woonkamer", "Sonos badkamer", "Sonos Port", "Printer"}
    gast = {"Pixel 8", "Galaxy Tab"}
    for c in clients:
        net = ("IoT", 20) if c["name"] in iot else ("Gasten", 30) if c["name"] in gast else ("Thuis", None)
        c.update(network=net[0], vlan=net[1], ssid=(("Thuis-IoT" if net[0] == "IoT" else "Thuis-Gast" if net[0] == "Gasten" else "Thuis")
                                                    if c["type"] == "WIRELESS" else None))
        c["apps"] = _DEMO_CLIENT_APPS.get(c["name"], [])
        if c["type"] == "WIRELESS":
            h = sum(map(ord, c["name"]))
            c.update(signal=-48 - h % 34, band=[5, 5, 2.4, 6][h % 4], channel={5: 36, 2.4: 6, 6: 37}[[5, 5, 2.4, 6][h % 4]],
                     experience=max(35, 100 - (h % 34) * 2), txRate=[866700, 1200900, 144400, 2402000][h % 4], rxRate=[780000, 960000, 72200, 2161000][h % 4])
    out = finish("Thuis", "10.0.140", devices, clients, {"rx": rx, "tx": tx, "gateway": "Dream Machine Pro", "isp": "Glasvezelprovider"})
    for d in out["devices"]:
        if d["kind"] == "gateway":
            d["storage"] = [{"name": "Systeem", "size": 16 * 1024 ** 3, "used": int(6.1 * 1024 ** 3)},
                            {"name": "Gegevens", "size": 112 * 1024 ** 3, "used": int(31.4 * 1024 ** 3)}]
    out["backups"] = [{"t": int(t - 3600 * (5 + 24 * i)), "size": int((4.2 - i * .1) * 1024 ** 2), "version": "10.0.140", "kind": "automatisch"} for i in range(5)]
    out["events"] = [{"t": int(t - o), "msg": m, "kind": k, "key": "", "title": "", "sev": 1 + (len(m) % 3)} for o, m, k in [
        (40, "Camera oprit is verbonden met Switch meterkast poort 2.", "Client"),
        (310, "Wandtablet werd geblokkeerd door de firewallregel Google DNS blokkeren.", "Firewall"),
        (520, "iPad keuken is gewisseld van AP zolder naar AP woonkamer (kanaal 36, -61 dBm).", "Wifi"),
        (1900, "AP tuin heeft geen verbinding meer.", "Toegangspunt"),
        (3600, "Pixel 8 is verbonden met Thuis-Gast.", "Wifi"),
        (7200, "Switch meterkast: firmware-update beschikbaar (7.1.26).", "Switch"),
        (14400, "Werklaptop heeft de verbinding met Thuis verbroken.", "Wifi")]]
    out["flows"] = {"total": 3245647, "sample": 500, "blocked": 44823, "risky": 1, "exact": True,
                    "dests": [{"name": n, "country": c, "n": k, "blocked": b} for n, c, k, b in [
                        ("192.168.1.1", "", 671517, 0), ("192.168.20.1", "", 564331, 0), ("api.camera-cloud.example", "DE", 120779, 0),
                        ("dns.google", "US", 76312, 76312), ("time.apple.com", "US", 40211, 0)]],
                    "clients": [{"name": n, "n": k} for n, k in [("Camera oprit", 812004), ("Home Assistant", 640120), ("Wandtablet", 301233), ("Apple TV", 120544)]]}
    out["insights"] = {"apps": _DEMO_APPS, "count": 77, "rx": sum(a["rx"] for a in _DEMO_APPS), "tx": sum(a["tx"] for a in _DEMO_APPS)}
    return out


_G = 1024 ** 3
_DEMO_APPS = [dict(name=n, cat=c, rx=int(r * _G), tx=int(t * _G), total=int((r + t) * _G)) for n, c, r, t in [
    ("SSL/TLS", "Netwerkprotocollen", 25.4, .8), ("Sony PlayStation", "Games", 7.7, .2), ("MP4", "Media", 6.3, .1),
    ("YouTube", "Media", 4.9, .1), ("Netflix", "Media", 3.8, .05), ("Apple", "Web", 2.1, .3), ("Spotify", "Media", 1.2, .02),
    ("Microsoft", "Web", .9, .2), ("WhatsApp", "Berichten", .4, .2), ("Google", "Web", .5, .05),
    ("Niet herkend", "", .6, .1), ("iCloud", "Cloud", .2, .3)]]
_DEMO_APPS.sort(key=lambda a: -a["total"])
_DEMO_CLIENT_APPS = {
    "PlayStation 5": [{"name": "Sony PlayStation", "cat": "Games", "total": int(7.9 * _G)}, {"name": "YouTube", "cat": "Media", "total": int(1.1 * _G)}],
    "Apple TV": [{"name": "Netflix", "cat": "Media", "total": int(3.8 * _G)}, {"name": "MP4", "cat": "Media", "total": int(2.6 * _G)}],
    "MacBook Pro": [{"name": "SSL/TLS", "cat": "Netwerkprotocollen", "total": int(4.2 * _G)}, {"name": "Microsoft", "cat": "Web", "total": int(.8 * _G)}],
}


def backfill_demo():
    now = int(time.time() // 60) * 60
    for k in range(1440, 0, -1):
        t = now - k * 60
        base = _demo_curve(t) * (0.92 + _rng.random() * 0.16)
        peak = base * (1.2 + _rng.random() * (2.5 if _rng.random() < 0.05 else 0.6))
        MINUTES.append({"t": t, "rx": int(base), "tx": int(base * 0.08 + _rng.random() * 4e6), "max": int(peak)})


# ---------------------------------------------------------------- geschiedenis
def record(now, rx, tx):
    rx, tx = rx or 0, tx or 0
    RAW.append({"t": round(now, 1), "rx": int(rx), "tx": int(tx)})
    m = int(now // 60)
    if _CUR["m"] is not None and m != _CUR["m"] and _CUR["n"]:
        n = _CUR["n"]
        MINUTES.append({"t": _CUR["m"] * 60, "rx": int(_CUR["rx"] / n), "tx": int(_CUR["tx"] / n),
                        "max": int(_CUR["max"])})
    if m != _CUR["m"]:
        _CUR.update(m=m, rx=0.0, tx=0.0, max=0.0, n=0)
    _CUR["rx"] += rx
    _CUR["tx"] += tx
    _CUR["max"] = max(_CUR["max"], rx)
    _CUR["n"] += 1


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            pts = json.load(f)
        cutoff = time.time() - 86400
        MINUTES.extend(p for p in pts if p.get("t", 0) >= cutoff)
    except Exception:
        pass


def save_history():
    try:
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(list(MINUTES), f)
        os.replace(tmp, HISTORY_FILE)
    except Exception:
        pass


def history_for(rng):
    if rng == "6h":
        return list(MINUTES)[-360:]
    if rng == "24h":
        return list(MINUTES)
    return list(RAW)


# ---------------------------------------------------------------- verzamelen
def describe(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return f"De console weigert de API-key ({e.code}). Controleer de API-key in de instellingen."
        if e.code == 404:
            return "De API is niet gevonden (404). Werk UniFi Network bij naar versie 9 of hoger."
        return f"De console gaf een fout: HTTP {e.code}."
    if isinstance(e, urllib.error.URLError):
        return f"De console op {UNIFI_HOST} is niet bereikbaar ({e.reason}). Controleer het adres."
    return f"{type(e).__name__}: {e}"


def collector():
    last_save = time.time()
    while True:
        try:
            snap = collect_demo() if DEMO else collect_live()
            now = time.time()
            with LOCK:
                STATE.update(snap, ok=True, error=None, updated=now)
                if snap.get("wan"):
                    record(now, snap["wan"]["rx"], snap["wan"]["tx"])
            if not DEMO and now - last_save > 300:
                with LOCK:
                    save_history()
                last_save = now
        except Exception as e:  # laatste goede data blijft staan
            with LOCK:
                STATE.update(ok=False, error=describe(e))
        time.sleep(REFRESH_SECONDS)


# ---------------------------------------------------------------- toegang
ALLOWED_USERS = [str(u).strip().lower() for u in (_OPTS.get("allowed_users") or []) if str(u).strip()]
DENIED_PAGE = """<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>No access · Geen toegang</title><style>body{margin:0;font:16px/1.5 system-ui,sans-serif;background:#1c222b;color:#e6ebf2;display:grid;place-items:center;min-height:100vh;padding:24px;box-sizing:border-box}
main{max-width:440px}h1{font-size:22px;margin:0 0 8px}p{color:#a5afbd;margin:0}</style></head><body><main><h1>No access</h1>
<p>Your account is not on the list of allowed users of this dashboard. Ask an administrator to add your username in the settings of the app.</p>
<h1 style="margin-top:28px">Geen toegang</h1>
<p>Je account staat niet in de lijst met toegestane gebruikers van dit dashboard. Vraag een beheerder om je gebruikersnaam toe te voegen in de instellingen van de app.</p></main></body></html>"""


_SEEN_DENIED = set()


def user_label(headers):
    name = (headers.get("X-Remote-User-Name") or "").strip()
    disp = (headers.get("X-Remote-User-Display-Name") or "").strip()
    if name and disp and disp.lower() != name.lower():
        return f"{name} ({disp})"
    return name or disp


def user_allowed(headers):
    """Toegang via Home Assistant: alleen gebruikers uit allowed_users, of iedereen als de lijst leeg is."""
    if not ALLOWED_USERS:
        return True
    names = {(headers.get(h) or "").strip().lower()
             for h in ("X-Remote-User-Name", "X-Remote-User-Display-Name", "X-Remote-User-Id")}
    ok = bool(names & set(ALLOWED_USERS))
    if not ok:
        who = user_label(headers) or "onbekende gebruiker"
        if who not in _SEEN_DENIED:
            _SEEN_DENIED.add(who)
            print(f"Toegang geweigerd voor gebruikersnaam '{who}'. Hoort deze gebruiker erbij? "
                  f"Voeg de naam precies zo toe bij Toegestane gebruikers.", flush=True)
    return ok


def user_footer(headers):
    who = user_label(headers)
    if not who:
        return ""
    return ('<p style="max-width:1180px;margin:0 auto;padding:0 16px 24px;font:13px system-ui,sans-serif;color:#7d8899">'
            'Ingelogd als <b style="color:#b9c2cf">' + html_escape(who) + '</b></p>')

# ---------------------------------------------------------------- webserver
class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not user_allowed(self.headers):
            if "/api/" in self.path:
                self.send(403, json.dumps({"error": "No access to this dashboard. / Geen toegang tot dit dashboard."}), "application/json")
            else:
                who = user_label(self.headers)
                extra = (f"<p style='margin-top:12px'>Signed in as <b>{html_escape(who)}</b>. Pass this name on to an administrator. / Je bent ingelogd als <b>{html_escape(who)}</b>. Geef deze naam door aan een beheerder.</p>"
                         if who else "")
                self.send(403, DENIED_PAGE.replace("</main>", extra + "</main>"), "text/html; charset=utf-8")
            return
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self.send(200, PAGE.replace("</body>", user_footer(self.headers) + "</body>"), "text/html; charset=utf-8")
        elif url.path == "/api/cliimg":
            q = urllib.parse.parse_qs(url.query)
            try:
                path = fp_file(q.get("id", [""])[0], int(q.get("s", ["129"])[0]))
                if not path:
                    raise ValueError("onbekend")
                with open(path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=604800")
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self.send(404, "Geen afbeelding", "text/plain; charset=utf-8")
        elif url.path == "/api/devimg":
            q = urllib.parse.parse_qs(url.query)
            try:
                path = icon_file(q.get("id", [""])[0], min(512, max(25, int(q.get("s", ["129"])[0]))))
                if not path:
                    raise ValueError("onbekend")
                with open(path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=604800")
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self.send(404, "Geen afbeelding", "text/plain; charset=utf-8")
        elif url.path == "/api/summary":
            rng = urllib.parse.parse_qs(url.query).get("range", ["30m"])[0]
            with LOCK:
                payload = dict(STATE, history=history_for(rng), range=rng, refresh=REFRESH_SECONDS,
                               historyMinutes=len(MINUTES))
            self.send(200, json.dumps(payload), "application/json")
        else:
            self.send(404, "Niet gevonden", "text/plain; charset=utf-8")

    def log_message(self, *args):
        pass


PAGE = r"""<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>UniFi netwerk</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#EDF1F6;--surface:#FFFFFF;--sunk:#F4F7FA;--line:#D5DDE8;--line2:#E4E9F0;
  --text:#14213A;--muted:#5C6C84;--faint:#93A1B5;
  --down:#0B978B;--up:#CF7A22;--ok:#24945F;--bad:#D04343;--warn:#B98010;
  --g24:#CF7A22;--g5:#2F7CC0;--g6:#8562D6;--focus:#2F7CC0;
  --sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#0E1622;--surface:#152030;--sunk:#111A28;--line:#233249;--line2:#1C293C;
  --text:#E4EBF5;--muted:#93A3BA;--faint:#5E6F88;
  --down:#3CC5B6;--up:#F0A257;--ok:#4CC787;--bad:#F06969;--warn:#EFC04F;
  --g24:#F0A257;--g5:#69ACE6;--g6:#B39AF2;--focus:#69ACE6}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;color-scheme:light dark;scrollbar-color:var(--line) transparent}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 var(--sans);font-feature-settings:"tnum" 1}
button{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:6px}
.wrap{max-width:1320px;margin:0 auto;padding:28px 16px 48px}
.num{font-variant-numeric:tabular-nums}
.mono{font-family:var(--mono);font-size:13px}

/* kop */
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:22px}
h1{font-size:28px;line-height:1.15;font-weight:600;margin:0;letter-spacing:-.01em}
.status{margin:6px 0 0;color:var(--muted);display:flex;align-items:center;gap:8px}
.status .sd{flex:none}
.stamp{color:var(--faint);font-size:13px;text-align:right}
.hright{display:flex;flex-direction:column;align-items:flex-end;gap:8px}
.openui{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:500;color:var(--text);text-decoration:none;border:1px solid var(--line);border-radius:8px;padding:5px 10px;background:var(--surface)}
.openui:hover{border-color:var(--muted)}
@media (max-width:700px){.hright{align-items:flex-start;width:100%}}
.sd{width:8px;height:8px;border-radius:50%;background:var(--faint);display:inline-block}
.sd.ok{background:var(--ok)} .sd.bad{background:var(--bad)} .sd.warn{background:var(--warn)}
.note{border-radius:10px;padding:10px 14px;margin:0 0 18px;font-size:14px;border:1px solid var(--line);background:var(--surface)}
.note.err{border-color:color-mix(in srgb,var(--bad) 50%,var(--line));background:color-mix(in srgb,var(--bad) 8%,var(--surface))}
.note[hidden]{display:none}

section{margin-bottom:18px}
[hidden]{display:none!important}
.tabs{display:flex;gap:4px;margin:0 0 18px;border-bottom:1px solid var(--line);overflow-x:auto;overflow-y:hidden;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tabs a{padding:8px 14px 10px;color:var(--muted);text-decoration:none;font-weight:500;border-bottom:2px solid transparent;margin-bottom:-1px;white-space:nowrap}
.tabs a:hover{color:var(--text)}
.tabs a[aria-current="page"]{color:var(--text);border-bottom-color:var(--down)}
.back{display:inline-block;color:var(--muted);text-decoration:none;font-size:14px;margin:0 0 12px}
.back::before{content:"‹ ";font-size:17px}
.back:hover{color:var(--text)}
.dhead{display:grid;grid-template-columns:64px 1fr;gap:12px;align-items:start;margin-bottom:16px}
.dhead .gl{color:var(--muted);margin-top:4px}
.dhead h2{font-size:22px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.dhead .sub{color:var(--muted);font-size:14px;margin-top:2px}
.dhead a,.kv a,.cl a,.who .pw a{color:inherit;text-decoration:underline;text-decoration-color:var(--line);text-underline-offset:3px}
.dhead a:hover,.kv a:hover,.cl a:hover,.who .pw a:hover{text-decoration-color:currentColor}
.dgrid{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,340px);gap:18px;align-items:start}
.dgrid > div > section:last-child{margin-bottom:0}
.pbody{padding:14px 20px 18px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:8px 16px;margin:0;font-size:14px}
.kv dt{color:var(--muted)} .kv dd{margin:0;text-align:right;overflow-wrap:anywhere}
.kv .meter{justify-content:flex-end}
.cl{list-style:none;margin:0;padding:0}
.cl li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 12px;padding:9px 0;border-top:1px solid var(--line2);font-size:14px}
.cl li:first-child{border-top:0}
.cl .m{grid-column:1 / -1;color:var(--muted);font-size:13px}
.cl .t{color:var(--muted);font-size:13px;text-align:right}
.dv{cursor:pointer}
.dv:hover{background:color-mix(in srgb,var(--text) 3%,transparent)}
.dv h3 a{color:inherit;text-decoration:none}
.dv:hover h3 a{text-decoration:underline;text-underline-offset:3px}
tr.click{cursor:pointer}
tr.click:hover td{background:color-mix(in srgb,var(--text) 3%,transparent)}
td a.cn{color:inherit;text-decoration:none}
tr.click:hover a.cn{text-decoration:underline;text-underline-offset:3px}
@media (max-width:900px){.dgrid{grid-template-columns:minmax(0,1fr)}}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:14px}
.phead{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;padding:16px 20px 0}
h2{font-size:17px;font-weight:600;margin:0}
.aside{color:var(--muted);font-size:13px}

/* netwerkkaart */
.mapbox{position:relative;overflow-x:auto;padding:18px 20px 22px}
.tree{position:relative;display:inline-flex;min-width:100%}
.br{display:flex;align-items:center}
.kids{display:flex;flex-direction:column;gap:12px;padding-left:40px}
svg.links{position:absolute;left:0;top:0;pointer-events:none;overflow:visible}
svg.links path{fill:none;stroke:var(--line);stroke-width:2}
svg.links path.live{stroke-dasharray:3 7;animation:flow var(--spd,2s) linear infinite}
svg.links path.l1{stroke:color-mix(in srgb,var(--down) 55%,var(--line))}
svg.links path.l2{stroke:color-mix(in srgb,var(--down) 80%,var(--line));stroke-width:2.5}
svg.links path.l3{stroke:var(--down);stroke-width:3}
svg.links path.dead{stroke:var(--bad);stroke-dasharray:4 6;opacity:.6}
@keyframes flow{to{stroke-dashoffset:-20}}
@media (prefers-reduced-motion: reduce){svg.links path.live{animation:none;stroke-dasharray:none}}
svg.links path.v{stroke-dasharray:none;animation:none;stroke-linecap:round;stroke-linejoin:round}
svg.links path.v.dead{stroke-dasharray:none;opacity:.5}
.node{position:relative;z-index:1;width:172px;text-align:left;background:var(--surface);border:1px solid var(--line);
  border-radius:12px;padding:10px 12px;display:grid;grid-template-columns:36px 1fr;column-gap:10px;cursor:pointer}
.node:hover{border-color:color-mix(in srgb,var(--focus) 60%,var(--line))}
.node .gl{grid-row:span 3;color:var(--muted);align-self:start;margin-top:2px}
.node .nm{font-weight:600;font-size:14px;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.node .sub{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.node .nmeta{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px;margin-top:4px;white-space:nowrap}
.node.off{border-style:dashed;border-color:color-mix(in srgb,var(--bad) 55%,var(--line))}
.node.off .nm{color:var(--muted)}
.node.k-gateway{border-width:1.5px}
.inet{width:auto;min-width:0;cursor:pointer;grid-template-columns:30px 1fr;padding:14px 16px}
.inet:hover{border-color:var(--line)}
.inet .rates{display:flex;gap:18px;margin-top:6px}
.rate b{display:block;font-size:30px;line-height:1.05;font-weight:500;letter-spacing:-.01em}
.rate small{font-size:13px;color:var(--muted)}
.rate.d b{color:var(--down)} .rate.u b{color:var(--up)}
.rate span{font-size:12px;color:var(--muted);white-space:nowrap}

/* grafiek */
.seg{display:inline-flex;background:var(--sunk);border:1px solid var(--line);border-radius:9px;padding:2px}
.seg button{background:transparent;border:0;padding:5px 12px;border-radius:7px;cursor:pointer;color:var(--muted);font-size:13px}
.seg button[aria-pressed="true"]{background:var(--surface);color:var(--text);font-weight:600;box-shadow:0 0 0 1px var(--line)}
.chartwrap{position:relative;padding:8px 20px 16px}
canvas{width:100%;height:240px;display:block}
.tip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--line);border-radius:8px;
  padding:6px 10px;font-size:12px;white-space:nowrap;transform:translate(-50%,-110%);box-shadow:0 4px 14px rgb(0 0 0 / .12)}
.tip[hidden]{display:none}
.tip .d{color:var(--down)} .tip .u{color:var(--up)}
.facts{display:flex;gap:28px;flex-wrap:wrap;padding:12px 20px 0}
.fact{font-size:13px;color:var(--muted)}
.fact b{display:block;font-size:19px;font-weight:500;color:var(--text)}
.key{display:inline-block;width:12px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}

/* apparaten */
.devs{margin-top:14px}
.dv{display:grid;grid-template-columns:minmax(200px,1.1fr) minmax(0,2fr) minmax(180px,.9fr);gap:20px;align-items:center;
  padding:16px 20px;border-top:1px solid var(--line2);scroll-margin-top:16px;transition:background-color .6s}
.dv.flash{background:color-mix(in srgb,var(--focus) 10%,transparent);transition:none}
.dv-id{display:grid;grid-template-columns:44px 1fr;column-gap:10px;align-items:start}
.dv-id .gl{color:var(--muted);margin-top:-8px}
.dv h3{margin:0;font-size:15px;font-weight:600;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.dv .sub{color:var(--muted);font-size:13px}
.tag{font-size:11px;font-weight:600;padding:1px 7px;border-radius:999px;white-space:nowrap}
.tag.bad{color:var(--bad);background:color-mix(in srgb,var(--bad) 13%,transparent)}
.tag.warn{color:var(--warn);background:color-mix(in srgb,var(--warn) 15%,transparent)}
.tag.new{color:var(--down);background:color-mix(in srgb,var(--down) 13%,transparent)}
.dv-stats{display:grid;gap:3px;font-size:13px;margin:0}
.st{display:flex;justify-content:space-between;align-items:center;gap:12px}
.dv-stats dt{color:var(--muted)} .dv-stats dd{margin:0;text-align:right}
.meter{display:flex;align-items:center;gap:8px;justify-content:flex-end}
.meter i{display:block;width:64px;height:5px;border-radius:3px;background:var(--line2);overflow:hidden}
.meter i b{display:block;height:100%;background:var(--muted);border-radius:3px}
.meter.hot i b{background:var(--warn)}
.vis-cap{font-size:12px;color:var(--muted);margin-top:8px}
.offline{color:var(--muted);font-size:13px}

/* poorten als frontpaneel */
.ports{display:inline-flex;flex-direction:column;gap:4px;background:var(--sunk);border:1px solid var(--line);border-radius:8px;padding:6px;max-width:100%;overflow-x:auto}
.prow{display:flex;gap:4px}
.pt{position:relative;width:24px;height:20px;border-radius:4px;display:grid;place-items:center;flex:none;
  border:1px solid var(--line);background:var(--surface);font-family:var(--mono);font-size:9px;color:var(--faint)}
.pt.p100{background:color-mix(in srgb,var(--up) 22%,var(--surface));border-color:color-mix(in srgb,var(--up) 55%,var(--line));color:var(--text)}
.pt.p1g{background:color-mix(in srgb,var(--ok) 22%,var(--surface));border-color:color-mix(in srgb,var(--ok) 55%,var(--line));color:var(--text)}
.pt.p25{background:color-mix(in srgb,var(--down) 28%,var(--surface));border-color:var(--down);color:var(--text)}
.pt.sfp{border-radius:2px;width:30px}
.pt.poe::after{content:"";position:absolute;top:-3px;right:-3px;width:7px;height:7px;border-radius:50%;background:var(--g5);border:1.5px solid var(--sunk)}
.pgap{width:8px;flex:none}
.pt.has{cursor:pointer}
.hwi{display:block}
.fgrid{display:grid;grid-template-columns:1fr 1fr;gap:8px 28px}
@media (max-width:700px){.fgrid{grid-template-columns:1fr}}
.fact.bad b{color:var(--bad)}
.flg{width:22px;height:16px;object-fit:cover;border-radius:2px;flex:none;display:inline-grid;place-items:center;color:var(--muted)}
.flg.globe{height:18px}
.diag{margin:0 0 18px;font-size:13px;color:var(--muted)}
.diag summary{cursor:pointer;width:max-content}
.diag ul{list-style:none;margin:10px 0 0;padding:0}
.diag li{padding:6px 0;border-top:1px solid var(--line2)}
.diag code{display:block;font-family:var(--mono);font-size:11px;overflow-wrap:anywhere;margin-top:2px}
.okc{color:var(--ok)} .errc{color:var(--bad)}
.sig{display:inline-flex;align-items:flex-end;gap:2px;white-space:nowrap}
.sig i{width:3px;border-radius:1px;background:var(--line);display:inline-block}
.sig i:nth-child(1){height:5px}.sig i:nth-child(2){height:8px}.sig i:nth-child(3){height:11px}.sig i:nth-child(4){height:14px}
.sig.ok i.on{background:var(--ok)}.sig.warn i.on{background:var(--warn)}.sig.bad i.on{background:var(--bad)}
.sig .num{margin-left:6px;font-size:13px;color:var(--muted);align-self:center}
.checks{list-style:none;margin:0;padding:0}
.checks li{display:grid;grid-template-columns:30px 1fr;gap:12px;padding:10px 0;border-top:1px solid var(--line2)}
.checks li:first-child{border-top:0}
.checks .ck{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:13px}
.checks .good .ck{color:var(--ok);border:1.5px solid var(--ok)}
.checks .todo .ck{color:var(--warn);border:1.5px solid var(--warn)}
.checks b{font-weight:600}.checks p{margin:2px 0 0;color:var(--muted);font-size:14px}
.checks a,.log a{color:inherit;text-decoration:underline;text-decoration-color:var(--line);text-underline-offset:3px}
.note2{margin:12px 0 0;color:var(--faint);font-size:12px}
.log{list-style:none;margin:0;padding:0}
.log li{padding:10px 0;border-top:1px solid var(--line2)}
.log li:first-child{border-top:0}
.log p{margin:0;font-size:14px;line-height:1.45}
.log .lm{display:flex;gap:10px;align-items:center;margin-top:4px;font-size:12px;color:var(--muted)}
.log .lt{border:1px solid var(--line);border-radius:999px;padding:0 8px}
.log .lm{flex-wrap:wrap}
.log .ltt{color:var(--text);opacity:.8}
.sev{display:inline-flex;gap:2px}.sev i{width:9px;height:4px;border-radius:2px;background:var(--line)}
.sev.s1 i:nth-child(-n+1){background:var(--ok)}.sev.s2 i:nth-child(-n+2){background:var(--warn)}
.sev.s3 i:nth-child(-n+3){background:var(--up)}.sev.s4 i{background:var(--bad)}
.apps{list-style:none;margin:0;padding:0}
.apps li{display:grid;grid-template-columns:minmax(140px,1.2fr) 2fr 84px;gap:14px;align-items:center;padding:8px 0;border-top:1px solid var(--line2);font-size:14px}
.apps li:first-child{border-top:0}
.apps .an b{font-weight:500;display:block;overflow-wrap:anywhere} .apps .an span{font-size:12px;color:var(--muted)}
.apps .ab{height:6px;border-radius:3px;background:var(--line2);overflow:hidden}
.apps .ab i{display:block;height:100%;background:var(--down);border-radius:3px}
.apps .av{text-align:right;white-space:nowrap} .apps .pc{display:block;font-size:12px;color:var(--muted)}
@media (max-width:700px){.apps li{grid-template-columns:1fr auto;gap:4px 12px}.apps .ab{grid-column:1 / -1;grid-row:2}}
.node .isp{display:block;font-weight:400;font-size:12px;color:var(--muted)}
.hwi.photo{object-fit:contain}
.ci{display:inline-grid;place-items:center;flex:none;color:var(--muted);vertical-align:middle}
.ci img{width:100%;height:100%;object-fit:contain}
.cn2{display:inline-flex;align-items:center;gap:8px;min-width:0}
.who .pw .ci{margin-right:2px;vertical-align:-4px}
.hwi.line.bare{border:0;background:none}
.hwi.line{display:grid;place-items:center;color:var(--muted);border:1px solid var(--line);border-radius:10px;background:var(--sunk)}
.node .hwi.line{border:0;background:none}
.dhead .gl .hwi{margin-top:-12px}
.subh{font-size:15px;font-weight:600;margin:20px 0 10px}
.pt.has:hover{outline:2px solid var(--text);outline-offset:1px}
.who{margin-top:14px;font-size:13px}
.who ul{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:0 18px}
.who li{display:grid;grid-template-columns:28px 1fr auto;gap:8px;align-items:baseline;padding:5px 0;border-top:1px solid var(--line)}
.who li.hl{background:color-mix(in srgb,var(--ok) 14%,transparent);border-radius:6px}
.who .pn{font-family:var(--mono);font-size:11px;color:var(--muted);text-align:right}
.who .pw{min-width:0;overflow-wrap:anywhere}
.who .ps{font-size:12px;color:var(--muted);white-space:nowrap}
.who .unk{color:var(--faint)}
.who .upl{font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:0 6px;margin-left:2px}
.cport{color:var(--muted)}
.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:8px}
.legend span{display:inline-flex;align-items:center;gap:5px}
.legend i{width:10px;height:10px;border-radius:3px;display:inline-block;border:1px solid}
.lg100{background:color-mix(in srgb,var(--up) 22%,var(--surface));border-color:color-mix(in srgb,var(--up) 55%,var(--line))!important}
.lg1g{background:color-mix(in srgb,var(--ok) 22%,var(--surface));border-color:color-mix(in srgb,var(--ok) 55%,var(--line))!important}
.lg25{background:color-mix(in srgb,var(--down) 28%,var(--surface));border-color:var(--down)!important}
.lgpoe{background:var(--g5);border-color:var(--g5)!important;border-radius:50%!important}

/* radio's */
.radios{display:flex;gap:8px;flex-wrap:wrap}
.radio{border:1px solid var(--line);border-left:3px solid var(--rc);border-radius:8px;padding:6px 10px;background:var(--sunk);min-width:132px}
.radio b{display:block;font-size:14px;font-weight:600;color:var(--rc)}
.radio.off{background:transparent;border-style:dashed}
.radio span{display:block;font-size:12px;color:var(--muted)}
.wan{display:flex;gap:26px;flex-wrap:wrap;align-items:flex-end}

/* clients */
.tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input[type=search],select{background:var(--sunk);border:1px solid var(--line);color:var(--text);border-radius:9px;padding:6px 10px;font:inherit;font-size:14px}
input[type=search]{min-width:210px}
.tablewrap{overflow-x:auto;padding:8px 8px 6px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--muted);font-weight:500;font-size:13px;padding:8px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
th button{background:none;border:0;padding:0;cursor:pointer;color:inherit;font-weight:inherit}
th[aria-sort="ascending"] button::after{content:" ↑"} th[aria-sort="descending"] button::after{content:" ↓"}
td{padding:9px 12px;border-bottom:1px solid var(--line2);white-space:nowrap}
td.c-meta{display:none}
tr:last-child td{border-bottom:0}
td .mono{color:var(--muted)}
.conn{display:inline-flex;align-items:center;gap:6px}
.empty{color:var(--muted);padding:18px 12px}

@media (max-width:900px){
  .dv{grid-template-columns:minmax(0,1fr);gap:12px}
  .dv-stats{max-width:360px}
}
@media (max-width:700px){
  .br{flex-direction:column;align-items:flex-start}
  .br,.kids{width:100%}
  .kids{padding-left:22px;padding-top:10px;gap:10px}
  .node{width:100%;max-width:320px}
  .tree{display:block}
}
@media (max-width:700px){
  .wrap{padding:18px 10px 36px}
  .phead{padding:14px 14px 0}
  .mapbox{padding:14px}
  .chartwrap{padding:6px 10px 12px}
  canvas{height:200px}
  .facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;padding:12px 14px 0}
  .fact{font-size:12px;line-height:1.3}
  .fact b{font-size:16px}
  .key{display:none}
  .stamp{text-align:left;width:100%}
  .dv{padding:14px}
  .dv-stats{grid-template-columns:1fr 1fr;gap:10px 16px;max-width:none}
  .st{flex-direction:column;align-items:flex-start;gap:0}
  .st dt{font-size:12px} .st dd{text-align:left}
  .meter{justify-content:flex-start}
  .ports{display:flex;flex-direction:row;flex-wrap:wrap;gap:5px;width:100%;overflow:visible}
  .prow{display:contents}
  .pgap{display:none}
  .radios{flex-direction:column;gap:6px}
  .radio{display:grid;grid-template-columns:62px 1fr auto;align-items:baseline;gap:8px;min-width:0;padding:7px 10px}
  .radio b{font-size:13px}
  .radio span{display:inline}
  .tools{width:100%}
  input[type=search]{flex:1 1 100%}
  .tools select{flex:1}
  .tablewrap{padding:4px 0 6px}
  table,tbody{display:block}
  thead{display:none}
  tr{display:grid;grid-template-columns:1fr auto;gap:2px 10px;padding:10px 14px;border-bottom:1px solid var(--line2)}
  tr:last-child{border-bottom:0}
  td{padding:0;border:0;white-space:normal}
  .c-name{font-weight:500}
  .c-since{grid-column:2;grid-row:1;color:var(--muted);font-size:13px;text-align:right}
  .c-type,.c-via,.c-ip,.c-mac,.c-net{display:none}
  .c-sig{grid-column:2;grid-row:2;text-align:right}
  td.c-meta{display:block;grid-column:1 / -1;font-size:13px;color:var(--muted)}
  .empty{padding:14px}
}
@media (max-width:560px){
  h1{font-size:23px}
  .rate b{font-size:24px}
  input[type=search]{min-width:0;flex:1 1 100%}
}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div>
      <h1 id="title">Netwerk</h1>
      <p class="status"><span id="dot" class="sd"></span><span id="status">Gegevens laden</span></p>
    </div>
    <div class="hright"><div class="stamp" id="stamp"></div>
      <a class="openui" href="https://unifi.ui.com/" target="_blank" rel="noopener">UniFi openen<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg></a></div>
  </header>
  <p id="note" class="note" hidden></p>

  <nav class="tabs" id="tabs" aria-label="Onderdelen">
    <a href="#overzicht" data-v="overzicht">Overzicht</a><a href="#apparaten" data-v="apparaten">Apparaten</a><a href="#clients" data-v="clients">Clients</a><a href="#inzichten" data-v="inzichten">Inzichten</a><a href="#logboek" data-v="logboek">Logboek</a>
  </nav>

  <section class="panel" aria-labelledby="h-map" data-view="overzicht">
    <div class="phead"><h2 id="h-map">Netwerkkaart</h2><span class="aside">Klik op een apparaat voor de details</span></div>
    <div class="mapbox" id="mapbox"></div>
  </section>

  <div id="inetinfo" data-view="internet" hidden></div>
  <section class="panel" aria-labelledby="h-net" data-view="overzicht internet">
    <div class="phead">
      <h2 id="h-net">Internetverkeer</h2>
      <div class="seg" id="range" role="group" aria-label="Periode">
        <button data-r="30m" aria-pressed="true">30 min</button><button data-r="6h" aria-pressed="false">6 uur</button><button data-r="24h" aria-pressed="false">24 uur</button>
      </div>
    </div>
    <div class="facts" id="facts"></div>
    <div class="chartwrap"><canvas id="chart" aria-label="Grafiek van download en upload"></canvas><div class="tip" id="tip" hidden></div></div>
  </section>

  <section class="panel" aria-labelledby="h-dev" data-view="apparaten" hidden>
    <div class="phead"><h2 id="h-dev">Apparaten</h2><span class="aside" id="devAside"></span></div>
    <div class="devs" id="devices"></div>
  </section>

  <section class="panel" aria-labelledby="h-cli" data-view="clients" hidden>
    <div class="phead">
      <h2 id="h-cli">Clients</h2>
      <div class="tools">
        <input type="search" id="q" placeholder="Zoek op naam, IP of MAC" aria-label="Zoek clients">
        <select id="via" aria-label="Verbonden met"><option value="">Alle apparaten</option></select>
        <select id="net" aria-label="Netwerk"><option value="">Alle netwerken</option></select>
        <div class="seg" id="kind" role="group" aria-label="Verbinding">
          <button data-f="all" aria-pressed="true">Alle</button><button data-f="WIRELESS" aria-pressed="false">Wifi</button><button data-f="WIRED" aria-pressed="false">Kabel</button>
        </div>
      </div>
    </div>
    <div class="tablewrap">
      <table>
        <thead><tr>
          <th data-k="name" aria-sort="ascending"><button>Naam</button></th>
          <th data-k="type"><button>Verbinding</button></th>
          <th data-k="signal"><button>Signaal</button></th>
          <th data-k="network"><button>Netwerk</button></th>
          <th data-k="uplinkName"><button>Verbonden met</button></th>
          <th data-k="ip"><button>IP-adres</button></th>
          <th data-k="mac"><button>MAC-adres</button></th>
          <th data-k="since"><button>Verbonden sinds</button></th>
        </tr></thead>
        <tbody id="clients"></tbody>
      </table>
    </div>
  </section>

  <div id="detail" data-view="detail" hidden></div>
  <div id="insightsBox" data-view="inzichten" hidden></div>
  <section class="panel" data-view="logboek" hidden>
    <div class="phead"><h2>Logboek</h2><div class="tools">
      <input type="search" id="lq" placeholder="Zoeken in het logboek" aria-label="Zoeken in het logboek">
      <select id="lk" aria-label="Soort"><option value="">Alles</option></select></div></div>
    <div class="pbody"><ul class="log" id="logList"></ul></div>
  </section>
</div>

<script>
__I18N__
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nf = (v, d=0) => Number(v).toLocaleString(LOCALE, {minimumFractionDigits:d, maximumFractionDigits:d});
let data = null, range = '30m', kindFilter = 'all', sortKey = 'name', sortDir = 1, timer = null;

const ICON = {
  internet:'<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.7 2.6 15.3 0 18M12 3c-2.6 2.7-2.6 15.3 0 18"/></svg>',
  gateway:'<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="7" width="18" height="10" rx="2"/><path d="M7 12h1M10 12h1M13 12h1"/><circle cx="17.5" cy="12" r="1" fill="currentColor"/></svg>',
  switch:'<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="2.5" y="8" width="19" height="8" rx="1.5"/><path d="M6 11v2M9 11v2M12 11v2M15 11v2M18 11v2"/></svg>',
  ap:'<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="15" r="5"/><circle cx="12" cy="15" r="1" fill="currentColor"/><path d="M7.5 6.5a7 7 0 0 1 9 0M9.7 9a3.6 3.6 0 0 1 4.6 0"/></svg>',
  other:'<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>'
};
ICON.wifi = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3.5 9.5a12 12 0 0 1 17 0M6.5 12.7a7.6 7.6 0 0 1 11 0M9.5 15.8a3.3 3.3 0 0 1 5 0"/><circle cx="12" cy="18.8" r="1" fill="currentColor"/></svg>';
ICON.cable = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M7 4h10v7l-2 2H9l-2-2z"/><path d="M10 4v3M12 4v3M14 4v3M12 13v7"/></svg>';

/* Eigen tekeningen per vormfactor, gekozen op modelnaam. De led toont de status. */
function devMini(id, px = 22){
  const d = (data.devices || []).find(x => x.id === id);
  return d ? `<span class="ci" style="width:${px}px;height:${px}px">${devIcon(d, px).replace('class="hwi line"', 'class="hwi line bare"')}</span>` : '';
}
function devIcon(d, px = 36){
  if (d.img) return `<img class="hwi photo" src="api/devimg?id=${encodeURIComponent(d.img)}&s=${px > 64 ? 257 : 129}" width="${px}" height="${px}" alt="" loading="lazy"
    onerror="this.outerHTML=lineIcon(${esc(JSON.stringify({kind: d.kind}))}, ${px})">`;
  return lineIcon(d, px);
}
function cliIcon(c, px = 24){
  const fb = (c.type === 'WIRELESS' ? ICON.wifi : ICON.cable).replace(/width="24" height="24"/, `width="${Math.round(px * .75)}" height="${Math.round(px * .75)}"`);
  const box = inner => `<span class="ci" style="width:${px}px;height:${px}px">${inner}</span>`;
  if (c.fp == null) return box(fb);
  return box(`<img src="api/cliimg?id=${encodeURIComponent(c.fp)}&s=${px > 48 ? 257 : 129}" width="${px}" height="${px}" alt="" loading="lazy" onerror="this.replaceWith(document.createRange().createContextualFragment(this.dataset.fb))" data-fb="${esc(fb)}">`);
}
function lineIcon(d, px){
  return `<span class="hwi line" style="width:${px}px;height:${px}px">${(ICON[d.kind] || ICON.other).replace(/width="24" height="24"/, `width="${Math.round(px * .7)}" height="${Math.round(px * .7)}"`)}</span>`;
}
const KIND = {gateway:'Gateway', switch:'Switch', ap:T('Toegangspunt', 'Access point'), other:T('Apparaat', 'Device')};
const STATES = {ONLINE:['Online','ok'],OFFLINE:['Offline','bad'],UPDATING:['Wordt bijgewerkt','warn'],
  PENDING_ADOPTION:['Wacht op adoptie','warn'],GETTING_READY:['Start op','warn'],ADOPTING:['Wordt geadopteerd','warn'],
  CONNECTION_INTERRUPTED:['Verbinding onderbroken','bad'],ISOLATED:['Geïsoleerd','bad']};
const stateOf = s => STATES[s] || [String(s || 'Onbekend').toLowerCase().replace(/_/g,' '), 'warn'];

function rate(b){
  if (b == null) return ['–',''];
  if (b >= 1e9) return [nf(b/1e9, 2),'Gbit/s'];
  if (b >= 1e6) return [nf(b/1e6, b >= 1e8 ? 0 : 1),'Mbit/s'];
  return [nf(b/1e3, 0),'kbit/s'];
}
const rateTxt = b => rate(b).join(' ');
const speedTxt = m => m >= 1000 ? nf(m/1000, m % 1000 ? 1 : 0) + ' Gbit/s' : m + ' Mbit/s';
function dur(s){
  if (s == null || isNaN(s)) return '–';
  const d = Math.floor(s/86400), h = Math.floor(s%86400/3600), m = Math.floor(s%3600/60);
  const u = T('u', 'h'); return d ? `${d} d ${h} ${u}` : h ? `${h} ${u} ${m} m` : `${Math.max(1,m)} m`;
}
const secsSince = iso => iso ? (Date.now() - Date.parse(iso)) / 1000 : NaN;
const clock = t => new Date(t*1000).toLocaleTimeString(LOCALE, {hour:'2-digit', minute:'2-digit'});

/* ---------------- kop ---------------- */
function renderHead(){
  const d = data, devs = d.devices || [];
  $('#title').textContent = d.site ? `Netwerk ${d.site}` : 'Netwerk';
  const off = devs.filter(x => x.state !== 'ONLINE');
  let msg, cls;
  if (!d.updated) { msg = 'Gegevens laden'; cls = ''; }
  else if (d.error) { msg = 'De console is niet bereikbaar'; cls = 'bad'; }
  else if (!off.length) { msg = `Alle ${devs.length} apparaten zijn online, ${d.clients.length} clients verbonden`; cls = 'ok'; }
  else { msg = off.length === 1 ? `${off[0].name} is ${stateOf(off[0].state)[0].toLowerCase()}` : `${off.length} apparaten zijn niet online`; cls = 'bad'; }
  $('#status').textContent = msg; $('#dot').className = 'sd ' + cls;
  $('#stamp').innerHTML = (d.updated ? `Bijgewerkt om ${clock(d.updated)}` : '') + (d.version ? `<br>UniFi Network ${esc(d.version)}` : '');
  const n = $('#note');
  if (d.error) { n.hidden = false; n.className = 'note err'; n.textContent = d.error + (d.updated ? ' Je ziet de laatst bekende gegevens.' : ''); }
  else if (d.demo) { n.hidden = false; n.className = 'note'; n.textContent = 'Je bekijkt voorbeeldgegevens. Vul het adres van je console en je API-key in om je eigen netwerk te zien; in Home Assistant doe je dat op het tabblad Configuratie van de app.'; }
  else n.hidden = true;
}

/* ---------------- netwerkkaart ---------------- */
function linkClass(d){
  if (d.state !== 'ONLINE') return 'dead';
  const b = (d.rx || 0) + (d.tx || 0);
  return b > 100e6 ? 'live l3' : b > 10e6 ? 'live l2' : b > 5e5 ? 'live l1' : '';
}
function nodeHTML(d, parent){
  const [st, cls] = stateOf(d.state), on = d.state === 'ONLINE';
  const meta = on ? `<span class="sd ok"></span>${d.kind === 'switch' ? portUse(d) : d.kind === 'gateway' ? '↓ ' + rateTxt(d.rx) : d.clients + (d.clients === 1 ? ' client' : ' clients')}`
                  : `<span class="sd ${cls}"></span>${esc(st)}`;
  return `<button class="node k-${d.kind} ${on ? '' : 'off'}" data-id="${esc(d.id)}" data-parent="${esc(parent)}" data-link="${linkClass(d)}" aria-label="${esc(d.name)}, ${esc(st)}">
    <span class="gl">${devIcon(d, 36)}</span><span class="nm">${esc(d.name)}</span>
    <span class="sub">${esc(d.model || KIND[d.kind])}</span><span class="nmeta">${meta}</span></button>`;
}
function portUse(d){
  const p = d.ports || [], up = p.filter(x => x.state === 'UP').length;
  return p.length ? `${up} van ${p.length} poorten` : `${d.clients} clients`;
}
function renderMap(){
  const devs = data.devices || [], byId = Object.fromEntries(devs.map(d => [d.id, d]));
  const gw = devs.find(d => d.kind === 'gateway'), kids = {};
  devs.forEach(d => {
    if (d === gw) return;
    const p = d.uplinkId && byId[d.uplinkId] && d.uplinkId !== d.id ? d.uplinkId : (gw ? gw.id : '__inet');
    (kids[p] = kids[p] || []).push(d);
  });
  const seen = new Set();
  const branch = (d, parent) => {
    if (seen.has(d.id)) return ''; seen.add(d.id);
    const ch = (kids[d.id] || []).map(c => branch(c, d.id)).join('');
    return `<div class="br">${nodeHTML(d, parent)}${ch ? `<div class="kids">${ch}</div>` : ''}</div>`;
  };
  const w = data.wan, [rv, ru] = rate(w && w.rx), [tv, tu] = rate(w && w.tx);
  const inet = `<button class="node inet" data-id="__inet" aria-label="Internet, details"><span class="gl">${ICON.internet}</span><span class="nm">Internet${w && w.isp ? `<span class="isp">${esc(w.isp)}</span>` : ''}</span>
    <span class="rates"><span class="rate d"><b class="num">${rv}</b><span>↓ ${ru}</span></span><span class="rate u"><b class="num">${tv}</b><span>↑ ${tu}</span></span></span></button>`;
  const roots = (gw ? branch(gw, '__inet') : '') + (kids.__inet || []).map(c => branch(c, '__inet')).join('');
  const box = $('#mapbox'), sx = box.scrollLeft;
  box.innerHTML = `<div class="tree" id="tree"><svg class="links" id="links"></svg><div class="br">${inet}${roots ? `<div class="kids">${roots}</div>` : ''}</div></div>`;
  box.scrollLeft = sx;
  box.querySelectorAll('button.node').forEach(b => b.addEventListener('click', () => b.dataset.id === '__inet' ? (location.hash = 'internet') : focusDevice(b.dataset.id)));
  drawLinks();
}
function drawLinks(){
  const tree = $('#tree'), svg = $('#links'); if (!tree) return;
  const T = tree.getBoundingClientRect();
  svg.setAttribute('width', tree.scrollWidth); svg.setAttribute('height', tree.scrollHeight);
  const w = data.wan, wanB = w ? (w.rx || 0) + (w.tx || 0) : 0;
  let html = '';
  tree.querySelectorAll('.node[data-parent]').forEach(n => {
    const p = tree.querySelector(`.node[data-id="${CSS.escape(n.dataset.parent)}"]`); if (!p) return;
    const a = p.getBoundingClientRect(), b = n.getBoundingClientRect(), vert = b.left < a.right - 4;
    let d;
    if (vert) { const x1 = a.left - T.left + 12, y1 = a.bottom - T.top, x2 = b.left - T.left, y2 = b.top + b.height/2 - T.top, r = Math.min(8, (x2 - x1) / 2);
      d = `M${x1} ${y1} L${x1} ${y2 - r} Q${x1} ${y2} ${x1 + r} ${y2} L${x2} ${y2}`; }
    else { const x1 = a.right - T.left, y1 = a.top + a.height/2 - T.top, x2 = b.left - T.left, y2 = b.top + b.height/2 - T.top, c = (x2 - x1) * .55;
      d = `M${x1} ${y1} C${x1 + c} ${y1}, ${x2 - c} ${y2}, ${x2} ${y2}`; }
    let cls = n.dataset.link;
    if (n.dataset.parent === '__inet' && !n.classList.contains('off')) cls = wanB > 100e6 ? 'live l3' : wanB > 10e6 ? 'live l2' : wanB > 5e5 ? 'live l1' : '';
    const spd = cls.includes('l3') ? '.8s' : cls.includes('l2') ? '1.5s' : '2.6s';
    html += `<path class="${cls}${vert ? ' v' : ''}" style="--spd:${spd}" d="${d}"/>`;
  });
  svg.innerHTML = html;
}

/* ---------------- grafiek ---------------- */
let chartPts = [], chartGeo = null;
function renderChart(){
  const h = data.history || [], longRange = range !== '30m';
  chartPts = h;
  const f = $('#facts');
  if (h.length > 1) {
    const peak = Math.max(...h.map(p => longRange ? (p.max || p.rx) : p.rx));
    const avg = h.reduce((s, p) => s + p.rx, 0) / h.length, avgU = h.reduce((s, p) => s + p.tx, 0) / h.length;
    f.innerHTML = `<div class="fact"><b class="num">${rateTxt(avg)}</b><span class="key" style="background:var(--down)"></span>Gemiddelde download</div>
      <div class="fact"><b class="num">${rateTxt(avgU)}</b><span class="key" style="background:var(--up)"></span>Gemiddelde upload</div>
      <div class="fact"><b class="num">${rateTxt(peak)}</b>Hoogste download</div>`;
  } else f.innerHTML = '';
  drawChart();
}
function drawChart(hover){
  const c = $('#chart'), dpr = window.devicePixelRatio || 1, w = c.clientWidth, H = c.clientHeight;
  c.width = w * dpr; c.height = H * dpr;
  const g = c.getContext('2d'); g.scale(dpr, dpr); g.clearRect(0, 0, w, H);
  const css = getComputedStyle(document.documentElement), col = k => css.getPropertyValue(k).trim();
  const h = chartPts, L = 62, R = 6, T = 10, B = 24;
  g.font = '12px ' + col('--sans');
  if (h.length < 2) {
    g.fillStyle = col('--muted');
    g.fillText(range === '30m' ? 'De grafiek verschijnt na een paar metingen.' : 'De langere geschiedenis bouwt zich vanzelf op; kom over een paar minuten terug.', L, H/2);
    chartGeo = null; return;
  }
  const raw = Math.max(1e5, ...h.map(p => Math.max(p.rx, p.tx))) * 1.05;
  const nice = x => { const e = Math.pow(10, Math.floor(Math.log10(x))), f = x / e; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * e; };
  const step = nice(raw / 4), steps = Math.ceil(raw / step), max = step * steps;
  const t0 = h[0].t, t1 = h[h.length-1].t;
  const X = t => L + (t - t0) / Math.max(1, t1 - t0) * (w - L - R), Y = v => T + (1 - v/max) * (H - T - B);
  chartGeo = {X, Y, L, R, T, B, w, H};
  const dec = (v, u) => nf(v / u, Math.abs(v / u - Math.round(v / u)) > 1e-9 ? 1 : 0);
  const axis = v => v >= 1e9 ? dec(v, 1e9) + ' Gb/s' : v >= 1e6 ? dec(v, 1e6) + ' Mb/s' : dec(v, 1e3) + ' kb/s';
  g.strokeStyle = col('--line2'); g.fillStyle = col('--faint'); g.lineWidth = 1;
  for (let i = 0; i <= steps; i++) { const v = step * i, y = Math.round(Y(v)) + .5;
    g.beginPath(); g.moveTo(L, y); g.lineTo(w - R, y); g.stroke(); g.fillText(i ? axis(v) : '0', 4, y + 4); }
  const ticks = Math.max(2, Math.min(7, Math.floor((w - L) / 110)));
  for (let i = 0; i < ticks; i++) { const t = t0 + (t1 - t0) * i / (ticks - 1), s = clock(t), tw = g.measureText(s).width;
    g.fillText(s, Math.min(w - R - tw, Math.max(L, X(t) - tw/2)), H - 6); }
  const area = (k, color, alpha) => {
    g.beginPath(); h.forEach((p, i) => i ? g.lineTo(X(p.t), Y(p[k])) : g.moveTo(X(p.t), Y(p[k])));
    g.strokeStyle = color; g.lineWidth = 1.8; g.lineJoin = 'round'; g.stroke();
    g.lineTo(X(t1), Y(0)); g.lineTo(X(t0), Y(0)); g.closePath();
    const gr = g.createLinearGradient(0, T, 0, H - B); gr.addColorStop(0, color); gr.addColorStop(1, 'transparent');
    g.globalAlpha = alpha; g.fillStyle = gr; g.fill(); g.globalAlpha = 1;
  };
  area('rx', col('--down'), .22); area('tx', col('--up'), .18);
  if (hover != null) {
    const p = h[hover], x = X(p.t);
    g.strokeStyle = col('--faint'); g.setLineDash([3, 3]); g.beginPath(); g.moveTo(x, T); g.lineTo(x, H - B); g.stroke(); g.setLineDash([]);
    [['rx', '--down'], ['tx', '--up']].forEach(([k, v]) => { g.beginPath(); g.arc(x, Y(p[k]), 4, 0, 7); g.fillStyle = col(v); g.fill();
      g.lineWidth = 2; g.strokeStyle = col('--surface'); g.stroke(); });
  }
}
function chartHover(e){
  const tip = $('#tip');
  if (!chartGeo || chartPts.length < 2) { tip.hidden = true; return; }
  const r = $('#chart').getBoundingClientRect(), x = e.clientX - r.left;
  if (x < chartGeo.L) { tip.hidden = true; drawChart(); return; }
  let best = 0, bd = Infinity;
  chartPts.forEach((p, i) => { const d = Math.abs(chartGeo.X(p.t) - x); if (d < bd) { bd = d; best = i; } });
  const p = chartPts[best];
  drawChart(best);
  tip.hidden = false;
  tip.innerHTML = `<b>${clock(p.t)}</b> <span class="d">↓ ${rateTxt(p.rx)}</span> <span class="u">↑ ${rateTxt(p.tx)}</span>`;
  tip.style.left = Math.min(r.width - 90, Math.max(90, chartGeo.X(p.t) + 20)) + 'px';
  tip.style.top = (chartGeo.Y(Math.max(p.rx, p.tx)) + 8) + 'px';
}

/* ---------------- apparaten ---------------- */
function meter(v){ return v == null ? '–' : `<span class="meter ${v > 80 ? 'hot' : ''}"><i><b style="width:${Math.min(100, v)}%"></b></i><span class="num">${nf(v, 0)}%</span></span>`; }
function portsHTML(ports){
  const rj = ports.filter(p => !String(p.connector || '').startsWith('SFP')).sort((a, b) => a.idx - b.idx);
  const sfp = ports.filter(p => String(p.connector || '').startsWith('SFP')).sort((a, b) => a.idx - b.idx);
  const cell = p => {
    const up = p.state === 'UP', s = p.speed || 0, isSfp = String(p.connector || '').startsWith('SFP');
    const cls = !up ? '' : s >= 2500 ? 'p25' : s >= 1000 ? 'p1g' : 'p100';
    const who = (p.who || []).map(w => w.name).join(', ');
    const t = `Poort ${p.idx}${isSfp ? ' (SFP)' : ''}: ${up ? speedTxt(s) : 'niet verbonden'}${p.poe ? ', levert PoE' : ''}${who ? '\n' + who : ''}`;
    return `<span class="pt ${cls}${p.poe ? ' poe' : ''}${isSfp ? ' sfp' : ''}${who ? ' has' : ''}" style="order:${p.idx}" title="${esc(t)}" data-port="${p.idx}">${p.idx}</span>`;
  };
  const two = rj.length > 8;
  const top = two ? rj.filter((_, i) => i % 2 === 0) : rj, bot = two ? rj.filter((_, i) => i % 2 === 1) : [];
  const sTop = two ? sfp.filter((_, i) => i % 2 === 0) : sfp, sBot = two ? sfp.filter((_, i) => i % 2 === 1) : [];
  const row = (a, s) => `<div class="prow">${a.map(cell).join('')}${s.length ? '<span class="pgap"></span>' + s.map(cell).join('') : ''}</div>`;
  return `<div class="ports" role="img" aria-label="Poortoverzicht">${row(top, sTop)}${bot.length || sBot.length ? row(bot, sBot) : ''}</div>`;
}
let hlPort = null;
const devHref = id => '#apparaat/' + encodeURIComponent(id);
const cliHref = k => '#client/' + encodeURIComponent(k);
function whoHTML(d){
  const used = (d.ports || []).filter(p => p.state === 'UP' || (p.who || []).length).sort((a, b) => a.idx - b.idx);
  if (!used.some(p => (p.who || []).length)) return '';
  const name = w => w.id ? `<a href="${devHref(w.id)}">${esc(w.name)}</a>` : w.key ? `<a href="${cliHref(w.key)}">${esc(w.name)}</a>` : esc(w.name);
  const rows = used.map(p => {
    const w = p.who || [];
    const what = !w.length ? '<span class="unk">Onbekend apparaat</span>'
      : w.map(x => (x.kind === 'client' ? cliIcon(x, 18) + ' ' : x.id ? devMini(x.id, 18) + ' ' : '') + name(x) + (x.uplink ? ' <span class="upl">uplink</span>' : '')).join(', ');
    return `<li data-port="${p.idx}"${p.idx === hlPort ? ' class="hl"' : ''}><span class="pn">${p.idx}</span><span class="pw">${what}</span><span class="ps">${p.state === 'UP' ? speedTxt(p.speed || 0) : 'niet verbonden'}${p.poe ? ', PoE' : ''}</span></li>`;
  }).join('');
  return `<div class="who"><ul>${rows}</ul></div>`;
}
function portBlock(d, full){
  const p = d.ports || [], up = p.filter(x => x.state === 'UP').length, poe = p.filter(x => x.poe).length;
  if (!p.length) return `<p class="offline">Geen poortgegevens beschikbaar.</p>`;
  return portsHTML(p) + `<div class="legend"><span><i class="lg100"></i>100 Mbit/s</span><span><i class="lg1g"></i>1 Gbit/s</span><span><i class="lg25"></i>2,5 Gbit/s of meer</span><span><i class="lgpoe"></i>Levert PoE</span></div>
      <p class="vis-cap">${up} van de ${p.length} poorten in gebruik${poe ? `, ${poe} leveren stroom via PoE` : ''}</p>${full ? whoHTML(d) : ''}`;
}
function visHTML(d, full){
  if (d.state !== 'ONLINE') return `<p class="offline">Dit apparaat reageert niet. Controleer de stroom en de kabel naar ${esc(upName(d) || 'de switch')}.</p>`;
  if (d.kind === 'switch') return portBlock(d, full);
  if (d.kind === 'ap') {
    const bandCol = b => b >= 6 ? 'var(--g6)' : b >= 5 ? 'var(--g5)' : 'var(--g24)';
    const r = (d.radios || []).map(x => !x.channel
      ? `<div class="radio off" style="--rc:var(--faint)"><b>${nf(x.band, x.band % 1 ? 1 : 0)} GHz</b><span class="ch">Uit</span><span class="rt"></span></div>`
      : `<div class="radio" style="--rc:${bandCol(x.band)}"><b>${nf(x.band, x.band % 1 ? 1 : 0)} GHz</b>
      <span class="ch">Kanaal ${esc(x.channel)}${x.width ? `, ${x.width} MHz` : ''}</span><span class="rt">${x.retries != null ? `${nf(x.retries, 1)}% herhaald` : esc(x.standard || '')}</span></div>`).join('');
    return (r ? `<div class="radios">${r}</div>` : '') + `<p class="vis-cap">${d.clients} ${d.clients === 1 ? 'client' : 'clients'} verbonden, uplink ↓ ${rateTxt(d.rx)} ↑ ${rateTxt(d.tx)}</p>`;
  }
  if (d.kind === 'gateway') {
    return `<div class="wan"><div class="rate d"><b class="num">${rate(d.rx)[0]}</b><span>${rate(d.rx)[1]} download</span></div>
      <div class="rate u"><b class="num">${rate(d.tx)[0]}</b><span>${rate(d.tx)[1]} upload</span></div>
      ${d.load != null ? `<div class="fact"><b class="num">${nf(d.load, 2)}</b>Belasting</div>` : ''}
      ${data.wan && data.wan.isp ? `<div class="fact"><b>${esc(data.wan.isp)}</b>Provider</div>` : ''}</div>`
      + (full && (d.ports || []).length ? `<h3 class="subh">Poorten</h3>${portBlock(d, true)}` : '');
  }
  return `<p class="vis-cap">Uplink ↓ ${rateTxt(d.rx)} ↑ ${rateTxt(d.tx)}</p>`;
}
function upName(d){ const u = (data.devices || []).find(x => x.id === d.uplinkId); return u && u.name; }
function renderDevices(){
  const devs = data.devices || [], upd = devs.filter(x => x.updatable).length;
  $('#devAside').textContent = upd ? `${upd} ${upd === 1 ? T('update beschikbaar', 'update available') : T('updates beschikbaar', 'updates available')}` : 'Alle firmware is actueel';
  $('#devices').innerHTML = devs.map(d => {
    const [st, cls] = stateOf(d.state), on = d.state === 'ONLINE', up = upName(d);
    return `<article class="dv" id="dv-${esc(d.id)}" data-id="${esc(d.id)}">
      <div class="dv-id"><span class="gl">${devIcon(d, 44)}</span><div>
        <h3><a href="${devHref(d.id)}">${esc(d.name)}</a>${on ? '' : ` <span class="tag ${cls}">${esc(st)}</span>`}${d.updatable ? ' <span class="tag warn">Update</span>' : ''}</h3>
        <div class="sub">${esc(d.model || '')}${d.ip ? `, <span class="mono">${esc(d.ip)}</span>` : ''}</div>
        ${up ? `<div class="sub">Verbonden via ${esc(up)}${d.uplinkPort != null ? `, poort ${d.uplinkPort}` : ''}</div>` : ''}</div></div>
      <div>${visHTML(d)}</div>
      <dl class="dv-stats"><div class="st"><dt>Uptime</dt><dd class="num">${dur(d.uptime)}</dd></div><div class="st"><dt>Firmware</dt><dd>${esc(d.firmware || '–')}</dd></div>
        <div class="st"><dt>Processor</dt><dd>${meter(d.cpu)}</dd></div><div class="st"><dt>Geheugen</dt><dd>${meter(d.mem)}</dd></div></dl>
    </article>`;
  }).join('') || '<p class="empty">Er zijn nog geen apparaten gevonden op deze site.</p>';
  document.querySelectorAll('#devices .dv').forEach(el => el.onclick = e => {
    if (e.target.closest('a')) return;
    const pt = e.target.closest('.pt.has');
    location.hash = devHref(el.dataset.id).slice(1) + (pt ? '/' + pt.dataset.port : '');
  });
}
function focusDevice(id){ location.hash = devHref(id).slice(1); }

/* ---------------- detailpagina's ---------------- */
function sigLevel(dbm){ return dbm == null ? 0 : dbm >= -60 ? 4 : dbm >= -67 ? 3 : dbm >= -75 ? 2 : 1; }
function sigHTML(c, label = true){
  if (c.signal == null) return c.type === 'WIRELESS' ? '–' : '';
  const l = sigLevel(c.signal), cls = l >= 3 ? 'ok' : l === 2 ? 'warn' : 'bad';
  return `<span class="sig ${cls}" title="${esc(c.signal)} dBm"><i class="${l >= 1 ? 'on' : ''}"></i><i class="${l >= 2 ? 'on' : ''}"></i><i class="${l >= 3 ? 'on' : ''}"></i><i class="${l >= 4 ? 'on' : ''}"></i>${label ? `<span class="num">${esc(c.signal)} dBm</span>` : ''}</span>`;
}
const linkTxt = k => k == null ? '' : k >= 1e6 ? nf(k / 1e6, 1) + ' Gbit/s' : nf(k / 1000, 0) + ' Mbit/s';
const netTxt = c => c.network ? c.network + (c.vlan ? ` (VLAN ${c.vlan})` : '') : '';
function bytes(b){
  if (b == null) return '–';
  const u = ['B', 'kB', 'MB', 'GB', 'TB']; let i = 0; while (b >= 1000 && i < u.length - 1) { b /= 1024; i++; }
  return nf(b, b >= 100 || i === 0 ? 0 : 1) + ' ' + u[i];
}
function connLine(c){
  const typ = c.type === 'WIRELESS' ? 'Wifi' + (c.ssid ? ' ' + c.ssid : '') : c.type === 'WIRED' ? 'Kabel' : '';
  return [typ, c.port != null ? 'poort ' + c.port : '', netTxt(c), c.ip || ''].filter(Boolean).join(', ');
}
function clientLi(c){
  const s = secsSince(c.since);
  return `<li><span class="cn2">${cliIcon(c, 22)}<a href="${cliHref(c.key)}">${esc(c.name)}</a></span><span class="t">${isNaN(s) ? '' : dur(s)}</span><span class="m">${esc(connLine(c))}</span></li>`;
}
function renderDevice(id){
  const devs = data.devices || [], d = devs.find(x => x.id === id), box = $('#detail');
  if (!d) { box.innerHTML = `<a class="back" href="#apparaten">Alle apparaten</a><div class="panel"><p class="empty">Dit apparaat is niet gevonden op deze site.</p></div>`; return; }
  const [st, cls] = stateOf(d.state), on = d.state === 'ONLINE', parent = devs.find(x => x.id === d.uplinkId);
  const kids = devs.filter(x => x.uplinkId === d.id && x.id !== d.id);
  const cl = (data.clients || []).filter(c => c.uplink === d.id)
    .sort((a, b) => (a.port ?? 999) - (b.port ?? 999) || String(a.name).localeCompare(String(b.name), 'nl'));
  const title = d.kind === 'switch' ? 'Poorten' : d.kind === 'ap' ? "Radio's" : d.kind === 'gateway' ? 'Internet en poorten' : 'Verkeer';
  const conn = kids.map(k => `<li><span class="cn2">${devMini(k.id)}<a href="${devHref(k.id)}">${esc(k.name)}</a></span><span class="t">${esc(KIND[k.kind] || '')}</span><span class="m">${esc([k.model, k.uplinkPort != null ? 'poort ' + k.uplinkPort : '', stateOf(k.state)[0]].filter(Boolean).join(', '))}</span></li>`).join('')
             + cl.map(clientLi).join('');
  box.innerHTML = `<a class="back" href="#apparaten">Alle apparaten</a>
    <div class="dhead"><span class="gl">${devIcon(d, 64)}</span><div>
      <h2>${esc(d.name)} <span class="tag ${on ? 'new' : cls}">${esc(st)}</span>${d.updatable ? ' <span class="tag warn">Update beschikbaar</span>' : ''}</h2>
      <div class="sub">${esc(KIND[d.kind] || '')}, ${esc(d.model || '')}${parent ? `, ${T('verbonden via', 'connected via')} <a href="${devHref(parent.id)}">${esc(parent.name)}</a>${d.uplinkPort != null ? ` ${T('poort', 'port')} ${d.uplinkPort}` : ''}` : ''}</div>
    </div></div>
    <div class="dgrid"><div>
      <section class="panel"><div class="phead"><h2>${title}</h2></div><div class="pbody">${visHTML(d, true)}</div></section>
      <section class="panel"><div class="phead"><h2>Verbonden (${kids.length + cl.length})</h2></div><div class="pbody">
        ${conn ? `<ul class="cl">${conn}</ul>` : '<p class="offline">Er is niets direct op dit apparaat aangesloten.</p>'}</div></section>
    </div><div>
      <section class="panel"><div class="phead"><h2>Gegevens</h2></div><div class="pbody"><dl class="kv">
        <dt>Status</dt><dd>${esc(st)}</dd><dt>IP-adres</dt><dd class="mono">${esc(d.ip || '–')}</dd>${d.wanIp && d.wanIp !== d.ip ? `<dt>Internetadres</dt><dd class="mono">${esc(d.wanIp)}</dd>` : ''}<dt>MAC-adres</dt><dd class="mono">${esc(d.mac || '–')}</dd>
        <dt>Firmware</dt><dd>${esc(d.firmware || '–')}</dd><dt>Uptime</dt><dd class="num">${dur(d.uptime)}</dd>
        <dt>Processor</dt><dd>${meter(d.cpu)}</dd><dt>Geheugen</dt><dd>${meter(d.mem)}</dd>
        ${d.rx != null ? `<dt>Uplink</dt><dd>↓ ${rateTxt(d.rx)} ↑ ${rateTxt(d.tx)}</dd>` : ''}
      </dl></div></section>
      ${d.kind === 'gateway' || (d.storage || []).length ? storageHTML(d) : ''}
    </div></div>`;
  box.querySelectorAll('.pt.has').forEach(el => el.onclick = () => {
    hlPort = +el.dataset.port; box.querySelectorAll('.who li').forEach(li => li.classList.toggle('hl', +li.dataset.port === hlPort));
    const li = box.querySelector(`.who li[data-port="${hlPort}"]`); if (li) li.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  });
}
function storageHTML(d){
  const st = d.storage || [], bk = data.backups;
  if (!st.length && !bk && !data.backupsError) return '';
  const when = t => t ? new Date(t * 1000).toLocaleString(LOCALE, {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) : '–';
  const bar = x => { const pc = x.size ? x.used / x.size * 100 : 0;
    return `<li><div class="an"><b>${esc(x.name)}</b><span>${bytes(x.used)} van ${bytes(x.size)}</span></div><div class="ab"><i style="width:${pc.toFixed(1)}%;${pc > 85 ? 'background:var(--warn)' : ''}"></i></div><div class="av"><span class="num">${nf(pc, 0)}%</span></div></li>`; };
  return `<section class="panel"><div class="phead"><h2>Opslag en back-ups</h2></div><div class="pbody">
    ${st.length ? `<ul class="apps">${st.map(bar).join('')}</ul>` : ''}
    ${bk ? (bk.length ? `<h3 class="subh">Back-ups</h3><ul class="cl">${bk.map(b => `<li><span>${when(b.t)}</span><span class="t">${b.size ? bytes(b.size) : ''}</span><span class="m">${esc([b.kind, b.version ? 'versie ' + b.version : ''].filter(Boolean).join(', '))}</span></li>`).join('')}</ul>`
          : '<p class="offline">Er zijn geen back-ups van de Network-app gevonden.</p>')
        : `<p class="offline">De back-ups van je console zijn niet op te halen.</p>`}
  </div></section>`;
}
function renderInternet(){
  const w = data.wan || {}, gw = (data.devices || []).find(d => d.kind === 'gateway');
  const big = (b, cls, lbl) => { const [v, u] = rate(b); return `<div class="rate ${cls}"><b class="num">${v}</b><span>${u} ${lbl}</span></div>`; };
  $('#inetinfo').innerHTML = `<a class="back" href="#overzicht">Overzicht</a>
    <div class="dhead"><span class="gl">${ICON.internet.replace(/width="24" height="24"/, 'width="44" height="44"')}</span><div>
      <h2>Internet</h2><div class="sub">${[w.isp ? esc(w.isp) : '', gw ? `via <a href="${devHref(gw.id)}">${esc(gw.name)}</a>` : ''].filter(Boolean).join(', ') || 'Geen gegevens van de gateway'}</div></div></div>
    <div class="dgrid"><div>
      <section class="panel"><div class="phead"><h2>Nu</h2></div><div class="pbody"><div class="wan">${big(w.rx, 'd', 'download')}${big(w.tx, 'u', 'upload')}
        ${gw && gw.load != null ? `<div class="fact"><b class="num">${nf(gw.load, 2)}</b>Belasting gateway</div>` : ''}</div></div></section>
    </div><div>
      <section class="panel"><div class="phead"><h2>Verbinding</h2></div><div class="pbody"><dl class="kv">
        <dt>Provider</dt><dd>${esc(w.isp || 'Onbekend')}</dd>
        ${gw ? `<dt>Gateway</dt><dd><span class="cn2">${devMini(gw.id, 20)}<a href="${devHref(gw.id)}">${esc(gw.name)}</a></span></dd>` : ''}
        ${gw && gw.wanIp ? `<dt>Internetadres</dt><dd class="mono">${esc(gw.wanIp)}</dd>` : ''}
        ${gw && (gw.wanPorts || []).length ? `<dt>WAN-poort</dt><dd><a href="${devHref(gw.id)}/${gw.wanPorts[0]}">Poort ${gw.wanPorts.join(', ')}</a></dd>` : ''}
        ${gw ? `<dt>Uptime gateway</dt><dd class="num">${dur(gw.uptime)}</dd>` : ''}
      </dl></div></section>
    </div></div>`;
}
function appList(apps){
  const max = Math.max(1, ...apps.map(a => a.total)), sum = apps.reduce((t, a) => t + a.total, 0) || 1;
  return `<ul class="apps">${apps.map(a => `<li><div class="an"><b>${esc(a.name)}</b>${a.cat ? `<span>${esc(a.cat)}</span>` : ''}</div>
    <div class="ab"><i style="width:${Math.max(1.5, a.total / max * 100).toFixed(1)}%"></i></div>
    <div class="av"><span class="num">${bytes(a.total)}</span><span class="pc num">${nf(a.total / sum * 100, a.total / sum < .1 ? 1 : 0)}%</span></div></li>`).join('')}</ul>`;
}
function wifiCheck(){
  const cl = (data.clients || []).filter(c => c.type === 'WIRELESS'), devs = data.devices || [];
  const weak = cl.filter(c => c.signal != null && c.signal < -75).sort((a, b) => a.signal - b.signal);
  const exp = cl.filter(c => c.experience != null), avg = exp.length ? exp.reduce((t, c) => t + c.experience, 0) / exp.length : null;
  const poor = exp.filter(c => c.experience < 50);
  const radios = devs.filter(d => d.kind === 'ap' && d.state === 'ONLINE').flatMap(d => (d.radios || []).filter(r => r.channel && r.retries > 10).map(r => `${d.name} ${nf(r.band, r.band % 1 ? 1 : 0)} GHz (${nf(r.retries, 1)}%)`));
  const offAps = devs.filter(d => d.kind === 'ap' && d.state !== 'ONLINE'), upd = devs.filter(d => d.updatable);
  const link = c => `<a href="${cliHref(c.key)}">${esc(c.name)}</a>`;
  const rows = [
    [!weak.length, 'Signaalsterkte', weak.length ? `${weak.length} ${weak.length === 1 ? T('client heeft', 'client has') : T('clients hebben', 'clients have')} ${T('een zwak signaal (onder -75 dBm)', 'a weak signal (below -75 dBm)')}: ${weak.slice(0, 6).map(c => `${link(c)} ${c.signal} dBm`).join(', ')}${weak.length > 6 ? T(' en meer', ' and more') : ''}.` : 'Alle wifi-clients hebben een goed signaal.'],
    [!poor.length, 'Wifi-ervaring', avg == null ? 'Je console geeft geen wifi-ervaring door.' : `${T('Gemiddeld', 'Average')} ${nf(avg, 0)}%.${poor.length ? ` ${T('Slecht bij', 'Poor at')} ${poor.slice(0, 6).map(link).join(', ')}.` : ''}`],
    [!radios.length, 'Storing op kanalen', radios.length ? `Veel herhaalde pakketten bij ${esc(radios.join(', '))}. Overweeg een ander kanaal of een smallere kanaalbreedte.` : 'Geen radio met veel herhaalde pakketten.'],
    [!offAps.length, "Toegangspunten", offAps.length ? `${offAps.map(d => `<a href="${devHref(d.id)}">${esc(d.name)}</a>`).join(', ')} ${offAps.length === 1 ? 'is' : T('zijn', 'are')} ${T('niet online.', 'not online.')}` : "Alle toegangspunten zijn online."],
    ...(data.backups ? [[data.backups.length && data.backups[0].t > Date.now() / 1000 - 8 * 86400, 'Back-ups', data.backups.length
        ? `Laatste back-up van de Network-app: ${new Date(data.backups[0].t * 1000).toLocaleString(LOCALE, {day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit'})}.` : 'Er zijn geen back-ups gevonden.']] : []),
    [!upd.length, 'Firmware', upd.length ? `${T('Update beschikbaar voor', 'Update available for')} ${upd.map(d => `<a href="${devHref(d.id)}">${esc(d.name)}</a>`).join(', ')}.` : 'Alle firmware is actueel.']];
  const ok = rows.filter(r => r[0]).length;
  return `<section class="panel"><div class="phead"><h2>Netwerkcheck</h2><span class="aside">${ok === rows.length ? 'Alles in orde' : `${rows.length - ok} ${rows.length - ok === 1 ? T('punt om na te lopen', 'item to check') : T('punten om na te lopen', 'items to check')}`}</span></div>
    <div class="pbody"><ul class="checks">${rows.map(([good, t, txt]) => `<li class="${good ? 'good' : 'todo'}"><span class="ck" aria-hidden="true">${good ? '✓' : '!'}</span><div><b>${t}</b><p>${txt}</p></div></li>`).join('')}</ul>
    <p class="note2">Een eigen controle op basis van de gegevens van je console. De WiFi Doctor van UniFi zelf is niet via de API beschikbaar.</p></div></section>`;
}
function renderLog(){
  const items = data.events, list = $('#logList');
  const kinds = [...new Set((items || []).map(e => e.kind))].sort((a, b) => a.localeCompare(b, 'nl')), ks = $('#lk'), kc = ks.value;
  ks.innerHTML = '<option value="">Alles</option>' + kinds.map(k => `<option value="${esc(k)}">${esc(k)}</option>`).join(''); ks.value = kc;
  if (!items) { list.innerHTML = `<li class="empty">${data.eventsError ? 'Het logboek van je console is niet op te halen. De app probeert het elke minuut opnieuw.' : 'Logboek laden.'}</li>`; return; }
  const q = $('#lq').value.trim().toLowerCase(), k = ks.value;
  const rows = items.filter(e => (!k || e.kind === k) && (!q || e.msg.toLowerCase().includes(q)));
  const today = new Date().toDateString();
  const when = t => { const d = new Date(t * 1000); return (d.toDateString() === today ? 'Vandaag' : d.toLocaleDateString(LOCALE, {day: 'numeric', month: 'short'})) + ' ' + d.toLocaleTimeString(LOCALE); };
  const sev = n => n ? `<span class="sev s${n}" title="Ernst ${n} van 4"><i></i><i></i><i></i><i></i></span>` : '';
  list.innerHTML = rows.map(e => `<li><p>${esc(e.msg)}</p><div class="lm">${sev(e.sev)}<span class="num">${when(e.t)}</span>${e.title ? `<span class="ltt">${esc(e.title)}</span>` : ''}<span class="lt">${esc(e.kind)}</span></div></li>`).join('')
    || `<li class="empty">${items.length ? 'Niets gevonden met dit filter.' : 'Geen gebeurtenissen in de afgelopen 48 uur.'}</li>`;
}
function flowsHTML(){
  const f = data.flows;
  if (!f) return `<section class="panel"><div class="phead"><h2>Flows</h2></div><div class="pbody"><p class="offline">${data.flowsError ? 'Je console geeft (nog) geen flows door. De app probeert het elke 5 minuten opnieuw.' : 'Flows worden opgehaald.'}</p></div></section>`;
  const allowed = Math.max(0, (f.total || 0) - (f.blocked || 0));
  const flagI = c => c && c.length === 2 ? `<img class="flg" src="https://flagcdn.com/w40/${esc(c.toLowerCase())}.png" alt="" loading="lazy">` : `<span class="flg globe">${ICON.internet.replace(/width="24" height="24"/, 'width="18" height="18"')}</span>`;
  const top = (rows, render) => rows.length ? `<ul class="cl">${rows.map(render).join('')}</ul>` : '<p class="offline">Nog niets gemeten.</p>';
  return `<section class="panel"><div class="phead"><h2>Flows</h2><span class="aside">afgelopen 24 uur</span></div><div class="pbody">
    <div class="wan"><div class="fact"><b class="num">${nf(allowed)}</b>Toegestaan</div><div class="fact bad"><b class="num">${nf(f.blocked || 0)}</b>Geblokkeerd</div>
      <div class="fact"><b class="num">${nf(f.risky || 0)}</b>Verdacht</div><div class="fact"><b class="num">${nf(f.total || 0)}</b>Totaal</div></div>
    <div class="fgrid"><div><h3 class="subh">Topbestemmingen</h3>${top(f.dests || [], d => `<li><span class="cn2">${flagI(d.country)}<span>${esc(d.name)}</span></span><span class="t num">${nf(d.n)}</span>${d.blocked ? `<span class="m">${nf(d.blocked)} geblokkeerd</span>` : ''}</li>`)}</div>
      <div><h3 class="subh">Topclients</h3>${top(f.clients || [], c => { const cc = (data.clients || []).find(x => x.name === c.name);
        return `<li><span class="cn2">${cc ? cliIcon(cc, 20) : ''}${cc ? `<a href="${cliHref(cc.key)}">${esc(c.name)}</a>` : esc(c.name)}</span><span class="t num">${nf(c.n)}</span></li>`; })}</div></div>
    ${f.exact ? '' : `<p class="note2">Gebaseerd op de laatste ${nf(f.sample)} verbindingen.</p>`}
  </div></section>`;
}
function diagHTML(){
  const d = data.diag || {}, keys = Object.keys(d);
  if (!keys.length) return '';
  const L = {backups: 'Back-ups', apps: 'Gebruik per app', flows: 'Flows', logboek: 'Logboek', poorten: 'Poorten, SSID en signaal', productcatalogus: 'Productafbeeldingen'};
  return `<details class="diag"><summary>Technische controle</summary><ul>${keys.map(k => `<li><b>${esc(L[k] || k)}</b> <span class="${d[k].ok ? 'okc' : 'errc'}">${d[k].ok ? 'werkt' : 'werkt niet'}</span><code>${esc(d[k].info || '')}</code></li>`).join('')}</ul>
    <p class="note2">Bevat alleen veldnamen en foutmeldingen, geen gegevens uit je netwerk. Handig om te delen als iets niet werkt.</p></details>`;
}
function renderInsights(){
  const ins = data.insights, box = $('#insightsBox');
  const usage = !ins ? `<section class="panel"><div class="phead"><h2>Dataverbruik</h2></div><div class="pbody"><p class="offline">${data.insightsError
      ? 'Je console geeft (nog) geen gebruik per app door. Controleer in UniFi of <b>Traffic Identification</b> aan staat; de app probeert het elke 10 minuten opnieuw.'
      : 'Gebruik per app wordt opgehaald.'}</p></div></section>`
    : `<section class="panel"><div class="phead"><h2>Dataverbruik</h2><span class="aside">afgelopen 24 uur</span></div>
      <div class="pbody"><div class="wan"><div class="rate d"><b class="num">${bytes(ins.rx)}</b><span>download</span></div>
        <div class="rate u"><b class="num">${bytes(ins.tx)}</b><span>upload</span></div>
        <div class="fact"><b class="num">${bytes((ins.rx || 0) + (ins.tx || 0))}</b>totaal</div></div></div></section>`;
  const apps = ins ? `<section class="panel"><div class="phead"><h2>Apps (${ins.count})</h2><span class="aside">Meeste data bovenaan</span></div>
      <div class="pbody">${(ins.apps || []).length ? appList(ins.apps) : '<p class="offline">Nog geen gebruik gemeten.</p>'}</div></section>` : '';
  box.innerHTML = wifiCheck() + usage + flowsHTML() + apps + diagHTML();
}
function renderClient(key){
  const c = (data.clients || []).find(x => x.key === key), box = $('#detail');
  if (!c) { box.innerHTML = `<a class="back" href="#clients">Alle clients</a><div class="panel"><p class="empty">Deze client is nu niet verbonden.</p></div>`; return; }
  const dev = (data.devices || []).find(x => x.id === c.uplink), s = secsSince(c.since);
  const typ = c.type === 'WIRELESS' ? 'Wifi' : c.type === 'WIRED' ? 'Kabel' : String(c.type || 'Onbekend').toLowerCase();
  const peers = (data.clients || []).filter(x => x.uplink === c.uplink && x.key !== c.key)
    .sort((a, b) => String(a.name).localeCompare(String(b.name), 'nl'));
  const since = c.since ? new Date(c.since).toLocaleString(LOCALE, {day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit'}) : '';
  box.innerHTML = `<a class="back" href="#clients">Alle clients</a>
    <div class="dhead"><span class="gl">${cliIcon(c, 64)}</span><div><h2>${esc(c.name)}</h2>
      <div class="sub">${esc(typ)}${dev ? `, via <a href="${devHref(dev.id)}${c.port != null ? '/' + c.port : ''}">${esc(dev.name)}</a>${c.port != null ? ` poort ${c.port}` : ''}` : ''}</div></div></div>
    <div class="dgrid"><div>
      <section class="panel"><div class="phead"><h2>Gegevens</h2></div><div class="pbody"><dl class="kv">
        <dt>Verbinding</dt><dd>${esc(typ)}</dd>
        ${c.ssid ? `<dt>Wifi-netwerk</dt><dd>${esc(c.ssid)}</dd>` : ''}
        ${c.signal != null ? `<dt>Signaal</dt><dd>${sigHTML(c)}</dd>` : ''}
        ${c.band ? `<dt>Band en kanaal</dt><dd>${nf(c.band, c.band % 1 ? 1 : 0)} GHz${c.channel ? ', kanaal ' + esc(c.channel) : ''}</dd>` : ''}
        ${c.experience != null ? `<dt>Wifi-ervaring</dt><dd>${esc(c.experience)}%</dd>` : ''}
        ${c.txRate ? `<dt>Linksnelheid</dt><dd>↓ ${linkTxt(c.txRate)} ↑ ${linkTxt(c.rxRate)}</dd>` : ''}
        ${c.network ? `<dt>Netwerk</dt><dd>${esc(c.network)}</dd>` : ''}
        ${c.network ? `<dt>VLAN</dt><dd>${c.vlan ? esc(c.vlan) : 'Geen (standaard)'}</dd>` : ''}
        <dt>Verbonden met</dt><dd>${dev ? `<span class="cn2">${devMini(dev.id, 20)}<a href="${devHref(dev.id)}">${esc(dev.name)}</a></span>` : '–'}</dd>
        ${c.port != null ? `<dt>Poort</dt><dd><a href="${devHref(dev ? dev.id : '')}/${c.port}">Poort ${c.port}</a></dd>` : ''}
        <dt>IP-adres</dt><dd class="mono">${esc(c.ip || '–')}</dd><dt>MAC-adres</dt><dd class="mono">${esc(c.mac || '–')}</dd>
        <dt>Verbonden sinds</dt><dd>${isNaN(s) ? '–' : `${esc(since)}, ${dur(s)} geleden`}</dd>
      </dl></div></section>
      ${(c.apps || []).length ? `<section class="panel"><div class="phead"><h2>Meest gebruikte apps</h2><span class="aside">afgelopen 24 uur</span></div><div class="pbody">${appList(c.apps)}</div></section>` : ''}
    </div><div>
      ${dev ? `<section class="panel"><div class="phead"><h2>Ook op ${esc(dev.name)} (${peers.length})</h2></div><div class="pbody">
        ${peers.length ? `<ul class="cl">${peers.map(clientLi).join('')}</ul>` : '<p class="offline">Geen andere clients.</p>'}</div></section>` : ''}
    </div></div>`;
}

/* ---------------- clients ---------------- */
function renderClients(){
  const sel = $('#via'), cur = sel.value;
  sel.innerHTML = '<option value="">Alle apparaten</option>' + (data.devices || []).filter(d => d.kind !== 'gateway' || d.clients)
    .map(d => `<option value="${esc(d.id)}">${esc(d.name)} (${d.clients})</option>`).join('');
  sel.value = cur;
  const ns = $('#net'), ncur = ns.value, nets = [...new Set((data.clients || []).map(c => c.network).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'nl'));
  ns.hidden = !nets.length;
  ns.innerHTML = '<option value="">Alle netwerken</option>' + nets.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
  ns.value = ncur;
  const q = $('#q').value.trim().toLowerCase(), via = $('#via').value, net = ns.value;
  const rows = (data?.clients || []).filter(c =>
    (kindFilter === 'all' || c.type === kindFilter) && (!via || c.uplink === via) && (!net || c.network === net) &&
    (!q || [c.name, c.ip, c.mac, c.uplinkName, c.ssid, c.network].some(v => String(v || '').toLowerCase().includes(q))));
  rows.sort((a, b) => {
    let x = a[sortKey] ?? '', y = b[sortKey] ?? '';
    if (sortKey === 'signal') return ((a.signal ?? -999) - (b.signal ?? -999)) * -sortDir;
    if (sortKey === 'since') return ((Date.parse(y) || 0) - (Date.parse(x) || 0)) * sortDir;
    if (sortKey === 'ip') { const n = s => String(s).split('.').map(p => p.padStart(3, '0')).join('.'); x = n(x); y = n(y); }
    return String(x).localeCompare(String(y), 'nl', {numeric: true}) * sortDir;
  });
  const typ = t => t === 'WIRELESS' ? 'Wifi' : t === 'WIRED' ? 'Kabel' : esc(String(t || '–').toLowerCase());
  $('#clients').innerHTML = rows.map(c => {
    const s = secsSince(c.since), fresh = s < 3600;
    return `<tr class="click" data-key="${esc(c.key)}"><td class="c-name"><span class="cn2">${cliIcon(c, 22)}<a class="cn" href="${cliHref(c.key)}">${esc(c.name)}</a></span>${fresh ? ' <span class="tag new">Nieuw</span>' : ''}</td><td class="c-type">${typ(c.type)}${c.ssid ? `<span class="cport">, ${esc(c.ssid)}</span>` : ''}</td><td class="c-sig">${sigHTML(c)}</td><td class="c-net">${esc(netTxt(c) || '–')}</td><td class="c-via">${esc(c.uplinkName || '–')}${c.port != null ? `<span class="cport">, poort ${c.port}</span>` : ''}</td>
    <td class="c-ip"><span class="mono">${esc(c.ip || '–')}</span></td><td class="c-mac"><span class="mono">${esc(c.mac || '–')}</span></td><td class="c-since num">${isNaN(s) ? '–' : dur(s) + ' geleden'}</td>
    <td class="c-meta">${typ(c.type)}${c.ssid ? ' ' + esc(c.ssid) : ''}${c.network ? ', ' + esc(netTxt(c)) : ''}${c.uplinkName ? ' via ' + esc(c.uplinkName) + (c.port != null ? `, poort ${c.port}` : '') : ''}, <span class="mono">${esc(c.ip || 'geen IP')}</span></td></tr>`;
  }).join('') || `<tr><td colspan="8" class="empty">${q || via || net || kindFilter !== 'all' ? 'Geen clients gevonden met dit filter.' : 'Er zijn nu geen clients verbonden.'}</td></tr>`;
  document.querySelectorAll('#clients tr.click').forEach(tr => tr.onclick = e => { if (!e.target.closest('a')) location.hash = cliHref(tr.dataset.key).slice(1); });
}

/* ---------------- laden ---------------- */
function route(){
  const parts = decodeURIComponent(location.hash.slice(1)).split('/');
  if (parts[0] === 'apparaat' && parts[1]) return {v: 'apparaat', id: parts[1], port: parts[2] ? +parts[2] : null};
  if (parts[0] === 'client' && parts[1]) return {v: 'client', id: parts.slice(1).join('/')};
  return {v: ['apparaten', 'clients', 'internet', 'inzichten', 'logboek'].includes(parts[0]) ? parts[0] : 'overzicht'};
}
let lastRoute = '';
function render(){
  renderHead();
  const r = route(), det = r.v === 'apparaat' || r.v === 'client';
  const tab = r.v === 'apparaat' ? 'apparaten' : r.v === 'client' ? 'clients' : r.v === 'internet' ? 'overzicht' : r.v;
  document.querySelectorAll('[data-view]').forEach(el => el.hidden = !el.dataset.view.split(' ').includes(det ? 'detail' : r.v));
  document.querySelectorAll('#tabs a').forEach(a => a.setAttribute('aria-current', a.dataset.v === tab ? 'page' : 'false'));
  const at = document.querySelector('#tabs a[aria-current="page"]'), tb = $('#tabs');
  if (at && (at.offsetLeft < tb.scrollLeft || at.offsetLeft + at.offsetWidth > tb.scrollLeft + tb.clientWidth)) tb.scrollLeft = at.offsetLeft - 16;
  if (location.hash !== lastRoute) { hlPort = r.port ?? null; lastRoute = location.hash; }
  if (r.v === 'overzicht') { renderMap(); renderChart(); }
  else if (r.v === 'internet') { renderInternet(); renderChart(); }
  else if (r.v === 'apparaten') renderDevices();
  else if (r.v === 'clients') renderClients();
  else if (r.v === 'inzichten') renderInsights();
  else if (r.v === 'logboek') renderLog();
  else if (r.v === 'apparaat') renderDevice(r.id);
  else renderClient(r.id);
  if (hlPort != null && r.v === 'apparaat' && !render.scrolled) {
    render.scrolled = true;
    const li = document.querySelector(`#detail .who li[data-port="${hlPort}"]`); if (li) li.scrollIntoView({block: 'center'});
  }
}
window.addEventListener('hashchange', () => {
  if (!data) return;
  render.scrolled = true; render(); window.scrollTo(0, 0);
  const li = document.querySelector('#detail .who li.hl'); if (li) li.scrollIntoView({block: 'center'});
});
async function load(){
  clearTimeout(timer);
  try {
    const r = await fetch('api/summary?range=' + range, {cache: 'no-store'});
    data = await r.json(); render();
  } catch (e) {
    $('#dot').className = 'sd bad';
    $('#status').textContent = 'Geen verbinding met de dashboardservice. Controleer of de app of het script nog draait.';
  }
  timer = setTimeout(load, ((data && data.refresh) || 10) * 1000);
}

$('#range').addEventListener('click', e => { const r = e.target.dataset.r; if (!r || r === range) return; range = r;
  document.querySelectorAll('#range button').forEach(b => b.setAttribute('aria-pressed', b === e.target)); load(); });
$('#kind').addEventListener('click', e => { const f = e.target.dataset.f; if (!f) return; kindFilter = f;
  document.querySelectorAll('#kind button').forEach(b => b.setAttribute('aria-pressed', b === e.target)); renderClients(); });
$('#q').addEventListener('input', renderClients);
$('#via').addEventListener('change', renderClients);
$('#net').addEventListener('change', renderClients);
$('#lq').addEventListener('input', () => data && renderLog());
$('#lk').addEventListener('change', () => data && renderLog());
document.querySelectorAll('th[data-k] button').forEach(btn => btn.addEventListener('click', () => {
  const th = btn.parentElement, k = th.dataset.k; sortDir = k === sortKey ? -sortDir : 1; sortKey = k;
  document.querySelectorAll('th[data-k]').forEach(x => x.removeAttribute('aria-sort'));
  th.setAttribute('aria-sort', sortDir === 1 ? 'ascending' : 'descending'); renderClients(); }));
$('#chart').addEventListener('mousemove', chartHover);
$('#chart').addEventListener('mouseleave', () => { $('#tip').hidden = true; drawChart(); });
let rz; window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { if (data && route().v === 'overzicht') { drawLinks(); drawChart(); } }, 80); });
if (document.fonts) document.fonts.ready.then(() => data && route().v === 'overzicht' && drawLinks());
load();
</script>
</body>
</html>
"""


def _i18n_js():
    """De vertaallaag (Nederlands of Engels, volgt de taal van Home Assistant) met de Engelse teksten."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import i18n_en
    with open(os.path.join(here, "dom_i18n.js"), encoding="utf-8") as f:
        js = f.read()
    exact = dict(i18n_en.EXACT, **(i18n_en.DEMO if DEMO else {}))
    return js.replace("__I18N_EXACT__", json.dumps(exact, ensure_ascii=False)).replace("__I18N_PATTERNS__", json.dumps(i18n_en.PATTERNS, ensure_ascii=False))


PAGE = PAGE.replace("__I18N__", _i18n_js())


def main():
    if DEMO:
        backfill_demo()
    else:
        load_history()
    threading.Thread(target=collector, daemon=True).start()
    if ALLOWED_USERS:
        print("Toegestane gebruikers: " + ", ".join(ALLOWED_USERS), flush=True)
    else:
        print("Toegestane gebruikers: iedereen (de lijst is leeg).", flush=True)
    server = ThreadingHTTPServer((LISTEN, PORT), Handler)
    shown = "localhost" if LISTEN in ("127.0.0.1", "0.0.0.0") else LISTEN
    print(f"UniFi-dashboard draait op http://{shown}:{PORT}")
    print("Voorbeeldgegevens (demo)." if DEMO else f"Console: {UNIFI_HOST}")
    print("Stoppen met Ctrl+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if not DEMO:
            save_history()


if __name__ == "__main__":
    main()
