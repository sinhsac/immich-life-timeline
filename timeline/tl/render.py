"""Man hinh 4: dung video tu danh sach anh da chon.

Hai che do, khac han nhau ve ban chat:

  mode='story'  (mac dinh) Ke chuyen. Moi anh la mot SHOT co do dai rieng: anh
                chu dao cua chuong duoc giu lau, anh phu di nhanh; cac shot chong
                mo len nhau; zoom cham quanh diem neo mat; nhan chuong hien ra roi
                tan di; mo man tu den va dong man ve den. Frame duoc sinh o dung
                fps dau ra roi day THANG vao stdin cua ffmpeg — khong ghi jpg ra
                dia, khong encode hai lan.

  mode='flip'   Cach cu: mot anh mot frame, deu tang tang, ffmpeg ghep chuoi jpg.
                Giu lai vi no re va co nguoi thich kieu "flipbook" that.

Vi sao khong dung filter xfade/zoompan cua ffmpeg: chuoi xfade cho 60 clip sinh
ra filtergraph khong lo, ton RAM va rat kho suy ra thoi luong chinh xac. Sinh
frame bang numpy thi moi frame la mot phep warpAffine — de kiem soat, de tinh
dung so frame, va van du nhanh vi anh preview chi 1440px.

Chi cho phep MOT render chay cung luc — may 8GB dung chung voi Immich.
"""
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import beats as beatmod
from . import media, music, projects, story, textdraw
from .db import rows
from .settings import get

DEFAULT_OPTIONS = {
    # ---------------- khung hinh (dung cho ca hai che do) ----------------
    "size": 900,          # CANH DAI cua khung ra
    "aspect": "4:3",      # ty le khung, xem media.ASPECTS
    # Khoang cach hai mat / chieu ngang khung. Tham so quyet dinh khung lay rong
    # hay hep: 0.55 = chan dung sat mat, 0.12 = thay ca nguoi va boi canh.
    "face_frac": 0.12,
    "eye_y": 0.33,        # vi tri mat theo chieu doc
    "anchor_x": 0.5,      # vi tri mat theo chieu ngang
    "fill": "crop",       # 'crop' phu kin khung | 'blur' tron anh + nen mo
    "level": True,        # xoay cho hai mat nam ngang
    # Video hai nguoi: khoang cach GIUA HAI NGUOI tinh theo chieu ngang khung.
    # Lon hon face_frac nhieu vi phai chua ca hai khuon mat, khong phai mot.
    "pair_frac": 0.30,

    # ---------------- cach ke ----------------
    "mode": "story",      # 'story' | 'flip'
    "out_fps": 24,        # fps that cua video khi mode='story'
    "motion": "subtle",   # none | subtle | normal | strong (zoom Ken Burns)
    "title": True,        # the tieu de mo dau: ten + khoang nam
    "title_text": None,   # de trong thi lay ten nguoi cua du an
    "title_seconds": 2.4,
    "chapter_card": True, # hien nhan chuong khi sang chuong moi
    "card_seconds": 1.8,
    "birth_year": None,   # co thi nhan chuong hien them "N tuoi"
    "arc": True,          # mo dau va ket thuc cham hon mot chut
    "intro_s": 0.8,       # mo man tu den
    "outro_s": 1.6,       # giu them roi dong man ve den
    "label": "none",      # nhan goc duoi: none|year|month|date

    # ---------------- tieng cua doan video ----------------
    # Tieng that cua tung doan, dat dung vi tri tren dong thoi gian. Anh tinh
    # khong co tieng nen giua cac doan la im lang — de khong bi giat cuc, tieng
    # duoc BAT DAU SOM va KEO DAI hon phan hinh (J-cut / L-cut trong dung phim).
    "audio": True,
    "audio_lead": 0.5,        # tieng vao truoc hinh bao nhieu giay
    "audio_tail": 0.8,        # tieng con lai sau khi hinh da cat
    "audio_fade_in": 0.35,
    "audio_fade_out": 0.6,
    "audio_gain": 0.0,        # dB
    "audio_normalize": True,  # can muc giua cac doan, roi chan dinh

    # ---------------- nhac nen ----------------
    # Ten bai trong MUSIC_DIR (tuong doi, xem tl/music.py). None = khong co nhac.
    #
    # Nhac giai quyet mot van de CAU TRUC, khong phai trang tri: anh tinh im
    # lang, doan video co tieng, nen mot video tron thanh tung khoi tieng roi rac
    # giua nhung khoang lang. Nhac giu mach am lien tuc tu dau den cuoi.
    "music": None,
    "music_gain": -14.0,      # dB. Nhac la NEN, khong phai chu the
    # Ha nhac bao nhieu dB khi tieng that cua doan video dang phat. Day la
    # 'ducking' — thu khien nhac va tieng cung ton tai duoc thay vi tranh nhau.
    "music_duck": -11.0,
    "music_fade_in": 1.2,
    "music_fade_out": 2.5,
    "music_loop": True,       # bai ngan hon video thi lap lai

    # Cat canh dung phach nhac. Chi co tac dung khi da chon 'music'.
    #
    # KHONG bo cau truc hero/beat: do dai tu nhien cua moi shot van tinh nhu cu,
    # roi bien that duoc keo ve phach gan nhat. Nhac nhanh -> canh don dap; nhac
    # cham -> canh dai va sau. Do dai video se lech mot chut so voi du toan, do
    # la he qua tat yeu cua viec bam nhip.
    #
    # beat_every=2 nghia la cat moi hai phach — voi nhac 140 BPM thi mot phach
    # chi 0.43 giay, cat moi phach la qua nhanh cho video ky niem.
    "beat_sync": False,
    "beat_every": 1,

    # Ke thua tu filters cua du an de so anh va thoi luong khop nhau.
    # Gui len o day thi ghi de, nhung chi doi NHIP chu khong doi anh da chon.
    "pace": None, "target_seconds": None, "xfade": None,

    # ---------------- rieng mode='flip' ----------------
    "fps": 6,             # so anh moi giay
    "smooth": "blend",    # 'none' | 'blend'

    # ---------------- encode ----------------
    "crf": 20,
    "preset": "medium",
    "jpeg_quality": 93,
    "eye_dx": None,       # cu, = face_frac/2. Nhan de client cu khong loi
}

_INHERIT = ("pace", "target_seconds", "chapter_by", "max_per_chapter")

_lock = threading.Lock()
_running = {"id": None}


def options(over=None, filters=None):
    """Gop tham so render. filters la bo nguong cua du an, dung de ke thua nhip.

    Thu tu uu tien: over > filters > DEFAULT_OPTIONS. Nhip (pace/target_seconds)
    phai lay tu filters vi so anh da duoc chon theo dung bo so do — doi pace o
    day ma khong chon lai anh thi video dai/ngan khong nhu ky vong.
    """
    o = dict(DEFAULT_OPTIONS)
    for k in _INHERIT:
        v = (filters or {}).get(k)
        if v is not None:
            o[k] = v
    over = over or {}
    # Du an chon anh theo che do 'even' thi frame cua no khong co chuong va khong
    # co anh diem nhan. Render bang che do story se ra mot chuoi shot dai bang
    # nhau, khong nhan chuong — dung ky thuat nhung khong phai cai nguoi dung
    # muon. Mac dinh theo dung che do da chon anh; ghi de duoc neu co y that.
    if (filters or {}).get("mode") == "even" and over.get("mode") is None:
        o["mode"] = "flip"
    for k, v in over.items():
        if (k in DEFAULT_OPTIONS or k in _INHERIT) and v is not None:
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
    o["pair_frac"] = max(0.08, min(0.80, float(o["pair_frac"])))
    o["eye_y"] = max(0.15, min(0.80, float(o["eye_y"])))
    o["anchor_x"] = max(0.2, min(0.8, float(o["anchor_x"])))
    o["fill"] = o["fill"] if o["fill"] in ("crop", "blur") else "crop"
    o["level"] = bool(o["level"])
    o["mode"] = o["mode"] if o["mode"] in ("story", "flip") else "story"
    o["out_fps"] = max(12, min(60, int(o["out_fps"])))
    o["motion"] = o["motion"] if o["motion"] in story.MOTION else "subtle"
    o["title"] = bool(o["title"])
    o["chapter_card"] = bool(o["chapter_card"])
    o["arc"] = bool(o["arc"])
    o["title_seconds"] = max(0.0, min(8.0, float(o["title_seconds"])))
    o["card_seconds"] = max(0.4, min(6.0, float(o["card_seconds"])))
    o["intro_s"] = max(0.0, min(4.0, float(o["intro_s"])))
    o["outro_s"] = max(0.0, min(6.0, float(o["outro_s"])))
    o["birth_year"] = _year(o["birth_year"])
    o["audio"] = bool(o["audio"])
    o["audio_lead"] = max(0.0, min(3.0, float(o["audio_lead"])))
    o["audio_tail"] = max(0.0, min(4.0, float(o["audio_tail"])))
    o["audio_fade_in"] = max(0.02, min(2.0, float(o["audio_fade_in"])))
    o["audio_fade_out"] = max(0.02, min(3.0, float(o["audio_fade_out"])))
    o["audio_gain"] = max(-24.0, min(12.0, float(o["audio_gain"])))
    o["audio_normalize"] = bool(o["audio_normalize"])
    # Ten bai chi duoc GIU LAI neu phan giai duoc thanh mot file that nam trong
    # MUSIC_DIR. Khong hop le thi bo im lang thay vi bao loi: mot cai ten sai
    # khong nen lam that bai ca lan render.
    o["music"] = (str(o["music"]).strip() or None) if o["music"] else None
    if o["music"] and music.resolve(get(), o["music"]) is None:
        print(f"[render] khong tim thay ban nhac {o['music']!r} trong MUSIC_DIR "
              f"-> render khong nhac")
        o["music"] = None
    o["music_gain"] = max(-40.0, min(6.0, float(o["music_gain"])))
    o["music_duck"] = max(-40.0, min(0.0, float(o["music_duck"])))
    o["music_fade_in"] = max(0.0, min(8.0, float(o["music_fade_in"])))
    o["music_fade_out"] = max(0.0, min(10.0, float(o["music_fade_out"])))
    o["music_loop"] = bool(o["music_loop"])
    # Bam nhip ma khong co nhac thi khong co gi de bam vao.
    o["beat_sync"] = bool(o["beat_sync"]) and o["music"] is not None
    o["beat_every"] = max(1, min(8, int(o["beat_every"])))
    o["fps"] = max(1, min(30, int(o["fps"])))
    o["crf"] = max(14, min(32, int(o["crf"])))
    if o["smooth"] not in ("none", "blend"):
        o["smooth"] = "none"
    if o["label"] not in ("none", "year", "month", "date"):
        o["label"] = "none"
    if o["xfade"] is not None:
        o["xfade"] = max(0.0, min(2.0, float(o["xfade"])))
    if o["pace"] not in story.PACE:
        o["pace"] = None
    return o


def _year(v):
    try:
        y = int(v)
    except (TypeError, ValueError):
        return None
    return y if 1900 <= y <= 2100 else None


def current():
    return _running["id"]


def preflight(fr, s):
    """Bo cac shot khong doc duoc TRUOC khi tinh thoi luong.

    Bat buoc voi mode='story': storyboard chot so frame cua tung shot, mot anh
    chet giua duong se thanh mot doan dung hinh. Chi kiem duoc khi doc tu volume
    (mot lan stat moi file, rat re); che do API thi phai chiu.

    Doan video thi bat buoc phai co MEDIA_ROOT: khong tai ca file video qua HTTP
    chi de lay 3 giay giua.
    """
    if not s.media_root:
        ok = [r for r in fr if r.get("kind") != "clip"]
        return ok, len(fr) - len(ok)
    ok = []
    for r in fr:
        path = (r.get("video_path") if r.get("kind") == "clip"
                else r.get("preview_path"))
        if media.resolve(s.media_root, path):
            ok.append(r)
    return ok, len(fr) - len(ok)


def start(project_id, over=None):
    """Tao render moi va chay o thread nen. Tra ve render_id."""
    s = get()
    p = projects.get_project(project_id)
    o = options(over, p.get("filters"))
    fr = projects.frames(project_id)
    if not fr:
        raise ValueError("no frame has been selected yet")
    if _running["id"] is not None:
        raise RuntimeError(f"render #{_running['id']} is running, wait for it to "
                           f"finish")

    fr, n_missing = preflight(fr, s)
    if len(fr) < 2:
        raise ValueError(f"only {len(fr)} photos could be read, at least 2 are "
                         f"needed")
    if o["title"] and not o["title_text"]:
        o["title_text"] = p.get("person_name") or p.get("name") or ""

    sb = None
    if o["mode"] == "story":
        sb = _storyboard(fr, o, s)
        n_total = sb["n_frames"]
        note = {"n_shots": sb["n_shots"], "n_hero": sb["n_hero"],
                "n_chapter": len(sb["chapters"]), "n_clip": sb["n_clip"],
                "duration_s": sb["duration_s"], "fps": sb["fps"],
                "text": textdraw.backend(), "n_missing": n_missing}
    else:
        n_total = len(fr)
        note = {"n_shots": len(fr), "n_missing": n_missing}

    with rows() as (c, cur):
        cur.execute(
            f"INSERT INTO {s.table('render')}(project_id,status,options,n_total)"
            f" VALUES(%s,'queued',%s::jsonb,%s) RETURNING id",
            (project_id, json.dumps({**_jsonable(o), "_story": note}), n_total))
        rid = cur.fetchone()["id"]
        c.commit()

    th = threading.Thread(target=_worker, args=(rid, o, fr, sb),
                          name=f"render-{rid}", daemon=True)
    th.start()
    return rid


def _jsonable(o):
    return {k: v for k, v in o.items()
            if isinstance(v, (str, int, float, bool, type(None)))}


# Ket qua do nhip duoc nho lai theo (file, so lan sua doi): mot ban nhac cho ra
# cung mot luoi phach mai mai, ma do nhip la giai ma ca bai + FFT — khong co ly gi
# lam lai moi lan nguoi dung keo mot thanh truot o buoc xem truoc.
_beat_cache = {}


def beat_grid(o, s, total_s):
    """Luoi phach cho storyboard, hoac None. Dung chung boi xem truoc va render.

    total_s la do dai NHAM cua video: luoi phai phu het, va nhac ngan hon video
    thi beats.grid() noi tiep bang chinh chu ky trung binh.
    """
    if not o.get("beat_sync") or not o.get("music"):
        return None
    p = music.resolve(s, o["music"])
    if p is None:
        return None
    try:
        key = (str(p), p.stat().st_mtime_ns)
    except OSError:
        return None
    hit = _beat_cache.get(key)
    if hit is None:
        hit = beatmod.detect(p, s)
        _beat_cache[key] = hit
        if hit[0]:
            print(f"[render] nhip {p.name}: {len(hit[0])} phach, "
                  f"{hit[1]:.0f} BPM")
        else:
            print(f"[render] khong tim ra nhip ro trong {p.name} "
                  f"-> dung nhip ke chuyen thong thuong")
    times, _bpm = hit
    if not times:
        return None
    return beatmod.grid(times, max(1.0, float(total_s) * 1.5),
                        o.get("beat_every", 1))


def beat_info(o, s):
    """BPM cua ban nhac dang chon, doc TU CACHE. None khi chua do duoc nhip.

    Tach ra thay vi cho beat_grid tra ve thêm mot gia tri: beat_grid da nap
    cache truoc khi ham nay duoc goi, nen day chi la mot phep tra cuu — khong
    giai ma lai ca bai, va khong ai phai doi chu ky cua beat_grid.

    Can cho UI vi 'khong tim ra nhip ro' la ket qua BINH THUONG (piano tu do,
    tieng mua): render lui ve nhip ke chuyen va khong bao gi. Khong noi ra thi
    nguoi dung bat 'cat theo phach' roi khong hieu vi sao khong co gi doi.
    """
    if not o.get("music"):
        return None
    p = music.resolve(s, o["music"])
    if p is None:
        return None
    try:
        key = (str(p), p.stat().st_mtime_ns)
    except OSError:
        return None
    hit = _beat_cache.get(key)
    if hit is None:
        return None
    times, bpm = hit
    if not times:
        return {"found": False, "bpm": None, "n_beat": 0}
    return {"found": True, "bpm": round(float(bpm or 0.0), 1),
            "n_beat": len(times)}


def _storyboard(fr, o, s):
    """storyboard() co bam nhip. Hai buoc vi luoi phach can biet do dai nham.

    Do dai nham lay tu chinh storyboard chay MOT LAN khong bam nhip. Khong the
    doan truoc bang cong thuc: doan video mang do dai cua chinh no, the tieu de va
    doan dong man cong them, va cac chuong dau/cuoi duoc keo dai 12%.
    """
    sb = story.storyboard(fr, o)
    grid = beat_grid(o, s, sb["duration_s"])
    if not grid:
        return sb
    return story.storyboard(fr, o, beats=grid)


def plan_for(project_id, over=None):
    """Storyboard cho UI xem truoc, khong render gi. Tra ve tom tat + tung shot."""
    s = get()
    p = projects.get_project(project_id)
    o = options(over, p.get("filters"))
    fr = projects.frames(project_id)
    if not fr:
        return {"n_shots": 0, "n_frames": 0, "duration_s": 0.0, "chapters": [],
                "shots": [], "mode": o["mode"]}
    if o["mode"] != "story":
        n = len(fr)
        return {"mode": "flip", "n_shots": n, "n_frames": n, "fps": o["fps"],
                "duration_s": round(n / float(o["fps"]), 2), "chapters": [],
                "shots": []}
    fr, n_missing = preflight(fr, s)
    sb = _storyboard(fr, o, s)
    return {
        "mode": "story", "n_shots": sb["n_shots"], "n_frames": sb["n_frames"],
        "fps": sb["fps"], "duration_s": sb["duration_s"],
        "n_hero": sb["n_hero"], "n_clip": sb["n_clip"], "n_missing": n_missing,
        "pace": sb["pace"], "target_seconds": sb["target_seconds"],
        "n_beat_snap": sb.get("n_beat_snap", 0),
        # Tra ve ten bai ma SERVER da chap nhan, khong phai ten client gui len.
        # options() am tham bo mot ten khong phan giai duoc (co y: mot cai ten
        # sai khong nen lam that bai ca lan render), nen day la cach duy nhat de
        # UI biet no da bi bo va noi cho nguoi dung.
        "music": o.get("music"), "beat_sync": bool(o.get("beat_sync")),
        "beat_every": o.get("beat_every", 1), "beat": beat_info(o, s),
        "text_backend": textdraw.backend(),
        "chapters": sb["chapters"],
        "shots": [{"asset_id": sh["asset_id"], "fidx": sh["fidx"],
                   "key": f"{sh['asset_id']}:{sh['fidx']}",
                   "kind": sh["kind"],
                   "taken_at": story.iso(sh["taken_at"]) if sh["taken_at"] else None,
                   "chapter": sh["chapter"], "label": sh["label"],
                   "hero": sh["hero"], "first_of_chapter": sh["first_of_chapter"],
                   "seconds": round(sh["hold"] / sb["fps"], 2)}
                  for sh in sb["shots"]],
    }


def status(render_id):
    s = get()
    with rows() as (c, cur):
        cur.execute(f"SELECT * FROM {s.table('render')} WHERE id=%s", (render_id,))
        r = cur.fetchone()
        c.rollback()
    if not r:
        raise KeyError(f"no render {render_id}")
    for k in ("started_at", "finished_at"):
        if r.get(k) is not None:
            r[k] = r[k].isoformat()
    r["pct"] = round(100.0 * (r["n_done"] or 0) / max(1, r["n_total"] or 1), 1)
    r["story"] = (r.get("options") or {}).get("_story")
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
            f"SELECT id,status,n_total,n_done,duration_s,started_at,err,"
            f"       options->>'mode' AS mode"
            f" FROM {s.table('render')} WHERE project_id=%s"
            f" ORDER BY id DESC LIMIT 20", (project_id,))
        out = []
        for r in cur.fetchall():
            r["started_at"] = r["started_at"].isoformat()
            out.append(r)
        c.rollback()
    return out


# ---------------------------------------------------------------- worker
def _worker(rid, o, fr, sb):
    s = get()
    with _lock:
        _running["id"] = rid
    work = s.renders / str(rid)
    try:
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        out = work / "video.mp4"
        t0 = time.time()
        if o["mode"] == "story":
            dur = _story(rid, sb, o, s, out, work)
        else:
            dur = _flip(rid, fr, o, s, out, work)
        _set(rid, status="done", video_path=str(out), duration_s=dur,
             finished=True)
        print(f"[render {rid}] xong sau {time.time() - t0:.0f}s: {out} "
              f"({dur:.1f}s video)")
    except Exception as e:                                   # noqa: BLE001
        print(f"[render {rid}] LOI: {e}")
        _set(rid, status="error", err=str(e)[:500], finished=True)
    finally:
        shutil.rmtree(work / "frames", ignore_errors=True)
        with _lock:
            _running["id"] = None


# ============================================================ che do story
def _story(rid, sb, o, s, out, work):
    """Sinh tung frame dau ra roi day vao stdin cua ffmpeg."""
    shots = sb["shots"]
    total = sb["n_frames"]
    fps = sb["fps"]
    w, h = o["out_w"], o["out_h"]
    _set(rid, status="frames", n_total=total)

    proc, log = _pipe(out, w, h, fps, o, s, work)
    black = np.zeros((h, w, 3), np.uint8)
    srcs, last, n_dead = {}, None, 0
    b = 0
    try:
        for t in range(total):
            while b + 1 < len(shots) and shots[b + 1]["start"] <= t:
                b += 1
                # shot b-1 con can cho chuyen canh, b-2 thi chac chan xong roi.
                # Phai close() de xoa file tam khi anh tai qua API Immich.
                old = srcs.pop(b - 2, None)
                if old:
                    old.close()
            sh = shots[b]
            local = t - sh["start"]
            cur = _src(srcs, shots, b, o, s)
            f = cur.frame(local)
            owned = cur.fresh

            # chong mo voi shot truoc o dau shot nay
            if b > 0 and local < sh["xin"]:
                prev = _src(srcs, shots, b - 1, o, s)
                pf = prev.frame(shots[b]["start"] - shots[b - 1]["start"] + local)
                if pf is not None and f is not None:
                    a = (local + 1) / float(sh["xin"] + 1)
                    f = cv2.addWeighted(pf, 1.0 - a, f, a, 0.0)
                    owned = True
                elif f is None:
                    f, owned = pf, prev.fresh

            if f is None:
                n_dead += 1
                f, owned = (last if last is not None else black), False

            f, owned = _overlays(f, owned, sh, shots, local, o, sb, fps)
            fade = _fade(t, total, sb)
            if fade < 0.999:
                f = media.dim(f if owned else f.copy(), fade)
                owned = True
            last = f
            proc.stdin.write(np.ascontiguousarray(f, np.uint8).tobytes())

            if t % 24 == 0:
                _set(rid, n_done=t)
        _set(rid, n_done=total)
    except BrokenPipeError as e:
        raise RuntimeError("ffmpeg died mid-pipe: " + _tail(log)) from e
    finally:
        for sr in srcs.values():
            sr.close()
        try:
            proc.stdin.close()
        except OSError:
            pass
        # Timeout de mot ffmpeg treo khong giu luon ca render lock mai mai.
        try:
            rc = proc.wait(timeout=180)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = proc.wait()
        fh = getattr(proc, "log_fh", None)
        if fh:
            fh.close()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed (exit {rc}): " + _tail(log))
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced no file")
    if n_dead:
        print(f"[render {rid}] {n_dead}/{total} frame phai lap lai frame truoc "
              f"(khong doc duoc anh)")

    _set(rid, status="audio")
    withaudio = _mux_audio(out, shots, sb, o, s, work)
    if withaudio is not None:
        out.unlink(missing_ok=True)
        withaudio.replace(out)
    return total / float(fps)


def _src(srcs, shots, i, o, s):
    sr = srcs.get(i)
    if sr is None:
        sh = shots[i]
        cls = _ClipSrc if sh.get("kind") == "clip" else _Src
        sr = srcs[i] = cls(sh, o, s)
    return sr


class _Src:
    """Mot anh nguon + phep neo cua no. Doc anh mot lan, dung cho ca shot.

    fresh cho biet frame vua tra ve co phai bo dem rieng hay khong: khi shot
    khong zoom, frame la MOT anh duy nhat duoc dung lai cho ca tram lan goi —
    ve chu len do la lam ban het cac frame sau, nen phai copy truoc.
    """

    def __init__(self, sh, o, s):
        self.sh, self.o, self.s = sh, o, s
        self.img = self.tmp = self.kps = self.still = None
        self.dead = False
        self.opened = False
        self.fresh = False
        self.frac = o["face_frac"]
        self.static = abs(sh["zoom_to"] - sh["zoom_from"]) < 1e-4

    def _open(self):
        self.opened = True
        img, tmp = media.load(self.sh["asset_id"], self.sh["preview_path"], self.s)
        if img is None:
            self.dead = True
            return
        self.img, self.tmp = img, tmp
        hh, ww = img.shape[:2]
        self.kps = media.kps_from_blob(self.sh["kps"], ww, hh)
        if self.kps is None:
            self.dead = True
            return
        # Anh co ca nguoi thu hai -> neo theo CA HAI khuon mat. Anh chi co mot
        # nguoi trong nhom van neo binh thuong theo mot mat, khong bi loai.
        k2 = media.kps_from_blob(self.sh.get("kps2"), ww, hh)
        if k2 is not None:
            pair = media.pair_kps(self.kps, k2)
            if pair is not None:
                self.kps, self.frac = pair, self.o["pair_frac"]

    def frame(self, idx):
        if not self.opened:
            self._open()
        if self.dead:
            self.fresh = False
            return None
        if self.static and self.still is not None:
            self.fresh = False
            return self.still
        o = self.o
        z0, z1 = self.sh["zoom_from"], self.sh["zoom_to"]
        ph = story.smoothstep(idx / max(1, self.sh["vis"] - 1))
        f = media.anchor_frame(
            self.img, self.kps, o["out_w"], o["out_h"],
            face_frac=self.frac, anchor_x=o["anchor_x"], eye_y=o["eye_y"],
            level=o["level"], fill=o["fill"], zoom=z0 + (z1 - z0) * ph,
            interp=cv2.INTER_LANCZOS4 if self.static else cv2.INTER_LINEAR)
        if f is None:
            self.dead = True
            self.fresh = False
            return None
        if o["label"] != "none":
            textdraw.corner(f, _stamp(self.sh["taken_at"], o["label"]), 0.9)
        if self.static:
            self.still = f
            self.fresh = False
            return f
        self.fresh = True
        return f

    def close(self):
        media.release(self.tmp)
        self.img = self.still = self.tmp = None


class _ClipSrc:
    """Mot DOAN VIDEO: doc frame that tu file, neo theo duong di cua khuon mat.

    Khac han _Src o mot diem ban chat: khuon mat DI CHUYEN trong suot doan. Neo
    vao mot vi tri co dinh lay tu mot moc thi den cuoi doan mat da troi ra khoi
    cho. Vi vay indexer luu ca 'track' — kps o tung moc lay mau — va o day noi
    suy tuyen tinh giua hai moc gan nhat.

    Ket qua: khuon mat van dung mot cho xuyen suot ca doan video, giong nhu voi
    anh tinh. Nguoi trong khung cu dong, con khung thi khong.

    Doc tuan tu, khong seek tung frame: seek lai tu keyframe cho moi frame se
    cham gap nhieu lan. Chi seek MOT lan den dau doan.
    """

    def __init__(self, sh, o, s):
        self.sh, self.o, self.s = sh, o, s
        self.cap = None
        self.dead = False
        self.opened = False
        self.fresh = True            # moi frame la mot anh moi, khong dung lai
        self.img = None
        self.img_ms = -1.0
        self.track = _track(sh.get("track"))
        self.t0 = float(sh.get("t_start_ms") or 0)
        self.t1 = float(sh.get("t_end_ms") or 0)
        self.fps = max(1, int(o["out_fps"]))

    def _open(self):
        self.opened = True
        if self.track is None or self.track.shape[0] < 1:
            self.dead = True
            return
        path = media.resolve(self.s.media_root, self.sh.get("video_path"))
        if path is None:
            self.dead = True
            return
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            cap.release()
            self.dead = True
            return
        # Seek mot lan den dau doan. Lui lai mot chut vi voi codec co B-frame,
        # POS_MSEC lang o keyframe gan nhat truoc do — doc tiep vai frame la dung.
        if self.t0 > 40:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, self.t0 - 40.0))
        self.cap = cap
        self._advance(self.t0)

    def _advance(self, want_ms):
        """Doc tiep cho den frame co moc >= want_ms. Het video thi giu frame cuoi."""
        cap = self.cap
        if cap is None:
            return
        guard = 0
        while self.img_ms < want_ms - 1.0 and guard < 4000:
            guard += 1
            ok, img = cap.read()
            if not ok or img is None:
                break
            pos = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
            self.img = img
            self.img_ms = pos if pos > 0 else (self.img_ms + 33.0)

    def frame(self, idx):
        if not self.opened:
            self._open()
        if self.dead:
            self.fresh = False
            return None
        want = self.t0 + idx * 1000.0 / self.fps
        self._advance(min(want, self.t1 if self.t1 > self.t0 else want))
        if self.img is None:
            self.dead = True
            self.fresh = False
            return None
        o = self.o
        hh, ww = self.img.shape[:2]
        kps = self._kps_at(want, ww, hh)
        if kps is None:
            self.dead = True
            self.fresh = False
            return None
        f = media.anchor_frame(
            self.img, kps, o["out_w"], o["out_h"], face_frac=o["face_frac"],
            anchor_x=o["anchor_x"], eye_y=o["eye_y"], level=o["level"],
            fill=o["fill"], interp=cv2.INTER_LINEAR)
        if f is None:
            self.dead = True
            self.fresh = False
            return None
        if o["label"] != "none":
            textdraw.corner(f, _stamp(self.sh["taken_at"], o["label"]), 0.9)
        self.fresh = True
        return f

    def _kps_at(self, t_ms, w, h):
        """Noi suy 5 diem tai thoi diem t_ms tu track, roi doi ve pixel."""
        tr = self.track
        if tr is None or tr.shape[0] == 0:
            return None
        ts = tr[:, 0] * 1000.0
        t = float(t_ms)
        if tr.shape[0] == 1 or t <= ts[0]:
            k = tr[0, 1:]
        elif t >= ts[-1]:
            k = tr[-1, 1:]
        else:
            j = int(np.searchsorted(ts, t))
            j = max(1, min(tr.shape[0] - 1, j))
            span = max(1e-6, ts[j] - ts[j - 1])
            u = (t - ts[j - 1]) / span
            k = tr[j - 1, 1:] * (1.0 - u) + tr[j, 1:] * u
        out = np.asarray(k, np.float32).reshape(5, 2).copy()
        out[:, 0] *= float(w)
        out[:, 1] *= float(h)
        return out

    def close(self):
        if self.cap is not None:
            self.cap.release()
        self.cap = None
        self.img = None


def _track(blob):
    """float32[n][11] = t_giay + 5 cap (x,y) chuan hoa. Do indexer ghi."""
    if not blob:
        return None
    a = np.frombuffer(blob, np.float32)
    if a.size < 11 or a.size % 11:
        return None
    return a.reshape(-1, 11)


def _overlays(f, owned, sh, shots, local, o, sb, fps):
    """The tieu de va nhan chuong, mo dan len roi tan di.

    Chi ve khi CO CHU that. Khong kiem thi mot du an che do 'even' render bang
    che do story se ra mot dai toi o day khung ma khong co chu nao trong do —
    vi frame cua no khong co nhan chuong.
    """
    jobs = []
    if o["title"] and sh is shots[0] and sb["f_title"] > 0:
        lines = _title_lines(o, shots)
        a = _ramp(local, 0, sb["f_title"], fps, 0.5, 0.6) if lines else 0.0
        if a > 0:
            jobs.append((a, 0.70, lines, 0.40))
    if o["chapter_card"] and sh["first_of_chapter"] and sh["label"]:
        start = sb["f_title"] if sh is shots[0] else 0
        span = min(int(round(o["card_seconds"] * fps)), max(1, sh["hold"] - start))
        a = _ramp(local, start, span, fps, 0.35, 0.45)
        if a > 0:
            jobs.append((a, 0.855, _card_lines(sh, o), 0.30))
    if not jobs:
        return f, owned
    if not owned:
        f = f.copy()
        owned = True
    for a, y, lines, sc in jobs:
        textdraw.scrim(f, height=sc, strength=0.62 * a)
        px = int(min(f.shape[1], f.shape[0]) * (0.085 if y < 0.8 else 0.055))
        textdraw.block(f, lines, y_frac=y, px=px, alpha=a)
    return f, owned


def _title_lines(o, shots):
    """[] neu khong co gi de viet — de _overlays biet ma bo qua ca lop toi."""
    name = (o.get("title_text") or "").strip()
    y0 = story.dt(shots[0]["taken_at"]).year if shots[0]["taken_at"] else None
    y1 = story.dt(shots[-1]["taken_at"]).year if shots[-1]["taken_at"] else None
    span = f"{y0}–{y1}" if y0 and y1 and y0 != y1 else (str(y0) if y0 else "")
    if name:
        return [(name, 1.0), (span, 0.52)]
    return [(span, 1.0)] if span else []


def _card_lines(sh, o):
    lines = [(sh["label"], 1.0)]
    by = o.get("birth_year")
    if by and sh["taken_at"]:
        age = story.dt(sh["taken_at"]).year - by
        if 0 <= age <= 120:
            lines.append((f"age {age}", 0.68))
    return lines


def _ramp(local, start, length, fps, fin=0.35, fout=0.45):
    """Alpha hinh thang: len trong fin giay, giu, xuong trong fout giay."""
    x = local - start
    if length <= 0 or x < 0 or x >= length:
        return 0.0
    up = (x + 1) / max(1.0, round(fin * fps))
    down = (length - x) / max(1.0, round(fout * fps))
    return max(0.0, min(1.0, up, down))


def _fade(t, total, sb):
    """He so sang cho mo man / dong man."""
    k = 1.0
    if sb["fade_in"]:
        k = min(k, (t + 1) / float(sb["fade_in"]))
    if sb["fade_out"]:
        k = min(k, (total - t) / float(sb["fade_out"]))
    return max(0.0, min(1.0, k))


def _stamp(taken_at, kind):
    if taken_at is None:
        return ""
    s = taken_at.isoformat() if hasattr(taken_at, "isoformat") else str(taken_at)
    return {"year": s[:4], "month": s[:7], "date": s[:10]}.get(kind, "")


# ============================================================== tieng
def audio_plan(shots, sb, o, s):
    """Xep tieng cua tung doan len dong thoi gian cua video ra.

    Tra ve list dict, moi cai la mot nguon tieng: file, cat tu dau den dau, dat
    o giay thu bao nhieu, fade bao lau. Tach ra khoi viec goi ffmpeg de test duoc
    ma khong can file video nao.

    Hai chi tiet lam nen cam giac "song lai khoanh khac" thay vi "chen tieng":

    1. Tieng vao TRUOC hinh (audio_lead) va con lai SAU khi hinh da cat
       (audio_tail). Trong dung phim day la J-cut va L-cut: tai nghe thay khong
       gian moi truoc khi mat thay no, va khong gian do khong tat dot ngot cung
       luc voi hinh. Bo hai cai nay thi moi doan thanh mot khoi tieng bi dong mo
       cua — dung la 'chen tieng'.

    2. Doan dau tien khong the vao truoc giay 0, nen lead bi cat theo dung cho no
       con: lead_eff = min(lead, vi tri cua doan). Khong lam vay thi delay am,
       va tieng se lech voi hinh suot ca doan.
    """
    fps = float(sb["fps"])
    total = sb["n_frames"] / fps
    out = []
    for sh in shots:
        if sh.get("kind") != "clip" or not sh.get("video_path"):
            continue
        path = media.resolve(s.media_root, sh["video_path"])
        if path is None:
            continue
        pos = sh["start"] / fps                      # hinh xuat hien o giay nay
        t0 = float(sh["t_start_ms"] or 0) / 1000.0
        t1 = float(sh["t_end_ms"] or 0) / 1000.0
        if t1 <= t0:
            continue
        lead = min(float(o["audio_lead"]), pos, t0)
        tail = float(o["audio_tail"])
        src_dur = float(sh.get("src_dur_ms") or 0) / 1000.0
        a0 = t0 - lead
        a1 = t1 + tail
        if src_dur > 0:
            a1 = min(a1, src_dur)
        a1 = min(a1, t1 + max(0.0, total - (pos + (t1 - t0))))
        dur = a1 - a0
        if dur < 0.25:
            continue
        fin = min(float(o["audio_fade_in"]), dur / 2.0)
        fout = min(float(o["audio_fade_out"]), dur / 2.0)
        out.append({
            "path": str(path), "src_start": round(a0, 3), "dur": round(dur, 3),
            "at": round(pos - lead, 3), "fade_in": round(fin, 3),
            "fade_out": round(fout, 3),
        })
    return out


# Nhac ha xuong / tro lai trong bao nhieu giay quanh moi doan co tieng that.
# 0.6s la du de tai khong nghe thay "nac" ma cung khong tre den muc cau dau tien
# cua doan bi nhac de len.
DUCK_RAMP = 0.6

# Tran so khoang ducking. Vuot qua thi gop cac khoang lai gan hon: mot bieu thuc
# volume voi 200 so hang la thu ffmpeg phai danh gia MOI FRAME tieng.
DUCK_MAX_SPANS = 40


def merge_spans(spans, gap):
    """Gop cac khoang [a,b] chong nhau hoac cach nhau duoi `gap`."""
    out = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def duck_spans(plan, ramp=DUCK_RAMP, cap=DUCK_MAX_SPANS):
    """Cac khoang thoi gian co tieng that -> can ha nhac.

    Gop dan cho den khi con duoi `cap` khoang. Gop them chi lam nhac bi ha lau
    hon mot chut, khong lam sai gi — con mot bieu thuc dai vo han thi lam cham
    ca lan render.
    """
    spans = [(float(a["at"]), float(a["at"]) + float(a["dur"])) for a in plan
             if float(a["dur"]) > 0]
    if not spans:
        return []
    gap = ramp * 2.0
    merged = merge_spans(spans, gap)
    while len(merged) > cap:
        gap *= 2.0
        merged = merge_spans(merged, gap)
    return merged


def duck_expr(spans, duck_db, ramp=DUCK_RAMP):
    """Bieu thuc `volume` cua ffmpeg: 1.0 luc thuong, ha xuong trong cac khoang.

    Vi sao khong dung sidechaincompress — cach lam "dung sach" cho ducking:
    o day ta DA BIET chinh xac tieng that nam o giay nao (audio_plan tinh ra
    truoc khi goi ffmpeg). Mot compressor phai suy dieu do tu bien do tin hieu,
    va muc ha thi phu thuoc threshold/ratio nen khong dat duoc "ha dung 11 dB".
    Duong bao tinh san thi chinh xac, on dinh giua cac lan chay, va kiem tra duoc
    bang mot ham Python.

    Moi khoang la mot hinh thang: len trong `ramp` giay truoc khi vao, giu, roi
    xuong trong `ramp` giay sau khi ra. Lay max cua cac hinh thang de hai khoang
    gan nhau khong cong don thanh ha gap doi.
    """
    if not spans or duck_db >= 0:
        return None
    g = 10.0 ** (float(duck_db) / 20.0)
    terms = []
    for a, b in spans:
        lo = max(0.0, a - ramp)
        terms.append(f"clip((t-{lo:.3f})/{ramp:.3f},0,1)"
                     f"*clip(({b + ramp:.3f}-t)/{ramp:.3f},0,1)")
    env = terms[0]
    for t in terms[1:]:
        env = f"max({env},{t})"
    return f"1-{1.0 - g:.4f}*({env})"


def music_chain(idx, o, total, spans):
    """Chuoi filter cho mot input nhac -> nhan [mus]."""
    fin = min(float(o["music_fade_in"]), total / 2.0)
    fout = min(float(o["music_fade_out"]), total / 2.0)
    parts = [f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
             f"channel_layouts=stereo",
             f"atrim=0:{total:.3f}", "asetpts=N/SR/TB",
             f"volume={float(o['music_gain']):.2f}dB"]
    expr = duck_expr(spans, float(o["music_duck"]))
    if expr:
        # eval=frame: bieu thuc phu thuoc t nen phai danh gia lai tung frame,
        # mac dinh (eval=once) se lay gia tri tai t=0 va giu nguyen ca bai.
        parts.append(f"volume='{expr}':eval=frame")
    if fin > 0:
        parts.append(f"afade=t=in:st=0:d={fin:.2f}")
    if fout > 0:
        parts.append(f"afade=t=out:st={max(0.0, total - fout):.2f}:d={fout:.2f}")
    return ",".join(parts) + "[mus]"


def audio_filter(plan, o, total, music_idx=None):
    """Chuoi filter_complex cho ffmpeg + nhan cua dau ra. ('', '') neu khong co.

    music_idx: chi so input cua ban nhac trong dong lenh ffmpeg, hoac None.
    Ba truong hop: chi tieng doan, chi nhac, hoac ca hai (nhac bi ducking).
    """
    if not plan and music_idx is None:
        return "", ""
    if not plan:
        # Chi co nhac: toan bo video la anh tinh, hoac tieng doan bi tat.
        return (music_chain(music_idx, o, total, []) + ";"
                + f"[mus]alimiter=limit=0.95,apad=whole_dur={total:.3f}[aout]",
                "[aout]")
    parts, labels = [], []
    for k, a in enumerate(plan, start=1):
        lab = f"a{k}"
        delay = int(round(a["at"] * 1000))
        # aformat truoc moi thu: cac clip co the khac sample rate va so kenh, ma
        # amix doi tat ca giong nhau — khong chuan hoa thi ffmpeg bao loi kho hieu.
        chain = (f"[{k}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
                 f"channel_layouts=stereo,"
                 f"afade=t=in:st=0:d={a['fade_in']:.2f},"
                 f"afade=t=out:st={max(0.0, a['dur'] - a['fade_out']):.2f}:"
                 f"d={a['fade_out']:.2f}")
        if delay > 0:
            chain += f",adelay={delay}|{delay}"
        parts.append(chain + f"[{lab}]")
        labels.append(f"[{lab}]")

    last = labels[0][1:-1]
    if len(labels) > 1:
        # normalize=0: amix mac dinh chia am luong cho so input, nen 8 doan thi
        # moi doan chi con 1/8 — nghe nhu thi tham. O day cac doan gan nhu khong
        # chong nhau nen cong thang lai la dung, va alimiter chan dinh phia sau.
        parts.append("".join(labels)
                     + f"amix=inputs={len(labels)}:normalize=0"
                       f":dropout_transition=0[mx]")
        last = "mx"
    tail = []
    if o["audio_normalize"]:
        # dynaudnorm can muc giua cac doan quay o cac dieu kien khac nhau — day
        # la thu lam chuoi tieng nghe lien mach thay vi to nho giat cuc.
        tail.append("dynaudnorm=f=250:g=7:p=0.9")
    if abs(float(o["audio_gain"])) > 0.01:
        tail.append(f"volume={float(o['audio_gain']):.2f}dB")
    if music_idx is None:
        tail.append("alimiter=limit=0.95")
        # apad + do dai chinh xac: doan cuoi thuong ket thuc truoc khi video het,
        # thieu apad thi track tieng ngan hon track hinh va mot so may phat bo
        # qua luon phan cuoi.
        tail.append(f"apad=whole_dur={total:.3f}")
        parts.append(f"[{last}]" + ",".join(tail) + "[aout]")
        return ";".join(parts), "[aout]"

    # Co nhac: tieng doan giu nguyen muc, nhac bi ha trong dung nhung khoang do.
    # normalize=0 vi hai nguon nay CO Y chong len nhau — chia doi muc thi vua mat
    # tieng that vua mat nhac.
    parts.append(f"[{last}]" + ",".join(tail) + "[voice]")
    parts.append(music_chain(music_idx, o, total, duck_spans(plan)))
    parts.append(f"[mus][voice]amix=inputs=2:normalize=0:dropout_transition=0,"
                 f"alimiter=limit=0.95,apad=whole_dur={total:.3f}[aout]")
    return ";".join(parts), "[aout]"


def _mux_audio(video, shots, sb, o, s, work):
    """Ghep tieng vao video da encode. Tra ve Path moi, hoac None neu bo qua.

    Lam thanh MOT LUOT RIENG sau khi encode xong, khong tron vao pipe rawvideo.
    Ly do: video duoc copy nguyen (-c:v copy) nen re, va neu buoc tieng that bai
    vi bat ky ly do gi thi van con nguyen video im lang — thay vi mat ca video.
    """
    plan = []
    if o["audio"]:
        plan = [a for a in audio_plan(shots, sb, o, s)
                if _has_audio(a["path"], s)]
    total = sb["n_frames"] / float(sb["fps"])

    # Nhac doc lap voi tieng doan: video toan anh tinh (khong co doan nao co
    # tieng) van phai co nhac, va do chinh la truong hop nhac giup nhieu nhat.
    mus = music.resolve(s, o["music"]) if o.get("music") else None
    if not plan and mus is None:
        return None

    music_idx = (1 + len(plan)) if mus is not None else None
    fc, out_lab = audio_filter(plan, o, total, music_idx)
    if not fc:
        return None

    dst = work / "video_audio.mp4"
    cmd = [s.ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
           "-i", str(video)]
    for a in plan:
        cmd += ["-ss", f"{a['src_start']:.3f}", "-t", f"{a['dur']:.3f}",
                "-vn", "-i", a["path"]]
    if mus is not None:
        # -stream_loop thay vi filter aloop: aloop dem bang SAMPLE va phai giu ca
        # vong lap trong bo dem, mot bai 3 phut la ~8.6 trieu sample moi kenh.
        # stream_loop cho ffmpeg doc lai file, ton gan nhu khong gi.
        dur = music.duration(mus, s)
        if o["music_loop"] and (dur is None or dur < total):
            cmd += ["-stream_loop", "-1"]
        cmd += ["-vn", "-i", str(mus)]
    cmd += ["-filter_complex", fc,
            "-map", "0:v", "-map", out_lab,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ac", "2",
            "-t", f"{total:.3f}", "-movflags", "+faststart", str(dst)]
    log = work / "ffmpeg-audio.log"
    print(f"[render] ghep tieng tu {len(plan)} doan"
          + (f" + nhac {mus.name}" if mus is not None else ""))
    with open(log, "wb") as fh:
        rc = subprocess.run(cmd, stdout=fh, stderr=fh).returncode
    if rc != 0 or not dst.exists() or dst.stat().st_size == 0:
        print("[render] ghep tieng that bai, giu video im lang: " + _tail(log))
        return None
    return dst


def _has_audio(path, s):
    """Clip khong co track tieng thi phai bo ra: mot input khong co audio se lam
    ca filter_complex that bai, keo theo mat tieng cua tat ca doan khac."""
    try:
        p = subprocess.run(
            [s.ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False                     # khong co ffprobe -> khong doan bua
    return p.returncode == 0 and "audio" in (p.stdout or "")


def _pipe(out, w, h, fps, o, s, work):
    """ffmpeg doc rawvideo tu stdin. stderr ra file de KHONG BAO GIO tac pipe.

    Neu de stderr=PIPE ma khong doc, ffmpeg noi nhieu la day day bo dem cua OS
    roi treo, con minh thi dang cho ghi frame — deadlock ca hai dau.
    """
    log = work / "ffmpeg.log"
    cmd = [
        s.ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
        "-r", str(fps), "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-preset", o["preset"], "-crf", str(o["crf"]),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-threads", str(max(1, s.ffmpeg_threads)), str(out),
    ]
    print("[render] " + " ".join(cmd))
    fh = open(log, "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=fh, stderr=fh,
                            bufsize=0)
    proc.log_fh = fh              # giu tham chieu de dong tu te sau khi xong
    return proc, log


def _tail(log, n=400):
    try:
        return Path(log).read_text("utf-8", "replace")[-n:]
    except OSError:
        return "(khong doc duoc log ffmpeg)"


# ============================================================= che do flip
def _flip(rid, fr, o, s, out, work):
    """Cach cu: align tung anh ra jpg roi cho ffmpeg ghep chuoi anh."""
    frames_dir = work / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    # Che do flip la chuoi anh tinh, khong co cho cho doan video. Bo qua cho ro
    # rang thay vi de _aligned() tra False mot cach im lang.
    fr = [r for r in fr if r.get("kind") != "clip"]
    _set(rid, status="frames", n_total=len(fr))
    n_ok = 0
    for r in fr:
        if _aligned(r, frames_dir, n_ok + 1, o, s):
            n_ok += 1
            if n_ok % 10 == 0:
                _set(rid, n_done=n_ok)
    _set(rid, n_done=n_ok)
    if n_ok < 2:
        raise RuntimeError(f"only {n_ok} frames could be aligned, not enough to "
                           f"build a video")

    _set(rid, status="encoding")
    vf = ["scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    if o["smooth"] == "blend":
        # framerate= noi suy co pha tron, re hon minterpolate nhieu lan
        vf.insert(0, f"framerate=fps={max(o['fps'] * 4, 24)}")
    cmd = [
        s.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", str(o["fps"]), "-start_number", "1",
        "-i", str(frames_dir / "f_%05d.jpg"),
        "-vf", ",".join(vf),
        "-c:v", "libx264", "-preset", o["preset"], "-crf", str(o["crf"]),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-threads", str(max(1, s.ffmpeg_threads)), "-an", str(out),
    ]
    print("[render] " + " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + (p.stderr or "")[-400:])
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced no file")
    return n_ok / float(o["fps"])


def _aligned(row, out_dir, n, o, s):
    img, tmp = media.load(row["asset_id"], row["preview_path"], s)
    if img is None:
        return False
    try:
        h, w = img.shape[:2]
        kps = media.kps_from_blob(row["kps"], w, h)
        if kps is None:
            return False
        frac = o["face_frac"]
        k2 = media.kps_from_blob(row.get("kps2"), w, h)
        if k2 is not None:
            pair = media.pair_kps(kps, k2)
            if pair is not None:
                kps, frac = pair, o["pair_frac"]
        frame = media.anchor_frame(
            img, kps, o["out_w"], o["out_h"], face_frac=frac,
            anchor_x=o["anchor_x"], eye_y=o["eye_y"], level=o["level"],
            fill=o["fill"])
        if frame is None:
            return False
        if o["label"] != "none":
            textdraw.corner(frame, _stamp(row["taken_at"], o["label"]), 0.9)
        return media.imwrite(out_dir / f"f_{n:05d}.jpg", frame, o["jpeg_quality"])
    finally:
        media.release(tmp)


# ================================================================= chung
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
        return False, f"{s.ffmpeg} not found in PATH"
