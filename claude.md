# YouTube to Email Platform

영문 유튜브 채널의 신규 영상을 자동 감지하여 한국어 요약 보고서를 이메일로 발송하는 웹 플랫폼.

## 기술 스택

- **백엔드**: Python 3.9+, FastAPI, uvicorn
- **데이터베이스**: SQLite (WAL 모드)
- **템플릿**: Jinja2
- **외부 API**: OpenAI Chat Completions, YouTube RSS/Transcript
- **보안**: PBKDF2-SHA256 (비밀번호), Fernet AES-128 (API 키 암호화), CSRF 토큰

## 프로젝트 구조

```
src/
  core.py        — 공통 모듈 (DB, 암호화, YouTube, 요약, 이메일)
  web.py         — FastAPI 웹 플랫폼 + PipelineWorker
  main.py        — CLI 실행기
  templates/     — Jinja2 HTML 템플릿 (base, login, register, dashboard, settings)
data/
  app.db         — SQLite DB (자동 생성)
```

## 핵심 모듈 관계

- `core.py`는 순수 유틸리티 모듈로, web.py와 main.py 양쪽에서 import
- `web.py`의 `PipelineWorker`가 백그라운드 데몬 스레드로 주기적 스캔 수행
- DB 테이블: `app_users`, `user_channels`, `sent_items`

## 실행 방법

```bash
# 웹 서버
uvicorn src.web:app --reload

# CLI
python -m src.main add-channel --channel "@handle" --email user@example.com
python -m src.main run-once
```

## 환경변수 (.env)

필수: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ENCRYPT_KEY`
선택: `SESSION_SECRET`, `POLL_INTERVAL_MINUTES` (기본 15), `SMTP_USE_TLS` (기본 true)

## 코드 컨벤션

- 한국어 사용자 대상: UI 텍스트, 에러 메시지, 주석 모두 한국어
- DB 접근: `get_db()` 또는 `db_connection()` 컨텍스트 매니저 사용
- API 키는 반드시 `encrypt_value()` / `decrypt_value()`로 처리
- 모든 POST 엔드포인트에 CSRF 토큰 필수 (`csrf_token` 폼 필드)
- 시간: `now_iso()` → UTC ISO 8601 형식

## 파이프라인 흐름

YouTube RSS 피드 감지 → 영문 자막 수집 (youtube-transcript-api) → OpenAI Chat Completions 한국어 요약 → HTML+Plain Text SMTP 이메일 발송 → sent_items에 기록 (중복 방지)
