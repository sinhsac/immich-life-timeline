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
_LIP_TOP, _LIP_BOT = 51, 57          # dinh moi tren / day moi duoi, vien NGOAI
_LIP_IN_TOP, _LIP_IN_BOT = 62, 66    # vien TRONG -> do ho cua mieng

# Duoi nguong nay thi 68 diem quanh mieng qua nhieu de suy bieu cam. Cung ly do
# khien EAR khong dang tin o mat nho: mot con mat rong 16px chi co 6 diem mo ta.
SMILE_MIN_EYE_PX = 26.0

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


def smile_from_68(lmk, eye_px=None, aspect=1.0):
    """0..1 "dang cuoi", suy tu 68 diem. None neu khong du tin cay.

    VI SAO CAN: moi chi so con lai trong module nay do MAT SACH KY THUAT — chinh
    dien, net, sang deu. Xep hang bang chung thi anh the va selfie dung yen luon
    thang, con anh dang cuoi ngoai dau lai thi bi loai. Mot video ky niem xep
    hang nhu vay thi kho dep, bat ke buoc dung muot den dau.

    KHONG can model moi: 1k3d68 da tra ve day du 68 diem va chung DA NAM trong
    fp_face.lmk68 / fp_vface.lmk68. Day thuan la doc lai.

    Cach do, trong he toa do CUA CHINH CAI MIENG nen khong phu thuoc roll:
      u  truc ngang = huong tu khoe trai (48) sang khoe phai (54)
      v  truc doc, vuong goc u, chieu duong huong xuong

      curl   than moi nam THAP hon duong noi hai khoe bao nhieu. Cuoi thi hai
             khoe bi keo len, nen than moi tut xuong tuong doi -> curl duong.
             Meu thi nguoc lai -> curl am. Day la tin hieu chac nhat.
      width  be rong mieng / khoang cach hai mat. Mieng thuong xap xi bang
             khoang cach hai mat; cuoi rong thi vuot len 1.2-1.4.
      open   do ho vien trong moi -> cuoi ho mieng / cuoi to.

    Ca 'width' va 'open' deu chia cho KHOANG CACH HAI MAT, khong chia cho be rong
    mieng. Chia cho be rong mieng thi mieng hep tu nhien duoc cong diem ho, va
    mot khuon mat meu (mieng hep) leo len tren mot khuon mat trung tinh — do la
    loi that da gap khi thu.

    Diem chi di tu 0 len: meu va trung tinh deu ra ~0. No do "cuoi bao nhieu",
    khong do sac thai am duong.

    CANH BAO ve hieu chuan: cac moc so duoi day dua tren ty le nhan trac khuon
    mat, KHONG phai do tu tap du lieu co nhan. Chung dung de XEP HANG trong cung
    mot thu vien, dung doc ra thanh "nguoi nay cuoi 0.8". Gia tri tho duoc luu
    nguyen vao db de xem lai vai chuc anh roi hieu chuan lai neu can.

    aspect = img_w/img_h. Bat buoc khi doc lai tu db: lmk68 luu chuan hoa x/w va
    y/h RIENG nhau, nen voi anh khong vuong thi he toa do bi keo mot chieu — goc
    va ty le tinh tren do la sai. Truyen aspect de dua ve dang doanh. Luc index
    thi toa do con la pixel, aspect=1.0.
    """
    if lmk is None:
        return None
    p = np.asarray(lmk, np.float32)
    if p.ndim != 2 or p.shape[0] < 68:
        return None
    if eye_px is not None and float(eye_px) < SMILE_MIN_EYE_PX:
        return None
    p = p[:, :2].astype(np.float32).copy()
    if aspect and aspect != 1.0:
        p[:, 0] *= float(aspect)

    left, right = p[_L_MOUTH], p[_R_MOUTH]
    u = right - left
    width = float(np.hypot(u[0], u[1]))
    if width < 1e-6:
        return None
    u = u / width
    v = np.array([-u[1], u[0]], np.float32)     # phap tuyen; dau se chuan sau
    mid = (left + right) / 2.0

    # Chieu duong cua v phai la "xuong duoi mat nguoi". Lay day moi duoi (57) lam
    # moc: no luon nam duoi duong noi hai khoe, bat ke anh bi xoay bao nhieu.
    if float(np.dot(p[_LIP_BOT] - mid, v)) < 0:
        v = -v

    def proj_v(i):
        return float(np.dot(p[i] - mid, v))

    eye_l, eye_r = p[_L_EYE].mean(0), p[_R_EYE].mean(0)
    inter = float(np.linalg.norm(eye_r - eye_l))

    def unit(x, lo, hi):
        return max(0.0, min(1.0, (x - lo) / (hi - lo)))

    curl_t = unit((proj_v(_LIP_TOP) + proj_v(_LIP_BOT)) / 2.0 / width,
                  0.010, 0.110)
    gap = abs(proj_v(_LIP_IN_BOT) - proj_v(_LIP_IN_TOP))
    if inter <= 1e-6:
        # Khong doc duoc khoang cach hai mat: chi con curl la dang tin, do la
        # dai luong duy nhat khong can moc so sanh ben ngoai cai mieng.
        return float(curl_t)
    open_t = unit(gap / inter, 0.03, 0.30)
    w_t = unit(width / inter, 1.00, 1.32)
    return float(max(0.0, min(1.0,
                              0.45 * curl_t + 0.40 * w_t + 0.15 * open_t)))


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


def pose_from_kps5(kps):
    """Uoc luong (yaw, pitch, roll) chi tu 5 diem, KHONG dung model nao.

    Dung cho frame video: mot clip 3 giay lay mau 2 fps la 6 frame, moi frame co
    the co vai mat — chay them 1k3d68 cho tung mat la nhan doi chi phi cua stage
    dat nhat. Ma de LOC doan (mat co huong vao may khong) thi khong can do chinh
    xac den do.

    Cach tinh, sau khi quay khung ve cho hai mat nam ngang:
      roll   goc that cua duong noi hai mat, cai nay chinh xac
      yaw    mui lech khoi trung diem hai mat bao nhieu, chia cho khoang cach
             hai mat. Chinh dien ~0, quay 30 do thi ~0.35 -> nhan 80 ra do
      pitch  mui nam o dau giua duong mat va duong mieng. Chinh dien ~0.5

    Hai gia tri sau la UOC LUONG, dung de xep hang va loc, dung dem ra so do.
    """
    k = np.asarray(kps, np.float32)
    if k.shape[0] < 5:
        return 0.0, 0.0, 0.0
    le, re, nose = k[0], k[1], k[2]
    mouth = (k[3] + k[4]) / 2.0
    d = re - le
    dist = float(np.hypot(d[0], d[1]))
    if dist < 1e-3:
        return 0.0, 0.0, 0.0
    roll = float(np.degrees(np.arctan2(d[1], d[0])))

    # quay ve he toa do cua khuon mat -> yaw/pitch khong bi roll lam nhieu
    c, s = np.cos(np.radians(-roll)), np.sin(np.radians(-roll))
    rot = np.array([[c, -s], [s, c]], np.float32)
    eye_mid = (le + re) / 2.0
    n = rot @ (nose - eye_mid)
    m = rot @ (mouth - eye_mid)

    yaw = float(np.clip(n[0] / dist * 80.0, -90.0, 90.0))
    span = float(m[1])
    if abs(span) < 1e-3:
        pitch = 0.0
    else:
        pitch = float(np.clip((0.5 - n[1] / span) * 90.0, -90.0, 90.0))
    return yaw, pitch, roll


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
