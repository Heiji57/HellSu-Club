# 헬스클럽 출석 봇

Discord `/인증` 명령으로 운동을 인증하고 주간 출석을 자동으로 집계하는 봇입니다.

- 요구사항: [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)
- 구조와 DB: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 구현 계획과 진행 상황: [docs/PLAN.md](docs/PLAN.md)
- 결정 기록: [docs/adr/](docs/adr/)

## Discord 준비

1. [개발자 포털](https://discord.com/developers/applications)에서 애플리케이션과 봇을 만들고 토큰을 발급합니다.
2. **Bot → Privileged Gateway Intents → Server Members Intent**를 켭니다. 역할 기반 회원 등록에 필요합니다.
3. OAuth2 URL Generator에서 `bot`, `applications.commands` scope로 서버에 초대합니다.
4. 서버에 회원 역할, 관리자 역할, 관리자 전용 채널, 리포트 채널을 만들고 각 ID를 복사합니다.
   ID를 복사하려면 Discord 설정에서 개발자 모드를 켜야 합니다.
5. **서버 설정 → 연동 → 봇 → `/관리`**에서 관리자 역할이 명령을 사용할 수 있게 허용합니다.
   `/관리`는 기본적으로 "서버 관리" 권한자에게만 보입니다.

## 로컬 실행

```bash
cp .env.example .env          # 값 채우기
docker compose up -d db       # PostgreSQL 17
uv sync
uv run alembic upgrade head
uv run python -m bot.main
```

## Docker로 실행

```bash
docker compose --profile bot up -d --build   # 시작 시 마이그레이션 자동 적용
docker compose logs -f bot
```

## 테스트

```bash
docker compose up -d db
uv run pytest            # 통합 테스트는 TEST_DATABASE_URL(gym_test)을 자동 생성해 사용
uv run ruff check . && uv run ruff format --check .
```
