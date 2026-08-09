#!/usr/bin/env python3
"""Chuan bi model vao MODEL_DIR truoc khi build/deploy.

    python tools/fetch_models.py --all --out ./models

  --face  tai buffalo_l qua insightface (~300MB, gom 5 file onnx)
  --body  export yolov8n-pose.onnx tu ultralytics (~6MB)

Nen chay mot lan tren may dev roi copy thu muc models/ vao image hoac vao
PersistentVolume, de container khong phai tai lai moi lan restart.
"""
import argparse
import os
import shutil
from pathlib import Path


def fetch_face(out, name="buffalo_l"):
    os.environ["INSIGHTFACE_HOME"] = str(out)
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name=name, root=str(out),
                       providers=["CPUExecutionProvider"],
                       allowed_modules=["detection", "landmark_3d_68", "genderage"])
    app.prepare(ctx_id=-1, det_size=(320, 320))
    d = out / "models" / name
    files = sorted(p.name for p in d.glob("*.onnx")) if d.exists() else []
    print(f"[face] {d}\n       {', '.join(files) or 'khong thay file onnx'}")
    if "1k3d68.onnx" not in files:
        print("       CANH BAO: thieu 1k3d68.onnx -> stage landmarks se loi")


def fetch_body(out, weights="yolov8n-pose.pt", imgsz=640):
    out.mkdir(parents=True, exist_ok=True)
    dst = out / "yolov8n-pose.onnx"
    if dst.exists():
        print(f"[body] da co {dst}")
        return
    try:
        from ultralytics import YOLO
    except ImportError:
        raise SystemExit(
            "Can ultralytics de export:\n"
            "  pip install ultralytics\n"
            "Hoac tai san file yolov8n-pose.onnx roi dat vao " + str(out))
    m = YOLO(weights)
    path = m.export(format="onnx", imgsz=imgsz, opset=12,
                    simplify=False, dynamic=False, nms=False)
    shutil.copyfile(path, dst)
    size = dst.stat().st_size / 1e6
    print(f"[body] {dst}  ({size:.1f} MB, input {imgsz}x{imgsz})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.environ.get("MODEL_DIR", "./models"))
    ap.add_argument("--face", action="store_true")
    ap.add_argument("--body", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--face-name", default="buffalo_l")
    ap.add_argument("--imgsz", type=int, default=640)
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if a.all or a.face:
        fetch_face(out, a.face_name)
    if a.all or a.body:
        fetch_body(out, imgsz=a.imgsz)
    if not (a.all or a.face or a.body):
        ap.error("chon --face, --body hoac --all")
    print(f"\nxong. dat MODEL_DIR={out}")


if __name__ == "__main__":
    main()
