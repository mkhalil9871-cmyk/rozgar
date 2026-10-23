"""Fixed multi-agent pipeline: Router -> Specialist -> Pricing -> Safety, and Verifier.

Gemma 4 agents do the perception and reasoning; Python tools do the money
and the safety rules. Every step is recorded in a trace for the UI.
"""

import json
import time

from .agents import RouterOut, SpecialistOut, VerifierOut, call_agent, image_part
from .pricing import GUIDES, SECTIONS, fair_range, safety_guard

ROUTER_SYSTEM = """You are the Router agent of Rozgar, a home-repair marketplace in Pakistan.
Look at the photo (if any) and the customer's description (Urdu, Roman Urdu or English).
Choose exactly ONE section id for the worker who should do the job:
{sections}
Rules: anything with wires, switches, sockets, breakers, fans, lights, UPS or solar wiring is "bijli".
Broken or cracked walls, plaster, holes, seepage/damp, tiles, floors and roof leaks are "deewar".
Gas smell, gas pipes, stoves and gas heaters are "gas".
problem_summary: one short English sentence.
confidence: 0 to 1. If the photo and text are too unclear to choose, set confidence below 0.5 and
write one short clarifying_question in the customer's language; otherwise clarifying_question is null."""

SPECIALIST_SYSTEM = """You are the {worker} specialist agent of Rozgar in Faisalabad, Pakistan.
Diagnose the repair from the photo and description using this guide:

{guide}

Give: diagnosis and likely_cause (short, plain English), the materials needed (item, qty, est_pkr per unit
in Pakistani Rupees, use names from the guide where possible), realistic labour_hours, difficulty,
safety (level none/caution/danger with one line of advice), and job_card_urdu: 2-3 short lines in Urdu
script telling the worker what to do and what to bring. Do not give a total price."""

VERIFIER_SYSTEM = """You are the Verifier agent of Rozgar. The first image is BEFORE the repair, the second is AFTER.
The job was: {diagnosis}
Decide if the AFTER photo shows the problem fixed. If the photos look the same or the after photo shows a
different place or object, answer "no" or "unclear". evidence: one or two short sentences on what you see."""


def _section_list():
    return "\n".join(f'- "{sid}": {s["emoji"]} {s["name"]} -> {s["worker"]}' for sid, s in SECTIONS.items())


def diagnose_pipeline(description, image=None, mime=None, section_hint=None, answer=None):
    trace = []
    parts = []
    if image:
        parts.append(image_part(image, mime))
    text = f"Customer description: {description or '(none, use the photo)'}"
    if answer:
        text += f"\nCustomer's answer to your question: {answer}"
    if section_hint in SECTIONS:
        text += f'\nThe customer opened the "{section_hint}" section, so prefer it unless clearly wrong.'
    parts.append(text)

    if section_hint in SECTIONS and not answer:
        # customer already chose the section tile: skip one Gemma call
        router = RouterOut(section=section_hint, problem_summary=(description or "Photo only")[:120], confidence=1.0)
        trace.append({"agent": "Router (customer picked section)", "model": "python", "ms": 0, "output": router.model_dump()})
    else:
        router, step = call_agent("Router", ROUTER_SYSTEM.format(sections=_section_list()), parts, RouterOut)
        trace.append(step)

    if router.confidence < 0.5 and router.clarifying_question and not answer:
        return {"status": "clarify", "question": router.clarifying_question, "router": router.model_dump(), "trace": trace}

    sec = SECTIONS[router.section]
    spec, step = call_agent(
        f"{sec['worker']} specialist",
        SPECIALIST_SYSTEM.format(worker=sec["worker"], guide=GUIDES[router.section]),
        parts,
        SpecialistOut,
    )
    trace.append(step)

    t0 = time.time()
    price = fair_range(router.section, spec.materials, spec.labour_hours)
    trace.append({"agent": "Pricing tool (rates.json)", "model": "python", "ms": int((time.time() - t0) * 1000), "output": price})

    t0 = time.time()
    safety = safety_guard(router.section, f"{description} {answer or ''}", spec.safety.level, spec.safety.advice)
    trace.append({"agent": "Safety guard", "model": "python", "ms": int((time.time() - t0) * 1000), "output": safety})

    return {
        "status": "ok",
        "section": router.section,
        "section_info": sec,
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


def verify_pipeline(before, before_mime, after, after_mime, diagnosis):
    if before is None:
        parts = ["(No before photo was provided.)", image_part(after, after_mime)]
    else:
        parts = [image_part(before, before_mime), image_part(after, after_mime)]
    out, step = call_agent("Verifier", VERIFIER_SYSTEM.format(diagnosis=diagnosis), parts, VerifierOut)
    if before is not None and before == after:
        out.fixed = "no"
        out.evidence = "The after photo is identical to the before photo. " + out.evidence
        step["output"] = out.model_dump()
        step["rule"] = "identical-image check"
    return out.model_dump(), [step]


if __name__ == "__main__":
    print(json.dumps(diagnose_pipeline("Bathroom ka nalka tapak raha hai raat bhar"), indent=2, ensure_ascii=False))
