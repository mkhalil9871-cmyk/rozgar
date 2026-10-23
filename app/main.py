import base64
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
from .pricing import SECTIONS, SKILL_MD, bid_check

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_IMAGE = 8 * 1024 * 1024

app = FastAPI(title="Rozgar", description="InDrive for mistris, powered by Gemma 4")
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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def api_health():
    return health()


@app.get("/api/sections")
def sections():
    return SECTIONS


@app.get("/api/skill", response_class=JSONResponse)
def skill():
    return {"skill_md": SKILL_MD}


@app.post("/api/diagnose")
async def diagnose(
    description: str = Form(""),
    section: str = Form(""),
    answer: str = Form(""),
    photo: Optional[UploadFile] = File(None),
):
    image, mime = await _read_image(photo)
    if not image and not description.strip():
        raise HTTPException(400, "Add a photo or a description")
    result = await run_in_threadpool(diagnose_pipeline, description, image, mime, section or None, answer or None)
    if image:
        result["photo"] = {"mime": mime, "b64": base64.b64encode(image).decode()}
    return result


class JobIn(BaseModel):
    diagnosis: dict
    offer: int
    area: str = ""
    customer: str = "Customer"


@app.post("/api/jobs")
def create_job(body: JobIn):
    d = body.diagnosis
    if d.get("status") != "ok":
        raise HTTPException(400, "Diagnose the problem first")
    price = d["price"]
    job = store.create({
        "section": d["section"],
        "section_info": d["section_info"],
        "problem_summary": d["problem_summary"],
        "diagnosis": d["diagnosis"],
        "job_card_urdu": d["job_card_urdu"],
        "price": price,
        "safety": d["safety"],
        "photo": d.get("photo"),
        "offer": body.offer,
        "offer_check": bid_check(body.offer, price["fair_min"], price["fair_max"]),
        "area": body.area,
        "customer": body.customer,
        "trace": d.get("trace", []),
    })
    return job


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
    worker: str
    amount: int
    note: str = ""


@app.post("/api/jobs/{job_id}/bids")
def add_bid(job_id: str, body: BidIn):
    job = get_job(job_id)
    if job["status"] != "open":
        raise HTTPException(400, "This job is no longer open")
    check = bid_check(body.amount, job["price"]["fair_min"], job["price"]["fair_max"])
    return store.add_bid(job_id, {"worker": body.worker, "amount": body.amount, "note": body.note, "check": check})


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
    result, trace = await run_in_threadpool(verify_pipeline, before, before_mime, after, after_mime, job["diagnosis"])
    status = "verified" if result["fixed"] == "yes" else "in_progress"
    return store.update(
        job_id,
        status=status,
        verification={**result, "after_photo": {"mime": after_mime, "b64": base64.b64encode(after).decode()}},
        trace=job["trace"] + trace,
    )
