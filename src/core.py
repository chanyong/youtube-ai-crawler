"""
core.py — 공통 유틸리티 모듈
YouTube 채널 파싱, 자막 수집, 요약, 이메일 발송, DB, 암호화 등
main.py / web.py 양쪽에서 import하여 사용
"""

import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from smtplib import SMTP
from typing import Optional
from urllib.parse import parse_qs, urlparse
from xml.etree.ElementTree import ParseError
from importlib.metadata import version

import feedparser
import requests
from cryptography.fernet import Fernet
from openai import OpenAI
from youtube_transcript_api import YouTubeTranscriptApi
from yt_dlp import YoutubeDL

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
# Railway Volume 사용 시 DB_PATH 환경변수로 경로 지정 가능
# 예) DB_PATH=/data/app.db  (Railway Volume을 /data에 마운트한 경우)
DB_PATH = Path(os.environ.get("DB_PATH", str(BASE_DIR / "data" / "app.db")))

# ---------------------------------------------------------------------------
# Encryption helpers  (API 키 암호화 저장)
# ---------------------------------------------------------------------------
_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.environ.get("ENCRYPT_KEY", "")
        if not key:
            raise ValueError(
                "ENCRYPT_KEY 환경변수가 설정되지 않았습니다. "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" 로 생성하세요."
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt_value(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_value(token: str) -> str:
    return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
import hashlib


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 240_000
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    computed = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 240_000
    ).hex()
    return secrets.compare_digest(computed, digest)


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


@contextmanager
def db_connection():
    """Context-manager that auto-commits on success and always closes."""
    con = get_db()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    os.makedirs(BASE_DIR / "data", exist_ok=True)
    with db_connection() as con:
        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                recipient_email TEXT,
                openai_api_key TEXT,
                openai_model TEXT DEFAULT 'gpt-4o-mini',
                summary_prompt TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        # Lightweight migration for existing DBs.
        cols = [r[1] for r in cur.execute("PRAGMA table_info(app_users)").fetchall()]
        if "summary_prompt" not in cols:
            cur.execute("ALTER TABLE app_users ADD COLUMN summary_prompt TEXT")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                source TEXT NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, channel_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                video_title TEXT,
                sent_at TEXT NOT NULL,
                UNIQUE(user_id, video_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS generated_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                channel_title TEXT,
                video_id TEXT NOT NULL,
                video_title TEXT,
                video_url TEXT,
                summary_ko TEXT,
                generation_status TEXT NOT NULL DEFAULT 'generated',
                error_message TEXT,
                generated_at TEXT NOT NULL,
                UNIQUE(user_id, video_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS scanned_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                channel_title TEXT,
                video_id TEXT NOT NULL,
                video_title TEXT,
                video_url TEXT,
                published_at TEXT,
                scanned_at TEXT NOT NULL,
                UNIQUE(user_id, video_id)
            )
            """
        )


# ---------------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------------
def extract_channel_id(channel_ref: str) -> str:
    channel_ref = channel_ref.strip()
    if re.fullmatch(r"UC[a-zA-Z0-9_-]{22}", channel_ref):
        return channel_ref

    if channel_ref.startswith("http"):
        parsed = urlparse(channel_ref)
        parts = [p for p in parsed.path.split("/") if p]
        if (
            len(parts) >= 2
            and parts[0] == "channel"
            and re.fullmatch(r"UC[a-zA-Z0-9_-]{22}", parts[1])
        ):
            return parts[1]
        # /@handle/videos, /@handle/streams 등을 /@handle로 정규화
        if parts and parts[0].startswith("@"):
            channel_ref = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}"
        return _resolve_channel_id_from_page(channel_ref)

    if channel_ref.startswith("@"):
        return _resolve_channel_id_from_page(
            f"https://www.youtube.com/{channel_ref}"
        )

    raise ValueError("채널 URL, @handle 또는 UC... 채널 ID를 입력하세요.")


def _resolve_channel_id_from_page(url: str) -> str:
    # 1) yt-dlp 기반 해석 (handle/videos URL에 강함)
    try:
        with YoutubeDL({"quiet": True, "skip_download": True, "extract_flat": True, "playlistend": 1}) as ydl:
            info = ydl.extract_info(url, download=False)
        ch_id = info.get("channel_id")
        if ch_id and re.fullmatch(r"UC[a-zA-Z0-9_-]{22}", ch_id):
            return ch_id
    except Exception:
        pass

    # 2) 페이지 파싱 fallback
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    canonical = re.search(r'rel="canonical"\s+href="https://www\.youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})"', resp.text)
    if canonical:
        return canonical.group(1)
    m = re.search(r'"channelId":"(UC[a-zA-Z0-9_-]{22})"', resp.text)
    if not m:
        raise ValueError(f"채널 ID를 찾을 수 없습니다: {url}")
    return m.group(1)


def get_feed(channel_id: str):
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(feed_url)
    # bozo=True means minor XML issues; YouTube feeds sometimes trigger this.
    # Only fail if feedparser couldn't extract any entries at all.
    if getattr(feed, "bozo", False) and not feed.entries:
        raise ValueError(f"피드 조회 실패: {channel_id}")
    return feed


def parse_video_id(entry) -> Optional[str]:
    if "yt_videoid" in entry:
        return entry.get("yt_videoid")
    link = entry.get("link", "")
    vals = parse_qs(urlparse(link).query).get("v")
    return vals[0] if vals else None


def fetch_transcript(video_id: str) -> str:
    # youtube-transcript-api v1.1.0+ 권장
    try:
        ver = version("youtube-transcript-api")
        parts = ver.split(".")
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        if major == 1 and minor < 1:
            print(f"[warn] youtube-transcript-api v{ver} 감지: v1.1.0+ 업그레이드 권장")
    except Exception:
        pass

    last_error: Optional[Exception] = None
    for _ in range(2):
        try:
            # 최신 API (v1.1.0+): 인스턴스 메서드
            try:
                ytt = YouTubeTranscriptApi()
                items = ytt.list(video_id)
            except AttributeError:
                # 구버전 API (v0.x) fallback - 메서드 자체가 없을 때만
                items = YouTubeTranscriptApi.list_transcripts(video_id)  # type: ignore[attr-defined]

            transcript = None
            try:
                transcript = items.find_transcript(["en"])
            except Exception:
                try:
                    transcript = items.find_generated_transcript(["en"])
                except Exception:
                    pass
            if not transcript:
                raise ValueError("영문 자막을 찾을 수 없습니다.")

            rows = transcript.fetch()
            # youtube-transcript-api 최신 버전 호환
            if rows and hasattr(rows[0], "text"):
                text = " ".join(r.text.replace("\n", " ").strip() for r in rows)
            else:
                text = " ".join(
                    r.get("text", "").replace("\n", " ").strip() for r in rows
                )
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                raise ValueError("자막 내용이 비어 있습니다.")
            return text
        except Exception as e:
            last_error = e
            time.sleep(0.5)
    if isinstance(last_error, ParseError) or "no element found" in str(last_error):
        raise ValueError("이 영상의 자막을 가져올 수 없습니다(비공개/비활성화/접근 제한).")
    raise ValueError(f"자막 수집 실패: {last_error}")




# ---------------------------------------------------------------------------
# Summarization (OpenAI Chat Completions API)
# ---------------------------------------------------------------------------
SUMMARY_SYSTEM_PROMPT = (
    "다음은 영어 유튜브 영상 자막입니다. 최대한 상세하게 내용을 정리해줘."
)


def summarize_korean(
    api_key: str,
    model: str,
    video_title: str,
    video_url: str,
    transcript: str,
    prompt: str = "",
) -> str:
    client = OpenAI(api_key=api_key)
    clipped = transcript[:12_000]
    user_content = f"제목: {video_title}\nURL: {video_url}\n\n자막:\n{clipped}"
    system_prompt = (prompt or "").strip() or SUMMARY_SYSTEM_PROMPT

    resp = client.chat.completions.create(
        model=model or "gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
    )
    return resp.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Email (HTML + plain text)
# ---------------------------------------------------------------------------
EMAIL_HTML_TEMPLATE = """\
<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"></head>
<body style="font-family:'Apple SD Gothic Neo','Noto Sans KR',sans-serif;color:#1a1a2e;max-width:680px;margin:0 auto;padding:20px;">
  <div style="border-bottom:3px solid #0b7285;padding-bottom:12px;margin-bottom:20px;">
    <h2 style="margin:0;color:#0b7285;">{subject}</h2>
    <p style="margin:4px 0 0;color:#486581;font-size:14px;">채널: {channel} &nbsp;|&nbsp; <a href="{url}" style="color:#0b7285;">{video_title}</a></p>
  </div>
  <div style="line-height:1.8;white-space:pre-wrap;">{summary}</div>
  <hr style="border:none;border-top:1px solid #d9e2ec;margin:24px 0;">
  <p style="font-size:12px;color:#829ab1;">이 메일은 YouTube-to-Email 자동 요약 시스템에서 발송되었습니다.</p>
</body>
</html>
"""


def send_email(to_email: str, subject: str, body_plain: str, body_html: Optional[str] = None) -> None:
    host = os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip()
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"

    if not host or not user or not password:
        raise ValueError("SMTP 환경변수가 설정되지 않았습니다.")

    if body_html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body_plain, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))
    else:
        msg = MIMEText(body_plain, _charset="utf-8")

    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_email

    with SMTP(host, port, timeout=30) as smtp:
        if use_tls:
            smtp.starttls()
        smtp.login(user, password)
        smtp.sendmail(user, [to_email], msg.as_string())


def build_and_send_summary_email(
    to_email: str,
    channel_title: str,
    video_title: str,
    video_url: str,
    summary: str,
) -> None:
    subject = f"[YouTube 요약] {channel_title} - {video_title}"
    body_plain = f"채널: {channel_title}\n제목: {video_title}\nURL: {video_url}\n\n{summary}"
    body_html = EMAIL_HTML_TEMPLATE.format(
        subject=subject,
        channel=channel_title,
        url=video_url,
        video_title=video_title,
        summary=summary.replace("\n", "<br>"),
    )
    send_email(to_email, subject, body_plain, body_html)
