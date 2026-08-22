"""Man hinh 2 + 3: lay anh cua mot nguoi roi loc theo pose.

Cach lam: mot query keo TOAN BO face cua nguoi do kem chi so, roi loc bang
Python. Vai nghin dong nen nhanh, va doi lai duoc hai thu quan trong:

  - moi anh bi loai deu co LY DO cu the -> UI giai thich duoc, nguoi dung biet
    phai keo nguong nao
  - doi filter khong phai viet lai SQL, thu nghiem tuc thi

Sau khi loc con phai QUYET DINH LAY ANH NAO. Hai che do:

  mode='story'  (mac dinh) Chia thoi gian thanh chuong, moi chuong lay mot anh
                chu dao + vai anh phu, tong so anh suy ra tu ngan sach thoi
                luong. Xem tl/story.py.
  mode='even'   Cach cu: chia timeline thanh o bang nhau (bucket_days), moi o
                lay per_bucket anh diem cao nhat. Thoi luong = so anh / fps,
                khong kiem soat duoc. Giu lai cho ai muon toan quyen.
"""
from . import story
from .db import rows
from .settings import get

dt, ts, iso = story.dt, story.ts, story.iso

# Ly do loai khi nguoi dung tu bo mot anh trong UI. Mot hang so vi UI dua vao
# dung chuoi nay de biet anh nao co the lay lai duoc (app.js: MANUAL_REASON).
MANUAL_REASON = "dropped by hand"

# Nguong mac dinh, nham vao video hanh trinh mot nguoi tu be den lon.
DEFAULTS = {
    # --- pose dau ---
    "max_yaw": 22.0,          # quay trai/phai
    "max_pitch": 18.0,        # ngua/cui
    "max_roll": 20.0,         # nghieng dau
    "min_frontality": 0.45,   # 0..1 gop pose + doi xung
    "min_ear": 0.15,          # loai anh nham mat
    # --- chat luong anh ---
    "min_eye_ratio": 0.030,   # mat cach nhau >= 3% canh dai: loai mat qua nho
    "min_sharp": 60.0,
    "bright_min": 45.0,
    "bright_max": 215.0,
    "min_quality": 0.0,
    # --- nguoi khac trong anh ---
    "allow_others": True,     # cho phep anh co nguoi khac
    "max_faces": 0,           # 0 = khong gioi han so mat trong anh
    # --- doan video (do stage clips cua indexer cat ra) ---
    "use_clips": True,        # gom ca doan video vao video ke chuyen
    "max_clip_motion": 2.6,   # do rung toi da: "chieu rong mat" moi giay
    "min_clip_seconds": 0.8,
    # --- body pose ---
    "use_body": True,         # co dung du lieu fp_body de loc khong
    "postures": ["standing", "sitting", "unknown"],
    "orientations": ["front", "side", "unknown"],
    "allow_missing_body": True,   # khong detect ra nguoi thi van nhan
    "min_body_front": 0.0,
    # --- ke chuyen (xem story.py) ---
    "mode": "story",            # 'story' | 'even'
    # None = TU SUY do dai tu du lieu. Nguoi dung chi noi "dung video cua ong A",
    # khong noi "dung video 60 giay". Dat mot con so vao day la che do chuyen gia.
    "target_seconds": None,
    "pace": "normal",           # slow | normal | quick | snap
    "chapter_by": "auto",       # auto | years2 | year | half | quarter | month
    "max_per_chapter": 6,
    # --- che do 'even' cu: rai deu theo thoi gian ---
    "bucket_days": 30,
    "per_bucket": 1,
}

# Doan video: mot dong = mot doan da duoc indexer cat ra va cham diem. Dat cac
# cot cung ten voi anh de _reject() dung chung mot ham, khong phai viet hai
# nhanh loc song song roi lech nhau.
_FETCH_CLIP = """
SELECT c.asset_id,
       (-1 - c.cidx)::int              AS fidx,   -- am: danh dau day la doan video
       c.person_id, f.person_name,
       a.taken_at, a.date_src, a.filename,
       1 AS n_face, NULL::int AS n_body,
       NULL::real AS x1, NULL::real AS y1, NULL::real AS x2, NULL::real AS y2,
       0.0::real  AS yaw, 0.0::real AS pitch, 0.0::real AS roll,
       c.frontality, NULL::real AS ear,
       NULL::real AS eye_px, c.face_ratio AS eye_ratio,
       c.sharp, c.bright, NULL::real AS symm,
       c.score AS quality, NULL::real AS age,
       NULL::text AS posture, NULL::text AS orientation,
       NULL::real AS body_front, NULL::real AS torso_deg, NULL::real AS area_ratio,
       c.cidx, c.t_start_ms, c.t_end_ms, c.t_peak_ms, c.motion, c.sim, c.track,
       a.video_path, a.dur_ms
FROM {vclip} c
JOIN {asset} a ON a.id = c.asset_id
LEFT JOIN LATERAL (
    SELECT ff.person_name FROM {face} ff
    WHERE ff.person_id = c.person_id AND ff.person_name IS NOT NULL LIMIT 1
) f ON true
WHERE c.person_id = ANY(%s::uuid[])
  AND a.taken_at IS NOT NULL
  AND a.video_path IS NOT NULL
  AND (%s::date IS NULL OR a.taken_at >= %s::date)
  AND (%s::date IS NULL OR a.taken_at < (%s::date + INTERVAL '1 day'))
ORDER BY a.taken_at, c.cidx
"""

_FETCH = """
SELECT f.asset_id, f.fidx, f.person_id, f.person_name,
       a.taken_at, a.date_src, a.filename,
       a.n_face, a.n_body,
       f.x1, f.y1, f.x2, f.y2,
       f.yaw, f.pitch, f.roll, f.frontality, f.ear,
       f.eye_px, f.eye_ratio, f.sharp, f.bright, f.symm, f.quality, f.age,
       b.posture, b.orientation, b.body_front, b.torso_deg, b.area_ratio
FROM {face} f
JOIN {asset} a ON a.id = f.asset_id
LEFT JOIN {body} b ON b.asset_id = f.asset_id AND b.face_fidx = f.fidx
WHERE f.person_id = ANY(%s::uuid[])
  AND f.state = 1
  AND f.kps IS NOT NULL
  AND a.taken_at IS NOT NULL
  AND (%s::date IS NULL OR a.taken_at >= %s::date)
  AND (%s::date IS NULL OR a.taken_at < (%s::date + INTERVAL '1 day'))
ORDER BY a.taken_at, f.asset_id, f.fidx
"""


def merge(filters):
    """Gop filter nguoi dung gui len voi mac dinh, bo qua key la.

    target_seconds la ngoai le: None la mot GIA TRI co nghia ("tu suy"), khong
    phai "khong gui gi". Neu bo qua None nhu cac key khac thi khong ai tat duoc
    che do dat tay sau khi da bat.
    """
    out = dict(DEFAULTS)
    given = filters or {}
    for k, v in given.items():
        if k in DEFAULTS and v is not None:
            out[k] = v
    if "target_seconds" in given:
        out["target_seconds"] = given["target_seconds"]
    if out["mode"] not in ("story", "even"):
        out["mode"] = "story"
    # Chuan hoa cac tham so nhip ngay tu day: buoc chon anh va buoc render doc
    # cung mot bo so, lech nhau la video dai sai so voi ngan sach.
    p = story.plan(out)
    for k in ("target_seconds", "pace", "chapter_by", "max_per_chapter"):
        out[k] = p[k]
    return out


def groups_of(subjects):
    """Chuan hoa cach mo ta 'video cua ai' thanh danh sach NHOM cluster.

    Mot nhom = mot nguoi. Immich hay tach mot nguoi thanh nhieu cluster theo do
    tuoi, nen mot nguoi la mot danh sach cluster chu khong phai mot id.

        "abc"                 -> [["abc"]]                mot nguoi, mot cluster
        ["abc","def"]         -> [["abc","def"]]          MOT nguoi, hai cluster
        [["abc","def"],["gh"]]-> [["abc","def"],["gh"]]   HAI nguoi
    """
    if not subjects:
        return []
    if isinstance(subjects, str):
        return [[subjects]]
    out = []
    for item in subjects:
        if isinstance(item, str):
            out.append([str(item)])
        elif item:
            ids = [str(x) for x in item if x]
            if ids:
                out.append(ids)
    # ["abc","def"] la mot nguoi hai cluster, khong phai hai nguoi
    if all(len(g) == 1 for g in out) and len(out) > 1 and not any(
            isinstance(i, (list, tuple)) for i in subjects):
        return [[g[0] for g in out]]
    return out


def fetch(subjects, date_from=None, date_to=None, together=False,
          use_clips=True):
    """Anh VA doan video cua mot hoac nhieu NGUOI trong khoang thoi gian.

    together=True: chi lay anh co MAT DU tat ca cac nguoi — "video cua ong A voi
    ba B" theo nghia hai nguoi chup chung. together=False: anh co bat ky ai trong
    so ho.

    Mot anh chi ra MOT dong ket qua (mat diem cao nhat cua nguoi chinh), du trong
    anh co nhieu mat thuoc cac nguoi da chon. Neu khong the thi cung mot buc anh
    xuat hien hai lan trong video.

    use_clips=True thi gom ca cac doan video ma stage clips da cat ra. Doan video
    di qua dung bo loc voi anh, chi khac la fidx am de phan biet.
    """
    grps = groups_of(subjects)
    if not grps:
        return []
    flat = [pid for g in grps for pid in g]
    owner = {pid: gi for gi, g in enumerate(grps) for pid in g}

    s = get()
    sql = _FETCH.format(face=s.table("face"), asset=s.table("asset"),
                        body=s.table("body"))
    args = (flat, date_from, date_from, date_to, date_to)
    with rows() as (c, cur):
        cur.execute(sql, args)
        got = [dict(r) for r in cur.fetchall()]
        clips = []
        if use_clips and _has_clips(cur, s):
            cur.execute(_FETCH_CLIP.format(
                vclip=s.table("vclip"), asset=s.table("asset"),
                face=s.table("face")), args)
            clips = [dict(r) for r in cur.fetchall()]
        c.rollback()

    for r in got:
        r["group"] = owner.get(str(r["person_id"]), 0)
        r["kind"] = "image"
    out = _one_per_asset(got, len(grps), together)

    # Doan video khong tham gia luat "mot anh mot dong": mot clip dai co the co
    # hai doan dang giu, va chung khong trung nhau ve thoi gian.
    for r in clips:
        r["group"] = owner.get(str(r["person_id"]), 0)
        r["kind"] = "clip"
        r["dur_s"] = max(0.1, (r["t_end_ms"] - r["t_start_ms"]) / 1000.0)
    if together and len(grps) > 1:
        clips = []          # "chup chung" trong clip can du lieu theo tung frame
    out.extend(clips)
    out.sort(key=lambda r: (_ts(r["taken_at"]), str(r["asset_id"]),
                            int(r["fidx"])))
    return out


_clip_tbl = {"at": 0.0, "ok": False}


def _has_clips(cur, s):
    """Bang fp_vclip chi ton tai sau khi indexer chay ban co video. Cache 60s."""
    import time
    if time.time() - _clip_tbl["at"] < 60.0:
        return _clip_tbl["ok"]
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema=%s AND table_name=%s",
        (s.pg_schema, f"{s.prefix}vclip"))
    _clip_tbl.update(at=time.time(), ok=cur.fetchone() is not None)
    return _clip_tbl["ok"]


def _one_per_asset(rows_, n_groups, together):
    """Gop cac mat trong cung mot anh thanh mot dong.

    Nguoi chinh la nhom 0. Neu anh co ca nhom 1 thi ghi lai fidx cua nguoi do
    (fidx2) — buoc dung dung no de neo theo CA HAI khuon mat thay vi mot.
    """
    by_asset = {}
    for r in rows_:
        by_asset.setdefault(r["asset_id"], []).append(r)

    out = []
    for rs in by_asset.values():
        present = {r["group"] for r in rs}
        if together and len(present) < n_groups:
            continue
        pool = [r for r in rs if r["group"] == 0] or rs
        prim = dict(max(pool, key=_score))
        prim["n_subject"] = len(present)
        if n_groups > 1:
            mate = [r for r in rs if r["group"] != prim["group"]]
            if mate:
                partner = max(mate, key=_score)
                prim["fidx2"] = partner["fidx"]
                prim["group2"] = partner["group"]
                prim["person_name2"] = partner.get("person_name")
        out.append(prim)
    out.sort(key=lambda r: (_ts(r["taken_at"]), str(r["asset_id"])))
    return out


# --------------------------------------------------------------------- loc
def _reject(r, f):
    """Tra ve ly do loai, hoac None neu dat. Thu tu kiem tra tu re den dat."""
    if r.get("kind") == "clip":
        why = _reject_clip(r, f)
        if why:
            return why
        # roi di tiep qua cac nguong dung chung: do net, do sang, mat qua nho.
        # yaw/pitch/roll cua clip la 0 nen ba nguong dau khong loai gi.

    # KHONG dung `r["yaw"] or 999`: yaw = 0.0 la gia tri hop le va la gia tri
    # TOT NHAT (chinh dien tuyet doi), nhung 0.0 la falsy nen se bien thanh 999
    # va bi loai. None moi la "chua tinh duoc" va moi dang bi loai.
    yaw, pitch, roll = (_ang(r["yaw"]), _ang(r["pitch"]), _ang(r["roll"]))
    if yaw > f["max_yaw"]:
        return f"head turned {yaw:.0f}\u00b0 > {f['max_yaw']:.0f}\u00b0"
    if pitch > f["max_pitch"]:
        return f"tilted up/down {pitch:.0f}\u00b0 > {f['max_pitch']:.0f}\u00b0"
    if roll > f["max_roll"]:
        return f"head tilted sideways {roll:.0f}\u00b0 > {f['max_roll']:.0f}\u00b0"

    fr = r["frontality"]
    if fr is not None and fr < f["min_frontality"]:
        return f"not frontal enough {fr:.2f} < {f['min_frontality']:.2f}"

    ear = r["ear"]
    if ear is not None and f["min_ear"] > 0 and ear < f["min_ear"]:
        return f"eyes look closed (EAR {ear:.2f})"

    er = r["eye_ratio"]
    if er is not None and er < f["min_eye_ratio"]:
        return f"face too small in the photo ({er * 100:.1f}%)"

    sh = r["sharp"]
    if sh is not None and sh < f["min_sharp"]:
        return f"blurry (sharpness {sh:.0f} < {f['min_sharp']:.0f})"

    br = r["bright"]
    if br is not None and not (f["bright_min"] <= br <= f["bright_max"]):
        return f"too bright or too dark ({br:.0f})"

    q = r["quality"]
    if q is not None and f["min_quality"] > 0 and q < f["min_quality"]:
        return f"low quality score ({q:.0f})"

    n_face = r["n_face"] or 1
    if not f["allow_others"] and n_face > 1:
        return f"photo contains other people ({n_face} faces)"
    if f["max_faces"] and n_face > f["max_faces"]:
        return f"too many faces {n_face} > {f['max_faces']}"

    if f["use_body"]:
        has_body = r["posture"] is not None or r["orientation"] is not None
        if not has_body:
            if not f["allow_missing_body"]:
                return "no body detected"
        else:
            if r["posture"] and r["posture"] not in f["postures"]:
                return f"posture {r['posture']}"
            if r["orientation"] and r["orientation"] not in f["orientations"]:
                return f"torso orientation {r['orientation']}"
            bf = r["body_front"]
            if bf is not None and bf < f["min_body_front"]:
                return f"torso not frontal ({bf:.2f})"
    return None


def _ang(v):
    """Goc tuyet doi. None = chua tinh duoc -> tra ve mot so lon de bi loai."""
    return 999.0 if v is None else abs(float(v))


def _reject_clip(r, f):
    """Nguong rieng cho doan video. Anh khong co nhung chi so nay."""
    if not f["use_clips"]:
        return "video clips are turned off"
    dur = float(r.get("dur_s") or 0.0)
    if dur < f["min_clip_seconds"]:
        return f"clip too short ({dur:.1f}s)"
    mo = r.get("motion")
    if mo is not None and mo > f["max_clip_motion"]:
        return f"clip too shaky ({mo:.1f} face widths/s)"
    if not r.get("video_path"):
        return "no video file path"
    return None


def _score(r):
    """Diem de xep hang trong cung mot o thoi gian.

    Doan video duoc cong mot khoan nho: mot doan dong dang gia hon mot buc anh
    tinh o cung diem chat luong, va no la thu bien video thanh "co cau chuyen"
    chu khong phai bang anh. It thoi, de khong bien video thanh toan clip.
    """
    if r["quality"] is not None:
        q = float(r["quality"])
    else:
        fr = r["frontality"] or 0.0
        sh = min((r["sharp"] or 0.0) / 400.0, 1.0)
        er = min((r["eye_ratio"] or 0.0) / 0.12, 1.0)
        q = 40.0 * fr + 25.0 * sh + 25.0 * er
    return q + (8.0 if r.get("kind") == "clip" else 0.0)


def apply(cands, filters, excluded=None):
    """Loc + rai deu theo thoi gian. Tra ve dict ket qua day du cho UI."""
    f = merge(filters)
    excluded = set(excluded or ())

    passed, rejected, reasons = [], [], {}
    for r in cands:
        key = f"{r['asset_id']}:{r['fidx']}"
        if key in excluded:
            r = dict(r, reason=MANUAL_REASON, score=_score(r))
            rejected.append(r)
            reasons[MANUAL_REASON] = reasons.get(MANUAL_REASON, 0) + 1
            continue
        why = _reject(r, f)
        if why:
            r = dict(r, reason=why, score=_score(r))
            rejected.append(r)
            head = why.split("(")[0].split(">")[0].strip()
            reasons[head] = reasons.get(head, 0) + 1
        else:
            passed.append(dict(r, reason=None, score=_score(r)))

    s = get()
    if f["mode"] == "story":
        kept, dropped, meta = story.build(passed, f, hard_cap=s.max_frames)
        why, head = ("this chapter already has a better photo",
                     "not picked for its chapter")
    else:
        kept, dropped = _spread(passed, f)
        meta = None
        why, head = ("a better photo exists in the same period",
                     "same period as a better photo")
    for r in dropped:
        r["reason"] = why
    if dropped:
        reasons[head] = reasons.get(head, 0) + len(dropped)
    rejected.extend(dropped)

    if len(kept) > s.max_frames:
        for r in kept[s.max_frames:]:
            r["reason"] = f"over the {s.max_frames} frame limit"
        rejected.extend(kept[s.max_frames:])
        kept = kept[:s.max_frames]

    for i, r in enumerate(kept):
        r["ord"] = i

    out = {
        "filters": f,
        "n_candidate": len(cands),
        "n_pass": len(passed),
        "n_selected": len(kept),
        "n_rejected": len(rejected),
        "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "selected": kept,
        "rejected": rejected,
        "timeline": _timeline(kept),
        "gaps": _gaps(kept, f["bucket_days"]),
    }
    out["story"] = _story_info(kept, meta)
    return out


def _story_info(kept, meta):
    """Tom tat cau chuyen cho UI: bao nhieu chuong, dai bao nhieu giay.

    Thoi luong o day CHUA tinh the tieu de va mo/dong man — day la con so cua
    buoc chon anh. Con so chinh xac den tung frame nam o /storyboard, tinh sau
    khi biet fps va cac tham so cua buoc render.
    """
    if not meta:
        return None
    p = meta["plan"]
    n_hero = sum(1 for r in kept if r.get("hero"))
    est = story.estimate(n_hero, len(kept) - n_hero, p)
    return {
        "grain": meta["grain"], "grain_label": meta["grain_label"],
        "n_chapter": len(meta["chapters"]), "chapters": meta["chapters"],
        "n_hero": n_hero, "pace": p["pace"],
        "target_seconds": p["target_seconds"],      # None = tu suy
        # est_seconds tinh theo do dai THAT cua tung shot, ke ca doan video —
        # story.build() da tra ve san trong meta, khong tinh lai bang cong thuc
        # chi biet anh tinh.
        "est_seconds": meta["seconds"],
        "hold_hero": p["hold_hero"], "hold_beat": p["hold_beat"],
        "capped": meta["capped"], "exhausted": meta["exhausted"],
        "max_per_chapter": p["max_per_chapter"],
        "auto": meta["auto"], "n_clip": meta["n_clip"],
        "seconds": meta["seconds"],
    }


def _spread(passed, f):
    """Chia timeline thanh o bang nhau, moi o giu per_bucket anh diem cao nhat."""
    if not passed:
        return [], []
    days = max(1, int(f["bucket_days"]))
    per = max(1, int(f["per_bucket"]))
    base = min(_ts(r["taken_at"]) for r in passed)

    buckets = {}
    for r in passed:
        b = int((_ts(r["taken_at"]) - base) // (days * 86400))
        r["bucket"] = b
        buckets.setdefault(b, []).append(r)

    kept, dropped = [], []
    for b in sorted(buckets):
        items = sorted(buckets[b], key=lambda r: -r["score"])
        kept.extend(items[:per])
        dropped.extend(items[per:])
    kept.sort(key=lambda r: _ts(r["taken_at"]))
    return kept, dropped


def _timeline(kept):
    """Dem so frame moi nam, de UI ve thanh bieu do phan bo."""
    by = {}
    for r in kept:
        y = _dt(r["taken_at"]).year
        by[y] = by.get(y, 0) + 1
    return [{"year": y, "n": n} for y, n in sorted(by.items())]


def _gaps(kept, bucket_days):
    """Khoang trong dai bat thuong: cho nguoi dung biet cho nao thieu anh."""
    if len(kept) < 2:
        return []
    thr = max(90, int(bucket_days) * 4)
    out = []
    for a, b in zip(kept, kept[1:]):
        d = (_ts(b["taken_at"]) - _ts(a["taken_at"])) / 86400.0
        if d >= thr:
            out.append({"from": _iso(a["taken_at"]), "to": _iso(b["taken_at"]),
                        "days": int(d)})
    return sorted(out, key=lambda x: -x["days"])[:12]


# ------------------------------------------------------------------ time utils
# Dung chung voi story.py de hai ben khong bao gio tinh lech mui gio.
_dt, _ts, _iso = story.dt, story.ts, story.iso


def suggest(cands):
    """Nguong mac dinh cho lan dau mo mot nguoi.

    Che do 'story' khong can suy gi: so anh do ngan sach thoi luong quyet dinh,
    va do tho cua chuong tu chon theo do dai hanh trinh. Chi bucket_days cua che
    do 'even' can suy tu do day du lieu, tinh san de ai chuyen sang do khong
    phai mo mot con so vo nghia.
    """
    out = dict(DEFAULTS)
    if len(cands) < 2:
        return out
    t = sorted(_ts(r["taken_at"]) for r in cands)
    span_days = max(1.0, (t[-1] - t[0]) / 86400.0)
    target = min(400, max(60, len(cands) // 3))     # nham 150-400 frame
    out["bucket_days"] = int(max(7, min(365, round(span_days / target))))
    return out
