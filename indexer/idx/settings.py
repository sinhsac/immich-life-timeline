"""Cau hinh doc tu bien moi truong (12-factor, phu hop k3s ConfigMap/Secret).

Khong dung config.json de deploy khong phai mount them volume.
Moi gia tri co default hop ly, chi PG_PASSWORD la bat buoc.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _s(key, default=""):
    v = os.environ.get(key)
    return default if v is None or v == "" else v


def _i(key, default):
    try:
        return int(_s(key, str(default)))
    except ValueError:
        return default


def _f(key, default):
    try:
        return float(_s(key, str(default)))
    except ValueError:
        return default


def _b(key, default=False):
    return _s(key, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # ---- Postgres cua Immich: dung chung user/pass/db, khac prefix bang ----
    pg_host: str = field(default_factory=lambda: _s("PG_HOST", "127.0.0.1"))
    pg_port: int = field(default_factory=lambda: _i("PG_PORT", 5432))
    pg_db: str = field(default_factory=lambda: _s("PG_DATABASE", "immich"))
    pg_user: str = field(default_factory=lambda: _s("PG_USER", "postgres"))
    pg_password: str = field(default_factory=lambda: _s("PG_PASSWORD", ""))
    pg_schema: str = field(default_factory=lambda: _s("PG_SCHEMA", "public"))
    prefix: str = field(default_factory=lambda: _s("TABLE_PREFIX", "fp_"))
    pg_timeout: int = field(default_factory=lambda: _i("PG_CONNECT_TIMEOUT", 10))

    # ---- Nguon anh ----
    # MEDIA_ROOT tro vao volume UPLOAD_LOCATION cua Immich, mount read-only.
    media_root: str = field(default_factory=lambda: _s("MEDIA_ROOT", ""))
    immich_url: str = field(default_factory=lambda: _s("IMMICH_URL", ""))
    immich_api_key: str = field(default_factory=lambda: _s("IMMICH_API_KEY", ""))
    http_timeout: int = field(default_factory=lambda: _i("HTTP_TIMEOUT", 30))
    cache_dir: str = field(default_factory=lambda: _s("CACHE_DIR", "/tmp/fp-cache"))

    # ---- Model ----
    model_dir: str = field(default_factory=lambda: _s("MODEL_DIR", "/models"))
    face_model: str = field(default_factory=lambda: _s("FACE_MODEL", "buffalo_l"))
    body_model: str = field(default_factory=lambda: _s("BODY_MODEL", "yolov8n-pose.onnx"))
    body_imgsz: int = field(default_factory=lambda: _i("BODY_IMGSZ", 640))
    body_conf: float = field(default_factory=lambda: _f("BODY_CONF", 0.35))
    body_iou: float = field(default_factory=lambda: _f("BODY_IOU", 0.55))
    body_max: int = field(default_factory=lambda: _i("BODY_MAX_PERSON", 12))
    use_gpu: bool = field(default_factory=lambda: _b("USE_GPU", False))

    # ---- Tai nguyen: may 8GB RAM / i5, dung chung node voi Immich ----
    onnx_threads: int = field(default_factory=lambda: _i("ONNX_THREADS", 2))
    batch: int = field(default_factory=lambda: _i("BATCH_COMMIT", 200))
    sleep_ms: int = field(default_factory=lambda: _i("SLEEP_MS", 0))
    limit: int = field(default_factory=lambda: _i("LIMIT", 0))          # 0 = khong gioi han
    copy_embedding: bool = field(default_factory=lambda: _b("COPY_EMBEDDING", True))
    max_side: int = field(default_factory=lambda: _i("MAX_SIDE", 1600))  # resize truoc infer

    # ---- Video ----
    # Immich chi detect mat cho video tren MOT frame thumbnail, nen muon biet
    # nguoi do xuat hien o giay thu bao nhieu thi phai tu quet. Day la stage
    # dat nhat cua ca job, va la stage duy nhat chay detection + recognition.
    do_video: bool = field(default_factory=lambda: _b("DO_VIDEO", True))
    video_fps: float = field(default_factory=lambda: _f("VIDEO_FPS", 2.0))
    video_max_side: int = field(default_factory=lambda: _i("VIDEO_MAX_SIDE", 960))
    video_max_seconds: float = field(default_factory=lambda: _f("VIDEO_MAX_SECONDS", 0))
    # Tran dung lieu khi PHAI tai video qua HTTP (khong co MEDIA_ROOT).
    #
    # cv2.VideoCapture can mot file cuc bo doc duoc ngau nhien, nen che do HTTP
    # buoc phai tai het file truoc khi quet. Do thuc te tren thu vien nay:
    # median 1.7MB, p90 1MB, nhung p99 la 144MB va dai nhat 659MB. Khong chan
    # thi mot video 66 phut se chiem het bang thong va dia cua ca lo.
    #
    # 200MB phu ~99% thu vien. Video vuot tran bi ghi clip_state=-1 kem ly do,
    # chay 'job.py --reset clips' sau khi nang tran la quet lai duoc.
    # Dat 0 de bo tran.
    video_max_mb: float = field(default_factory=lambda: _f("VIDEO_MAX_MB", 200))
    video_det_size: int = field(default_factory=lambda: _i("VIDEO_DET_SIZE", 512))
    video_det_conf: float = field(default_factory=lambda: _f("VIDEO_DET_CONF", 0.45))
    # Cosine toi thieu voi vector trung tam cua mot person trong fp_face.
    video_sim: float = field(default_factory=lambda: _f("VIDEO_SIM", 0.38))
    # Cach biet toi thieu so voi person xep thu hai, de khong gan bua cho nguoi
    # than co net giong nhau.
    video_margin: float = field(default_factory=lambda: _f("VIDEO_MARGIN", 0.04))
    video_centroid_per: int = field(default_factory=lambda: _i("VIDEO_CENTROID_PER", 24))

    # ---- Do day cua du lieu video ----
    # Giu ca mat KHONG khop person nao. Tat di thi bang gon hon nhieu, nhung
    # nguoi chua gan ten trong Immich va nguoi hoan toan moi se khong de lai dau
    # vet gi — tuc la tu bo kha nang phat hien person moi tu video.
    video_keep_unmatched: bool = field(
        default_factory=lambda: _b("VIDEO_KEEP_UNMATCHED", True))
    # Luu vector ArcFace vao fp_vface. 512 float32 = 2KB/dong. O muc lay mau 2
    # frame/s day la khoang 0.5-1MB moi phut video, doi lay viec khong bao gio
    # phai quet lai de gom cum hay gan ten.
    video_store_emb: bool = field(
        default_factory=lambda: _b("VIDEO_STORE_EMB", True))
    # Chay 1k3d68 cho tung mat trong frame -> lmk68 + pitch that + age + ear.
    # Khong co lmk68 thi frame video khong align duoc ngang voi anh tinh.
    # Doi lai: them mot lan infer moi mat moi frame. Tat di van co yaw/pitch/roll
    # uoc luong tu 5 diem (pose_from_kps5), chi kem chinh xac hon.
    video_lmk68: bool = field(default_factory=lambda: _b("VIDEO_LMK68", True))
    # Body pose (yolov8n-pose) tren frame video -> fp_vbody.
    do_vbody: bool = field(default_factory=lambda: _b("DO_VBODY", True))
    # Bo qua mat qua nho: nguong khoang cach hai mat, tinh bang pixel tren frame
    # da resize ve VIDEO_MAX_SIDE. 0 = giu tat ca.
    #
    # Day la van dieu chinh chinh khi bang phinh: mot canh dong nguoi co the cho
    # ra vai chuc mat cao 10px moi frame, khong dung duoc vao viec gi ma chiem
    # phan lon so dong.
    video_min_face_px: int = field(
        default_factory=lambda: _i("VIDEO_MIN_FACE_PX", 0))
    # IoU toi thieu de coi hai bbox o hai frame lien tiep la cung mot nguoi.
    video_track_iou: float = field(
        default_factory=lambda: _f("VIDEO_TRACK_IOU", 0.30))
    # Gom frame thanh doan: cho phep mat hut trong bao lau ma van tinh la lien tuc
    video_gap_ms: int = field(default_factory=lambda: _i("VIDEO_GAP_MS", 800))
    clip_seconds: float = field(default_factory=lambda: _f("CLIP_SECONDS", 2.6))
    clip_min_seconds: float = field(default_factory=lambda: _f("CLIP_MIN_SECONDS", 1.2))
    clip_max_seconds: float = field(default_factory=lambda: _f("CLIP_MAX_SECONDS", 4.5))
    clip_per_person: int = field(default_factory=lambda: _i("CLIP_PER_PERSON", 3))

    # ---- Pham vi scan ----
    taken_after: str = field(default_factory=lambda: _s("TAKEN_AFTER", ""))
    taken_before: str = field(default_factory=lambda: _s("TAKEN_BEFORE", ""))

    @property
    def media(self):
        return Path(self.media_root) if self.media_root else None

    def table(self, name):
        """Ten bang cua rieng job nay, da qualify schema."""
        return f'{self.pg_schema}."{self.prefix}{name}"'

    def require_pg(self):
        if not self.pg_password:
            raise SystemExit("Thieu PG_PASSWORD (k3s: dat qua Secret).")

    def require_media(self):
        if not self.media_root and not (self.immich_url and self.immich_api_key):
            raise SystemExit(
                "Khong co nguon anh. Chon mot trong hai:\n"
                "  MEDIA_ROOT=/immich-upload            (mount volume, nhanh nhat)\n"
                "  IMMICH_URL=... + IMMICH_API_KEY=...  (tai qua HTTP)")

    def describe(self):
        src = f"file {self.media_root}" if self.media_root else (
            f"http {self.immich_url}" if self.immich_url else "chua cau hinh")
        vid = (f"{self.video_fps or 'moi'} frame/s, mat>={self.video_sim} cosine, "
               f"doan {self.clip_seconds}s" if self.do_video else "tat")
        deep = ", ".join(x for x in (
            "mat-la" if self.video_keep_unmatched else "",
            "emb" if self.video_store_emb else "",
            "lmk68" if self.video_lmk68 else "",
            "body" if self.do_vbody else "",
            f"min-mat={self.video_min_face_px}px" if self.video_min_face_px else "",
        ) if x) or "toi thieu"
        return (f"pg      {self.pg_user}@{self.pg_host}:{self.pg_port}/{self.pg_db}"
                f"  prefix={self.prefix}\n"
                f"anh     {src}\n"
                f"model   dir={self.model_dir} face={self.face_model} "
                f"body={self.body_model}\n"
                f"video   {vid}\n"
                f"vdata   {deep}\n"
                f"resource gpu={self.use_gpu} threads={self.onnx_threads} "
                f"sleep={self.sleep_ms}ms batch={self.batch} limit={self.limit or '-'}")


def load():
    s = Settings()
    # Gioi han thread cho ca BLAS lan onnxruntime, tranh giành CPU voi Immich.
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(k, str(max(1, s.onnx_threads)))
    return s
