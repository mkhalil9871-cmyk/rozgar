"""In-memory job store. Fine for a single-instance free deploy; resets on restart."""

import threading
import time
import uuid

_jobs = {}
_lock = threading.Lock()


def create(job):
    job_id = uuid.uuid4().hex[:8]
    with _lock:
        job.update({"id": job_id, "created": time.time(), "status": "open", "bids": [], "accepted": None, "verification": None})
        _jobs[job_id] = job
    return job


def get(job_id):
    return _jobs.get(job_id)


def all_jobs(section=None):
    with _lock:
        jobs = list(_jobs.values())
    if section:
        jobs = [j for j in jobs if j["section"] == section]
    return sorted(jobs, key=lambda j: j["created"], reverse=True)


def update(job_id, **fields):
    with _lock:
        _jobs[job_id].update(fields)
        return _jobs[job_id]


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


def seed_demo_job(worker, section_info, worker_info):
    """One clearly labelled sample job inviting this worker, so the worker view is never empty in a demo."""
    with _lock:
        for j in _jobs.values():
            if j.get("demo") and worker["id"] in j.get("invited", []) and j["status"] == "open":
                return j
    trade = worker["trade"]
    summary, cause, card, mats, lo, hi, offer = DEMO_JOBS.get(trade, DEMO_JOBS["bijli"])
    materials = [{"item": i, "qty": q, "unit_pkr": p, "total_pkr": q * p} for i, q, p in mats]
    job = {
        "demo": True,
        "lang": "ur",
        "section": worker["section"],
        "section_info": section_info,
        "trade": trade,
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
        "area": worker["area"],
        "invited": [worker["id"]],
        "customer": "Sample customer",
        "booking": {"address": DEMO_ADDRESSES[len(_jobs) % len(DEMO_ADDRESSES)], "landmark": "near Jamia Masjid", "day": "tomorrow", "slot": "morning", "payment": "cash"},
        "trace": [],
    }
    return create(job)


def add_message(job_id, msg):
    with _lock:
        msg["time"] = time.time()
        _jobs[job_id].setdefault("messages", []).append(msg)
        return msg


def add_bid(job_id, bid):
    with _lock:
        bid["id"] = uuid.uuid4().hex[:6]
        bid["time"] = time.time()
        _jobs[job_id]["bids"].append(bid)
        return bid
