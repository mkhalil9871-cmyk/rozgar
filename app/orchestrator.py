"""Fixed multi-agent pipeline: Router -> Specialist -> Pricing -> Safety, and Verifier.

Gemma 4 agents do the perception and reasoning; Python tools do the money
and the safety rules. Every step is recorded in a trace for the UI.
"""

import json
import time

from .agents import RouterOut, SpecialistOut, VerifierOut, call_agent, image_part
from .pricing import SECTIONS, answers_mult, fair_range, find_sub, guide_for, safety_guard, trade_info, trade_of

LANG_RULE = {
    "en": "Write every text field in simple, plain English.",
    "ur": "Write every text field in Urdu, using Urdu script (اردو). Do not use English words unless there is no Urdu word.",
    "roman": "Write every text field in Roman Urdu: Urdu language written in English letters, like 'Pipe ka joint dheela hai'.",
}

ROUTER_SYSTEM = """You are the Router agent of Rozgar, a home-repair and home-services marketplace in Pakistan.
Look at the photo (if any) and the customer's description (Urdu, Roman Urdu or English).
Choose exactly ONE section id for the worker who should do the job:
{sections}
Rules: anything with wires, switches, sockets, breakers, fans, lights, UPS or solar wiring is "bijli".
ACs, washing machines, fridges, water pumps/motors, microwaves and other home machines are also "bijli".
Broken or cracked walls, plaster, holes, seepage/damp, tiles, floors and roof leaks are "deewar".
Gas smell, gas pipes, stoves and gas heaters are "gas".
Computers, laptops, Windows, printers, Wi-Fi and software are "computer".
problem_summary: one short sentence. confidence: 0 to 1. If the input is too unclear to choose, set
confidence below 0.5 and write one short clarifying_question; otherwise clarifying_question is null.
{lang}"""

SPECIALIST_SYSTEM = """You are the {worker} specialist agent of Rozgar in Faisalabad, Pakistan.
Diagnose the job from the customer's details{photo_note}, using this guide:

{guide}

Give: diagnosis and likely_cause (short and plain), the materials needed (item, qty, est_pkr per unit in
Pakistani Rupees; keep item names in English using the guide's names where possible), realistic
labour_hours for a SMALL/basic version of the job (size and quantity are applied separately),
difficulty, safety (level none/caution/danger with one line of advice), and job_card_urdu: 2-3 short
lines telling the worker what to do and what to bring. Do not give a total price.
{lang}"""

VERIFIER_SYSTEM = """You are the Verifier agent of Rozgar. The first image is BEFORE the work, the second is AFTER.
The job was: {diagnosis}
Decide if the AFTER photo shows the problem fixed. If the photos look the same or the after photo shows a
different place or object, answer "no" or "unclear". evidence: one or two short sentences on what you see.
{lang}"""


def _section_list():
    return "\n".join(f'- "{sid}": {s["name"]["en"]} -> {s["worker"]["en"]}' for sid, s in SECTIONS.items())


def _details(section, sub_id, answers, symptoms):
    """Turn the guided answers into plain English context for the agents."""
    sub = find_sub(section, sub_id)
    if not sub:
        return ""
    lines = [f"Problem type: {sub['label']['en']}"]
    for q in sub["questions"]:
        chosen = next((o for o in q["options"] if o["v"] == (answers or {}).get(q["id"])), None)
        if chosen:
            lines.append(f"{q['label']['en']} {chosen['label']['en']}")
    picked = [s["label"]["en"] for s in sub["symptoms"] if s["v"] in (symptoms or [])]
    if picked:
        lines.append("Symptoms: " + ", ".join(picked))
    return "\n".join(lines)


def diagnose_pipeline(description, image=None, mime=None, section_hint=None, answer=None,
                      subcategory=None, answers=None, symptoms=None, lang="en"):
    lang = lang if lang in LANG_RULE else "en"
    trace = []
    parts = []
    if image:
        parts.append(image_part(image, mime))
    details = _details(section_hint, subcategory, answers, symptoms) if section_hint in SECTIONS else ""
    text = f"Customer description: {description or '(none)'}"
    if details:
        text += f"\n{details}"
    if answer:
        text += f"\nCustomer's answer to your question: {answer}"
    if section_hint in SECTIONS:
        text += f'\nThe customer opened the "{section_hint}" section, so prefer it unless clearly wrong.'
    parts.append(text)

    if section_hint in SECTIONS and not answer:
        # customer already chose the section: skip one Gemma call
        summary = (details.splitlines()[0] if details else description or "")[:140]
        router = RouterOut(section=section_hint, problem_summary=summary, confidence=1.0)
        trace.append({"agent": "Router (customer picked section)", "model": "python", "ms": 0, "output": router.model_dump()})
    else:
        router, step = call_agent("Router", ROUTER_SYSTEM.format(sections=_section_list(), lang=LANG_RULE[lang]), parts, RouterOut)
        trace.append(step)
        if router.confidence < 0.5 and router.clarifying_question and not answer:
            return {"status": "clarify", "question": router.clarifying_question, "router": router.model_dump(), "trace": trace}

    sec = SECTIONS[router.section]
    sub_known = router.section == section_hint and find_sub(router.section, subcategory) is not None
    trade = trade_of(router.section, subcategory) if sub_known else router.section
    worker = trade_info(trade)["worker"] if sub_known or router.section != "bijli" else sec["worker"]
    if image:
        photo_note = " and the photo (it may be a photo of the problem or of the machine's model sticker)"
    else:
        photo_note = " (there is NO photo: the problem cannot be photographed, so diagnose from the symptoms and description)"
    spec, step = call_agent(
        f"{worker['en']} specialist",
        SPECIALIST_SYSTEM.format(worker=worker["en"], guide=guide_for(router.section, trade, sub_known), photo_note=photo_note, lang=LANG_RULE[lang]),
        parts,
        SpecialistOut,
    )
    trace.append(step)

    t0 = time.time()
    mult = answers_mult(router.section, subcategory, answers)
    remote = (answers or {}).get("visit") == "remote"
    price = fair_range(trade, spec.materials, spec.labour_hours, mult, remote)
    trace.append({"agent": "Pricing tool (rates.json + size answers)", "model": "python", "ms": int((time.time() - t0) * 1000), "output": price})

    t0 = time.time()
    flags = " ".join(f"{k}={v}" for k, v in (answers or {}).items()) + " " + " ".join(symptoms or [])
    safety = safety_guard(router.section, f"{description} {answer or ''} {flags}", spec.safety.level, spec.safety.advice)
    trace.append({"agent": "Safety guard", "model": "python", "ms": int((time.time() - t0) * 1000), "output": safety})

    return {
        "status": "ok",
        "lang": lang,
        "section": router.section,
        "section_info": sec,
        "trade": trade,
        "worker_info": {**trade_info(trade), "worker": worker},
        "subcategory": subcategory,
        "answers": answers or {},
        "symptoms": symptoms or [],
        "problem_summary": router.problem_summary,
        "confidence": router.confidence,
        "diagnosis": spec.diagnosis,
        "likely_cause": spec.likely_cause,
        "difficulty": spec.difficulty,
        "job_card_urdu": spec.job_card_urdu,
        "price": price,
        "safety": safety,
        "trace": trace,
    }


def verify_pipeline(before, before_mime, after, after_mime, diagnosis, lang="en"):
    if before is None:
        parts = ["(No before photo was provided.)", image_part(after, after_mime)]
    else:
        parts = [image_part(before, before_mime), image_part(after, after_mime)]
    system = VERIFIER_SYSTEM.format(diagnosis=diagnosis, lang=LANG_RULE.get(lang, LANG_RULE["en"]))
    out, step = call_agent("Verifier", system, parts, VerifierOut)
    if before is not None and before == after:
        out.fixed = "no"
        step["output"] = out.model_dump()
        step["rule"] = "identical-image check"
    return out.model_dump(), [step]


if __name__ == "__main__":
    print(json.dumps(diagnose_pipeline("", section_hint="appliance", subcategory="washing", symptoms=["no_spin", "noise"], lang="roman"), indent=2, ensure_ascii=False))
