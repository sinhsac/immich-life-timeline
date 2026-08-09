"""Man hinh 2 + 3: lay anh cua mot nguoi roi loc theo pose.

Cach lam: mot query keo TOAN BO face cua nguoi do kem chi so, roi loc bang
Python. Vai nghin dong nen nhanh, va doi lai duoc hai thu quan trong:

  - moi anh bi loai deu co LY DO cu the -> UI giai thich duoc, nguoi dung biet
    phai keo nguong nao
  - doi filter khong phai viet lai SQL, thu nghiem tuc thi

Sau khi loc con chia timeline thanh cac o thoi gian bang nhau va chi lay vai
anh tot nhat moi o. Day la buoc quyet dinh video co "deu" hay khong: khong lam
thi mot chuyen di co 200 anh se chiem het video, con nhung nam it anh bi mat.
"""
from datetime import date, datetime, timezone

from .db import rows
from .settings import get

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
    # --- body pose ---
    "use_body": True,         # co dung du lieu fp_body de loc khong
    "postures": ["standing", "sitting", "unknown"],
    "orientations": ["front", "side", "unknown"],
    "allow_missing_body": True,   # khong detect ra nguoi thi van nhan
    "min_body_front": 0.0,
    # --- rai deu theo thoi gian ---
    "bucket_days": 30,
    "per_bucket": 1,
}

_FETCH = """
SELECT f.asset_id, f.fidx, a.taken_at, a.date_src, a.filename,
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
    """Gop filter nguoi dung gui len voi mac dinh, bo qua key la."""
    out = dict(DEFAULTS)
    for k, v in (filters or {}).items():
        if k in DEFAULTS and v is not None:
            out[k] = v
    return out


def fetch(person_ids, date_from=None, date_to=None):
    """Anh cua MOT HOAC NHIEU cluster. Immich hay tach mot nguoi thanh nhieu
    cluster theo do tuoi, gop lai moi ra duoc video ca hanh trinh."""
    if isinstance(person_ids, str):
        person_ids = [person_ids]
    person_ids = [str(p) for p in person_ids if p]
    if not person_ids:
        return []
    s = get()
    sql = _FETCH.format(face=s.table("face"), asset=s.table("asset"),
                        body=s.table("body"))
    with rows() as (c, cur):
        cur.execute(sql, (person_ids, date_from, date_from, date_to, date_to))
        got = [dict(r) for r in cur.fetchall()]
        c.rollback()
    return got


# --------------------------------------------------------------------- loc
def _reject(r, f):
    """Tra ve ly do loai, hoac None neu dat. Thu tu kiem tra tu re den dat."""
    yaw = abs(r["yaw"] or 999.0)
    pitch = abs(r["pitch"] or 999.0)
    roll = abs(r["roll"] or 999.0)
    if yaw > f["max_yaw"]:
        return f"quay dau {yaw:.0f}\u00b0 > {f['max_yaw']:.0f}\u00b0"
    if pitch > f["max_pitch"]:
        return f"ngua/cui {pitch:.0f}\u00b0 > {f['max_pitch']:.0f}\u00b0"
    if roll > f["max_roll"]:
        return f"nghieng dau {roll:.0f}\u00b0 > {f['max_roll']:.0f}\u00b0"

    fr = r["frontality"]
    if fr is not None and fr < f["min_frontality"]:
        return f"khong du chinh dien {fr:.2f} < {f['min_frontality']:.2f}"

    ear = r["ear"]
    if ear is not None and f["min_ear"] > 0 and ear < f["min_ear"]:
        return f"co ve nham mat (EAR {ear:.2f})"

    er = r["eye_ratio"]
    if er is not None and er < f["min_eye_ratio"]:
        return f"mat qua nho trong anh ({er * 100:.1f}%)"

    sh = r["sharp"]
    if sh is not None and sh < f["min_sharp"]:
        return f"mo (sharp {sh:.0f} < {f['min_sharp']:.0f})"

    br = r["bright"]
    if br is not None and not (f["bright_min"] <= br <= f["bright_max"]):
        return f"sang/toi qua ({br:.0f})"

    q = r["quality"]
    if q is not None and f["min_quality"] > 0 and q < f["min_quality"]:
        return f"diem chat luong thap ({q:.0f})"

    n_face = r["n_face"] or 1
    if not f["allow_others"] and n_face > 1:
        return f"anh co {n_face} nguoi"
    if f["max_faces"] and n_face > f["max_faces"]:
        return f"anh co {n_face} nguoi > {f['max_faces']}"

    if f["use_body"]:
        has_body = r["posture"] is not None or r["orientation"] is not None
        if not has_body:
            if not f["allow_missing_body"]:
                return "khong detect duoc than nguoi"
        else:
            if r["posture"] and r["posture"] not in f["postures"]:
                return f"tu the {r['posture']}"
            if r["orientation"] and r["orientation"] not in f["orientations"]:
                return f"huong than {r['orientation']}"
            bf = r["body_front"]
            if bf is not None and bf < f["min_body_front"]:
                return f"than khong chinh dien ({bf:.2f})"
    return None


def _score(r):
    """Diem de xep hang trong cung mot o thoi gian."""
    if r["quality"] is not None:
        return float(r["quality"])
    fr = r["frontality"] or 0.0
    sh = min((r["sharp"] or 0.0) / 400.0, 1.0)
    er = min((r["eye_ratio"] or 0.0) / 0.12, 1.0)
    return 40.0 * fr + 25.0 * sh + 25.0 * er


def apply(cands, filters, excluded=None):
    """Loc + rai deu theo thoi gian. Tra ve dict ket qua day du cho UI."""
    f = merge(filters)
    excluded = set(excluded or ())

    passed, rejected, reasons = [], [], {}
    for r in cands:
        key = f"{r['asset_id']}:{r['fidx']}"
        if key in excluded:
            r = dict(r, reason="bo tay", score=_score(r))
            rejected.append(r)
            reasons["bo tay"] = reasons.get("bo tay", 0) + 1
            continue
        why = _reject(r, f)
        if why:
            r = dict(r, reason=why, score=_score(r))
            rejected.append(r)
            head = why.split("(")[0].split(">")[0].strip()
            reasons[head] = reasons.get(head, 0) + 1
        else:
            passed.append(dict(r, reason=None, score=_score(r)))

    kept, dropped = _spread(passed, f)
    for r in dropped:
        r["reason"] = "da co anh tot hon trong cung giai doan"
        reasons["trung giai doan"] = reasons.get("trung giai doan", 0) + 1
    rejected.extend(dropped)

    s = get()
    if len(kept) > s.max_frames:
        for r in kept[s.max_frames:]:
            r["reason"] = f"vuot gioi han {s.max_frames} frame"
        rejected.extend(kept[s.max_frames:])
        kept = kept[:s.max_frames]

    for i, r in enumerate(kept):
        r["ord"] = i

    return {
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
def _dt(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _ts(v):
    d = _dt(v)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _iso(v):
    return _dt(v).isoformat()


def suggest(cands):
    """Goi y bucket_days tu do day cua du lieu, de lan dau mo len da hop ly."""
    if len(cands) < 2:
        return dict(DEFAULTS)
    ts = sorted(_ts(r["taken_at"]) for r in cands)
    span_days = max(1.0, (ts[-1] - ts[0]) / 86400.0)
    # nham khoang 150-400 frame cho ca hanh trinh
    target = min(400, max(60, len(cands) // 3))
    days = int(max(7, min(365, round(span_days / target))))
    out = dict(DEFAULTS)
    out["bucket_days"] = days
    return out
