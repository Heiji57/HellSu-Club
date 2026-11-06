# 헬스클럽 출석 봇

Discord `/인증` 명령으로 운동을 인증하고 주간 출석을 자동으로 집계하는 봇입니다.

- 회원용 안내: [COMMANDS.md](COMMANDS.md)
- 요구사항: [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)
- 구조와 DB: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 구현 계획과 진행 상황: [docs/PLAN.md](docs/PLAN.md)
- 결정 기록: [docs/adr/](docs/adr/)

## 하는 일

- **인증:** 회원이 `/인증`으로 내용이나 사진을 올리면 바로 판정합니다.
  - 이전에 올라온 사진과 같거나 비슷하면 관리자 채널에 검토 요청을 올립니다.
  - 그 외에는 자동 승인하고 출석으로 셉니다. 하루 한 번만 인정합니다.
- **관리:** 관리자는 검토 요청의 버튼으로 승인·거절하고, `/관리` 명령으로 출석 취소, 목표 변경, 통계 확인을 합니다.
- **리포트:** 매주 월요일 09:00(KST) 이후 리포트 채널에 지난주 회원별 횟수와 달성 여부를 게시합니다.
- **정리:** 인증 사진은 판정 후 30일이 지나면 지웁니다. 중복 탐지용 해시는 계속 남습니다.

## Discord 준비

1. [개발자 포털](https://discord.com/developers/applications)에서 애플리케이션과 봇을 만들고 토큰을 발급합니다.
2. **Bot → Privileged Gateway Intents → Server Members Intent**를 켭니다. 역할 기반 회원 등록에 필요합니다.
3. OAuth2 URL Generator에서 `bot`, `applications.commands` scope로 서버에 초대합니다. 봇 권한:
   - 채널 보기, 메시지 보내기, 링크 첨부, 파일 첨부, 메시지 기록 보기
4. 서버에 다음을 만들고 각 ID를 복사합니다. ID를 복사하려면 Discord 설정에서 개발자 모드를 켜야 합니다.
   - 회원 역할, 관리자 역할
   - 관리자 전용 채널(검토 요청이 올라옴), 리포트 채널(공개)
   - (선택) 인증 전용 채널
5. **서버 설정 → 연동 → 봇 → `/관리`**에서 관리자 역할이 명령을 사용할 수 있게 허용합니다.
   `/관리`는 기본적으로 "서버 관리" 권한자에게만 보입니다. 실제 권한은 실행할 때마다 관리자 역할로 확인합니다.

## 설정

`.env.example`을 `.env`로 복사해 값을 채웁니다. 토큰과 비밀번호는 `.env`에만 두고 커밋하지 않습니다.

| 이름 | 설명 |
|---|---|
| `DISCORD_TOKEN` | 봇 토큰 |
| `GUILD_ID` | 서버 ID |
| `MEMBER_ROLE_ID`, `ADMIN_ROLE_ID` | 회원·관리자 역할 ID |
| `ADMIN_CHANNEL_ID`, `REPORT_CHANNEL_ID` | 관리자 채널, 리포트 채널 ID |
| `VERIFICATION_CHANNEL_ID` | 비워 두면 어느 채널에서나 `/인증` 가능 |
| `INITIAL_WEEKLY_TARGET` | 처음 시작할 때의 주간 목표(기본 4). 이후 변경은 `/관리 목표설정` |
| `POSTGRES_PASSWORD` | 운영에서는 반드시 바꿉니다 |

나머지 값(사진 용량, 중복 판정 기준, 보관 기간, 리포트 시각)은 `.env.example`의 설명을 참고하세요.

## 운영 서버에 배포 (Docker Compose)

```bash
cp .env.example .env                          # 값 채우기, POSTGRES_PASSWORD 변경
docker compose --profile bot up -d --build    # 시작 시 마이그레이션 자동 적용
docker compose logs -f bot                    # "길드 명령어 N개 동기화", "member reconcile" 확인
```

- DB와 사진은 Docker 볼륨(`pgdata`, `images`)에 저장됩니다. `docker compose down -v`는 데이터를 지우므로 쓰지 마세요.
- 두 컨테이너 모두 `restart: unless-stopped`라 서버가 재부팅되어도 다시 올라옵니다.
- 봇은 **하나만** 실행합니다. 리포트 중복 방지와 버튼 처리가 단일 프로세스를 전제로 합니다(ADR-0001).

### 업데이트

```bash
git pull
scripts/backup.sh                             # 업데이트 전 백업
docker compose --profile bot up -d --build
```

### 백업과 복원

```bash
scripts/backup.sh                             # backups/gym-날짜.sql.gz 생성, 14일 지난 백업 삭제
scripts/restore.sh backups/gym-20261106-040000.sql.gz   # 확인 후 덮어쓰기
```

매일 백업하려면 cron에 등록합니다: `0 4 * * * /경로/scripts/backup.sh >> /경로/backup.log 2>&1`

- 백업은 DB만 포함합니다. 사진은 30일만 보관하는 임시 자료라 백업하지 않습니다.
- 백업 파일은 다른 서버나 저장소에 복사해 두세요. 같은 디스크에만 있으면 디스크 장애에 대비할 수 없습니다.

### 상태 확인

- `/관리 시스템상태`: 봇 지연, DB, 검토 대기 수, 최근 리포트 게시 여부, 사진 저장 용량
- 리포트가 게시되지 않았다면 `/관리 주간리포트 게시:True`로 직접 게시할 수 있습니다. 5분마다 자동으로 재시도도 합니다.

## 로컬 개발

```bash
cp .env.example .env
docker compose up -d db       # PostgreSQL 17
uv sync
uv run alembic upgrade head
uv run python -m bot.main
```

## 테스트

```bash
docker compose up -d db
uv run pytest            # 통합 테스트는 TEST_DATABASE_URL(gym_test)을 자동 생성해 사용
uv run ruff check . && uv run ruff format --check .
```

통합 테스트는 실제 PostgreSQL에 대고 실행합니다. Discord에는 접속하지 않고, 명령 처리는 가짜 Interaction으로 확인합니다.
