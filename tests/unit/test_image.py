import io
from datetime import UTC, datetime

import pytest
from aiohttp import web
from PIL import Image

from app.core.exceptions import ImageTooLarge, ImageUnreadable, UnsupportedImageFormat
from app.image.fetcher import HttpImageFetcher
from app.image.hashing import hamming_distance, phash_hex, sha256_hex
from app.image.processor import MAX_PIXELS, process_image
from app.image.storage import ImageStorage
from tests.images import encode, exif_with_location, photo, scene


class TestProcessImage:
    @pytest.mark.parametrize(
        ("fmt", "mime", "ext"),
        [
            ("JPEG", "image/jpeg", "jpg"),
            ("PNG", "image/png", "png"),
            ("WEBP", "image/webp", "webp"),
        ],
    )
    def test_supported_formats(self, fmt, mime, ext):
        raw = photo(fmt=fmt)
        result = process_image(raw)
        assert (result.mime, result.ext) == (mime, ext)
        assert result.original_bytes == len(raw)
        assert result.sha256 == sha256_hex(raw)
        assert len(result.phash) == 16
        assert Image.open(io.BytesIO(result.data)).format == fmt

    def test_format_is_detected_from_bytes_not_name(self):
        # PNG 바이트는 이름과 상관없이 PNG로 판별한다
        assert process_image(photo(fmt="PNG")).mime == "image/png"

    @pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
    def test_unsupported_image_formats(self, fmt):
        with pytest.raises(UnsupportedImageFormat):
            process_image(photo(fmt=fmt))

    def test_not_an_image(self):
        with pytest.raises(UnsupportedImageFormat):
            process_image(b"%PDF-1.7 this is not an image" * 10)

    @pytest.mark.parametrize("raw", [b"", photo()[:2000]], ids=["empty", "truncated"])
    def test_corrupt_image(self, raw):
        with pytest.raises(ImageUnreadable):
            process_image(raw)

    @pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
    def test_metadata_is_stripped(self, fmt):
        raw = photo(fmt=fmt, exif=exif_with_location())
        assert Image.open(io.BytesIO(raw)).getexif()  # 원본에는 위치 정보가 있다

        stored = Image.open(io.BytesIO(process_image(raw).data))
        assert not stored.getexif()
        assert "exif" not in stored.info

    def test_orientation_is_applied_before_stripping(self):
        # Orientation 6(90도 회전)인 800x600 사진은 600x800으로 저장되어야 한다
        result = process_image(photo(exif=exif_with_location(orientation=6)))
        assert (result.width, result.height) == (600, 800)

    def test_too_many_pixels(self, monkeypatch):
        monkeypatch.setattr("app.image.processor.MAX_PIXELS", 1000)
        assert MAX_PIXELS > 1000
        with pytest.raises(ImageTooLarge):
            process_image(photo())


class TestPerceptualHash:
    def base(self) -> str:
        return phash_hex(scene(1))

    @pytest.mark.parametrize(
        "variant",
        [
            lambda: encode(scene(1).resize((400, 300)), "JPEG", quality=70),
            lambda: encode(scene(1), "JPEG", quality=25),
            lambda: encode(scene(1), "PNG"),
            lambda: encode(scene(1).resize((1600, 1200)), "WEBP"),
        ],
        ids=["resized", "recompressed", "png", "upscaled-webp"],
    )
    def test_same_picture_is_close(self, variant):
        assert hamming_distance(self.base(), process_image(variant()).phash) <= 4

    def test_different_pictures_are_far(self):
        distances = [hamming_distance(self.base(), phash_hex(scene(s))) for s in range(2, 20)]
        assert min(distances) > 10

    @pytest.mark.parametrize("seed", range(30))
    def test_matches_imagehash_library(self, seed):
        # 직접 구현한 pHash가 imagehash.phash와 비트 단위로 같아야 한다
        imagehash = pytest.importorskip("imagehash")
        img = scene(seed, size=(300 + seed * 37, 200 + seed * 23))
        assert phash_hex(img) == str(imagehash.phash(img))

    def test_hamming_distance(self):
        assert hamming_distance("0000000000000000", "0000000000000000") == 0
        assert hamming_distance("0000000000000000", "000000000000000f") == 4
        assert hamming_distance("ffffffffffffffff", "0000000000000000") == 64


class TestStorage:
    def test_relative_path_uses_kst_month(self):
        # 2026-10-31 15:30 UTC = 2026-11-01 00:30 KST
        submitted = datetime(2026, 10, 31, 15, 30, tzinfo=UTC)
        assert ImageStorage.relative_path(42, submitted, "jpg") == "2026/11/42.jpg"

    async def test_save_and_delete(self, tmp_path):
        storage = ImageStorage(tmp_path)
        path = await storage.save("2026/11/1.jpg", b"data")
        assert path.read_bytes() == b"data"
        assert [p.name for p in path.parent.iterdir()] == ["1.jpg"]  # 임시 파일이 남지 않는다
        assert await storage.delete("2026/11/1.jpg") is True
        assert await storage.delete("2026/11/1.jpg") is False

    def test_rejects_paths_outside_root(self, tmp_path):
        with pytest.raises(ValueError):
            ImageStorage(tmp_path).absolute("../../etc/passwd")


class TestHttpFetcher:
    @pytest.fixture
    async def server(self):
        big = b"x" * 5000

        async def ok(request):
            return web.Response(body=b"hello")

        async def whole(request):
            return web.Response(body=big)

        async def chunked(request):
            # Content-Length 없이 보내도 받는 도중에 용량 초과를 감지해야 한다
            resp = web.StreamResponse()
            await resp.prepare(request)
            for i in range(0, len(big), 1000):
                await resp.write(big[i : i + 1000])
            await resp.write_eof()
            return resp

        async def missing(request):
            return web.Response(status=404)

        app = web.Application()
        app.router.add_get("/ok", ok)
        app.router.add_get("/big", whole)
        app.router.add_get("/chunked", chunked)
        app.router.add_get("/missing", missing)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
        await runner.cleanup()

    @pytest.fixture
    async def fetcher(self):
        f = HttpImageFetcher(allow_http=True, timeout_seconds=5)
        yield f
        await f.aclose()

    async def test_downloads(self, server, fetcher):
        assert await fetcher.fetch(f"{server}/ok", max_bytes=100) == b"hello"

    @pytest.mark.parametrize("path", ["/big", "/chunked"])
    async def test_stops_when_too_large(self, server, fetcher, path):
        with pytest.raises(ImageTooLarge):
            await fetcher.fetch(f"{server}{path}", max_bytes=1000)

    async def test_http_error(self, server, fetcher):
        with pytest.raises(ImageUnreadable):
            await fetcher.fetch(f"{server}/missing", max_bytes=100)

    async def test_connection_error(self, fetcher):
        with pytest.raises(ImageUnreadable):
            await fetcher.fetch("http://127.0.0.1:1/x", max_bytes=100)

    async def test_plain_http_rejected_by_default(self):
        f = HttpImageFetcher()
        with pytest.raises(ImageUnreadable):
            await f.fetch("http://example.com/a.jpg", max_bytes=100)
        await f.aclose()
