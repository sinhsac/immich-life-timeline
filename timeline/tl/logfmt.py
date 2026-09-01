"""Dinh dang log: moi dong co timestamp va run id.

VI SAO LAM KIEU BOC STDOUT thay vi doi sang module logging: ca hai service dang
dung print() o hang tram cho. Doi tung cho sang logger la mot thay doi rat lon,
rui ro cao, va khong mang lai gi ngoai dinh dang. Boc sys.stdout thi moi print()
co san tien to, diff toi thieu, khong dung vao logic.

Dinh dang moi dong:

    2026-09-01T18:30:04+07:00 [a3f9c1d2] assets 54000/110730  8999.5/s

    <thoi gian ISO co mui gio>  [run id]  <noi dung goc>

stderr them tag [err] de phan biet khi doc lan lon.

RUN ID de lam gi: mot lan chay CronJob la mot pod moi, va log cua nhieu lan chay
nam lan trong cung mot cho khi tim kiem. Loc theo run id la tach duoc dung mot
lan chay. Dat qua bien RUN_ID neu muon tu chon, khong thi sinh ngau nhien.

MUI GIO: container mac dinh UTC. Dat TZ=Asia/Ho_Chi_Minh trong ConfigMap
fp-base-env thi timestamp ra gio Viet Nam, khop voi lich CronJob.

LUU Y: file nay co ban giong het o timeline/tl/logfmt.py. Hai image build rieng,
khong dung chung package nao, nen nhan doi ~100 dong re hon la dung goi chung.
Sua mot ben thi sua ca ben kia.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from datetime import datetime

_LOCK = threading.Lock()
_RUN_ID = ""
_INSTALLED = False


def run_id() -> str:
    """Run id cua tien trinh nay. Giu nguyen trong suot ca lan chay."""
    global _RUN_ID
    if not _RUN_ID:
        _RUN_ID = os.environ.get("RUN_ID") or uuid.uuid4().hex[:8]
    return _RUN_ID


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class _PrefixStream:
    """Boc mot stream, chen tien to vao dau moi DONG.

    print() goi write() nhieu lan: mot lan cho noi dung, mot lan cho "\\n". Nen
    phai dem lai tới khi gap newline moi xuat, neu khong tien to se roi vao giua
    dong. Cac ky tu con du sau newline cuoi duoc giu lai cho lan write ke tiep.
    """

    def __init__(self, raw, tag: str = "") -> None:
        self._raw = raw
        self._tag = tag
        self._buf = ""

    # Phan giao tiep toi thieu de thu vien khac khong vo khi thay stdout that.
    # uvicorn va onnxruntime co kiem tra isatty()/fileno().
    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self._raw.fileno()

    def writable(self) -> bool:
        return True

    @property
    def encoding(self) -> str:
        return getattr(self._raw, "encoding", "utf-8")

    def write(self, s) -> int:
        if not isinstance(s, str):
            s = str(s)
        with _LOCK:
            self._buf += s
            if "\n" not in self._buf:
                return len(s)
            *lines, self._buf = self._buf.split("\n")
            prefix = f"{_stamp()} [{run_id()}]{self._tag} "
            self._raw.write("".join(prefix + ln + "\n" for ln in lines))
            self._raw.flush()
        return len(s)

    def flush(self) -> None:
        with _LOCK:
            if self._buf:
                self._raw.write(
                    f"{_stamp()} [{run_id()}]{self._tag} {self._buf}\n")
                self._buf = ""
            self._raw.flush()


def banner(component: str, extra: dict | None = None) -> None:
    """In mot dong ngu canh de doi chieu log voi pod/node tren ArgoCD."""
    ctx = {
        "component": component,
        "run_id": run_id(),
        "pod": os.environ.get("POD_NAME") or os.environ.get("HOSTNAME", "?"),
        "node": os.environ.get("NODE_NAME", "?"),
        "tz": time.strftime("%Z%z"),
        "python": sys.version.split()[0],
    }
    if extra:
        ctx.update(extra)
    print("ctx " + " ".join(f"{k}={v}" for k, v in ctx.items()))


def install(component: str, extra: dict | None = None) -> str:
    """Boc stdout/stderr roi in dong ngu canh. Tra ve run id.

    Idempotent. Loi o day KHONG bao gio duoc lam chet ung dung - log hong thi
    chay tiep voi stream goc con hon la khong chay.
    """
    global _INSTALLED
    if _INSTALLED:
        return run_id()

    # Container mac dinh UTC; TZ lay tu bien moi truong.
    if os.environ.get("TZ") and hasattr(time, "tzset"):
        try:
            time.tzset()
        except Exception:
            pass

    try:
        sys.stdout = _PrefixStream(sys.stdout)
        sys.stderr = _PrefixStream(sys.stderr, tag=" [err]")
        _INSTALLED = True
    except Exception:
        return run_id()

    banner(component, extra)
    return run_id()


def attach_to_logging() -> None:
    """Ap cung dinh dang cho cac logger da ton tai (uvicorn).

    Can rieng ham nay vi uvicorn cau hinh logging TRUOC khi import app, nen
    handler cua no giu tham chieu toi stderr GOC - boc sys.stderr sau do khong
    anh huong gi tới chung. Doi Formatter thi moi tac dung.
    """
    import logging

    fmt = logging.Formatter(
        fmt=f"%(asctime)s [{run_id()}] [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z")
    seen = set()
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        for h in logging.getLogger(name).handlers:
            if id(h) not in seen:
                seen.add(id(h))
                h.setFormatter(fmt)
