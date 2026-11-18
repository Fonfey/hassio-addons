#!/usr/bin/env python3
"""
AdGuard Dashboard
=====================

Statistieken van je AdGuard DNS-account (de clouddienst op adguard-dns.io) als
Home Assistant-app of los op je computer.

Los gebruiken (Python 3.8+, geen extra pakketten):
1. Maak een API-key aan in je AdGuard DNS-dashboard (User preferences -> API keys).
2. Zet de key in de omgevingsvariabele ADGUARD_API_KEY of vul hem hieronder in.
3. Start:  py adguard_dashboard.py   (Windows)   of   python3 adguard_dashboard.py
   Open:   http://localhost:8081
   Demo:   py adguard_dashboard.py --demo

De API-key blijft in dit script en gaat nooit naar de browser.
"""

import hashlib
import json
from html import escape as html_escape
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
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
    val = _OPTS.get(key.lower().replace("adguard_", ""))
    if val in (None, ""):
        val = os.environ.get(key, default)
    return val


ADGUARD_API_KEY = _opt("ADGUARD_API_KEY", "")
MODE = "home" if str(_opt("SOURCE", "") or _opt("MODE", "cloud")).strip().lower() == "home" else "cloud"
HOME_URL = str(_opt("HOME_URL", "")).strip()
HOME = MODE == "home"
REFRESH_SECONDS = int(_opt("REFRESH_SECONDS", "60"))
HOME_COUNTRY = str(_opt("HOME_COUNTRY", "NL")).upper()[:2]
_ab = _OPTS.get("allow_blocking", os.environ.get("ALLOW_BLOCKING", "true"))
ALLOW_BLOCKING = _ab if isinstance(_ab, bool) else str(_ab).lower() not in ("0", "false", "nee", "no")
LISTEN = "0.0.0.0" if HA_APP else os.environ.get("LISTEN", "127.0.0.1")
PORT = 8099 if HA_APP else int(os.environ.get("PORT", "8081"))
DEMO = "--demo" in sys.argv or (not HOME and not ADGUARD_API_KEY) or (HOME and not HOME_URL)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agh_home  # noqa: E402
agh_home.configure(HOME_URL, _opt("HOME_USERNAME", ""), _opt("HOME_PASSWORD", ""))

API_BASE = "https://api.adguard-dns.io/oapi/"
ALLOWED = ("v1/account/limits", "v1/account/license", "v2/dns_servers", "v2/devices", "v1/stats/", "v1/query_log",
           "v1/dns_servers/", "v2/filter_lists", "home/")
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE, CACHE_LOCK = {}, threading.Lock()


# ---------------------------------------------------------------- AdGuard API
def fetch_api(path, query):
    url = API_BASE + path + ("?" + query if query else "")
    ttl = 10 if path.startswith("v1/query_log") else 30
    with CACHE_LOCK:
        hit = CACHE.get(url)
        if hit and time.time() - hit[0] < ttl:
            return 200, hit[1]
    req = urllib.request.Request(url, headers={"Authorization": f"ApiKey {ADGUARD_API_KEY}",
                                               "Accept": "application/json",
                                               "User-Agent": "ha-adguard-dns-dashboard"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            msg = "AdGuard DNS weigert de API-key. Controleer de key in de instellingen van de app."
        elif e.code == 429:
            msg = "Te veel verzoeken naar AdGuard DNS. Verhoog de verversingstijd in de instellingen."
        else:
            msg = f"AdGuard DNS gaf een fout: HTTP {e.code}."
        return e.code, json.dumps({"error": msg})
    except urllib.error.URLError as e:
        return 502, json.dumps({"error": f"AdGuard DNS is niet bereikbaar ({e.reason})."})
    with CACHE_LOCK:
        CACHE[url] = (time.time(), body)
        if len(CACHE) > 400:
            for k in sorted(CACHE, key=lambda k: CACHE[k][0])[:200]:
                CACHE.pop(k, None)
    return 200, body


def api_json(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API_BASE + path, data=data, method=method,
                                 headers={"Authorization": f"ApiKey {ADGUARD_API_KEY}", "Accept": "application/json",
                                          "Content-Type": "application/json", "User-Agent": "ha-adguard-dns-dashboard"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def rule_variants(domain):
    d = domain.strip().lower().rstrip(".")
    return f"||{d}^", f"@@||{d}^"


def apply_rules(rules, domain=None, action=None, filtering_type=None, add=(), remove=()):
    """Past de lijst gebruikersregels aan en geeft (nieuwe lijst, uitleg) terug."""
    rules = list(rules)
    if domain and action in ("block", "unblock"):
        block, allow = rule_variants(domain)
        if action == "block":
            rules = [r for r in rules if r.strip() != allow]
            if block not in rules:
                rules.append(block)
            return rules, f"{domain} wordt geblokkeerd."
        had_block = block in [r.strip() for r in rules]
        rules = [r for r in rules if r.strip() != block]
        if not had_block or filtering_type not in ("USER_FILTER",):
            if allow not in rules:
                rules.append(allow)
        return rules, f"{domain} wordt niet meer geblokkeerd."
    for r in remove:
        rules = [x for x in rules if x.strip() != str(r).strip()]
    for r in add:
        r = str(r).strip()
        if r and r not in rules:
            rules.append(r[:1024])
    return rules, "De gebruikersregels zijn bijgewerkt."


def change_rules(payload):
    srv = str(payload.get("server_id") or "")
    if not srv or "/" in srv or ".." in srv:
        raise ValueError("Geen geldige server opgegeven.")
    kwargs = dict(domain=payload.get("domain"), action=payload.get("action"),
                  filtering_type=payload.get("filtering_type"), add=payload.get("add") or [], remove=payload.get("remove") or [])
    if HOME and not DEMO:
        new, msg = apply_rules(agh_home.read_rules(), **kwargs)
        agh_home.set_rules(new)
        return {"ok": True, "message": msg, "rules": new}
    if DEMO:
        server = next((x for x in DEMO_SERVERS if x["id"] == srv), None)
        if not server:
            raise ValueError("Server niet gevonden.")
        new, msg = apply_rules(server["settings"]["user_rules_settings"]["rules"], **kwargs)
        server["settings"]["user_rules_settings"]["rules"] = new
        server["settings"]["user_rules_settings"]["rules_count"] = len(new)
        return {"ok": True, "message": msg, "rules": new}
    current = api_json("GET", f"v1/dns_servers/{urllib.parse.quote(srv)}")
    rules = ((current.get("settings") or {}).get("user_rules_settings") or {}).get("rules") or []
    new, msg = apply_rules(rules, **kwargs)
    api_json("PUT", f"v1/dns_servers/{urllib.parse.quote(srv)}/settings", {"user_rules_settings": {"enabled": True, "rules": new}})
    with CACHE_LOCK:
        CACHE.clear()
    return {"ok": True, "message": msg, "rules": new}


BOOL, INT = "bool", "int"
SETTINGS_SCHEMA = {
    "protection_enabled": BOOL, "ip_log_enabled": BOOL, "auto_connect_devices_enabled": BOOL,
    "block_chrome_prefetch": BOOL, "block_firefox_canary": BOOL, "block_private_relay": BOOL, "block_ttl_seconds": INT,
    "blocking_mode_settings": {"blocking_mode": ("NONE", "NULL_IP", "REFUSED", "NXDOMAIN", "CUSTOM_IP"),
                               "ipv4_blocking_address": "str", "ipv6_blocking_address": "str"},
    "safebrowsing_settings": {"enabled": BOOL, "block_dangerous_domains": BOOL, "block_nrd": BOOL,
                              "homoglyph_protection_enabled": BOOL, "typosquatting_protection_enabled": BOOL},
    "parental_control_settings": {"enabled": BOOL, "block_adult_websites_enabled": BOOL,
                                  "engines_safe_search_enabled": BOOL, "youtube_safe_search_enabled": BOOL},
}


def clean_patch(patch, schema=SETTINGS_SCHEMA):
    """Laat alleen bekende instellingen met het juiste type door."""
    out = {}
    for k, v in (patch or {}).items():
        t = schema.get(k)
        if t is None:
            raise ValueError(f"Onbekende instelling: {k}")
        if isinstance(t, dict):
            out[k] = clean_patch(v, t)
        elif t == BOOL:
            if not isinstance(v, bool):
                raise ValueError(f"{k} moet aan of uit zijn.")
            out[k] = v
        elif t == INT:
            v = int(v)
            if not 0 <= v <= 3600:
                raise ValueError(f"{k} moet tussen 0 en 3600 liggen.")
            out[k] = v
        elif isinstance(t, tuple):
            if v not in t:
                raise ValueError(f"Ongeldige waarde voor {k}.")
            out[k] = v
        else:
            out[k] = str(v)[:64]
    if "blocking_mode_settings" in out and "blocking_mode" not in out["blocking_mode_settings"]:
        raise ValueError("Kies een blokkeermodus.")
    return out


def _safe_id(x):
    x = str(x or "")
    if not x or "/" in x or ".." in x or len(x) > 128:
        raise ValueError("Ongeldige id.")
    return urllib.parse.quote(x)


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def change_settings(payload):
    srv = _safe_id(payload.get("server_id"))
    if HOME and not DEMO:
        if payload.get("filter_id") is not None:
            on = bool(payload.get("enabled"))
            agh_home.toggle_list(payload["filter_id"], on)
            return {"ok": True, "message": "Blokkeerlijst ingeschakeld." if on else "Blokkeerlijst uitgeschakeld."}
        patch = clean_patch(payload.get("patch"))
        if not patch:
            raise ValueError("Er is niets om te wijzigen.")
        agh_home.change_settings(patch)
        return {"ok": True, "message": "Instelling opgeslagen."}
    if payload.get("filter_id") is not None:
        fid, on = str(payload["filter_id"]), bool(payload.get("enabled"))
        if DEMO:
            server = next(x for x in DEMO_SERVERS if x["id"] == payload["server_id"])
            fl = server["settings"]["filter_lists_settings"]["filter_list"]
        else:
            fl = ((api_json("GET", f"v1/dns_servers/{srv}").get("settings") or {}).get("filter_lists_settings") or {}).get("filter_list") or []
        items = [{"filter_id": f["filter_id"], "enabled": bool(f.get("enabled"))} for f in fl]
        hit = next((f for f in items if f["filter_id"] == fid), None)
        if hit:
            hit["enabled"] = on
        else:
            items.append({"filter_id": fid, "enabled": on})
        if DEMO:
            server["settings"]["filter_lists_settings"]["filter_list"] = items
        else:
            api_json("PUT", f"v1/dns_servers/{srv}/settings", {"filter_lists_settings": {"filter_list": items}})
        msg = "Blokkeerlijst ingeschakeld." if on else "Blokkeerlijst uitgeschakeld."
    else:
        patch = clean_patch(payload.get("patch"))
        if not patch:
            raise ValueError("Er is niets om te wijzigen.")
        if DEMO:
            _merge(next(x for x in DEMO_SERVERS if x["id"] == payload["server_id"])["settings"], patch)
        else:
            api_json("PUT", f"v1/dns_servers/{srv}/settings", patch)
        msg = "Instelling opgeslagen."
    with CACHE_LOCK:
        CACHE.clear()
    return {"ok": True, "message": msg}


def change_device(payload):
    if HOME and not DEMO:
        raise ValueError("Bescherming per apparaat stel je in AdGuard Home zelf in, bij Instellingen, Clientinstellingen.")
    dev = _safe_id(payload.get("device_id"))
    on = payload.get("protection_enabled")
    if not isinstance(on, bool):
        raise ValueError("Geef aan of de bescherming aan of uit moet.")
    if DEMO:
        next(x for x in DEMO_DEVICES if x["id"] == payload["device_id"])["settings"]["protection_enabled"] = on
    else:
        api_json("PUT", f"v1/devices/{dev}/settings", {"protection_enabled": on})
    with CACHE_LOCK:
        CACHE.clear()
    return {"ok": True, "message": "Bescherming ingeschakeld." if on else "Bescherming uitgeschakeld."}


def home_action(payload):
    if not HOME or DEMO:
        raise ValueError("Dit werkt alleen met AdGuard Home.")
    res = agh_home.act(payload if isinstance(payload, dict) else {})
    return dict({"ok": True}, **res) if isinstance(res, dict) else {"ok": True, "message": res}


def read_rules(srv):
    if HOME and not DEMO:
        return agh_home.read_rules()
    if DEMO:
        server = next((x for x in DEMO_SERVERS if x["id"] == srv), None)
        return (server or {}).get("settings", {}).get("user_rules_settings", {}).get("rules", [])
    current = api_json("GET", f"v1/dns_servers/{urllib.parse.quote(srv)}")
    return ((current.get("settings") or {}).get("user_rules_settings") or {}).get("rules") or []


# ---------------------------------------------------------------- demo-data
def _seed(*parts):
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


DEMO_FILTERS = [("adguard_dns_filter", "AdGuard DNS filter", 158000, "Advertenties, trackers en analytics in één lijst."),
                ("adaway", "AdAway Default Blocklist", 6500, "Advertentieservers op mobiele apparaten."),
                ("easylist", "EasyList", 54000, "De bekendste lijst tegen advertenties."),
                ("easyprivacy", "EasyPrivacy", 47000, "Trackers en analytics."),
                ("easylist_dutch", "EasyList Dutch", 3200, "Aanvulling voor Nederlandse websites."),
                ("phishing_army", "Phishing Army", 120000, "Phishingdomeinen."),
                ("urlhaus", "Malicious URL Blocklist (URLHaus)", 9800, "Domeinen die malware verspreiden."),
                ("hagezi_pro", "HaGeZi's Pro DNS Blocklist", 210000, "Uitgebreide lijst tegen advertenties en tracking."),
                ("oisd_small", "OISD Blocklist Small", 52000, "Compacte lijst met weinig vals-positieven."),
                ("steven_black", "Steven Black's List", 81000, "Advertenties en malware."),
                ("windows_spy", "WindowsSpyBlocker", 350, "Telemetrie van Windows."),
                ("smart_tv", "Smart-TV Blocklist", 260, "Telemetrie en advertenties van smart-tv's."),
                ("game_console", "Game Console Adblock List", 120, "Advertenties op spelcomputers.")]
DEMO_SERVERS = [{"id": "srv1", "name": "Standaard DNS", "default": True, "devices_count": 2,
                 "settings": {"protection_enabled": True, "ip_log_enabled": True, "auto_connect_devices_enabled": False,
                              "block_chrome_prefetch": True, "block_firefox_canary": True, "block_private_relay": False,
                              "block_ttl_seconds": 10,
                              "blocking_mode_settings": {"blocking_mode": "REFUSED"},
                              "safebrowsing_settings": {"enabled": True, "block_dangerous_domains": True, "block_nrd": False,
                                                        "homoglyph_protection_enabled": True, "typosquatting_protection_enabled": True},
                              "parental_control_settings": {"enabled": False, "block_adult_websites_enabled": False,
                                                            "engines_safe_search_enabled": False, "youtube_safe_search_enabled": False},
                              "filter_lists_settings": {"enabled": True,
                                                        "filter_list": [{"filter_id": f[0], "enabled": True} for f in DEMO_FILTERS[:9]]},
                              "user_rules_settings": {"enabled": True, "rules_count": 4,
                                                      "rules": ["||doubleclick.net^", "||appnext.com^", "@@||spotify.com^",
                                                                "||o427061.ingest.sentry.io^"]}}}]
def _addr(code):
    return {"dns_over_https_url": f"https://d.adguard-dns.com/dns-query/{code}", "dns_over_tls_url": f"tls://{code}.d.adguard-dns.com",
            "dns_over_quic_url": f"quic://{code}.d.adguard-dns.com", "ip_addresses": [{"ip_address": "94.140.14.49", "type": "V4"}],
            "bootstrap_addresses": []}


DEMO_DEVICES = [{"id": "dev1", "name": "Unifi UDM", "device_type": "ROUTER", "dns_server_id": "srv1",
                 "settings": {"protection_enabled": True}, "dns_addresses": _addr("a1b2c3d4")},
                {"id": "dev2", "name": "iPhone", "device_type": "IOS", "dns_server_id": "srv1",
                 "settings": {"protection_enabled": True}, "dns_addresses": _addr("e5f6a7b8")}]
DEV_SHARE = {"dev1": 0.86, "dev2": 0.14}
DEMO_COMPANIES = [
    ("Microsoft Corporation", 380, 0.416, 0.73, "eu-teams.events.data.microsoft.com", "eu-teams.events.data.microsoft.com"),
    ("Google", 251, 0.145, 0.16, "google.com", "ssl.google-analytics.com"),
    ("appnext.com", 2, 0.056, 1.0, "global.appnext.com", "global.appnext.com"),
    ("Sentry", 18, 0.054, 1.0, "o427061.ingest.sentry.io", "o427061.ingest.sentry.io"),
    ("Meta Platforms, Inc.", 64, 0.022, 0.25, "graph.facebook.com", "connect.facebook.net"),
    ("Apple Inc.", 120, 0.022, 0.03, "gateway.icloud.com", "metrics.icloud.com"),
    ("AppLovin", 14, 0.016, 1.0, "ms.applovin.com", "ms.applovin.com"),
    ("Amazon", 88, 0.015, 0.08, "amazonaws.com", "device-metrics-us.amazon.com"),
    ("Cloudflare", 40, 0.013, 0.0, "cloudflare-dns.com", None),
    ("Spotify", 22, 0.009, 0.12, "spclient.wg.spotify.com", "log.spotify.com"),
    ("Samsung", 31, 0.008, 0.55, "samsungcloudsolution.com", "samsungads.com"),
    ("TikTok", 19, 0.006, 0.4, "tiktokv.com", "log.tiktokv.com"),
    ("Akamai", 57, 0.006, 0.0, "akamaized.net", None),
    ("Netflix", 12, 0.005, 0.0, "nflxvideo.net", None),
    ("Ubiquiti", 9, 0.004, 0.0, "ui.com", None),
    (None, 3, 0.004, 0.5, "unknown-tracker.example", "unknown-tracker.example"),
]
DEMO_COUNTRIES = [("US", 0.371, 0.62), ("", 0.182, 0.10), ("NL", 0.178, 0.44), ("IE", 0.09, 0.3), ("DE", 0.06, 0.2),
                  ("GB", 0.04, 0.15), ("SE", 0.02, 0.1), ("FR", 0.02, 0.1), ("IL", 0.012, 0.5), ("JP", 0.008, 0.05),
                  ("AU", 0.006, 0.02)]
DEMO_DOMAINS = [("microsoft.com", 0.371, 0.8), ("google.com", 0.087, 0.01), ("appnext.com", 0.056, 1.0),
                ("sentry.io", 0.054, 1.0), ("apple.com", 0.03, 0.02), ("icloud.com", 0.025, 0.05),
                ("facebook.com", 0.02, 0.3), ("applovin.com", 0.016, 1.0), ("doubleclick.net", 0.014, 1.0),
                ("spotify.com", 0.012, 0.1), ("netflix.com", 0.01, 0.0), ("whatsapp.net", 0.009, 0.0)]
CATS = [("ADS", 0.125), ("TRACKERS", 0.071), ("SOCIAL_MEDIA", 0.0014), ("CDN", 0.04), ("OTHERS", 0.7626)]


def _demo_bucket(t_ms, step_ms):
    rnd = random.Random(_seed("b", t_ms // step_ms))
    hour = time.localtime(t_ms / 1000).tm_hour
    base = 3600 + 1400 * (1 if 17 <= hour <= 21 else 0) - 900 * (1 if 1 <= hour <= 6 else 0)
    q = int(base * (0.75 + rnd.random() * 0.6))
    b = int(q * (0.35 + rnd.random() * 0.3))
    scale = step_ms / 3600000
    return int(q * scale), int(b * scale), int(30 + rnd.random() * 40)


def _period_totals(frm, to):
    step = 3600000
    q = b = 0
    for t in range(frm - frm % step, to, step):
        qq, bb, _ = _demo_bucket(t, step)
        q, b = q + qq, b + bb
    return q, b


def _share(params):
    devs = params.get("devices") or []
    return sum(DEV_SHARE.get(d, 0) for d in devs) if devs else 1.0


def _page(items, params, default_limit=10):
    limit = int((params.get("limit") or [default_limit])[0])
    return {"stats": items[:limit], "navigation": {}}


def demo_response(path, params):
    frm = int((params.get("time_from_millis") or [int(time.time() * 1000) - 86400000])[0])
    to = int((params.get("time_to_millis") or [int(time.time() * 1000)])[0])
    share = _share(params)
    q_tot, b_tot = _period_totals(frm, to)
    q_tot, b_tot = int(q_tot * share), int(b_tot * share)
    v = lambda frac, blk: {"queries": int(q_tot * frac), "blocked": int(q_tot * frac * blk), "companies": 0}

    if path == "v1/account/limits":
        lim = lambda u, l: {"used": u, "limit": l}
        return {"requests": lim(413000, 10000000), "devices": lim(2, 20), "dns_servers": lim(1, 5),
                "access_rules": lim(0, 1000), "dedicated_ipv4": lim(0, 1), "user_rules": lim(34, 1000)}
    if path == "v1/account/license":
        return {"license_type": "PERSONAL", "status": "VALID", "expiration_date": "2026-12-29T00:00:00Z"}
    if path == "v2/dns_servers":
        return {"items": DEMO_SERVERS, "navigation": {}}
    if path == "v2/devices":
        return {"items": DEMO_DEVICES, "navigation": {}}
    if path.startswith("v1/dns_servers/"):
        sid = path.split("/")[2]
        return next((x for x in DEMO_SERVERS if x["id"] == sid), {"error": "Server niet gevonden."})
    if path == "v2/filter_lists":
        return {"items": [{"filter_id": f, "name": n, "type": "INTERNAL", "rules_count": c, "description": d,
                           "time_updated": "2026-11-06T06:00:00Z"} for f, n, c, d in DEMO_FILTERS]}
    if path == "v1/stats/time":
        step = 3600000 if to - frm <= 2 * 86400000 else 86400000
        stats = []
        for t in range(frm - frm % step + step, to + 1, step):
            if step == 3600000:
                q, b, c = _demo_bucket(t, step)
            else:
                q, b = _period_totals(t - step, t)
                c = 90 + _seed("d", t // step) % 40
            stats.append({"time_millis": t, "value": {"queries": int(q * share), "blocked": int(b * share), "companies": c}})
        return {"stats": stats}
    if path == "v1/stats/categories":
        return {"stats": [{"category_type": c, "queries": int(q_tot * f)} for c, f in CATS]}
    if path in ("v1/stats/companies", "v1/stats/companies/detailed"):
        n = 108 + (_seed("c", frm // 3600000) % 12) - 6
        items = []
        for name, doms, frac, blk, top, topb in DEMO_COMPANIES:
            it = {"company_name": name, "value": v(frac, blk)}
            if path.endswith("detailed"):
                it.update(domains_count=doms, top_queries_domain=top)
                if topb:
                    it["top_blocked_domain"] = topb
            items.append(it)
        for i in range(len(items), n):
            it = {"company_name": f"Bedrijf {i + 1}", "value": v(0.0008, 0.2)}
            if path.endswith("detailed"):
                it.update(domains_count=1 + i % 7, top_queries_domain=f"bedrijf{i + 1}.example")
            items.append(it)
        return _page(items, params)
    if path == "v1/stats/countries":
        return _page([{"country": c, "value": v(f, blk)} for c, f, blk in DEMO_COUNTRIES], params)
    if path == "v1/stats/domains":
        return _page([{"domain": d, "value": v(f, blk)} for d, f, blk in DEMO_DOMAINS], params)
    if path == "v1/stats/devices":
        items = [{"device_id": d["id"], "last_activity_time_millis": int(time.time() * 1000) - 60000,
                  "value": {"queries": int(q_tot * DEV_SHARE[d["id"]]), "blocked": int(b_tot * DEV_SHARE[d["id"]]),
                            "companies": 108 if d["id"] == "dev1" else 46}} for d in DEMO_DEVICES]
        return _page(items, params)
    if path == "v1/query_log":
        rnd = random.Random(_seed("log", int(time.time()) // 20))
        items, now = [], int(time.time() * 1000)
        for i in range(int((params.get("limit") or [50])[0])):
            dom, _, blk = rnd.choice(DEMO_DOMAINS)
            sub = rnd.choice(["", "www.", "api.", "events.data.", "cdn."])
            blocked = rnd.random() < blk
            cat = rnd.choice(["ADS", "TRACKERS"]) if blocked else rnd.choice(["OTHERS", "OTHERS", "CDN"])
            full = sub + dom
            rules = DEMO_SERVERS[0]["settings"]["user_rules_settings"]["rules"]
            ftype = "FILTERS" if blocked else None
            status = "REQUEST_BLOCKED" if blocked else "NONE"
            for r in rules:
                pat = r.replace("@@", "").strip("|^")
                if full == pat or full.endswith("." + pat):
                    if r.startswith("@@"):
                        status, ftype, blocked = "REQUEST_ALLOWED", "USER_FILTER", False
                    else:
                        status, ftype, blocked = "REQUEST_BLOCKED", "USER_FILTER", True
            items.append({"domain": full, "time_millis": now - i * rnd.randint(2000, 40000),
                          "device_id": rnd.choice(["dev1"] * 5 + ["dev2"]), "dns_server_id": "srv1",
                          "category_type": cat, "company_id": dom.split(".")[0], "dnssec": False,
                          "response_country": rnd.choice(["US", "NL", "IE", "DE"]),
                          "network": "203.0.113.42", "dns_proto_type": "DOH",
                          "dns_request_type": rnd.choice(["TypeA", "TypeA", "TypeAAAA", "TypeHTTPS"]),
                          "filtering_info": {"filtering_status": status, "filtering_type": ftype}})
        items.sort(key=lambda x: -x["time_millis"])
        st = (params.get("statuses") or [])
        if st:
            items = [x for x in items if x["filtering_info"]["filtering_status"] in st]
        s = (params.get("search") or [""])[0]
        if s:
            items = [x for x in items if s.lower() in x["domain"]]
        return {"items": items, "pages": [{"current": True, "page_cursor": "", "page_number": 1}]}
    return {"error": "Onbekend pad in demomodus."}


# ---------------------------------------------------------------- toegang
ALLOWED_USERS = [str(u).strip().lower() for u in (_OPTS.get("allowed_users") or []) if str(u).strip()]
DENIED_PAGE = """<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Geen toegang / No access</title><style>body{margin:0;font:16px/1.5 system-ui,sans-serif;background:#1c222b;color:#e6ebf2;display:grid;place-items:center;min-height:100vh;padding:24px;box-sizing:border-box}
main{max-width:440px}h1{font-size:22px;margin:0 0 8px}p{color:#a5afbd;margin:0 0 14px}</style></head><body><main><h1>No access</h1>
<p>Your account is not in the list of allowed users of this dashboard. Ask an administrator to add your username in the app settings.</p>
<h1>Geen toegang</h1>
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
def load_map():
    try:
        with open(os.path.join(HERE, "map.json"), encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "{}"


MAP_JSON = load_map()


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype, cache=False):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not user_allowed(self.headers):
            if "/api/" in self.path:
                self.send(403, json.dumps({"error": "Geen toegang tot dit dashboard."}), "application/json")
            else:
                who = user_label(self.headers)
                extra = (f"<p style='margin-top:12px'>Je bent ingelogd als <b>{html_escape(who)}</b>. Geef deze naam door aan een beheerder.</p>"
                         if who else "")
                self.send(403, DENIED_PAGE.replace("</main>", extra + "</main>"), "text/html; charset=utf-8")
            return
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/index.html"):
            page = PAGE.replace("__CONFIG__", json.dumps({"demo": DEMO, "refresh": REFRESH_SECONDS, "mode": "cloud" if DEMO else MODE, "want": MODE,
                                                          "home_url": agh_home.CFG["url"] if HOME else "",
                                                          "home": HOME_COUNTRY, "blocking": ALLOW_BLOCKING}))
            self.send(200, page.replace("</body>", user_footer(self.headers) + "</body>"), "text/html; charset=utf-8")
        elif url.path == "/map.json":
            self.send(200, MAP_JSON, "application/json", cache=True)
        elif url.path == "/api/rules":
            srv = urllib.parse.parse_qs(url.query).get("server_id", [""])[0]
            try:
                self.send(200, json.dumps({"rules": read_rules(srv)}), "application/json")
            except Exception as e:
                self.send(502, json.dumps({"error": describe_err(e)}), "application/json")
        elif url.path == "/api/proxy":
            pairs = urllib.parse.parse_qsl(url.query, keep_blank_values=True)
            path = next((v for k, v in pairs if k == "p"), "")
            rest = [(k, v) for k, v in pairs if k != "p"]
            if not path or ".." in path or not path.startswith(ALLOWED):
                self.send(400, json.dumps({"error": "Pad niet toegestaan."}), "application/json")
                return
            if DEMO or HOME:
                params = {}
                for k, v in rest:
                    params.setdefault(k, []).append(v)
                if DEMO:
                    self.send(200, json.dumps(demo_response(path, params)), "application/json")
                    return
                try:
                    self.send(200, json.dumps(agh_home.respond(path, params)), "application/json")
                except agh_home.HomeError as e:
                    self.send(502, json.dumps({"error": str(e)}), "application/json")
                except ValueError as e:
                    self.send(400, json.dumps({"error": str(e)}), "application/json")
                return
            code, body = fetch_api(path, urllib.parse.urlencode(rest))
            self.send(code, body, "application/json")
        else:
            self.send(404, "Niet gevonden", "text/plain; charset=utf-8")

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if not user_allowed(self.headers):
            self.send(403, json.dumps({"error": "Geen toegang tot dit dashboard."}), "application/json")
            return
        handler = {"/api/rules": change_rules, "/api/settings": change_settings, "/api/device": change_device,
                   "/api/home": home_action}.get(url.path)
        if not handler:
            self.send(404, json.dumps({"error": "Niet gevonden."}), "application/json")
            return
        if not ALLOW_BLOCKING:
            self.send(403, json.dumps({"error": "Wijzigingen staan uit in de instellingen van de app."}), "application/json")
            return
        if self.headers.get("X-Requested-With") != "dashboard":
            self.send(400, json.dumps({"error": "Ongeldig verzoek."}), "application/json")
            return
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 200000)
            payload = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            self.send(200, json.dumps(handler(payload)), "application/json")
        except ValueError as e:
            self.send(400, json.dumps({"error": str(e)}), "application/json")
        except Exception as e:
            self.send(502, json.dumps({"error": describe_err(e)}), "application/json")

    def log_message(self, *args):
        pass


def describe_err(e):
    if isinstance(e, agh_home.HomeError):
        return str(e)
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return "AdGuard DNS staat deze wijziging niet toe. Controleer of je API-key schrijfrechten heeft."
        return f"AdGuard DNS gaf een fout: HTTP {e.code}."
    if isinstance(e, urllib.error.URLError):
        return f"AdGuard DNS is niet bereikbaar ({e.reason})."
    return f"{type(e).__name__}: {e}"


PAGE = r"""<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AdGuard DNS</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#262c36;--card:#343c48;--card2:#2c333e;--line:#424b58;--line2:#3b4350;
  --text:#ffffff;--soft:#d5dae2;--muted:#9ca6b4;--faint:#6f7a89;
  --blue:#6d9df5;--red:#f26b4f;--green:#38c793;--purple:#c47cf2;--orange:#f2a43a;--link:#82a8f8;
  --cat-ads:#f0574a;--cat-trk:#35b98a;--cat-soc:#4d8df0;--cat-cdn:#9a62e8;--cat-oth:#8d97a5;
  --sans:"Inter",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;color-scheme:dark;scrollbar-color:#4a5361 transparent}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 var(--sans);font-variant-numeric:tabular-nums}
a{color:var(--link)} button,select,input{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--blue);outline-offset:2px;border-radius:6px}
.wrap{max-width:1180px;margin:0 auto;padding:18px 16px 48px}

/* navigatie */
.nav{display:flex;gap:4px;margin:0 0 22px;border-bottom:1px solid var(--line2);overflow-x:auto;overflow-y:hidden;scrollbar-width:none}
.nav::-webkit-scrollbar{display:none}
.nav a{padding:10px 14px;color:var(--muted);text-decoration:none;font-weight:500;border-bottom:2px solid transparent;margin-bottom:-1px;white-space:nowrap}
.nav a[aria-current="page"]{color:var(--text);border-color:var(--blue)}
.note{background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:12px 16px;margin:0 0 18px;font-size:14px;color:var(--soft)}
.note.err{border-color:var(--red)}
.note[hidden]{display:none}

/* limieten */
body.home .c-co{display:none}
.muted{color:var(--soft)} .qtable .rule{font-size:12px;margin-top:3px;font-family:ui-monospace,monospace;overflow-wrap:anywhere}
.limits{display:grid;grid-template-columns:repeat(3,minmax(0,1fr)) auto;gap:18px 26px;align-items:center;margin-bottom:16px}
.lim{display:grid;grid-template-columns:44px 1fr;column-gap:12px;align-items:center}
.lim svg{color:#c6ccd6}
.lim .t{font-size:14px;color:var(--soft)} .lim .v{font-size:14px;font-weight:700}
.lim .v span{font-weight:400;color:var(--soft)}
.bar{grid-column:1 / -1;height:3px;background:#1d222a;border-radius:2px;margin-top:10px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--blue);min-width:4px}
.plan{font-size:14px;color:var(--soft)} .plan b{color:var(--text);font-weight:700}

/* kaarten */
.card{background:var(--card);border-radius:24px;padding:22px 24px;margin-bottom:22px}
.card h2{font-size:22px;font-weight:700;margin:0 0 16px;display:flex;align-items:center;gap:10px}
.help{width:20px;height:20px;border-radius:50%;border:1.5px solid var(--blue);color:var(--blue);font-size:12px;font-weight:700;display:inline-grid;place-items:center;flex:none}
.cardhead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.cardhead h2{margin-bottom:16px}
.cardhead .right{color:var(--soft);font-size:15px;padding-top:4px}
.req{color:var(--blue)} .blk{color:var(--red)} .cmp{color:var(--purple)}
a.req,a.blk,a.cmp{text-decoration:underline;text-underline-offset:2px}
.linkbtn{background:none;border:0;padding:0;color:var(--link);text-decoration:underline;text-underline-offset:2px;cursor:pointer;font-size:14px}

/* tabs met kerncijfer */
.kpitabs{display:flex;gap:34px;margin-bottom:10px;flex-wrap:wrap}
.kpitab{background:none;border:0;padding:0 0 12px;text-align:left;cursor:pointer;border-bottom:2px solid transparent;min-width:200px}
.kpitab[aria-selected="true"]{border-color:var(--blue)}
.kpitab .t{font-size:16px;color:var(--soft);display:block}
.kpitab[aria-selected="true"] .t{color:var(--text)}
.kpitab .n{font-size:20px;font-weight:700;display:block;margin-top:8px}
.kpitab .d{font-size:13px;color:var(--soft);display:block;margin-top:2px}
.up{color:var(--green)} .down{color:var(--red)}
.viewall{float:right;font-size:14px}

/* grafiek */
.chart{position:relative;height:150px;margin-top:8px}
.chart svg{display:block;width:100%;height:100%;overflow:visible}
.chart text{fill:var(--muted);font-size:11px;font-family:var(--sans)}
.ctip{position:absolute;pointer-events:none;background:#1f252e;border:1px solid var(--line);border-radius:10px;padding:8px 12px;font-size:13px;white-space:nowrap;transform:translate(-50%,-100%);z-index:2}
.ctip[hidden]{display:none}

/* DNS-servers */
.sect{display:flex;align-items:center;gap:10px;font-size:16px;color:var(--soft);margin:6px 0 14px}
.srv{padding-bottom:0;overflow:hidden}
.srv h2{font-size:26px}
.srvrow{display:flex;align-items:center;gap:22px;flex-wrap:wrap;margin-bottom:18px}
.toggle{display:inline-flex;align-items:center;gap:12px;font-size:15px}
.sw{width:42px;height:24px;border-radius:12px;background:#59626f;position:relative;flex:none}
.sw::after{content:"";position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:left .15s}
.sw.on{background:var(--blue)} .sw.on::after{left:21px}
.srvrow a,.srvrow .linkbtn{color:var(--text);font-size:14px}
.search{width:100%;background:#262c36;border:1px solid var(--line);border-radius:12px;padding:12px 16px;color:var(--text);margin-bottom:18px}
.search::placeholder{color:var(--muted)}
.dtable{width:100%;border-collapse:collapse;font-size:14px}
.dtable th{text-align:left;font-size:12px;font-weight:700;color:var(--soft);letter-spacing:.03em;text-transform:uppercase;padding:0 10px 12px}
.dtable td{padding:12px 10px;white-space:nowrap}
.dtable .dn{display:flex;align-items:center;gap:14px}
.dtable .dn svg{color:#c6ccd6;flex:none}
.dtable .act{text-align:right}
.dtable .act .linkbtn,.dtable .act a{margin-left:18px}
.srvfoot{margin:6px -24px 0;padding:16px 24px;background:var(--blue);color:#fff;font-weight:500;display:flex;align-items:center;gap:10px;text-decoration:none}

/* statistieken */
.ptitle{font-size:34px;font-weight:700;margin:4px 0 16px;display:flex;justify-content:space-between;align-items:center}
.refresh{width:40px;height:40px;border-radius:10px;border:0;background:var(--blue);color:#fff;display:grid;place-items:center;cursor:pointer}
.filters{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:22px}
.filters select{appearance:none;-webkit-appearance:none;background:#262c36 url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%23c6ccd6' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E") no-repeat right 14px center;
  border:1px solid var(--line);border-radius:10px;padding:9px 40px 9px 16px;width:100%;cursor:pointer;color:var(--text)}
.grid2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:22px}
.grid2 .card{margin-bottom:0}
.grid2 + .grid2,.grid2 + .card,.card + .grid2{margin-top:22px}
.list{width:100%;border-collapse:collapse;font-size:15px}
.list td{padding:9px 0;white-space:nowrap}
.list td.nm{width:100%;white-space:normal}
.list td.nm > span{display:flex;align-items:center;gap:14px}
.list td.n{text-align:right;padding-left:28px}
.list td.p{text-align:right;padding-left:36px;color:var(--soft)}
.ico{width:22px;height:22px;flex:none;border-radius:4px;display:grid;place-items:center;overflow:hidden}
.ico img{width:20px;height:20px;object-fit:contain}
.ico.letter{background:#5a6472;color:#fff;font-size:11px;font-weight:700}
.flag{width:24px;height:24px;border-radius:50%;object-fit:cover;flex:none;background:#4a5361}
.flag.na{display:grid;place-items:center;color:var(--soft);font-size:12px;background:none}
.empty{color:var(--muted);padding:12px 0;font-size:14px}

/* kaart */
.mapcard{padding:18px 24px}
.map{position:relative}
.map svg{display:block;width:100%;height:auto}
.map .land{fill:#5f6d80;stroke:var(--card);stroke-width:.6}
.map .arc{fill:none;stroke:#7f9cd6;stroke-opacity:.45;stroke-width:1.6}
.map .dot{fill:var(--blue);stroke:#fff;stroke-width:2.5}
.zoom{position:absolute;right:6px;top:42%;display:grid;gap:10px}
.zoom button{width:34px;height:34px;border-radius:50%;border:1.5px solid var(--blue);background:none;color:var(--blue);font-size:22px;line-height:1;cursor:pointer}

/* bedrijven */
.ctable{width:100%;border-collapse:collapse;font-size:14px}
.ctable th{text-align:left;font-size:12px;font-weight:700;color:var(--soft);text-transform:uppercase;letter-spacing:.03em;padding:6px 12px 14px;white-space:nowrap}
.ctable td{padding:12px;border-top:1px solid var(--line2);white-space:nowrap}
.ctable td.co > span{display:flex;align-items:center;gap:12px}
.ctable .dom{max-width:240px;overflow:hidden;text-overflow:ellipsis;text-decoration:underline;text-underline-offset:2px}
.sortsel{background:none;border:0;color:var(--soft);font-size:14px;cursor:pointer}
.topco h3{margin:0 0 6px;font-size:22px}
.topco .who{display:block;margin-bottom:12px}
.tablewrap{overflow-x:auto;margin:0 -8px;padding:0 8px}

/* querylog */
.logtools{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.logtools .search{margin:0;flex:1;min-width:200px}
.logtools select{background:#262c36;border:1px solid var(--line);border-radius:12px;padding:0 14px;color:var(--text)}
.ltable{width:100%;border-collapse:collapse;font-size:14px}
.ltable th{text-align:left;font-size:12px;font-weight:700;color:var(--soft);text-transform:uppercase;letter-spacing:.03em;padding:6px 10px 12px;white-space:nowrap}
.ltable td{padding:10px;border-top:1px solid var(--line2);white-space:nowrap}
.ltable td.dm{white-space:normal;word-break:break-all}
.st{font-weight:600;font-size:13px}
.st.b{color:var(--red)} .st.a{color:var(--green)}
.more{margin-top:14px;background:none;border:1px solid var(--line);border-radius:10px;padding:8px 16px;cursor:pointer}

/* tooltip, toast, venster */
.help{cursor:help}
.tip{position:absolute;z-index:20;max-width:300px;background:#343c48;border:1px solid var(--line);border-radius:12px;padding:12px 14px;font-size:14px;line-height:1.45;color:var(--soft);box-shadow:0 8px 24px rgb(0 0 0 / .35)}
.tip[hidden],.toast[hidden]{display:none}
.toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:40;background:#343c48;border:1px solid var(--line);border-left:4px solid var(--green);border-radius:12px;padding:12px 18px;font-size:14px;box-shadow:0 8px 24px rgb(0 0 0 / .35);max-width:calc(100vw - 32px)}
.toast.err{border-left-color:var(--red)}
.modal{position:fixed;inset:0;z-index:30;background:rgb(10 14 20 / .6);display:grid;place-items:center;padding:16px}
.modal .box{background:var(--card);border-radius:20px;padding:22px 24px;width:min(560px,100%);max-height:calc(100vh - 32px);overflow:auto}
.modal h3{margin:0 0 10px;font-size:20px}
.modal p{margin:0 0 16px;color:var(--soft)}
.modal .btns{display:flex;justify-content:flex-end;gap:10px;margin-top:18px}
.btn{border:0;border-radius:10px;padding:9px 16px;cursor:pointer;font-weight:600;background:#4a5361;color:#fff}
.btn.pri{background:var(--blue)} .btn.dng{background:var(--red)}
.btn[disabled]{opacity:.6;cursor:default}
.rules{list-style:none;margin:0;padding:0;max-height:46vh;overflow:auto;border:1px solid var(--line2);border-radius:12px}
.rules li{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:8px 12px;border-top:1px solid var(--line2);font-family:ui-monospace,Consolas,monospace;font-size:13px;word-break:break-all}
.rules li:first-child{border-top:0}
.rules button{background:none;border:0;color:var(--muted);cursor:pointer;font-size:18px;line-height:1;padding:2px 6px}
.rules button:hover{color:var(--red)}
.addrule{display:flex;gap:8px;margin-top:12px}
.addrule input{flex:1;background:#262c36;border:1px solid var(--line);border-radius:10px;padding:9px 12px;color:var(--text);font-family:ui-monospace,Consolas,monospace;font-size:13px}
[data-go]{cursor:pointer}
button[data-go]{background:none;border:0;padding:0;font:inherit;color:inherit;text-align:left}
.list button[data-go].nmlink:hover,.ctable button[data-go].nmlink:hover{text-decoration:underline}

/* querylog zoals AdGuard */
.lqbar{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:18px;font-size:15px}
.lqbar .sep{width:1px;height:24px;background:var(--line)}
.lqbar select{background:none;border:0;color:var(--text);cursor:pointer;font-size:15px}
.lqbar .plain{color:var(--link);text-decoration:none;font-size:15px}
.lqbar .muted{color:var(--soft)}
.lqbar .push{margin-left:auto}
.sw{cursor:pointer;border:0;padding:0}
.lfilters{display:grid;gap:14px;margin-bottom:20px}
.lfilters .r1{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.lfilters .r2{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px}
.lfilters select{appearance:none;-webkit-appearance:none;background:#262c36 url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%23c6ccd6' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E") no-repeat right 14px center;
  border:1px solid var(--line);border-radius:10px;padding:9px 40px 9px 16px;width:100%;cursor:pointer;color:var(--text)}
.sbox{position:relative}
.sbox svg{position:absolute;left:14px;top:50%;transform:translateY(-50%);color:var(--soft)}
.sbox input{width:100%;background:#262c36;border:1px solid var(--line);border-radius:10px;padding:9px 14px 9px 46px;color:var(--text)}
.qtable{width:100%;border-collapse:collapse;font-size:14px}
.qtable th{text-align:left;font-size:14px;font-weight:600;color:var(--soft);padding:8px 12px 14px;white-space:nowrap}
.qtable td{padding:10px 12px;white-space:nowrap;vertical-align:middle}
.qtable tr:hover td{background:#2b323c}
.pill{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.03em;padding:6px 12px;border-radius:999px;text-transform:uppercase}
.pill.b{background:#5a3438;color:#f26b4f} .pill.p{background:#3e4653;color:#c9cfd8} .pill.a{background:#24473f;color:#38c793} .pill.m{background:#47395c;color:#c47cf2}
.rq{display:flex;align-items:center;gap:10px;max-width:420px}
.rq .dom{overflow:hidden;text-overflow:ellipsis}
.rq svg.ico{width:16px;height:16px}
.lock{color:#c6ccd6;flex:none}
.dt{line-height:1.35;font-weight:500}
.cl{display:flex;align-items:center;gap:12px}
.cl .q{width:20px;height:20px;border-radius:50%;border:1.5px solid var(--blue);color:var(--blue);font-size:12px;font-weight:700;display:grid;place-items:center;flex:none}
.cl img.flag{width:20px;height:20px}
.kebab{position:relative}
.kebab > button{background:none;border:0;color:var(--soft);cursor:pointer;font-size:20px;line-height:1;padding:4px 8px;border-radius:8px}
.kebab > button:hover{background:#3b4350}
.menu{position:absolute;right:0;top:100%;z-index:10;background:#343c48;border:1px solid var(--line);border-radius:10px;padding:6px;min-width:190px;box-shadow:0 8px 24px rgb(0 0 0 / .35)}
.menu button{display:block;width:100%;text-align:left;background:none;border:0;padding:8px 12px;border-radius:6px;cursor:pointer;color:var(--link);font-size:14px}
.menu button:hover{background:#3b4350}
.menu[hidden]{display:none}
.co-name{max-width:150px;overflow:hidden;text-overflow:ellipsis}

/* instellingenpagina's */
.back{display:inline-flex;align-items:center;gap:6px;color:var(--link);text-decoration:none;font-size:14px;margin-bottom:6px}
.psub{color:var(--soft);margin:-8px 0 20px}
.setcard h2{font-size:19px;margin-bottom:6px}
.setrow{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:14px 0;border-top:1px solid var(--line2)}
.setcard h2 + .setrow{border-top:0}
.setrow .sl{font-weight:500}
.setrow .sh{font-size:13px;color:var(--muted);margin-top:2px;max-width:560px}
.setrow select,.setrow input[type=number]{background:#262c36;border:1px solid var(--line);border-radius:10px;padding:8px 12px;color:var(--text)}
.setrow input[type=number]{width:96px}
.sw[disabled]{opacity:.5;cursor:not-allowed}
.sw.busy{opacity:.6}
.flrow .meta{font-size:13px;color:var(--muted);margin-top:2px}
.fltools{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:6px}
.fltools .search{margin:0;flex:1;min-width:200px}
.copyrow{display:grid;grid-template-columns:150px minmax(0,1fr) auto;gap:12px;align-items:center;padding:12px 0;border-top:1px solid var(--line2)}
.copyrow code{font-family:ui-monospace,Consolas,monospace;font-size:13px;background:#262c36;border:1px solid var(--line2);border-radius:8px;padding:7px 10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.copyrow .lbl{color:var(--soft);font-size:14px}
.ro{font-size:13px;color:var(--muted);margin:0 0 14px}

/* AdGuard Home */
.hhead{display:flex;justify-content:space-between;align-items:center;gap:14px 24px;flex-wrap:wrap;margin-bottom:18px}
.hhead .ptitle{margin:0}
.ptitle .ver{font-size:15px;font-weight:500;color:var(--muted);margin-left:10px}
.hctl{display:flex;align-items:center;gap:18px;flex-wrap:wrap;font-size:14px}
.pause,.rowctl select{background:#262c36;border:1px solid var(--line);border-radius:10px;padding:6px 10px;color:var(--text);font-size:14px}
.pause[hidden]{display:none}
.tiles4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px;margin-bottom:22px}
button.htile[data-go]{position:relative;display:flex;flex-direction:column;background:var(--card);border:0;border-radius:20px;padding:18px 20px 14px;text-align:left;cursor:pointer;color:var(--text);min-height:150px}
button.htile[data-go]:hover{background:#3a4350}
.htile .tn{font-size:28px;font-weight:700;line-height:1.1}
.htile .tp{position:absolute;right:18px;top:20px;font-size:14px;font-weight:600;color:var(--c)}
.htile .spark{width:100%;height:46px;margin:12px 0 8px;display:block}
.htile .tl{font-size:14px;color:var(--c);margin-top:auto}
.csub{color:var(--muted);font-size:14px;margin:-10px 0 14px}
.setcard .csub{margin:0 0 12px}
.list td.pc{width:150px;padding-left:20px}
.lscroll{max-height:372px;overflow-y:auto;margin-right:-10px;padding-right:10px;scrollbar-width:thin;scrollbar-color:#4a5361 transparent}
.lscroll::-webkit-scrollbar{width:6px} .lscroll::-webkit-scrollbar-thumb{background:#4a5361;border-radius:3px}
.pc{white-space:nowrap}
.pbar{display:inline-block;vertical-align:middle;width:84px;height:5px;background:#262c36;border-radius:3px;overflow:hidden;margin-right:8px}
.pbar i{display:block;height:100%;border-radius:3px}
.pv{font-size:13px;color:var(--soft);display:inline-block;min-width:44px;text-align:right}
.dname{overflow-wrap:anywhere}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:13px}
.subnav{display:flex;gap:8px;flex-wrap:wrap;margin:-4px 0 20px}
.subnav a{padding:7px 14px;border-radius:999px;background:var(--card);color:var(--soft);text-decoration:none;font-size:14px;font-weight:500}
.subnav a[aria-current="page"]{background:var(--blue);color:#fff}
.btnrow{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.btnrow.end{justify-content:flex-end;margin-top:14px}
.btnrow.end .muted{margin-right:auto;font-size:14px}
.rowctl{display:flex;align-items:center;gap:14px;flex-wrap:wrap;justify-content:flex-end}
.grow{min-width:0}
.flrow .sh.mono{overflow-wrap:anywhere}
.xbtn{background:none;border:0;color:var(--muted);cursor:pointer;font-size:20px;line-height:1;padding:2px 6px;border-radius:6px}
.xbtn:hover{color:var(--red);background:#3b4350}
.xbtn[disabled]{opacity:.4;cursor:not-allowed}
textarea.code{width:100%;background:#262c36;border:1px solid var(--line);border-radius:12px;padding:12px 14px;color:var(--text);font:13px/1.55 ui-monospace,Consolas,monospace;resize:vertical}
.inform{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.inform input{flex:1;min-width:160px;background:#262c36;border:1px solid var(--line);border-radius:10px;padding:9px 12px;color:var(--text)}
.rtable td.mono{white-space:normal;overflow-wrap:anywhere}
.dtable td.wrap{white-space:normal}
.act .linkbtn[data-go]{color:var(--link);text-decoration:underline}
.setrow input[data-k=cache_size]{width:140px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.chip{background:#262c36;border:1px solid var(--line);border-radius:999px;padding:6px 12px;cursor:pointer;font-size:13px;color:var(--soft)}
.chip[aria-pressed="true"]{background:var(--blue);border-color:var(--blue);color:#fff}
.svcgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.svc{display:flex;align-items:center;gap:12px;background:#2c333e;border-radius:12px;padding:10px 12px}
.svc .si{width:24px;height:24px;flex:none;display:grid;place-items:center}
.svc .si img{width:22px;height:22px;filter:invert(1);opacity:.85}
.svc .sn{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.checkres{margin-top:4px;padding:14px 16px;background:#2c333e;border-radius:12px}
.checkres p{margin:8px 0;color:var(--soft)}
.checkres code{font:12px ui-monospace,Consolas,monospace;background:#262c36;border-radius:6px;padding:3px 7px}
.engines{display:flex;gap:10px 22px;flex-wrap:wrap;padding:0 0 14px}
.engines[hidden],.own[hidden]{display:none}
.chk{display:flex;align-items:center;gap:8px;font-size:14px;cursor:pointer}
.chk input{width:16px;height:16px;accent-color:var(--blue)}
h3.sub{font-size:16px;margin:18px 0 4px}
.tres{list-style:none;padding:0;margin:14px 0 0;border:1px solid var(--line2);border-radius:12px}
.tres li{display:flex;justify-content:space-between;gap:12px;padding:8px 12px;border-top:1px solid var(--line2);font-size:14px}
.tres li:first-child{border-top:0}
.tres .ok{color:var(--green)} .tres .bad{color:var(--red);overflow-wrap:anywhere;text-align:right}
.mform{display:grid;gap:14px}
.mform label:not(.chk){display:grid;gap:6px;font-size:14px;color:var(--soft)}
.mform input:not([type=checkbox]),.mform select{background:#262c36;border:1px solid var(--line);border-radius:10px;padding:9px 12px;color:var(--text)}
.mform .hint{font-size:12px;color:var(--muted)}
.mform .own{display:grid;gap:10px;padding-left:24px}
.mform .btns{margin-top:4px}
@media (max-width:900px){
  .tiles4{grid-template-columns:repeat(2,minmax(0,1fr))}
  .lfilters .r1{grid-template-columns:repeat(2,minmax(0,1fr))}
  .lfilters .r2{grid-template-columns:repeat(2,minmax(0,1fr))}
  .limits{grid-template-columns:repeat(3,minmax(0,1fr))}
  .plan{grid-column:1 / -1}
  .grid2{grid-template-columns:minmax(0,1fr)}
  .filters{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media (max-width:640px){
  .tiles4{gap:10px} .htile{padding:14px 14px 10px;min-height:120px} .htile .tn{font-size:22px} .htile .tp{right:12px;top:16px}
  .list td.pc{width:auto;padding-left:12px} .pbar{width:44px} .pv{min-width:38px}
  .svcgrid{grid-template-columns:minmax(0,1fr)} .rowctl{gap:8px}
  .wrap{padding:12px 10px 36px}
  .card{padding:18px 16px;border-radius:20px}
  .limits{grid-template-columns:minmax(0,1fr);gap:14px}
  .lim{grid-template-columns:34px 1fr}
  .kpitabs{gap:18px}
  .kpitab{min-width:0;flex:1}
  .kpitab .n{font-size:17px}
  .viewall{float:none;display:inline-block;margin-bottom:6px}
  .srv h2{font-size:21px}
  .srvfoot{margin:6px -16px 0;padding:14px 16px}
  .dtable thead{display:none}
  .dtable tr{display:grid;grid-template-columns:repeat(3,auto);gap:6px 12px;padding:12px 0;border-top:1px solid var(--line2)}
  .dtable td{padding:0}
  .dtable td.dn{grid-column:1 / -1}
  .dtable td.act{grid-column:1 / -1;text-align:left}
  .dtable td.act .linkbtn,.dtable td.act a{margin:0 18px 0 0}
  .dtable td[data-l]::before{content:attr(data-l);display:block;font-size:11px;color:var(--muted);text-transform:uppercase}
  .ptitle{font-size:27px}
  .filters{grid-template-columns:minmax(0,1fr);gap:10px}
  .list{font-size:14px}
  .list td.n{padding-left:14px} .list td.p{padding-left:14px}
  .list td.nm > span{gap:10px}
  .zoom{display:none}
  .copyrow{grid-template-columns:minmax(0,1fr) auto}
  .copyrow .lbl{grid-column:1 / -1}
  .lfilters .r1,.lfilters .r2{grid-template-columns:minmax(0,1fr)}
  .lqbar{gap:10px}
  .lqbar .sep{display:none}
  .lqbar .push{margin-left:0}
  .qtable thead{display:none}
  .qtable tr{display:grid;grid-template-columns:1fr auto;gap:6px 10px;padding:12px 4px;border-top:1px solid var(--line2)}
  .qtable td{padding:0;white-space:normal}
  .qtable td.c-st{grid-column:1;grid-row:1}
  .qtable td.c-mn{grid-column:2;grid-row:1 / span 2;align-self:start}
  .qtable td.c-rq{grid-column:1 / -1}
  .qtable td.c-co,.qtable td.c-dv,.qtable td.c-dt,.qtable td.c-cl{grid-column:1 / -1;font-size:13px;color:var(--soft)}
  .qtable td.c-dt .dt{display:inline}
  .qtable td.c-dt .dt br{display:none}
  .rq{max-width:none}
}
</style>
</head>
<body>
<div class="wrap">
  <nav class="nav" aria-label="Onderdelen">
    <a href="#dashboard">Dashboard</a><a href="#statistieken">Statistieken</a><a href="#bedrijven">Bedrijven</a><a href="#querylog">Querylog</a>
  </nav>
  <p id="note" class="note" hidden></p>
  <main id="view"></main>
</div>
<div class="ctip" id="ctip" hidden></div>
<div class="tip" id="tip" role="tooltip" hidden></div>
<div class="toast" id="toast" role="status" hidden></div>

<script>
__I18N__
const CFG = __CONFIG__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dec1 = v => (Math.round(v * 10) / 10).toLocaleString(LOCALE, {maximumFractionDigits: 1});
const K = n => { n = Number(n) || 0; if (n >= 1e9) return dec1(n/1e9) + 'B'; if (n >= 1e6) return dec1(n/1e6) + 'M'; if (n >= 1e3) return dec1(n/1e3) + 'K'; return String(Math.round(n)); };
const pct = (a, b) => b ? Math.round(a / b * 100) : 0;
const RANGES = {'24h': ['Afgelopen 24 uur', 864e5], '7d': ['Afgelopen 7 dagen', 6048e5], '30d': ['Afgelopen 30 dagen', 2592e6]};
const CAT = {ADS:['Advertenties','ads'], TRACKERS:['Trackers','trk'], SOCIAL_MEDIA:['Sociale media','soc'], CDN:['CDN','cdn'], OTHERS:['Overig','oth']};
const regionNames = (() => { try { return new Intl.DisplayNames([LANG], {type: 'region'}); } catch (e) { return null; } })();
const countryName = c => !c || c.length !== 2 ? 'Niet van toepassing' : (regionNames ? regionNames.of(c.toUpperCase()) : c.toUpperCase());
const HOME = CFG.mode === 'home';
const ADG = HOME ? (CFG.home_url || '#') : 'https://adguard-dns.io/en/dashboard/';
const C = html => HOME ? '' : html;
if (HOME) { document.body.classList.add('home'); const bc = document.querySelector('.nav a[href="#bedrijven"]'); if (bc) bc.remove();
  document.querySelector('.nav').insertAdjacentHTML('beforeend', '<a href="#filters">Filters</a><a href="#instellingen">Instellingen</a>'); }

const state = {range: '24h', server: '', device: '', country: '', kpi: 'blocked', sort: 'blocked', search: '', logStatus: '', logSearch: ''};
let meta = {servers: [], devices: [], countries: [], companies: []}, timer = null, logTimer = null, mapData = null, mapZoom = 1;
Object.assign(state, {logCategory: '', logCompany: '', logType: '', logAuto: true, logInterval: 5, filtersHidden: matchMedia('(max-width:640px)').matches});

/* ---------------- doorklikken, tooltips, meldingen ---------------- */
const goAttr = (view, patch = {}) => `data-go="${esc(JSON.stringify({view, ...patch}))}"`;
document.addEventListener('click', e => {
  const t = e.target.closest('[data-go]'); if (!t) return;
  e.preventDefault();
  const {view, ...patch} = JSON.parse(t.dataset.go);
  if (view === 'querylog') Object.assign(state, {logStatus: '', logCategory: '', logCompany: '', logSearch: '', logType: ''});
  Object.assign(state, patch);
  if (location.hash === '#' + view) route(); else location.hash = '#' + view;
});
function showTip(el){
  const tip = $('#tip'); tip.textContent = el.dataset.tip; tip.hidden = false;
  const r = el.getBoundingClientRect(), w = Math.min(300, innerWidth - 24);
  tip.style.left = Math.max(12, Math.min(innerWidth - w - 12, r.left + scrollX - 20)) + 'px';
  tip.style.top = (r.bottom + scrollY + 10) + 'px';
}
document.addEventListener('mouseover', e => { const h = e.target.closest('[data-tip]'); if (h) showTip(h); });
document.addEventListener('mouseout', e => { if (e.target.closest('[data-tip]')) $('#tip').hidden = true; });
document.addEventListener('focusin', e => { const h = e.target.closest('[data-tip]'); if (h) showTip(h); });
document.addEventListener('focusout', () => $('#tip').hidden = true);
document.addEventListener('click', e => { const h = e.target.closest('[data-tip]'); if (h) { showTip(h); } else $('#tip').hidden = true;
  if (!e.target.closest('.kebab')) document.querySelectorAll('.menu').forEach(m => m.hidden = true); });
let toastT; function toast(msg, err){ const t = $('#toast'); t.textContent = msg; t.className = 'toast' + (err ? ' err' : ''); t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => t.hidden = true, 4500); }
function modal(html){
  const m = document.createElement('div'); m.className = 'modal'; m.innerHTML = `<div class="box" role="dialog" aria-modal="true">${html}</div>`;
  document.body.appendChild(m);
  const close = () => { m.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = e => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  m.addEventListener('click', e => { if (e.target === m || e.target.closest('[data-close]')) close(); });
  setTimeout(() => { const f = m.querySelector('[data-focus]') || m.querySelector('button'); f && f.focus(); }, 0);
  return {el: m, close};
}
async function post(path, body){
  const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'dashboard', 'X-Lang': LANG}, body: JSON.stringify(body)});
  const j = await r.json().catch(() => ({error: 'Onverwacht antwoord van de dashboardservice.'}));
  if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
function confirmRule(domain, action, serverId, ftype, after){
  const block = action === 'block', srv = meta.servers.find(s => s.id === serverId);
  const m = modal(`<h3>${block ? 'Domein blokkeren' : 'Domein deblokkeren'}</h3>
    <p>Verzoeken naar <b>${esc(domain)}</b> worden ${block ? 'geblokkeerd' : 'niet meer geblokkeerd'} voor alle apparaten op ${esc(srv ? srv.name : 'deze server')}. Dit wordt een gebruikersregel die je altijd weer kunt verwijderen.</p>
    <div class="btns"><button class="btn" data-close>Annuleren</button><button class="btn ${block ? 'dng' : 'pri'}" data-ok data-focus>${block ? 'Blokkeren' : 'Deblokkeren'}</button></div>`);
  m.el.querySelector('[data-ok]').addEventListener('click', async ev => {
    ev.target.disabled = true;
    try { const r = await post('api/rules', {server_id: serverId, domain, action, filtering_type: ftype}); m.close();
      toast(r.message + ' Het kan een minuut duren voordat het overal werkt.'); meta.servers = []; after && after(); }
    catch (e) { m.close(); toast(e.message, true); }
  });
}
async function rulesModal(serverId){
  const srv = meta.servers.find(s => s.id === serverId);
  const m = modal(`<h3>Gebruikersregels ${esc(srv ? srv.name : '')}</h3><p>Regels die je zelf hebt toegevoegd. <code>||domein^</code> blokkeert een domein, <code>@@||domein^</code> staat het toe.</p>
    <ul class="rules" id="rl"><li>Regels laden…</li></ul>
    ${CFG.blocking ? '<form class="addrule" id="ra"><input data-focus placeholder="||voorbeeld.nl^" aria-label="Nieuwe regel"><button class="btn pri">Regel toevoegen</button></form>' : ''}
    <div class="btns"><button class="btn" data-close>Sluiten</button></div>`);
  const draw = rules => { m.el.querySelector('#rl').innerHTML = rules.length ? rules.map(r => `<li><span>${esc(r)}</span>${CFG.blocking ? `<button data-del="${esc(r)}" aria-label="Regel verwijderen">×</button>` : ''}</li>`).join('') : '<li>Er zijn nog geen gebruikersregels.</li>'; };
  try { const r = await fetch('api/rules?server_id=' + encodeURIComponent(serverId)).then(x => x.json()); if (r.error) throw new Error(r.error); draw(r.rules || []); }
  catch (e) { m.el.querySelector('#rl').innerHTML = `<li>${esc(e.message)}</li>`; }
  m.el.addEventListener('click', async e => { const d = e.target.closest('[data-del]'); if (!d) return;
    try { const r = await post('api/rules', {server_id: serverId, remove: [d.dataset.del]}); draw(r.rules); toast('Regel verwijderd.'); meta.servers = []; } catch (er) { toast(er.message, true); } });
  const f = m.el.querySelector('#ra');
  if (f) f.addEventListener('submit', async e => { e.preventDefault(); const inp = f.querySelector('input'), v = inp.value.trim(); if (!v) return;
    try { const r = await post('api/rules', {server_id: serverId, add: [v]}); draw(r.rules); inp.value = ''; toast('Regel toegevoegd.'); meta.servers = []; } catch (er) { toast(er.message, true); } });
}
const companyId = name => { name = String(name || '').toLowerCase(); const map = {'meta platforms, inc.': 'facebook', 'microsoft corporation': 'microsoft', 'apple inc.': 'apple'};
  return map[name] || name.replace(/\.(com|net|io|org)$/, '').split(/[\s,]+/)[0].replace(/[^a-z0-9]/g, ''); };
function companyName(id){
  if (!id) return '–';
  const hit = meta.companies.find(c => companyId(c) === id);
  return hit || id.replace(/[_-]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

/* ---------------- API ---------------- */
async function api(path, params = {}){
  const qs = new URLSearchParams([['p', path]]);
  for (const [k, v] of Object.entries(params)) {
    if (v == null || v === '') continue;
    (Array.isArray(v) ? v : [v]).forEach(x => qs.append(k, x));
  }
  const r = await fetch('api/proxy?' + qs);
  const j = await r.json().catch(() => ({error: 'Onverwacht antwoord van de dashboardservice.'}));
  if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
function period(prev){
  const len = RANGES[state.range][1], to = Date.now() - (prev ? len : 0);
  return {time_from_millis: Math.round(to - len), time_to_millis: Math.round(to)};
}
function filt(){ return {dns_servers: state.server || null, devices: state.device || null, countries: state.country || null}; }
async function loadMeta(){
  const [s, d] = await Promise.all([api('v2/dns_servers', {limit: 100}), api('v2/devices', {limit: 1000})]);
  meta.servers = (s.items || []).map(x => ({...x, name: x.name || 'Naamloze server'}));
  meta.devices = (d.items || []).map(x => ({...x, name: x.name || 'Naamloos apparaat'}));
}
function showError(e){ const n = $('#note'); n.hidden = false; n.className = 'note err'; n.textContent = e.message || String(e); }
function showDemo(){ const n = $('#note'); if (CFG.demo) { n.hidden = false; n.className = 'note';
  n.textContent = CFG.want === 'home' ? 'Je bekijkt voorbeeldgegevens. Vul het adres van je AdGuard Home en je gebruikersnaam en wachtwoord in op het tabblad Configuratie van de app om je eigen statistieken te zien.'
    : 'Je bekijkt voorbeeldgegevens. Vul je AdGuard DNS API-key in op het tabblad Configuratie van de app om je eigen statistieken te zien.'; } else n.hidden = true; }

/* ---------------- iconen ---------------- */
const SVG = {
  cloud:'<svg width="38" height="38" viewBox="0 0 40 40" fill="currentColor"><circle cx="20" cy="20" r="19" fill="#4a5361"/><path d="M13 25h14a4 4 0 0 0 .6-8 6 6 0 0 0-11.5-1.5A4.8 4.8 0 0 0 13 25z" fill="#d7dce4"/></svg>',
  device:'<svg width="38" height="38" viewBox="0 0 40 40"><rect x="4" y="8" width="32" height="21" rx="2" fill="#4a5361"/><rect x="13" y="31" width="14" height="3" rx="1.5" fill="#4a5361"/></svg>',
  server:'<svg width="38" height="38" viewBox="0 0 40 40"><rect x="5" y="7" width="30" height="11" rx="3" fill="#4a5361"/><rect x="5" y="22" width="30" height="11" rx="3" fill="#4a5361"/><circle cx="11" cy="12.5" r="1.6" fill="#d7dce4"/><circle cx="11" cy="27.5" r="1.6" fill="#d7dce4"/><path d="M17 12.5h12M17 27.5h12" stroke="#d7dce4" stroke-width="1.6"/></svg>',
  router:'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 15c0-1.7 1.3-3 3-3h12c1.7 0 3 1.3 3 3s-1.3 3-3 3H6c-1.7 0-3-1.3-3-3z"/><path d="M8 9c2.3-2 5.7-2 8 0"/></svg>',
  phone:'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/></svg>',
  pc:'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="5" width="18" height="12" rx="1.5"/><path d="M9 20h6"/></svg>',
  tv:'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="5" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 17v3"/></svg>',
  game:'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M7 8h10a4 4 0 0 1 4 4v1a3 3 0 0 1-5.2 2L14 13h-4l-1.8 2A3 3 0 0 1 3 13v-1a4 4 0 0 1 4-4z"/><path d="M8 11v3M6.5 12.5h3"/></svg>',
  refresh:'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 4v5h-5"/></svg>',
  plus:'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M12 8v8M8 12h8"/></svg>',
};
const CATICON = {
  ads:'<svg class="ico" viewBox="0 0 22 22"><rect x="2" y="2" width="18" height="18" rx="2" fill="var(--cat-ads)"/><path d="M4 18L18 4" stroke="var(--card)" stroke-width="2.5"/></svg>',
  trk:'<svg class="ico" viewBox="0 0 22 22"><path d="M1.5 11C4 6.5 7.3 5 11 5s7 1.5 9.5 6c-2.5 4.5-5.8 6-9.5 6s-7-1.5-9.5-6z" fill="var(--cat-trk)"/><circle cx="11" cy="11" r="2.6" fill="var(--card)"/></svg>',
  soc:'<svg class="ico" viewBox="0 0 22 22"><path d="M11 3c4.7 0 8 3.2 8 7.4S15.7 18 11 18c-1 0-2-.2-2.8-.5L4 19.5l1.3-3.7C3.9 14.4 3 12.5 3 10.4 3 6.2 6.3 3 11 3z" fill="var(--cat-soc)"/></svg>',
  cdn:'<svg class="ico" viewBox="0 0 22 22"><circle cx="6" cy="16" r="3.5" fill="var(--cat-cdn)"/><circle cx="16" cy="6" r="3.5" fill="var(--cat-cdn)"/><path d="M8 14l6-6" stroke="var(--cat-cdn)" stroke-width="2"/></svg>',
  oth:'<svg class="ico" viewBox="0 0 22 22"><circle cx="11" cy="11" r="8" fill="var(--cat-oth)"/><circle cx="11" cy="11" r="2.5" fill="var(--card)"/></svg>',
};
const devIcon = t => ({ROUTER: SVG.router, IOS: SVG.phone, ANDROID: SVG.phone, SMART_TV: SVG.tv, GAME_CONSOLE: SVG.game}[t] || SVG.pc);
function favicon(domain, name){
  const letter = esc((name || domain || '?').trim().charAt(0).toUpperCase());
  if (!domain) return `<span class="ico letter">${letter}</span>`;
  const host = String(domain).split('.').slice(-2).join('.');
  return `<span class="ico"><img src="https://icons.duckduckgo.com/ip3/${encodeURIComponent(host)}.ico" alt="" loading="lazy" onerror="this.parentNode.className='ico letter';this.parentNode.textContent='${letter}'"></span>`;
}
/* Publieke IP-adressen (je internetadres) tonen we niet; interne adressen wel. */
function isPublicIp(ip){
  ip = String(ip).trim().toLowerCase();
  const v4 = ip.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (v4) { const [a, b] = [+v4[1], +v4[2]];
    return !(a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254)); }
  if (ip.includes(':')) return !(ip === '::1' || /^f[cd]/.test(ip) || /^fe[89ab]/.test(ip));
  return false;
}
const flag = c => !c || c.length !== 2 ? '<span class="flag na">?</span>' : `<img class="flag" src="https://flagcdn.com/w40/${esc(c.toLowerCase())}.png" alt="" loading="lazy" onerror="this.outerHTML='<span class=&quot;flag na&quot;>${esc(c.toUpperCase())}</span>'">`;

/* ---------------- grafiek ---------------- */
function barChart(el, stats, mode){
  const W = Math.max(280, el.clientWidth), H = el.clientHeight || 150, L = 46, B = 22, T = 6;
  const vals = stats.map(s => mode === 'companies' ? (s.value.companies || 0) : (s.value.queries || 0));
  const max = Math.max(1, ...vals);
  const nice = x => { const e = Math.pow(10, Math.floor(Math.log10(x))), f = x / e; return (f <= 1.5 ? 1.5 : f <= 2.1 ? 2.1 : f <= 3 ? 3 : f <= 6.4 ? 6.4 : 10) * e; };
  const top = nice(max), n = stats.length, gw = (W - L) / Math.max(1, n), bw = Math.max(3, Math.min(30, gw * 0.62));
  const Y = v => T + (1 - v / top) * (H - T - B);
  let g = '';
  [0, 1/3, 2/3, 1].forEach(f => { g += `<text x="${L - 12}" y="${Y(top * f) + 4}" text-anchor="end">${K(top * f)}</text>`; });
  const daily = n > 1 && (stats[1].time_millis - stats[0].time_millis) >= 864e5;
  const every = Math.max(1, Math.round(n / 5));
  stats.forEach((s, i) => {
    const x = L + gw * i + (gw - bw) / 2, q = s.value.queries || 0, b = s.value.blocked || 0;
    if (mode === 'companies') {
      const c = s.value.companies || 0;
      g += `<rect x="${x}" y="${Y(c)}" width="${bw}" height="${Math.max(0, Y(0) - Y(c))}" rx="2" fill="var(--purple)"/>`;
    } else {
      g += `<rect x="${x}" y="${Y(q)}" width="${bw}" height="${Math.max(0, Y(0) - Y(q))}" rx="2" fill="var(--blue)"/>`;
      g += `<rect x="${x}" y="${Y(b)}" width="${bw}" height="${Math.max(0, Y(0) - Y(b))}" fill="var(--red)"/>`;
    }
    g += `<rect x="${L + gw * i}" y="0" width="${gw}" height="${H - B}" fill="transparent" data-i="${i}"/>`;
    if (i % every === 0) {
      const d = new Date(s.time_millis - (daily ? 0 : 36e5));
      const lab = daily ? d.toLocaleDateString(LOCALE, {day: 'numeric', month: 'short'}) : d.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'});
      g += `<text x="${x}" y="${H - 4}">${lab}</text>`;
    }
  });
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Verzoeken per periode">${g}</svg>`;
  const tip = $('#ctip');
  el.querySelectorAll('rect[data-i]').forEach(r => {
    r.addEventListener('mouseenter', e => {
      const s = stats[+r.dataset.i], d = new Date(s.time_millis), rb = r.getBoundingClientRect();
      const when = daily ? d.toLocaleDateString(LOCALE, {weekday: 'short', day: 'numeric', month: 'short'})
        : new Date(s.time_millis - 36e5).toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'}) + ' – ' + d.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'});
      tip.innerHTML = `<b>${when}</b><br><span class="req">${K(s.value.queries)} verzoeken</span><br><span class="blk">${K(s.value.blocked)} geblokkeerd (${pct(s.value.blocked, s.value.queries)}%)</span><br><span class="cmp">${s.value.companies || 0} bedrijven</span>`;
      tip.hidden = false; tip.style.left = (rb.left + rb.width / 2 + scrollX) + 'px'; tip.style.top = (rb.top + scrollY + 20) + 'px';
    });
    r.addEventListener('mouseleave', () => tip.hidden = true);
  });
}

/* ---------------- dashboard ---------------- */
async function viewDashboard(){
  if (HOME) return viewHomeDashboard();
  const P = period(), Q = period(true);
  const [lim, lic, time, ptime, comps, pcomps, devstats] = await Promise.all([
    api('v1/account/limits'), api('v1/account/license').catch(() => null),
    api('v1/stats/time', P), api('v1/stats/time', Q),
    api('v1/stats/companies', {...P, limit: 1000}), api('v1/stats/companies', {...Q, limit: 1000}),
    api('v1/stats/devices', {...P, limit: 1000})]);
  await loadMeta();
  const sum = (st, k) => (st.stats || []).reduce((a, s) => a + (s.value[k] || 0), 0);
  const q = sum(time, 'queries'), b = sum(time, 'blocked'), pb = sum(ptime, 'blocked');
  const nc = (comps.stats || []).length, pnc = (pcomps.stats || []).length;
  const ch = (a, p) => p ? Math.round((a - p) / p * 100) : 0;
  const delta = v => `<span class="d">${v >= 0 ? '+ ' : '- '}${Math.abs(v)} % <span class="${v >= 0 ? 'up' : 'down'}">${v >= 0 ? '↑' : '↓'}</span></span>`;
  const lm = (icon, label, l) => `<div class="lim">${icon}<div><div class="t">${label}</div><div class="v">${K(l.used)} <span>van ${K(l.limit)}</span></div></div>
    <div class="bar"><i style="width:${Math.min(100, l.used / Math.max(1, l.limit) * 100)}%"></i></div></div>`;
  const planName = lic ? ({STARTER: 'Starter', PERSONAL: 'Personal', TEAM: 'Team', ENTERPRISE: 'Enterprise'}[lic.license_type] || lic.license_type) + '-abonnement' : '';
  const exp = lic && lic.expiration_date ? new Date(lic.expiration_date).toLocaleDateString(LOCALE, {day: 'numeric', month: 'long', year: 'numeric'}) : '';
  const dstat = Object.fromEntries((devstats.stats || []).map(s => [s.device_id, s.value]));

  const servers = meta.servers.map(srv => {
    const devs = meta.devices.filter(d => d.dns_server_id === srv.id);
    const sv = devs.reduce((a, d) => { const v = dstat[d.id] || {}; a.q += v.queries || 0; a.b += v.blocked || 0; return a; }, {q: 0, b: 0});
    const st = srv.settings || {}, fl = ((st.filter_lists_settings || {}).filter_list || []).filter(f => f.enabled).length;
    const ur = (st.user_rules_settings || {}).rules_count ?? ((st.user_rules_settings || {}).rules || []).length;
    const rows = devs.map(d => { const v = dstat[d.id] || {};
      return `<tr data-name="${esc(d.name.toLowerCase())}">
        <td class="dn"><button class="sw ${(d.settings || {}).protection_enabled ? 'on' : ''}" role="switch" aria-checked="${!!(d.settings || {}).protection_enabled}" aria-label="Bescherming voor ${esc(d.name)}" data-devprot="${esc(d.id)}" ${CFG.blocking ? '' : 'disabled'}></button>${devIcon(d.device_type)}<span>${esc(d.name)}</span></td>
        <td data-l="Verzoeken"><button class="linkbtn req" ${goAttr('querylog', {device: d.id, server: '', country: ''})}>${K(v.queries)}</button></td>
        <td data-l="Geblokkeerd"><button class="linkbtn blk" ${goAttr('querylog', {device: d.id, server: '', country: '', logStatus: 'b'})}>${K(v.blocked)}</button> (${pct(v.blocked, v.queries)}%)</td>
        <td data-l="Bedrijven"><button class="linkbtn cmp" ${goAttr('bedrijven', {device: d.id, server: '', country: ''})}>${v.companies || 0}</button></td>
        <td class="act"><button class="linkbtn" ${goAttr('statistieken', {device: d.id, server: '', country: ''})}>Statistieken</button><a href="#apparaat/${esc(d.id)}">Instellingen</a></td></tr>`; }).join('');
    return `<section class="card srv">
      <div class="cardhead"><h2>${esc(srv.name)} <span class="help" tabindex="0" data-tip="Een DNS-server is een set instellingen voor je apparaten">?</span></h2>
        <div class="right">Geblokkeerd <button class="linkbtn blk" style="font-size:15px" ${goAttr('querylog', {server: srv.id, device: '', country: '', logStatus: 'b'})}>${K(sv.b)}</button> (${pct(sv.b, sv.q)}%) van <button class="linkbtn req" style="font-size:15px" ${goAttr('querylog', {server: srv.id, device: '', country: ''})}>${K(sv.q)}</button> verzoeken</div></div>
      <div class="srvrow"><span class="toggle"><span data-ptext>Bescherming is ${st.protection_enabled ? 'ingeschakeld' : 'uitgeschakeld'}</span> <button class="sw ${st.protection_enabled ? 'on' : ''}" role="switch" aria-checked="${!!st.protection_enabled}" aria-label="Bescherming" data-srvprot="${esc(srv.id)}" ${CFG.blocking ? '' : 'disabled'}></button></span>
        <a href="#server/${esc(srv.id)}">Serverinstellingen</a><a href="#lijsten/${esc(srv.id)}">Blokkeerlijsten (${fl})</a>
        <button class="linkbtn" data-rules="${esc(srv.id)}">Gebruikersregels (${ur})</button><button class="linkbtn" ${goAttr('statistieken', {server: srv.id, device: '', country: ''})}>Alle apparaten</button></div>
      <input class="search" type="search" placeholder="Zoek apparaat" aria-label="Zoek apparaat" data-filter>
      <table class="dtable"><thead><tr><th>Apparaten ${devs.length}</th><th>Verzoeken</th><th>Geblokkeerd</th><th>Bedrijven</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="empty">Er zijn nog geen apparaten gekoppeld aan deze server.</td></tr>'}</tbody></table>
      <a class="srvfoot" href="${ADG}" target="_blank" rel="noopener">${SVG.plus}Apparaat koppelen</a></section>`;
  }).join('');

  $('#view').innerHTML = `
    <section class="limits">${lm(SVG.cloud, 'Verzoeken', lim.requests)}${lm(SVG.device, 'Apparaten', lim.devices)}${lm(SVG.server, 'Servers', lim.dns_servers)}
      <div class="plan">${esc(planName)}${exp ? `<br><b>Volgende betaling: ${esc(exp)}</b>` : ''}</div></section>
    <section class="card">
      <a class="viewall" href="#statistieken">Alle statistieken bekijken</a>
      <div class="kpitabs" role="tablist">
        <button class="kpitab" role="tab" data-kpi="blocked" aria-selected="${state.kpi === 'blocked'}"><span class="t">Geblokkeerde verzoeken</span>
          <span class="n"><span class="blk">${K(b)}</span> (${pct(b, q)}%) van <span class="req">${K(q)}</span></span>${delta(ch(b, pb))}</button>
        <button class="kpitab" role="tab" data-kpi="companies" aria-selected="${state.kpi === 'companies'}"><span class="t">Bedrijven</span>
          <span class="n">${nc}${comps.navigation && comps.navigation.next ? '+' : ''}</span>${delta(ch(nc, pnc))}</button>
      </div>
      <div class="chart" id="chart"></div>
    </section>
    <div class="sect">DNS-servers <span class="help" tabindex="0" data-tip="Je DNS-servers en de apparaten die ze gebruiken">?</span></div>
    ${servers || '<section class="card"><p class="empty">Er zijn nog geen DNS-servers in dit account.</p></section>'}`;
  const draw = () => barChart($('#chart'), time.stats || [], state.kpi === 'companies' ? 'companies' : 'blocked');
  draw();
  document.querySelectorAll('.kpitab').forEach(t => t.addEventListener('click', () => { state.kpi = t.dataset.kpi;
    document.querySelectorAll('.kpitab').forEach(x => x.setAttribute('aria-selected', x === t)); draw(); }));
  document.querySelectorAll('[data-filter]').forEach(inp => inp.addEventListener('input', () => {
    const v = inp.value.trim().toLowerCase();
    inp.parentElement.querySelectorAll('tbody tr[data-name]').forEach(tr => tr.hidden = v && !tr.dataset.name.includes(v)); }));
  document.querySelectorAll('[data-rules]').forEach(x => x.addEventListener('click', () => rulesModal(x.dataset.rules)));
  document.querySelectorAll('[data-srvprot]').forEach(b => b.addEventListener('click', () => toggleSwitch(b, on => post('api/settings', {server_id: b.dataset.srvprot, patch: {protection_enabled: on}}),
    'Bescherming uitzetten?', 'Zonder bescherming worden advertenties, trackers en gevaarlijke domeinen niet meer geblokkeerd op alle apparaten van deze server.',
    on => { const t = b.parentElement.querySelector('[data-ptext]'); if (t) t.textContent = 'Bescherming is ' + (on ? 'ingeschakeld' : 'uitgeschakeld'); })));
  document.querySelectorAll('[data-devprot]').forEach(b => b.addEventListener('click', () => toggleSwitch(b, on => post('api/device', {device_id: b.dataset.devprot, protection_enabled: on}),
    'Bescherming voor dit apparaat uitzetten?', 'Dit apparaat krijgt dan geen blokkades meer, ook niet van je blokkeerlijsten en gebruikersregels.')));
  window.__redraw = draw;
}

/* ---------------- filters ---------------- */
function filtersHTML(){
  const opt = (v, l, cur) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(l)}</option>`;
  const devs = meta.devices.filter(d => !state.server || d.dns_server_id === state.server);
  if (HOME) return `<div class="filters"><select data-f="range" aria-label="Periode">${Object.entries(RANGES).map(([k, v]) => opt(k, v[0], state.range)).join('')}</select></div>`;
  return `<div class="filters">
    <select data-f="range" aria-label="Periode">${Object.entries(RANGES).map(([k, v]) => opt(k, v[0], state.range)).join('')}</select>
    <select data-f="server" aria-label="Server">${opt('', 'Alle servers', state.server)}${meta.servers.map(s => opt(s.id, s.name, state.server)).join('')}</select>
    <select data-f="device" aria-label="Apparaat">${opt('', 'Alle apparaten', state.device)}${devs.map(d => opt(d.id, d.name, state.device)).join('')}</select>
    <select data-f="country" aria-label="Land">${opt('', 'Alle landen', state.country)}${meta.countries.filter(c => c).map(c => opt(c, countryName(c), state.country)).join('')}</select></div>`;
}
function bindFilters(){
  document.querySelectorAll('select[data-f]').forEach(s => s.addEventListener('change', () => {
    state[s.dataset.f] = s.value; if (s.dataset.f === 'server') state.device = ''; route(); }));
}
async function ensureMeta(){
  if (!meta.servers.length) await loadMeta();
  if (HOME) return;
  if (!meta.countries.length) { const c = await api('v1/stats/countries', {...period(), limit: 100}); meta.countries = (c.stats || []).map(x => x.country); }
  if (!meta.companies.length) { const c = await api('v1/stats/companies', {...period(), limit: 100}).catch(() => ({stats: []})); meta.companies = (c.stats || []).map(x => x.company_name).filter(Boolean); }
}

/* ---------------- statistieken ---------------- */
async function viewStats(){
  if (HOME) return viewHomeStats();
  await ensureMeta();
  const P = {...period(), ...filt()};
  const [time, cats, comps, ctry, doms] = await Promise.all([
    api('v1/stats/time', P), api('v1/stats/categories', P), api('v1/stats/companies', {...P, limit: 5}),
    api('v1/stats/countries', {...P, limit: 100}), api('v1/stats/domains', {...P, limit: 5})]);
  const tq = (time.stats || []).reduce((a, s) => a + s.value.queries, 0), tb = (time.stats || []).reduce((a, s) => a + s.value.blocked, 0);
  const catTotal = (cats.stats || []).reduce((a, s) => a + s.queries, 0);
  const order = ['ADS', 'TRACKERS', 'SOCIAL_MEDIA', 'CDN', 'OTHERS'];
  const catRows = order.map(k => { const s = (cats.stats || []).find(x => x.category_type === k) || {queries: 0}; const [l, ic] = CAT[k];
    return `<tr><td class="nm"><button class="nmlink" ${goAttr('querylog', {logCategory: k})} style="display:flex;align-items:center;gap:14px">${CATICON[ic]}${l}</button></td>
      <td class="n"><button class="linkbtn req" style="font-size:15px" ${goAttr('querylog', {logCategory: k})}>${K(s.queries)}</button></td><td class="p">${pct(s.queries, catTotal)}%</td></tr>`; }).join('');
  const valRow = (icon, name, v, nav) => `<tr><td class="nm">${nav.name ? `<button class="nmlink" ${nav.name} style="display:flex;align-items:center;gap:14px">${icon}${esc(name)}</button>` : `<span>${icon}${esc(name)}</span>`}</td>
    <td class="n"><button class="linkbtn req" style="font-size:15px" ${nav.req}>${K(v.queries)}</button></td>
    <td class="p"><button class="linkbtn blk" style="font-size:15px" ${nav.blk}>${K(v.blocked)}</button> (${pct(v.blocked, v.queries)}%)</td></tr>`;
  const coNav = n => ({name: goAttr('querylog', {logCompany: companyId(n)}), req: goAttr('querylog', {logCompany: companyId(n)}), blk: goAttr('querylog', {logCompany: companyId(n), logStatus: 'b'})});
  const compRows = (comps.stats || []).map(s => valRow(favicon(guessDomain(s.company_name), s.company_name || 'Onbekend bedrijf'), s.company_name || 'Onbekend bedrijf', s.value || {}, coNav(s.company_name))).join('');
  const ctryList = (ctry.stats || []);
  const ctryRows = ctryList.slice(0, 5).map(s => valRow(flag(s.country), countryName(s.country), s.value,
    {name: s.country ? goAttr('statistieken', {country: s.country}) : '', req: goAttr('querylog', {country: s.country || ''}), blk: goAttr('querylog', {country: s.country || '', logStatus: 'b'})})).join('');
  const domRows = (doms.stats || []).map(s => valRow(favicon(s.domain), s.domain || 'Onbekend domein', s.value || {},
    {name: goAttr('querylog', {logSearch: s.domain}), req: goAttr('querylog', {logSearch: s.domain}), blk: goAttr('querylog', {logSearch: s.domain, logStatus: 'b'})})).join('');
  const none = '<tr><td class="empty">Nog geen gegevens in deze periode.</td></tr>';
  $('#view').innerHTML = `${filtersHTML()}
    <section class="card"><div class="cardhead"><h2>Verzoeken, ${esc(RANGES[state.range][0].toLowerCase())} <span class="help" tabindex="0" data-tip="Totaal aantal verzoeken van de geselecteerde apparaten naar de geselecteerde landen">?</span></h2>
      <div class="right">Geblokkeerd <button class="linkbtn blk" style="font-size:15px" ${goAttr('querylog', {logStatus: 'b'})}>${K(tb)}</button> (${pct(tb, tq)}%) van <button class="linkbtn req" style="font-size:15px" ${goAttr('querylog', {})}>${K(tq)}</button> verzoeken</div></div>
      <div class="chart" id="chart"></div></section>
    <div class="grid2">
      <section class="card"><h2>Soorten verzoeken <span class="help" tabindex="0" data-tip="Waar de verzoeken over gingen">?</span></h2><table class="list">${catRows}</table></section>
      <section class="card"><div class="cardhead"><h2>Topbedrijven <span class="help" tabindex="0" data-tip="Bedrijven met de meeste verzoeken">?</span></h2><a href="#bedrijven" class="right" style="font-size:14px;color:var(--link)">Alle bedrijven</a></div><table class="list">${compRows || none}</table></section>
    </div>
    <div class="grid2">
      <section class="card"><div class="cardhead"><h2>Topbestemmingen <span class="help" tabindex="0" data-tip="Landen waar de servers staan die je apparaten benaderen">?</span></h2></div><table class="list">${ctryRows || none}</table></section>
      <section class="card"><div class="cardhead"><h2>Topdomeinen <span class="help" tabindex="0" data-tip="Domeinen met de meeste verzoeken">?</span></h2><a href="#querylog" class="right" style="font-size:14px;color:var(--link)">Querylog</a></div><table class="list">${domRows || none}</table></section>
    </div>
    <section class="card mapcard"><div class="map" id="map"></div></section>`;
  bindFilters();
  const draw = () => barChart($('#chart'), time.stats || [], 'blocked');
  draw(); window.__redraw = draw;
  drawMap(ctryList);
}
async function viewHomeStats(){
  await ensureMeta();
  const P = period();
  const [time, doms, bdoms, devs] = await Promise.all([api('v1/stats/time', P), api('v1/stats/domains', {...P, limit: 10}),
    api('v1/stats/domains', {...P, limit: 10, sort: 'blocked'}), api('v1/stats/devices', {...P, limit: 10})]);
  const tq = (time.stats || []).reduce((a, s) => a + s.value.queries, 0), tb = (time.stats || []).reduce((a, s) => a + s.value.blocked, 0);
  const devName = Object.fromEntries(meta.devices.map(d => [d.id, d.name]));
  const none = '<tr><td class="empty">Nog geen gegevens in deze periode.</td></tr>';
  const domRow = (s, blk) => `<tr><td class="nm"><button class="nmlink" ${goAttr('querylog', {logSearch: s.domain})} style="display:flex;align-items:center;gap:14px">${favicon(s.domain)}${esc(s.domain)}</button></td>
    <td class="n"><button class="linkbtn ${blk ? 'blk' : 'req'}" style="font-size:15px" ${goAttr('querylog', blk ? {logSearch: s.domain, logStatus: 'b'} : {logSearch: s.domain})}>${K(blk ? s.value.blocked : s.value.queries)}</button></td></tr>`;
  const top = (doms.stats || []).map(s => domRow(s, false)).join(''), topb = (bdoms.stats || []).filter(s => s.value.blocked).map(s => domRow(s, true)).join('');
  const devRows = (devs.stats || []).map(s => `<tr><td class="nm"><button class="nmlink" ${goAttr('querylog', {device: s.device_id})} style="display:flex;align-items:center;gap:14px">${devIcon((meta.devices.find(d => d.id === s.device_id) || {}).device_type)}${esc(devName[s.device_id] || s.device_id)}</button></td>
    <td class="n"><button class="linkbtn req" style="font-size:15px" ${goAttr('querylog', {device: s.device_id})}>${K(s.value.queries)}</button></td></tr>`).join('');
  $('#view').innerHTML = `${filtersHTML()}
    <section class="card"><div class="cardhead"><h2>Verzoeken <span class="help" tabindex="0" data-tip="AdGuard Home bewaart statistieken zo lang als ingesteld bij Instellingen, Algemene instellingen">?</span></h2>
      <div class="right">Geblokkeerd <button class="linkbtn blk" style="font-size:15px" ${goAttr('querylog', {logStatus: 'b'})}>${K(tb)}</button> (${pct(tb, tq)}%) van <button class="linkbtn req" style="font-size:15px" ${goAttr('querylog', {})}>${K(tq)}</button> verzoeken</div></div>
      <div class="chart" id="chart"></div></section>
    <div class="grid2">
      <section class="card"><div class="cardhead"><h2>Meest opgevraagde domeinen</h2><a href="#querylog" class="right" style="font-size:14px;color:var(--link)">Querylog</a></div><table class="list">${top || none}</table></section>
      <section class="card"><h2>Meest geblokkeerde domeinen</h2><table class="list">${topb || none}</table></section>
    </div>
    <section class="card"><h2>Actiefste clients</h2><table class="list">${devRows || none}</table></section>`;
  bindFilters();
  const draw = () => barChart($('#chart'), time.stats || [], 'blocked');
  draw(); window.__redraw = draw;
}
const KNOWN = {'Microsoft Corporation':'microsoft.com','Google':'google.com','Sentry':'sentry.io','Meta Platforms, Inc.':'meta.com','Apple Inc.':'apple.com',
  'AppLovin':'applovin.com','Amazon':'amazon.com','Cloudflare':'cloudflare.com','Spotify':'spotify.com','Samsung':'samsung.com','TikTok':'tiktok.com',
  'Akamai':'akamai.com','Netflix':'netflix.com','Ubiquiti':'ui.com','Yandex':'yandex.com','Twitter':'x.com','Adobe':'adobe.com','Criteo':'criteo.com'};
function guessDomain(name){
  if (!name) return '';
  name = String(name);
  if (KNOWN[name]) return KNOWN[name];
  if (/\.[a-z]{2,}$/i.test(name)) return name;
  return name.toLowerCase().replace(/,?\s*(inc|ltd|llc|corporation|corp|gmbh|b\.v\.)\.?$/i, '').replace(/[^a-z0-9]/g, '') + '.com';
}

/* ---------------- wereldkaart ---------------- */
async function drawMap(list){
  const el = $('#map'); if (!el) return;
  if (!mapData) { try { mapData = await (await fetch('map.json')).json(); } catch (e) { el.innerHTML = ''; return; } }
  const M = mapData, home = M.c[CFG.home] || M.nl, max = Math.max(1, ...list.map(s => s.value.queries));
  let arcs = '', dots = '';
  list.filter(s => s.country && M.c[s.country]).forEach(s => {
    const [x, y] = M.c[s.country], r = 6 + Math.sqrt(s.value.queries / max) * 24;
    if (s.country !== CFG.home) {
      const mx = (home[0] + x) / 2, my = Math.min(home[1], y) - Math.abs(x - home[0]) * 0.22;
      arcs += `<path class="arc" d="M${home[0]} ${home[1]} Q${mx} ${my} ${x} ${y}"/>`;
    }
    dots += `<circle class="dot" cx="${x}" cy="${y}" r="${r.toFixed(1)}"><title>${esc(countryName(s.country))}: ${K(s.value.queries)} verzoeken</title></circle>`;
  });
  const pin = `<g transform="translate(${home[0]} ${home[1] - 30})"><path d="M0 30C-12 16-16 10-16 0a16 16 0 0 1 32 0c0 10-4 16-16 30z" fill="#e0457b" stroke="#fff" stroke-width="3"/><circle cx="0" cy="0" r="5.5" fill="#fff"/></g>`;
  const z = mapZoom, vw = M.w / z, vh = M.h / z, vx = Math.min(M.w - vw, Math.max(0, home[0] - vw / 2)), vy = Math.min(M.h - vh, Math.max(0, home[1] - vh / 2));
  el.innerHTML = `<svg viewBox="${vx} ${vy} ${vw} ${vh}" role="img" aria-label="Kaart met bestemmingen van DNS-verzoeken"><path class="land" d="${M.d}"/>${arcs}${dots}${pin}</svg>
    <div class="zoom"><button data-z="1" aria-label="Inzoomen">+</button><button data-z="-1" aria-label="Uitzoomen">−</button></div>`;
  el.querySelectorAll('[data-z]').forEach(b => b.addEventListener('click', () => { mapZoom = Math.min(4, Math.max(1, mapZoom * (b.dataset.z === '1' ? 1.6 : 1 / 1.6))); drawMap(list); }));
}

/* ---------------- bedrijven ---------------- */
async function viewCompanies(){
  await ensureMeta();
  const P = {...period(), ...filt()};
  const res = await api('v1/stats/companies/detailed', {...P, limit: 100});
  const all = (res.stats || []).map(s => ({...s, company_name: s.company_name || 'Onbekend bedrijf', domains_count: s.domains_count || 0,
    value: s.value || {queries: 0, blocked: 0, companies: 0}}));
  const byQ = [...all].sort((a, b) => b.value.queries - a.value.queries), byB = [...all].sort((a, b) => b.value.blocked - a.value.blocked);
  const rows = (state.sort === 'blocked' ? byB : byQ);
  const topCard = (title, s, help) => !s ? '' : `<section class="card topco"><h2>${title} <span class="help" tabindex="0" data-tip="${help}">?</span></h2>
    <button class="who linkbtn" style="display:flex;align-items:center;gap:10px;font-size:15px" ${goAttr('querylog', {logCompany: companyId(s.company_name)})}>${favicon(guessDomain(s.company_name), s.company_name)}${esc(s.company_name)}</button>
    <table class="list">
      <tr><td class="nm">Verzoeken</td><td class="n"><span class="req" style="text-decoration:underline">${K(s.value.queries)}</span></td><td class="p"></td></tr>
      <tr><td class="nm">Geblokkeerd</td><td class="n"><span class="blk" style="text-decoration:underline">${K(s.value.blocked)}</span></td><td class="p">${pct(s.value.blocked, s.value.queries)}%</td></tr>
      <tr><td class="nm">Domeinen</td><td class="n">${s.domains_count}</td><td class="p"></td></tr>
      <tr><td class="nm">Meest opgevraagd domein</td><td class="n" colspan="2" style="max-width:240px;overflow:hidden;text-overflow:ellipsis">${esc(s.top_queries_domain || '–')}</td></tr>
      <tr><td class="nm">Meest geblokkeerd domein</td><td class="n" colspan="2" style="max-width:240px;overflow:hidden;text-overflow:ellipsis">${esc(s.top_blocked_domain || '–')}</td></tr>
    </table></section>`;
  $('#view').innerHTML = `<h1 class="ptitle">Bedrijven <button class="refresh" id="rf" aria-label="Vernieuwen">${SVG.refresh}</button></h1>${filtersHTML()}
    <div class="grid2">${topCard('Meest opgevraagd bedrijf', byQ[0], 'Het bedrijf met de meeste verzoeken')}${topCard('Meest geblokkeerd bedrijf', byB[0], 'Het bedrijf met de meeste geblokkeerde verzoeken')}</div>
    <section class="card" style="margin-top:22px"><div class="cardhead"><h2>Topbedrijven</h2>
      <select class="sortsel" id="sort" aria-label="Sortering"><option value="blocked" ${state.sort === 'blocked' ? 'selected' : ''}>Meest geblokkeerde bedrijven eerst</option><option value="queries" ${state.sort === 'queries' ? 'selected' : ''}>Meest opgevraagde bedrijven eerst</option></select></div>
      <div class="tablewrap"><table class="ctable"><thead><tr><th>Bedrijf</th><th>Domeinen</th><th>Verzoeken</th><th>Geblokkeerd</th><th>Meest opgevraagd domein</th><th>Meest geblokkeerd domein</th></tr></thead>
      <tbody>${rows.map(s => { const cid = companyId(s.company_name);
        return `<tr><td class="co"><button class="nmlink" ${goAttr('querylog', {logCompany: cid})} style="display:flex;align-items:center;gap:12px">${favicon(guessDomain(s.company_name), s.company_name)}${esc(s.company_name)}</button></td>
        <td><button class="linkbtn" style="color:var(--text)" ${goAttr('querylog', {logCompany: cid})}>${s.domains_count}</button></td>
        <td><button class="linkbtn req" ${goAttr('querylog', {logCompany: cid})}>${K(s.value.queries)}</button></td><td><button class="linkbtn blk" ${goAttr('querylog', {logCompany: cid, logStatus: 'b'})}>${K(s.value.blocked)}</button></td>
        <td>${s.top_queries_domain ? `<button class="linkbtn dom" style="color:var(--text)" title="${esc(s.top_queries_domain)}" ${goAttr('querylog', {logSearch: s.top_queries_domain})}>${esc(s.top_queries_domain)}</button>` : '–'}</td>
        <td>${s.top_blocked_domain ? `<button class="linkbtn dom" style="color:var(--text)" title="${esc(s.top_blocked_domain)}" ${goAttr('querylog', {logSearch: s.top_blocked_domain, logStatus: 'b'})}>${esc(s.top_blocked_domain)}</button>` : '–'}</td></tr>`; }).join('')
        || '<tr><td colspan="6" class="empty">Nog geen gegevens in deze periode.</td></tr>'}</tbody></table></div></section>`;
  bindFilters();
  $('#sort').addEventListener('change', e => { state.sort = e.target.value; viewCompanies().catch(showError); });
  $('#rf').addEventListener('click', () => route());
}

/* ---------------- querylog ---------------- */
let logItems = [], logPages = [];
const STATUS = {b: ['REQUEST_BLOCKED', 'RESPONSE_BLOCKED'], p: ['NONE'], a: ['REQUEST_ALLOWED', 'RESPONSE_ALLOWED'], m: ['MODIFIED']};
const pillOf = st => /BLOCKED/.test(st || '') ? ['b', 'Geblokkeerd'] : /ALLOWED/.test(st || '') ? ['a', 'Toegestaan'] : st === 'MODIFIED' ? ['m', 'Gewijzigd'] : ['p', 'Verwerkt'];
const SECURE = ['DOH', 'DOQ', 'DOT', 'DNSCRYPT', 'DNSCRYPT_TCP', 'DNSCRYPT_UDP'];
const LOCK = '<svg class="lock" width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M7 10V8a5 5 0 0 1 10 0v2h1a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1zm2 0h6V8a3 3 0 0 0-6 0z"/></svg>';
const SEARCH = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>';
const protoOf = x => { const p = x.dns_proto_type; return typeof p === 'string' ? p : p && typeof p.value === 'string' ? p.value : ''; };
const rtype = t => String(t || '').replace(/^Type/, '');

async function viewLog(){
  await ensureMeta();
  const opt = (v, l, cur) => `<option value="${esc(v)}" ${String(v) === String(cur) ? 'selected' : ''}>${esc(l)}</option>`;
  const devs = meta.devices.filter(d => !state.server || d.dns_server_id === state.server);
  const companyOpts = [...new Map(meta.companies.map(n => [companyId(n), n])).entries()];
  if (state.logCompany && !companyOpts.some(([id]) => id === state.logCompany)) companyOpts.unshift([state.logCompany, companyName(state.logCompany)]);
  $('#view').innerHTML = `<h1 class="ptitle">Querylog</h1>
    <div class="lqbar"><span class="toggle"><button class="sw ${state.logAuto ? 'on' : ''}" id="auto" role="switch" aria-checked="${state.logAuto}" aria-label="Automatisch verversen"></button>Automatisch verversen</span>
      <span class="sep"></span><select id="ivl" aria-label="Interval">${[5, 10, 30, 60].map(v => opt(v, v < 60 ? v + ' s' : '1 min', state.logInterval)).join('')}</select>
      <button class="linkbtn plain" id="rf">Vernieuwen</button><span class="muted" id="last"></span>
      <button class="linkbtn plain push" id="hf">${state.filtersHidden ? 'Filters tonen' : 'Filters verbergen'}</button></div>
    <div class="lfilters" ${state.filtersHidden ? 'hidden' : ''}>
      <div class="r1"><label class="sbox">${SEARCH}<input id="ls" type="search" placeholder="Zoeken" value="${esc(state.logSearch)}" aria-label="Zoek domein"></label>
        <select data-f="range" aria-label="Periode">${Object.entries(RANGES).map(([k, v]) => opt(k, v[0], state.range)).join('')}</select>
        <select data-f="device" aria-label="Apparaat">${opt('', 'Alle apparaten', state.device)}${devs.map(d => opt(d.id, d.name, state.device)).join('')}</select>
        ${C(`<select data-f="country" aria-label="Land">${opt('', 'Alle landen', state.country)}${meta.countries.filter(c => c).map(c => opt(c, countryName(c), state.country)).join('')}</select>`)}</div>
      <div class="r2"><select data-l="logStatus" aria-label="Status">${opt('', 'Alle statussen', state.logStatus)}${opt('b', 'Geblokkeerd', state.logStatus)}${opt('p', 'Verwerkt', state.logStatus)}${opt('a', 'Toegestaan', state.logStatus)}${opt('m', 'Gewijzigd', state.logStatus)}</select>
        <select data-l="logType" aria-label="Type" id="types">${opt('', 'Alle types', state.logType)}</select>
        ${C(`<select data-l="logCompany" aria-label="Bedrijf">${opt('', 'Alle bedrijven', state.logCompany)}${companyOpts.map(([id, n]) => opt(id, n, state.logCompany)).join('')}</select>
        <select data-f="server" aria-label="Server">${opt('', 'Alle servers', state.server)}${meta.servers.map(s => opt(s.id, s.name, state.server)).join('')}</select>
        <select data-l="logCategory" aria-label="Categorie">${opt('', 'Alle categorieën', state.logCategory)}${Object.entries(CAT).map(([k, v]) => opt(k, v[0], state.logCategory)).join('')}</select>`)}</div>
    </div>
    <div class="tablewrap"><table class="qtable"><thead><tr><th>Status</th><th>Verzoek</th><th class="c-co">Bedrijf</th><th>${HOME ? 'Client' : 'Apparaat'}</th><th>Datum</th><th>${HOME ? 'IP-adres' : 'Client'}</th><th></th></tr></thead><tbody id="qrows"><tr><td colspan="7" class="empty">Querylog laden…</td></tr></tbody></table></div>
    <div id="qmore"></div>`;
  bindFilters();
  document.querySelectorAll('select[data-l]').forEach(sel => sel.addEventListener('change', () => { state[sel.dataset.l] = sel.value; sel.dataset.l === 'logType' ? drawRows() : loadRows(); }));
  let t; $('#ls').addEventListener('input', e => { clearTimeout(t); t = setTimeout(() => { state.logSearch = e.target.value.trim(); loadRows(); }, 400); });
  $('#auto').addEventListener('click', () => { state.logAuto = !state.logAuto; const b = $('#auto'); b.classList.toggle('on', state.logAuto); b.setAttribute('aria-checked', state.logAuto); schedule(); });
  $('#ivl').addEventListener('change', e => { state.logInterval = +e.target.value; schedule(); });
  $('#rf').addEventListener('click', () => loadRows());
  $('#hf').addEventListener('click', () => { state.filtersHidden = !state.filtersHidden; document.querySelector('.lfilters').hidden = state.filtersHidden; $('#hf').textContent = state.filtersHidden ? 'Filters tonen' : 'Filters verbergen'; });
  await loadRows();
  schedule();
}
function schedule(){
  clearInterval(logTimer);
  if (state.logAuto) logTimer = setInterval(() => {
    if (document.hidden || document.querySelector('.modal') || [...document.querySelectorAll('.menu')].some(m => !m.hidden)) return;
    loadRows(false, true).catch(() => {});
  }, state.logInterval * 1000);
}
async function loadRows(more, silent){
  const params = {...period(), ...filt(), limit: 100, search: state.logSearch || null, statuses: STATUS[state.logStatus] || null,
    categories: state.logCategory || null, companies: state.logCompany || null};
  if (more) { const cur = logPages.find(p => p.current), nxt = cur && logPages.find(p => p.page_number === cur.page_number + 1); if (!nxt) return; params.cursor = nxt.page_cursor; }
  try {
    const res = await api('v1/query_log', params);
    logItems = more ? logItems.concat(res.items || []) : (res.items || []); logPages = res.pages || [];
    if (!silent) showDemo();
  } catch (e) { if (!silent) showError(e); return; }
  const last = $('#last'); if (last) last.textContent = 'Laatst ververst: ' + new Date().toLocaleTimeString(LOCALE);
  const types = [...new Set(logItems.map(x => rtype(x.dns_request_type)).filter(Boolean))].sort();
  const ts = $('#types'); if (ts) ts.innerHTML = `<option value="">Alle types</option>` + types.map(v => `<option value="${esc(v)}" ${v === state.logType ? 'selected' : ''}>${esc(v)}</option>`).join('');
  drawRows();
}
function drawRows(){
  const body = $('#qrows'); if (!body) return;
  const devName = Object.fromEntries(meta.devices.map(d => [d.id, d.name]));
  const items = logItems.filter(x => !state.logType || rtype(x.dns_request_type) === state.logType);
  body.innerHTML = items.map((x, i) => {
    const fi = x.filtering_info || {}, [pc, pl] = pillOf(fi.filtering_status), c = CAT[x.category_type] || CAT.OTHERS, d = new Date(x.time_millis);
    const blocked = pc === 'b', raw = x.client_ip || x.client_address || x.ip || x.network || '';
    const client = raw || '–';
    const menu = CFG.blocking && x.domain ? `<div class="kebab"><button aria-label="Acties voor ${esc(x.domain)}" data-k="${i}">⋮</button>
      <div class="menu" hidden><button data-act="${blocked ? 'unblock' : 'block'}" data-i="${i}">${blocked ? 'Domein deblokkeren' : 'Domein blokkeren'}</button>
      <button ${goAttr('querylog', {logSearch: x.domain})}>Alleen dit domein tonen</button></div></div>` : '';
    return `<tr><td class="c-st"><span class="pill ${pc}">${pl}</span></td>
      <td class="c-rq"><span class="rq">${HOME ? '' : CATICON[c[1]].replace('class="ico"', 'class="ico" role="img" aria-label="' + c[0] + '"')}${SECURE.includes(protoOf(x)) ? LOCK : ''}<span class="dom" title="${esc(x.rule ? x.domain + ' (' + x.rule + ')' : x.domain)}">${esc(x.domain)}</span></span>${HOME && x.rule ? `<div class="muted rule">${esc(x.rule)}</div>` : ''}</td>
      <td class="c-co"><div class="co-name">${esc(companyName(x.company_id))}</div></td><td class="c-dv">${esc(devName[x.device_id] || '–')}</td>
      <td class="c-dt"><div class="dt">${d.toLocaleTimeString(LOCALE)}<br>${d.toLocaleDateString(LOCALE, {day: 'numeric', month: 'short', year: 'numeric'})}</div></td>
      <td class="c-cl"><span class="cl">${HOME ? '' : x.client_country ? flag(x.client_country) : '<span class="q">?</span>'}${esc(client)}</span></td><td class="c-mn">${menu}</td></tr>`;
  }).join('') || '<tr><td colspan="7" class="empty">Geen verzoeken gevonden met deze filters. Kies een langere periode of minder filters.</td></tr>';
  body.querySelectorAll('[data-k]').forEach(b => b.addEventListener('click', e => { e.stopPropagation(); const m = b.nextElementSibling, open = m.hidden;
    document.querySelectorAll('.menu').forEach(x => x.hidden = true); m.hidden = !open; }));
  body.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => { const x = items[+b.dataset.i]; b.closest('.menu').hidden = true;
    confirmRule(x.domain, b.dataset.act, x.dns_server_id, (x.filtering_info || {}).filtering_type, () => loadRows()); }));
  const cur = logPages.find(p => p.current), hasMore = cur && logPages.some(p => p.page_number === cur.page_number + 1);
  $('#qmore').innerHTML = hasMore ? '<button class="more" id="more">Meer laden</button>' : '';
  if (hasMore) $('#more').addEventListener('click', () => loadRows(true));
}

/* ---------------- instellingen ---------------- */
function confirmBox(title, text, okLabel){
  return new Promise(res => { const m = modal(`<h3>${esc(title)}</h3><p>${esc(text)}</p><div class="btns"><button class="btn" data-close>Annuleren</button><button class="btn dng" data-ok data-focus>${esc(okLabel || 'Uitzetten')}</button></div>`);
    m.el.querySelector('[data-ok]').addEventListener('click', () => { m.close(); res(true); });
    m.el.addEventListener('click', e => { if (e.target === m.el || e.target.closest('[data-close]')) res(false); }); });
}
async function toggleSwitch(b, send, offTitle, offText, after){
  if (b.disabled || b.classList.contains('busy')) return;
  const on = !b.classList.contains('on');
  if (!on && offTitle && !(await confirmBox(offTitle, offText))) return;
  b.classList.add('busy'); b.classList.toggle('on', on); b.setAttribute('aria-checked', on);
  try { const r = await send(on); toast(r.message || 'Opgeslagen.'); after && after(on); meta.servers = []; }
  catch (e) { b.classList.toggle('on', !on); b.setAttribute('aria-checked', !on); toast(e.message, true); }
  b.classList.remove('busy');
}
const getPath = (o, path) => path.split('.').reduce((a, k) => (a || {})[k], o);
const patchFor = (path, v) => path.split('.').reverse().reduce((acc, k) => ({[k]: acc}), v);
const roNote = () => CFG.blocking ? '' : '<p class="ro">Wijzigen staat uit in de instellingen van de app. Je kunt hier alleen kijken.</p>';

async function viewServer(id){
  const srv = await api('v1/dns_servers/' + encodeURIComponent(id));
  const st = srv.settings || {}, fl = ((st.filter_lists_settings || {}).filter_list || []).filter(f => f.enabled).length;
  const ur = ((st.user_rules_settings || {}).rules || []).length;
  const row = (label, help, path, offWarn) => `<div class="setrow"><div><div class="sl">${label}</div>${help ? `<div class="sh">${help}</div>` : ''}</div>
    <button class="sw ${getPath(st, path) ? 'on' : ''}" role="switch" aria-checked="${!!getPath(st, path)}" aria-label="${esc(label)}" data-path="${path}" ${offWarn ? `data-warn="${esc(offWarn)}"` : ''} ${CFG.blocking ? '' : 'disabled'}></button></div>`;
  const mode = (st.blocking_mode_settings || {}).blocking_mode || 'NONE';
  const MODES = {NONE: 'Standaard', REFUSED: 'Weigeren (REFUSED)', NXDOMAIN: 'Bestaat niet (NXDOMAIN)', NULL_IP: 'Leeg adres (0.0.0.0)', CUSTOM_IP: 'Eigen IP-adres'};
  $('#view').innerHTML = `<a class="back" href="#dashboard">← Dashboard</a><h1 class="ptitle">${esc(srv.name)}</h1><p class="psub">${HOME ? 'Instellingen' : 'Serverinstellingen'}</p>${roNote()}
    <section class="card setcard"><h2>Algemeen</h2>
      ${row('Bescherming', HOME ? 'Blokkeert advertenties, trackers en gevaarlijke domeinen voor alle clients van AdGuard Home.' : 'Blokkeert advertenties, trackers en gevaarlijke domeinen voor alle apparaten op deze server.', 'protection_enabled', 'Zonder bescherming wordt er op deze server niets meer geblokkeerd.')}
      <div class="setrow"><div><div class="sl">Blokkeermodus</div><div class="sh">Wat een apparaat terugkrijgt als een domein geblokkeerd is.</div></div>
        <select data-mode ${CFG.blocking ? '' : 'disabled'} aria-label="Blokkeermodus">${Object.entries(MODES).filter(([k]) => k !== 'CUSTOM_IP' || mode === 'CUSTOM_IP').map(([k, v]) => `<option value="${k}" ${k === mode ? 'selected' : ''}>${v}</option>`).join('')}</select></div>
      <div class="setrow"><div><div class="sl">Bewaartijd geblokkeerde antwoorden</div><div class="sh">Hoeveel seconden een apparaat een blokkade onthoudt (TTL).</div></div>
        <input type="number" min="0" max="3600" value="${st.block_ttl_seconds ?? 10}" data-ttl ${CFG.blocking ? '' : 'disabled'} aria-label="TTL in seconden"></div>
      ${C(row('IP-adressen bewaren in de querylog', 'Laat zien vanaf welk adres een verzoek kwam.', 'ip_log_enabled'))}
      ${C(row('Nieuwe apparaten automatisch koppelen', 'Apparaten die deze server gebruiken worden vanzelf toegevoegd.', 'auto_connect_devices_enabled'))}
      <div class="setrow"><div><div class="sl">Blokkeerlijsten</div><div class="sh">${fl} lijsten ingeschakeld.</div></div><a href="#lijsten/${esc(srv.id)}" class="linkbtn">Beheren</a></div>
      <div class="setrow"><div><div class="sl">Gebruikersregels</div><div class="sh">${ur} eigen regels.</div></div><button class="linkbtn" data-rules="${esc(srv.id)}">Beheren</button></div></section>
    <section class="card setcard"><h2>Beveiliging</h2>
      ${row('Veilig surfen', 'Blokkeert bekende phishing- en malwaredomeinen.', 'safebrowsing_settings.enabled', 'Phishing- en malwaredomeinen worden dan niet meer geblokkeerd.')}
      ${C(row('Gevaarlijke domeinen blokkeren', 'Domeinen die bekendstaan om misleiding of schadelijke software.', 'safebrowsing_settings.block_dangerous_domains'))}
      ${C(row('Nieuw geregistreerde domeinen blokkeren', 'Domeinen van minder dan 30 dagen oud worden vaak gebruikt voor oplichting.', 'safebrowsing_settings.block_nrd'))}
      ${C(row('Typosquatting tegengaan', 'Blokkeert domeinen die bijna hetzelfde heten als bekende sites, zoals gooogle.com.', 'safebrowsing_settings.typosquatting_protection_enabled'))}
      ${C(row('Lookalike-tekens tegengaan', 'Blokkeert domeinen met tekens die op gewone letters lijken (homoglyphs).', 'safebrowsing_settings.homoglyph_protection_enabled'))}</section>
    <section class="card setcard"><h2>Ouderlijk toezicht</h2>
      ${row('Ouderlijk toezicht', HOME ? 'Blokkeert websites voor volwassenen.' : 'Schakelt de opties hieronder in.', 'parental_control_settings.enabled')}
      ${C(row('Websites voor volwassenen blokkeren', '', 'parental_control_settings.block_adult_websites_enabled'))}
      ${row('Veilig zoeken afdwingen', HOME ? 'Voor Google, Bing, YouTube en andere zoekmachines.' : 'Voor Google, Bing en andere zoekmachines.', 'parental_control_settings.engines_safe_search_enabled')}
      ${C(row('Beperkte modus op YouTube', '', 'parental_control_settings.youtube_safe_search_enabled'))}</section>
    ${C(`<section class="card setcard"><h2>Browsers en privacy</h2>
      ${row('Chrome-prefetch blokkeren', 'Voorkomt dat Chrome pagina’s vooraf laadt via eigen servers.', 'block_chrome_prefetch')}
      ${row('Firefox-DNS-over-HTTPS uitschakelen', 'Zorgt dat Firefox deze DNS-server blijft gebruiken.', 'block_firefox_canary')}
      ${row('iCloud Privé-relay blokkeren', 'Zorgt dat Apple-apparaten deze DNS-server blijven gebruiken.', 'block_private_relay')}</section>`)}`;
  document.querySelectorAll('[data-path]').forEach(b => b.addEventListener('click', () => toggleSwitch(b,
    on => post('api/settings', {server_id: srv.id, patch: patchFor(b.dataset.path, on)}), b.dataset.warn ? 'Weet je het zeker?' : null, b.dataset.warn)));
  const ms = $('[data-mode]'); if (ms) ms.addEventListener('change', async () => {
    try { toast((await post('api/settings', {server_id: srv.id, patch: {blocking_mode_settings: {blocking_mode: ms.value}}})).message); } catch (e) { toast(e.message, true); ms.value = mode; } });
  const tt = $('[data-ttl]'); if (tt) tt.addEventListener('change', async () => {
    try { toast((await post('api/settings', {server_id: srv.id, patch: {block_ttl_seconds: Math.max(0, Math.min(3600, +tt.value || 0))}})).message); } catch (e) { toast(e.message, true); } });
  document.querySelectorAll('[data-rules]').forEach(x => x.addEventListener('click', () => rulesModal(x.dataset.rules)));
}

async function viewLists(id){
  const [srv, cat] = await Promise.all([api('v1/dns_servers/' + encodeURIComponent(id)), api('v2/filter_lists')]);
  const on = new Set(((srv.settings || {}).filter_lists_settings || {}).filter_list?.filter(f => f.enabled).map(f => f.filter_id) || []);
  const items = (cat.items || []).map(f => ({...f, on: on.has(f.filter_id)})).sort((a, b) => (b.on - a.on) || String(a.name).localeCompare(String(b.name), LANG));
  const upd = t => t ? 'bijgewerkt ' + new Date(t).toLocaleDateString(LOCALE, {day: 'numeric', month: 'short'}) : '';
  $('#view').innerHTML = `<a class="back" href="#server/${esc(srv.id)}">← ${esc(srv.name)}</a><h1 class="ptitle">Blokkeerlijsten</h1><p class="psub">${on.size} van ${items.length} lijsten ingeschakeld op ${esc(srv.name)}</p>${roNote()}
    <section class="card setcard"><div class="fltools"><input class="search" type="search" id="fq" placeholder="Zoek blokkeerlijst" aria-label="Zoek blokkeerlijst"></div>
      ${items.map(f => `<div class="setrow flrow" data-n="${esc(String(f.name || '').toLowerCase())}"><div><div class="sl">${esc(f.name || f.filter_id)}</div>
        ${f.description ? `<div class="sh">${esc(f.description)}</div>` : ''}<div class="meta">${f.rules_count != null ? K(f.rules_count) + ' regels' : ''}${f.time_updated ? ', ' + upd(f.time_updated) : ''}${f.type === 'CUSTOM' && !HOME ? ', eigen lijst' : ''}</div></div>
        <button class="sw ${f.on ? 'on' : ''}" role="switch" aria-checked="${f.on}" aria-label="${esc(f.name)}" data-fid="${esc(f.filter_id)}" ${CFG.blocking ? '' : 'disabled'}></button></div>`).join('') || '<p class="empty">Er zijn geen blokkeerlijsten beschikbaar.</p>'}
    </section>`;
  $('#fq').addEventListener('input', e => { const v = e.target.value.trim().toLowerCase(); document.querySelectorAll('.flrow').forEach(r => r.hidden = v && !r.dataset.n.includes(v)); });
  document.querySelectorAll('[data-fid]').forEach(b => b.addEventListener('click', () => toggleSwitch(b, v => post('api/settings', {server_id: srv.id, filter_id: b.dataset.fid, enabled: v}))));
}

async function viewDevice(id){
  await loadMeta();
  const d = meta.devices.find(x => x.id === id); if (!d) throw new Error('Dit apparaat bestaat niet (meer).');
  const srv = meta.servers.find(x => x.id === d.dns_server_id), a = d.dns_addresses || {}, prot = (d.settings || {}).protection_enabled;
  const TYPES = {WINDOWS: 'Windows', ANDROID: 'Android', MAC: 'Mac', IOS: 'iPhone of iPad', LINUX: 'Linux', ROUTER: 'Router', SMART_TV: 'Smart-tv', GAME_CONSOLE: 'Spelcomputer', UNKNOWN: 'Overig'};
  const addr = [['DNS-over-HTTPS', a.dns_over_https_url], ['DNS-over-TLS', a.dns_over_tls_url], ['DNS-over-QUIC', a.dns_over_quic_url],
    ...((a.ip_addresses || []).map(ip => [ip.type === 'V6' ? 'IPv6-adres' : 'IPv4-adres', ip.ip_address]))].filter(x => x[1]);
  $('#view').innerHTML = `<a class="back" href="#dashboard">← Dashboard</a><h1 class="ptitle">${esc(d.name)}</h1><p class="psub">${esc(TYPES[d.device_type] || 'Apparaat')}${srv ? ' op ' + esc(srv.name) : ''}</p>${roNote()}
    <section class="card setcard"><h2>Bescherming</h2>
      <div class="setrow"><div><div class="sl">Bescherming voor dit apparaat</div><div class="sh">Blokkeringen van de server gelden voor dit apparaat.</div></div>
        <button class="sw ${prot ? 'on' : ''}" role="switch" aria-checked="${!!prot}" aria-label="Bescherming" id="dp" ${CFG.blocking ? '' : 'disabled'}></button></div>
      <div class="setrow"><div><div class="sl">Statistieken en querylog</div><div class="sh">Bekijk wat dit apparaat opvraagt.</div></div>
        <span><button class="linkbtn" ${goAttr('statistieken', {device: d.id, server: '', country: ''})}>Statistieken</button> &nbsp; <button class="linkbtn" ${goAttr('querylog', {device: d.id, server: '', country: ''})}>Querylog</button></span></div></section>
    <section class="card setcard"><h2>DNS-adressen</h2><p class="sh" style="margin:0 0 6px">Deze adressen stel je in op het apparaat zelf om het via AdGuard DNS te laten lopen.</p>
      ${addr.map(([l, v]) => `<div class="copyrow"><span class="lbl">${l}</span><code title="${esc(v)}">${esc(v)}</code><button class="linkbtn" data-copy="${esc(v)}">Kopiëren</button></div>`).join('') || '<p class="empty">Er zijn geen adressen bekend voor dit apparaat.</p>'}</section>`;
  $('#dp').addEventListener('click', e => toggleSwitch(e.currentTarget, on => post('api/device', {device_id: d.id, protection_enabled: on}),
    'Bescherming voor dit apparaat uitzetten?', 'Dit apparaat krijgt dan geen blokkades meer, ook niet van je blokkeerlijsten en gebruikersregels.'));
  document.querySelectorAll('[data-copy]').forEach(b => b.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast('Gekopieerd.'); }
    catch (e) { const r = document.createRange(); r.selectNodeContents(b.previousElementSibling); const s = getSelection(); s.removeAllRanges(); s.addRange(r); toast('Selecteer en kopieer het adres handmatig.'); } }));
}

/* ---------------- AdGuard Home: eigen pagina's ---------------- */
const N = n => Number(n || 0).toLocaleString(LOCALE);
const dis = () => CFG.blocking ? '' : 'disabled';
const hpost = body => post('api/home', body);
const swb = (attrs, on, label) => `<button class="sw ${on ? 'on' : ''}" role="switch" aria-checked="${!!on}" aria-label="${esc(label)}" ${attrs} ${dis()}></button>`;
const setrow = (label, help, ctrl) => `<div class="setrow"><div><div class="sl">${label}</div>${help ? `<div class="sh">${help}</div>` : ''}</div>${ctrl}</div>`;
const b64ok = s => /^[A-Za-z0-9+/=]+$/.test(String(s || ''));
const DAYMS = 864e5;
const RETAIN = {[DAYMS / 4]: '6 uur', [DAYMS]: '24 uur', [7 * DAYMS]: '7 dagen', [30 * DAYMS]: '30 dagen', [90 * DAYMS]: '90 dagen'};
const SVCGROUP = {ai: 'AI', cdn: 'CDN', dating: 'Dating', gambling: 'Gokken', gaming: 'Games', hosting: 'Hosting', messenger: 'Berichtenapps',
  privacy: 'Privacy', shopping: 'Winkelen', social_network: 'Sociale netwerken', software: 'Software', streaming: 'Streaming'};
const TAGS = {device_phone: 'Telefoon', device_tablet: 'Tablet', device_pc: 'Computer', device_laptop: 'Laptop', device_tv: 'Tv', device_nas: 'NAS',
  device_gameconsole: 'Spelcomputer', device_camera: 'Camera', device_printer: 'Printer', device_audio: 'Audio', device_securityalarm: 'Alarm', device_other: 'Overig'};
const HMODES = {default: 'Standaard', refused: 'Weigeren (REFUSED)', nxdomain: 'Bestaat niet (NXDOMAIN)', null_ip: 'Leeg adres (0.0.0.0)', custom_ip: 'Eigen IP-adres'};
const UPMODES = {'': 'Taakverdeling', load_balance: 'Taakverdeling', parallel: 'Parallelle verzoeken', fastest_addr: 'Snelste IP-adres'};

function subnav(base, subs, cur){
  return `<nav class="subnav" aria-label="Onderdelen">${Object.entries(subs).map(([k, v]) => `<a href="#${base}/${k}" ${k === cur ? 'aria-current="page"' : ''}>${v}</a>`).join('')}</nav>`;
}
function spark(arr, color){
  const a = (arr || []).map(Number), n = a.length, max = Math.max(1, ...a);
  if (n < 2) return '<svg class="spark" viewBox="0 0 100 40" preserveAspectRatio="none"></svg>';
  const pts = a.map((v, i) => [i / (n - 1) * 100, 38 - v / max * 34]);
  const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(2) + ' ' + p[1].toFixed(2)).join(' ');
  return `<svg class="spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"><path d="${line} L100 40 L0 40Z" fill="${color}" fill-opacity=".18"/><path d="${line}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}
function pctRow(name, n, total, color, go){
  const p = total ? n / total * 100 : 0;
  return `<tr><td class="nm">${go ? `<button class="nmlink" ${go} style="display:flex;align-items:center;gap:12px">${name}</button>` : `<span>${name}</span>`}</td>
    <td class="n">${N(n)}</td><td class="pc"><span class="pbar"><i style="width:${Math.max(1, Math.min(100, p))}%;background:${color}"></i></span><span class="pv">${dec1(p)}%</span></td></tr>`;
}
function homeBits(o){
  return `<span class="toggle"><span data-ptext>${o.protection_enabled ? 'Bescherming is ingeschakeld' : o.protection_disabled_duration ? 'Bescherming gepauzeerd, nog ' + Math.ceil(o.protection_disabled_duration / 60000) + ' min' : 'Bescherming is uitgeschakeld'}</span>
    ${swb('data-hprot', o.protection_enabled, 'Bescherming')}</span>
    <select class="pause" data-pause aria-label="Bescherming pauzeren" ${dis()} ${o.protection_enabled ? '' : 'hidden'}><option value="">Pauzeren…</option>
      <option value="30000">30 seconden</option><option value="60000">1 minuut</option><option value="600000">10 minuten</option><option value="3600000">1 uur</option><option value="28800000">8 uur</option></select>`;
}
function bindProtection(after){
  const b = $('[data-hprot]'); if (b) b.addEventListener('click', () => toggleSwitch(b, on => hpost({action: 'protection', enabled: on}),
    'Bescherming uitzetten?', 'Zonder bescherming worden advertenties, trackers en gevaarlijke domeinen niet meer geblokkeerd op je hele netwerk.', after));
  const p = $('[data-pause]'); if (p) p.addEventListener('change', async () => { if (!p.value) return;
    try { await hpost({action: 'protection', enabled: false, duration: +p.value}); toast('Bescherming gepauzeerd.'); route(); } catch (e) { toast(e.message, true); p.value = ''; } });
}

async function viewHomeDashboard(){
  const P = period();
  const [o, time] = await Promise.all([api('home/overview'), api('v1/stats/time', P)]);
  const s = o.series || {}, q = o.queries, period24 = o.time_units === 'days' ? `voor de afgelopen ${(s.dns_queries || []).length} dagen` : 'voor de afgelopen 24 uur';
  const tile = (n, label, arr, color, go, share) => `<button class="htile" ${go} style="--c:${color}"><span class="tn">${N(n)}</span>${share != null ? `<span class="tp">${dec1(share)}%</span>` : ''}
    ${spark(arr, color)}<span class="tl">${label}</span></button>`;
  const none = '<tr><td class="empty">Nog geen gegevens.</td></tr>';
  const sum = a => (a || []).reduce((x, y) => x + y.n, 0);
  const cl = o.top_clients || [], tq = o.top_queried || [], tb = o.top_blocked || [], up = o.upstreams || [], upms = o.upstream_ms || [];
  const st = [['DNS-verzoeken', q, '', {}], ['Geblokkeerd door filters', o.blocked, 'blk', {logStatus: 'b'}], ['Geblokkeerde malware en phishing', o.safebrowsing, '', null],
    ['Geblokkeerde websites voor volwassenen', o.parental, '', null], ['Veilig zoeken afgedwongen', o.safesearch, '', null]];
  $('#view').innerHTML = `
    <div class="hhead"><h1 class="ptitle">AdGuard Home <span class="ver">${esc(o.version || '')}</span></h1>
      <div class="hctl">${homeBits(o)}${CFG.home_url ? `<a href="${esc(CFG.home_url)}" target="_blank" rel="noopener">AdGuard Home openen</a>` : ''}</div></div>
    <section class="tiles4">
      ${tile(q, 'DNS-verzoeken', s.dns_queries, 'var(--blue)', goAttr('querylog', {}), null)}
      ${tile(o.blocked, 'Geblokkeerd door filters', s.blocked_filtering, 'var(--red)', goAttr('querylog', {logStatus: 'b'}), q ? o.blocked / q * 100 : 0)}
      ${tile(o.safebrowsing, 'Geblokkeerde malware en phishing', s.replaced_safebrowsing, 'var(--orange)', goAttr('querylog', {logStatus: 'b'}), q ? o.safebrowsing / q * 100 : 0)}
      ${tile(o.parental, 'Geblokkeerde websites voor volwassenen', s.replaced_parental, 'var(--purple)', goAttr('querylog', {logStatus: 'b'}), q ? o.parental / q * 100 : 0)}
    </section>
    <section class="card"><div class="cardhead"><h2>Verzoeken</h2><div class="right">Geblokkeerd <span class="blk">${N(o.blocked + o.safebrowsing + o.parental)}</span> (${pct(o.blocked + o.safebrowsing + o.parental, q)}%) van <span class="req">${N(q)}</span>, ${esc(period24)}</div></div>
      <div class="chart" id="chart"></div></section>
    <div class="grid2">
      <section class="card"><h2>Algemene statistieken</h2><p class="csub">${esc(period24)}</p><table class="list">
        ${st.map(([l, n, c, go]) => `<tr><td class="nm">${go ? `<button class="nmlink" ${goAttr('querylog', go)}>${l}</button>` : l}</td><td class="n ${c}">${N(n)}</td></tr>`).join('')}
        <tr><td class="nm">Gemiddelde verwerkingstijd</td><td class="n">${esc(dec1(o.avg_ms))} ms</td></tr></table></section>
      <section class="card"><div class="cardhead"><h2>Actiefste clients</h2><a class="right" href="#instellingen/clients" style="font-size:14px">Clients beheren</a></div><div class="lscroll"><table class="list">
        ${cl.map(c => pctRow(`${devIcon('')}<span>${esc(c.name || c.id)}${c.name ? `<span class="muted"> ${esc(c.id)}</span>` : ''}</span>`, c.n, q, 'var(--blue)', goAttr('querylog', {device: c.id}))).join('') || none}</table></div></section>
    </div>
    <div class="grid2">
      <section class="card"><h2>Meest opgevraagde domeinen</h2><div class="lscroll"><table class="list">
        ${tq.map(d => pctRow(`${favicon(d.domain)}<span class="dname">${esc(d.domain)}</span>`, d.n, q, 'var(--blue)', goAttr('querylog', {logSearch: d.domain}))).join('') || none}</table></div></section>
      <section class="card"><h2>Meest geblokkeerde domeinen</h2><div class="lscroll"><table class="list">
        ${tb.map(d => pctRow(`${favicon(d.domain)}<span class="dname">${esc(d.domain)}</span>`, d.n, o.blocked || 1, 'var(--red)', goAttr('querylog', {logSearch: d.domain, logStatus: 'b'}))).join('') || none}</table></div></section>
    </div>
    <div class="grid2">
      <section class="card"><div class="cardhead"><h2>Upstreams</h2><a class="right" href="#instellingen/dns" style="font-size:14px">DNS-instellingen</a></div><p class="csub">Antwoorden per upstream DNS-server</p><div class="lscroll"><table class="list">
        ${up.map(u => pctRow(`<span class="dname mono">${esc(u.addr)}</span>`, u.n, sum(up), 'var(--green)')).join('') || none}</table></div></section>
      <section class="card"><h2>Reactietijd upstreams</h2><p class="csub">Gemiddelde tijd tot een antwoord</p><div class="lscroll"><table class="list">
        ${upms.map(u => `<tr><td class="nm"><span class="dname mono">${esc(u.addr)}</span></td><td class="n">${esc(dec1(u.ms))} ms</td></tr>`).join('') || none}</table></div></section>
    </div>`;
  const draw = () => barChart($('#chart'), time.stats || [], 'blocked');
  draw(); window.__redraw = draw;
  bindProtection(() => route());
}

/* ---------------- filters ---------------- */
const FSUBS = {blokkeerlijsten: 'Blokkeerlijsten', toelatingslijsten: 'Toelatingslijsten', herschrijvingen: 'DNS-herschrijvingen', diensten: 'Geblokkeerde diensten', regels: 'Eigen filterregels'};
async function viewHomeFilters(sub){
  if (!FSUBS[sub]) sub = 'blokkeerlijsten';
  const head = `<h1 class="ptitle">Filters</h1>${subnav('filters', FSUBS, sub)}${roNote()}`;
  if (sub === 'blokkeerlijsten' || sub === 'toelatingslijsten') return homeLists(head, sub === 'toelatingslijsten');
  if (sub === 'herschrijvingen') return homeRewrites(head);
  if (sub === 'diensten') return homeServices(head);
  return homeRules(head);
}
async function homeLists(head, wl){
  const f = await api('home/filters'), items = wl ? f.allowlists : f.blocklists;
  const upd = t => t ? 'bijgewerkt ' + new Date(t).toLocaleString(LOCALE, {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) : 'nog niet bijgewerkt';
  const on = items.filter(x => x.enabled);
  $('#view').innerHTML = `${head}
    <section class="card setcard"><div class="cardhead"><div><h2>${wl ? 'Toelatingslijsten' : 'Blokkeerlijsten'}</h2>
      <p class="csub"><span>${wl ? 'Domeinen op deze lijsten worden nooit geblokkeerd.' : 'Domeinen op deze lijsten worden geblokkeerd.'}</span> <span>${on.length} van ${items.length} ingeschakeld, ${N(on.reduce((a, x) => a + x.rules_count, 0))} regels.</span></p></div>
      <div class="btnrow"><button class="btn" data-refresh ${dis()}>Nu bijwerken</button><button class="btn pri" data-addlist ${dis()}>Lijst toevoegen</button></div></div>
      ${items.map(x => `<div class="setrow flrow"><div class="grow"><div class="sl">${esc(x.name)}</div><div class="sh mono">${esc(x.url)}</div>
        <div class="meta">${N(x.rules_count)} regels, ${upd(x.last_updated)}</div></div>
        <div class="rowctl">${swb(`data-lurl="${esc(x.url)}"`, x.enabled, x.name)}<button class="xbtn" data-ldel="${esc(x.url)}" data-lname="${esc(x.name)}" aria-label="Lijst verwijderen" ${dis()}>×</button></div></div>`).join('')
        || `<p class="empty">Er zijn nog geen ${wl ? 'toelatingslijsten' : 'blokkeerlijsten'}.</p>`}
    </section>`;
  document.querySelectorAll('[data-lurl]').forEach(b => b.addEventListener('click', () => toggleSwitch(b, v => hpost({action: 'filter_toggle', url: b.dataset.lurl, whitelist: wl, enabled: v}))));
  document.querySelectorAll('[data-ldel]').forEach(b => b.addEventListener('click', async () => {
    if (!(await confirmBox('Lijst verwijderen?', `${b.dataset.lname} wordt uit AdGuard Home verwijderd.`, 'Verwijderen'))) return;
    try { toast((await hpost({action: 'filter_remove', url: b.dataset.ldel, whitelist: wl})).message); route(); } catch (e) { toast(e.message, true); } }));
  $('[data-refresh]').addEventListener('click', async e => { e.target.disabled = true;
    try { toast((await hpost({action: 'filters_refresh', whitelist: wl})).message); route(); } catch (er) { toast(er.message, true); e.target.disabled = false; } });
  $('[data-addlist]').addEventListener('click', () => {
    const m = modal(`<h3>${wl ? 'Toelatingslijst toevoegen' : 'Blokkeerlijst toevoegen'}</h3><p>Vul de naam en het adres van de lijst in. Lijsten vind je onder meer op adguard.com en filterlists.com.</p>
      <form class="mform" id="lf"><label>Naam<input name="name" data-focus required maxlength="200"></label><label>Adres<input name="url" type="url" required placeholder="https://"></label>
      <div class="btns"><button type="button" class="btn" data-close>Annuleren</button><button class="btn pri">Toevoegen</button></div></form>`);
    m.el.querySelector('#lf').addEventListener('submit', async ev => { ev.preventDefault(); const fd = new FormData(ev.target); ev.submitter && (ev.submitter.disabled = true);
      try { toast((await hpost({action: 'filter_add', whitelist: wl, name: fd.get('name'), url: fd.get('url')})).message); m.close(); route(); }
      catch (er) { toast(er.message, true); ev.submitter && (ev.submitter.disabled = false); } });
  });
}
async function homeRewrites(head){
  const rw = await api('home/rewrites');
  $('#view').innerHTML = `${head}
    <section class="card setcard"><h2>DNS-herschrijvingen</h2><p class="csub">Geef een domein een eigen antwoord, bijvoorbeeld een naam voor een apparaat in je netwerk. Een * aan het begin geldt voor alle subdomeinen.</p>
      ${CFG.blocking ? `<form class="inform" id="rf"><input name="domain" placeholder="${T('nas.thuis', 'nas.home')}" aria-label="Domein" required><input name="answer" placeholder="192.168.1.10" aria-label="Antwoord: IP-adres of domein" required><button class="btn pri">Toevoegen</button></form>` : ''}
      <table class="dtable rtable"><thead><tr><th>Domein</th><th>Antwoord</th><th></th></tr></thead><tbody>
      ${rw.map(r => `<tr><td class="mono">${esc(r.domain)}</td><td class="mono">${esc(r.answer)}</td><td class="act"><button class="xbtn" data-rdel='${esc(JSON.stringify([r.domain, r.answer]))}' aria-label="Verwijderen" ${dis()}>×</button></td></tr>`).join('')
        || '<tr><td colspan="3" class="empty">Er zijn nog geen herschrijvingen.</td></tr>'}</tbody></table></section>`;
  const f = $('#rf'); if (f) f.addEventListener('submit', async e => { e.preventDefault(); const fd = new FormData(f);
    try { toast((await hpost({action: 'rewrite_add', domain: fd.get('domain'), answer: fd.get('answer')})).message); route(); } catch (er) { toast(er.message, true); } });
  document.querySelectorAll('[data-rdel]').forEach(b => b.addEventListener('click', async () => { const [domain, answer] = JSON.parse(b.dataset.rdel);
    if (!(await confirmBox('Herschrijving verwijderen?', `${domain} → ${answer}`, 'Verwijderen'))) return;
    try { toast((await hpost({action: 'rewrite_delete', domain, answer})).message); route(); } catch (e) { toast(e.message, true); } }));
}
async function homeServices(head){
  const d = await api('home/services'), blocked = new Set(d.blocked);
  state.svcGroup = state.svcGroup || '';
  const draw = () => {
    const q = ($('#sq') || {value: ''}).value.trim().toLowerCase();
    const list = d.services.filter(s => (!state.svcGroup || s.group === state.svcGroup) && (!q || s.name.toLowerCase().includes(q)));
    $('#svc').innerHTML = list.map(s => `<div class="svc"><span class="si">${b64ok(s.icon) ? `<img src="data:image/svg+xml;base64,${s.icon}" alt="">` : ''}</span><span class="sn">${esc(s.name)}</span>
      ${swb(`data-svc="${esc(s.id)}"`, blocked.has(s.id), 'Blokkeer ' + s.name)}</div>`).join('') || '<p class="empty">Geen diensten gevonden.</p>';
    const cnt = () => $('#scount').textContent = T(`${blocked.size} geblokkeerd.`, `${blocked.size} blocked.`);
    cnt();
    document.querySelectorAll('[data-svc]').forEach(b => b.addEventListener('click', () => toggleSwitch(b, async on => {
      on ? blocked.add(b.dataset.svc) : blocked.delete(b.dataset.svc);
      try { const r = await hpost({action: 'services', ids: [...blocked]}); cnt(); return r; }
      catch (e) { on ? blocked.delete(b.dataset.svc) : blocked.add(b.dataset.svc); throw e; } })));
  };
  $('#view').innerHTML = `${head}
    <section class="card setcard"><div class="cardhead"><div><h2>Geblokkeerde diensten</h2><p class="csub"><span>Blokkeer complete diensten en apps met één schakelaar.</span> <span id="scount"></span></p></div>
      <div class="btnrow"><button class="btn" data-unall ${dis()}>Alles deblokkeren</button></div></div>
      <div class="chips">${['', ...d.groups].map(g => `<button class="chip" data-grp="${esc(g)}" aria-pressed="${g === state.svcGroup}">${esc(g ? SVCGROUP[g] || g : 'Alle')}</button>`).join('')}</div>
      <input class="search" type="search" id="sq" placeholder="Zoek dienst" aria-label="Zoek dienst">
      <div class="svcgrid" id="svc"></div></section>`;
  draw();
  $('#sq').addEventListener('input', draw);
  document.querySelectorAll('[data-grp]').forEach(c => c.addEventListener('click', () => { state.svcGroup = c.dataset.grp;
    document.querySelectorAll('[data-grp]').forEach(x => x.setAttribute('aria-pressed', x === c)); draw(); }));
  $('[data-unall]').addEventListener('click', async () => { if (!blocked.size) return;
    if (!(await confirmBox('Alles deblokkeren?', `${blocked.size} diensten worden weer toegestaan.`, 'Deblokkeren'))) return;
    try { toast((await hpost({action: 'services', ids: []})).message); route(); } catch (e) { toast(e.message, true); } });
}
async function homeRules(head){
  const f = await api('home/filters');
  $('#view').innerHTML = `${head}
    <section class="card setcard"><h2>Eigen filterregels</h2><p class="csub">Eén regel per regel. <code>||domein^</code> blokkeert een domein, <code>@@||domein^</code> staat het toe, en een regel die met <code>!</code> begint is een opmerking.</p>
      <textarea class="code" id="rt" rows="14" spellcheck="false" aria-label="Eigen filterregels" ${dis()}>${esc(f.user_rules.join('\n'))}</textarea>
      <div class="btnrow end"><span class="muted" id="rc">${f.user_rules.length} regels</span><button class="btn pri" id="rs" ${dis()}>Opslaan</button></div></section>
    <section class="card setcard"><h2>Filtering testen</h2><p class="csub">Kijk wat AdGuard Home met een domein doet en welke regel daarvoor zorgt.</p>
      <form class="inform" id="ct"><input name="name" placeholder="${T('voorbeeld.nl', 'example.com')}" aria-label="Domein" required><button class="btn pri">Testen</button></form><div id="cr"></div></section>`;
  const t = $('#rt'); t.addEventListener('input', () => $('#rc').textContent = t.value.split('\n').filter(x => x.trim()).length + ' regels');
  $('#rs').addEventListener('click', async e => { e.target.disabled = true;
    try { toast((await hpost({action: 'rules_set', rules: t.value.split('\n')})).message); meta.servers = []; } catch (er) { toast(er.message, true); } e.target.disabled = false; });
  $('#ct').addEventListener('submit', async e => { e.preventDefault(); const name = new FormData(e.target).get('name');
    try { const r = await api('home/check', {name});
      const R = {NotFilteredNotFound: ['p', 'Niet gefilterd', 'Er is geen regel voor dit domein.'], NotFilteredWhiteList: ['a', 'Toegestaan', 'Een toelatingsregel staat dit domein toe.'],
        FilteredBlackList: ['b', 'Geblokkeerd', 'Een blokkeerregel blokkeert dit domein.'], FilteredSafeBrowsing: ['b', 'Geblokkeerd', 'Geblokkeerd door Veilig surfen.'],
        FilteredParental: ['b', 'Geblokkeerd', 'Geblokkeerd door Ouderlijk toezicht.'], FilteredBlockedService: ['b', 'Geblokkeerd', 'Geblokkeerd als dienst ' + r.service_name + '.'],
        FilteredSafeSearch: ['m', 'Gewijzigd', 'Veilig zoeken stuurt dit domein om.'], Rewrite: ['m', 'Gewijzigd', 'Een DNS-herschrijving geeft een eigen antwoord.'],
        RewriteEtcHosts: ['m', 'Gewijzigd', 'Het hosts-bestand geeft een eigen antwoord.'], RewriteRule: ['m', 'Gewijzigd', 'Een herschrijfregel geeft een eigen antwoord.']}[r.reason] || ['p', r.reason, ''];
      $('#cr').innerHTML = `<div class="checkres"><span class="pill ${R[0]}">${esc(R[1])}</span> <b>${esc(name)}</b><p>${esc(R[2])}</p>${r.rules.map(x => `<code>${esc(x)}</code>`).join(' ')}</div>`; }
    catch (er) { $('#cr').innerHTML = `<p class="empty">${esc(er.message)}</p>`; } });
}

/* ---------------- instellingen ---------------- */
const SSUBS = {algemeen: 'Algemeen', dns: 'DNS', clients: 'Clients', dhcp: 'DHCP', encryptie: 'Versleuteling'};
async function viewHomeSettings(sub){
  if (!SSUBS[sub]) sub = 'algemeen';
  const head = `<h1 class="ptitle">Instellingen</h1>${subnav('instellingen', SSUBS, sub)}${roNote()}`;
  return {algemeen: homeGeneral, dns: homeDns, clients: homeClients, dhcp: homeDhcp, encryptie: homeTls}[sub](head);
}
const retainSel = (attr, cur, list) => `<select ${attr} ${dis()} aria-label="Bewaartijd">${list.map(v => `<option value="${v}" ${+cur === v ? 'selected' : ''}>${RETAIN[v]}</option>`).join('')}</select>`;
async function homeGeneral(head){
  const g = await api('home/general'), ss = g.safesearch || {};
  const ENG = {google: 'Google', bing: 'Bing', duckduckgo: 'DuckDuckGo', ecosia: 'Ecosia', yandex: 'Yandex', pixabay: 'Pixabay', youtube: 'YouTube'};
  $('#view').innerHTML = `${head}
    <section class="card setcard"><h2>Bescherming</h2>
      ${setrow('Bescherming', 'Blokkeert advertenties, trackers en gevaarlijke domeinen voor je hele netwerk.', `<div class="rowctl">${homeBits(g)}</div>`)}
      ${setrow('Veilig surfen', 'Blokkeert bekende phishing- en malwaredomeinen.', swb('data-g="safebrowsing"', g.safebrowsing, 'Veilig surfen'))}
      ${setrow('Ouderlijk toezicht', 'Blokkeert websites voor volwassenen.', swb('data-g="parental"', g.parental, 'Ouderlijk toezicht'))}
      ${setrow('Veilig zoeken afdwingen', 'Zoekmachines tonen dan alleen veilige resultaten.', swb('data-ss="enabled"', ss.enabled, 'Veilig zoeken'))}
      <div class="engines" ${ss.enabled ? '' : 'hidden'}>${Object.entries(ENG).filter(([k]) => k in ss).map(([k, v]) => `<label class="chk"><input type="checkbox" data-ss="${k}" ${ss[k] ? 'checked' : ''} ${dis()}> ${v}</label>`).join('')}</div></section>
    <section class="card setcard"><h2>Querylog</h2>
      ${setrow('Querylog bijhouden', 'Bewaart elk DNS-verzoek met tijd, client en resultaat.', swb('data-ql="enabled"', g.querylog.enabled, 'Querylog bijhouden'))}
      ${setrow('Bewaartijd', 'Hoe lang verzoeken in de querylog blijven.', retainSel('data-ql="interval"', g.querylog.interval, [DAYMS / 4, DAYMS, 7 * DAYMS, 30 * DAYMS, 90 * DAYMS]))}
      ${setrow('IP-adressen anonimiseren', 'Het laatste deel van elk IP-adres wordt niet bewaard.', swb('data-ql="anonymize_client_ip"', g.querylog.anonymize_client_ip, 'IP-adressen anonimiseren'))}
      ${setrow('Querylog wissen', 'Verwijdert alle verzoeken uit de querylog.', `<button class="btn dng" data-clear="querylog_clear" ${dis()}>Wissen</button>`)}</section>
    <section class="card setcard"><h2>Statistieken</h2>
      ${setrow('Statistieken bijhouden', 'Tellingen voor het dashboard en de statistieken.', swb('data-sc="enabled"', g.stats.enabled, 'Statistieken bijhouden'))}
      ${setrow('Bewaartijd', 'Over welke periode de statistieken gaan.', retainSel('data-sc="interval"', g.stats.interval, [DAYMS, 7 * DAYMS, 30 * DAYMS, 90 * DAYMS]))}
      ${setrow('Statistieken wissen', 'Zet alle tellingen terug naar nul.', `<button class="btn dng" data-clear="stats_reset" ${dis()}>Wissen</button>`)}</section>`;
  bindProtection(() => route());
  document.querySelectorAll('[data-g]').forEach(b => b.addEventListener('click', () => toggleSwitch(b, on => hpost({action: b.dataset.g, enabled: on}),
    b.dataset.g === 'safebrowsing' ? 'Weet je het zeker?' : null, 'Phishing- en malwaredomeinen worden dan niet meer geblokkeerd.')));
  const ssb = $('[data-ss="enabled"]'); ssb.addEventListener('click', () => toggleSwitch(ssb, on => hpost({action: 'safesearch', values: {enabled: on}}), null, null,
    on => $('.engines').hidden = !on));
  document.querySelectorAll('input[data-ss]').forEach(c => c.addEventListener('change', async () => {
    try { toast((await hpost({action: 'safesearch', values: {[c.dataset.ss]: c.checked}})).message); } catch (e) { c.checked = !c.checked; toast(e.message, true); } }));
  const cfg = (sel, action) => {
    document.querySelectorAll(`button[${sel}]`).forEach(b => b.addEventListener('click', () => toggleSwitch(b, on => hpost({action, [b.getAttribute(sel)]: on}))));
    document.querySelectorAll(`select[${sel}]`).forEach(s => s.addEventListener('change', async () => {
      try { toast((await hpost({action, interval: +s.value})).message); } catch (e) { toast(e.message, true); } }));
  };
  cfg('data-ql', 'querylog_config'); cfg('data-sc', 'stats_config');
  document.querySelectorAll('[data-clear]').forEach(b => b.addEventListener('click', async () => {
    const ql = b.dataset.clear === 'querylog_clear';
    if (!(await confirmBox(ql ? 'Querylog wissen?' : 'Statistieken wissen?', ql ? 'Alle verzoeken in de querylog worden verwijderd. Dit kun je niet ongedaan maken.' : 'Alle tellingen gaan terug naar nul. Dit kun je niet ongedaan maken.', 'Wissen'))) return;
    try { toast((await hpost({action: b.dataset.clear})).message); } catch (e) { toast(e.message, true); } }));
}
async function homeDns(head){
  const d = await api('home/dns');
  const ta = (k, rows, ph) => `<textarea class="code" data-k="${k}" rows="${rows}" spellcheck="false" placeholder="${esc(ph)}" ${dis()}>${esc((d[k] || []).join('\n'))}</textarea>`;
  const num = (k, max) => `<input type="number" min="0" max="${max}" data-k="${k}" value="${esc(d[k] ?? 0)}" ${dis()}>`;
  const chk = (k, label) => setrow(label[0], label[1], swb(`data-kb="${k}"`, d[k], label[0]));
  $('#view').innerHTML = `${head}
    <section class="card setcard" data-card><h2>Upstream DNS-servers</h2><p class="csub">Waar AdGuard Home verzoeken naartoe stuurt die niet geblokkeerd worden. Eén adres per regel, bijvoorbeeld <code>https://dns.quad9.net/dns-query</code> of <code>9.9.9.9</code>.</p>
      ${ta('upstream_dns', 6, 'https://dns.quad9.net/dns-query')}
      ${setrow('Modus', 'Hoe AdGuard Home de upstreams gebruikt.', `<select data-k="upstream_mode" ${dis()} aria-label="Modus">${['load_balance', 'parallel', 'fastest_addr'].map(m => `<option value="${m}" ${(d.upstream_mode || 'load_balance') === m ? 'selected' : ''}>${UPMODES[m]}</option>`).join('')}</select>`)}
      <h3 class="sub">Fallback-DNS-servers</h3><p class="csub">Worden gebruikt als geen enkele upstream antwoordt.</p>${ta('fallback_dns', 3, '')}
      <h3 class="sub">Bootstrap-DNS-servers</h3><p class="csub">Gewone IP-adressen om de namen van versleutelde upstreams op te zoeken.</p>${ta('bootstrap_dns', 3, '9.9.9.10')}
      <div id="tr"></div><div class="btnrow end"><button class="btn" id="tu" ${dis()}>Upstreams testen</button><button class="btn pri" data-save ${dis()}>Opslaan</button></div></section>
    <section class="card setcard" data-card><h2>DNS-server</h2>
      ${setrow('Limiet per client', 'Maximaal aantal verzoeken per seconde per client. 0 is geen limiet.', num('ratelimit', 100000))}
      ${setrow('Blokkeermodus', 'Wat een client terugkrijgt als een domein geblokkeerd is.', `<select data-k="blocking_mode" ${dis()} aria-label="Blokkeermodus">${Object.entries(HMODES).filter(([k]) => k !== 'custom_ip' || d.blocking_mode === 'custom_ip').map(([k, v]) => `<option value="${k}" ${k === d.blocking_mode ? 'selected' : ''}>${v}</option>`).join('')}</select>`)}
      ${setrow('Bewaartijd geblokkeerde antwoorden', 'Hoeveel seconden een client een blokkade onthoudt (TTL).', num('blocked_response_ttl', 86400))}
      ${chk('dnssec_enabled', ['DNSSEC', 'Vraagt upstreams om ondertekende antwoorden.'])}
      ${chk('disable_ipv6', ['IPv6-adressen niet opzoeken', 'Beantwoordt verzoeken om IPv6-adressen (AAAA) leeg.'])}
      ${chk('edns_cs_enabled', ['EDNS Client Subnet', 'Stuurt een deel van je IP-adres mee, voor dichterbij gelegen servers.'])}
      <div class="btnrow end"><button class="btn pri" data-save ${dis()}>Opslaan</button></div></section>
    <section class="card setcard" data-card><h2>Cache</h2>
      ${chk('cache_enabled', ['Cache gebruiken', 'Onthoudt antwoorden zodat ze sneller terugkomen.'])}
      ${setrow('Grootte van de cache', 'In bytes. 4194304 is 4 MB.', num('cache_size', 1073741824))}
      ${chk('cache_optimistic', ['Optimistische cache', 'Geeft een verlopen antwoord direct terug en vernieuwt het daarna.'])}
      <div class="btnrow end"><button class="btn pri" data-save ${dis()}>Opslaan</button></div></section>`;
  document.querySelectorAll('[data-kb]').forEach(b => b.addEventListener('click', () => { if (b.disabled) return; const on = !b.classList.contains('on');
    b.classList.toggle('on', on); b.setAttribute('aria-checked', on); }));
  document.querySelectorAll('[data-save]').forEach(btn => btn.addEventListener('click', async () => {
    const card = btn.closest('[data-card]'), values = {};
    card.querySelectorAll('[data-k]').forEach(el => { values[el.dataset.k] = el.tagName === 'TEXTAREA' ? el.value.split('\n').map(x => x.trim()).filter(Boolean) : el.type === 'number' ? +el.value : el.value; });
    card.querySelectorAll('[data-kb]').forEach(el => values[el.dataset.kb] = el.classList.contains('on'));
    btn.disabled = true;
    try { toast((await hpost({action: 'dns', values})).message); } catch (e) { toast(e.message, true); } btn.disabled = false; }));
  $('#tu').addEventListener('click', async e => { e.target.disabled = true; const v = k => $(`[data-k="${k}"]`).value.split('\n').map(x => x.trim()).filter(Boolean);
    try { const r = await hpost({action: 'test_upstream', upstream_dns: v('upstream_dns'), bootstrap_dns: v('bootstrap_dns')});
      $('#tr').innerHTML = `<ul class="tres">${Object.entries(r.result || {}).map(([a, s]) => `<li><span class="mono">${esc(a)}</span><span class="${s === 'OK' ? 'ok' : 'bad'}">${s === 'OK' ? 'Werkt' : esc(s)}</span></li>`).join('')}</ul>`; }
    catch (er) { toast(er.message, true); } e.target.disabled = false; });
}
async function homeClients(head){
  const c = await api('home/clients');
  const summary = x => x.use_global_settings ? 'Algemene instellingen' : [x.filtering_enabled && 'filtering', x.safebrowsing_enabled && 'veilig surfen', x.parental_enabled && 'ouderlijk toezicht', x.safe_search && 'veilig zoeken'].filter(Boolean).join(', ').replace(/^./, c => c.toUpperCase()) || 'Geen bescherming';
  const tag = x => (x.tags || []).map(t => TAGS[t]).filter(Boolean)[0] || '';
  const known = new Set(c.clients.flatMap(x => x.ids || []));
  $('#view').innerHTML = `${head}
    <section class="card setcard"><div class="cardhead"><div><h2>Vaste clients</h2><p class="csub">Apparaten met een eigen naam en eventueel eigen instellingen.</p></div>
      <div class="btnrow"><button class="btn pri" data-cadd ${dis()}>Client toevoegen</button></div></div>
      <table class="dtable"><thead><tr><th>Naam</th><th>Herkend aan</th><th>Instellingen</th><th></th></tr></thead><tbody>
      ${c.clients.map((x, i) => `<tr><td><b>${esc(x.name)}</b>${tag(x) ? `<div class="muted">${esc(tag(x))}</div>` : ''}</td><td class="mono wrap">${(x.ids || []).map(esc).join('<br>')}</td>
        <td class="wrap">${esc(summary(x))}</td><td class="act"><button class="linkbtn" ${goAttr('querylog', {device: (x.ids || [])[0]})}>Querylog</button>
        <button class="linkbtn" data-cedit="${i}" ${dis()}>Wijzigen</button><button class="xbtn" data-cdel="${esc(x.name)}" aria-label="Verwijderen" ${dis()}>×</button></td></tr>`).join('')
        || '<tr><td colspan="4" class="empty">Er zijn nog geen vaste clients.</td></tr>'}</tbody></table></section>
    <section class="card setcard"><h2>Gevonden clients</h2><p class="csub">Adressen die AdGuard Home zelf heeft gevonden, bijvoorbeeld via DHCP, rDNS of het hosts-bestand.</p>
      <table class="dtable"><thead><tr><th>IP-adres</th><th>Naam</th><th>Bron</th><th></th></tr></thead><tbody>
      ${c.auto.filter(x => !known.has(x.ip)).map(x => `<tr><td class="mono">${esc(x.ip)}</td><td>${esc(x.name)}</td><td>${esc(x.source)}</td>
        <td class="act"><button class="linkbtn" data-cfrom='${esc(JSON.stringify(x))}' ${dis()}>Toevoegen als client</button></td></tr>`).join('')
        || '<tr><td colspan="4" class="empty">Er zijn geen andere clients gevonden.</td></tr>'}</tbody></table></section>`;
  const form = (x, isNew) => {
    const own = !x.use_global_settings;
    const m = modal(`<h3>${isNew ? 'Client toevoegen' : 'Client wijzigen'}</h3>
      <form class="mform" id="cf"><label>Naam<input name="name" value="${esc(x.name || '')}" required maxlength="100" data-focus></label>
      <label>Herkend aan<textarea name="ids" rows="3" class="code" placeholder="192.168.1.20" required>${esc((x.ids || []).join('\n'))}</textarea><span class="hint">IP-adres, reeks (192.168.1.0/24), MAC-adres of ClientID. Eén per regel.</span></label>
      <label>Soort apparaat<select name="tag"><option value="">Geen</option>${Object.entries(TAGS).map(([k, v]) => `<option value="${k}" ${(x.tags || []).includes(k) ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <label class="chk"><input type="checkbox" name="global" ${own ? '' : 'checked'}> Algemene instellingen gebruiken</label>
      <div class="own" ${own ? '' : 'hidden'}>${[['filtering_enabled', 'Filtering'], ['safebrowsing_enabled', 'Veilig surfen'], ['parental_enabled', 'Ouderlijk toezicht'], ['safe_search', 'Veilig zoeken']]
        .map(([k, l]) => `<label class="chk"><input type="checkbox" name="${k}" ${x[k] ? 'checked' : ''}> ${l}</label>`).join('')}</div>
      <label class="chk"><input type="checkbox" name="ignore_querylog" ${x.ignore_querylog ? 'checked' : ''}> Niet in de querylog</label>
      <label class="chk"><input type="checkbox" name="ignore_statistics" ${x.ignore_statistics ? 'checked' : ''}> Niet in de statistieken</label>
      <div class="btns"><button type="button" class="btn" data-close>Annuleren</button><button class="btn pri">${isNew ? 'Toevoegen' : 'Opslaan'}</button></div></form>`);
    const f = m.el.querySelector('#cf');
    f.global.addEventListener('change', () => f.querySelector('.own').hidden = f.global.checked);
    f.addEventListener('submit', async e => { e.preventDefault();
      const data = {name: f.name.value.trim(), ids: f.ids.value.split('\n').map(s => s.trim()).filter(Boolean),
        tags: [...(x.tags || []).filter(t => !TAGS[t]), ...(f.tag.value ? [f.tag.value] : [])], use_global_settings: f.global.checked,
        ignore_querylog: f.ignore_querylog.checked, ignore_statistics: f.ignore_statistics.checked};
      ['filtering_enabled', 'safebrowsing_enabled', 'parental_enabled', 'safe_search'].forEach(k => data[k] = f[k].checked);
      try { toast((await hpost(isNew ? {action: 'client_add', data} : {action: 'client_update', name: x.name, data})).message); m.close(); meta.servers = []; meta.devices = []; route(); }
      catch (er) { toast(er.message, true); } });
  };
  $('[data-cadd]').addEventListener('click', () => form({use_global_settings: true}, true));
  document.querySelectorAll('[data-cedit]').forEach(b => b.addEventListener('click', () => form(c.clients[+b.dataset.cedit], false)));
  document.querySelectorAll('[data-cfrom]').forEach(b => b.addEventListener('click', () => { const a = JSON.parse(b.dataset.cfrom); form({name: a.name || a.ip, ids: [a.ip], use_global_settings: true}, true); }));
  document.querySelectorAll('[data-cdel]').forEach(b => b.addEventListener('click', async () => {
    if (!(await confirmBox('Client verwijderen?', `${b.dataset.cdel} wordt verwijderd. Het apparaat blijft gewoon werken met de algemene instellingen.`, 'Verwijderen'))) return;
    try { toast((await hpost({action: 'client_delete', name: b.dataset.cdel})).message); meta.servers = []; meta.devices = []; route(); } catch (e) { toast(e.message, true); } }));
}
async function homeDhcp(head){
  const d = await api('home/dhcp'), v4 = d.v4 || {};
  const exp = t => t ? new Date(t).toLocaleString(LOCALE, {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) : '';
  const tbl = (rows, withExp) => `<table class="dtable"><thead><tr><th>IP-adres</th><th>Naam</th><th>MAC-adres</th>${withExp ? '<th>Verloopt</th>' : ''}</tr></thead><tbody>
    ${rows.map(l => `<tr><td class="mono">${esc(l.ip)}</td><td>${esc(l.hostname)}</td><td class="mono">${esc(l.mac)}</td>${withExp ? `<td>${esc(exp(l.expires))}</td>` : ''}</tr>`).join('')
      || `<tr><td colspan="${withExp ? 4 : 3}" class="empty">Geen.</td></tr>`}</tbody></table>`;
  $('#view').innerHTML = `${head}
    <section class="card setcard"><h2>DHCP-server</h2>
      ${setrow('Status', d.enabled ? 'AdGuard Home deelt IP-adressen uit in je netwerk.' : 'Uit. Je router deelt de IP-adressen uit.', `<span class="pill ${d.enabled ? 'a' : 'p'}">${d.enabled ? 'Aan' : 'Uit'}</span>`)}
      ${d.enabled ? setrow('Netwerkinterface', '', `<span class="mono">${esc(d.interface)}</span>`) + setrow('Gateway', '', `<span class="mono">${esc(v4.gateway_ip || '')}</span>`)
        + setrow('Reeks', '', `<span class="mono">${esc((v4.range_start || '') + ' – ' + (v4.range_end || ''))}</span>`) : ''}
      <p class="ro">De DHCP-server stel je in AdGuard Home zelf in, omdat een fout hier je hele netwerk kan raken.${CFG.home_url ? ` <a href="${esc(CFG.home_url)}/#dhcp" target="_blank" rel="noopener">DHCP-instellingen openen</a>` : ''}</p></section>
    ${d.enabled ? `<section class="card setcard"><h2>Uitgedeelde adressen</h2>${tbl(d.leases, true)}</section><section class="card setcard"><h2>Vaste adressen</h2>${tbl(d.static_leases, false)}</section>` : ''}`;
}
async function homeTls(head){
  const t = await api('home/tls'), ok = t.valid_cert && t.valid_key && t.valid_pair;
  const until = t.not_after && !/^0001/.test(t.not_after) ? new Date(t.not_after).toLocaleDateString(LOCALE, {day: 'numeric', month: 'long', year: 'numeric'}) : '';
  $('#view').innerHTML = `${head}
    <section class="card setcard"><h2>Versleuteling</h2>
      ${setrow('Status', t.enabled ? 'AdGuard Home is bereikbaar via HTTPS, DNS-over-HTTPS, DNS-over-TLS en DNS-over-QUIC.' : 'Uit. Clients gebruiken gewone, onversleutelde DNS.', `<span class="pill ${t.enabled ? 'a' : 'p'}">${t.enabled ? 'Aan' : 'Uit'}</span>`)}
      ${t.enabled ? setrow('Servernaam', '', `<span class="mono">${esc(t.server_name || '')}</span>`)
        + setrow('Certificaat', t.warning_validation ? esc(t.warning_validation) : until ? 'Geldig tot ' + esc(until) + '.' : '', `<span class="pill ${ok ? 'a' : 'b'}">${ok ? 'Geldig' : 'Probleem'}</span>`)
        + setrow('Poorten', '', `<span class="mono">HTTPS ${esc(t.port_https)}, DoT ${esc(t.port_dns_over_tls)}, DoQ ${esc(t.port_dns_over_quic)}</span>`)
        + setrow('Gewone DNS', '', `<span class="pill ${t.serve_plain_dns ? 'a' : 'p'}">${t.serve_plain_dns ? 'Aan' : 'Uit'}</span>`) : ''}
      <p class="ro">Certificaten en poorten stel je in AdGuard Home zelf in.${CFG.home_url ? ` <a href="${esc(CFG.home_url)}/#encryption" target="_blank" rel="noopener">Versleuteling openen</a>` : ''}</p></section>`;
}

/* ---------------- routering ---------------- */
const VIEWS = {dashboard: viewDashboard, statistieken: viewStats, bedrijven: viewCompanies, querylog: viewLog, server: viewServer, lijsten: viewLists, apparaat: viewDevice,
  filters: viewHomeFilters, instellingen: viewHomeSettings};
const PARENT = {server: 'dashboard', lijsten: 'dashboard', apparaat: 'dashboard'};
async function route(){
  clearTimeout(timer); clearInterval(logTimer);
  let [name, arg] = decodeURIComponent((location.hash || '#dashboard').slice(1)).split('/');
  if (HOME && (name === 'bedrijven' || name === 'apparaat')) name = 'dashboard';
  if (HOME && name === 'server') [name, arg] = ['instellingen', 'algemeen'];
  if (HOME && name === 'lijsten') [name, arg] = ['filters', 'blokkeerlijsten'];
  if (!HOME && (name === 'filters' || name === 'instellingen')) name = 'dashboard';
  const fn = VIEWS[name] || viewDashboard;
  const navName = PARENT[name] || (VIEWS[name] ? name : 'dashboard');
  document.querySelectorAll('.nav a').forEach(a => { if (a.getAttribute('href') === '#' + navName) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  window.__redraw = null;
  if (!$('#view').innerHTML) $('#view').innerHTML = '<p class="empty">Gegevens laden…</p>';
  try { showDemo(); await fn(arg); }
  catch (e) { showError(e); }
  if (['dashboard', 'statistieken', 'bedrijven'].includes(navName) && !PARENT[name]) timer = setTimeout(route, CFG.refresh * 1000);
}
window.addEventListener('hashchange', () => { $('#view').innerHTML = ''; route(); window.scrollTo(0, 0); });
let rz; window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => window.__redraw && window.__redraw(), 120); });
route();
</script>
</body>
</html>
"""


def _i18n_js():
    """De vertaallaag (Nederlands of Engels, volgt de taal van Home Assistant) met de Engelse teksten."""
    import i18n_en
    with open(os.path.join(HERE, "dom_i18n.js"), encoding="utf-8") as f:
        js = f.read()
    return js.replace("__I18N_EXACT__", json.dumps(i18n_en.EXACT, ensure_ascii=False)).replace("__I18N_PATTERNS__", json.dumps(i18n_en.PATTERNS, ensure_ascii=False))


PAGE = PAGE.replace("__I18N__", _i18n_js())


def main():
    if ALLOWED_USERS:
        print("Toegestane gebruikers: " + ", ".join(ALLOWED_USERS), flush=True)
    else:
        print("Toegestane gebruikers: iedereen (de lijst is leeg).", flush=True)
    server = ThreadingHTTPServer((LISTEN, PORT), Handler)
    shown = "localhost" if LISTEN in ("127.0.0.1", "0.0.0.0") else LISTEN
    print(f"AdGuard-dashboard draait op http://{shown}:{PORT}")
    if DEMO:
        print("Voorbeeldgegevens (demo).")
    elif HOME:
        print(f"Bron: AdGuard Home op {agh_home.CFG['url']}.")
        try:
            print(f"Verbonden met AdGuard Home {agh_home.get('status').get('version', '')}.", flush=True)
        except agh_home.HomeError as e:
            print(f"Let op: {e}", flush=True)
    else:
        print("Bron: AdGuard DNS (cloud), via je API-key.")
    print("Stoppen met Ctrl+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
