# Day 12 Lab - Mission Answers

> **Student:** Lê Hữu Khoa | **ID:** 2A202600863 | **Date:** 12/06/2026  
> **Environment:** conda `ml` (Python 3.11.15), macOS

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found in `01-localhost-vs-production/develop/app.py`

1. **Hardcoded secrets** — `OPENAI_API_KEY` và `DATABASE_URL` được viết thẳng vào code. Nếu push lên GitHub, key bị lộ ngay lập tức.
2. **Không có config management** — `DEBUG = True`, `MAX_TOKENS = 500` là magic constants, không đọc từ environment.
3. **`print()` thay vì logging** — In ra secret key vào stdout (`print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`). Log không có cấu trúc, không thể parse.
4. **Không có health check endpoint** — Platform (Railway, Render, K8s) không biết khi nào app crash để restart.
5. **Port cố định và host sai** — `host="localhost"` không chạy được trong container (cần `0.0.0.0`). Port `8000` hardcode, không đọc từ `PORT` env var.
6. **`reload=True` trong production** — Debug reload làm chậm và tăng attack surface.

### Exercise 1.2: So sánh develop vs production

| Feature | Develop (`develop/app.py`) | Production (`production/app.py`) | Tại sao quan trọng? |
|---------|--------------------------|----------------------------------|---------------------|
| **Config** | Hardcode trong code | Đọc từ env vars qua `Settings` dataclass | 12-Factor: secrets không được commit vào repo |
| **Logging** | `print()` + secret key lộ ra | JSON structured logging, không log secret | Dễ parse bởi log aggregator (Datadog, Loki); tuân thủ OWASP |
| **Health check** | Không có | `GET /health` + `GET /ready` | Platform biết restart khi crash; load balancer dừng traffic khi chưa ready |
| **Host/Port** | `localhost:8000` hardcode | `0.0.0.0` + `os.getenv("PORT", "8000")` | Chạy được trong Docker container; Railway/Render inject PORT tự động |
| **Lifecycle** | Không có | `@asynccontextmanager lifespan` (startup/shutdown) | Graceful shutdown: hoàn thành request hiện tại trước khi dừng |
| **CORS** | Không có | Chỉ cho phép origins được cấu hình | Ngăn cross-site request forgery từ unknown origins |
| **Secrets** | `sk-hardcoded-fake-key` | Từ env, validate trong production mode | Không bao giờ commit secrets; validate khi start |

### Exercise 1.3: Kết luận

Production app tuân thủ **12-Factor App** methodology: config từ environment, stateless processes, logs như event stream. Develop app vi phạm tất cả những nguyên tắc này.

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions (`02-docker/develop/Dockerfile`)

1. **Base image:** `python:3.11` — Full Python distribution (~1 GB, bao gồm pip, compiler tools)
2. **Working directory:** `/app`
3. **Tại sao copy `requirements.txt` trước code?** — Docker build cache: nếu requirements không đổi, layer pip install được cache lại. Chỉ khi `requirements.txt` thay đổi mới cần rebuild layer này. Giúp tiết kiệm thời gian build đáng kể.
4. **CMD cuối cùng làm gì?** — `CMD ["python", "app.py"]` — Start uvicorn web server qua `if __name__ == "__main__": uvicorn.run(...)` trong app.py

### Exercise 2.2: Multi-stage Dockerfile (`02-docker/production/Dockerfile`)

**Stage 1 (builder):** `python:3.11-slim`
- Cài `gcc`, `libpq-dev` (build tools cho native extensions)
- `pip install --user -r requirements.txt` → install vào `/root/.local`
- Image này KHÔNG được deploy

**Stage 2 (runtime):** `python:3.11-slim`
- Tạo non-root user `appuser` (security best practice)
- `COPY --from=builder /root/.local /home/appuser/.local` → chỉ lấy packages, không lấy build tools
- Chạy app với user `appuser`, không phải `root`

**Tại sao nhỏ hơn?** Stage 2 không có `gcc`, `pip`, build tools → chỉ chứa Python runtime và site-packages.

### Exercise 2.3: Image size comparison

| Image | Dockerfile | Estimated Size |
|-------|------------|----------------|
| `agent-develop` | `python:3.11` single-stage | ~1.0 GB |
| `agent-production` | `python:3.11-slim` multi-stage | ~180 MB |
| **Difference** | | **~82% nhỏ hơn** |

> Lý do: `python:3.11-slim` không có GCC/build tools (~700MB tiết kiệm). Multi-stage loại bỏ build artifacts.

### Exercise 2.4: Security improvements

1. **Non-root user** (`RUN groupadd -r appuser && useradd -r -g appuser appuser`) — Giảm attack surface nếu container bị compromise
2. **HEALTHCHECK** trong Dockerfile — Docker tự restart container khi health fail
3. **Không copy `.env`** vào image (dùng `.dockerignore`)
4. **`--no-cache-dir`** trong pip install — Giảm image size, không lưu cache credentials

---

## Part 3: Cloud Deployment

### Exercise 3.1: Railway deployment

- **Platform:** Railway
- **URL:** https://day122a202600863lehuukhoa-production.up.railway.app
- **Config file:** `railway.toml` với `healthcheckPath = "/health"`, PORT đọc từ env var
- **Test kết quả:**
  ```
  GET /health  → {"status":"ok","uptime_seconds":10731.7,"platform":"Railway"}  HTTP 200
  POST /ask    → {"question":"...","answer":"...","platform":"Railway"}           HTTP 200
  ```

### Exercise 3.2: railway.toml analysis

```toml
[build]
builder = "DOCKERFILE"          # Dùng Dockerfile để build (không phải Nixpacks)

[deploy]
startCommand = "uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2"
healthcheckPath = "/health"     # Railway gọi endpoint này để kiểm tra app alive
healthcheckTimeout = 30         # Timeout 30 giây
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3     # Retry 3 lần nếu crash
```

### Exercise 3.3: Environment variables cần set trên Railway

| Variable | Value | Lý do |
|----------|-------|-------|
| `AGENT_API_KEY` | (secret value) | Authentication cho `/ask` endpoint |
| `ENVIRONMENT` | `production` | Kích hoạt strict validation, tắt `/docs` |
| `DAILY_BUDGET_USD` | `10` | Cost guard |
| `RATE_LIMIT_PER_MINUTE` | `10` | Rate limiting |
| `LOG_LEVEL` | `INFO` | Production logging level |

---

## Part 4: API Security

### Exercise 4.1: Authentication test results

```bash
# Test 1: Không có API key → 401
curl -X POST http://localhost:8005/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"hello"}'
# → HTTP 401: {"detail":"Invalid or missing API key. Include header: X-API-Key: <key>"}

# Test 2: API key sai → 401
curl -X POST http://localhost:8005/ask \
  -H "X-API-Key: wrong-key" \
  -H "Content-Type: application/json" \
  -d '{"question":"hello"}'
# → HTTP 401

# Test 3: API key đúng → 200
curl -X POST http://localhost:8005/ask \
  -H "X-API-Key: test-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is Docker?"}'
# → HTTP 200: {"question":"What is Docker?","answer":"Container là cách đóng gói app...","model":"gpt-4o-mini"}
```

### Exercise 4.2: Rate limiting test results (10 req/min)

```
Request 1:  HTTP 200
Request 2:  HTTP 200
Request 3:  HTTP 200
Request 4:  HTTP 200
Request 5:  HTTP 200
Request 6:  HTTP 200
Request 7:  HTTP 200
Request 8:  HTTP 200
Request 9:  HTTP 200
Request 10: HTTP 200
Request 11: HTTP 429 {"detail":"Rate limit exceeded: 10 req/min"}
Request 12: HTTP 429
Request 13: HTTP 429
```

**Algorithm:** Sliding Window Counter — mỗi API key có một deque timestamp. Khi request đến, xóa timestamps cũ (> 60s), đếm còn lại. Nếu ≥ 10 → 429.

### Exercise 4.3: Security headers

```
X-Content-Type-Options: nosniff      → Ngăn MIME-type sniffing
X-Frame-Options: DENY                → Ngăn clickjacking (iframe)
```

Tắt `server` header để ẩn thông tin server version.

### Exercise 4.4: Cost guard implementation

Cost guard trong `app/main.py` theo dõi chi phí token theo ngày:

```python
PRICE_PER_1K_INPUT_TOKENS  = $0.00015  (GPT-4o-mini)
PRICE_PER_1K_OUTPUT_TOKENS = $0.0006

def check_and_record_cost(input_tokens, output_tokens):
    today = time.strftime("%Y-%m-%d")
    if today != _cost_reset_day:
        _daily_cost = 0.0    # Reset mỗi ngày
    if _daily_cost >= settings.daily_budget_usd:
        raise HTTPException(503, "Daily budget exhausted. Try tomorrow.")
    cost = (input_tokens/1000)*0.00015 + (output_tokens/1000)*0.0006
    _daily_cost += cost
```

- **Tại sao cần cost guard?** LLM API tính tiền theo token. Không có guard → bill có thể tăng không kiểm soát nếu bị abuse
- **Giới hạn:** $10/ngày (DAILY_BUDGET_USD=10). Khi hết → HTTP 503
- **Production improvement:** Lưu cost trong Redis thay vì in-memory để chia sẻ giữa nhiều instances

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Stateless design

**Vấn đề:** Nếu lưu session trong memory của instance A, khi request tiếp theo đến instance B → mất session.

**Giải pháp:** Lưu tất cả state trong Redis:
```python
redis_client.setex(f"session:{user_id}", 3600, json.dumps(session_data))
```

Bất kỳ instance nào cũng đọc được session → horizontal scaling hoạt động.

### Exercise 5.2: Health checks

| Endpoint | Mục đích | Khi nào fail? |
|----------|----------|---------------|
| `GET /health` | **Liveness probe** — App còn sống không? | Khi process crash, deadlock |
| `GET /ready` | **Readiness probe** — App ready nhận traffic? | Trong lúc startup, shutdown |

```
Platform → GET /health → 200 = OK, 500 = restart container
Load Balancer → GET /ready → 200 = route traffic, 503 = stop routing
```

### Exercise 5.3: Graceful shutdown

```python
signal.signal(signal.SIGTERM, _handle_signal)
```

Khi nhận SIGTERM (platform shutdown):
1. `_is_ready = False` → `/ready` trả về 503 → load balancer dừng gửi traffic mới
2. Các request đang xử lý hoàn thành (`timeout_graceful_shutdown=30`)
3. Log "shutdown complete" và exit

### Exercise 5.4: Docker Compose scaling

```yaml
# docker-compose.yml
agent:
  depends_on:
    redis:
      condition: service_healthy  # Chờ Redis healthy trước khi start agent
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
    interval: 30s
    timeout: 10s
    retries: 3
```

### Exercise 5.5: Tại sao Redis cần thiết trong production?

1. **Rate limiting shared:** 10 instances chia sẻ cùng counter → đúng limit
2. **Session storage:** User không mất session khi request hit instance khác
3. **Cost tracking:** $10/ngày tổng cộng, không phải per-instance

---

## Tổng kết

| Concept | Implement ở đâu | Trạng thái |
|---------|----------------|-----------|
| 12-Factor Config | `app/config.py` | Done |
| JSON Logging | `app/main.py` | Done |
| API Key Auth | `verify_api_key()` | Done |
| Rate Limiting | `check_rate_limit()` sliding window | 10 req/min |
| Cost Guard | `check_and_record_cost()` | $10/day |
| Health + Ready | `GET /health`, `GET /ready` | Done |
| Graceful Shutdown | `signal.SIGTERM` + lifespan | Done |
| Multi-stage Docker | `Dockerfile` | < 200MB |
| Non-root container | `USER agent` | Done |
| No hardcoded secrets | `.env.example` + env vars | Done |
