"""이미지 해시.

pHash는 imagehash.phash와 같은 알고리즘을 numpy로 구현했다. imagehash는 DCT 한 번을 위해
scipy와 PyWavelets(약 150MB)를 끌고 오기 때문에 런타임 의존성에서 뺐다.
결과가 imagehash와 비트 단위로 같은지는 tests/unit/test_image.py에서 확인한다.
"""

import hashlib
from functools import cache

import numpy as np
from PIL import Image

HASH_SIZE = 8  # 8x8 = 64비트
HIGHFREQ_FACTOR = 4  # 32x32로 줄인 뒤 DCT
PHASH_HEX_LENGTH = HASH_SIZE * HASH_SIZE // 4


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@cache
def _dct_matrix(n: int) -> np.ndarray:
    """정규화하지 않은 DCT-II 행렬 (scipy.fftpack.dct 기본값과 같다)."""
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    return 2.0 * np.cos(np.pi * k * (2 * i + 1) / (2 * n))


def phash_hex(image: Image.Image) -> str:
    """64비트 perceptual hash를 16자리 소문자 hex로."""
    size = HASH_SIZE * HIGHFREQ_FACTOR
    gray = image.convert("L").resize((size, size), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.float64)
    m = _dct_matrix(size)
    dct = m @ pixels @ m.T  # 열 방향, 행 방향 순으로 1차원 DCT
    low = dct[:HASH_SIZE, :HASH_SIZE]
    bits = (low > np.median(low)).flatten()
    value = int("".join("1" if b else "0" for b in bits), 2)
    return f"{value:0{PHASH_HEX_LENGTH}x}"


def hamming_distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()
