import base64
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .agents import AgentError, health
from .orchestrator import diagnose_pipeline, verify_pipeline
from .pricing import CATALOG, SECTIONS, SKILL_MD, TRADES, bid_check, find_sub
from .store import worker, workers_for

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_IMAGE = 8 * 1024 * 1024
# a sensible upper bound per unit, to catch typing mistakes like 5000 ft for a crack
QTY_LIMITS = {"ft": 300, "sqft": 5000, "count": 50, "job": 5}

app = FastAPI(title="Rozgar", description="Fair home-repair prices, powered by Gemma 4")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(AgentError)
async def agent_error(_, exc):
    return JSONResponse(status_code=503, content={"error": str(exc)})


async def _read_image(f: Optional[UploadFile]):
    if f is None or not f.filename:
        return None, None
    data = await f.read()
    if len(data) > MAX_IMAGE:
        raise HTTPException(413, "Image is larger than 8 MB")
    return data, f.content_type


def _json_field(raw, default):
    try:
        return json.loads(raw) if raw else default
    except ValueError:
        return default


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def api_health():
    return health()


@app.get("/api/sections")
def sections():
    return SECTIONS


@app.get("/api/trades")
def trades():
    return TRADES


@app.get("/api/catalog")
def catalog():
    return CATALOG


@app.get("/api/workers")
def list_workers(section: str = "", area: str = "", trade: str = ""):
    return workers_for(section, area, trade) if section else store.all_workers()


class WorkerIn(BaseModel):
    name: str
    phone: str
    trade: str
    area: str
    years: int = 1
    cnic: str = ""


@app.post("/api/workers")
def join_worker(body: WorkerIn):
    if body.trade not in TRADES and body.trade not in SECTIONS:
        raise HTTPException(400, "Unknown trade")
    if not body.name.strip():
        raise HTTPException(400, "Enter your name")
    new = {
        "id": store.new_worker_id(),
        "name": {"en": body.name.strip(), "ur": body.name.strip(), "roman": body.name.strip()},
        "section": "bijli" if body.trade in ("ac", "appliance") else body.trade,
        "trade": body.trade,
        "area": body.area,
        "rating": 0,
        "jobs_done": 0,
        "years": max(0, body.years),
        "verified": False,
        "new": True,
        "past_projects": [],
    }
    # phone and CNIC are only format-checked by the app, never stored
    return store.add_worker(new)


@app.post("/api/workers/{worker_id}/demo-job")
def demo_job(worker_id: str):
    w = worker(worker_id)
    if not w:
        raise HTTPException(404, "Worker not found")
    sec = SECTIONS[w["section"]]
    wi = TRADES.get(w["trade"]) or sec
    return public(store.seed_demo_job(w, sec, wi))


@app.get("/api/areas")
def areas():
    return store.areas()


@app.get("/api/skill", response_class=JSONResponse)
def skill():
    return {"skill_md": SKILL_MD}


@app.post("/api/diagnose")
async def diagnose(
    description: str = Form(""),
    section: str = Form(""),
    subcategory: str = Form(""),
    answers: str = Form(""),
    symptoms: str = Form(""),
    lang: str = Form("en"),
    answer: str = Form(""),
    qty: float = Form(0),
    worker_brings: bool = Form(False),
    photo: Optional[UploadFile] = File(None),
):
    image, mime = await _read_image(photo)
    ans = _json_field(answers, {})
    syms = _json_field(symptoms, [])
    if not image and not description.strip() and not subcategory:
        raise HTTPException(400, "Add a photo, pick a problem or describe it")
    sub = find_sub(section, subcategory) if subcategory else None
    if sub:
        # ask before estimating: the amount of work and every guided question are required
        if qty <= 0:
            raise HTTPException(400, "Enter how much work there is")
        limit = QTY_LIMITS.get(sub.get("unit", {}).get("type"), 1000)
        if qty > limit:
            raise HTTPException(400, f"That amount looks too large (maximum {limit}). Please check the number.")
        missing = [q["id"] for q in sub["questions"] if q["id"] not in ans]
        if missing:
            raise HTTPException(400, f"Answer all questions first: {', '.join(missing)}")
    result = await run_in_threadpool(
        diagnose_pipeline, description, image, mime, section or None, answer or None,
        subcategory or None, ans, syms, lang, qty or None, worker_brings,
    )
    if image:
        result["photo"] = {"mime": mime, "b64": base64.b64encode(image).decode()}
    return result


class JobIn(BaseModel):
    diagnosis: dict
    offer: int
    area: str = ""
    invited: list[str] = []
    customer: str = ""
    booking: dict = {}


@app.post("/api/jobs")
def create_job(body: JobIn):
    d = body.diagnosis
    if d.get("status") != "ok":
        raise HTTPException(400, "Diagnose the problem first")
    if body.offer <= 0:
        raise HTTPException(400, "Enter your price")
    price = d["price"]
    job = store.create({
        "lang": d.get("lang", "en"),
        "section": d["section"],
        "section_info": d["section_info"],
        "trade": d.get("trade", d["section"]),
        "worker_info": d.get("worker_info", d["section_info"]),
        "subcategory": d.get("subcategory"),
        "answers": d.get("answers", {}),
        "symptoms": d.get("symptoms", []),
        "problem_summary": d["problem_summary"],
        "diagnosis": d["diagnosis"],
        "likely_cause": d.get("likely_cause", ""),
        "job_card_urdu": d["job_card_urdu"],
        "price": price,
        "safety": d["safety"],
        "photo": d.get("photo"),
        "offer": body.offer,
        "offer_check": bid_check(body.offer, price["fair_min"], price["fair_max"]),
        "area": body.area,
        "invited": body.invited,
        "customer": body.customer[:60],
        "booking": {k: str(v)[:120] for k, v in body.booking.items()},
        "trace": d.get("trace", []),
    })
    store.schedule_sample_offers(job["id"])
    return public(job)


def _job(job_id):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def public(job):
    """What the browser gets: photos as small URLs instead of base64, and distance on every offer."""
    job = dict(job)
    if job.get("photo"):
        job["photo"] = {"url": f"/api/jobs/{job['id']}/photo/before"}
    if job.get("verification") and job["verification"].get("after_photo"):
        job["verification"] = {**job["verification"], "after_photo": {"url": f"/api/jobs/{job['id']}/photo/after"}}
    for b in job.get("bids", []):
        if b.get("worker"):
            b["km"] = store.km_between(job.get("area") or "", b["worker"]["area"])
    return job


@app.get("/api/jobs")
def list_jobs(section: str = ""):
    return [public(j) for j in store.all_jobs(section or None)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return public(_job(job_id))


@app.get("/api/jobs/{job_id}/photo/{which}")
def job_photo(job_id: str, which: str):
    job = _job(job_id)
    photo = job.get("photo") if which == "before" else (job.get("verification") or {}).get("after_photo")
    if not photo:
        raise HTTPException(404, "No photo")
    return Response(base64.b64decode(photo["b64"]), media_type=photo["mime"] or "image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


class BidIn(BaseModel):
    worker_id: str
    amount: int
    note: str = ""


@app.post("/api/jobs/{job_id}/bids")
def add_bid(job_id: str, body: BidIn):
    job = _job(job_id)
    w = worker(body.worker_id)
    if not w:
        raise HTTPException(404, "Worker not found")
    if job["status"] != "open":
        raise HTTPException(400, "This job is no longer open")
    check = bid_check(body.amount, job["price"]["fair_min"], job["price"]["fair_max"])
    store.add_bid(job_id, {"worker_id": w["id"], "worker": w, "amount": body.amount, "note": body.note, "check": check})
    return get_job(job_id)


class MessageIn(BaseModel):
    worker_id: str
    sender: str  # "customer" or "worker"
    text: str


DEMO_REPLY = {
    "en": "Thanks! Please come tomorrow morning around 10. The address is in the job details.",
    "ur": "شکریہ! کل صبح 10 بجے کے قریب آ جائیں۔ پتہ کام کی تفصیل میں ہے۔",
}


@app.post("/api/jobs/{job_id}/messages")
def send_message(job_id: str, body: MessageIn):
    """One chat thread per job and worker, visible to that worker and the customer."""
    job = _job(job_id)
    text = body.text.strip()[:500]
    if not text or body.sender not in ("customer", "worker") or not worker(body.worker_id):
        raise HTTPException(400, "Empty message")
    store.add_message(job_id, {"worker_id": body.worker_id, "sender": body.sender, "text": text})
    if job.get("demo") and body.sender == "worker":
        # the sample customer answers so the chat can be shown in a demo
        store.add_message(job_id, {"worker_id": body.worker_id, "sender": "customer", "text": DEMO_REPLY["ur"]})
    return get_job(job_id)


class AcceptIn(BaseModel):
    bid_id: str


@app.post("/api/jobs/{job_id}/accept")
def accept(job_id: str, body: AcceptIn):
    job = _job(job_id)
    bid = next((b for b in job["bids"] if b["id"] == body.bid_id), None)
    if not bid:
        raise HTTPException(404, "Bid not found")
    if job["status"] != "open":
        raise HTTPException(400, "This job already has a worker")
    return public(store.update(job_id, status="in_progress", accepted={k: v for k, v in bid.items() if k != "worker"}))


@app.post("/api/jobs/{job_id}/verify")
async def verify(job_id: str, photo: UploadFile = File(...)):
    job = _job(job_id)
    after, after_mime = await _read_image(photo)
    if not after:
        raise HTTPException(400, "Upload the after photo")
    before = before_mime = None
    if job.get("photo"):
        before = base64.b64decode(job["photo"]["b64"])
        before_mime = job["photo"]["mime"]
    result, trace = await run_in_threadpool(
        verify_pipeline, before, before_mime, after, after_mime, job["diagnosis"], job.get("lang", "en")
    )
    after_photo = {"mime": after_mime, "b64": base64.b64encode(after).decode()}
    status = "verified" if result["fixed"] == "yes" else "in_progress"
    if status == "verified" and job.get("accepted"):
        # a Gemma-verified job becomes part of the worker's public portfolio
        store.add_project(job["accepted"]["worker_id"], {
            "title": {"en": job["problem_summary"], "ur": job["problem_summary"], "roman": job["problem_summary"]},
            "area": job.get("area") or "Faisalabad",
            "price": job["accepted"]["amount"],
            "rating": 5,
            "verified_by_gemma": True,
            "after_photo": after_photo,
        })
    return public(store.update(
        job_id,
        status=status,
        verification={**result, "after_photo": after_photo},
        trace=job["trace"] + trace,
    ))
