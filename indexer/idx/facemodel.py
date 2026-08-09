"""Chay 1k3d68 (68 diem 3D -> head pose) + genderage tren bbox co san.

Bo qua detection (SCRFD) va recognition (ArcFace) — hai model ton nhat — vi
bbox va embedding da lay tu Postgres cua Immich. Nho vay stage nay nhanh hon
nhieu lan so voi full pipeline, va RAM chi khoang 300-400MB.

Model buffalo_l lay tu MODEL_DIR (mount vao container). Neu chua co, insightface
se tu tai — nen mount san de container khong phai tai lai moi lan restart.
"""
import os
from pathlib import Path

import numpy as np

from .metrics import ear_from_68, kps5_from_68

# 'detection' phai co trong danh sach vi FaceAnalysis assert no ton tai,
# nhung o che do nay ta khong bao gio goi den no.
LMK_MODULES = ["detection", "landmark_3d_68", "genderage"]


class FaceLandmarker:
    def __init__(self, s):
        self.s = s
        os.environ.setdefault("INSIGHTFACE_HOME", s.model_dir)
        root = Path(s.model_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.app = self._build()
        self.lmk = self.app.models.get("landmark_3d_68")
        self.ga = self.app.models.get("genderage")
        if self.lmk is None:
            raise SystemExit(
                f"Bo model {s.face_model} khong co landmark_3d_68.\n"
                f"Kiem tra {root}/models/{s.face_model}/1k3d68.onnx")

    def _build(self):
        import onnxruntime as ort
        from insightface.app import FaceAnalysis
        ort.set_default_logger_severity(3)
        providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                     if self.s.use_gpu else ["CPUExecutionProvider"])
        app = FaceAnalysis(name=self.s.face_model, root=self.s.model_dir,
                           providers=providers, allowed_modules=LMK_MODULES)
        # det_size nho vi detection khong duoc dung, chi de thoa man prepare()
        app.prepare(ctx_id=0 if self.s.use_gpu else -1, det_size=(320, 320))
        for m in app.models.values():
            sess = getattr(m, "session", None)
            if sess is not None:
                try:
                    sess.set_providers(providers)
                except Exception:                        # noqa: BLE001
                    pass
        return app

    def __call__(self, img, bbox_px):
        """bbox_px = (x1,y1,x2,y2) theo pixel cua img.

        Cach 1k3d68 hoat dong: crop tu bbox voi scale = input_size/(max(w,h)*1.5),
        du doan 68 diem 3D chuan hoa, roi anh xa nguoc bang affine dao. Pose lay
        tu estimate_affine_matrix_3d23d(mean_lmk, pred) -> P2sRt -> matrix2angle,
        tuc khop affine 3D-3D voi hinh dang trung binh cua model, khong phai PnP
        co hieu chuan camera. Gan dung, du cho viec loc goc.

        Tra ve dict hoac None.
        """
        from insightface.app.common import Face
        face = Face(bbox=np.asarray(bbox_px, np.float32), det_score=1.0)
        try:
            pred = self.lmk.get(img, face)
        except Exception:                                # noqa: BLE001
            return None
        if pred is None or len(pred) < 68:
            return None
        age = None
        if self.ga is not None:
            try:
                self.ga.get(img, face)
                age = float(getattr(face, "age", 0) or 0) or None
            except Exception:                            # noqa: BLE001
                pass
        yaw, pitch, roll = pose_of(face)
        lmk = np.asarray(pred, np.float32)
        return {"kps": kps5_from_68(lmk), "lmk68": lmk, "age": age,
                "yaw": yaw, "pitch": pitch, "roll": roll,
                "ear": ear_from_68(lmk)}

    def close(self):
        """Tra RAM lai truoc khi stage sau load model khac."""
        self.lmk = self.ga = None
        self.app = None


def pose_of(face):
    """insightface tra ve pose theo thu tu (pitch, yaw, roll)."""
    p = getattr(face, "pose", None)
    if p is None:
        return 0.0, 0.0, 0.0
    pitch, yaw, roll = (float(x) for x in p)
    return yaw, pitch, roll
