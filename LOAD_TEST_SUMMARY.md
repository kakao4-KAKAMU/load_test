# 부하 테스트 시나리오 요약

`locustfile.py` 코드와 `LOAD_TEST_PLAN.md` 문서를 분석하여 정리한 실제 부하 테스트 계획과 API별 가중치(Weight)가 반영된 시나리오 표입니다.

## 1. API 호출 부하 분산 표 (locustfile.py 기준)

`locustfile.py`에 정의된 `@task()` 가중치(Weight)의 총합은 **47**이며, 이를 바탕으로 각 작업이 전체 부하에서 차지하는 비중을 계산했습니다.

| 영역 (Domain) | 작업명 (Task) | 대상 API (Endpoint) | 가중치 | 비중 (%) | 부여된 태그 (Tags) |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **Feed** | 피드 조회 (browse_feed) | `GET /posts/` | 10 | 21.3% | `read`, `feed` |
| | 게시글 상세 조회 (read_post_detail) | `GET /posts/{id}` | 5 | 10.6% | `read`, `feed` |
| | 댓글 조회 (read_comments) | `GET /posts/{id}/comments/` | 4 | 8.5% | `read`, `feed` |
| | 좋아요한 글 조회 (read_liked_posts) | `GET /posts/liked` | 1 | 2.1% | `read`, `feed` |
| **Search** | 실시간 검색 (search_live_posts) | `GET /v1/search/live` | 4 | 8.5% | `read`, `search` |
| | For You 검색 (search_for_you_posts) | `GET /v1/search/for-you` | 3 | 6.4% | `read`, `search`, `ml` |
| | 콘텐츠 검색 (search_content) | `GET /v1/search/content` | 3 | 6.4% | `read`, `search` |
| | 사용자 검색 (search_users) | `GET /v1/search/user` | 2 | 4.3% | `read`, `search` |
| **Movie** | 영화 카탈로그 검색 (search_movie_catalog)| `GET /v1/search/movie` | 3 | 6.4% | `read`, `movie` |
| | 영화 추천 (recommend_movies) | `GET /movies/recommend` | 2 | 4.3% | `read`, `movie`, `ml` |
| **Write** * | 좋아요 토글 (toggle_post_like) | `POST /likes/` | 1 | 2.1% | `write`, `like` |
| | 댓글 작성 (create_comment) | `POST /posts/{id}/comments/` | 1 | 2.1% | `write`, `comment` |
| | 게시글 작성 (create_post) | `POST /posts/` | 1 | 2.1% | `write`, `post` |
| | 활동 로그 저장 (log_activity) | `POST /logs/activity` | 1 | 2.1% | `write`, `activity` |
| **Account** | 사용자/페르소나 조회 (read_account_context)| `GET /users/me`, `GET /personas` | 2 | 4.3% | `read`, `account` |
| **Metadata** | 장르/트렌드 조회 (read_metadata) | `GET /v1/genre/list` 등 | 2 | 4.3% | `read`, `metadata` |
| **Noti** | 알림 조회 (read_notifications) | `GET /notifications/` | 1 | 2.1% | `read`, `notification` |
| **Health** | 상태 확인 (health) | `GET /health` | 1 | 2.1% | `read`, `health` |
| **총합** | | | **47** | **100%** | |

*(참고: Write 영역의 작업들은 `KAKAMU_ENABLE_WRITES=true` 환경 변수가 설정된 경우에만 실제로 동작합니다.)*

## 2. 단계별 부하 테스트 실행 계획 (LOAD_TEST_PLAN.md 기준)

목적에 따라 명령어의 `--tags`와 `--exclude-tags`를 활용하여 시나리오를 통제합니다.

| 테스트 단계 | 목적 및 확인 사항 | 가상 유저 (VU) | 진행 시간 | 포함 대상 태그 |
| :--- | :--- | :--- | :--- | :--- |
| **1. Smoke Test** | 인증, 시스템 기본 동작 및 모니터링 연동 정상 여부 확인 | 5명 | 5분 | `health`, `account`, `feed`, `metadata` |
| **2. Baseline Test** | ML 및 쓰기 작업을 제외한 순수 백엔드 읽기 성능의 기준선 확보 | 50 ~ 100명 | 30분 | `--exclude-tags ml write` |
| **3. ML Read Test** | 추천 시스템(ML 서버)이 포함된 읽기 작업의 지연 시간 및 성능 확인 | 100명 | 30분 | `read`, `ml` |
| **4. Ramp Test** | 점진적 부하 증가에 따른 오토스케일링(HPA) 반응 및 지연 시간 변화 관찰 | 50 ➡️ 300명 (단계별 증가) | 각 15~20분 | 목표 시나리오에 맞춰 유동적 |
| **5. Write-mix Test** | 쓰기 트래픽이 포함된 복합 부하 테스트 (사전 승인된 환경에서만 진행) | 50명 | 20분 | `read`, `write` (`KAKAMU_ENABLE_WRITES=true`) |
| **6. Stress Test** | 목표 트래픽의 120~200%를 인가하여 병목 구간 및 429/5xx 발생 지점 파악 | 120~200% | 20~30분 | 부하 유발 목표 태그 |
| **7. Soak Test** | 장시간 트래픽 유지 시 메모리 누수, DB 커넥션 누수 등 안정성 확인 | 목표 트래픽 | 2~4시간 | 주요 시나리오 전체 |
