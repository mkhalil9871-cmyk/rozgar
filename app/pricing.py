"""Deterministic tools: skill loading, cost estimate, safety guard and price check.

No LLM here, so every rupee on screen can be explained from rates.json.
"""

import json
import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "rozgar-repair-estimator"


def _load(name):
    return json.loads((SKILL_DIR / name).read_text(encoding="utf-8"))


RATES = _load("rates.json")
SECTIONS = RATES["sections"]
TRADES = RATES["trades"]
CATALOG = _load("catalog.json")["sections"]
WORKERS = _load("workers.json")["workers"]
GUIDES = {f.stem: f.read_text(encoding="utf-8") for f in (SKILL_DIR / "sections").glob("*.md")}
SKILL_MD = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

HELPLINES = {
    "gas": "SNGPL 1199",
    "bijli": "FESCO 118",
    "default": "Rescue 1122",
}

DANGER_PATTERNS = {
    "bijli": [r"spark", r"chingari", r"چنگاری", r"burning smell", r"jal raha", r"jalne ki boo", r"smoke", r"dhuan",
              r"دھواں", r"shock", r"current lag", r"کرنٹ", r"pani.*(wire|socket|board)", r"water.*(wire|socket|board)"],
    "deewar": [r"collapse", r"girne wal", r"gir sakt", r"گرنے", r"bulg", r"lean", r"jhuk", r"depth=through"],
    "pani": [r"pani.*(wire|socket|board|bijli)", r"water.*(wire|socket|board)"],
}


def trade_info(trade):
    """Worker type, rates and icon for a trade (bijli, ac, appliance, or a section id)."""
    return TRADES.get(trade) or SECTIONS[trade]


def trade_of(section, sub_id):
    sub = find_sub(section, sub_id)
    return (sub or {}).get("trade") or section


def guide_for(section, trade, sub_known):
    """The specialist guide; the merged electrical section gets all three guides when the problem type is unknown."""
    if section == "bijli" and not sub_known:
        return "\n\n".join(GUIDES[g] for g in ("bijli", "ac", "appliance"))
    return GUIDES[trade]


def find_sub(section, sub_id):
    return next((s for s in CATALOG.get(section, []) if s["id"] == sub_id), None)


def answers_mult(section, sub_id, answers):
    """Largest multiplier among the chosen guided answers (size, depth, count...)."""
    sub = find_sub(section, sub_id)
    if not sub or not answers:
        return 1.0
    mult = 1.0
    for q in sub["questions"]:
        chosen = next((o for o in q["options"] if o["v"] == answers.get(q["id"])), None)
        if chosen:
            mult = max(mult, chosen.get("mult", 1.0))
    return mult


def material_price(item, est_pkr):
    """Prefer the skill's reference price when the item name matches one."""
    name = item.lower()
    matches = [k for k in RATES["materials"] if k in name or name in k]
    if not matches:
        return est_pkr
    # "tap washer" must beat "tap": take the most specific key
    return RATES["materials"][max(matches, key=len)]


def estimate(trade, sub, qty, mult, materials, worker_brings, labour_hours=1.0):
    """Price the way workers quote: quantity × per-unit labour rate (with a minimum charge).

    Materials are only added when the worker brings them; otherwise they are a shopping list.
    Falls back to Gemma's hours × hourly rate only when the problem type is unknown.
    """
    rate = trade_info(trade)
    mat_rows = []
    mat_total = 0
    for m in materials:
        unit_price = material_price(m.item, m.est_pkr)
        line = unit_price * m.qty
        mat_total += line
        mat_rows.append({"item": m.item, "qty": m.qty, "unit_pkr": unit_price, "total_pkr": line})
    unit = (sub or {}).get("unit")
    if unit:
        qty = qty if qty and qty > 0 else unit["default"]
        labour = max(qty * unit["rate"] * mult, unit["min"])
        visit = 0
    else:
        qty = None
        labour = labour_hours * rate["labour_per_hour"]
        visit = rate["visit_fee"]
    base = labour + visit + (mat_total if worker_brings else 0)
    return {
        "materials": mat_rows,
        "materials_total": round(mat_total),
        "materials_included": bool(worker_brings),
        "qty": qty,
        "unit": unit,
        "difficulty": mult,
        "labour_hours": labour_hours,
        "labour_per_hour": rate["labour_per_hour"],
        "labour_total": round(labour),
        "visit_fee": visit,
        "base": round(base),
        "fair_min": int(round(base * 0.85, -1)),
        "fair_max": int(round(base * 1.15, -1)),
    }


def fair_range(trade, materials, labour_hours, mult=1.0, remote=False):
    rate = trade_info(trade)
    mat_rows = []
    mat_total = 0
    for m in materials:
        unit = material_price(m.item, m.est_pkr)
        line = unit * m.qty
        mat_total += line
        mat_rows.append({"item": m.item, "qty": m.qty, "unit_pkr": unit, "total_pkr": line})
    hours = round(labour_hours * mult, 1)
    labour = hours * rate["labour_per_hour"]
    visit = 0 if remote else rate["visit_fee"]
    base = mat_total + labour + visit
    return {
        "materials": mat_rows,
        "materials_total": round(mat_total),
        "labour_hours": hours,
        "size_multiplier": mult,
        "labour_per_hour": rate["labour_per_hour"],
        "labour_total": round(labour),
        "visit_fee": visit,
        "base": round(base),
        "fair_min": int(round(base * 0.8, -1)),
        "fair_max": int(round(base * 1.2, -1)),
    }


def safety_guard(section, description, model_level, model_advice):
    """Combine the specialist's safety flag with hard rules."""
    text = (description or "").lower()
    level = model_level
    reasons = []
    if section == "gas":
        level = "danger"
        reasons.append("gas")
    hit = any(re.search(p, text) for p in DANGER_PATTERNS.get(section, []))
    if hit and level != "danger":
        level = "danger"
        reasons.append("keyword")
    helpline = HELPLINES.get(section, HELPLINES["default"])
    return {
        "level": level,
        "advice": model_advice,
        "rule_reasons": reasons,
        "helplines": [helpline, HELPLINES["default"]] if helpline != HELPLINES["default"] else [helpline],
    }


def bid_check(amount, fair_min, fair_max):
    """Language-neutral status; the UI translates it."""
    if amount > fair_max * 1.25:
        return {"status": "high"}
    if amount < fair_min * 0.75:
        return {"status": "low"}
    return {"status": "fair"}


def workers_for(section, area="", trade=""):
    ws = [w for w in WORKERS if w["section"] == section]
    return sorted(ws, key=lambda w: (bool(trade) and w["trade"] != trade, w["area"] != area, -w["rating"]))


def worker(worker_id):
    return next((w for w in WORKERS if w["id"] == worker_id), None)
