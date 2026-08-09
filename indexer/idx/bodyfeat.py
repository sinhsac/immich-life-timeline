"""Suy dac trung co the index duoc tu 17 keypoint COCO.

Luu ca 17 diem tho (bytea) de sau nay doi cong thuc khong phai chay lai model,
dong thoi tinh san vai truong vo huong de query bang SQL:

  orientation  front / back / side / unknown
  posture      standing / sitting / lying / unknown
  torso_deg    goc than so voi truc doc, 0 = dung thang
  body_front   0..1, do chinh dien cua than nguoi
"""
import numpy as np

NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SHO, R_SHO, L_ELB, R_ELB = 5, 6, 7, 8
L_WRI, R_WRI, L_HIP, R_HIP = 9, 10, 11, 12
L_KNE, R_KNE, L_ANK, R_ANK = 13, 14, 15, 16

VIS = 0.35          # nguong conf coi la thay duoc


def _ok(k, i):
    return k[i, 2] >= VIS


def _mid(k, a, b):
    if _ok(k, a) and _ok(k, b):
        return (k[a, :2] + k[b, :2]) / 2.0
    if _ok(k, a):
        return k[a, :2]
    if _ok(k, b):
        return k[b, :2]
    return None


def describe(kps, img_w, img_h, bbox=None):
    """kps: (17,3) toa do pixel + conf. Tra ve dict cac truong vo huong."""
    k = np.asarray(kps, np.float32)
    n_vis = int((k[:, 2] >= VIS).sum())
    sho = _mid(k, L_SHO, R_SHO)
    hip = _mid(k, L_HIP, R_HIP)

    torso_deg = _torso_angle(sho, hip)
    orient = _orientation(k)
    posture = _posture(k, sho, hip, torso_deg)
    front = _body_frontality(k, orient, torso_deg)

    area = None
    if bbox is not None and img_w and img_h:
        bw = max(0.0, bbox[2] - bbox[0])
        bh = max(0.0, bbox[3] - bbox[1])
        area = float(bw * bh) / float(img_w * img_h)

    return {"n_visible": n_vis, "orientation": orient, "posture": posture,
            "torso_deg": torso_deg, "body_front": front, "area_ratio": area}


# --------------------------------------------------------------------- goc
def _torso_angle(sho, hip):
    """0 = than doc (dung/ngoi), 90 = than ngang (nam)."""
    if sho is None or hip is None:
        return None
    v = np.asarray(hip, np.float32) - np.asarray(sho, np.float32)
    n = float(np.linalg.norm(v))
    if n < 1e-6:
        return None
    # goc so voi truc y (huong xuong duoi cua anh)
    return float(np.degrees(np.arctan2(abs(v[0]), abs(v[1]))))


# ---------------------------------------------------------------- huong
def _orientation(k):
    """Suy huong tu tin hieu tren mat va do rong vai.

    Thay mui + it nhat mot mat  -> chinh dien.
    Thay hai tai ma khong thay mui/mat -> quay lung.
    Chi thay mot ben tai/mat, vai hep -> nhin nghieng.
    """
    face_front = _ok(k, NOSE) and (_ok(k, L_EYE) or _ok(k, R_EYE))
    ears = int(_ok(k, L_EAR)) + int(_ok(k, R_EAR))
    eyes = int(_ok(k, L_EYE)) + int(_ok(k, R_EYE))

    sho_w = None
    if _ok(k, L_SHO) and _ok(k, R_SHO):
        sho_w = float(abs(k[L_SHO, 0] - k[R_SHO, 0]))
    torso_h = None
    sho, hip = _mid(k, L_SHO, R_SHO), _mid(k, L_HIP, R_HIP)
    if sho is not None and hip is not None:
        torso_h = float(np.linalg.norm(np.asarray(hip) - np.asarray(sho)))
    ratio = (sho_w / torso_h) if (sho_w and torso_h and torso_h > 1e-6) else None

    if face_front and eyes == 2:
        return "front"
    if ears == 2 and not _ok(k, NOSE) and eyes == 0:
        return "back"
    if ratio is not None and ratio < 0.45 and eyes <= 1:
        return "side"
    if face_front:
        return "front"
    if eyes <= 1 and ears <= 1 and _ok(k, L_SHO) != _ok(k, R_SHO):
        return "side"
    if not _ok(k, NOSE) and eyes == 0:
        return "back"
    return "unknown"


# ----------------------------------------------------------------- tu the
def _posture(k, sho, hip, torso_deg):
    """Phan biet dung / ngoi / nam bang ti le hinh hoc, khong dung model them.

    nam    : than gan nam ngang
    ngoi   : dau goi gap ro (hip-knee gan ngang, knee cao gan hip)
    dung   : than doc va chan thang
    """
    if torso_deg is not None and torso_deg > 55:
        return "lying"
    if sho is None or hip is None:
        return "unknown"

    torso = float(np.linalg.norm(np.asarray(hip) - np.asarray(sho)))
    if torso < 1e-6:
        return "unknown"

    knee = _mid(k, L_KNE, R_KNE)
    ankle = _mid(k, L_ANK, R_ANK)
    if knee is None:
        # Khong thay chan: doan theo than. Anh chan dung nua nguoi rat pho bien.
        return "standing" if (torso_deg or 0) < 30 else "unknown"

    hip_knee = float(abs(knee[1] - hip[1])) / torso
    horiz = float(abs(knee[0] - hip[0])) / torso

    if hip_knee < 0.55 and horiz > 0.35:
        return "sitting"
    if ankle is not None:
        knee_ank = float(abs(ankle[1] - knee[1])) / torso
        if hip_knee < 0.6 and knee_ank > 0.8:
            return "sitting"
        if hip_knee > 0.8 and knee_ank > 0.7:
            return "standing"
    return "standing" if hip_knee > 0.75 else "unknown"


# ------------------------------------------------------------- chinh dien
def _body_frontality(k, orient, torso_deg):
    """0..1. Gop huong + do can doi vai/hong + do doc cua than."""
    base = {"front": 1.0, "side": 0.35, "back": 0.0}.get(orient, 0.5)

    sym = 0.5
    if _ok(k, L_SHO) and _ok(k, R_SHO) and _ok(k, L_HIP) and _ok(k, R_HIP):
        sho_w = abs(k[L_SHO, 0] - k[R_SHO, 0])
        hip_w = abs(k[L_HIP, 0] - k[R_HIP, 0])
        if sho_w > 1e-6:
            sym = float(min(1.0, hip_w / sho_w))
    upright = 1.0 if torso_deg is None else max(0.0, 1.0 - abs(torso_deg) / 60.0)
    return float(max(0.0, min(1.0, 0.55 * base + 0.20 * sym + 0.25 * upright)))


# -------------------------------------------------------- khop voi khuon mat
def match_face(kps, faces_px):
    """Tim face nam trong vung dau cua nguoi nay -> tra ve fidx hoac None.

    faces_px: list (fidx, x1, y1, x2, y2) theo pixel.
    Dung tam cac diem mat/mui/tai lam moc, khong can IoU chinh xac.
    """
    k = np.asarray(kps, np.float32)
    pts = [k[i, :2] for i in (NOSE, L_EYE, R_EYE, L_EAR, R_EAR) if _ok(k, i)]
    if not pts or not faces_px:
        return None
    c = np.mean(pts, axis=0)
    best, best_d = None, None
    for fidx, x1, y1, x2, y2 in faces_px:
        if x1 <= c[0] <= x2 and y1 <= c[1] <= y2:
            fc = np.array([(x1 + x2) / 2.0, (y1 + y2) / 2.0], np.float32)
            d = float(np.linalg.norm(c - fc))
            if best_d is None or d < best_d:
                best, best_d = fidx, d
    return best
