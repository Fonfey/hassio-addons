#!/usr/bin/env python3
"""
Tijdmachine voor Home Assistant
===============================

Bewaart bij elke wijziging een versie van je automations, scripts, scènes,
dashboards en helpers. Je ziet per onderdeel wat er veranderde en zet één
onderdeel terug naar een eerdere versie, ook als je het hebt verwijderd.

Los starten zonder Home Assistant toont voorbeeldgegevens:  python3 tijdmachine.py --demo
"""

import difflib
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse

import ha_base as hb

try:
    import yaml

    class _Dumper(yaml.SafeDumper):
        pass

    def _str(dumper, value):
        style = "|" if "\n" in value else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)

    _Dumper.add_representer(str, _str)

    def to_text(cfg):
        return yaml.dump(cfg, Dumper=_Dumper, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)
except ImportError:  # pragma: no cover
    def to_text(cfg):
        return json.dumps(cfg, indent=2, ensure_ascii=False)

KEEP_DAYS = hb.opt_int("keep_days", 30, 1, 3650)
KEEP_MIN = hb.opt_int("keep_min_versions", 5, 1, 100)
SCAN_MIN = hb.opt_int("scan_minutes", 5, 1, 120)
DATA = os.path.join(hb.data_dir(__file__), "versies")
os.makedirs(DATA, exist_ok=True)

HELPERS = ["input_boolean", "input_number", "input_select", "input_text", "input_datetime", "input_button",
           "counter", "timer", "schedule"]
KIND_L = {"automation": hb.L("Automation", "Automation"), "script": hb.L("Script", "Script"), "scene": hb.L("Scène", "Scene"),
          "dashboard": hb.L("Dashboard", "Dashboard"), "helper": hb.L("Helper", "Helper")}

LOCK = threading.RLock()


def COPY():
    return hb.tx(" (kopie)", " (copy)")


def is_copy_name(name):
    return name.endswith(" (kopie)") or name.endswith(" (copy)")
ITEMS = {}  # key -> {"key","kind","id","name","domain","deleted","versions":[{"t","cfg","h"}]}  (versies van oud naar nieuw)
STATUS = {"last_scan": None, "error": None, "since": None, "scanning": False, "dash_skipped": []}
_SCAN = threading.Event()
PENDING_COPY = {}  # sleutel van een net gemaakte kopie -> sleutel van het origineel
PURGED = {}  # sleutel -> tijdstip tot wanneer een definitief verwijderd onderdeel genegeerd wordt


# ---------------------------------------------------------------- opslag
def _file(key):
    return os.path.join(DATA, re.sub(r"[^A-Za-z0-9_.-]", "_", key) + "-" + hashlib.sha1(key.encode()).hexdigest()[:8] + ".json")


def load_all():
    for name in os.listdir(DATA):
        if name.endswith(".json"):
            it = hb.load_json(os.path.join(DATA, name), None)
            if isinstance(it, dict) and it.get("key"):
                ITEMS[it["key"]] = it
    meta = hb.load_json(os.path.join(DATA, "_meta.json"), {})
    STATUS["since"] = meta.get("since") or time.time()
    hb.save_json(os.path.join(DATA, "_meta.json"), {"since": STATUS["since"]})


def save(it):
    hb.save_json(_file(it["key"]), it)


def digest(cfg):
    return hashlib.sha1(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def prune(it):
    cut = time.time() - KEEP_DAYS * 86400
    vs = it["versions"]
    keep = [v for i, v in enumerate(vs) if v["t"] >= cut or i >= len(vs) - KEEP_MIN]
    if len(keep) != len(vs):
        it["versions"] = keep
        return True
    return False


def record(kind, oid, name, cfg, domain=None):
    """Voegt een versie toe als de inhoud anders is dan de laatste. Geeft True terug bij een nieuwe versie."""
    key = f"{kind}:{domain + ':' if domain else ''}{oid}"
    h = digest(cfg)
    if PURGED.get(key, 0) > time.time():
        return key  # net definitief verwijderd; Home Assistant is nog bezig
    with LOCK:
        it = ITEMS.get(key)
        if not it:
            it = ITEMS[key] = {"key": key, "kind": kind, "id": oid, "domain": domain, "name": name, "deleted": None, "versions": []}
            if key in PENDING_COPY:
                it["copyOf"] = PENDING_COPY.pop(key)
        changed = False
        if it["name"] != name:
            it["name"], changed = name, True
        if it.get("deleted"):
            it["deleted"], changed = None, True
        if not it["versions"] or it["versions"][-1]["h"] != h:
            it["versions"].append({"t": time.time(), "cfg": cfg, "h": h})
            changed = True
        changed = prune(it) or changed
        if changed:
            save(it)
        return key


def mark_deleted(kind, seen):
    with LOCK:
        for it in ITEMS.values():
            if it["kind"] == kind and it["key"] not in seen and not it.get("deleted"):
                it["deleted"] = time.time()
                save(it)
                print(f"Verwijderd: {it['name']}", flush=True)


# ---------------------------------------------------------------- Home Assistant lezen
def scan():
    if hb.DEMO or hb.NO_TOKEN:
        return
    with LOCK:
        if STATUS["scanning"]:
            return
        STATUS["scanning"] = True
    errors = []
    try:
        states = hb.core("GET", "/states")
        by = lambda dom: [s for s in states if s["entity_id"].split(".")[0] == dom]

        def each(kind, rows, fetch):
            seen, ok = set(), True
            for oid, fallback in rows:
                try:
                    cfg = fetch(oid)
                except urllib.error.HTTPError as e:
                    if e.code in (400, 404):  # niet via de editor gemaakt (bv. YAML-pakketten): niet te bewaren
                        continue
                    ok = False
                    continue
                except Exception:
                    ok = False
                    continue
                if isinstance(cfg, dict) and cfg:
                    seen.add(record(kind, oid, str(cfg.get("alias") or cfg.get("name") or fallback), cfg))
            if ok:
                mark_deleted(kind, seen)
            else:
                errors.append(KIND_L[kind])

        q = urllib.parse.quote
        each("automation", [(s["attributes"]["id"], s["attributes"].get("friendly_name") or s["entity_id"])
                            for s in by("automation") if s["attributes"].get("id")],
             lambda i: hb.core("GET", f"/config/automation/config/{q(str(i))}"))
        each("script", [(s["entity_id"].split(".", 1)[1], s["attributes"].get("friendly_name") or s["entity_id"]) for s in by("script")],
             lambda i: hb.core("GET", f"/config/script/config/{q(str(i))}"))
        each("scene", [(s["attributes"]["id"], s["attributes"].get("friendly_name") or s["entity_id"])
                       for s in by("scene") if s["attributes"].get("id")],
             lambda i: hb.core("GET", f"/config/scene/config/{q(str(i))}"))

        ws = hb.WebSocket().auth()
        try:
            # Dashboards (alleen die je in de editor maakt)
            try:
                seen, skipped = set(), []
                listed = ws.call("lovelace/dashboards/list") or []
                boards = [{"url_path": None, "title": "Overzicht", "mode": "storage"}] + listed
                for d in boards:
                    title = d.get("title") or d.get("url_path") or "Overzicht"
                    if d.get("mode", "storage") != "storage":
                        skipped.append(hb.L(f"{title} (YAML-bestand)", f"{title} (YAML file)"))
                        continue
                    try:
                        cfg = ws.call("lovelace/config", url_path=d.get("url_path"))
                    except RuntimeError:
                        if d.get("url_path"):
                            skipped.append(hb.L(f"{title} (nog niets opgeslagen)", f"{title} (nothing saved yet)"))
                        continue  # automatisch gegenereerd: geen eigen inhoud
                    if isinstance(cfg, dict):
                        oid = d.get("url_path") or "lovelace"
                        # de naam uit de zijbalk, want die herken je; de titel in de inhoud is vaak iets anders
                        seen.add(record("dashboard", oid, title if d.get("url_path") else (cfg.get("title") or title), cfg))
                STATUS["dash_skipped"] = skipped
                mark_deleted("dashboard", seen)
            except Exception as e:
                errors.append(hb.L(f"Dashboards ({e})", f"Dashboards ({e})"))
            # Helpers
            seen, ok = set(), True
            for dom in HELPERS:
                try:
                    for h in ws.call(f"{dom}/list") or []:
                        if h.get("id"):
                            seen.add(record("helper", h["id"], h.get("name") or h["id"], h, domain=dom))
                except RuntimeError:
                    continue  # integratie niet geladen
                except Exception:
                    ok = False
            if ok:
                mark_deleted("helper", seen)
            else:
                errors.append(hb.L("Helpers", "Helpers"))
        finally:
            ws.close()
        STATUS["error"] = hb.L("Niet alles kon worden gelezen: " + ", ".join(hb.resolve(errors, "nl")), "Not everything could be read: " + ", ".join(hb.resolve(errors, "en"))) if errors else None
    except Exception as e:
        STATUS["error"] = hb.L(f"Home Assistant niet bereikbaar: {e}", f"Home Assistant not reachable: {e}")
        print(hb.resolve(STATUS["error"], "nl"), flush=True)
    finally:
        STATUS["last_scan"] = time.time()
        STATUS["scanning"] = False


def scan_loop():
    while True:
        _SCAN.wait(SCAN_MIN * 60)
        _SCAN.clear()
        time.sleep(3)  # wacht tot een reeks wijzigingen klaar is
        scan()


def on_event(ev):
    et = ev.get("event_type")
    if et == "call_service":
        d = ev.get("data") or {}
        if d.get("service") == "reload" or str(d.get("domain")).startswith("input_"):
            _SCAN.set()
    elif et in ("automation_reloaded", "scene_reloaded", "lovelace_updated", "entity_registry_updated"):
        _SCAN.set()


# ---------------------------------------------------------------- terugzetten
def restore(key, idx, copy=False):
    with LOCK:
        it = ITEMS.get(key)
        if not it:
            raise ValueError(hb.tx("Onbekend onderdeel.", "Unknown item."))
        vs = it["versions"]
        if not 0 <= idx < len(vs):
            raise ValueError(hb.tx("Onbekende versie.", "Unknown version."))
        cfg = json.loads(json.dumps(vs[idx]["cfg"]))
    if hb.DEMO:
        if not copy:
            return hb.tx("In de demo wordt niets teruggezet.", "Nothing is restored in the demo.")
        nk = f"{it['kind']}:demo{int(time.time())}"
        suffix = COPY()

        def later():
            time.sleep(6)
            with LOCK:
                ITEMS[nk] = {"key": nk, "kind": it["kind"], "id": nk, "domain": it.get("domain"), "name": hb.resolve(it["name"]) + suffix,
                             "deleted": None, "copyOf": key, "versions": [{"t": time.time(), "cfg": cfg, "h": digest(cfg)}]}
        threading.Thread(target=later, daemon=True).start()
        return {"message": hb.tx("Kopie gemaakt en uitgezet, zodat hij niet dubbel draait. Je vindt hem bij Automations.", "Copy created and turned off, so it does not run twice. You can find it under Automations."), "waitKey": nk}
    kind, oid, q = it["kind"], it["id"], urllib.parse.quote
    msg, new_key = hb.tx("Teruggezet.", "Restored."), None
    if kind in ("automation", "scene"):
        if copy:
            oid = str(int(time.time() * 1000))
            cfg["id"] = oid
            cfg["alias" if kind == "automation" else "name"] = (cfg.get("alias") or cfg.get("name") or "") + COPY()
            new_key = f"{kind}:{oid}"
            PENDING_COPY[new_key] = key
        hb.core("POST", f"/config/{kind}/config/{q(str(oid))}", cfg)
        if copy and kind == "automation":
            threading.Thread(target=turn_off_new_automation, args=(oid,), daemon=True).start()
            msg = hb.tx("Kopie gemaakt en uitgezet, zodat hij niet dubbel draait. Je vindt hem bij Automations.", "Copy created and turned off, so it does not run twice. You can find it under Automations.")
        elif copy:
            msg = hb.tx("Kopie gemaakt. Je vindt hem bij Scènes.", "Copy created. You can find it under Scenes.")
    elif kind == "script":
        if copy:
            oid = f"{oid}_kopie_{int(time.time())}"
            cfg["alias"] = (cfg.get("alias") or it["name"]) + COPY()
            new_key = f"script:{oid}"
            PENDING_COPY[new_key] = key
            msg = hb.tx("Kopie gemaakt. Je vindt hem bij Scripts.", "Copy created. You can find it under Scripts.")
        hb.core("POST", f"/config/script/config/{q(str(oid))}", cfg)
    elif kind == "dashboard":
        if copy:
            raise ValueError(hb.tx("Een dashboard kan alleen in zijn geheel worden teruggezet.", "A dashboard can only be restored as a whole."))
        try:
            hb.ws_call("lovelace/config/save", url_path=None if oid == "lovelace" else oid, config=cfg)
        except RuntimeError as e:
            raise ValueError(hb.tx(f"Dit dashboard bestaat niet meer. Maak het opnieuw aan met dezelfde URL ({oid}) en zet daarna de inhoud terug.",
                                   f"This dashboard no longer exists. Create it again with the same URL ({oid}) and then restore the content.")) from e
    elif kind == "helper":
        dom = it["domain"]
        fields = {k: v for k, v in cfg.items() if k != "id"}
        if copy or it.get("deleted"):
            if copy:
                fields["name"] = (fields.get("name") or it["name"]) + COPY()
            res = hb.ws_call(f"{dom}/create", **fields)
            if copy and isinstance(res, dict) and res.get("id"):
                new_key = f"helper:{dom}:{res['id']}"
                PENDING_COPY[new_key] = key
                msg = hb.tx("Kopie gemaakt. Je vindt hem bij Helpers.", "Copy created. You can find it under Helpers.")
        else:
            hb.ws_call(f"{dom}/update", **{f"{dom}_id": oid}, **fields)
    wait_key = new_key or (key if it.get("deleted") else None)
    threading.Thread(target=scan_until, args=(wait_key,), daemon=True).start()
    return {"message": msg, "waitKey": wait_key}


def scan_until(key, tries=30):
    """Controleert een tijdje vaker, tot een nieuw of teruggezet onderdeel in Home Assistant staat."""
    for _ in range(tries if key else 1):
        time.sleep(2)
        scan()
        with LOCK:
            it = ITEMS.get(key) if key else None
            if not key or (it and not it.get("deleted")):
                return


def turn_off_new_automation(aid):
    """Een kopie van een automation zet de app meteen uit, zodat hij niet naast het origineel draait."""
    for _ in range(20):
        time.sleep(1)
        try:
            for st in hb.core("GET", "/states"):
                if st["entity_id"].startswith("automation.") and str(st["attributes"].get("id")) == str(aid):
                    hb.core("POST", "/services/automation/turn_off", {"entity_id": st["entity_id"]})
                    return
        except Exception:
            pass
    print(f"Kopie {aid} niet gevonden om uit te zetten.", flush=True)


def purge(key):
    """Verwijdert een onderdeel definitief: uit Home Assistant (als het er nog is) en alle bewaarde versies."""
    with LOCK:
        it = ITEMS.get(key)
        if not it:
            raise ValueError(hb.tx("Onbekend onderdeel.", "Unknown item."))
        if not it.get("deleted") and not it.get("copyOf"):
            raise ValueError(hb.tx("Verwijder het onderdeel eerst. Definitief verwijderen kan bij verwijderde onderdelen en bij kopieën.", "Delete the item first. Permanent deletion is possible for deleted items and for copies."))
    if not it.get("deleted") and not hb.DEMO:
        delete(key)
    with LOCK:
        PURGED[key] = time.time() + 300
        ITEMS.pop(key, None)
        for other in ITEMS.values():
            if other.get("copyOf") == key:
                other.pop("copyOf", None)
                save(other)
        try:
            os.remove(_file(key))
        except OSError:
            pass
    return hb.tx("Definitief verwijderd.", "Permanently deleted.")


def delete(key):
    with LOCK:
        it = ITEMS.get(key)
        if not it:
            raise ValueError(hb.tx("Onbekend onderdeel.", "Unknown item."))
        if it.get("deleted"):
            raise ValueError(hb.tx("Dit onderdeel is al verwijderd.", "This item has already been deleted."))
    if hb.DEMO:
        return hb.tx("In de demo wordt niets verwijderd.", "Nothing is deleted in the demo.")
    kind, oid, q = it["kind"], it["id"], urllib.parse.quote
    if kind in ("automation", "scene", "script"):
        hb.core("DELETE", f"/config/{kind}/config/{q(str(oid))}")
    elif kind == "helper":
        hb.ws_call(f"{it['domain']}/delete", **{f"{it['domain']}_id": oid})
    else:
        raise ValueError(hb.tx("Een dashboard verwijder je in Home Assistant zelf, bij Instellingen, Dashboards.", "Delete a dashboard in Home Assistant itself, under Settings, Dashboards."))
    threading.Thread(target=lambda: (time.sleep(2), scan()), daemon=True).start()
    return hb.tx("Verwijderd. De Tijdmachine bewaart de versies, dus je kunt het altijd terugzetten.", "Deleted. Time Machine keeps the versions, so you can always restore it.")


# ---------------------------------------------------------------- weergave
def change_summary(old, new):
    if old is None:
        return hb.tx("Eerste versie", "First version")
    if not isinstance(old, dict) or not isinstance(new, dict):
        return hb.tx("Gewijzigd", "Changed")
    keys = [k for k in dict.fromkeys(list(old) + list(new)) if old.get(k) != new.get(k)]
    NL = {"alias": "naam", "name": "naam", "description": "omschrijving", "triggers": "triggers", "trigger": "triggers",
          "conditions": "voorwaarden", "condition": "voorwaarden", "actions": "acties", "action": "acties", "sequence": "acties",
          "mode": "modus", "views": "weergaven", "entities": "entiteiten", "options": "opties", "icon": "icoon", "title": "titel"}
    EN = {"alias": "name", "name": "name", "description": "description", "triggers": "triggers", "trigger": "triggers",
          "conditions": "conditions", "condition": "conditions", "actions": "actions", "action": "actions", "sequence": "actions",
          "mode": "mode", "views": "views", "entities": "entities", "options": "options", "icon": "icon", "title": "title"}
    names = list(dict.fromkeys(hb.tx(NL, EN).get(k, k) for k in keys))
    if not names:
        return hb.tx("Gewijzigd", "Changed")
    more = hb.tx(" en meer", " and more") if len(names) > 4 else ""
    return hb.tx("Gewijzigd: ", "Changed: ") + ", ".join(names[:4]) + more


def href(it):
    k, i = it["kind"], urllib.parse.quote(str(it["id"]))
    return {"automation": f"/config/automation/edit/{i}", "script": f"/config/script/edit/{i}", "scene": f"/config/scene/edit/{i}",
            "dashboard": f"/{i}", "helper": "/config/helpers"}[k]


def item_list():
    with LOCK:
        out = []
        for it in ITEMS.values():
            if not it["versions"]:
                continue
            last = it["deleted"] or it["versions"][-1]["t"]
            out.append({"key": it["key"], "kind": it["kind"], "kindNl": KIND_L[it["kind"]], "name": it["name"],
                        "count": len(it["versions"]), "last": last, "deleted": it["deleted"]})
        out.sort(key=lambda x: -x["last"])
        total = sum(x["count"] for x in out)
    return {"items": out, "total": total, "since": STATUS["since"], "lastScan": STATUS["last_scan"], "error": STATUS["error"],
            "demo": hb.DEMO, "keepDays": KEEP_DAYS, "keepMin": KEEP_MIN, "dashSkipped": STATUS["dash_skipped"]}


def item_detail(key):
    with LOCK:
        it = ITEMS.get(key)
        if not it:
            raise ValueError(hb.tx("Onbekend onderdeel.", "Unknown item."))
        vs = it["versions"]
        rows = []
        for i in range(len(vs) - 1, -1, -1):
            what = change_summary(vs[i - 1]["cfg"] if i > 0 else None, vs[i]["cfg"])
            if i == len(vs) - 1 and not it["deleted"]:
                what = hb.tx("Huidige versie. ", "Current version. ") + what
            rows.append({"i": i, "t": vs[i]["t"], "what": what, "h": vs[i]["h"][:12]})
        orig = ITEMS.get(it.get("copyOf") or "")
        nm = it["name"] if isinstance(it["name"], str) else ""
        if not orig and is_copy_name(nm):  # kopieën van voor versie 1.0.1
            base = nm.rsplit(" (", 1)[0]
            orig = next((o for o in ITEMS.values() if o["kind"] == it["kind"] and o["name"] == base), None)
        copies = [{"key": c["key"], "name": c["name"], "deleted": c["deleted"]} for c in ITEMS.values()
                  if c.get("copyOf") == key or (not c.get("copyOf") and c["kind"] == it["kind"] and nm and c["name"] in (nm + " (kopie)", nm + " (copy)"))]
        return {"key": key, "kind": it["kind"], "kindNl": KIND_L[it["kind"]], "name": it["name"], "deleted": it["deleted"],
                "href": href(it), "versions": rows, "canCopy": it["kind"] != "dashboard",
                "canDelete": not it["deleted"] and it["kind"] != "dashboard",
                "canPurge": bool(it["deleted"]) or (bool(it.get("copyOf")) and it["kind"] != "dashboard"),
                "copyOf": {"key": orig["key"], "name": orig["name"]} if orig else None, "copies": copies}


def drop_version(key, idx, h):
    """Eén bewaarde versie weggooien. De huidige versie van een bestaand onderdeel blijft altijd staan."""
    with LOCK:
        it = ITEMS.get(key)
        if not it:
            raise ValueError(hb.tx("Onbekend onderdeel.", "Unknown item."))
        vs = it["versions"]
        if not 0 <= idx < len(vs) or vs[idx]["h"][:12] != h:
            raise ValueError(hb.tx("Deze versie is intussen veranderd. Ververs de pagina en probeer het opnieuw.", "This version has changed in the meantime. Refresh the page and try again."))
        if len(vs) == 1:
            raise ValueError(hb.tx("Dit is de enige versie. Gebruik Definitief verwijderen om het hele onderdeel te wissen.", "This is the only version. Use Delete permanently to remove the whole item."))
        if idx == len(vs) - 1 and not it["deleted"]:
            raise ValueError(hb.tx("De huidige versie kun je niet weggooien; die staat nu in Home Assistant.", "You cannot discard the current version; it is what Home Assistant uses now."))
        del vs[idx]
        if not hb.DEMO:
            save(it)
    return hb.tx("Versie weggegooid.", "Version discarded.")


def diff(key, idx):
    """Vergelijkt versie idx met de huidige (of, bij de huidige, met de vorige)."""
    with LOCK:
        it = ITEMS.get(key)
        vs = it["versions"]
        cur = len(vs) - 1
        if it["deleted"]:
            a, b = vs[idx]["cfg"], None
            title = hb.tx("Inhoud van deze versie (het onderdeel is verwijderd)", "Content of this version (the item has been deleted)")
        elif idx == cur:
            if cur == 0:
                a, b, title = None, vs[cur]["cfg"], hb.tx("Eerste versie die de Tijdmachine zag", "First version Time Machine saw")
            else:
                a, b, title = vs[cur - 1]["cfg"], vs[cur]["cfg"], hb.tx("Wat er bij de laatste wijziging veranderde", "What changed in the last change")
        else:
            a, b, title = vs[idx]["cfg"], vs[cur]["cfg"], hb.tx("Deze versie vergeleken met nu", "This version compared with now")
    ta = to_text(a).splitlines() if a is not None else []
    tb = to_text(b).splitlines() if b is not None else []
    lines = []
    if a is None or b is None:
        for n, t in enumerate(ta or tb, 1):
            lines.append({"n": n, "s": " ", "t": t})
        return {"title": title, "lines": lines, "added": 0, "removed": 0}
    sm = difflib.SequenceMatcher(a=ta, b=tb, autojunk=False)
    added = removed = 0
    for g in sm.get_grouped_opcodes(4):
        if lines:
            lines.append({"n": "", "s": "…", "t": ""})
        for op, a1, a2, b1, b2 in g:
            if op == "equal":
                for k in range(a2 - a1):
                    lines.append({"n": b1 + k + 1, "s": " ", "t": tb[b1 + k]})
            else:
                for k in range(a1, a2):
                    lines.append({"n": k + 1, "s": "-", "t": ta[k]})
                    removed += 1
                for k in range(b1, b2):
                    lines.append({"n": k + 1, "s": "+", "t": tb[k]})
                    added += 1
    if not lines:
        lines = [{"n": "", "s": " ", "t": hb.tx("Geen verschil met de huidige versie.", "No difference from the current version.")}]
    return {"title": title, "lines": lines, "added": added, "removed": removed}


# ---------------------------------------------------------------- voorbeeldgegevens
def demo_data():
    now = time.time()
    H = 3600
    D = 86400

    def auto(alias, at, temp, workdays=True):
        cfg = {"id": "1700000000001", "alias": alias, "description": "", "mode": "single",
               "triggers": [{"trigger": "time", "at": at}],
               "conditions": [{"condition": "time", "weekday": ["mon", "tue", "wed", "thu", "fri"]}] if workdays else [],
               "actions": [{"action": "climate.set_temperature", "target": {"entity_id": "climate.woonkamer"},
                            "data": {"hvac_mode": "heat", "temperature": temp}}]}
        return cfg

    def add(kind, oid, name, versions, deleted=None, domain=None):
        key = f"{kind}:{domain + ':' if domain else ''}{oid}"
        ITEMS[key] = {"key": key, "kind": kind, "id": oid, "domain": domain, "name": name, "deleted": deleted,
                      "versions": [{"t": t, "cfg": c, "h": digest(c)} for t, c in versions]}

    a = "Airco - Woonkamer Ochtend opwarmen"
    a_l = hb.L(a, "Air con - Living room morning warm-up")
    add("automation", "1700000000001", a_l, [(now - 24 * D, auto(a, "05:30:00", 20, False)), (now - 5 * D, auto(a, "05:30:00", 20)),
                                           (now - 6 * H, auto(a, "06:00:00", 21))])
    m = {"id": "1700000000002", "alias": "Marstek - Laden bij laagste stroomprijs", "mode": "single",
         "triggers": [{"trigger": "numeric_state", "entity_id": "sensor.stroomprijs_nu", "below": 0.12}],
         "actions": [{"action": "select.select_option", "target": {"entity_id": "select.marstek_modus"}, "data": {"option": "Laden"}}]}
    m2 = dict(m, triggers=[dict(m["triggers"][0], below=0.10)])
    add("automation", "1700000000002", hb.L(m["alias"], "Marstek - Charge at lowest electricity price"), [(now - 30 * D, m), (now - 18 * D, m2)], deleted=now - 4 * D)
    s = {"id": "1700000000003", "alias": "Marstek - Slim op stroomprijs", "mode": "restart",
         "triggers": [{"trigger": "state", "entity_id": "sensor.stroomprijs_nu"}],
         "actions": [{"choose": [{"conditions": [{"condition": "numeric_state", "entity_id": "sensor.stroomprijs_nu", "below": 0.09}],
                                  "sequence": [{"action": "select.select_option", "target": {"entity_id": "select.marstek_modus"},
                                                "data": {"option": "Laden"}}]}]}]}
    s2 = json.loads(json.dumps(s))
    s2["actions"][0]["choose"][0]["conditions"][0]["below"] = 0.10
    add("automation", "1700000000003", hb.L(s["alias"], "Marstek - Smart on electricity price"), [(now - 12 * D, s), (now - 1 * D - 3 * H, s2)])
    dash = {"title": "Wandtablet", "views": [{"title": "Thuis", "path": "thuis", "cards": [{"type": "weather-forecast", "entity": "weather.thuis"},
                                                                                            {"type": "thermostat", "entity": "climate.woonkamer"}]}]}
    dash2 = json.loads(json.dumps(dash))
    dash2["views"][0]["cards"].append({"type": "entities", "title": "Energie", "entities": ["sensor.stroomprijs_nu", "sensor.marstek_soc"]})
    add("dashboard", "wandtablet", hb.L("Wandtablet", "Wall tablet"), [(now - 9 * D, dash), (now - 3 * D, dash2)])
    add("script", "alle_lichten_uit", hb.L("Alle lichten uit", "All lights off"), [(now - 6 * D, {"alias": "Alle lichten uit", "sequence": [
        {"action": "light.turn_off", "target": {"area_id": ["woonkamer", "keuken"]}}]})])
    add("helper", "prijsdrempel_laden", hb.L("Prijsdrempel laden", "Charge price threshold"), [(now - 20 * D, {"id": "prijsdrempel_laden", "name": "Prijsdrempel laden",
                                                                            "min": 0, "max": 0.5, "step": 0.01, "mode": "box", "initial": 0.12}),
                                                                 (now - 8 * D, {"id": "prijsdrempel_laden", "name": "Prijsdrempel laden",
                                                                                "min": 0, "max": 0.5, "step": 0.01, "mode": "box", "initial": 0.10})],
        domain="input_number")
    add("automation", "1700000000009", hb.L(a + " (kopie)", "Air con - Living room morning warm-up (copy)"), [(now - 2 * H, dict(auto(a, "05:30:00", 20), id="1700000000009", alias=a + COPY()))])
    ITEMS["automation:1700000000009"]["copyOf"] = "automation:1700000000001"
    STATUS.update(since=now - 31 * D, last_scan=now - 120)


# ---------------------------------------------------------------- webserver
class Handler(hb.BaseHandler):
    APP = "tijdmachine"

    def get_api(self, path, q):
        if path == "/api/items":
            self.json(item_list())
        elif path == "/api/item":
            self.json(item_detail(q.get("key", [""])[0]))
        elif path == "/api/diff":
            self.json(diff(q.get("key", [""])[0], int(q.get("v", ["0"])[0])))
        else:
            self.json({"error": hb.tx("Niet gevonden.", "Not found.")}, 404)

    def post_api(self, path, body):
        if path == "/api/restore":
            try:
                res = restore(str(body.get("key") or ""), int(body.get("v", -1)), bool(body.get("copy")))
                res = res if isinstance(res, dict) else {"message": res, "waitKey": None}
                self.json(dict(res, ok=True))
            except ValueError as e:
                self.json({"error": str(e)}, 400)
            except Exception as e:
                self.json({"error": hb.tx(f"Terugzetten mislukt: {e}", f"Restoring failed: {e}")}, 502)
        elif path == "/api/delete":
            try:
                self.json({"ok": True, "message": delete(str(body.get("key") or ""))})
            except ValueError as e:
                self.json({"error": str(e)}, 400)
            except Exception as e:
                self.json({"error": hb.tx(f"Verwijderen mislukt: {e}", f"Deleting failed: {e}")}, 502)
        elif path == "/api/drop":
            try:
                self.json({"ok": True, "message": drop_version(str(body.get("key") or ""), int(body.get("v", -1)), str(body.get("h") or ""))})
            except ValueError as e:
                self.json({"error": str(e)}, 400)
        elif path == "/api/purge":
            try:
                self.json({"ok": True, "message": purge(str(body.get("key") or ""))})
            except ValueError as e:
                self.json({"error": str(e)}, 400)
            except Exception as e:
                self.json({"error": hb.tx(f"Verwijderen mislukt: {e}", f"Deleting failed: {e}")}, 502)
        elif path == "/api/scan":
            threading.Thread(target=scan, daemon=True).start()
            self.json({"ok": True})
        else:
            self.json({"error": hb.tx("Niet gevonden.", "Not found.")}, 404)


BODY = r"""<div class="wrap">
<div class="top"><div><h1 data-en="Time Machine">Tijdmachine</h1><p class="sum" id="sum"></p></div>
<div class="meta" id="meta"></div></div>
<p class="note" id="note" hidden></p>
<p class="note busy" id="busy" role="status" hidden><i class="spin" aria-hidden="true"></i><span></span></p>
<div class="row2">
<section class="side card">
  <div class="pad" style="display:flex;flex-direction:column;gap:10px;border-bottom:1px solid var(--line2)">
    <label class="field"><span data-en="Search">Zoeken</span><input type="search" id="q" placeholder="Naam van een automation, script of dashboard" data-en-placeholder="Name of an automation, script or dashboard"></label>
    <div class="chips" id="kinds"></div>
  </div>
  <ul class="list" id="items"></ul>
</section>
<section class="main" id="detail"><div class="card empty" data-en="Choose an item on the left.">Kies links een onderdeel.</div></section>
</div>
<p class="faint small" id="foot"></p>
<!--FOOT-->
</div>"""

CSSX = r"""
.it-n{font-weight:500;overflow-wrap:anywhere}
.it-m{display:flex;justify-content:space-between;gap:10px;font-size:13px;color:var(--muted);margin-top:2px}
.it-m .del{color:var(--bad)}
.btn.quiet{border-color:transparent;color:var(--muted)} .btn.quiet:hover{color:var(--bad)}
.vers{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.vers button{width:100%;text-align:left;cursor:pointer;border:1px solid var(--line2);background:transparent;border-radius:10px;padding:9px 12px;
  display:grid;grid-template-columns:150px minmax(0,1fr);gap:12px;align-items:baseline}
.vers button[aria-current="true"]{border-color:var(--selLine);background:var(--sel)}
.vers .w{font-weight:500;font-size:14px} .vers .x{font-size:14px;color:var(--muted);overflow-wrap:anywhere}
.diff{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:13px;line-height:1.6;overflow-x:auto;padding:8px 0}
.diff div{display:grid;grid-template-columns:48px 22px max-content;white-space:pre;padding-right:16px}
.diff .n{color:var(--faint);text-align:right;padding-right:8px;user-select:none}
.diff .a{background:var(--addbg);color:var(--addtx)} .diff .r{background:var(--delbg);color:var(--deltx)} .diff .g{color:var(--faint)}
.dh{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;padding:14px 18px;border-bottom:1px solid var(--line2)}
.busy{display:flex;gap:10px;align-items:center;border-color:var(--selLine);color:var(--text)}
.spin{width:16px;height:16px;flex:none;border-radius:50%;border:2px solid var(--line);border-top-color:var(--accent);animation:sp .8s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.spin{animation-duration:2.4s}}
.btn.danger{color:var(--bad);border-color:var(--bad)}
.acts{display:flex;gap:10px;flex-wrap:wrap;align-items:center;padding:14px 18px;border-top:1px solid var(--line2)}
@media (max-width:640px){.vers button{grid-template-columns:1fr;gap:2px}}
"""

JS = r"""
const APP = 'tijdmachine';
let data = null, kind = 'all', sel = null, ver = null, detail = null;
const KINDS = [['all', t('Alles', 'All')], ['automation', 'Automations'], ['script', 'Scripts'], ['scene', t('Scènes', 'Scenes')], ['dashboard', 'Dashboards'], ['helper', 'Helpers']];
const nVer = n => `${n} ${n === 1 ? t('versie', 'version') : t('versies', 'versions')}`;
function renderList(){
  const q = $('#q').value.trim().toLowerCase();
  const cnt = k => data.items.filter(i => k === 'all' || i.kind === k).length;
  $('#kinds').innerHTML = KINDS.filter(([k]) => k === 'all' || cnt(k)).map(([k, l]) => `<button class="chip" aria-pressed="${kind === k}" data-k="${k}">${l} (${cnt(k)})</button>`).join('');
  document.querySelectorAll('#kinds .chip').forEach(b => b.onclick = () => { kind = b.dataset.k; renderList(); });
  const rows = data.items.filter(i => (kind === 'all' || i.kind === kind) && (!q || i.name.toLowerCase().includes(q)));
  $('#items').innerHTML = rows.map(i => `<li><button class="pick" data-key="${esc(i.key)}" aria-current="${i.key === sel}">
    <div class="it-n">${esc(i.name)}</div><div class="it-m"><span class="${i.deleted ? 'del' : ''}">${esc(i.kindNl)}, ${i.deleted ? t('verwijderd ', 'deleted ') + when(i.deleted) : when(i.last)}</span><span>${nVer(i.count)}</span></div></button></li>`).join('')
    || `<li class="empty">${t('Niets gevonden.', 'Nothing found.')}</li>`;
  if (kind === 'dashboard' && !q) $('#items').insertAdjacentHTML('beforeend', `<li class="empty" style="text-align:left">${t('Ingebouwde dashboards zoals Thuis, Verlichting, Beveiliging, Klimaat en Energie bouwt Home Assistant zelf op. Daar is niets van te bewaren, dus die staan hier niet.', 'Built-in dashboards such as Home, Lights, Security, Climate and Energy are generated by Home Assistant itself. There is nothing to keep, so they are not listed here.')}${data.dashSkipped && data.dashSkipped.length ? ' ' + t('Ook niet bewaard: ', 'Also not kept: ') + esc(data.dashSkipped.join(', ')) + '.' : ''}</li>`);
  document.querySelectorAll('#items .pick').forEach(b => b.onclick = () => open(b.dataset.key));
}
async function open(key, v){
  sel = key; renderList();
  detail = await api('api/item?key=' + encodeURIComponent(key));
  ver = v ?? detail.versions[0].i;
  await renderDetail();
  if (matchMedia('(max-width: 900px)').matches) $('#detail').scrollIntoView({behavior: 'smooth'});
}
async function renderDetail(){
  const d = detail, df = await api(`api/diff?key=${encodeURIComponent(d.key)}&v=${ver}`);
  const cur = d.versions[0].i, isCur = ver === cur && !d.deleted;
  const restoreLbl = d.deleted ? t(`${d.kindNl} terugzetten`, `Restore ${d.kindNl.toLowerCase()}`) : isCur ? t('Vorige versie terugzetten', 'Restore previous version') : t('Deze versie terugzetten', 'Restore this version');
  const target = d.deleted ? ver : isCur ? (d.versions[1] ? d.versions[1].i : null) : ver;
  const lines = df.lines.map(l => `<div class="${l.s === '+' ? 'a' : l.s === '-' ? 'r' : l.s === '…' ? 'g' : ''}"><span class="n">${l.n}</span><span>${l.s === '-' ? '−' : esc(l.s)}</span><span>${esc(l.t)}</span></div>`).join('');
  $('#detail').innerHTML = `<div class="card pad">
      <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-start">
        <div><h2 style="font-size:20px">${esc(d.name)}</h2><p class="muted small" style="margin:4px 0 0">${esc(d.kindNl)}, ${nVer(d.versions.length)} ${t('bewaard', 'kept')}</p>
          ${d.copyOf ? `<p class="small" style="margin:4px 0 0">${t('Kopie van', 'Copy of')} <a href="#" class="jump" data-key="${esc(d.copyOf.key)}">${esc(d.copyOf.name)}</a></p>` : ''}
          ${d.copies.length ? `<p class="small" style="margin:4px 0 0">${t('Kopieën', 'Copies')}: ${d.copies.map(c => `<a href="#" class="jump" data-key="${esc(c.key)}">${esc(c.name)}</a>${c.deleted ? t(' (verwijderd)', ' (deleted)') : ''}`).join(', ')}</p>` : ''}</div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${d.deleted ? `<span class="tag t-bad">${t('Verwijderd', 'Deleted')} ${esc(when(d.deleted))}</span>` : `<a class="btn" href="${esc(HA(d.href))}" target="_top">${t('Openen in Home Assistant', 'Open in Home Assistant')}</a>`}
          ${d.canDelete && !d.copyOf ? `<button class="btn danger" id="del">${t('Verwijderen', 'Delete')}</button>` : ''}
          ${d.canPurge ? `<button class="btn danger" id="purge">${d.deleted ? t('Definitief verwijderen', 'Delete permanently') : t('Kopie definitief verwijderen', 'Delete copy permanently')}</button>` : ''}</div>
      </div>
      <h3 class="muted" style="margin:16px 0 8px;font-size:14px;font-weight:600">${t('Versies', 'Versions')}</h3>
      <ol class="vers">${d.versions.map(v => `<li><button data-v="${v.i}" aria-current="${v.i === ver}"><span class="w">${esc(when(v.t))}</span><span class="x">${esc(v.what)}</span></button></li>`).join('')}</ol>
    </div>
    <div class="card" style="overflow:hidden">
      <div class="dh"><h2 style="font-size:15px">${esc(df.title)}</h2>
        ${df.added || df.removed ? `<span class="small muted"><span style="color:var(--deltx)">− ${df.removed} ${t('weg', 'removed')}</span>, <span style="color:var(--addtx)">+ ${df.added} ${t('nieuw', 'added')}</span></span>` : ''}</div>
      <div class="diff">${lines}</div>
      <div class="acts">
        ${target !== null ? `<button class="btn primary" id="rs">${restoreLbl}</button>` : ''}
        ${target !== null && d.canCopy ? `<button class="btn" id="rc">${t('Als kopie terugzetten', 'Restore as copy')}</button>` : ''}
        ${d.versions.length > 1 && !isCur ? `<button class="btn quiet" id="drop">${t('Deze versie weggooien', 'Discard this version')}</button>` : ''}
        <span class="faint small" style="flex-basis:100%">${target === null ? t('Er is nog maar één versie. Terugzetten kan zodra het onderdeel een keer is gewijzigd.', 'There is only one version so far. You can restore once the item has been changed.') : t('Terugzetten raakt alleen dit onderdeel. De huidige versie blijft bewaard, dus je kunt altijd terug.', 'Restoring only affects this item. The current version is kept, so you can always go back.')}${d.kind === 'automation' ? t(' Een kopie van een automation staat na het maken uit, zodat hij niet naast het origineel draait.', ' A copy of an automation is turned off after it is created, so it does not run alongside the original.') : ''}</span>
      </div></div>`;
  document.querySelectorAll('.vers button').forEach(b => b.onclick = () => { ver = +b.dataset.v; renderDetail(); });
  const go = async copy => {
    const ok = await confirmBox(copy ? t(`Kopie maken van ${d.name}?`, `Make a copy of ${d.name}?`) : t(`${d.name} terugzetten?`, `Restore ${d.name}?`),
      copy ? t('Er komt een nieuw onderdeel bij met deze inhoud. Het origineel blijft zoals het is.', 'A new item with this content is added. The original stays as it is.')
           : t(`De versie van ${when(d.versions.find(v => v.i === target).t)} wordt teruggezet. De huidige versie blijft in de Tijdmachine bewaard.`, `The version from ${when(d.versions.find(v => v.i === target).t)} is restored. The current version stays in Time Machine.`),
      copy ? t('Kopie maken', 'Make copy') : t('Terugzetten', 'Restore'));
    if (!ok) return;
    try {
      const r = await post('api/restore', {key: d.key, v: target, copy});
      if (r.waitKey) waitFor(r.waitKey, copy, r.message); else { toast(r.message); setTimeout(load, 3000); }
    } catch (e) { toast(e.message, true); }
  };
  if ($('#rs')) $('#rs').onclick = () => go(false);
  document.querySelectorAll('#detail .jump').forEach(a => a.onclick = e => { e.preventDefault(); open(a.dataset.key); });
  if ($('#del')) $('#del').onclick = async () => {
    if (!await confirmBox(t(`${d.name} verwijderen?`, `Delete ${d.name}?`), t(`${d.kindNl} wordt uit Home Assistant verwijderd. De Tijdmachine bewaart alle versies, dus je kunt hem later terugzetten.`, `The ${d.kindNl.toLowerCase()} is deleted from Home Assistant. Time Machine keeps all versions, so you can restore it later.`), t('Verwijderen', 'Delete'))) return;
    try { const r = await post('api/delete', {key: d.key}); toast(r.message); setTimeout(load, 3000); } catch (e) { toast(e.message, true); }
  };
  if ($('#rc')) $('#rc').onclick = () => go(true);
  if ($('#drop')) $('#drop').onclick = async () => {
    const v = d.versions.find(x => x.i === ver);
    if (!await confirmBox(t('Deze versie weggooien?', 'Discard this version?'), t(`De versie van ${when(v.t)} wordt uit de Tijdmachine gewist. De andere versies en ${d.deleted ? 'het onderdeel' : 'het onderdeel in Home Assistant'} blijven zoals ze zijn.`, `The version from ${when(v.t)} is erased from Time Machine. The other versions and ${d.deleted ? 'the item' : 'the item in Home Assistant'} stay as they are.`), t('Weggooien', 'Discard'))) return;
    try { const r = await post('api/drop', {key: d.key, v: v.i, h: v.h}); toast(r.message); await open(d.key); } catch (e) { toast(e.message, true); }
  };
  if ($('#purge')) $('#purge').onclick = async () => {
    const txt = d.deleted ? t(`Alle bewaarde versies van ${d.name} worden gewist. Daarna kun je het niet meer terugzetten.`, `All kept versions of ${d.name} are erased. After that you can no longer restore it.`)
                          : t('De kopie wordt uit Home Assistant verwijderd en de Tijdmachine wist alle versies ervan. Het origineel blijft zoals het is. Dit kan niet ongedaan worden.', 'The copy is deleted from Home Assistant and Time Machine erases all its versions. The original stays as it is. This cannot be undone.');
    if (!await confirmBox(d.deleted ? t('Definitief verwijderen?', 'Delete permanently?') : t(`${d.name} definitief verwijderen?`, `Delete ${d.name} permanently?`), txt, t('Definitief verwijderen', 'Delete permanently'))) return;
    try { const r = await post('api/purge', {key: d.key}); toast(r.message); sel = d.copyOf ? d.copyOf.key : null; ver = null; await load(); } catch (e) { toast(e.message, true); }
  };
}
async function waitFor(key, copy, doneMsg){
  const bar = $('#busy'), txt = copy ? t('De kopie wordt aangemaakt. Home Assistant verwerkt de wijziging; dat duurt meestal een paar seconden.', 'The copy is being created. Home Assistant is processing the change; this usually takes a few seconds.')
                                     : t('Het onderdeel wordt teruggezet. Home Assistant laadt het opnieuw; dat duurt meestal een paar seconden.', 'The item is being restored. Home Assistant is reloading it; this usually takes a few seconds.');
  const slow = t('Het duurt wat langer dan normaal, even geduld.', 'This is taking a bit longer than usual, please wait.');
  bar.querySelector('span').textContent = txt; bar.hidden = false;
  const t0 = Date.now();
  while (Date.now() - t0 < 60000) {
    await new Promise(r => setTimeout(r, 1500));
    if (Date.now() - t0 > 12000) bar.querySelector('span').textContent = txt.replace(/;.*$/, '. ') + slow;
    try {
      const d = await api('api/items'); const it = d.items.find(i => i.key === key);
      if (it && !it.deleted) { bar.hidden = true; data = d; toast(copy ? doneMsg.replace(/ (Je vindt hem bij|You can find it under) .*$/, '') + t(' Hier is hij.', ' Here it is.') : doneMsg); renderList(); open(key); return; }
    } catch (e) {}
  }
  bar.querySelector('span').textContent = t('Het aanmaken duurt langer dan verwacht. Het onderdeel verschijnt vanzelf in de lijst zodra Home Assistant klaar is.', 'Creating takes longer than expected. The item appears in the list by itself as soon as Home Assistant is done.');
  setTimeout(() => { bar.hidden = true; load(); }, 8000);
}
async function load(){
  try { data = await api('api/items'); } catch (e) { $('#note').textContent = e.message; $('#note').className = 'note err'; $('#note').hidden = false; return; }
  const since = new Date(data.since * 1000).toLocaleDateString(LOCALE, {day: 'numeric', month: 'long'});
  $('#sum').textContent = data.items.length ? t(`${data.total} versies van ${data.items.length} onderdelen bewaard, sinds ${since}.`, `${data.total} versions of ${data.items.length} items kept, since ${since}.`) : t('Nog geen versies. De eerste worden nu opgeslagen.', 'No versions yet. The first ones are being saved now.');
  $('#meta').textContent = data.lastScan ? t('Laatst gecontroleerd ', 'Last checked ') + when(data.lastScan) : '';
  const n = $('#note');
  if (data.demo) { n.textContent = t('Je bekijkt voorbeeldgegevens. Als Home Assistant-app bewaart de Tijdmachine je eigen configuratie.', 'You are looking at sample data. As a Home Assistant app, Time Machine keeps your own configuration.'); n.className = 'note'; n.hidden = false; }
  else if (data.error) { n.textContent = data.error; n.className = 'note err'; n.hidden = false; } else n.hidden = true;
  $('#foot').textContent = t(`Versies blijven ${data.keepDays} dagen bewaard, en per onderdeel altijd de laatste ${data.keepMin}. Dat stel je in bij de instellingen van de app.`, `Versions are kept for ${data.keepDays} days, and always the last ${data.keepMin} per item. You can change this in the app settings.`);
  renderList();
  if (sel && data.items.some(i => i.key === sel)) open(sel, ver); else if (!sel && data.items.length && !matchMedia('(max-width: 900px)').matches) open(data.items[0].key);
}
$('#q').addEventListener('input', () => data && renderList());
// Geopend vanuit Maintenance (Onderhoud) na het verwijderen van een automation: dat onderdeel meteen tonen.
function handoff(){
  try { const v = JSON.parse(localStorage.getItem('fonfey.tijdmachine.open') || 'null'); localStorage.removeItem('fonfey.tijdmachine.open');
    return v && v.key && Date.now() - v.t < 600000 ? String(v.key) : null; } catch (e) { return null; }
}
async function start(){
  const want = handoff();
  if (!want) return load();
  const bar = $('#busy'); bar.querySelector('span').textContent = t('De verwijderde automation opzoeken.', 'Looking up the deleted automation.'); bar.hidden = false;
  try { await post('api/scan'); } catch (e) {}
  const t0 = Date.now(); let found = null;
  while (Date.now() - t0 < 20000) {
    await new Promise(r => setTimeout(r, 1500));
    try { const d = await api('api/items'); found = d.items.find(i => i.key === want); if (found && (found.deleted || d.demo)) break; } catch (e) {}
  }
  bar.hidden = true;
  if (found) { sel = want; ver = null; } else toast(t('Deze automation staat niet in de Tijdmachine. Hij is waarschijnlijk gemaakt en verwijderd voordat de Tijdmachine er een versie van kon bewaren.', 'This automation is not in Time Machine. It was probably created and deleted before Time Machine could keep a version of it.'), true);
  await load();
}
start(); setInterval(() => { if (document.visibilityState === 'visible' && $('#modal').hidden) api('api/items').then(d => { data = d; renderList(); }).catch(() => {}); }, 60000);
"""

Handler.PAGE = hb.page("Time Machine", BODY, CSSX, JS)


def main():
    if hb.DEMO:
        demo_data()
    else:
        load_all()
        if not hb.NO_TOKEN:
            hb.start_thread(scan)
            hb.start_thread(scan_loop)
            hb.start_thread(hb.listen_forever, ["automation_reloaded", "scene_reloaded", "lovelace_updated", "call_service",
                                                "entity_registry_updated"], on_event)
        else:
            STATUS["error"] = hb.L("De app krijgt geen toegang tot Home Assistant. Herstart de app; helpt dat niet, installeer hem opnieuw.", "The app has no access to Home Assistant. Restart the app; if that does not help, reinstall it.")
    hb.serve(Handler, port_local=8091)


if __name__ == "__main__":
    main()
