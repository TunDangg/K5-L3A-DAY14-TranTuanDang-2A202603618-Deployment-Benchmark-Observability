# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON trong `reports/` (bản gốc nằm ở `06-lab-complete/benchmark-observability/develop/reports/`) và CSV trong `benchmark-results/`. Không có số ước lượng.

**Họ tên:** Trần Tuấn Đăng — 2A202603618
**Ngày chạy:** 2026-11-25
**Môi trường:** MacBook Air M2 (Apple M2, 8 CPU, 8 GB RAM), macOS, Python 3.11.18. Agent develop chạy 1 process uvicorn; load generator (`load_test.py`, httpx async) chạy **trên cùng máy**.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500** ms | Một request bình thường mất khoảng 190–220 ms (p50 đo được 190–197 ms ở tải vừa), nên 500 ms cho dư khoảng 2,5 lần để chịu jitter và tải tăng. Đặt theo p95 chứ không theo avg, vì avg bị request chậm kéo lệch nhưng lại che mất trải nghiệm của nhóm người dùng chậm (xem 2.2) |
| Error rate `/ask` | < **2** % | Upstream LLM mock có khoảng 1% lỗi ngẫu nhiên (`MOCK_ERROR_RATE=0.01`), đo được 0,33–1,33%. 2% chừa error budget gấp đôi mức nền, vượt ngưỡng này nghĩa là có sự cố thật chứ không còn là nhiễu |
| Accuracy trên golden dataset | ≥ **85** % **và không regression** | Dùng làm quality gate trong CI. v2 ban đầu đạt 91,3% nên 85% chặn được bản nào tụt rõ rệt. Điều kiện "không regression so với baseline" chặn cả trường hợp accuracy vẫn trên 85% nhưng làm hỏng câu trước đó đúng. Bản cuối đạt 100% |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

Mỗi mức 300 request, `MOCK_SLOW_RATE=0.03` (mặc định). Nguồn: `reports/load_v2_c{1,10,50,100}.json`.

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 3.54 | 194.3 | 1675.5 | 1708.5 | 1.00% (3×503) | ❌ FAIL (p95) |
| 10 | 37.99 | 193.5 | 222.4 | 1703.5 | 0.67% (2×503) | ✅ PASS |
| 50 | 107.91 | 197.2 | 267.9 | 1669.2 | 0.67% (2×503) | ✅ PASS |
| 100 | 50.12 | 646.8 | 4127.3 | 4436.3 | 0.67% (2×503) | ❌ FAIL (p95) |

**Nhận xét:**

- **Từ 1 lên 50 concurrency, RPS tăng gần tuyến tính** (3,5 → 38 → 108 req/s) và p50 gần như không đổi (khoảng 195 ms). Mock LLM là `asyncio.sleep` (chờ I/O), nên trong lúc chờ, event loop vẫn nhận request khác. Tăng concurrency lúc này chỉ là chồng thêm nhiều lượt chờ song song, CPU còn rảnh.
- **Tới 100 concurrency thì hệ thống bão hoà và RPS tụt xuống** còn 50 req/s. p50 tăng hơn 3 lần (197 → 647 ms), p95 lên 4,1 giây. Agent chỉ chạy 1 process Python, nên một event loop trên một core phải gánh cả parse JSON, validate pydantic, ghi 2 dòng log JSON và cập nhật metrics cho mỗi request. Load generator lại chạy cùng máy và tranh CPU với agent. Khi hàng đợi dài ra, thời gian chờ trong hàng đợi cộng thẳng vào latency (định luật Little). Như vậy điểm gãy nằm trong khoảng 50 đến 100 concurrency. Muốn chịu tải cao hơn thì phải tăng số worker hoặc số instance (xem mục 5), tăng concurrency trên một process không giúp được.
- **c=1 trượt SLO dù tải nhẹ nhất.** Ở c=1 không có tranh chấp nào, nên p95 vượt ngưỡng hoàn toàn do request chậm ngẫu nhiên. Với 297 request thành công, p95 rơi vào khoảng request chậm thứ 282. Chỉ cần hơn 5% request bị chậm (khoảng 16 request trở lên) là p95 nhảy lên mức ~1,7 giây, trong khi tỉ lệ kỳ vọng là 3% (khoảng 9 request). Mẫu 300 request đủ nhỏ để độ dao động ngẫu nhiên đẩy p95 qua ranh giới đó. Ở c=10 và c=50 thì p95 vẫn ở 222–268 ms. Kết luận: p95 đặt sát tỉ lệ tail (3% so với 5%) sẽ rất nhạy với nhiễu. Đo SLO nên chạy lâu hơn (vd `--duration 60`) hoặc lặp nhiều lần.
- p99 ở mọi mức đều khoảng 1,7 giây. Đó là các request bị cộng thêm `MOCK_SLOW_MS=1500`, đúng phần tail latency mà mock cố tình tạo ra.

### 2.2 Tail latency

`--requests 300 --concurrency 20`, khởi động lại v2 với từng giá trị. Nguồn: `reports/load_v2_slow_{0,0.03,0.1}.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 190.0 | 214.8 | 219.3 |
| 0.03 (mặc định) | 229.4 | 221.5 | 1687.4 |
| 0.1 | 308.3 | 1678.0 | 1709.1 |

(Thêm: p50 lần lượt là 190.6 / 190.1 / 195.9 ms; RPS 101.2 / 64.11 / 51.8; SLO p95: PASS / PASS / **FAIL**.)

**Nhận xét:**

- **p99 nhạy nhất.** Từ 0 lên 3% request chậm, p99 tăng **7,7 lần** (219 → 1687 ms), trong khi avg chỉ tăng 21% (190 → 229 ms) và p50 gần như không đổi. p99 là "người thứ 99 trong 100", nên chỉ cần hơn 1% request chậm là p99 lộ ra ngay.
- **p95 chỉ đổi khi tỉ lệ chậm vượt 5%.** Ở 3%, p95 vẫn 221 ms. Ở 10%, p95 nhảy lên 1678 ms và trượt SLO 500 ms.
- **avg là chỉ số kém nhất để đặt SLO.** Ở 10% request chậm, avg là 308 ms, vẫn "trông ổn", dưới 500 ms. Nhưng 1 trên 10 người dùng phải chờ khoảng 1,7 giây. Average gộp nhóm nhanh và nhóm chậm thành một con số không ứng với trải nghiệm của ai cả. Nó cũng có thể vẫn đạt SLO trong khi hàng nghìn người dùng chịu latency tệ. SLO đặt theo p95/p99 thì cam kết được "95%/99% request nhanh hơn X".
- RPS giảm theo tỉ lệ chậm (101 → 64 → 52 req/s) vì mỗi request chậm giữ 1 trên 20 slot concurrency thêm khoảng 1,5 giây, làm thông lượng tổng giảm.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json` (baseline là v1), `reports/eval_v2_fixed.json` (baseline là v2, `--min-accuracy 1.0`).

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | **100.0% (23/23)** |
| Avg tokens / câu | 57.0 | 108.8 | 116.6 |
| Tổng cost (USD) | 0.000402 | 0.000757 | 0.000816 |
| Cost / câu đúng (USD) | 0.00040215 | 0.00003607 | 0.00003547 |
| Quality gate | **FAIL** (accuracy ≥ 80%) | **PASS** (≥ 80%, 0 regression so với v1) | **PASS** (≥ 100%, 0 regression so với v2) |

(Latency p50/p95 trong eval: v1 166.8/186.9 ms, v2 189.7/222.0 ms, v2-fixed 193.4/1519.9 ms. p95 của bản fixed cao chỉ vì 2/23 câu (`docker-01` 1700 ms, `config-01` 1664,5 ms) rơi vào request chậm ngẫu nhiên. 23 mẫu quá ít để kết luận về latency, nên latency phải đo bằng load test ở mục 2.)

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | KB không có tài liệu nào khớp nên v2 rơi về câu trả lời chung chung. Đã thêm vào `KNOWLEDGE_BASE` entry có từ khoá `["jwt", "json web token"]`, nội dung so sánh API key (chuỗi cố định, dùng tới khi thu hồi) với JWT (header + payload có `exp` + chữ ký số, server chỉ xác minh chữ ký và hạn dùng, hết hạn thì đăng nhập lại) |
| `scaling-03` ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Thêm entry có từ khoá `["hpa", "horizontal pod autoscaler", "autoscal"]`: HPA tăng giảm số pod theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi, request đang xử lý) |

Không sửa `golden_dataset.jsonl`. Trước khi thêm, đã kiểm tra từ khoá mới (`jwt`, `hpa`, `autoscal`...) không xuất hiện trong câu hỏi nào khác, để tránh retrieval chọn nhầm tài liệu. Kết quả: 2 câu được cải thiện, **0 regression**. Bản sửa được chép sang `production/utils/mock_llm.py` để stack Docker ở mục 4–5 cũng đạt 100%.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:** v2 (RAG) tăng accuracy từ 4,3% lên 91,3%, rồi 100% sau khi bổ sung KB. v1 chỉ trả lời đúng câu "Deploy nghĩa là gì?" nhờ câu trả lời cài sẵn, còn lại là câu chung chung.
- **Chi phí:** v2 tốn gần gấp đôi token mỗi câu (57 → 108,8) vì prompt có thêm context, nên tổng cost tăng 1,88 lần ($0.000402 → $0.000757). Tuy vậy **cost cho mỗi câu đúng giảm khoảng 11 lần** ($0.000402 → $0.0000361). Tiền trả cho v1 gần như phí hoàn toàn vì câu trả lời sai. So sánh hai phiên bản phải dùng "cost / câu đúng", không phải "cost / câu", vì cost / câu chỉ thưởng cho bản rẻ mà không biết bản đó có trả lời đúng hay không.
- **Latency:** v2 thêm bước retrieval (khoảng 20 ms) và prompt dài hơn (latency mock tăng 0,2 ms/token). p50 eval tăng khoảng 23 ms (166,8 → 189,7 ms), p95 tăng 35 ms. Mức này vẫn nằm xa dưới SLO 500 ms.
- **Kết luận:** đổi khoảng 2 lần token và khoảng 20–35 ms latency lấy +87–96 điểm % accuracy là đáng. Bổ sung KB làm token trung bình tăng nhẹ (108,8 → 116,6) nhưng cost / câu đúng vẫn giảm tiếp.

---

## 4. Observability stack (production)

Stack `06-lab-complete/benchmark-observability/production/` gồm agent, Nginx, Prometheus, Grafana, Jaeger và Locust. Bật bằng `docker compose -p k5-day14-observability up -d --build`. Tải nền cho mục 4 là Locust headless **30 users, ramp 5/s**, chạy liên tục suốt các thí nghiệm. Unit test alert: `promtool test rules /p/alert_rules_test.yml` cho kết quả **SUCCESS**.

> **Sửa 1 lỗi của đề:** lần đầu bật, container agent restart liên tục với lỗi `ModuleNotFoundError: No module named 'uvicorn'`. Nguyên nhân: Dockerfile copy packages vào `/home/agent/.local`, nhưng user `agent` được tạo với home là `/app` (`useradd -d /app`), nên Python tìm user site-packages ở `/app/.local`. Đã thêm `ENV PYTHONUSERBASE=/home/agent/.local` vào `production/Dockerfile` (cách bài Part 5 đã làm), không đổi gì khác.

### 4.1 Screenshot

Các ảnh được chụp tự động bằng Chrome headless (điều khiển qua DevTools Protocol để click mở chi tiết span và alert), lấy trực tiếp từ stack đang chạy trên máy.

- [x] Dashboard Grafana khi đang có tải: [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png). Lúc chụp, tải nền 30 users: Traffic 18,6 req/s, Error rate 0,98%, p95 294 ms, in-flight 5, 2 instance up. Biểu đồ 15 phút còn thấy rõ 3 đợt 100 users của benchmark scaling.
- [x] Trace chậm trong Jaeger: [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png). Span `chat mock-llm-small` dài 1.7s, chiếm gần hết trace (xem 4.3).
- [x] Trace lỗi trong Jaeger: [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png). `POST /ask` trả **503**, span `chat mock-llm-small` có `error.type = upstream_unavailable` và **event `exception`** (`exception.message = Upstream LLM error: 503 Service Unavailable (mock)`).
- [x] Trang Alerts của Prometheus: [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png). Nhóm `agent-slo` hiện **FIRING (2)**: `AgentHighErrorRate` (value 0,18) và `AgentHighLatencyP95` (value 1,89 s) **cùng firing**, chụp trong lúc chạy thí nghiệm A+B.

**Exercise 7.4: số đo lúc tải nền 30 users** (PromQL, cửa sổ 2 phút, 10:47:24Z):

| Câu hỏi | Kết quả đo | Giải thích |
|---|---|---|
| p95 và avg? | p95 = **294 ms**, avg = **233 ms** (p50 184 ms, p99 **1813 ms**) | p95 và avg gần nhau vì tỉ lệ request chậm (`MOCK_SLOW_RATE=0.03`) nhỏ hơn 5% nên chưa chạm tới p95. Khoảng cách thật nằm giữa avg và **p99** (233 ms so với 1,8 s): có khoảng 3% người dùng chờ khoảng 1,7 giây mà avg không cho thấy |
| Nginx chia đều 2 instance? | `172.20.0.7` = 9,30 req/s, `172.20.0.8` = 9,27 req/s | Chia đều (chênh 0,3%). Nginx round-robin qua `server agent:8000 resolve`, Docker DNS trả về IP của cả 2 container |
| Vì sao input tokens/s > output? | input **1436** tokens/s, output **756** tokens/s (gấp khoảng 1,9 lần) | Prompt gửi vào LLM = system prompt + **tài liệu retrieval (context)** + câu hỏi. Câu trả lời chỉ là phần nội dung tài liệu. v2 có RAG nên input luôn dài hơn output, và chi phí input chiếm phần lớn token |
| `curl http://localhost:8080/metrics`? | **HTTP 404** (trang 404 của nginx/1.28.3) | `nginx.conf` có `location = /metrics { return 404; }`. Metrics lộ thông tin nội bộ (route, lưu lượng, version, cost) và có thể bị lạm dụng, nên chỉ Prometheus được scrape **trực tiếp từng container trong mạng nội bộ** `obs_net` (DNS service discovery), không đi qua cổng public |

### 4.2 Thí nghiệm "phá hệ thống"

Mỗi lần đổi cấu hình bằng `VAR=... docker compose -p k5-day14-observability up -d --no-deps --scale agent=2 agent`. Thời gian đo bằng script đọc `/api/v1/alerts` của Prometheus mỗi 2 giây, tính từ lúc chạy lệnh đổi cấu hình (t0).

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (value 0,188 = 18,8%) | **92 s** (pending ở +31 s, cộng `for: 1m`) | **Error rate** chuyển đỏ; "Requests/s theo status code" có đường 503 tăng; "LLM calls theo kết quả" có đường error tăng |
| B | `MOCK_SLOW_RATE=0.3` (giữ A) | `AgentHighLatencyP95` (value 1,88 s) | **149 s** (pending ở +30 s, cộng `for: 2m`) | **"Latency /ask p50/p95/p99"**: p95 nhảy lên 1,88 s, p99 1,98 s, trong khi avg chỉ 0,54 s và p50 0,21 s |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh`, nhưng **đã firing sẵn từ mức nền** (xem ghi chú). Không có alert latency | Không có "lần firing mới". Value tăng từ $2,32 lên $16,8/giờ trong cửa sổ 5 phút, tức $34,15/giờ nếu tính cửa sổ 1 phút, **gấp khoảng 14,7 lần** | **Cost burn rate (USD/giờ)**. Ngoài ra "LLM latency p95 theo model": p95 LLM 0,50 s, p50 HTTP tăng từ 184 lên 415 ms, p95 HTTP từ 295 lên 708 ms (vẫn dưới 1 s nên không firing) |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` (`up{instance="172.20.0.7:8000"} = 0`) | **40 s** (pending ở +9 s, cộng `for: 30s`) | **Instances up** giảm từ 2 xuống 1; "Requests/s theo instance" chỉ còn 1 đường (13,6 req/s). Nginx ghi **29 lỗi 504** nhưng panel Error rate của agent vẫn **xanh (0,94%)** |
| E | `AGENT_VERSION=v1` | **Không có** (`/api/v1/alerts` rỗng sau 3 phút; `LLMCostBurnRateHigh` còn tự tắt vì cost giảm) | Không firing | **"Phiên bản đang chạy"** đổi sang `v1` (2 instance); **"Retrieval hit ratio"** mất dữ liệu (`agent_retrieval_total` không có series mới); **Tokens/s** giảm khoảng một nửa (input 1446 → 683, output 763 → 369); **Cost burn rate** từ $2,33 xuống $1,35/giờ; latency còn *tốt hơn* (p95 295 → 198 ms) |

Ghi chú:

- **C: alert cost đặt ngưỡng quá thấp so với traffic.** Ở mức nền (model nhỏ, 30 users, khoảng 18,6 req/s), cost burn rate đo được là **$2,33/giờ**, đã vượt ngưỡng $2/giờ. Sau khi khởi động lại agent, `LLMCostBurnRateHigh` vào pending ở +176 s và **firing ở +297 s** dù chưa phá gì. Khoảng thời gian này bằng thời gian cửa sổ `rate[5m]` lấp đầy, cộng `for: 2m`. Một alert luôn đỏ ở trạng thái bình thường sẽ gây alert fatigue. Nên đặt ngưỡng theo baseline (vd gấp 3 lần cost bình thường), hoặc so với cùng giờ hôm trước. Ở A+B, alert này lại tự **về INACTIVE** (thấy trong ảnh Prometheus), vì lỗi 20% và request chậm làm giảm số LLM call thành công nên cost giảm.
- **D, vì sao 504 không thấy trên dashboard:** request vào instance bị pause bị Nginx cắt sau `proxy_read_timeout 10s`, agent chưa bao giờ xử lý xong nên không đếm. Metric chỉ phản ánh những gì chính service đó thấy. Cần giám sát thêm ở tầng LB (metric hoặc log 5xx của Nginx) và synthetic check từ bên ngoài. Dùng `pause` thay vì `stop` vì container bị stop sẽ biến khỏi DNS, Prometheus coi như target không tồn tại chứ không phải "down", nên không có alert.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Ở thí nghiệm E, mọi Golden Signal đều xanh, thậm chí đẹp hơn bình thường: nhanh hơn, rẻ hơn, error rate 0,6%. Nhưng theo eval ở mục 3, v1 chỉ trả lời đúng **4,3%** so với 100% của v2. Metric hạ tầng chỉ đo "service có trả lời không, nhanh không", không đo "trả lời có đúng không". Cách phát hiện:

1. **Chặn trước khi deploy**: quality gate trong CI (`run_eval.py --min-accuracy 0.85 --baseline ...`, xem phụ lục 7.3). Thay đổi làm tụt accuracy hoặc gây regression sẽ cho exit code 1, PR không merge được. Đã thử: trỏ eval vào v1 thì gate FAIL, 22 câu bị regression.
2. **Eval định kỳ trên bản đang chạy** (synthetic eval): cron chạy golden dataset vào production mỗi 15–60 phút, đẩy `eval_accuracy` thành metric và alert khi giảm. Cách này bắt được cả trường hợp cấu hình sai trong lúc chạy như `AGENT_VERSION=v1`.
3. **Metric "proxy" cho chất lượng** và alert khi lệch khỏi baseline: retrieval hit ratio (về 0 ở thí nghiệm E), tỉ lệ câu trả lời generic/fallback, tokens/câu đột ngột giảm một nửa, alert khi `agent_info{version}` khác bản dự kiến.
4. **Tín hiệu từ người dùng**: 👍/👎, tỉ lệ hỏi lại, kết hợp LLM-as-judge chấm mẫu một phần traffic thật.

### 4.3 Debug bằng trace

Trace chậm nhất tìm được qua Jaeger API (`query.duration_min=1500ms`, chọn trace dài nhất):

- `trace_id`: `1378e795d517e0d7fd3592ce0ee1951b`
- Tổng thời gian: **1734.6 ms** (span gốc `POST /ask`, instance `f282c7fa8501`, agent v2)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (1700.4 ms, 98.0%)**. Các span còn lại: `agent.run` 1725.1 ms (bao cả retrieval + LLM), `agent.retrieve_context` chỉ **22.9 ms (1.3%)**. Attributes của span LLM: `gen_ai.request.model = mock-llm-small`, `gen_ai.usage.input_tokens = 95`, `gen_ai.usage.output_tokens = 55`, `llm.cost_usd = 4.725e-05`.
- Kết luận: request chậm vì **LLM upstream chậm** (rơi vào nhóm `MOCK_SLOW_RATE`, cộng thêm khoảng 1,5 s), không phải do code agent hay bước retrieval. Muốn giảm tail latency thì phải xử lý ở tầng gọi LLM: timeout + retry/hedging, streaming, hoặc model/provider dự phòng.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep 1378e795d517e0d7fd3592ce0ee1951b`):

```json
{"ts": "2026-11-25T10:44:15.761934+00:00", "level": "INFO", "service": "observable-agent", "instance": "f282c7fa8501", "event": "llm_call", "trace_id": "1378e795d517e0d7fd3592ce0ee1951b", "span_id": "52288d662125f5de", "request_id": "28aea466d8e9", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 95, "output_tokens": 55, "cost_usd": 4.7e-05, "llm_ms": 1691.7}
{"ts": "2026-11-25T10:44:15.768068+00:00", "level": "INFO", "service": "observable-agent", "instance": "f282c7fa8501", "event": "http_request", "trace_id": "1378e795d517e0d7fd3592ce0ee1951b", "span_id": "52288d662125f5de", "request_id": "28aea466d8e9", "method": "POST", "route": "/ask", "status": 200, "ms": 1733.3}
```

Trace lỗi trong ảnh (`01258a64fdcf40d2f177a2e255ae989e`, 109 ms, HTTP 503) cũng tìm lại được trong log bằng cùng `trace_id`:

```json
{"ts": "2026-11-25T10:43:13.717353+00:00", "level": "INFO", "service": "observable-agent", "instance": "f282c7fa8501", "event": "llm_error", "trace_id": "01258a64fdcf40d2f177a2e255ae989e", "span_id": "968ceef94b3e13ab", "request_id": "8aefbe7dfb7a", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-25T10:43:13.717453+00:00", "level": "INFO", "service": "observable-agent", "instance": "f282c7fa8501", "event": "http_request", "trace_id": "01258a64fdcf40d2f177a2e255ae989e", "span_id": "968ceef94b3e13ab", "request_id": "8aefbe7dfb7a", "method": "POST", "route": "/ask", "status": 503, "ms": 108.8}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Nguồn: `benchmark-results/scale_1_stats.csv`, `benchmark-results/scale_3_stats.csv` (dòng `POST /ask`; dòng Aggregated có thêm `/health`).

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.77 (Aggregated 65.83) | 200 | 230 | 1700 | 80 / 7141 (1.12%, đều là 503 từ LLM mock) |
| 3 | 59.23 (Aggregated 65.59) | 190 | 230 | 1700 | 70 / 7081 (0.99%, đều là 503 từ LLM mock) |

> **Lưu ý về cách chạy (đã kiểm chứng):** lệnh trong CHECKPOINTS `docker compose run --rm locust ...` với Docker Compose v5.7.1 **tự đưa service `agent` về `replicas: 2`** khai báo trong compose, vì `locust` phụ thuộc `nginx`, `nginx` phụ thuộc `agent`. Lần chạy đầu, log ghi `agent-2 Created` ở run "1 instance" và `agent-3 Removed` ở run "3 instance", tức cả hai thực chất đều chạy 2 instance. Đã bỏ kết quả đó và chạy lại với **`docker compose run --rm --no-deps locust ...`**, kiểm tra ngay giữa run bằng `docker compose ps agent` (đúng 1, rồi đúng 3 container) và targets của Prometheus. Một lần chạy scale_1 khác bị khựng (kéo dài 176 s thay vì 120 s, `Requests/s` bị kéo xuống 40) nên cũng bị bỏ và chạy lại. Bản nộp có thời gian chạy đúng 120 s.

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

- **Scale từ 1 lên 3 instance không cải thiện gì**: RPS (59,8 so với 59,2), p95 (230 ms ở cả hai) và p99 (1700 ms) gần như y hệt. Chênh lệch nằm trong mức nhiễu.
- **Lý do 1: 1 instance chưa hề bão hoà.** Giữa run scale_1, `docker stats` cho thấy agent chỉ dùng **16% CPU** (giới hạn 0,5 CPU, tức khoảng 32% quota) và 64 MiB/256 MiB RAM. p50 (190–200 ms) bằng đúng latency gốc của LLM mock (120 ms + jitter 0–60 ms + khoảng 0,2 ms/token), nghĩa là request hầu như không phải chờ trong hàng đợi.
- **Lý do 2: tải là closed-loop và bị giới hạn bởi think time.** Mỗi user Locust nghỉ 0,5–2 s (trung bình 1,25 s) giữa các lần gọi, cộng khoảng 0,22 s response, nên 100 users chỉ tạo tối đa khoảng 100 / 1,47 ≈ **68 req/s**. Đúng bằng mức đo được (65,8 req/s aggregated, history ổn định 67–69 req/s). Thêm instance không làm user gửi nhanh hơn.
- **Lý do 3: latency do LLM quyết định.** Trace (4.3) cho thấy LLM chiếm khoảng 98% thời gian request. Panel "LLM latency p95" giữ khoảng 0,2 s bất kể số instance. p99 khoảng 1,7 s đến từ `MOCK_SLOW_RATE` của LLM. Scale ngang chỉ thêm khả năng xử lý song song, **không làm từng lời gọi LLM nhanh hơn**.
- **Khi nào scale mới có ích?** Khi một instance bão hoà thật (CPU gần quota, in-flight tăng, p50 bắt đầu tăng vì phải chờ hàng đợi). Develop đã thấy hiện tượng này ở c=100 (mục 2.1, p50 tăng gấp 3), hoặc khi tăng users/giảm think time đủ để đẩy một instance tới giới hạn CPU 0,5 core. Muốn giảm p95/p99 trong lab này thì phải xử lý **tầng LLM** (timeout/retry, cache câu trả lời, model nhanh hơn), không phải thêm container.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối, percentile thì không.** Ở 10% request chậm, avg vẫn 308 ms ("đạt SLO") nhưng p95 là 1678 ms. SLO phải đặt theo p95/p99, và cần đủ mẫu: 300 request ở c=1 đã đủ nhiễu để p95 trượt ngưỡng chỉ vì ngẫu nhiên.
  2. **Thông lượng có điểm gãy.** RPS tăng tuyến tính tới 50 concurrency rồi tụt ở 100, p50 tăng gấp 3 vì một process bão hoà. Load test phải quét nhiều mức tải để tìm điểm gãy, không chỉ chạy một mức.
  3. **Chất lượng phải đo riêng và đưa vào CI.** v1 nhanh hơn, rẻ hơn mỗi câu, và không có metric hạ tầng nào báo lỗi, nhưng chỉ đúng 4,3%. Chỉ eval trên golden dataset với quality gate (exit code 1 khi accuracy thấp hoặc có regression) mới chặn được loại lỗi này. "Cost / câu đúng" là chỉ số so sánh phiên bản tốt hơn "cost / câu".
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

  - **Giám sát tầng load balancer và synthetic check từ bên ngoài**: thí nghiệm D cho thấy 29 lỗi 504 ở Nginx mà agent không thấy. Cần alert theo tỉ lệ 5xx của Nginx (nginx exporter hoặc log-based metric), cộng một probe gọi `/ask` từ ngoài (blackbox exporter) đo đúng trải nghiệm người dùng.
  - **Metric chất lượng online để bắt sự cố kiểu E**: tỉ lệ retrieval hit (`retrieval.documents = 0`), số câu trả lời rơi vào nhánh "chung chung", độ dài câu trả lời, feedback 👍/👎 của người dùng. Cộng thêm **eval định kỳ** (cron chạy `run_eval.py` trên golden dataset vào bản đang chạy) và alert khi accuracy giảm, cùng quality gate trong CI trước khi deploy.
  - **Alert theo error budget burn rate** (multi-window, vd 5 phút và 1 giờ) thay cho ngưỡng tức thời, để vừa bắt sự cố nhanh vừa không báo động vì spike ngắn.
  - **Sửa ngưỡng `LLMCostBurnRateHigh`** theo baseline thật ($2,33/giờ ở 30 users đã vượt $2): dùng ngưỡng tương đối (gấp 3 lần trung bình 1 ngày), thêm alert theo **budget ngày/tháng còn lại**, và metric cost theo `agent_version`/`model` để thấy ngay khi đổi model như thí nghiệm C.
  - **Saturation chi tiết hơn**: CPU/memory so với quota của container, độ dài hàng đợi, kết hợp p50 (khi p50 tăng là bắt đầu xếp hàng) để biết lúc nào cần scale thật (mục 5 cho thấy scale khi chưa bão hoà là vô ích).
  - **Timeout + retry cho LLM** kèm metric `llm_retries_total` và p99 theo model/provider, vì trace cho thấy tail latency đến từ upstream LLM.

---

## Phụ lục — Exercise 7.1–7.3 (develop)

### Exercise 7.1: Đọc metrics

Gọi `POST /ask` một lần trên v2 (cổng 8002) rồi `curl -s http://localhost:8002/metrics | grep -v "^#"` (trích):

```text
http_requests_total{method="POST",route="/ask",status="200"} 1.0
http_requests_total{method="GET",route="unmatched",status="404"} 1.0
http_request_duration_seconds_bucket{le="0.2",method="POST",route="/ask"} 0.0
http_request_duration_seconds_bucket{le="0.3",method="POST",route="/ask"} 1.0
http_request_duration_seconds_count{method="POST",route="/ask"} 1.0
http_request_duration_seconds_sum{method="POST",route="/ask"} 0.22048291700775735
agent_in_flight_requests 0.0
llm_tokens_total{model="mock-llm-small",type="input"} 91.0
llm_tokens_total{model="mock-llm-small",type="output"} 55.0
llm_cost_usd_total{model="mock-llm-small"} 4.6649999999999996e-05
agent_question_length_chars_bucket{le="20.0"} 0.0
agent_question_length_chars_bucket{le="50.0"} 1.0
agent_question_length_chars_count 1.0
agent_question_length_chars_sum 40.0
agent_info{model="mock-llm-small",version="v2"} 1.0
```

1. **Mỗi loại metric một ví dụ:**
   - **Counter**: `http_requests_total` (hoặc `llm_tokens_total`, `llm_cost_usd_total`). Chỉ tăng, đọc bằng `rate()`.
   - **Gauge**: `agent_in_flight_requests` (lên xuống theo số request đang xử lý), `agent_info`.
   - **Histogram**: `http_request_duration_seconds` (các series `_bucket{le=...}`, `_sum`, `_count`). Request 220 ms rơi vào bucket `le="0.3"`, chưa vào `le="0.2"`. Tính percentile bằng `histogram_quantile()`.
2. **Dùng URL thật làm label `route`:** mỗi URL lạ (`/wp-admin`, `/.env`, `/a1b2c3`...) sinh **một time series mới** cho mỗi tổ hợp `method × route × status`, và Histogram còn nhân thêm khoảng 14 series (bucket + sum + count). Bot quét hàng nghìn URL ngẫu nhiên sẽ làm cardinality bùng nổ: Prometheus tốn RAM và chậm query, dashboard rác, thậm chí OOM. Kẻ tấn công điều khiển được số series chỉ bằng cách gọi URL ngẫu nhiên. Dùng template `route.path` thì mọi URL không khớp route bị gom về `route="unmatched"`. Đã thử `GET /wp-admin` và chỉ thấy đúng 1 series `route="unmatched",status="404"`.
3. **Metric mới `agent_question_length_chars`** (đã thêm vào `develop/app.py`): Histogram với buckets `(20, 50, 100, 200, 500, 1000, 2000)`, gọi `QUESTION_LENGTH.observe(len(body.question))` ở đầu `/ask`. Câu hỏi mẫu dài 40 ký tự nên rơi vào bucket `le="50"` (`_sum 40.0`). Dùng Histogram chứ không dùng label `length=...` vì độ dài có vô số giá trị, nếu làm label sẽ gây cardinality bùng nổ.

### Exercise 7.3: Quality gate trong CI

File job: [`.github/workflows/eval-quality-gate.yml`](.github/workflows/eval-quality-gate.yml). Job chạy khi có PR sửa `06-lab-complete/benchmark-observability/**`:

1. Cài `requirements.txt`, bật agent v2 với `MOCK_ERROR_RATE=0` để lỗi upstream ngẫu nhiên không làm job fail oan, chờ `/health` trả 200.
2. Chạy `python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`.
3. `run_eval.py` gọi `SystemExit(1)` khi accuracy < 85% **hoặc** có câu bị regression so với baseline. Step trả exit code khác 0 thì GitHub Actions đánh dấu job fail. Bật branch protection "Require status checks to pass" cho job `eval` thì PR **không merge được** cho tới khi gate pass. Report JSON và log agent được upload làm artifact để xem câu nào sai.

Đã chạy thử các bước của job trên máy:

```text
# agent v2 (bản đã sửa KB)
Quality gate (accuracy ≥ 85%, không regression): ✅ PASS      → exit code = 0

# giả lập một PR làm hỏng chất lượng: trỏ eval vào agent v1
Accuracy      : 4.3%  (1/23)
⚠️  Câu bị regression : 22 ['docker-01', 'docker-02', ...]
Quality gate (accuracy ≥ 85%, không regression): ❌ FAIL      → exit code = 1
```
