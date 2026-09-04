"""Nhac nen: liet ke thu muc, phan giai ten an toan, do dai bai.

Vi sao co module rieng cho mot viec nho nhu vay: ten bai nhac den TU CLIENT, va
no duoc dat vao dong lenh ffmpeg. Neu chi noi music_dir / name la mot yeu cau
'../../etc/shadow' hoac '/etc/passwd' se doc duoc file ngoai thu muc. Toan bo viec
kiem tra nam o day, mot cho duy nhat, de khong co duong nao di tat.

Module khong goi ffmpeg de liet ke: doc do dai bang ffprobe chi khi that su can
(luc render), vi mot thu muc 50 bai se thanh 50 lan spawn process cho moi lan mo
man hinh.
"""
import subprocess
from pathlib import Path

# Nhung duoi ffmpeg doc duoc ma nguoi ta thuc su dung cho nhac nen.
EXTS = (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma")


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
