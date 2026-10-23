---
name: rozgar-repair-estimator
description: Diagnose a home-repair or home-service problem from a photo, guided answers (size, depth, count), ticked symptoms or a description, route it to the right worker (electrician, plumber, mason, carpenter, painter, AC technician, gas fitter, computer technician, appliance technician), estimate the cost in Pakistani Rupees, flag dangerous jobs, check prices against the estimate, and verify finished work from before/after photos. Answers in English, Urdu or Roman Urdu. Use when someone in Pakistan needs to know who to call, what a job should cost, or whether a worker's quote or finished work is reasonable.
license: MIT
metadata:
  project: Rozgar
  model: gemma-4-31b-it
---

# Rozgar repair estimator

This skill turns "something is broken at home" into a fair, checkable repair job.
It is the same logic the Rozgar app runs; the app loads these files at startup.

## Files

- [rates.json](rates.json): labour rate per hour, visit fee and common material prices per section (Faisalabad, 2026, rough estimates), with section and worker names in English, Urdu and Roman Urdu.
- [catalog.json](catalog.json): the problem types inside each section, the guided questions for each (size, depth, count, urgency...) with a labour multiplier per answer, and symptom lists for machines that cannot be photographed.
- [workers.json](workers.json): SAMPLE worker profiles (area, rating, jobs done, past projects) for demos. Not real people.
- [sections/](sections/): one guide per trade section with common faults, typical materials and safety rules. Read only the guide for the section you route to.

## Sections

| id | Section | Worker |
|---|---|---|
| `bijli` | Electrical & appliances (Bijli) | Electrician, AC technician or appliance technician, set by the problem type's `trade` |
| `pani` | Plumbing (Pani) | Plumber |
| `deewar` | Walls & roof (Deewar/Chhat) | Mason (raj mistri) |
| `lakri` | Carpentry (Lakri) | Carpenter |
| `rang` | Painting (Rang) | Painter |
| `gas` | Gas | Gas fitter |
| `computer` | Computer & IT | Computer technician |

Anything with wires, switches, sockets, breakers, fans, UPS or solar wiring goes to `bijli`.
Broken or cracked walls, plaster, seepage, tiles and roof leaks go to `deewar`.
Windows installs, slow PCs, printers, Wi-Fi and software go to `computer`.
ACs, washing machines, fridges, water pumps, microwaves and small machines also go to `bijli`. Their problem types carry `trade: "ac"` or `trade: "appliance"`, which picks the worker, the rate (`rates.json` → `trades`) and the guide (`sections/ac.md`, `sections/appliance.md`).

## Procedure

0. **Ask, don't guess.** If the section is known, ask the problem type and its guided questions from `catalog.json` (e.g. crack length, width and depth). For a closed machine, ask the symptoms and, optionally, a photo of the model sticker instead of a photo of the fault.
1. **Route.** From the photo and description, pick exactly one section id from the table above. Give a one-line problem summary and a confidence from 0 to 1. If confidence is below 0.5, ask one short clarifying question instead of guessing.
2. **Diagnose.** Read `sections/<id>.md`. Return the diagnosis, likely cause, materials (item, qty, estimated PKR each), labour hours, difficulty (easy/medium/hard), a safety level (none/caution/danger) with advice, and a short job card in Urdu for the worker.
3. **Price.** Do not invent a total and do not guess the size. Ask the customer how much work there is in the problem type's unit from `catalog.json` (feet, square feet, number of items, or one job), and who buys the materials. Then compute:
   `labour = max(quantity × unit.rate × difficulty, unit.min)` where `difficulty` is the largest `mult` among the chosen answers (1.0–1.5).
   Add materials (priced from `rates.json`) only if the worker brings them; otherwise show them as the customer's shopping list.
   `fair_min = round(base × 0.85, -1)`, `fair_max = round(base × 1.15, -1)`
4. **Safety.** Force `danger` for gas smell or leak, sparking, burning smell from wiring, water near electrics, or a wall/roof that may collapse. Show helplines: SNGPL 1199 (gas), FESCO 118 (electricity), Rescue 1122 (emergency).
5. **Check prices.** The customer types their own price and each worker sends an offer. Above `fair_max × 1.25` is "higher than the estimate". Below `fair_min × 0.75` is "too low, unfair to the worker". Otherwise it is reasonable.
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
- Reply in the user's chosen language: English, Urdu in Urdu script, or Roman Urdu in Latin letters. The job card uses the same language.
