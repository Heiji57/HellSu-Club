"""테스트용 이미지 생성. 같은 seed는 같은 그림을 만든다."""

import io
import random

from PIL import Image, ImageDraw

GPS_IFD = 0x8825
MAKE = 0x010F
ORIENTATION = 0x0112


def scene(seed: int, size: tuple[int, int] = (800, 600)) -> Image.Image:
    """도형 몇 개로 된 그림. 무작위 노이즈와 달리 리사이즈해도 pHash가 안정적이다."""
    rng = random.Random(seed)
    img = Image.new("RGB", size, tuple(rng.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    for _ in range(12):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        w, h = rng.randrange(50, 300), rng.randrange(50, 300)
        draw.ellipse([x, y, x + w, y + h], fill=tuple(rng.randrange(256) for _ in range(3)))
    return img


def encode(img: Image.Image, fmt: str = "JPEG", **kwargs) -> bytes:
    buf = io.BytesIO()
    if fmt == "JPEG" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def photo(seed: int = 1, fmt: str = "JPEG", **kwargs) -> bytes:
    return encode(scene(seed), fmt, **kwargs)


def exif_with_location(orientation: int = 1) -> bytes:
    exif = Image.Exif()
    exif[MAKE] = "TestCam"
    exif[ORIENTATION] = orientation
    exif.get_ifd(GPS_IFD)[2] = (37.0, 33.0, 0.0)  # GPSLatitude
    return exif.tobytes()
