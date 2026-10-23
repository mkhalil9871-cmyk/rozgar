# 🛠️ Rozgar (روزگار): InDrive for mistris

**Rozgar helps Pakistani households and mistris agree on a fair price for home repairs. Gemma 4 diagnoses the problem from a photo, routes it to the right worker, sets a fair price, checks every bid, and verifies the finished job.**

Built in one afternoon at Hacktoberfest Hack Day Faisalabad 2026 for the *Best Open-Source AI Project* and *Best Use of Gemma 4* challenges.

## The problem

Every week, millions of households in Pakistan call a plumber, electrician or raj mistri, and both sides lose:

- **Customers get overcharged.** "Bara kaam hai, 4000 lagenge" for a Rs 300 tap washer.
- **Honest workers get underpaid.** Haggling pushes good mistris below a fair wage.
- **Nobody can prove the job was done** before the money changes hands.

InDrive fixed this for rides by letting the customer offer a fare. Rozgar does the same for home repairs, with **Gemma 4 as a neutral judge** between both sides.

## How it works

1. **Pick a section, or just snap a photo.** ⚡ Bijli (anything electrical) goes to an electrician, 🧱 Deewar/Chhat (a broken or cracked wall, seepage, a roof leak) goes to a raj mistri. There are also 🚿 Pani, 🪵 Lakri, 🎨 Rang, ❄️ AC/Fridge and 🔥 Gas.
2. **Gemma 4 diagnoses the problem** and writes a job card in Urdu for the worker.
3. **A fair price range** is computed from open, editable rates. Each rupee is explained.
4. **Dangerous jobs are flagged first.** A gas smell, sparking or a collapsing wall shows SNGPL 1199, FESCO 118 or Rescue 1122.
5. **The customer makes an offer and mistris bid.** Every bid is checked: *above fair price*, *unfair to worker*, or *fair*.
6. **The mistri uploads an "after" photo.** Gemma compares it with the "before" photo and marks the job ✅ verified before payment.

## Multi-agent orchestration

```
 photo + text (Urdu / Roman Urdu / English)
        │
        ▼
 ┌──────────────┐  section, summary, confidence  (asks a clarifying question if unsure)
 │ Router agent │──────────────────────────────┐
 │  Gemma 4     │                              │
 └──────────────┘                              ▼
                               ┌─────────────────────────────┐
                               │ Section specialist agent    │  loads skills/…/sections/<id>.md
                               │ Gemma 4 (electrician, mason…)│  → diagnosis, materials, hours,
                               └─────────────────────────────┘    safety, Urdu job card
                                              │
                    ┌─────────────────────────┼──────────────────────────┐
                    ▼                         ▼                          ▼
          Pricing tool (Python)      Safety guard (Python)       Bid check (Python)
          rates.json → fair range    hard rules + helplines      every offer and bid
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
- [sections/](skills/rozgar-repair-estimator/sections/): one guide per trade, covering common faults, materials and safety rules

The app loads these same files at startup, so the product and the skill share one source of truth. Any agent that supports skills, such as Claude Code, can use it on its own: copy the folder into its skills directory.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env        # then paste your Gemini API key
python -m uvicorn app.main:app --port 8000
```

Open http://localhost:8000. To demo both sides, open it in two tabs: switch one to **Customer** and the other to **Mistri**. On Windows you can double-click `run.bat` instead.

## Deploy for free (Render)

1. Push this repo to GitHub.
2. On [render.com](https://render.com), choose **New → Blueprint** and pick the repo. `render.yaml` sets everything up.
3. Set `GEMINI_API_KEY` in the service's Environment tab. `GEMINI_API_KEYS` is optional: extra comma-separated keys for rate limits.

Two things to know about the free plan:

- **It sleeps after about 15 minutes idle**, so the first visit takes around 30–50 seconds to wake it.
- **Jobs live in memory** and reset when the service restarts. That's fine for a prototype; a real launch would use a database.

**Why not Vercel?** Vercel runs Python as serverless functions that don't share memory, so live bidding between customer and mistri would need an external database. Render runs one always-on process, which this app needs.

## API

| Method | Path | What it does |
|---|---|---|
| GET | `/api/health` | model in use and number of keys loaded (never the keys themselves) |
| GET | `/api/sections` | trade sections from the skill |
| POST | `/api/diagnose` | multipart `photo`, `description`, optional `section` and `answer` → diagnosis, price, safety, trace |
| POST | `/api/jobs` | post a job with the customer's offer |
| GET | `/api/jobs?section=` | list jobs for mistris |
| POST | `/api/jobs/{id}/bids` | mistri bid, checked for fairness |
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

## What's next

- Escrow payment through JazzCash or Easypaisa, released automatically when Gemma verifies the job
- Mistri ratings and verified CNIC profiles
- A WhatsApp intake: forward a photo, get a fair price
- Live rates per city, learned from accepted bids

## Disclaimer

Prices are estimates to guide negotiation, not quotes. For gas leaks, sparking wires or structural cracks, call the helpline and a qualified professional first.

## License

[MIT](LICENSE)
