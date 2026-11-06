"""이미지 검증과 정규화.

- 형식은 첨부 메타데이터가 아니라 실제 바이트를 디코딩해서 판별한다.
- 방향(EXIF Orientation)을 반영한 뒤 EXIF 등 메타데이터를 모두 제거해 다시 인코딩한다.
  위치 정보가 들어 있는 사진이 서버에 남지 않게 하기 위해서다.
- SHA-256은 받은 원본 바이트로, pHash는 방향을 반영한 이미지로 계산한다.

CPU를 쓰는 동기 함수이므로 호출하는 쪽에서 `asyncio.to_thread`로 실행한다.
"""

import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.exceptions import ImageTooLarge, ImageUnreadable, UnsupportedImageFormat
from app.image.hashing import phash_hex, sha256_hex

# Pillow 형식 이름 → (MIME, 확장자)
SUPPORTED_FORMATS: dict[str, tuple[str, str]] = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}
MAX_PIXELS = 50_000_000  # 압축 폭탄 방지. 휴대폰 사진(약 1,200만~5,000만 화소)은 통과한다.
JPEG_QUALITY = 90
WEBP_QUALITY = 90


@dataclass(frozen=True)
class ProcessedImage:
    data: bytes  # 메타데이터를 제거한, 저장할 바이트
    mime: str
    ext: str
    width: int
    height: int
    original_bytes: int
    sha256: str
    phash: str


def process_image(raw: bytes) -> ProcessedImage:
    if not raw:
        raise ImageUnreadable()
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            img = Image.open(io.BytesIO(raw))
        except UnidentifiedImageError as e:
            raise UnsupportedImageFormat() from e
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
            raise ImageTooLarge("사진 해상도가 너무 큽니다.") from e

        fmt = img.format or ""
        if fmt not in SUPPORTED_FORMATS:
            raise UnsupportedImageFormat()
        if img.width * img.height > MAX_PIXELS:
            raise ImageTooLarge("사진 해상도가 너무 큽니다.")
        try:
            img.load()  # 잘리거나 손상된 파일은 여기서 실패한다
            img = ImageOps.exif_transpose(img)
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombWarning) as e:
            raise ImageUnreadable() from e

    mime, ext = SUPPORTED_FORMATS[fmt]
    return ProcessedImage(
        data=_encode_without_metadata(img, fmt),
        mime=mime,
        ext=ext,
        width=img.width,
        height=img.height,
        original_bytes=len(raw),
        sha256=sha256_hex(raw),
        phash=phash_hex(img),
    )


def _encode_without_metadata(img: Image.Image, fmt: str) -> bytes:
    img.info.clear()  # exif, xmp, 텍스트 청크 등이 저장 시 따라가지 않게 한다
    out = io.BytesIO()
    if fmt == "JPEG":
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(out, format="JPEG", quality=JPEG_QUALITY)
    elif fmt == "WEBP":
        img.save(out, format="WEBP", quality=WEBP_QUALITY)
    else:
        img.save(out, format="PNG")
    return out.getvalue()
