from __future__ import annotations

import csv
import itertools
import base64
import json
import os
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from locust import HttpUser, between, task, tag


ROOT = Path(__file__).resolve().parent


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _split_env(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _normalize_prefix(prefix: str) -> str:
    prefix = prefix.strip()
    if not prefix or prefix == "/":
        return ""
    return "/" + prefix.strip("/")


API_PREFIX = _normalize_prefix(os.getenv("KAKAMU_API_PREFIX", "/api"))
USER_FILE = Path(os.getenv("KAKAMU_TEST_USERS_FILE", str(ROOT / "users.csv")))
ENABLE_WRITES = _env_bool("KAKAMU_ENABLE_WRITES", False)
ALLOW_ANONYMOUS = _env_bool("KAKAMU_ALLOW_ANONYMOUS", False)
TOKEN_REFRESH_SECONDS = _env_float("KAKAMU_TOKEN_REFRESH_SECONDS", 60 * 150)
MIN_WAIT_SECONDS = _env_float("KAKAMU_MIN_WAIT_SECONDS", 1.0)
MAX_WAIT_SECONDS = max(MIN_WAIT_SECONDS, _env_float("KAKAMU_MAX_WAIT_SECONDS", 4.0))

SEARCH_TERMS = _split_env(
    "KAKAMU_SEARCH_TERMS",
    "movie,action,drama,love,crime,family,comedy,thriller",
)
RECOMMEND_QUERIES = _split_env(
    "KAKAMU_RECOMMEND_QUERIES",
    "recommend a movie,light comedy,good thriller,family movie",
)
SEED_POST_IDS = [int(item) for item in _split_env("KAKAMU_SEED_POST_IDS") if item.isdigit()]
SEED_MOVIE_IDS = _split_env("KAKAMU_SEED_MOVIE_IDS")
SEED_USER_IDS = _split_env("KAKAMU_SEED_USER_IDS")


@dataclass(frozen=True)
class Credential:
    email: str
    password: str
    persona_id: str | None = None


def _load_credentials() -> list[Credential]:
    credentials: list[Credential] = []

    if USER_FILE.exists():
        with USER_FILE.open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            for row in reader:
                enabled = row.get("enabled", "true").strip().lower()
                if enabled in {"0", "false", "no", "n", "off"}:
                    continue

                email = row.get("email", "").strip()
                password = row.get("password", "").strip()
                persona_id = row.get("persona_id", "").strip() or None
                weight_raw = row.get("weight", "1").strip()

                if not email or not password:
                    continue

                try:
                    weight = max(1, int(weight_raw))
                except ValueError:
                    weight = 1

                credentials.extend(
                    Credential(email=email, password=password, persona_id=persona_id)
                    for _ in range(weight)
                )

    env_email = os.getenv("KAKAMU_TEST_EMAIL", "").strip()
    env_password = os.getenv("KAKAMU_TEST_PASSWORD", "").strip()
    if env_email and env_password:
        credentials.append(
            Credential(
                email=env_email,
                password=env_password,
                persona_id=os.getenv("KAKAMU_TEST_PERSONA_ID", "").strip() or None,
            )
        )

    if not credentials and ALLOW_ANONYMOUS:
        credentials.append(Credential(email="", password="", persona_id=None))

    if not credentials:
        raise RuntimeError(
            "No load-test credentials found. Create load_tests/users.csv from "
            "users.example.csv or set KAKAMU_TEST_EMAIL and KAKAMU_TEST_PASSWORD."
        )

    return credentials


CREDENTIALS = _load_credentials()
_credential_cycle = itertools.cycle(CREDENTIALS)
_credential_lock = threading.Lock()


def _next_credential() -> Credential:
    with _credential_lock:
        return next(_credential_cycle)


def api_path(path: str) -> str:
    normalized = "/" + path.lstrip("/")
    return f"{API_PREFIX}{normalized}"


def jwt_subject(token: str | None) -> str | None:
    if not token:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        claims = json.loads(decoded)
        subject = claims.get("sub")
        return str(subject) if subject else None
    except Exception:
        return None


class KakamuApiUser(HttpUser):
    wait_time = between(MIN_WAIT_SECONDS, MAX_WAIT_SECONDS)

    def on_start(self) -> None:
        self.credential = _next_credential()
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.user_id: str | None = None
        self.token_issued_at = 0.0
        self.persona_id: str | None = self.credential.persona_id
        self.persona_ids: list[str] = []
        self.post_ids: list[int] = list(SEED_POST_IDS)
        self.comment_ids: list[int] = []
        self.movie_ids: list[str] = list(SEED_MOVIE_IDS)
        self.user_ids: list[str] = list(SEED_USER_IDS)

        if self.credential.email:
            self.login()
            if self.access_token:
                self.load_personas()
                self.collect_feed()
                self.collect_movie_candidates()

    def _headers(self, include_persona: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if include_persona and self.persona_id:
            headers["X-Persona-Id"] = self.persona_id
        return headers

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        name: str,
        auth: bool = True,
        include_persona: bool = True,
        expected: tuple[int, ...] = (200,),
        **kwargs: Any,
    ) -> Any | None:
        if auth and not self.access_token:
            return None

        if auth:
            self.refresh_access_token_if_needed()

        headers = dict(kwargs.pop("headers", {}) or {})
        if auth:
            headers.update(self._headers(include_persona=include_persona))

        with self.client.request(
            method,
            api_path(path),
            name=name,
            headers=headers or None,
            catch_response=True,
            **kwargs,
        ) as response:
            if response.status_code not in expected:
                response.failure(f"{response.status_code}: {response.text[:300]}")
                return None

            if not response.content:
                response.success()
                return {}

            try:
                payload = response.json()
            except ValueError:
                response.failure(f"Invalid JSON response: {response.text[:300]}")
                return None

            response.success()
            return payload

    def login(self) -> None:
        payload = self._request_json(
            "POST",
            "/users/login/local",
            name="POST /users/login/local",
            auth=False,
            json={"email": self.credential.email, "password": self.credential.password},
        )
        if not payload:
            return

        self.access_token = payload.get("access_token")
        self.refresh_token = payload.get("refresh_token")
        self.user_id = jwt_subject(self.access_token)
        self.token_issued_at = time.monotonic()

    def refresh_access_token_if_needed(self) -> None:
        if not self.refresh_token:
            return
        if time.monotonic() - self.token_issued_at < TOKEN_REFRESH_SECONDS:
            return

        payload = self._request_json(
            "POST",
            "/users/login/refresh",
            name="POST /users/login/refresh",
            auth=False,
            json={"refresh_token": self.refresh_token},
        )
        if payload and payload.get("access_token"):
            self.access_token = payload.get("access_token")
            self.refresh_token = payload.get("refresh_token")
            self.user_id = jwt_subject(self.access_token)
            self.token_issued_at = time.monotonic()
        else:
            self.login()

    def _remember_unique(self, bucket: list[Any], value: Any, limit: int = 500) -> None:
        if value is None or value == "":
            return
        if value not in bucket:
            bucket.append(value)
        if len(bucket) > limit:
            del bucket[: len(bucket) - limit]

    def _remember_post_items(self, items: list[dict[str, Any]] | None) -> None:
        for item in items or []:
            post_id = item.get("id")
            if isinstance(post_id, int):
                self._remember_unique(self.post_ids, post_id)

            user = item.get("user") or {}
            self._remember_unique(self.user_ids, user.get("id"))

            for movie in item.get("movies") or []:
                self._remember_unique(self.movie_ids, movie.get("id"))

    def _remember_movie_items(self, items: list[dict[str, Any]] | None) -> None:
        for item in items or []:
            self._remember_unique(self.movie_ids, item.get("id"))

    def load_personas(self) -> None:
        payload = self._request_json("GET", "/personas", name="GET /personas")
        if not isinstance(payload, list):
            return

        self.persona_ids = [str(item["id"]) for item in payload if item.get("id")]
        if not self.user_id:
            for item in payload:
                if item.get("user_id"):
                    self.user_id = str(item["user_id"])
                    break
        if not self.persona_id and self.persona_ids:
            self.persona_id = random.choice(self.persona_ids)

    def collect_feed(self) -> None:
        payload = self._request_json(
            "GET",
            "/posts/",
            name="GET /posts",
            params={"limit": 20},
            include_persona=False,
        )
        if isinstance(payload, dict):
            self._remember_post_items(payload.get("items"))

    def collect_movie_candidates(self) -> None:
        payload = self._request_json(
            "GET",
            "/v1/search/movie",
            name="GET /v1/search/movie",
            params={"limit": 20, "skip": 0, "sort": "year_desc"},
            include_persona=False,
        )
        if isinstance(payload, dict):
            self._remember_movie_items(payload.get("items"))

    @tag("read", "health")
    @task(1)
    def health(self) -> None:
        self._request_json("GET", "/health", name="GET /health", auth=False)

    @tag("read", "account")
    @task(2)
    def read_account_context(self) -> None:
        if self.user_id:
            self._request_json(
                "GET",
                f"/users/{self.user_id}",
                name="GET /users/[user_id]",
                include_persona=False,
            )
        self.load_personas()

    @tag("read", "feed")
    @task(10)
    def browse_feed(self) -> None:
        self.collect_feed()

    @tag("read", "feed")
    @task(5)
    def read_post_detail(self) -> None:
        if not self.post_ids:
            self.collect_feed()
            return

        post_id = random.choice(self.post_ids)
        payload = self._request_json(
            "GET",
            f"/posts/{post_id}",
            name="GET /posts/[post_id]",
            include_persona=False,
        )
        if isinstance(payload, dict):
            self._remember_post_items([payload])

    @tag("read", "feed")
    @task(4)
    def read_comments(self) -> None:
        if not self.post_ids:
            self.collect_feed()
            return

        post_id = random.choice(self.post_ids)
        payload = self._request_json(
            "GET",
            f"/posts/{post_id}/comments/",
            name="GET /posts/[post_id]/comments",
            params={"page": 1, "size": 20},
            include_persona=False,
        )
        if not isinstance(payload, dict):
            return

        for item in payload.get("items") or []:
            comment_id = item.get("id")
            if isinstance(comment_id, int):
                self._remember_unique(self.comment_ids, comment_id)

    @tag("read", "search")
    @task(4)
    def search_live_posts(self) -> None:
        payload = self._request_json(
            "GET",
            "/v1/search/live",
            name="GET /v1/search/live",
            params={"q": random.choice(SEARCH_TERMS), "limit": 20},
            include_persona=False,
        )
        if isinstance(payload, dict):
            self._remember_post_items(payload.get("items"))

    @tag("read", "search", "ml")
    @task(3)
    def search_for_you_posts(self) -> None:
        payload = self._request_json(
            "GET",
            "/v1/search/for-you",
            name="GET /v1/search/for-you",
            params={"q": random.choice(SEARCH_TERMS), "limit": 20},
        )
        if isinstance(payload, dict):
            self._remember_post_items(payload.get("items"))

    @tag("read", "search")
    @task(3)
    def search_content(self) -> None:
        payload = self._request_json(
            "GET",
            "/v1/search/content",
            name="GET /v1/search/content",
            params={"q": random.choice(SEARCH_TERMS), "limit": 20, "sort": "accuracy"},
            include_persona=False,
        )
        if isinstance(payload, dict):
            self._remember_movie_items(payload.get("items"))

    @tag("read", "search")
    @task(2)
    def search_users(self) -> None:
        payload = self._request_json(
            "GET",
            "/v1/search/user",
            name="GET /v1/search/user",
            params={"q": random.choice(SEARCH_TERMS), "limit": 20},
            include_persona=False,
        )
        if not isinstance(payload, dict):
            return

        for item in payload.get("items") or []:
            self._remember_unique(self.user_ids, item.get("id"))

    @tag("read", "movie")
    @task(3)
    def search_movie_catalog(self) -> None:
        self.collect_movie_candidates()

    @tag("read", "metadata")
    @task(2)
    def read_metadata(self) -> None:
        if random.random() < 0.5:
            self._request_json("GET", "/v1/genre/list", name="GET /v1/genre/list", auth=False)
        else:
            self._request_json(
                "GET",
                "/v1/search/trend",
                name="GET /v1/search/trend",
                params={"limit": 10},
                auth=False,
            )

    @tag("read", "notification")
    @task(1)
    def read_notifications(self) -> None:
        self._request_json("GET", "/notifications/", name="GET /notifications")

    @tag("read", "feed")
    @task(1)
    def read_liked_posts(self) -> None:
        self._request_json(
            "GET",
            "/posts/liked",
            name="GET /posts/liked",
            params={"limit": 20},
        )

    @tag("read", "movie", "ml")
    @task(2)
    def recommend_movies(self) -> None:
        if not self.persona_id:
            self.load_personas()
            return

        payload = self._request_json(
            "GET",
            "/movies/recommend",
            name="GET /movies/recommend",
            params={"query": random.choice(RECOMMEND_QUERIES)},
        )
        if isinstance(payload, dict):
            self._remember_movie_items(payload.get("items"))

    @tag("write", "like")
    @task(1)
    def toggle_post_like(self) -> None:
        if not ENABLE_WRITES:
            return
        if not self.post_ids:
            self.collect_feed()
            return

        self._request_json(
            "POST",
            "/likes/",
            name="POST /likes",
            json={"target_type": "POST", "target_id": random.choice(self.post_ids)},
        )

    @tag("write", "comment")
    @task(1)
    def create_comment(self) -> None:
        if not ENABLE_WRITES:
            return
        if not self.persona_id:
            self.load_personas()
            return
        if not self.post_ids:
            self.collect_feed()
            return

        payload = self._request_json(
            "POST",
            f"/posts/{random.choice(self.post_ids)}/comments/",
            name="POST /posts/[post_id]/comments",
            expected=(201,),
            json={
                "content": f"load test comment {int(time.time())}",
                "parent_id": None,
                "is_spoiler": 0,
            },
        )
        if isinstance(payload, dict) and isinstance(payload.get("comment_id"), int):
            self._remember_unique(self.comment_ids, payload["comment_id"])

    @tag("write", "post")
    @task(1)
    def create_post(self) -> None:
        if not ENABLE_WRITES:
            return
        if not self.persona_id:
            self.load_personas()
            return
        if not self.movie_ids:
            self.collect_movie_candidates()
            return

        payload = self._request_json(
            "POST",
            "/posts/",
            name="POST /posts",
            expected=(201,),
            json={
                "title": f"load test post {int(time.time())}",
                "content": "Created by Locust during an approved KAKAMU load test.",
                "movie_ids": [random.choice(self.movie_ids)],
                "image_urls": [],
                "is_spoiler": 0,
            },
        )
        if isinstance(payload, dict) and isinstance(payload.get("post_id"), int):
            self._remember_unique(self.post_ids, payload["post_id"])

    @tag("write", "activity")
    @task(1)
    def log_activity(self) -> None:
        if not ENABLE_WRITES:
            return
        if not self.persona_id:
            self.load_personas()
            return

        target_type = "POST"
        target_id: int | str | None = random.choice(self.post_ids) if self.post_ids else None
        if target_id is None and self.movie_ids:
            target_type = "MOVIE"
            target_id = random.choice(self.movie_ids)
        if target_id is None:
            return

        self._request_json(
            "POST",
            "/logs/activity",
            name="POST /logs/activity",
            expected=(202,),
            json={
                "target_type": target_type,
                "target_id": target_id,
                "action": "VIEW",
                "duration_ms": random.randint(500, 9000),
                "metadata": {"source": "locust"},
            },
        )
