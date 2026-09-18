# GridWise — LLM-Assisted Smart Campus Energy Optimization

[![BUP CSE Fest 2026](https://img.shields.io/badge/BUP%20CSE%20Fest-2026%20Hackathon-blue.svg)](https://fest.bupcopc.tech)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-Production%20Ready-teal.svg)](https://fastapi.tiangolo.com)
[![PuLP CBC](https://img.shields.io/badge/Solver-CoinOR%20CBC-orange.svg)](https://github.com/coin-or/pulp)

Production-grade HTTP API service developed for the **BUP CSE Fest 2026 Hackathon (GridWise LLM)** Preliminary Round. The system ingests a 24-hour campus energy forecast (demand, solar, tariff, battery state) and 1–3 unstructured natural-language operator notes, interprets them into machine-checkable directives, rigorously guardrails the extraction, and determines the provably optimal, cost-minimized hourly dispatch schedule using Linear Programming.

---

## Architecture Overview

```
┌───────────────────────────┐
│ POST /optimize-energy     │
│ (24h Data + Notes)        │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ 1. LLM Interpreter        │◄── Google Gemini / OpenAI / Resilient NLP Fallback
│ (Operator Notes Analysis) │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ 2. Deterministic          │◄── Section 08 Guardrail Normalization
│    Guardrails             │    (Hour intervals, type checks, bounds)
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ 3. Mathematical Optimizer │◄── PuLP Linear Program (Coin-OR CBC Solver)
│    (Linear Programming)   │    (Energy balance, battery rules, neutrality)
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ 4. Consistency Recalculator│◄── Recalculates total_grid, cost & peak from plan
│ & JSON Response           │
└───────────────────────────┘
```

---

## Key Features & Compliance

1. **Mandatory Language-Model Interpretation:**
   - Interprets 1–3 natural-language operator notes into one of 6 supported directive types:
     - `solar_reduction` (`factor`, `hours`)
     - `minimum_battery_reserve` (`minimum_energy_kwh`, `hours`)
     - `no_charge_window` (`hours`)
     - `no_discharge_window` (`hours`)
     - `max_grid_window` (`max_grid_kwh`, `hours`)
     - `no_op` (`structured_adjustment: null`, `applies: false`)
   - Fully supports **Google Gemini 2.5 Flash / 1.5 Flash** and **OpenAI GPT-4o-mini**.
   - Includes a high-precision semantic fallback parser to ensure **zero downtime** and **100% test reliability** even if external API limits or network drops occur.

2. **Section 08 Deterministic Guardrails:**
   - Verifies note ordering ($0 \dots N-1$).
   - Normalizes time intervals to start-inclusive, end-exclusive sorted ascending unique integers $\in [0, 23]$ (e.g., *1 PM to 3 PM* $\rightarrow$ `[13, 14]`).
   - Normalizes percentage reserves against `battery.capacity_kwh`.
   - Bounds solar factors $\in [0.0, 1.0]$.
   - Safe failure handling: malformed notes gracefully default to `no_op` without crashing.

3. **Global Cost Optimization (Linear Programming):**
   - Formulated with **PuLP** using the high-performance **Coin-OR CBC** solver.
   - Strictly satisfies all GridWise physical equations:
     $$\text{grid}[h] + \text{solar\_used}[h] + \text{discharge}[h] = \text{demand}[h] + \text{charge}[h]$$
   - Satisfies hourly charge/discharge physical and directive bounds.
   - Enforces **End-of-day Neutrality**: $E_{23} = E_{\text{init}}$.
   - Smooths peak demand and eliminates simultaneous charging and discharging.
   - Verified **100.0% exact match** on all 10 public official sample cases.

4. **Performance & Reliability:**
   - `/health` latency: $< 15\text{ ms}$.
   - `/optimize-energy` latency: $< 50\text{ ms}$ (well within the $\le 5\text{s}$ $p95$ threshold).
   - Clean, controlled HTTP 400 validation and HTTP 500 error responses with **zero secret or stack trace leakage**.

---

## Local Quickstart (Clean Environment)

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.11, 3.12, 3.14)
- `pip` package manager

### 2. Clone Repository & Install Dependencies
```bash
git clone https://github.com/Shuchita-Islam-Shuvra/BupHackathon.git
cd BupHackathon

# Optional: Create virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
# source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables (Optional)
The service includes an autonomous fallback parser that requires zero API keys. To enable hosted cloud LLMs, create a `.env` file:
```bash
cp .env.example .env
```
Add your credentials:
```ini
GEMINI_API_KEY=your_gemini_api_key_here
# or
OPENAI_API_KEY=your_openai_api_key_here
```

### 4. Start the API Service
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
The API is now running at `http://localhost:8000`.

---

## Verification & API Curl Examples

### Health Readiness Check (`GET /health`)
```bash
curl -X GET http://localhost:8000/health
```
**Expected Response:**
```json
{
  "status": "ok"
}
```

### Energy Optimization (`POST /optimize-energy`)
```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "TEST-01",
    "operator_notes": [
      "Facilities will wash the rooftop solar panels from noon until 2 PM. Usable solar is roughly 25% of forecast.",
      "Cafeteria menu changes tomorrow."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 7},
      {"hour": 1, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6.5},
      {"hour": 2, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 3, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 4, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6.5},
      {"hour": 5, "demand_kwh": 95, "solar_kwh": 0, "tariff_bdt_per_kwh": 7.5},
      {"hour": 6, "demand_kwh": 110, "solar_kwh": 10, "tariff_bdt_per_kwh": 9},
      {"hour": 7, "demand_kwh": 130, "solar_kwh": 35, "tariff_bdt_per_kwh": 11},
      {"hour": 8, "demand_kwh": 160, "solar_kwh": 70, "tariff_bdt_per_kwh": 13},
      {"hour": 9, "demand_kwh": 180, "solar_kwh": 110, "tariff_bdt_per_kwh": 15},
      {"hour": 10, "demand_kwh": 200, "solar_kwh": 140, "tariff_bdt_per_kwh": 16},
      {"hour": 11, "demand_kwh": 210, "solar_kwh": 160, "tariff_bdt_per_kwh": 16.5},
      {"hour": 12, "demand_kwh": 205, "solar_kwh": 170, "tariff_bdt_per_kwh": 16.5},
      {"hour": 13, "demand_kwh": 195, "solar_kwh": 165, "tariff_bdt_per_kwh": 16},
      {"hour": 14, "demand_kwh": 190, "solar_kwh": 145, "tariff_bdt_per_kwh": 15},
      {"hour": 15, "demand_kwh": 185, "solar_kwh": 115, "tariff_bdt_per_kwh": 14},
      {"hour": 16, "demand_kwh": 175, "solar_kwh": 75, "tariff_bdt_per_kwh": 13},
      {"hour": 17, "demand_kwh": 170, "solar_kwh": 30, "tariff_bdt_per_kwh": 14},
      {"hour": 18, "demand_kwh": 180, "solar_kwh": 5, "tariff_bdt_per_kwh": 18},
      {"hour": 19, "demand_kwh": 190, "solar_kwh": 0, "tariff_bdt_per_kwh": 20},
      {"hour": 20, "demand_kwh": 175, "solar_kwh": 0, "tariff_bdt_per_kwh": 18},
      {"hour": 21, "demand_kwh": 150, "solar_kwh": 0, "tariff_bdt_per_kwh": 15},
      {"hour": 22, "demand_kwh": 120, "solar_kwh": 0, "tariff_bdt_per_kwh": 11},
      {"hour": 23, "demand_kwh": 100, "solar_kwh": 0, "tariff_bdt_per_kwh": 8}
    ],
    "battery": {
      "capacity_kwh": 300,
      "initial_energy_kwh": 110,
      "minimum_energy_kwh": 30,
      "max_charge_kwh_per_hour": 60,
      "max_discharge_kwh_per_hour": 60
    }
  }'
```

---

## Running the Automated Test Suite

A comprehensive test runner is provided to test the 10 official public sample scenarios:

```bash
# 1. Test local pipeline directly:
python scripts/test_all_samples.py

# 2. Test live running HTTP API:
python scripts/test_all_samples.py --http http://localhost:8000
```

**Benchmark Results:**
```
=== Testing API service at http://localhost:8000 ===
  [PASS] GET /health -> 200 OK in 11.9ms

Evaluating 10 public sample cases via HTTP API...
  [PASS] Case 00 (SAMPLE-01) - Solar cleaning + distractor    | Grid: 2692.5 kWh | Cost: 38365 BDT | Peak: 175.0 | Latency: 45.5ms
  [PASS] Case 01 (SAMPLE-02) - Battery charging maintenance   | Grid: 2915.0 kWh | Cost: 42885 BDT | Peak: 180.0 | Latency: 33.0ms
  [PASS] Case 02 (SAMPLE-03) - Emergency reserve as percentag | Grid: 2430.0 kWh | Cost: 35480 BDT | Peak: 205.0 | Latency: 53.1ms
  [PASS] Case 03 (SAMPLE-04) - No-discharge protection test   | Grid: 2645.0 kWh | Cost: 40495 BDT | Peak: 225.0 | Latency: 43.0ms
  [PASS] Case 04 (SAMPLE-05) - Temporary feeder grid cap      | Grid: 2430.0 kWh | Cost: 33950 BDT | Peak: 175.0 | Latency: 35.0ms
  [PASS] Case 05 (SAMPLE-06) - Multiple notes with distractor | Grid: 2395.0 kWh | Cost: 34090 BDT | Peak: 175.0 | Latency: 31.0ms
  [PASS] Case 06 (SAMPLE-07) - Reserve plus transformer cap   | Grid: 2560.0 kWh | Cost: 38550 BDT | Peak: 185.0 | Latency: 47.7ms
  [PASS] Case 07 (SAMPLE-08) - Separate charge/discharge outa | Grid: 2490.0 kWh | Cost: 37665 BDT | Peak: 210.0 | Latency: 32.3ms
  [PASS] Case 08 (SAMPLE-09) - Reduction wording normalizatio | Grid: 2504.0 kWh | Cost: 34873 BDT | Peak: 170.0 | Latency: 42.2ms
  [PASS] Case 09 (SAMPLE-10) - Multi-constraint evening opera | Grid: 2715.0 kWh | Cost: 41620 BDT | Peak: 190.0 | Latency: 53.1ms

Summary: 10/10 cases passed all checks successfully!
```

---

## Docker Fallback Execution

The project includes a production-ready `Dockerfile` with the pre-compiled `coinor-cbc` solver.

### Build and Run with Docker
```bash
# Build Docker image
docker build -t gridwise-api:latest .

# Run Docker container exposing port 8000
docker run -d -p 8000:8000 --name gridwise-service gridwise-api:latest

# Verify health inside container
curl http://localhost:8000/health
```

### Run with Docker Compose
```bash
docker compose up -d
```

---

## Video Script (Tie-Breaker Deliverable)

A complete, timed 3-minute presentation script covering problem understanding, architecture flow, live benchmark, and deployment is located at:
📁 [`scripts/video_script.md`](./scripts/video_script.md)

---

## Technical Specifications & Solver Disclosure

- **Framework:** FastAPI / Uvicorn (Asynchronous I/O)
- **Data Validation:** Pydantic v2
- **Mathematical Optimization Solver:** PuLP with Coin-OR CBC Linear Programming Engine
- **Language Models Supported:** Google Gemini (`gemini-2.5-flash`, `gemini-1.5-flash`), OpenAI (`gpt-4o-mini`), alongside deterministic semantic parsing.
- **Known Limitations:**
  - Optimization horizon is fixed to 24 hourly intervals (0 through 23).
  - Unused solar is curtailed without grid export per challenge specifications.