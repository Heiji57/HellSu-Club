"""인증 이미지 파일 저장소.

경로: `{IMAGE_DIR}/{yyyy}/{mm}/{verification_id}.{ext}` (KST 기준 제출 연월).
DB에는 IMAGE_DIR 기준 상대 경로를 저장해 볼륨 위치가 바뀌어도 그대로 쓸 수 있게 한다.
"""

import asyncio
import os
import tempfile
from datetime import datetime
from pathlib import Path

from app.core.week import to_kst


class ImageStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def relative_path(verification_id: int, submitted_at: datetime, ext: str) -> str:
        kst = to_kst(submitted_at)
        return f"{kst:%Y}/{kst:%m}/{verification_id}.{ext}"

    def absolute(self, relative: str) -> Path:
        root = self.root.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"저장소 밖의 경로: {relative}")
        return path

    async def save(self, relative: str, data: bytes) -> Path:
        return await asyncio.to_thread(self._write_atomic, self.absolute(relative), data)

    async def delete(self, relative: str) -> bool:
        return await asyncio.to_thread(self._unlink, self.absolute(relative))

    async def usage(self) -> tuple[int, int]:
        """(파일 수, 전체 바이트)."""
        return await asyncio.to_thread(self._usage)

    def _usage(self) -> tuple[int, int]:
        if not self.root.exists():
            return 0, 0
        files = [p for p in self.root.rglob("*") if p.is_file() and not p.name.startswith(".tmp-")]
        return len(files), sum(p.stat().st_size for p in files)

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> Path:
        # 임시 파일에 쓴 뒤 이름을 바꿔, 쓰다 만 파일이 남지 않게 한다.
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return path

    @staticmethod
    def _unlink(path: Path) -> bool:
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True
