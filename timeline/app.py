#!/usr/bin/env python3
"""Service dung video timeline mot nguoi tu thu vien Immich.

    uvicorn app:app --host 0.0.0.0 --port 8080
    python app.py --check          # kiem tra pg / anh / ffmpeg roi thoat

Bon buoc: chon nguoi -> tu dong lay anh -> tinh chinh pose -> ffmpeg dung video.
Doc du lieu do job indexer tao ra (fp_asset / fp_face / fp_body), ghi vao bang
rieng cua minh (fp_project / fp_project_frame / fp_render).

Service KHONG load model nao: align dung kps da luu san.
"""
import secrets
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from tl import db, render
from tl.api import router
from tl.settings import get

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Immich timeline video", version="1.0.0",
              docs_url="/api/docs", openapi_url="/api/openapi.json")


COOKIE = "fp_token"
OPEN_PATHS = ("/api/health", "/healthz")


def _same(a, b):
    """So sanh thoi gian hang so. Token la ASCII; request xau co the khong."""
    try:
        return secrets.compare_digest(a, b)
    except (TypeError, ValueError):
        return False


@app.middleware("http")
async def auth(request: Request, call_next):
    """Xac thuc bang token. Service nay xem duoc anh gia dinh nen dat API_TOKEN
    di, dung de mo cong ra internet ma khong co gi chan.

    Token nhan tu ba nguon: header Authorization, ?token=..., hoac cookie.
    Cookie la bat buoc chu khong phai tien nghi: trinh duyet tai style.css va
    app.js bang the <link>/<script> nen KHONG gui duoc header, va index.html
    khong the tu them ?token= vao do. Thieu cookie thi hai file nay bi 401 va
    trang hien ra dang HTML tran, khong co tuong tac.
    """
    s = get()
    if not s.api_token or request.url.path in OPEN_PATHS:
        return await call_next(request)

    q = request.query_params.get("token", "")
    sent = (request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            or q or request.cookies.get(COOKIE, ""))
    if not _same(sent, s.api_token):
        return JSONResponse({"detail": "thieu hoac sai token"}, 401)

    response = await call_next(request)
    # Vao bang ?token= mot lan -> ghi cookie de cac request sau tu di qua.
    if q and request.cookies.get(COOKIE) != s.api_token:
        response.set_cookie(COOKIE, s.api_token, max_age=30 * 86400,
                            httponly=True, samesite="lax", path="/")
    return response


app.include_router(router)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.on_event("startup")
def startup():
    s = get()
    print("=" * 62)
    print(s.describe())
    print("=" * 62)
    problems = s.check()
    if problems:
        for p in problems:
            print(f"  [LOI] {p}")
        raise SystemExit("thieu cau hinh, xem .env.example")
    if not s.api_token:
        print("  [CANH BAO] API_TOKEN chua dat -> service khong co xac thuc.")
        print("             Chi de sau reverse proxy hoac trong mang noi bo.")
    db.ensure_schema()
    ok, msg = db.indexer_ready()
    print(f"  indexer: {'ok' if ok else 'CHUA SAN SANG'} - {msg}")
    fok, fmsg = render.ffmpeg_ok()
    print(f"  ffmpeg : {'ok' if fok else 'THIEU'} - {fmsg}")
    if not fok:
        print("             buoc render se loi cho den khi co ffmpeg")


@app.on_event("shutdown")
def shutdown():
    db.close()


if STATIC.exists():
    app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")


def _check():
    s = get()
    print(s.describe())
    bad = s.check()
    for p in bad:
        print(f"  [LOI] {p}")
    if bad:
        return 1
    db.ensure_schema()
    ok, msg = db.indexer_ready()
    print(f"  indexer: {'ok' if ok else 'CHUA SAN SANG'} - {msg}")
    fok, fmsg = render.ffmpeg_ok()
    print(f"  ffmpeg : {'ok' if fok else 'THIEU'} - {fmsg}")
    return 0 if (ok and fok) else 1


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(_check())
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080, workers=1)
