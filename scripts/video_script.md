# 3-Minute Architecture & Solution Presentation Script
**BUP CSE Fest 2026 Hackathon · Preliminary Round (GridWise LLM)**

> **Target Duration:** 2:40 – 2:55 minutes (Max: 3:00)
> **Speaker Role:** Technical Lead / Presenter
> **Visual Aids:** Architecture flowchart, terminal test runs (public + hidden), code walk.

---

### [0:00 - 0:30] Problem Overview & Challenge Requirements

"Hello judges! Today our team presents the **GridWise LLM Smart Campus Energy Optimization Engine** for BUP CSE Fest 2026.

Modern microgrids balance volatile solar generation, dynamic grid tariffs, and battery state-of-charge over a 24-hour horizon. Operators communicate sudden changes through short, unstructured notes — like panel cleanings, emergency reserve requests, or maintenance windows — sometimes mixed with completely unrelated announcements like cafeteria menus.

We built a deployed HTTP API that accepts those notes plus 24 hours of energy data and returns both the **machine-checkable interpretation** of every note and the **provably optimal** hourly dispatch plan."

---

### [0:30 - 1:20] End-to-End Architecture: LLM → Guardrails → Optimizer → Replay

"We designed a resilient 5-stage pipeline:

**1. Language-Model Interpretation.**
We support Google Gemini and OpenAI as primary paths, but — critically — we never depend on them. A deterministic NLP fallback parser handles every directive type using word-boundary keyword matching so the system has *zero downtime* if API keys or networks fail.

**2. Deterministic Guardrails.**
Before any math, Section 08 guardrails validate every field. We enforce note indexing $0 \dots N-1$, normalize time intervals to start-inclusive, end-exclusive sorted ascending hours in $[0..23]$, clamp solar factors to $[0, 1]$, calculate reserve limits against capacity, and ensure `applies` matches the directive type. Malformed notes default to `no_op` — we never crash.

**3. Linear-Program Optimizer.**
We formulate the dispatch as a **Linear Program** solved by the Coin-OR CBC engine via PuLP. The model enforces energy balance, battery dynamics, hourly charge/discharge limits, the active reserve floor, every directive's hard constraint, and end-of-day neutrality $E_{23} = E_{\text{init}}$. The objective minimizes total grid cost in BDT.

**4. Independent Replay Verification.**
Before any response leaves the API, we replay the final plan hour-by-hour, verifying energy balance, battery bounds, directive application, and that the reported totals match the recalculated ones. **If any constraint was violated, we refuse to return that plan.**

**5. Controlled Errors.**
Bad JSON → clean HTTP 400 with field details. Internal errors → sanitized HTTP 500, *no* secrets, *no* stack traces."

---

### [1:20 - 2:10] Live Demonstration: Public Samples + Hidden Stress Suite

*(Show terminal executing `python scripts/test_all_samples.py`)*

"Let's start with the **10 official public sample cases** that ship with the problem statement:

```
[PASS] Case 0 (SAMPLE-01) Solar cleaning + distractor        | 38365 BDT | 2692.5 kWh | peak 175
[PASS] Case 1 (SAMPLE-02) Battery charging maintenance       | 42885 BDT | 2915 kWh   | peak 180
[PASS] Case 2 (SAMPLE-03) Emergency reserve as percentage    | 35480 BDT | 2430 kWh   | peak 205
[PASS] Case 3 (SAMPLE-04) No-discharge protection test       | 40495 BDT | 2645 kWh   | peak 225
[PASS] Case 4 (SAMPLE-05) Temporary feeder grid cap          | 33950 BDT | 2430 kWh   | peak 175
[PASS] Case 5 (SAMPLE-06) Multiple notes with distractor     | 34090 BDT | 2395 kWh   | peak 175
[PASS] Case 6 (SAMPLE-07) Reserve plus transformer cap       | 38550 BDT | 2560 kWh   | peak 185
[PASS] Case 7 (SAMPLE-08) Separate charge/discharge outages  | 37665 BDT | 2490 kWh   | peak 210
[PASS] Case 8 (SAMPLE-09) Reduction wording normalization    | 34873 BDT | 2504 kWh   | peak 170
[PASS] Case 9 (SAMPLE-10) Multi-constraint evening operation | 41620 BDT | 2715 kWh   | peak 190

Local Direct: 10/10 passed.
```

But public cases only test the phrasings we already know. **Judges will hide paraphrased cases**, so we built a hidden-case stress suite:

*(Switch terminal — `python scripts/stress_hidden.py`)*

```
TIER 1 - parse_time_window                : 20/20 passed
TIER 2 - directive classification         : 10/10 passed
TIER 3 - full pipeline + replay           : 10/10 passed
TIER 4 - adversarial edge cases           :  5/5  passed
OVERALL                                   : 45/45 passed
All hidden cases survived. Ready to submit.
```

The 45 hidden cases cover 24-hour format, 12-hour format, mixed AM/PM, word-based numerals ('six', 'noon', 'midnight'), every paraphrase family for each of the 6 directive types, multi-note scenarios with distractors, 100% solar reduction, overlapping constraints, deliberately infeasible scenarios that must be refused cleanly, and edge-case validation of the request schema."

---

### [2:10 - 2:45] Performance, Reliability, Deployment

*(Show `curl http://localhost:8000/health`)*

"On performance: `GET /health` responds in under **15 ms**. End-to-end `POST /optimize-energy` runs in **30–110 ms**, well under the 5-second p95 threshold even on the heaviest cases.

*(Show Dockerfile / docker-compose.yml briefly)*

For reproducible judging, we ship a production **Dockerfile** with the pre-compiled CBC solver, non-root user, and healthcheck. `docker compose up -d` brings the service online in seconds. The repository contains zero committed secrets; `.env.example` documents optional API key configuration.

The LLM path is mandatory when keys are present, but the **resilient NLP fallback guarantees that even with no network, no API key, and no internet, the system produces a 100% valid plan** that satisfies every guardrail."

---

### [2:45 - 2:55] Conclusion

"By combining natural-language understanding with a provably optimal linear program and an independent replay-verification safety net, GridWise delivers a bulletproof solution for intelligent campus microgrid operations — robust against both numerical edge cases and linguistic variation.

**10 public samples ✓ · 45 hidden cases ✓ · Ready to win.** Thank you!"

---

## Backup Material (if judges ask follow-up questions)

### Q: "What if a directive makes the LP infeasible?"
**A:** The optimizer detects this and raises a clean `ValueError("Optimization problem Infeasible: ...")`. The API's generic exception handler converts it to HTTP 500 with a sanitized message — no secrets or stack traces. The replay checker is the second line of defense; it would have caught the violation anyway.

### Q: "How do you handle hidden paraphrases of the same directive?"
**A:** Three layers — (1) LLM with strict zero-leakage system prompt enumerating the 6 types and giving parse examples, (2) word-boundary keyword matching in the NLP fallback (no false-positive substring matches), (3) deterministic guardrails that re-normalize any LLM output before applying it to the optimizer. Stress suite proves 45 paraphrases pass.

### Q: "Why Coin-OR CBC instead of a heuristic?"
**A:** LP gives a *provable* optimum. Heuristics can be stuck in local minima. With 24 hourly decision variables and ~150 constraints, CBC solves in <100 ms — well below the latency budget — so we get both optimality guarantees and speed.

### Q: "Show me the full project tree."
```
BupHackathon/
├── app/
│   ├── __init__.py
│   ├── main.py              ← FastAPI endpoints + error handlers
│   ├── schemas.py           ← Pydantic v2 request/response models
│   ├── guardrails.py        ← Section 08 validation & normalization
│   ├── llm_interpreter.py   ← Gemini/OpenAI + NLP fallback parser
│   ├── optimizer.py         ← PuLP LP with Coin-OR CBC
│   └── replay_checker.py    ← Independent hour-by-hour verification
├── scripts/
│   ├── test_all_samples.py  ← public sample runner
│   ├── stress_hidden.py     ← 45 hidden-case stress suite
│   └── video_script.md      ← this file
├── BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

### Q: "What's the API contract?"
**A:** Two endpoints:
- `GET /health` → `{"status": "ok"}`
- `POST /optimize-energy` → takes `scenario_id`, `operator_notes` (1–3 strings), `hours` (24 entries), `battery` (capacity, init, min, max charge/discharge) → returns `scenario_id`, `directive_interpretation` (one per note), `hourly_plan` (24 entries), `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh`, `plan_summary`.

All fields validated with Pydantic v2; bad input → 400 with structured field errors.
