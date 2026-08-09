"""Bon stage tuan tu cua job. Moi stage resumable qua cot state trong db.

  1 assets     doc danh sach anh tu Immich  -> fp_asset
  2 faces      copy bbox + embedding + person -> fp_face   (face_state 0 -> 2)
  3 landmarks  1k3d68 + genderage + chi so   -> fp_face    (face_state 2 -> 1)
  4 bodies     yolov8n-pose 17 keypoint      -> fp_body    (body_state 0 -> 1)

Thiet ke de khong lam sap server:
  - tuan tu hoan toan, khong thread, khong process pool
  - chi mot model trong RAM tai mot thoi diem (stage 3 xong moi load stage 4)
  - commit theo lo BATCH_COMMIT, sap giua duong thi chay lai tiep tuc
  - SLEEP_MS de nhuong CPU cho Immich neu can
"""
import gc
import json
import time

import numpy as np

from . import control
from . import immich_src as src
from .metrics import face_metrics, frontality, quality
from .pgdb import get_state, run_log, set_state

PAGE = 1000


# --------------------------------------------------------------------- utils
def _throttle(s):
    if s.sleep_ms:
        time.sleep(s.sleep_ms / 1000.0)


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _vec(v):
    """Embedding cua Immich: pgvector -> str '[...]', hoac list neu co adapter."""
    if v is None:
        return None
    if isinstance(v, (list, tuple, np.ndarray)):
        return np.asarray(v, np.float32)
    txt = str(v).strip()
    if not txt:
        return None
    try:
        return np.asarray(json.loads(txt), np.float32)
    except (ValueError, TypeError):
        return None


def _progress(label, n, total, t0, every=200):
    if n % every:
        return
    el = time.time() - t0
    rate = n / el if el > 0 else 0
    eta = (total - n) / rate if rate > 0 and total else 0
    print(f"  {label} {n}/{total or '?'}  {rate:.1f}/s"
          + (f"  con ~{eta / 60:.0f} phut" if eta else ""))


# ------------------------------------------------------------------ stage 1
def sync_assets(conn, s, t):
    """Dong bo danh sach anh. Anh moi -> state 0, anh cu giu nguyen state."""
    tbl = s.table("asset")
    total = src.count_assets(conn, t, s.taken_after, s.taken_before)
    print(f"  Immich co {total} anh trong pham vi scan")

    ins = (f"INSERT INTO {tbl}"
           f"(id,filename,taken_at,date_src,preview_path,img_w,img_h,seen_at)"
           f" VALUES(%s,%s,%s,%s,%s,%s,%s,now())"
           f" ON CONFLICT(id) DO UPDATE SET"
           f"   filename=EXCLUDED.filename,"
           f"   taken_at=EXCLUDED.taken_at,"
           f"   date_src=EXCLUDED.date_src,"
           f"   preview_path=EXCLUDED.preview_path,"
           f"   img_w=COALESCE(EXCLUDED.img_w, {tbl}.img_w),"
           f"   img_h=COALESCE(EXCLUDED.img_h, {tbl}.img_h),"
           f"   seen_at=now()")

    with run_log(conn, s, "assets") as box:
        after, n, n_exif, no_prev = None, 0, 0, 0
        t0 = time.time()
        while True:
            sql, params = src.asset_page(t, after, s.taken_after,
                                         s.taken_before, PAGE)
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            conn.rollback()
            if not rows:
                break
            batch = []
            for (aid, fname, exif_dt, local_dt, created, w, h, prev) in rows:
                date = exif_dt or local_dt or created
                dsrc = "exif" if exif_dt else ("local" if local_dt else "file")
                n_exif += bool(exif_dt)
                no_prev += not prev
                batch.append((str(aid), fname, _iso(date), dsrc, prev,
                              int(w) if w else None, int(h) if h else None))
                after = str(aid)
            with conn.cursor() as cur:
                cur.executemany(ins, batch)
            conn.commit()
            n += len(rows)
            _progress("assets", n, total, t0, PAGE)
            if s.limit and n >= s.limit:
                break
            if control.should_stop():
                print("  nhan tin hieu dung, thoat sau khi da commit")
                break
        box["done"] = n
        box["note"] = f"exif={n_exif} no_preview={no_prev}"
        print(f"  {n} anh dong bo, {n_exif} co EXIF DateTimeOriginal, "
              f"{n - n_exif} phai dung ngay file")
        if no_prev:
            print(f"  canh bao: {no_prev} anh chua co ban preview trong Immich")
        set_state(conn, s, "assets_synced_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        conn.commit()
        _audit_dates(conn, s)
    return n


def _audit_dates(conn, s):
    """Canh bao truong hop ngay = ngay scan chu khong phai ngay chup."""
    tbl = s.table("asset")
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT taken_at::date d, COUNT(*) n FROM {tbl} "
            f"WHERE date_src <> 'exif' AND taken_at IS NOT NULL "
            f"GROUP BY d HAVING COUNT(*) > 30 ORDER BY n DESC LIMIT 8")
        rows = cur.fetchall()
    conn.rollback()
    if rows:
        print("  " + "!" * 58)
        print("  Ngay nay co ve la ngay SCAN chu khong phai ngay chup:")
        for d, n in rows:
            print(f"     {d}  {n} anh")
        print("  Sua EXIF trong Immich roi chay lai stage assets.")
        print("  " + "!" * 58)


# ------------------------------------------------------------------ stage 2
def requeue_missing_faces(conn, s, t):
    """Xet lai nhung anh da danh dau xong voi 0 khuon mat.

    Bay: neu job chay TRUOC khi Immich lam xong Facial Recognition cho anh moi
    thi asset_face con trong -> stage faces dat face_state=2, stage landmarks
    thay 0 face nen cho luon face_state=1. Anh do xong voi n_face=0 va KHONG
    BAO GIO duoc xet lai, vi stage faces chi tim face_state=0.

    Cach chua cu la --reset faces: xoa sach fp_face va dat lai toan bo ve 0,
    tuc chay lai landmarks cho ca 86k anh. Qua dat.

    O day chi dat lai dung nhung anh gio da co face ben Immich. Chay bang
    index, khong doc anh nao.
    """
    a_tbl = s.table("asset")
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {a_tbl} SET face_state = 0, updated_at = now() "
            f"WHERE face_state = 1 AND n_face = 0 "
            f"  AND EXISTS (SELECT 1 FROM {t.q(t.face)} af "
            f"              WHERE af.\"assetId\" = {a_tbl}.id "
            f"                AND af.\"deletedAt\" IS NULL)")
        n = cur.rowcount
    conn.commit()
    if n:
        print(f"  {n} anh truoc day 0 mat, gio Immich da nhan dien -> xet lai")
    return n


def sync_faces(conn, s, t):
    """Copy bbox + embedding + person tu Immich. Khong chay model nao."""
    a_tbl, f_tbl = s.table("asset"), s.table("face")
    requeue_missing_faces(conn, s, t)
    if not t.search and s.copy_embedding:
        print("  instance khong co bang face_search -> bo qua embedding")

    fsql = src.faces_for(t, s.copy_embedding)
    ins = (f"INSERT INTO {f_tbl}"
           f"(asset_id,fidx,immich_face,person_id,person_name,"
           f" x1,y1,x2,y2,det,emb,emb_norm,kps_src,state)"
           f" VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,1.0,%s,%s,'pg',0)"
           f" ON CONFLICT(asset_id,fidx) DO UPDATE SET"
           f"   immich_face=EXCLUDED.immich_face,"
           f"   person_id=EXCLUDED.person_id,"
           f"   person_name=EXCLUDED.person_name,"
           f"   x1=EXCLUDED.x1, y1=EXCLUDED.y1,"
           f"   x2=EXCLUDED.x2, y2=EXCLUDED.y2,"
           f"   emb=COALESCE(EXCLUDED.emb, {f_tbl}.emb),"
           f"   emb_norm=COALESCE(EXCLUDED.emb_norm, {f_tbl}.emb_norm),"
           f"   updated_at=now()")

    with run_log(conn, s, "faces") as box:
        n_face = n_asset = n_named = n_noemb = 0
        t0 = time.time()
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id FROM {a_tbl} WHERE face_state = 0 "
                    f"ORDER BY id LIMIT %s", (PAGE,))
                ids = [str(r[0]) for r in cur.fetchall()]
            conn.rollback()
            if not ids:
                break

            with conn.cursor() as cur:
                cur.execute(fsql, (ids,))
                rows = cur.fetchall()
            conn.rollback()

            per_asset = {}
            for r in rows:
                per_asset.setdefault(str(r[0]), []).append(r)

            batch = []
            for aid in ids:
                items = per_asset.get(aid, [])
                items.sort(key=lambda r: str(r[1]))   # thu tu on dinh theo face id
                for i, r in enumerate(items):
                    iw = max(int(r[2] or 0), 1)
                    ih = max(int(r[3] or 0), 1)
                    x1, y1, x2, y2 = (float(r[4]), float(r[5]),
                                      float(r[6]), float(r[7]))
                    face_id, pid, pname = r[1], r[8], r[9]
                    e = _vec(r[10])
                    if e is None or e.size == 0:
                        emb, norm = None, None
                        n_noemb += 1
                    else:
                        norm = float(np.linalg.norm(e))
                        emb = (e / max(norm, 1e-6)).astype(np.float32).tobytes()
                    n_named += bool(pname)
                    batch.append((
                        aid, i, str(face_id), str(pid) if pid else None, pname,
                        x1 / iw, y1 / ih, x2 / iw, y2 / ih,
                        emb, norm))
                    n_face += 1
                n_asset += 1

            with conn.cursor() as cur:
                if batch:
                    cur.executemany(ins, batch)
                # anh khong co face nao van chuyen state, khong quet lai
                cur.execute(
                    f"UPDATE {a_tbl} SET face_state=2, "
                    f"n_face=(SELECT COUNT(*) FROM {f_tbl} f "
                    f"        WHERE f.asset_id = {a_tbl}.id), "
                    f"updated_at=now() "
                    f"WHERE id = ANY(%s::uuid[])", (ids,))
            conn.commit()
            _progress("faces", n_asset, None, t0, PAGE)
            if control.should_stop():
                print("  nhan tin hieu dung, thoat sau khi da commit")
                break

        box["done"] = n_face
        box["note"] = f"assets={n_asset} named={n_named} no_emb={n_noemb}"
        print(f"  {n_face} face tu {n_asset} anh, {n_named} face da co ten"
              + (f", {n_noemb} face thieu embedding" if n_noemb else ""))
    return n_face


# ------------------------------------------------------------------ stage 3
def landmarks(conn, s, t, media):
    """Chay 1k3d68 + genderage cho face chua co landmark.

    Day la stage duy nhat can doc pixel cua khuon mat. Bo qua detection va
    recognition nen nhanh hon full pipeline nhieu lan.
    """
    from .facemodel import FaceLandmarker

    a_tbl, f_tbl = s.table("asset"), s.table("face")
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {a_tbl} WHERE face_state = 2")
        total = cur.fetchone()[0]
    conn.rollback()
    if not total:
        print("  khong co anh nao cho landmark")
        return 0

    print(f"  {total} anh cho landmark")
    model = FaceLandmarker(s)
    upd = (f"UPDATE {f_tbl} SET yaw=%s,pitch=%s,roll=%s,frontality=%s,"
           f"eye_px=%s,eye_ratio=%s,sharp=%s,bright=%s,symm=%s,ear=%s,age=%s,"
           f"quality=%s,kps=%s,lmk68=%s,kps_src='lmk68',state=1,updated_at=now() "
           f"WHERE asset_id=%s AND fidx=%s")

    with run_log(conn, s, "landmarks") as box:
        n_img = n_face = n_err = 0
        t0 = time.time()
        try:
            while True:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT a.id, a.preview_path FROM {a_tbl} a "
                        f"WHERE a.face_state = 2 ORDER BY a.id LIMIT %s",
                        (s.batch,))
                    todo = cur.fetchall()
                conn.rollback()
                if not todo:
                    break

                done_ids, err_ids, batch = [], [], []
                for aid, prev in todo:
                    aid = str(aid)
                    with conn.cursor() as cur:
                        cur.execute(
                            f"SELECT fidx,x1,y1,x2,y2,emb_norm FROM {f_tbl} "
                            f"WHERE asset_id=%s AND state=0 ORDER BY fidx", (aid,))
                        faces = cur.fetchall()
                    conn.rollback()
                    if not faces:
                        done_ids.append(aid)
                        continue

                    img, tmp = media.read(aid, prev, s.max_side)
                    if img is None:
                        err_ids.append(aid)
                        n_err += 1
                        continue
                    h, w = img.shape[:2]
                    for fidx, x1, y1, x2, y2, enorm in faces:
                        bbox = (x1 * w, y1 * h, x2 * w, y2 * h)
                        r = model(img, bbox)
                        if r is None:
                            continue
                        met = face_metrics(img, bbox, r["kps"])
                        if met is None:
                            continue
                        fr = frontality(r["yaw"], r["pitch"], r["roll"],
                                        met["symm"])
                        qs = quality(met["eye_px"], met["sharp"], met["bright"],
                                     r["yaw"], r["pitch"], r["roll"], 1.0,
                                     met["symm"], enorm)
                        # Chuan hoa toa do truoc khi luu: MAX_SIDE co the doi,
                        # va buoc render video doc anh o kich thuoc khac.
                        kn = r["kps"].copy()
                        kn[:, 0] /= float(w)
                        kn[:, 1] /= float(h)
                        ln = r["lmk68"].copy()
                        ln[:, 0] /= float(w)
                        ln[:, 1] /= float(h)
                        if ln.shape[1] > 2:
                            ln[:, 2] /= float(w)
                        batch.append((
                            r["yaw"], r["pitch"], r["roll"], fr,
                            met["eye_px"], met["eye_px"] / float(max(w, h)),
                            met["sharp"], met["bright"],
                            met["symm"], r["ear"], r["age"], qs,
                            kn.astype(np.float32).tobytes(),
                            ln.astype(np.float32).tobytes(),
                            aid, fidx))
                        n_face += 1
                    media.release(tmp)
                    del img
                    done_ids.append(aid)
                    n_img += 1
                    _throttle(s)
                    _progress("landmark", n_img, total, t0, 100)

                with conn.cursor() as cur:
                    if batch:
                        cur.executemany(upd, batch)
                    if done_ids:
                        cur.execute(
                            f"UPDATE {a_tbl} SET face_state=1, err=NULL, "
                            f"updated_at=now() WHERE id = ANY(%s::uuid[])",
                            (done_ids,))
                    if err_ids:
                        cur.execute(
                            f"UPDATE {a_tbl} SET face_state=-1, "
                            f"err='khong doc duoc preview', updated_at=now() "
                            f"WHERE id = ANY(%s::uuid[])", (err_ids,))
                conn.commit()
                gc.collect()
                if control.should_stop():
                    print("  nhan tin hieu dung, thoat sau khi da commit")
                    break
        finally:
            model.close()
            del model
            gc.collect()

        box["done"] = n_face
        box["err"] = n_err
        box["note"] = f"images={n_img}"
        print(f"  {n_face} face co landmark tu {n_img} anh"
              + (f", {n_err} anh loi doc" if n_err else ""))
    return n_face


# ------------------------------------------------------------------ stage 4
def bodies(conn, s, t, media):
    """Chay yolov8n-pose: detect nguoi + 17 keypoint COCO cho moi anh."""
    from . import bodyfeat as BF
    from .bodymodel import BodyPose

    a_tbl, f_tbl, b_tbl = s.table("asset"), s.table("face"), s.table("body")
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {a_tbl} WHERE body_state = 0")
        total = cur.fetchone()[0]
    conn.rollback()
    if not total:
        print("  khong co anh nao cho body pose")
        return 0

    print(f"  {total} anh cho body pose")
    model = BodyPose(s)
    ins = (f"INSERT INTO {b_tbl}"
           f"(asset_id,pidx,x1,y1,x2,y2,det,kps,n_visible,orientation,"
           f" posture,torso_deg,body_front,area_ratio,face_fidx)"
           f" VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
           f" ON CONFLICT(asset_id,pidx) DO UPDATE SET"
           f"   x1=EXCLUDED.x1,y1=EXCLUDED.y1,x2=EXCLUDED.x2,y2=EXCLUDED.y2,"
           f"   det=EXCLUDED.det,kps=EXCLUDED.kps,"
           f"   n_visible=EXCLUDED.n_visible,"
           f"   orientation=EXCLUDED.orientation,posture=EXCLUDED.posture,"
           f"   torso_deg=EXCLUDED.torso_deg,body_front=EXCLUDED.body_front,"
           f"   area_ratio=EXCLUDED.area_ratio,face_fidx=EXCLUDED.face_fidx,"
           f"   updated_at=now()")

    with run_log(conn, s, "bodies") as box:
        n_img = n_body = n_err = 0
        t0 = time.time()
        try:
            while True:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT id, preview_path FROM {a_tbl} "
                        f"WHERE body_state = 0 ORDER BY id LIMIT %s", (s.batch,))
                    todo = cur.fetchall()
                conn.rollback()
                if not todo:
                    break

                done, errs, batch, counts = [], [], [], []
                for aid, prev in todo:
                    aid = str(aid)
                    img, tmp = media.read(aid, prev, s.max_side)
                    if img is None:
                        errs.append(aid)
                        n_err += 1
                        continue
                    h, w = img.shape[:2]

                    with conn.cursor() as cur:
                        cur.execute(
                            f"SELECT fidx,x1,y1,x2,y2 FROM {f_tbl} "
                            f"WHERE asset_id=%s ORDER BY fidx", (aid,))
                        faces_px = [(r[0], r[1] * w, r[2] * h, r[3] * w, r[4] * h)
                                    for r in cur.fetchall()]
                    conn.rollback()

                    people = model(img)
                    for pidx, p in enumerate(people):
                        k = p["kps"]
                        feat = BF.describe(k, w, h, p["bbox"])
                        fidx = BF.match_face(k, faces_px)
                        norm = k.copy()
                        norm[:, 0] /= float(w)
                        norm[:, 1] /= float(h)
                        bb = p["bbox"]
                        batch.append((
                            aid, pidx,
                            bb[0] / w, bb[1] / h, bb[2] / w, bb[3] / h,
                            p["det"], norm.astype(np.float32).tobytes(),
                            feat["n_visible"], feat["orientation"],
                            feat["posture"], feat["torso_deg"],
                            feat["body_front"], feat["area_ratio"], fidx))
                        n_body += 1
                    counts.append((len(people), aid))
                    media.release(tmp)
                    del img
                    done.append(aid)
                    n_img += 1
                    _throttle(s)
                    _progress("body", n_img, total, t0, 100)

                with conn.cursor() as cur:
                    if batch:
                        cur.executemany(ins, batch)
                    if counts:
                        cur.executemany(
                            f"UPDATE {a_tbl} SET n_body=%s, body_state=1, "
                            f"updated_at=now() WHERE id=%s::uuid", counts)
                    if errs:
                        cur.execute(
                            f"UPDATE {a_tbl} SET body_state=-1, "
                            f"err=COALESCE(err,'') || ' body:no-preview', "
                            f"updated_at=now() WHERE id = ANY(%s::uuid[])",
                            (errs,))
                conn.commit()
                gc.collect()
                if control.should_stop():
                    print("  nhan tin hieu dung, thoat sau khi da commit")
                    break
        finally:
            model.close()
            del model
            gc.collect()

        box["done"] = n_body
        box["err"] = n_err
        box["note"] = f"images={n_img}"
        print(f"  {n_body} nguoi tu {n_img} anh"
              + (f", {n_err} anh loi doc" if n_err else ""))
    return n_body


# ---------------------------------------------------------------- reset / stat
def reset(conn, s, what):
    """Danh dau lai de chay lai mot stage. Khong xoa du lieu Immich."""
    a, f, b = s.table("asset"), s.table("face"), s.table("body")
    with conn.cursor() as cur:
        if what in ("faces", "all"):
            cur.execute(f"DELETE FROM {f}")
            cur.execute(f"UPDATE {a} SET face_state=0, n_face=0")
        elif what == "landmarks":
            cur.execute(f"UPDATE {f} SET state=0, kps=NULL, lmk68=NULL")
            cur.execute(f"UPDATE {a} SET face_state=2 WHERE face_state IN (1,-1)")
        if what in ("bodies", "all"):
            cur.execute(f"DELETE FROM {b}")
            cur.execute(f"UPDATE {a} SET body_state=0, n_body=0")
        if what == "errors":
            cur.execute(f"UPDATE {a} SET face_state=2 WHERE face_state=-1")
            cur.execute(f"UPDATE {a} SET body_state=0 WHERE body_state=-1")
            cur.execute(f"UPDATE {a} SET err=NULL")
    conn.commit()
    print(f"reset '{what}' xong")


def stats(conn, s):
    a, f, b, r = (s.table("asset"), s.table("face"),
                  s.table("body"), s.table("run"))
    rows = [
        ("assets", f"SELECT COUNT(*) FROM {a}"),
        ("  cho faces", f"SELECT COUNT(*) FROM {a} WHERE face_state=0"),
        ("  cho landmark", f"SELECT COUNT(*) FROM {a} WHERE face_state=2"),
        ("  face xong", f"SELECT COUNT(*) FROM {a} WHERE face_state=1"),
        ("  face loi", f"SELECT COUNT(*) FROM {a} WHERE face_state=-1"),
        ("  cho body", f"SELECT COUNT(*) FROM {a} WHERE body_state=0"),
        ("  body xong", f"SELECT COUNT(*) FROM {a} WHERE body_state=1"),
        ("  body loi", f"SELECT COUNT(*) FROM {a} WHERE body_state=-1"),
        ("  thieu preview", f"SELECT COUNT(*) FROM {a} WHERE preview_path IS NULL"),
        ("faces", f"SELECT COUNT(*) FROM {f}"),
        ("  co embedding", f"SELECT COUNT(*) FROM {f} WHERE emb IS NOT NULL"),
        ("  co person_id", f"SELECT COUNT(DISTINCT person_id) FROM {f} "
                           f"WHERE person_id IS NOT NULL"),
        ("  co lmk68", f"SELECT COUNT(*) FROM {f} WHERE lmk68 IS NOT NULL"),
        ("  co ten", f"SELECT COUNT(*) FROM {f} WHERE person_name IS NOT NULL"),
        ("bodies", f"SELECT COUNT(*) FROM {b}"),
        ("  khop khuon mat", f"SELECT COUNT(*) FROM {b} WHERE face_fidx IS NOT NULL"),
    ]
    with conn.cursor() as cur:
        for label, sql in rows:
            cur.execute(sql)
            print(f"{label:<20}{cur.fetchone()[0]}")
        for col in ("orientation", "posture"):
            cur.execute(f"SELECT {col}, COUNT(*) FROM {b} GROUP BY {col} "
                        f"ORDER BY 2 DESC")
            got = cur.fetchall()
            if got:
                print(f"{col:<20}" + "  ".join(f"{k}={v}" for k, v in got))
        cur.execute(f"SELECT stage,started_at,finished_at,n_done,n_err,note "
                    f"FROM {r} ORDER BY id DESC LIMIT 6")
        runs = cur.fetchall()
    conn.rollback()
    if runs:
        print("\nlan chay gan nhat:")
        for st, s0, s1, nd, ne, note in runs:
            state = "dang chay" if s1 is None else "xong"
            print(f"  {st:<10}{str(s0)[:19]}  {state:<10}"
                  f"done={nd} err={ne} {note or ''}")
