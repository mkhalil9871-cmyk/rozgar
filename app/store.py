"""SQLite storage for workers, jobs, offers (bids) and chat messages.

The database file lives at ROZGAR_DB (default data/rozgar.db) and is created on
first run. Sample workers are loaded from the skill's workers.json the first time.
Jobs keep their flexible details (diagnosis, price, trace, photos) as JSON in one
column; offers and messages have their own tables.
"""

import json
import math
import os
import random
import sqlite3
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("ROZGAR_DB", ROOT / "data" / "rozgar.db"))
SEED_WORKERS = ROOT / "skills" / "rozgar-repair-estimator" / "workers.json"

_lock = threading.RLock()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS workers (
    id        TEXT PRIMARY KEY,
    section   TEXT NOT NULL,
    trade     TEXT NOT NULL,
    area      TEXT NOT NULL,
    rating    REAL NOT NULL DEFAULT 0,
    jobs_done INTEGER NOT NULL DEFAULT 0,
    created   REAL NOT NULL,
    data      TEXT NOT NULL            -- name, years, verified, past_projects (JSON)
);
CREATE TABLE IF NOT EXISTS jobs (
    id       TEXT PRIMARY KEY,
    created  REAL NOT NULL,
    status   TEXT NOT NULL,            -- open, in_progress, verified
    section  TEXT NOT NULL,
    trade    TEXT NOT NULL,
    area     TEXT,
    offer    INTEGER,
    demo     INTEGER NOT NULL DEFAULT 0,
    data     TEXT NOT NULL             -- diagnosis, price, booking, photo, trace... (JSON)
);
CREATE TABLE IF NOT EXISTS bids (
    id        TEXT PRIMARY KEY,
    job_id    TEXT NOT NULL REFERENCES jobs(id),
    worker_id TEXT NOT NULL REFERENCES workers(id),
    amount    INTEGER NOT NULL,
    check_status TEXT NOT NULL,        -- fair, high, low
    note      TEXT,
    time      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    TEXT NOT NULL REFERENCES jobs(id),
    worker_id TEXT NOT NULL REFERENCES workers(id),
    sender    TEXT NOT NULL,           -- customer or worker
    text      TEXT NOT NULL,
    time      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_section ON jobs(section, created);
CREATE INDEX IF NOT EXISTS idx_bids_job ON bids(job_id);
CREATE INDEX IF NOT EXISTS idx_messages_job ON messages(job_id, worker_id);
"""

# columns stored outside the JSON blob
WORKER_COLS = ("id", "section", "trade", "area", "rating", "jobs_done")
JOB_COLS = ("id", "created", "status", "section", "trade", "area", "offer", "demo")


def _init():
    with _lock:
        _db.executescript(SCHEMA)
        if _db.execute("SELECT COUNT(*) FROM workers").fetchone()[0] == 0:
            for w in json.loads(SEED_WORKERS.read_text(encoding="utf-8"))["workers"]:
                add_worker(w)
        _db.commit()


# ---------- workers ----------

def _row_to_worker(row):
    w = json.loads(row["data"])
    w.update({k: row[k] for k in WORKER_COLS})
    return w


def add_worker(w):
    data = {k: v for k, v in w.items() if k not in WORKER_COLS}
    with _lock:
        _db.execute(
            "INSERT INTO workers (id, section, trade, area, rating, jobs_done, created, data) VALUES (?,?,?,?,?,?,?,?)",
            (w["id"], w["section"], w["trade"], w["area"], w.get("rating", 0), w.get("jobs_done", 0), time.time(),
             json.dumps(data, ensure_ascii=False)),
        )
        _db.commit()
    return w


def new_worker_id():
    with _lock:
        n = _db.execute("SELECT COUNT(*) FROM workers").fetchone()[0]
    return f"n{n + 1:03d}-{uuid.uuid4().hex[:4]}"


def worker(worker_id):
    with _lock:
        row = _db.execute("SELECT * FROM workers WHERE id = ?", (worker_id,)).fetchone()
    return _row_to_worker(row) if row else None


def all_workers():
    with _lock:
        rows = _db.execute("SELECT * FROM workers ORDER BY created").fetchall()
    return [_row_to_worker(r) for r in rows]


def workers_for(section, area="", trade=""):
    with _lock:
        rows = _db.execute("SELECT * FROM workers WHERE section = ?", (section,)).fetchall()
    ws = [_row_to_worker(r) for r in rows]
    return sorted(ws, key=lambda w: (bool(trade) and w["trade"] != trade, w["area"] != area, -w["rating"]))


def areas():
    with _lock:
        return [r[0] for r in _db.execute("SELECT DISTINCT area FROM workers ORDER BY area")]


def add_project(worker_id, project):
    """A Gemma-verified job joins the worker's public portfolio."""
    with _lock:
        w = worker(worker_id)
        if not w:
            return None
        w["past_projects"].insert(0, project)
        data = {k: v for k, v in w.items() if k not in WORKER_COLS}
        _db.execute("UPDATE workers SET jobs_done = jobs_done + 1, data = ? WHERE id = ?",
                    (json.dumps(data, ensure_ascii=False), worker_id))
        _db.commit()
    return worker(worker_id)


# ---------- jobs ----------

def _assemble(row):
    job = json.loads(row["data"])
    job.update({k: row[k] for k in JOB_COLS})
    job["demo"] = bool(row["demo"])
    bids = _db.execute("SELECT * FROM bids WHERE job_id = ? ORDER BY time", (row["id"],)).fetchall()
    job["bids"] = [{"id": b["id"], "worker_id": b["worker_id"], "worker": worker(b["worker_id"]), "amount": b["amount"],
                    "note": b["note"], "check": {"status": b["check_status"]}, "time": b["time"]} for b in bids]
    msgs = _db.execute("SELECT * FROM messages WHERE job_id = ? ORDER BY id", (row["id"],)).fetchall()
    job["messages"] = [{"worker_id": m["worker_id"], "sender": m["sender"], "text": m["text"], "time": m["time"]} for m in msgs]
    return job


def _save(job):
    data = {k: v for k, v in job.items() if k not in JOB_COLS and k not in ("bids", "messages")}
    _db.execute(
        "INSERT OR REPLACE INTO jobs (id, created, status, section, trade, area, offer, demo, data) VALUES (?,?,?,?,?,?,?,?,?)",
        (job["id"], job["created"], job["status"], job["section"], job.get("trade") or job["section"], job.get("area"),
         job.get("offer"), int(bool(job.get("demo"))), json.dumps(data, ensure_ascii=False)),
    )
    _db.commit()


def create(job):
    with _lock:
        job.update({"id": uuid.uuid4().hex[:8], "created": time.time(), "status": "open", "accepted": None, "verification": None})
        _save(job)
        return get(job["id"])


def get(job_id):
    with _lock:
        row = _db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _assemble(row) if row else None


def all_jobs(section=None):
    with _lock:
        if section:
            rows = _db.execute("SELECT * FROM jobs WHERE section = ? ORDER BY created DESC", (section,)).fetchall()
        else:
            rows = _db.execute("SELECT * FROM jobs ORDER BY created DESC").fetchall()
        return [_assemble(r) for r in rows]


def update(job_id, **fields):
    with _lock:
        job = get(job_id)
        job.update(fields)
        _save(job)
        return get(job_id)


def add_bid(job_id, bid):
    with _lock:
        bid_id = uuid.uuid4().hex[:6]
        _db.execute(
            "INSERT INTO bids (id, job_id, worker_id, amount, check_status, note, time) VALUES (?,?,?,?,?,?,?)",
            (bid_id, job_id, bid["worker_id"], bid["amount"], bid["check"]["status"], bid.get("note", ""), time.time()),
        )
        _db.commit()
        return next(b for b in get(job_id)["bids"] if b["id"] == bid_id)


def add_message(job_id, msg):
    with _lock:
        _db.execute("INSERT INTO messages (job_id, worker_id, sender, text, time) VALUES (?,?,?,?,?)",
                    (job_id, msg["worker_id"], msg["sender"], msg["text"], time.time()))
        _db.commit()
    return msg


# ---------- distance between Faisalabad areas ----------

# approximate centre of each area, enough to show "2.4 km away" on an offer
AREA_COORDS = {
    "Madina Town": (31.4211, 73.1160), "Peoples Colony": (31.4002, 73.1034), "D Ground": (31.4045, 73.0927),
    "Gulberg": (31.4268, 73.0950), "Samanabad": (31.3966, 73.0646), "Jinnah Colony": (31.4255, 73.0786),
    "Satiana Road": (31.3905, 73.0990), "Batala Colony": (31.4330, 73.1080),
}


def km_between(a, b):
    if a not in AREA_COORDS or b not in AREA_COORDS:
        return None
    (la1, lo1), (la2, lo2) = AREA_COORDS[a], AREA_COORDS[b]
    x = math.radians(lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2))
    y = math.radians(la2 - la1)
    return round(max(0.6, 6371 * math.hypot(x, y)), 1)


# ---------- sample offers (demo) ----------

SAMPLE_OFFERS = os.getenv("ROZGAR_SAMPLE_OFFERS", "1") == "1"


def schedule_sample_offers(job_id):
    """Like InDrive: a few nearby sample workers answer a new job within seconds.

    Offers are labelled as samples. Turn off with ROZGAR_SAMPLE_OFFERS=0.
    """
    if not SAMPLE_OFFERS:
        return
    job = get(job_id)
    if not job or job.get("demo"):
        return
    invited = job.get("invited", [])
    candidates = workers_for(job["section"], job.get("area") or "", job.get("trade") or "")
    candidates = sorted(candidates, key=lambda w: w["id"] not in invited)[:3]
    lo, hi = job["price"]["fair_min"], job["price"]["fair_max"]

    def offer(w):
        j = get(job_id)
        if not j or j["status"] != "open" or any(b["worker_id"] == w["id"] for b in j["bids"]):
            return
        amount = int(round(random.uniform(lo, hi * 1.05) / 50) * 50) or lo
        status = "high" if amount > hi * 1.25 else "low" if amount < lo * 0.75 else "fair"
        add_bid(job_id, {"worker_id": w["id"], "amount": amount, "note": "sample", "check": {"status": status}})

    for delay, w in zip((3, 7, 12), candidates):
        threading.Timer(delay, offer, args=(w,)).start()


# ---------- demo ----------

DEMO_JOBS = {
    "bijli": ("Two burnt sockets in the living room", "Sockets are black around the pins; loose wires overheated.", "دو جلے ہوئے ساکٹ تبدیل کریں، تاریں کس دیں۔ دو نئے ساکٹ ساتھ لائیں۔", [("socket", 2, 350)], 1300, 1800, 1500),
    "ac": ("Split AC not cooling, needs service", "Dirty filters and coil; gas pressure should be checked.", "اسپلٹ اے سی کی سروس کریں، فلٹر اور کوائل صاف کریں، گیس چیک کریں۔", [], 2100, 2900, 2500),
    "appliance": ("Washing machine does not spin", "Likely a worn belt or a faulty motor capacitor.", "واشنگ مشین کا بیلٹ اور کیپیسیٹر چیک کریں۔ کیپیسیٹر ساتھ لائیں۔", [("motor capacitor", 1, 600)], 1300, 1900, 1600),
    "pani": ("Kitchen tap leaking all night", "Worn tap washer; the joint under the sink needs new teflon.", "کچن کا نلکا ٹپک رہا ہے۔ واشر بدلیں اور ٹیفلون لگائیں۔", [("tap washer", 2, 50), ("teflon tape", 1, 80)], 500, 750, 650),
    "deewar": ("6 ft crack in bedroom wall near the window", "Plaster crack from settlement; surface depth only.", "کھڑکی کے پاس 6 فٹ دراڑ بھریں اور پلستر کریں۔ سیمنٹ گاہک دے گا۔", [], 700, 1000, 850),
    "lakri": ("Bedroom door not closing", "Loose hinge and swollen door edge.", "دروازے کا قبضہ کسیں اور کنارہ رندا کریں۔", [("door hinge", 2, 300)], 1100, 1600, 1300),
    "rang": ("Paint one room (about 300 sq ft)", "Old paint faded; two coats of emulsion needed.", "ایک کمرہ، تقریباً 300 مربع فٹ، دو کوٹ ایملشن۔ پینٹ گاہک دے گا۔", [], 5100, 6900, 6000),
    "gas": ("Gas stove burner flame is weak", "Blocked burner jet; needs cleaning.", "چولہے کا جیٹ صاف کریں، لیک چیک کریں۔", [], 700, 950, 800),
    "computer": ("Install Windows 11 on a laptop", "Fresh install; customer wants files saved first.", "لیپ ٹاپ پر ونڈوز 11 انسٹال کریں، پہلے فائلیں محفوظ کریں۔", [("usb drive 16gb", 1, 1200)], 2300, 3200, 2700),
}
DEMO_ADDRESSES = ["House 14, Street 3", "House 7-B, Street 9", "Flat 2, Al-Noor Plaza"]


def seed_demo_job(w, section_info, worker_info):
    """One clearly labelled sample job inviting this worker, so the worker view is never empty in a demo."""
    with _lock:
        for j in all_jobs(w["section"]):
            if j["demo"] and w["id"] in j.get("invited", []) and j["status"] == "open":
                return j
        summary, cause, card, mats, lo, hi, offer = DEMO_JOBS.get(w["trade"], DEMO_JOBS["bijli"])
        materials = [{"item": i, "qty": q, "unit_pkr": p, "total_pkr": q * p} for i, q, p in mats]
        n = _db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        return create({
            "demo": True,
            "lang": "ur",
            "section": w["section"],
            "section_info": section_info,
            "trade": w["trade"],
            "worker_info": worker_info,
            "subcategory": None,
            "answers": {},
            "symptoms": [],
            "problem_summary": summary,
            "diagnosis": summary,
            "likely_cause": cause,
            "job_card_urdu": card,
            "price": {"materials": materials, "fair_min": lo, "fair_max": hi},
            "safety": {"level": "none", "advice": "", "rule_reasons": [], "helplines": []},
            "photo": None,
            "offer": offer,
            "offer_check": {"status": "fair"},
            "area": w["area"],
            "invited": [w["id"]],
            "customer": "Sample customer",
            "booking": {"address": DEMO_ADDRESSES[n % len(DEMO_ADDRESSES)], "landmark": "near Jamia Masjid",
                        "day": "tomorrow", "slot": "morning", "payment": "cash"},
            "trace": [],
        })


_init()
