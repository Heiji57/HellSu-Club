#!/usr/bin/env bash
# 백업 복원. 현재 DB 내용을 백업 시점으로 덮어쓴다.
# 사용: scripts/restore.sh backups/gym-YYYYMMDD-HHMMSS.sql.gz
set -euo pipefail
cd "$(dirname "$0")/.."

file="${1:?복원할 백업 파일을 지정하세요}"
[ -f "$file" ] || { echo "파일이 없습니다: $file" >&2; exit 1; }

read -r -p "현재 데이터베이스를 $file 내용으로 덮어씁니다. 계속할까요? [y/N] " answer
[ "$answer" = "y" ] || { echo "취소했습니다."; exit 1; }

docker compose stop bot 2>/dev/null || true
gunzip -c "$file" | docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
echo "복원 완료. 봇을 다시 시작하세요: docker compose --profile bot up -d"
