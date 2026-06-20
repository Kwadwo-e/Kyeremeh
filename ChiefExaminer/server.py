#!/usr/bin/env python3
import hashlib
import hmac
import ipaddress
import io
import json
import mimetypes
import os
import posixpath
import random
import re
import secrets
import socket
import sqlite3
import sys
import csv
import tempfile
import traceback
import urllib.parse
import zipfile
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default as email_policy
from html import escape as html_escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree


def app_base_dir():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def default_data_dir():
    if not getattr(sys, "frozen", False):
        return BASE_DIR / "data"
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(root or Path.home()) / "ChiefExam"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ChiefExam"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "chiefexam"


BASE_DIR = app_base_dir()
PUBLIC_DIR = BASE_DIR / "public"
DATA_DIR = default_data_dir()
DEFAULT_DB_PATH = DATA_DIR / "kyeremeh.sqlite3"
LEGACY_DB_PATH = DATA_DIR / "chiefexam.sqlite3"
DB_PATH = Path(
    os.environ.get("KYEREMEH_DB")
    or os.environ.get("CHIEFEXAM_DB")
    or DEFAULT_DB_PATH
)
PUBLIC_URL = (
    os.environ.get("KYEREMEH_PUBLIC_URL")
    or os.environ.get("CHIEFEXAM_PUBLIC_URL")
)
SESSION_COOKIE = "kyeremeh_session"
SESSION_HOURS = int(
    os.environ.get("KYEREMEH_SESSION_HOURS")
    or os.environ.get("CHIEFEXAM_SESSION_HOURS", "8")
)
MAX_JSON_BYTES = 1_000_000
MAX_UPLOAD_BYTES = 8_000_000

mimetypes.add_type("application/manifest+json", ".webmanifest")


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat(timespec="seconds")


def new_id(prefix):
    return f"{prefix}_{secrets.token_urlsafe(16)}"


def connect_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH == DEFAULT_DB_PATH and not DB_PATH.exists() and LEGACY_DB_PATH.exists():
        LEGACY_DB_PATH.rename(DB_PATH)
    with connect_db() as db:
        schema = (BASE_DIR / "schema.sql").read_text(encoding="utf-8")
        db.executescript(schema)
        migrate_db(db)
        admin_count = db.execute(
            "SELECT COUNT(*) AS count FROM users WHERE role = 'admin'"
        ).fetchone()["count"]
        if admin_count == 0:
            salt, password_hash = hash_password(
                os.environ.get("KYEREMEH_ADMIN_PASSWORD")
                or os.environ.get("CHIEFEXAM_ADMIN_PASSWORD", "Admin@12345")
            )
            db.execute(
                """
                INSERT INTO users (
                  id, role, full_name, username, password_hash,
                  password_salt, approved, approved_at, created_at
                )
                VALUES (?, 'admin', ?, 'admin', ?, ?, 1, ?, ?)
                """,
                (
                    new_id("usr"),
                    "System Administrator",
                    password_hash,
                    salt,
                    iso_now(),
                    iso_now(),
                ),
            )
        db.commit()


def migrate_db(db):
    users_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()
    sessions_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
    ).fetchone()
    if (
        users_sql
        and users_sql["sql"]
        and "'examiner'" not in users_sql["sql"]
    ) or (
        sessions_sql
        and sessions_sql["sql"]
        and "'examiner'" not in sessions_sql["sql"]
    ):
        rebuild_role_tables(db)

    user_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()
    }
    if "approved" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN approved INTEGER NOT NULL DEFAULT 1")
    if "approved_at" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN approved_at TEXT")
        db.execute(
            "UPDATE users SET approved_at = created_at WHERE approved = 1 AND approved_at IS NULL"
        )
    if "approved_by" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN approved_by TEXT REFERENCES users(id) ON DELETE SET NULL")
    if "suspended" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN suspended INTEGER NOT NULL DEFAULT 0")

    exam_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(exams)").fetchall()
    }
    if "expires_at" not in exam_columns:
        db.execute("ALTER TABLE exams ADD COLUMN expires_at TEXT")
    if "created_by" not in exam_columns:
        db.execute("ALTER TABLE exams ADD COLUMN created_by TEXT REFERENCES users(id) ON DELETE SET NULL")
        admin = db.execute(
            "SELECT id FROM users WHERE role = 'admin' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        if admin:
            db.execute(
                "UPDATE exams SET created_by = ? WHERE created_by IS NULL OR created_by = ''",
                (admin["id"],),
            )
    if "max_attempts" not in exam_columns:
        db.execute("ALTER TABLE exams ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 1")
    rows = db.execute(
        """
        SELECT id, scheduled_at, time_limit_minutes
        FROM exams
        WHERE expires_at IS NULL OR expires_at = ''
        """
    ).fetchall()
    for row in rows:
        db.execute(
            "UPDATE exams SET expires_at = ? WHERE id = ?",
            (default_expires_at(row["scheduled_at"], row["time_limit_minutes"]), row["id"]),
        )

    attempts_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'attempts'"
    ).fetchone()
    attempt_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(attempts)").fetchall()
    }
    if attempts_sql and attempts_sql["sql"] and "UNIQUE (exam_id, candidate_id)" in attempts_sql["sql"]:
        rebuild_attempts_table(db)
    elif "attempt_number" not in attempt_columns:
        db.execute("ALTER TABLE attempts ADD COLUMN attempt_number INTEGER NOT NULL DEFAULT 1")
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_in_progress
        ON attempts(exam_id, candidate_id)
        WHERE status = 'in_progress'
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS login_audit (
          id TEXT PRIMARY KEY,
          session_id TEXT,
          user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
          user_type TEXT NOT NULL,
          display_name TEXT NOT NULL,
          user_identifier TEXT,
          login_date TEXT NOT NULL,
          time_in TEXT NOT NULL,
          time_out TEXT,
          device_used TEXT,
          ip_address TEXT,
          outcome TEXT NOT NULL DEFAULT 'success',
          failure_reason TEXT,
          flags TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        )
        """
    )
    login_audit_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(login_audit)").fetchall()
    }
    if "outcome" not in login_audit_columns:
        db.execute("ALTER TABLE login_audit ADD COLUMN outcome TEXT NOT NULL DEFAULT 'success'")
    if "failure_reason" not in login_audit_columns:
        db.execute("ALTER TABLE login_audit ADD COLUMN failure_reason TEXT")
    if "flags" not in login_audit_columns:
        db.execute("ALTER TABLE login_audit ADD COLUMN flags TEXT NOT NULL DEFAULT ''")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_audit (
          id TEXT PRIMARY KEY,
          user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
          user_type TEXT NOT NULL,
          display_name TEXT NOT NULL,
          user_identifier TEXT,
          action_type TEXT NOT NULL,
          action_label TEXT NOT NULL,
          target_type TEXT,
          target_name TEXT,
          details_json TEXT NOT NULL DEFAULT '{}',
          occurred_at TEXT NOT NULL,
          device_used TEXT,
          ip_address TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_suppressed (
          record_type TEXT NOT NULL,
          record_id TEXT NOT NULL,
          deleted_at TEXT NOT NULL,
          deleted_by TEXT,
          PRIMARY KEY (record_type, record_id)
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_exams_created_by ON exams(created_by)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_login_audit_time_in ON login_audit(time_in)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_login_audit_session_id ON login_audit(session_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_activity_audit_occurred_at ON activity_audit(occurred_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_activity_audit_user_id ON activity_audit(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_audit_suppressed_type ON audit_suppressed(record_type)")
    db.commit()


def rebuild_role_tables(db):
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        """
        CREATE TABLE users_new (
          id TEXT PRIMARY KEY,
          role TEXT NOT NULL CHECK (role IN ('candidate', 'examiner', 'admin')),
          full_name TEXT,
          index_number TEXT UNIQUE,
          username TEXT UNIQUE,
          password_hash TEXT NOT NULL,
          password_salt TEXT NOT NULL,
          active_session_id TEXT,
          approved INTEGER NOT NULL DEFAULT 1,
          approved_at TEXT,
          approved_by TEXT REFERENCES users(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT INTO users_new (
          id, role, full_name, index_number, username, password_hash,
          password_salt, active_session_id, approved, approved_at, approved_by, created_at
        )
        SELECT
          id, role, full_name, index_number, username, password_hash,
          password_salt, active_session_id,
          1,
          created_at,
          NULL,
          created_at
        FROM users
        """
    )
    db.execute("DROP TABLE users")
    db.execute("ALTER TABLE users_new RENAME TO users")

    db.execute(
        """
        CREATE TABLE sessions_new (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          role TEXT NOT NULL CHECK (role IN ('candidate', 'examiner', 'admin')),
          user_agent TEXT,
          created_at TEXT NOT NULL,
          last_seen TEXT NOT NULL,
          expires_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT INTO sessions_new (
          id, user_id, role, user_agent, created_at, last_seen, expires_at
        )
        SELECT id, user_id, role, user_agent, created_at, last_seen, expires_at
        FROM sessions
        """
    )
    db.execute("DROP TABLE sessions")
    db.execute("ALTER TABLE sessions_new RENAME TO sessions")
    db.execute("PRAGMA foreign_keys = ON")


def rebuild_attempts_table(db):
    db.commit()
    columns = {
        row["name"] for row in db.execute("PRAGMA table_info(attempts)").fetchall()
    }
    attempt_number_expr = "attempt_number" if "attempt_number" in columns else "1"
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        """
        CREATE TABLE attempts_new (
          id TEXT PRIMARY KEY,
          exam_id TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
          candidate_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          attempt_number INTEGER NOT NULL DEFAULT 1,
          started_at TEXT NOT NULL,
          due_at TEXT NOT NULL,
          submitted_at TEXT,
          status TEXT NOT NULL CHECK (status IN ('in_progress', 'submitted')),
          answers_json TEXT NOT NULL DEFAULT '{}',
          question_order_json TEXT NOT NULL DEFAULT '[]',
          option_orders_json TEXT NOT NULL DEFAULT '{}',
          score REAL,
          total_marks REAL,
          percentage REAL,
          time_spent_seconds INTEGER
        )
        """
    )
    db.execute(
        f"""
        INSERT INTO attempts_new (
          id, exam_id, candidate_id, attempt_number, started_at, due_at,
          submitted_at, status, answers_json, question_order_json,
          option_orders_json, score, total_marks, percentage, time_spent_seconds
        )
        SELECT
          id, exam_id, candidate_id, {attempt_number_expr}, started_at, due_at,
          submitted_at, status, answers_json, question_order_json,
          option_orders_json, score, total_marks, percentage, time_spent_seconds
        FROM attempts
        """
    )
    db.execute("DROP TABLE attempts")
    db.execute("ALTER TABLE attempts_new RENAME TO attempts")
    db.execute("PRAGMA foreign_keys = ON")


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 180_000
    )
    return salt, digest.hex()


def verify_password(password, salt, expected_hash):
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 180_000
    ).hex()
    return hmac.compare_digest(digest, expected_hash)


def normalize_index(value):
    return re.sub(r"\s+", "", str(value or "").strip().upper())


def compact_text(value, max_len=5000):
    text = str(value or "").replace("\x00", "").strip()
    if len(text) > max_len:
        return text[:max_len]
    return text


def user_agent_summary(value):
    agent = str(value or "")
    if not agent:
        return "Unknown device"
    lower = agent.lower()
    if "edg/" in lower or "edge/" in lower:
        browser = "Edge"
    elif "opr/" in lower or "opera" in lower:
        browser = "Opera"
    elif "firefox/" in lower:
        browser = "Firefox"
    elif "chrome/" in lower or "crios/" in lower:
        browser = "Chrome"
    elif "safari/" in lower:
        browser = "Safari"
    else:
        browser = "Browser"

    if "iphone" in lower:
        device = "iPhone"
    elif "ipad" in lower:
        device = "iPad"
    elif "android" in lower:
        device = "Android"
    elif "windows" in lower:
        device = "Windows"
    elif "mac os x" in lower or "macintosh" in lower:
        device = "macOS"
    elif "linux" in lower:
        device = "Linux"
    else:
        device = "device"
    return f"{browser} on {device}"


def as_bool(value):
    return bool(value) and str(value).lower() not in {"0", "false", "no", "off"}


def normalized_base_url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    if not re.match(r"^https?://", value, re.IGNORECASE):
        value = f"http://{value}"
    return value.rstrip("/") + "/"


def host_without_port(value):
    value = str(value or "").strip().split(",", 1)[0]
    if not value:
        return ""
    if value.startswith("[") and "]" in value:
        return value[1:value.index("]")]
    if value.count(":") == 1:
        return value.rsplit(":", 1)[0]
    return value


def port_from_host_header(value):
    value = str(value or "").strip().split(",", 1)[0]
    if value.startswith("[") and "]:" in value:
        return value.rsplit(":", 1)[-1]
    if value.count(":") == 1:
        return value.rsplit(":", 1)[-1]
    return ""


def is_loopback_host(value):
    value = host_without_port(value).lower()
    if value == "localhost":
        return True
    if not value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_loopback


def is_unusable_network_host(value):
    value = host_without_port(value).lower()
    if not value or value == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def collect_lan_ipv4_addresses():
    addresses = []
    seen = set()

    def add(value):
        try:
            address = ipaddress.ip_address(str(value))
        except ValueError:
            return
        if address.version != 4 or address.is_loopback or address.is_unspecified:
            return
        text = str(address)
        if text not in seen:
            seen.add(text)
            addresses.append(text)

    for target in ("8.8.8.8", "1.1.1.1", "10.255.255.255"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((target, 80))
            add(sock.getsockname()[0])
        except OSError:
            pass
        finally:
            sock.close()

    try:
        hostname = socket.gethostname()
        for item in socket.gethostbyname_ex(hostname)[2]:
            add(item)
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass

    def rank(value):
        address = ipaddress.ip_address(value)
        if address.is_private and not address.is_link_local:
            return (0, value)
        if address.is_link_local:
            return (2, value)
        return (1, value)

    return sorted(addresses, key=rank)


def json_loads(value, default):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def parse_datetime_local(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("Date and time is required.")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Use a valid examination date and time.") from exc


def normalize_datetime_local(value):
    return parse_datetime_local(value).isoformat(timespec="minutes")


def default_expires_at(scheduled_at, time_limit_minutes):
    scheduled = parse_datetime_local(scheduled_at)
    expires = scheduled + timedelta(minutes=int(time_limit_minutes or 1))
    return expires.isoformat(timespec="minutes")


def datetime_local_to_utc(value):
    parsed = parse_datetime_local(value)
    return parsed.astimezone(timezone.utc)


def schedule_has_started(value):
    try:
        scheduled = parse_datetime_local(value)
    except ValueError:
        return False
    return scheduled <= datetime.now(scheduled.tzinfo)


def exam_has_expired(value):
    try:
        expires = parse_datetime_local(value)
    except ValueError:
        return False
    return datetime.now(expires.tzinfo) >= expires


def parse_utc(value):
    text = str(value or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_seconds(seconds):
    seconds = int(seconds or 0)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    return f"{minutes}m {sec}s"


def assigned_list(value):
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[\s,;]+", str(value or ""))
    seen = []
    for item in raw:
        index = normalize_index(item)
        if index and index not in seen:
            seen.append(index)
    return seen


def row_to_user(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "role": row["role"],
        "fullName": row["full_name"],
        "indexNumber": row["index_number"],
        "username": row["username"],
        "approved": bool(row["approved"]) if "approved" in row.keys() else True,
        "suspended": bool(row["suspended"]) if "suspended" in row.keys() else False,
    }


def row_to_exam(row, include_assignments=True):
    started = schedule_has_started(row["scheduled_at"])
    expired = exam_has_expired(row["expires_at"])
    keys = row.keys()
    exam = {
        "id": row["id"],
        "title": row["title"],
        "instructions": row["instructions"],
        "scheduledAt": row["scheduled_at"],
        "expiresAt": row["expires_at"],
        "timeLimitMinutes": row["time_limit_minutes"],
        "maxAttempts": row["max_attempts"] if "max_attempts" in keys else 1,
        "active": bool(row["active"]),
        "started": started,
        "expired": expired,
        "status": "expired" if expired else "active" if row["active"] else "inactive",
        "createdBy": row["created_by"] if "created_by" in keys else None,
        "ownerName": row["owner_name"] if "owner_name" in keys else None,
        "randomizeQuestions": bool(row["randomize_questions"]),
        "randomizeOptions": bool(row["randomize_options"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }
    if include_assignments:
        exam["assignedIndexNumbers"] = json_loads(row["assigned_index_numbers"], [])
    return exam


def row_to_question(row, reveal_answer=True):
    question = {
        "id": row["id"],
        "examId": row["exam_id"],
        "text": row["question_text"],
        "options": {
            "A": row["option_a"],
            "B": row["option_b"],
            "C": row["option_c"],
            "D": row["option_d"],
        },
        "marks": row["marks"],
        "rationale": row["rationale"],
        "position": row["position"],
    }
    if reveal_answer:
        question["correctAnswer"] = row["correct_answer"]
    return question


def ensure_candidate_assignment(exam_row, user_row):
    assigned = json_loads(exam_row["assigned_index_numbers"], [])
    return user_row["index_number"] in assigned


def get_questions(db, exam_id):
    return db.execute(
        """
        SELECT * FROM questions
        WHERE exam_id = ?
        ORDER BY position ASC, created_at ASC
        """,
        (exam_id,),
    ).fetchall()


def compute_result(db, attempt_row):
    question_ids = json_loads(attempt_row["question_order_json"], [])
    answers = json_loads(attempt_row["answers_json"], {})
    if question_ids:
        placeholders = ",".join("?" for _ in question_ids)
        rows = db.execute(
            f"SELECT * FROM questions WHERE id IN ({placeholders})", question_ids
        ).fetchall()
    else:
        rows = get_questions(db, attempt_row["exam_id"])
    by_id = {row["id"]: row for row in rows}
    score = 0.0
    total = 0.0
    for question_id in question_ids or [row["id"] for row in rows]:
        question = by_id.get(question_id)
        if not question:
            continue
        marks = float(question["marks"])
        total += marks
        if answers.get(question_id) == question["correct_answer"]:
            score += marks
    percentage = round((score / total) * 100, 2) if total else 0
    return round(score, 2), round(total, 2), percentage


def submit_attempt(db, attempt_id, status_note="submitted"):
    attempt = db.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
    if not attempt:
        raise ApiError(HTTPStatus.NOT_FOUND, "Attempt not found.")
    if attempt["status"] == "submitted":
        return attempt

    submitted_at = utc_now()
    started_at = parse_utc(attempt["started_at"])
    due_at, _ = attempt_deadline_info(db, attempt)
    score, total, percentage = compute_result(db, attempt)
    time_spent = int(max(0, min((submitted_at - started_at).total_seconds(), (due_at - started_at).total_seconds())))
    db.execute(
        """
        UPDATE attempts
        SET status = 'submitted', submitted_at = ?, score = ?, total_marks = ?,
            percentage = ?, time_spent_seconds = ?
        WHERE id = ?
        """,
        (
            submitted_at.isoformat(timespec="seconds"),
            score,
            total,
            percentage,
            time_spent,
            attempt_id,
        ),
    )
    if status_note:
        db.execute(
            """
            INSERT INTO exam_events (id, attempt_id, candidate_id, event_type, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("evt"),
                attempt_id,
                attempt["candidate_id"],
                status_note,
                "{}",
                iso_now(),
            ),
        )
    db.commit()
    return db.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()


def expire_if_needed(db, attempt_row):
    if attempt_row and attempt_row["status"] == "in_progress":
        deadline, reason = attempt_deadline_info(db, attempt_row)
        if utc_now() >= deadline:
            return submit_attempt(db, attempt_row["id"], reason)
    return attempt_row


def attempt_deadline_info(db, attempt_row):
    deadline = parse_utc(attempt_row["due_at"])
    reason = "time_expired"
    exam = db.execute(
        "SELECT expires_at FROM exams WHERE id = ?", (attempt_row["exam_id"],)
    ).fetchone()
    if exam and exam["expires_at"]:
        try:
            exam_deadline = datetime_local_to_utc(exam["expires_at"])
        except ValueError:
            exam_deadline = None
        if exam_deadline and exam_deadline < deadline:
            deadline = exam_deadline
            reason = "exam_expired"
    return deadline, reason


def parse_question_text(text):
    lines = [line.strip() for line in str(text or "").replace("\r\n", "\n").split("\n")]
    questions = []
    current = None
    last_field = None

    def finish():
        nonlocal current
        if not current:
            return
        missing = []
        if not current["text"]:
            missing.append("question text")
        for key in "ABCD":
            if not current["options"][key]:
                missing.append(f"option {key}")
        if current["correctAnswer"] not in "ABCD":
            missing.append("answer")
        if missing:
            current = None
            return
        questions.append(current)
        current = None

    for line in lines:
        if not line:
            continue

        question_match = re.match(r"^(?:Question\s*\d*|Q\s*\d*)\s*[:.)-]\s*(.+)$", line, re.I)
        if question_match:
            finish()
            current = {
                "text": question_match.group(1).strip(),
                "options": {"A": "", "B": "", "C": "", "D": ""},
                "correctAnswer": "",
                "marks": 1,
                "rationale": "",
            }
            last_field = "text"
            continue

        if current is None:
            current = {
                "text": line,
                "options": {"A": "", "B": "", "C": "", "D": ""},
                "correctAnswer": "",
                "marks": 1,
                "rationale": "",
            }
            last_field = "text"
            continue

        option_match = re.match(r"^([ABCD])\s*[.)]\s*(.+)$", line, re.I)
        if option_match:
            key = option_match.group(1).upper()
            current["options"][key] = option_match.group(2).strip()
            last_field = f"option_{key}"
            continue

        answer_match = re.match(r"^Answer\s*[:.)-]\s*([ABCD])\b", line, re.I)
        if answer_match:
            current["correctAnswer"] = answer_match.group(1).upper()
            last_field = "answer"
            continue

        marks_match = re.match(r"^Marks?\s*[:.)-]\s*([0-9]+(?:\.[0-9]+)?)", line, re.I)
        if marks_match:
            current["marks"] = float(marks_match.group(1))
            last_field = "marks"
            continue

        rationale_match = re.match(r"^(?:Rationale|Explanation)\s*[:.)-]\s*(.+)$", line, re.I)
        if rationale_match:
            current["rationale"] = rationale_match.group(1).strip()
            last_field = "rationale"
            continue

        if last_field == "text":
            current["text"] = f"{current['text']} {line}".strip()
        elif last_field and last_field.startswith("option_"):
            key = last_field[-1]
            current["options"][key] = f"{current['options'][key]} {line}".strip()
        elif last_field == "rationale":
            current["rationale"] = f"{current['rationale']} {line}".strip()
        else:
            current["text"] = f"{current['text']} {line}".strip()

    finish()
    return questions


def parse_question_csv(text):
    def normalized_header(value):
        return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

    def first_value(row, *names):
        for name in names:
            if name in row and str(row[name] or "").strip():
                return row[name]
        return ""

    def parse_marks(value):
        try:
            return float(value or 1)
        except (TypeError, ValueError):
            return 1

    def valid_item(item):
        return (
            item["text"]
            and all(item["options"].get(key) for key in "ABCD")
            and item["correctAnswer"] in "ABCD"
            and float(item["marks"] or 0) > 0
        )

    source = io.StringIO(str(text or ""))
    sample = source.read(2048)
    source.seek(0)
    try:
        dialect = csv.Sniffer().sniff(sample or "", delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(source, dialect))
    rows = [[cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return []

    header = [normalized_header(cell) for cell in rows[0]]
    has_header = any(item in header for item in {"question", "questiontext", "text", "answer", "correctanswer"})
    parsed = []
    if has_header:
        for raw in rows[1:]:
            row = {header[index]: raw[index] if index < len(raw) else "" for index in range(len(header))}
            item = {
                "text": compact_text(first_value(row, "question", "questiontext", "text", "q"), 6000),
                "options": {
                    "A": compact_text(first_value(row, "a", "optiona", "option1"), 2000),
                    "B": compact_text(first_value(row, "b", "optionb", "option2"), 2000),
                    "C": compact_text(first_value(row, "c", "optionc", "option3"), 2000),
                    "D": compact_text(first_value(row, "d", "optiond", "option4"), 2000),
                },
                "correctAnswer": compact_text(first_value(row, "answer", "correct", "correctanswer", "key"), 1).upper(),
                "marks": parse_marks(first_value(row, "marks", "mark", "score")),
                "rationale": compact_text(first_value(row, "rationale", "explanation"), 4000),
            }
            if valid_item(item):
                parsed.append(item)
    else:
        for raw in rows:
            if len(raw) < 6:
                continue
            item = {
                "text": compact_text(raw[0], 6000),
                "options": {
                    "A": compact_text(raw[1], 2000),
                    "B": compact_text(raw[2], 2000),
                    "C": compact_text(raw[3], 2000),
                    "D": compact_text(raw[4], 2000),
                },
                "correctAnswer": compact_text(raw[5], 1).upper(),
                "marks": parse_marks(raw[6] if len(raw) > 6 else 1),
                "rationale": compact_text(raw[7] if len(raw) > 7 else "", 4000),
            }
            if valid_item(item):
                parsed.append(item)
    return parsed


def extract_docx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml_data = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml_data)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{namespace}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{namespace}tab":
                parts.append("\t")
            elif node.tag == f"{namespace}br":
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def xlsx_escape(value):
    return html_escape(str(value if value is not None else ""), quote=True)


def column_name(index):
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def make_xlsx(rows):
    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row):
            ref = f"{column_name(col_index)}{row_index}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(
                    f'<c r="{ref}" t="inlineStr"><is><t>{xlsx_escape(value)}</t></is></c>'
                )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    worksheet = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <cols>
    <col min="1" max="1" width="28" customWidth="1"/>
    <col min="2" max="2" width="18" customWidth="1"/>
    <col min="3" max="3" width="34" customWidth="1"/>
    <col min="4" max="7" width="18" customWidth="1"/>
  </cols>
  <sheetData>{"".join(sheet_rows)}</sheetData>
</worksheet>"""

    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Results" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        "xl/worksheets/sheet1.xml": worksheet,
    }

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class ChiefExamHandler(BaseHTTPRequestHandler):
    server_version = "ChiefExam/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        super().end_headers()

    def do_GET(self):
        self.route()

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
            return
        self.serve_static(parsed.path, head_only=True)

    def do_POST(self):
        self.route()

    def do_PUT(self):
        self.route()

    def do_DELETE(self):
        self.route()

    def route(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            with connect_db() as db:
                self.db = db
                try:
                    self.route_api(path, urllib.parse.parse_qs(parsed.query))
                except ApiError as exc:
                    self.send_json({"error": exc.message}, exc.status)
                except Exception:
                    traceback.print_exc()
                    self.send_json({"error": "The server could not complete that request."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                finally:
                    self.db = None
        else:
            self.serve_static(path)

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, data, content_type, filename=None):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def read_bytes(self, limit):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > limit:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The request is too large.")
        return self.rfile.read(length)

    def read_json(self):
        data = self.read_bytes(MAX_JSON_BYTES)
        if not data:
            return {}
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Use valid JSON.") from exc

    def read_multipart(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Use multipart form upload.")
        body = self.read_bytes(MAX_UPLOAD_BYTES)
        message = BytesParser(policy=email_policy).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        )
        fields = {}
        files = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                files[name] = {"filename": filename, "content": payload}
            else:
                fields[name] = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return fields, files

    def parse_cookies(self):
        cookies = {}
        for chunk in self.headers.get("Cookie", "").split(";"):
            if "=" in chunk:
                key, value = chunk.split("=", 1)
                cookies[key.strip()] = urllib.parse.unquote(value.strip())
        return cookies

    def set_cookie(self, value="", max_age=None):
        secure_cookie_setting = (
            os.environ.get("KYEREMEH_SECURE_COOKIES")
            or os.environ.get("CHIEFEXAM_SECURE_COOKIES")
        )
        secure = "; Secure" if secure_cookie_setting == "1" else ""
        cookie = f"{SESSION_COOKIE}={urllib.parse.quote(value)}; Path=/; HttpOnly; SameSite=Lax{secure}"
        if max_age is not None:
            cookie += f"; Max-Age={max_age}"
        self.send_header("Set-Cookie", cookie)

    def send_login_response(self, user, session_id):
        body = json.dumps({"user": row_to_user(user)}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.set_cookie(session_id, SESSION_HOURS * 3600)
        self.end_headers()
        self.wfile.write(body)

    def clear_login_cookie(self):
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.set_cookie("", 0)
        self.end_headers()
        self.wfile.write(body)

    def client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return compact_text(forwarded.split(",", 1)[0], 80)
        real_ip = self.headers.get("X-Real-IP", "")
        if real_ip:
            return compact_text(real_ip, 80)
        return compact_text(self.client_address[0] if self.client_address else "", 80)

    def close_audit_for_sessions(self, session_ids, closed_at=None):
        session_ids = [session_id for session_id in session_ids if session_id]
        if not session_ids:
            return
        closed_at = closed_at or iso_now()
        placeholders = ",".join("?" for _ in session_ids)
        self.db.execute(
            f"""
            UPDATE login_audit
            SET time_out = COALESCE(time_out, ?)
            WHERE session_id IN ({placeholders})
            """,
            [closed_at, *session_ids],
        )

    def close_expired_sessions(self, now):
        rows = self.db.execute(
            "SELECT id, expires_at FROM sessions WHERE expires_at <= ?", (now,)
        ).fetchall()
        for row in rows:
            self.close_audit_for_sessions([row["id"]], row["expires_at"] or now)
        if rows:
            self.db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))

    def user_display_name(self, user):
        if not user:
            return "Unknown user"
        return (
            user["full_name"]
            or user["username"]
            or user["index_number"]
            or "Unknown user"
        )

    def user_identifier(self, user):
        if not user:
            return ""
        return (
            user["index_number"]
            if user["role"] == "candidate"
            else user["username"] or user["id"]
        )

    def create_login_audit(self, user, session_id, time_in, flags=""):
        display_name = (
            user["full_name"]
            or user["username"]
            or user["index_number"]
            or "Unknown user"
        )
        identifier = (
            user["index_number"]
            if user["role"] == "candidate"
            else user["username"] or user["id"]
        )
        self.db.execute(
            """
            INSERT INTO login_audit (
              id, session_id, user_id, user_type, display_name, user_identifier,
              login_date, time_in, device_used, ip_address, outcome, flags, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', ?, ?)
            """,
            (
                new_id("aud"),
                session_id,
                user["id"],
                user["role"],
                display_name,
                identifier,
                time_in[:10],
                time_in,
                user_agent_summary(self.headers.get("User-Agent", "")),
                self.client_ip(),
                flags,
                time_in,
            ),
        )

    def record_failed_login(self, user_type, identifier, reason, user=None):
        now = iso_now()
        display_name = self.user_display_name(user) if user else (identifier or "Unknown user")
        self.db.execute(
            """
            INSERT INTO login_audit (
              id, user_id, user_type, display_name, user_identifier, login_date,
              time_in, time_out, device_used, ip_address, outcome,
              failure_reason, flags, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'failed', ?, 'failed_login', ?)
            """,
            (
                new_id("aud"),
                user["id"] if user else None,
                user["role"] if user else user_type,
                display_name,
                self.user_identifier(user) if user else identifier,
                now[:10],
                now,
                now,
                user_agent_summary(self.headers.get("User-Agent", "")),
                self.client_ip(),
                reason,
                now,
            ),
        )

    def record_activity(self, user, action_type, action_label, target_type="", target_name="", details=None):
        now = iso_now()
        self.db.execute(
            """
            INSERT INTO activity_audit (
              id, user_id, user_type, display_name, user_identifier,
              action_type, action_label, target_type, target_name, details_json,
              occurred_at, device_used, ip_address
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("act"),
                user["id"],
                user["role"],
                self.user_display_name(user),
                self.user_identifier(user),
                action_type,
                action_label,
                target_type,
                target_name,
                json.dumps(details or {})[:2000],
                now,
                user_agent_summary(self.headers.get("User-Agent", "")),
                self.client_ip(),
            ),
        )

    def get_auth(self):
        session_id = self.parse_cookies().get(SESSION_COOKIE)
        if not session_id:
            return None, None
        now = iso_now()
        self.close_expired_sessions(now)
        row = self.db.execute(
            """
            SELECT sessions.id AS session_id, sessions.expires_at, users.*
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.id = ?
            """,
            (session_id,),
        ).fetchone()
        if not row:
            self.db.commit()
            return None, None
        if row["role"] == "candidate" and row["active_session_id"] != session_id:
            self.close_audit_for_sessions([session_id], now)
            self.db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self.db.commit()
            return None, None
        self.db.execute("UPDATE sessions SET last_seen = ? WHERE id = ?", (now, session_id))
        self.db.commit()
        return row, session_id

    def require_role(self, role):
        user, session_id = self.get_auth()
        if not user:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "Please log in.")
        roles = {role} if isinstance(role, str) else set(role)
        if user["role"] not in roles:
            raise ApiError(HTTPStatus.FORBIDDEN, "You do not have access to this area.")
        return user, session_id

    def create_session(self, user):
        session_id = new_id("ses")
        now = utc_now()
        now_iso = now.isoformat(timespec="seconds")
        expires_at = now + timedelta(hours=SESSION_HOURS)
        flags = ""
        if user["role"] == "candidate":
            rows = self.db.execute(
                "SELECT id FROM sessions WHERE user_id = ?", (user["id"],)
            ).fetchall()
            if rows:
                flags = "multiple_login"
            self.close_audit_for_sessions([row["id"] for row in rows], now_iso)
            self.db.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
            self.db.execute(
                "UPDATE users SET active_session_id = ? WHERE id = ?",
                (session_id, user["id"]),
            )
        self.db.execute(
            """
            INSERT INTO sessions (id, user_id, role, user_agent, created_at, last_seen, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                user["id"],
                user["role"],
                self.headers.get("User-Agent", "")[:250],
                now_iso,
                now_iso,
                expires_at.isoformat(timespec="seconds"),
            ),
        )
        self.create_login_audit(user, session_id, now_iso, flags)
        self.db.commit()
        return session_id

    def route_api(self, path, query):
        method = self.command
        parts = [part for part in path.split("/") if part]

        if method == "GET" and path == "/api/network-info":
            self.require_role("admin")
            self.api_network_info()
            return

        if method == "GET" and path == "/api/public/active-exams":
            self.api_public_active_exams()
            return

        if method == "GET" and path == "/api/me":
            user, _ = self.get_auth()
            if not user:
                raise ApiError(HTTPStatus.UNAUTHORIZED, "Please log in.")
            self.send_json({"user": row_to_user(user)})
            return

        if method == "POST" and path == "/api/logout":
            user, session_id = self.get_auth()
            if session_id:
                self.close_audit_for_sessions([session_id], iso_now())
                self.db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                if user and user["active_session_id"] == session_id:
                    self.db.execute("UPDATE users SET active_session_id = NULL WHERE id = ?", (user["id"],))
                self.db.commit()
            self.clear_login_cookie()
            return

        if method == "POST" and path == "/api/candidate/register":
            self.api_candidate_register()
            return

        if method == "POST" and path == "/api/candidate/login":
            self.api_candidate_login()
            return

        if method == "POST" and path == "/api/admin/login":
            self.api_admin_login()
            return

        if parts[:2] == ["api", "candidate"]:
            self.route_candidate(method, parts)
            return

        if parts[:2] == ["api", "admin"]:
            self.route_admin(method, parts, query)
            return

        raise ApiError(HTTPStatus.NOT_FOUND, "Route not found.")

    def api_network_info(self):
        server_host, server_port = self.server.server_address[:2]
        forwarded_host = self.headers.get("X-Forwarded-Host", "")
        forwarded_port = self.headers.get("X-Forwarded-Port", "")
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0]
        through_lan_proxy = self.headers.get("X-LAN-Proxy") == "1"
        request_host = self.headers.get("Host", "")
        scheme = "https" if forwarded_proto == "https" else "http"
        display_port = forwarded_port or port_from_host_header(forwarded_host) or str(server_port)
        lan_addresses = collect_lan_ipv4_addresses()

        urls = []
        if PUBLIC_URL:
            urls.append(normalized_base_url(PUBLIC_URL))

        forwarded_name = host_without_port(forwarded_host)
        if forwarded_name and not is_unusable_network_host(forwarded_name):
            forwarded_display_port = port_from_host_header(forwarded_host) or display_port
            urls.append(f"{scheme}://{forwarded_name}:{forwarded_display_port}/")

        request_name = host_without_port(request_host)
        if request_name and not is_unusable_network_host(request_name):
            request_display_port = port_from_host_header(request_host) or display_port
            urls.append(f"http://{request_name}:{request_display_port}/")

        for address in lan_addresses:
            urls.append(f"http://{address}:{display_port}/")

        deduped = []
        for url in urls:
            url = normalized_base_url(url)
            if url and url not in deduped:
                deduped.append(url)

        bound_local_only = is_loopback_host(server_host)
        lan_ready = bool(PUBLIC_URL) or through_lan_proxy or not bound_local_only
        if not deduped:
            deduped.append(f"http://127.0.0.1:{server_port}/")

        if lan_ready and len(deduped) > 0:
            message = "Devices on the same network can scan this code."
        elif lan_addresses:
            message = "Restart with HOST=0.0.0.0 so other devices can reach this computer."
        else:
            message = "Connect this computer to the exam Wi-Fi network, then refresh the QR code."

        self.send_json(
            {
                "url": deduped[0],
                "urls": deduped,
                "lanReady": lan_ready,
                "boundHost": server_host,
                "port": int(display_port) if str(display_port).isdigit() else display_port,
                "lanAddresses": lan_addresses,
                "message": message,
            }
        )

    def api_public_active_exams(self):
        rows = self.db.execute(
            """
            SELECT
              exams.title,
              exams.scheduled_at,
              exams.expires_at,
              users.role AS owner_role,
              users.full_name AS owner_name,
              users.username AS owner_username
            FROM exams
            LEFT JOIN users ON users.id = exams.created_by
            WHERE exams.active = 1
            ORDER BY exams.scheduled_at ASC, exams.title ASC
            """
        ).fetchall()
        exams = []
        for row in rows:
            if exam_has_expired(row["expires_at"]):
                continue
            examiner_name = (
                row["owner_name"]
                or (row["owner_username"] if row["owner_role"] == "examiner" else None)
                or "Administrator"
            )
            exams.append({
                "title": row["title"],
                "scheduledAt": row["scheduled_at"],
                "expiresAt": row["expires_at"],
                "examinerName": examiner_name,
            })
        self.send_json({"exams": exams})

    def api_candidate_register(self):
        payload = self.read_json()
        full_name = compact_text(payload.get("fullName"), 160)
        index_number = normalize_index(payload.get("indexNumber"))
        password = str(payload.get("password") or "")
        confirm = str(payload.get("confirmPassword") or "")
        if len(full_name.split()) < 2:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Enter the candidate full name.")
        if not re.match(r"^[A-Z0-9/_-]{3,40}$", index_number):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Use a valid index number.")
        if len(password) < 8:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Password must be at least 8 characters.")
        if password != confirm:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Passwords do not match.")
        existing = self.db.execute(
            "SELECT id FROM users WHERE index_number = ?", (index_number,)
        ).fetchone()
        if existing:
            raise ApiError(HTTPStatus.CONFLICT, "That index number is already registered.")
        salt, password_hash = hash_password(password)
        self.db.execute(
            """
            INSERT INTO users (
              id, role, full_name, index_number, password_hash,
              password_salt, approved, created_at
            )
            VALUES (?, 'candidate', ?, ?, ?, ?, 0, ?)
            """,
            (new_id("usr"), full_name, index_number, password_hash, salt, iso_now()),
        )
        self.db.commit()
        self.send_json({"ok": True})

    def api_candidate_login(self):
        payload = self.read_json()
        index_number = normalize_index(payload.get("indexNumber"))
        password = str(payload.get("password") or "")
        user = self.db.execute(
            "SELECT * FROM users WHERE role = 'candidate' AND index_number = ?",
            (index_number,),
        ).fetchone()
        if not user or not verify_password(password, user["password_salt"], user["password_hash"]):
            self.record_failed_login("candidate", index_number, "Invalid index number or password.", user)
            raise ApiError(HTTPStatus.UNAUTHORIZED, "Invalid index number or password.")
        if user["suspended"]:
            self.record_failed_login("candidate", index_number, "Candidate account suspended.", user)
            raise ApiError(HTTPStatus.FORBIDDEN, "This candidate account has been suspended.")
        if not user["approved"]:
            self.record_failed_login("candidate", index_number, "Candidate account awaiting administrator approval.", user)
            raise ApiError(HTTPStatus.FORBIDDEN, "This candidate account is awaiting administrator approval.")
        session_id = self.create_session(user)
        user = self.db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        self.send_login_response(user, session_id)

    def api_admin_login(self):
        payload = self.read_json()
        username = compact_text(payload.get("username"), 80).lower()
        password = str(payload.get("password") or "")
        user = self.db.execute(
            "SELECT * FROM users WHERE role = 'admin' AND username = ?",
            (username,),
        ).fetchone()
        if not user or not verify_password(password, user["password_salt"], user["password_hash"]):
            self.record_failed_login("admin", username, "Invalid administrator credentials.", user)
            raise ApiError(HTTPStatus.UNAUTHORIZED, "Invalid administrator credentials.")
        if user["suspended"]:
            self.record_failed_login("admin", username, "Administrator account suspended.", user)
            raise ApiError(HTTPStatus.FORBIDDEN, "This account has been suspended.")
        session_id = self.create_session(user)
        self.send_login_response(user, session_id)

    def api_admin_update_credentials(self, user):
        self.require_host_admin(user)
        payload = self.read_json()
        current_password = str(payload.get("currentPassword") or "")
        username = compact_text(payload.get("username"), 80).lower()
        new_password = str(payload.get("newPassword") or "")
        confirm_password = str(payload.get("confirmPassword") or "")

        admin = self.db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not admin or not verify_password(
            current_password, admin["password_salt"], admin["password_hash"]
        ):
            raise ApiError(HTTPStatus.UNAUTHORIZED, "Current administrator password is incorrect.")
        if not re.match(r"^[a-z0-9._-]{3,80}$", username):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Use a valid administrator username.")
        existing = self.db.execute(
            "SELECT id FROM users WHERE username = ? AND id != ?",
            (username, user["id"]),
        ).fetchone()
        if existing:
            raise ApiError(HTTPStatus.CONFLICT, "That username is already in use.")

        if new_password:
            if len(new_password) < 8:
                raise ApiError(HTTPStatus.BAD_REQUEST, "New password must be at least 8 characters.")
            if new_password != confirm_password:
                raise ApiError(HTTPStatus.BAD_REQUEST, "New password and confirmation do not match.")
            salt, password_hash = hash_password(new_password)
            self.db.execute(
                """
                UPDATE users
                SET username = ?, password_hash = ?, password_salt = ?
                WHERE id = ? AND role = 'admin'
                """,
                (username, password_hash, salt, user["id"]),
            )
        else:
            self.db.execute(
                "UPDATE users SET username = ? WHERE id = ? AND role = 'admin'",
                (username, user["id"]),
            )
        self.db.commit()
        updated = self.db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        self.send_json({"ok": True, "user": row_to_user(updated)})

    def route_candidate(self, method, parts):
        user, _ = self.require_role("candidate")
        if method == "GET" and parts == ["api", "candidate", "exams"]:
            self.api_candidate_exams(user)
            return
        if method == "POST" and len(parts) == 5 and parts[2] == "exams" and parts[4] == "start":
            self.api_start_exam(user, parts[3])
            return
        if len(parts) >= 4 and parts[2] == "attempts":
            attempt_id = parts[3]
            if method == "GET" and len(parts) == 4:
                self.api_get_attempt(user, attempt_id)
                return
            if method == "POST" and len(parts) == 5 and parts[4] == "answers":
                self.api_save_answer(user, attempt_id)
                return
            if method == "POST" and len(parts) == 5 and parts[4] == "submit":
                self.api_submit_attempt(user, attempt_id)
                return
            if method == "POST" and len(parts) == 5 and parts[4] == "events":
                self.api_log_event(user, attempt_id)
                return
        raise ApiError(HTTPStatus.NOT_FOUND, "Candidate route not found.")

    def api_candidate_exams(self, user):
        exams = []
        rows = self.db.execute("SELECT * FROM exams ORDER BY scheduled_at DESC").fetchall()
        for row in rows:
            if not row["active"] or not ensure_candidate_assignment(row, user):
                continue
            question_count = self.db.execute(
                "SELECT COUNT(*) AS count FROM questions WHERE exam_id = ?", (row["id"],)
            ).fetchone()["count"]
            total_marks = self.db.execute(
                "SELECT COALESCE(SUM(marks), 0) AS total FROM questions WHERE exam_id = ?", (row["id"],)
            ).fetchone()["total"]
            attempt = self.db.execute(
                """
                SELECT * FROM attempts
                WHERE exam_id = ? AND candidate_id = ?
                  AND status = 'in_progress'
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (row["id"], user["id"]),
            ).fetchone()
            attempt = expire_if_needed(self.db, attempt)
            if attempt and attempt["status"] != "in_progress":
                attempt = None
            submitted_attempts = self.db.execute(
                """
                SELECT COUNT(*) AS count FROM attempts
                WHERE exam_id = ? AND candidate_id = ? AND status = 'submitted'
                """,
                (row["id"], user["id"]),
            ).fetchone()["count"]
            max_attempts = max(1, int(row["max_attempts"] if "max_attempts" in row.keys() else 1))
            attempts_remaining = max(0, max_attempts - submitted_attempts)
            exam = row_to_exam(row, include_assignments=False)
            started = schedule_has_started(row["scheduled_at"])
            expired = exam_has_expired(row["expires_at"])
            exam["questionCount"] = question_count
            exam["totalMarks"] = total_marks
            exam["started"] = started
            exam["expired"] = expired
            exam["submittedAttempts"] = submitted_attempts
            exam["attemptsRemaining"] = attempts_remaining
            exam["available"] = started and not expired and question_count > 0 and (bool(attempt) or attempts_remaining > 0)
            exam["attemptStatus"] = attempt["status"] if attempt else ("submitted" if attempts_remaining <= 0 and submitted_attempts else "not_started")
            exam["attemptId"] = attempt["id"] if attempt else None
            exams.append(exam)
        self.send_json({"exams": exams})

    def api_start_exam(self, user, exam_id):
        exam = self.db.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
        if not exam:
            raise ApiError(HTTPStatus.NOT_FOUND, "Exam not found.")
        if not exam["active"]:
            raise ApiError(HTTPStatus.FORBIDDEN, "This exam is not active.")
        if not ensure_candidate_assignment(exam, user):
            raise ApiError(HTTPStatus.FORBIDDEN, "This exam is not assigned to this candidate.")
        if not schedule_has_started(exam["scheduled_at"]):
            raise ApiError(HTTPStatus.FORBIDDEN, "This exam has not started yet.")
        exam_expired = exam_has_expired(exam["expires_at"])
        questions = get_questions(self.db, exam_id)
        if not questions:
            raise ApiError(HTTPStatus.BAD_REQUEST, "The exam has no questions yet.")

        existing = self.db.execute(
            """
            SELECT * FROM attempts
            WHERE exam_id = ? AND candidate_id = ? AND status = 'in_progress'
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (exam_id, user["id"]),
        ).fetchone()
        existing = expire_if_needed(self.db, existing)
        if existing and existing["status"] == "in_progress":
            self.send_json({"attemptId": existing["id"]})
            return
        submitted_attempts = self.db.execute(
            """
            SELECT COUNT(*) AS count FROM attempts
            WHERE exam_id = ? AND candidate_id = ? AND status = 'submitted'
            """,
            (exam_id, user["id"]),
        ).fetchone()["count"]
        max_attempts = max(1, int(exam["max_attempts"] if "max_attempts" in exam.keys() else 1))
        if submitted_attempts >= max_attempts:
            raise ApiError(HTTPStatus.CONFLICT, "The maximum number of attempts has been used.")
        if existing:
            if existing["status"] == "in_progress":
                self.send_json({"attemptId": existing["id"]})
                return
        if exam_expired:
            raise ApiError(HTTPStatus.FORBIDDEN, "This exam has expired.")

        question_ids = [row["id"] for row in questions]
        if exam["randomize_questions"]:
            random.shuffle(question_ids)
        option_orders = {}
        for question_id in question_ids:
            keys = ["A", "B", "C", "D"]
            if exam["randomize_options"]:
                random.shuffle(keys)
            option_orders[question_id] = keys
        attempt_id = new_id("att")
        started_at = utc_now()
        due_at = started_at + timedelta(minutes=int(exam["time_limit_minutes"]))
        try:
            exam_deadline = datetime_local_to_utc(exam["expires_at"])
        except ValueError:
            exam_deadline = None
        if exam_deadline and exam_deadline < due_at:
            due_at = exam_deadline
        if due_at <= started_at:
            raise ApiError(HTTPStatus.FORBIDDEN, "This exam has expired.")
        self.db.execute(
            """
            INSERT INTO attempts (
              id, exam_id, candidate_id, started_at, due_at, status,
              attempt_number, answers_json, question_order_json, option_orders_json
            )
            VALUES (?, ?, ?, ?, ?, 'in_progress', ?, '{}', ?, ?)
            """,
            (
                attempt_id,
                exam_id,
                user["id"],
                started_at.isoformat(timespec="seconds"),
                due_at.isoformat(timespec="seconds"),
                submitted_attempts + 1,
                json.dumps(question_ids),
                json.dumps(option_orders),
            ),
        )
        self.db.execute(
            """
            INSERT INTO exam_events (id, attempt_id, candidate_id, event_type, details_json, created_at)
            VALUES (?, ?, ?, 'started_exam', '{}', ?)
            """,
            (new_id("evt"), attempt_id, user["id"], iso_now()),
        )
        self.db.commit()
        self.send_json({"attemptId": attempt_id})

    def api_get_attempt(self, user, attempt_id):
        attempt = self.db.execute(
            "SELECT * FROM attempts WHERE id = ? AND candidate_id = ?",
            (attempt_id, user["id"]),
        ).fetchone()
        if not attempt:
            raise ApiError(HTTPStatus.NOT_FOUND, "Attempt not found.")
        attempt = expire_if_needed(self.db, attempt)
        exam = self.db.execute("SELECT * FROM exams WHERE id = ?", (attempt["exam_id"],)).fetchone()
        if not exam:
            raise ApiError(HTTPStatus.NOT_FOUND, "Exam not found.")
        effective_due_at, _ = attempt_deadline_info(self.db, attempt)
        question_order = json_loads(attempt["question_order_json"], [])
        option_orders = json_loads(attempt["option_orders_json"], {})
        if question_order:
            placeholders = ",".join("?" for _ in question_order)
            rows = self.db.execute(
                f"SELECT * FROM questions WHERE id IN ({placeholders})", question_order
            ).fetchall()
            by_id = {row["id"]: row for row in rows}
            question_rows = [by_id[qid] for qid in question_order if qid in by_id]
        else:
            question_rows = get_questions(self.db, exam["id"])

        questions = []
        for row in question_rows:
            order = option_orders.get(row["id"], ["A", "B", "C", "D"])
            options = []
            for index, key in enumerate(order):
                options.append(
                    {
                        "displayKey": chr(65 + index),
                        "optionKey": key,
                        "text": row[f"option_{key.lower()}"],
                    }
                )
            questions.append(
                {
                    "id": row["id"],
                    "text": row["question_text"],
                    "options": options,
                    "marks": row["marks"],
                }
            )
        self.send_json(
            {
                "candidate": row_to_user(user),
                "exam": row_to_exam(exam, include_assignments=False),
                "attempt": {
                    "id": attempt["id"],
                    "attemptNumber": attempt["attempt_number"] if "attempt_number" in attempt.keys() else 1,
                    "status": attempt["status"],
                    "startedAt": attempt["started_at"],
                    "dueAt": effective_due_at.isoformat(timespec="seconds"),
                    "submittedAt": attempt["submitted_at"],
                    "answers": json_loads(attempt["answers_json"], {}),
                    "score": attempt["score"],
                    "totalMarks": attempt["total_marks"],
                    "percentage": attempt["percentage"],
                    "timeSpentSeconds": attempt["time_spent_seconds"],
                },
                "questions": questions,
            }
        )

    def api_save_answer(self, user, attempt_id):
        payload = self.read_json()
        attempt = self.db.execute(
            "SELECT * FROM attempts WHERE id = ? AND candidate_id = ?",
            (attempt_id, user["id"]),
        ).fetchone()
        if not attempt:
            raise ApiError(HTTPStatus.NOT_FOUND, "Attempt not found.")
        attempt = expire_if_needed(self.db, attempt)
        if attempt["status"] != "in_progress":
            self.send_json({"submitted": True})
            return
        question_id = compact_text(payload.get("questionId"), 120)
        answer = compact_text(payload.get("answer"), 1).upper()
        question_order = json_loads(attempt["question_order_json"], [])
        if question_id not in question_order or answer not in "ABCD":
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid answer.")
        answers = json_loads(attempt["answers_json"], {})
        answers[question_id] = answer
        self.db.execute(
            "UPDATE attempts SET answers_json = ? WHERE id = ?",
            (json.dumps(answers), attempt_id),
        )
        self.db.commit()
        self.send_json({"ok": True})

    def api_submit_attempt(self, user, attempt_id):
        payload = self.read_json()
        attempt = self.db.execute(
            "SELECT * FROM attempts WHERE id = ? AND candidate_id = ?",
            (attempt_id, user["id"]),
        ).fetchone()
        if not attempt:
            raise ApiError(HTTPStatus.NOT_FOUND, "Attempt not found.")
        attempt = submit_attempt(
            self.db,
            attempt_id,
            "auto_submitted" if as_bool(payload.get("auto")) else "submitted_exam",
        )
        self.send_json(
            {
                "submitted": True,
                "score": attempt["score"],
                "totalMarks": attempt["total_marks"],
                "percentage": attempt["percentage"],
                "timeSpentSeconds": attempt["time_spent_seconds"],
            }
        )

    def api_log_event(self, user, attempt_id):
        payload = self.read_json()
        attempt = self.db.execute(
            "SELECT * FROM attempts WHERE id = ? AND candidate_id = ?",
            (attempt_id, user["id"]),
        ).fetchone()
        if not attempt:
            raise ApiError(HTTPStatus.NOT_FOUND, "Attempt not found.")
        event_type = re.sub(r"[^a-z0-9_-]+", "_", compact_text(payload.get("type"), 80).lower()).strip("_")
        if not event_type:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Event type is required.")
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        self.db.execute(
            """
            INSERT INTO exam_events (id, attempt_id, candidate_id, event_type, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("evt"),
                attempt_id,
                user["id"],
                event_type,
                json.dumps(details)[:2000],
                iso_now(),
            ),
        )
        self.db.commit()
        self.send_json({"ok": True})

    def scoped_exam_clause(self, user, table_name="exams"):
        if user["role"] == "admin":
            return "1 = 1", []
        return f"{table_name}.created_by = ?", [user["id"]]

    def get_exam_for_backend_user(self, user, exam_id):
        where, params = self.scoped_exam_clause(user, "exams")
        exam = self.db.execute(
            f"SELECT * FROM exams WHERE exams.id = ? AND {where}",
            [exam_id, *params],
        ).fetchone()
        if not exam:
            raise ApiError(HTTPStatus.NOT_FOUND, "Exam not found.")
        return exam

    def route_admin(self, method, parts, query):
        user, _ = self.require_role("admin")
        if method == "PUT" and parts == ["api", "admin", "settings", "credentials"]:
            self.api_admin_update_credentials(user)
            return
        if method == "POST" and parts == ["api", "admin", "users"]:
            self.api_admin_save_user(user)
            return
        if len(parts) >= 4 and parts[:3] == ["api", "admin", "users"]:
            target_id = parts[3]
            if method == "PUT" and len(parts) == 4:
                self.api_admin_save_user(user, target_id)
                return
            if method == "POST" and len(parts) == 5 and parts[4] == "accept":
                self.api_admin_accept_user(user, target_id)
                return
            if method == "POST" and len(parts) == 5 and parts[4] == "suspend":
                self.api_admin_suspend_user(user, target_id, True)
                return
            if method == "POST" and len(parts) == 5 and parts[4] == "restore":
                self.api_admin_suspend_user(user, target_id, False)
                return
            if method == "POST" and len(parts) == 5 and parts[4] == "reset-password":
                self.api_admin_reset_user_password(user, target_id)
                return
            if method == "DELETE" and len(parts) == 4:
                self.api_admin_delete_user(user, target_id)
                return
        if method == "GET" and parts == ["api", "admin", "candidates"]:
            self.api_admin_candidates(user)
            return
        if method == "GET" and parts == ["api", "admin", "audit"]:
            self.api_admin_audit(user)
            return
        if method == "GET" and parts == ["api", "admin", "devices"]:
            self.api_admin_devices(user)
            return
        if method == "DELETE" and parts == ["api", "admin", "audit"]:
            self.api_admin_clear_audit(user)
            return
        if method == "GET" and parts == ["api", "admin", "backup.sqlite3"]:
            self.api_admin_backup(user)
            return
        if method == "POST" and parts == ["api", "admin", "restore"]:
            self.api_admin_restore(user)
            return
        if method == "DELETE" and len(parts) == 5 and parts[:3] == ["api", "admin", "audit"]:
            self.api_admin_delete_audit(user, parts[3], parts[4])
            return
        if method == "DELETE" and len(parts) == 4 and parts[:3] == ["api", "admin", "audit"]:
            self.api_admin_delete_audit(user, "login", parts[3])
            return
        if method == "GET" and parts == ["api", "admin", "stats"]:
            self.api_admin_stats(user)
            return
        if method == "GET" and parts == ["api", "admin", "exams"]:
            self.api_admin_exams(user)
            return
        if method == "POST" and parts == ["api", "admin", "exams"]:
            self.api_admin_save_exam(user)
            return
        if len(parts) >= 4 and parts[:3] == ["api", "admin", "exams"]:
            exam_id = parts[3]
            if method == "GET" and len(parts) == 4:
                self.api_admin_exam_detail(user, exam_id)
                return
            if method == "PUT" and len(parts) == 4:
                self.api_admin_save_exam(user, exam_id)
                return
            if method == "DELETE" and len(parts) == 4:
                self.api_admin_delete_exam(user, exam_id)
                return
            if method == "POST" and len(parts) == 5 and parts[4] == "import":
                self.api_admin_import_questions(user, exam_id)
                return
            if method == "POST" and len(parts) == 5 and parts[4] == "questions":
                self.api_admin_save_question(user, exam_id)
                return
            if len(parts) == 6 and parts[4] == "questions":
                question_id = parts[5]
                if method == "PUT":
                    self.api_admin_save_question(user, exam_id, question_id)
                    return
                if method == "DELETE":
                    self.api_admin_delete_question(user, exam_id, question_id)
                    return
        if method == "GET" and parts == ["api", "admin", "results"]:
            self.api_admin_results(user, query)
            return
        if method == "GET" and parts == ["api", "admin", "results", "export.xlsx"]:
            self.api_admin_results_export(user, query)
            return
        raise ApiError(HTTPStatus.NOT_FOUND, "Admin route not found.")

    def require_host_admin(self, user):
        if user["role"] != "admin":
            raise ApiError(HTTPStatus.FORBIDDEN, "Only the administrator can perform this action.")

    def clean_admin_user_payload(self, payload, existing=None):
        role = compact_text(payload.get("role") or (existing["role"] if existing else ""), 20)
        if role != "candidate":
            raise ApiError(HTTPStatus.BAD_REQUEST, "Only candidate accounts can be created here.")
        full_name = compact_text(payload.get("fullName"), 160)
        if len(full_name.split()) < 2:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Enter the full name.")

        cleaned = {"role": role, "full_name": full_name}
        index_number = normalize_index(payload.get("indexNumber"))
        if not re.match(r"^[A-Z0-9/_-]{3,40}$", index_number):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Use a valid index number.")
        duplicate = self.db.execute(
            "SELECT id FROM users WHERE index_number = ? AND id != ?",
            (index_number, existing["id"] if existing else ""),
        ).fetchone()
        if duplicate:
            raise ApiError(HTTPStatus.CONFLICT, "That index number is already registered.")
        cleaned["index_number"] = index_number
        cleaned["username"] = None
        return cleaned

    def close_user_sessions(self, user_id, closed_at=None):
        rows = self.db.execute("SELECT id FROM sessions WHERE user_id = ?", (user_id,)).fetchall()
        self.close_audit_for_sessions([row["id"] for row in rows], closed_at or iso_now())
        self.db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self.db.execute("UPDATE users SET active_session_id = NULL WHERE id = ?", (user_id,))

    def api_admin_save_user(self, user, target_id=None):
        self.require_host_admin(user)
        payload = self.read_json()
        existing = None
        if target_id:
            existing = self.db.execute(
                "SELECT * FROM users WHERE id = ? AND role = 'candidate'",
                (target_id,),
            ).fetchone()
            if not existing:
                raise ApiError(HTTPStatus.NOT_FOUND, "Registered user not found.")
        cleaned = self.clean_admin_user_payload(payload, existing)
        now = iso_now()
        if existing:
            self.db.execute(
                """
                UPDATE users
                SET full_name = ?, index_number = ?, username = ?
                WHERE id = ? AND role = ?
                """,
                (
                    cleaned["full_name"],
                    cleaned["index_number"],
                    cleaned["username"],
                    target_id,
                    cleaned["role"],
                ),
            )
            action_type = f"{cleaned['role']}_edited"
            action_label = f"{cleaned['role'].title()} edited"
            saved_id = target_id
        else:
            password = str(payload.get("password") or "")
            confirm = str(payload.get("confirmPassword") or "")
            if len(password) < 8:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Password must be at least 8 characters.")
            if password != confirm:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Passwords do not match.")
            salt, password_hash = hash_password(password)
            saved_id = new_id("usr")
            self.db.execute(
                """
                INSERT INTO users (
                  id, role, full_name, index_number, username, password_hash,
                  password_salt, approved, approved_at, approved_by, suspended, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0, ?)
                """,
                (
                    saved_id,
                    cleaned["role"],
                    cleaned["full_name"],
                    cleaned["index_number"],
                    cleaned["username"],
                    password_hash,
                    salt,
                    now,
                    user["id"],
                    now,
                ),
            )
            action_type = f"{cleaned['role']}_created"
            action_label = f"{cleaned['role'].title()} created"

        self.record_activity(
            user,
            action_type,
            action_label,
            cleaned["role"],
            cleaned["full_name"] or cleaned["username"] or cleaned["index_number"] or saved_id,
            {"userId": saved_id},
        )
        self.db.commit()
        self.send_json({"ok": True, "userId": saved_id})

    def api_admin_accept_user(self, user, target_id):
        self.require_host_admin(user)
        target = self.db.execute(
            "SELECT * FROM users WHERE id = ? AND role = 'candidate'",
            (target_id,),
        ).fetchone()
        if not target:
            raise ApiError(HTTPStatus.NOT_FOUND, "Registered user not found.")
        self.db.execute(
            """
            UPDATE users
            SET approved = 1, approved_at = ?, approved_by = ?, suspended = 0
            WHERE id = ?
            """,
            (iso_now(), user["id"], target_id),
        )
        self.record_activity(
            user,
            f"{target['role']}_accepted",
            f"{target['role'].title()} accepted",
            target["role"],
            target["full_name"] or target["username"] or target["index_number"] or target_id,
        )
        self.db.commit()
        self.send_json({"ok": True})

    def api_admin_suspend_user(self, user, target_id, suspended):
        self.require_host_admin(user)
        target = self.db.execute(
            "SELECT * FROM users WHERE id = ? AND role = 'candidate'",
            (target_id,),
        ).fetchone()
        if not target:
            raise ApiError(HTTPStatus.NOT_FOUND, "Registered user not found.")
        now = iso_now()
        if suspended:
            self.close_user_sessions(target_id, now)
            self.db.execute("UPDATE users SET suspended = 1 WHERE id = ?", (target_id,))
            action_type = f"{target['role']}_suspended"
            action_label = f"{target['role'].title()} suspended"
        else:
            self.db.execute(
                """
                UPDATE users
                SET suspended = 0, approved = 1,
                    approved_at = COALESCE(approved_at, ?),
                    approved_by = COALESCE(approved_by, ?)
                WHERE id = ?
                """,
                (now, user["id"], target_id),
            )
            action_type = f"{target['role']}_restored"
            action_label = f"{target['role'].title()} restored"
        self.record_activity(
            user,
            action_type,
            action_label,
            target["role"],
            target["full_name"] or target["username"] or target["index_number"] or target_id,
            {"userId": target_id},
        )
        self.db.commit()
        self.send_json({"ok": True})

    def api_admin_reset_user_password(self, user, target_id):
        self.require_host_admin(user)
        payload = self.read_json()
        password = str(payload.get("newPassword") or "")
        confirm = str(payload.get("confirmPassword") or "")
        if len(password) < 8:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Password must be at least 8 characters.")
        if password != confirm:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Passwords do not match.")
        target = self.db.execute(
            "SELECT * FROM users WHERE id = ? AND role = 'candidate'",
            (target_id,),
        ).fetchone()
        if not target:
            raise ApiError(HTTPStatus.NOT_FOUND, "Registered user not found.")
        salt, password_hash = hash_password(password)
        self.close_user_sessions(target_id, iso_now())
        self.db.execute(
            "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
            (password_hash, salt, target_id),
        )
        self.record_activity(
            user,
            f"{target['role']}_password_reset",
            f"{target['role'].title()} password reset",
            target["role"],
            target["full_name"] or target["username"] or target["index_number"] or target_id,
            {"userId": target_id},
        )
        self.db.commit()
        self.send_json({"ok": True})

    def api_admin_delete_user(self, user, target_id):
        self.require_host_admin(user)
        target = self.db.execute(
            "SELECT * FROM users WHERE id = ? AND role = 'candidate'",
            (target_id,),
        ).fetchone()
        if not target:
            raise ApiError(HTTPStatus.NOT_FOUND, "Registered user not found.")
        rows = self.db.execute(
            "SELECT id FROM sessions WHERE user_id = ?", (target_id,)
        ).fetchall()
        self.close_audit_for_sessions([row["id"] for row in rows], iso_now())
        self.record_activity(
            user,
            f"{target['role']}_removed",
            f"{target['role'].title()} removed",
            target["role"],
            target["full_name"] or target["username"] or target["index_number"] or target_id,
        )
        self.db.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
        self.db.execute("DELETE FROM users WHERE id = ?", (target_id,))
        self.db.commit()
        self.send_json({"ok": True})

    def api_admin_stats(self, user):
        where, params = self.scoped_exam_clause(user, "exams")
        active_open_exams = 0
        for row in self.db.execute(
            f"SELECT active, expires_at FROM exams WHERE {where}", params
        ).fetchall():
            if row["active"] and not exam_has_expired(row["expires_at"]):
                active_open_exams += 1
        exam_count = self.db.execute(
            f"SELECT COUNT(*) AS count FROM exams WHERE {where}", params
        ).fetchone()["count"]
        submission_count = self.db.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM attempts
            JOIN exams ON exams.id = attempts.exam_id
            WHERE attempts.status = 'submitted' AND {where}
            """,
            params,
        ).fetchone()["count"]
        stats = {
            "candidates": self.db.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'candidate'").fetchone()["count"],
            "exams": exam_count,
            "activeExams": active_open_exams,
            "submissions": submission_count,
        }
        self.send_json({"stats": stats})

    def api_admin_candidates(self, user):
        rows = self.db.execute(
            """
            SELECT id, full_name, index_number, approved, approved_at, suspended, created_at
            FROM users
            WHERE role = 'candidate'
            ORDER BY suspended DESC, approved ASC, full_name ASC
            """
        ).fetchall()
        self.send_json(
            {
                "candidates": [
                    {
                        "id": row["id"],
                        "fullName": row["full_name"],
                        "indexNumber": row["index_number"],
                        "approved": bool(row["approved"]),
                        "approvedAt": row["approved_at"],
                        "suspended": bool(row["suspended"]),
                        "createdAt": row["created_at"],
                    }
                    for row in rows
                ]
            }
        )

    def latest_successful_login(self, user_id, before_time=None):
        if not user_id:
            return None
        query = """
            SELECT * FROM login_audit
            WHERE user_id = ? AND outcome = 'success'
        """
        params = [user_id]
        if before_time:
            query += " AND time_in <= ?"
            params.append(before_time)
        query += " ORDER BY time_in DESC LIMIT 1"
        return self.db.execute(query, params).fetchone()

    def attempt_submission_mode(self, attempt_id):
        row = self.db.execute(
            """
            SELECT event_type FROM exam_events
            WHERE attempt_id = ?
              AND event_type IN ('submitted_exam', 'auto_submitted', 'time_expired', 'exam_expired')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (attempt_id,),
        ).fetchone()
        event_type = row["event_type"] if row else ""
        if event_type == "submitted_exam":
            return "Manual"
        if event_type in {"auto_submitted", "time_expired"}:
            return "Time expired"
        if event_type == "exam_expired":
            return "Exam ended"
        return "In progress"

    def student_activity_rows(self):
        rows = self.db.execute(
            """
            SELECT
              attempts.*,
              users.full_name,
              users.index_number,
              exams.title AS exam_title,
              exams.scheduled_at AS exam_scheduled_at
            FROM attempts
            JOIN users ON users.id = attempts.candidate_id
            JOIN exams ON exams.id = attempts.exam_id
            WHERE NOT EXISTS (
              SELECT 1 FROM audit_suppressed
              WHERE audit_suppressed.record_type = 'student'
                AND audit_suppressed.record_id = attempts.id
            )
            ORDER BY COALESCE(attempts.submitted_at, attempts.started_at) DESC
            """
        ).fetchall()
        activities = []
        for row in rows:
            login = self.latest_successful_login(row["candidate_id"], row["started_at"])
            suspicious_count = self.db.execute(
                """
                SELECT COUNT(*) AS count FROM exam_events
                WHERE attempt_id = ?
                  AND event_type NOT IN ('started_exam', 'submitted_exam', 'auto_submitted', 'time_expired', 'exam_expired')
                """,
                (row["id"],),
            ).fetchone()["count"]
            early_exit_count = self.db.execute(
                """
                SELECT COUNT(*) AS count FROM exam_events
                WHERE attempt_id = ? AND event_type = 'attempt_leave'
                """,
                (row["id"],),
            ).fetchone()["count"]
            late_login = False
            try:
                late_login = parse_utc(row["started_at"]) > datetime_local_to_utc(row["exam_scheduled_at"])
            except Exception:
                late_login = False
            activities.append(
                {
                    "id": row["id"],
                    "recordType": "student",
                    "name": row["full_name"],
                    "indexNumber": row["index_number"],
                    "loginDateTime": login["time_in"] if login else "",
                    "examTitle": row["exam_title"],
                    "examStartedAt": row["started_at"],
                    "examEndedAt": row["due_at"],
                    "submittedAt": row["submitted_at"],
                    "submissionMode": self.attempt_submission_mode(row["id"]),
                    "score": row["score"],
                    "totalMarks": row["total_marks"],
                    "percentage": row["percentage"],
                    "deviceUsed": login["device_used"] if login else "",
                    "ipAddress": login["ip_address"] if login else "",
                    "suspiciousCount": suspicious_count,
                    "earlyExit": early_exit_count > 0,
                    "lateLogin": late_login,
                }
            )
        return activities

    def examiner_activity_rows(self):
        login_rows = self.db.execute(
            """
            SELECT * FROM login_audit
            WHERE user_type IN ('examiner', 'admin') AND outcome = 'success'
            ORDER BY time_in DESC
            """
        ).fetchall()
        activity_rows = self.db.execute(
            """
            SELECT * FROM activity_audit
            WHERE user_type IN ('examiner', 'admin')
            ORDER BY occurred_at DESC
            """
        ).fetchall()
        rows = []
        for row in login_rows:
            rows.append(
                {
                    "id": row["id"],
                    "recordType": "login",
                    "userType": row["user_type"],
                    "name": row["display_name"],
                    "identifier": row["user_identifier"],
                    "action": "Logged in",
                    "target": "",
                    "occurredAt": row["time_in"],
                    "timeOut": row["time_out"],
                    "deviceUsed": row["device_used"],
                    "ipAddress": row["ip_address"],
                    "deletable": True,
                }
            )
        for row in activity_rows:
            rows.append(
                {
                    "id": row["id"],
                    "recordType": "activity",
                    "userType": row["user_type"],
                    "name": row["display_name"],
                    "identifier": row["user_identifier"],
                    "action": row["action_label"],
                    "target": row["target_name"],
                    "occurredAt": row["occurred_at"],
                    "timeOut": "",
                    "deviceUsed": row["device_used"],
                    "ipAddress": row["ip_address"],
                    "deletable": True,
                }
            )
        return sorted(rows, key=lambda item: item["occurredAt"] or "", reverse=True)

    def super_admin_alerts(self, student_rows):
        alerts = []
        failed = self.db.execute(
            """
            SELECT * FROM login_audit
            WHERE outcome = 'failed'
            ORDER BY time_in DESC
            """
        ).fetchall()
        for row in failed:
            alerts.append(
                {
                    "id": row["id"],
                    "recordType": "login",
                    "category": "Failed login",
                    "userType": row["user_type"],
                    "name": row["display_name"],
                    "identifier": row["user_identifier"],
                    "time": row["time_in"],
                    "detail": row["failure_reason"] or "Login failed",
                    "deviceUsed": row["device_used"],
                    "ipAddress": row["ip_address"],
                }
            )
        flagged = self.db.execute(
            """
            SELECT * FROM login_audit
            WHERE outcome = 'success' AND flags != ''
            ORDER BY time_in DESC
            """
        ).fetchall()
        for row in flagged:
            category = "Multiple login" if "multiple_login" in row["flags"] else "Suspicious login"
            alerts.append(
                {
                    "id": row["id"],
                    "recordType": "login",
                    "category": category,
                    "userType": row["user_type"],
                    "name": row["display_name"],
                    "identifier": row["user_identifier"],
                    "time": row["time_in"],
                    "detail": row["flags"].replace("_", " "),
                    "deviceUsed": row["device_used"],
                    "ipAddress": row["ip_address"],
                }
            )
        for row in student_rows:
            if row["lateLogin"]:
                alerts.append(
                    {
                        "id": row["id"],
                        "recordType": "student",
                        "category": "Late login",
                        "userType": "candidate",
                        "name": row["name"],
                        "identifier": row["indexNumber"],
                        "time": row["examStartedAt"],
                        "detail": row["examTitle"],
                        "deviceUsed": row["deviceUsed"],
                        "ipAddress": row["ipAddress"],
                    }
                )
            if row["earlyExit"]:
                alerts.append(
                    {
                        "id": row["id"],
                        "recordType": "student",
                        "category": "Early exit",
                        "userType": "candidate",
                        "name": row["name"],
                        "identifier": row["indexNumber"],
                        "time": row["submittedAt"] or row["examStartedAt"],
                        "detail": row["examTitle"],
                        "deviceUsed": row["deviceUsed"],
                        "ipAddress": row["ipAddress"],
                    }
                )
        alerts.sort(key=lambda item: item["time"] or "", reverse=True)
        examiner_activity_count = self.db.execute(
            """
            SELECT COUNT(*) AS count FROM activity_audit
            WHERE user_type IN ('examiner', 'admin')
            """
        ).fetchone()["count"]
        examiner_login_count = self.db.execute(
            """
            SELECT COUNT(*) AS count FROM login_audit
            WHERE user_type IN ('examiner', 'admin') AND outcome = 'success'
            """
        ).fetchone()["count"]
        summary = {
            "studentActivity": len(student_rows),
            "examinerActivity": examiner_activity_count + examiner_login_count,
            "failedLogins": len(failed),
            "multipleLogins": sum(1 for row in flagged if "multiple_login" in row["flags"]),
            "lateLogins": sum(1 for row in student_rows if row["lateLogin"]),
            "earlyExits": sum(1 for row in student_rows if row["earlyExit"]),
        }
        return {"summary": summary, "alerts": alerts}

    def api_admin_audit(self, user):
        self.require_host_admin(user)
        students = self.student_activity_rows()
        examiners = self.examiner_activity_rows()
        self.send_json(
            {
                "students": students,
                "examiners": examiners,
                "superAdmin": self.super_admin_alerts(students),
            }
        )

    def api_admin_devices(self, user):
        self.require_host_admin(user)
        now = iso_now()
        self.close_expired_sessions(now)
        rows = self.db.execute(
            """
            SELECT
              sessions.id,
              sessions.role AS session_role,
              sessions.user_agent,
              sessions.created_at,
              sessions.last_seen,
              sessions.expires_at,
              users.role AS user_role,
              users.full_name,
              users.index_number,
              users.username,
              login_audit.device_used,
              login_audit.ip_address,
              attempts.id AS attempt_id,
              attempts.started_at AS attempt_started_at,
              attempts.due_at AS attempt_due_at,
              exams.title AS exam_title
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            LEFT JOIN login_audit ON login_audit.session_id = sessions.id
            LEFT JOIN attempts ON attempts.candidate_id = users.id AND attempts.status = 'in_progress'
            LEFT JOIN exams ON exams.id = attempts.exam_id
            WHERE sessions.expires_at > ?
            ORDER BY sessions.last_seen DESC, sessions.created_at DESC
            """,
            (now,),
        ).fetchall()
        devices = []
        for row in rows:
            name = row["full_name"] or row["username"] or row["index_number"] or "Unknown user"
            identifier = row["index_number"] if row["user_role"] == "candidate" else row["username"]
            devices.append(
                {
                    "sessionId": row["id"],
                    "userType": row["user_role"],
                    "name": name,
                    "identifier": identifier,
                    "deviceUsed": row["device_used"] or user_agent_summary(row["user_agent"]),
                    "ipAddress": row["ip_address"] or "",
                    "connectedAt": row["created_at"],
                    "lastSeen": row["last_seen"],
                    "expiresAt": row["expires_at"],
                    "examTitle": row["exam_title"] or "",
                    "attemptStartedAt": row["attempt_started_at"] or "",
                    "attemptDueAt": row["attempt_due_at"] or "",
                }
            )
        self.db.commit()
        self.send_json({"devices": devices})

    def api_admin_backup(self, user):
        self.require_host_admin(user)
        self.record_activity(
            user,
            "database_backup_downloaded",
            "Database backup downloaded",
            "system",
            "SQLite database",
        )
        self.db.commit()
        filename = f"chiefexam-backup-{utc_now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
        self.send_bytes(DB_PATH.read_bytes(), "application/vnd.sqlite3", filename)

    def api_admin_restore(self, user):
        self.require_host_admin(user)
        _, files = self.read_multipart()
        upload = files.get("file")
        if not upload:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Choose a SQLite backup file.")
        tmp_name = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite3") as tmp:
                tmp.write(upload["content"])
                tmp_name = tmp.name
            source = sqlite3.connect(tmp_name)
            try:
                integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity.lower() != "ok":
                    raise ApiError(HTTPStatus.BAD_REQUEST, "The backup database failed integrity checks.")
                tables = {
                    row[0]
                    for row in source.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                required = {"users", "exams", "questions", "attempts"}
                if not required.issubset(tables):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "That file is not a compatible app backup.")
                source.backup(self.db)
            finally:
                source.close()
            migrate_db(self.db)
            self.db.commit()
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
        self.send_json({"ok": True})

    def api_admin_delete_audit(self, user, audit_type, audit_id):
        self.require_host_admin(user)
        if audit_type == "student":
            self.db.execute(
                """
                INSERT OR REPLACE INTO audit_suppressed (record_type, record_id, deleted_at, deleted_by)
                VALUES ('student', ?, ?, ?)
                """,
                (audit_id, iso_now(), user["id"]),
            )
        elif audit_type == "activity":
            self.db.execute("DELETE FROM activity_audit WHERE id = ?", (audit_id,))
        else:
            self.db.execute("DELETE FROM login_audit WHERE id = ?", (audit_id,))
        self.db.commit()
        self.send_json({"ok": True})

    def api_admin_clear_audit(self, user):
        self.require_host_admin(user)
        now = iso_now()
        attempt_rows = self.db.execute("SELECT id FROM attempts").fetchall()
        for row in attempt_rows:
            self.db.execute(
                """
                INSERT OR REPLACE INTO audit_suppressed (record_type, record_id, deleted_at, deleted_by)
                VALUES ('student', ?, ?, ?)
                """,
                (row["id"], now, user["id"]),
            )
        self.db.execute("DELETE FROM login_audit")
        self.db.execute("DELETE FROM activity_audit")
        self.db.commit()
        self.send_json({"ok": True})

    def api_admin_exams(self, user):
        where, params = self.scoped_exam_clause(user, "exams")
        rows = self.db.execute(
            f"""
            SELECT exams.*, users.full_name AS owner_name
            FROM exams
            LEFT JOIN users ON users.id = exams.created_by
            WHERE {where}
            ORDER BY exams.scheduled_at DESC, exams.title ASC
            """,
            params,
        ).fetchall()
        exams = []
        for row in rows:
            exam = row_to_exam(row)
            exam["questionCount"] = self.db.execute(
                "SELECT COUNT(*) AS count FROM questions WHERE exam_id = ?", (row["id"],)
            ).fetchone()["count"]
            exam["totalMarks"] = self.db.execute(
                "SELECT COALESCE(SUM(marks), 0) AS total FROM questions WHERE exam_id = ?", (row["id"],)
            ).fetchone()["total"]
            exams.append(exam)
        self.send_json({"exams": exams})

    def api_admin_exam_detail(self, user, exam_id):
        exam = self.get_exam_for_backend_user(user, exam_id)
        questions = [row_to_question(row) for row in get_questions(self.db, exam_id)]
        self.send_json({"exam": row_to_exam(exam), "questions": questions})

    def clean_exam_payload(self, payload):
        title = compact_text(payload.get("title"), 180)
        instructions = compact_text(payload.get("instructions"), 5000)
        scheduled_at = compact_text(payload.get("scheduledAt"), 40)
        time_limit = int(payload.get("timeLimitMinutes") or 0)
        max_attempts = int(payload.get("maxAttempts") or 1)
        try:
            scheduled_dt = parse_datetime_local(scheduled_at)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        if not title:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Exam title is required.")
        if time_limit <= 0 or time_limit > 600:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Time limit must be between 1 and 600 minutes.")
        if max_attempts <= 0 or max_attempts > 10:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Number of attempts must be between 1 and 10.")
        expires_at = compact_text(payload.get("expiresAt"), 40)
        try:
            expires_dt = (
                parse_datetime_local(expires_at)
                if expires_at
                else scheduled_dt + timedelta(minutes=time_limit)
            )
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Use a valid expiration date and time.") from exc
        if expires_dt <= scheduled_dt:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Expiration date and time must be after the exam start date and time.")
        return {
            "title": title,
            "instructions": instructions,
            "scheduled_at": scheduled_dt.isoformat(timespec="minutes"),
            "expires_at": expires_dt.isoformat(timespec="minutes"),
            "time_limit_minutes": time_limit,
            "active": 1 if as_bool(payload.get("active")) else 0,
            "randomize_questions": 1 if as_bool(payload.get("randomizeQuestions")) else 0,
            "randomize_options": 1 if as_bool(payload.get("randomizeOptions")) else 0,
            "max_attempts": max_attempts,
            "assigned_index_numbers": json.dumps(assigned_list(payload.get("assignedIndexNumbers"))),
        }

    def api_admin_save_exam(self, user, exam_id=None):
        payload = self.read_json()
        cleaned = self.clean_exam_payload(payload)
        now = iso_now()
        if exam_id:
            existing_exam = self.get_exam_for_backend_user(user, exam_id)
            self.db.execute(
                """
                UPDATE exams
                SET title = ?, instructions = ?, scheduled_at = ?, expires_at = ?, time_limit_minutes = ?,
                    active = ?, randomize_questions = ?, randomize_options = ?, max_attempts = ?,
                    assigned_index_numbers = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    cleaned["title"],
                    cleaned["instructions"],
                    cleaned["scheduled_at"],
                    cleaned["expires_at"],
                    cleaned["time_limit_minutes"],
                    cleaned["active"],
                    cleaned["randomize_questions"],
                    cleaned["randomize_options"],
                    cleaned["max_attempts"],
                    cleaned["assigned_index_numbers"],
                    now,
                    exam_id,
                ),
            )
            self.record_activity(
                user,
                "exam_edited",
                "Exam edited",
                "exam",
                cleaned["title"] or existing_exam["title"],
                {"examId": exam_id},
            )
        else:
            exam_id = new_id("exm")
            self.db.execute(
                """
                INSERT INTO exams (
                  id, title, instructions, scheduled_at, expires_at, time_limit_minutes,
                  active, randomize_questions, randomize_options, max_attempts,
                  assigned_index_numbers, created_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exam_id,
                    cleaned["title"],
                    cleaned["instructions"],
                    cleaned["scheduled_at"],
                    cleaned["expires_at"],
                    cleaned["time_limit_minutes"],
                    cleaned["active"],
                    cleaned["randomize_questions"],
                    cleaned["randomize_options"],
                    cleaned["max_attempts"],
                    cleaned["assigned_index_numbers"],
                    user["id"],
                    now,
                    now,
                ),
            )
            self.record_activity(
                user,
                "exam_created",
                "Exam created",
                "exam",
                cleaned["title"],
                {"examId": exam_id},
            )
        self.db.commit()
        self.api_admin_exam_detail(user, exam_id)

    def api_admin_delete_exam(self, user, exam_id):
        exam = self.get_exam_for_backend_user(user, exam_id)
        attempts = self.db.execute(
            "SELECT COUNT(*) AS count FROM attempts WHERE exam_id = ?", (exam_id,)
        ).fetchone()["count"]
        if attempts:
            raise ApiError(HTTPStatus.CONFLICT, "This exam has attempts and cannot be deleted.")
        self.record_activity(user, "exam_deleted", "Exam deleted", "exam", exam["title"], {"examId": exam_id})
        self.db.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
        self.db.commit()
        self.send_json({"ok": True})

    def clean_question_payload(self, payload):
        text = compact_text(payload.get("text"), 6000)
        options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        option_a = compact_text(options.get("A"), 2000)
        option_b = compact_text(options.get("B"), 2000)
        option_c = compact_text(options.get("C"), 2000)
        option_d = compact_text(options.get("D"), 2000)
        answer = compact_text(payload.get("correctAnswer"), 1).upper()
        rationale = compact_text(payload.get("rationale"), 4000)
        try:
            marks = float(payload.get("marks") or 0)
        except (TypeError, ValueError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Marks must be a number.") from exc
        if not text:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Question text is required.")
        if not all([option_a, option_b, option_c, option_d]):
            raise ApiError(HTTPStatus.BAD_REQUEST, "All four options are required.")
        if answer not in "ABCD":
            raise ApiError(HTTPStatus.BAD_REQUEST, "Correct answer must be A, B, C, or D.")
        if marks <= 0 or marks > 100:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Marks must be between 0 and 100.")
        return {
            "question_text": text,
            "option_a": option_a,
            "option_b": option_b,
            "option_c": option_c,
            "option_d": option_d,
            "correct_answer": answer,
            "rationale": rationale,
            "marks": marks,
        }

    def api_admin_save_question(self, user, exam_id, question_id=None):
        exam = self.get_exam_for_backend_user(user, exam_id)
        payload = self.read_json()
        cleaned = self.clean_question_payload(payload)
        now = iso_now()
        action_type = "question_edited" if question_id else "question_added"
        action_label = "Question edited" if question_id else "Question added"
        if question_id:
            existing = self.db.execute(
                "SELECT id FROM questions WHERE id = ? AND exam_id = ?",
                (question_id, exam_id),
            ).fetchone()
            if not existing:
                raise ApiError(HTTPStatus.NOT_FOUND, "Question not found.")
            self.db.execute(
                """
                UPDATE questions
                SET question_text = ?, option_a = ?, option_b = ?, option_c = ?,
                    option_d = ?, correct_answer = ?, rationale = ?, marks = ?,
                    updated_at = ?
                WHERE id = ? AND exam_id = ?
                """,
                (
                    cleaned["question_text"],
                    cleaned["option_a"],
                    cleaned["option_b"],
                    cleaned["option_c"],
                    cleaned["option_d"],
                    cleaned["correct_answer"],
                    cleaned["rationale"],
                    cleaned["marks"],
                    now,
                    question_id,
                    exam_id,
                ),
            )
        else:
            position = self.db.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS next_position FROM questions WHERE exam_id = ?",
                (exam_id,),
            ).fetchone()["next_position"]
            question_id = new_id("qst")
            self.db.execute(
                """
                INSERT INTO questions (
                  id, exam_id, question_text, option_a, option_b, option_c,
                  option_d, correct_answer, rationale, marks, position,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question_id,
                    exam_id,
                    cleaned["question_text"],
                    cleaned["option_a"],
                    cleaned["option_b"],
                    cleaned["option_c"],
                    cleaned["option_d"],
                    cleaned["correct_answer"],
                    cleaned["rationale"],
                    cleaned["marks"],
                    position,
                    now,
                    now,
                ),
            )
        self.record_activity(
            user,
            action_type,
            action_label,
            "exam",
            exam["title"],
            {"examId": exam_id, "questionId": question_id},
        )
        self.db.commit()
        self.api_admin_exam_detail(user, exam_id)

    def api_admin_delete_question(self, user, exam_id, question_id):
        exam = self.get_exam_for_backend_user(user, exam_id)
        self.record_activity(
            user,
            "question_deleted",
            "Question deleted",
            "exam",
            exam["title"],
            {"examId": exam_id, "questionId": question_id},
        )
        self.db.execute("DELETE FROM questions WHERE id = ? AND exam_id = ?", (question_id, exam_id))
        self.db.commit()
        self.api_admin_exam_detail(user, exam_id)

    def api_admin_import_questions(self, user, exam_id):
        exam = self.get_exam_for_backend_user(user, exam_id)
        _, files = self.read_multipart()
        upload = files.get("file")
        if not upload:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Choose a .txt, .docx, or .csv file.")
        filename = upload["filename"].lower()
        content = upload["content"]
        if filename.endswith(".txt"):
            text = content.decode("utf-8-sig", errors="replace")
            parsed = parse_question_text(text)
        elif filename.endswith(".docx"):
            try:
                text = extract_docx_text(content)
            except Exception as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Could not read that Word document.") from exc
            parsed = parse_question_text(text)
        elif filename.endswith(".csv"):
            text = content.decode("utf-8-sig", errors="replace")
            parsed = parse_question_csv(text)
        else:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Only .txt, .docx, and .csv uploads are supported.")
        if not parsed:
            raise ApiError(HTTPStatus.BAD_REQUEST, "No valid questions were found in the file.")

        position = self.db.execute(
            "SELECT COALESCE(MAX(position), 0) AS max_position FROM questions WHERE exam_id = ?",
            (exam_id,),
        ).fetchone()["max_position"]
        now = iso_now()
        for item in parsed:
            position += 1
            self.db.execute(
                """
                INSERT INTO questions (
                  id, exam_id, question_text, option_a, option_b, option_c,
                  option_d, correct_answer, rationale, marks, position,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("qst"),
                    exam_id,
                    item["text"],
                    item["options"]["A"],
                    item["options"]["B"],
                    item["options"]["C"],
                    item["options"]["D"],
                    item["correctAnswer"],
                    item["rationale"],
                    item["marks"],
                    position,
                    now,
                    now,
                ),
            )
        self.record_activity(
            user,
            "questions_uploaded",
            "Questions uploaded",
            "exam",
            exam["title"],
            {"examId": exam_id, "filename": filename, "questionCount": len(parsed)},
        )
        self.db.commit()
        self.send_json({"imported": len(parsed)})

    def query_results(self, user, query):
        exam_id = (query.get("examId") or [""])[0]
        params = []
        where = "attempts.status = 'submitted'"
        if exam_id:
            where += " AND attempts.exam_id = ?"
            params.append(exam_id)
        scope_where, scope_params = self.scoped_exam_clause(user, "exams")
        where += f" AND {scope_where}"
        params.extend(scope_params)
        return self.db.execute(
            f"""
            SELECT
              attempts.*,
              users.full_name,
              users.index_number,
              exams.title AS exam_title,
              (
                SELECT COUNT(*) FROM exam_events
                WHERE exam_events.attempt_id = attempts.id
                  AND event_type NOT IN ('started_exam', 'submitted_exam', 'time_expired', 'exam_expired')
              ) AS suspicious_count
            FROM attempts
            JOIN users ON users.id = attempts.candidate_id
            JOIN exams ON exams.id = attempts.exam_id
            WHERE {where}
            ORDER BY attempts.submitted_at DESC
            """,
            params,
        ).fetchall()

    def api_admin_results(self, user, query):
        rows = self.query_results(user, query)
        self.send_json(
            {
                "results": [
                    {
                        "attemptId": row["id"],
                        "attemptNumber": row["attempt_number"] if "attempt_number" in row.keys() else 1,
                        "candidateFullName": row["full_name"],
                        "indexNumber": row["index_number"],
                        "examTitle": row["exam_title"],
                        "score": row["score"],
                        "totalMarks": row["total_marks"],
                        "percentage": row["percentage"],
                        "submittedAt": row["submitted_at"],
                        "timeSpentSeconds": row["time_spent_seconds"],
                        "timeSpent": format_seconds(row["time_spent_seconds"]),
                        "suspiciousCount": row["suspicious_count"],
                    }
                    for row in rows
                ]
            }
        )

    def api_admin_results_export(self, user, query):
        rows = self.query_results(user, query)
        self.record_activity(
            user,
            "results_downloaded",
            "Results downloaded",
            "results",
            "Candidate results",
            {"rowCount": len(rows), "examId": (query.get("examId") or [""])[0]},
        )
        self.db.commit()
        data = [
            [
                "Full name",
                "Index number",
                "Exam title",
                "Attempt",
                "Score obtained",
                "Total marks",
                "Percentage",
                "Submission time",
                "Time spent",
            ]
        ]
        for row in rows:
            data.append(
                [
                    row["full_name"],
                    row["index_number"],
                    row["exam_title"],
                    row["attempt_number"] if "attempt_number" in row.keys() else 1,
                    row["score"],
                    row["total_marks"],
                    row["percentage"],
                    row["submitted_at"],
                    format_seconds(row["time_spent_seconds"]),
                ]
            )
        self.send_bytes(
            make_xlsx(data),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "kyeremeh-v1.1-results.xlsx",
        )

    def serve_static(self, path, head_only=False):
        if path == "/":
            path = "/index.html"
        decoded = urllib.parse.unquote(path)
        normalized = posixpath.normpath(decoded).lstrip("/")
        file_path = (PUBLIC_DIR / normalized).resolve()
        try:
            file_path.relative_to(PUBLIC_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not file_path.exists() or not file_path.is_file():
            if "." not in file_path.name:
                file_path = PUBLIC_DIR / "index.html"
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if file_path.name in {"index.html", "manifest.webmanifest", "service-worker.js"}:
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        if file_path.name == "service-worker.js":
            self.send_header("Service-Worker-Allowed", "/")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)


def main():
    init_db()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), ChiefExamHandler)
    actual_port = server.server_address[1]
    print(f"ChiefExam running locally at http://127.0.0.1:{actual_port}")
    if PUBLIC_URL:
        print(f"Configured public link: {normalized_base_url(PUBLIC_URL)}")
    elif is_loopback_host(host):
        print("LAN access is off because HOST is set to a loopback address.")
        print("Restart with HOST=0.0.0.0 to let nearby devices scan in.")
    else:
        lan_urls = [f"http://{address}:{actual_port}" for address in collect_lan_ipv4_addresses()]
        if lan_urls:
            print("LAN links:")
            for url in lan_urls:
                print(f"  {url}")
        else:
            print("No LAN address detected yet. Connect to Wi-Fi/Ethernet and refresh the QR code.")
    print("Default admin: admin / Admin@12345")
    print("Set KYEREMEH_ADMIN_PASSWORD before first run to change the initial password.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ChiefExam.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
