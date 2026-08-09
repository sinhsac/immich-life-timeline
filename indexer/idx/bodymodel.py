"""YOLOv8n-pose tren onnxruntime: detect nguoi + 17 keypoint COCO trong 1 luot.

Chon yolov8n (nano) vi may dich 8GB RAM / i5 khong GPU:
  model    ~6MB onnx
  RAM      ~200MB khi infer
  toc do   ~40-60ms/anh 640px, 2 thread

Output cua yolov8-pose khi export khong kem NMS: (1, 56, N) voi N=8400 o 640px.
56 = 4 (cx,cy,w,h) + 1 (conf nguoi) + 17*3 (x,y,conf moi keypoint).
Toa do o he pixel cua anh dau vao da letterbox, phai anh xa nguoc.
"""
from pathlib import Path

import cv2
import numpy as np

KPT_NAMES = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle")
NK = len(KPT_NAMES)


class BodyPose:
    def __init__(self, s):
        import onnxruntime as ort
        ort.set_default_logger_severity(3)
        path = Path(s.body_model)
        if not path.is_absolute():
            path = Path(s.model_dir) / s.body_model
        if not path.exists():
            raise SystemExit(
                f"Khong tim thay model body pose: {path}\n"
                f"Tao bang:  python tools/fetch_models.py --body\n"
                f"hoac tai yolov8n-pose.onnx roi dat vao {s.model_dir}")
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, s.onnx_threads)
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                     if s.use_gpu else ["CPUExecutionProvider"])
        self.sess = ort.InferenceSession(str(path), so, providers=providers)
        self.iname = self.sess.get_inputs()[0].name
        shape = self.sess.get_inputs()[0].shape
        dyn = not isinstance(shape[2], int)
        self.size = s.body_imgsz if dyn else int(shape[2])
        self.conf = s.body_conf
        self.iou = s.body_iou
        self.max_person = s.body_max
        print(f"  body model: {path.name} input={self.size} "
              f"providers={self.sess.get_providers()[0]}")

    # -------------------------------------------------------- pre / post
    def _letterbox(self, img):
        h, w = img.shape[:2]
        r = min(self.size / h, self.size / w)
        nh, nw = int(round(h * r)), int(round(w * r))
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((self.size, self.size, 3), 114, np.uint8)
        top, left = (self.size - nh) // 2, (self.size - nw) // 2
        canvas[top:top + nh, left:left + nw] = resized
        return canvas, r, left, top

    def __call__(self, img):
        """Tra ve list dict: bbox pixel, det, kps (NK,3) pixel + conf."""
        h, w = img.shape[:2]
        canvas, r, dx, dy = self._letterbox(img)
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None]
        blob = np.ascontiguousarray(blob, np.float32) / 255.0
        out = self.sess.run(None, {self.iname: blob})[0]

        pred = np.squeeze(out)
        if pred.ndim != 2:
            return []
        if pred.shape[0] < pred.shape[1]:      # (56, N) -> (N, 56)
            pred = pred.T
        if pred.shape[1] < 5 + NK * 3:
            return []

        scores = pred[:, 4]
        keep = scores >= self.conf
        if not keep.any():
            return []
        pred, scores = pred[keep], scores[keep]

        cx, cy, bw, bh = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
        boxes = np.stack([cx - bw / 2, cy - bh / 2, bw, bh], 1)   # xywh cho NMS
        idx = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(),
                               self.conf, self.iou)
        if idx is None or len(idx) == 0:
            return []
        idx = np.asarray(idx).reshape(-1)
        idx = idx[np.argsort(-scores[idx])][:self.max_person]

        res = []
        for i in idx:
            x1 = (cx[i] - bw[i] / 2 - dx) / r
            y1 = (cy[i] - bh[i] / 2 - dy) / r
            x2 = (cx[i] + bw[i] / 2 - dx) / r
            y2 = (cy[i] + bh[i] / 2 - dy) / r
            k = pred[i, 5:5 + NK * 3].reshape(NK, 3).astype(np.float32).copy()
            k[:, 0] = (k[:, 0] - dx) / r
            k[:, 1] = (k[:, 1] - dy) / r
            res.append({
                "bbox": (float(np.clip(x1, 0, w)), float(np.clip(y1, 0, h)),
                         float(np.clip(x2, 0, w)), float(np.clip(y2, 0, h))),
                "det": float(scores[i]),
                "kps": k,
            })
        return res

    def close(self):
        self.sess = None
