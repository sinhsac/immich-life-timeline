"""Cau hinh service, doc tu bien moi truong. Xem .env.example.

Service nay KHONG load model nao. Align dung kps da luu san trong fp_face,
nen RAM chi khoang 250-350MB, chay thuong tru tren may 8GB thoai mai.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _s(k, d=""):
    v = os.environ.get(k)
    return d if v is None or v == "" else v


def _i(k, d):
    try:
        return int(_s(k, str(d)))
    except ValueError:
        return d


def _b(k, d=False):
    return _s(k, "1" if d else "0").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # ---- Postgres: cung db voi Immich va voi job indexer ----
    pg_host: str = field(default_factory=lambda: _s("PG_HOST", "127.0.0.1"))
    pg_port: int = field(default_factory=lambda: _i("PG_PORT", 5432))
    pg_db: str = field(default_factory=lambda: _s("PG_DATABASE", "immich"))
    pg_user: str = field(default_factory=lambda: _s("PG_USER", "postgres"))
    pg_password: str = field(default_factory=lambda: _s("PG_PASSWORD", ""))
    pg_schema: str = field(default_factory=lambda: _s("PG_SCHEMA", "public"))
    prefix: str = field(default_factory=lambda: _s("TABLE_PREFIX", "fp_"))
    pool_max: int = field(default_factory=lambda: _i("PG_POOL_MAX", 4))

    # ---- Anh: hai duong, uu tien file neu co ----
    #   MEDIA_ROOT                     doc thang volume cua Immich, read-only
    #   IMMICH_URL + IMMICH_API_KEY    tai qua HTTP API, khong can mount gi
    media_root: str = field(default_factory=lambda: _s("MEDIA_ROOT", ""))
    immich_url: str = field(default_factory=lambda: _s("IMMICH_URL", ""))
    immich_api_key: str = field(default_factory=lambda: _s("IMMICH_API_KEY", ""))
    http_timeout: int = field(default_factory=lambda: _i("HTTP_TIMEOUT", 30))

    # ---- Thu muc ghi: thumbnail cache, frame, video ----
    work_dir: str = field(default_factory=lambda: _s("WORK_DIR", "/work"))

    # ---- ffmpeg ----
    ffmpeg: str = field(default_factory=lambda: _s("FFMPEG", "ffmpeg"))
    ffmpeg_threads: int = field(default_factory=lambda: _i("FFMPEG_THREADS", 2))
    font_file: str = field(default_factory=lambda: _s("FONT_FILE", ""))

    # ---- Bao mat: service nay xem duoc anh gia dinh, dat token di ----
    api_token: str = field(default_factory=lambda: _s("API_TOKEN", ""))

    # ---- Gioi han ----
    max_frames: int = field(default_factory=lambda: _i("MAX_FRAMES", 1200))
    thumb_size: int = field(default_factory=lambda: _i("THUMB_SIZE", 160))
    read_max_side: int = field(default_factory=lambda: _i("READ_MAX_SIDE", 0))

    @property
    def media(self):
        return Path(self.media_root) if self.media_root else None

    @property
    def work(self):
        return Path(self.work_dir)

    @property
    def thumbs(self):
        return self.work / "thumbs"

    @property
    def renders(self):
        return self.work / "renders"

    @property
    def cache(self):
        """Noi chua anh tam khi tai qua API. Xoa ngay sau khi dung."""
        return self.work / "cache"

    @property
    def media_src(self):
        if self.media_root:
            return f"file {self.media_root}"
        if self.immich_url and self.immich_api_key:
            return f"api {self.immich_url}"
        return "(chua dat)"

    def table(self, name):
        return f'{self.pg_schema}."{self.prefix}{name}"'

    def check(self):
        problems = []
        if not self.pg_password:
            problems.append("thieu PG_PASSWORD")
        if self.media_root:
            if not self.media.exists():
                problems.append(f"MEDIA_ROOT khong ton tai: {self.media_root}")
        elif not (self.immich_url and self.immich_api_key):
            problems.append(
                "khong co nguon anh (can de align + render). Chon mot trong hai:\n"
                "  MEDIA_ROOT=/immich-upload            mount volume, nhanh nhat\n"
                "  IMMICH_URL=... + IMMICH_API_KEY=...  tai qua HTTP")
        for d in (self.work, self.thumbs, self.renders, self.cache):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                problems.append(f"khong tao duoc {d}: {e}")
        return problems

    def describe(self):
        return (f"pg     {self.pg_user}@{self.pg_host}:{self.pg_port}/{self.pg_db}"
                f"  prefix={self.prefix}\n"
                f"anh    {self.media_src}\n"
                f"work   {self.work_dir}\n"
                f"ffmpeg {self.ffmpeg} threads={self.ffmpeg_threads}\n"
                f"auth   {'token' if self.api_token else 'MO - khong co xac thuc'}")


_cached = None


def get():
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached
