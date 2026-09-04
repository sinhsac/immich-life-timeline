"""Nhac nen: liet ke thu muc, phan giai ten an toan, do dai bai, va NHAN UPLOAD.

Vi sao co module rieng cho mot viec nho nhu vay: ten bai nhac den TU CLIENT, va
no duoc dat vao dong lenh ffmpeg. Neu chi noi music_dir / name la mot yeu cau
'../../etc/shadow' hoac '/etc/passwd' se doc duoc file ngoai thu muc. Toan bo viec
kiem tra nam o day, mot cho duy nhat, de khong co duong nao di tat.

Module khong goi ffmpeg de liet ke: doc do dai bang ffprobe chi khi that su can
(luc render), vi mot thu muc 50 bai se thanh 50 lan spawn process cho moi lan mo
man hinh.

## Ve viec cho upload

Ban dau thu muc nay mount READ-ONLY va khong co duong ghi nao — chu y, khong phai
thieu sot. Doi lai la moi lan them bai phai ssh vao may, nen gio co upload, va
phan doi lai duoc tra bang bon trong o day:

  1. TRAN TUNG FILE, kiem TRONG LUC GHI chu khong phai sau khi ghi xong.
  2. TRAN CA THU MUC. Quan trong hon nguoi ta tuong: /home/sokoda cung phan vung
     voi /var/lib/rancher, tuc lam day dia la keo sap ca k3s lan Immich, khong
     chi lam upload that bai.
  3. XAC THUC NOI DUNG bang ffprobe, khong tin duoi file. Mot file '.mp3' co the
     la bat cu thu gi; thu duy nhat chung minh no la nhac la co stream audio doc
     duoc.
  4. Ghi ra ten tam '.part' roi doi ten NGUYEN TU. UI hoi lai danh sach lien tuc,
     nen mot file dang tai do khong duoc phep xuat hien nhu mot bai chon duoc.

Endpoint upload di qua middleware xac thuc nhu moi endpoint khac. Service nay
xem duoc toan bo anh gia dinh VA gio ghi duoc file — dat API_TOKEN.
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path

# Nhung duoi ffmpeg doc duoc ma nguoi ta thuc su dung cho nhac nen.
EXTS = (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma")

# Tran mot bai. 30MB du cho mot ban mp3 320kbps dai 12 phut, hoac flac 3 phut.
MAX_MB = 30.0
# Tran ca thu muc. Nhac nen cho video ky niem thi vai chuc bai la nhieu; con so
# nay ton tai de mot vong lap upload khong the an vao 19G con lai cua rootfs.
MAX_TOTAL_MB = 500.0

# Ten file sau khi lam sach. Giu chu, so, khoang trang, gach, cham; con lai thay
# bang gach duoi. Khong co dau phan cach duong dan nao ton tai qua duoc buoc nay.
_CLEAN = re.compile(r"[^0-9A-Za-z\u00C0-\u024F\u1E00-\u1EFF ._-]+")


def available(s):
    """Danh sach bai nhac trong MUSIC_DIR. [] neu chua cau hinh.

    Tra ve 'name' la duong dan TUONG DOI so voi MUSIC_DIR (co the co thu muc con,
    vd 'cham/piano-01.mp3'), vi do la thu client gui lai khi render.
    """
    root = s.music
    if root is None or not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in EXTS:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size <= 0:
            continue
        out.append({"name": p.relative_to(root).as_posix(),
                    "size": size,
                    "label": p.stem.replace("_", " ").replace("-", " ")})
    return out


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


# ------------------------------------------------------------------- upload
class MusicError(Exception):
    """Loi nguoi dung sua duoc: sai duoi, qua to, khong phai file nhac."""


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
    return (stem[:80] + ext)


def total_size(s):
    """Tong byte dang chiem trong MUSIC_DIR. 0 neu chua cau hinh."""
    root = s.music
    if root is None or not root.is_dir():
        return 0
    n = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                n += p.stat().st_size
        except OSError:
            continue
    return n


def _has_audio(path, s):
    """File nay co stream audio doc duoc khong?

    Day la kiem tra THAT SU, khac han viec tin vao duoi file: mot '.mp3' co the
    la script, la anh, la zip. ffprobe doc duoc mot stream audio la bang chung
    duy nhat dang tin — va no cung bat luon file nhac bi hong, ngay luc upload
    thay vi giua lan render.
    """
    try:
        p = subprocess.run(
            [s.ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return p.returncode == 0 and "audio" in (p.stdout or "")


def save(s, filename, chunks, declared=0):
    """Ghi mot bai vao MUSIC_DIR. Tra ve (ten tuong doi, so byte).

    `chunks` la iterable cac lat bytes — nhan iterable chu khong nhan ca bytes de
    tran duoc kiem TRONG LUC ghi. `declared` la Content-Length neu client co khai,
    dung de tu choi som truoc khi ghi byte nao.

    Raise MusicError cho moi truong hop nguoi dung sua duoc.
    """
    root = s.music
    if root is None:
        raise MusicError("MUSIC_DIR chua duoc cau hinh tren server")
    if not root.is_dir():
        raise MusicError(f"MUSIC_DIR khong ton tai: {s.music_dir}")

    name = safe_name(filename)
    cap = int(MAX_MB * 1024 * 1024)
    if declared and declared > cap:
        raise MusicError(f"file {declared / 1e6:.1f}MB vuot tran {MAX_MB:g}MB")

    used = total_size(s)
    room = int(MAX_TOTAL_MB * 1024 * 1024) - used
    if room <= 0:
        raise MusicError(
            f"thu muc nhac da dung {used / 1e6:.0f}MB, dat tran "
            f"{MAX_TOTAL_MB:g}MB — xoa vai bai truoc")
    cap = min(cap, room)

    # Ghi ra ten tam trong CHINH thu muc dich, khong phai /tmp: doi ten chi la
    # nguyen tu khi hai ben cung mot filesystem. Duoi .part de available() bo qua.
    fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".upload-", suffix=".part")
    got = 0
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in chunks:
                if not chunk:
                    continue
                got += len(chunk)
                # Van phai dem khi ghi: khong co Content-Length dang tin thi
                # kiem tra o tren khong du. Cung mot the voi _download_video
                # cua indexer.
                if got > cap:
                    raise MusicError(
                        f"vuot tran ({MAX_MB:g}MB/bai, {MAX_TOTAL_MB:g}MB ca "
                        f"thu muc)")
                f.write(chunk)
        if got == 0:
            raise MusicError("file rong")
        if not _has_audio(Path(tmp), s):
            raise MusicError("khong doc duoc stream audio nao trong file nay")

        dst = root / name
        # Khong ghi de im lang: '01.mp3' lan hai phai thanh '01-2.mp3', neu khong
        # thi mot du an dang tro toi bai cu bong nhien doi nhac.
        if dst.exists():
            stem, ext = Path(name).stem, Path(name).suffix
            for i in range(2, 100):
                dst = root / f"{stem}-{i}{ext}"
                if not dst.exists():
                    break
            else:
                raise MusicError("qua nhieu file trung ten")
        os.replace(tmp, dst)
        tmp = None
        os.chmod(dst, 0o644)
        return dst.relative_to(root).as_posix(), got
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


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
    return True


def usage(s):
    """So lieu cho UI: dang dung bao nhieu, con bao nhieu."""
    used = total_size(s)
    return {"used_mb": round(used / 1e6, 1),
            "total_mb": MAX_TOTAL_MB,
            "max_file_mb": MAX_MB,
            "exts": list(EXTS)}
