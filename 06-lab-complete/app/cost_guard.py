"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis, theo ngày và theo tháng (UTC)
  • Tổng chi phí đã đạt DAILY_BUDGET_USD hoặc MONTHLY_BUDGET_USD → HTTP 402
  • Key đổi theo ngày/tháng nên budget tự reset, EXPIRE để Redis tự dọn key cũ.

Budget tính cho cả agent (tổng chi phí LLM của service), dùng chung giữa mọi instance.
Lưu ý: chi phí chỉ biết SAU khi gọi LLM, nên các request đang chạy song song có thể làm
tổng vượt budget một chút (tối đa vài request) — request kế tiếp sẽ bị chặn.
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006

DAY_TTL_SECONDS = 2 * 24 * 3600
MONTH_TTL_SECONDS = 32 * 24 * 3600


def _keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return f"cost:daily:{now:%Y-%m-%d}", f"cost:monthly:{now:%Y-%m}"


def current_spend() -> tuple[float, float]:
    daily_key, monthly_key = _keys()
    daily, monthly = r.mget(daily_key, monthly_key)
    return float(daily or 0), float(monthly or 0)


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    daily, monthly = current_spend()
    if daily >= settings.daily_budget_usd:
        raise HTTPException(status_code=402, detail={
            "error": "Daily budget exceeded", "spent_usd": round(daily, 6),
            "budget_usd": settings.daily_budget_usd, "resets": "00:00 UTC"})
    if monthly >= settings.monthly_budget_usd:
        raise HTTPException(status_code=402, detail={
            "error": "Monthly budget exceeded", "spent_usd": round(monthly, 6),
            "budget_usd": settings.monthly_budget_usd, "resets": "first day of month (UTC)"})


def estimate_tokens(text: str) -> int:
    """Ước lượng thô: ~1 token / từ (mock LLM không trả usage thật)."""
    return max(1, len(text.split()))


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    cost = (input_tokens / 1000) * PRICE_INPUT_PER_1K + (output_tokens / 1000) * PRICE_OUTPUT_PER_1K
    daily_key, monthly_key = _keys()
    pipe = r.pipeline(transaction=True)
    pipe.incrbyfloat(daily_key, cost)
    pipe.expire(daily_key, DAY_TTL_SECONDS)
    pipe.incrbyfloat(monthly_key, cost)
    pipe.expire(monthly_key, MONTH_TTL_SECONDS)
    pipe.execute()
    return cost
