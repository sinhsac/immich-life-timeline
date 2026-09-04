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
  kind          text DEFAULT 'image', -- 'image' | 'video'
  taken_at      timestamptz,          -- uu tien EXIF DateTimeOriginal
  date_src      text,                 -- 'exif' | 'local' | 'file'
  preview_path  text,                 -- duong dan trong container Immich
  video_path    text,                 -- chi voi kind='video'
  dur_ms        int,                  -- do dai video
  img_w         int,
  img_h         int,
  n_face        int  DEFAULT 0,
  n_body        int  DEFAULT 0,
  n_clip        int  DEFAULT 0,       -- so doan da chon ra tu video nay
  n_vface       int  DEFAULT 0,       -- so dong fp_vface sinh ra tu video nay
  n_vbody       int  DEFAULT 0,       -- so dong fp_vbody sinh ra tu video nay
  n_vframe      int  DEFAULT 0,       -- so frame da lay mau
  face_state    smallint DEFAULT 0,   -- 0 cho | 1 xong | 2 co bbox+emb, cho landmark | -1 loi
  body_state    smallint DEFAULT 0,   -- 0 cho | 1 xong | -1 loi
  clip_state    smallint DEFAULT 0,   -- 0 cho | 1 xong | -1 loi | 2 bo qua (khong phai video)
  err           text,
  seen_at       timestamptz DEFAULT now(),
  updated_at    timestamptz DEFAULT now()
);

-- Mot dong moi khuon mat PHAT HIEN DUOC tren moi frame da lay mau cua video.
-- Day la bang duy nhat sinh ra tu detection + recognition tu chay, vi Immich chi
-- detect mat cho video tren dung mot frame thumbnail.
--
-- GIU CA MAT KHONG KHOP PERSON (person_id NULL). Ban dau bang nay bo han chung
-- de khoi phinh, nhung the la tu chan duong: mot nguoi chua duoc gan ten trong
-- Immich, hoac mot nguoi hoan toan moi, se khong de lai dau vet nao. Muon phat
-- hien "day co the la mot person moi" thi phai co du lieu cua ho truoc da.
--
-- Vi vay bang nay luu du de KHONG BAO GIO phai quet lai video:
--   emb      vector ArcFace -> gom cum mat la, gan ten sau ma khong quet lai
--   lmk68    68 diem -> align duoc khi render, ngang voi anh tinh
--   track_id gom cac mat cua cung mot nguoi trong cung mot video theo IoU
CREATE TABLE IF NOT EXISTS {vface}(
  asset_id      uuid NOT NULL,
  t_ms          int  NOT NULL,        -- moc thoi gian trong video
  fidx          smallint NOT NULL,    -- thu tu mat trong frame do (theo dien tich)
  x1 real, y1 real, x2 real, y2 real, -- chuan hoa 0..1
  det           real,
  n_face        smallint,             -- tong so mat detect duoc trong frame do
  kps           bytea,                -- float32[5][2] chuan hoa 0..1
  lmk68         bytea,                -- float32[68][3] chuan hoa, NULL neu tat 1k3d68
  person_id     uuid,                 -- khop voi person cua Immich, hoac NULL
  person_name   text,
  sim           real,                 -- cosine voi vector trung tam cua person do
  sim2          real,                 -- cosine voi person xep thu hai
  -- track_id: cum theo IoU giua cac frame lien tiep, danh so trong PHAM VI MOT
  -- video. Voi mat khong khop person day la moc de gom lai va hoi "cum nay xuat
  -- hien 40 lan, co phai mot nguoi moi khong".
  track_id      smallint,
  yaw real, pitch real, roll real, frontality real,
  sharp real, bright real, symm real,
  eye_ratio     real,
  ear           real,
  age           real,
  smile         real,                 -- 0..1, chi co khi VIDEO_LMK68=1
  quality       real,                 -- cung cong thuc voi {face}.quality
  emb           bytea,                -- float32[512] da chuan hoa L2
  emb_norm      real,                 -- do dai vector truoc chuan hoa
  -- CAMERA dich chuyen bao nhieu so voi frame lay mau TRUOC DO, chuan hoa theo
  -- (w,h) cua frame. Nho no ma tach duoc "may rung" khoi "chu the dong": dich
  -- chuyen cua mat trong khung = camera + chu the. Xem idx/motion.py.
  -- NULL o frame dau tien, va khi canh qua tron de theo doi diem goc.
  cam_dx        real,
  cam_dy        real,
  PRIMARY KEY(asset_id, t_ms, fidx)
);

-- Body pose tren frame video: doi xung voi {body} cua anh tinh, them cot t_ms.
-- Chay CUNG MOT LUOT giai ma voi {vface} — giai ma video (va o che do HTTP la
-- tai ca file) dat hon nhieu so voi ban than model, nen quet hai lan la vo ly.
CREATE TABLE IF NOT EXISTS {vbody}(
  asset_id      uuid NOT NULL,
  t_ms          int  NOT NULL,
  pidx          smallint NOT NULL,
  x1 real, y1 real, x2 real, y2 real, -- chuan hoa 0..1
  det           real,
  kps           bytea NOT NULL,       -- float32[17][3] = x,y,conf chuan hoa 0..1
  n_visible     smallint,
  orientation   text,                 -- 'front' | 'back' | 'side' | 'unknown'
  posture       text,                 -- 'standing' | 'sitting' | 'lying' | 'unknown'
  torso_deg     real,
  body_front    real,
  area_ratio    real,
  face_fidx     smallint,             -- khop voi {vface}.fidx CUNG t_ms
  PRIMARY KEY(asset_id, t_ms, pidx)
);

-- Doan video da chon: mot nguoi, mot khoang thoi gian, kem duong di cua khuon
-- mat trong khoang do de buoc dung neo duoc tung frame.
CREATE TABLE IF NOT EXISTS {vclip}(
  asset_id      uuid NOT NULL,
  person_id     uuid NOT NULL,
  cidx          smallint NOT NULL,    -- 0 = doan tot nhat
  t_start_ms    int NOT NULL,
  t_end_ms      int NOT NULL,
  -- Moc KHOANH KHAC (kieu HiLight cua GoPro): dinh cua duong diem, va la thu
  -- ma doan nay duoc cat ra vi no. Nam o khoang 60% doan, khong phai giua.
  t_peak_ms     int,
  score         real,
  n_frame       int,
  sim           real,
  face_ratio    real,                 -- khoang cach hai mat / canh dai, trung binh
  sharp real, bright real, frontality real,
  smile         real,                 -- trung binh diem nu cuoi trong doan
  motion        real,                 -- do rung TONG (may + chu the), giu nghia cu
  -- Hai cot tach ra tu motion. NULL voi cac doan quet bang ban cu.
  shake         real,                 -- rieng do lac cua MAY: cang thap cang on
  action        real,                 -- rieng chuyen dong CHU THE: cang cao cang tot
  -- track: float32[n][11] = t_giay, roi 5 cap (x,y) chuan hoa. Nho vay buoc dung
  -- noi suy duoc diem neo o bat ky thoi diem nao ma khong phai join lai vface.
  track         bytea,
  updated_at    timestamptz DEFAULT now(),
  PRIMARY KEY(asset_id, person_id, cidx)
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
  -- 0..1 "dang cuoi", suy tu lmk68 (khoe mieng + be rong + do ho). Khong phai
  -- model moi: 68 diem da co san. Xem metrics.smile_from_68.
  smile         real,
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
"""

# Index tach RIENG khoi DDL va chay SAU MIGRATIONS.
#
# Ly do: voi db da co tu ban truoc, 'CREATE TABLE IF NOT EXISTS {asset}' la
# no-op nen cac cot moi (kind, clip_state, ...) chua ton tai. Neu de lenh
# CREATE INDEX ... (clip_state) chung block voi DDL thi no chay truoc
# MIGRATIONS va bao UndefinedColumn, ca transaction rollback -> MIGRATIONS
# khong bao gio duoc chay, va lan sau van loi y nguyen.
INDEXES = """
CREATE INDEX IF NOT EXISTS {p}asset_face_state ON {asset}(face_state);
CREATE INDEX IF NOT EXISTS {p}asset_body_state ON {asset}(body_state);
CREATE INDEX IF NOT EXISTS {p}asset_clip_state ON {asset}(clip_state);
CREATE INDEX IF NOT EXISTS {p}asset_taken      ON {asset}(taken_at);
CREATE INDEX IF NOT EXISTS {p}asset_kind       ON {asset}(kind);
CREATE INDEX IF NOT EXISTS {p}face_state       ON {face}(state);
CREATE INDEX IF NOT EXISTS {p}face_person      ON {face}(person_id);
-- Buoc chon anh xep hang theo person + do hap dan, nen index ghep.
CREATE INDEX IF NOT EXISTS {p}face_smile       ON {face}(person_id, smile DESC);
CREATE INDEX IF NOT EXISTS {p}body_orient      ON {body}(orientation);
CREATE INDEX IF NOT EXISTS {p}body_posture     ON {body}(posture);
CREATE INDEX IF NOT EXISTS {p}vface_person     ON {vface}(person_id);
CREATE INDEX IF NOT EXISTS {p}vface_track      ON {vface}(asset_id, track_id);
-- Index rieng cho cau hoi "nhung mat chua biet la ai": partial index nen no chi
-- to bang so dong that su chua khop, khong phai bang ca bang.
CREATE INDEX IF NOT EXISTS {p}vface_unknown    ON {vface}(asset_id, track_id)
  WHERE person_id IS NULL;
CREATE INDEX IF NOT EXISTS {p}vclip_person     ON {vclip}(person_id, score DESC);
CREATE INDEX IF NOT EXISTS {p}vbody_asset      ON {vbody}(asset_id, t_ms);
CREATE INDEX IF NOT EXISTS {p}vbody_orient     ON {vbody}(orientation);
CREATE INDEX IF NOT EXISTS {p}vbody_posture    ON {vbody}(posture);
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
    # Nang cap sang ban co video. Anh da index truoc day khong phai lam lai gi:
    # kind mac dinh 'image', va clip_state=2 nghia la 'khong phai video, bo qua'.
    "ALTER TABLE {asset} ADD COLUMN IF NOT EXISTS kind text DEFAULT 'image'",
    "ALTER TABLE {asset} ADD COLUMN IF NOT EXISTS video_path text",
    "ALTER TABLE {asset} ADD COLUMN IF NOT EXISTS dur_ms int",
    "ALTER TABLE {asset} ADD COLUMN IF NOT EXISTS n_clip int DEFAULT 0",
    "ALTER TABLE {asset} ADD COLUMN IF NOT EXISTS clip_state smallint DEFAULT 0",
    "UPDATE {asset} SET clip_state = 2 WHERE clip_state = 0 AND kind <> 'video'",
    # Nang cap sang ban cat doan theo khoanh khac. Doan cu khong co t_peak_ms;
    # chung van dung duoc, chi thieu moc de hien tren UI.
    "ALTER TABLE {vclip} ADD COLUMN IF NOT EXISTS t_peak_ms int",
    # Nang cap sang ban quet video day du. Du lieu vface cu VAN DUNG DUOC, chi
    # thieu cac cot moi (NULL). Muon co day du thi 'job.py --reset clips'.
    "ALTER TABLE {vface} ADD COLUMN IF NOT EXISTS pitch real",
    "ALTER TABLE {vface} ADD COLUMN IF NOT EXISTS ear real",
    "ALTER TABLE {vface} ADD COLUMN IF NOT EXISTS age real",
    "ALTER TABLE {vface} ADD COLUMN IF NOT EXISTS quality real",
    "ALTER TABLE {vface} ADD COLUMN IF NOT EXISTS emb bytea",
    "ALTER TABLE {vface} ADD COLUMN IF NOT EXISTS emb_norm real",
    "ALTER TABLE {vface} ADD COLUMN IF NOT EXISTS lmk68 bytea",
    "ALTER TABLE {vface} ADD COLUMN IF NOT EXISTS person_name text",
    "ALTER TABLE {vface} ADD COLUMN IF NOT EXISTS track_id smallint",
    "ALTER TABLE {asset} ADD COLUMN IF NOT EXISTS n_vface int DEFAULT 0",
    "ALTER TABLE {asset} ADD COLUMN IF NOT EXISTS n_vbody int DEFAULT 0",
    "ALTER TABLE {asset} ADD COLUMN IF NOT EXISTS n_vframe int DEFAULT 0",
    # Diem nu cuoi. Suy tu lmk68 DA LUU nen db cu khong phai quet lai anh:
    # 'job.py --stage smiles' doc lai blob va dien cot nay.
    "ALTER TABLE {face} ADD COLUMN IF NOT EXISTS smile real",
    "ALTER TABLE {vface} ADD COLUMN IF NOT EXISTS smile real",
    "ALTER TABLE {vclip} ADD COLUMN IF NOT EXISTS smile real",
    # Tach chuyen dong may / chu the. Can quet lai video de co (--reset clips):
    # cam_dx/cam_dy phai do giua hai frame lien tiep, khong suy lai tu db duoc.
    "ALTER TABLE {vface} ADD COLUMN IF NOT EXISTS cam_dx real",
    "ALTER TABLE {vface} ADD COLUMN IF NOT EXISTS cam_dy real",
    "ALTER TABLE {vclip} ADD COLUMN IF NOT EXISTS shake real",
    "ALTER TABLE {vclip} ADD COLUMN IF NOT EXISTS action real",
)


def ensure_schema(conn, s):
    names = dict(
        p=s.prefix, asset=s.table("asset"), face=s.table("face"),
        body=s.table("body"), run=s.table("run"), state=s.table("state"),
        vface=s.table("vface"), vclip=s.table("vclip"),
        vbody=s.table("vbody"))
    with conn.cursor() as cur:
        # Thu tu bat buoc: tao bang -> them cot thieu -> tao index.
        # Index co the tham chieu cot chi xuat hien o MIGRATIONS.
        cur.execute(DDL.format(**names))
        for sql in MIGRATIONS:
            cur.execute(sql.format(**names))
        cur.execute(INDEXES.format(**names))
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
