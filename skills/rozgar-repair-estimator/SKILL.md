---
name: rozgar-repair-estimator
description: Diagnose a home-repair problem from a photo and description, route it to the right trade section (electrician, plumber, mason, carpenter, painter, AC technician, gas fitter), estimate a fair price in Pakistani Rupees, flag dangerous jobs, check bids against the fair range, and verify a finished job from before/after photos. Use when someone in Pakistan needs to know who to call for a repair, what it should cost, or whether a mistri's quote or finished work is fair.
license: MIT
metadata:
  project: Rozgar
  model: gemma-4-31b-it
---

# Rozgar repair estimator

This skill turns "something is broken at home" into a fair, checkable repair job.
It is the same logic the Rozgar app runs; the app loads these files at startup.

## Files

- [rates.json](rates.json): labour rate per hour, visit fee and common material prices per section (Faisalabad, 2026, rough estimates).
- [sections/](sections/): one guide per trade section with common faults, typical materials and safety rules. Read only the guide for the section you route to.

## Sections

| id | Section | Worker |
|---|---|---|
| `bijli` | ⚡ Bijli (electrical) | Electrician |
| `pani` | 🚿 Pani (plumbing) | Plumber |
| `deewar` | 🧱 Deewar/Chhat (masonry) | Raj mistri |
| `lakri` | 🪵 Lakri (carpentry) | Carpenter |
| `rang` | 🎨 Rang (painting) | Painter |
| `ac` | ❄️ AC/Fridge | AC technician |
| `gas` | 🔥 Gas | Gas fitter |

Anything with wires, switches, sockets, breakers, fans, UPS or solar wiring goes to `bijli`.
Broken or cracked walls, plaster, seepage, tiles and roof leaks go to `deewar`.

## Procedure

1. **Route.** From the photo and description, pick exactly one section id from the table above. Give a one-line problem summary and a confidence from 0 to 1. If confidence is below 0.5, ask one short clarifying question instead of guessing.
2. **Diagnose.** Read `sections/<id>.md`. Return the diagnosis, likely cause, materials (item, qty, estimated PKR each), labour hours, difficulty (easy/medium/hard), a safety level (none/caution/danger) with advice, and a short job card in Urdu for the worker.
3. **Price.** Do not invent a total. Compute it:
   `base = sum(qty × est_pkr) + labour_hours × rates[id].labour_per_hour + rates[id].visit_fee`
   `fair_min = round(base × 0.8, -1)`, `fair_max = round(base × 1.2, -1)`
4. **Safety.** Force `danger` for gas smell or leak, sparking, burning smell from wiring, water near electrics, or a wall/roof that may collapse. Show helplines: SNGPL 1199 (gas), FESCO 118 (electricity), Rescue 1122 (emergency).
5. **Check bids.** A bid above `fair_max × 1.25` is "above fair price". An offer below `fair_min × 0.75` is "unfair to worker". Otherwise it is fair.
6. **Verify.** Compare the before and after photos with the diagnosis. Return `fixed` (yes/no/unclear), the visible evidence, and a confidence. If the two photos look identical, answer `no` or `unclear`.

## Output formats

Router:
```json
{"section": "deewar", "problem_summary": "Diagonal crack in plaster near window", "confidence": 0.86, "clarifying_question": null}
```

Specialist:
```json
{"diagnosis": "...", "likely_cause": "...", "materials": [{"item": "Cement bag", "qty": 1, "est_pkr": 1500}], "labour_hours": 3, "difficulty": "medium", "safety": {"level": "caution", "advice": "..."}, "job_card_urdu": "..."}
```

Verifier:
```json
{"fixed": "yes", "evidence": "Crack filled and plastered, surface is smooth", "confidence": 0.8}
```

## Notes

- Prices are estimates for guidance, not quotes.
- Reply in the user's language (Urdu, Roman Urdu or English); the job card is always in Urdu.
