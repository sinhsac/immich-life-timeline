"""Chon "doan dep nhat co ngu canh nhat" trong mot video.

Vao: cac frame da lay mau cua MOT nguoi trong MOT video, moi frame kem chi so
(mat to nho, do net, do sang, do chinh dien, vi tri mat). Ra: mot vai doan
[t_start, t_end] duoc xep hang, moi doan kem MOC KHOANH KHAC (t_peak_ms).

Bon buoc, va thu tu quan trong:

  1 GOM DOAN LIEN TUC. Nguoi do co the ra vao khung nhieu lan trong mot clip.
    Cat mot doan bat qua khoang nguoi do khong co mat trong khung la vo nghia.
    Cho phep mat hut trong gap_ms (mat quay di mot chut, detect truot mot frame)
    ma van tinh la lien tuc.

  2 CHAM DIEM TUNG FRAME. Mat du to, du net, du sang, co huong vao may, co nguoi
    khac ben canh.

  3 TIM KHOANH KHAC (highlight). Day la cho da doi cach lam. Ban dau la truot
    cua so tim doan co diem TRUNG BINH cao nhat — nhung the la tim "doan deu
    tot", khong phai tim "khoanh khac". Mot doan 3 giay deu deu 70 diem se thang
    mot doan co dung mot giay 95 diem, trong khi cai dang xem lai la giay 95 diem
    do.

    Cach cua GoPro: nguoi dung (hoac may) danh dau HiLight — mot DIEM tren dong
    thoi gian — roi video ket qua lay mot doan quanh diem do. O day cung vay:
    tim cac dinh cua duong diem (da lam tron de mot frame nhieu khong thanh dinh),
    loai cac dinh qua gan nhau, roi moi dinh sinh ra mot doan ung vien.

  4 DAT DINH VAO DUNG CHO trong doan. Dinh khong nam giua: dat o PEAK_POS (60%)
    nen truoc no la doan dan, sau no la doan buong. Do la nhip co mo dau va co
    ket, thay vi mot khuc cat ngau nhien. Roi cham diem ca cua so (co tru do
    rung) de xep hang giua cac dinh.

Module nay thuan tinh toan — khong doc video, khong goi model, khong cham db.
"""

# Trong so cham diem mot frame. Tong 100.
# 'context' la mot khoan nho co chu y: doan co nguoi khac trong khung thuong la
# mot khoanh khac that (dang choi, dang an, dang chup cung ai do) chu khong phai
# mot doan mat nhin vao may. Yeu cau la "doan dep nhat CO NGU CANH NHAT", nen no
# co diem — nhung it, de khong bien video thanh toan canh dong nguoi.
W = {"frontal": 28.0, "face": 23.0, "sharp": 23.0, "expo": 10.0,
     "det": 10.0, "context": 6.0}

# Mat cach nhau 9% canh dai la "thay ro nguoi" — tren muc do khong cong them.
FACE_REF = 0.09
SHARP_REF = 300.0
BRIGHT_MID = 120.0

# Khoanh khac nam o dau trong doan. 0.6 = truoc no 60% de dan, sau no 40% de
# buong. Dat giua (0.5) thi doan bi can doi den muc phang; dat cuoi thi cat ngay
# sau cao trao, nguoi xem chua kip cam nhan.
PEAK_POS = 0.60


def score_frame(f):
    """0..100 cho mot frame. Thieu chi so nao thi coi nhu trung binh, khong loai."""
    fr = f.get("frontality")
    fr = 0.5 if fr is None else max(0.0, min(1.0, float(fr)))
    face = min(1.0, max(0.0, float(f.get("face_ratio") or 0.0)) / FACE_REF)
    sharp = min(1.0, max(0.0, float(f.get("sharp") or 0.0)) / SHARP_REF)
    br = f.get("bright")
    expo = 0.5 if br is None else 1.0 - min(1.0, abs(float(br) - BRIGHT_MID) / BRIGHT_MID)
    det = min(1.0, max(0.0, float(f.get("det") or 0.0)))
    ctx = min(1.0, max(0, int(f.get("n_face") or 1) - 1) / 2.0)
    return (W["frontal"] * fr + W["face"] * face + W["sharp"] * sharp
            + W["expo"] * expo + W["det"] * det + W["context"] * ctx)


def runs(samples, gap_ms=800):
    """Chia thanh cac doan lien tuc theo thoi gian."""
    out, cur = [], []
    for f in sorted(samples, key=lambda x: x["t_ms"]):
        if cur and f["t_ms"] - cur[-1]["t_ms"] > gap_ms:
            out.append(cur)
            cur = []
        cur.append(f)
    if cur:
        out.append(cur)
    return out


def motion_of(win):
    """Do rung: khuon mat dich chuyen bao nhieu "chieu rong mat" moi giay.

    Chia cho kich thuoc mat chu khong phai cho kich thuoc khung: mat to di 50px
    la binh thuong, mat nho di 50px la giat.
    """
    if len(win) < 2:
        return 0.0
    tot, n = 0.0, 0
    for a, b in zip(win, win[1:]):
        dt = (b["t_ms"] - a["t_ms"]) / 1000.0
        if dt <= 0:
            continue
        scale = max(1e-6, (a.get("face_px") or 0.0))
        if scale <= 1e-6:
            continue
        dx = (b.get("cx") or 0.0) - (a.get("cx") or 0.0)
        dy = (b.get("cy") or 0.0) - (a.get("cy") or 0.0)
        tot += ((dx * dx + dy * dy) ** 0.5) / scale / dt
        n += 1
    return tot / n if n else 0.0


def smooth(vals, k=3):
    """Trung binh truot. Mot frame nhieu (detect truot, den flash) khong duoc
    tro thanh mot 'khoanh khac' chi vi no cao dot ngot."""
    n = len(vals)
    if n <= 2 or k < 2:
        return list(vals)
    half = k // 2
    out = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out.append(sum(vals[lo:hi]) / (hi - lo))
    return out


def peaks(run, min_gap_ms, top=4):
    """Cac chi so la KHOANH KHAC trong mot doan lien tuc, tot nhat truoc.

    Dinh = diem cao hon hai ben (sau khi lam tron). Sau do loai dan: giu dinh cao
    nhat, bo moi dinh cach no duoi min_gap_ms, lap lai. Nho vay hai khoanh khac
    duoc chon khong bao gio la hai lat cua cung mot cao trao.
    """
    if not run:
        return []
    sc = smooth([score_frame(f) for f in run])
    n = len(sc)
    cand = []
    for i in range(n):
        left = sc[i - 1] if i > 0 else -1.0
        right = sc[i + 1] if i + 1 < n else -1.0
        if sc[i] >= left and sc[i] >= right:
            cand.append(i)
    if not cand:
        cand = [max(range(n), key=lambda i: sc[i])]
    cand.sort(key=lambda i: -sc[i])
    keep = []
    for i in cand:
        if all(abs(run[i]["t_ms"] - run[j]["t_ms"]) >= min_gap_ms for j in keep):
            keep.append(i)
        if len(keep) >= top:
            break
    return keep


def window_around(run, peak, target_ms, min_ms, max_ms):
    """Cua so quanh mot khoanh khac, voi dinh nam o PEAK_POS. Tra ve (i, j).

    Chay den bien doan thi khong keo dai sang phia kia de bu — mot doan bi cat
    ngan o dau van la doan quanh dung khoanh khac, con keo dai phia sau la lay
    thanh phan khong lien quan.
    """
    if not run:
        return None
    n = len(run)
    t_peak = run[peak]["t_ms"]
    want0 = t_peak - target_ms * PEAK_POS
    want1 = want0 + target_ms

    i = peak
    while i > 0 and run[i - 1]["t_ms"] >= want0:
        i -= 1
    j = peak
    while j + 1 < n and run[j + 1]["t_ms"] <= want1:
        j += 1

    # Chua du dai vi dinh nam sat mot bien -> noi ra phia con lai, toi da max_ms
    while (run[j]["t_ms"] - run[i]["t_ms"]) < min_ms:
        if j + 1 < n and (run[j + 1]["t_ms"] - run[i]["t_ms"]) <= max_ms:
            j += 1
        elif i > 0 and (run[j]["t_ms"] - run[i - 1]["t_ms"]) <= max_ms:
            i -= 1
        else:
            break                       # het doan hoac cham tran, chiu vay
    return (i, j) if j > i else None


def score_window(win, sample_ms, target_ms):
    """Diem cua mot cua so, da tinh do rung va do day frame."""
    if len(win) < 2:
        return 0.0, {}
    dur = win[-1]["t_ms"] - win[0]["t_ms"]
    if dur <= 0:
        return 0.0, {}
    base = sum(score_frame(f) for f in win) / len(win)
    # Thieu frame giua doan = nguoi do bien mat mot luc. Ha diem theo ty le.
    want = max(1.0, dur / max(1.0, sample_ms) + 1.0)
    cover = min(1.0, len(win) / want)
    motion = motion_of(win)
    # Uu tien do dai gan target, nhung khong cung nhac: mot doan 4s tot han han
    # thi van thang mot doan 2.6s tam thuong.
    fit = 1.0 - 0.25 * min(1.0, abs(dur - target_ms) / max(1.0, target_ms))
    score = base * cover * fit / (1.0 + 0.8 * motion)
    return score, {"dur_ms": int(dur), "n": len(win), "cover": cover,
                   "motion": motion, "base": base}


def best_windows(samples, sample_ms, target_ms=2600, min_ms=1200, max_ms=4500,
                 gap_ms=800, top=3):
    """Cac doan tot nhat, khong chong nhau, xep theo diem giam dan.

    Moi doan sinh ra tu MOT khoanh khac, khong phai tu viec ro tim moi cach cat
    co the. Ngoai chuyen dung ban chat hon, no con re hon han: mot doan 30 giay
    lay mau 2 fps co 60 frame, ro het cap (i,j) la 1800 cua so; con o day la 4
    dinh, moi dinh mot cua so.
    """
    cands = []
    for run in runs(samples, gap_ms):
        for p in peaks(run, min_gap_ms=max(min_ms, target_ms), top=top + 1):
            span = window_around(run, p, target_ms, min_ms, max_ms)
            if span is None:
                continue
            i, j = span
            win = run[i:j + 1]
            sc, meta = score_window(win, sample_ms, target_ms)
            if sc > 0:
                cands.append((sc, run[i]["t_ms"], run[j]["t_ms"],
                              run[p]["t_ms"], win, meta))

    # Doan qua ngan de dat min_ms: van lay ca doan neu no du dai toi thieu 0.6s.
    # Video 1 giay co nguoi do van dang hon la bo han.
    if not cands:
        for run in runs(samples, gap_ms):
            if len(run) < 2:
                continue
            if run[-1]["t_ms"] - run[0]["t_ms"] < 600:
                continue
            sc, meta = score_window(run, sample_ms, target_ms)
            if sc > 0:
                pk = peaks(run, 0, top=1)
                cands.append((sc, run[0]["t_ms"], run[-1]["t_ms"],
                              run[pk[0]]["t_ms"] if pk else run[0]["t_ms"],
                              run, meta))

    cands.sort(key=lambda x: -x[0])
    picked = []
    for item in cands:
        t0, t1 = item[1], item[2]
        if any(not (t1 <= p[1] or t0 >= p[2]) for p in picked):
            continue                        # chong len doan da chon
        picked.append(item)
        if len(picked) >= top:
            break
    return [{"score": sc, "t_start_ms": t0, "t_end_ms": t1, "t_peak_ms": tp,
             "frames": win, **meta}
            for sc, t0, t1, tp, win, meta in picked]


def summarize(win):
    """Chi so trung binh cua mot doan, de luu vao fp_vclip va de UI hien."""
    n = max(1, len(win))

    def avg(key, default=None):
        vals = [float(f[key]) for f in win if f.get(key) is not None]
        return sum(vals) / len(vals) if vals else default

    return {
        "sim": avg("sim"),
        "face_ratio": avg("face_ratio", 0.0),
        "sharp": avg("sharp", 0.0),
        "bright": avg("bright", 0.0),
        "frontality": avg("frontality", 0.0),
        "motion": motion_of(win),
        "n_frame": n,
    }


def track_blob(win):
    """float32[n][11]: t_giay roi 5 cap (x,y) chuan hoa 0..1.

    Buoc dung video can biet khuon mat o dau tai TUNG thoi diem de neo, chu khong
    chi o mot moc. Nhung no cung khong can join lai fp_vface: mot doan 3 giay lay
    mau 2 fps chi co 6-7 moc, nhet vao mot blob 300 byte la xong, va noi suy
    tuyen tinh giua hai moc la du muot.
    """
    import numpy as np
    rows = []
    for f in win:
        k = f.get("kps01")
        if k is None:
            continue
        rows.append([f["t_ms"] / 1000.0] + [float(x) for x in
                                           np.asarray(k, np.float32).reshape(-1)])
    if not rows:
        return None
    return np.asarray(rows, np.float32).tobytes()
