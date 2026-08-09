"""Man hinh 4: align tung frame roi goi ffmpeg dung video.

Hai buoc:
  frames    doc anh preview, align ve cung vi tri mat, ghi f_00001.jpg...
  encoding  ffmpeg ghep chuoi anh thanh mp4

Chu de "hanh trinh mot nguoi": vi moi frame da align nen mat nam dung mot cho,
xem lai giong mot khuon mat lon dan chu khong phai slideshow nhay loan.

Chi cho phep MOT render chay cung luc — may 8GB dung chung voi Immich.
Nhan date len anh bang cv2.putText thay vi drawtext cua ffmpeg, de khoi phai
mount font vao container.
"""
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import cv2

from . import media, projects
from .db import rows
from .settings import get

DEFAULT_OPTIONS = {
    "size": 900,          # CANH DAI cua khung ra
    "aspect": "4:3",      # ty le khung, xem media.ASPECTS
    # Khoang cach hai mat / chieu ngang khung. Day la tham so quyet dinh khung
    # anh lay rong hay hep: 0.55 = chan dung sat mat, 0.12 = thay ca nguoi.
    "face_frac": 0.12,
    "eye_y": 0.33,        # vi tri mat theo chieu doc
    "anchor_x": 0.5,      # vi tri mat theo chieu ngang
    "fill": "crop",       # 'crop' phu kin khung | 'blur' tron anh + nen mo
    "level": True,        # xoay cho hai mat nam ngang
    "fps": 6,             # so anh moi giay
    "smooth": "blend",    # 'none' | 'blend'
    "out_fps": 30,        # chi dung khi smooth='blend'
    "label": "year",      # 'none' | 'year' | 'month' | 'date'
    "eye_dx": None,       # cu, = face_frac/2. Nhan de client cu khong loi
    "crf": 20,
    "preset": "medium",
    "jpeg_quality": 93,
}

_lock = threading.Lock()
_running = {"id": None}


def options(over=None):
    o = dict(DEFAULT_OPTIONS)
    over = over or {}
    for k, v in over.items():
        if k in DEFAULT_OPTIONS and v is not None:
            o[k] = v
    # client cu chi biet eye_dx: interocular = 2*eye_dx*size -> face_frac
    if over.get("eye_dx") is not None and over.get("face_frac") is None:
        o["face_frac"] = float(over["eye_dx"]) * 2.0
    o.pop("eye_dx", None)

    o["size"] = max(256, min(1440, int(o["size"])))
    if o["aspect"] not in media.ASPECTS:
        o["aspect"] = "4:3"
    o["out_w"], o["out_h"] = media.frame_size(o["size"], o["aspect"])
    o["face_frac"] = max(0.04, min(0.70, float(o["face_frac"])))
    o["eye_y"] = max(0.15, min(0.80, float(o["eye_y"])))
    o["anchor_x"] = max(0.2, min(0.8, float(o["anchor_x"])))
    o["fill"] = o["fill"] if o["fill"] in ("crop", "blur") else "crop"
    o["level"] = bool(o["level"])
    o["fps"] = max(1, min(30, int(o["fps"])))
    o["out_fps"] = max(o["fps"], min(60, int(o["out_fps"])))
    o["crf"] = max(14, min(32, int(o["crf"])))
    if o["smooth"] not in ("none", "blend"):
        o["smooth"] = "none"
    if o["label"] not in ("none", "year", "month", "date"):
        o["label"] = "none"
    return o


def current():
    return _running["id"]


def start(project_id, over=None):
    """Tao render moi va chay o thread nen. Tra ve render_id."""
    s = get()
    o = options(over)
    fr = projects.frames(project_id)
    if not fr:
        raise ValueError("chua co frame nao duoc chon")
    if _running["id"] is not None:
        raise RuntimeError(f"dang render #{_running['id']}, doi xong roi chay tiep")

    with rows() as (c, cur):
        cur.execute(
            f"INSERT INTO {s.table('render')}(project_id,status,options,n_total)"
            f" VALUES(%s,'queued',%s::jsonb,%s) RETURNING id",
            (project_id, json.dumps(o), len(fr)))
        rid = cur.fetchone()["id"]
        c.commit()

    th = threading.Thread(target=_worker, args=(rid, project_id, o, fr),
                          name=f"render-{rid}", daemon=True)
    th.start()
    return rid


def status(render_id):
    s = get()
    with rows() as (c, cur):
        cur.execute(f"SELECT * FROM {s.table('render')} WHERE id=%s", (render_id,))
        r = cur.fetchone()
        c.rollback()
    if not r:
        raise KeyError(f"khong co render {render_id}")
    for k in ("started_at", "finished_at"):
        if r.get(k) is not None:
            r[k] = r[k].isoformat()
    r["pct"] = round(100.0 * (r["n_done"] or 0) / max(1, r["n_total"] or 1), 1)
    return r


def video_path(render_id):
    r = status(render_id)
    if r["status"] != "done" or not r["video_path"]:
        return None
    p = Path(r["video_path"])
    return p if p.exists() else None


def listing(project_id):
    s = get()
    with rows() as (c, cur):
        cur.execute(
            f"SELECT id,status,n_total,n_done,duration_s,started_at,err"
            f" FROM {s.table('render')} WHERE project_id=%s"
            f" ORDER BY id DESC LIMIT 20", (project_id,))
        out = []
        for r in cur.fetchall():
            r["started_at"] = r["started_at"].isoformat()
            out.append(r)
        c.rollback()
    return out


# ---------------------------------------------------------------- worker
def _worker(rid, project_id, o, fr):
    s = get()
    with _lock:
        _running["id"] = rid
    work = s.renders / str(rid)
    frames_dir = work / "frames"
    try:
        _set(rid, status="frames")
        shutil.rmtree(work, ignore_errors=True)
        frames_dir.mkdir(parents=True, exist_ok=True)

        n_ok = 0
        t0 = time.time()
        for r in fr:
            if _aligned(r, frames_dir, n_ok + 1, o, s):
                n_ok += 1
                if n_ok % 10 == 0:
                    _set(rid, n_done=n_ok)
        _set(rid, n_done=n_ok)
        if n_ok < 2:
            raise RuntimeError(f"chi align duoc {n_ok} frame, khong du dung video")
        print(f"[render {rid}] {n_ok} frame trong {time.time() - t0:.0f}s")

        _set(rid, status="encoding")
        out = work / "video.mp4"
        dur = _encode(frames_dir, out, o, s)
        _set(rid, status="done", video_path=str(out), duration_s=dur,
             finished=True)
        print(f"[render {rid}] xong: {out} ({dur:.1f}s video)")
    except Exception as e:                                   # noqa: BLE001
        print(f"[render {rid}] LOI: {e}")
        _set(rid, status="error", err=str(e)[:500], finished=True)
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)
        with _lock:
            _running["id"] = None


def _aligned(row, out_dir, n, o, s):
    img, tmp = media.load(row["asset_id"], row["preview_path"], s)
    if img is None:
        return False
    try:
        h, w = img.shape[:2]
        kps = media.kps_from_blob(row["kps"], w, h)
        if kps is None:
            return False
        frame = media.anchor_frame(
            img, kps, o["out_w"], o["out_h"], face_frac=o["face_frac"],
            anchor_x=o["anchor_x"], eye_y=o["eye_y"], level=o["level"],
            fill=o["fill"])
        if frame is None:
            return False
        if o["label"] != "none":
            _label(frame, row["taken_at"], o)
        return media.imwrite(out_dir / f"f_{n:05d}.jpg", frame, o["jpeg_quality"])
    finally:
        media.release(tmp)


def _label(frame, taken_at, o):
    """Nhan thoi gian goc duoi. Chi ASCII nen HERSHEY du dung, khong can font."""
    if taken_at is None:
        return
    iso = taken_at.isoformat() if hasattr(taken_at, "isoformat") else str(taken_at)
    text = {"year": iso[:4], "month": iso[:7], "date": iso[:10]}[o["label"]]
    fh, fw = frame.shape[:2]
    scale = min(fw, fh) / 720.0 * 1.1
    th = max(1, int(round(2 * scale)))
    (tw, tht), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
    x = int((fw - tw) / 2)
    y = int(fh - max(12, fh * 0.045))
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (0, 0, 0), th + 3, cv2.LINE_AA)
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (255, 255, 255), th, cv2.LINE_AA)


def _encode(frames_dir, out, o, s):
    n = len(list(frames_dir.glob("f_*.jpg")))
    vf = ["scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    if o["smooth"] == "blend" and o["out_fps"] > o["fps"]:
        # framerate= noi suy co pha tron, re hon minterpolate nhieu lan
        vf.insert(0, f"framerate=fps={o['out_fps']}")
    cmd = [
        s.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", str(o["fps"]),
        "-start_number", "1",
        "-i", str(frames_dir / "f_%05d.jpg"),
        "-vf", ",".join(vf),
        "-c:v", "libx264", "-preset", o["preset"], "-crf", str(o["crf"]),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-threads", str(max(1, s.ffmpeg_threads)),
        "-an", str(out),
    ]
    print("[render] " + " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg loi: " + (p.stderr or "")[-400:])
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("ffmpeg khong tao ra file")
    return n / float(o["fps"])


def _set(rid, finished=False, **kw):
    s = get()
    sets, vals = [], []
    for k, v in kw.items():
        sets.append(f"{k}=%s")
        vals.append(v)
    if finished:
        sets.append("finished_at=now()")
    if not sets:
        return
    vals.append(rid)
    with rows() as (c, cur):
        cur.execute(f"UPDATE {s.table('render')} SET {','.join(sets)} WHERE id=%s",
                    vals)
        c.commit()


def ffmpeg_ok():
    s = get()
    try:
        p = subprocess.run([s.ffmpeg, "-version"], capture_output=True, text=True)
        if p.returncode == 0:
            return True, p.stdout.splitlines()[0]
        return False, (p.stderr or "")[:200]
    except FileNotFoundError:
        return False, f"khong tim thay {s.ffmpeg} trong PATH"
