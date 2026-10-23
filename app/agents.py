"""Gemma 4 agents called through the Gemini API.

Every agent goes through call_agent(), which validates JSON with pydantic,
repairs bad JSON once, rotates Gemini API keys on rate limits and retries
server errors before giving up with a clean AgentError.
"""

import json
import os
import re
import threading
import time
from typing import Literal, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

load_dotenv()

MODEL = os.getenv("GEMMA_MODEL", "gemma-4-31b-it")
FALLBACK_MODEL = os.getenv("GEMMA_FALLBACK_MODEL", "gemma-4-26b-a4b-it").strip()


def _load_keys():
    keys = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
    single = os.getenv("GEMINI_API_KEY", "").strip()
    if single and single not in keys:
        keys.insert(0, single)
    return [k for k in keys if k != "paste-your-key-here"]


KEYS = _load_keys()
_clients = {}
_key_index = 0
_lock = threading.Lock()
_cooldown = {}


class AgentError(Exception):
    pass


def _client(i):
    if i not in _clients:
        _clients[i] = genai.Client(api_key=KEYS[i])
    return _clients[i]


# ---------- schemas ----------

SectionId = Literal["bijli", "pani", "deewar", "lakri", "rang", "ac", "gas"]


class RouterOut(BaseModel):
    section: SectionId
    problem_summary: str
    confidence: float = Field(ge=0, le=1)
    clarifying_question: Optional[str] = None


class Material(BaseModel):
    item: str
    qty: float = 1
    est_pkr: float = 0


class Safety(BaseModel):
    level: Literal["none", "caution", "danger"] = "none"
    advice: str = ""


class SpecialistOut(BaseModel):
    diagnosis: str
    likely_cause: str = ""
    materials: list[Material] = []
    labour_hours: float = 1
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    safety: Safety = Safety()
    job_card_urdu: str = ""


class VerifierOut(BaseModel):
    fixed: Literal["yes", "no", "unclear"]
    evidence: str
    confidence: float = Field(ge=0, le=1)


# ---------- core call ----------

def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start : end + 1])


def _generate(model, parts):
    """One Gemma call with key rotation on 429 and retries on 5xx."""
    global _key_index
    if not KEYS:
        raise AgentError("No Gemini API key. Set GEMINI_API_KEY in .env or the host's environment.")
    last = None
    tries = 0
    keys_tried = 0
    while tries < 2 and keys_tried < len(KEYS):
        with _lock:
            i = _key_index
        try:
            resp = _client(i).models.generate_content(
                model=model,
                contents=parts,
                config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=1200),
            )
            if not resp.text:
                raise AgentError("Empty reply from model")
            return resp.text
        except AgentError:
            raise
        except Exception as e:
            last = e
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                with _lock:
                    _key_index = (i + 1) % len(KEYS)
                keys_tried += 1
                continue
            if any(c in msg for c in ("500", "503", "INTERNAL", "UNAVAILABLE", "timed out", "Timeout")):
                tries += 1
                time.sleep(1)
                continue
            raise AgentError(f"Gemma request failed: {msg[:300]}")
    raise AgentError(f"Gemma unavailable after retries: {str(last)[:300]}")


def call_agent(name, system, parts, schema):
    """Run one agent. Returns (validated model, trace step dict)."""
    t0 = time.time()
    prompt = (
        f"{system}\n\nReturn ONLY one JSON object, no prose, matching this JSON schema:\n"
        f"{json.dumps(schema.model_json_schema())}"
    )
    contents = [prompt, *parts]
    models = [MODEL] + ([FALLBACK_MODEL] if FALLBACK_MODEL and FALLBACK_MODEL != MODEL else [])
    # a model that just failed goes to the back of the queue for a minute
    models.sort(key=lambda m: _cooldown.get(m, 0) > time.time())
    errors = []
    for model in models:
        try:
            text = _generate(model, contents)
            try:
                out = schema.model_validate(_extract_json(text))
            except (ValueError, ValidationError) as e:
                fix = contents + [
                    f"Your previous reply was not valid ({str(e)[:200]}). "
                    f"Previous reply:\n{text[:2000]}\n\nReturn ONLY the corrected JSON object."
                ]
                text = _generate(model, fix)
                out = schema.model_validate(_extract_json(text))
            step = {
                "agent": name,
                "model": model,
                "ms": int((time.time() - t0) * 1000),
                "output": out.model_dump(),
            }
            return out, step
        except (AgentError, ValueError, ValidationError) as e:
            _cooldown[model] = time.time() + 60
            errors.append(f"{model}: {e}")
    raise AgentError(f"{name} failed. " + " | ".join(errors))


def image_part(data, mime):
    return types.Part.from_bytes(data=data, mime_type=mime or "image/jpeg")


def health():
    return {"model": MODEL, "fallback_model": FALLBACK_MODEL or None, "keys_loaded": len(KEYS)}
