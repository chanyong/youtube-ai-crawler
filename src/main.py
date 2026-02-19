"""
main.py — CLI 기반 채널 등록 / 1회 실행 / 데몬 모드
core.py의 공통 함수를 사용합니다.
"""

import argparse
import os
import time

from dotenv import load_dotenv

from .core import (
    build_and_send_summary_email,
    db_connection,
    decrypt_value,
    extract_channel_id,
    fetch_transcript,
    get_db,
    get_feed,
    init_db,
    now_iso,
    parse_video_id,
    summarize_korean,
)


def add_channel(channel_ref: str, email: str) -> None:
    channel_id = extract_channel_id(channel_ref)
    feed = get_feed(channel_id)
    title = feed.feed.get("title", channel_id)

    with db_connection() as con:
        con.execute(
            """
            INSERT INTO user_channels (user_id, channel_id, source, title, created_at)
            VALUES (0, ?, ?, ?, ?)
            ON CONFLICT(user_id, channel_id) DO UPDATE SET source=excluded.source, title=excluded.title
            """,
            (channel_id, channel_ref, title, now_iso()),
        )
    print(f"등록 완료: {title} ({channel_id}) -> {email}")


def run_once() -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        print("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        return

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT id, channel_id, source, title FROM user_channels WHERE user_id = 0 ORDER BY id DESC")
    channels = cur.fetchall()
    con.close()

    if not channels:
        print("등록된 채널이 없습니다. 먼저 add-channel을 실행하세요.")
        return

    total = 0
    for ch in channels:
        try:
            total += _process_channel(ch, api_key, model)
        except Exception as e:
            print(f"채널 처리 실패 ({ch['channel_id']}): {e}")
    print(f"완료: 이번 실행에서 {total}개 영상을 발송했습니다.")


def _process_channel(channel, api_key: str, model: str) -> int:
    feed = get_feed(channel["channel_id"])
    entries = feed.entries[:5]
    sent_count = 0

    for entry in reversed(entries):
        video_id = parse_video_id(entry)
        if not video_id:
            continue

        # 이미 발송 여부 확인
        con = get_db()
        cur = con.cursor()
        cur.execute("SELECT 1 FROM sent_items WHERE user_id = 0 AND video_id = ?", (video_id,))
        already = cur.fetchone()
        con.close()
        if already:
            continue

        title = entry.get("title", "(제목 없음)")
        url = entry.get("link", f"https://www.youtube.com/watch?v={video_id}")

        try:
            transcript = fetch_transcript(video_id)
            summary = summarize_korean(api_key, model, title, url, transcript)
        except Exception as e:
            summary = f"요약 생성 실패.\n오류: {e}\n\n제목: {title}\nURL: {url}"

        recipient = channel.get("recipient_email") or os.environ.get("RECIPIENT_EMAIL", "")
        if not recipient:
            print(f"수신 이메일 없음 — 건너뜀: {title}")
            continue

        ch_title = channel["title"] or channel["channel_id"]
        build_and_send_summary_email(recipient, ch_title, title, url, summary)

        with db_connection() as con:
            con.execute(
                "INSERT OR IGNORE INTO sent_items (user_id, channel_id, video_id, video_title, sent_at) VALUES (0, ?, ?, ?, ?)",
                (channel["channel_id"], video_id, title, now_iso()),
            )
        sent_count += 1
        print(f"발송 완료: {title}")

    return sent_count


def run_daemon(interval_min: int) -> None:
    print(f"데몬 시작: {interval_min}분 간격")
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"실행 중 오류: {e}")
        time.sleep(interval_min * 60)


def main() -> None:
    load_dotenv()
    init_db()

    parser = argparse.ArgumentParser(description="YouTube 신규 영상 한국어 이메일 요약기 (CLI)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add-channel", help="채널 등록/갱신")
    add.add_argument("--channel", required=True, help="채널 URL, @handle, 또는 UC... 채널 ID")
    add.add_argument("--email", required=True, help="수신 이메일")

    sub.add_parser("run-once", help="즉시 1회 실행")
    run = sub.add_parser("run", help="주기 실행(데몬)")
    run.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("POLL_INTERVAL_MINUTES", "15")),
        help="실행 주기(분)",
    )

    args = parser.parse_args()

    if args.cmd == "add-channel":
        add_channel(args.channel, args.email)
    elif args.cmd == "run-once":
        run_once()
    elif args.cmd == "run":
        run_daemon(args.interval)


if __name__ == "__main__":
    main()
