"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • redis.from_url() KHÔNG mở kết nối ngay → app vẫn khởi động được khi Redis chưa sẵn sàng.
    Kết nối thật chỉ được mở ở lệnh Redis đầu tiên (và tự kết nối lại khi Redis quay lại).
  • Timeout ngắn để /ready và /ask không bị treo khi Redis chết.
  • redis_healthy() dùng cho GET /ready.
"""
import redis

from app.config import settings

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=2,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False


def close() -> None:
    try:
        r.close()
    except redis.RedisError:
        pass
