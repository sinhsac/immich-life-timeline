"""Man hinh 1: danh sach cluster de chon.

Nguon la fp_face.person_id — chinh la cluster Immich da phan loai san. Khong
can API key, khong can goi Immich: moi thu da nam trong Postgres sau job indexer.

Mot nguoi thuong bi Immich tach thanh NHIEU cluster khi khoang thoi gian dai
(be, thieu nien, truong thanh). Vi vay ngoai listing() con co similar(): so
sanh vector trung tam cua cac cluster de goi y nhung cluster co ve la cung mot
nguoi, roi cho chon nhieu cluster vao cung mot du an.
"""
import time

import numpy as np

from .db import rows
from .settings import get

_LIST_SQL = """
SELECT f.person_id,
       MAX(f.person_name)                       AS person_name,
       COUNT(*)                                 AS n_face,
       COUNT(*) FILTER (WHERE f.state = 1)      AS n_ready,
       MIN(a.taken_at)                          AS first_seen,
       MAX(a.taken_at)                          AS last_seen,
       COUNT(DISTINCT date_trunc('month', a.taken_at)) AS n_month,
       AVG(f.frontality)                        AS avg_frontality
FROM {face} f
JOIN {asset} a ON a.id = f.asset_id
WHERE f.person_id IS NOT NULL AND a.taken_at IS NOT NULL
GROUP BY f.person_id
HAVING COUNT(*) FILTER (WHERE f.state = 1) >= %s
ORDER BY COUNT(*) DESC
"""

# Anh dai dien: mat ro nhat, chinh dien nhat cua nguoi do.
_COVER_SQL = """
SELECT f.asset_id, f.fidx
FROM {face} f
JOIN {asset} a ON a.id = f.asset_id
WHERE f.person_id = %s AND f.state = 1 AND f.kps IS NOT NULL
ORDER BY f.quality DESC NULLS LAST
LIMIT 1
"""


def listing(min_ready=3):
    s = get()
    sql = _LIST_SQL.format(face=s.table("face"), asset=s.table("asset"))
    cover = _COVER_SQL.format(face=s.table("face"), asset=s.table("asset"))
    out = []
    with rows() as (c, cur):
        cur.execute(sql, (min_ready,))
        people = cur.fetchall()
        for p in people:
            cur.execute(cover, (p["person_id"],))
            cv = cur.fetchone()
            out.append({
                "person_id": str(p["person_id"]),
                "name": p["person_name"] or "",
                "n_face": p["n_face"],
                "n_ready": p["n_ready"],
                "n_month": p["n_month"],
                "first_seen": _d(p["first_seen"]),
                "last_seen": _d(p["last_seen"]),
                "span_years": _span(p["first_seen"], p["last_seen"]),
                "avg_frontality": _r(p["avg_frontality"]),
                "cover": (f"{cv['asset_id']}/{cv['fidx']}" if cv else None),
            })
        c.rollback()
    # nguoi co nhieu thang xuat hien len truoc: phu hop lam video hanh trinh
    out.sort(key=lambda x: (-(x["n_month"] or 0), -x["n_ready"]))
    return out


def detail(person_id):
    """Phan bo anh theo nam, de biet khoang thoi gian nao du day."""
    s = get()
    sql = f"""
    SELECT date_part('year', a.taken_at)::int AS year,
           COUNT(*) AS n,
           COUNT(*) FILTER (WHERE f.frontality >= 0.45) AS n_frontal,
           AVG(f.quality) AS avg_q
    FROM {s.table('face')} f
    JOIN {s.table('asset')} a ON a.id = f.asset_id
    WHERE f.person_id = %s AND f.state = 1 AND a.taken_at IS NOT NULL
    GROUP BY year ORDER BY year
    """
    with rows() as (c, cur):
        cur.execute(sql, (person_id,))
        years = [{"year": r["year"], "n": r["n"], "n_frontal": r["n_frontal"],
                  "avg_quality": _r(r["avg_q"])} for r in cur.fetchall()]
        c.rollback()
    return {"person_id": str(person_id), "by_year": years}


# ============================================== goi y cluster cung mot nguoi
# Chi lay per_person face diem cao nhat moi cluster: du de vector trung tam on
# dinh ma khong phai keo ca 89k embedding (~180MB) qua duong day.
_CENTROID_SQL = """
SELECT person_id, emb FROM (
    SELECT f.person_id, f.emb,
           row_number() OVER (PARTITION BY f.person_id
                              ORDER BY f.quality DESC NULLS LAST) AS rn
    FROM {face} f
    WHERE f.person_id IS NOT NULL AND f.state = 1 AND f.emb IS NOT NULL
) t WHERE rn <= %s
"""

_META_SQL = """
SELECT f.person_id,
       MAX(f.person_name)                       AS person_name,
       COUNT(*)                                 AS n_face,
       COUNT(*) FILTER (WHERE f.state = 1)      AS n_ready,
       MIN(a.taken_at)                          AS first_seen,
       MAX(a.taken_at)                          AS last_seen,
       COUNT(DISTINCT date_trunc('month', a.taken_at)) AS n_month
FROM {face} f
JOIN {asset} a ON a.id = f.asset_id
WHERE f.person_id = ANY(%s::uuid[]) AND a.taken_at IS NOT NULL
GROUP BY f.person_id
"""

_cent = {"at": 0.0, "ids": None, "mat": None, "per": 0}


def centroids(per_person=16, ttl=600.0):
    """(ids, mat) — vector trung tam moi cluster, da chuan hoa L2.

    emb trong fp_face da duoc chuan hoa L2 o stage faces, nen trung binh roi
    chuan hoa lai la xong. Tinh mot lan mat vai chuc giay -> cache theo ttl.
    """
    now = time.time()
    if (_cent["mat"] is not None and _cent["per"] == per_person
            and now - _cent["at"] < ttl):
        return _cent["ids"], _cent["mat"]

    s = get()
    acc, cnt, dim = {}, {}, None
    with rows() as (c, cur):
        cur.execute(_CENTROID_SQL.format(face=s.table("face")), (int(per_person),))
        for r in cur:
            v = np.frombuffer(r["emb"], np.float32)
            if dim is None:
                dim = v.size
            if v.size != dim or v.size == 0:
                continue                      # bo qua ban ghi la kich thuoc
            pid = str(r["person_id"])
            if pid in acc:
                acc[pid] += v
                cnt[pid] += 1
            else:
                acc[pid] = v.astype(np.float64)
                cnt[pid] = 1
        c.rollback()

    ids = sorted(acc)
    if not ids:
        _cent.update(at=now, ids=[], mat=np.zeros((0, 0), np.float32),
                     per=per_person)
        return _cent["ids"], _cent["mat"]
    mat = np.stack([acc[p] / cnt[p] for p in ids])
    mat /= np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9)
    mat = mat.astype(np.float32)
    _cent.update(at=now, ids=ids, mat=mat, per=per_person)
    return ids, mat


def _meta(pids):
    """Thong tin hien thi cho mot nhom cluster, kem anh dai dien."""
    if not pids:
        return {}
    s = get()
    out = {}
    with rows() as (c, cur):
        cur.execute(_META_SQL.format(face=s.table("face"), asset=s.table("asset")),
                    (list(pids),))
        got = cur.fetchall()
        cover = _COVER_SQL.format(face=s.table("face"), asset=s.table("asset"))
        for p in got:
            pid = str(p["person_id"])
            cur.execute(cover, (p["person_id"],))
            cv = cur.fetchone()
            out[pid] = {
                "person_id": pid,
                "name": p["person_name"] or "",
                "n_face": p["n_face"],
                "n_ready": p["n_ready"],
                "n_month": p["n_month"],
                "first_seen": _d(p["first_seen"]),
                "last_seen": _d(p["last_seen"]),
                "span_years": _span(p["first_seen"], p["last_seen"]),
                "cover": (f"{cv['asset_id']}/{cv['fidx']}" if cv else None),
            }
        c.rollback()
    return out


def similar(person_id, limit=24, min_sim=0.25, per_person=16, seeds=None):
    """Cac cluster co ve cung mot nguoi voi person_id.

    Do bang cosine giua vector trung tam. Neu truyen 'seeds' (cac cluster da
    chon) thi so voi trung tam GOP cua ca nhom — chon them mot cluster xong
    goi lai se ra goi y sat hon, tuc la lan rong dan.
    """
    ids, mat = centroids(per_person)
    if not ids:
        return {"person_id": str(person_id), "n_cluster": 0, "similar": [],
                "detail": "chua co embedding nao (can chay stage faces)"}
    index = {p: i for i, p in enumerate(ids)}
    chosen = [str(p) for p in (seeds or [str(person_id)]) if str(p) in index]
    if not chosen:
        return {"person_id": str(person_id), "n_cluster": len(ids), "similar": [],
                "detail": "cluster nay khong co embedding"}

    q = mat[[index[p] for p in chosen]].mean(0)
    q /= max(float(np.linalg.norm(q)), 1e-9)
    sims = mat @ q

    skip = set(chosen)
    order = np.argsort(-sims)
    picked = [(ids[j], float(sims[j])) for j in order
              if ids[j] not in skip and sims[j] >= min_sim][:int(limit)]

    # Ten ma Immich (hoac ban) da dat la tin hieu manh hon ca cosine: nguoi
    # than co net giong nhau van dat 0.43-0.45, khong tach duoc bang nguong.
    # Cum da co ten KHAC thi gan nhu chac chan la nguoi khac -> danh dau va
    # day xuong duoi, nhung khong loai han vi ten cung co the dat sai.
    seed_names = {(m["name"] or "").strip().lower()
                  for m in _meta(chosen).values() if (m["name"] or "").strip()}

    meta = _meta([p for p, _ in picked])
    out = []
    for pid, sc in picked:
        m = meta.get(pid)
        if not m:
            continue
        nm = (m["name"] or "").strip().lower()
        m["similarity"] = round(sc, 4)
        m["name_conflict"] = bool(nm and seed_names and nm not in seed_names)
        out.append(m)
    out.sort(key=lambda m: (m["name_conflict"], -m["similarity"]))
    return {"person_id": str(person_id), "n_cluster": len(ids),
            "seeds": chosen, "seed_names": sorted(seed_names), "similar": out}


def _d(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _r(v, n=3):
    return None if v is None else round(float(v), n)


def _span(a, b):
    if not a or not b:
        return None
    return round((b - a).days / 365.25, 1)
