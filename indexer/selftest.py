"""Kiem thu logic thuan cua indexer: khong can Postgres, Immich, model, video.

    python selftest.py

Verify duoc: cham diem frame, gom doan lien tuc, truot cua so chon doan, phat
hien rung, va uoc luong huong dau tu 5 diem. Phan can model/db thi verify tren
may dich bang `python job.py --dry-run`.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np                                                    # noqa: E402

from idx import clips as CL                                            # noqa: E402
from idx.metrics import pose_from_kps5                                 # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def frame(t_ms=0, face=0.10, sharp=300, bright=120, front=0.8, det=0.9,
          cx=0.5, cy=0.4, n_face=1):
    """Mot frame da lay mau. cx/cy tinh theo pixel gia dinh 1000x1000."""
    return {"t_ms": t_ms, "face_ratio": face, "sharp": sharp, "bright": bright,
            "frontality": front, "det": det, "n_face": n_face,
            "face_px": face * 1000.0, "cx": cx * 1000.0, "cy": cy * 1000.0}


# ------------------------------------------------------------- cham diem
def t_score():
    print("\n[1] cham diem tung frame")
    good = CL.score_frame(frame())
    check(0 < good <= 100, f"diem nam trong 0..100 ({good:.1f})")
    check(CL.score_frame(frame(0, front=0.1)) < good, "mat quay di thi diem thap hon")
    check(CL.score_frame(frame(0, sharp=10)) < good, "anh mo thi diem thap hon")
    check(CL.score_frame(frame(0, face=0.01)) < good, "mat qua nho thi diem thap hon")
    check(CL.score_frame(frame(0, bright=250)) < good, "chay sang thi diem thap hon")
    check(CL.score_frame(frame(0, n_face=3)) > good,
          "co nguoi khac trong khung thi duoc cong (ngu canh)")
    check(CL.score_frame({"t_ms": 0}) > 0,
          "thieu het chi so van ra diem, khong no ra loi")


# --------------------------------------------------------- gom doan lien tuc
def t_runs():
    print("\n[2] gom doan lien tuc")
    # nguoi do o 0-2s, mat hut 5s, roi lai xuat hien 7-9s
    a = [frame(t) for t in range(0, 2001, 500)]
    b = [frame(t) for t in range(7000, 9001, 500)]
    got = CL.runs(a + b, gap_ms=800)
    check(len(got) == 2, f"tach dung hai doan ({len(got)})")
    check(len(got[0]) == len(a) and len(got[1]) == len(b), "khong lam mat frame nao")
    # mat hut mot frame duy nhat (500ms) van tinh la lien tuc
    hole = [frame(t) for t in (0, 500, 1500, 2000)]
    check(len(CL.runs(hole, gap_ms=800)) == 2,
          "cach 1000ms > gap 800ms -> tach")
    check(len(CL.runs(hole, gap_ms=1200)) == 1,
          "cach 1000ms < gap 1200ms -> van lien tuc")


# ------------------------------------------------------------- do rung
def t_motion():
    print("\n[3] do rung")
    still = [frame(t) for t in range(0, 2001, 500)]
    check(CL.motion_of(still) == 0.0, "mat dung yen -> rung = 0")
    shaky = [frame(t, cx=0.5 + 0.15 * (i % 2)) for i, t in
             enumerate(range(0, 2001, 500))]
    check(CL.motion_of(shaky) > 0.5, f"mat nhay qua lai -> rung cao "
                                     f"({CL.motion_of(shaky):.2f})")
    drift = [frame(t, cx=0.4 + 0.0002 * t) for t in range(0, 2001, 500)]
    check(0 < CL.motion_of(drift) < CL.motion_of(shaky),
          "di chuyen deu thi rung thap hon nhay qua lai")


# --------------------------------------------------------- chon doan tot nhat
def t_windows():
    print("\n[4] truot cua so chon doan")
    # 10 giay: 0-3s tam thuong, 4-7s rat tot, 8-10s mo
    samples = []
    for t in range(0, 3001, 500):
        samples.append(frame(t, front=0.45, sharp=120))
    for t in range(4000, 7001, 500):
        samples.append(frame(t, front=0.95, sharp=380, face=0.12))
    for t in range(8000, 10001, 500):
        samples.append(frame(t, sharp=15, front=0.3))

    wins = CL.best_windows(samples, 500, target_ms=2600, min_ms=1200,
                           max_ms=4500, gap_ms=800, top=3)
    check(len(wins) >= 1, "chon duoc it nhat mot doan")
    top = wins[0]
    # Doan duoc noi toi da MOT khoang lay mau ra ngoai frame ngoai cung (xem
    # clip_bounds), nen no khong con nam tron trong 4-7s nua. Kiem tra theo phan
    # CHONG voi khuc tot, va theo cho dat khoanh khac.
    dur = top["t_end_ms"] - top["t_start_ms"]
    ov = min(top["t_end_ms"], 7000) - max(top["t_start_ms"], 4000)
    check(dur > 0 and ov / dur >= 0.7,
          f"doan tot nhat chu yeu nam trong 4-7s "
          f"(duoc {top['t_start_ms']}-{top['t_end_ms']}, chong {ov}/{dur})")
    check(4000 <= top["t_peak_ms"] <= 7000,
          f"khoanh khac nam trong khuc tot (duoc {top['t_peak_ms']})")
    check(1200 <= top["dur_ms"] <= 4500, f"do dai trong khoang ({top['dur_ms']}ms)")
    check(all(wins[i]["score"] >= wins[i + 1]["score"] for i in range(len(wins) - 1)),
          "xep theo diem giam dan")
    for i in range(len(wins) - 1):
        for j in range(i + 1, len(wins)):
            check(wins[i]["t_end_ms"] <= wins[j]["t_start_ms"]
                  or wins[i]["t_start_ms"] >= wins[j]["t_end_ms"],
                  f"doan {i} va {j} khong chong nhau")

    # doan rung phai thua doan yen du diem tung frame bang nhau
    calm = [frame(t) for t in range(0, 3001, 500)]
    jerky = [frame(t, cx=0.5 + 0.2 * (i % 2)) for i, t in
             enumerate(range(5000, 8001, 500))]
    both = CL.best_windows(calm + jerky, 500, gap_ms=800, top=2)
    check(both and both[0]["t_start_ms"] < 4000,
          "doan yen thang doan rung khi diem tung frame nhu nhau")

    # video ngan hon min_ms van phai ra duoc mot doan
    short = [frame(t) for t in (0, 300, 600, 900)]
    check(len(CL.best_windows(short, 300, min_ms=1200)) >= 1,
          "video 0.9s ngan hon min_ms van chon duoc doan")

    # khong co frame nao -> khong no
    check(CL.best_windows([], 500) == [], "danh sach rong -> khong co doan")

    # --- hoi quy: bang chung thua thot khong duoc ra doan ngan hon min_ms ---
    # Truoc khi co clip_bounds, t_start/t_end lay thang t_ms cua frame dau/cuoi
    # nen hai frame cach nhau 480ms ra doan 480ms. Bo loc min_clip_seconds cua
    # UI (0.8s) nem het nhung doan nay di: quet ton CPU roi bo.
    sparse = [frame(112800), frame(113280)]
    w = CL.best_windows(sparse, 500, target_ms=2600, min_ms=1200, max_ms=4500,
                        gap_ms=800, top=3)
    check(len(w) == 1, f"hai frame roi rac van ra mot doan ({len(w)})")
    d = w[0]["t_end_ms"] - w[0]["t_start_ms"]
    check(d >= 1200, f"doan tu bang chung thua thot van dat min_ms (duoc {d}ms)")
    check(w[0]["t_start_ms"] <= w[0]["t_peak_ms"] <= w[0]["t_end_ms"],
          "khoanh khac van nam trong doan da noi")

    # Khong duoc noi vuot qua cuoi video khi biet dur_ms.
    w2 = CL.best_windows(sparse, 500, min_ms=1200, max_ms=4500, gap_ms=800,
                         dur_ms=113500)
    check(w2 and w2[0]["t_end_ms"] <= 113500,
          f"khong noi vuot cuoi video (duoc {w2 and w2[0]['t_end_ms']})")
    check(w2 and w2[0]["t_start_ms"] >= 0, "khong noi xuong duoi 0")

    # Bang chung day du thi do dai van bam target, khong bi phinh len max_ms.
    dense = [frame(t) for t in range(0, 6001, 500)]
    w3 = CL.best_windows(dense, 500, target_ms=2600, min_ms=1200, max_ms=4500,
                         gap_ms=800, top=1)
    check(w3 and 1200 <= (w3[0]["t_end_ms"] - w3[0]["t_start_ms"]) <= 4500,
          f"bang chung day du -> do dai trong khoang "
          f"(duoc {w3 and w3[0]['t_end_ms'] - w3[0]['t_start_ms']}ms)")


def t_peaks():
    print("\n[4b] tim khoanh khac (kieu HiLight)")
    # duong diem co hai cao trao: quanh 2s va quanh 8s
    run = []
    for t in range(0, 10001, 250):
        s = 40.0
        for c in (2000, 8000):
            s += 50.0 * max(0.0, 1.0 - abs(t - c) / 1200.0)
        run.append(frame(t, front=min(1.0, s / 100.0), sharp=s * 3))
    pk = CL.peaks(run, min_gap_ms=2600, top=4)
    check(len(pk) >= 2, f"tim duoc ca hai cao trao ({len(pk)})")
    times = sorted(run[i]["t_ms"] for i in pk[:2])
    check(abs(times[0] - 2000) < 600 and abs(times[1] - 8000) < 600,
          f"dinh nam dung cho ({times})")
    check(all(abs(run[a]["t_ms"] - run[b]["t_ms"]) >= 2600
              for i, a in enumerate(pk) for b in pk[i + 1:]),
          "hai dinh khong bao gio la hai lat cua cung mot cao trao")

    # mot frame nhieu don doc khong duoc thanh khoanh khac
    flat = [frame(t, front=0.5, sharp=100) for t in range(0, 5001, 250)]
    flat[7] = frame(flat[7]["t_ms"], front=1.0, sharp=400)
    sm = CL.smooth([CL.score_frame(f) for f in flat])
    raw = [CL.score_frame(f) for f in flat]
    check(sm[7] < raw[7], "lam tron ha bot mot frame cao dot ngot")

    # dinh nam o dau doan
    span = CL.window_around(run, pk[0], target_ms=2600, min_ms=1200, max_ms=4500)
    check(span is not None, "sinh duoc cua so quanh dinh")
    i, j = span
    t0, t1, tp = run[i]["t_ms"], run[j]["t_ms"], run[pk[0]]["t_ms"]
    frac = (tp - t0) / max(1, t1 - t0)
    check(t0 <= tp <= t1, "khoanh khac nam trong doan")
    check(abs(frac - CL.PEAK_POS) < 0.2,
          f"khoanh khac nam o khoang {CL.PEAK_POS} cua doan (duoc {frac:.2f})")
    check(1200 <= (t1 - t0) <= 4500, f"do dai trong khoang ({t1 - t0}ms)")

    # dinh sat dau doan -> khong the dat 60%, phai noi ra phia sau
    edge = CL.window_around(run, 0, target_ms=2600, min_ms=1200, max_ms=4500)
    check(edge is not None and (run[edge[1]]["t_ms"] - run[edge[0]]["t_ms"]) >= 1200,
          "dinh sat bien van ra doan du dai")

    wins = CL.best_windows(run, 250, target_ms=2600, min_ms=1200, max_ms=4500,
                           gap_ms=800, top=3)
    check(wins and all("t_peak_ms" in w for w in wins),
          "moi doan tra ve deu kem moc khoanh khac")
    check(all(w["t_start_ms"] <= w["t_peak_ms"] <= w["t_end_ms"] for w in wins),
          "moc khoanh khac luon nam trong doan cua no")


def t_track():
    print("\n[5] track blob")
    win = [dict(frame(t), kps01=np.full((5, 2), 0.5, np.float32))
           for t in (0, 500, 1000)]
    blob = CL.track_blob(win)
    a = np.frombuffer(blob, np.float32).reshape(-1, 11)
    check(a.shape == (3, 11), f"float32[n][11] ({a.shape})")
    check(abs(a[1, 0] - 0.5) < 1e-6, "cot dau la thoi gian theo GIAY")
    check(CL.track_blob([frame(0)]) is None, "khong co kps -> None")


# ------------------------------------------------------- huong dau tu 5 diem
def t_pose():
    print("\n[6] uoc luong huong dau tu 5 diem")

    def kps(le, re, nose, lm, rm):
        return np.array([le, re, nose, lm, rm], np.float32)

    front = kps((40, 50), (80, 50), (60, 68), (48, 86), (72, 86))
    yaw, pitch, roll = pose_from_kps5(front)
    check(abs(yaw) < 6 and abs(roll) < 2, f"chinh dien -> yaw~0 roll~0 "
                                          f"({yaw:.1f}, {roll:.1f})")

    # quay sang phai: mui lech ve mot ben
    right = kps((40, 50), (80, 50), (72, 68), (60, 86), (84, 86))
    yaw_r, _, _ = pose_from_kps5(right)
    check(yaw_r > 15, f"mui lech phai -> yaw duong ({yaw_r:.1f})")
    left = kps((40, 50), (80, 50), (48, 68), (36, 86), (60, 86))
    yaw_l, _, _ = pose_from_kps5(left)
    check(yaw_l < -15, f"mui lech trai -> yaw am ({yaw_l:.1f})")

    # nghieng dau 20 do
    import math
    ang = math.radians(20)
    c, s = math.cos(ang), math.sin(ang)
    tilt = kps((60 - 20 * c, 50 - 20 * s), (60 + 20 * c, 50 + 20 * s),
               (60, 68), (48, 86), (72, 86))
    _, _, roll_t = pose_from_kps5(tilt)
    check(abs(roll_t - 20) < 3, f"nghieng 20 do -> roll ~20 ({roll_t:.1f})")

    check(pose_from_kps5(np.zeros((5, 2), np.float32)) == (0.0, 0.0, 0.0),
          "diem trung nhau -> khong chia cho 0")
    check(pose_from_kps5(np.zeros((2, 2), np.float32)) == (0.0, 0.0, 0.0),
          "thieu diem -> tra ve 0, khong no")


def main():
    t_score()
    t_runs()
    t_motion()
    t_windows()
    t_peaks()
    t_track()
    t_pose()
    print("\n" + "=" * 60)
    if FAIL:
        print(f"{len(FAIL)} kiem tra THAT BAI:")
        for f in FAIL:
            print("  - " + f)
        return 1
    print("tat ca kiem tra dat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
