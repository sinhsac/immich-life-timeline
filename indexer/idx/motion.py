"""Uoc luong CAMERA dich chuyen bao nhieu giua hai frame lay mau.

VI SAO CAN: clips.motion_of() do khuon mat dich chuyen bao nhieu trong khung, roi
TRU DIEM theo con so do. Nhung dich chuyen trong khung la tong cua hai thu khac
han nhau ve y nghia:

  camera lac    tay rung, lia may vong vo -> doan xem met, dung la nen tru diem
  chu the dong  nhay len, cung ly, be quay lai cuoi -> dung la KHOANH KHAC,
                dang le phai duoc CONG diem

Gop hai cai lam mot nghia la mot cu nhay dep bi tru diem y nhu mot cu rung tay.
Bo chi so cu khong phan biet duoc, va do la ly do doan hanh dong dep hay bi bo.

Cach tach: uoc luong dich chuyen TOAN CUC cua khung bang optical flow thua roi
lay ra:

    dich chuyen cua mat trong khung = camera + chu the
    => chu the = dich chuyen cua mat - camera

Dung LK thua (goodFeaturesToTrack + calcOpticalFlowPyrLK) chu khong phai flow
day (Farneback): ta chi can MOT vector cho ca khung, khong can truong vector.
Thua thi re hon vai chuc lan.

Lay TRUNG VI cua cac vector diem, khong lay trung binh: chinh nhung diem nam tren
nguoi dang chuyen dong se cho vector khac han, va trung binh se bi chung keo theo
— tuc la mot phan chuyen dong cua chu the bi tinh nham thanh camera, dung cai ma
module nay ton tai de tranh.
"""
import cv2
import numpy as np

# Canh dai cua anh dung de tinh flow. Nho hon frame that nhieu: ta chi can mot
# vector toan cuc, va 320px du de do dich chuyen chinh xac den ~1px cua ban goc.
FLOW_SIDE = 320

# So diem goc theo doi. 60 diem la du de trung vi on dinh ma van re.
MAX_POINTS = 60

# Duoi bay nhieu diem theo doi thanh cong thi khong ket luan gi: canh troi, canh
# tuong tron, hoac chuyen canh cat cung.
MIN_TRACKED = 8

_LK = dict(winSize=(21, 21), maxLevel=3,
           criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))


def small_gray(img, side=FLOW_SIDE):
    """Anh xam thu nho, kem ty le so voi anh vao."""
    h, w = img.shape[:2]
    m = max(h, w)
    if m <= 0:
        return None, 1.0
    r = min(1.0, float(side) / float(m))
    if r < 1.0:
        img = cv2.resize(img, (max(1, int(round(w * r))),
                              max(1, int(round(h * r)))),
                         interpolation=cv2.INTER_AREA)
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img, r


def shift(prev, cur):
    """Camera dich chuyen (dx, dy) giua hai anh xam, theo pixel CUA ANH XAM DO.

    None neu khong ket luan duoc. Phia goi phai chia lai cho ty le thu nho de doi
    ve toa do cua frame that.
    """
    if prev is None or cur is None or prev.shape != cur.shape:
        return None
    pts = cv2.goodFeaturesToTrack(prev, maxCorners=MAX_POINTS,
                                  qualityLevel=0.01, minDistance=8,
                                  blockSize=7)
    if pts is None or len(pts) < MIN_TRACKED:
        return None
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, **_LK)
    if nxt is None or st is None:
        return None
    ok = st.reshape(-1).astype(bool)
    if int(ok.sum()) < MIN_TRACKED:
        return None
    d = (nxt.reshape(-1, 2)[ok] - pts.reshape(-1, 2)[ok])
    return float(np.median(d[:, 0])), float(np.median(d[:, 1]))


class CameraTracker:
    """Giu frame truoc, tra ve dich chuyen camera CHUAN HOA cho tung frame moi.

    Chuan hoa theo (w, h) cua frame that — cung he voi kps/bbox trong fp_vface,
    nen doc lai tu db khong phai biet kich thuoc frame goc.
    """

    def __init__(self, side=FLOW_SIDE):
        self.side = side
        self._prev = None
        self._scale = 1.0

    def step(self, img):
        """(cam_dx, cam_dy) chuan hoa 0..1, hoac (None, None).

        Frame dau tien luon tra ve (None, None): khong co gi de so.
        """
        g, r = small_gray(img, self.side)
        if g is None:
            return None, None
        out = (None, None)
        if self._prev is not None and r > 0:
            d = shift(self._prev, g)
            if d is not None:
                h, w = img.shape[:2]
                # d tinh tren anh nho -> chia r de ve pixel cua frame that,
                # roi chia w/h de chuan hoa.
                out = (d[0] / r / float(max(w, 1)),
                       d[1] / r / float(max(h, 1)))
        self._prev = g
        self._scale = r
        return out
