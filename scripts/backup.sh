#!/usr/bin/env bash
# PostgreSQL 백업: docker compose의 db 컨테이너에서 pg_dump → gzip.
# 사용: scripts/backup.sh            (cron 예: 0 4 * * * /path/to/scripts/backup.sh)
# 환경변수: BACKUP_DIR(기본 ./backups), BACKUP_KEEP_DAYS(기본 14)
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
mkdir -p "$BACKUP_DIR"

file="$BACKUP_DIR/gym-$(date +%Y%m%d-%H%M%S).sql.gz"
# 쓰다 만 파일이 백업처럼 남지 않도록 임시 이름으로 받은 뒤 바꾼다.
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --clean --if-exists' \
  | gzip > "$file.tmp"
mv "$file.tmp" "$file"

find "$BACKUP_DIR" -name 'gym-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "백업 완료: $file ($(du -h "$file" | cut -f1))"
