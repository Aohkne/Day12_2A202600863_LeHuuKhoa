"""
FastAPI server wrapping ShoppingAssistant for Railway deployment.

Endpoints:
  GET  /          → info
  GET  /health    → liveness probe
  GET  /ready     → readiness probe
  POST /ask       → ask the shopping agent (requires X-API-Key)
"""
from __future__ import annotations

import os
import time
import logging
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Security, Request, Response
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ── resolve paths so imports work from project root ──────────────
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (06-lab-complete/.env) BEFORE agent imports
# agent/src/app/config.py also calls load_dotenv(agent/.env) but dotenv
# won't override vars already set — so root .env takes precedence.
_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")

SRC_DIR = _ROOT / "agent" / "src"
sys.path.insert(0, str(SRC_DIR))

from app.graph import ShoppingAssistant  # noqa: E402

# ─────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

START_TIME = time.time()
_is_ready = False
_assistant: ShoppingAssistant | None = None

AGENT_API_KEY = os.getenv("AGENT_API_KEY", "dev-key-change-me")

# ─────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    if not api_key or api_key != AGENT_API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Include header: X-API-Key: <key>",
        )
    return api_key


# ─────────────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _assistant, _is_ready
    logger.info(json.dumps({"event": "startup", "version": "1.0.0"}))
    try:
        _assistant = ShoppingAssistant()
        _is_ready = True
        logger.info(json.dumps({"event": "ready", "provider": _assistant.settings.provider}))
    except Exception as e:
        logger.error(json.dumps({"event": "startup_error", "error": str(e)}))
        raise

    yield

    _is_ready = False
    logger.info(json.dumps({"event": "shutdown"}))


# ─────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="VinShop Shopping Assistant",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if os.getenv("ENVIRONMENT") != "production" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if "server" in response.headers:
        del response.headers["server"]
    return response


# ─────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


class AskResponse(BaseModel):
    question: str
    answer: str
    route: dict
    timestamp: str


# ─────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "app": "VinShop Shopping Assistant",
        "version": "1.0.0",
        "endpoints": {
            "ask": "POST /ask (requires X-API-Key)",
            "health": "GET /health",
            "ready": "GET /ready",
        },
    }


@app.post("/ask", response_model=AskResponse)
async def ask(
    body: AskRequest,
    _key: str = Security(verify_api_key),
):
    if not _assistant:
        raise HTTPException(503, "Agent not initialized")
    try:
        result = _assistant.ask(body.question)
        return AskResponse(
            question=body.question,
            answer=result.get("answer", result.get("response", str(result))),
            route=result.get("route", {}),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        logger.error(json.dumps({"event": "ask_error", "error": str(e)}))
        raise HTTPException(500, f"Agent error: {e}")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
def ready():
    if not _is_ready:
        raise HTTPException(503, "Not ready")
    return {"ready": True}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
