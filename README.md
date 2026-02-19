<<<<<<< HEAD
# youtube-ai-crawler
=======
# YouTube Crawl Tracker

회원가입 기반 웹 플랫폼입니다.
각 회원이 자신의 유튜브 채널과 OpenAI API 키를 등록하면, 최근 스캔 에피소드를 기준으로 한국어 요약 내역을 생성하고 웹에서 바로 확인할 수 있습니다.

## 기능

- 회원가입 / 로그인 / 로그아웃
- 회원별 설정
  - OpenAI API 키 (Fernet 암호화 저장)
  - OpenAI 모델 선택
- 회원별 유튜브 채널 등록/삭제
- 대시보드
  - 채널 관리
  - 최근 생성내역 조회 (팝업 상세 보기)
  - 즉시 스캔 실행 버튼
- 요약 내역 생성
  - 최근 스캔 에피소드 기준
  - 영문 자막 수집
  - 한국어 요약 생성 (OpenAI Chat Completions)
  - DB 저장 후 웹 UI 팝업으로 조회
- CSRF 보호 (Double-Submit Cookie)
- SQLite WAL 모드 (동시 접근 안정성)
- CLI 모드 지원 (main.py)

## 1) 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 2) 환경변수

`.env` 파일을 편집하세요.
 
필수:
- `ENCRYPT_KEY` — API 키 암호화 키 (아래 명령으로 생성)

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

선택:
- `POLL_INTERVAL_MINUTES` (기본 `15`)
- `SESSION_SECRET` (세션 서명 키)

## 3) 실행 (웹 플랫폼)

```bash
uvicorn src.web:app --reload
```

브라우저에서 `http://127.0.0.1:8000` 접속 후:

1. 회원가입
2. 설정 페이지에서 OpenAI API 키 입력
3. 대시보드에서 유튜브 채널 등록
4. "지금 스캔 실행" 버튼으로 최근 에피소드 수집
5. "요약 내역 생성" 버튼으로 한국어 요약 생성
6. "최근 생성내역"에서 팝업으로 상세 읽기

## 4) 실행 (CLI 모드)

```bash
python -m src.main add-channel --channel "@Fireship" --email you@example.com
python -m src.main run-once
python -m src.main run --interval 30
```

## 프로젝트 구조

```
src/
  core.py        — 공통 모듈 (DB, 암호화, YouTube, 요약)
  web.py         — FastAPI 웹 플랫폼
  main.py        — CLI 실행기 (레거시 이메일 모드)
  templates/     — Jinja2 HTML 템플릿
data/
  app.db         — SQLite 데이터베이스
```

## 데이터 저장

- SQLite: `data/app.db`
- 테이블: `app_users`, `user_channels`, `scanned_items`, `generated_items`

## 운영 참고

- 회원별 OpenAI API 키는 Fernet 대칭 암호화로 저장됩니다.
- 대규모 운영 시 SQLite → PostgreSQL 전환을 권장합니다.
>>>>>>> 170e865 (Initial commit: YouTube Crawl Tracker)
