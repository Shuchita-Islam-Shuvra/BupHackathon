# GridWise — Smart Campus Energy Optimization Engine

> **BUP CSE Fest 2026 Hackathon · GridWise LLM Preliminary Round**
> Built by **Team GridWise** · University of Dhaka-affiliated BUP students



A deployed HTTP API service that interprets **1–3 free-text operator notes**, validates them through **deterministic guardrails**, and produces the **provably optimal, cost-minimized hourly energy dispatch** for a 24-hour campus microgrid using **Linear Programming**.

> “By combining natural-language understanding with a provably optimal linear program and an independent replay-verification safety net, GridWise delivers a bulletproof solution — robust against both numerical edge cases and linguistic variation.”

---



---



## Solution at a glance

```
Free-text note            GridWise API                       Verified plan
─────────────────         ────────────────────────────       ──────────────────
"Wash panels     ──►   1. Interpret       ──►              hourly_plan
 noon to 2 PM,                   (LLM or NLP)                   (24 entries)
 solar 25%"           2. Guardrails                            total_grid_kwh
                ──►   3. LP Optimize     ──►            ──►   total_cost_bdt
"Cafeteria menu         4. Replay Verify                       peak_grid_kwh
 updates tomorrow"          (independent)                      directive_interpretation
                           5. Respond (HTTP 200)
```

**Pipeline facts

- **24** decision variables (`grid[h]`, `charge[h]`, `discharge[h]`, `solar_used[h]`, `energy[h]`, `peak`)
- **~150** constraints (energy balance ×24, battery dynamics ×24, peak cap ×24, directive-specific, end-of-day neutrality, etc.)
- **Solver:** Coin-OR CBC (shipped with PuLP, MIT/BSD)
- **Typical solve time:** 30 – 110 ms per request

---

## Architecture — 5-stage pipeline

```
┌──────────────────────────────────────────────────────────────┐
│                  POST /optimize-energy                       │
│            (24-hour data + 1–3 operator notes)               │
└────────────────────────┬─────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  1. LLM Interpreter (app/llm_interpreter.py)                 │
│     • Primary:   Google Gemini 2.5 Flash                     │
│     • Secondary: OpenAI gpt-4o-mini                          │
│     • Fallback:  Deterministic NLP parser (word-boundary     │
│                  regex + parse_time_window)                  │
│     Output: ordered list of directive interpretations        │
└────────────────────────┬─────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  2. Deterministic Guardrails (app/guardrails.py)             │
│     • Note indexing 0 … N-1                                  │
│     • Hours → sorted unique integers in [0, 23]              │
│     • Solar factor clamped to [0.0, 1.0]                     │
│     • Reserve kWh ≤ capacity_kwh                             │
│     • Malformed notes default to no_op (never crash)         │
└────────────────────────┬─────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  3. Mathematical Optimizer (app/optimizer.py)                │
│     Formulated as a Linear Program in PuLP.                  │
│     Objective  :  minimize Σ grid[h] · tariff[h]             │
│     Subject to :  energy balance, battery dynamics,           │
│                   hourly charge/discharge bounds,             │
│                   end-of-day neutrality E₂₃ = E_init         │
│     Solver     :  Coin-OR CBC (bundled)                      │
└────────────────────────┬─────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  4. Independent Replay Verification (app/replay_checker.py)   │
│     Recomputes the plan hour-by-hour:                        │
│       • energy balance                                       │
│       • battery bounds                                       │
│       • directive application                                │
│       • total_grid / cost / peak match reported totals        │
│     On any violation → refuses the response.                  │
└────────────────────────┬─────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  5. JSON Response (POST /optimize-energy, HTTP 200)           │
│     Pydantic v2 schema (app/schemas.py).                     │
│     On error  → sanitized HTTP 400 / 500 (no stack traces).  │
└──────────────────────────────────────────────────────────────┘
```

### Directive types

| Type | `applies` | Key fields | Example note |
|---|---|---|---|
| `solar_reduction` | `true` | `hours: list[int]`, `factor: 0..1` | “PV drops to 20% from 13:00 to 15:00.” |
| `minimum_battery_reserve` | `true` | `hours`, `minimum_energy_kwh` | “Keep at least 100 kWh in battery 18:00–22:00.” |
| `no_charge_window` | `true` | `hours` | “Do not charge 14:00 to 16:00.” |
| `no_discharge_window` | `true` | `hours` | “Discharging disabled 18:00–21:00.” |
| `max_grid_window` | `true` | `hours`, `max_grid_kwh` | “Feeder capped at 80 kWh 18:00–21:00.” |
| `no_op` | `false` | `structured_adjustment: null` | “Cafeteria menu updates tomorrow.” |

---


Run all test cases:

```bash
# Public samples (10)
python scripts/test_all_samples.py

# Hidden stress (45)
python scripts/stress_hidden.py
```

---

## Quick start (3 options)

### Option A · Bare-metal Python

```bash
git clone https://github.com/Shuchita-Islam-Shuvra/BupHackathon.git
cd BupHackathon
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Option B · Docker (single command)

```bash
docker compose up -d
# wait ~3 seconds, then:
curl http://localhost:8000/health
```

### Option C · Local Python virtualenv (cleanest on Windows)

```bash
python -m venv venv
.\venv\Scripts\activate              # PowerShell
pip install -r requirements.txt
uvicorn app.main:app --reload
```

> **No API key required.** A deterministic NLP fallback guarantees 100 % valid output without internet, network, or LLM access.

---

## API reference

### `GET /health`

```bash
curl http://localhost:8000/health
```

```json
{ "status": "ok" }
```

### `POST /optimize-energy`

**Request**

```json
{
  "scenario_id": "TEST-01",
  "operator_notes": [
    "Wash panels noon to 2 PM, solar ~25%.",
    "Cafeteria menu changes tomorrow."
  ],
  "hours": [
    {"hour": 0,  "demand_kwh":  90, "solar_kwh":   0, "tariff_bdt_per_kwh": 7.0},
    {"hour": 1,  "demand_kwh":  85, "solar_kwh":   0, "tariff_bdt_per_kwh": 6.5}
    /* …24 entries, hour 0…23… */
  ],
  "battery": {
    "capacity_kwh": 300,
    "initial_energy_kwh": 110,
    "minimum_energy_kwh": 30,
    "max_charge_kwh_per_hour": 60,
    "max_discharge_kwh_per_hour": 60
  }
}
```

**Response**

```json
{
  "scenario_id": "TEST-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": { "hours": [12, 13], "factor": 0.25 },
      "explanation": "Solar reduced to 25% during noon-2 PM cleaning."
    },
    {
      "note_index": 1,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "Unrelated administrative note."
    }
  ],
  "hourly_plan": [
    {
      "hour": 0,
      "grid_kwh": 90,
      "solar_used_kwh": 0,
      "battery_action": "idle",
      "battery_kwh": 0,
      "battery_energy_after_kwh": 110
    }
    /* … 24 entries … */
  ],
  "total_grid_kwh": 2692.5,
  "total_cost_bdt": 38365,
  "peak_grid_kwh": 175.0,
  "plan_summary": "Optimal 24-hour energy schedule …"
}
```

### Errors

| Code | When | Body |
|---|---|---|
| `400` | Bad JSON / missing required fields | structured Pydantic field errors |
| `500` | LP infeasible, internal failure | sanitized message — no secrets, no stack traces |

---

## Mathematical model

### Decision variables

For each hour `h ∈ {0, …, 23}`:

| Variable | Meaning | Bounds |
|---|---|---|
| `grid[h]` | kWh purchased from grid | `[0, grid_cap[h]]` |
| `solar_used[h]` | kWh from PV actually consumed | `[0, solar_kwh[h] · factor[h]]` |
| `charge[h]` | kWh charged into battery | `[0, max_charge_kwh_per_hour]` (0 during `no_charge_window`) |
| `discharge[h]` | kWh discharged from battery | `[0, max_discharge_kwh_per_hour]` (0 during `no_discharge_window`) |
| `energy[h]` | battery state-of-charge after hour h | `[min_reserve[h], capacity_kwh]` |
| `peak` | max hourly grid import | `≥ 0` |

### Constraints

1. **Energy balance (24 constraints)**
   `grid[h] + solar_used[h] + discharge[h] = demand[h] + charge[h]`

2. **Battery dynamics (24 constraints)**
   - `energy[0] = init + charge[0] − discharge[0]`
   - `energy[h] = energy[h−1] + charge[h] − discharge[h]   ∀ h ≥ 1`

3. **Peak cap (24 constraints)**
   `grid[h] ≤ peak  ∀ h`

4. **End-of-day neutrality (1 constraint)**
   `energy[23] = init`

5. **Directive-induced bounds (variable)**
   `charge_allowed[h] = false`, `discharge_allowed[h] = false`,
   `min_reserve[h] = max(min_reserve[h], required)`, etc.

### Objective

Minimize

```
Σ_h grid[h] · tariff_bdt_per_kwh
+ 1 × 10⁻⁴ · peak                 # tiny peak-smoothing nudge
+ 1 × 10⁻⁶ · Σ_h (charge[h] + discharge[h])  # discourages free battery cycling
```

Solved with **Coin-OR CBC** (`pulp.PULP_CBC_CMD(msg=False)`) in 30–110 ms.

---

## Project structure

```
BupHackathon/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI endpoints + sanitized error handlers
│   ├── schemas.py           # Pydantic v2 request/response models
│   ├── guardrails.py        # Section 08 validation & normalization
│   ├── llm_interpreter.py   # Gemini / OpenAI / NLP fallback parser
│   ├── optimizer.py         # PuLP LP with Coin-OR CBC
│   └── replay_checker.py    # Independent hour-by-hour verification
│
├── scripts/
│   ├── test_all_samples.py  # 10 public-sample runner
│   ├── stress_hidden.py     # 45 hidden-case stress suite
│   └── video_script.md      # 3-minute presentation script
│
├── stress_failures/         # auto-created JSON dumps of failed tests
│   └── .gitkeep
│
├── BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
├── Dockerfile               # production build with bundled CBC
├── docker-compose.yml
├── requirements.txt
├── .env.example             # documentation only — never commit secrets
├── .gitignore
└── README.md                # you are here
```

---

## Reliability & security

| Concern | Mitigation |
|---|---|
| API key leak | `.env` is git-ignored; `.env.example` documents config |
| Crash on bad note | Guardrails default malformed notes to `no_op` |
| Crash on infeasible LP | Optimizer raises `ValueError`; API returns clean 500 |
| Crash on large inputs | Pydantic bounds & explicit type checks |
| Crash on bad JSON | Pydantic 400 with structured field errors |
| Plan vs reported-totals mismatch | Independent replay verification refuses mismatch |
| Secrets in error responses | Generic exception handler strips stack traces & env vars |
| Determinism for judges | NLP fallback works offline; LLM is enabled only when keys are set |

---

## Deployment

### Render.com (recommended for a public URL)

1. Push this repository to GitHub.
2. New → **Web Service** → connect the repo.
3. Render auto-detects the `Dockerfile`.
4. After deploy, your endpoint will be `https://<service-name>.onrender.com`.
5. Smoke-test: `curl https://<service-name>.onrender.com/health`.

### Local Docker

```bash
docker build -t gridwise-api:latest .
docker run --rm -p 8000:8000 gridwise-api:latest
```

### Environment variables (all optional)

| Name | Effect if set | Effect if unset |
|---|---|---|
| `GEMINI_API_KEY` | Gemini used for note interpretation | skip |
| `GOOGLE_API_KEY` | alias for `GEMINI_API_KEY` | skip |
| `OPENAI_API_KEY` | OpenAI used if Gemini absent or fails | skip |

> Even with **no** API keys and **no** internet, the system still produces a 100 % valid plan via the deterministic NLP fallback.

---

## Tech credits

- **[FastAPI](https://fastapi.tiangolo.com/)** — modern async web framework
- **[Pydantic](https://docs.pydantic.dev/)** v2 — request validation
- **[PuLP](https://github.com/coin-or/pulp)** — Pythonic LP modeling
- **[Coin-OR CBC](https://github.com/coin-or/cbc)** — open-source branch-and-cut solver
- **[uvicorn](https://www.uvicorn.org/)** — ASGI server
- **[Google Gemini](https://ai.google.dev/)** · **[OpenAI](https://openai.com)** — LLM interpretation paths
- **Docker** — reproducible deployment artifact

---

## Team — GridWise

Built by students who care about clean engineering, strong testing, and the kind of math that doesn’t lie.

> *“Win the hackathon by doing the boring things brilliantly.”*

---

## License

Released under the **MIT License** for educational use. See `LICENSE` (or include notice above) for details.

---

<p align="center">
  <sub>10 public samples ✓ · 45 hidden cases ✓ · End-of-day battery neutrality ✓ · Real-time parity ✓</sub>
</p>
