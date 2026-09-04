"""Nhac nen: index thu muc, tim kiem, phan trang, nhan upload, do metadata.

Vi sao co module rieng cho mot viec nghe nho nhu vay: ten bai nhac den TU CLIENT
va no duoc dat vao dong lenh ffmpeg. Neu chi noi music_dir / name thi mot yeu cau
'../../etc/shadow' se doc duoc file ngoai thu muc. Toan bo viec kiem tra nam o
day, MOT cho duy nhat — doc, ghi, xoa, phat — de khong co duong nao di tat.

## Vi sao co index thay vi quet moi lan

Ban dau module nay quet thu muc bang rglob moi lan duoc goi, va tra ve ca danh
sach. Dung cho hai chuc bai. O muc mot nghin bai thi:

  - GET /api/music quet thu muc HAI luot (mot cho danh sach, mot cho usage)
    = 2000 lan stat cho moi lan mo man hinh
  - tra ve ~100KB JSON moi lan
  - va phia UI khong the dung mot <select> 1000 dong

Nen gio: quet mot lan, cache theo TTL, va tra ve TUNG TRANG da loc. Ghi (upload /
xoa) lam cache het hieu luc ngay, khong doi TTL — nguoi vua up mot bai phai thay
no lap tuc.

## Vi sao tran theo DUNG LUONG TRONG moi la tran that

MUSIC_DIR nam tren cung phan vung voi container runtime. Lam day dia khong lam
"upload that bai", no lam **ca node** ngung hoat dong. Mot tran tong co dinh
khong biet gi ve viec dia con bao nhieu: 6GB nhac la vo hai khi con 200GB va la
tai hoa khi con 19GB. Vi vay co ca hai, va cai quyet dinh la MUSIC_MIN_FREE_MB.
"""
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

from . import beats as beatmod

# Nhung duoi ffmpeg doc duoc ma nguoi ta thuc su dung cho nhac nen.
EXTS = (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma")

# Kieu MIME cho endpoint phat thu. Trinh duyet can dung loai de chon decoder.
MIME = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
        ".flac": "audio/flac", ".ogg": "audio/ogg", ".opus": "audio/ogg",
        ".wav": "audio/wav", ".wma": "audio/x-ms-wma"}

# Ten file sau khi lam sach. Giu chu (ke ca chu co dau), so, khoang trang, gach,
# cham; con lai thay bang gach duoi. Khong dau phan cach duong dan nao qua duoc.
_CLEAN = re.compile(r"[^0-9A-Za-z\u00C0-\u024F\u1E00-\u1EFF ._-]+")

# Bao lau thi quet lai thu muc neu khong co ai ghi. Ngan thoi: muc dich chi la go
# gánh cho mot loat request lien tiep (nguoi dung go vao o tim kiem), khong phai
# giu cache lau.
INDEX_TTL = 20.0

_index = {"at": 0.0, "root": None, "items": [], "by_name": {}}
# Do dai cache theo (ten, mtime, size): file bi thay the thi khoa doi theo, khong
# bao gio tra ve do dai cua ban cu.
_dur = {}
_lock = threading.RLock()


class MusicError(Exception):
    """Loi nguoi dung sua duoc: sai duoi, qua to, khong phai file nhac."""


# ------------------------------------------------------------------ tim kiem
def fold(t):
    """Bo dau va ha chu thuong, de go 'nhac cham' tim ra 'Nhạc Chậm'.

    Ten file cua nguoi dung o day la tieng Viet co dau, ma go co dau vao o tim
    kiem thi bat tien — khong bo dau thi o tim kiem gan nhu vo dung.
    """
    n = unicodedata.normalize("NFD", str(t or ""))
    return "".join(c for c in n if not unicodedata.combining(c)).lower()


def _scan(root):
    out = []
    for p in root.rglob("*"):
        if p.suffix.lower() not in EXTS:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if not p.is_file() or st.st_size <= 0:
            continue
        name = p.relative_to(root).as_posix()
        out.append({
            "name": name,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "label": p.stem.replace("_", " ").replace("-", " "),
            "folder": str(Path(name).parent).replace(".", "") or "",
            "_fold": fold(name),
        })
    out.sort(key=lambda x: x["_fold"])
    return out


def invalidate():
    """Bo cache index. Goi sau MOI lan ghi, khong de nguoi dung doi het TTL."""
    with _lock:
        _index["at"] = 0.0


def index(s, force=False):
    """Danh sach bai da cache. [] neu chua cau hinh MUSIC_DIR."""
    root = s.music
    if root is None or not root.is_dir():
        return []
    with _lock:
        fresh = (_index["root"] == str(root)
                 and not force
                 and time.time() - _index["at"] < INDEX_TTL)
        if fresh:
            return _index["items"]
        items = _scan(root)
        # Map theo ten dung mot lan o day. Truoc do moi lan can mtime/size cua
        # mot bai lai quet ca danh sach, nen sort theo do dai la O(n^2) — mot
        # trieu phep so sanh o muc mot nghin bai.
        _index.update(at=time.time(), root=str(root), items=items,
                      by_name={it["name"]: it for it in items})
        return items


def available(s):
    """Toan bo danh sach. Giu cho cac cho goi noi bo; UI dung find() de phan trang.

    'name' la thu gui lai trong render options: {"music": "cham/piano-01.mp3"}.
    Chi la duong dan TUONG DOI — duong dan tuyet doi tren server khong phai viec
    cua client, va nhan lai duong dan tuyet doi tu client la mot lo hong.
    """
    return [{k: v for k, v in it.items() if not k.startswith("_")}
            for it in index(s)]


SORTS = ("name", "newest", "largest", "shortest", "longest")


def find(s, q="", offset=0, limit=30, sort="name", folder=None):
    """Mot TRANG ket qua da loc. Tra ve (tong so khop, cac muc, cac thu muc con).

    Loc bang Python tren index da cache chu khong quet lai: o muc mot nghin bai,
    loc trong bo nho la vai tram micro giay, con rglob la hang nghin syscall.
    """
    items = index(s)
    folders = sorted({it["folder"] for it in items if it["folder"]})
    if folder:
        items = [it for it in items if it["folder"] == folder]
    terms = [t for t in fold(q).split() if t]
    if terms:
        # Moi tu phai xuat hien, khong nhat thiet lien nhau: 'piano cham' tim ra
        # 'cham/piano-01.mp3'.
        items = [it for it in items if all(t in it["_fold"] for t in terms)]
    total = len(items)

    if sort == "newest":
        items = sorted(items, key=lambda x: -x["mtime"])
    elif sort == "largest":
        items = sorted(items, key=lambda x: -x["size"])
    elif sort in ("shortest", "longest"):
        # Do dai chi biet voi nhung bai DA do; bai chua do xep xuong cuoi thay vi
        # bi coi la dai 0 giay roi nhay len dau. Doc _dur truc tiep tu item nen
        # khong co phep tra cuu nao trong vong sort.
        rev = (sort == "longest")
        def key(x):
            d = _dur.get(_dkey(x))
            return (d is None, -(d or 0.0) if rev else (d or 0.0))
        items = sorted(items, key=key)

    offset = max(0, int(offset))
    limit = max(1, min(200, int(limit)))
    page = items[offset:offset + limit]
    out = []
    for it in page:
        row = {k: v for k, v in it.items() if not k.startswith("_")}
        row.update(_meta_of(s, it))
        out.append(row)
    return total, out, folders


# ---------------------------------------------------------------- metadata
def _dkey(item):
    """Khoa cache do dai. Nhan CHINH item de khong phai tra cuu lai."""
    return (item["name"], item["mtime"], item["size"])


def _item(s, name):
    index(s)
    return _index["by_name"].get(name)


def _meta_of(s, item):
    """Do dai + BPM neu DA BIET. Khong bao gio tu do o day.

    Ly do quan trong: do dai bang ffprobe la ~20-50ms/bai — chap nhan duoc cho
    mot trang 30 bai. Nhung BPM la giai ma 120 giay + FFT, khoang 1-3 GIAY moi
    bai; lam viec do cho mot trang danh sach la treo request, va cho ca nghin bai
    la 20-50 phut CPU tranh voi Immich. Nen BPM chi hien khi da do vi mot ly do
    khac: nguoi dung chon bai do, hoac da tung render voi no.
    """
    out = {"duration": None, "bpm": None}
    d = _dur.get(_dkey(item))
    if d is not None:
        out["duration"] = round(d, 1)
    p = resolve(s, item["name"])
    if p is not None:
        hit = beatmod.peek(p)
        if hit is not None:
            out["bpm"] = round(float(hit[1] or 0.0), 1) if hit[0] else 0.0
    return out


def meta(s, name, want_bpm=False):
    """Metadata cua MOT bai, do neu chua biet. Dung khi nguoi dung chon/nghe thu.

    want_bpm=True moi chay do nhip — dat, nen chi lam khi that su can.
    """
    p = resolve(s, name)
    it = _item(s, name)
    if p is None or it is None:
        return None
    k = _dkey(it)
    if k not in _dur:
        d = duration(p, s)
        if d is not None:
            _dur[k] = d
    out = {"name": name, "duration": None, "bpm": None}
    if k in _dur:
        out["duration"] = round(_dur[k], 1)
    if want_bpm:
        times, bpm = beatmod.cached_detect(p, s)
        out["bpm"] = round(float(bpm or 0.0), 1) if times else 0.0
    else:
        hit = beatmod.peek(p)
        if hit is not None:
            out["bpm"] = round(float(hit[1] or 0.0), 1) if hit[0] else 0.0
    return out


# ------------------------------------------------------------------ doc file
def resolve(s, name):
    """Ten tuong doi -> Path that. None neu khong hop le.

    Ba lop kiem tra, va lop thu ba moi la lop that su chan duoc:
      1. tu choi duong dan tuyet doi va cac doan '..'
      2. ghep vao MUSIC_DIR roi resolve() de rut het symlink va '..' con lai
      3. kiem tra ket qua CO NAM TRONG MUSIC_DIR da resolve. Chi so sanh chuoi
         truoc khi resolve la khong du: mot symlink hop le ve mat cu phap van tro
         ra ngoai duoc.
    """
    root = s.music
    if root is None or not name:
        return None
    raw = str(name).strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        return None
    try:
        base = root.resolve()
        p = (base / raw).resolve()
    except OSError:
        return None
    if base != p and base not in p.parents:
        return None
    if not p.is_file() or p.suffix.lower() not in EXTS:
        return None
    return p


def mime(path):
    return MIME.get(Path(path).suffix.lower(), "application/octet-stream")


def duration(path, s):
    """Do dai bai nhac theo giay, hoac None neu khong doc duoc.

    Dung de biet co phai lap lai bai hay khong. Khong doc duoc thi phia goi cu
    lap — lap mot bai dai hon video la vo hai, con de video im lang o cuoi thi
    khong.
    """
    try:
        p = subprocess.run(
            [s.ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if p.returncode != 0:
        return None
    try:
        d = float((p.stdout or "").strip().split(",")[0])
    except (TypeError, ValueError):
        return None
    return d if d > 0 else None


# ------------------------------------------------------------------- ghi
def safe_name(raw):
    """Ten client gui -> mot ten file an toan, hoac raise MusicError.

    Lay basename TRUOC khi lam sach: 'a/../../b.mp3' phai thanh 'b.mp3' chu khong
    phai 'a_.._.._b.mp3'. Upload luon nam TRUC TIEP trong MUSIC_DIR, khong tao
    thu muc con — thu muc con van doc duoc neu ban tu copy vao, chi la upload
    khong sinh ra chung.
    """
    raw = str(raw or "").strip().replace("\\", "/")
    base = os.path.basename(raw)
    ext = Path(base).suffix.lower()
    if ext not in EXTS:
        raise MusicError(
            f"duoi {ext or '(khong co)'} khong nhan; chi nhan: "
            + ", ".join(EXTS))
    stem = _CLEAN.sub("_", Path(base).stem).strip(" ._-")
    if not stem:
        stem = "track"
    return stem[:80] + ext


def total_size(s):
    """Tong byte dang chiem trong MUSIC_DIR, tinh tu index da cache."""
    return sum(it["size"] for it in index(s))


def free_mb(s):
    """Dung luong trong con lai cua filesystem chua MUSIC_DIR, theo MB."""
    root = s.music
    if root is None or not root.is_dir():
        return None
    try:
        return shutil.disk_usage(str(root)).free / 1e6
    except OSError:
        return None


def usage(s):
    """So lieu cho UI: dang dung bao nhieu, con bao nhieu, tran o dau."""
    items = index(s)
    return {"n": len(items),
            "used_mb": round(sum(it["size"] for it in items) / 1e6, 1),
            "total_mb": float(s.music_max_total_mb),
            "max_file_mb": float(s.music_max_mb),
            "min_free_mb": float(s.music_min_free_mb),
            "free_mb": (round(free_mb(s), 1) if free_mb(s) is not None else None),
            "exts": list(EXTS)}


def _room(s, want):
    """Bao nhieu byte duoc phep ghi them. Raise MusicError neu khong con cho.

    Hai tran, va cai thu hai moi la cai quan trong — xem ghi chu dau file.
    """
    used = total_size(s)
    room = int(s.music_max_total_mb * 1e6) - used
    if room <= 0:
        raise MusicError(
            f"thu muc nhac dang dung {used / 1e6:.0f}MB, dat tran "
            f"MUSIC_MAX_TOTAL_MB={s.music_max_total_mb:g}MB — xoa vai bai truoc")
    fm = free_mb(s)
    if fm is not None:
        spare = int((fm - float(s.music_min_free_mb)) * 1e6)
        if spare <= 0:
            raise MusicError(
                f"dia chi con {fm:.0f}MB trong, duoi nguong an toan "
                f"MUSIC_MIN_FREE_MB={s.music_min_free_mb:g}MB. Khong ghi them de "
                f"khong lam day phan vung — don dia truoc")
        room = min(room, spare)
    return min(int(s.music_max_mb * 1e6), room, max(1, want) if want else room)


def save(s, filename, chunks, declared=0):
    """Ghi mot bai vao MUSIC_DIR. Tra ve (ten tuong doi, so byte).

    `chunks` la iterable cac lat bytes — nhan iterable chu khong nhan ca bytes de
    tran duoc kiem TRONG LUC ghi. `declared` la kich thuoc that neu biet truoc,
    dung de tu choi som truoc khi ghi byte nao.

    Raise MusicError cho moi truong hop nguoi dung sua duoc.
    """
    root = s.music
    if root is None:
        raise MusicError("MUSIC_DIR chua duoc cau hinh tren server")
    if not root.is_dir():
        raise MusicError(f"MUSIC_DIR khong ton tai: {s.music_dir}")

    name = safe_name(filename)
    cap = _room(s, 0)
    if declared and declared > int(s.music_max_mb * 1e6):
        raise MusicError(f"file {declared / 1e6:.1f}MB vuot tran "
                         f"{s.music_max_mb:g}MB/bai")
    if declared and declared > cap:
        raise MusicError(f"file {declared / 1e6:.1f}MB khong con du cho "
                         f"({cap / 1e6:.0f}MB)")

    # Ghi ra ten tam trong CHINH thu muc dich, khong phai /tmp: os.replace chi
    # nguyen tu khi hai ben cung mot filesystem. Duoi .part de _scan() bo qua.
    fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".upload-", suffix=".part")
    got = 0
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in chunks:
                if not chunk:
                    continue
                got += len(chunk)
                # Van phai dem khi ghi: khong co kich thuoc dang tin thi kiem tra
                # o tren khong du. Cung mot the voi _download_video cua indexer.
                if got > cap:
                    raise MusicError(
                        f"vuot cho con lai ({cap / 1e6:.0f}MB). Tran: "
                        f"{s.music_max_mb:g}MB/bai, "
                        f"{s.music_max_total_mb:g}MB ca thu muc, va giu it nhat "
                        f"{s.music_min_free_mb:g}MB trong tren dia")
                f.write(chunk)
        if got == 0:
            raise MusicError("file rong")
        if not has_audio(Path(tmp), s):
            raise MusicError("khong doc duoc stream audio nao trong file nay")

        dst = root / name
        # Khong ghi de im lang: '01.mp3' lan hai phai thanh '01-2.mp3', neu khong
        # thi mot du an dang tro toi bai cu bong nhien doi nhac.
        if dst.exists():
            stem, ext = Path(name).stem, Path(name).suffix
            for i in range(2, 1000):
                dst = root / f"{stem}-{i}{ext}"
                if not dst.exists():
                    break
            else:
                raise MusicError("qua nhieu file trung ten")
        os.replace(tmp, dst)
        tmp = None
        os.chmod(dst, 0o644)
        invalidate()
        return dst.relative_to(root).as_posix(), got
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def has_audio(path, s):
    """File nay co stream audio doc duoc khong?

    Day la kiem tra THAT SU, khac han viec tin vao duoi file: mot '.mp3' co the
    la script, la anh, la zip. ffprobe doc duoc mot stream audio la bang chung
    duy nhat dang tin — va no cung bat luon file nhac bi hong, ngay luc upload
    thay vi giua lan render.

    Thieu ffprobe thi tra ve False, tuc TU CHOI. Fail-closed la dung o day:
    /api/health da bao ffmpeg co hay khong, nen day khong phai loi am tham.
    """
    try:
        p = subprocess.run(
            [s.ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return p.returncode == 0 and "audio" in (p.stdout or "")


def delete(s, name):
    """Xoa mot bai. Tra ve True neu da xoa.

    Di qua resolve() nen khong the xoa gi ngoai MUSIC_DIR — cung mot cong kiem
    tra voi luc doc, khong co duong rieng cho viec xoa.
    """
    p = resolve(s, name)
    if p is None:
        return False
    try:
        p.unlink()
    except OSError:
        return False
    invalidate()
    return True
