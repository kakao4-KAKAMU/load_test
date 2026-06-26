# KAKAMU 2000 VU Load Test Execution Plan

이 문서는 부하테스트 전용 VM에서 더미 유저 2000명을 생성한 뒤 Locust 부하테스트를 단계적으로 실행하는 최종 명령어 계획이다.

결론부터 말하면 순서는 맞다.

1. 테스트 대상 DB에 더미 유저 2000명 생성
2. 생성된 `users.csv`로 로그인 smoke test
3. Locust를 낮은 VU부터 단계적으로 증가
4. 최종 2000 VU 테스트 실행

운영 DB에 바로 실행하지 말고 스테이징 또는 부하테스트 전용 환경에서 먼저 검증한다.

## 0. 전제

예상 폴더:

```bash
cd ~/load_test
```

Windows PowerShell에서 실행한다면:

```powershell
cd "C:\Users\oyt20\OneDrive\바탕 화면\load_test"
```

필요 파일:

- `locustfile.py`
- `seed_dummy_users.py`
- `requirements-loadtest.txt`
- `users.example.csv`

## 1. VM 기본 준비

Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-loadtest.txt
mkdir -p reports
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-loadtest.txt
New-Item -ItemType Directory -Force reports | Out-Null
```

Linux VM에서 고동시 연결을 만들 경우 아래 값도 확인한다.

```bash
ulimit -n
```

권장:

```bash
ulimit -n 65535
```

영구 적용은 VM 이미지/OS 정책에 맞춰 `/etc/security/limits.conf`, systemd 설정 등을 사용한다.

## 2. 환경 변수 설정

Linux:

```bash
export DATABASE_URL='postgresql://USER:PASSWORD@DB_HOST:5432/DB_NAME'
export KAKAMU_DUMMY_PASSWORD='LoadTest1234!'
export KAKAMU_API_PREFIX='/api'
export KAKAMU_SEARCH_TERMS='영화,액션,드라마,코미디,스릴러,가족,로맨스,범죄'
export KAKAMU_RECOMMEND_QUERIES='액션 영화 추천,가볍게 볼 영화 추천,스릴러 영화 추천,가족 영화 추천'
```

Windows PowerShell:

```powershell
$env:DATABASE_URL = 'postgresql://USER:PASSWORD@DB_HOST:5432/DB_NAME'
$env:KAKAMU_DUMMY_PASSWORD = 'LoadTest1234!'
$env:KAKAMU_API_PREFIX = '/api'
$env:KAKAMU_SEARCH_TERMS = '영화,액션,드라마,코미디,스릴러,가족,로맨스,범죄'
$env:KAKAMU_RECOMMEND_QUERIES = '액션 영화 추천,가볍게 볼 영화 추천,스릴러 영화 추천,가족 영화 추천'
```

`KAKAMU_API_PREFIX`는 API가 `https://host/api/...` 형태면 `/api`로 둔다. 프록시가 `/api`를 제거하고 앱에 전달한다면 빈 문자열로 바꾼다.

## 3. 더미 유저 2000명 생성

먼저 dry-run으로 생성 범위를 확인한다.

Linux:

```bash
python seed_dummy_users.py \
  --count 2000 \
  --email-prefix kakamu-load \
  --email-domain loadtest.local \
  --csv-path users.csv \
  --dry-run
```

Windows PowerShell:

```powershell
python .\seed_dummy_users.py `
  --count 2000 `
  --email-prefix kakamu-load `
  --email-domain loadtest.local `
  --csv-path users.csv `
  --dry-run
```

문제가 없으면 실제 생성한다.

Linux:

```bash
python seed_dummy_users.py \
  --count 2000 \
  --commit-batch-size 100 \
  --email-prefix kakamu-load \
  --email-domain loadtest.local \
  --csv-path users.csv
```

Windows PowerShell:

```powershell
python .\seed_dummy_users.py `
  --count 2000 `
  --commit-batch-size 100 `
  --email-prefix kakamu-load `
  --email-domain loadtest.local `
  --csv-path users.csv
```

생성 결과:

- DB `user` 2000명
- DB `local_auth` 2000개
- DB `persona` 2000개
- Locust용 `users.csv`

같은 prefix/domain으로 다시 실행하면 같은 이메일 기준으로 비밀번호 해시와 유저 정보를 갱신한다.
스크립트는 기본적으로 100명마다 commit하고 진행 상황을 출력한다.
전화번호는 기본적으로 `01090` + 6자리 index 형식으로 생성한다. 예: `01090000001`. 20자를 넘는 전화번호는 조용히 잘라내지 않고 에러로 중단한다.

## 4. 단일 로그인 확인

API URL을 실제 값으로 바꾼다.

Linux:

```bash
curl -X POST 'https://YOUR-API-HOST/api/users/login/local' \
  -H 'Content-Type: application/json' \
  -d '{"email":"kakamu-load-000001@loadtest.local","password":"LoadTest1234!"}'
```

정상이라면 `access_token`, `refresh_token`이 반환된다.

## 5. Locust Smoke Test

목적:

- API prefix 확인
- DB seed 계정 로그인 확인
- persona 조회 확인
- Locust VM 자체 CPU/RAM/network 확인

```bash
locust -f locustfile.py \
  --host https://YOUR-API-HOST \
  --headless \
  --users 10 \
  --spawn-rate 2 \
  --run-time 5m \
  --tags health account feed metadata \
  --html reports/00-smoke.html \
  --csv reports/00-smoke
```

Smoke 단계에서 401, 403, 404, 429, 5xx가 반복되면 2000 VU로 넘어가지 않는다.

## 6. Backend-Only Ramp Test

먼저 ML과 쓰기를 제외하고 백엔드/DB/Redis 기준선을 잡는다.

### 100 VU

```bash
locust -f locustfile.py \
  --host https://YOUR-API-HOST \
  --headless \
  --users 100 \
  --spawn-rate 10 \
  --run-time 15m \
  --exclude-tags ml write \
  --html reports/01-backend-100vu.html \
  --csv reports/01-backend-100vu
```

### 500 VU

```bash
locust -f locustfile.py \
  --host https://YOUR-API-HOST \
  --headless \
  --users 500 \
  --spawn-rate 25 \
  --run-time 20m \
  --exclude-tags ml write \
  --html reports/02-backend-500vu.html \
  --csv reports/02-backend-500vu
```

### 1000 VU

```bash
locust -f locustfile.py \
  --host https://YOUR-API-HOST \
  --headless \
  --users 1000 \
  --spawn-rate 50 \
  --run-time 25m \
  --exclude-tags ml write \
  --html reports/03-backend-1000vu.html \
  --csv reports/03-backend-1000vu
```

### 1500 VU

```bash
locust -f locustfile.py \
  --host https://YOUR-API-HOST \
  --headless \
  --users 1500 \
  --spawn-rate 75 \
  --run-time 30m \
  --exclude-tags ml write \
  --html reports/04-backend-1500vu.html \
  --csv reports/04-backend-1500vu
```

### 2000 VU

```bash
locust -f locustfile.py \
  --host https://YOUR-API-HOST \
  --headless \
  --users 2000 \
  --spawn-rate 100 \
  --run-time 30m \
  --exclude-tags ml write \
  --html reports/05-backend-2000vu.html \
  --csv reports/05-backend-2000vu
```

## 7. ML 포함 테스트

Backend-only 2000 VU가 안정적일 때만 진행한다. ML 서버 또는 추천 서버 병목을 별도로 보기 위한 테스트다.

```bash
locust -f locustfile.py \
  --host https://YOUR-API-HOST \
  --headless \
  --users 500 \
  --spawn-rate 25 \
  --run-time 20m \
  --tags read ml \
  --html reports/06-ml-500vu.html \
  --csv reports/06-ml-500vu
```

ML 포함 2000 VU는 ML 서버 용량과 HPA/timeout 정책 확인 후 진행한다.

```bash
locust -f locustfile.py \
  --host https://YOUR-API-HOST \
  --headless \
  --users 2000 \
  --spawn-rate 100 \
  --run-time 30m \
  --tags read ml \
  --html reports/07-ml-2000vu.html \
  --csv reports/07-ml-2000vu
```

## 8. 쓰기 포함 테스트

쓰기 테스트는 데이터가 실제로 생성/변경된다. 테스트 환경에서만 실행한다.

```bash
export KAKAMU_ENABLE_WRITES=true
```

```bash
locust -f locustfile.py \
  --host https://YOUR-API-HOST \
  --headless \
  --users 200 \
  --spawn-rate 10 \
  --run-time 20m \
  --tags read write \
  --html reports/08-write-200vu.html \
  --csv reports/08-write-200vu
```

쓰기 포함 2000 VU는 게시글/댓글/좋아요 데이터가 대량 생성되므로, 사전 승인과 정리 정책이 있을 때만 실행한다.

## 9. 2000 VU에서 특히 봐야 할 것

Locust VM:

- CPU 80~90% 이상 지속 여부
- Memory 증가 여부
- Network bandwidth 포화 여부
- Locust가 지정한 users/spawn-rate를 따라가는지 여부

AWS/Kubernetes:

- pod CPU/memory
- HPA scale-out 이벤트와 scale-out 지연 시간
- pod restart/OOMKilled
- Ingress/ALB/API Gateway 4xx/5xx/timeout

DB:

- active connection
- slow query
- lock wait
- CPU/IOPS

Redis:

- latency
- ops/sec
- memory
- evicted keys

ML:

- request latency
- 503/timeout/fallback 비율
- GPU/worker 사용률

## 10. 중단 기준

아래 중 하나가 발생하면 테스트를 중단하고 병목을 확인한다.

- 5xx error rate 1% 이상 지속
- p95 latency가 목표치의 2배 이상으로 5분 이상 지속
- DB connection이 한계의 80% 이상 지속
- pod restart/OOMKilled 발생
- Locust VM CPU가 90% 이상 지속
- 네트워크 timeout이 급증
- 검색 API 429가 대량 발생

## 11. 실행 순서 요약

최종 순서는 다음이다.

```bash
# 1. 의존성 설치
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-loadtest.txt

# 2. 환경 변수
export DATABASE_URL='postgresql://USER:PASSWORD@DB_HOST:5432/DB_NAME'
export KAKAMU_DUMMY_PASSWORD='LoadTest1234!'

# 3. 더미 유저 2000명 생성
python seed_dummy_users.py --count 2000 --commit-batch-size 100 --csv-path users.csv

# 4. smoke
locust -f locustfile.py --host https://YOUR-API-HOST --headless --users 10 --spawn-rate 2 --run-time 5m --tags health account feed metadata --html reports/00-smoke.html --csv reports/00-smoke

# 5. 단계별 증가
locust -f locustfile.py --host https://YOUR-API-HOST --headless --users 100 --spawn-rate 10 --run-time 15m --exclude-tags ml write --html reports/01-backend-100vu.html --csv reports/01-backend-100vu
locust -f locustfile.py --host https://YOUR-API-HOST --headless --users 500 --spawn-rate 25 --run-time 20m --exclude-tags ml write --html reports/02-backend-500vu.html --csv reports/02-backend-500vu
locust -f locustfile.py --host https://YOUR-API-HOST --headless --users 1000 --spawn-rate 50 --run-time 25m --exclude-tags ml write --html reports/03-backend-1000vu.html --csv reports/03-backend-1000vu
locust -f locustfile.py --host https://YOUR-API-HOST --headless --users 1500 --spawn-rate 75 --run-time 30m --exclude-tags ml write --html reports/04-backend-1500vu.html --csv reports/04-backend-1500vu
locust -f locustfile.py --host https://YOUR-API-HOST --headless --users 2000 --spawn-rate 100 --run-time 30m --exclude-tags ml write --html reports/05-backend-2000vu.html --csv reports/05-backend-2000vu
```

## 12. 메모

2000 VU면 더미 유저도 최소 2000명이 필요하다. 검색 API에 사용자 기준 rate limit이 있으므로, 여유 있게 2500명 정도 생성해도 좋다.

```bash
python seed_dummy_users.py --count 2500 --commit-batch-size 100 --csv-path users.csv
```

다만 이번 계획의 기준은 요청대로 2000명이다.
