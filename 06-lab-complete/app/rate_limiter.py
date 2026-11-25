"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window log)
  • Vượt giới hạn → HTTP 429 + header Retry-After
  • State lưu trong Redis (sorted set, score = timestamp ms) → mọi worker / instance dùng chung
    và vẫn còn sau khi restart agent.

Cả chuỗi ZREMRANGEBYSCORE → ZCARD → ZADD → PEXPIRE chạy trong MỘT Lua script, nên atomic:
2 worker nhận request cùng lúc không thể cùng "lọt" qua khi chỉ còn 1 suất.
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_MS = 60_000

_SLIDING_WINDOW_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    return {0, count, tonumber(oldest[2])}
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('PEXPIRE', key, window)
return {1, count + 1, 0}
"""
_sliding_window = r.register_script(_SLIDING_WINDOW_LUA)


def _bucket_key(api_key: str) -> str:
    # Không lưu API key thô trong Redis — chỉ lưu hash
    return "ratelimit:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    now_ms = int(time.time() * 1000)
    allowed, count, oldest_ms = _sliding_window(
        keys=[_bucket_key(key)],
        args=[now_ms, WINDOW_MS, limit, f"{now_ms}-{uuid.uuid4().hex[:8]}"],
    )
    if not allowed:
        retry_after = max(1, math.ceil((int(oldest_ms) + WINDOW_MS - now_ms) / 1000))
        raise HTTPException(
            status_code=429,
            detail={"error": "Rate limit exceeded", "limit_per_minute": limit,
                    "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after),
                     "X-RateLimit-Limit": str(limit),
                     "X-RateLimit-Remaining": "0"},
        )
