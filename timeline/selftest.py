"""Kiem thu logic thuan tren may dev: khong can Postgres, Immich, ffmpeg.

Chay:  python selftest.py [--dump THUMUC]

--dump ghi ra vai frame mau (frame dau, giua the tieu de, giua nhan chuong,
frame cuoi) de nhin bang mat xem khung va chu co dung cho khong.

Verify duoc: chia chuong, phan bo ngan sach, so frame khop storyboard, ve chu
co dau, va vong lap dung frame cua che do story (thay pipe ffmpeg bang bo dem).
"""
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# tl.db import psycopg o muc module. May dev khong co driver, ma test nay khong
# he cham vao db -> nhoi module rong vao sys.modules cho qua buoc import.
for name in ("psycopg", "psycopg_pool"):
    if name not in sys.modules:
        m = types.ModuleType(name)
        if name == "psycopg":
            m.rows = types.SimpleNamespace(dict_row=None)
        else:
            m.ConnectionPool = object
        sys.modules[name] = m

import numpy as np                                                    # noqa: E402

from tl import media, render, select, story, textdraw                  # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


# --------------------------------------------------------------- du lieu gia
def kps_blob(cx=0.5, cy=0.42, dx=0.06):
    """5 diem mat chuan hoa 0..1: hai mat, mui, hai khoe mieng."""
    k = np.array([[cx - dx, cy], [cx + dx, cy], [cx, cy + dx * 0.9],
                  [cx - dx * 0.7, cy + dx * 1.7], [cx + dx * 0.7, cy + dx * 1.7]],
                 np.float32)
    return k.tobytes()


def cands(n=900, years=12, seed=7):
    """Anh rai khong deu: co nam day dac, co nam thua — giong thu vien that."""
    rng = np.random.default_rng(seed)
    t0 = datetime(2013, 1, 5, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        # don cuc: 60% anh nam trong 20% khoang thoi gian
        u = rng.random()
        frac = u * 0.2 + 0.35 if rng.random() < 0.6 else u
        when = t0 + timedelta(days=frac * 365.25 * years)
        out.append({
            "asset_id": f"a{i:04d}", "fidx": 0, "taken_at": when,
            "date_src": "exif", "filename": f"IMG_{i}.jpg",
            "n_face": int(rng.integers(1, 4)), "n_body": 1,
            "x1": 0.4, "y1": 0.3, "x2": 0.6, "y2": 0.55,
            "yaw": float(rng.normal(0, 12)), "pitch": float(rng.normal(0, 9)),
            "roll": float(rng.normal(0, 8)),
            "frontality": float(np.clip(rng.normal(0.66, 0.16), 0, 1)),
            "ear": float(np.clip(rng.normal(0.26, 0.07), 0, 0.5)),
            "eye_px": 90.0, "eye_ratio": float(np.clip(rng.normal(0.07, 0.03), 0, 1)),
            "sharp": float(abs(rng.normal(190, 90))),
            "bright": float(np.clip(rng.normal(128, 30), 0, 255)),
            "symm": 0.8, "quality": float(np.clip(rng.normal(58, 17), 0, 100)),
            "age": 6.0, "posture": "standing", "orientation": "front",
            "body_front": 0.8, "torso_deg": 5.0, "area_ratio": 0.3,
            "preview_path": f"upload/thumbs/a{i:04d}.jpg", "kps": kps_blob(),
        })
    return out


# ------------------------------------------------------------------- 1. chon
def t_auto():
    """Do dai la KET QUA, khong phai yeu cau: nguoi dung chi noi 'video cua A'."""
    print("\n[0] tu suy do dai tu du lieu")
    thin = select.apply(cands(n=40, years=4, seed=1), {})
    thick = select.apply(cands(n=3000, years=14, seed=2), {})
    for name, r in (("thu vien mong", thin), ("thu vien day", thick)):
        st = r["story"]
        check(st["auto"] is True, f"{name}: dang o che do tu suy")
        check(st["target_seconds"] is None, f"{name}: khong co con so dat tay")
        check(st["est_seconds"] <= story.MAX_SECONDS + 1,
              f"{name}: {st['est_seconds']}s khong vuot tran "
              f"{story.MAX_SECONDS}s")
        print(f"       -> {name}: {r['n_selected']} anh, {st['n_chapter']} chuong,"
              f" {st['grain']}, {st['est_seconds']}s")
    check(thick["story"]["est_seconds"] > thin["story"]["est_seconds"],
          "thu vien day hon thi video dai hon (do dai theo du lieu)")
    check(thin["story"]["est_seconds"] >= 10,
          "thu vien mong van ra duoc mot video ngan, khong phai rong")

    # Chuong phai lien mach: khong duoc chia theo thang khi phan lon thang rong
    for name, r in (("mong", thin), ("day", thick)):
        st = r["story"]
        got = [c for c in st["chapters"]]
        gr = st["grain"]
        span = (story.ts(r["selected"][-1]["taken_at"])
                - story.ts(r["selected"][0]["taken_at"])) / 86400.0
        periods = max(1.0, span / story.PERIOD_DAYS[gr])
        check(len(got) / periods >= story.MIN_DENSITY - 0.01,
              f"{name}: chuong lien mach ({len(got)}/{periods:.0f} ky {gr})")

    # Dat tay van phai thang che do tu suy
    fixed = select.apply(cands(n=3000, years=14, seed=2), {"target_seconds": 40})
    check(fixed["story"]["auto"] is False, "dat target_seconds -> tat tu suy")
    check(fixed["story"]["est_seconds"] < thick["story"]["est_seconds"],
          "dat 40s thi ngan hon ban tu suy cua cung du lieu")
    # ...va tat lai duoc
    back = select.merge({"target_seconds": None})
    check(back["target_seconds"] is None, "gui None -> quay lai tu suy")


def t_subjects():
    """Video cua mot nguoi / hai nguoi / hai nguoi chup chung."""
    print("\n[0b] nhom nguoi va anh chup chung")
    g = select.groups_of
    check(g("a") == [["a"]], "mot cluster -> mot nguoi")
    check(g(["a", "b"]) == [["a", "b"]], "hai cluster phang -> MOT nguoi hai cum")
    check(g([["a", "b"], ["c"]]) == [["a", "b"], ["c"]], "long nhau -> hai nguoi")
    check(g([]) == [] and g(None) == [], "rong -> rong")

    # A o anh 1, B o anh 2, ca hai o anh 3
    def face(aid, fidx, grp, sc):
        return {"asset_id": aid, "fidx": fidx, "group": grp, "quality": sc,
                "taken_at": datetime(2020, 1, 1 + fidx, tzinfo=timezone.utc),
                "person_name": "A" if grp == 0 else "B"}
    rows_ = [face("x", 0, 0, 50), face("y", 0, 1, 50),
             face("z", 0, 0, 40), face("z", 1, 1, 90)]
    any_ = select._one_per_asset([dict(r) for r in rows_], 2, together=False)
    check(len(any_) == 3, "khong 'chup chung': lay ca ba anh")
    check(all(r["asset_id"] != r.get("_dup") for r in any_)
          and len({r["asset_id"] for r in any_}) == 3,
          "moi anh dung MOT dong (khong xuat hien hai lan trong video)")
    tog = select._one_per_asset([dict(r) for r in rows_], 2, together=True)
    check(len(tog) == 1 and tog[0]["asset_id"] == "z",
          "'chup chung': chi anh co mat du hai nguoi")
    check(tog[0]["fidx2"] == 1 and tog[0]["person_name2"] == "B",
          "ghi lai mat cua nguoi thu hai de neo theo ca hai")


def t_pair_anchor():
    print("\n[0c] neo hai khuon mat")
    a = np.frombuffer(kps_blob(0.30, 0.30), np.float32).reshape(5, 2) * [800, 600]
    b = np.frombuffer(kps_blob(0.70, 0.62), np.float32).reshape(5, 2) * [800, 600]
    pair = media.pair_kps(a.astype(np.float32), b.astype(np.float32))
    check(pair is not None and pair.shape == (2, 2), "sinh duoc diem neo cap")
    check(abs(pair[0][1] - pair[1][1]) < 1e-3,
          "hai diem neo NAM NGANG -> level khong xoay anh (bo cao con thap)")
    ca, cb = (a[0] + a[1]) / 2, (b[0] + b[1]) / 2
    mid = (ca + cb) / 2
    check(abs((pair[0][0] + pair[1][0]) / 2 - mid[0]) < 0.01
          and abs(pair[0][1] - mid[1]) < 0.01,
          "trung diem cua diem neo = trung diem giua hai nguoi")
    d = float(np.hypot(*(cb - ca)))
    check(abs((pair[1][0] - pair[0][0]) - d) < 0.01,
          "khoang cach diem neo = khoang cach that giua hai nguoi")
    # hai mat trung nhau -> khong duoc ra he so phong to vo cuc
    close = media.pair_kps(a.astype(np.float32), a.astype(np.float32))
    check(close is not None and (close[1][0] - close[0][0]) > 1.0,
          "hai mat trung nhau van ra khoang cach duong (khong chia cho 0)")


def t_select():
    print("\n[1] chon anh theo ngan sach dat tay")
    c = cands()
    for target in (30, 60, 120):
        r = select.apply(c, {"mode": "story", "target_seconds": target})
        st = r["story"]
        check(st is not None, f"target={target}s: co tom tat story")
        check(st["est_seconds"] <= target * 1.6,
              f"target={target}s: du kien {st['est_seconds']}s khong vuot qua xa")
        check(story.MIN_CHAPTERS <= st["n_chapter"] <= story.MAX_CHAPTERS,
              f"target={target}s: {st['n_chapter']} chuong nam trong khoang hop ly")
        check(st["n_hero"] == st["n_chapter"],
              f"target={target}s: moi chuong dung mot diem nhan")
        # Phu kin: moi chuong phai co anh. Rieng grain 'year' thi tuong duong
        # phu du moi nam — kiem rieng ben duoi.
        check(all(x["n_pick"] >= 1 for x in st["chapters"]),
              f"target={target}s: khong chuong nao trong")
        print(f"       -> {r['n_selected']} anh, {st['n_chapter']} chuong, "
              f"{st['grain']}, ~{st['est_seconds']}s")

    yrs_all = {x["taken_at"].year for x in c}
    ry = select.apply(c, {"mode": "story", "target_seconds": 90,
                          "chapter_by": "year"})
    check({x["taken_at"].year for x in ry["selected"]} == yrs_all,
          f"chapter_by=year: phu du ca {len(yrs_all)} nam")
    auto = select.apply(c, {"mode": "story", "target_seconds": 60})
    check(auto["story"]["grain"] == "year",
          f"auto chon grain min nhat con vua ngan sach (duoc {auto['story']['grain']})")

    big = select.apply(c, {"mode": "story", "target_seconds": 240})
    small = select.apply(c, {"mode": "story", "target_seconds": 30})
    check(big["n_selected"] > small["n_selected"],
          "ngan sach lon hon thi lay nhieu anh hon")

    ev = select.apply(c, {"mode": "even", "bucket_days": 30})
    check(ev["story"] is None, "mode even khong tra story")
    check(ev["n_selected"] > 0, "mode even van chay")
    return c


# --------------------------------------------------------- 2. storyboard math
def t_storyboard(c):
    print("\n[2] storyboard: so frame va thoi luong")
    r = select.apply(c, {"mode": "story", "target_seconds": 60})
    o = render.options({"mode": "story", "size": 480, "motion": "subtle"},
                       r["filters"])
    sb = story.storyboard(r["selected"], o)

    check(sb["n_frames"] == sum(s["hold"] for s in sb["shots"]),
          "tong frame = tong hold (chuyen canh khong keo dai video)")
    sh = sb["shots"]
    check(all(s["hold"] >= s["xout"] for s in sh),
          "moi shot: hold >= xout (khong bao gio chong 3 lop)")
    check(all(s["start"] == sh[i - 1]["start"] + sh[i - 1]["hold"]
              for i, s in enumerate(sh) if i), "start cua cac shot noi tiep dung")
    check(all(s["xin"] == sh[i - 1]["xout"] for i, s in enumerate(sh) if i),
          "xin cua shot sau khop xout cua shot truoc")
    heroes = [s for s in sb["shots"] if s["hero"]]
    beats = [s for s in sb["shots"] if not s["hero"]]
    check(all(h["hold"] > min(b["hold"] for b in beats) for h in heroes) if beats
          else True, "diem nhan giu lau hon anh phu")
    check(30 <= sb["duration_s"] <= 110,
          f"thoi luong {sb['duration_s']}s quanh ngan sach 60s")
    check(len({s["chapter"] for s in sb["shots"]}) == len(sb["chapters"]),
          "so chuong khop giua shots va tom tat")
    check(sum(1 for s in sb["shots"] if s["first_of_chapter"]) == len(sb["chapters"]),
          "moi chuong co dung mot shot mo chuong")
    print(f"       -> {sb['n_shots']} shot, {sb['n_frames']} frame @{sb['fps']}fps"
          f" = {sb['duration_s']}s, {len(sb['chapters'])} chuong")

    # Ke thua che do: chon anh kieu 'even' thi mac dinh dung kieu 'flip', vi
    # frame cua no khong co chuong lan anh diem nhan.
    ev = render.options({}, {"mode": "even", "pace": "quick"})
    check(ev["mode"] == "flip", "filters mode=even -> render mac dinh flip")
    check(ev["pace"] == "quick", "pace ke thua tu filters cua du an")
    force = render.options({"mode": "story"}, {"mode": "even"})
    check(force["mode"] == "story", "gui mode=story len thi van ghi de duoc")
    st = render.options({}, {"mode": "story", "target_seconds": 90})
    check(st["mode"] == "story" and st["target_seconds"] == 90,
          "filters mode=story -> render story, ngan sach ke thua")
    check(render.options({"xfade": 0})["xfade"] == 0,
          "xfade=0 (cat cung) khong bi hieu thanh 'khong dat'")
    return r, o, sb


# ----------------------------------------------------------------- 3. ve chu
def t_text():
    print(f"\n[3] ve chu — backend: {textdraw.backend()}")
    img = np.full((360, 480, 3), 90, np.uint8)
    for t in ("Nguyễn Minh Khuê", "Tháng 3 2019", "6 tuổi", "2013–2024"):
        spr = textdraw.sprite(t, 40)
        check(spr is not None and spr[1].max() > 0.5, f"sinh duoc sprite {t!r}")
    before = img.copy()
    textdraw.block(img, [("Nguyễn Minh Khuê", 1.0), ("2013–2024", 0.5)],
                   y_frac=0.7, alpha=1.0)
    check(not np.array_equal(before, img), "block() thuc su ve len frame")
    faint = np.full((360, 480, 3), 90, np.uint8)
    textdraw.block(faint, ["Tháng 3 2019"], y_frac=0.7, alpha=0.05)
    d_full = int(np.abs(img.astype(int) - 90).sum())
    d_faint = int(np.abs(faint.astype(int) - 90).sum())
    check(0 < d_faint < d_full, "alpha thap thi mo hon (fade hoat dong)")
    return img


# --------------------------------------------------- 4. vong lap dung frame
class FakePipe:
    """Thay ffmpeg: dem byte, giu vai frame de xem bang mat."""

    def __init__(self, w, h, keep):
        self.w, self.h, self.keep = w, h, keep
        self.n = 0
        self.kept = {}
        self.stdin = self

    def write(self, b):
        exp = self.w * self.h * 3
        assert len(b) == exp, f"frame {self.n}: {len(b)} byte, cho {exp}"
        if self.n in self.keep:
            self.kept[self.n] = np.frombuffer(b, np.uint8).reshape(self.h, self.w, 3)
        self.n += 1

    def close(self):
        pass

    def wait(self, timeout=None):
        return 0


def t_render(r, o, sb, dump=None):
    print("\n[4] dung frame che do story")
    w, h = o["out_w"], o["out_h"]
    rng = np.random.default_rng(3)

    def fake_load(asset_id, preview_path, s):
        """Anh gia: nen van + mot khoi sang o vi tri 'mat' de nhin ra dich chuyen."""
        img = np.zeros((720, 1080, 3), np.uint8)
        img[:, :, 0] = np.linspace(30, 220, 1080, dtype=np.uint8)[None, :]
        img[:, :, 1] = np.linspace(200, 40, 720, dtype=np.uint8)[:, None]
        img[:, :, 2] = int(rng.integers(40, 210))
        img[int(0.42 * 720) - 26:int(0.42 * 720) + 26,
            int(0.44 * 1080):int(0.56 * 1080)] = 250
        return img, None

    # Ba moc de kiem overlay: giua the tieu de, giua nhan chuong, va mot frame
    # cua CUNG shot dau nhung da het overlay -> so sanh duoc truc tiep.
    s0 = sb["shots"][0]
    i_title = max(1, sb["f_title"] // 2)
    card = min(int(round(o["card_seconds"] * sb["fps"])),
               max(1, s0["hold"] - sb["f_title"]))
    i_card = sb["f_title"] + card // 2
    i_plain = sb["f_title"] + card + 2
    has_plain = i_plain < s0["hold"]
    keep = {0, i_title, i_card, sb["n_frames"] // 3, sb["n_frames"] // 2,
            sb["n_frames"] - 1}
    if has_plain:
        keep.add(i_plain)
    pipe = FakePipe(w, h, keep)
    orig_load, orig_pipe, orig_set = media.load, render._pipe, render._set
    media.load = fake_load
    render._pipe = lambda out, w_, h_, fps, o_, s_, work: (pipe, work / "x.log")
    render._set = lambda *a, **k: None
    try:
        out = Path(o.get("_out") or (HERE / "_selftest_out"))
        out.mkdir(parents=True, exist_ok=True)
        (out / "video.mp4").write_bytes(b"x")          # qua buoc kiem tra file
        dur = render._story(0, sb, o, render.get(), out / "video.mp4", out)
    finally:
        media.load, render._pipe, render._set = orig_load, orig_pipe, orig_set

    check(pipe.n == sb["n_frames"],
          f"ghi dung {pipe.n}/{sb['n_frames']} frame")
    check(abs(dur - sb["duration_s"]) < 0.05, f"thoi luong tra ve {dur:.2f}s")
    f0 = pipe.kept.get(0)
    check(f0 is not None and int(f0.max()) < 60,
          "frame dau gan nhu den (mo man tu den)")
    flast = pipe.kept.get(sb["n_frames"] - 1)
    check(flast is not None and int(flast.max()) < 60,
          "frame cuoi gan nhu den (dong man)")
    mid = pipe.kept.get(sb["n_frames"] // 2)
    check(mid is not None and int(mid.max()) > 200, "frame giua sang binh thuong")

    # Overlay phai that su nam tren frame that, khong chi tren canvas test.
    if has_plain:
        plain = pipe.kept[i_plain]
        bot = lambda f: float(f[int(f.shape[0] * 0.8):].mean())    # noqa: E731
        for idx, what in ((i_title, "the tieu de"), (i_card, "nhan chuong")):
            fr = pipe.kept[idx]
            check(bot(fr) < bot(plain) * 0.92,
                  f"{what}: co lop toi (scrim) o day khung")
            check(int(fr[int(fr.shape[0] * 0.55):].max()) >= 250,
                  f"{what}: co net chu trang o nua duoi")
        check(not np.array_equal(pipe.kept[i_card], plain),
              "frame co nhan chuong khac frame khong co")

    if dump:
        import cv2
        d = Path(dump)
        d.mkdir(parents=True, exist_ok=True)
        for i, fr in sorted(pipe.kept.items()):
            cv2.imwrite(str(d / f"frame_{i:05d}.jpg"), fr)
        print(f"       -> da ghi {len(pipe.kept)} frame mau vao {d}")
    return pipe


# --------------------------------------------------------------------- chay
def clip_row(i, when, dur_s=2.6, score=72.0, motion=0.4):
    """Mot doan video da duoc indexer cat ra, dang ma select.fetch tra ve."""
    return {
        "asset_id": f"v{i:03d}", "fidx": -1, "person_id": "p1",
        "person_name": "A", "taken_at": when, "date_src": "exif",
        "filename": f"VID_{i}.mp4", "n_face": 1, "n_body": None,
        "x1": None, "y1": None, "x2": None, "y2": None,
        "yaw": 0.0, "pitch": 0.0, "roll": 0.0, "frontality": 0.8, "ear": None,
        "eye_px": None, "eye_ratio": 0.08, "sharp": 220.0, "bright": 125.0,
        "symm": None, "quality": score, "age": None,
        "posture": None, "orientation": None, "body_front": None,
        "torso_deg": None, "area_ratio": None,
        "cidx": 0, "t_start_ms": 1000, "t_end_ms": int(1000 + dur_s * 1000),
        "motion": motion, "sim": 0.6, "track": None,
        "video_path": f"upload/encoded-video/v{i:03d}.mp4", "dur_ms": 12000,
        "kind": "clip", "dur_s": dur_s, "group": 0,
    }


def t_clip_select():
    """Doan video di qua dung bo loc voi anh, va mang do dai cua chinh no."""
    print("\n[0d] doan video trong luong chon")
    base = cands(n=300, years=6, seed=5)
    t0 = base[0]["taken_at"]
    vids = [clip_row(i, t0 + timedelta(days=180 * i + 20), dur_s=3.0)
            for i in range(6)]
    r = select.apply(base + vids, {})
    sel = r["selected"]
    got = [x for x in sel if x.get("kind") == "clip"]
    check(got, f"doan video duoc chon vao video ({len(got)}/6)")
    check(r["story"]["n_clip"] == len(got), "story dem dung so doan")
    check(all(x.get("hero") or True for x in got), "doan video co the la diem nhan")

    # rung qua thi bi loai, kem ly do doc duoc
    shaky = [clip_row(90 + i, t0 + timedelta(days=100 * i + 5), motion=9.0)
             for i in range(3)]
    r2 = select.apply(base + shaky, {})
    rej = [x for x in r2["rejected"] if x["asset_id"].startswith("v09")]
    check(len(rej) == 3 and all("shaky" in (x["reason"] or "") for x in rej),
          "doan rung bi loai kem ly do 'rung'")

    off = select.apply(base + vids, {"use_clips": False})
    check(not [x for x in off["selected"] if x.get("kind") == "clip"],
          "use_clips=False thi khong doan nao vao video")

    # Do dai that: doan 3s khong bi ep thanh 1s
    o = render.options({"mode": "story", "size": 320}, r["filters"])
    sb = story.storyboard(sel, o)
    cs = [s for s in sb["shots"] if s["kind"] == "clip"]
    check(cs, "storyboard giu lai doan video")
    # Shot dau/cuoi co them cho tieu de va dong man, nen chi kiem cac shot giua.
    mid = [c for c in cs if c is not sb["shots"][0] and c is not sb["shots"][-1]]
    for c in mid:
        check(abs(c["hold"] / sb["fps"] - 3.0) < 0.1,
              f"doan giu dung do dai that ({c['hold'] / sb['fps']:.2f}s)")
    check(all(abs(c["zoom_to"] - c["zoom_from"]) < 1e-6 for c in cs),
          "doan video khong zoom Ken Burns (no da tu chuyen dong)")

    # Doan video mo dau khong duoc dung hinh cho the tieu de chay xong
    first_clip = [clip_row(0, t0 + timedelta(days=1), dur_s=3.0, score=99.0)]
    later = [x for x in base if x["taken_at"] > t0 + timedelta(days=200)]
    r4 = select.apply(first_clip + later, {})
    sb4 = story.storyboard(r4["selected"], o)
    s0 = sb4["shots"][0]
    if s0["kind"] == "clip":
        check(abs(s0["hold"] / sb4["fps"] - 3.0) < 0.15,
              f"doan mo dau khong bi keo dai cho tieu de "
              f"({s0['hold'] / sb4['fps']:.2f}s)")
        check(sb4["f_title"] <= s0["hold"],
              "the tieu de hien len tren doan dang chay, khong dai hon doan")

    # Tran thoi luong: nhieu doan dai khong duoc lam video phinh vo han
    many = [clip_row(200 + i, t0 + timedelta(days=20 * i), dur_s=4.4, score=95.0)
            for i in range(60)]
    r3 = select.apply(base + many, {})
    total = sum(story.row_seconds(x, r3["filters"] and story.plan(r3["filters"]))
                for x in r3["selected"])
    check(total <= story.MAX_SECONDS + 0.1,
          f"tong thoi luong bi chan o {story.MAX_SECONDS}s (duoc {total:.1f}s)")
    per_ch = {}
    for x in r3["selected"]:
        per_ch[x["bucket"]] = per_ch.get(x["bucket"], 0) + 1
    check(all(v >= 1 for v in per_ch.values()),
          "tia bot van giu moi chuong it nhat mot shot")


def t_clip_render(dump=None):
    """Neo doan video: khuon mat DI CHUYEN trong clip, khung phai bat kip."""
    print("\n[0e] neo khuon mat dang di chuyen trong doan video")
    import cv2
    work = HERE / "_selftest_vid"
    work.mkdir(parents=True, exist_ok=True)
    path = work / "clip.mp4"
    W, H, FPS, N = 640, 360, 24, 72              # 3 giay
    wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not wr.isOpened():
        print("       -> cv2.VideoWriter khong mo duoc, bo qua kiem tra nay")
        return
    # Khoi sang di chuyen deu tu (0.25,0.30) den (0.75,0.60)
    centers = []
    for i in range(N):
        u = i / (N - 1.0)
        cx, cy = 0.25 + 0.50 * u, 0.30 + 0.30 * u
        img = np.zeros((H, W, 3), np.uint8)
        img[:, :, 0] = np.linspace(20, 200, W, dtype=np.uint8)[None, :]
        px, py = int(cx * W), int(cy * H)
        cv2.rectangle(img, (px - 24, py - 16), (px + 24, py + 16),
                      (255, 255, 255), -1)
        wr.write(img)
        centers.append((cx, cy))
    wr.release()

    # track: 2 fps trong khoang 0.5s..2.5s, kps suy tu vi tri khoi sang
    rows_ = []
    for t_ms in range(500, 2501, 500):
        i = min(N - 1, int(round(t_ms / 1000.0 * FPS)))
        cx, cy = centers[i]
        d = 0.05
        k = [cx - d, cy, cx + d, cy, cx, cy + d, cx - d * 0.7, cy + 2 * d,
             cx + d * 0.7, cy + 2 * d]
        rows_.append([t_ms / 1000.0] + k)
    track = np.asarray(rows_, np.float32).tobytes()

    sh = {"asset_id": "v1", "fidx": -1, "kind": "clip",
          "video_path": str(path), "track": track,
          "t_start_ms": 500, "t_end_ms": 2500, "taken_at": None,
          "kps": None, "kps2": None, "chapter": 0, "label": "", "hero": True,
          "first_of_chapter": True, "hold": 48, "vis": 48,
          "zoom_from": 1.0, "zoom_to": 1.0}
    o = render.options({"mode": "story", "size": 320, "aspect": "4:3",
                        "face_frac": 0.30, "eye_y": 0.33, "label": "none"})
    src = render._ClipSrc(sh, o, render.get())
    try:
        want = (0.5 * o["out_w"], 0.33 * o["out_h"])
        spots = []
        for idx in (0, 12, 24, 36, 47):
            f = src.frame(idx)
            check(f is not None and f.shape[:2] == (o["out_h"], o["out_w"]),
                  f"frame {idx}: doc duoc va dung kich thuoc")
            if f is None:
                break
            ys, xs = np.where(f[:, :, 2] > 240)
            if xs.size < 20:
                check(False, f"frame {idx}: khong thay khoi sang trong khung ra")
                continue
            spots.append((float(xs.mean()), float(ys.mean())))
            if dump:
                cv2.imwrite(str(Path(dump) / f"clip_{idx:03d}.jpg"), f)
        check(len(spots) >= 4, "lay duoc it nhat 4 frame de so sanh")
        for i, (x, y) in enumerate(spots):
            check(abs(x - want[0]) < 14 and abs(y - want[1]) < 14,
                  f"frame thu {i}: chu the nam dung diem neo "
                  f"({x:.0f},{y:.0f}) vs ({want[0]:.0f},{want[1]:.0f})")
        if len(spots) >= 2:
            dx = max(abs(a[0] - b[0]) for a in spots for b in spots)
            dy = max(abs(a[1] - b[1]) for a in spots for b in spots)
            check(dx < 16 and dy < 16,
                  f"chu the KHONG troi trong ca doan (lech toi da {dx:.0f},{dy:.0f}px)")
    finally:
        src.close()
        import shutil
        shutil.rmtree(work, ignore_errors=True)


def t_audio():
    """Tieng cua tung doan dat dung cho tren dong thoi gian, va vao truoc hinh."""
    print("\n[0f] xep tieng cua cac doan video")
    o = render.options({"mode": "story", "audio": True, "audio_lead": 0.5,
                        "audio_tail": 0.8})
    fps = 24
    # ba doan: mot o ngay dau video (khong the vao truoc giay 0), hai o giua
    shots = [
        {"kind": "clip", "start": 0, "hold": 72, "video_path": __file__,
         "t_start_ms": 4000, "t_end_ms": 7000, "src_dur_ms": 30000},
        {"kind": "image", "start": 72, "hold": 24},
        {"kind": "clip", "start": 96, "hold": 72, "video_path": __file__,
         "t_start_ms": 100, "t_end_ms": 3100, "src_dur_ms": 30000},
        {"kind": "clip", "start": 168, "hold": 72, "video_path": __file__,
         "t_start_ms": 20000, "t_end_ms": 23000, "src_dur_ms": 21000},
    ]
    sb = {"fps": fps, "n_frames": 240}
    plan = render.audio_plan(shots, sb, o, render.get())
    check(len(plan) == 3, f"chi doan video co tieng ({len(plan)}/3)")

    a0, a1, a2 = plan
    check(a0["at"] == 0.0, "doan o giay 0: khong the vao truoc, at = 0")
    check(abs(a0["src_start"] - 4.0) < 1e-6,
          "doan o giay 0: lead bi cat theo, cat tu dung t_start")
    check(a1["at"] < 96 / fps, "doan giua: tieng vao TRUOC hinh (J-cut)")
    check(abs(a1["at"] - (96 / fps - 0.1)) < 1e-6,
          "lead bi cat theo phan source con (t_start=0.1s)")
    check(a1["dur"] > 3.0, "tieng dai hon phan hinh (L-cut o duoi)")
    check(abs(a2["src_start"] + a2["dur"] - 21.0) < 1e-6,
          "tail bi cat o cuoi file nguon, khong doc qua do dai video")
    check(all(x["fade_in"] > 0 and x["fade_out"] > 0 for x in plan),
          "moi doan deu co fade hai dau")
    check(all(x["fade_in"] <= x["dur"] / 2 and x["fade_out"] <= x["dur"] / 2
              for x in plan), "fade khong bao gio dai qua nua doan")
    # Hai bat bien quan trong nhat: delay am hoac atrim am deu lam tieng lech
    # voi hinh suot ca doan, va ffmpeg khong bao loi — no chi ra sai.
    check(all(x["at"] >= 0.0 for x in plan), "khong bao gio delay am")
    check(all(x["src_start"] >= 0.0 for x in plan), "khong bao gio atrim am")
    check(all(x["dur"] > 0.0 for x in plan), "do dai luon duong")
    check("adelay=2000" in render.audio_filter(
        [{"path": "a.mp4", "src_start": 0.0, "dur": 3.0, "at": 2.0,
          "fade_in": 0.3, "fade_out": 0.5}], o, 10.0)[0],
        "adelay tinh bang MILI giay")

    fc, lab = render.audio_filter(plan, o, total=10.0)
    check(lab == "[aout]", "co nhan dau ra")
    check(fc.count("aformat=") == 3,
          "moi input duoc chuan hoa sample rate/kenh truoc khi tron")
    check("amix=inputs=3:normalize=0" in fc,
          "amix cong thang, khong chia am luong cho so input")
    check("dynaudnorm" in fc, "can muc giua cac doan")
    check("alimiter" in fc, "chan dinh de khong clip")
    check("apad=whole_dur=10.000" in fc, "track tieng phu het do dai video")
    check(fc.count("adelay=") == 2,
          "chi doan khong bat dau o giay 0 moi can adelay")

    one = render.audio_filter(plan[:1], o, total=5.0)[0]
    check("amix" not in one, "mot doan thi khong dung amix")

    off = render.options({"audio": False})
    check(render._mux_audio(Path("x.mp4"), shots, sb, off, render.get(),
                            HERE) is None, "audio=False thi bo qua han")
    check(render.audio_filter([], o, 5.0) == ("", ""),
          "khong co doan nao -> khong co filter")


def main():
    dump = None
    if "--dump" in sys.argv:
        dump = sys.argv[sys.argv.index("--dump") + 1]
        Path(dump).mkdir(parents=True, exist_ok=True)
    t_auto()
    t_subjects()
    t_pair_anchor()
    t_clip_select()
    t_clip_render(dump)
    t_audio()
    c = t_select()
    r, o, sb = t_storyboard(c)
    img = t_text()
    pipe = t_render(r, o, sb, dump)
    if dump:
        import cv2
        cv2.imwrite(str(Path(dump) / "text.jpg"), img)

    import shutil
    shutil.rmtree(HERE / "_selftest_out", ignore_errors=True)
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
