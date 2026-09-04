"""Nam stage tuan tu cua job. Moi stage resumable qua cot state trong db.

  1 assets     doc danh sach anh + video tu Immich -> fp_asset
  2 faces      copy bbox + embedding + person -> fp_face   (face_state 0 -> 2)
  3 landmarks  1k3d68 + genderage + chi so   -> fp_face    (face_state 2 -> 1)
  4 bodies     yolov8n-pose 17 keypoint      -> fp_body    (body_state 0 -> 1)
  5 clips      quet frame video: SCRFD + ArcFace (+1k3d68 +yolo-pose)
               -> fp_vface + fp_vbody + fp_vclip
               (clip_state 0 -> 1). Chi chay tren kind='video'.

Ngoai ra co mot stage goi ten ro rang moi chay, khong nam trong 'all':

  - rematch    gan lai person tu emb DA LUU trong fp_vface roi cat lai doan.
               Khong giai ma video, khong load model. Chay sau khi ten nguoi
               trong Immich thay doi.

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


# fp_asset.dur_ms la 'int' cua Postgres. Gia tri rac trong Immich (hoac doan sai
# don vi) tung lam ca stage assets chet giua duong voi NumericValueOutOfRange,
# keo theo rollback ca lo 1000 dong. Chan tai day thay vi de Postgres chan.
_INT32_MAX = 2 ** 31 - 1


def _dur_ms(v):
    """Do dai video -> milliseconds. None neu khong doc duoc.

    Ba dang duration da gap tuy phien ban Immich:
      - interval           -> co total_seconds()
      - text '0:00:12.345' -> gio:phut:giay, co dau ':'
      - integer            -> DA la milliseconds san (bang 'asset' cot integer)

    Dung nham dang thu ba thanh giay la sai 1000 lan, va voi video dai thi vuot
    luon tran int32.
    """
    if v is None:
        return None
    if hasattr(v, "total_seconds"):
        ms = v.total_seconds() * 1000.0
    elif isinstance(v, bool):
        return None
    elif isinstance(v, (int, float)):
        ms = float(v)                     # cot integer: da la ms
    else:
        txt = str(v).strip()
        if not txt:
            return None
        try:
            parts = [float(x) for x in txt.split(":")]
        except ValueError:
            return None
        if len(parts) == 1:
            # Khong co dau ':' -> khong phai dang gio:phut:giay, hieu la ms.
            ms = parts[0]
        else:
            sec = 0.0
            for p in parts:
                sec = sec * 60.0 + p
            ms = sec * 1000.0
    try:
        ms = int(round(ms))
    except (ValueError, OverflowError):
        return None
    if ms <= 0:
        return None
    # Kep vao tran int32: thua 24 ngay video thi con so khong con y nghia nua,
    # nhung KHONG duoc phep lam sap ca stage.
    return min(ms, _INT32_MAX)


# ------------------------------------------------------------------ stage 1
def sync_assets(conn, s, t):
    """Dong bo danh sach anh VA video. Moi -> state 0, cu giu nguyen state."""
    tbl = s.table("asset")
    kinds = ("IMAGE", "VIDEO") if (s.do_video and t.can_video()) else ("IMAGE",)
    total = src.count_assets(conn, t, s.taken_after, s.taken_before, kinds)
    print(f"  Immich co {total} asset trong pham vi scan ({'+'.join(kinds)})")

    ins = (f"INSERT INTO {tbl}"
           f"(id,filename,kind,taken_at,date_src,preview_path,video_path,dur_ms,"
           f" img_w,img_h,clip_state,seen_at)"
           f" VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())"
           f" ON CONFLICT(id) DO UPDATE SET"
           f"   filename=EXCLUDED.filename,"
           f"   kind=EXCLUDED.kind,"
           f"   taken_at=EXCLUDED.taken_at,"
           f"   date_src=EXCLUDED.date_src,"
           f"   preview_path=EXCLUDED.preview_path,"
           f"   video_path=COALESCE(EXCLUDED.video_path, {tbl}.video_path),"
           f"   dur_ms=COALESCE(EXCLUDED.dur_ms, {tbl}.dur_ms),"
           f"   img_w=COALESCE(EXCLUDED.img_w, {tbl}.img_w),"
           f"   img_h=COALESCE(EXCLUDED.img_h, {tbl}.img_h),"
           f"   seen_at=now()")

    with run_log(conn, s, "assets") as box:
        after, n, n_exif, no_prev, n_vid = None, 0, 0, 0, 0
        t0 = time.time()
        while True:
            sql, params = src.asset_page(t, after, s.taken_after,
                                         s.taken_before, PAGE, kinds)
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            conn.rollback()
            if not rows:
                break
            batch = []
            for (aid, fname, exif_dt, local_dt, created, w, h, prev,
                 atype, vpath, dur) in rows:
                date = exif_dt or local_dt or created
                dsrc = "exif" if exif_dt else ("local" if local_dt else "file")
                n_exif += bool(exif_dt)
                no_prev += not prev
                is_vid = str(atype).upper() == "VIDEO"
                n_vid += is_vid
                batch.append((
                    str(aid), fname, "video" if is_vid else "image",
                    _iso(date), dsrc, prev,
                    vpath if is_vid else None,
                    _dur_ms(dur) if is_vid else None,
                    int(w) if w else None, int(h) if h else None,
                    # 2 = "khong phai video, bo qua" -> stage clips khong nhin
                    0 if is_vid else 2))
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
        box["note"] = f"exif={n_exif} video={n_vid} no_preview={no_prev}"
        print(f"  {n} asset dong bo ({n_vid} video), {n_exif} co EXIF "
              f"DateTimeOriginal, {n - n_exif} phai dung ngay file")
        if no_prev:
            print(f"  canh bao: {no_prev} asset chua co ban preview trong Immich")
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
    from .metrics import smile_from_68

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
           f"smile=%s,"
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
                        # Tinh smile trên toa do PIXEL (aspect=1.0): chinh xac.
                        # Doc lai tu blob da chuan hoa thi phai bu aspect.
                        sm = smile_from_68(r["lmk68"], met["eye_px"])
                        batch.append((
                            r["yaw"], r["pitch"], r["roll"], fr,
                            met["eye_px"], met["eye_px"] / float(max(w, h)),
                            met["sharp"], met["bright"],
                            met["symm"], r["ear"], r["age"], sm, qs,
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


# ------------------------------------------------------------------ stage 5
def clips(conn, s, t, media):
    """Quet frame video, gan mat cho person, roi cat ra cac doan dep nhat.

    Stage dat nhat cua ca job, va la stage duy nhat chay detection + recognition.
    Ly do: Immich chi detect mat cho video tren DUNG MOT frame thumbnail, nen
    biet clip nao co ong A la du de liet ke nhung khong du de cat "doan dep nhat
    co ong A".

    Hai model can dung (det_10g + w600k_r50) DA nam san trong bo buffalo_l ma
    fetch_models.py tai ve — tu truoc gio chi khong dung den.

    Quet MOT luot, ghi ba bang:
      fp_vface  moi mat tren moi frame da lay mau, KE CA mat chua biet la ai
      fp_vbody  body pose tren cung frame do (DO_VBODY)
      fp_vclip  cac doan da chon ra, theo tung person

    Uu tien MEDIA_ROOT. Khong co thi tai qua HTTP, nhung phai tai HET file moi
    quet duoc nen co tran VIDEO_MAX_MB.
    """
    from .facedetect import FaceDetector, PersonIndex
    from .media import video_frames, video_info

    a_tbl = s.table("asset")
    vf_tbl, vc_tbl, vb_tbl = (s.table("vface"), s.table("vclip"),
                              s.table("vbody"))

    if not s.do_video:
        print("  DO_VIDEO=0 -> bo qua")
        return 0
    if not t.can_video():
        print("  Immich khong co cot duong dan video -> bo qua")
        return 0
    if not (media.root or (s.immich_url and s.immich_api_key)):
        print("  stage clips can MEDIA_ROOT hoac IMMICH_URL + IMMICH_API_KEY.")
        return 0
    if not media.root:
        # Che do HTTP: giong anh, nhung dat hon nhieu vi phai tai HET file moi
        # quet duoc (cv2.VideoCapture can file seek duoc, khong stream duoc).
        print(f"  che do HTTP: tai video qua {s.immich_url}, "
              f"tran {s.video_max_mb:g}MB moi file")

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {a_tbl} "
                    f"WHERE kind='video' AND clip_state = 0")
        total = cur.fetchone()[0]
    conn.rollback()
    if not total:
        print("  khong co video nao cho quet")
        return 0

    index = PersonIndex(conn, s)
    print(f"  {total} video cho quet, so voi {len(index)} person da biet")
    if not len(index):
        # Truoc day day la diem dung han: khong co person thi khong gan duoc ten
        # nen coi nhu vo ich. Gio thi khong con dung: quet van sinh ra embedding
        # + track cho tung mat, va do chinh la du lieu de PHAT HIEN person moi.
        # Chi khong gan duoc ten, va chua cat duoc doan nao (fp_vclip theo
        # person). Khi Immich da co ten thi chay '--stage rematch' de gan va cat
        # tu du lieu nay, khong phai quet lai video.
        if not s.video_keep_unmatched:
            print("  fp_face chua co embedding nao co person_id, va "
                  "VIDEO_KEEP_UNMATCHED=0 -> khong co gi de luu. "
                  "Chay stage faces + landmarks truoc.")
            return 0
        print("  canh bao: chua co person nao trong fp_face. Van quet va luu "
              "mat + embedding, nhung person_id se NULL het va chua cat doan. "
              "Gan ten trong Immich roi chay '--stage rematch'.")
    sample_ms = int(round(1000.0 / s.video_fps)) if s.video_fps > 0 else 40

    ins_vf, ins_vb, ins_vc = _vface_sql(s), _vbody_sql(s), _vclip_sql(s)

    # Ba model cung luc, khac voi nguyen tac "mot model moi luc" cua cac stage
    # anh. Ly do: chi phi that o day la GIAI MA VIDEO (va o che do HTTP la tai
    # ca file, toi 200MB), khong phai RAM cua model. Quet hai luot de tach model
    # ra se nhan doi phan dat nhat de tiet kiem phan re nhat.
    #   det_10g + w600k_r50   ~200MB
    #   1k3d68 + genderage    ~300MB  (VIDEO_LMK68=0 de tat)
    #   yolov8n-pose          ~200MB  (DO_VBODY=0 de tat)
    model = FaceDetector(s)
    lmk = body = None
    if s.video_lmk68:
        from .facemodel import FaceLandmarker
        lmk = FaceLandmarker(s)
    if s.do_vbody:
        from .bodymodel import BodyPose
        body = BodyPose(s)
    print(f"  luu: mat-la={'co' if s.video_keep_unmatched else 'khong'} "
          f"emb={'co' if s.video_store_emb else 'khong'} "
          f"lmk68={'co' if lmk else 'khong'} "
          f"body={'co' if body else 'khong'}")

    with run_log(conn, s, "clips") as box:
        n_vid = n_frame = n_face = n_clip = n_err = n_bd = n_unk = 0
        t0 = time.time()
        try:
            while True:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT id, video_path, dur_ms FROM {a_tbl} "
                        f"WHERE kind='video' AND clip_state = 0 "
                        f"ORDER BY id LIMIT 1")
                    row = cur.fetchone()
                conn.rollback()
                if not row:
                    break
                aid, vpath, dur_ms = str(row[0]), row[1], row[2]

                path, tmp, why = media.read_video(aid, vpath)
                if path is None:
                    if "vuot tran" in (why or ""):
                        media.n_vid_skip += 1
                    _fail_clip(conn, s, aid, why or "khong doc duoc video")
                    n_err += 1
                    continue
                # Tu day tro di PHAI tra file tam ve, ke ca khi loi: che do HTTP
                # tai ca file video vao cache, khong xoa thi dia day sau vai
                # tram video.
                try:
                    info = video_info(path)
                    if info is None:
                        _fail_clip(conn, s, aid, "khong mo duoc video")
                        n_err += 1
                        continue
                    per_person, rows_vf, rows_vb, nf = _scan_video(
                        path, aid, model, index, s, video_frames,
                        lmk=lmk, body=body)
                finally:
                    media.release(tmp)
                n_frame += nf

                # Can do dai video de khong noi doan vuot qua cuoi file.
                # dur_ms tu db co the NULL -> lay tu header video.
                picked = _pick_clips(aid, per_person, s, sample_ms,
                                     dur_ms or (info[2] or 0))

                with conn.cursor() as cur:
                    if rows_vf:
                        cur.executemany(ins_vf, rows_vf)
                    if rows_vb:
                        cur.executemany(ins_vb, rows_vb)
                    cur.execute(f"DELETE FROM {vc_tbl} WHERE asset_id=%s::uuid",
                                (aid,))
                    if picked:
                        cur.executemany(ins_vc, picked)
                    cur.execute(
                        f"UPDATE {a_tbl} SET clip_state=1, n_clip=%s, "
                        f"n_vface=%s, n_vbody=%s, n_vframe=%s, "
                        f"dur_ms=COALESCE(%s, dur_ms), err=NULL, updated_at=now() "
                        f"WHERE id=%s::uuid",
                        (len(picked), len(rows_vf), len(rows_vb), nf,
                         dur_ms or (info[2] or None), aid))
                conn.commit()
                gc.collect()

                n_vid += 1
                n_face += len(rows_vf)
                n_bd += len(rows_vb)
                pcol = VFACE_COLS.index("person_id")
                n_unk += sum(1 for r in rows_vf if r[pcol] is None)
                n_clip += len(picked)
                _throttle(s)
                _progress("clips", n_vid, total, t0, 5)
                if s.limit and n_vid >= s.limit:
                    break
                if control.should_stop():
                    print("  nhan tin hieu dung, thoat sau khi da commit")
                    break
        finally:
            for m in (model, lmk, body):
                if m is not None:
                    m.close()
            model = lmk = body = None
            gc.collect()

        box["done"] = n_clip
        box["err"] = n_err
        box["note"] = (f"videos={n_vid} frames={n_frame} faces={n_face} "
                       f"unknown={n_unk} bodies={n_bd}")
        print(f"  {n_vid} video, {n_frame} frame da quet, {n_face} mat "
              f"({n_face - n_unk} khop nguoi, {n_unk} chua biet la ai), "
              f"{n_bd} luot nguoi, {n_clip} doan duoc chon"
              + (f", {n_err} video loi" if n_err else ""))
    return n_clip


# smallint cua Postgres. Video co hon 32k track la chuyen khong tuong, nhung neu
# detect nhieu thi khong duoc phep lam sap ca video vi mot con so.
_INT16_MAX = 2 ** 15 - 1


def _track_faces(prev, faces, thr):
    """Gan track_id cho cac mat trong frame hien tai.

    Ghep doi tham lam theo IoU voi frame TRUOC DO. Khong phai Kalman, khong doan
    van toc: o muc lay mau 2 frame/s thi giua hai mau nguoi ta da di duoc nua
    buoc, mo hinh chuyen dong nao cung sai. IoU la thu duy nhat con dung.

    Cai nay dung de GOM, khong dung de dinh danh. Mat quay di roi quay lai sau
    mot giay se thanh track moi — va do la ket qua chap nhan duoc: buoc gom cum
    ve sau lam viec tren embedding, track chi giup cat bot cong viec.

    prev: list (track_id, bbox). Tra ve (track_ids moi, prev cho vong sau).
    """
    from .metrics import iou

    ids, used = [], set()
    for f in faces:
        best_tid, best_v = None, float(thr)
        for tid, bb in prev:
            if tid in used:
                continue
            v = iou(f["bbox"], bb)
            if v >= best_v:
                best_tid, best_v = tid, v
        ids.append(best_tid)
        if best_tid is not None:
            used.add(best_tid)
    return ids


def _scan_video(path, aid, model, index, s, video_frames, lmk=None, body=None):
    """Quet mot video. Tra ve (samples theo person, rows vface, rows vbody, n_frame).

    Muc tieu la du lieu DAY DU DE KHONG PHAI QUET LAI. Cu the:

      - Giu ca mat khong khop person nao (VIDEO_KEEP_UNMATCHED). Ban dau chung bi
        bo han, nghia la nguoi chua gan ten trong Immich khong de lai dau vet gi
        va khong bao gio phat hien duoc "day la mot person moi".
      - Luu embedding (VIDEO_STORE_EMB) de gom cum / gan ten ve sau bang SQL,
        khong phai giai ma lai video.
      - Chay 1k3d68 (VIDEO_LMK68) de co lmk68 + pitch that + age + ear, ngang voi
        anh tinh. Tat di thi pitch/yaw/roll van co, uoc luong tu 5 diem.
      - Chay yolov8n-pose (DO_VBODY) tren CUNG frame do -> fp_vbody.

    Chi mat KHOP person moi vao per_person, vi fp_vclip la "doan dep nhat cua mot
    nguoi" — khong co person thi khong co doan.
    """
    from . import bodyfeat as BF
    from .metrics import (face_metrics, frontality, pose_from_kps5, quality,
                          smile_from_68)
    from .motion import CameraTracker

    per_person, out, out_b = {}, [], []
    n_frame = 0
    prev_tracks, next_tid = [], 0
    min_eye = float(s.video_min_face_px or 0)
    cam = CameraTracker()

    for t_ms, img in video_frames(path, s.video_fps, s.video_max_side,
                                  s.video_max_seconds):
        n_frame += 1
        t_ms = int(t_ms)
        # Do camera TRUOC khi detect: can chinh frame nay so voi frame truoc, va
        # thu tu goi quyet dinh 'frame truoc' la cai nao.
        cam_dx, cam_dy = cam.step(img)
        faces = model(img)
        h, w = img.shape[:2]
        long_side = float(max(w, h))

        # Track gan TRUOC khi loc, de id on dinh khi mot mat truot bo loc o mot
        # frame roi dat lai o frame sau.
        tids = _track_faces(prev_tracks, faces, s.video_track_iou)
        for i, tid in enumerate(tids):
            if tid is None:
                tids[i] = next_tid
                next_tid += 1
        prev_tracks = [(tids[i], faces[i]["bbox"]) for i in range(len(faces))]

        kept_px = []          # (fidx, x1, y1, x2, y2) de khop body voi mat
        for fidx, f in enumerate(faces):
            pid, sim, sim2 = index.match(f["emb"])
            if pid is None and not s.video_keep_unmatched:
                continue
            met = face_metrics(img, f["bbox"], f["kps"])
            if met is None:
                continue
            if min_eye and met["eye_px"] < min_eye:
                continue

            kps5, lm_blob = f["kps"], None
            age = ear = smile = None
            if lmk is not None:
                r = lmk(img, f["bbox"])
                if r is not None:
                    kps5 = r["kps"]
                    yaw, pitch, roll = r["yaw"], r["pitch"], r["roll"]
                    age, ear = r["age"], r["ear"]
                    smile = smile_from_68(r["lmk68"], met["eye_px"])
                    ln = r["lmk68"].copy()
                    ln[:, 0] /= float(w)
                    ln[:, 1] /= float(h)
                    if ln.shape[1] > 2:
                        ln[:, 2] /= float(w)
                    lm_blob = ln.astype(np.float32).tobytes()
                else:
                    yaw, pitch, roll = pose_from_kps5(f["kps"])
            else:
                yaw, pitch, roll = pose_from_kps5(f["kps"])

            fr = frontality(yaw, pitch, roll, met["symm"])
            enorm = f.get("emb_norm")
            qs = quality(met["eye_px"], met["sharp"], met["bright"],
                         yaw, pitch, roll, f["det"], met["symm"], enorm)
            k01 = np.asarray(kps5, np.float32).copy()
            k01[:, 0] /= float(w)
            k01[:, 1] /= float(h)
            bb = f["bbox"]
            emb_blob = None
            if s.video_store_emb and f["emb"] is not None:
                emb_blob = np.asarray(f["emb"], np.float32).tobytes()

            out.append((
                aid, t_ms, fidx,
                float(bb[0]) / w, float(bb[1]) / h,
                float(bb[2]) / w, float(bb[3]) / h,
                f["det"], len(faces),
                k01.astype(np.float32).tobytes(), lm_blob,
                pid, index.name_of(pid), sim, sim2,
                min(int(tids[fidx]), _INT16_MAX),
                yaw, pitch, roll, fr,
                met["sharp"], met["bright"], met["symm"],
                met["eye_px"] / long_side,
                ear, age, smile, qs, emb_blob, enorm,
                cam_dx, cam_dy))
            kept_px.append((fidx, float(bb[0]), float(bb[1]),
                            float(bb[2]), float(bb[3])))

            if pid is None:
                continue
            eye_mid = (np.asarray(kps5, np.float32)[0]
                       + np.asarray(kps5, np.float32)[1]) / 2.0
            per_person.setdefault(pid, []).append({
                "t_ms": t_ms, "det": f["det"], "sim": sim,
                "n_face": len(faces), "sharp": met["sharp"],
                "bright": met["bright"], "frontality": fr, "smile": smile,
                "face_px": met["eye_px"],
                "face_ratio": met["eye_px"] / long_side,
                "cx": float(eye_mid[0]), "cy": float(eye_mid[1]),
                # cam_* duoc luu chuan hoa; cx/cy o day la PIXEL nen phai doi ve
                # cung he, khong thi phep tru 'mat - camera' vo nghia.
                "cam_dx": None if cam_dx is None else cam_dx * w,
                "cam_dy": None if cam_dy is None else cam_dy * h,
                "kps01": k01,
            })

        if body is not None:
            for pidx, p in enumerate(body(img)):
                k = p["kps"]
                feat = BF.describe(k, w, h, p["bbox"])
                norm = k.copy()
                norm[:, 0] /= float(w)
                norm[:, 1] /= float(h)
                bb = p["bbox"]
                out_b.append((
                    aid, t_ms, pidx,
                    bb[0] / w, bb[1] / h, bb[2] / w, bb[3] / h,
                    p["det"], norm.astype(np.float32).tobytes(),
                    feat["n_visible"], feat["orientation"], feat["posture"],
                    feat["torso_deg"], feat["body_front"], feat["area_ratio"],
                    BF.match_face(k, kept_px)))
        del img
    return per_person, out, out_b, n_frame


# Thu tu cot cua mot dong fp_vface. Dat o day, mot cho duy nhat, vi tuple do
# _scan_video sinh ra PHAI khop chinh xac — 29 cot thi lech mot dau la du de ghi
# age vao cot quality ma Postgres khong he bao loi (deu la real).
VFACE_COLS = (
    "asset_id", "t_ms", "fidx", "x1", "y1", "x2", "y2", "det", "n_face",
    "kps", "lmk68", "person_id", "person_name", "sim", "sim2", "track_id",
    "yaw", "pitch", "roll", "frontality", "sharp", "bright", "symm",
    "eye_ratio", "ear", "age", "smile", "quality", "emb", "emb_norm",
    "cam_dx", "cam_dy")

VBODY_COLS = (
    "asset_id", "t_ms", "pidx", "x1", "y1", "x2", "y2", "det", "kps",
    "n_visible", "orientation", "posture", "torso_deg", "body_front",
    "area_ratio", "face_fidx")

# Cot duoc ghi de khi quet lai cung mot (asset_id, t_ms, fidx).
_VFACE_UPSERT = (
    "lmk68", "person_id", "person_name", "sim", "sim2", "track_id",
    "yaw", "pitch", "roll", "frontality", "ear", "age", "smile", "quality",
    "emb", "emb_norm", "cam_dx", "cam_dy")


def _upsert(cols):
    return ", ".join(f"{c}=EXCLUDED.{c}" for c in cols)


def _vface_sql(s):
    """INSERT cho fp_vface, sinh tu VFACE_COLS de khong bao gio lech so cot."""
    return (f"INSERT INTO {s.table('vface')}({','.join(VFACE_COLS)})"
            f" VALUES({','.join(['%s'] * len(VFACE_COLS))})"
            f" ON CONFLICT(asset_id,t_ms,fidx) DO UPDATE SET "
            + _upsert(_VFACE_UPSERT))


def _vbody_sql(s):
    """INSERT cho fp_vbody."""
    skip = {"asset_id", "t_ms", "pidx"}
    return (f"INSERT INTO {s.table('vbody')}({','.join(VBODY_COLS)})"
            f" VALUES({','.join(['%s'] * len(VBODY_COLS))})"
            f" ON CONFLICT(asset_id,t_ms,pidx) DO UPDATE SET "
            + _upsert([c for c in VBODY_COLS if c not in skip]))


def _vclip_sql(s):
    """INSERT cho fp_vclip. Dung boi ca stage clips va stage rematch."""
    return (f"INSERT INTO {s.table('vclip')}"
            f"(asset_id,person_id,cidx,t_start_ms,t_end_ms,t_peak_ms,score,"
            f" n_frame,sim,face_ratio,sharp,bright,frontality,smile,motion,"
            f" shake,action,track,"
            f" updated_at)"
            f" VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())"
            f" ON CONFLICT(asset_id,person_id,cidx) DO UPDATE SET"
            f"   t_start_ms=EXCLUDED.t_start_ms, t_end_ms=EXCLUDED.t_end_ms,"
            f"   t_peak_ms=EXCLUDED.t_peak_ms,"
            f"   score=EXCLUDED.score, n_frame=EXCLUDED.n_frame,"
            f"   sim=EXCLUDED.sim, face_ratio=EXCLUDED.face_ratio,"
            f"   sharp=EXCLUDED.sharp, bright=EXCLUDED.bright,"
            f"   frontality=EXCLUDED.frontality, smile=EXCLUDED.smile,"
            f"   motion=EXCLUDED.motion, shake=EXCLUDED.shake,"
            f"   action=EXCLUDED.action,"
            f"   track=EXCLUDED.track, updated_at=now()")


def _pick_clips(aid, per_person, s, sample_ms, dur_ms):
    """Tu samples theo person -> cac dong fp_vclip. Thuan tinh toan."""
    from . import clips as CL

    picked = []
    for pid, samples in per_person.items():
        wins = CL.best_windows(
            samples, sample_ms,
            target_ms=int(s.clip_seconds * 1000),
            min_ms=int(s.clip_min_seconds * 1000),
            max_ms=int(s.clip_max_seconds * 1000),
            gap_ms=s.video_gap_ms, top=s.clip_per_person,
            dur_ms=dur_ms or 0)
        for cidx, wn in enumerate(wins):
            agg = CL.summarize(wn["frames"])
            picked.append((
                aid, pid, cidx, wn["t_start_ms"], wn["t_end_ms"],
                wn.get("t_peak_ms"), wn["score"], agg["n_frame"], agg["sim"],
                agg["face_ratio"], agg["sharp"], agg["bright"],
                agg["frontality"], agg["smile"], agg["motion"],
                agg["shake"], agg["action"],
                CL.track_blob(wn["frames"])))
    return picked


# ------------------------------------------------------------------ stage 7
def smiles(conn, s):
    """Dien cot smile cho cac dong DA CO lmk68. Khong doc anh, khong load model.

    Stage landmarks tu gio tinh san smile, nen stage nay chi de nang cap db cu.
    Chay bang chi so va vai phep nhan vector: 86k anh xong trong vai phut, so voi
    vai gio neu phai chay lai 1k3d68.

    Duyet bang KEYSET (asset_id, fidx) chu khong phai 'WHERE smile IS NULL':
    mat qua nho thi smile_from_68 tra ve None mot cach co y, va dong do se mai mai
    thoa dieu kien IS NULL -> vong lap khong bao gio ket thuc.
    """
    from .metrics import smile_from_68

    a_tbl, f_tbl, vf_tbl = (s.table("asset"), s.table("face"),
                            s.table("vface"))
    with run_log(conn, s, "smiles") as box:
        n_face = n_vface = n_set = 0
        t0 = time.time()

        # ---------------------------------------------------------- anh tinh
        last = ("00000000-0000-0000-0000-000000000000", -1)
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT f.asset_id, f.fidx, f.lmk68, f.eye_px, "
                    f"       a.img_w, a.img_h "
                    f"FROM {f_tbl} f JOIN {a_tbl} a ON a.id = f.asset_id "
                    f"WHERE f.lmk68 IS NOT NULL "
                    f"  AND (f.asset_id, f.fidx) > (%s::uuid, %s) "
                    f"ORDER BY f.asset_id, f.fidx LIMIT %s",
                    (last[0], last[1], s.batch))
                got = cur.fetchall()
            conn.rollback()
            if not got:
                break
            batch = []
            for aid, fidx, blob, eye_px, iw, ih in got:
                last = (str(aid), int(fidx))
                sm = smile_from_68(_lmk(blob), eye_px, _aspect(iw, ih))
                batch.append((sm, str(aid), fidx))
                n_face += 1
                n_set += sm is not None
            with conn.cursor() as cur:
                cur.executemany(
                    f"UPDATE {f_tbl} SET smile=%s "
                    f"WHERE asset_id=%s::uuid AND fidx=%s", batch)
            conn.commit()
            _progress("smile anh", n_face, None, t0, 2000)
            if control.should_stop():
                print("  nhan tin hieu dung, thoat sau khi da commit")
                break

        # ------------------------------------------------------ frame video
        # eye_px khong co trong fp_vface, chi co eye_ratio = eye_px / canh dai
        # cua frame DA RESIZE. Frame do co canh dai = VIDEO_MAX_SIDE, nen nhan
        # lai la dung nguong ma model thuc su da nhin thay.
        if not control.should_stop():
            side = float(s.video_max_side or 960)
            last = ("00000000-0000-0000-0000-000000000000", -1, -1)
            while True:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT v.asset_id, v.t_ms, v.fidx, v.lmk68, "
                        f"       v.eye_ratio, a.img_w, a.img_h "
                        f"FROM {vf_tbl} v JOIN {a_tbl} a ON a.id = v.asset_id "
                        f"WHERE v.lmk68 IS NOT NULL "
                        f"  AND (v.asset_id, v.t_ms, v.fidx) > (%s::uuid, %s, %s) "
                        f"ORDER BY v.asset_id, v.t_ms, v.fidx LIMIT %s",
                        (last[0], last[1], last[2], s.batch))
                    got = cur.fetchall()
                conn.rollback()
                if not got:
                    break
                batch = []
                for aid, t_ms, fidx, blob, er, iw, ih in got:
                    last = (str(aid), int(t_ms), int(fidx))
                    eye_px = float(er) * side if er is not None else None
                    sm = smile_from_68(_lmk(blob), eye_px, _aspect(iw, ih))
                    batch.append((sm, str(aid), t_ms, fidx))
                    n_vface += 1
                    n_set += sm is not None
                with conn.cursor() as cur:
                    cur.executemany(
                        f"UPDATE {vf_tbl} SET smile=%s "
                        f"WHERE asset_id=%s::uuid AND t_ms=%s AND fidx=%s", batch)
                conn.commit()
                _progress("smile video", n_vface, None, t0, 2000)
                if control.should_stop():
                    print("  nhan tin hieu dung, thoat sau khi da commit")
                    break

        box["done"] = n_set
        box["note"] = f"faces={n_face} vfaces={n_vface}"
        skip = n_face + n_vface - n_set
        print(f"  {n_set} diem nu cuoi tu {n_face} mat anh + {n_vface} mat video"
              + (f", {skip} bo qua vi mat qua nho" if skip else ""))
    return n_set


def _lmk(blob):
    """bytea -> float32[68][3] (hoac [68][2] neu ban cu)."""
    if not blob:
        return None
    a = np.frombuffer(blob, np.float32)
    for k in (3, 2):
        if a.size % k == 0 and a.size // k >= 68:
            return a.reshape(-1, k)
    return None


def _aspect(img_w, img_h):
    """img_w/img_h, dung de bu viec lmk68 duoc chuan hoa x/w va y/h rieng nhau."""
    try:
        w, h = float(img_w or 0), float(img_h or 0)
    except (TypeError, ValueError):
        return 1.0
    return w / h if w > 0 and h > 0 else 1.0


# ------------------------------------------------------------------ stage 6
def rematch(conn, s):
    """Gan lai person cho mat DA QUET, roi cat lai doan. Khong doc video.

    Day la phan thu lai cua viec luu emb trong fp_vface. Truoc day, khi Immich
    vua duoc gan ten cho mot nguoi, cach duy nhat de co doan video cua ho la
    '--reset clips' roi quet lai ca thu vien: hang gio CPU va bang thong de lam
    lai dung viec vua lam, chi vi buoc gan ten thieu du lieu.

    Gio thi doc lai vector da luu, so voi PersonIndex moi, cap nhat person_id roi
    cat lai doan. Khong load model nao, khong mo file nao. Chay bang chi so va
    phep nhan ma tran.

    Khong nam trong 'all': no chi co viec lam sau khi ten trong Immich thay doi.
    """
    from .facedetect import PersonIndex

    a_tbl, vf_tbl = s.table("asset"), s.table("vface")
    index = PersonIndex(conn, s)
    if not len(index):
        print("  chua co person nao trong fp_face -> khong co gi de gan")
        return 0

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(DISTINCT asset_id) FROM {vf_tbl} "
                    f"WHERE emb IS NOT NULL")
        total = cur.fetchone()[0]
    conn.rollback()
    if not total:
        print("  fp_vface chua co dong nao co emb. Ban quet cu khong luu vector; "
              "chay 'job.py --reset clips' mot lan de co.")
        return 0
    print(f"  {total} video co vector da luu, so voi {len(index)} person")

    ins_vc = _vclip_sql(s)
    upd_vf = (f"UPDATE {vf_tbl} SET person_id=%s, person_name=%s, sim=%s, sim2=%s "
              f"WHERE asset_id=%s::uuid AND t_ms=%s AND fidx=%s")
    sample_ms = int(round(1000.0 / s.video_fps)) if s.video_fps > 0 else 40

    with run_log(conn, s, "rematch") as box:
        n_vid = n_row = n_chg = n_clip = 0
        t0 = time.time()
        with conn.cursor() as cur:
            cur.execute(f"SELECT DISTINCT asset_id FROM {vf_tbl} "
                        f"WHERE emb IS NOT NULL ORDER BY asset_id")
            aids = [str(r[0]) for r in cur.fetchall()]
        conn.rollback()

        for aid in aids:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT t_ms,fidx,emb,det,n_face,sharp,bright,frontality,"
                    f"       eye_ratio,kps,person_id,smile,cam_dx,cam_dy "
                    f"FROM {vf_tbl} WHERE asset_id=%s::uuid AND emb IS NOT NULL "
                    f"ORDER BY t_ms,fidx", (aid,))
                rows = cur.fetchall()
                cur.execute(f"SELECT dur_ms FROM {a_tbl} WHERE id=%s::uuid", (aid,))
                got = cur.fetchone()
            conn.rollback()
            dur_ms = (got[0] if got else None) or 0

            per_person, changes = {}, []
            for (t_ms, fidx, blob, det, n_face, sharp, bright, fr,
                 eye_ratio, kps_blob, old_pid, smile, cdx, cdy) in rows:
                n_row += 1
                emb = np.frombuffer(blob, np.float32)
                pid, sim, sim2 = index.match(emb)
                if str(pid) != str(old_pid):
                    changes.append((pid, index.name_of(pid), sim, sim2,
                                    aid, t_ms, fidx))
                if pid is None:
                    continue
                # Toa do da chuan hoa 0..1. Giu MOI thu trong cung he chuan hoa
                # do: motion_of chia dich chuyen cho be rong mat, nen chi can hai
                # dai luong cung don vi, khong can quay ve pixel.
                k = (np.frombuffer(kps_blob, np.float32).reshape(-1, 2)
                     if kps_blob else None)
                if k is None or k.shape[0] < 2:
                    continue
                eye_mid = (k[0] + k[1]) / 2.0
                per_person.setdefault(str(pid), []).append({
                    "t_ms": int(t_ms), "det": det, "sim": sim,
                    "n_face": n_face, "sharp": sharp, "bright": bright,
                    "frontality": fr, "smile": smile,
                    "face_px": float(np.linalg.norm(k[1] - k[0])),
                    "face_ratio": eye_ratio,
                    "cx": float(eye_mid[0]), "cy": float(eye_mid[1]),
                    # cx/cy o day la CHUAN HOA, va cam_* trong db cung chuan hoa
                    # -> dung nguyen, khong doi don vi nhu trong _scan_video.
                    "cam_dx": cdx, "cam_dy": cdy,
                    "kps01": k,
                })

            picked = _pick_clips(aid, per_person, s, sample_ms, dur_ms)
            with conn.cursor() as cur:
                if changes:
                    cur.executemany(upd_vf, changes)
                cur.execute(f"DELETE FROM {s.table('vclip')} "
                            f"WHERE asset_id=%s::uuid", (aid,))
                if picked:
                    cur.executemany(ins_vc, picked)
                cur.execute(f"UPDATE {a_tbl} SET n_clip=%s, updated_at=now() "
                            f"WHERE id=%s::uuid", (len(picked), aid))
            conn.commit()
            n_vid += 1
            n_chg += len(changes)
            n_clip += len(picked)
            _progress("rematch", n_vid, total, t0, 50)
            if control.should_stop():
                print("  nhan tin hieu dung, thoat sau khi da commit")
                break

        box["done"] = n_clip
        box["note"] = f"videos={n_vid} rows={n_row} changed={n_chg}"
        print(f"  {n_vid} video, {n_row} mat doc lai, {n_chg} mat doi person, "
              f"{n_clip} doan")
    return n_clip


def _fail_clip(conn, s, aid, why):
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {s.table('asset')} SET clip_state=-1, err=%s, "
            f"updated_at=now() WHERE id=%s::uuid", (why[:200], aid))
    conn.commit()
    print(f"  [loi] {aid}: {why}")


# ---------------------------------------------------------------- reset / stat
def reset(conn, s, what):
    """Danh dau lai de chay lai mot stage. Khong xoa du lieu Immich."""
    a, f, b = s.table("asset"), s.table("face"), s.table("body")
    vf, vc, vb = s.table("vface"), s.table("vclip"), s.table("vbody")
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
        if what in ("clips", "all"):
            cur.execute(f"DELETE FROM {vf}")
            cur.execute(f"DELETE FROM {vc}")
            cur.execute(f"DELETE FROM {vb}")
            cur.execute(f"UPDATE {a} SET n_clip=0, n_vface=0, n_vbody=0, "
                        f"n_vframe=0, "
                        f"clip_state=CASE WHEN kind='video' THEN 0 ELSE 2 END")

        if what == "errors":
            cur.execute(f"UPDATE {a} SET face_state=2 WHERE face_state=-1")
            cur.execute(f"UPDATE {a} SET body_state=0 WHERE body_state=-1")
            cur.execute(f"UPDATE {a} SET clip_state=0 "
                        f"WHERE clip_state=-1 AND kind='video'")
            cur.execute(f"UPDATE {a} SET err=NULL")
    conn.commit()
    print(f"reset '{what}' xong")


def stats(conn, s):
    a, f, b, r = (s.table("asset"), s.table("face"),
                  s.table("body"), s.table("run"))
    vf, vc, vb = s.table("vface"), s.table("vclip"), s.table("vbody")
    rows = [
        ("assets", f"SELECT COUNT(*) FROM {a}"),
        ("  anh", f"SELECT COUNT(*) FROM {a} WHERE kind='image'"),
        ("  video", f"SELECT COUNT(*) FROM {a} WHERE kind='video'"),
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
        ("  co diem cuoi", f"SELECT COUNT(*) FROM {f} WHERE smile IS NOT NULL"),
        ("  dang cuoi >0.5", f"SELECT COUNT(*) FROM {f} WHERE smile > 0.5"),
        ("  co ten", f"SELECT COUNT(*) FROM {f} WHERE person_name IS NOT NULL"),
        ("bodies", f"SELECT COUNT(*) FROM {b}"),
        ("  khop khuon mat", f"SELECT COUNT(*) FROM {b} WHERE face_fidx IS NOT NULL"),
        ("video cho quet", f"SELECT COUNT(*) FROM {a} "
                           f"WHERE kind='video' AND clip_state=0"),
        ("  quet xong", f"SELECT COUNT(*) FROM {a} "
                        f"WHERE kind='video' AND clip_state=1"),
        ("  loi", f"SELECT COUNT(*) FROM {a} "
                  f"WHERE kind='video' AND clip_state=-1"),
        ("  frame da quet", f"SELECT COALESCE(SUM(n_vframe),0) FROM {a} "
                            f"WHERE kind='video'"),
        ("mat trong video", f"SELECT COUNT(*) FROM {vf}"),
        ("  khop person", f"SELECT COUNT(*) FROM {vf} "
                          f"WHERE person_id IS NOT NULL"),
        ("  chua biet la ai", f"SELECT COUNT(*) FROM {vf} "
                              f"WHERE person_id IS NULL"),
        ("  cum chua biet", f"SELECT COUNT(*) FROM ("
                            f"SELECT DISTINCT asset_id, track_id FROM {vf} "
                            f"WHERE person_id IS NULL AND track_id IS NOT NULL) t"),
        ("  co embedding", f"SELECT COUNT(*) FROM {vf} WHERE emb IS NOT NULL"),
        ("  co lmk68", f"SELECT COUNT(*) FROM {vf} WHERE lmk68 IS NOT NULL"),
        ("nguoi trong video", f"SELECT COUNT(*) FROM {vb}"),
        ("  khop khuon mat", f"SELECT COUNT(*) FROM {vb} "
                             f"WHERE face_fidx IS NOT NULL"),
        ("doan da chon", f"SELECT COUNT(*) FROM {vc}"),
        ("  nguoi co doan", f"SELECT COUNT(DISTINCT person_id) FROM {vc}"),
    ]
    with conn.cursor() as cur:
        for label, sql in rows:
            cur.execute(sql)
            print(f"{label:<20}{cur.fetchone()[0]}")
        for tbl, tag in ((b, "anh"), (vb, "video")):
            for col in ("orientation", "posture"):
                cur.execute(f"SELECT {col}, COUNT(*) FROM {tbl} GROUP BY {col} "
                            f"ORDER BY 2 DESC")
                got = cur.fetchall()
                if got:
                    print(f"{col + ' ' + tag:<20}"
                          + "  ".join(f"{k}={v}" for k, v in got))
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
