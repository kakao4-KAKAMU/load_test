# KAKAMU_BE Locust Load Test Plan

## 1. 목적

KAKAMU_BE FastAPI 백엔드의 주요 사용자 흐름에 대해 처리량, 지연 시간, 오류율, 병목 자원을 확인한다. 테스트는 별도 VM에서 Locust를 실행하고, 애플리케이션/DB/Redis/ML 서버는 배포 환경을 대상으로 한다.

이번 테스트는 다음을 분리해서 본다.

- Backend-only read path: 피드, 게시글 상세, 댓글, 알림, 검색 메타데이터
- Search-heavy path: live/user/content 검색
- ML-integrated path: For You 검색, 영화 추천
- Optional write path: 좋아요, 댓글 작성, 게시글 작성, activity log

## 2. 구현 파일

- `load_tests/locustfile.py`: Locust 시나리오
- `load_tests/users.example.csv`: 테스트 계정 CSV 예시
- `requirements-loadtest.txt`: Locust VM용 의존성

실제 계정 파일은 `load_tests/users.csv`로 생성하고 git에 올리지 않는다.

## 3. 테스트 시나리오

| 영역 | Locust tag | 대표 API | 기본 성격 |
| --- | --- | --- | --- |
| Health | `health` | `GET /health` | 무인증 상태 확인 |
| Account | `account` | `POST /users/login/local`, `GET /users/me`, `GET /personas` | 로그인 및 세션 컨텍스트 |
| Feed | `feed` | `GET /posts/`, `GET /posts/{id}`, `GET /posts/{id}/comments/`, `GET /posts/liked` | 읽기 중심 핵심 트래픽 |
| Search | `search` | `GET /v1/search/live`, `GET /v1/search/user`, `GET /v1/search/content` | 검색 부하 |
| Movie | `movie` | `GET /v1/search/movie`, `GET /movies/recommend` | 영화 목록/추천 |
| Metadata | `metadata` | `GET /v1/genre/list`, `GET /v1/search/trend` | 캐시/DB 조회성 트래픽 |
| Notification | `notification` | `GET /notifications/` | 사용자별 알림 조회 |
| ML | `ml` | `GET /v1/search/for-you`, `GET /movies/recommend` | ML 서버 연동 포함 |
| Write | `write` | `POST /likes/`, `POST /comments`, `POST /posts/`, `POST /logs/activity` | 명시적으로 켤 때만 실행 |

주의: 검색 API에는 프로세스 메모리 기반 rate limit이 있어 같은 사용자 기준 1초에 5회 이상 검색 시 429가 발생할 수 있다. 목표 VU와 검색 비중에 맞춰 충분한 테스트 계정을 준비한다.

주의: `GET /notifications/`는 조회와 동시에 알림 읽음 처리를 수행한다. 운영 계정이 아닌 테스트 계정으로만 실행한다.

## 4. VM 준비

권장 시작 사양:

- 2 vCPU / 4 GiB RAM 이상
- 대상 서버와 같은 리전 또는 실제 사용자와 유사한 네트워크 구간
- NTP 동기화 활성화
- 테스트 VM의 공인 IP를 WAF, 방화벽, API Gateway allowlist에 등록

설치:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-loadtest.txt
cp load_tests/users.example.csv load_tests/users.csv
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-loadtest.txt
Copy-Item load_tests\users.example.csv load_tests\users.csv
```

`load_tests/users.csv`에는 실제 테스트 계정을 넣는다. `persona_id`는 비워도 되며, Locust가 로그인 후 `/personas`에서 자동 선택한다. 고정 페르소나로 테스트해야 하면 CSV에 넣는다.

## 5. 주요 환경 변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `KAKAMU_API_PREFIX` | `/api` | API prefix. 프록시 없이 앱에 직접 붙으면 빈 문자열로 설정 |
| `KAKAMU_TEST_USERS_FILE` | `load_tests/users.csv` | 테스트 계정 CSV |
| `KAKAMU_TEST_EMAIL` / `KAKAMU_TEST_PASSWORD` | 없음 | 단일 계정으로 빠른 smoke test |
| `KAKAMU_TEST_PERSONA_ID` | 없음 | 단일 계정 페르소나 고정 |
| `KAKAMU_SEARCH_TERMS` | 영문 기본 검색어 | 쉼표 구분 검색어. 실제 데이터에 맞춰 한국어/영화명 권장 |
| `KAKAMU_RECOMMEND_QUERIES` | 영문 기본 추천 문구 | 영화 추천 쿼리 |
| `KAKAMU_SEED_POST_IDS` | 없음 | 미리 알고 있는 게시글 ID 목록 |
| `KAKAMU_SEED_MOVIE_IDS` | 없음 | 게시글 작성에 사용할 영화 UUID 목록 |
| `KAKAMU_ENABLE_WRITES` | `false` | `true`일 때만 쓰기 API 실행 |
| `KAKAMU_MIN_WAIT_SECONDS` | `1.0` | 사용자 task 간 최소 대기 |
| `KAKAMU_MAX_WAIT_SECONDS` | `4.0` | 사용자 task 간 최대 대기 |

## 6. 실행 예시

Smoke test:

```bash
locust -f load_tests/locustfile.py \
  --host https://YOUR-API-HOST \
  --headless --users 5 --spawn-rate 1 --run-time 5m \
  --tags health account feed metadata \
  --html load_tests/reports/smoke.html \
  --csv load_tests/reports/smoke
```

Backend-only baseline:

```bash
locust -f load_tests/locustfile.py \
  --host https://YOUR-API-HOST \
  --headless --users 100 --spawn-rate 10 --run-time 30m \
  --exclude-tags ml write \
  --html load_tests/reports/backend-baseline.html \
  --csv load_tests/reports/backend-baseline
```

ML 포함 read test:

```bash
locust -f load_tests/locustfile.py \
  --host https://YOUR-API-HOST \
  --headless --users 100 --spawn-rate 10 --run-time 30m \
  --tags read ml \
  --html load_tests/reports/ml-read.html \
  --csv load_tests/reports/ml-read
```

쓰기 포함 테스트는 사전 승인된 스테이징/테스트 데이터에서만 실행한다.

```bash
KAKAMU_ENABLE_WRITES=true locust -f load_tests/locustfile.py \
  --host https://YOUR-API-HOST \
  --headless --users 50 --spawn-rate 5 --run-time 20m \
  --tags read write \
  --html load_tests/reports/write-mix.html \
  --csv load_tests/reports/write-mix
```

## 7. 단계별 계획

1. Smoke: 5 VU, 5분
   - 인증, prefix, 테스트 계정, 대시보드 수집 여부 확인
   - 실패율 0% 또는 원인 설명 가능한 수준이어야 다음 단계 진행

2. Baseline: 50~100 VU, 30분
   - ML 제외 read 중심
   - 백엔드 pod, DB, Redis의 정상 상태 기준선 확보

3. Ramp test: 50 -> 100 -> 200 -> 300 VU, 단계별 15~20분
   - HPA scale-out 반응 시간, p95/p99 지연 시간 변화 확인
   - DB connection, Redis latency, pod CPU throttling 확인

4. Stress test: 목표 트래픽의 120~200%, 20~30분
   - 병목 지점과 graceful degradation 확인
   - 429/5xx/timeout 발생 구간 기록

5. Soak test: 목표 트래픽, 2~4시간
   - 메모리 누수, DB connection 누수, Redis 메모리 증가, 스케줄러 영향 확인

6. Spike test: 0 -> 목표의 150%, 5분 이내 급상승
   - HPA와 cold start, DB connection pool 고갈 여부 확인

## 8. 모니터링 체크리스트

Locust:

- RPS, 평균/p50/p95/p99 latency
- failure rate, HTTP status 분포
- endpoint별 latency와 실패율
- 테스트 VM CPU/RAM/network 사용률

Application:

- FastAPI pod CPU/memory, restart, OOMKilled
- Uvicorn worker 수와 request queue 징후
- `/metrics` Prometheus 지표
- OpenTelemetry trace에서 느린 span

PostgreSQL:

- CPU, memory, IOPS, active connection
- slow query, lock wait, dead tuple, index scan 여부
- connection pool 포화 여부

Redis:

- CPU, memory, evicted keys
- latency, blocked clients, ops/sec
- 검색 rate limit store와 background sync 영향

ML server:

- 추천/For You request latency
- timeout, 503, fallback 비율
- vLLM/GPU 사용률이 있다면 GPU util/memory

Kubernetes:

- HPA target metric, min/max replicas, scale-up/down 이벤트
- pod readiness delay, rolling update 중 영향
- ingress/API gateway latency, 4xx/5xx

## 9. 임시 성공 기준

실제 SLO가 정해지기 전 임시 기준이다.

- 5xx error rate: 1% 미만
- 전체 request failure rate: 1% 미만
- read API p95: 500 ms 이하
- search API p95: 1 s 이하
- ML 포함 API p95: 3 s 이하 또는 별도 fallback 정책 충족
- pod restart/OOMKilled 없음
- DB active connection이 max connection의 70% 이하

## 10. 추가로 필요한 정보

테스트 전에 아래 값을 확정해야 한다.

- 대상 환경 base URL과 `/api` prefix 처리 방식
- 스테이징/운영 중 어느 환경에서 진행할지, 테스트 가능 시간대
- 목표 동시 사용자 수, 목표 RPS, 기대 p95/p99 지연 시간
- 읽기/검색/ML/쓰기 트래픽 비율
- 테스트 계정 수와 계정별 페르소나 준비 상태
- 게시글 작성 테스트에 사용할 영화 UUID 목록
- 쓰기 API 허용 여부와 테스트 후 데이터 정리 정책
- 애플리케이션 pod requests/limits, worker 수, replica 수
- HPA min/max replicas, target metric, scale-up/down policy
- PostgreSQL/Redis/ML 서버 사양과 connection limit
- Grafana/Prometheus/로그/트레이싱 대시보드 URL
- WAF, rate limit, API Gateway timeout, ingress timeout 설정
- 테스트 VM의 위치, 사양, 공인 IP allowlist 여부

## 11. 결과 리포트 템플릿

- 테스트 일시:
- 대상 환경/버전/커밋:
- Locust VM 사양/위치:
- 실행 명령:
- 사용자 수/증가율/실행 시간:
- 포함 tags:
- p50/p95/p99:
- 최대 RPS:
- 실패율과 주요 오류:
- DB/Redis/ML 병목:
- HPA scale 이벤트:
- 결론:
- 다음 액션:
