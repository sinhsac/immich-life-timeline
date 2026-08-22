"""Connection pool + bang rieng cua service.

Service doc fp_asset / fp_face / fp_body do job indexer tao ra, va ghi vao
ba bang rieng cua no:

  fp_project        mot du an video = mot nguoi + mot bo filter
  fp_project_frame  ket qua chon anh, ke ca anh bi loai tay
  fp_render         lan render video, de theo tien do

Khong bao gio ghi vao bang cua Immich, cung khong sua fp_asset / fp_face.
"""
import contextlib

import psycopg
from psycopg_pool import ConnectionPool

from .settings import get

DDL = """
CREATE TABLE IF NOT EXISTS {project}(
  id            bigserial PRIMARY KEY,
  name          text NOT NULL,
  -- person_id: cluster chinh, giu lai cho tuong thich nguoc
  -- person_ids: TAT CA cluster cua cung mot nguoi. Immich hay tach mot nguoi
  --             thanh nhieu cluster khi khoang thoi gian dai.
  person_id     uuid NOT NULL,
  person_ids    uuid[],
  -- subjects: [[cluster cua nguoi 1...], [cluster cua nguoi 2...]]
  -- together: chi lay anh co mat DU tat ca nhung nguoi do
  subjects      jsonb,
  together      boolean DEFAULT false,
  person_name   text,
  date_from     date,
  date_to       date,
  filters       jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  n_candidate   int DEFAULT 0,
  n_selected    int DEFAULT 0,
  created_at    timestamptz DEFAULT now(),
  updated_at    timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {frame}(
  project_id    bigint NOT NULL REFERENCES {project}(id) ON DELETE CASCADE,
  asset_id      uuid NOT NULL,
  fidx          smallint NOT NULL,
  ord           int,                  -- thu tu trong video, NULL = khong duoc chon
  taken_at      timestamptz,
  fidx2         smallint,             -- mat cua NGUOI THU HAI trong cung anh
  -- kind='clip' thi fidx la SO AM (-1-cidx) va shot lay tu fp_vclip
  kind          text DEFAULT 'image',
  person_id     uuid,                 -- can de tim lai dung dong fp_vclip
  t_start_ms    int,
  t_end_ms      int,
  bucket        int,                  -- so CHUONG (mode story) hoac so o (even)
  label         text,                 -- nhan chuong: '2019', 'Tháng 3 2019'...
  hero          boolean DEFAULT false,-- anh chu dao cua chuong, duoc giu lau hon
  score         real,
  excluded      boolean DEFAULT false,-- nguoi bo tay trong UI
  reason        text,                 -- ly do bi loai tu dong
  PRIMARY KEY(project_id, asset_id, fidx)
);

CREATE TABLE IF NOT EXISTS {render}(
  id            bigserial PRIMARY KEY,
  project_id    bigint NOT NULL REFERENCES {project}(id) ON DELETE CASCADE,
  status        text NOT NULL DEFAULT 'queued',
  -- queued | frames | encoding | audio | done | error
  options       jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  n_total       int DEFAULT 0,
  n_done        int DEFAULT 0,
  video_path    text,
  duration_s    real,
  err           text,
  started_at    timestamptz DEFAULT now(),
  finished_at   timestamptz
);

CREATE INDEX IF NOT EXISTS {p}frame_sel   ON {frame}(project_id, ord);
CREATE INDEX IF NOT EXISTS {p}render_proj ON {render}(project_id, id DESC);

-- Nang cap ban cu: project truoc day chi co mot cluster.
ALTER TABLE {project} ADD COLUMN IF NOT EXISTS person_ids uuid[];
UPDATE {project} SET person_ids = ARRAY[person_id] WHERE person_ids IS NULL;

-- Nang cap sang ban ke chuyen: frame co them chuong va co danh dau anh chu dao.
ALTER TABLE {frame} ADD COLUMN IF NOT EXISTS label text;
ALTER TABLE {frame} ADD COLUMN IF NOT EXISTS hero boolean DEFAULT false;
ALTER TABLE {frame} ADD COLUMN IF NOT EXISTS fidx2 smallint;

-- Nang cap sang ban co doan video.
ALTER TABLE {frame} ADD COLUMN IF NOT EXISTS kind text DEFAULT 'image';
ALTER TABLE {frame} ADD COLUMN IF NOT EXISTS person_id uuid;
ALTER TABLE {frame} ADD COLUMN IF NOT EXISTS t_start_ms int;
ALTER TABLE {frame} ADD COLUMN IF NOT EXISTS t_end_ms int;

-- Video nhieu nguoi: subjects la danh sach NHOM cluster, moi nhom mot nguoi.
-- Dung jsonb chu khong phai uuid[][] vi Postgres khong cho mang long le rang
-- (moi nhom co so cluster khac nhau).
ALTER TABLE {project} ADD COLUMN IF NOT EXISTS subjects jsonb;
ALTER TABLE {project} ADD COLUMN IF NOT EXISTS together boolean DEFAULT false;
"""

_pool = None


def dsn(s):
    return (f"host={s.pg_host} port={s.pg_port} dbname={s.pg_db} "
            f"user={s.pg_user} password={s.pg_password} connect_timeout=10")


def pool():
    global _pool
    if _pool is None:
        s = get()
        _pool = ConnectionPool(dsn(s), min_size=1, max_size=s.pool_max,
                               kwargs={"autocommit": False}, open=True,
                               timeout=15)
    return _pool


@contextlib.contextmanager
def conn():
    with pool().connection() as c:
        yield c


@contextlib.contextmanager
def rows():
    """Cursor tra ve dict, tien cho API JSON."""
    with pool().connection() as c:
        with c.cursor(row_factory=psycopg.rows.dict_row) as cur:
            yield c, cur


def ensure_schema():
    s = get()
    names = dict(p=s.prefix, project=s.table("project"),
                 frame=s.table("project_frame"), render=s.table("render"))
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(DDL.format(**names))
        c.commit()


def indexer_ready():
    """Kiem tra job indexer da chay chua. Tra ve (ok, message)."""
    s = get()
    need = [f"{s.prefix}asset", f"{s.prefix}face", f"{s.prefix}body"]
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name = ANY(%s)",
                (s.pg_schema, need))
            got = {r[0] for r in cur.fetchall()}
            missing = [n for n in need if n not in got]
            if missing:
                return False, ("chua thay bang " + ", ".join(missing)
                               + " - chay job indexer truoc")
            cur.execute(f"SELECT COUNT(*) FROM {s.table('face')} WHERE state=1")
            n_face = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM {s.table('face')} "
                        f"WHERE state=1 AND kps IS NOT NULL")
            n_kps = cur.fetchone()[0]
        c.rollback()
    if not n_face:
        return False, "fp_face has no rows with state=1 - run the landmarks stage"
    if not n_kps:
        return False, ("fp_face has no kps - cannot align, rerun the landmarks "
                       "stage")
    return True, f"{n_face} faces ready, {n_kps} with landmarks"


_PROGRESS = """
SELECT COUNT(*)                                  AS n_asset,
       COUNT(*) FILTER (WHERE face_state = 0)    AS face_wait,
       COUNT(*) FILTER (WHERE face_state = 2)    AS face_todo,
       COUNT(*) FILTER (WHERE face_state = 1)    AS face_done,
       COUNT(*) FILTER (WHERE face_state = -1)   AS face_err,
       COUNT(*) FILTER (WHERE body_state = 0)    AS body_todo,
       COUNT(*) FILTER (WHERE body_state = 1)    AS body_done,
       COUNT(*) FILTER (WHERE body_state = -1)   AS body_err
FROM {asset}
"""


def progress():
    """Tien do job indexer, du de ve thanh progress tren UI.

    Vong doi mot anh: face_state 0 -> 2 (stage faces) -> 1 (stage landmarks),
    va body_state 0 -> 1 (stage bodies). -1 la loi doc anh, tinh la da xu ly
    vi job khong tu thu lai (can --reset errors).
    """
    s = get()
    with rows() as (c, cur):
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s",
            (s.pg_schema, f"{s.prefix}asset"))
        if cur.fetchone() is None:
            c.rollback()
            return {"ready": False, "detail": "no fp_asset table yet"}

        cur.execute(_PROGRESS.format(asset=s.table("asset")))
        p = dict(cur.fetchone())
        cur.execute(f"SELECT COUNT(*) n FROM {s.table('face')}")
        p["n_face"] = cur.fetchone()["n"]
        cur.execute(f"SELECT COUNT(*) n FROM {s.table('face')} WHERE state=1")
        p["n_face_ready"] = cur.fetchone()["n"]
        cur.execute(f"SELECT COUNT(*) n FROM {s.table('body')}")
        p["n_body"] = cur.fetchone()["n"]

        runs = []
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s",
            (s.pg_schema, f"{s.prefix}run"))
        if cur.fetchone() is not None:
            cur.execute(
                f"SELECT stage, started_at, finished_at, n_done, n_err, note "
                f"FROM {s.table('run')} ORDER BY id DESC LIMIT 5")
            for r in cur.fetchall():
                r = dict(r)
                for k in ("started_at", "finished_at"):
                    if r.get(k) is not None:
                        r[k] = r[k].isoformat()
                r["running"] = r["finished_at"] is None
                runs.append(r)
        c.rollback()

    total = max(1, p["n_asset"])
    p["stages"] = [
        {"name": "faces",
         "label": "Copy faces from Immich",
         "done": p["face_todo"] + p["face_done"] + p["face_err"],
         "total": p["n_asset"]},
        {"name": "landmarks",
         "label": "Head pose (1k3d68)",
         "done": p["face_done"] + p["face_err"],
         "total": p["n_asset"]},
        {"name": "bodies",
         "label": "Body pose (yolov8n-pose)",
         "done": p["body_done"] + p["body_err"],
         "total": p["n_asset"]},
    ]
    for st in p["stages"]:
        st["pct"] = round(100.0 * st["done"] / total, 1)
    p["runs"] = runs
    p["running"] = next((r["stage"] for r in runs if r["running"]), None)
    p["ready"] = True
    return p


def close():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
