#!/usr/bin/env python3
"""
Media manager voor Home Assistant
=================================

Toont de bestanden in de map /media van Home Assistant met echte miniaturen,
de volledige naam en de datum. Je kunt bestanden uploaden, hernoemen,
verplaatsen, downloaden en verwijderen. Verwijderen gaat eerst naar een
prullenbak, zodat je een vergissing kunt terugdraaien.

Los starten zonder Home Assistant gebruikt de map demo_media naast dit bestand:
    python3 mediabeheer.py --demo
"""

import hashlib
import mimetypes
import os
import re
import shutil
import threading
import time
import urllib.parse
import uuid
import zipfile
from datetime import datetime, timezone

import ha_base as hb

try:
    from PIL import Image, ImageOps, features
    PIL_OK = True
    try:  # HEIC-foto's van iPhones, alleen als de bibliotheek er is
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
        HEIC_OK = True
    except Exception:
        HEIC_OK = False
except Exception:  # zonder Pillow werkt alles behalve de miniaturen
    PIL_OK = HEIC_OK = False

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWED_BASES = ("/media", "/share")   # de mappen die Home Assistant aan de app geeft (zie map: in config.yaml)
ROOT_ERROR = None


def choose_root():
    """De map uit de instelling 'folder': /media (standaard), een map daarin, of een map in /share."""
    global ROOT_ERROR
    if not hb.HA_APP:
        root = os.environ.get("MEDIA_DIR", os.path.join(HERE, "demo_media"))
        os.makedirs(root, exist_ok=True)
        return os.path.realpath(root), "media", "/media"
    want = "/" + str(hb.OPTS.get("folder") or "/media").strip().strip("/")
    real = os.path.realpath(want)
    base = next((b for b in ALLOWED_BASES if real == b or real.startswith(b + "/")), None)
    if not base:
        ROOT_ERROR = hb.L(f"De map '{want}' kan niet. Kies /media, een map in /media of een map in /share. Nu wordt /media gebruikt.",
                          f"The folder '{want}' is not allowed. Choose /media, a folder in /media or a folder in /share. Using /media for now.")
        real, want = "/media", "/media"
    elif not os.path.isdir(real):
        ROOT_ERROR = hb.L(f"De map '{want}' bestaat niet. Maak hem aan of kies een andere map in de instellingen. Nu wordt /media gebruikt.",
                          f"The folder '{want}' does not exist. Create it or choose another folder in the settings. Using /media for now.")
        real, want = "/media", "/media"
    return real, want.lstrip("/"), want


ROOT, ROOT_LABEL, ROOT_FS = choose_root()   # echte map, naam in de pagina, pad zoals Home Assistant het ziet
# media-source-adressen kloppen alleen voor de standaardmap van Home Assistant (/media = 'local')
MS_BASE = ("media-source://media_source/local/" + ROOT_FS[len("/media"):].strip("/") + "/").replace("local//", "local/") \
    if (ROOT_FS == "/media" or ROOT_FS.startswith("/media/")) else None
DATA = hb.data_dir(__file__)
THUMBS = os.path.join(DATA, "thumbs")
TRASH = os.path.join(DATA, "trash")
INDEX_FILE = os.path.join(DATA, "index.json")
TRASH_FILE = os.path.join(DATA, "trash.json")
for _d in (THUMBS, TRASH):
    os.makedirs(_d, exist_ok=True)

TRASH_DAYS = hb.opt_int("trash_days", 30, 0, 365)
THUMB_SIDE = 360                  # kortste zijde van een miniatuur, scherp op een retinascherm
MAX_UPLOAD = 8 * 1024 ** 3        # 8 GB per bestand
MAX_CHUNK = 16 * 1024 ** 2        # de pagina stuurt stukken van 4 MB
PART_PREFIX = ".mm-upload-"       # half geüploade bestanden; Home Assistant toont geen namen met een punt
THUMB_FMT = "WEBP" if PIL_OK and features.check("webp") else "JPEG"
THUMB_TYPE = "image/webp" if THUMB_FMT == "WEBP" else "image/jpeg"

IMG = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "heic", "heif", "avif", "svg"}
THUMBABLE = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff"} | ({"heic", "heif"} if HEIC_OK else set())
VID = {"mp4", "m4v", "mov", "webm", "mkv", "avi", "3gp"}
AUD = {"mp3", "m4a", "aac", "wav", "flac", "ogg", "oga", "opus"}
for _ext, _mt in ((".webp", "image/webp"), (".heic", "image/heic"), (".heif", "image/heif"), (".avif", "image/avif"),
                  (".m4a", "audio/mp4"), (".opus", "audio/ogg"), (".oga", "audio/ogg"), (".flac", "audio/flac"),
                  (".mkv", "video/x-matroska"), (".m4v", "video/mp4"), (".mov", "video/quicktime"), (".3gp", "video/3gpp")):
    mimetypes.add_type(_mt, _ext)

LOCK = threading.RLock()
INDEX = {}            # relatief pad -> {"m": mtime_ns, "s": grootte, "t": opnamedatum, "w", "h", "e": fout}
TRASHED = []          # [{"id","name","orig","t","size","dir"}]
STATE = {"gen": 1, "pending": 0, "dirty": False, "totals": None, "totals_t": 0}
WAKE = threading.Event()
GEN_SEM = threading.Semaphore(2)   # niet meer dan twee miniaturen tegelijk, de Green heeft vier kernen
_KEYLOCKS = {}


class Fail(Exception):
    def __init__(self, code, nl, en):
        super().__init__(en)
        self.code, self.msg = code, hb.L(nl, en)


def changed():
    with LOCK:
        STATE["gen"] += 1
        STATE["totals"] = None
    WAKE.set()


# ---------------------------------------------------------------- paden
def kind_of(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return "image" if ext in IMG else "video" if ext in VID else "audio" if ext in AUD else "other"


def thumbable(name):
    return PIL_OK and "." in name and name.rsplit(".", 1)[-1].lower() in THUMBABLE


def clean_rel(rel):
    """Maakt van wat de pagina stuurt een veilig pad binnen de mediamap, of weigert het."""
    rel = str(rel or "").replace("\\", "/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    for p in parts:
        if p == ".." or p.startswith(".") or any(ord(c) < 32 for c in p):
            raise Fail(400, "Ongeldig pad.", "Invalid path.")
    return "/".join(parts)


def full_path(rel, must_exist=True):
    rel = clean_rel(rel)
    full = os.path.realpath(os.path.join(ROOT, *rel.split("/"))) if rel else ROOT
    if full != ROOT and not full.startswith(ROOT + os.sep):
        raise Fail(400, "Dat pad valt buiten de mediamap.", "That path is outside the media folder.")
    if must_exist and not os.path.lexists(full):
        raise Fail(404, "Dat bestand of die map bestaat niet (meer).", "That file or folder does not exist (any more).")
    return full, rel


def check_name(name):
    name = str(name or "").strip()
    if not name or name in (".", "..") or name.startswith(".") or "/" in name or "\\" in name \
            or len(name.encode("utf-8")) > 240 or any(ord(c) < 32 for c in name):
        raise Fail(400, "Die naam kan niet. Gebruik geen / of \\ en begin niet met een punt.",
                   "That name is not allowed. Do not use / or \\ and do not start with a dot.")
    return name


def unique_target(folder, name):
    base, ext = os.path.splitext(name)
    cand, n = name, 2
    while os.path.lexists(os.path.join(folder, cand)):
        cand, n = f"{base} ({n}){ext}", n + 1
    return cand


def rel_of(full):
    return "" if full == ROOT else os.path.relpath(full, ROOT).replace(os.sep, "/")


# ---------------------------------------------------------------- datums
FN_DATE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
                     r"(?:[ _T.-]?([01]\d|2[0-3])[-_.:h]?([0-5]\d)(?:[-_.:m]?([0-5]\d))?)?")


def date_from_name(name):
    """20250815_145843.jpg, IMG_20250815_145843.jpg, PXL_20250815_145843123.jpg, 2025-08-15 14.58.43.png ..."""
    m = FN_DATE.search(name)
    if not m:
        return None
    y, mo, d, hh, mi, ss = (int(x) if x else 0 for x in m.groups())
    try:
        return datetime(y, mo, d, hh, mi, ss).strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def date_from_mtime(mtime):
    return datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- miniaturen
def thumb_key(rel, st):
    return hashlib.sha1(f"{ROOT}|{rel}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")).hexdigest()[:24]


def thumb_file(key):
    return os.path.join(THUMBS, key[:2], key + (".webp" if THUMB_FMT == "WEBP" else ".jpg"))


def make_thumb(full, rel, st):
    """Maakt de miniatuur (als die er nog niet is) en leest meteen afmetingen en EXIF-datum. Geeft het pad of None."""
    key = thumb_key(rel, st)
    out = thumb_file(key)
    with LOCK:
        info = INDEX.get(rel)
        fresh = bool(info) and info.get("m") == st.st_mtime_ns and info.get("s") == st.st_size
        if fresh and info.get("e"):
            return None
        if fresh and os.path.exists(out):
            return out
        klock = _KEYLOCKS.setdefault(key, threading.Lock())
    with klock, GEN_SEM:
        with LOCK:
            info = INDEX.get(rel)
        if info and info.get("m") == st.st_mtime_ns and info.get("s") == st.st_size and os.path.exists(out):
            return out
        meta = {"m": st.st_mtime_ns, "s": st.st_size}
        try:
            with Image.open(full) as im:
                w, h = im.size
                exif = im.getexif()
                orient = exif.get(274) or 1
                if orient in (5, 6, 7, 8):
                    w, h = h, w
                dt = None
                try:
                    dt = exif.get_ifd(0x8769).get(36867) or exif.get(306)
                except Exception:
                    dt = exif.get(306)
                if dt:
                    try:
                        meta["t"] = datetime.strptime(str(dt).strip()[:19], "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%dT%H:%M:%S")
                    except ValueError:
                        pass
                meta["w"], meta["h"] = w, h
                if im.format == "JPEG":
                    im.draft("RGB", (THUMB_SIDE * 2, THUMB_SIDE * 2))   # veel sneller bij grote foto's
                im.seek(0)
                im = ImageOps.exif_transpose(im)
                scale = min(1.0, THUMB_SIDE / max(1, min(im.size)))
                if scale < 1:
                    im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
                if THUMB_FMT == "JPEG" or im.mode not in ("RGB", "RGBA"):
                    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                        im = im.convert("RGBA")
                        if THUMB_FMT == "JPEG":
                            bg = Image.new("RGB", im.size, (255, 255, 255))
                            bg.paste(im, mask=im.split()[-1])
                            im = bg
                    else:
                        im = im.convert("RGB")
                os.makedirs(os.path.dirname(out), exist_ok=True)
                tmp = out + ".tmp"
                im.save(tmp, THUMB_FMT, quality=78, method=4) if THUMB_FMT == "WEBP" else im.save(tmp, "JPEG", quality=80, optimize=True)
                os.replace(tmp, out)
        except Exception as err:  # beschadigd of onbekend bestand: niet steeds opnieuw proberen
            meta["e"] = 1
            print(f"Miniatuur lukte niet voor {rel}: {type(err).__name__}: {err}", flush=True)
            out = None
        finally:
            with LOCK:
                _KEYLOCKS.pop(key, None)
        if "t" not in meta and info and info.get("t") and info.get("m") == st.st_mtime_ns and info.get("s") == st.st_size:
            meta["t"] = info["t"]       # datum uit de oude bestandsnaam, bewaard bij hernoemen
        with LOCK:
            INDEX[rel] = meta
            STATE["dirty"] = True
        return out


def walk_files():
    """Alle zichtbare bestanden in de mediamap: (volledig pad, relatief pad, stat)."""
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if fn.startswith("."):
                if fn.startswith(PART_PREFIX):
                    try:  # achtergebleven halve upload van meer dan een dag oud
                        if time.time() - os.stat(full).st_mtime > 86400:
                            os.remove(full)
                    except OSError:
                        pass
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            yield full, rel_of(full), st


def worker():
    """Maakt op de achtergrond alle ontbrekende miniaturen en ruimt oude op."""
    try:
        os.nice(5)
    except Exception:
        pass
    while True:
        WAKE.clear()
        try:
            todo, keep_keys, keep_rels = [], set(), set()
            for full, rel, st in walk_files():
                keep_rels.add(rel)
                if not thumbable(rel):
                    continue
                key = thumb_key(rel, st)
                keep_keys.add(key)
                with LOCK:
                    info = INDEX.get(rel)
                fresh = bool(info) and info.get("m") == st.st_mtime_ns and info.get("s") == st.st_size
                if not (fresh and (info.get("e") or os.path.exists(thumb_file(key)))):
                    todo.append((full, rel, st))
            with LOCK:
                STATE["pending"] = len(todo)
            if todo:
                print(f"Miniaturen maken: {len(todo)}", flush=True)
            for i, (full, rel, st) in enumerate(todo):
                make_thumb(full, rel, st)
                with LOCK:
                    STATE["pending"] = len(todo) - i - 1
                if i % 10 == 9:
                    save_index()
            # opruimen: miniaturen en indexregels van bestanden die er niet meer zijn
            for sub in os.listdir(THUMBS):
                d = os.path.join(THUMBS, sub)
                if not os.path.isdir(d):
                    continue
                for fn in os.listdir(d):
                    if fn.split(".")[0] not in keep_keys:
                        try:
                            os.remove(os.path.join(d, fn))
                        except OSError:
                            pass
            with LOCK:
                for rel in [r for r in INDEX if r not in keep_rels]:
                    del INDEX[rel]
                    STATE["dirty"] = True
            save_index()
            purge_trash()
        except Exception as err:
            print(f"Achtergrondtaak: {type(err).__name__}: {err}", flush=True)
        WAKE.wait(3600)


def save_index(force=False):
    with LOCK:
        if not (STATE["dirty"] or force):
            return
        data = dict(INDEX)
        STATE["dirty"] = False
    hb.save_json(INDEX_FILE, data)


# ---------------------------------------------------------------- overzicht
def file_entry(full, rel, st):
    name = os.path.basename(full)
    kind = kind_of(name)
    with LOCK:
        info = INDEX.get(rel)
    fresh = info if info and info.get("m") == st.st_mtime_ns and info.get("s") == st.st_size else {}
    taken = fresh.get("t") or date_from_name(name)
    e = {"name": name, "path": rel, "kind": kind, "size": st.st_size, "taken": taken or date_from_mtime(st.st_mtime),
         "dated": bool(taken), "mtime": int(st.st_mtime)}
    if fresh.get("w"):
        e["w"], e["h"] = fresh["w"], fresh["h"]
    if thumbable(name) and not fresh.get("e"):
        e["thumb"] = "api/thumb?" + urllib.parse.urlencode({"p": rel, "v": thumb_key(rel, st)})
    return e


def totals():
    with LOCK:
        if STATE["totals"] and time.time() - STATE["totals_t"] < 60:
            return STATE["totals"]
    t = {"files": 0, "bytes": 0, "image": 0, "video": 0, "audio": 0, "other": 0}
    for full, rel, st in walk_files():
        t["files"] += 1
        t["bytes"] += st.st_size
        t[kind_of(rel)] += 1
    with LOCK:
        STATE["totals"], STATE["totals_t"] = t, time.time()
    return t


def folder_entry(full):
    items, size, cover = 0, 0, None
    for dirpath, dirnames, filenames in os.walk(full):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            items += 1
            try:
                st = os.stat(os.path.join(dirpath, fn))
            except OSError:
                continue
            size += st.st_size
            if cover is None and thumbable(fn):
                rel = rel_of(os.path.join(dirpath, fn))
                cover = "api/thumb?" + urllib.parse.urlencode({"p": rel, "v": thumb_key(rel, st)})
    return {"name": os.path.basename(full), "path": rel_of(full), "items": items, "size": size, "cover": cover}


def listing(rel):
    full, rel = full_path(rel)
    if not os.path.isdir(full):
        raise Fail(400, "Dat is geen map.", "That is not a folder.")
    folders, files = [], []
    with os.scandir(full) as it:
        for de in it:
            if de.name.startswith("."):
                continue
            try:
                if de.is_dir(follow_symlinks=False):
                    folders.append(folder_entry(de.path))
                elif de.is_file():
                    files.append(file_entry(de.path, rel_of(de.path), de.stat()))
            except OSError:
                continue
    folders.sort(key=lambda f: f["name"].lower())
    with LOCK:
        pending, gen = STATE["pending"], STATE["gen"]
        trash = {"count": len(TRASHED), "bytes": sum(x.get("size", 0) for x in TRASHED)}
    return {"dir": rel, "folders": folders, "files": files, "totals": totals(), "pending": pending, "gen": gen,
            "trash": trash, "trash_days": TRASH_DAYS, "thumbs": PIL_OK, "demo": hb.DEMO,
            "root": ROOT_LABEL, "fs": ROOT_FS, "ms": MS_BASE, "error": ROOT_ERROR}


def all_folders():
    out = [""]
    for dirpath, dirnames, _ in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        out.extend(rel_of(os.path.join(dirpath, d)) for d in dirnames)
    return sorted(out, key=str.lower)


# ---------------------------------------------------------------- bewerken
def tree_size(full):
    if os.path.isfile(full):
        return os.path.getsize(full)
    return sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(full) for f in fs
               if os.path.exists(os.path.join(dp, f)))


def carry(old_rel, new_full):
    """Neemt de miniaturen en gegevens mee naar de nieuwe naam, zodat ze niet opnieuw gemaakt hoeven te worden."""
    moves = []
    if os.path.isdir(new_full):
        new_rel = rel_of(new_full)
        for dp, _, fs in os.walk(new_full):
            for f in fs:
                full = os.path.join(dp, f)
                nrel = rel_of(full)
                moves.append((old_rel + nrel[len(new_rel):], nrel, full))
    else:
        moves.append((old_rel, rel_of(new_full), new_full))
    for orel, nrel, full in moves:
        try:
            st = os.stat(full)
        except OSError:
            continue
        with LOCK:
            info = INDEX.pop(orel, None)
            if not (info and info.get("m") == st.st_mtime_ns and info.get("s") == st.st_size):
                info = {"m": st.st_mtime_ns, "s": st.st_size}
            if not info.get("t"):  # een nieuwe naam zonder datum mag de opnamedatum niet kwijtraken
                d = date_from_name(os.path.basename(orel))
                if d and not date_from_name(os.path.basename(nrel)):
                    info["t"] = d
            INDEX[nrel] = info
            STATE["dirty"] = True
        src, dst = thumb_file(thumb_key(orel, st)), thumb_file(thumb_key(nrel, st))
        if os.path.exists(src):
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(src, dst)
            except OSError:
                pass


def do_mkdir(parent, name):
    pfull, _ = full_path(parent)
    name = check_name(name)
    target = os.path.join(pfull, name)
    if os.path.lexists(target):
        raise Fail(409, "Er is al iets met die naam.", "Something with that name already exists.")
    os.mkdir(target)
    changed()
    return rel_of(target)


def do_rename(rel, name):
    full, rel = full_path(rel)
    if not rel:
        raise Fail(400, "De hoofdmap kan niet worden hernoemd.", "The main folder cannot be renamed.")
    name = check_name(name)
    target = os.path.join(os.path.dirname(full), name)
    if os.path.basename(full) == name:
        return rel
    if os.path.lexists(target) and os.path.basename(full).lower() != name.lower():
        raise Fail(409, "Er is al iets met die naam in deze map.", "Something with that name already exists in this folder.")
    os.rename(full, target)
    carry(rel, target)
    changed()
    return rel_of(target)


def do_move(rels, to):
    tfull, _ = full_path(to)
    if not os.path.isdir(tfull):
        raise Fail(400, "Kies een map om naartoe te verplaatsen.", "Choose a folder to move to.")
    moved = []
    for r in rels:
        full, rel = full_path(r)
        if not rel:
            continue
        if os.path.isdir(full) and (tfull == full or tfull.startswith(full + os.sep)):
            raise Fail(400, "Een map kan niet in zichzelf worden gezet.", "A folder cannot be moved into itself.")
        if os.path.dirname(full) == tfull:
            continue
        name = unique_target(tfull, os.path.basename(full))
        shutil.move(full, os.path.join(tfull, name))
        carry(rel, os.path.join(tfull, name))
        moved.append(name)
    changed()
    return moved


def do_delete(rels, who):
    gone = []
    for r in rels:
        full, rel = full_path(r)
        if not rel:
            continue
        size = tree_size(full)
        is_dir = os.path.isdir(full)
        if TRASH_DAYS == 0:
            shutil.rmtree(full) if is_dir else os.remove(full)
        else:
            tid = uuid.uuid4().hex[:16]
            dest = os.path.join(TRASH, tid)
            os.mkdir(dest)
            shutil.move(full, os.path.join(dest, os.path.basename(full)))
            with LOCK:
                TRASHED.append({"id": tid, "name": os.path.basename(full), "orig": rel, "root": ROOT, "label": ROOT_LABEL,
                                "t": time.time(), "size": size, "dir": is_dir})
        gone.append(rel)
    if gone:
        print(f"Verwijderd{' (prullenbak)' if TRASH_DAYS else ''}: {', '.join(gone)}" + (f" door {who}" if who else ""), flush=True)
    save_trash()
    changed()
    return gone


def trash_item(tid):
    with LOCK:
        it = next((x for x in TRASHED if x["id"] == str(tid)), None)
    if not it:
        raise Fail(404, "Niet (meer) in de prullenbak.", "Not in the trash (any more).")
    return it


def trash_path(it):
    # de map is een id dat de app zelf maakte, de naam komt uit de eigen gegevens
    return os.path.join(TRASH, re.sub(r"[^0-9a-f]", "", it["id"]), os.path.basename(it["name"]))


def do_restore(ids):
    back = []
    for tid in ids:
        it = trash_item(tid)
        src = trash_path(it)
        orig = clean_rel(it["orig"])
        parent_rel = orig.rsplit("/", 1)[0] if "/" in orig else ""
        root = it.get("root") or ROOT
        if not hb.HA_APP or not any(root == b or root.startswith(b + "/") for b in ALLOWED_BASES) or not os.path.isdir(root):
            root = ROOT   # de map van toen bestaat niet meer of mag niet: terug naar de huidige map
        pfull = os.path.join(root, *parent_rel.split("/")) if parent_rel else root
        os.makedirs(pfull, exist_ok=True)
        name = unique_target(pfull, os.path.basename(orig))
        if os.path.lexists(src):
            shutil.move(src, os.path.join(pfull, name))
        shutil.rmtree(os.path.dirname(src), ignore_errors=True)
        with LOCK:
            TRASHED[:] = [x for x in TRASHED if x["id"] != it["id"]]
        back.append(os.path.join(pfull, name)[len(root):].strip("/") if root != ROOT else rel_of(os.path.join(pfull, name)))
    save_trash()
    changed()
    return back


def do_purge(ids=None):
    with LOCK:
        items = list(TRASHED) if ids is None else [trash_item(t) for t in ids]
    for it in items:
        shutil.rmtree(os.path.dirname(trash_path(it)), ignore_errors=True)
        with LOCK:
            TRASHED[:] = [x for x in TRASHED if x["id"] != it["id"]]
    save_trash()
    changed()
    return len(items)


def purge_trash():
    if not TRASHED:
        return
    limit = time.time() - max(TRASH_DAYS, 0) * 86400
    with LOCK:
        old = [x["id"] for x in TRASHED if x["t"] < limit]
    if old:
        do_purge(old)
        print(f"Prullenbak: {len(old)} oude bestanden definitief verwijderd.", flush=True)


def save_trash():
    with LOCK:
        data = list(TRASHED)
    hb.save_json(TRASH_FILE, data)


# ---------------------------------------------------------------- webserver
class Handler(hb.BaseHandler):
    APP = "mediabeheer"

    # -- lezen
    def get_api(self, path, q):
        one = lambda k: (q.get(k) or [""])[0]
        try:
            if path == "/api/list":
                self.json(listing(one("dir")))
            elif path == "/api/thumb":
                self.thumb(one("p"))
            elif path == "/api/file":
                self.file(one("p"), one("dl") == "1")
            elif path == "/api/zip":
                self.zip(q.get("p") or [])
            elif path == "/api/folders":
                self.json({"folders": all_folders()})
            elif path == "/api/trash":
                with LOCK:
                    items = sorted(TRASHED, key=lambda x: -x["t"])
                self.json({"items": items, "days": TRASH_DAYS})
            else:
                self.json({"error": hb.tx("Niet gevonden.", "Not found.")}, 404)
        except Fail as e:
            self.fail(e)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def thumb(self, rel):
        full, rel = full_path(rel)
        if not os.path.isfile(full) or not thumbable(full):
            raise Fail(404, "Geen miniatuur.", "No thumbnail.")
        out = make_thumb(full, rel, os.stat(full))
        if not out:
            raise Fail(404, "Geen miniatuur.", "No thumbnail.")
        with open(out, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", THUMB_TYPE)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    def file(self, rel, download):
        full, rel = full_path(rel)
        if not os.path.isfile(full):
            raise Fail(404, "Dat bestand bestaat niet.", "That file does not exist.")
        size = os.path.getsize(full)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype == "image/svg+xml" and not download:
            ctype = "text/plain; charset=utf-8"   # nooit scripts uit een geüpload bestand laten draaien
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range") or ""
        m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
        if m and size and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1 if size else 0))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        name = os.path.basename(full)
        disp = "attachment" if download else "inline"
        self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{urllib.parse.quote(name)}")
        self.end_headers()
        if not size:
            return
        with open(full, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def zip(self, rels):
        entries = []
        for r in rels[:2000]:
            full, rel = full_path(r)
            if os.path.isdir(full):
                for dp, dns, fns in os.walk(full):
                    dns[:] = [d for d in dns if not d.startswith(".")]
                    entries += [(os.path.join(dp, f), os.path.relpath(os.path.join(dp, f), os.path.dirname(full)))
                                for f in fns if not f.startswith(".")]
            elif os.path.isfile(full):
                entries.append((full, os.path.basename(full)))
        if not entries:
            raise Fail(400, "Niets om te downloaden.", "Nothing to download.")
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", "attachment; filename=\"media.zip\"")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.close_connection = True
        seen = set()
        with zipfile.ZipFile(self.wfile, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            for full, arc in entries:
                arc = arc.replace(os.sep, "/")
                base, ext, n = arc, "", 2
                if arc in seen:
                    base, ext = os.path.splitext(arc)
                    while f"{base} ({n}){ext}" in seen:
                        n += 1
                    arc = f"{base} ({n}){ext}"
                seen.add(arc)
                z.write(full, arc)

    # -- schrijven
    def body_chunks(self, limit):
        """Leest de inhoud van een verzoek in stukken. Home Assistant stuurt die soms zonder lengte
        (Transfer-Encoding: chunked), dus beide manieren worden ondersteund."""
        got = 0
        self._body_open = True
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            while True:
                line = self.rfile.readline(1024).strip()
                size = int(line.split(b";")[0] or b"0", 16)
                if size == 0:
                    while self.rfile.readline(1024).strip():   # eventuele trailers
                        pass
                    self._body_open = False
                    return
                got += size
                if got > limit:
                    raise Fail(413, "Te veel gegevens in één keer.", "Too much data at once.")
                left = size
                while left > 0:
                    chunk = self.rfile.read(min(262144, left))
                    if not chunk:
                        raise Fail(400, "De upload werd onderbroken.", "The upload was interrupted.")
                    left -= len(chunk)
                    yield chunk
                self.rfile.readline(16)
        else:
            left = int(self.headers.get("Content-Length") or 0)
            if left > limit:
                raise Fail(413, "Te veel gegevens in één keer.", "Too much data at once.")
            while left > 0:
                chunk = self.rfile.read(min(262144, left))
                if not chunk:
                    raise Fail(400, "De upload werd onderbroken.", "The upload was interrupted.")
                left -= len(chunk)
                yield chunk
        self._body_open = False

    def drain(self):
        """Leest de rest van het verzoek weg voor een foutmelding; anders meldt de proxy 'Bad Gateway'."""
        if getattr(self, "_body_open", None) is False:
            return
        try:
            for _ in self.body_chunks(MAX_UPLOAD):
                pass
        except Exception:
            pass

    def fail(self, err):
        self.drain()
        self.json({"error": err.msg}, err.code)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        hb._set_lang(self.headers)
        self.close_connection = True
        if not hb.user_allowed(self.headers):
            return self.json({"error": hb.tx("Geen toegang.", "No access.")}, 403)
        if self.headers.get("X-Requested-With") != self.APP:
            return self.json({"error": hb.tx("Ongeldig verzoek.", "Invalid request.")}, 400)
        try:
            if url.path == "/api/upload":
                return self.upload(urllib.parse.parse_qs(url.query))
            raw = b"".join(self.body_chunks(2_000_000))
            try:
                body = __import__("json").loads(raw.decode("utf-8") or "{}")
            except ValueError:
                raise Fail(400, "Ongeldig verzoek.", "Invalid request.")
            if not isinstance(body, dict):
                raise Fail(400, "Ongeldig verzoek.", "Invalid request.")
            self.post_api(url.path, body)
        except Fail as e:
            self.fail(e)
        except Exception as e:  # noqa: BLE001
            self.drain()
            self.json({"error": str(e)}, 500)

    def upload(self, q):
        """Bestanden komen in stukken van 4 MB, zodat ook grote video's via Nabu Casa werken."""
        one = lambda k: (q.get(k) or [""])[0]
        uid = one("id")
        if not re.fullmatch(r"[0-9a-f]{12,40}", uid):
            raise Fail(400, "Ongeldige upload.", "Invalid upload.")
        dfull, _ = full_path(one("dir"))
        if not os.path.isdir(dfull):
            raise Fail(400, "Die map bestaat niet.", "That folder does not exist.")
        name = check_name(one("name"))
        try:
            off, total = int(one("off") or 0), int(one("total") or 0)
        except ValueError:
            raise Fail(400, "Ongeldige upload.", "Invalid upload.")
        if total > MAX_UPLOAD or off < 0 or off > total:
            raise Fail(413, "Dit bestand is te groot (maximaal 8 GB).", "This file is too large (8 GB at most).")
        part = os.path.join(dfull, PART_PREFIX + uid)
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if off != have:
            if off == 0:
                have = 0
            else:
                self.drain()
                return self.json({"error": "offset", "have": have}, 409)
        free = shutil.disk_usage(dfull).free
        if off == 0 and total > free - 512 * 1024 ** 2:
            raise Fail(507, "Er is niet genoeg ruimte op de schijf voor dit bestand.", "There is not enough disk space for this file.")
        with open(part, "r+b" if off else "wb") as f:
            f.seek(off)
            f.truncate()
            for chunk in self.body_chunks(MAX_CHUNK):
                if f.tell() + len(chunk) > total:
                    raise Fail(400, "De upload is groter dan aangekondigd.", "The upload is larger than announced.")
                f.write(chunk)
            have = f.tell()
        if have < total:
            return self.json({"ok": True, "have": have})
        final = unique_target(dfull, name)
        os.replace(part, os.path.join(dfull, final))
        changed()
        print(f"Geüpload: {rel_of(os.path.join(dfull, final))}" + (f" door {hb.user_label(self.headers)}" if hb.user_label(self.headers) else ""), flush=True)
        self.json({"ok": True, "done": True, "name": final, "path": rel_of(os.path.join(dfull, final))})

    def post_api(self, path, b):
        who = hb.user_label(self.headers)
        paths = [str(p) for p in (b.get("paths") or [])][:5000]
        try:
            if path == "/api/mkdir":
                self.json({"path": do_mkdir(b.get("dir"), b.get("name"))})
            elif path == "/api/rename":
                self.json({"path": do_rename(b.get("path"), b.get("name"))})
            elif path == "/api/move":
                self.json({"moved": do_move(paths, b.get("to"))})
            elif path == "/api/delete":
                self.json({"deleted": do_delete(paths, who), "trash": TRASH_DAYS > 0})
            elif path == "/api/restore":
                self.json({"restored": do_restore([str(i) for i in (b.get("ids") or [])])})
            elif path == "/api/purge":
                self.json({"purged": do_purge(None if b.get("all") else [str(i) for i in (b.get("ids") or [])])})
            else:
                self.json({"error": hb.tx("Niet gevonden.", "Not found.")}, 404)
        except Fail as e:
            self.fail(e)


# ---------------------------------------------------------------- pagina
ICONS = {
    "folder": "M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z",
    "image": "M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3 3.5-4.5 4.5 6H5l3.5-4.5z",
    "video": "M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z",
    "audio": "M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z",
    "other": "M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm-1 7V3.5L18.5 9H13z",
    "upload": "M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z",
    "newfolder": "M20 6h-8l-2-2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-1 8h-3v3h-2v-3h-3v-2h3V9h2v3h3v2z",
    "trash": "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
    "download": "M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z",
    "rename": "M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z",
    "move": "M20 6h-8l-2-2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-6 12v-3h-4v-4h4V8l5 5-5 5z",
    "close": "M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
    "prev": "M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z",
    "next": "M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z",
    "copy": "M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z",
    "restore": "M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18z",
    "check": "M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z",
}


def svg(name, cls="ic"):
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="{ICONS[name]}"/></svg>'


BODY = r"""<div class="wrap">
<div class="top">
  <div><h1 data-en="Media manager">Media manager</h1><p class="sum" id="sum">&nbsp;</p></div>
  <div class="acts">
    <button class="btn" id="bTrash" type="button" title="Prullenbak" data-en-title="Trash">__trash__<span class="lbl" data-en="Trash">Prullenbak</span> <span class="cnt" id="trashN"></span></button>
    <button class="btn" id="bMkdir" type="button" title="Nieuwe map" data-en-title="New folder">__newfolder__<span class="lbl" data-en="New folder">Nieuwe map</span></button>
    <label class="btn primary" for="fileIn">__upload__<span data-en="Upload">Uploaden</span></label>
    <input type="file" id="fileIn" multiple hidden>
  </div>
</div>
<p class="note" id="note" hidden></p>
<div class="card pad bar1" id="tools">
  <nav class="crumbs" id="crumbs" aria-label="Map" data-en-aria-label="Folder"></nav>
  <div class="filters">
    <label class="field grow"><span data-en="Search in this folder">Zoeken in deze map</span><input type="search" id="q" placeholder="Naam of datum, bijv. 2025-08" data-en-placeholder="Name or date, e.g. 2025-08"></label>
    <label class="field"><span data-en="Sort">Sorteren</span><select id="sort">
      <option value="new" data-en="Newest first">Nieuwste eerst</option><option value="old" data-en="Oldest first">Oudste eerst</option>
      <option value="name" data-en="Name A–Z">Naam A–Z</option><option value="big" data-en="Largest first">Grootste eerst</option></select></label>
    <div class="field zoomf"><span data-en="Size">Grootte</span><div class="chips" id="zoom">
      <button class="chip" type="button" data-z="s" data-en="Small">Klein</button><button class="chip" type="button" data-z="m" data-en="Medium">Middel</button><button class="chip" type="button" data-z="l" data-en="Large">Groot</button></div></div>
  </div>
  <div class="chips" id="kinds"></div>
  <div class="chips" id="years"></div>
</div>
<p class="small faint" id="pend" hidden></p>
<main id="grid" aria-live="polite"></main>
<p class="faint small" id="foot"></p>
<!--FOOT-->
</div>

<div class="selbar" id="selbar" hidden>
  <span id="selTxt"></span>
  <button class="btn ghost" type="button" id="sAll" data-en="Select all">Alles selecteren</button>
  <span class="sp"></span>
  <button class="btn" type="button" id="sDl">__download__<span data-en="Download">Downloaden</span></button>
  <button class="btn" type="button" id="sMv">__move__<span data-en="Move">Verplaatsen</span></button>
  <button class="btn danger" type="button" id="sDel">__trash__<span data-en="Delete">Verwijderen</span></button>
  <button class="ibtn" type="button" id="sX" aria-label="Selectie wissen" data-en-aria-label="Clear selection">__close__</button>
</div>

<div class="lb" id="lb" hidden role="dialog" aria-modal="true" aria-labelledby="lbName">
  <div class="lbStage" id="lbStage">
    <button class="ibtn nav prev" type="button" id="lbPrev" aria-label="Vorige" data-en-aria-label="Previous">__prev__</button>
    <div class="lbMedia" id="lbMedia"></div>
    <button class="ibtn nav next" type="button" id="lbNext" aria-label="Volgende" data-en-aria-label="Next">__next__</button>
  </div>
  <aside class="lbInfo">
    <div class="lbHead"><h2 id="lbName"></h2><button class="ibtn" type="button" id="lbX" aria-label="Sluiten" data-en-aria-label="Close">__close__</button></div>
    <p class="small muted" id="lbPos"></p>
    <dl class="kv" id="lbKv"></dl>
    <div class="lbActs">
      <a class="btn" id="lbDl" download>__download__<span data-en="Download">Downloaden</span></a>
      <button class="btn" type="button" id="lbRn">__rename__<span data-en="Rename">Hernoemen</span></button>
      <button class="btn" type="button" id="lbMv">__move__<span data-en="Move">Verplaatsen</span></button>
      <button class="btn danger" type="button" id="lbDel">__trash__<span data-en="Delete">Verwijderen</span></button>
    </div>
    <div class="paths">
      <p class="small muted" data-en="Use in Home Assistant">Gebruiken in Home Assistant</p>
      <div class="pth"><code id="pMs"></code><button class="ibtn sm" type="button" data-copy="pMs" aria-label="Kopiëren" data-en-aria-label="Copy">__copy__</button></div>
      <div class="pth"><code id="pFs"></code><button class="ibtn sm" type="button" data-copy="pFs" aria-label="Kopiëren" data-en-aria-label="Copy">__copy__</button></div>
      <p class="small faint" id="pHint" data-en="The first is for media players and picture cards, the second for automations that need a file path.">De eerste is voor mediaspelers en afbeeldingskaarten, de tweede voor automations die een bestandspad nodig hebben.</p>
    </div>
  </aside>
</div>

<div class="modal" id="pr" hidden><form class="box" id="prF" role="dialog" aria-modal="true" aria-labelledby="prT">
  <h3 id="prT"></h3><p id="prX"></p>
  <input class="inp" id="prI" autocomplete="off" spellcheck="false">
  <div class="acts"><button class="btn" type="button" id="prNo" data-en="Cancel">Annuleren</button><button class="btn primary" id="prOk" type="submit"></button></div>
</form></div>

<div class="modal" id="mv" hidden><div class="box" role="dialog" aria-modal="true" aria-labelledby="mvT">
  <h3 id="mvT"></h3><p id="mvX" data-en="Choose the folder.">Kies de map.</p>
  <ul class="flist" id="mvL"></ul>
  <div class="acts"><button class="btn" type="button" id="mvNo" data-en="Cancel">Annuleren</button><button class="btn primary" type="button" id="mvOk" data-en="Move here">Hierheen verplaatsen</button></div>
</div></div>

<div class="drop" id="drop" hidden><div>__upload__<p id="dropT"></p></div></div>
<div class="upl card" id="upl" hidden><div class="uplHead"><b id="uplT"></b><button class="ibtn sm" type="button" id="uplX" aria-label="Sluiten" data-en-aria-label="Close">__close__</button></div><ul id="uplL"></ul></div>
"""
for _k in ICONS:
    BODY = BODY.replace(f"__{_k}__", svg(_k))

CSSX = r"""
.modal{z-index:9}.toast{z-index:10}
.top .acts{display:flex;gap:8px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:7px}
.btn .ic{width:18px;height:18px;fill:currentColor;flex:none}
.btn.danger{color:var(--bad)} .btn.danger:hover{border-color:var(--bad)}
.btn.ghost{border-color:transparent;color:var(--link)}
.cnt{font-size:12px;color:var(--muted)}
.btn[aria-pressed="true"]{background:var(--sel);border-color:var(--selLine)}
.ibtn{border:0;background:transparent;border-radius:999px;width:38px;height:38px;display:inline-grid;place-items:center;cursor:pointer;color:var(--muted);flex:none}
.ibtn:hover{background:var(--line2);color:var(--text)} .ibtn .ic{width:22px;height:22px;fill:currentColor}
.ibtn.sm{width:30px;height:30px} .ibtn.sm .ic{width:17px;height:17px}
.bar1{display:flex;flex-direction:column;gap:12px}
.filters{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}
.grow{flex:1 1 240px}
.crumbs{display:flex;flex-wrap:wrap;align-items:center;gap:2px;font-size:15px}
.crumbs button{border:0;background:transparent;cursor:pointer;padding:4px 8px;border-radius:8px;color:var(--link)}
.crumbs button:hover{background:var(--sel)} .crumbs b{padding:4px 8px;font-weight:600}
.crumbs .sep{color:var(--faint)}
.chips:empty{display:none}
.chip .n{color:var(--faint);margin-left:4px}
#grid{--tile:168px;display:flex;flex-direction:column;gap:22px;padding-bottom:84px}
#grid.zs{--tile:124px} #grid.zl{--tile:236px}
.grp h2{font-size:15px;color:var(--muted);font-weight:600;margin:0 0 10px;display:flex;gap:8px;align-items:baseline}
.grp h2 span{font-weight:400;color:var(--faint);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(var(--tile),1fr));gap:12px}
.tile{position:relative;background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column;min-width:0}
.tile:hover{border-color:var(--muted)}
.tile.on{border-color:var(--selLine);box-shadow:0 0 0 2px var(--selLine)}
.tile .th{all:unset;cursor:pointer;display:block;aspect-ratio:1/1;background:var(--sunk);position:relative;overflow:hidden}
.tile .th:focus-visible{outline:2px solid var(--link);outline-offset:-2px}
.tile .th img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;opacity:0;transition:opacity .2s}
.tile .th img.ok{opacity:1}
.tile .th .big{position:absolute;inset:0;display:grid;place-items:center}
.tile .th .big .ic{width:42%;height:42%;fill:var(--faint);opacity:.8}
.tile.k-video .th .big .ic{fill:var(--purple)} .tile.k-audio .th .big .ic{fill:var(--info)} .tile.k-folder .th .big .ic{fill:var(--accent)}
.tile .badge{position:absolute;right:8px;bottom:8px;background:rgba(0,0,0,.6);color:#fff;font-size:11px;font-weight:600;padding:2px 7px;border-radius:999px;text-transform:uppercase;letter-spacing:.03em}
.tile.k-folder .th img{opacity:.35;filter:saturate(.6)}
.tile .ck{position:absolute;left:8px;top:8px;width:26px;height:26px;border-radius:999px;background:rgba(255,255,255,.92);border:1.5px solid rgba(20,33,58,.35);display:grid;place-items:center;cursor:pointer;opacity:0;transition:opacity .12s;z-index:1}
.tile .ck .ic{width:17px;height:17px;fill:#fff;opacity:0}
.tile:hover .ck,.tile .ck:focus-visible,.sel .tile .ck,.tile.on .ck{opacity:1}
.tile.on .ck{background:var(--accent);border-color:var(--accent)} .tile.on .ck .ic{opacity:1}
@media (hover:none){.tile .ck{opacity:1}}
.tile .tx{padding:8px 10px 10px;display:flex;flex-direction:column;gap:2px;min-width:0}
.tile .nm{font-size:13px;font-weight:500;line-height:1.35;overflow-wrap:anywhere;word-break:break-word}
.tile .mt{font-size:12px;color:var(--faint);line-height:1.4}
#grid.zs .tile .mt{display:none} #grid.zs .tile .nm{font-size:12px}
.selbar{position:fixed;left:50%;transform:translateX(-50%);bottom:16px;z-index:4;background:var(--surface);border:1px solid var(--line);border-radius:14px;
  box-shadow:0 10px 30px rgba(0,0,0,.18);padding:8px 8px 8px 16px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;width:min(860px,calc(100% - 24px))}
.selbar .sp{flex:1}
#selTxt{font-weight:600;font-size:14px}
.lb{position:fixed;inset:0;z-index:7;display:grid;grid-template-columns:minmax(0,1fr) 340px;background:#0B1119}
.lbStage{position:relative;display:grid;place-items:center;min-height:0;min-width:0;padding:16px 64px}
.lbMedia{width:100%;height:100%;display:grid;place-items:center;min-height:0}
.lbMedia img,.lbMedia video{max-width:100%;max-height:calc(100vh - 32px);object-fit:contain;border-radius:6px}
.lbMedia audio{width:min(520px,100%)}
.lbMedia .big .ic{width:120px;height:120px;fill:#5C6C84}
.lbMedia .big{display:flex;flex-direction:column;align-items:center;gap:12px;color:#93A3BA;text-align:center}
.lb .nav{position:absolute;top:50%;transform:translateY(-50%);color:#E4EBF5;background:rgba(255,255,255,.08);width:46px;height:46px}
.lb .nav:hover{background:rgba(255,255,255,.18);color:#fff} .lb .nav:disabled{opacity:.2;cursor:default}
.lb .prev{left:10px} .lb .next{right:10px}
.lbInfo{background:var(--surface);border-left:1px solid var(--line);padding:16px 18px;overflow:auto;display:flex;flex-direction:column;gap:12px}
.lbHead{display:flex;gap:8px;align-items:flex-start;justify-content:space-between}
.lbHead h2{overflow-wrap:anywhere;font-size:16px;padding-top:7px}
.lbActs{display:flex;flex-wrap:wrap;gap:8px}
.paths{border-top:1px solid var(--line2);padding-top:12px;display:flex;flex-direction:column;gap:6px}
.paths p{margin:0}
.pth{display:flex;align-items:center;gap:4px;background:var(--sunk);border:1px solid var(--line);border-radius:8px;padding:2px 2px 2px 10px}
.pth code{flex:1;font:12px/1.4 "IBM Plex Mono",ui-monospace,monospace;overflow-wrap:anywhere;padding:5px 0}
.inp{width:100%;font-size:15px;background:var(--sunk);border:1px solid var(--line);border-radius:9px;padding:10px 12px;color:var(--text);margin:0 0 16px}
.flist{list-style:none;margin:0 0 16px;padding:0;max-height:50vh;overflow:auto;border:1px solid var(--line);border-radius:10px}
.flist button{width:100%;text-align:left;border:0;background:transparent;padding:9px 12px;cursor:pointer;display:flex;gap:8px;align-items:center;font-size:14px}
.flist button .ic{width:18px;height:18px;fill:var(--accent);flex:none}
.flist button:hover{background:var(--sel)} .flist button[aria-pressed="true"]{background:var(--sel);box-shadow:inset 3px 0 0 var(--selLine);font-weight:600}
.flist button:disabled{opacity:.45;cursor:default}
.drop{position:fixed;inset:10px;z-index:8;border:3px dashed var(--accent);border-radius:18px;background:color-mix(in srgb,var(--bg) 88%,transparent);display:grid;place-items:center;pointer-events:none}
.drop div{display:flex;flex-direction:column;align-items:center;gap:6px;font-size:18px;font-weight:600}
.drop .ic{width:56px;height:56px;fill:var(--accent)}
.upl{position:fixed;right:16px;bottom:16px;z-index:6;width:min(380px,calc(100% - 32px));max-height:50vh;overflow:auto;box-shadow:0 10px 30px rgba(0,0,0,.18)}
.uplHead{display:flex;justify-content:space-between;align-items:center;padding:8px 8px 8px 16px;border-bottom:1px solid var(--line2);font-size:14px}
#uplL{list-style:none;margin:0;padding:6px 16px 12px}
#uplL li{font-size:13px;padding:6px 0;display:flex;flex-direction:column;gap:4px}
#uplL .r{display:flex;justify-content:space-between;gap:10px} #uplL .r span:first-child{overflow-wrap:anywhere}
#uplL .pb{height:5px;background:var(--line2);border-radius:3px;overflow:hidden} #uplL .pb i{display:block;height:100%;background:var(--accent);width:0;transition:width .2s}
#uplL li.err .pb i{background:var(--bad)} #uplL li.done .pb i{background:var(--ok)}
.trow{display:grid;grid-template-columns:44px minmax(0,1fr) auto;gap:12px;align-items:center;padding:10px 16px;border-top:1px solid var(--line2)}
.trow:first-child{border-top:0}
.trow .ic{width:28px;height:28px;fill:var(--faint)}
.trow .nm{font-weight:500;overflow-wrap:anywhere} .trow .mt{font-size:13px;color:var(--muted)}
.trow .ac{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.thead{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;padding:14px 16px;border-bottom:1px solid var(--line)}
@media (max-width:860px){.lb{grid-template-columns:1fr;grid-template-rows:minmax(0,1fr) auto}.lbInfo{border-left:0;border-top:1px solid var(--line);max-height:45vh}
  .lbStage{padding:8px 52px}.lbMedia img,.lbMedia video{max-height:calc(55vh - 16px)}}
@media (max-width:640px){.top .acts{width:100%} .top .acts .btn:not(.primary) .lbl{display:none} .top .acts .primary{margin-left:auto}
  .zoomf{display:none} .filters .field:not(.grow){flex:1 1 auto} .filters .field select{width:100%} .bar1{gap:10px} .crumbs{font-size:14px}
  #grid{--tile:132px} #grid.zs{--tile:96px} #grid.zl{--tile:180px} .trow{grid-template-columns:34px minmax(0,1fr)} .trow .ac{grid-column:1 / -1;justify-content:flex-start}}
"""

JS = r"""
const APP = 'mediabeheer';
const ICON = __ICONS__;
const ic = (n, c) => `<svg class="${c || 'ic'}" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICON[n]}"/></svg>`;
const NF = new Intl.NumberFormat(LOCALE, {maximumFractionDigits: 1});
const size = b => b < 1024 ? NF.format(b) + ' B' : b < 1048576 ? NF.format(b / 1024) + ' kB' : b < 1073741824 ? NF.format(b / 1048576) + ' MB' : NF.format(b / 1073741824) + ' GB';
const plural = (n, one, many) => `${n.toLocaleString(LOCALE)} ${n === 1 ? one : many}`;
const KIND = {image: [t('Foto', 'Photo'), t("Foto's", 'Photos')], video: ['Video', t("Video's", 'Videos')], audio: ['Audio', 'Audio'], other: [t('Overig', 'Other'), t('Overig', 'Other')]};
const ls = {get(k, d){ try { return localStorage.getItem('mm_' + k) ?? d; } catch (e) { return d; } }, set(k, v){ try { localStorage.setItem('mm_' + k, v); } catch (e) {} }};

let ROOT = 'media';
const S = {dir: '', data: null, view: 'files', kind: 'all', year: 'all', q: '', sort: ls.get('sort', 'new'), zoom: ls.get('zoom', 'm'),
  sel: new Set(), last: null, shown: [], lb: -1, poll: null, gen: 0};

const dt = s => new Date(s);
const fmtDate = (s, withTime) => { const d = dt(s); return withTime
  ? d.toLocaleDateString(LOCALE, {day: 'numeric', month: 'long', year: 'numeric'}) + ', ' + d.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'})
  : d.toLocaleDateString(LOCALE, {day: 'numeric', month: 'short', year: 'numeric'}); };
const monthKey = s => s.slice(0, 7);
const monthLabel = k => { const [y, m] = k.split('-'); const s = new Date(+y, +m - 1, 1).toLocaleDateString(LOCALE, {month: 'long', year: 'numeric'}); return s.charAt(0).toUpperCase() + s.slice(1); };
const ext = n => n.includes('.') ? n.split('.').pop() : '';
const fileUrl = (p, dl) => 'api/file?' + new URLSearchParams(dl ? {p, dl: 1} : {p});
const sortKey = f => Date.parse(f.taken) || 0;

async function load(keepScroll){
  try {
    const d = await api('api/list?' + new URLSearchParams({dir: S.dir}));
    S.data = d; S.gen = d.gen;
    const valid = new Set([...d.files.map(f => f.path), ...d.folders.map(f => f.path)]);
    for (const p of [...S.sel]) if (!valid.has(p)) S.sel.delete(p);
    if (!keepScroll) window.scrollTo(0, 0);
    render();
    schedulePoll();
  } catch (e) {
    if (S.dir) { S.dir = ''; return load(); }
    note(t('Kan de mediamap niet lezen: ', 'Cannot read the media folder: ') + e.message, true);
  }
}
function schedulePoll(){ clearTimeout(S.poll); if (S.data && S.data.pending > 0 && S.view === 'files') S.poll = setTimeout(() => { if (S.lb < 0) load(true); else schedulePoll(); }, 4000); }
function note(msg, bad){ const n = $('#note'); n.hidden = !msg; n.textContent = msg || ''; n.classList.toggle('err', !!bad); }

function render(){
  const d = S.data; if (!d) return;
  const tt = d.totals;
  const parts = [];
  if (tt.image) parts.push(plural(tt.image, t('foto', 'photo'), t("foto's", 'photos')));
  if (tt.video) parts.push(plural(tt.video, 'video', t("video's", 'videos')));
  if (tt.audio) parts.push(plural(tt.audio, t('audiobestand', 'audio file'), t('audiobestanden', 'audio files')));
  if (tt.other) parts.push(plural(tt.other, t('ander bestand', 'other file'), t('andere bestanden', 'other files')));
  $('#sum').textContent = (parts.join(', ') || t('Nog niets in de mediamap', 'Nothing in the media folder yet')) + (tt.bytes ? ' · ' + size(tt.bytes) + t(' in totaal, gaat mee in elke backup', ' in total, included in every backup') : '');
  $('#trashN').textContent = d.trash.count ? `(${d.trash.count})` : '';
  $('#bTrash').hidden = d.trash_days === 0 && !d.trash.count;
  $('#bTrash').setAttribute('aria-pressed', S.view === 'trash');
  const pend = $('#pend'); pend.hidden = !d.pending || S.view !== 'files';
  pend.textContent = t(`Miniaturen maken… nog ${d.pending}. Dit gebeurt één keer per bestand.`, `Making thumbnails… ${d.pending} to go. This happens once per file.`);
  const warn = d.error || (!d.thumbs ? t('Miniaturen zijn niet beschikbaar: de beeldbibliotheek ontbreekt.', 'Thumbnails are unavailable: the image library is missing.') : '');
  note(warn, !!warn);
  ROOT = d.root || 'media';
  $('#foot').textContent = d.demo ? t('Voorbeeldgegevens: de app draait los van Home Assistant.', 'Sample data: the app runs outside Home Assistant.') : '';
  $('#tools').hidden = S.view !== 'files';
  $('#bMkdir').hidden = S.view !== 'files';
  if (S.view === 'trash') return renderTrash();
  renderCrumbs(); renderFilters(); renderGrid(); renderSel();
}

function renderCrumbs(){
  const segs = S.dir ? S.dir.split('/') : [];
  let h = segs.length ? `<button type="button" data-d="">${esc(ROOT)}</button>` : `<b>${esc(ROOT)}</b>`;
  segs.forEach((s, i) => { const p = segs.slice(0, i + 1).join('/'); h += '<span class="sep">/</span>' + (i === segs.length - 1 ? `<b>${esc(s)}</b>` : `<button type="button" data-d="${esc(p)}">${esc(s)}</button>`); });
  $('#crumbs').innerHTML = h;
}

function filtered(){
  const d = S.data, q = S.q.trim().toLowerCase();
  let files = d.files.filter(f => (S.kind === 'all' || f.kind === S.kind) && (S.year === 'all' || f.taken.slice(0, 4) === S.year)
    && (!q || f.name.toLowerCase().includes(q) || f.taken.slice(0, 10).includes(q)));
  const by = {new: (a, b) => sortKey(b) - sortKey(a) || a.name.localeCompare(b.name), old: (a, b) => sortKey(a) - sortKey(b) || a.name.localeCompare(b.name),
    name: (a, b) => a.name.localeCompare(b.name, LOCALE, {numeric: true}), big: (a, b) => b.size - a.size};
  files.sort(by[S.sort]);
  const folders = d.folders.filter(f => !q || f.name.toLowerCase().includes(q));
  return {files, folders};
}

function renderFilters(){
  const d = S.data;
  const cnt = {}; d.files.forEach(f => cnt[f.kind] = (cnt[f.kind] || 0) + 1);
  const kinds = Object.keys(cnt);
  if (!kinds.includes(S.kind)) S.kind = 'all';
  $('#kinds').innerHTML = kinds.length > 1 ? [['all', t('Alles', 'All'), d.files.length], ...['image', 'video', 'audio', 'other'].filter(k => cnt[k]).map(k => [k, KIND[k][1], cnt[k]])]
    .map(([k, l, n]) => `<button class="chip" type="button" data-k="${k}" aria-pressed="${S.kind === k}">${esc(l)}<span class="n">${n}</span></button>`).join('') : '';
  const ys = {}; d.files.forEach(f => { const y = f.taken.slice(0, 4); ys[y] = (ys[y] || 0) + 1; });
  const years = Object.keys(ys).sort().reverse();
  if (!years.includes(S.year)) S.year = 'all';
  $('#years').innerHTML = years.length > 1 ? [['all', t('Alle jaren', 'All years'), d.files.length], ...years.map(y => [y, y, ys[y]])]
    .map(([k, l, n]) => `<button class="chip" type="button" data-y="${k}" aria-pressed="${S.year === k}">${esc(l)}<span class="n">${n}</span></button>`).join('') : '';
  $('#sort').value = S.sort;
  document.querySelectorAll('#zoom .chip').forEach(b => b.setAttribute('aria-pressed', b.dataset.z === S.zoom));
}

function tileFolder(f){
  const on = S.sel.has(f.path);
  return `<div class="tile k-folder${on ? ' on' : ''}" data-p="${esc(f.path)}" data-folder="1">
  <button class="th" type="button" data-open="${esc(f.path)}" aria-label="${esc(t('Map openen: ', 'Open folder: ') + f.name)}">${f.cover ? `<img alt="" loading="lazy" src="${esc(f.cover)}" onload="this.classList.add('ok')" onerror="this.remove()">` : ''}<span class="big">${ic('folder')}</span></button>
  <button class="ck" type="button" role="checkbox" aria-checked="${on}" aria-label="${esc(t('Selecteren: ', 'Select: ') + f.name)}">${ic('check')}</button>
  <div class="tx"><div class="nm">${esc(f.name)}</div><div class="mt">${plural(f.items, t('bestand', 'file'), t('bestanden', 'files'))}${f.size ? ' · ' + size(f.size) : ''}</div></div></div>`;
}
function tileFile(f, i){
  const on = S.sel.has(f.path);
  const thumb = f.thumb ? `<img alt="" loading="lazy" src="${esc(f.thumb)}" onload="this.classList.add('ok')" onerror="this.remove()">` : '';
  const badge = f.kind !== 'image' || !f.thumb ? `<span class="badge">${esc(ext(f.name) || '?')}</span>` : '';
  return `<div class="tile k-${f.kind}${on ? ' on' : ''}" data-p="${esc(f.path)}" data-i="${i}">
  <button class="th" type="button" data-view="${i}" aria-label="${esc(t('Bekijken: ', 'View: ') + f.name)}"><span class="big">${ic(f.kind === 'other' ? 'other' : f.kind)}</span>${thumb}${badge}</button>
  <button class="ck" type="button" role="checkbox" aria-checked="${on}" aria-label="${esc(t('Selecteren: ', 'Select: ') + f.name)}">${ic('check')}</button>
  <div class="tx"><div class="nm" title="${esc(f.name)}">${esc(f.name)}</div><div class="mt">${f.dated ? fmtDate(f.taken) : t('gewijzigd ', 'modified ') + fmtDate(f.taken)} · ${size(f.size)}</div></div></div>`;
}

function renderGrid(){
  const g = $('#grid'); g.className = 'z' + S.zoom; g.classList.toggle('sel', S.sel.size > 0);
  const {files, folders} = filtered(); S.shown = files;
  let h = '';
  if (folders.length) h += `<section class="grp"><h2>${t('Mappen', 'Folders')} <span>${folders.length}</span></h2><div class="tiles">${folders.map(tileFolder).join('')}</div></section>`;
  // groeperen op maand, of op jaar als er per maand maar een paar bestanden zijn; niet als de meeste bestanden geen datum hebben
  const dated = files.filter(f => f.dated).length;
  if ((S.sort === 'new' || S.sort === 'old') && files.length > 3 && dated * 2 >= files.length) {
    const months = new Set(files.map(f => monthKey(f.taken))).size;
    const byYear = files.length / months < 3;
    const keyOf = f => byYear ? f.taken.slice(0, 4) : monthKey(f.taken), label = k => byYear ? k : monthLabel(k);
    let cur = null, buf = [];
    const flush = () => { if (buf.length) h += `<section class="grp"><h2>${esc(label(cur))} <span>${buf.length}</span></h2><div class="tiles">${buf.join('')}</div></section>`; buf = []; };
    files.forEach((f, i) => { const k = keyOf(f); if (k !== cur) { flush(); cur = k; } buf.push(tileFile(f, i)); });
    flush();
  } else if (files.length) {
    h += `<section class="grp"><h2>${t('Bestanden', 'Files')} <span>${files.length}</span></h2><div class="tiles">${files.map(tileFile).join('')}</div></section>`;
  }
  if (!h) h = `<div class="card empty">${S.q || S.kind !== 'all' || S.year !== 'all' ? t('Niets gevonden met deze filters.', 'Nothing found with these filters.')
    : t('Deze map is leeg. Sleep bestanden hierheen of kies Uploaden.', 'This folder is empty. Drag files here or choose Upload.')}</div>`;
  g.innerHTML = h;
}

function renderSel(){
  const n = S.sel.size, bar = $('#selbar');
  bar.hidden = !n || S.view !== 'files';
  $('#grid').classList.toggle('sel', n > 0);
  if (!n) return;
  let bytes = 0; const all = [...S.data.files, ...S.data.folders];
  all.forEach(f => { if (S.sel.has(f.path)) bytes += f.size || 0; });
  $('#selTxt').textContent = t(`${n} geselecteerd`, `${n} selected`) + (bytes ? ` · ${size(bytes)}` : '');
}

function toggle(path, on){
  if (on === undefined) on = !S.sel.has(path);
  on ? S.sel.add(path) : S.sel.delete(path);
  const el = document.querySelector(`.tile[data-p="${CSS.escape(path)}"]`);
  if (el) { el.classList.toggle('on', on); el.querySelector('.ck').setAttribute('aria-checked', on); }
}
function tileClick(e){
  const tile = e.target.closest('.tile'); if (!tile) return;
  const p = tile.dataset.p, ck = e.target.closest('.ck');
  if (ck || (S.sel.size && !e.target.closest('[data-open]')) || e.ctrlKey || e.metaKey || e.shiftKey) {
    e.preventDefault();
    if (e.shiftKey && S.last && tile.dataset.i !== undefined) {
      const a = S.shown.findIndex(f => f.path === S.last), b = +tile.dataset.i;
      if (a >= 0) { const [x, y] = a < b ? [a, b] : [b, a]; for (let k = x; k <= y; k++) toggle(S.shown[k].path, true); renderSel(); return; }
    }
    toggle(p); S.last = p; renderSel(); return;
  }
  const open = e.target.closest('[data-open]');
  if (open) { S.dir = open.dataset.open; S.sel.clear(); S.q = ''; $('#q').value = ''; S.year = 'all'; S.kind = 'all'; push(); load(); return; }
  const v = e.target.closest('[data-view]');
  if (v) openLb(+v.dataset.view);
}

// ---- grote weergave
function openLb(i){ S.lb = i; $('#lb').hidden = false; document.body.style.overflow = 'hidden'; showLb(); $('#lbX').focus(); }
function closeLb(){ const m = $('#lbMedia'); m.innerHTML = ''; S.lb = -1; $('#lb').hidden = true; document.body.style.overflow = ''; schedulePoll(); }
function showLb(){
  const f = S.shown[S.lb]; if (!f) return closeLb();
  const m = $('#lbMedia'), src = fileUrl(f.path);
  if (f.kind === 'image') {
    m.innerHTML = `<img alt="${esc(f.name)}" src="${esc(src)}">`;
    m.querySelector('img').onerror = () => { m.innerHTML = `<div class="big">${ic('image')}<p>${t('Je browser kan dit bestand niet tonen. Download het om het te bekijken.', 'Your browser cannot show this file. Download it to view it.')}</p></div>`; };
  } else if (f.kind === 'video') m.innerHTML = `<video controls playsinline preload="metadata" src="${esc(src)}"></video>`;
  else if (f.kind === 'audio') m.innerHTML = `<div class="big">${ic('audio')}<audio controls preload="metadata" src="${esc(src)}"></audio></div>`;
  else m.innerHTML = `<div class="big">${ic(f.kind === 'image' ? 'image' : 'other')}<p>${t('Geen voorbeeld voor dit soort bestand.', 'No preview for this kind of file.')}</p></div>`;
  $('#lbName').textContent = f.name;
  $('#lbPos').textContent = t(`${S.lb + 1} van ${S.shown.length}`, `${S.lb + 1} of ${S.shown.length}`);
  const kv = [[f.dated ? t('Gemaakt', 'Taken') : t('Gewijzigd', 'Modified'), fmtDate(f.taken, true)], [t('Grootte', 'Size'), size(f.size)]];
  if (f.w) kv.push([t('Afmetingen', 'Dimensions'), `${f.w} × ${f.h} px`]);
  kv.push([t('Map', 'Folder'), ROOT + (S.dir ? '/' + S.dir : '')]);
  $('#lbKv').innerHTML = kv.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  const dl = $('#lbDl'); dl.href = fileUrl(f.path, 1); dl.setAttribute('download', f.name);
  $('#pMs').textContent = S.data.ms ? S.data.ms + f.path : '';
  $('#pMs').parentElement.hidden = !S.data.ms;
  $('#pFs').textContent = S.data.fs.replace(/\/$/, '') + '/' + f.path;
  $('#pHint').hidden = !S.data.ms;
  $('#lbPrev').disabled = S.lb <= 0; $('#lbNext').disabled = S.lb >= S.shown.length - 1;
  [S.lb - 1, S.lb + 1].forEach(j => { const g = S.shown[j]; if (g && g.kind === 'image' && g.thumb) { const im = new Image(); im.src = fileUrl(g.path); } });
}
function stepLb(d){ const j = S.lb + d; if (j >= 0 && j < S.shown.length) { S.lb = j; showLb(); } }

// ---- dialogen
function ask(title, text, value, ok){ return new Promise(res => {
  const m = $('#pr'), inp = $('#prI'); $('#prT').textContent = title; $('#prX').textContent = text || ''; $('#prX').hidden = !text; $('#prOk').textContent = ok; inp.value = value || ''; m.hidden = false;
  const dot = (value || '').lastIndexOf('.'); inp.focus(); inp.setSelectionRange(0, dot > 0 ? dot : inp.value.length);
  const done = v => { m.hidden = true; $('#prF').onsubmit = null; res(v); };
  $('#prF').onsubmit = e => { e.preventDefault(); done(inp.value.trim()); }; $('#prNo').onclick = () => done(null); m.onclick = e => { if (e.target === m) done(null); };
}); }
async function pickFolder(title, paths){
  const {folders} = await api('api/folders');
  return new Promise(res => {
    const m = $('#mv'); let pick = null; $('#mvT').textContent = title;
    const blocked = f => paths.some(p => f === p || f.startsWith(p + '/'));
    $('#mvL').innerHTML = folders.map(f => `<li><button type="button" data-f="${esc(f)}" ${blocked(f) || f === S.dir ? 'disabled' : ''} style="padding-left:${12 + (f ? f.split('/').length : 0) * 16}px">${ic('folder')}${esc(f ? f.split('/').pop() : ROOT)}${f === S.dir ? ' <span class="faint small">' + t('(huidige map)', '(current folder)') + '</span>' : ''}</button></li>`).join('');
    $('#mvOk').disabled = true;
    $('#mvL').onclick = e => { const b = e.target.closest('button'); if (!b || b.disabled) return; pick = b.dataset.f; $('#mvL').querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b)); $('#mvOk').disabled = false; };
    const done = v => { m.hidden = true; res(v); };
    $('#mvOk').onclick = () => done(pick); $('#mvNo').onclick = () => done(null); m.onclick = e => { if (e.target === m) done(null); };
    m.hidden = false; i18n(m);
  });
}

// ---- acties
const nameOf = p => p.split('/').pop();
async function doDelete(paths){
  if (!paths.length) return;
  const trash = S.data.trash_days > 0, one = paths.length === 1;
  const what = one ? `"${nameOf(paths[0])}"` : t(`${paths.length} items`, `${paths.length} items`);
  const ok = await confirmBox(t(`${what} verwijderen?`, `Delete ${what}?`),
    trash ? t(`Het gaat naar de prullenbak. Daar kun je het ${S.data.trash_days} dagen terugzetten.`, `It goes to the trash, where you can restore it for ${S.data.trash_days} days.`)
          : t('Dit kan niet ongedaan worden gemaakt.', 'This cannot be undone.'), t('Verwijderen', 'Delete'));
  if (!ok) return;
  try { const r = await post('api/delete', {paths}); paths.forEach(p => S.sel.delete(p));
    toast(trash ? t(`${r.deleted.length} naar de prullenbak`, `${r.deleted.length} moved to the trash`) : t(`${r.deleted.length} verwijderd`, `${r.deleted.length} deleted`));
    if (S.lb >= 0) { const keep = S.lb; await load(true); if (S.shown.length) { S.lb = Math.min(keep, S.shown.length - 1); showLb(); } else closeLb(); } else load(true);
  } catch (e) { toast(e.message, true); }
}
async function doMove(paths){
  if (!paths.length) return;
  const to = await pickFolder(paths.length === 1 ? t(`"${nameOf(paths[0])}" verplaatsen`, `Move "${nameOf(paths[0])}"`) : t(`${paths.length} items verplaatsen`, `Move ${paths.length} items`), paths);
  if (to === null) return;
  try { const r = await post('api/move', {paths, to}); paths.forEach(p => S.sel.delete(p)); toast(t(`Verplaatst naar ${to ? to : ROOT}`, `Moved to ${to ? to : ROOT}`));
    if (S.lb >= 0) closeLb(); load(true); } catch (e) { toast(e.message, true); }
}
async function doRename(path){
  const old = nameOf(path);
  const name = await ask(t('Hernoemen', 'Rename'), '', old, t('Opslaan', 'Save'));
  if (!name || name === old) return;
  try { const r = await post('api/rename', {path, name}); toast(t('Hernoemd', 'Renamed'));
    const wasLb = S.lb >= 0; await load(true);
    if (wasLb) { const j = S.shown.findIndex(f => f.path === r.path); if (j >= 0) { S.lb = j; showLb(); } else closeLb(); }
  } catch (e) { toast(e.message, true); }
}
function download(paths){
  if (paths.length === 1 && S.data.files.some(f => f.path === paths[0])) { location.href = fileUrl(paths[0], 1); return; }
  const q = new URLSearchParams(); paths.forEach(p => q.append('p', p)); location.href = 'api/zip?' + q;
}

// ---- uploaden
const UP = {queue: [], busy: false, CH: 4 * 1048576};
function addUploads(files){
  if (S.view !== 'files') { S.view = 'files'; render(); }
  const ul = $('#uplL');
  for (const file of files) {
    if (!file.size && !file.type && !/\.[a-z0-9]{2,5}$/i.test(file.name)) continue;   // een map, die kan de browser niet zo sturen
    const li = document.createElement('li');
    li.innerHTML = `<div class="r"><span>${esc(file.name)}</span><span class="st">${t('wacht', 'waiting')}</span></div><div class="pb"><i></i></div>`;
    ul.appendChild(li);
    UP.queue.push({file, li, dir: S.dir});
  }
  $('#upl').hidden = !ul.children.length; updUplHead(); runUploads();
}
function updUplHead(){ const all = $('#uplL').children.length, done = $('#uplL').querySelectorAll('li.done').length;
  $('#uplT').textContent = UP.busy || UP.queue.length ? t(`Uploaden: ${done} van ${all}`, `Uploading: ${done} of ${all}`) : t(`${done} van ${all} geüpload`, `${done} of ${all} uploaded`); }
async function runUploads(){
  if (UP.busy) return; UP.busy = true;
  let any = false;
  while (UP.queue.length) {
    const job = UP.queue.shift(), {file, li} = job, st = li.querySelector('.st'), bar = li.querySelector('.pb i');
    const id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/[^0-9a-f]/gi, '').toLowerCase().slice(0, 32).padEnd(16, '0');
    let off = 0, tries = 0;
    try {
      while (true) {
        const end = Math.min(off + UP.CH, file.size);
        const q = new URLSearchParams({dir: job.dir, name: file.name, id, off, total: file.size});
        let r;
        try { r = await fetch('api/upload?' + q, {method: 'POST', headers: {'X-Requested-With': APP, 'X-Lang': LANG, 'Content-Type': 'application/octet-stream'}, body: file.slice(off, end)}); }
        catch (e) { if (++tries > 4) throw new Error(t('Geen verbinding.', 'No connection.')); await new Promise(z => setTimeout(z, 1500 * tries)); continue; }
        const j = await r.json().catch(() => ({}));
        if (r.status === 409 && j.have !== undefined) { off = j.have; continue; }
        if (!r.ok) throw new Error(j.error || r.statusText);
        if (j.have !== undefined && j.have <= off && !j.done) throw new Error(t('De app ontving geen gegevens.', 'The app received no data.'));
        tries = 0; off = j.have !== undefined ? j.have : end;
        bar.style.width = (file.size ? Math.round(off / file.size * 100) : 100) + '%'; st.textContent = file.size ? Math.round(off / file.size * 100) + '%' : '';
        if (j.done) { li.classList.add('done'); st.textContent = j.name !== file.name ? t('opgeslagen als ', 'saved as ') + j.name : t('klaar', 'done'); any = true; break; }
      }
    } catch (e) { li.classList.add('err'); bar.style.width = '100%'; st.textContent = e.message; }
    updUplHead();
  }
  UP.busy = false; updUplHead();
  if (any) load(true);
}

// ---- prullenbak
async function renderTrash(){
  $('#selbar').hidden = true;
  const g = $('#grid'); g.className = '';
  let r; try { r = await api('api/trash'); } catch (e) { g.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
  const left = it => Math.max(0, Math.ceil((it.t + r.days * 86400 - Date.now() / 1000) / 86400));
  const total = r.items.reduce((a, b) => a + (b.size || 0), 0);
  g.innerHTML = `<div class="card" style="overflow:hidden"><div class="thead"><div><h2>${t('Prullenbak', 'Trash')}</h2><p class="small muted" style="margin:2px 0 0">${
    r.days ? t(`Verwijderde bestanden blijven hier ${r.days} dagen, daarna gaan ze definitief weg. Ze tellen niet mee in backups.`, `Deleted files stay here for ${r.days} days, then they are removed for good. They are not included in backups.`)
           : t('De prullenbak staat uit; verwijderen is meteen definitief.', 'The trash is off; deleting is permanent right away.')}</p></div>
    <div style="display:flex;gap:8px"><button class="btn" type="button" id="tBack">${t('Terug', 'Back')}</button>${r.items.length ? `<button class="btn danger" type="button" id="tAll">${ic('trash')}${t('Prullenbak legen', 'Empty trash')} (${size(total)})</button>` : ''}</div></div>
    ${r.items.length ? r.items.map(it => `<div class="trow">${ic(it.dir ? 'folder' : (({image: 'image', video: 'video', audio: 'audio'})[kindOf(it.name)] || 'other'))}
      <div><div class="nm">${esc(it.name)}</div><div class="mt">${t('uit', 'from')} ${esc(it.label || ROOT)}${it.orig.includes('/') ? '/' + esc(it.orig.slice(0, it.orig.lastIndexOf('/'))) : ''} · ${size(it.size || 0)} · ${t('verwijderd', 'deleted')} ${when(it.t)} · ${t(`nog ${left(it)} dagen`, `${left(it)} days left`)}</div></div>
      <div class="ac"><button class="btn" type="button" data-rs="${esc(it.id)}">${ic('restore')}${t('Terugzetten', 'Restore')}</button><button class="btn danger" type="button" data-pg="${esc(it.id)}" data-n="${esc(it.name)}">${t('Definitief weg', 'Delete forever')}</button></div></div>`).join('')
      : `<div class="empty">${t('De prullenbak is leeg.', 'The trash is empty.')}</div>`}</div>`;
}
function kindOf(n){ const e = ext(n).toLowerCase();
  return ['jpg','jpeg','png','webp','gif','bmp','tif','tiff','heic','heif','avif','svg'].includes(e) ? 'image' : ['mp4','m4v','mov','webm','mkv','avi','3gp'].includes(e) ? 'video'
    : ['mp3','m4a','aac','wav','flac','ogg','oga','opus'].includes(e) ? 'audio' : 'other'; }

// ---- navigatie en gebeurtenissen
function push(){ try { history.pushState({dir: S.dir, view: S.view}, '', '#' + encodeURIComponent(S.view === 'trash' ? ':trash' : S.dir)); } catch (e) {} }
window.addEventListener('popstate', e => { const st = e.state || {}; S.dir = st.dir || ''; S.view = st.view || 'files'; S.sel.clear(); if (S.lb >= 0) closeLb(); load(); });

$('#grid').addEventListener('click', async e => {
  if (S.view === 'trash') {
    const rs = e.target.closest('[data-rs]'), pg = e.target.closest('[data-pg]');
    if (e.target.closest('#tBack')) { S.view = 'files'; push(); render(); return; }
    try {
      if (rs) { const r = await post('api/restore', {ids: [rs.dataset.rs]}); toast(t('Teruggezet: ', 'Restored: ') + r.restored.join(', ')); await load(true); }
      if (pg && await confirmBox(t(`"${pg.dataset.n}" definitief verwijderen?`, `Delete "${pg.dataset.n}" forever?`), t('Dit kan niet ongedaan worden gemaakt.', 'This cannot be undone.'), t('Definitief verwijderen', 'Delete forever'))) {
        await post('api/purge', {ids: [pg.dataset.pg]}); toast(t('Definitief verwijderd', 'Deleted forever')); await load(true); }
      if (e.target.closest('#tAll') && await confirmBox(t('Prullenbak legen?', 'Empty the trash?'), t('Alles in de prullenbak wordt definitief verwijderd.', 'Everything in the trash is deleted for good.'), t('Legen', 'Empty'))) {
        await post('api/purge', {all: true}); toast(t('Prullenbak geleegd', 'Trash emptied')); await load(true); }
    } catch (err) { toast(err.message, true); }
    return;
  }
  tileClick(e);
});
$('#crumbs').addEventListener('click', e => { const b = e.target.closest('[data-d]'); if (!b) return; S.dir = b.dataset.d; S.sel.clear(); S.year = 'all'; S.kind = 'all'; push(); load(); });
$('#kinds').addEventListener('click', e => { const b = e.target.closest('[data-k]'); if (b) { S.kind = b.dataset.k; renderFilters(); renderGrid(); } });
$('#years').addEventListener('click', e => { const b = e.target.closest('[data-y]'); if (b) { S.year = b.dataset.y; renderFilters(); renderGrid(); } });
$('#zoom').addEventListener('click', e => { const b = e.target.closest('[data-z]'); if (b) { S.zoom = b.dataset.z; ls.set('zoom', S.zoom); renderFilters(); $('#grid').className = 'z' + S.zoom; } });
$('#sort').addEventListener('change', e => { S.sort = e.target.value; ls.set('sort', S.sort); renderGrid(); });
let qT; $('#q').addEventListener('input', e => { clearTimeout(qT); qT = setTimeout(() => { S.q = e.target.value; renderGrid(); }, 120); });
$('#bTrash').addEventListener('click', () => { S.view = S.view === 'trash' ? 'files' : 'trash'; S.sel.clear(); push(); render(); });
$('#bMkdir').addEventListener('click', async () => { const name = await ask(t('Nieuwe map', 'New folder'), t('In ', 'In ') + ROOT + (S.dir ? '/' + S.dir : ''), '', t('Maken', 'Create'));
  if (!name) return; try { await post('api/mkdir', {dir: S.dir, name}); toast(t('Map gemaakt', 'Folder created')); load(true); } catch (e) { toast(e.message, true); } });
$('#fileIn').addEventListener('change', e => { addUploads([...e.target.files]); e.target.value = ''; });
$('#uplX').addEventListener('click', () => { if (UP.busy) return toast(t('Wacht tot het uploaden klaar is.', 'Wait until the upload is finished.')); $('#upl').hidden = true; $('#uplL').innerHTML = ''; });
$('#sAll').addEventListener('click', () => { S.shown.forEach(f => toggle(f.path, true)); renderSel(); });
$('#sX').addEventListener('click', () => { [...S.sel].forEach(p => toggle(p, false)); renderSel(); });
$('#sDel').addEventListener('click', () => doDelete([...S.sel]));
$('#sMv').addEventListener('click', () => doMove([...S.sel]));
$('#sDl').addEventListener('click', () => download([...S.sel]));
$('#lbX').addEventListener('click', closeLb);
$('#lbPrev').addEventListener('click', () => stepLb(-1));
$('#lbNext').addEventListener('click', () => stepLb(1));
$('#lbDel').addEventListener('click', () => doDelete([S.shown[S.lb].path]));
$('#lbMv').addEventListener('click', () => doMove([S.shown[S.lb].path]));
$('#lbRn').addEventListener('click', () => doRename(S.shown[S.lb].path));
$('#lbStage').addEventListener('click', e => { if (e.target.id === 'lbStage' || e.target.id === 'lbMedia') closeLb(); });
document.querySelectorAll('[data-copy]').forEach(b => b.addEventListener('click', async () => {
  const txt = $('#' + b.dataset.copy).textContent;
  try { await navigator.clipboard.writeText(txt); } catch (e) { const ta = document.createElement('textarea'); ta.value = txt; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); }
  toast(t('Gekopieerd', 'Copied')); }));
let sx = null; $('#lbStage').addEventListener('touchstart', e => { sx = e.touches[0].clientX; }, {passive: true});
$('#lbStage').addEventListener('touchend', e => { if (sx === null) return; const dx = e.changedTouches[0].clientX - sx; sx = null; if (Math.abs(dx) > 60) stepLb(dx < 0 ? 1 : -1); });
document.addEventListener('keydown', e => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  const modal = !$('#pr').hidden || !$('#mv').hidden || !$('#modal').hidden;
  if (S.lb >= 0 && !modal) {
    if (e.key === 'Escape') closeLb(); else if (e.key === 'ArrowLeft') stepLb(-1); else if (e.key === 'ArrowRight') stepLb(1);
    else if (e.key === 'Delete') doDelete([S.shown[S.lb].path]); else if (e.key === 'F2') { e.preventDefault(); doRename(S.shown[S.lb].path); }
    return;
  }
  if (typing || modal || S.view !== 'files') { if (e.key === 'Escape' && !$('#pr').hidden) $('#prNo').click(); else if (e.key === 'Escape' && !$('#mv').hidden) $('#mvNo').click(); return; }
  if (e.key === 'Escape' && S.sel.size) { [...S.sel].forEach(p => toggle(p, false)); renderSel(); }
  else if (e.key === 'Delete' && S.sel.size) doDelete([...S.sel]);
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); S.shown.forEach(f => toggle(f.path, true)); renderSel(); }
});
let dragN = 0; const hasFiles = e => [...(e.dataTransfer?.types || [])].includes('Files');
window.addEventListener('dragenter', e => { if (!hasFiles(e)) return; e.preventDefault(); dragN++; $('#dropT').textContent = t('Loslaten om te uploaden naar ', 'Drop to upload to ') + ROOT + (S.dir ? '/' + S.dir : ''); $('#drop').hidden = false; });
window.addEventListener('dragover', e => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener('dragleave', e => { if (!hasFiles(e)) return; if (--dragN <= 0) { dragN = 0; $('#drop').hidden = true; } });
window.addEventListener('drop', e => { if (!hasFiles(e)) return; e.preventDefault(); dragN = 0; $('#drop').hidden = true; addUploads([...e.dataTransfer.files]); });
document.addEventListener('visibilitychange', () => { if (!document.hidden && S.data && S.lb < 0 && S.view === 'files') load(true); });

(() => { const h = decodeURIComponent(location.hash.slice(1) || ''); if (h === ':trash') S.view = 'trash'; else S.dir = h;
  try { history.replaceState({dir: S.dir, view: S.view}, ''); } catch (e) {} load(); })();
"""
JS = JS.replace("__ICONS__", __import__("json").dumps(ICONS))

Handler.PAGE = hb.page("Media manager", BODY, CSSX, JS)


# ---------------------------------------------------------------- start
def demo_media():
    """Voorbeeldbestanden zoals in een echte mediamap, alleen als de map nog leeg is."""
    if any(not n.startswith(".") for n in os.listdir(ROOT)) or not PIL_OK:
        return
    import random
    random.seed(7)
    from PIL import ImageDraw
    names = ["20170605_112517.jpg", "20190803_102543.jpg", "20190803_105548.jpg", "20190805_132953.jpg", "20190806_130125.jpg",
             "20240616_132254.jpg", "20240717_143905.jpg", "20241102_122229.jpg", "20250402_165121.jpg", "20250430_154606.jpg",
             "20250501_154914.jpg", "20250607_113941.jpg", "20250614_201844.jpg", "20250615_161008.jpg", "20250815_145843.jpg",
             "PXL_20250921_081530123.jpg", "Screenshot 2025-10-02 at 09.14.55.png", "carport.jpg", "voordeur.png"]
    for i, n in enumerate(names):
        w, h = random.choice([(1600, 1200), (1200, 1600), (1920, 1080), (4000, 3000)])
        top = tuple(random.randint(40, 200) for _ in range(3))
        im = Image.new("RGB", (w, h), top)
        dr = ImageDraw.Draw(im)
        for k in range(h // 2, h, 8):
            dr.rectangle([0, k, w, k + 8], fill=tuple(max(0, c - (k - h // 2) * 120 // h) for c in top))
        dr.ellipse([w * .6, h * .15, w * .8, h * .15 + w * .2], fill=(255, 226, 140))
        for _ in range(4):
            x = random.randint(0, w)
            dr.polygon([(x - w * .3, h), (x, h * random.uniform(.35, .6)), (x + w * .3, h)], fill=tuple(random.randint(20, 120) for _ in range(3)))
        im.save(os.path.join(ROOT, n), quality=82)
    os.makedirs(os.path.join(ROOT, "mp3"))
    for n in ("deurbel.mp3", "wasmachine klaar.mp3", "goedemorgen.mp3"):
        with open(os.path.join(ROOT, "mp3", n), "wb") as f:
            f.write(b"ID3" + bytes(2000))
    os.makedirs(os.path.join(ROOT, "camera"))
    with open(os.path.join(ROOT, "camera", "oprit_2025-11-10_18-04.mp4"), "wb") as f:
        f.write(bytes(50000))
    shutil.copy(os.path.join(ROOT, "carport.jpg"), os.path.join(ROOT, "camera", "snapshot_voordeur.jpg"))


def main():
    global INDEX
    if hb.DEMO:
        demo_media()
    INDEX = hb.load_json(INDEX_FILE, {}) or {}
    TRASHED.extend(x for x in (hb.load_json(TRASH_FILE, []) or []) if isinstance(x, dict) and x.get("id"))
    if not PIL_OK:
        print("Pillow ontbreekt: geen miniaturen.", flush=True)
    if ROOT_ERROR:
        print(ROOT_ERROR["nl"], flush=True)
    print(f"Mediamap: {ROOT} · prullenbak: {TRASH_DAYS} dagen · miniaturen: {THUMB_FMT}", flush=True)
    hb.start_thread(worker)
    hb.serve(Handler, port_local=8095)


if __name__ == "__main__":
    main()
