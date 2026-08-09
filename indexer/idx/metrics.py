"""Do chat luong khuon mat: align, doi xung, EAR, do chinh dien, diem tong hop.

Cung cong thuc voi pipeline/faceutil.py de so lieu hai ben so sanh duoc.
Tach ra day de folder indexer chay doc lap, khong import pipeline cu.
"""
import cv2
import numpy as np

# iBUG 68 diem
_L_EYE = slice(36, 42)
_R_EYE = slice(42, 48)
_NOSE = 30
_L_MOUTH = 48
_R_MOUTH = 54

_W_DEFAULT = {"frontal": 40.0, "sharp": 25.0, "res": 25.0,
              "expo": 10.0, "embnorm": 10.0, "det": 10.0}


def align(img, kps, size=512, eye_dx=0.29, eye_y=0.42):
    """Similarity transform dua 2 mat ve vi tri co dinh trong khung size x size."""
    left = np.array([(0.5 - eye_dx) * size, eye_y * size])
    right = np.array([(0.5 + eye_dx) * size, eye_y * size])
    src = np.asarray(kps, np.float32)[:2]
    dst = np.stack([left, right]).astype(np.float32)
    ds, dd = src[1] - src[0], dst[1] - dst[0]
    scale = float(np.linalg.norm(dd)) / max(float(np.linalg.norm(ds)), 1e-6)
    ang = np.arctan2(dd[1], dd[0]) - np.arctan2(ds[1], ds[0])
    c, s = np.cos(ang) * scale, np.sin(ang) * scale
    m = np.array([[c, -s, 0.0], [s, c, 0.0]], np.float32)
    m[:, 2] = dst[0] - m[:, :2] @ src[0]
    return cv2.warpAffine(img, m, (size, size), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def kps5_from_68(lmk):
    p = np.asarray(lmk, np.float32)[:, :2]
    return np.stack([p[_L_EYE].mean(0), p[_R_EYE].mean(0),
                     p[_NOSE], p[_L_MOUTH], p[_R_MOUTH]]).astype(np.float32)


def _ear_one(p):
    horiz = float(np.linalg.norm(p[0] - p[3]))
    if horiz < 1e-6:
        return 0.0
    return (float(np.linalg.norm(p[1] - p[5]))
            + float(np.linalg.norm(p[2] - p[4]))) / (2.0 * horiz)


def ear_from_68(lmk):
    """Trung binh EAR hai mat. Mo ~0.25-0.35, nham < 0.15. Chi la goi y."""
    if lmk is None:
        return None
    p = np.asarray(lmk, np.float32)[:, :2]
    if p.shape[0] < 48:
        return None
    return 0.5 * (_ear_one(p[_L_EYE]) + _ear_one(p[_R_EYE]))


def symmetry(aligned_gray):
    """0..1, cang cao cang chinh dien. Khong dung model nao."""
    h, w = aligned_gray.shape[:2]
    roi = aligned_gray[int(h * 0.20):int(h * 0.88), int(w * 0.14):int(w * 0.86)]
    if roi.size < 64:
        return 0.0
    a = roi.astype(np.float32)
    b = cv2.flip(a, 1)
    a -= a.mean()
    b -= b.mean()
    den = float(np.linalg.norm(a) * np.linalg.norm(b))
    if den < 1e-6:
        return 0.0
    return float(max(0.0, min(1.0, (a * b).sum() / den)))


def frontality(yaw, pitch, roll, symm=None):
    ang = 1.0 - min(1.0, abs(yaw) / 35.0 + abs(pitch) / 30.0 + abs(roll) / 45.0)
    ang = max(0.0, ang)
    if symm is None:
        return ang
    return max(0.0, 0.65 * ang + 0.35 * float(symm))


def quality(eye_px, sharp, bright, yaw, pitch, roll, det,
            symm=None, emb_norm=None, weights=None):
    w = dict(_W_DEFAULT, **(weights or {}))
    comp = {
        "frontal": frontality(yaw, pitch, roll, symm),
        "sharp": min(max(sharp or 0.0, 0.0) / 400.0, 1.0),
        "res": min(max(eye_px or 0.0, 0.0) / 110.0, 1.0),
        "expo": 1.0 - min(1.0, abs((bright or 0.0) - 120.0) / 120.0),
        "embnorm": 0.5 if emb_norm is None
                   else min(1.0, max(0.0, (float(emb_norm) - 12.0) / 18.0)),
        "det": min(1.0, max(0.0, float(det or 0.0))),
    }
    return float(sum(w[k] * v for k, v in comp.items()))


def face_metrics(img, bbox, kps):
    """Do net / sang / khoang cach mat tren crop chuan hoa 128px.

    Chuan hoa kich thuoc truoc khi do Laplacian la bat buoc, khong thi anh
    phan giai cao luon 'net hon' va diem so vo nghia.
    """
    h, w = img.shape[:2]
    x1, y1 = max(0, int(bbox[0])), max(0, int(bbox[1]))
    x2, y2 = min(w, int(bbox[2])), min(h, int(bbox[3]))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    g = cv2.cvtColor(cv2.resize(img[y1:y2, x1:x2], (128, 128)), cv2.COLOR_BGR2GRAY)
    small = align(img, kps, 128)
    return {
        "eye_px": float(np.linalg.norm(np.asarray(kps[1]) - np.asarray(kps[0]))),
        "sharp": float(cv2.Laplacian(g, cv2.CV_64F).var()),
        "bright": float(g.mean()),
        "symm": symmetry(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)),
    }


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = ((a[2] - a[0]) * (a[3] - a[1])
             + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / union if union > 0 else 0.0
