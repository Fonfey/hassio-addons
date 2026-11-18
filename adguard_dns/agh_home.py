"""AdGuard Home als bron voor het dashboard.

Het dashboard is gebouwd op de API van AdGuard DNS (cloud). Deze module beantwoordt dezelfde
paden met gegevens uit de API van AdGuard Home, zodat de pagina's ongewijzigd kunnen blijven.
Wat AdGuard Home niet kent (bedrijven, landen, categorieën, abonnement) wordt niet geleverd;
de pagina verbergt die onderdelen in de Home-modus.
"""
import base64
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

SERVER_ID = "home"
CACHE, LOCK = {}, threading.Lock()
CFG = {"url": "", "auth": ""}


def configure(url, username, password):
    url = str(url or "").strip().rstrip("/")
    if url and not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    CFG["url"] = url
    CFG["auth"] = "Basic " + base64.b64encode(f"{username or ''}:{password or ''}".encode()).decode() if username else ""


class HomeError(Exception):
    pass


def _request(method, path, body=None, timeout=15):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json", "User-Agent": "ha-adguard-dashboard"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if CFG["auth"]:
        headers["Authorization"] = CFG["auth"]
    req = urllib.request.Request(CFG["url"] + "/control/" + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise HomeError("AdGuard Home weigert de gebruikersnaam of het wachtwoord. Controleer ze in de instellingen van de app.")
        if e.code == 404:
            raise HomeError("AdGuard Home kent deze functie niet. Werk AdGuard Home bij naar de nieuwste versie.")
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace").strip()[:200]
        except Exception:
            pass
        raise HomeError(f"AdGuard Home gaf een fout: HTTP {e.code}." + (f" {detail}" if detail else ""))
    except urllib.error.URLError as e:
        raise HomeError(f"AdGuard Home is niet bereikbaar op {CFG['url']} ({e.reason}). Controleer het adres in de instellingen van de app.")
    except (TimeoutError, OSError) as e:
        raise HomeError(f"AdGuard Home is niet bereikbaar op {CFG['url']} ({e}).")
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {"text": raw}


def get(path, ttl=10):
    with LOCK:
        hit = CACHE.get(path)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    res = _request("GET", path)
    with LOCK:
        CACHE[path] = (time.time(), res)
        if len(CACHE) > 200:
            for k in sorted(CACHE, key=lambda k: CACHE[k][0])[:100]:
                CACHE.pop(k, None)
    return res


def post(path, body=None, method="POST"):
    res = _request(method, path, body)
    with LOCK:
        CACHE.clear()
    return res


# ---------------------------------------------------------------- vertalen naar de vorm van AdGuard DNS
MODE_TO_CLOUD = {"default": "NONE", "refused": "REFUSED", "nxdomain": "NXDOMAIN", "null_ip": "NULL_IP", "custom_ip": "CUSTOM_IP"}
MODE_TO_HOME = {v: k for k, v in MODE_TO_CLOUD.items()}
TAG_TYPE = {"device_phone": "ANDROID", "device_tablet": "IOS", "device_tv": "SMART_TV", "device_gameconsole": "GAME_CONSOLE",
            "device_nas": "LINUX", "device_pc": "WINDOWS", "device_laptop": "WINDOWS", "device_printer": "UNKNOWN",
            "device_camera": "UNKNOWN", "device_securityalarm": "UNKNOWN", "device_audio": "UNKNOWN", "device_other": "UNKNOWN",
            "os_android": "ANDROID", "os_ios": "IOS", "os_macos": "MAC", "os_windows": "WINDOWS", "os_linux": "LINUX"}


def _ms(iso):
    """ISO-tijd van AdGuard Home (met nanoseconden) naar milliseconden."""
    if not iso:
        return 0
    s = re.sub(r"(\.\d{6})\d+", r"\1", str(iso)).replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except ValueError:
        return 0


def server():
    st = get("status")
    dns = get("dns_info")
    flt = get("filtering/status")
    sb = get("safebrowsing/status")
    pc = get("parental/status")
    ss = get("safesearch/status")
    rules = [r for r in (flt.get("user_rules") or []) if str(r).strip()]
    return {"id": SERVER_ID, "name": "AdGuard Home", "default": True, "version": st.get("version", ""),
            "devices_count": len(devices()),
            "settings": {
                "protection_enabled": bool(dns.get("protection_enabled", st.get("protection_enabled"))),
                "block_ttl_seconds": dns.get("blocked_response_ttl", 10),
                "blocking_mode_settings": {"blocking_mode": MODE_TO_CLOUD.get(dns.get("blocking_mode") or "default", "NONE")},
                "safebrowsing_settings": {"enabled": bool(sb.get("enabled"))},
                "parental_control_settings": {"enabled": bool(pc.get("enabled")), "engines_safe_search_enabled": bool(ss.get("enabled"))},
                "filter_lists_settings": {"enabled": bool(flt.get("enabled", True)),
                                          "filter_list": [{"filter_id": str(f.get("id")), "enabled": bool(f.get("enabled"))}
                                                          for f in (flt.get("filters") or [])]},
                "user_rules_settings": {"enabled": True, "rules": rules, "rules_count": len(rules)}}}


def _ip_like(s):
    return bool(re.match(r"^[0-9a-fA-F:.]+(/\d+)?$", str(s)))


def devices():
    """Vaste clients uit AdGuard Home, aangevuld met de actiefste andere adressen uit de statistieken."""
    res, seen = [], set()
    for c in (get("clients").get("clients") or []):
        ids = [str(i) for i in (c.get("ids") or [])]
        if not ids:
            continue
        dtype = next((TAG_TYPE[t] for t in (c.get("tags") or []) if t in TAG_TYPE), "UNKNOWN")
        res.append({"id": ids[0], "name": c.get("name") or ids[0], "device_type": dtype, "dns_server_id": SERVER_ID,
                    "ids": ids, "persistent": True,
                    "settings": {"protection_enabled": True if c.get("use_global_settings", True) else bool(c.get("filtering_enabled"))}})
        seen.update(ids)
    names = {}
    try:
        for c in (get("clients").get("auto_clients") or []):
            if c.get("ip"):
                names[c["ip"]] = c.get("name") or ""
    except HomeError:
        pass
    for row in (stats().get("top_clients") or []):
        for ip in row:
            if ip in seen:
                continue
            seen.add(ip)
            res.append({"id": ip, "name": names.get(ip) or ip, "device_type": "UNKNOWN", "dns_server_id": SERVER_ID,
                        "ids": [ip], "persistent": False, "settings": {"protection_enabled": True}})
    return res


def _ip_map():
    m = {}
    for d in devices():
        for i in d["ids"]:
            m[i] = d["id"]
    return m


def stats():
    return get("stats", ttl=15)


def _time_stats(frm, to):
    s = stats()
    q = s.get("dns_queries") or []
    blk = [sum(x) for x in zip(s.get("blocked_filtering") or [0] * len(q), s.get("replaced_safebrowsing") or [0] * len(q),
                                 s.get("replaced_parental") or [0] * len(q))]
    step = 86400000 if s.get("time_units") == "days" else 3600000
    now = int(time.time() * 1000)
    end = now - now % step + step
    out = []
    n = len(q)
    for i in range(n):
        t = end - (n - 1 - i) * step
        if t <= frm or t - step > to:
            continue
        out.append({"time_millis": t, "value": {"queries": q[i], "blocked": blk[i] if i < len(blk) else 0, "companies": 0}})
    return out


def _pairs(rows):
    for row in rows or []:
        for k, v in row.items():
            yield k, v


def _limit(params, default=10):
    try:
        return max(1, min(1000, int((params.get("limit") or [default])[0])))
    except ValueError:
        return default


REASON_BLOCKED = ("FilteredBlackList", "FilteredSafeBrowsing", "FilteredParental", "FilteredInvalid", "FilteredBlockedService")
REASON_MODIFIED = ("Rewrite", "RewriteEtcHosts", "RewriteRule", "FilteredSafeSearch")
STATUS_TO_HOME = {"REQUEST_BLOCKED": "blocked", "RESPONSE_BLOCKED": "blocked", "REQUEST_ALLOWED": "whitelisted",
                  "RESPONSE_ALLOWED": "whitelisted", "NONE": "processed", "MODIFIED": "rewritten"}


def _log_item(e, ipmap):
    reason = e.get("reason") or ""
    if reason in REASON_BLOCKED:
        status = "REQUEST_BLOCKED"
    elif reason == "NotFilteredWhiteList":
        status = "REQUEST_ALLOWED"
    elif reason in REASON_MODIFIED:
        status = "MODIFIED"
    else:
        status = "NONE"
    rules = e.get("rules") or []
    ftype = None
    if status in ("REQUEST_BLOCKED", "REQUEST_ALLOWED"):
        ftype = "USER_FILTER" if any(int(r.get("filter_list_id") or 0) == 0 for r in rules) else "FILTERS"
        if reason in ("FilteredSafeBrowsing", "FilteredParental"):
            ftype = "SAFEBROWSING" if reason == "FilteredSafeBrowsing" else "PARENTAL"
    q = e.get("question") or {}
    domain = str(q.get("unicode_name") or q.get("name") or "").rstrip(".")
    client = str(e.get("client") or "")
    return {"domain": domain, "time_millis": _ms(e.get("time")), "device_id": ipmap.get(client, client),
            "dns_server_id": SERVER_ID, "client_ip": client, "dns_request_type": "Type" + str(q.get("type") or ""),
            "dns_proto_type": str(e.get("client_proto") or "").upper(),
            "elapsed_ms": e.get("elapsedMs"), "rule": (rules[0].get("text") if rules else e.get("rule")) or "",
            "filtering_info": {"filtering_status": status, "filtering_type": ftype}}


def query_log(params):
    limit = _limit(params, 100)
    frm = int((params.get("time_from_millis") or [0])[0] or 0)
    search = (params.get("search") or [""])[0].strip()
    devs = params.get("devices") or []
    ipmap = _ip_map()
    dev_ips = set()
    if devs:
        by_id = {d["id"]: d["ids"] for d in devices()}
        for d in devs:
            dev_ips.update(by_id.get(d, [d]))
    statuses = params.get("statuses") or []
    home_status = {STATUS_TO_HOME[s] for s in statuses if s in STATUS_TO_HOME}
    q = {"limit": str(min(500, limit if not dev_ips else limit * 3))}
    if len(home_status) == 1:
        q["response_status"] = next(iter(home_status))
    if search:
        q["search"] = search
    elif len(dev_ips) == 1:
        q["search"] = next(iter(dev_ips))
    cursor = (params.get("cursor") or [""])[0]
    if cursor:
        q["older_than"] = cursor
    res = get("querylog?" + urllib.parse.urlencode(q), ttl=3)
    raw = res.get("data") or []
    items = []
    for e in raw:
        it = _log_item(e, ipmap)
        if frm and it["time_millis"] < frm:
            continue
        if dev_ips and it["client_ip"] not in dev_ips:
            continue
        items.append(it)
    items = items[:limit]
    pages = [{"current": True, "page_number": 1, "page_cursor": cursor}]
    oldest = res.get("oldest") or ""
    if raw and oldest and len(raw) >= int(q["limit"]) and not (frm and _ms(oldest) < frm):
        pages.append({"current": False, "page_number": 2, "page_cursor": oldest})
    return {"items": items, "pages": pages}


def respond(path, params):
    """Antwoord op een pad uit de API van AdGuard DNS, met gegevens uit AdGuard Home."""
    now = int(time.time() * 1000)
    frm = int((params.get("time_from_millis") or [now - 86400000])[0])
    to = int((params.get("time_to_millis") or [now])[0])
    if path in PAGES:
        return PAGES[path]()
    if path == "home/check":
        return check_host((params.get("name") or [""])[0])
    if path == "home/info":
        st, s = get("status"), stats()
        flt = get("filtering/status")
        return {"version": st.get("version", ""), "protection_enabled": bool(st.get("protection_enabled")),
                "avg_ms": round(float(s.get("avg_processing_time") or 0) * 1000, 1),
                "queries": int(s.get("num_dns_queries") or 0),
                "blocked": int(s.get("num_blocked_filtering") or 0) + int(s.get("num_replaced_safebrowsing") or 0)
                + int(s.get("num_replaced_parental") or 0),
                "clients": len(devices()), "time_units": s.get("time_units") or "hours", "points": len(s.get("dns_queries") or []),
                "lists": sum(1 for f in (flt.get("filters") or []) if f.get("enabled")),
                "rules": sum(int(f.get("rules_count") or 0) for f in (flt.get("filters") or []) if f.get("enabled"))}
    if path == "v2/dns_servers":
        return {"items": [server()], "navigation": {}}
    if path.startswith("v1/dns_servers/"):
        if path.split("/")[2] != SERVER_ID:
            return {"error": "Server niet gevonden."}
        return server()
    if path == "v2/devices":
        return {"items": devices(), "navigation": {}}
    if path == "v2/filter_lists":
        flt = get("filtering/status")
        return {"items": [{"filter_id": str(f.get("id")), "name": f.get("name") or f.get("url"), "type": "CUSTOM",
                           "rules_count": f.get("rules_count"), "description": f.get("url"),
                           "time_updated": f.get("last_updated")} for f in (flt.get("filters") or [])]}
    if path == "v1/stats/time":
        return {"stats": _time_stats(frm, to)}
    if path == "v1/stats/domains":
        s = stats()
        allowed, blocked = dict(_pairs(s.get("top_queried_domains"))), dict(_pairs(s.get("top_blocked_domains")))
        names = set(allowed) | set(blocked)
        rows = [{"domain": d, "value": {"queries": allowed.get(d, 0) + blocked.get(d, 0), "blocked": blocked.get(d, 0), "companies": 0}}
                for d in names]
        key = (params.get("sort") or [""])[0]
        rows.sort(key=lambda r: -(r["value"]["blocked"] if key == "blocked" else r["value"]["queries"]))
        return {"stats": rows[:_limit(params)], "navigation": {}}
    if path == "v1/stats/devices":
        ipmap = _ip_map()
        agg = {}
        for ip, n in _pairs(stats().get("top_clients")):
            did = ipmap.get(ip, ip)
            agg[did] = agg.get(did, 0) + int(n or 0)
        rows = [{"device_id": d, "value": {"queries": n, "companies": 0}} for d, n in sorted(agg.items(), key=lambda x: -x[1])]
        return {"stats": rows[:_limit(params)], "navigation": {}}
    if path == "v1/query_log":
        return query_log(params)
    if path in ("v1/stats/companies", "v1/stats/companies/detailed", "v1/stats/countries", "v1/stats/categories"):
        return {"stats": [], "navigation": {}}
    if path in ("v1/account/limits", "v1/account/license"):
        return {}
    return {"error": "Dit onderdeel bestaat niet in AdGuard Home."}


# ---------------------------------------------------------------- wijzigen
def read_rules():
    return [r for r in (get("filtering/status", ttl=0).get("user_rules") or []) if str(r).strip()]


def set_rules(rules):
    post("filtering/set_rules", {"rules": list(rules)})


def toggle_list(filter_id, enabled):
    flt = get("filtering/status", ttl=0)
    f = next((x for x in (flt.get("filters") or []) if str(x.get("id")) == str(filter_id)), None)
    if not f:
        raise ValueError("Blokkeerlijst niet gevonden.")
    post("filtering/set_url", {"url": f["url"], "whitelist": False,
                               "data": {"name": f.get("name") or f["url"], "url": f["url"], "enabled": bool(enabled)}})


def change_settings(patch):
    """Voert een wijziging uit in de vorm van AdGuard DNS; alleen wat AdGuard Home ook kent."""
    done = False
    if "protection_enabled" in patch:
        post("protection", {"enabled": bool(patch["protection_enabled"])})
        done = True
    sb = patch.get("safebrowsing_settings") or {}
    if "enabled" in sb:
        post("safebrowsing/" + ("enable" if sb["enabled"] else "disable"))
        done = True
    pc = patch.get("parental_control_settings") or {}
    if "enabled" in pc:
        post("parental/" + ("enable" if pc["enabled"] else "disable"))
        done = True
    if "engines_safe_search_enabled" in pc:
        cur = get("safesearch/status", ttl=0)
        cur["enabled"] = bool(pc["engines_safe_search_enabled"])
        post("safesearch/settings", cur, method="PUT")
        done = True
    dns = {}
    bm = (patch.get("blocking_mode_settings") or {}).get("blocking_mode")
    if bm:
        if bm not in MODE_TO_HOME:
            raise ValueError("Ongeldige blokkeermodus.")
        dns["blocking_mode"] = MODE_TO_HOME[bm]
    if "block_ttl_seconds" in patch:
        dns["blocked_response_ttl"] = int(patch["block_ttl_seconds"])
    if dns:
        post("dns_config", dns)
        done = True
    if not done:
        raise ValueError("Deze instelling bestaat niet in AdGuard Home.")


# ---------------------------------------------------------------- eigen pagina's voor AdGuard Home
def _names():
    """IP-adres of ID naar naam, uit vaste en automatisch gevonden clients."""
    m = {}
    try:
        cl = get("clients")
    except HomeError:
        return m
    for c in (cl.get("auto_clients") or []):
        if c.get("ip") and c.get("name"):
            m[c["ip"]] = c["name"]
    for c in (cl.get("clients") or []):
        for i in (c.get("ids") or []):
            m[str(i)] = c.get("name") or str(i)
    return m


def overview():
    s, st = stats(), get("status")
    names = _names()
    n = lambda k: int(s.get(k) or 0)
    return {
        "version": st.get("version", ""), "protection_enabled": bool(st.get("protection_enabled")),
        "protection_disabled_duration": int(st.get("protection_disabled_duration") or 0),
        "time_units": s.get("time_units") or "hours",
        "queries": n("num_dns_queries"), "blocked": n("num_blocked_filtering"), "safebrowsing": n("num_replaced_safebrowsing"),
        "parental": n("num_replaced_parental"), "safesearch": n("num_replaced_safesearch"),
        "avg_ms": round(float(s.get("avg_processing_time") or 0) * 1000, 1),
        "series": {k: list(s.get(k) or []) for k in ("dns_queries", "blocked_filtering", "replaced_safebrowsing", "replaced_parental")},
        "top_clients": [{"id": k, "name": names.get(k, ""), "n": int(v or 0)} for k, v in _pairs(s.get("top_clients"))],
        "top_queried": [{"domain": k, "n": int(v or 0)} for k, v in _pairs(s.get("top_queried_domains"))],
        "top_blocked": [{"domain": k, "n": int(v or 0)} for k, v in _pairs(s.get("top_blocked_domains"))],
        "upstreams": [{"addr": k, "n": int(v or 0)} for k, v in _pairs(s.get("top_upstreams_responses"))],
        "upstream_ms": [{"addr": k, "ms": round(float(v or 0) * 1000, 1)} for k, v in _pairs(s.get("top_upstreams_avg_time"))],
    }


def _flist(items):
    return [{"id": f.get("id"), "name": f.get("name") or f.get("url"), "url": f.get("url"), "enabled": bool(f.get("enabled")),
             "rules_count": int(f.get("rules_count") or 0), "last_updated": f.get("last_updated") or ""} for f in (items or [])]


def filters():
    f = get("filtering/status", ttl=3)
    return {"enabled": bool(f.get("enabled", True)), "interval": f.get("interval"),
            "blocklists": _flist(f.get("filters")), "allowlists": _flist(f.get("whitelist_filters")),
            "user_rules": [r for r in (f.get("user_rules") or []) if str(r).strip()]}


def services():
    allsvc = get("blocked_services/all", ttl=3600)
    cur = get("blocked_services/get", ttl=3)
    return {"groups": [g.get("id") for g in (allsvc.get("groups") or [])],
            "services": [{"id": x.get("id"), "name": x.get("name"), "group": x.get("group_id") or "", "icon": x.get("icon_svg") or ""}
                         for x in (allsvc.get("blocked_services") or [])],
            "blocked": list(cur.get("ids") or [])}


def general():
    st, ss = get("status", ttl=3), get("safesearch/status", ttl=3)
    return {"protection_enabled": bool(st.get("protection_enabled")),
            "protection_disabled_duration": int(st.get("protection_disabled_duration") or 0),
            "safebrowsing": bool(get("safebrowsing/status", ttl=3).get("enabled")),
            "parental": bool(get("parental/status", ttl=3).get("enabled")),
            "safesearch": {k: bool(v) for k, v in ss.items() if isinstance(v, bool)},
            "querylog": {k: v for k, v in get("querylog/config", ttl=3).items() if k in ("enabled", "interval", "anonymize_client_ip")},
            "stats": {k: v for k, v in get("stats/config", ttl=3).items() if k in ("enabled", "interval")}}


DNS_KEYS = {"upstream_dns": list, "bootstrap_dns": list, "fallback_dns": list, "upstream_mode": str, "ratelimit": int,
            "blocking_mode": str, "blocked_response_ttl": int, "blocking_ipv4": str, "blocking_ipv6": str,
            "cache_enabled": bool, "cache_size": int, "cache_optimistic": bool, "dnssec_enabled": bool, "disable_ipv6": bool,
            "edns_cs_enabled": bool, "upstream_timeout": int}


def dns():
    d = get("dns_info", ttl=3)
    return {k: d.get(k) for k in DNS_KEYS}


CLIENT_KEYS = ("name", "ids", "tags", "use_global_settings", "filtering_enabled", "parental_enabled", "safebrowsing_enabled",
               "use_global_blocked_services", "blocked_services", "upstreams", "ignore_querylog", "ignore_statistics")


def clients():
    c = get("clients", ttl=3)
    return {"clients": [dict({k: x.get(k) for k in CLIENT_KEYS}, safe_search=(x.get("safe_search") or {}).get("enabled", bool(x.get("safesearch_enabled"))))
                        for x in (c.get("clients") or [])],
            "auto": [{"ip": x.get("ip"), "name": x.get("name") or "", "source": x.get("source") or ""} for x in (c.get("auto_clients") or [])],
            "tags": list(c.get("supported_tags") or [])}


def dhcp():
    d = get("dhcp/status", ttl=5)
    lease = lambda x: {"mac": x.get("mac", ""), "ip": x.get("ip", ""), "hostname": x.get("hostname", ""), "expires": x.get("expires", "")}
    return {"enabled": bool(d.get("enabled")), "interface": d.get("interface_name") or "", "v4": d.get("v4") or {},
            "leases": [lease(x) for x in (d.get("leases") or [])], "static_leases": [lease(x) for x in (d.get("static_leases") or [])]}


def tls():
    t = get("tls/status", ttl=10)
    keep = ("enabled", "server_name", "force_https", "port_https", "port_dns_over_tls", "port_dns_over_quic", "valid_cert",
            "valid_chain", "valid_key", "valid_pair", "not_after", "issuer", "subject", "dns_names", "warning_validation", "serve_plain_dns")
    return {k: t.get(k) for k in keep}


def check_host(name):
    name = str(name or "").strip()[:253]
    if not re.match(r"^[A-Za-z0-9._*-]+$", name):
        raise ValueError("Vul een geldige domeinnaam in.")
    r = get("filtering/check_host?" + urllib.parse.urlencode({"name": name}), ttl=0)
    return {"reason": r.get("reason") or "", "rules": [x.get("text") for x in (r.get("rules") or []) if x.get("text")],
            "service_name": r.get("service_name") or "", "cname": r.get("cname") or "", "ip_addrs": r.get("ip_addrs") or []}


PAGES = {"home/overview": overview, "home/filters": filters, "home/services": services, "home/general": general,
         "home/dns": dns, "home/clients": clients, "home/dhcp": dhcp, "home/tls": tls,
         "home/rewrites": lambda: [{"domain": r.get("domain"), "answer": r.get("answer"), "enabled": r.get("enabled", True)}
                                   for r in (get("rewrite/list", ttl=3) or [])]}


# ---------------------------------------------------------------- acties vanuit de eigen pagina's
def _s(v, n=500):
    v = str(v if v is not None else "").strip()
    if len(v) > n or any(c in v for c in "\r\n\x00"):
        raise ValueError("Ongeldige invoer.")
    return v


def _strs(v, n=200, each=500):
    if isinstance(v, str):
        v = v.splitlines()
    if not isinstance(v, list) or len(v) > n:
        raise ValueError("Ongeldige lijst.")
    return [_s(x, each) for x in v if str(x).strip()]


def _int(v, lo, hi):
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ValueError("Vul een getal in.")
    if not lo <= v <= hi:
        raise ValueError(f"Kies een getal van {lo} tot {hi}.")
    return v


def _url(v):
    v = _s(v, 1000)
    if not re.match(r"^(https?://\S+|/\S+)$", v):
        raise ValueError("Vul een geldig adres in dat begint met http:// of https://.")
    return v


DAY = 86400000
INTERVALS = {"querylog": (DAY // 4, DAY, 7 * DAY, 30 * DAY, 90 * DAY), "stats": (DAY, 7 * DAY, 30 * DAY, 90 * DAY)}


def _client_data(d, base=None):
    out = dict(base or {})
    out["name"] = _s(d.get("name", out.get("name")), 100)
    if not out["name"]:
        raise ValueError("Geef de client een naam.")
    ids = _strs(d.get("ids", out.get("ids") or []), 50, 100)
    if not ids:
        raise ValueError("Vul minstens één IP-adres, MAC-adres of ClientID in.")
    out["ids"] = ids
    for k in ("use_global_settings", "filtering_enabled", "parental_enabled", "safebrowsing_enabled", "use_global_blocked_services",
              "ignore_querylog", "ignore_statistics"):
        if k in d:
            out[k] = bool(d[k])
    if "tags" in d:
        out["tags"] = _strs(d["tags"], 20, 50)
    if "safe_search" in d:
        ss = dict(out.get("safe_search") or {})
        ss["enabled"] = bool(d["safe_search"])
        out["safe_search"] = ss
        out["safesearch_enabled"] = bool(d["safe_search"])
    out.setdefault("use_global_settings", True)
    out.setdefault("use_global_blocked_services", True)
    return out


def act(p):
    """Voert één wijziging uit. Alleen de acties hieronder; alle invoer wordt gecontroleerd."""
    a = str(p.get("action") or "")
    if a == "protection":
        body = {"enabled": bool(p.get("enabled"))}
        if not body["enabled"] and p.get("duration"):
            body["duration"] = _int(p["duration"], 1000, 7 * DAY)
        post("protection", body)
        return "Bescherming ingeschakeld." if body["enabled"] else "Bescherming uitgeschakeld."
    if a in ("safebrowsing", "parental"):
        post(a + "/" + ("enable" if p.get("enabled") else "disable"))
        return "Instelling opgeslagen."
    if a == "safesearch":
        cur = {k: v for k, v in get("safesearch/status", ttl=0).items() if isinstance(v, bool)}
        for k, v in (p.get("values") or {}).items():
            if k in cur:
                cur[k] = bool(v)
        post("safesearch/settings", cur, method="PUT")
        return "Instelling opgeslagen."
    if a in ("querylog_config", "stats_config"):
        kind = a.split("_")[0]
        cur = get(kind + "/config", ttl=0)
        if "enabled" in p:
            cur["enabled"] = bool(p["enabled"])
        if "interval" in p:
            iv = _int(p["interval"], 0, 366 * DAY)
            if iv not in INTERVALS[kind]:
                raise ValueError("Ongeldige bewaartijd.")
            cur["interval"] = iv
        if kind == "querylog" and "anonymize_client_ip" in p:
            cur["anonymize_client_ip"] = bool(p["anonymize_client_ip"])
        post(kind + "/config/update", cur, method="PUT")
        return "Instelling opgeslagen."
    if a == "querylog_clear":
        post("querylog_clear")
        return "De querylog is gewist."
    if a == "stats_reset":
        post("stats_reset")
        return "De statistieken zijn gewist."
    if a == "dns":
        body = {}
        for k, t in DNS_KEYS.items():
            if k not in (p.get("values") or {}):
                continue
            v = p["values"][k]
            if t is list:
                v = _strs(v, 100, 300)
                if k == "upstream_dns" and not v:
                    raise ValueError("Vul minstens één upstream DNS-server in.")
            elif t is bool:
                v = bool(v)
            elif t is int:
                v = _int(v, 0, {"ratelimit": 100000, "blocked_response_ttl": 86400, "cache_size": 1 << 30, "upstream_timeout": 60}[k])
            else:
                v = _s(v, 100)
                if k == "upstream_mode" and v not in ("", "load_balance", "parallel", "fastest_addr"):
                    raise ValueError("Ongeldige upstream-modus.")
                if k == "blocking_mode" and v not in MODE_TO_CLOUD:
                    raise ValueError("Ongeldige blokkeermodus.")
            body[k] = v
        if not body:
            raise ValueError("Er is niets om te wijzigen.")
        post("dns_config", body)
        return "DNS-instellingen opgeslagen."
    if a == "test_upstream":
        res = post("test_upstream_dns", {"upstream_dns": _strs(p.get("upstream_dns"), 100, 300),
                                         "bootstrap_dns": _strs(p.get("bootstrap_dns"), 100, 300), "fallback_dns": []})
        return {"message": "Test klaar.", "result": res}
    if a in ("filter_add", "filter_remove", "filter_toggle"):
        wl = bool(p.get("whitelist"))
        url = _url(p.get("url"))
        if a == "filter_add":
            name = _s(p.get("name"), 200) or url
            post("filtering/add_url", {"name": name, "url": url, "whitelist": wl})
            return "Lijst toegevoegd."
        if a == "filter_remove":
            post("filtering/remove_url", {"url": url, "whitelist": wl})
            return "Lijst verwijderd."
        f = filters()
        hit = next((x for x in f["allowlists" if wl else "blocklists"] if x["url"] == url), None)
        if not hit:
            raise ValueError("Lijst niet gevonden.")
        on = bool(p.get("enabled"))
        post("filtering/set_url", {"url": url, "whitelist": wl, "data": {"name": hit["name"], "url": url, "enabled": on}})
        return "Lijst ingeschakeld." if on else "Lijst uitgeschakeld."
    if a == "filters_refresh":
        res = post("filtering/refresh", {"whitelist": bool(p.get("whitelist"))})
        return f"Lijsten bijgewerkt: {int((res or {}).get('updated') or 0)} gewijzigd."
    if a == "rules_set":
        set_rules(_strs(p.get("rules"), 20000, 2000))
        return "Filterregels opgeslagen."
    if a in ("rewrite_add", "rewrite_delete"):
        dom, ans = _s(p.get("domain"), 253), _s(p.get("answer"), 300)
        if not dom or not ans:
            raise ValueError("Vul een domein en een antwoord in.")
        post("rewrite/" + ("add" if a == "rewrite_add" else "delete"), {"domain": dom, "answer": ans})
        return "Herschrijving toegevoegd." if a == "rewrite_add" else "Herschrijving verwijderd."
    if a == "services":
        known = {s["id"] for s in services()["services"]}
        ids = [i for i in _strs(p.get("ids"), 500, 100) if i in known]
        cur = get("blocked_services/get", ttl=0)
        post("blocked_services/update", {"ids": ids, "schedule": cur.get("schedule") or {}}, method="PUT")
        return "Geblokkeerde diensten opgeslagen."
    if a == "client_add":
        post("clients/add", _client_data(p.get("data") or {}))
        return "Client toegevoegd."
    if a == "client_update":
        name = _s(p.get("name"), 100)
        cur = next((c for c in (get("clients", ttl=0).get("clients") or []) if c.get("name") == name), None)
        if not cur:
            raise ValueError("Client niet gevonden.")
        post("clients/update", {"name": name, "data": _client_data(p.get("data") or {}, cur)})
        return "Client opgeslagen."
    if a == "client_delete":
        post("clients/delete", {"name": _s(p.get("name"), 100)})
        return "Client verwijderd."
    raise ValueError("Onbekende actie.")
