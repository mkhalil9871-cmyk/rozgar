"""Storage for workers, jobs, offers (bids) and chat messages.

Two backends, same tables:
- Postgres when DATABASE_URL (or POSTGRES_URL) is set, e.g. a free Neon database on Vercel,
  where the disk is not permanent and many function instances share one database.
- SQLite otherwise, in ROZGAR_DB (default data/rozgar.db), for running on a laptop.

Sample workers are loaded from the skill's workers.json the first time. Jobs keep their
flexible details (diagnosis, price, trace, photos) as JSON in one column; offers and
messages have their own tables.
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
SEED_WORKERS = ROOT / "skills" / "rozgar-repair-estimator" / "workers.json"
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or ""
PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
# on Vercel without a database the code folder is read-only: fall back to a temporary file
DB_PATH = Path(os.getenv("ROZGAR_DB") or ("/tmp/rozgar.db" if os.getenv("VERCEL") else ROOT / "data" / "rozgar.db"))

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS workers (
    id        TEXT PRIMARY KEY,
    section   TEXT NOT NULL,
    trade     TEXT NOT NULL,
    area      TEXT NOT NULL,
    rating    {real} NOT NULL DEFAULT 0,
    jobs_done INTEGER NOT NULL DEFAULT 0,
    created   {real} NOT NULL,
    data      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id       TEXT PRIMARY KEY,
    created  {real} NOT NULL,
    status   TEXT NOT NULL,
    section  TEXT NOT NULL,
    trade    TEXT NOT NULL,
    area     TEXT,
    offer    INTEGER,
    demo     INTEGER NOT NULL DEFAULT 0,
    data     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bids (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL,
    worker_id    TEXT NOT NULL,
    amount       INTEGER NOT NULL,
    check_status TEXT NOT NULL,
    note         TEXT,
    time         {real} NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id        {serial},
    job_id    TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    sender    TEXT NOT NULL,
    text      TEXT NOT NULL,
    time      {real} NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_section ON jobs(section, created);
CREATE INDEX IF NOT EXISTS idx_bids_job ON bids(job_id);
CREATE INDEX IF NOT EXISTS idx_messages_job ON messages(job_id, worker_id)
"""

WORKER_COLS = ("id", "section", "trade", "area", "rating", "jobs_done")
JOB_COLS = ("id", "created", "status", "section", "trade", "area", "offer", "demo")


# ---------- connection ----------

def _connect():
    global _conn
    if PG:
        import psycopg
        from psycopg.rows import dict_row
        _conn = psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row)
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def _run(sql, params=()):
    """Run one statement on either backend; reconnect once if a hosted DB dropped the connection."""
    if PG:
        sql = sql.replace("?", "%s")
    with _lock:
        for attempt in (1, 2):
            try:
                cur = (_conn or _connect()).execute(sql, params)
                if not PG:
                    _conn.commit()
                return cur
            except Exception as e:
                if not PG or attempt == 2 or "closed" not in str(e).lower() and "connection" not in str(e).lower():
                    raise
                _connect()


def _all(sql, params=()):
    return _run(sql, params).fetchall()


def _one(sql, params=()):
    return _run(sql, params).fetchone()


def _init():
    schema = (SCHEMA.replace("{serial}", "SERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY AUTOINCREMENT")
              .replace("{real}", "DOUBLE PRECISION" if PG else "REAL"))
    for stmt in schema.split(";"):
        if stmt.strip():
            _run(stmt)
    if _one("SELECT COUNT(*) AS n FROM workers")["n"] == 0:
        for w in json.loads(SEED_WORKERS.read_text(encoding="utf-8"))["workers"]:
            add_worker(w)


# ---------- workers ----------

def _row_to_worker(row):
    w = json.loads(row["data"])
    w.update({k: row[k] for k in WORKER_COLS})
    return w


def add_worker(w):
    data = {k: v for k, v in w.items() if k not in WORKER_COLS}
    _run("INSERT INTO workers (id, section, trade, area, rating, jobs_done, created, data) VALUES (?,?,?,?,?,?,?,?) "
         "ON CONFLICT (id) DO NOTHING",
         (w["id"], w["section"], w["trade"], w["area"], w.get("rating", 0), w.get("jobs_done", 0), time.time(),
          json.dumps(data, ensure_ascii=False)))
    return w


def new_worker_id():
    n = _one("SELECT COUNT(*) AS n FROM workers")["n"]
    return f"n{n + 1:03d}-{uuid.uuid4().hex[:4]}"


def worker(worker_id):
    row = _one("SELECT * FROM workers WHERE id = ?", (worker_id,))
    return _row_to_worker(row) if row else None


def all_workers():
    return [_row_to_worker(r) for r in _all("SELECT * FROM workers ORDER BY created")]


def workers_for(section, area="", trade=""):
    ws = [_row_to_worker(r) for r in _all("SELECT * FROM workers WHERE section = ?", (section,))]
    return sorted(ws, key=lambda w: (bool(trade) and w["trade"] != trade, w["area"] != area, -w["rating"]))


def areas():
    return [r["area"] for r in _all("SELECT DISTINCT area FROM workers ORDER BY area")]


def add_project(worker_id, project):
    """A Gemma-verified job joins the worker's public portfolio."""
    with _lock:
        w = worker(worker_id)
        if not w:
            return None
        w["past_projects"].insert(0, project)
        data = {k: v for k, v in w.items() if k not in WORKER_COLS}
        _run("UPDATE workers SET jobs_done = jobs_done + 1, data = ? WHERE id = ?", (json.dumps(data, ensure_ascii=False), worker_id))
    return worker(worker_id)


# ---------- jobs ----------

def _assemble(row):
    job = json.loads(row["data"])
    job.update({k: row[k] for k in JOB_COLS})
    job["demo"] = bool(row["demo"])
    bids = _all("SELECT * FROM bids WHERE job_id = ? ORDER BY time", (row["id"],))
    job["bids"] = [{"id": b["id"], "worker_id": b["worker_id"], "worker": worker(b["worker_id"]), "amount": b["amount"],
                    "note": b["note"], "check": {"status": b["check_status"]}, "time": b["time"]} for b in bids]
    msgs = _all("SELECT * FROM messages WHERE job_id = ? ORDER BY id", (row["id"],))
    job["messages"] = [{"worker_id": m["worker_id"], "sender": m["sender"], "text": m["text"], "time": m["time"]} for m in msgs]
    return job


def _save(job):
    data = {k: v for k, v in job.items() if k not in JOB_COLS and k not in ("bids", "messages")}
    _run("INSERT INTO jobs (id, created, status, section, trade, area, offer, demo, data) VALUES (?,?,?,?,?,?,?,?,?) "
         "ON CONFLICT (id) DO UPDATE SET status = excluded.status, area = excluded.area, offer = excluded.offer, data = excluded.data",
         (job["id"], job["created"], job["status"], job["section"], job.get("trade") or job["section"], job.get("area"),
          job.get("offer"), int(bool(job.get("demo"))), json.dumps(data, ensure_ascii=False)))


def create(job):
    with _lock:
        job.update({"id": uuid.uuid4().hex[:8], "created": time.time(), "status": "open", "accepted": None, "verification": None})
        _save(job)
        return get(job["id"])


def get(job_id):
    row = _one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not row:
        return None
    if _deliver_sample_offers(json.loads(row["data"]), row):
        row = _one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return _assemble(row)


def all_jobs(section=None):
    if section:
        rows = _all("SELECT * FROM jobs WHERE section = ? ORDER BY created DESC", (section,))
    else:
        rows = _all("SELECT * FROM jobs ORDER BY created DESC")
    for r in rows:
        _deliver_sample_offers(json.loads(r["data"]), r)
    return [_assemble(r) for r in rows]


def update(job_id, **fields):
    with _lock:
        job = get(job_id)
        job.update(fields)
        _save(job)
        return get(job_id)


def delete_job(job_id):
    with _lock:
        _run("DELETE FROM messages WHERE job_id = ?", (job_id,))
        _run("DELETE FROM bids WHERE job_id = ?", (job_id,))
        _run("DELETE FROM jobs WHERE id = ?", (job_id,))


def add_bid(job_id, bid):
    bid_id = uuid.uuid4().hex[:6]
    _run("INSERT INTO bids (id, job_id, worker_id, amount, check_status, note, time) VALUES (?,?,?,?,?,?,?)",
         (bid_id, job_id, bid["worker_id"], bid["amount"], bid["check"]["status"], bid.get("note", ""), time.time()))
    return bid_id


def add_message(job_id, msg):
    _run("INSERT INTO messages (job_id, worker_id, sender, text, time) VALUES (?,?,?,?,?)",
         (job_id, msg["worker_id"], msg["sender"], msg["text"], time.time()))
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

    The offers are planned now and stored with the job, then delivered whenever the
    job is read after their time has come. No timers, so it also works on serverless
    hosts such as Vercel. Offers are labelled as samples; ROZGAR_SAMPLE_OFFERS=0 turns this off.
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
    now = time.time()
    plan = [{"worker_id": w["id"], "amount": int(round(random.uniform(lo, hi * 1.05) / 50) * 50) or lo, "due": now + delay}
            for delay, w in zip((3, 7, 12), candidates)]
    update(job_id, sample_plan=plan)


def _deliver_sample_offers(data, row):
    """Turn planned sample offers whose time has come into real offers. Returns True if any were added."""
    plan = data.get("sample_plan")
    if not plan or row["status"] != "open":
        return False
    now = time.time()
    due = [p for p in plan if p["due"] <= now]
    if not due:
        return False
    with _lock:
        lo = data.get("price", {}).get("fair_min", 0)
        hi = data.get("price", {}).get("fair_max", 0)
        taken = {b["worker_id"] for b in _all("SELECT worker_id FROM bids WHERE job_id = ?", (row["id"],))}
        for p in due:
            if p["worker_id"] not in taken:
                a = p["amount"]
                status = "high" if a > hi * 1.25 else "low" if a < lo * 0.75 else "fair"
                add_bid(row["id"], {"worker_id": p["worker_id"], "amount": a, "note": "sample", "check": {"status": status}})
        data["sample_plan"] = [p for p in plan if p["due"] > now]
        _run("UPDATE jobs SET data = ? WHERE id = ?", (json.dumps(data, ensure_ascii=False), row["id"]))
    return True


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
        n = _one("SELECT COUNT(*) AS n FROM jobs")["n"]
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
