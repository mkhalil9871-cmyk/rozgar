# Rozgar (روزگار): fair prices for home repairs and services

**Rozgar helps Pakistani households and skilled workers agree on a fair price for home jobs, in English, Urdu or Roman Urdu. Gemma 4 understands the problem from a photo, guided questions or symptoms, routes it to the right worker, estimates the cost, checks every offer, and verifies the finished work.**

Built in one afternoon at Hacktoberfest Hack Day Faisalabad 2026 for the *Best Open-Source AI Project* and *Best Use of Gemma 4* challenges.

## The problem

Every week, millions of households in Pakistan call a plumber, electrician, mason or technician, and both sides lose:

- **Customers get overcharged.** "Bara kaam hai, 4000 lagenge" for a Rs 300 tap washer.
- **Honest workers get underpaid.** Haggling pushes good workers below a fair wage.
- **Many people can't describe the problem.** They don't know who to call, and closed machines like a washing machine or a water pump can't be photographed.
- **Nobody can prove the job was done** before the money changes hands.

Rozgar puts **Gemma 4 between both sides as a neutral helper**, in the language each person is comfortable with.

## How it works

1. **Log in as a customer or a worker**, as on Urban Company or TaskRabbit:
   - **Customers** give their name, mobile number with a verification code, and area.
   - **Workers** sign in to their profile (demo PIN 1234), or join with name, mobile and verification code, CNIC, trade, area and experience.

   This is a demo login: no SMS is sent (the demo code is 1234), and the phone and CNIC numbers are only checked for format, never stored.
2. **Choose your language.** The whole app switches between English, اردو (right-to-left) and Roman Urdu, and Gemma's answers come back in that language too.
3. **Pick what you need.** There are 7 sections, each with its own problem types:
   - **Electrical & Appliances** → electrician, AC technician or appliance technician, depending on the problem. Switches, wiring, fans, UPS/solar, ACs, washing machines, fridges and water motors are all in one place.
   - **Walls & roof** → mason. A broken or cracked wall, seepage, a roof leak.
   - **Plumbing, Carpentry, Painting, Gas.**
   - **Computer & IT** → a technician for Windows installs, slow PCs, printers or Wi-Fi, for people who aren't technical.
4. **Answer a few guided questions.** For example, a wall crack asks for its length (<30 cm / 30 cm–1 m / >1 m), width (hairline / coin / finger) and depth (plaster / bricks / through the wall). These answers scale the estimate.
5. **Machines you can't photograph** get a symptoms checklist ("doesn't spin", "hums but no water"). You can also add a photo of the model sticker.
6. **Gemma 4 diagnoses the problem** and writes a job card for the worker. It works from a photo, from symptoms only, or from text.
7. **Get an estimated cost.** It's computed from open, editable rates, and each rupee is explained.
8. **Dangerous jobs are flagged first.** A gas smell, sparking or a wall about to collapse shows SNGPL 1199, FESCO 118 or Rescue 1122.
9. **Booking details.** Give the house address and a nearby landmark, the preferred day and time slot, and the payment method (cash after work, JazzCash or Easypaisa).
10. **Type your own price.** The app reminds you to enter a justifiable amount and tells you live whether it's reasonable, too low (unfair to the worker), or higher than the estimate.
11. **See workers near you.** The list is filtered by your area and shows each worker's rating, jobs done, years of experience and past projects. Invite specific workers or post to everyone nearby.
12. **Workers send offers**, and each offer is checked the same way.
13. **The worker uploads an "after" photo.** Gemma compares it with the "before" photo and marks the job verified. The verified job is added to that worker's past projects.

## Multi-agent orchestration

```
 photo / guided answers / symptoms / text   (output language: English, Urdu or Roman Urdu)
        │
        ▼
 ┌──────────────┐  section, summary, confidence  (asks a clarifying question if unsure)
 │ Router agent │──────────────────────────────┐
 │  Gemma 4     │                              │
 └──────────────┘                              ▼
                               ┌─────────────────────────────┐
                               │ Section specialist agent    │  loads skills/…/sections/<id>.md
                               │ Gemma 4 (trade experts)     │  → diagnosis, materials, hours,
                               └─────────────────────────────┘    safety, Urdu job card
                                              │
                    ┌─────────────────────────┼──────────────────────────┐
                    ▼                         ▼                          ▼
          Pricing tool (Python)      Safety guard (Python)       Bid check (Python)
          rates.json × size answers  hard rules + helplines      customer price and offers
                                              │
                                              ▼
                               ┌─────────────────────────────┐
                               │ Verifier agent  Gemma 4     │  before + after photos → fixed?
                               └─────────────────────────────┘
```

- **Gemma 4 does perception and reasoning; Python does money and safety.** Prices come from `rates.json`, never from the model, so the AI can't make up a total.
- **Every agent output is validated** against a pydantic schema. If the JSON is bad, the agent is asked once to repair it.
- **Resilience:**
  - When the primary Gemma 4 model is busy (503), the app fails over to a second Gemma 4 model and puts the busy one on a short cooldown.
  - Gemini API keys rotate when one hits a rate limit (429).
  - Errors are shown cleanly in the UI instead of crashing.
- **Auditable.** Every step's output, model and timing is shown in the **Agent trace** panel.

Code: [app/orchestrator.py](app/orchestrator.py), [app/agents.py](app/agents.py), [app/pricing.py](app/pricing.py).

## Model

| | |
|---|---|
| Primary | **Gemma 4 31B** (`gemma-4-31b-it`) |
| Fallback | **Gemma 4 26B A4B** (`gemma-4-26b-a4b-it`) |
| Access | Gemini API ([Google AI Studio](https://aistudio.google.com/apikey)) via the `google-genai` SDK |
| Weights | Open-weight, released under the [Apache 2.0 license](https://ai.google.dev/gemma/apache_2) ([Gemma docs](https://ai.google.dev/gemma)) |

Only Gemma 4 models are used. No closed models.

## Agent Skill

[skills/rozgar-repair-estimator/SKILL.md](skills/rozgar-repair-estimator/SKILL.md) follows the [Agent Skills open standard](https://agentskills.io). It packages the whole method (route → diagnose → price → safety → bid check → verify) together with:

- [rates.json](skills/rozgar-repair-estimator/rates.json): labour rates, visit fees and material prices (Faisalabad 2026 estimates; edit them for your city)
- [catalog.json](skills/rozgar-repair-estimator/catalog.json): 40 problem types across 7 sections, each with guided questions and symptoms, all in 3 languages
- [workers.json](skills/rozgar-repair-estimator/workers.json): **sample** worker profiles for the demo. These are not real people.
- [sections/](skills/rozgar-repair-estimator/sections/): one guide per trade, covering common faults, materials and safety rules

The app loads these same files at startup, so the product and the skill share one source of truth. Any agent that supports skills, such as Claude Code, can use it on its own: copy the folder into its skills directory.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env        # then paste your Gemini API key
python -m uvicorn app.main:app --port 8000
```

Open http://localhost:8000. To demo both sides, open it in two tabs: switch one to **Customer** and the other to **Worker**, then sign in as a sample worker. On Windows you can double-click `run.bat` instead.

## Deploy for free (Render)

1. Push this repo to GitHub.
2. On [render.com](https://render.com), choose **New → Blueprint** and pick the repo. `render.yaml` sets everything up.
3. Set `GEMINI_API_KEY` in the service's Environment tab. `GEMINI_API_KEYS` is optional: extra comma-separated keys for rate limits.

Two things to know about the free plan:

- **It sleeps after about 15 minutes idle**, so the first visit takes around 30–50 seconds to wake it.
- **Jobs live in memory** and reset when the service restarts. That's fine for a prototype; a real launch would use a database.

**Why not Vercel?** Vercel runs Python as serverless functions that don't share memory, so live bidding between customer and worker would need an external database. Render runs one always-on process, which this app needs.

## API

| Method | Path | What it does |
|---|---|---|
| GET | `/api/health` | model in use and number of keys loaded (never the keys themselves) |
| GET | `/api/sections` | trade sections from the skill |
| GET | `/api/catalog` | problem types, guided questions and symptoms per section |
| GET | `/api/workers?section=&area=` | sample workers, nearest area first, then by rating |
| POST | `/api/diagnose` | multipart `photo`, `description`, `section`, `subcategory`, `answers`, `symptoms`, `lang` → diagnosis, estimate, safety, trace |
| POST | `/api/jobs` | post a job with the customer's offer |
| GET | `/api/jobs?section=` | list jobs for workers |
| POST | `/api/jobs/{id}/bids` | worker offer (`worker_id`, `amount`), checked against the estimate |
| POST | `/api/jobs/{id}/accept` | customer accepts a bid |
| POST | `/api/jobs/{id}/verify` | after photo → Gemma Verifier |

## Dependencies and licenses

| Package | License |
|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic) | MIT |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 |
| [google-genai](https://github.com/googleapis/python-genai) | Apache-2.0 |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | BSD-3-Clause |

The frontend is plain HTML, CSS and JavaScript with no frameworks.

Icons are from [Lucide](https://lucide.dev) (ISC). Photos in `static/photos/` are from [Unsplash](https://unsplash.com) under the [Unsplash License](https://unsplash.com/license) (free to use). The Rozgar logo (`static/logo.svg`) was made for this project and is MIT-licensed with the code. Fonts are [Inter](https://fonts.google.com/specimen/Inter) and [Noto Nastaliq Urdu](https://fonts.google.com/noto/specimen/Noto+Nastaliq+Urdu) (SIL OFL).

## What's next

- Escrow payment through JazzCash or Easypaisa, released automatically when Gemma verifies the job
- Real worker sign-up with verified CNIC profiles and customer ratings
- A WhatsApp intake: forward a photo, get an estimate
- Voice input and output in Urdu for people who can't read
- Live rates per city, learned from accepted bids

## Disclaimer

Prices are estimates to guide negotiation, not quotes. For gas leaks, sparking wires or structural cracks, call the helpline and a qualified professional first.

## License

[MIT](LICENSE)
