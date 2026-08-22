"""Du an video: mot nguoi + mot bo filter + danh sach frame da chot.

Luu vao db de dong may / mo lai trang khong mat cong tinh chinh lai nguong,
va de buoc render biet phai lay dung nhung anh nao.
"""
import json

from . import select as SEL
from .db import rows
from .settings import get


def create(subjects, name=None, date_from=None, date_to=None, filters=None,
           together=False):
    """subjects: mot hoac nhieu NGUOI, moi nguoi la mot danh sach cluster.

    Xem SEL.groups_of() cho cac dang duoc chap nhan. together=True thi chi lay
    anh co mat du tat ca nhung nguoi do.
    """
    s = get()
    grps = SEL.groups_of(subjects)
    if not grps:
        raise ValueError("no cluster was selected")
    flat = [p for p in dict.fromkeys(pid for g in grps for pid in g)]
    together = bool(together) and len(grps) > 1

    cands = SEL.fetch(grps, date_from, date_to, together,
                      (filters or {}).get("use_clips", True))
    if not cands:
        raise ValueError(
            "no eligible photo found"
            + (" containing every selected person" if together else "")
            + " (state=1 and landmarks are required)")
    f = SEL.merge(filters) if filters else SEL.suggest(cands)
    res = SEL.apply(cands, f)
    pname = _title(cands, grps)

    with rows() as (c, cur):
        cur.execute(
            f"INSERT INTO {s.table('project')}"
            f"(name,person_id,person_ids,subjects,together,person_name,"
            f" date_from,date_to,filters,n_candidate,n_selected)"
            f" VALUES(%s,%s,%s::uuid[],%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s,%s)"
            f" RETURNING id",
            (name or (pname or "video"), flat[0], flat, json.dumps(grps),
             together, pname, date_from, date_to, json.dumps(res["filters"]),
             res["n_candidate"], res["n_selected"]))
        pid = cur.fetchone()["id"]
        _write_frames(cur, s, pid, res)
        c.commit()
    return pid, res


def _title(cands, grps):
    """Ten hien thi: 'Khue' hoac 'Khue & Minh' cho video hai nguoi.

    Ten cua nguoi thu hai co the chi xuat hien o person_name2 (dong ket qua la
    mat cua nguoi chinh), nen phai gom tu ca hai cho.
    """
    seen = {}
    for c in cands:
        for gi, nm in ((c.get("group"), c.get("person_name")),
                       (c.get("group2"), c.get("person_name2"))):
            if gi is not None and nm and gi not in seen:
                seen[gi] = nm
        if len(seen) >= len(grps):
            break
    names = [seen[gi] for gi in sorted(seen)]
    return " & ".join(dict.fromkeys(names)) if names else None


def get_project(project_id):
    s = get()
    with rows() as (c, cur):
        cur.execute(f"SELECT * FROM {s.table('project')} WHERE id=%s",
                    (project_id,))
        p = cur.fetchone()
        c.rollback()
    if not p:
        raise KeyError(f"no project {project_id}")
    p["person_id"] = str(p["person_id"])
    # project cu (truoc khi co person_ids / subjects) van chay duoc
    p["person_ids"] = [str(x) for x in (p.get("person_ids") or [p["person_id"]])]
    p["subjects"] = p.get("subjects") or [p["person_ids"]]
    p["together"] = bool(p.get("together"))
    for k in ("date_from", "date_to", "created_at", "updated_at"):
        if p.get(k) is not None:
            p[k] = p[k].isoformat()
    return p


def listing():
    s = get()
    with rows() as (c, cur):
        cur.execute(
            f"SELECT p.id,p.name,p.person_id,p.person_name,p.n_candidate,"
            f"       p.n_selected,p.created_at,"
            f"       (SELECT r.status FROM {s.table('render')} r "
            f"         WHERE r.project_id=p.id ORDER BY r.id DESC LIMIT 1) last_render"
            f" FROM {s.table('project')} p ORDER BY p.id DESC LIMIT 100")
        out = []
        for r in cur.fetchall():
            r["person_id"] = str(r["person_id"])
            r["created_at"] = r["created_at"].isoformat()
            out.append(r)
        c.rollback()
    return out


def delete(project_id):
    s = get()
    with rows() as (c, cur):
        cur.execute(f"DELETE FROM {s.table('project')} WHERE id=%s", (project_id,))
        c.commit()


def excluded_keys(project_id):
    s = get()
    with rows() as (c, cur):
        cur.execute(
            f"SELECT asset_id,fidx FROM {s.table('project_frame')} "
            f"WHERE project_id=%s AND excluded", (project_id,))
        out = {f"{r['asset_id']}:{r['fidx']}" for r in cur.fetchall()}
        c.rollback()
    return out


def recompute(project_id, filters=None, date_from=None, date_to=None):
    """Ap filter moi. Giu nguyen cac anh nguoi dung da bo tay."""
    p = get_project(project_id)
    s = get()
    df = date_from if date_from is not None else p["date_from"]
    dt = date_to if date_to is not None else p["date_to"]
    f = SEL.merge({**(p["filters"] or {}), **(filters or {})})
    cands = SEL.fetch(p["subjects"], df, dt, p["together"], f["use_clips"])
    res = SEL.apply(cands, f, excluded_keys(project_id))

    with rows() as (c, cur):
        cur.execute(
            f"UPDATE {s.table('project')} SET filters=%s::jsonb, date_from=%s,"
            f" date_to=%s, n_candidate=%s, n_selected=%s, updated_at=now()"
            f" WHERE id=%s",
            (json.dumps(res["filters"]), df, dt, res["n_candidate"],
             res["n_selected"], project_id))
        _write_frames(cur, s, project_id, res, keep_excluded=True)
        c.commit()
    return res


def set_excluded(project_id, asset_id, fidx, excluded=True):
    """Bo / lay lai mot anh bang tay trong UI."""
    s = get()
    with rows() as (c, cur):
        cur.execute(
            f"INSERT INTO {s.table('project_frame')}"
            f"(project_id,asset_id,fidx,excluded,reason)"
            f" VALUES(%s,%s,%s,%s,%s)"
            f" ON CONFLICT(project_id,asset_id,fidx) DO UPDATE SET"
            f"   excluded=EXCLUDED.excluded,"
            # Lay thang tu EXCLUDED de chuoi ly do chi ton tai o MOT cho
            # (SEL.MANUAL_REASON) — truoc day no bi lap lai trong SQL.
            f"   reason=EXCLUDED.reason",
            (project_id, asset_id, fidx, bool(excluded),
             SEL.MANUAL_REASON if excluded else None))
        c.commit()


def rename(project_id, name):
    s = get()
    with rows() as (c, cur):
        cur.execute(f"UPDATE {s.table('project')} SET name=%s, updated_at=now() "
                    f"WHERE id=%s", (name, project_id))
        c.commit()


def frames(project_id):
    """Danh sach frame da chon, dung thu tu video. Dung cho buoc render.

    Keo theo bucket/label/hero: buoc render can biet chuong nao va anh nao la
    chu dao de chia thoi luong. Tinh lai o day thi co nguy co lech voi cai
    nguoi dung da nhin thay o buoc 3 — nen doc dung cai da luu.
    """
    s = get()
    has_clip = _vclip_exists(s)
    clip_cols = (", vc.track, vc.motion, vc.t_peak_ms" if has_clip
                 else ", NULL::bytea AS track, NULL::real AS motion,"
                      " NULL::int AS t_peak_ms")
    clip_join = (
        f" LEFT JOIN {s.table('vclip')} vc"
        f"   ON pf.kind = 'clip' AND vc.asset_id = pf.asset_id"
        f"  AND vc.person_id = pf.person_id AND vc.cidx = (-1 - pf.fidx)"
        if has_clip else "")
    with rows() as (c, cur):
        # LEFT JOIN vao fp_face, khong phai JOIN: doan video co fidx AM nen
        # khong co dong face nao khop, INNER JOIN se lam bien mat het clip.
        cur.execute(
            f"SELECT pf.ord, pf.asset_id, pf.fidx, pf.fidx2, pf.taken_at,"
            f"       pf.kind, pf.person_id, pf.t_start_ms, pf.t_end_ms,"
            f"       pf.bucket, pf.label, pf.hero, pf.score,"
            f"       a.preview_path, a.video_path, a.dur_ms,"
            f"       f.kps, f2.kps AS kps2{clip_cols}"
            f" FROM {s.table('project_frame')} pf"
            f" JOIN {s.table('asset')} a ON a.id = pf.asset_id"
            f" LEFT JOIN {s.table('face')} f"
            f"   ON f.asset_id = pf.asset_id AND f.fidx = pf.fidx"
            f" LEFT JOIN {s.table('face')} f2"
            f"   ON f2.asset_id = pf.asset_id AND f2.fidx = pf.fidx2"
            f"{clip_join}"
            f" WHERE pf.project_id=%s AND pf.ord IS NOT NULL AND NOT pf.excluded"
            f"   AND (pf.kind = 'clip' OR f.kps IS NOT NULL)"
            f" ORDER BY pf.ord", (project_id,))
        out = [dict(r) for r in cur.fetchall()]
        c.rollback()
    for r in out:
        if r.get("kind") == "clip" and r.get("t_end_ms") is not None:
            r["dur_s"] = max(0.1, (r["t_end_ms"] - r["t_start_ms"]) / 1000.0)
    return out


_vclip = {"at": 0.0, "ok": False}


def _vclip_exists(s):
    """fp_vclip chi co sau khi indexer chay ban co video. Cache 60s."""
    import time
    if time.time() - _vclip["at"] < 60.0:
        return _vclip["ok"]
    with rows() as (c, cur):
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s",
            (s.pg_schema, f"{s.prefix}vclip"))
        ok = cur.fetchone() is not None
        c.rollback()
    _vclip.update(at=time.time(), ok=ok)
    return ok


def _write_frames(cur, s, project_id, res, keep_excluded=False):
    tbl = s.table("project_frame")
    if keep_excluded:
        cur.execute(f"DELETE FROM {tbl} WHERE project_id=%s AND NOT excluded",
                    (project_id,))
        cur.execute(f"UPDATE {tbl} SET ord=NULL WHERE project_id=%s", (project_id,))
    else:
        cur.execute(f"DELETE FROM {tbl} WHERE project_id=%s", (project_id,))

    ins = (f"INSERT INTO {tbl}"
           f"(project_id,asset_id,fidx,fidx2,kind,person_id,t_start_ms,t_end_ms,"
           f" ord,taken_at,bucket,label,hero,score,excluded,reason)"
           f" VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
           f" ON CONFLICT(project_id,asset_id,fidx) DO UPDATE SET"
           f"   ord=EXCLUDED.ord, taken_at=EXCLUDED.taken_at,"
           f"   fidx2=EXCLUDED.fidx2, kind=EXCLUDED.kind,"
           f"   person_id=EXCLUDED.person_id,"
           f"   t_start_ms=EXCLUDED.t_start_ms, t_end_ms=EXCLUDED.t_end_ms,"
           f"   bucket=EXCLUDED.bucket, label=EXCLUDED.label,"
           f"   hero=EXCLUDED.hero, score=EXCLUDED.score,"
           f"   reason=CASE WHEN {tbl}.excluded THEN {tbl}.reason"
           f"               ELSE EXCLUDED.reason END")

    def row(r, ordv, excluded):
        return (project_id, r["asset_id"], r["fidx"], r.get("fidx2"),
                r.get("kind") or "image",
                str(r["person_id"]) if r.get("person_id") else None,
                r.get("t_start_ms"), r.get("t_end_ms"),
                ordv, r["taken_at"], r.get("bucket"), r.get("label"),
                bool(r.get("hero")) and ordv is not None, r.get("score"),
                excluded, r.get("reason"))

    batch = [row(r, r.get("ord"), False) for r in res["selected"]]
    batch += [row(r, None, r.get("reason") == SEL.MANUAL_REASON)
              for r in res["rejected"]]
    if batch:
        cur.executemany(ins, batch)
