# Day 14 Lab — Mission Answers

> Môi trường chạy: macOS (arm64), Python 3.11.18 (venv), Docker 29.9.3, Docker Compose v5.7.1.
> Tất cả output bên dưới là output thật từ máy, chạy ngày 2026-11-25. Token/secret đã được che.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng | Hậu quả |
|---|---|---|---|
| 1 | **Hardcode secret**: `OPENAI_API_KEY = "sk-..."`, `DATABASE_URL` chứa mật khẩu `password123` | 17–18 | Push lên GitHub là lộ key/mật khẩu; muốn đổi key phải sửa code và deploy lại |
| 2 | **Log ra secret**: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 34 | Secret nằm trong log (log thường được gửi sang hệ thống khác, nhiều người đọc được). Đã thấy thật trong log khi chạy, xem Exercise 1.2 |
| 3 | **`print()` thay cho logging có cấu trúc** | 33, 38 | Không có level, timestamp, không parse được để tìm kiếm/cảnh báo; log cả nội dung câu trả lời |
| 4 | **Không có health check** | (thiếu) | `GET /health` trả **404**. Platform không biết app còn sống không để restart |
| 5 | **Bind `host="localhost"`** | 51 | Trong container chỉ nhận kết nối từ chính container, từ ngoài không gọi vào được (cần `0.0.0.0`) |
| 6 | **Port cố định `port=8000`** | 52 | Railway/Render cấp port qua biến `PORT`; port cứng sẽ làm health check của platform thất bại |
| 7 | **`reload=True` và `DEBUG = True` cố định** | 21, 53 | Chế độ reload tốn tài nguyên, theo dõi file; debug bật trong production dễ lộ thông tin |
| 8 | **Không có config management**: `MAX_TOKENS = 500` hằng số trong code | 20–22 | Muốn đổi giữa dev/staging/prod phải sửa code (vi phạm 12-Factor "Config in env") |
| 9 | **Không xử lý shutdown / lifespan** | (thiếu) | Không có chỗ đóng kết nối, dọn tài nguyên khi platform gửi SIGTERM |
| 10 | **Input không validate**: `question: str` qua query string, không giới hạn độ dài | 31 | Có thể gửi chuỗi rất dài, tốn token/tiền; câu hỏi nằm trên URL nên bị ghi vào access log |

### Exercise 1.2: Chạy bản develop

```text
$ cd 01-localhost-vs-production/develop && python app.py

$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
content-type: application/json

{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 422 Unprocessable Entity

{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found

{"detail":"Not Found"}
```

Log server (thấy rõ secret bị in ra):

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Đây là câu trả lời từ AI agent (mock). ...
```

**Nhận xét:** app vẫn chạy được, nhưng chưa sẵn sàng cho production: lộ secret trong log, không có `/health`, chỉ nghe ở `localhost`.

### Exercise 1.3: So sánh develop với production

Chạy bản production (`cp .env.example .env && python app.py`):

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":3.8,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T09:28:33.088753+00:00"}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). ...","model":"gpt-4o-mini"}
```

Thử graceful shutdown: gửi request rồi `kill -TERM <pid>` ngay sau đó:

```text
inflight HTTP 200
{"question":"Long task","answer":"...","model":"gpt-4o-mini"}
process exited

# log server
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
INFO:     Finished server process [16129]
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`DEBUG`, `MAX_TOKENS`, key) | Đọc từ env qua `config.py` (`os.getenv`), có `.env.example` | Một image chạy được ở mọi môi trường; đổi cấu hình không cần sửa code |
| Secrets | `sk-...`, `password123` trong source, còn in ra log | Đọc từ env, không log secret (chỉ log `question_length`) | Tránh lộ key khi push GitHub hoặc khi log bị chuyển ra hệ thống khác |
| Host binding | `localhost` | `0.0.0.0` (`HOST` env) | Trong container phải nghe `0.0.0.0` thì bên ngoài mới gọi vào được |
| Port | Cố định 8000 | `PORT` env | Cloud platform cấp port động qua `PORT` |
| Health check | Không có (`/health` → 404) | `/health` (liveness) + `/ready` (readiness) | Platform/LB biết khi nào restart container, khi nào được đưa traffic vào |
| Logging | `print()` | Logging có level, format JSON | Parse được bằng Loki/Datadog; lọc, cảnh báo theo field |
| Shutdown | Đột ngột, không có lifespan | `lifespan` dọn dẹp + uvicorn xử lý SIGTERM; request đang chạy vẫn trả 200 (đã test) | Deploy hoặc scale down không cắt ngang request của user |
| Reload/Debug | `reload=True` luôn bật | `reload` chỉ bật khi `DEBUG=true` | Production không chạy file watcher, không bật debug |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ cho phép frontend hợp lệ gọi API |
| Input | Query string, không validate | JSON body, `question` rỗng → 422 | Lỗi rõ ràng, câu hỏi không nằm trên URL/access log |

**Quan sát thêm:** ở bản production, các log JSON `startup` / `agent_request` **không hiện ra**. Lý do: `config.py` gọi `logging.warning(...)` lúc import, trước khi `app.py` gọi `logging.basicConfig(...)`. Gọi `logging.warning` trên root logger chưa có handler sẽ tự gọi `basicConfig()` với level mặc định WARNING. Vì vậy `basicConfig` trong `app.py` không còn tác dụng và mọi log INFO bị bỏ. Ngoài ra, format `'{"msg":"%(message)s"}'` bọc một chuỗi JSON vào trong chuỗi JSON khác, nên dòng log **không phải JSON hợp lệ**. Ở Part 6 cần tự viết `JsonFormatter` dùng `json.dumps`.

---

## Part 2: Docker

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11`, bản Python đầy đủ trên Debian (có sẵn gcc, header, nhiều tool). Image to, khoảng 1 GB trở lên.
2. **Working directory:** `/app` (`WORKDIR /app`). Các lệnh `COPY`/`RUN`/`CMD` sau đó chạy trong thư mục này.
3. **Tại sao `COPY requirements.txt` trước:** để tận dụng **layer cache**. Docker cache theo từng layer. Nếu chỉ sửa `app.py` mà `requirements.txt` không đổi, layer `pip install` được dùng lại, build chỉ mất vài giây. Nếu `COPY . .` trước, mỗi lần sửa code đều làm hỏng cache và phải cài lại toàn bộ dependencies.
4. **CMD vs ENTRYPOINT:**
   - `CMD` là lệnh hoặc tham số mặc định, bị **thay thế** khi chạy `docker run image <lệnh khác>`.
   - `ENTRYPOINT` là chương trình chính luôn chạy. Tham số của `docker run` (hoặc `CMD`) được **nối thêm** vào sau, muốn đổi phải dùng `--entrypoint`.
   - Thường dùng `ENTRYPOINT ["uvicorn"]` + `CMD ["app:app", "--port", "8000"]`. Nên dùng exec form (`["..."]`) để process là PID 1 và nhận được SIGTERM.

### Exercise 2.2: Build và chạy bản develop

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d --name p2dev -p 8000:8000 my-agent:develop

$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":4.0,"container":true}

$ docker exec p2dev whoami
root                      # ← bản develop chạy bằng root
```

### Exercise 2.3: Multi-stage build (`02-docker/production/Dockerfile`)

- **Stage 1 (`builder`):** từ `python:3.11-slim`, cài `gcc`, `libpq-dev` (cần để compile một số package), rồi `pip install --user` vào `/root/.local`. Stage này **không được deploy**.
- **Stage 2 (`runtime`):** từ `python:3.11-slim` mới. Tạo user `appuser` (non-root), chỉ `COPY --from=builder /root/.local` (packages đã cài) cùng `main.py` và `utils/`. Có `HEALTHCHECK`, chạy bằng `USER appuser`, CMD exec form với uvicorn `--workers 2`.
- **Tại sao nhỏ hơn:** image cuối không chứa gcc, header, apt cache, pip cache, vốn chỉ cần lúc build. Base `slim` cũng nhỏ hơn nhiều so với `python:3.11` đầy đủ.

```text
$ docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .
$ docker images my-agent
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   65464ed75ae8        265MB         57.5MB
my-agent:develop    f9ea2587949b       1.67GB          415MB

$ docker image inspect --format '{{.RepoTags}} {{.Size}}' my-agent:develop my-agent:advanced
[my-agent:develop] 1672157417
[my-agent:advanced] 265493197

$ docker run --rm --entrypoint whoami my-agent:advanced
appuser
```

**Image size comparison** (đo bằng `docker image inspect`, đơn vị MB = 10^6 byte như `docker images`):

- Develop: **1672 MB** (1.67 GB)
- Production: **265 MB**
- Difference: **giảm 84.1%** (`(1672.2 − 265.5) / 1672.2`). Production chỉ bằng khoảng 1/6.3 bản develop.
- Nếu tính dung lượng nén khi pull/push (cột CONTENT SIZE): 415 MB → 57.5 MB, giảm 86.1%.

### Exercise 2.4: Docker Compose stack (`02-docker/production/docker-compose.yml`)

```text
$ cd 02-docker/production && touch .env.local
$ docker compose -p k5-day14-docker up -d --build
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 15 seconds (healthy)   8000/tcp
nginx     nginx:alpine            Up 15 seconds             0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 26 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 26 seconds (healthy)   6379/tcp
```

Kiến trúc: 4 service, tất cả nằm trong network bridge `internal`. Chỉ Nginx mở port ra máy host.

```text
                 ┌────────────────────── network "internal" (bridge) ─────────────────────┐
  Client ──:80──▶│  nginx (nginx:alpine)                                                  │
  (host)         │   reverse proxy, rate limit 10r/s/IP (burst 20), security headers      │
                 │        │ proxy_pass http://agent_backend  (upstream agent:8000)        │
                 │        ▼                                                               │
                 │  agent (build multi-stage, uvicorn --workers 2, :8000, không mở port)  │
                 │        │ REDIS_URL=redis://redis:6379/0   QDRANT_URL=http://qdrant:6333 │
                 │        ├──────────────▶ redis:7-alpine  (:6379, volume redis_data)     │
                 │        └──────────────▶ qdrant v1.9.0   (:6333, volume qdrant_data)    │
                 └────────────────────────────────────────────────────────────────────────┘
  Thứ tự khởi động: redis + qdrant healthy  →  agent (depends_on: service_healthy)  →  nginx
```

- Các service gọi nhau bằng **tên service**, Docker DNS nội bộ phân giải `agent`, `redis`, `qdrant` ra IP container.
- Agent không publish port, nên mọi traffic phải đi qua Nginx.
- Lưu ý: `main.py` của bản demo này chỉ nhận `REDIS_URL`/`QDRANT_URL` qua env, **chưa thật sự dùng** Redis hay Qdrant.

Test qua Nginx:

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":20.9,"version":"2.0.0","timestamp":"2026-11-25T09:31:06.521029"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

# 40 request song song qua Nginx (limit 10r/s, burst 20):
429 ×19, 200 ×21
```

Header `Server: nginx` (không lộ phiên bản nhờ `server_tokens off`) và các security header chứng tỏ request đi qua Nginx. Khi gửi 40 request song song, có 21 request lọt qua (1 + burst 20) và 19 request bị Nginx trả 429.

Debug container: `docker compose -p k5-day14-docker logs agent` (log mỗi request) và `docker exec -it <container> sh`. Xong thì dọn stack bằng `docker compose -p k5-day14-docker down -v`.

---

## Part 3: Cloud Deployment

> Theo CHECKPOINTS (K5): deploy luôn agent Part 6 (`06-lab-complete/`), không deploy app mẫu `03-cloud-deployment/`. Chi tiết trong [DEPLOYMENT.md](DEPLOYMENT.md).

### Exercise 3.1: Railway deployment (nền tảng chính)

- **URL:** https://agent-production-0a8aebec.up.railway.app
- **Screenshot:** [dashboard](screenshots/dashboard.png) · [running](screenshots/running.png) · [test](screenshots/test.png)

Các bước đã làm (trong `06-lab-complete/`):

```bash
npm i -g @railway/cli && railway login
railway init --name day14-agent
railway add --database redis                         # service "Redis"
railway add --service agent \
  --variables 'REDIS_URL=${{Redis.REDIS_URL}}' --variables "AGENT_API_KEY=<secret>" \
  --variables ENVIRONMENT=production --variables RATE_LIMIT_PER_MINUTE=10 \
  --variables DAILY_BUDGET_USD=1.0 --variables MONTHLY_BUDGET_USD=10.0
railway up --service agent --ci                       # build bằng Dockerfile (railway.toml)
railway domain --service agent
```

Kết quả 4 lệnh tự kiểm của CP6 (`/health`, `/ready`, `/ask` không key, `/ask` có key):

```text
200
200
401
200
```

`check_production_ready.py --public-url ...` báo `✅ Public URL: GET https://agent-production-0a8aebec.up.railway.app/health → 200`. Trên cloud, gửi 12 request liên tiếp thì từ request thứ 11 bị **429** (`retry-after: 45`), tức rate limit đang dùng Redis của Railway.

`.railwayignore` loại `.venv/`, `.env`, `__pycache__/`, `benchmark-observability/` để `railway up` không upload secret hay 53 MB venv.

### So sánh `railway.toml` với `render.yaml` (nền tảng còn lại)

Hai file đều nằm trong `06-lab-complete/` và cùng build từ một `Dockerfile`.

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Phạm vi | Chỉ mô tả **một service** (cách build và deploy service hiện tại) | **Blueprint cho cả hệ thống**: web service + Key Value (Redis) trong một file | Railway tạo các service khác (Redis) bằng CLI/dashboard rồi nối bằng reference variable. Render muốn mô tả toàn bộ hạ tầng trong repo (IaC) |
| Build | `[build] builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `rootDir: 06-lab-complete`, `dockerfilePath` | Render đọc blueprint từ repo GitHub nên phải chỉ rõ thư mục con. Railway build đúng thư mục đã `railway up` |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout = 60` | `healthCheckPath: /health` | Cùng ý tưởng: platform chỉ chuyển traffic sang bản deploy mới khi `/health` trả 200 (zero-downtime deploy) |
| Restart | `restartPolicyType = "ON_FAILURE"`, `MaxRetries = 3` | Không khai báo (Render tự restart service web) | Railway cho chỉnh chính sách restart. Render quản lý mặc định |
| Secret | **Không có trong file**. Đặt bằng `railway variables` / dashboard | Có khai báo **tên** biến nhưng không có giá trị: `AGENT_API_KEY` dùng `sync: false` (nhập tay trên dashboard) | Render cần biết blueprint cần những biến nào. `sync: false` (hoặc `generateValue: true`) giúp không ghi giá trị vào git |
| Redis | `REDIS_URL=${{Redis.REDIS_URL}}` (reference variable đặt trên Railway) | `fromService: {type: keyvalue, name: day14-redis, property: connectionString}` | Cả hai đều lấy URL nội bộ do platform sinh, không hard-code |
| Port | Không khai báo: Railway cấp `PORT`, app đọc `${PORT}` | Không khai báo: Render cũng cấp `PORT` | Vì vậy `CMD` trong Dockerfile phải đọc `PORT` thay vì cố định 8000 |
| Gói | Trial / Hobby theo tài khoản | `plan: free` khai báo trong file | |

Tóm lại: `railway.toml` là **config build/deploy của một service** (Railway gọi là config-as-code; CLI 5.75 đã báo chuyển dần sang `.railway/railway.ts`, file `.toml` vẫn chạy tới 2026-12-01). `render.yaml` là **blueprint IaC của cả stack**, tạo luôn Redis và nối biến giữa các service.

### Exercise 3.3: Pipeline Cloud Run (đọc `cloudbuild.yaml` + `service.yaml`)

1. **Trigger:** push lên `main` khiến Cloud Build chạy `cloudbuild.yaml` (máy `E2_MEDIUM`, timeout 20 phút, log lên Cloud Logging).
2. **`test`:** trong container `python:3.11-slim`, cài `requirements.txt` + `pytest`, chạy `pytest tests/`. Test fail thì pipeline dừng, không build.
3. **`build` → `push`:** `docker build` với 2 tag `ai-agent:$COMMIT_SHA` và `:latest` (dùng `--cache-from :latest` để tái sử dụng layer), rồi `docker push --all-tags` lên Container Registry `gcr.io/$PROJECT_ID`.
4. **`deploy`:** `gcloud run deploy ai-agent --image ...:$COMMIT_SHA` ở `asia-southeast1`, public (`--allow-unauthenticated`), 1–10 instance (min 1 để tránh cold start), 512Mi/1 CPU, timeout 60s. `OPENAI_API_KEY` lấy từ **Secret Manager** (`--set-secrets`). Deploy theo SHA nên rollback được về đúng commit cũ.
5. **`service.yaml`** mô tả cùng service dưới dạng IaC (Knative): `containerConcurrency: 80`, autoscale theo target 80 request/instance, secret `OPENAI_API_KEY` và `AGENT_API_KEY` từ Secret Manager, `livenessProbe` gọi `/health` mỗi 30s, `startupProbe` gọi `/ready` (tối đa 10 lần × 3s) trước khi nhận traffic. Áp dụng bằng `gcloud run services replace service.yaml`.

---

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop/app.py`)

- **Key được check ở đâu:** hàm dependency `verify_api_key` (dòng 39–54) đọc header `X-API-Key` qua `APIKeyHeader(name="X-API-Key", auto_error=False)` (dòng 36), rồi gắn vào endpoint bằng `_key: str = Depends(verify_api_key)` (dòng 70). Key đúng được đọc từ env `AGENT_API_KEY` (dòng 35).
- **Sai key thì sao:** thiếu header trả **401** "Missing API key", sai key trả **403** "Invalid API key". Request bị chặn trước khi gọi LLM. (Ở Part 6, script chấm yêu cầu cả hai trường hợp đều trả **401**.)
- **Rotate key:** key nằm trong env nên chỉ cần đặt `AGENT_API_KEY` mới trên platform rồi restart hoặc redeploy, không phải sửa code. Muốn rotate **không downtime**: cho phép nhiều key cùng lúc (ví dụ `AGENT_API_KEYS=old,new`), chuyển client sang key mới, rồi xoá key cũ.

Chạy bằng `AGENT_API_KEY=secret-key-123 python app.py`:

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

### Exercise 4.2: JWT authentication (`04-api-gateway/production/`)

**JWT flow** (`auth.py`):
1. Client gửi `POST /auth/token` với `username`/`password`. `authenticate_user` kiểm tra với `DEMO_USERS`.
2. Server tạo token bằng `jwt.encode` với payload `{sub, role, iat, exp = now + 60 phút}`, ký HS256 bằng `JWT_SECRET` (env).
3. Client gửi `Authorization: Bearer <token>` ở mỗi request.
4. `verify_token` gọi `jwt.decode` để kiểm tra chữ ký và hạn, sau đó lấy `username`/`role` ra **mà không cần tra database**. Đây là kiểu auth stateless.
   Thiếu token trả 401, token hết hạn trả 401, token sai chữ ký trả 403.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***MASKED***"}

$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"wrong"}'
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","usage":{"requests_remaining":9,"budget_remaining_usd":1.6e-05}}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer invalid.token.here" -H "Content-Type: application/json" -d '{"question":"x"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}
```

### Exercise 4.3: Rate limiting (`rate_limiter.py`)

- **Thuật toán:** **sliding window**. Mỗi user có một `deque` chứa timestamp các request. Mỗi lần check, bỏ các timestamp cũ hơn 60 giây, rồi đếm số còn lại. Comment trong code ghi "Sliding Window Counter", nhưng thực chất đây là **sliding window log** vì nó lưu từng timestamp chứ không chỉ lưu bộ đếm.
- **Limit:** user thường **10 request / 60 giây**, admin **100 request / 60 giây**.
- **Bypass cho admin:** `app.py` dòng 141 chọn `rate_limiter_admin` nếu `role == "admin"` trong JWT (tài khoản `teacher`), nên admin có limit cao hơn chứ không bị bỏ hẳn. Muốn bỏ hẳn thì bỏ qua `limiter.check()` khi role là admin.
- **Hạn chế:** state nằm trong RAM của process, nên mất khi restart và không dùng chung giữa nhiều worker hay instance. Đây là lý do Part 6 yêu cầu lưu trong Redis.

Vòng lặp 20 request với token `student` (đã dùng 1 request "Explain JWT" ở trên):

```text
$ for i in {1..20}; do curl -s -o /dev/null -w "req $i -> %{http_code}\n" -X POST http://localhost:8000/ask \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\":\"Test $i\"}"; done
req 1 -> 200
req 2 -> 200
req 3 -> 200
req 4 -> 200
req 5 -> 200
req 6 -> 200
req 7 -> 200
req 8 -> 200
req 9 -> 200
req 10 -> 429
req 11 -> 429
req 12 -> 429
req 13 -> 429
req 14 -> 429
req 15 -> 429
req 16 -> 429
req 17 -> 429
req 18 -> 429
req 19 -> 429
req 20 -> 429

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"one more"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795599172
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

Chỉ có 9 request trong vòng lặp được 200, vì request "Explain JWT" trước đó đã chiếm 1 trong 10 suất. Từ request thứ 10 trở đi bị **429**, kèm header `Retry-After: 59`.

### Exercise 4.4: Cost guard

**Bản có sẵn (`cost_guard.py`):**
- `check_budget()` được gọi **trước** khi gọi LLM. Chi phí của user trong ngày `>= $1` thì trả **402**. Tổng chi phí toàn hệ thống `>= $10/ngày` thì trả **503**. Dùng tới 80% thì log cảnh báo.
- `record_usage()` được gọi **sau** khi gọi LLM, cộng token theo giá gpt-4o-mini ($0.15/1M input, $0.60/1M output).
- Hạn chế: lưu trong RAM, nên restart là mất và nhiều instance không cộng dồn chung.

Đo thật sau 10 request thành công của `student`:

```text
$ curl -i http://localhost:8000/me/usage -H "Authorization: Bearer $TOKEN"
HTTP/1.1 200 OK
{"user_id":"student","date":"2026-11-25","requests":10,"input_tokens":40,"output_tokens":308,"cost_usd":0.000191,"budget_usd":1.0,"budget_remaining_usd":0.999809,"budget_used_pct":0.0}
```

Trung bình khoảng $0.0000191 mỗi request, nên budget $1/ngày tương đương khoảng 52.000 request mock. *(Lỗi nhỏ trong code mẫu: field `budget_remaining_usd` trong response của `/ask` thực ra trả về **số đã dùng** `usage.total_cost_usd`, không phải số còn lại.)*

**Cách mình implement `check_budget` theo đề ($10/tháng/user, lưu trong Redis, tự reset đầu tháng):**

```python
import os
from datetime import datetime, timezone
import redis

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
MONTHLY_BUDGET_USD = 10.0

# Lua chạy atomic trong Redis: đọc + so sánh + cộng trong MỘT bước
_LUA = """
local cur = tonumber(redis.call('GET', KEYS[1]) or '0')
local add = tonumber(ARGV[1])
if cur + add > tonumber(ARGV[2]) then return 0 end
redis.call('INCRBYFLOAT', KEYS[1], add)
redis.call('EXPIRE', KEYS[1], 32*24*3600)
return 1
"""
_reserve = r.register_script(_LUA)

def check_budget(user_id: str, estimated_cost: float) -> bool:
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    key = f"budget:{user_id}:{month_key}"
    return bool(_reserve(keys=[key], args=[estimated_cost, MONTHLY_BUDGET_USD]))
```

Giải thích cách làm:
- **Key theo user và theo tháng** (`budget:<user>:2026-11`): sang tháng mới thì key đổi, nên budget **tự reset** mà không cần cron. `EXPIRE` 32 ngày để Redis tự xoá key cũ.
- **Lưu trong Redis**: mọi instance cộng dồn vào cùng một key, và restart không mất số liệu.
- **Atomic bằng Lua**: nếu làm `GET` rồi mới `INCRBYFLOAT` thành 2 lệnh riêng, hai request song song có thể cùng đọc thấy "còn tiền" và cùng lọt qua. Script Lua chạy trọn vẹn trong Redis nên không bị race condition.
- Vượt budget thì hàm trả `False`, endpoint trả **402 Payment Required**.

Chạy thử với Redis thật (`redis:7-alpine`):

```text
5 lần check_budget('alice', 3.0): [True, True, True, False, False]
spent = 9 | TTL (ngày) = 32.0
check_budget('bob', 3.0): True (user khác có budget riêng)
```

3 lần đầu cộng lên $9. Lần thứ 4 sẽ thành $12 > $10 nên bị từ chối, và số đã tiêu vẫn giữ ở $9 (không cộng nhầm phần bị từ chối).

**Tại sao cần cả rate limit và cost guard:**
- Rate limit chặn **tốc độ**: số request mỗi phút, chống spam/DoS.
- Cost guard chặn **tổng tiền**: kể cả user gọi chậm, đều đặn dưới rate limit, hoặc gửi câu hỏi rất dài tốn nhiều token, thì tổng chi phí vẫn có trần.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/app.py`)

- **`/health` (liveness)** trả lời câu hỏi "process còn sống không?". Trả 200 kèm uptime, version, tình trạng memory. Nếu endpoint này fail hoặc timeout, platform **restart** container. Endpoint này không nên phụ thuộc Redis hay DB: nếu Redis chết mà `/health` cũng fail, platform sẽ restart agent liên tục mà vẫn không chữa được lỗi.
- **`/ready` (readiness)** trả lời câu hỏi "có nên đưa traffic vào chưa?". Trả 503 khi đang khởi động, đang shutdown, hoặc dependency (Redis/DB) chưa kết nối được. Load balancer chỉ **ngừng gửi traffic** tới instance đó, chứ không restart nó.

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":3.7,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T09:32:29.032389+00:00","checks":{"memory":{"status":"ok","used_percent":85.0}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

(`in_flight_requests` là 1 vì middleware đếm luôn chính request `/ready` đang chạy.)

### Exercise 5.2: Graceful shutdown

Cơ chế trong code:
1. Middleware `track_requests` đếm số request đang xử lý (`_in_flight_requests`).
2. Khi nhận SIGTERM, uvicorn **ngừng nhận kết nối mới** và chờ các request đang chạy xong (`timeout_graceful_shutdown=30`).
3. Sau đó uvicorn chạy phần shutdown của `lifespan`: đặt `_is_ready = False` (để `/ready` trả 503), chờ tới khi `_in_flight_requests == 0` (tối đa 30 giây), rồi đóng kết nối.
4. Process thoát bình thường.

```text
$ python app.py & PID=16551
$ curl -X POST "http://localhost:8000/ask?question=Long%20task" &     # request đang chạy
$ kill -TERM $PID

{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
[in-flight request] HTTP 200
→ process tự thoát sau khoảng 3 giây

# log server
INFO:     Shutting down
INFO:     127.0.0.1:60033 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 16:32:32,197 INFO 🔄 Graceful shutdown initiated...
2026-11-25 16:32:32,198 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [16551]
2026-11-25 16:32:32,199 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Kết quả:** request gửi ngay trước SIGTERM vẫn hoàn thành với **200**, và process tự thoát.

**Quan sát:** dòng `Received signal 15` của handler tự viết lại xuất hiện **cuối cùng**. Lý do: khi đang chạy, uvicorn tạm thay handler SIGTERM của app bằng handler của chính nó. Sau khi shutdown xong, uvicorn trả lại handler cũ và phát lại tín hiệu. Vì handler tự viết chỉ ghi log và không chặn, process vẫn thoát được. Nếu tự viết handler mà nuốt mất SIGTERM hoặc không gọi lại handler của uvicorn, process sẽ không bao giờ tắt. README Part 6 có cảnh báo đúng điều này.

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/app.py`)

- **Anti-pattern:** giữ `conversation_history = {}` trong RAM. Mỗi instance có vùng nhớ riêng, nên request 1 của user A vào instance 1 và request 2 vào instance 2 sẽ thấy history trống. Restart hoặc scale down cũng làm mất toàn bộ history.
- **Bản production:** session được lưu trong Redis (`save_session` dùng `SETEX session:<id>` với TTL 3600 giây, `load_session` dùng `GET`). Bất kỳ instance nào cũng đọc và ghi cùng một session. Response có field `served_by` để thấy instance nào xử lý request.
- **Điểm cần lưu ý trong code mẫu:**
  - Nếu lúc import không ping được Redis, app **âm thầm chuyển sang dùng dict trong RAM** (`USE_REDIS = False`). Hệ thống vẫn chạy nhưng không còn scale được.
  - Code cũng ping Redis ngay lúc import, nên Redis chưa lên là app quyết định sai ngay từ đầu.
  - Production nên báo `/ready` 503 thay vì fallback như vậy. Đây cũng là yêu cầu của Part 6.

### Exercise 5.4: Load balancing

```text
$ cd 05-scaling-reliability/production && touch .env.local
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
$ docker compose -p k5-day14-scaling ps
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 12 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 12 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 12 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 12 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 23 seconds (healthy)   6379/tcp

# 10 request POST /chat qua Nginx, đếm theo served_by:
   3 instance-454cc9 redis
   4 instance-9a9f44 redis
   3 instance-a1f347 redis
```

- Nginx dùng `upstream agent_cluster { server agent:8000; }`. Docker DNS phân giải tên `agent` ra IP của cả 3 container, và Nginx **round-robin** giữa chúng. 10 request được chia gần đều 3/4/3.
- `proxy_next_upstream error timeout http_503` cùng `proxy_next_upstream_tries 3`: instance nào lỗi hoặc trả 503 thì Nginx tự thử instance khác.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
============================================================
Stateless Scaling Demo
============================================================

Session ID: 6ebe6433-6552-4cb7-a2a7-f5b3b6d34c3a

Request 1: [instance-454cc9]
  Q: What is Docker?
Request 2: [instance-a1f347]
  Q: Why do we need containers?
Request 3: [instance-9a9f44]
  Q: What is Kubernetes?
Request 4: [instance-454cc9]
  Q: How does load balancing work?
Request 5: [instance-a1f347]
  Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-454cc9', 'instance-9a9f44', 'instance-a1f347'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ... (đủ 5 cặp user/assistant)

✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu hỏi trong **cùng một session** được xử lý bởi **cả 3 instance** khác nhau, theo thứ tự xoay vòng 454cc9 → a1f347 → 9a9f44 → 454cc9 → a1f347. Dù vậy history vẫn đủ 10 tin nhắn (5 user + 5 assistant) theo đúng thứ tự. Lý do là không instance nào giữ state riêng: mỗi request đều đọc và ghi session trong Redis dùng chung.

**Thí nghiệm thêm: dừng 1 instance giữa chừng** (script không tự kill instance nên mình làm tay):

```text
$ docker stop k5-day14-scaling-agent-1
$ # 6 request tiếp theo, cùng session_id=6ebe6433-...
turn=7 served_by=instance-a1f347 HTTP 200
turn=8 served_by=instance-a1f347 HTTP 200
turn=9 served_by=instance-9a9f44 HTTP 200
turn=10 served_by=instance-a1f347 HTTP 200
turn=11 served_by=instance-9a9f44 HTTP 200
turn=11 served_by=instance-a1f347 HTTP 200
$ curl http://localhost:8080/chat/$SID/history  → count = 20

$ docker compose -p k5-day14-scaling logs agent | grep "POST /chat" | cut -d"|" -f1 | sort | uniq -c
   5 agent-1
   9 agent-2
   7 agent-3
```

- Sau khi agent-1 (`instance-454cc9`) bị dừng, **cả 6 request đều trả 200**. Nginx chuyển sang 2 instance còn lại nhờ `proxy_next_upstream`, và session vẫn tiếp tục vì nằm trong Redis.
- Hai lỗi nhỏ của code mẫu lộ ra trong thí nghiệm này:
  1. `turn` bị **lệch 1**: request thứ 6 của session báo `turn=7`. Lý do là history được đọc *sau khi* đã thêm câu hỏi hiện tại, rồi lại cộng thêm 1.
  2. History bị **giới hạn ở 20 tin nhắn** (`history[-20:]`), nên `count` dừng ở 20 thay vì 22 và `turn` đứng yên ở 11 cho hai request cuối.

Dọn stack: `docker compose -p k5-day14-scaling down`.

---

## Part 6: Final Project

### Output `python check_production_ready.py` (chạy đầy đủ, có Docker, trong `06-lab-complete/`)

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số dòng chi tiết từ cùng lần chạy: image **297 MB** (build 14 giây); `docker stop` thoát với **exit code 0 sau 0.6 giây**; rate limit trả **429 sau 5 request thành công** (`RATE_LIMIT_PER_MINUTE=5`, chạy 2 worker dùng chung Redis); restart agent vẫn bị 429; Redis mất kết nối thì `/ready` trả 503 còn `/health` vẫn 200; 17 dòng log đều là JSON hợp lệ.

### Thiết kế từng phần (`06-lab-complete/app/`)

| File | Làm gì | Điểm chính |
|---|---|---|
| `config.py` | Đọc toàn bộ cấu hình từ env bằng `field(default_factory=lambda: os.getenv(...))` | `AGENT_API_KEY` mặc định rỗng; `ENVIRONMENT=production` mà không có key thì không cho khởi động (fail fast) |
| `redis_client.py` | `redis.from_url(..., socket_connect_timeout=1, socket_timeout=2)` | `from_url` không mở kết nối ngay, nên app khởi động được khi chưa có Redis và tự kết nối lại khi Redis lên. `redis_healthy()` gọi `PING` |
| `auth.py` | `APIKeyHeader("X-API-Key", auto_error=False)` | Thiếu hoặc sai key đều trả **401**. So sánh bằng `secrets.compare_digest`. Key chưa cấu hình thì từ chối hết (fail closed) |
| `rate_limiter.py` | Sliding window log trong Redis sorted set, key là hash của API key | `ZREMRANGEBYSCORE → ZCARD → ZADD → PEXPIRE` chạy trong **một Lua script**, nên atomic giữa các worker. Vượt giới hạn trả **429** kèm `Retry-After` tính từ request cũ nhất trong cửa sổ |
| `cost_guard.py` | Cộng dồn chi phí vào `cost:daily:YYYY-MM-DD` và `cost:monthly:YYYY-MM` | `check_budget()` được gọi trước LLM, vượt ngày hoặc tháng thì trả **402**. `record_cost()` được gọi sau LLM bằng `INCRBYFLOAT` trong transaction. Key đổi theo ngày/tháng nên tự reset, có `EXPIRE` để Redis tự dọn |
| `main.py` | `JsonFormatter` (`json.dumps`), middleware log mỗi request, `/health`, `/ready`, `/ask`, `lifespan` | Logger `agent` riêng với `propagate=False`. Không log API key hay nội dung câu hỏi. Lỗi Redis trả 503, lỗi khác trả 500 với thông báo chung, **không lộ stack trace** (traceback chỉ nằm trong log JSON) |
| `Dockerfile` | Builder tạo `/opt/venv`, runtime `python:3.11-slim` chỉ copy venv + `app/` + `utils/` | User `app` (uid 10001), `HEALTHCHECK` bằng Python. `CMD sh -c "exec uvicorn ... --port ${PORT:-8000}"`: nhờ `exec`, uvicorn là PID 1 và nhận SIGTERM trực tiếp |
| `docker-compose.yml` | `agent` (build) + `redis:7-alpine` (healthcheck, volume) | `REDIS_URL=redis://redis:6379/0`, `depends_on: condition: service_healthy`, `.env` được đọc với `required: false` |
| `railway.toml` / `render.yaml` | Builder `DOCKERFILE` + `healthcheckPath = "/health"` / web Docker + Key Value (Redis) | Secret không ghi trong file: Railway đặt bằng biến, Render dùng `sync: false`; `REDIS_URL` lấy `fromService` |

**Vì sao `/health` phải trả 200 khi Redis chết, còn `/ready` phải trả 503:**
- `/health` là liveness. Nếu nó fail theo Redis, platform sẽ restart agent liên tục. Restart không chữa được lỗi Redis mà còn làm mất các request đang chạy.
- `/ready` là readiness. Nó trả 503 để load balancer **ngừng gửi traffic** vào instance chưa phục vụ được, đến khi Redis quay lại thì tự trả 200. Đã test: dừng Redis thì `/ready` trả 503, bật lại thì trả 200 mà không cần restart agent.

**Graceful shutdown:** không tự đăng ký handler SIGTERM. Uvicorn tự ngừng nhận kết nối mới, chờ request đang chạy xong (`--timeout-graceful-shutdown 8`, nhỏ hơn mức 10 giây của `docker stop`), rồi chạy phần sau `yield` trong `lifespan`: đặt `/ready` thành 503, log `shutdown`, đóng Redis.

### Test thủ công qua `docker compose` (ngoài script chấm)

```text
--- input sai (không lộ stack trace)
{"detail":[{"type":"string_too_short","loc":["body","question"],"msg":"String should have at least 1 character",...}]} 422
{"detail":[{"type":"json_invalid","loc":["body",0],"msg":"JSON decode error",...}]} 422

--- rate limit 10/phút (1 request trước đó + 11 request)
200 200 200 200 200 200 200 200 200 429 429
HTTP/1.1 429 Too Many Requests
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit_per_minute":10,"retry_after_seconds":59}}

--- docker compose stop redis
GET /health → 200 {"status":"ok",...}
GET /ready  → 503 {"detail":"Redis not reachable"}
POST /ask   → 503 {"detail":"Service temporarily unavailable"}
--- docker compose start redis
GET /ready  → 200 {"status":"ready"}   (tự kết nối lại, không cần restart agent)

--- cost guard, Redis mới, DAILY_BUDGET_USD=0.00003
cost=1.005e-05 → 200
cost=1.005e-05 → 200
cost=1.005e-05 → 200
{"detail":{"error":"Daily budget exceeded","spent_usd":3e-05,"budget_usd":3e-05,"resets":"00:00 UTC"}} → 402

--- log JSON (mỗi dòng 1 object)
{"ts": "2026-11-25T09:44:24.133245+00:00", "lvl": "ERROR", "logger": "agent", "event": "redis_unavailable", "path": "/ask", "error": "ConnectionError"}
{"ts": "2026-11-25T09:44:24.133404+00:00", "lvl": "INFO", "logger": "agent", "event": "request", "request_id": "fdf2b0669157", "method": "POST", "path": "/ask", "status": 503, "ms": 4.1}

--- docker compose stop agent → 0.77 giây, log của cả 2 worker:
{"ts": "2026-11-25T09:44:53.202214+00:00", "lvl": "INFO", "logger": "agent", "event": "shutdown", "reason": "SIGTERM / server stop; in-flight requests finished"}
```

---

## AI log

| # | Hỏi AI gì | Nhận được gì | Mình tự kiểm tra / sửa thế nào |
|---|---|---|---|
| 1 | Nhờ Claude Code (Opus 5.5) đọc source repo và lập plan làm bài theo RUBRIC / CHECKPOINTS | Plan theo từng CP, các điểm script chấm kiểm tra ở Part 6 | Đối chiếu với `RUBRIC.md`, `CHECKPOINTS.md` và code `check_production_ready.py` |
| 2 | Nhờ Claude Code chạy các ví dụ Part 1, 2, 4, 5 và ghi output vào file này | Lệnh đã chạy trên máy mình kèm output thật (curl, `docker images`, `test_stateless.py`), cùng bản nháp câu trả lời | Mọi con số (1672 MB / 265 MB, 401/403/200/429, phân bố 3/4/3...) lấy từ output lệnh chạy trên máy, không lấy từ README. Mình đọc lại code từng dòng được trích dẫn để giải thích được khi Lab Coach hỏi |
| 3 | Nhờ viết `check_budget` dùng Redis cho Exercise 4.4 | Bản dùng Lua script để tránh race condition | Chạy thử với Redis `redis:7-alpine` thật: 3 lần đầu được, lần 4–5 bị chặn, TTL 32 ngày, user khác không ảnh hưởng |
| 4 | Nhờ Claude Code hoàn thành các TODO Part 6 trong `06-lab-complete/` (app/*.py, Dockerfile, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml`) | Code đầy đủ 12 tiêu chí; lần đầu chạy script chấm đã PASS 12/12 | Đọc `check_production_ready.py` để hiểu từng check. Chạy `--static-only`, `--skip-docker` rồi bản đầy đủ. Test thêm bằng tay qua `docker compose`: input sai trả 422, dừng/bật Redis, cost guard trên Redis mới, thời gian `docker stop`. Bỏ `railway.toml`/`render.yaml` khỏi `.dockerignore` để chắc platform đọc được config. Đọc lại từng file để giải thích được vì sao dùng Lua (atomic), vì sao `/health` không ping Redis, vì sao CMD có `exec` |
| 5 | Nhờ Claude Code deploy agent Part 6 lên Railway (CP6) và viết `DEPLOYMENT.md`, so sánh `railway.toml`/`render.yaml`, mô tả pipeline Cloud Run | Lệnh `railway init/add/up/domain`, biến môi trường đặt trên Railway, bản nháp so sánh và mô tả pipeline | Tự đăng nhập Railway bằng tài khoản của mình. Chạy 4 lệnh tự kiểm trên URL public (200/200/401/200) và `check_production_ready.py --public-url`. Kiểm tra key không nằm trong file nào của repo (chỉ có trong `.env` đã gitignore). Đối chiếu bảng so sánh và pipeline với nội dung thật của `railway.toml`, `render.yaml`, `cloudbuild.yaml`, `service.yaml` |
| 6 | Nhờ Claude Code làm CP7 (Part 7 Bước 1): thêm metric `agent_question_length_chars`, chạy load test và eval, sửa `KNOWLEDGE_BASE`, viết job CI, điền mục 1–3 của `BENCHMARK_REPORT.md` | Lệnh đã chạy, các file JSON trong `reports/`, 2 entry KB mới (JWT, HPA), `.github/workflows/eval-quality-gate.yml`, bản nháp nhận xét | Mọi số trong `BENCHMARK_REPORT.md` lấy từ JSON (kiểm lại phép tính 7,7×, 1,88×, 11×). Không sửa `golden_dataset.jsonl`. Kiểm tra từ khoá KB mới không trùng câu hỏi khác, eval cuối 23/23, 0 regression. Chạy thử các bước của job CI: v2 cho exit 0, trỏ sang v1 cho exit 1. Ghi rõ 2 điểm bất thường (c=1 trượt p95, c=100 RPS tụt) thay vì bỏ qua |
| 7 | Nhờ Claude Code làm CP8: bật stack observability, benchmark scaling bằng Locust, tìm trace, làm 5 thí nghiệm alert, chụp 4 ảnh, điền mục 4–6 `BENCHMARK_REPORT.md` | Sửa lỗi `PYTHONUSERBASE` trong `production/Dockerfile`; script đo thời gian alert qua API Prometheus; ảnh chụp bằng Chrome headless | Phát hiện `docker compose run locust` tự đưa agent về 2 replica nên bỏ kết quả và chạy lại với `--no-deps`, kiểm tra số container ngay giữa run. Bỏ 1 lần chạy bị khựng (176 s). Số trong báo cáo khớp CSV / output Prometheus. Đối chiếu `trace_id` trong ảnh Jaeger với log JSON. Xem lại từng ảnh trước khi nộp |
