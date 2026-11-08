#!/usr/bin/env python3
"""
Meldingen voor Home Assistant
=============================

Houdt bij welke meldingen Home Assistant verstuurt: wanneer, naar wie, door
welke automation of welk script, en wat ermee gebeurde (een knop gekozen,
weggeveegd op Android, of mislukt). Alleen kijken; de app verstuurt zelf niets.

Los starten zonder Home Assistant toont voorbeeldgegevens:  python3 notifications.py --demo
"""

import os
import re
import threading
import time
import uuid
from datetime import datetime

import ha_base as hb

KEEP_DAYS = hb.opt_int("keep_days", 30, 1, 365)
STORE = os.path.join(hb.data_dir(__file__), "meldingen.json")
MAX_ITEMS = 5000

LOCK = threading.RLock()
ITEMS = []        # nieuwste achteraan
CTX = {}          # context-id -> {"kind","name","entity","t"}
STATUS = {"since": None, "connected": False, "error": None}
_DIRTY = threading.Event()
_cache = {"t": 0, "states": {}}


# ---------------------------------------------------------------- hulpjes
def states():
    if time.time() - _cache["t"] > 300 and not hb.DEMO:
        try:
            _cache.update(t=time.time(), states={s["entity_id"]: s for s in hb.core("GET", "/states")})
        except Exception:
            _cache["t"] = time.time() - 240
    return _cache["states"]


def friendly(eid):
    s = states().get(eid)
    return (s or {}).get("attributes", {}).get("friendly_name") or eid


def person_name(user_id):
    for s in states().values():
        if s["entity_id"].startswith("person.") and s["attributes"].get("user_id") == user_id:
            return s["attributes"].get("friendly_name")
    return None


def pretty_service(svc):
    name = svc.split(".", 1)[-1]
    if name.startswith("mobile_app_"):
        name = name[len("mobile_app_"):]
    return name.replace("_", " ").strip().capitalize() or svc


SIDEBAR = hb.L("Home Assistant (zijbalk)", "Home Assistant (sidebar)")


def recipient(domain, service, data):
    if domain == "persistent_notification":
        return SIDEBAR
    if service == "send_message":
        ents = data.get("entity_id") or (data.get("target") or {}).get("entity_id") or []
        ents = [ents] if isinstance(ents, str) else ents
        return ", ".join(friendly(e) for e in ents) or hb.L("Meldingsentiteit", "Notification entity")
    if service == "notify":
        return hb.L("Alle apparaten (notify.notify)", "All devices (notify.notify)")
    if service == "persistent_notification":
        return SIDEBAR
    return pretty_service(f"{domain}.{service}")


def source(ctx):
    for key in ("id", "parent_id"):
        c = CTX.get((ctx or {}).get(key) or "")
        if c:
            return c
    uid = (ctx or {}).get("user_id")
    if uid:
        who = person_name(uid)
        return {"kind": "user", "name": hb.L(f"{who or 'Iemand'} (handmatig)", f"{who or 'Someone'} (manually)"), "entity": None}
    return {"kind": "other", "name": hb.L("Onbekend (bijvoorbeeld een integratie)", "Unknown (for example an integration)"), "entity": None}


def edit_link(entity):
    if not entity:
        return None
    dom, oid = entity.split(".", 1)
    if dom == "automation":
        aid = (states().get(entity) or {}).get("attributes", {}).get("id")
        return f"/config/automation/trace/{aid}" if aid else "/config/automation/dashboard"
    if dom == "script":
        return f"/config/script/trace/{oid}"
    return None


def ts(ev):
    try:
        return datetime.fromisoformat(str(ev.get("time_fired")).replace("Z", "+00:00")).timestamp()
    except Exception:
        return time.time()


def add(item):
    with LOCK:
        ITEMS.append(item)
        cut = time.time() - KEEP_DAYS * 86400
        while ITEMS and (ITEMS[0]["t"] < cut or len(ITEMS) > MAX_ITEMS):
            ITEMS.pop(0)
    _DIRTY.set()


# Opgeslagen meldingen van voor versie 1.1: vaste Nederlandse teksten omzetten naar beide talen.
_OLD = {"Home Assistant (zijbalk)": SIDEBAR, "Meldingsentiteit": hb.L("Meldingsentiteit", "Notification entity"),
        "Alle apparaten (notify.notify)": hb.L("Alle apparaten (notify.notify)", "All devices (notify.notify)"),
        "Onbekend (bijvoorbeeld een integratie)": hb.L("Onbekend (bijvoorbeeld een integratie)", "Unknown (for example an integration)"),
        "Onbekend": hb.L("Onbekend", "Unknown"), "Verstuurd": hb.L("Verstuurd", "Sent"), "Weggeveegd": hb.L("Weggeveegd", "Dismissed"),
        "Mislukt: Deze meldingsdienst bestaat niet (meer)": hb.L("Mislukt: Deze meldingsdienst bestaat niet (meer)",
                                                                 "Failed: this notification service does not exist (anymore)")}


def migrate(it):
    for k in ("to", "src", "result"):
        v = it.get(k)
        if isinstance(v, str):
            if v in _OLD:
                it[k] = _OLD[v]
            elif k == "result" and v.endswith(" gekozen"):
                it[k] = hb.L(v, v[:-8] + " chosen")
            elif k == "src" and v.endswith(" (handmatig)"):
                it[k] = hb.L(v, v[:-12].replace("Iemand", "Someone") + " (manually)")
            elif k == "result" and v.startswith("Mislukt: "):
                it[k] = hb.L(v, "Failed: " + v[9:])
    return it


# ---------------------------------------------------------------- gebeurtenissen
FAIL_RE = re.compile(r"\b(notify\.[a-z0-9_]+)")


def on_event(ev):
    et, data, ctx = ev.get("event_type"), ev.get("data") or {}, ev.get("context") or {}
    now = ts(ev)
    if et in ("automation_triggered", "script_started"):
        CTX[ctx.get("id")] = {"kind": "automation" if et == "automation_triggered" else "script",
                              "name": data.get("name") or friendly(data.get("entity_id", "")), "entity": data.get("entity_id"), "t": now}
        if len(CTX) > 4000:
            for k in sorted(CTX, key=lambda k: CTX[k]["t"])[:1000]:
                CTX.pop(k, None)
    elif et == "call_service":
        dom, svc = data.get("domain"), data.get("service")
        if not (dom == "notify" or (dom == "persistent_notification" and svc == "create")):
            return
        sd = data.get("service_data") or {}
        extra = sd.get("data") if isinstance(sd.get("data"), dict) else {}
        acts = [a for a in (extra.get("actions") or []) if isinstance(a, dict)]
        src = source(ctx)
        add({"id": uuid.uuid4().hex[:12], "t": now, "service": f"{dom}.{svc}", "to": recipient(dom, svc, sd),
             "title": str(sd.get("title") or ""), "message": str(sd.get("message") or ""),
             "actions": [str(a.get("title") or a.get("action")) for a in acts], "actionIds": [str(a.get("action")) for a in acts],
             "tag": str(extra.get("tag") or ""), "src": src["name"], "srcKind": src["kind"], "srcLink": edit_link(src.get("entity")),
             "status": "sent", "result": hb.L("Verstuurd", "Sent")})
    elif et == "mobile_app_notification_action":
        action = str(data.get("action") or "")
        with LOCK:
            for it in reversed(ITEMS):
                if action and action in it.get("actionIds", []) and now - it["t"] < 7 * 86400:
                    label = it["actions"][it["actionIds"].index(action)]
                    it.update(status="action", result=hb.L(f"{label} gekozen", f"{label} chosen"), rt=now)
                    break
        _DIRTY.set()
    elif et == "mobile_app_notification_cleared":
        tag, msg, title = str(data.get("tag") or ""), str(data.get("message") or ""), str(data.get("title") or "")
        with LOCK:
            for it in reversed(ITEMS):
                if it["status"] == "sent" and ((tag and it.get("tag") == tag) or (msg and it["message"] == msg and it["title"] == title)):
                    it.update(status="cleared", result=hb.L("Weggeveegd", "Dismissed"), rt=now)
                    break
        _DIRTY.set()
    elif et == "system_log_event":
        text = " ".join(map(str, data.get("message") or []))
        m = FAIL_RE.search(text)
        if not m or data.get("level") not in ("ERROR", "WARNING"):
            return
        logger = str(data.get("name") or "")
        src_name, link = hb.L("Onbekend", "Unknown"), None
        if ".automation." in logger or logger.startswith("homeassistant.components.automation"):
            ent = "automation." + logger.rsplit(".", 1)[-1]
            src_name, link = friendly(ent), edit_link(ent)
        elif ".script." in logger:
            ent = "script." + logger.rsplit(".", 1)[-1]
            src_name, link = friendly(ent), edit_link(ent)
        missing = re.search(r"not found|niet gevonden|Unable to find", text, re.I)
        with LOCK:
            if any(it["status"] == "failed" and it["service"] == m.group(1) and now - it["t"] < 60 for it in ITEMS[-20:]):
                return
        add({"id": uuid.uuid4().hex[:12], "t": now, "service": m.group(1), "to": pretty_service(m.group(1)), "title": "", "message": "",
             "actions": [], "actionIds": [], "tag": "", "src": src_name, "srcKind": "automation" if link else "other", "srcLink": link,
             "status": "failed", "result": hb.L("Mislukt: Deze meldingsdienst bestaat niet (meer)", "Failed: this notification service does not exist (anymore)")
             if missing else hb.L(f"Mislukt: {text[:200]}", f"Failed: {text[:200]}")})


def saver():
    while True:
        _DIRTY.wait()
        time.sleep(2)
        _DIRTY.clear()
        with LOCK:
            snapshot = list(ITEMS)
        try:
            hb.save_json(STORE, {"since": STATUS["since"], "items": snapshot})
        except Exception as e:
            print(f"Opslaan mislukt: {e}", flush=True)


def on_connect(first):
    STATUS["connected"] = True
    STATUS["error"] = None


# ---------------------------------------------------------------- overzicht
def overview(days):
    cut = time.time() - days * 86400
    with LOCK:
        rows = [hb.resolve(dict(i)) for i in ITEMS if i["t"] >= cut]
    rows.sort(key=lambda r: -r["t"])
    per, bursts, fails = {}, {}, {}
    for r in rows:
        if r["status"] != "failed":
            per[r["src"]] = per.get(r["src"], 0) + 1
        else:
            k = (r["service"], r["src"])
            fails[k] = fails.get(k, 0) + 1
    by_src = {}
    for r in sorted(rows, key=lambda r: r["t"]):
        by_src.setdefault(r["src"], []).append(r["t"])
    for src, times in by_src.items():
        # reeksen meldingen die elk binnen 10 minuten na de vorige kwamen; 3 of meer telt als vlak na elkaar
        n, run = 0, 1
        for a, b in zip(times, times[1:] + [None]):
            if b is not None and b - a <= 600:
                run += 1
                continue
            n += run if run >= 3 else 0
            run = 1
        if n:
            bursts[src] = n
    top = sorted(per.items(), key=lambda x: -x[1])[:6]
    return {"items": rows[:600], "total": len(rows), "top": [{"name": k, "n": v, "burst": bursts.get(k, 0)} for k, v in top],
            "fails": [{"service": s, "src": src, "n": n} for (s, src), n in sorted(fails.items(), key=lambda x: -x[1])],
            "recipients": sorted({r["to"] for r in rows}), "since": STATUS["since"], "demo": hb.DEMO, "days": days,
            "error": STATUS["error"], "keepDays": KEEP_DAYS}


# ---------------------------------------------------------------- voorbeeldgegevens
def demo_data():
    now = time.time()
    H, D = 3600, 86400
    STATUS["since"] = now - 20 * D
    L = hb.L
    SENT, CLEARED = L("Verstuurd", "Sent"), L("Weggeveegd", "Dismissed")

    def it(ago, title, msg, to, src, status, result, actions=(), link="/config/automation/dashboard"):
        ITEMS.append({"id": uuid.uuid4().hex[:12], "t": now - ago, "service": "notify.mobile_app", "to": to, "title": title, "message": msg,
                      "actions": list(actions), "actionIds": [f"demo_{i}" for i, _ in enumerate(actions)], "tag": "", "src": src, "srcKind": "automation",
                      "srcLink": link, "status": status, "result": result})
    phone = L("Telefoon", "Phone")
    for k in range(14):
        it(2 * D + k * 400 if k < 9 else (k - 8) * D, L("Wasmachine klaar", "Washing machine done"), L("De was is klaar om op te hangen.", "The laundry is ready to hang up."),
           phone, L("Wasmachine klaar", "Washing machine done"), "cleared" if k % 3 == 0 else "sent", CLEARED if k % 3 == 0 else SENT)
    for k in range(12):
        it(k * 13 * H + 5 * H, L("Thuisbatterij " + ("ontlaadt" if k % 2 else "laadt"), "Home battery " + ("discharging" if k % 2 else "charging")),
           L("Stroom is nu " + ("0,31" if k % 2 else "0,08") + " euro per kWh.", "Electricity is now " + ("0.31" if k % 2 else "0.08") + " euro per kWh."),
           L("Wandtablet", "Wall tablet"), L("Marstek - Slim op stroomprijs", "Marstek - Smart on electricity price"), "sent", SENT)
    for k in range(8):
        it(k * 19 * H + 2 * H, L("Voordeur open", "Front door open"), L("De voordeur staat al 10 minuten open.", "The front door has been open for 10 minutes."),
           "iPad", L("Deur te lang open", "Door open too long"), "action", L("Negeren gekozen", "Ignore chosen"), actions=(L("Negeren", "Ignore"), L("Herinner me", "Remind me")))
    for k in range(4):
        it(k * 2 * D + 18 * H, L("Afval buiten zetten", "Put the bins out"), L("Morgen wordt het gft opgehaald.", "The green waste is collected tomorrow."),
           phone, L("Melding afval buiten zetten", "Bin collection reminder"), "action" if k else "sent",
           L("Gedaan gekozen", "Done chosen") if k else SENT, actions=(L("Gedaan", "Done"), L("Herinner me om 21:30", "Remind me at 21:30")))
    it(5 * D, L("Batterij bijna leeg", "Battery almost empty"), L("Deursensor achterdeur: 9%.", "Back door sensor: 9%."), phone,
       L("Batterijen controleren", "Check batteries"), "sent", SENT)
    for k in range(2):
        ITEMS.append({"id": uuid.uuid4().hex[:12], "t": now - (1 * D + 3 * H + k * 600), "service": "notify.mobile_app_oude_telefoon",
                      "to": L("Oude telefoon", "Old phone"), "title": "", "message": "", "actions": [], "actionIds": [], "tag": "",
                      "src": L("Waterlek badkamer", "Water leak bathroom"), "srcKind": "automation", "srcLink": "/config/automation/dashboard",
                      "status": "failed", "result": _OLD["Mislukt: Deze meldingsdienst bestaat niet (meer)"]})
    ITEMS.sort(key=lambda r: r["t"])


# ---------------------------------------------------------------- webserver
class Handler(hb.BaseHandler):
    APP = "meldingen"

    def get_api(self, path, q):
        if path == "/api/list":
            days = 30 if q.get("days", ["7"])[0] == "30" else 7
            self.json(overview(days))
        else:
            self.json({"error": hb.tx("Niet gevonden.", "Not found.")}, 404)


BODY = r"""<div class="wrap">
<div class="top"><div><h1 data-en="Notifications">Meldingen</h1><p class="sum" id="sum"></p></div><div class="chips" id="days"></div></div>
<p class="note" id="note" hidden></p>
<div class="card pad" style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end">
  <label class="field" style="flex:1 1 260px"><span data-en="Search">Zoeken</span><input type="search" id="q" placeholder="Tekst, titel of automation" data-en-placeholder="Text, title or automation"></label>
  <label class="field"><span data-en="To">Naar</span><select id="to"></select></label>
  <div class="chips" id="flt"></div>
</div>
<div class="row2">
  <section class="main"><div class="card" style="overflow:hidden"><ol class="ml" id="list"></ol></div></section>
  <aside class="side" style="display:flex;flex-direction:column;gap:16px">
    <div class="card pad" id="det"></div>
    <div class="card pad"><h2 data-en="Most notifications">Meeste meldingen</h2><p class="small muted" id="topSub" style="margin:2px 0 10px"></p><div id="top"></div><p class="small" id="burst" style="color:var(--warn);margin:10px 0 0" hidden></p></div>
    <div class="card pad" id="failBox" hidden><h2 style="margin-bottom:6px" data-en="Did not arrive">Kwamen niet aan</h2><div id="fails"></div></div>
  </aside>
</div>
<p class="faint small" id="foot"></p>
<!--FOOT-->
</div>"""

CSSX = r"""
.ml{list-style:none;margin:0;padding:0}
.ml li{border-top:1px solid var(--line2)} .ml li:first-child{border-top:0}
.ml button{width:100%;text-align:left;border:0;background:transparent;cursor:pointer;padding:12px 16px;display:grid;grid-template-columns:110px minmax(0,1fr) auto;gap:3px 14px;align-items:start}
.ml button:hover{background:var(--sel)} .ml button[aria-current="true"]{background:var(--sel);box-shadow:inset 3px 0 0 var(--selLine)}
.ml .w{font-size:13px;color:var(--muted);line-height:1.5}
.ml .tt{display:block;font-weight:500} .ml .mm{display:block;font-size:14px;color:var(--muted);margin-top:2px;overflow-wrap:anywhere}
.ml .tf{display:block;font-size:12px;color:var(--faint);margin-top:3px}
.bar{display:grid;grid-template-columns:minmax(0,1.3fr) 1fr 28px;gap:10px;align-items:center;padding:5px 0;font-size:14px}
.bar .b{height:6px;border-radius:3px;background:var(--line2);overflow:hidden} .bar .b i{display:block;height:100%;border-radius:3px;background:var(--info)}
@media (max-width:640px){.ml button{grid-template-columns:minmax(0,1fr) auto}.ml .w{grid-column:1 / -1;order:-1}}
"""

JS = r"""
const APP = 'meldingen';
let data = null, flt = 'all', sel = null, days = 7, shown = 60;
const ST = {sent: [t('Verstuurd', 'Sent'), 't-info'], action: [t('Knop gekozen', 'Button chosen'), 't-ok'], cleared: [t('Weggeveegd', 'Dismissed'), 't-muted'], failed: [t('Mislukt', 'Failed'), 't-bad']};
const FLT = [['all', t('Alles', 'All')], ['action', ST.action[0]], ['failed', ST.failed[0]], ['cleared', ST.cleared[0]]];
const dateL = s => new Date(s * 1000).toLocaleDateString(LOCALE, {day: 'numeric', month: 'long'});
function rows(){ const q = $('#q').value.trim().toLowerCase(), to = $('#to').value;
  return data.items.filter(r => (flt === 'all' || r.status === flt) && (!to || r.to === to) && (!q || [r.title, r.message, r.src, r.to, r.service].join(' ').toLowerCase().includes(q))); }
function render(){
  const rs = rows();
  $('#flt').innerHTML = FLT.map(([k, l]) => `<button class="chip" aria-pressed="${flt === k}" data-f="${k}">${l}</button>`).join('');
  document.querySelectorAll('#flt .chip').forEach(b => b.onclick = () => { flt = b.dataset.f; sel = null; render(); });
  if (!sel || !rs.some(r => r.id === sel)) sel = rs[0] && rs[0].id;
  $('#list').innerHTML = rs.slice(0, shown).map(r => `<li><button data-id="${r.id}" aria-current="${r.id === sel}"><span class="w">${esc(when(r.t))}</span>
    <span style="min-width:0"><span class="tt">${esc(r.title || (r.status === 'failed' ? r.service : t('Melding zonder titel', 'Notification without title')))}</span>${r.message ? `<span class="mm">${esc(r.message)}</span>` : ''}
    <span class="tf">${t('Naar', 'To')} ${esc(r.to)}, ${t('door', 'by')} ${esc(r.src)}</span></span><span class="tag ${ST[r.status][1]}">${ST[r.status][0]}</span></button></li>`).join('')
    || `<li class="empty">${data.total ? t('Niets gevonden met dit filter.', 'Nothing found with this filter.') : t('Nog geen meldingen gezien. Zodra Home Assistant er een verstuurt, staat hij hier.', 'No notifications seen yet. As soon as Home Assistant sends one, it shows up here.')}</li>`;
  if (rs.length > shown) $('#list').insertAdjacentHTML('beforeend', `<li style="padding:12px 16px"><button class="btn" id="more">${t('Meer tonen', 'Show more')} (${rs.length - shown})</button></li>`);
  if ($('#more')) $('#more').onclick = () => { shown += 100; render(); };
  document.querySelectorAll('#list button[data-id]').forEach(b => b.onclick = () => { sel = b.dataset.id; render(); });
  const r = rs.find(x => x.id === sel);
  $('#det').innerHTML = r ? `<h2>${esc(r.title || r.service)}</h2>${r.message ? `<p class="muted" style="margin:6px 0 0;font-size:14px">${esc(r.message)}</p>` : ''}
    <dl class="kv" style="margin-top:12px"><dt>${t('Verstuurd', 'Sent')}</dt><dd>${esc(when(r.t))}</dd><dt>${t('Naar', 'To')}</dt><dd>${esc(r.to)} <span class="faint small">(${esc(r.service)})</span></dd>
    <dt>${t('Door', 'By')}</dt><dd>${r.srcLink ? `<a href="${esc(HA(r.srcLink))}" target="_top">${esc(r.src)}</a>` : esc(r.src)}</dd>
    <dt>${t('Knoppen', 'Buttons')}</dt><dd>${r.actions.length ? esc(r.actions.join(', ')) : t('Geen', 'None')}</dd>
    <dt>${t('Resultaat', 'Result')}</dt><dd style="color:${r.status === 'failed' ? 'var(--bad)' : 'inherit'}">${esc(r.result)}${r.rt ? ', ' + esc(when(r.rt)) : ''}</dd></dl>
    ${r.srcLink ? `<div style="margin-top:14px"><a class="btn primary" href="${esc(HA(r.srcLink))}" target="_top">${t('Trace bekijken', 'View trace')}</a></div>` : ''}`
    : `<p class="muted" style="margin:0">${t('Kies een melding.', 'Choose a notification.')}</p>`;
}
function renderSide(){
  const max = Math.max(1, ...data.top.map(x => x.n));
  $('#topSub').textContent = t(`Per automation of script, afgelopen ${days} dagen`, `Per automation or script, last ${days} days`);
  $('#top').innerHTML = data.top.map(x => `<div class="bar"><span class="muted" style="overflow-wrap:anywhere">${esc(x.name)}</span><span class="b"><i style="width:${x.n / max * 100}%;${x.burst ? 'background:var(--warn)' : ''}"></i></span><span style="text-align:right">${x.n}</span></div>`).join('') || `<p class="muted small" style="margin:0">${t('Nog niets.', 'Nothing yet.')}</p>`;
  const b = data.top.find(x => x.burst);
  $('#burst').hidden = !b; if (b) $('#burst').textContent = t(`${b.name} stuurde ${b.n} meldingen, waarvan ${b.burst} vlak na elkaar (steeds binnen 10 minuten na de vorige).`,
    `${b.name} sent ${b.n} notifications, ${b.burst} of them in quick succession (each within 10 minutes of the previous one).`);
  $('#failBox').hidden = !data.fails.length;
  $('#fails').innerHTML = data.fails.map(f => `<p style="margin:0 0 8px;font-size:14px"><b style="color:var(--bad)">${f.n}×</b> <code>${esc(f.service)}</code> ${t('bestaat niet of gaf een fout. Gebruikt in', 'does not exist or returned an error. Used in')} ${esc(f.src)}.</p>`).join('');
}
async function load(){
  try { data = await api('api/list?days=' + days); } catch (e) { const n = $('#note'); n.textContent = e.message; n.className = 'note err'; n.hidden = false; return; }
  const failed = data.items.filter(r => r.status === 'failed').length, rec = data.recipients.length;
  $('#sum').textContent = data.total
    ? t(`Afgelopen ${days} dagen: ${data.total} meldingen naar ${rec} ${rec === 1 ? 'ontvanger' : 'ontvangers'}${failed ? `. ${failed} kwamen niet aan` : ''}.`,
        `Last ${days} days: ${data.total} notifications to ${rec} ${rec === 1 ? 'recipient' : 'recipients'}${failed ? `. ${failed} did not arrive` : ''}.`)
    : t('Nog geen meldingen gezien.', 'No notifications seen yet.');
  $('#days').innerHTML = [7, 30].map(d => `<button class="chip" aria-pressed="${days === d}" data-d="${d}">${d} ${t('dagen', 'days')}</button>`).join('');
  document.querySelectorAll('#days .chip').forEach(b => b.onclick = () => { days = +b.dataset.d; load(); });
  const to = $('#to'), cur = to.value;
  to.innerHTML = `<option value="">${t('Alle ontvangers', 'All recipients')}</option>` + data.recipients.map(r => `<option>${esc(r)}</option>`).join(''); to.value = cur;
  const n = $('#note');
  if (data.demo) { n.textContent = t('Je bekijkt voorbeeldgegevens. Als Home Assistant-app zie je de meldingen die je eigen Home Assistant verstuurt.', 'You are looking at sample data. As a Home Assistant app you see the notifications your own Home Assistant sends.'); n.className = 'note'; n.hidden = false; }
  else if (data.error) { n.textContent = data.error; n.className = 'note err'; n.hidden = false; } else n.hidden = true;
  const since = data.since ? dateL(data.since) : t('de installatie', 'installation');
  $('#foot').textContent = t(`Meldingen worden bijgehouden sinds ${since} en ${data.keepDays} dagen bewaard. Of een melding is gelezen, weet Home Assistant niet; wel of hij is verstuurd, mislukt, weggeveegd (Android) of dat er op een knop is gedrukt.`,
    `Notifications are tracked since ${since} and kept for ${data.keepDays} days. Home Assistant does not know whether a notification was read, but it does know whether it was sent, failed, dismissed (Android) or whether a button was pressed.`);
  render(); renderSide();
}
$('#q').addEventListener('input', () => { shown = 60; data && render(); });
$('#to').addEventListener('change', () => data && render());
load(); setInterval(() => { if (document.visibilityState === 'visible') load(); }, 30000);
"""

Handler.PAGE = hb.page("Notifications", BODY, CSSX, JS)


def main():
    if hb.DEMO:
        demo_data()
    else:
        saved = hb.load_json(STORE, {})
        ITEMS.extend(migrate(i) for i in (saved.get("items") or []))
        STATUS["since"] = saved.get("since") or time.time()
        hb.save_json(STORE, {"since": STATUS["since"], "items": ITEMS})
        if hb.NO_TOKEN:
            STATUS["error"] = hb.L("De app krijgt geen toegang tot Home Assistant. Herstart de app.",
                                   "The app has no access to Home Assistant. Restart the app.")
        else:
            hb.start_thread(saver)
            hb.start_thread(hb.listen_forever, ["call_service", "automation_triggered", "script_started", "mobile_app_notification_action",
                                                "mobile_app_notification_cleared", "system_log_event"], on_event, on_connect)
    hb.serve(Handler, port_local=8093)


if __name__ == "__main__":
    main()
