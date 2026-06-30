from __future__ import annotations

import argparse
import base64
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class UserCredential:
    sequence: int
    email: str
    password: str
    persona_id: str
    weight: str


def normalize_prefix(prefix: str) -> str:
    prefix = prefix.strip()
    if not prefix or prefix == "/":
        return ""
    return "/" + prefix.strip("/")


def api_url(host: str, api_prefix: str, path: str) -> str:
    base = host.rstrip("/") + normalize_prefix(api_prefix) + "/"
    return urljoin(base, path.lstrip("/"))


def jwt_claims(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:
        return {}


def rewrite_email_domain(email: str, from_domain: str | None, to_domain: str | None) -> str:
    if not to_domain:
        return email

    local, sep, domain = email.partition("@")
    if not sep:
        return email

    if from_domain and domain != from_domain:
        return email

    return f"{local}@{to_domain}"


def load_users(args: argparse.Namespace) -> list[UserCredential]:
    users: list[UserCredential] = []
    with args.users_csv.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        for row in reader:
            enabled = row.get("enabled", "true").strip().lower()
            if enabled in {"0", "false", "no", "n", "off"}:
                continue

            email = row.get("email", "").strip()
            password = row.get("password", "").strip()
            if not email or not password:
                continue

            email = rewrite_email_domain(email, args.from_domain, args.to_domain)
            users.append(
                UserCredential(
                    sequence=len(users) + 1,
                    email=email,
                    password=password,
                    persona_id=row.get("persona_id", "").strip(),
                    weight=row.get("weight", "1").strip() or "1",
                )
            )

    if args.offset:
        users = users[args.offset :]
    if args.count:
        users = users[: args.count]

    return users


def post_json(url: str, payload: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any] | None, str]:
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw), raw
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        return error.code, None, raw
    except URLError as error:
        return 0, None, str(error)
    except TimeoutError as error:
        return 0, None, str(error)


def issue_token(user: UserCredential, login_url: str, timeout: float) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    status, payload, raw = post_json(
        login_url,
        {"email": user.email, "password": user.password},
        timeout,
    )
    if status != 200 or not payload:
        return None, {
            "sequence": user.sequence,
            "email": user.email,
            "status": status,
            "error": raw[:500],
        }

    access_token = payload.get("access_token", "")
    refresh_token = payload.get("refresh_token", "")
    access_claims = jwt_claims(access_token)
    refresh_claims = jwt_claims(refresh_token)

    return {
        "sequence": user.sequence,
        "email": user.email,
        "persona_id": user.persona_id,
        "user_id": str(access_claims.get("sub") or ""),
        "provider": str(access_claims.get("provider") or ""),
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_exp": access_claims.get("exp", ""),
        "refresh_exp": refresh_claims.get("exp", ""),
        "issued_at_epoch": int(time.time()),
        "weight": user.weight,
        "enabled": "true",
    }, None


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Issue KAKAMU load-test tokens before running Locust."
    )
    parser.add_argument("--host", required=True, help="API host, e.g. http://dev.filma.cloud")
    parser.add_argument("--api-prefix", default="/api", help="API prefix. Default: /api")
    parser.add_argument("--users-csv", type=Path, default=ROOT / "users.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "tokens.csv")
    parser.add_argument("--failures-output", type=Path, default=ROOT / "tokens_failures.csv")
    parser.add_argument("--count", type=int, default=0, help="Number of users to process. 0 means all.")
    parser.add_argument("--offset", type=int, default=0, help="Skip this many enabled users.")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--progress-interval", type=int, default=100)
    parser.add_argument(
        "--from-domain",
        default="loadtest.local",
        help="Only rewrite emails with this domain. Use empty string to rewrite all domains.",
    )
    parser.add_argument(
        "--to-domain",
        default="loadtest.filma.cloud",
        help="Rewrite matching user emails to this domain. Use empty string to disable.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.concurrency = max(1, args.concurrency)
    args.from_domain = args.from_domain.strip() or None
    args.to_domain = args.to_domain.strip() or None

    users = load_users(args)
    if not users:
        raise SystemExit(f"No users found in {args.users_csv}")

    login_url = api_url(args.host, args.api_prefix, "/users/login/local")
    print(f"Issuing tokens for {len(users)} users")
    print(f"Login URL: {login_url}")
    print(f"Concurrency: {args.concurrency}")

    token_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []
    started_at = time.time()

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(issue_token, user, login_url, args.timeout): user
            for user in users
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            token_row, failure_row = future.result()
            if token_row:
                token_rows.append(token_row)
            if failure_row:
                failure_rows.append(failure_row)

            if completed % args.progress_interval == 0 or completed == len(users):
                elapsed = time.time() - started_at
                print(
                    f"{completed}/{len(users)} done "
                    f"(success={len(token_rows)}, failed={len(failure_rows)}, elapsed={elapsed:.1f}s)"
                )

    token_rows.sort(key=lambda row: int(row["sequence"]))
    failure_rows.sort(key=lambda row: int(row["sequence"]))

    write_csv(
        args.output,
        token_rows,
        [
            "sequence",
            "email",
            "persona_id",
            "user_id",
            "provider",
            "access_token",
            "refresh_token",
            "access_exp",
            "refresh_exp",
            "issued_at_epoch",
            "weight",
            "enabled",
        ],
    )
    write_csv(args.failures_output, failure_rows, ["sequence", "email", "status", "error"])

    print(f"Token CSV: {args.output} ({len(token_rows)} rows)")
    print(f"Failure CSV: {args.failures_output} ({len(failure_rows)} rows)")
    if failure_rows:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
