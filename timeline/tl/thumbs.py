"""Thumbnail khuon mat, crop tu anh preview cua Immich, cache tren dia.

Anh lay qua media.load(): doc file neu co MEDIA_ROOT, khong thi tai qua API.
Cache theo asset/fidx/size nen mo lai trang la co ngay, khong tai lai.
"""
from pathlib import Path

from . import media
from .db import rows
from .settings import get

_BOX = """
SELECT f.x1, f.y1, f.x2, f.y2, a.preview_path
FROM {face} f JOIN {asset} a ON a.id = f.asset_id
WHERE f.asset_id = %s AND f.fidx = %s
"""


def face_thumb(asset_id, fidx, size=None):
    """Tra ve duong dan file jpg, tao neu chua co. None neu khong doc duoc anh."""
    s = get()
    size = int(size or s.thumb_size)
    size = max(48, min(512, size))
    if int(fidx) < 0:
        return clip_thumb(asset_id, -1 - int(fidx), size)
    dst = s.thumbs / f"{size}" / f"{asset_id}_{int(fidx)}.jpg"
    if dst.exists():
        return dst

    sql = _BOX.format(face=s.table("face"), asset=s.table("asset"))
    with rows() as (c, cur):
        cur.execute(sql, (asset_id, int(fidx)))
        row = cur.fetchone()
        c.rollback()
    if not row:
        return None
    img, tmp = media.load(asset_id, row["preview_path"], s)
    if img is None:
        return None
    try:
        crop = media.crop_box(img, (row["x1"], row["y1"], row["x2"], row["y2"]),
                              margin=0.30, out=size)
    finally:
        media.release(tmp)
    if crop is None:
        return None
    return dst if media.imwrite(dst, crop, 86) else None


_CLIP = """
SELECT c.t_start_ms, c.t_end_ms, c.track, a.video_path
FROM {vclip} c JOIN {asset} a ON a.id = c.asset_id
WHERE c.asset_id = %s AND c.cidx = %s
LIMIT 1
"""


def clip_thumb(asset_id, cidx, size=160):
    """Anh dai dien cho mot DOAN VIDEO: frame giua doan, crop quanh khuon mat.

    Khong co no thi o luoi "anh vao video" cua UI se la mot o den — nguoi dung
    khong biet doan nao vua duoc chon.
    """
    import cv2
    s = get()
    size = max(48, min(512, int(size)))
    dst = s.thumbs / f"clip{size}" / f"{asset_id}_{int(cidx)}.jpg"
    if dst.exists():
        return dst
    sql = _CLIP.format(vclip=s.table("vclip"), asset=s.table("asset"))
    try:
        with rows() as (c, cur):
            cur.execute(sql, (asset_id, int(cidx)))
            row = cur.fetchone()
            c.rollback()
    except Exception:                                    # noqa: BLE001
        return None                                      # chua co bang fp_vclip
    if not row or not row["video_path"]:
        return None
    path = media.resolve(s.media_root, row["video_path"])
    if path is None:
        return None
    mid = (float(row["t_start_ms"]) + float(row["t_end_ms"])) / 2.0
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        return None
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, mid))
        ok, img = cap.read()
    finally:
        cap.release()
    if not ok or img is None:
        return None
    box = _clip_box(row["track"], mid)
    crop = (media.crop_box(img, box, margin=0.55, out=size) if box
            else cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA))
    if crop is None:
        return None
    return dst if media.imwrite(dst, crop, 86) else None


def _clip_box(blob, t_ms):
    """bbox chuan hoa quanh khuon mat tai moc t_ms, suy tu track cua doan."""
    import numpy as np
    if not blob:
        return None
    a = np.frombuffer(blob, np.float32)
    if a.size < 11 or a.size % 11:
        return None
    tr = a.reshape(-1, 11)
    j = int(np.argmin(np.abs(tr[:, 0] * 1000.0 - float(t_ms))))
    k = tr[j, 1:].reshape(5, 2)
    d = float(np.hypot(*(k[1] - k[0]))) or 0.05
    cx, cy = float(k[:, 0].mean()), float(k[:, 1].mean())
    r = max(0.02, d * 1.6)
    return (max(0.0, cx - r), max(0.0, cy - r),
            min(1.0, cx + r), min(1.0, cy + r))


def aligned_preview(asset_id, fidx, size=256, aspect="4:3", face_frac=0.12,
                    eye_y=0.33, fill="crop", level=True):
    """Xem truoc DUNG khung se render, de kiem tra truoc khi dung ca video.

    Tham so phai khop buoc 4, neu khong preview noi doi. Cache key gom ca
    tham so nen doi ngu?ng la sinh anh moi chu khong tra anh cu.
    """
    s = get()
    size = max(64, min(1024, int(size)))
    out_w, out_h = media.frame_size(size, aspect)
    key = f"{size}_{aspect.replace(':', 'x')}_{face_frac:.3f}_{eye_y:.2f}_{fill}_{int(level)}"
    dst = s.thumbs / f"aligned_{key}" / f"{asset_id}_{int(fidx)}.jpg"
    if dst.exists():
        return dst
    sql = (f"SELECT f.kps, a.preview_path FROM {s.table('face')} f "
           f"JOIN {s.table('asset')} a ON a.id = f.asset_id "
           f"WHERE f.asset_id=%s AND f.fidx=%s")
    with rows() as (c, cur):
        cur.execute(sql, (asset_id, int(fidx)))
        row = cur.fetchone()
        c.rollback()
    if not row or not row["kps"]:
        return None
    img, tmp = media.load(asset_id, row["preview_path"], s)
    if img is None:
        return None
    try:
        h, w = img.shape[:2]
        kps = media.kps_from_blob(row["kps"], w, h)
        if kps is None:
            return None
        out = media.anchor_frame(img, kps, out_w, out_h, face_frac=face_frac,
                                 eye_y=eye_y, level=level, fill=fill)
    finally:
        media.release(tmp)
    if out is None:
        return None
    return dst if media.imwrite(dst, out, 88) else None


def purge(older_than_days=30):
    """Xoa cache cu. Goi tay qua API, khong tu dong."""
    import time
    s = get()
    cut = time.time() - older_than_days * 86400
    n = 0
    for p in Path(s.thumbs).rglob("*.jpg"):
        try:
            if p.stat().st_mtime < cut:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n
