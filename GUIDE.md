# YouTube to Email Platform — 상세 설명 및 사용 가이드

## 1. 프로그램 개요

YouTube to Email Platform은 영문 유튜브 채널의 신규 영상을 자동으로 감지하고, 영문 자막을 수집한 뒤 OpenAI를 활용하여 한국어 요약 보고서를 생성하고, 이를 이메일로 자동 발송하는 웹 기반 플랫폼입니다.

회원별로 독립된 환경을 제공하여, 각 사용자가 자신의 유튜브 채널 목록, OpenAI API 키, 수신 이메일을 개별적으로 관리할 수 있습니다. 웹 UI와 CLI 두 가지 실행 모드를 모두 지원합니다.


## 2. 전체 동작 흐름

```
[사용자]
   │
   ├─ 1. 회원가입 / 로그인
   ├─ 2. 설정: 수신 이메일 + OpenAI API 키 입력
   └─ 3. 대시보드: 유튜브 채널 등록
                │
        ┌───────┴───────┐
        │  백그라운드 워커  │  (15분 간격 자동 실행 또는 즉시 실행)
        └───────┬───────┘
                │
                ├─ 4. YouTube RSS 피드에서 신규 영상 감지
                ├─ 5. 영문 자막(Transcript) 수집
                ├─ 6. OpenAI Chat Completions API로 한국어 요약 생성
                └─ 7. HTML 이메일 자동 발송
```


## 3. 주요 기능

### 3.1 회원 시스템
사용자는 이메일과 비밀번호로 회원가입하며, 비밀번호는 PBKDF2-SHA256 해시로 안전하게 저장됩니다. 로그인 후 세션 기반으로 인증 상태가 유지되며, 모든 POST 요청에는 CSRF 토큰이 적용되어 있습니다.

### 3.2 회원별 설정 관리
설정 페이지에서 다음 항목을 등록합니다:
- **수신 이메일**: 요약 보고서를 받을 이메일 주소
- **OpenAI API 키**: 요약 생성에 사용되는 개인 API 키 (Fernet 대칭 암호화로 DB에 저장)
- **OpenAI 모델**: gpt-4o-mini, gpt-4o, gpt-4.1-mini, gpt-4.1 중 선택

### 3.3 유튜브 채널 관리
대시보드에서 유튜브 채널을 등록/삭제합니다. 채널 입력은 세 가지 형식을 지원합니다:
- 채널 URL: `https://www.youtube.com/@Fireship`
- @handle: `@Fireship`
- 채널 ID: `UCsBjURrPoezykLs9EqgamOA`

### 3.4 자동 요약 파이프라인
백그라운드 워커가 설정된 주기(기본 15분)마다 모든 사용자의 등록 채널을 스캔합니다. 신규 영상이 감지되면 영문 자막을 수집하고, OpenAI를 통해 한국어 보고서를 생성합니다.

요약 보고서 형식:
1. 한 줄 요약
2. 핵심 포인트 5개
3. 실무 적용 아이디어 3개
4. 주요 용어 5개 (영문 + 한국어 설명)

### 3.5 이메일 발송
생성된 요약은 HTML과 Plain Text 두 가지 형식으로 이메일 발송됩니다. HTML 이메일에는 채널명, 영상 제목, 링크가 깔끔하게 포맷되어 들어갑니다.

### 3.6 발송 이력 및 즉시 실행
대시보드에서 최근 발송 이력(최대 30건)을 확인할 수 있으며, "지금 스캔 실행" 버튼으로 워커 주기를 기다리지 않고 즉시 스캔을 실행할 수 있습니다.


## 4. 프로젝트 구조

```
youtube_to_email/
├── .env.example            # 환경변수 템플릿
├── .env                    # 실제 환경변수 (직접 생성)
├── requirements.txt        # Python 의존성 목록
├── README.md               # 간략 README
├── GUIDE.md                # 이 문서 (상세 가이드)
├── data/
│   └── app.db              # SQLite 데이터베이스 (자동 생성)
└── src/
    ├── __init__.py          # 패키지 초기화
    ├── core.py              # 공통 모듈 (DB, 암호화, YouTube, 요약, 이메일)
    ├── web.py               # FastAPI 웹 플랫폼
    ├── main.py              # CLI 실행기
    └── templates/
        ├── base.html        # 기본 레이아웃
        ├── login.html       # 로그인 페이지
        ├── register.html    # 회원가입 페이지
        ├── dashboard.html   # 대시보드 (채널 관리 + 발송 이력)
        └── settings.html    # 설정 페이지
```

### 모듈별 역할

| 파일 | 역할 |
|------|------|
| `core.py` | DB 관리(SQLite WAL), 비밀번호 해싱, API 키 암호화/복호화, YouTube 채널 파싱, 자막 수집, OpenAI 요약, 이메일 발송 등 모든 공통 로직 |
| `web.py` | FastAPI 기반 웹 서버. 회원가입/로그인, 대시보드, 설정, 채널 CRUD, 즉시 실행 API, 백그라운드 워커(PipelineWorker) |
| `main.py` | 터미널에서 직접 실행하는 CLI 도구. 채널 등록, 1회 실행, 데몬 모드 |


## 5. 설치 및 설정

### 5.1 사전 요구사항
- Python 3.9 이상
- Gmail 앱 비밀번호 (또는 다른 SMTP 서버 정보)
- OpenAI API 키 (각 사용자가 개별 보유)

### 5.2 설치

```bash
# 1. 프로젝트 디렉토리 이동
cd youtube_to_email

# 2. 가상환경 생성 및 활성화
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

# 3. 의존성 설치
pip install -r requirements.txt

# 4. 환경변수 파일 생성
cp .env.example .env
```

### 5.3 환경변수 설정

`.env` 파일을 텍스트 편집기로 열고 아래 항목을 수정합니다.

#### 필수 항목

| 변수명 | 설명 | 예시 |
|--------|------|------|
| `SMTP_HOST` | SMTP 서버 주소 | `smtp.gmail.com` |
| `SMTP_PORT` | SMTP 포트 | `587` |
| `SMTP_USER` | 발신 이메일 계정 | `yourname@gmail.com` |
| `SMTP_PASSWORD` | 이메일 앱 비밀번호 | `abcd efgh ijkl mnop` |
| `ENCRYPT_KEY` | API 키 암호화 키 | (아래 명령으로 생성) |

ENCRYPT_KEY 생성 방법:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

#### 선택 항목

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `SMTP_USE_TLS` | `true` | TLS 사용 여부 |
| `POLL_INTERVAL_MINUTES` | `15` | 백그라운드 스캔 주기 (분) |
| `SESSION_SECRET` | (자동 생성) | 세션 서명 키 |

#### Gmail 앱 비밀번호 발급 방법
1. Google 계정 > 보안 > 2단계 인증 활성화
2. Google 계정 > 보안 > 앱 비밀번호 생성
3. 생성된 16자리 비밀번호를 `SMTP_PASSWORD`에 입력


## 6. 사용법

### 6.1 웹 플랫폼 모드 (권장)

#### 서버 시작
```bash
uvicorn src.web:app --reload
```

서버가 시작되면 브라우저에서 `http://127.0.0.1:8000`에 접속합니다.

#### 사용 순서

**1단계 — 회원가입**

`/register` 페이지에서 이메일과 비밀번호(8자 이상)를 입력하여 가입합니다.

**2단계 — 설정**

`/settings` 페이지에서 다음을 입력합니다:
- 수신 이메일: 요약을 받을 이메일 주소
- OpenAI API 키: `sk-`로 시작하는 개인 API 키
- OpenAI 모델: 드롭다운에서 선택

**3단계 — 채널 등록**

`/dashboard` 페이지에서 유튜브 채널을 등록합니다. 입력 형식은 다음 중 하나를 사용합니다:
- `https://www.youtube.com/@Fireship`
- `@Fireship`
- `UCsBjURrPoezykLs9EqgamOA`

**4단계 — 요약 수신**

두 가지 방법으로 요약을 받을 수 있습니다:
- **자동**: 백그라운드 워커가 설정된 주기마다 자동 스캔
- **수동**: 대시보드의 "지금 스캔 실행" 버튼 클릭

### 6.2 CLI 모드

CLI 모드는 웹 서버 없이 터미널에서 직접 실행하는 방식입니다. `.env`에 OpenAI API 키와 수신 이메일도 추가로 설정해야 합니다.

```bash
# .env에 추가 설정
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4o-mini
RECIPIENT_EMAIL=you@example.com
```

#### 명령어

```bash
# 채널 등록
python -m src.main add-channel --channel "@Fireship" --email you@example.com

# 즉시 1회 실행 (등록된 모든 채널 스캔)
python -m src.main run-once

# 데몬 모드 (30분 간격으로 반복 실행)
python -m src.main run --interval 30
```


## 7. 데이터베이스 구조

SQLite 데이터베이스(`data/app.db`)에 세 개의 테이블이 사용됩니다.

### app_users (사용자)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER | 기본 키 (자동 증가) |
| account_email | TEXT | 로그인 이메일 (고유) |
| password_hash | TEXT | PBKDF2 해시된 비밀번호 |
| recipient_email | TEXT | 요약 수신 이메일 |
| openai_api_key | TEXT | Fernet 암호화된 API 키 |
| openai_model | TEXT | 사용 모델명 (기본: gpt-4o-mini) |
| created_at | TEXT | 가입 일시 (ISO 8601) |

### user_channels (등록 채널)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER | 기본 키 |
| user_id | INTEGER | 소유 사용자 ID |
| channel_id | TEXT | YouTube 채널 ID (UC...) |
| source | TEXT | 사용자 원본 입력값 |
| title | TEXT | 채널명 |
| created_at | TEXT | 등록 일시 |

### sent_items (발송 이력)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER | 기본 키 |
| user_id | INTEGER | 사용자 ID |
| channel_id | TEXT | 채널 ID |
| video_id | TEXT | 영상 ID |
| video_title | TEXT | 영상 제목 |
| sent_at | TEXT | 발송 일시 |


## 8. 보안 설계

### 비밀번호
PBKDF2-SHA256 알고리즘(240,000 반복)으로 해싱하고, 16바이트 랜덤 솔트를 사용합니다. 검증 시에는 `secrets.compare_digest`로 타이밍 공격을 방어합니다.

### API 키 암호화
OpenAI API 키는 Fernet 대칭 암호화(AES-128-CBC + HMAC-SHA256)로 암호화된 상태로 DB에 저장됩니다. 복호화는 워커가 요약 생성 시점에만 수행합니다.

### CSRF 보호
모든 POST 폼에 Double-Submit Cookie 방식의 CSRF 토큰이 포함됩니다. 세션에 저장된 토큰과 폼 제출 토큰을 `secrets.compare_digest`로 비교합니다.

### SQLite WAL 모드
Write-Ahead Logging 모드를 활성화하여, 웹 요청 스레드와 백그라운드 워커의 동시 접근 시 `database is locked` 오류를 방지합니다.


## 9. 이메일 보고서 예시

발송되는 이메일의 제목과 본문 형식은 다음과 같습니다.

**제목**: `[YouTube 요약] Fireship - 10 CSS Pro Tips`

**본문**:
```
채널: Fireship
제목: 10 CSS Pro Tips
URL: https://www.youtube.com/watch?v=...

1) 한 줄 요약
CSS의 생산성과 유지보수성을 높이는 10가지 핵심 기법을 소개하는 영상입니다.

2) 핵심 포인트 5개
- CSS 변수(Custom Properties)로 테마 관리를 체계화할 수 있다
- ...

3) 실무 적용 아이디어 3개
- 현재 프로젝트의 색상 시스템을 CSS 변수로 리팩터링
- ...

4) 주요 용어 5개
- Custom Properties: CSS에서 재사용 가능한 변수를 정의하는 기능
- ...
```

HTML 이메일은 상단에 채널명과 영상 링크가 포맷팅되어 표시됩니다.


## 10. 의존성 목록

| 패키지 | 버전 | 용도 |
|--------|------|------|
| cryptography | >= 42.0 | API 키 Fernet 암호화 |
| feedparser | 6.0.11 | YouTube RSS 피드 파싱 |
| fastapi | 0.115.8 | 웹 프레임워크 |
| jinja2 | 3.1.5 | HTML 템플릿 렌더링 |
| openai | 1.68.2 | OpenAI Chat Completions API |
| python-dotenv | 1.0.1 | .env 환경변수 로드 |
| python-multipart | 0.0.20 | 폼 데이터 파싱 |
| requests | 2.32.3 | HTTP 요청 (채널 ID 확인) |
| uvicorn | 0.34.0 | ASGI 서버 |
| youtube-transcript-api | 0.6.2 | YouTube 자막 수집 |


## 11. 문제 해결 (FAQ)

**Q. "ENCRYPT_KEY 환경변수가 설정되지 않았습니다" 오류**
A. `.env` 파일에 `ENCRYPT_KEY` 값을 설정하세요. 생성 명령:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Q. 이메일이 발송되지 않습니다**
A. 다음을 확인하세요:
- `.env`의 SMTP 설정이 올바른지 확인
- Gmail 사용 시 2단계 인증 + 앱 비밀번호가 필요합니다
- 설정 페이지에서 수신 이메일과 OpenAI API 키가 모두 등록되어 있는지 확인

**Q. "영문 자막을 찾을 수 없습니다" 오류**
A. 해당 영상에 영문 자막(수동 또는 자동 생성)이 없는 경우 발생합니다. 자막이 없는 영상은 기본 정보만 포함된 알림 이메일이 발송됩니다.

**Q. 채널 등록 시 "채널 ID를 찾을 수 없습니다" 오류**
A. 입력한 URL이나 핸들이 유효한지 확인하세요. YouTube 페이지에 직접 접속하여 채널이 존재하는지 확인한 후, 채널 URL을 그대로 복사하여 입력하세요.

**Q. 이미 발송된 영상이 다시 발송됩니다**
A. `data/app.db`의 `sent_items` 테이블에서 중복 방지를 관리합니다. DB 파일을 삭제하면 이력이 초기화되므로, 이미 발송된 영상이 다시 발송될 수 있습니다.

**Q. 서버 재시작 시 기존 데이터가 유지되나요?**
A. 네. 모든 데이터는 `data/app.db` SQLite 파일에 영구 저장됩니다. 서버를 재시작해도 회원 정보, 채널 목록, 발송 이력이 모두 유지됩니다.


## 12. Railway 배포 시 데이터 영구 보존 설정 (필수)

Railway는 배포할 때마다 컨테이너를 새로 생성하므로, **별도 설정 없이는 SQLite DB가 배포 시마다 초기화**됩니다. 아래 절차대로 Volume을 설정해야 회원 정보·채널·API 키가 유지됩니다.

### 12.1 Railway Volume 생성 및 마운트

1. Railway 프로젝트 대시보드에서 **서비스(Service)** 선택
2. 상단 탭 중 **Volumes** 클릭 → **Add Volume**
3. 아래와 같이 설정:

   | 항목 | 값 |
   |------|-----|
   | Mount Path | `/data` |
   | Volume 이름 | `youtube-ai-data` (자유) |

4. **Deploy** 또는 **Save** 클릭

### 12.2 환경변수 추가

Railway 서비스 > **Variables** 탭에서 아래 변수 추가:

| 변수명 | 값 |
|--------|-----|
| `DB_PATH` | `/data/app.db` |

> `ENCRYPT_KEY`, `SESSION_SECRET`도 동일한 값으로 유지되어야 기존 암호화된 API 키를 복호화할 수 있습니다. 변수 값을 변경하지 마세요.

### 12.3 확인 방법

Railway 대시보드 > 서비스 > **Deploy Logs**에서 서버 시작 시 아래 경로로 DB가 생성되는지 확인:
```
INFO: DB 경로: /data/app.db
```

설정이 완료되면 이후 재배포 시에도 `/data` 볼륨의 데이터는 유지됩니다.


## 13. 운영 시 참고사항

- **스캔 주기 조절**: `.env`의 `POLL_INTERVAL_MINUTES`를 변경하여 워커 스캔 주기를 조절할 수 있습니다. 채널이 많을 경우 30분 이상을 권장합니다.
- **대규모 운영**: 사용자가 많아지면 SQLite의 동시성 한계가 나타날 수 있습니다. PostgreSQL 등으로 전환을 고려하세요.
- **SMTP 발신 한도**: Gmail은 하루 500건(일반 계정) 발송 제한이 있습니다. 대량 발송이 필요하면 SendGrid, Mailgun 등 전문 서비스를 권장합니다.
- **자막 길이**: 현재 자막의 앞 12,000자만 요약에 사용됩니다. 매우 긴 강의 영상은 후반부 내용이 누락될 수 있습니다.
- **API 비용**: OpenAI API 사용 요금은 각 사용자 본인의 API 키로 청구됩니다. gpt-4o-mini는 비용 효율이 좋고, gpt-4o는 더 높은 품질의 요약을 생성합니다.
