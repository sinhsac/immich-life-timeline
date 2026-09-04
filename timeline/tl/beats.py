"""Tim nhip cua mot ban nhac -> mang moc thoi gian, de cat canh dung nhip.

Hai duong, cung mot dau ra:

  librosa   neu cai san. beat_track() cua no theo duoc ca nhac doi tempo giua bai
            nho quy hoach dong, tot hon han cach tu tinh.
  numpy     mac dinh. Chi can numpy + ffmpeg, ca hai da co san trong service.
            Gia dinh tempo GAN NHU KHONG DOI trong bai — dung voi hau het nhac
            nen, sai voi nhac co ritardando hay doi nhip giua bai.

Vi sao khong bat buoc librosa: no keo theo numba + scipy + soundfile, nang hon ca
phan con lai cua service cong lai, tren mot may 8GB dung chung voi Immich. Duong
numpy du dung cho nhac nen deu nhip, va ai muon tot hon thi
`pip install librosa` la tu dong duoc dung.

Module chi doc file nhac. Khong cham db, khong biet gi ve shot hay video.
"""
import subprocess
from pathlib import Path

import numpy as np

SR = 22050          # du de do nhip; nhac nen khong can 48kHz de dem phach
HOP = 512           # ~43 khung/giay
WIN = 1024

# Khoang tempo xet den. Duoi 60 thi mot phach dai hon mot giay — cat canh theo do
# thanh ra cham hon ca nhip ke chuyen; tren 180 thi dang dem nhip doi.
BPM_MIN, BPM_MAX = 60.0, 180.0

# Chi phan tich bao nhieu giay dau bai. Tempo cua mot ban nhac nen khong doi sau
# hai phut, ma doc het mot bai 8 phut thi ton bo nho vo ich.
ANALYZE_S = 120.0


def decode(path, s, seconds=ANALYZE_S):
    """Giai ma nhac thanh mono float32 qua ffmpeg. None neu that bai."""
    cmd = [s.ffmpeg, "-v", "error", "-i", str(path), "-vn",
           "-ac", "1", "-ar", str(SR), "-f", "f32le"]
    if seconds:
        cmd += ["-t", f"{float(seconds):.3f}"]
    cmd += ["pipe:1"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if p.returncode != 0 or not p.stdout:
        return None
    y = np.frombuffer(p.stdout, np.float32)
    return y if y.size >= SR else None      # duoi mot giay thi khong do duoc gi


def onset_envelope(y, sr=SR, hop=HOP, win=WIN):
    """Duong bao 'do dot ngot' theo thoi gian: spectral flux da chinh luu.

    Flux chu khong phai nang luong tho: mot doan bass giu deu co nang luong cao
    ma khong co phach nao, con mot tieng snare nhe thi nang luong thap nhung pho
    thay doi manh. Phach nam o cho PHO DOI, khong phai cho to.
    """
    if y is None or y.size < win * 2:
        return None
    n = 1 + (y.size - win) // hop
    if n < 8:
        return None
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = y[idx] * np.hanning(win).astype(np.float32)
    mag = np.abs(np.fft.rfft(frames, axis=1))
    flux = np.maximum(0.0, np.diff(mag, axis=0)).sum(axis=1)
    if flux.size < 8:
        return None
    # Bo trung binh truot roi chinh luu: nhac to dan (crescendo) khong duoc bien
    # thanh mot phach dai.
    k = 21
    pad = np.pad(flux, (k // 2, k // 2), mode="edge")
    base = np.convolve(pad, np.ones(k) / k, mode="valid")[:flux.size]
    env = np.maximum(0.0, flux - base)
    m = float(env.max())
    return (env / m) if m > 0 else None


def tempo_period(env, sr=SR, hop=HOP):
    """Chu ky mot phach, tinh bang SO KHUNG cua env. None neu khong ro nhip.

    Tu tuong quan cua duong bao: nhac deu nhip thi env giong chinh no khi dich di
    dung mot phach, nen tu tuong quan co dinh o do.
    """
    if env is None or env.size < 32:
        return None
    x = env - env.mean()
    ac = np.correlate(x, x, mode="full")[x.size - 1:]
    if ac.size < 8 or ac[0] <= 0:
        return None
    fps = sr / float(hop)
    lo = int(round(fps * 60.0 / BPM_MAX))
    hi = int(round(fps * 60.0 / BPM_MIN))
    lo, hi = max(2, lo), min(ac.size - 1, hi)
    if hi <= lo:
        return None
    band = ac[lo:hi + 1]
    best = int(np.argmax(band)) + lo
    # Dinh phai troi han nen nhieu, khong thi day la tieng on chu khong phai nhip.
    if band.max() <= 0 or band.max() < 0.15 * ac[0]:
        return None
    return float(best)


def _phase(env, period):
    """Lech pha tot nhat: offset trong [0, period) lam tong env tai cac phach lon nhat."""
    p = int(round(period))
    if p < 2:
        return 0
    best_off, best_sum = 0, -1.0
    for off in range(p):
        pos = np.arange(off, env.size, period).round().astype(int)
        pos = pos[pos < env.size]
        if pos.size == 0:
            continue
        tot = float(env[pos].sum()) / pos.size
        if tot > best_sum:
            best_off, best_sum = off, tot
    return best_off


def _numpy_beats(path, s):
    y = decode(path, s)
    env = onset_envelope(y)
    period = tempo_period(env)
    if period is None:
        return None, None
    off = _phase(env, period)
    fps = SR / float(HOP)
    n = int((env.size - off) / period) + 1
    beats = (off + period * np.arange(max(1, n))) / fps
    bpm = 60.0 * fps / period
    return [float(t) for t in beats], float(bpm)


def _librosa_beats(path, s):
    try:
        import librosa
    except ImportError:
        return None, None
    try:
        y, sr = librosa.load(str(path), sr=SR, mono=True, duration=ANALYZE_S)
        bpm, frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=HOP,
                                              units="frames")
        times = librosa.frames_to_time(frames, sr=sr, hop_length=HOP)
    except Exception:                                    # noqa: BLE001
        return None, None
    if times is None or len(times) < 4:
        return None, None
    val = float(np.atleast_1d(bpm)[0]) if bpm is not None else 0.0
    return [float(t) for t in times], val


def detect(path, s):
    """(moc phach theo giay, bpm) hoac (None, None) neu khong tim ra nhip.

    None la mot ket qua BINH THUONG, khong phai loi: nhac khong nhip ro (piano
    tu do, tieng mua, doc tho) thi cat theo nhip la sai. Phia goi phai lui ve
    nhip ke chuyen thong thuong.
    """
    beats, bpm = _librosa_beats(path, s)
    if beats:
        return beats, bpm
    return _numpy_beats(path, s)


# Ket qua do nhip nho lai theo (duong dan, lan sua doi): mot ban nhac cho ra cung
# mot luoi phach mai mai, ma do nhip la giai ma ca bai + FFT — khoang 1-3 giay moi
# bai tren may nay. Khong co ly gi lam lai moi lan nguoi dung keo mot thanh truot.
#
# Cache dat o DAY chu khong o render.py vi gio co hai phia can no: buoc render, va
# trinh chon nhac (muon hien BPM de loc bai khop nhip ke). Hai ban sao cua cung
# mot cache se lech nhau va lam viec dat nay chay hai lan.
_cache = {}


def _key(path):
    try:
        return (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None


def cached_detect(path, s):
    """detect() co nho ket qua. Tra ve (moc phach, bpm)."""
    k = _key(path)
    if k is None:
        return None, None
    hit = _cache.get(k)
    if hit is None:
        hit = detect(path, s)
        _cache[k] = hit
        if hit[0]:
            print(f"[beats] {Path(path).name}: {len(hit[0])} phach, "
                  f"{hit[1]:.0f} BPM")
        else:
            print(f"[beats] khong tim ra nhip ro trong {Path(path).name}")
    return hit


def peek(path):
    """Ket qua da do TRUOC DAY, hoac None neu chua do. Khong bao gio tu do.

    Trinh chon nhac dung ham nay de hien BPM cho nhung bai da biet ma khong lam
    mot lan mo danh sach thanh 30 lan giai ma.
    """
    k = _key(path)
    return _cache.get(k) if k else None


def grid(beats, total, every=1):
    """Luoi phach phu het `total` giay, lay moi `every` phach.

    Nhac ngan hon video (dang duoc lap lai) thi noi tiep luoi bang chinh chu ky
    trung binh, thay vi dung o cuoi bai roi de phan con lai khong co nhip.
    """
    if not beats or total <= 0:
        return []
    b = [t for t in sorted(float(x) for x in beats) if t >= 0]
    if len(b) < 2:
        return []
    step = (b[-1] - b[0]) / (len(b) - 1)
    if step <= 1e-3:
        return []
    out = list(b)
    while out[-1] < total:
        out.append(out[-1] + step)
    every = max(1, int(every))
    out = out[::every]
    return [t for t in out if t <= total + step]
