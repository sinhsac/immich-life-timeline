"""Du an video: mot nguoi + mot bo filter + danh sach frame da chot.

Luu vao db de dong may / mo lai trang khong mat cong tinh chinh lai nguong,
va de buoc render biet phai lay dung nhung anh nao.
"""
import json

from . import select as SEL
from .db import rows
from .settings import get


def create(person_ids, name=None, date_from=None, date_to=None, filters=None):
    """person_ids: mot hoac nhieu cluster cua CUNG mot nguoi."""
    s = get()
    if isinstance(person_ids, str):
        person_ids = [person_ids]
    person_ids = [str(p) for p in dict.fromkeys(person_ids) if p]
    if not person_ids:
        raise ValueError("chua chon cluster nao")

    cands = SEL.fetch(person_ids, date_from, date_to)
    if not cands:
        raise ValueError("nhung cluster nay khong co anh nao du dieu kien "
                         "(can state=1 va co landmark)")
    f = SEL.merge(filters) if filters else SEL.suggest(cands)
    res = SEL.apply(cands, f)
    pname = next((c.get("person_name") for c in cands if c.get("person_name")), None)

    with rows() as (c, cur):
        cur.execute(
            f"INSERT INTO {s.table('project')}"
            f"(name,person_id,person_ids,person_name,date_from,date_to,filters,"
            f" n_candidate,n_selected)"
            f" VALUES(%s,%s,%s::uuid[],%s,%s,%s,%s::jsonb,%s,%s) RETURNING id",
            (name or (pname or "video"), person_ids[0], person_ids, pname,
             date_from, date_to, json.dumps(res["filters"]),
             res["n_candidate"], res["n_selected"]))
        pid = cur.fetchone()["id"]
        _write_frames(cur, s, pid, res)
        c.commit()
    return pid, res


def get_project(project_id):
    s = get()
    with rows() as (c, cur):
        cur.execute(f"SELECT * FROM {s.table('project')} WHERE id=%s",
                    (project_id,))
        p = cur.fetchone()
        c.rollback()
    if not p:
        raise KeyError(f"khong co project {project_id}")
    p["person_id"] = str(p["person_id"])
    # project cu (truoc khi co person_ids) van chay duoc
    p["person_ids"] = [str(x) for x in (p.get("person_ids") or [p["person_id"]])]
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
    cands = SEL.fetch(p["person_ids"], df, dt)
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
            f"   reason=CASE WHEN EXCLUDED.excluded THEN 'bo tay' ELSE NULL END",
            (project_id, asset_id, fidx, bool(excluded),
             "bo tay" if excluded else None))
        c.commit()


def rename(project_id, name):
    s = get()
    with rows() as (c, cur):
        cur.execute(f"UPDATE {s.table('project')} SET name=%s, updated_at=now() "
                    f"WHERE id=%s", (name, project_id))
        c.commit()


def frames(project_id):
    """Danh sach frame da chon, dung thu tu video. Dung cho buoc render."""
    s = get()
    with rows() as (c, cur):
        cur.execute(
            f"SELECT pf.ord, pf.asset_id, pf.fidx, pf.taken_at,"
            f"       a.preview_path, f.kps"
            f" FROM {s.table('project_frame')} pf"
            f" JOIN {s.table('asset')} a ON a.id = pf.asset_id"
            f" JOIN {s.table('face')} f"
            f"   ON f.asset_id = pf.asset_id AND f.fidx = pf.fidx"
            f" WHERE pf.project_id=%s AND pf.ord IS NOT NULL AND NOT pf.excluded"
            f" ORDER BY pf.ord", (project_id,))
        out = [dict(r) for r in cur.fetchall()]
        c.rollback()
    return out


def _write_frames(cur, s, project_id, res, keep_excluded=False):
    tbl = s.table("project_frame")
    if keep_excluded:
        cur.execute(f"DELETE FROM {tbl} WHERE project_id=%s AND NOT excluded",
                    (project_id,))
        cur.execute(f"UPDATE {tbl} SET ord=NULL WHERE project_id=%s", (project_id,))
    else:
        cur.execute(f"DELETE FROM {tbl} WHERE project_id=%s", (project_id,))

    ins = (f"INSERT INTO {tbl}"
           f"(project_id,asset_id,fidx,ord,taken_at,bucket,score,excluded,reason)"
           f" VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)"
           f" ON CONFLICT(project_id,asset_id,fidx) DO UPDATE SET"
           f"   ord=EXCLUDED.ord, taken_at=EXCLUDED.taken_at,"
           f"   bucket=EXCLUDED.bucket, score=EXCLUDED.score,"
           f"   reason=CASE WHEN {tbl}.excluded THEN {tbl}.reason"
           f"               ELSE EXCLUDED.reason END")

    batch = [(project_id, r["asset_id"], r["fidx"], r.get("ord"),
              r["taken_at"], r.get("bucket"), r.get("score"), False,
              r.get("reason")) for r in res["selected"]]
    batch += [(project_id, r["asset_id"], r["fidx"], None, r["taken_at"],
               r.get("bucket"), r.get("score"), r.get("reason") == "bo tay",
               r.get("reason")) for r in res["rejected"]]
    if batch:
        cur.executemany(ins, batch)
