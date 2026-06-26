# Load-Test Dummy User Seed

`seed_dummy_users.py`는 부하테스트용 계정을 DB에 직접 생성하고, Locust가 읽을 `users.csv`를 같이 만든다.

생성 대상:

- `user`
- `local_auth`
- `persona`
- `users.csv`

`user` 테이블만 넣으면 이메일/비밀번호 로그인이 되지 않는다. 로컬 로그인 API는 `local_auth.email`과 `local_auth.password_hash`를 사용하므로 `local_auth`까지 반드시 필요하다.

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-loadtest.txt
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-loadtest.txt
```

## 실행 전 확인

운영 DB에 직접 실행하지 말고, 스테이징 또는 부하테스트 전용 DB에서 먼저 검증한다.

필요한 값:

- `DATABASE_URL`
- 생성할 유저 수
- 테스트용 공통 비밀번호
- 이메일 prefix/domain

## 예시

```bash
export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DBNAME'
export KAKAMU_DUMMY_PASSWORD='LoadTest1234!'

python seed_dummy_users.py \
  --count 1000 \
  --commit-batch-size 100 \
  --email-prefix kakamu-load \
  --email-domain loadtest.local \
  --csv-path users.csv
```

Windows PowerShell:

```powershell
$env:DATABASE_URL = 'postgresql://USER:PASSWORD@HOST:5432/DBNAME'
$env:KAKAMU_DUMMY_PASSWORD = 'LoadTest1234!'

python .\seed_dummy_users.py `
  --count 1000 `
  --commit-batch-size 100 `
  --email-prefix kakamu-load `
  --email-domain loadtest.local `
  --csv-path users.csv
```

생성된 `users.csv`를 그대로 Locust에서 사용한다.

```bash
locust -f locustfile.py \
  --host https://YOUR-API-HOST \
  --headless --users 100 --spawn-rate 10 --run-time 30m
```

## 동작 방식

- `ci_value`는 `sha256(username + phone)`으로 생성한다.
- 전화번호는 `phone_prefix + 6자리 index`로 만든다. 기본값은 `01090`이라 첫 번째 번호는 `01090000001`이다.
- 생성된 전화번호가 `user.phone` 컬럼 길이인 20자를 넘으면 조용히 자르지 않고 실패한다.
- `password_hash`는 백엔드와 같은 방식으로 `sha256(password)` 후 bcrypt 해시를 만든다.
- 같은 이메일로 다시 실행하면 해당 계정의 비밀번호 해시와 유저 정보를 갱신한다.
- 각 유저마다 활성 상태의 `LOAD_TEST` persona를 하나 만든다.
- 기본적으로 100명마다 commit하고 진행 상황을 출력한다.

## 빠른 점검

DB에 넣지 않고 생성 범위만 확인:

```bash
python seed_dummy_users.py --count 10 --dry-run
```

로그인 확인:

```bash
curl -X POST 'https://YOUR-API-HOST/api/users/login/local' \
  -H 'Content-Type: application/json' \
  -d '{"email":"kakamu-load-000001@loadtest.local","password":"LoadTest1234!"}'
```
