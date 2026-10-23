import base64
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .agents import AgentError, health
from .orchestrator import diagnose_pipeline, verify_pipeline
from .pricing import CATALOG, SECTIONS, SKILL_MD, TRADES, WORKERS, bid_check, find_sub, worker, workers_for

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_IMAGE = 8 * 1024 * 1024

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
    return workers_for(section, area, trade) if section else WORKERS


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
        "id": f"n{len(WORKERS) + 1:03d}",
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
    WORKERS.append(new)  # in memory, like jobs; phone and CNIC are only format-checked, never stored
    return new


@app.post("/api/workers/{worker_id}/demo-job")
def demo_job(worker_id: str):
    w = worker(worker_id)
    if not w:
        raise HTTPException(404, "Worker not found")
    sec = SECTIONS[w["section"]]
    wi = TRADES.get(w["trade"]) or sec
    return store.seed_demo_job(w, sec, wi)


@app.get("/api/areas")
def areas():
    return sorted({w["area"] for w in WORKERS})


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
    return store.create({
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


@app.get("/api/jobs")
def list_jobs(section: str = ""):
    return store.all_jobs(section or None)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


class BidIn(BaseModel):
    worker_id: str
    amount: int
    note: str = ""


@app.post("/api/jobs/{job_id}/bids")
def add_bid(job_id: str, body: BidIn):
    job = get_job(job_id)
    w = worker(body.worker_id)
    if not w:
        raise HTTPException(404, "Worker not found")
    if job["status"] != "open":
        raise HTTPException(400, "This job is no longer open")
    check = bid_check(body.amount, job["price"]["fair_min"], job["price"]["fair_max"])
    return store.add_bid(job_id, {"worker_id": w["id"], "worker": w, "amount": body.amount, "note": body.note, "check": check})


class AcceptIn(BaseModel):
    bid_id: str


@app.post("/api/jobs/{job_id}/accept")
def accept(job_id: str, body: AcceptIn):
    job = get_job(job_id)
    bid = next((b for b in job["bids"] if b["id"] == body.bid_id), None)
    if not bid:
        raise HTTPException(404, "Bid not found")
    return store.update(job_id, status="in_progress", accepted=bid)


@app.post("/api/jobs/{job_id}/verify")
async def verify(job_id: str, photo: UploadFile = File(...)):
    job = get_job(job_id)
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
        w = worker(job["accepted"]["worker_id"])
        if w:
            # a Gemma-verified job becomes part of the worker's public portfolio
            w["jobs_done"] += 1
            w["past_projects"].insert(0, {
                "title": {"en": job["problem_summary"], "ur": job["problem_summary"], "roman": job["problem_summary"]},
                "area": job.get("area") or "Faisalabad",
                "price": job["accepted"]["amount"],
                "rating": 5,
                "verified_by_gemma": True,
                "after_photo": after_photo,
            })
    return store.update(
        job_id,
        status=status,
        verification={**result, "after_photo": after_photo},
        trace=job["trace"] + trace,
    )
