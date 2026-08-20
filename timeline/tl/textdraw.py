"""Ve chu len frame, co dau tieng Viet.

Tai sao khong dung cv2.putText truc tiep: font HERSHEY cua OpenCV chi co ASCII,
"Tháng 3" ra thanh "Thang 3" hoac o vuong. Video ke chuyen can hien ten nguoi va
nhan chuong bang tieng Viet dung dau, nen phai co font that.

Cach lam: sinh SPRITE mot lan cho moi (chuoi, co chu) roi dan lai nhieu lan voi
alpha khac nhau. Nho vay hieu ung mo dan chi la nhan alpha, khong ve lai chu —
quan trong vi mot nhan chuong xuat hien lien tuc vai chuc frame.

Hai duong ve, tu dong chon:
  1. Pillow + font TTF  -> co dau day du. Uu tien.
  2. cv2.putText        -> bo dau (fold ve ASCII) de con doc duoc, khong vo layout.
"""
import os
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

# Font tim theo thu tu nay. FONT_FILE trong env duoc uu tien tuyet doi.
_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

# Bang bo dau, chi dung khi khong co Pillow/font. Khong hoan hao nhung doc duoc.
_FOLD = str.maketrans({
    **{c: "a" for c in "àáạảãâầấậẩẫăằắặẳẵ"},
    **{c: "A" for c in "ÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴ"},
    **{c: "e" for c in "èéẹẻẽêềếệểễ"},
    **{c: "E" for c in "ÈÉẸẺẼÊỀẾỆỂỄ"},
    **{c: "i" for c in "ìíịỉĩ"}, **{c: "I" for c in "ÌÍỊỈĨ"},
    **{c: "o" for c in "òóọỏõôồốộổỗơờớợởỡ"},
    **{c: "O" for c in "ÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠ"},
    **{c: "u" for c in "ùúụủũưừứựửữ"},
    **{c: "U" for c in "ÙÚỤỦŨƯỪỨỰỬỮ"},
    **{c: "y" for c in "ỳýỵỷỹ"}, **{c: "Y" for c in "ỲÝỴỶỸ"},
    "đ": "d", "Đ": "D", "\u2013": "-", "\u2014": "-", "\u00b7": "-",
})

_state = {"probed": False, "font": None, "pil": None}


def _probe():
    """Tim Pillow + mot font TTF. Chay mot lan, ket qua cache trong _state."""
    if _state["probed"]:
        return _state
    _state["probed"] = True
    try:
        from PIL import Image, ImageDraw, ImageFont
        _state["pil"] = (Image, ImageDraw, ImageFont)
    except ImportError:
        _state["pil"] = None
        return _state
    for p in (os.environ.get("FONT_FILE", ""),) + _CANDIDATES:
        if p and Path(p).is_file():
            _state["font"] = p
            break
    if _state["font"] is None:
        _state["pil"] = None          # co Pillow nhung khong co font -> vo dung
    return _state


def backend():
    st = _probe()
    if st["pil"] and st["font"]:
        return f"pillow {Path(st['font']).name}"
    return "cv2 (khong co font TTF, chu se bi bo dau)"


def unicode_ok():
    st = _probe()
    return bool(st["pil"] and st["font"])


# ------------------------------------------------------------------- sprite
@lru_cache(maxsize=256)
def sprite(text, px, stroke=0):
    """(bgr uint8 HxWx3, alpha float32 HxW) — chu trang, vien den.

    Vien den la bat buoc chu khong phai trang tri: anh gia dinh co ca nen sang
    va nen toi, chu trang tron tren nen trang la mat chu.
    """
    text = (text or "").strip()
    if not text:
        return None
    px = max(8, int(px))
    stroke = int(stroke) if stroke else max(2, round(px * 0.075))
    st = _probe()
    if st["pil"] and st["font"]:
        return _sprite_pil(text, px, stroke, st)
    return _sprite_cv2(text.translate(_FOLD), px, stroke)


def _sprite_pil(text, px, stroke, st):
    Image, ImageDraw, ImageFont = st["pil"]
    try:
        font = _pil_font(st["font"], px)
    except OSError:
        return _sprite_cv2(text.translate(_FOLD), px, stroke)
    pad = stroke + 2
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    x0, y0, x1, y1 = probe.textbbox((0, 0), text, font=font, stroke_width=stroke)
    w, h = max(1, x1 - x0 + 2 * pad), max(1, y1 - y0 + 2 * pad)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - x0, pad - y0), text, font=font,
                             fill=(255, 255, 255, 255), stroke_width=stroke,
                             stroke_fill=(0, 0, 0, 235))
    a = np.asarray(img, np.uint8)
    return np.ascontiguousarray(a[:, :, 2::-1]), (a[:, :, 3].astype(np.float32) / 255.0)


@lru_cache(maxsize=32)
def _pil_font(path, px):
    _, _, ImageFont = _state["pil"]
    return ImageFont.truetype(path, px)


def _sprite_cv2(text, px, stroke):
    """Duong du phong. Dung hai mask: vien va long chu, de ra alpha dung."""
    scale = px / 30.0
    th = max(1, round(px * 0.09))
    (tw, tht), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
    pad = stroke + 2
    w, h = tw + 2 * pad, tht + base + 2 * pad
    org = (pad, pad + tht)
    outer = np.zeros((h, w), np.uint8)
    inner = np.zeros((h, w), np.uint8)
    cv2.putText(outer, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, 255,
                th + 2 * stroke, cv2.LINE_AA)
    cv2.putText(inner, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, 255, th,
                cv2.LINE_AA)
    bgr = np.zeros((h, w, 3), np.uint8)
    bgr[inner > 0] = (255, 255, 255)
    alpha = np.maximum(outer.astype(np.float32) / 255.0,
                       inner.astype(np.float32) / 255.0)
    return bgr, alpha


# --------------------------------------------------------------------- paste
def paste(frame, spr, x, y, alpha=1.0):
    """Dan sprite vao frame tai (x, y) goc tren-trai, co cat theo bien frame."""
    if spr is None or alpha <= 0.003:
        return
    bgr, a = spr
    fh, fw = frame.shape[:2]
    sh, sw = a.shape
    x, y = int(round(x)), int(round(y))
    sx0, sy0 = max(0, -x), max(0, -y)
    dx0, dy0 = max(0, x), max(0, y)
    ww = min(sw - sx0, fw - dx0)
    hh = min(sh - sy0, fh - dy0)
    if ww <= 0 or hh <= 0:
        return
    src = bgr[sy0:sy0 + hh, sx0:sx0 + ww].astype(np.float32)
    m = (a[sy0:sy0 + hh, sx0:sx0 + ww] * float(min(1.0, alpha)))[:, :, None]
    dst = frame[dy0:dy0 + hh, dx0:dx0 + ww]
    dst[:] = (dst.astype(np.float32) * (1.0 - m) + src * m).astype(np.uint8)


def block(frame, lines, y_frac=0.5, px=None, alpha=1.0, gap=0.34, x_frac=0.5):
    """Nhieu dong can giua. lines: [(chuoi, ty le co chu)] hoac [chuoi].

    Dung cho the tieu de va nhan chuong. px mac dinh theo canh ngan cua khung
    nen doi kich thuoc video khong phai sua lai gi.
    """
    fh, fw = frame.shape[:2]
    base = px or max(16, int(min(fw, fh) * 0.085))
    items = []
    for ln in lines:
        text, k = (ln if isinstance(ln, (tuple, list)) else (ln, 1.0))
        if text:
            items.append(sprite(str(text), int(base * k)))
    items = [s for s in items if s is not None]
    if not items:
        return
    hs = [s[1].shape[0] for s in items]
    total = sum(hs) + int(base * gap) * (len(items) - 1)
    y = fh * y_frac - total / 2.0
    for s, hgt in zip(items, hs):
        paste(frame, s, fw * x_frac - s[1].shape[1] / 2.0, y, alpha)
        y += hgt + int(base * gap)


def corner(frame, text, alpha=1.0, px=None, pad=0.035):
    """Nhan nho goc duoi giua — dung cho ngay thang chay lien tuc."""
    fh, fw = frame.shape[:2]
    spr = sprite(str(text), px or max(12, int(min(fw, fh) * 0.045)))
    if spr is None:
        return
    paste(frame, spr, (fw - spr[1].shape[1]) / 2.0,
          fh - spr[1].shape[0] - fh * pad, alpha)


def scrim(frame, height=0.32, strength=0.55, top=False):
    """Lop toi mo dan o day (hoac dinh) khung, de chu noi tren anh sang.

    Netflix/Google Photos deu lam viec nay; khong co no thi nhan chuong bien mat
    tren nhung anh chup ngoai troi.
    """
    fh = frame.shape[0]
    k = max(2, int(fh * height))
    ramp = np.linspace(0.0, strength, k, dtype=np.float32)
    if not top:
        ramp = ramp[::-1]
        band = frame[fh - k:]
    else:
        band = frame[:k]
    band[:] = (band.astype(np.float32) * (1.0 - ramp)[:, None, None]).astype(np.uint8)
