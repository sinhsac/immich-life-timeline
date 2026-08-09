#!/usr/bin/env python3
"""Job index toan bo anh trong Immich: head pose + body pose -> Postgres.

Mot lenh duy nhat, chay tuan tu bon stage, resumable:

    python job.py                # = run all
    python job.py --stage assets
    python job.py --stage bodies
    python job.py --stats
    python job.py --reset errors

Cau hinh hoan toan qua bien moi truong, xem README.md.
Ket qua nam trong cac bang co prefix TABLE_PREFIX (mac dinh fp_):
    fp_asset  fp_face  fp_body  fp_run  fp_state

Bang cua Immich chi doc, khong bao gio bi ghi.
"""
import argparse
import signal
import sys
import time

from idx import control, immich_src, pgdb, settings, stages
from idx.media import MediaReader

STAGES = ("assets", "faces", "landmarks", "bodies")


def _on_signal(signum, _frame):
    """K8s gui SIGTERM khi evict/scale, va khi may shutdown.

    Co nam trong idx.control de stages.py kiem tra duoc sau moi lan commit.
    Truoc day co nam cuc bo trong file nay va chi kiem tra giua cac stage, nen
    SIGTERM giua mot stage khong co tac dung gi.
    """
    control.request_stop()
    print(f"\nnhan signal {signum}: se dung sau khi commit lo hien tai")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="job.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", action="append", choices=STAGES + ("all",),
                    help="chay rieng mot stage, lap lai duoc. Mac dinh: all")
    ap.add_argument("--stats", action="store_true", help="chi in tinh trang")
    ap.add_argument("--reset", choices=("faces", "landmarks", "bodies",
                                        "errors", "all"),
                    help="danh dau lai de chay lai stage tuong ung")
    ap.add_argument("--dry-run", action="store_true",
                    help="kiem tra ket noi + model roi thoat")
    a = ap.parse_args(argv)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    s = settings.load()
    print("=" * 64)
    print(s.describe())
    print("=" * 64)

    conn = pgdb.connect(s)
    try:
        pgdb.ensure_schema(conn, s)

        # Chi cac lenh that su ghi du lieu moi can khoa. --stats / --dry-run
        # doc thoi nen cho chay song song voi job dang lam viec.
        writes = not (a.stats or a.dry_run)
        if writes:
            if not pgdb.try_lock(conn, s):
                print("Da co mot indexer khac dang chay (khoa advisory dang bi "
                      "giu). Thoat, khong lam gi.")
                return 0
            pgdb.close_orphan_runs(conn, s)

        t = immich_src.Tables(conn, s.pg_schema)
        print(t.describe())

        if a.stats:
            stages.stats(conn, s)
            return 0
        if a.reset:
            stages.reset(conn, s, a.reset)
            return 0

        want = a.stage or ["all"]
        todo = list(STAGES) if "all" in want else [x for x in STAGES if x in want]

        if a.dry_run:
            return _dry_run(conn, s, t, todo)

        s.require_media()
        media = MediaReader(s)
        t0 = time.time()
        for name in todo:
            if control.should_stop():
                print("dung theo yeu cau")
                break
            _head(name)
            if name == "assets":
                stages.sync_assets(conn, s, t)
            elif name == "faces":
                stages.sync_faces(conn, s, t)
            elif name == "landmarks":
                stages.landmarks(conn, s, t, media)
            elif name == "bodies":
                stages.bodies(conn, s, t, media)
        print(f"\n{media.stats()}")
        print(f"tong thoi gian {(time.time() - t0) / 60:.1f} phut\n")
        _head("tinh trang")
        stages.stats(conn, s)
        return 0
    finally:
        try:
            conn.close()
        except Exception:                                # noqa: BLE001
            pass


def _dry_run(conn, s, t, todo):
    print("\n--dry-run: kiem tra dieu kien\n")
    ok = True
    n = immich_src.count_assets(conn, t, s.taken_after, s.taken_before)
    print(f"  [ok] doc duoc {n} anh tu Immich")

    s.require_media()
    m = MediaReader(s)
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, preview_path FROM {s.table('asset')} "
                    f"WHERE preview_path IS NOT NULL LIMIT 3")
        rows = cur.fetchall()
    conn.rollback()
    if not rows:
        print("  [--] fp_asset con trong, chay --stage assets truoc de kiem tra anh")
    for aid, prev in rows:
        img, tmp = m.read(str(aid), prev, s.max_side)
        m.release(tmp)
        if img is None:
            print(f"  [LOI] khong doc duoc anh {aid} (path={prev})")
            ok = False
        else:
            print(f"  [ok] doc anh {img.shape[1]}x{img.shape[0]} tu {aid}")
            break

    if "landmarks" in todo:
        try:
            from idx.facemodel import FaceLandmarker
            fm = FaceLandmarker(s)
            fm.close()
            print(f"  [ok] model {s.face_model} (1k3d68 + genderage) load duoc")
        except SystemExit as e:
            print(f"  [LOI] {e}")
            ok = False
    if "bodies" in todo:
        try:
            from idx.bodymodel import BodyPose
            bp = BodyPose(s)
            bp.close()
            print("  [ok] model body pose load duoc")
        except SystemExit as e:
            print(f"  [LOI] {e}")
            ok = False

    print("\n" + ("san sang chay." if ok else "con loi phia tren, sua truoc khi chay."))
    return 0 if ok else 1


def _head(name):
    print("\n" + "-" * 64)
    print(f"  stage: {name}")
    print("-" * 64)


if __name__ == "__main__":
    sys.exit(main() or 0)
