"""
Production AI Agent — Part 6 Final Project

Chạy:   uvicorn app.main:app --workers 2
Chấm:   python check_production_ready.py
"""
import json
import logging
import sys
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from app import redis_client
from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging (tiêu chí 10): mỗi dòng log = 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            # json.dumps escape xuống dòng → traceback vẫn nằm trên 1 dòng JSON
            entry["exc"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logger() -> logging.Logger:
    log = logging.getLogger("agent")
    log.setLevel(settings.log_level)
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        log.addHandler(handler)
    log.propagate = False      # không đi qua root logger (tránh bị format lại / in 2 lần)
    return log


logger = _setup_logger()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Lifespan + graceful shutdown (tiêu chí 11)
#   SIGTERM do uvicorn xử lý: ngừng nhận kết nối mới → chờ request đang chạy xong
#   → chạy phần sau `yield` → thoát exit code 0.
#   KHÔNG tự đăng ký signal handler để không đè handler của uvicorn.
# ─────────────────────────────────────────────────────────
_state = {"shutting_down": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("startup", environment=settings.environment, port=settings.port,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd,
              monthly_budget_usd=settings.monthly_budget_usd,
              api_key_configured=bool(settings.agent_api_key),
              redis_connected=redis_healthy())   # chỉ để log, Redis chết vẫn khởi động
    if not settings.agent_api_key:
        log_event("AGENT_API_KEY is not set — every /ask request will be rejected", logging.WARNING)
    yield
    _state["shutting_down"] = True
    log_event("shutdown", reason="SIGTERM / server stop; in-flight requests finished")
    redis_client.close()


app = FastAPI(title="Production AI Agent", version="1.0.0", lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None)


# ─────────────────────────────────────────────────────────
# Middleware: request id + log mỗi request (không log API key / nội dung câu hỏi)
# ─────────────────────────────────────────────────────────
@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        log_event("request", request_id=request_id, method=request.method,
                  path=request.url.path, status=status,
                  ms=round((time.perf_counter() - started) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Error handling: lỗi rõ ràng, KHÔNG lộ stack trace ra client
# ─────────────────────────────────────────────────────────
@app.exception_handler(redis.RedisError)
async def redis_error_handler(request: Request, exc: redis.RedisError):
    log_event("redis_unavailable", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"},
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.error("unhandled_error", exc_info=exc,
                 extra={"fields": {"path": request.url.path, "error": type(exc).__name__}})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    user_id: Annotated[str, StringConstraints(max_length=128)] | None = None


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"service": "production-ai-agent", "environment": settings.environment,
            "endpoints": ["GET /health", "GET /ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness (tiêu chí 4): process còn chạy là 200 — KHÔNG phụ thuộc Redis."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness (tiêu chí 5): 200 khi nhận được traffic; 503 khi mất Redis hoặc đang tắt."""
    if _state["shutting_down"]:
        raise HTTPException(status_code=503, detail="Shutting down")
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="Redis not reachable")
    return {"status": "ready"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    # def thường (không async): mock LLM dùng time.sleep → FastAPI chạy trong threadpool,
    # không chặn event loop.
    check_rate_limit(api_key)                       # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                                  # 402 khi hết budget

    answer = llm_ask(body.question)

    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log_event("llm_call", user_id=body.user_id, question_chars=len(body.question),
              input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=round(cost, 8))
    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8)},
    }
