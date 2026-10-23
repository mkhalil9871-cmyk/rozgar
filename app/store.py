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


def add_bid(job_id, bid):
    with _lock:
        bid["id"] = uuid.uuid4().hex[:6]
        bid["time"] = time.time()
        _jobs[job_id]["bids"].append(bid)
        return bid
