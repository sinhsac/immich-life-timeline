"""Ket noi Postgres + tao schema rieng cua job.

Nguyen tac: job CHI tao / ghi cac bang co prefix (mac dinh fp_). Bang cua Immich
chi doc, khong bao gio UPDATE hay DELETE.
"""
import contextlib
import time

DDL = """
CREATE TABLE IF NOT EXISTS {asset}(
  id            uuid PRIMARY KEY,
  filename      text,
  taken_at      timestamptz,          -- uu tien EXIF DateTimeOriginal
  date_src      text,                 -- 'exif' | 'local' | 'file'
  preview_path  text,                 -- duong dan trong container Immich
  img_w         int,
  img_h         int,
  n_face        int  DEFAULT 0,
  n_body        int  DEFAULT 0,
  face_state    smallint DEFAULT 0,   -- 0 cho | 1 xong | 2 co bbox+emb, cho landmark | -1 loi
  body_state    smallint DEFAULT 0,   -- 0 cho | 1 xong | -1 loi
  err           text,
  seen_at       timestamptz DEFAULT now(),
  updated_at    timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {face}(
  asset_id      uuid NOT NULL,
  fidx          smallint NOT NULL,
  immich_face   uuid,                 -- asset_face.id
  person_id     uuid,                 -- person da gan trong Immich (co the NULL)
  person_name   text,
  x1 real, y1 real, x2 real, y2 real, -- chuan hoa 0..1 theo anh goc
  det           real,
  yaw real, pitch real, roll real,    -- head pose, tu 1k3d68
  frontality    real,                 -- 0..1 gop pose + doi xung
  eye_px        real,               -- khoang cach 2 mat, pixel tren anh da resize
  eye_ratio     real,               -- eye_px / max(w,h): so sanh duoc giua cac anh
  sharp         real,
  bright        real,
  symm          real,
  ear           real,
  age           real,
  emb_norm      real,                 -- do dai vector ArcFace truoc chuan hoa
  quality       real,
  emb           bytea,                -- float32[512] da chuan hoa L2 (tuy COPY_EMBEDDING)
  -- kps/lmk68 luu toa do CHUAN HOA 0..1 (x/w, y/h, z/w) de doc lai o bat ky
  -- kich thuoc anh nao cung dung. Bat buoc cho buoc align khi render video.
  kps           bytea,                -- float32[5][2]
  lmk68         bytea,                -- float32[68][3]
  kps_src       text,                 -- 'pg' | 'lmk68' | 'det'
  state         smallint DEFAULT 0,   -- 0 cho landmark | 1 xong | -1 loi
  updated_at    timestamptz DEFAULT now(),
  PRIMARY KEY(asset_id, fidx)
);

CREATE TABLE IF NOT EXISTS {body}(
  asset_id      uuid NOT NULL,
  pidx          smallint NOT NULL,
  x1 real, y1 real, x2 real, y2 real, -- chuan hoa 0..1
  det           real,
  kps           bytea NOT NULL,       -- float32[17][3] = x,y,conf chuan hoa 0..1
  n_visible     smallint,
  orientation   text,                 -- 'front' | 'back' | 'side' | 'unknown'
  posture       text,                 -- 'standing' | 'sitting' | 'lying' | 'unknown'
  torso_deg     real,                 -- goc than so voi truc doc
  body_front    real,                 -- 0..1 do chinh dien cua than
  area_ratio    real,                 -- dien tich bbox / dien tich anh
  face_fidx     smallint,             -- khop voi {face}.fidx neu tim duoc
  updated_at    timestamptz DEFAULT now(),
  PRIMARY KEY(asset_id, pidx)
);

CREATE TABLE IF NOT EXISTS {run}(
  id            bigserial PRIMARY KEY,
  stage         text NOT NULL,
  started_at    timestamptz DEFAULT now(),
  finished_at   timestamptz,
  n_done        int DEFAULT 0,
  n_err         int DEFAULT 0,
  note          text
);

CREATE TABLE IF NOT EXISTS {state}(
  key   text PRIMARY KEY,
  value text
);

CREATE INDEX IF NOT EXISTS {p}asset_face_state ON {asset}(face_state);
CREATE INDEX IF NOT EXISTS {p}asset_body_state ON {asset}(body_state);
CREATE INDEX IF NOT EXISTS {p}asset_taken      ON {asset}(taken_at);
CREATE INDEX IF NOT EXISTS {p}face_state       ON {face}(state);
CREATE INDEX IF NOT EXISTS {p}face_person      ON {face}(person_id);
CREATE INDEX IF NOT EXISTS {p}body_orient      ON {body}(orientation);
CREATE INDEX IF NOT EXISTS {p}body_posture     ON {body}(posture);
"""


def driver():
    try:
        import psycopg
        return psycopg, 3
    except ImportError:
        pass
    try:
        import psycopg2
        return psycopg2, 2
    except ImportError:
        raise SystemExit('Can driver Postgres:  pip install "psycopg[binary]"')


def connect(s):
    s.require_pg()
    drv, ver = driver()
    kw = dict(host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
              user=s.pg_user, password=s.pg_password,
              connect_timeout=s.pg_timeout)
    last = None
    for attempt in range(5):
        try:
            conn = drv.connect(**kw)
            conn.autocommit = False
            return conn
        except Exception as e:                       # noqa: BLE001
            last = e
            wait = 2 ** attempt
            print(f"  ket noi pg that bai ({e}), thu lai sau {wait}s")
            time.sleep(wait)
    raise SystemExit(f"Khong ket noi duoc Postgres: {last}")


# Them cot vao db da ton tai. ADD COLUMN IF NOT EXISTS co tu Postgres 9.6.
MIGRATIONS = (
    "ALTER TABLE {face} ADD COLUMN IF NOT EXISTS eye_ratio real",
)


def ensure_schema(conn, s):
    names = dict(
        p=s.prefix, asset=s.table("asset"), face=s.table("face"),
        body=s.table("body"), run=s.table("run"), state=s.table("state"))
    with conn.cursor() as cur:
        cur.execute(DDL.format(**names))
        for sql in MIGRATIONS:
            cur.execute(sql.format(**names))
    conn.commit()


def _lock_key(s):
    """Khoa rieng theo db + prefix, khong dung chung voi bat ky ai khac."""
    import zlib
    raw = f"fp-indexer:{s.pg_db}:{s.pg_schema}:{s.prefix}".encode()
    # pg_try_advisory_lock nhan bigint; dua ve khoang co dau 32-bit cho gon.
    return zlib.crc32(raw) - 2 ** 31


def try_lock(conn, s):
    """Gianh khoa de KHONG BAO GIO co hai indexer chay cung luc.

    Khoa o muc session nen phai giu 'conn' mo suot job. Postgres tu nha khoa
    khi ket noi dut — ke ca khi may mat dien, nen khong bao gio bi khoa chet.

    Truoc day chi dua vao concurrencyPolicy: Forbid cua CronJob, nhung Forbid
    chi chan cac job DO CHINH CronJob sinh ra: mot Job tao tay van chay song
    song duoc, tai anh gap doi va tranh CPU voi Immich.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_lock_key(s),))
        got = bool(cur.fetchone()[0])
    conn.commit()
    return got


def close_orphan_runs(conn, s):
    """Dong cac dong fp_run bi bo lung tu lan chay truoc.

    run_log() mo dong luc bat dau va chi dong o khoi finally. May tat dien hoac
    bi SIGKILL thi finally khong chay -> dong do mo vinh vien, va
    /api/progress suy 'dang chay' tu dong chua dong nen bao sai mai mai.

    Goi ham nay SAU khi da gianh duoc khoa: luc do chac chan khong co job nao
    khac dang chay, nen moi dong con mo deu la xac chet.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {s.table('run')} "
            f"SET finished_at = COALESCE(finished_at, started_at), "
            f"    note = COALESCE(note,'') || ' [bo lung: job truoc khong ket thuc]' "
            f"WHERE finished_at IS NULL "
            f"RETURNING stage")
        dead = [r[0] for r in cur.fetchall()]
    conn.commit()
    if dead:
        print(f"  don {len(dead)} dong fp_run bo lung: {', '.join(dead)}")
    return len(dead)


@contextlib.contextmanager
def run_log(conn, s, stage):
    """Ghi lai moi lan chay mot stage vao bang fp_run de theo dau."""
    tbl = s.table("run")
    with conn.cursor() as cur:
        cur.execute(f"INSERT INTO {tbl}(stage) VALUES(%s) RETURNING id", (stage,))
        rid = cur.fetchone()[0]
    conn.commit()
    box = {"done": 0, "err": 0, "note": None}
    t0 = time.time()
    try:
        yield box
    finally:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {tbl} SET finished_at=now(), n_done=%s, n_err=%s, note=%s "
                f"WHERE id=%s",
                (box["done"], box["err"], box["note"], rid))
        conn.commit()
        print(f"  [{stage}] {box['done']} xong, {box['err']} loi, "
              f"{time.time() - t0:.1f}s")


def set_state(conn, s, key, value):
    tbl = s.table("state")
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {tbl}(key,value) VALUES(%s,%s) "
            f"ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
            (key, str(value)))


def get_state(conn, s, key, default=None):
    tbl = s.table("state")
    with conn.cursor() as cur:
        cur.execute(f"SELECT value FROM {tbl} WHERE key=%s", (key,))
        row = cur.fetchone()
    return row[0] if row else default
