# Deployment Information

## Public URL

https://agent-production-0a8aebec.up.railway.app

## Platform

**Railway**. Project `day14-agent` gồm 2 service:

| Service | Nguồn | Vai trò |
|---|---|---|
| `agent` | `railway up` từ thư mục `06-lab-complete/` (Dockerfile multi-stage, `railway.toml`) | FastAPI agent của Part 6, 2 uvicorn worker |
| `Redis` | Railway Redis database (`railway add --database redis`) | Rate limit + cost guard (state dùng chung, còn sau khi restart / redeploy) |

Config deploy: [`06-lab-complete/railway.toml`](06-lab-complete/railway.toml) với `builder = "DOCKERFILE"` và `healthcheckPath = "/health"`. Railway cấp biến `PORT` (thực tế là `8080`), và `CMD` trong Dockerfile lắng nghe theo `${PORT:-8000}`.

### Cách deploy lại

```bash
cd 06-lab-complete
railway link            # chọn project day14-agent, service agent (chỉ cần 1 lần)
railway up --service agent --ci
```

## Test Commands

Đặt biến (key lấy từ Railway dashboard → service `agent` → Variables, **không** ghi vào repo):

```bash
BASE=https://agent-production-0a8aebec.up.railway.app
AGENT_API_KEY=<key đã đặt trên Railway>
```

### 4 lệnh tự kiểm (CP6): chỉ in mã HTTP, không lộ key

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả chạy ngày 2026-11-25:

```text
200
200
401
200
```

### Health check

```bash
curl https://agent-production-0a8aebec.up.railway.app/health
# {"status":"ok","environment":"production"}
```

### API test (có authentication)

```bash
curl -X POST https://agent-production-0a8aebec.up.railway.app/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":3,"output_tokens":16,"cost_usd":1.005e-05}}
```

### Rate limit trên cloud (10 request/phút, state trong Redis của Railway)

```text
200 200 200 200 200 200 200 200 429 429      # 2 request trước đó + 10 request này
HTTP/2 429
retry-after: 45
{"detail":{"error":"Rate limit exceeded","limit_per_minute":10,"retry_after_seconds":45}}
```

### Script chấm với public URL

```text
$ python check_production_ready.py --public-url https://agent-production-0a8aebec.up.railway.app
✅ 12. Public URL ready (Railway / Render config)
     ✅ Public URL: GET https://agent-production-0a8aebec.up.railway.app/health → 200
```

## Environment Variables Set

Đặt trên Railway (service `agent` → Variables), **không** có trong code hay repo:

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `AGENT_API_KEY` | *(secret, 32 byte ngẫu nhiên từ `secrets.token_urlsafe`)* | Không commit; bản local nằm trong `06-lab-complete/.env` (đã gitignore) |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Reference variable: Railway tự điền URL nội bộ của service Redis |
| `ENVIRONMENT` | `production` | Bật fail-fast nếu thiếu `AGENT_API_KEY`, ẩn `/docs` |
| `RATE_LIMIT_PER_MINUTE` | `10` | |
| `DAILY_BUDGET_USD` | `1.0` | |
| `MONTHLY_BUDGET_USD` | `10.0` | |
| `LOG_LEVEL` | `INFO` | |
| `WEB_CONCURRENCY` | `2` | Số uvicorn worker |
| `PORT` | *(Railway tự cấp: 8080)* | Không tự đặt |

## Logs

Railway nhận ra log JSON của app và tách thành các trường có cấu trúc:

```text
[INFO]  ts="2026-11-25T10:01:12.647487+00:00" logger="agent" event="request" request_id="7c9495d3bfa5" method="POST" path="/ask" status=429 ms=3.8
[INFO]  ts="2026-11-25T10:01:12.952873+00:00" logger="agent" event="request" request_id="54df2e37bdea" method="GET" path="/docs" status=404 ms=0.4
```

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
