"""Doc anh preview cua Immich + align khuon mat.

Hai duong lay anh, uu tien duong 1:
  1. Doc thang file preview qua volume mount read-only (MEDIA_ROOT).
  2. Tai qua HTTP API bang x-api-key, ghi file tam roi xoa ngay.

Align dung kps da luu trong fp_face (toa do chuan hoa 0..1) nen KHONG can load
model nao. Nho vay moi frame co cung vi tri mat, cung do nghieng, cung khoang
cach — video ghep ra muot thay vi nhay loan.
"""
import os
import threading
from pathlib import Path

import cv2
import numpy as np

_ANCHORS = ("thumbs", "upload", "library", "encoded-video")

# Render chay o thread nen, request API o thread khac -> session theo thread.
_tls = threading.local()


def resolve(media_root, preview_path):
    """Duong dan trong db la duong dan TRONG container Immich."""
    if not preview_path:
        return None
    p = Path(str(preview_path))
    # Duong dan tuyet doi va co that thi dung luon, khong can MEDIA_ROOT. Truoc
    # day nhanh nay nam sau cai guard doi MEDIA_ROOT nen khong bao gio chay.
    if p.is_absolute() and p.exists():
        return p
    if not media_root:
        return None
    root = Path(media_root)
    cands = []
    parts = p.parts
    for a in _ANCHORS:
        if a in parts:
            i = parts.index(a)
            cands.append(root / Path(*parts[i:]))
            if i + 1 < len(parts):
                cands.append(root / Path(*parts[i + 1:]))
    cands.append(root / p)
    for c in cands:
        if c.exists():
            return c
    return None


def _session(s):
    sess = getattr(_tls, "sess", None)
    if sess is None:
        import requests
        sess = requests.Session()
        sess.headers.update({"x-api-key": s.immich_api_key,
                             "Accept": "application/octet-stream"})
        _tls.sess = sess
    return sess


def download(asset_id, s):
    """Tai anh preview qua API Immich vao file tam. Tra ve Path."""
    base = s.immich_url.rstrip("/")
    if not base.endswith("/api"):
        base += "/api"
    sess = _session(s)
    r = sess.get(f"{base}/assets/{asset_id}/thumbnail?size=preview",
                 timeout=s.http_timeout)
    if r.status_code == 404:                      # ten endpoint cua ban Immich cu
        r = sess.get(f"{base}/asset/thumbnail/{asset_id}?format=JPEG",
                     timeout=s.http_timeout)
    r.raise_for_status()
    s.cache.mkdir(parents=True, exist_ok=True)
    dst = s.cache / f"{threading.get_ident()}_{asset_id}.jpg"
    dst.write_bytes(r.content)
    return dst


def load(asset_id, preview_path, s):
    """Anh preview cua mot asset: (img BGR, tmp).

    Goi release(tmp) sau khi dung xong — tmp la None khi doc tu file.
    """
    path = resolve(s.media_root, preview_path)
    if path is not None:
        return imread(path), None
    if not (s.immich_url and s.immich_api_key):
        return None, None
    try:
        tmp = download(asset_id, s)
    except Exception:                                        # noqa: BLE001
        return None, None
    img = imread(tmp)
    if img is None:
        release(tmp)
        return None, None
    return img, tmp


def release(tmp):
    if tmp:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def imread(path):
    """cv2.imread khong xu ly duoc duong dan unicode tren Windows."""
    try:
        buf = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def imwrite(path, img, quality=92):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        return False
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    buf.tofile(str(path))
    return True


def kps_from_blob(blob, w, h):
    """float32[5][2] chuan hoa -> pixel cua anh w x h."""
    if not blob:
        return None
    a = np.frombuffer(blob, np.float32)
    if a.size < 10:
        return None
    k = a[:10].reshape(5, 2).copy()
    k[:, 0] *= float(w)
    k[:, 1] *= float(h)
    return k


def pair_kps(k1, k2, min_ratio=1.3):
    """Diem neo cho video HAI NGUOI: giu ca hai khuon mat o cho co dinh.

    anchor_frame chi dung k[0] va k[1] (hai mat) de suy ra tam, goc va ty le. Neu
    truyen thang hai tam mat cua hai nguoi vao do thi voi level=True ca anh se bi
    xoay cho hai nguoi nam ngang — bo cao con thap la lech 30 do, hong anh.

    Nen o day tra ve HAI DIEM AO nam ngang, cach nhau dung khoang cach that giua
    hai nguoi, dat quanh trung diem cua ho. Ket qua: goc xoay 0, trung diem cua
    hai nguoi luon o mot cho, va khoang cach giua ho luon bang mot ty le khung —
    ai xa nhau thi khung tu rong ra de chua het ca hai.

    min_ratio chan truong hop hai mat gan trung nhau (nguoi dung sat nhau, hoac
    cung mot nguoi bi Immich tach thanh hai cum): luc do d ~ 0 va he so phong to
    se no ra vo cuc.
    """
    a, b = np.asarray(k1, np.float32), np.asarray(k2, np.float32)
    if a.shape[0] < 2 or b.shape[0] < 2:
        return None
    ca, cb = (a[0] + a[1]) / 2.0, (b[0] + b[1]) / 2.0
    mid = (ca + cb) / 2.0
    d = float(np.hypot(*(cb - ca)))
    eye = max(float(np.hypot(*(a[1] - a[0]))), float(np.hypot(*(b[1] - b[0]))))
    d = max(d, min_ratio * max(eye, 1e-3))
    return np.array([[mid[0] - d / 2.0, mid[1]],
                     [mid[0] + d / 2.0, mid[1]]], np.float32)


ASPECTS = {"1:1": (1, 1), "4:3": (4, 3), "3:2": (3, 2), "16:9": (16, 9),
           "3:4": (3, 4), "2:3": (2, 3), "9:16": (9, 16)}


def frame_size(size, aspect="4:3"):
    """size la CANH DAI. Tra ve (w, h) chan, vi libx264 can chieu chia het 2."""
    aw, ah = ASPECTS.get(aspect, (1, 1))
    if aw >= ah:
        w, h = size, int(round(size * ah / aw))
    else:
        w, h = int(round(size * aw / ah)), size
    return max(2, w // 2 * 2), max(2, h // 2 * 2)


def _blur_cover(img, out_w, out_h, strength=0.06, dim=0.55):
    """Nen lam mo tu chinh anh do, thay cho vien den khi fill='blur'."""
    h, w = img.shape[:2]
    r = max(out_w / w, out_h / h)
    m = cv2.resize(img, (max(1, int(round(w * r))), max(1, int(round(h * r)))),
                   interpolation=cv2.INTER_LINEAR)
    y = max(0, (m.shape[0] - out_h) // 2)
    x = max(0, (m.shape[1] - out_w) // 2)
    m = m[y:y + out_h, x:x + out_w]
    k = max(3, int(round(min(out_w, out_h) * strength)) | 1)
    m = cv2.GaussianBlur(m, (k, k), 0)
    return (m.astype(np.float32) * dim).astype(np.uint8)


def anchor_frame(img, kps, out_w, out_h, face_frac=0.12, anchor_x=0.5,
                 eye_y=0.33, level=True, fill="crop", max_zoom=4.0,
                 zoom=1.0, interp=cv2.INTER_LANCZOS4):
    """Neo khuon mat vao mot cho co dinh nhung GIU CANG NHIEU KHUNG ANH CANG TOT.

    Khuon mat chi la diem neo, khong phai chu the duy nhat cua khung. Tham so
    quyet dinh la face_frac: khoang cach hai mat tinh theo chieu ngang khung ra.
      0.50-0.60  chan dung sat mat (hanh vi cu cua align())
      0.10-0.15  thay ca nguoi va boi canh  <- mac dinh moi
      0.06-0.08  toan canh, nguoi nho

    Vi moi frame co cung face_frac va cung eye_y, khuon mat nam dung mot cho
    va cung do lon xuyen suot video -> xem muot chu khong nhay.

    fill='crop'  phong to vua du phu kin khung, cat bot ria. Khong co vien.
    fill='blur'  giu tron khung anh, phan trong lap bang chinh anh do lam mo.

    Anh khong du lon de phu kin khung thi phai phong to them, luc do khuon mat
    to hon face_frac mot chut — max_zoom chan lai de khong phong to qua da.

    zoom la he so Ken Burns cho video ke chuyen. Diem quan trong: phep bien doi
    LUON dua diem giua hai mat ve dung 'target', nen zoom KHONG lam mat xe dich
    — chi khung anh rong ra hep vao. Neo van la neo, nhung khung het bat dong.
    interp: chuyen canh dung INTER_LINEAR cho re, frame tinh dung LANCZOS4.
    """
    k = np.asarray(kps, np.float32)
    if k.shape[0] < 2:
        return None
    e0, e1 = k[0], k[1]
    mid = (e0 + e1) / 2.0
    d = e1 - e0
    dist = float(np.hypot(d[0], d[1]))
    if dist < 1e-3:
        return None
    ang = float(np.arctan2(d[1], d[0])) if level else 0.0

    h, w = img.shape[:2]
    corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    target = np.array([anchor_x * out_w, eye_y * out_h], np.float32)

    def build(scale):
        c, sn = np.cos(-ang) * scale, np.sin(-ang) * scale
        lin = np.array([[c, -sn], [sn, c]], np.float32)
        m = np.zeros((2, 3), np.float32)
        m[:, :2] = lin
        m[:, 2] = target - lin @ mid
        return m

    def span(m):
        p = corners @ m[:, :2].T + m[:, 2]
        return (p[:, 0].min(), p[:, 0].max(), p[:, 1].min(), p[:, 1].max())

    scale = (face_frac * out_w) / dist * max(0.2, float(zoom))
    m = build(scale)
    x0, x1, y0, y1 = span(m)

    if fill == "blur":
        # thu nho lai neu tron khung anh khong nam vua trong khung ra
        f = min(1.0, out_w / max(x1 - x0, 1e-6), out_h / max(y1 - y0, 1e-6))
        if f < 1.0:
            m = build(scale * f)
            x0, x1, y0, y1 = span(m)
        m[0, 2] += (-x0) if x0 < 0 else (out_w - x1 if x1 > out_w else 0.0)
        m[1, 2] += (-y0) if y0 < 0 else (out_h - y1 if y1 > out_h else 0.0)
        fg = cv2.warpAffine(img, m, (out_w, out_h), flags=interp,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        hit = cv2.warpAffine(np.full((h, w), 255, np.uint8), m, (out_w, out_h),
                             flags=cv2.INTER_NEAREST, borderValue=0)
        out = _blur_cover(img, out_w, out_h)
        out[hit > 127] = fg[hit > 127]
        return out

    # fill='crop': phong to vua du phu kin khung, chi khi bat buoc
    f = max(1.0, out_w / max(x1 - x0, 1e-6), out_h / max(y1 - y0, 1e-6))
    if f > 1.0:
        m = build(scale * min(f, max_zoom))
        x0, x1, y0, y1 = span(m)
    # day anh vao cho phu kin, uu tien giu khuon mat dung vi tri neo
    m[0, 2] += (-x0) if x0 > 0 else (out_w - x1 if x1 < out_w else 0.0)
    m[1, 2] += (-y0) if y0 > 0 else (out_h - y1 if y1 < out_h else 0.0)
    return cv2.warpAffine(img, m, (out_w, out_h), flags=interp,
                          borderMode=cv2.BORDER_REPLICATE)


def dim(frame, k):
    """Nhan sang cua frame — dung cho mo man tu den va dong man ve den."""
    k = max(0.0, min(1.0, float(k)))
    if k >= 0.999:
        return frame
    return (frame.astype(np.float32) * k).astype(np.uint8)


def align(img, kps, size=512, eye_dx=0.29, eye_y=0.42):
    """Tuong thich nguoc: khung vuong, crop sat mat.

    eye_dx la nua khoang cach hai mat theo chieu ngang khung, nen doi sang
    face_frac chi la nhan 2. Giu lai de code cu va thumbnail khong phai sua.
    """
    return anchor_frame(img, kps, size, size, face_frac=2.0 * eye_dx,
                        eye_y=eye_y, fill="crop")


def crop_box(img, box01, margin=0.28, out=160):
    """Crop theo bbox chuan hoa, no thm margin. Dung cho thumbnail."""
    h, w = img.shape[:2]
    bw = (box01[2] - box01[0]) * w
    bh = (box01[3] - box01[1]) * h
    mx, my = bw * margin, bh * margin
    x1 = max(0, int(box01[0] * w - mx))
    y1 = max(0, int(box01[1] * h - my))
    x2 = min(w, int(box01[2] * w + mx))
    y2 = min(h, int(box01[3] * h + my))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return cv2.resize(img[y1:y2, x1:x2], (out, out), interpolation=cv2.INTER_AREA)
