from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PgConnection = Any


@dataclass(frozen=True)
class SeededUser:
    email: str
    password: str
    user_id: str
    persona_id: str


def build_password_hash(password: str, rounds: int) -> str:
    import bcrypt

    password_hash = hashlib.sha256(password.encode()).hexdigest().encode()
    return bcrypt.hashpw(password_hash, bcrypt.gensalt(rounds=rounds)).decode()


def build_ci_value(username: str, phone: str) -> str:
    return hashlib.sha256(f"{username}{phone}".encode("utf-8")).hexdigest()


def build_phone(prefix: str, index: int) -> str:
    phone = f"{prefix}{index:06d}"
    if len(phone) > 20:
        raise ValueError(
            f"Generated phone number is longer than 20 characters: {phone}. "
            "Shorten --phone-prefix or use a smaller --start-index."
        )
    return phone


def normalize_database_url(raw: str | None) -> str:
    if not raw:
        raise SystemExit(
            "DATABASE_URL is required. Pass --database-url or set DATABASE_URL."
        )
    return raw


def get_existing_user_id_by_email(conn: PgConnection, email: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT user_id::text FROM local_auth WHERE email = %s", (email,))
        row = cur.fetchone()
        return row[0] if row else None


def get_existing_user_id_by_ci(conn: PgConnection, ci_value: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute('SELECT id::text FROM "user" WHERE ci_value = %s', (ci_value,))
        row = cur.fetchone()
        return row[0] if row else None


def upsert_user(
    conn: PgConnection,
    *,
    user_id: str,
    ci_value: str,
    username: str,
    phone: str,
    nickname: str,
    tag: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO "user" (
                id,
                ci_value,
                username,
                phone,
                nickname,
                tag,
                profile_image_url,
                profile_msg,
                status,
                deleted_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, NULL, 'load test dummy user', 'ACTIVE', NULL)
            ON CONFLICT (ci_value) DO UPDATE SET
                username = EXCLUDED.username,
                phone = EXCLUDED.phone,
                nickname = EXCLUDED.nickname,
                tag = EXCLUDED.tag,
                status = 'ACTIVE',
                deleted_at = NULL,
                updated_at = now()
            """,
            (user_id, ci_value, username, phone, nickname, tag),
        )


def upsert_local_auth(
    conn: PgConnection,
    *,
    user_id: str,
    email: str,
    password_hash: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO local_auth (user_id, email, password_hash, email_verified)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (email) DO UPDATE SET
                password_hash = EXCLUDED.password_hash,
                email_verified = 1
            """,
            (user_id, email, password_hash),
        )


def ensure_persona(
    conn: PgConnection,
    *,
    user_id: str,
    nickname: str,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text
            FROM persona
            WHERE user_id = %s
              AND nickname = %s
              AND status <> 'DELETED'
            LIMIT 1
            """,
            (user_id, nickname),
        )
        row = cur.fetchone()
        if row:
            return row[0]

        persona_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO persona (
                id,
                user_id,
                nickname,
                profile_image_url,
                persona_type,
                status,
                deleted_at
            )
            VALUES (%s, %s, %s, '/static/default_profile_image.png', 'LOAD_TEST', 'ACTIVE', NULL)
            """,
            (persona_id, user_id, nickname),
        )
        return persona_id


def seed_one_user(conn: PgConnection, args: argparse.Namespace, index: int) -> SeededUser:
    email = f"{args.email_prefix}-{index:06d}@{args.email_domain}"
    username = f"{args.username_prefix}{index:06d}"
    nickname = f"{args.nickname_prefix}{index:06d}"
    persona_nickname = f"{args.persona_prefix}{index:06d}"
    phone = build_phone(args.phone_prefix, index)
    ci_value = build_ci_value(username, phone)

    existing_user_id = get_existing_user_id_by_email(conn, email)
    if existing_user_id:
        user_id = existing_user_id
    else:
        user_id = get_existing_user_id_by_ci(conn, ci_value) or str(uuid.uuid4())

    upsert_user(
        conn,
        user_id=user_id,
        ci_value=ci_value,
        username=username,
        phone=phone,
        nickname=nickname,
        tag=args.tag,
    )
    upsert_local_auth(
        conn,
        user_id=user_id,
        email=email,
        password_hash=build_password_hash(args.password, args.bcrypt_rounds),
    )
    persona_id = ensure_persona(conn, user_id=user_id, nickname=persona_nickname)

    return SeededUser(
        email=email,
        password=args.password,
        user_id=user_id,
        persona_id=persona_id,
    )


def write_locust_csv(path: Path, users: list[SeededUser]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["email", "password", "persona_id", "weight", "enabled"],
        )
        writer.writeheader()
        for user in users:
            writer.writerow(
                {
                    "email": user.email,
                    "password": user.password,
                    "persona_id": user.persona_id,
                    "weight": 1,
                    "enabled": "true",
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed KAKAMU load-test users, local_auth rows, personas, and Locust CSV."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="PostgreSQL URL. Defaults to DATABASE_URL.",
    )
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--email-prefix", default="kakamu-load")
    parser.add_argument("--email-domain", default="loadtest.local")
    parser.add_argument("--username-prefix", default="load_user_")
    parser.add_argument("--nickname-prefix", default="load_user_")
    parser.add_argument("--persona-prefix", default="load_persona_")
    parser.add_argument("--phone-prefix", default="01090")
    parser.add_argument("--tag", default="0000")
    parser.add_argument(
        "--password",
        default=os.getenv("KAKAMU_DUMMY_PASSWORD", "LoadTest1234!"),
        help="Plain password written to users.csv and hashed into local_auth.",
    )
    parser.add_argument(
        "--bcrypt-rounds",
        type=int,
        default=12,
        help="bcrypt cost. 12 matches the application default; lower values seed faster.",
    )
    parser.add_argument(
        "--commit-batch-size",
        type=int,
        default=100,
        help="Commit and print progress after this many users.",
    )
    parser.add_argument(
        "--csv-path",
        type=Path,
        default=SCRIPT_DIR / "users.csv",
        help="Locust credentials CSV output path.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be generated. Does not connect to the database.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.count < 1:
        raise SystemExit("--count must be at least 1.")
    if not 4 <= args.bcrypt_rounds <= 31:
        raise SystemExit("--bcrypt-rounds must be between 4 and 31.")
    if args.commit_batch_size < 1:
        raise SystemExit("--commit-batch-size must be at least 1.")

    first_email = f"{args.email_prefix}-{args.start_index:06d}@{args.email_domain}"
    last_index = args.start_index + args.count - 1
    last_email = f"{args.email_prefix}-{last_index:06d}@{args.email_domain}"

    if args.dry_run:
        print(f"Would create {args.count} users.")
        print(f"Email range: {first_email} ... {last_email}")
        print(f"CSV path: {args.csv_path}")
        return 0

    database_url = normalize_database_url(args.database_url)
    seeded_users: list[SeededUser] = []

    import psycopg2

    print(
        f"Seeding {args.count} users. Commit batch size: {args.commit_batch_size}",
        flush=True,
    )
    with psycopg2.connect(database_url) as conn:
        for generated_count, index in enumerate(
            range(args.start_index, args.start_index + args.count),
            start=1,
        ):
            seeded_users.append(seed_one_user(conn, args, index))
            if generated_count % args.commit_batch_size == 0:
                conn.commit()
                print(
                    f"  {generated_count}/{args.count} users committed "
                    f"(last email: {seeded_users[-1].email})",
                    flush=True,
                )

        if len(seeded_users) % args.commit_batch_size != 0:
            conn.commit()
            print(
                f"  {len(seeded_users)}/{args.count} users committed "
                f"(last email: {seeded_users[-1].email})",
                flush=True,
            )

    write_locust_csv(args.csv_path, seeded_users)

    print(f"Seeded {len(seeded_users)} load-test users.")
    print(f"Email range: {first_email} ... {last_email}")
    print(f"Locust CSV: {args.csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
