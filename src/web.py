"""
web.py — FastAPI 웹 플랫폼
회원가입 → 채널/키/이메일 등록 → 백그라운드 워커 자동 요약 발송
"""

import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware

from .core import (
    db_connection,
    decrypt_value,
    encrypt_value,
    extract_channel_id,
    fetch_transcript,
    get_db,
    get_feed,
    hash_password,
    init_db,
    now_iso,
    parse_video_id,
    summarize_korean,
    verify_password,
)

load_dotenv()

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


# ---------------------------------------------------------------------------
# CSRF helpers (simple double-submit cookie pattern)
# ---------------------------------------------------------------------------
def _get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_hex(32)
        request.session["csrf"] = token
    return token


def _verify_csrf(request: Request, token: str) -> bool:
    return secrets.compare_digest(request.session.get("csrf", ""), token or "")


def _parse_page(raw: Optional[str], default: int = 1) -> int:
    try:
        page = int(raw or default)
        return page if page > 0 else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def get_current_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM app_users WHERE id = ?", (uid,))
    row = cur.fetchone()
    con.close()
    return row


def require_user(request: Request):
    user = get_current_user(request)
    if not user:
        return None, RedirectResponse("/login", status_code=303)
    return user, None


def scan_recent_episodes_for_user(user_id: int, per_channel: int = 5, reset: bool = False) -> int:
    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT channel_id, title FROM user_channels WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    )
    channels = cur.fetchall()
    con.close()

    scanned = 0
    with db_connection() as con:
        if reset:
            con.execute("DELETE FROM scanned_items WHERE user_id = ?", (user_id,))
        for ch in channels:
            try:
                feed = get_feed(ch["channel_id"])
            except Exception as e:
                print(f"[scan] channel {ch['channel_id']} feed error: {e}")
                continue

            for entry in feed.entries[:per_channel]:
                video_id = parse_video_id(entry)
                if not video_id:
                    continue
                title = entry.get("title", "(제목 없음)")
                url = entry.get("link", f"https://www.youtube.com/watch?v={video_id}")
                published = entry.get("published", "") or entry.get("updated", "")
                con.execute(
                    """
                    INSERT INTO scanned_items (
                        user_id, channel_id, channel_title, video_id, video_title, video_url, published_at, scanned_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, video_id) DO UPDATE SET
                        channel_id=excluded.channel_id,
                        channel_title=excluded.channel_title,
                        video_title=excluded.video_title,
                        video_url=excluded.video_url,
                        published_at=excluded.published_at,
                        scanned_at=excluded.scanned_at
                    """,
                    (
                        user_id,
                        ch["channel_id"],
                        ch["title"] or ch["channel_id"],
                        video_id,
                        title,
                        url,
                        published,
                        now_iso(),
                    ),
                )
                scanned += 1
    return scanned


def generate_summaries_from_scanned(
    user_id: int,
    max_items: int = 20,
    selected_video_ids: Optional[list[str]] = None,
) -> tuple[int, int]:
    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, openai_api_key, openai_model, summary_prompt
        FROM app_users
        WHERE id = ?
        """,
        (user_id,),
    )
    user = cur.fetchone()
    if not user or not user["openai_api_key"]:
        con.close()
        return 0, 0

    try:
        api_key = decrypt_value(user["openai_api_key"])
    except Exception:
        api_key = user["openai_api_key"]

    if selected_video_ids:
        placeholders = ",".join("?" for _ in selected_video_ids)
        cur.execute(
            f"""
            SELECT s.channel_id, s.channel_title, s.video_id, s.video_title, s.video_url
            FROM scanned_items s
            WHERE s.user_id = ?
              AND s.video_id IN ({placeholders})
            ORDER BY s.scanned_at DESC, s.id DESC
            """,
            [user_id, *selected_video_ids],
        )
    else:
        cur.execute(
            """
            SELECT s.channel_id, s.channel_title, s.video_id, s.video_title, s.video_url
            FROM scanned_items s
            WHERE s.user_id = ?
            ORDER BY s.scanned_at DESC, s.id DESC
            LIMIT ?
            """,
            (user_id, max_items),
        )
    pending_rows = cur.fetchall()
    con.close()

    generated = 0
    failed = 0
    for row in reversed(pending_rows):
        video_id = row["video_id"]
        video_title = row["video_title"] or "(제목 없음)"
        video_url = row["video_url"] or f"https://www.youtube.com/watch?v={video_id}"
        channel_title = row["channel_title"] or row["channel_id"]

        status = "generated"
        error_message = None
        try:
            transcript = fetch_transcript(video_id)
            summary = summarize_korean(
                api_key=api_key,
                model=user["openai_model"] or "gpt-4o-mini",
                video_title=video_title,
                video_url=video_url,
                transcript=transcript,
                prompt=user["summary_prompt"] or "",
            )
        except Exception as e:
            status = "failed"
            error_message = str(e)[:1000]
            failed += 1
            summary = (
                "자막 번역 요약 생성에 실패했습니다.\n"
                f"오류: {e}\n\n"
                f"영상 제목: {video_title}\n"
                f"영상 링크: {video_url}"
            )

        with db_connection() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO generated_items (
                    user_id, channel_id, channel_title, video_id, video_title, video_url,
                    summary_ko, generation_status, error_message, generated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    row["channel_id"],
                    channel_title,
                    video_id,
                    video_title,
                    video_url,
                    summary,
                    status,
                    error_message,
                    now_iso(),
                ),
            )
        if status == "generated":
            generated += 1
    return generated, failed


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="YouTube Crawl Tracker", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", secrets.token_hex(32)),
)
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------
@app.get("/")
def home(request: Request):
    if get_current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/login", status_code=303)


@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse(
        "register.html", {"request": request, "error": None, "csrf": _get_csrf_token(request)}
    )


@app.post("/register")
def register_submit(
    request: Request,
    account_email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    csrf_token: str = Form(""),
):
    if not _verify_csrf(request, csrf_token):
        return templates.TemplateResponse(
            "register.html", {"request": request, "error": "잘못된 요청입니다.", "csrf": _get_csrf_token(request)}, status_code=400
        )

    if password != password_confirm:
        return templates.TemplateResponse(
            "register.html", {"request": request, "error": "비밀번호 확인이 일치하지 않습니다.", "csrf": _get_csrf_token(request)}, status_code=400
        )
    if len(password) < 8:
        return templates.TemplateResponse(
            "register.html", {"request": request, "error": "비밀번호는 8자 이상이어야 합니다.", "csrf": _get_csrf_token(request)}, status_code=400
        )

    try:
        with db_connection() as con:
            cur = con.cursor()
            cur.execute(
                "INSERT INTO app_users (account_email, password_hash, recipient_email, created_at) VALUES (?, ?, ?, ?)",
                (account_email.strip().lower(), hash_password(password), account_email.strip().lower(), now_iso()),
            )
            user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        return templates.TemplateResponse(
            "register.html", {"request": request, "error": "이미 등록된 계정 이메일입니다.", "csrf": _get_csrf_token(request)}, status_code=400
        )

    request.session["uid"] = user_id
    return RedirectResponse("/dashboard?msg=registered", status_code=303)


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": None, "csrf": _get_csrf_token(request)}
    )


@app.post("/login")
def login_submit(request: Request, account_email: str = Form(...), password: str = Form(...), csrf_token: str = Form("")):
    if not _verify_csrf(request, csrf_token):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "잘못된 요청입니다.", "csrf": _get_csrf_token(request)}, status_code=400
        )

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM app_users WHERE account_email = ?", (account_email.strip().lower(),))
    user = cur.fetchone()
    con.close()

    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "이메일 또는 비밀번호가 올바르지 않습니다.", "csrf": _get_csrf_token(request)}, status_code=400
        )
    request.session["uid"] = user["id"]
    return RedirectResponse("/dashboard?msg=logged-in", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login?msg=logged-out", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Dashboard (채널 관리 + 생성 이력 + 즉시 실행)
# ---------------------------------------------------------------------------
@app.get("/dashboard")
def dashboard(request: Request):
    user, redirect = require_user(request)
    if redirect:
        return redirect
    page_size = 10
    generated_page = _parse_page(request.query_params.get("generated_page"), 1)
    scanned_page = _parse_page(request.query_params.get("scanned_page"), 1)

    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT id, channel_id, source, title, created_at FROM user_channels WHERE user_id = ? ORDER BY id DESC",
        (user["id"],),
    )
    channels = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS cnt FROM generated_items WHERE user_id = ?", (user["id"],))
    generated_total = int(cur.fetchone()["cnt"])
    generated_total_pages = max(1, (generated_total + page_size - 1) // page_size)
    generated_page = min(generated_page, generated_total_pages)
    generated_offset = (generated_page - 1) * page_size
    cur.execute(
        """
        SELECT video_id, video_title, video_url, channel_id, channel_title, summary_ko,
               error_message, generated_at
        FROM generated_items
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (user["id"], page_size, generated_offset),
    )
    generated_items = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS cnt FROM scanned_items WHERE user_id = ?", (user["id"],))
    scanned_total = int(cur.fetchone()["cnt"])
    scanned_total_pages = max(1, (scanned_total + page_size - 1) // page_size)
    scanned_page = min(scanned_page, scanned_total_pages)
    scanned_offset = (scanned_page - 1) * page_size
    cur.execute(
        """
        SELECT id, video_id, video_title, video_url, channel_id, channel_title, published_at, scanned_at
        FROM scanned_items
        WHERE user_id = ?
        ORDER BY scanned_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (user["id"], page_size, scanned_offset),
    )
    scanned_items = cur.fetchall()

    con.close()

    msg = request.query_params.get("msg")
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "channels": channels,
            "generated_items": generated_items,
            "scanned_items": scanned_items,
            "generated_page": generated_page,
            "generated_total_pages": generated_total_pages,
            "scanned_page": scanned_page,
            "scanned_total_pages": scanned_total_pages,
            "msg": msg,
            "csrf": _get_csrf_token(request),
        },
    )


@app.post("/channels/add")
def add_channel(request: Request, source: str = Form(...), csrf_token: str = Form("")):
    user, redirect = require_user(request)
    if redirect:
        return redirect
    if not _verify_csrf(request, csrf_token):
        return RedirectResponse("/dashboard?msg=csrf-error", status_code=303)

    try:
        channel_id = extract_channel_id(source)
        feed = get_feed(channel_id)
        title = feed.feed.get("title", channel_id)
    except Exception as e:
        return RedirectResponse(f"/dashboard?msg=add-failed:{e}", status_code=303)

    with db_connection() as con:
        con.execute(
            """
            INSERT INTO user_channels (user_id, channel_id, source, title, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, channel_id) DO UPDATE SET source=excluded.source, title=excluded.title
            """,
            (user["id"], channel_id, source.strip(), title, now_iso()),
        )
    return RedirectResponse("/dashboard?msg=channel-added", status_code=303)


@app.post("/channels/delete")
def delete_channel(request: Request, channel_pk: int = Form(...), csrf_token: str = Form("")):
    user, redirect = require_user(request)
    if redirect:
        return redirect
    if not _verify_csrf(request, csrf_token):
        return RedirectResponse("/dashboard?msg=csrf-error", status_code=303)

    with db_connection() as con:
        con.execute("DELETE FROM user_channels WHERE id = ? AND user_id = ?", (channel_pk, user["id"]))
    return RedirectResponse("/dashboard?msg=channel-deleted", status_code=303)


@app.post("/generated/delete")
def delete_generated_item(
    request: Request,
    video_id: str = Form(...),
    generated_page: str = Form("1"),
    scanned_page: str = Form("1"),
    csrf_token: str = Form(""),
):
    user, redirect = require_user(request)
    if redirect:
        return redirect
    if not _verify_csrf(request, csrf_token):
        return RedirectResponse("/dashboard?msg=csrf-error", status_code=303)

    gp = _parse_page(generated_page, 1)
    sp = _parse_page(scanned_page, 1)
    with db_connection() as con:
        con.execute("DELETE FROM generated_items WHERE user_id = ? AND video_id = ?", (user["id"], video_id))
    return RedirectResponse(
        f"/dashboard?msg=generated-deleted&generated_page={gp}&scanned_page={sp}",
        status_code=303,
    )


@app.post("/run-now")
def run_now(request: Request, csrf_token: str = Form("")):
    """즉시 실행 — 현재 사용자의 채널 최근 에피소드를 스캔해 목록 갱신."""
    user, redirect = require_user(request)
    if redirect:
        return redirect
    if not _verify_csrf(request, csrf_token):
        return RedirectResponse("/dashboard?msg=csrf-error", status_code=303)

    scanned_count = scan_recent_episodes_for_user(user["id"], per_channel=5, reset=True)
    return RedirectResponse(f"/dashboard?msg=run-done:{scanned_count}&generated_page=1&scanned_page=1", status_code=303)


@app.post("/generate-summaries")
def generate_summaries(
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(""),
    selected_video_ids: Optional[list[str]] = Form(None),
    generated_page: str = Form("1"),
    scanned_page: str = Form("1"),
):
    """최근 스캔 에피소드를 기준으로 한국어 요약 내역을 백그라운드에서 생성."""
    user, redirect = require_user(request)
    if redirect:
        return redirect
    if not _verify_csrf(request, csrf_token):
        return RedirectResponse("/dashboard?msg=csrf-error", status_code=303)

    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT openai_api_key FROM app_users WHERE id = ?",
        (user["id"],),
    )
    row = cur.fetchone()
    con.close()
    gp = _parse_page(generated_page, 1)
    sp = _parse_page(scanned_page, 1)
    base_qs = f"generated_page={gp}&scanned_page={sp}"
    if not row or not row["openai_api_key"]:
        return RedirectResponse(f"/dashboard?msg=summary-generate-config-missing&{base_qs}", status_code=303)

    picked = [v.strip() for v in (selected_video_ids or []) if v and v.strip()]
    if not picked:
        return RedirectResponse(f"/dashboard?msg=summary-generate-no-selection&{base_qs}", status_code=303)

    # 즉시 응답 후 백그라운드에서 처리 — Railway 프록시 타임아웃 방지
    background_tasks.add_task(
        generate_summaries_from_scanned,
        user["id"],
        20,
        picked,
    )
    return RedirectResponse(f"/dashboard?msg=summary-generate-started:{len(picked)}&{base_qs}", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Settings
# ---------------------------------------------------------------------------
@app.get("/settings")
def settings_page(request: Request):
    user, redirect = require_user(request)
    if redirect:
        return redirect
    msg = request.query_params.get("msg")
    return templates.TemplateResponse(
        "settings.html", {"request": request, "user": user, "msg": msg, "csrf": _get_csrf_token(request)}
    )


@app.post("/settings")
def settings_submit(
    request: Request,
    openai_api_key: str = Form(""),
    openai_model: str = Form("gpt-4o-mini"),
    summary_prompt: str = Form(""),
    csrf_token: str = Form(""),
):
    user, redirect = require_user(request)
    if redirect:
        return redirect
    if not _verify_csrf(request, csrf_token):
        return RedirectResponse("/settings?msg=csrf-error", status_code=303)

    with db_connection() as con:
        if openai_api_key.strip():
            try:
                encrypted_key = encrypt_value(openai_api_key.strip())
            except ValueError as e:
                msg = str(e)
                if "ENCRYPT_KEY" in msg:
                    return RedirectResponse("/settings?msg=encrypt-key-missing", status_code=303)
                return RedirectResponse("/settings?msg=save-failed", status_code=303)
            con.execute(
                "UPDATE app_users SET openai_api_key = ?, openai_model = ?, summary_prompt = ? WHERE id = ?",
                (
                    encrypted_key,
                    openai_model.strip() or "gpt-4o-mini",
                    summary_prompt.strip(),
                    user["id"],
                ),
            )
        else:
            con.execute(
                "UPDATE app_users SET openai_model = ?, summary_prompt = ? WHERE id = ?",
                (
                    openai_model.strip() or "gpt-4o-mini",
                    summary_prompt.strip(),
                    user["id"],
                ),
            )
    return RedirectResponse("/settings?msg=saved", status_code=303)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "time": now_iso()}
