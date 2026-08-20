"""Lay anh preview cua mot asset.

Hai duong, uu tien duong 1:
  1. Doc thang file preview Immich da tao, qua volume mount read-only.
     Khong ton network, khong tao tai cho Immich server.
  2. Tai qua HTTP API, cache tam vao dia roi xoa. Cham hon nhieu.
"""
import os
from pathlib import Path

import cv2
import numpy as np

_ANCHORS = ("thumbs", "encoded-video", "upload", "library")


class MediaReader:
    def __init__(self, s):
        self.s = s
        self.root = s.media
        self.cache = Path(s.cache_dir)
        self._sess = None
        self.n_file = 0
        self.n_http = 0
        self.n_miss = 0
        if not self.root:
            self.cache.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ duong 1
    def resolve(self, preview_path):
        """Duong dan trong DB la duong dan TRONG container Immich, vd
        'upload/thumbs/<uid>/ab/cd/<id>-preview.jpeg'. Ghep lai voi MEDIA_ROOT.
        """
        if not (self.root and preview_path):
            return None
        p = Path(str(preview_path))
        if p.is_absolute() and p.exists():
            return p
        parts = p.parts
        cands = []
        for a in _ANCHORS:
            if a in parts:
                cands.append(self.root / Path(*parts[parts.index(a):]))
        # truong hop MEDIA_ROOT da tro thang vao thu muc upload
        for a in _ANCHORS:
            if a in parts:
                idx = parts.index(a)
                if idx + 1 < len(parts):
                    cands.append(self.root / Path(*parts[idx + 1:]))
        cands.append(self.root / p)
        for c in cands:
            if c.exists():
                return c
        return None

    # ------------------------------------------------------------ duong 2
    def _session(self):
        if self._sess is None:
            import requests
            self._sess = requests.Session()
            self._sess.headers.update({"x-api-key": self.s.immich_api_key,
                                       "Accept": "application/octet-stream"})
        return self._sess

    def _download(self, asset_id):
        base = self.s.immich_url.rstrip("/")
        if not base.endswith("/api"):
            base += "/api"
        url = f"{base}/assets/{asset_id}/thumbnail?size=preview"
        r = self._session().get(url, timeout=self.s.http_timeout)
        if r.status_code == 404:
            url = f"{base}/asset/thumbnail/{asset_id}?format=JPEG"
            r = self._session().get(url, timeout=self.s.http_timeout)
        r.raise_for_status()
        dst = self.cache / f"{asset_id}.jpg"
        dst.write_bytes(r.content)
        return dst

    # ------------------------------------------------------------ public
    def read(self, asset_id, preview_path, max_side=0):
        """Tra ve (img BGR, tmp_path hoac None). Goi ham release() sau khi xong."""
        p = self.resolve(preview_path)
        tmp = None
        if p is None:
            if not (self.s.immich_url and self.s.immich_api_key):
                self.n_miss += 1
                return None, None
            try:
                p = self._download(asset_id)
                tmp = p
                self.n_http += 1
            except Exception:                            # noqa: BLE001
                self.n_miss += 1
                return None, None
        else:
            self.n_file += 1
        img = _imread(p)
        if img is None:
            self.release(tmp)
            self.n_miss += 1
            return None, None
        if max_side:
            img = downscale(img, max_side)
        return img, tmp

    @staticmethod
    def release(tmp):
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def stats(self):
        return (f"anh doc: {self.n_file} tu file, {self.n_http} qua http, "
                f"{self.n_miss} khong doc duoc")


def _imread(path):
    """cv2.imread khong xu ly duoc duong dan unicode tren Windows."""
    try:
        buf = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def downscale(img, max_side):
    """Anh preview cua Immich thuong 1440px. Ha xuong de tiet kiem RAM/CPU.

    Bbox trong db la toa do chuan hoa 0..1 nen resize khong lam sai lech gi.
    """
    h, w = img.shape[:2]
    m = max(h, w)
    if m <= max_side:
        return img
    r = max_side / float(m)
    return cv2.resize(img, (int(round(w * r)), int(round(h * r))),
                      interpolation=cv2.INTER_AREA)


def video_info(path):
    """(fps, n_frame, dur_ms, w, h) — doc header, khong decode frame nao."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        dur = int(round(n / fps * 1000)) if fps > 0 and n > 0 else 0
        return fps, n, dur, w, h
    finally:
        cap.release()


def video_frames(path, fps=2.0, max_side=960, max_seconds=0.0):
    """Lay mau frame cua video: yield (t_ms, img BGR).

    fps=0 nghia la LAY TAT CA frame. Dat mot con so thi bo qua frame giua bang
    grab() — grab chi doc va giai ma toi thieu de nhay tiep, khong dung ra anh
    BGR, nen re hon retrieve() nhieu lan. Day la khac biet giua "quet 2 frame moi
    giay" ton bang 1/15 va ton bang 1 cua "decode het roi bo".

    Khong dung cap.set(POS_FRAMES) de nhay: voi codec co B-frame, seek phai
    decode lai tu keyframe gan nhat nen doc tuan tu con nhanh hon, va vi tri tra
    ve khong dang tin.
    """
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return
    try:
        src = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        step = 1
        if fps and fps > 0 and src > 0:
            step = max(1, int(round(src / float(fps))))
        i = 0
        limit_ms = max_seconds * 1000.0 if max_seconds else 0.0
        while True:
            if not cap.grab():
                break
            if i % step == 0:
                t_ms = (i / src * 1000.0 if src > 0
                        else float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0))
                if limit_ms and t_ms > limit_ms:
                    break
                ok, img = cap.retrieve()
                if ok and img is not None:
                    yield int(round(t_ms)), downscale(img, max_side)
            i += 1
    finally:
        cap.release()
