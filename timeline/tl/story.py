"""Bien mot dong anh phang thanh MOT CAU CHUYEN co chuong, co nhip, co diem nhan.

Van de cua cach cu: rai deu 1 anh moi 30 ngay roi cho moi anh dung 1/6 giay.
Ket qua la mot bang anh chay tu dau den cuoi, moi frame quan trong nhu nhau, mat
doi lien tuc — xem duoc 10 giay la met. Khong co cho nao de mat nghi.

Cach nay, giong huong Google Photos Memories di:

  chuong   Thoi gian chia thanh cac chuong (nam, nua nam, thang...). Moi chuong
           la mot doan cua cau chuyen, mo dau bang nhan thoi gian.
  diem nhan Moi chuong co MOT anh chu dao (diem cao nhat) duoc giu lau hon han
           cac anh phu. Day la thu tao ra nhip.
  do dai   TU SUY tu du lieu. Nguoi dung chi noi "dung video cua ong A", khong
           noi "dung video 60 giay" — do dai la ket qua, khong phai yeu cau.
           Chuong nao nhieu anh thi day hon, hanh trinh dai thi nhieu chuong hon,
           va tong bi chan trong MIN_SECONDS..MAX_SECONDS de khong bao gio ra
           mot video 20 phut. Che do chuyen gia van dat tay target_seconds duoc.
  chuyen canh Cac anh chong mo len nhau thay vi cat cung, va zoom rat cham quanh
           diem neo mat — mat van dung mot cho, chi boi canh "tho".

Module nay thuan tinh toan: khong doc db, khong doc anh, khong goi ffmpeg. Nho
vay test duoc tren may khong co Immich.
"""
from datetime import date, datetime, timezone

# Nhip ke. hero = giay cho anh chu dao, beat = anh phu, xfade = do dai chong mo.
# Do khong phai bam tu khong khi: 0.9-1.0s la muc nguoi xem kip nhan ra boi canh
# mot buc anh la, con 1.7s du de dung lai ngam mot khuon mat.
PACE = {
    "slow":   {"hero": 2.4, "beat": 1.5, "xfade": 0.75},
    "normal": {"hero": 1.7, "beat": 1.0, "xfade": 0.50},
    "quick":  {"hero": 1.2, "beat": 0.7, "xfade": 0.34},
    "snap":   {"hero": 0.8, "beat": 0.45, "xfade": 0.20},
}

# Do manh cua zoom Ken Burns. Zoom quanh DIEM GIUA HAI MAT nen khuon mat khong
# he xe dich — chi khung anh rong/hep dan. Neo van la neo.
MOTION = {"none": 0.0, "subtle": 0.035, "normal": 0.07, "strong": 0.12}

# Tu tho den min. 'auto' se chon mot muc trong danh sach nay.
GRAIN = ("years2", "year", "half", "quarter", "month")

# Do dai mot chuong theo tung muc, de biet mot khoang thoi gian co bao nhieu
# chuong CO THE co — tu do biet chuong co lien mach hay chi la vai moc roi rac.
PERIOD_DAYS = {"years2": 730.5, "year": 365.25, "half": 182.6,
               "quarter": 91.3, "month": 30.44}

# Ty le chuong co anh / chuong co the co. Duoi muc nay thi cac nhan chuong doc
# ra nhu ngay thang roi rac ("Tháng 3 2019, Tháng 7 2019, Tháng 11 2020") chu
# khong ra mot tien trinh.
MIN_DENSITY = 0.4
GRAIN_LABEL = {
    "years2": "two years per chapter",
    "year": "one year per chapter",
    "half": "half a year per chapter",
    "quarter": "one quarter per chapter",
    "month": "one month per chapter",
}

# So chuong hop ly cho mot video ke chuyen. It hon 3 thi khong thanh chuong,
# nhieu hon 18 thi nhan chuong nhay lien tuc, thanh tieng on.
MIN_CHAPTERS, MAX_CHAPTERS = 3, 18

# Chan tren/duoi khi tu suy do dai. Tren 2 phut rui thi khong ai xem het; duoi
# 16 giay thi chua kip thanh cau chuyen (nhung nguoi co 5 anh thi dung chiu).
MIN_SECONDS, MAX_SECONDS = 16.0, 150.0

DEFAULTS = {
    "mode": "story",            # 'story' | 'even' (rai deu nhu ban cu)
    "target_seconds": None,     # None = TU SUY tu du lieu. So la dat tay.
    "pace": "normal",
    "chapter_by": "auto",
    "max_per_chapter": 6,       # chan mot chuyen du lich chiem het video
    "reserve_seconds": 3.5,     # cho the tieu de + mo/dong man
}


# ------------------------------------------------------------------ thoi gian
def dt(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def ts(v):
    return dt(v).timestamp()


def iso(v):
    return dt(v).isoformat()


# -------------------------------------------------------------------- chuong
_MONTH = ("", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def chapter_key(v, grain):
    """(key sap xep duoc, nhan hien thi). key phai tang theo thoi gian."""
    d = dt(v)
    y, m = d.year, d.month
    if grain == "month":
        return (y, m), f"{_MONTH[m]} {y}"
    if grain == "quarter":
        q = (m - 1) // 3 + 1
        return (y, q), f"{y} · Q{q}"
    if grain == "half":
        h = 0 if m <= 6 else 1
        return (y, h), f"{y} · {'first' if h == 0 else 'second'} half"
    if grain == "years2":
        y0 = y - (y % 2)
        return (y0, 0), f"{y0}–{y0 + 1}"
    return (y, 0), str(y)


def group(rows, grain):
    """Gom anh thanh chuong theo thu tu thoi gian."""
    out, index = [], {}
    for r in sorted(rows, key=lambda x: ts(x["taken_at"])):
        k, label = chapter_key(r["taken_at"], grain)
        if k not in index:
            index[k] = len(out)
            out.append({"key": k, "label": label, "rows": []})
        out[index[k]]["rows"].append(r)
    return out


def choose_grain(rows, budget_s, pace):
    """Chon do tho cua chuong: MIN NHAT ma van vua ngan sach VA con lien mach.

    Ba rang buoc keo nhau:

    1. Vua ngan sach. Moi chuong toi thieu mot anh chu dao, nen chia qua min la
       vuot thoi luong va khong con cho cho anh phu nao.
    2. Cang min cang tot trong pham vi con lai — khong phai cang tho. Mot hanh
       trinh 13 nam gop thanh 7 chuong hai-nam thi co nam bi bo qua han, trong
       khi 13 chuong mot-nam van vua 60 giay.
    3. Chuong phai LIEN MACH. Day la cho de sai nhat: 40 anh rai trong 4 nam thi
       chia theo thang chi ra 13 chuong (48 thang, 35 thang rong) — nhan chuong
       doc ra nhu ngay thang roi rac chu khong ra mot tien trinh. Chia theo quy
       thi 12/16 quy co anh, doc lien mach hon nhieu.
    """
    if not rows:
        return "year"
    hero = PACE[pace]["hero"]
    room = max(1, int(budget_s // max(0.1, hero)))     # so chuong toi da vua budget
    cap = max(MIN_CHAPTERS, min(MAX_CHAPTERS, room))
    t = [ts(r["taken_at"]) for r in rows]
    span_days = max(1.0, (max(t) - min(t)) / 86400.0)

    fits = []
    for g in GRAIN:
        n = len({chapter_key(r["taken_at"], g)[0] for r in rows})
        if n > cap:
            break                       # min hon nua chi cang nhieu chuong
        periods = max(1.0, span_days / PERIOD_DAYS[g])
        fits.append((g, n, n / periods))
    if not fits:
        return GRAIN[0]                 # ngay muc tho nhat da vuot cap: chiu vay

    good = [g for g, n, d in fits if d >= MIN_DENSITY and n >= MIN_CHAPTERS]
    if good:
        return good[-1]                 # min nhat trong so cac muc lien mach
    enough = [g for g, n, _ in fits if n >= MIN_CHAPTERS]
    return enough[-1] if enough else fits[-1][0]


# ------------------------------------------------------------------ ngan sach
def plan(cfg=None):
    """Gop cau hinh nhip voi mac dinh, tra ve cac hang so dung cho ca hai phia.

    Buoc chon anh va buoc render PHAI dung cung mot ban plan, neu khong thi so
    anh va thoi luong lech nhau — chon 80 anh roi render ra video 200 giay.
    """
    c = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if k in DEFAULTS and v is not None:
            c[k] = v
    c["pace"] = c["pace"] if c["pace"] in PACE else "normal"
    c["chapter_by"] = c["chapter_by"] if c["chapter_by"] in GRAIN else "auto"
    c["max_per_chapter"] = max(1, min(30, int(c["max_per_chapter"])))
    c["reserve_seconds"] = max(0.0, min(15.0, float(c["reserve_seconds"])))
    p = PACE[c["pace"]]
    c["hold_hero"], c["hold_beat"], c["xfade"] = p["hero"], p["beat"], p["xfade"]

    # target_seconds None / 0 / 'auto' deu la "tu suy". Phan biet ro voi mot con
    # so that, vi hai duong phan bo ngan sach khac han nhau.
    t = c["target_seconds"]
    if t in (None, 0, "", "auto"):
        c["target_seconds"] = None
        c["auto_budget"] = True
        c["budget"] = MAX_SECONDS
    else:
        c["target_seconds"] = max(10.0, min(600.0, float(t)))
        c["auto_budget"] = False
        c["budget"] = max(2.0, c["target_seconds"] - c["reserve_seconds"])
    return c


def allocate(chapters, p, hard_cap=10_000):
    """Chia ngan sach thanh so anh cho tung chuong.

    Hai nguyen tac:
      1. Moi chuong duoc it nhat mot anh. Phu kin thoi gian quan trong hon do
         day: mat mot nam khoi video la mat mot doan cau chuyen.
      2. Phan con lai chia theo sqrt(so anh dat) / so anh da co. sqrt de mot
         chuyen di 300 anh khong an het, chia cho so da co de khong don cuc.
    """
    if not chapters:
        return []
    n = len(chapters)
    avail = [len(ch["rows"]) for ch in chapters]
    alloc = [1] * n
    left = p["budget"] - n * p["hold_hero"]

    # Neu ngay ca mot anh moi chuong da vuot ngan sach thi cu giu — phu kin
    # thoi gian dang gia hon la dung dung thoi luong. Nguoi dung se thay thoi
    # luong that trong storyboard truoc khi render.
    while left >= p["hold_beat"]:
        best, best_pri = -1, 0.0
        for i in range(n):
            if alloc[i] >= min(avail[i], p["max_per_chapter"]):
                continue
            if sum(alloc) >= hard_cap:
                break
            pri = (avail[i] ** 0.5) / alloc[i]
            if pri > best_pri:
                best, best_pri = i, pri
        if best < 0:
            break
        alloc[best] += 1
        left -= p["hold_beat"]
    return alloc


def cost(alloc, p):
    """Thoi luong cua mot cach phan bo: moi chuong mot diem nhan + n anh phu."""
    return sum(p["hold_hero"] + (k - 1) * p["hold_beat"] for k in alloc)


def row_seconds(r, p):
    """Thoi luong that cua mot shot.

    Doan video mang theo do dai cua chinh no — khong the ep mot doan 3,2 giay
    thanh 1 giay ma khong cat mat noi dung. Vi vay sau khi chon xong phai tinh
    lai tong that va tia neu vuot, chu khong the tin vao cost() thuan anh tinh.
    """
    if r.get("kind") == "clip":
        return max(0.4, float(r.get("dur_s") or 0.0))
    return p["hold_hero"] if r.get("hero") else p["hold_beat"]


def trim_to(kept, p, ceiling):
    """Bo dan anh phu diem thap nhat cho den khi vua tran. Giu moi chuong >= 1.

    Khong bao gio bo anh diem nhan cua mot chuong: mat no la mat luon chuong do
    khoi cau chuyen, trong khi bo mot anh phu chi lam chuong do mong hon.
    """
    total = sum(row_seconds(r, p) for r in kept)
    if total <= ceiling or len(kept) <= 1:
        return kept, [], total
    per_ch = {}
    for r in kept:
        per_ch[r.get("bucket")] = per_ch.get(r.get("bucket"), 0) + 1
    order = sorted((r for r in kept if not r.get("hero")),
                   key=lambda r: float(r.get("score") or 0.0))
    drop = set()
    for r in order:
        if total <= ceiling:
            break
        b = r.get("bucket")
        if per_ch.get(b, 0) <= 1:
            continue
        drop.add(id(r))
        per_ch[b] -= 1
        total -= row_seconds(r, p)
    if not drop:
        return kept, [], total
    keep = [r for r in kept if id(r) not in drop]
    return keep, [r for r in kept if id(r) in drop], total


def allocate_auto(chapters, p, hard_cap=10_000):
    """Do dai SUY RA TU DU LIEU, khong tu con so nguoi dung nhap.

    Moi chuong tu quyet dinh do day cua no theo so anh dat nguong minh co, tang
    theo log2: 1 anh -> 1, 3 anh -> 2, 7 -> 3, 15 -> 4, 31 -> 5. Log vi do day
    cua ky uc khong ty le thuan voi so anh chup duoc: mot chuyen di 300 anh khong
    dang gap 100 lan mot buoi chieu 3 anh, no chi dang gap vai lan.

    Cong lai duoc bao nhieu thi video dai bao nhieu, roi chan tren MAX_SECONDS.
    """
    from math import log2
    alloc, avail = [], []
    for ch in chapters:
        n = len(ch["rows"])
        extra = max(0, int(round(log2(1 + n))) - 1)
        alloc.append(1 + max(0, min(extra, p["max_per_chapter"] - 1, n - 1)))
        avail.append(n)

    # Vuot tran thi bot dan tu chuong dang duoc nhieu nhat. Ties: bot chuong it
    # anh nhat truoc, vi no it co so nhat de duoc day.
    while cost(alloc, p) > MAX_SECONDS or sum(alloc) > hard_cap:
        i = max(range(len(alloc)), key=lambda j: (alloc[j], -avail[j]))
        if alloc[i] <= 1:
            break
        alloc[i] -= 1
    return alloc


# Doan video chi duoc "gianh cho" mot suat neu diem cua no khong qua kem so voi
# buc anh bi thay. 0.75 nghia la: chiu mat 25% diem chat luong de doi lay mot
# doan dong, nhung khong doi mot buc anh xuat sac lay mot doan tam thuong.
CLIP_TRADE = 0.75


def pick(rows, k):
    """Chon k anh trong mot chuong: rai deu theo thoi gian, moi o lay anh tot nhat.

    Khong lay thang k anh diem cao nhat: chung hay nam cung mot buoi chup, ra
    ba anh gan nhu trung nhau. Chia khoang thoi gian cua chuong thanh k o roi
    moi o lay mot anh — chuong tu no cung co dien bien.

    Sau do GIANH MOT SUAT cho doan video neu chuong co doan ma chua duoc chon.
    Khong the de viec nay cho diem so: mot chuong 25 anh thi anh cao nhat gan nhu
    luon thang mot doan video trung binh, va the la ca tinh nang video khong bao
    gio xuat hien. Mot doan dong 3 giay dang gia hon mot buc anh dep hon no mot
    chut — do la ca ly do cat doan video ra.
    """
    rows = sorted(rows, key=lambda r: ts(r["taken_at"]))
    if k >= len(rows):
        return list(rows)
    t0, t1 = ts(rows[0]["taken_at"]), ts(rows[-1]["taken_at"])
    span = t1 - t0
    best = {}
    if span > 0:
        for r in rows:
            i = min(k - 1, int((ts(r["taken_at"]) - t0) / span * k))
            cur = best.get(i)
            if cur is None or _score(r) > _score(cur):
                best[i] = r
    chosen = list(best.values())
    if len(chosen) < k:                 # o rong (anh don cuc) -> bu bang diem cao
        seen = {id(r) for r in chosen}
        for r in sorted(rows, key=lambda r: -_score(r)):
            if id(r) not in seen:
                chosen.append(r)
                if len(chosen) >= k:
                    break
    chosen = _ensure_clip(rows, chosen)
    return sorted(chosen, key=lambda r: ts(r["taken_at"]))


def _ensure_clip(rows, chosen):
    """Gianh mot suat cho doan video tot nhat cua chuong, neu chua co doan nao."""
    if any(r.get("kind") == "clip" for r in chosen):
        return chosen
    clips = [r for r in rows if r.get("kind") == "clip"]
    if not clips or not chosen:
        return chosen
    best_clip = max(clips, key=_score)
    victim = min(chosen, key=_score)
    if _score(best_clip) < CLIP_TRADE * _score(victim):
        return chosen                   # doan qua kem so voi anh bi thay
    return [r for r in chosen if r is not victim] + [best_clip]


def _score(r):
    return float(r.get("score") or 0.0)


def build(passed, cfg=None, hard_cap=10_000):
    """Tu danh sach anh DAT NGUONG -> (kept, dropped, meta).

    kept: moi dong duoc gan bucket (so chuong), label (nhan chuong), hero.
    """
    p = plan(cfg)
    if not passed:
        # Phai tra ve DU cac khoa nhu duong thanh cong: siet nguong den muc
        # khong con anh nao dat la mot truong hop that, va _story_info() doc
        # thang cac khoa nay -> thieu mot cai la 500 thay vi "0 anh duoc chon".
        grain = p["chapter_by"] if p["chapter_by"] != "auto" else "year"
        return [], [], {"plan": p, "chapters": [], "grain": grain,
                        "grain_label": GRAIN_LABEL.get(grain, grain),
                        "capped": False, "exhausted": False,
                        "auto": p["auto_budget"], "n_clip": 0,
                        "seconds": 0.0}

    grain = p["chapter_by"]
    if grain == "auto":
        grain = choose_grain(passed, p["budget"], p["pace"])
    chapters = group(passed, grain)
    alloc = (allocate_auto(chapters, p, hard_cap) if p["auto_budget"]
             else allocate(chapters, p, hard_cap))

    # Tang ngan sach ma video khong dai them thi do mot trong hai tran nay chan.
    # Noi ro ra, neu khong nguoi dung keo thanh truot len 240s va khong hieu vi
    # sao khong co gi thay doi.
    capped = all(alloc[i] >= p["max_per_chapter"] for i in range(len(chapters))) \
        if chapters else False
    exhausted = all(alloc[i] >= len(ch["rows"])
                    for i, ch in enumerate(chapters)) if chapters else False

    kept, dropped, summary = [], [], []
    for ci, (ch, k) in enumerate(zip(chapters, alloc)):
        chosen = pick(ch["rows"], k)
        ids = {id(r) for r in chosen}
        hero = max(chosen, key=_score) if chosen else None
        for r in chosen:
            r["bucket"] = ci
            r["label"] = ch["label"]
            r["hero"] = (r is hero)
            kept.append(r)
        for r in ch["rows"]:
            if id(r) not in ids:
                dropped.append(r)
        summary.append({"chapter": ci, "label": ch["label"],
                        "n_avail": len(ch["rows"]), "n_pick": len(chosen),
                        "from": iso(chosen[0]["taken_at"]) if chosen else None,
                        "to": iso(chosen[-1]["taken_at"]) if chosen else None})

    kept.sort(key=lambda r: ts(r["taken_at"]))

    # Doan video dai hon mot anh tinh nen tong that co the vuot du toan. Tia lai
    # o day, khong de buoc render tu y bo — nguoi dung phai thay dung nhung gi
    # se vao video ngay tu buoc chon anh.
    ceiling = (MAX_SECONDS if p["auto_budget"] else p["budget"])
    kept, over, secs = trim_to(kept, p, ceiling)
    for r in over:
        r["reason"] = f"over the {ceiling:.0f}s duration ceiling"
    dropped.extend(over)
    n_clip = sum(1 for r in kept if r.get("kind") == "clip")
    for ch in summary:
        ch["n_pick"] = sum(1 for r in kept if r.get("bucket") == ch["chapter"])

    return kept, dropped, {"plan": p, "grain": grain,
                           "grain_label": GRAIN_LABEL.get(grain, grain),
                           "capped": capped, "exhausted": exhausted,
                           "auto": p["auto_budget"], "n_clip": n_clip,
                           "seconds": round(secs + p["reserve_seconds"], 1),
                           "chapters": summary}


def estimate(n_hero, n_beat, p):
    """Thoi luong du kien, chua tinh the tieu de. Dung cho UI o buoc chon anh."""
    return n_hero * p["hold_hero"] + n_beat * p["hold_beat"]


# ---------------------------------------------------------------- storyboard
def snap_beats(shots, beats, fps, min_frames=2):
    """Ep bien giua cac shot roi dung vao phach nhac. Sua 'hold' tai cho.

    Tra ve so shot da bat duoc vao nhip.

    Y tuong: khong bo cau truc hero/beat da co, chi DOI DON VI. Do dai tu nhien
    cua mot shot (hold_hero / hold_beat / do dai doan video) tro thanh "muc nham",
    roi bien that duoc keo ve phach gan nhat. Nhac nhanh thi mot shot an it phach
    -> canh cat don dap; nhac cham thi nguoc lai. Chuong va anh chu dao van con
    nguyen, chi la moi cu cat roi dung tieng trong.

    Doan video duoc xu ly KHAC anh tinh: chi keo XUONG phach truoc do, khong keo
    len. Keo len nghia la doi frame ma doan khong co — _ClipSrc se giu frame cuoi
    va nguoi xem thay hinh dung lai giua mot canh dang chuyen dong.
    """
    if not beats or not shots or fps <= 0:
        return 0
    grid = sorted({int(round(float(t) * fps)) for t in beats
                   if t is not None and float(t) >= 0})
    grid = [g for g in grid if g > 0]
    if len(grid) < 2:
        return 0

    pos, gi, n_snap = 0, 0, 0
    for sh in shots:
        nat = int(sh["hold"])
        want = pos + nat
        cands = []
        j = gi
        while j < len(grid):
            if grid[j] - pos >= min_frames:
                cands.append(j)
                if grid[j] > want:
                    break
            j += 1
        if not cands:
            pos += nat                  # het luoi phach -> giu do dai tu nhien
            continue
        if sh.get("kind") == "clip":
            below = [c for c in cands if grid[c] <= want]
            pick = below[-1] if below else cands[0]
        else:
            pick = min(cands, key=lambda c: abs(grid[c] - want))
        sh["hold"] = grid[pick] - pos
        sh["beat"] = True
        pos = grid[pick]
        gi = pick + 1
        n_snap += 1
    return n_snap


def storyboard(frames, o, beats=None):
    """Danh sach shot kem so frame chinh xac, cho buoc render.

    Mot 'shot' = mot buc anh nam tren man hinh trong hold frame, roi chong mo
    xfade frame sang shot sau. Tong so frame video = sum(hold) — chuyen canh
    khong keo dai video vi no LAY CHONG len phan cuoi cua shot truoc.

        shot i chiem  [start_i, start_i + hold_i + xout_i)
        start_(i+1) = start_i + hold_i          <- chong nhau dung xout_i frame

    beats: moc phach cua ban nhac (giay). Co thi bien shot bi keo ve phach gan
    nhat — xem snap_beats(). None thi nhip lay tu bang PACE nhu cu.
    """
    fps = max(6, min(60, int(o.get("out_fps") or 24)))
    p = plan(o)
    # xfade=0 la yeu cau cat cung, khac han voi 'khong dat' -> phai phan biet
    xf = o.get("xfade")
    xfade_s = float(xf) if xf is not None else p["xfade"]
    amp = MOTION.get(o.get("motion", "subtle"), MOTION["subtle"])
    arc = bool(o.get("arc", True))

    f_hero = max(2, round(p["hold_hero"] * fps))
    f_beat = max(2, round(p["hold_beat"] * fps))
    f_x = max(0, round(xfade_s * fps))
    f_title = max(0, round(float(o.get("title_seconds") or 0) * fps)) \
        if o.get("title") else 0
    f_out = max(0, round(float(o.get("outro_s") or 0) * fps))
    f_in = max(0, round(float(o.get("intro_s") or 0) * fps))

    shots, n = [], len(frames)
    last_ch = None
    n_ch = len({r.get("bucket") for r in frames})
    for i, r in enumerate(frames):
        hero = bool(r.get("hero"))
        is_clip = (r.get("kind") == "clip")
        # Doan video mang do dai cua chinh no: khong ep vao khuon hero/beat, va
        # khong zoom Ken Burns vi no da tu chuyen dong roi.
        hold = (max(2, round(row_seconds(r, p) * fps)) if is_clip
                else (f_hero if hero else f_beat))
        if arc and not is_clip:
            # mo dau va ket thuc cham hon mot chut: cau chuyen can cho de vao
            # va cho de dung lai. 12% la du de cam nhan, khong den muc lech nhip.
            ci = r.get("bucket") or 0
            if ci == 0 or ci >= n_ch - 1:
                hold = int(round(hold * 1.12))
        first_of_ch = r.get("bucket") != last_ch
        last_ch = r.get("bucket")
        if i == 0:
            if is_clip:
                # Doan video mo dau: KHONG keo dai them cho the tieu de, neu
                # khong nguoi xem nhin mot frame dung hinh 2,4 giay roi clip moi
                # chay. Cho tieu de hien LEN TREN doan dang chay.
                f_title = min(f_title, hold)
            else:
                hold += f_title
        if i == n - 1:
            # Ket thuc thi nguoc lai: giu them roi mo dan ve den. Voi doan video
            # la mot frame dung hinh o cuoi — cach dong man thong thuong, va
            # khong an mat giay nao cua noi dung.
            hold += f_out
        # xen ke huong zoom cho khoi don dieu; anh chu dao zoom manh hon chut
        k = 0.0 if is_clip else amp * (1.35 if hero else 1.0)
        z0, z1 = (1.0, 1.0 + k) if i % 2 == 0 else (1.0 + k, 1.0)
        shots.append({
            "asset_id": str(r["asset_id"]), "fidx": int(r["fidx"]),
            "kind": "clip" if is_clip else "image",
            "preview_path": r.get("preview_path"), "kps": r.get("kps"),
            "kps2": r.get("kps2"), "taken_at": r.get("taken_at"),
            "video_path": r.get("video_path"), "track": r.get("track"),
            "t_start_ms": r.get("t_start_ms"), "t_end_ms": r.get("t_end_ms"),
            # Do dai CA FILE goc, khong phai cua doan: buoc ghep tieng keo tieng
            # dai hon phan hinh (L-cut) nen phai biet cho nao la het file.
            "src_dur_ms": r.get("dur_ms"),
            "t_peak_ms": r.get("t_peak_ms"), "src_dur_ms": r.get("dur_ms"),
            "chapter": r.get("bucket") or 0, "label": r.get("label") or "",
            "hero": hero, "first_of_chapter": first_of_ch,
            "hold": int(hold), "zoom_from": z0, "zoom_to": z1,
        })

    # Bat vao nhip TRUOC khi tinh xfade va start: hold doi thi ca hai phai tinh
    # lai theo, khong thi bien shot lech dung bang phan da sua.
    n_beat_snap = snap_beats(shots, beats, fps) if beats else 0

    # chuyen canh: chi chong mo giua hai shot khi ca hai du dai
    for i, sh in enumerate(shots):
        nxt = shots[i + 1] if i + 1 < len(shots) else None
        sh["xout"] = min(f_x, sh["hold"], nxt["hold"]) if nxt else 0
    start = 0
    for i, sh in enumerate(shots):
        sh["start"] = start
        sh["xin"] = shots[i - 1]["xout"] if i else 0
        sh["vis"] = sh["hold"] + sh["xout"]
        start += sh["hold"]

    total = start
    ch_seen, chapters = {}, []
    for sh in shots:
        c = ch_seen.setdefault(sh["chapter"], {"chapter": sh["chapter"],
                                              "label": sh["label"], "n": 0,
                                              "frames": 0})
        c["n"] += 1
        c["frames"] += sh["hold"]
    for c in sorted(ch_seen.values(), key=lambda x: x["chapter"]):
        c["seconds"] = round(c["frames"] / fps, 2)
        chapters.append(c)

    return {
        "shots": shots, "n_shots": len(shots), "n_frames": total, "fps": fps,
        "duration_s": round(total / fps, 2),
        "n_hero": sum(1 for s in shots if s["hero"]),
        "fade_in": min(f_in, total), "fade_out": min(f_out, total),
        "f_title": f_title, "chapters": chapters,
        "n_clip": sum(1 for s in shots if s["kind"] == "clip"),
        "pace": p["pace"], "target_seconds": p["target_seconds"],
        "n_beat_snap": n_beat_snap,
    }


def smoothstep(x):
    """Lam mem hai dau, de zoom khong giat luc vao va luc ra khoi shot."""
    x = max(0.0, min(1.0, float(x)))
    return x * x * (3.0 - 2.0 * x)


def describe():
    """Cho /api/defaults, de UI khong phai hardcode lai bang nhip."""
    return {"pace": {k: dict(v) for k, v in PACE.items()},
            "motion": dict(MOTION), "grain": list(GRAIN),
            "grain_label": dict(GRAIN_LABEL), "defaults": dict(DEFAULTS),
            "min_chapters": MIN_CHAPTERS, "max_chapters": MAX_CHAPTERS,
            "min_seconds": MIN_SECONDS, "max_seconds": MAX_SECONDS}
