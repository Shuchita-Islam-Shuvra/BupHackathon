import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from app.schemas import (
    OptimizeEnergyRequest,
    OptimizeEnergyResponse,
    HealthResponse
)
from app.llm_interpreter import interpret_operator_notes
from app.guardrails import validate_and_guardrail_directives
from app.optimizer import solve_energy_optimization
from app.replay_checker import replay_and_verify_schedule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("gridwise.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("GridWise Energy Optimization Service initialized.")
    yield
    logger.info("GridWise Energy Optimization Service shutting down.")

app = FastAPI(
    title="GridWise LLM Campus Energy Optimization API",
    description="LLM-Assisted Smart Campus Energy Scheduling Service for BUP CSE Fest 2026",
    version="2.0.0",
    lifespan=lifespan
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return controlled 400 response for malformed or structurally invalid request."""
    logger.warning(f"Validation error on {request.url.path}: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": "Malformed or structurally invalid request.",
            "details": [
                {"field": ".".join(str(loc) for loc in err["loc"]), "msg": err["msg"]}
                for err in exc.errors()
            ]
        }
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Controlled internal error handler that NEVER leaks secrets or raw stack traces."""
    logger.error(f"Internal error processing {request.url.path}: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "An internal processing error occurred while optimizing the energy schedule.",
            "status": "error"
        }
    )

@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def get_health():
    """Readiness endpoint for the judging harness."""
    return HealthResponse(status="ok")

@app.post("/optimize-energy", response_model=OptimizeEnergyResponse, tags=["Optimization"])
async def post_optimize_energy(req: OptimizeEnergyRequest):
    """
    Main LLM interpretation + 24-hour mathematical optimization endpoint.
    1. Interprets operator notes using LLM (Gemini / OpenAI) with fallback.
    2. Enforces deterministic guardrails (Section 08).
    3. Solves Linear Program with PuLP/CBC to minimize grid cost.
    4. Replays and validates all constraints and recalculated metrics.
    5. Returns exact structured JSON response.
    """
    # 1. LLM Interpretation
    raw_directives = interpret_operator_notes(req.operator_notes, req.battery)
    
    # 2. Deterministic Guardrails
    directives = validate_and_guardrail_directives(raw_directives, req.operator_notes, req.battery)
    
    # 3. Mathematical Optimization (Linear Programming)
    hourly_plan, total_grid, total_cost, peak_grid, plan_summary = solve_energy_optimization(
        req.hours,
        req.battery,
        directives
    )

    # 4. Final Replay & Verification (Safety Net)
    replay_and_verify_schedule(
        req.hours,
        req.battery,
        directives,
        hourly_plan,
        total_grid,
        total_cost,
        peak_grid
    )
    
    return OptimizeEnergyResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=directives,
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak_grid,
        plan_summary=plan_summary
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
