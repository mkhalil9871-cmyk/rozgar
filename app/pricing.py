"""Deterministic tools: skill loading, fair price, safety guard and bid check.

No LLM here, so every rupee on screen can be explained from rates.json.
"""

import json
import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "rozgar-repair-estimator"
RATES = json.loads((SKILL_DIR / "rates.json").read_text(encoding="utf-8"))
SECTIONS = RATES["sections"]
GUIDES = {sid: (SKILL_DIR / "sections" / f"{sid}.md").read_text(encoding="utf-8") for sid in SECTIONS}
SKILL_MD = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

HELPLINES = {
    "gas": "SNGPL 1199",
    "bijli": "FESCO 118",
    "default": "Rescue 1122",
}

DANGER_PATTERNS = {
    "bijli": [r"spark", r"chingari", r"چنگاری", r"burning smell", r"jal raha", r"jalne ki boo", r"smoke", r"dhuan",
              r"دھواں", r"shock", r"current lag", r"کرنٹ", r"pani.*(wire|socket|board)", r"water.*(wire|socket|board)"],
    "deewar": [r"collapse", r"girne wal", r"gir sakt", r"گرنے", r"bulg", r"lean", r"jhuk"],
    "pani": [r"pani.*(wire|socket|board|bijli)", r"water.*(wire|socket|board)"],
}


def material_price(item, est_pkr):
    """Prefer the skill's reference price when the item name matches one."""
    name = item.lower()
    matches = [k for k in RATES["materials"] if k in name or name in k]
    if not matches:
        return est_pkr
    # "tap washer" must beat "tap": take the most specific key
    return RATES["materials"][max(matches, key=len)]


def fair_range(section, materials, labour_hours):
    rate = SECTIONS[section]
    mat_rows = []
    mat_total = 0
    for m in materials:
        unit = material_price(m.item, m.est_pkr)
        line = unit * m.qty
        mat_total += line
        mat_rows.append({"item": m.item, "qty": m.qty, "unit_pkr": unit, "total_pkr": line})
    labour = labour_hours * rate["labour_per_hour"]
    base = mat_total + labour + rate["visit_fee"]
    return {
        "materials": mat_rows,
        "materials_total": round(mat_total),
        "labour_hours": labour_hours,
        "labour_per_hour": rate["labour_per_hour"],
        "labour_total": round(labour),
        "visit_fee": rate["visit_fee"],
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
        reasons.append("Any gas job is treated as dangerous until checked.")
    hit = any(re.search(p, text) for p in DANGER_PATTERNS.get(section, []))
    if hit and level != "danger":
        level = "danger"
        reasons.append("Description mentions a danger sign (sparking, burning, shock, water on wiring or collapse).")
    helpline = HELPLINES.get(section, HELPLINES["default"])
    return {
        "level": level,
        "advice": model_advice,
        "rule_reasons": reasons,
        "helplines": [helpline, HELPLINES["default"]] if helpline != HELPLINES["default"] else [helpline],
    }


def bid_check(amount, fair_min, fair_max):
    if amount > fair_max * 1.25:
        return {"status": "high", "label": "⚠️ Above fair price", "detail": f"More than 25% above the fair maximum of Rs {fair_max:,}."}
    if amount < fair_min * 0.75:
        return {"status": "low", "label": "⚠️ Unfair to worker", "detail": f"More than 25% below the fair minimum of Rs {fair_min:,}."}
    return {"status": "fair", "label": "✅ Fair price", "detail": f"Within the fair range Rs {fair_min:,} – {fair_max:,}."}
