"""REST API. Bon nhom endpoint tuong ung bon buoc cua man hinh.

  GET    /api/people                     chon nguoi
  GET    /api/people/{pid}               phan bo anh theo nam
  POST   /api/projects                   tao du an -> tu dong chon anh
  GET    /api/projects/{id}/result       ket qua loc hien tai (+ ly do loai)
  PATCH  /api/projects/{id}/filters      tinh chinh nguong -> tinh lai ngay
  POST   /api/projects/{id}/exclude      bo / lay lai mot anh bang tay
  POST   /api/projects/{id}/storyboard  cau truc cau chuyen + thoi luong that
  POST   /api/projects/{id}/render       dung video
  GET    /api/renders/{id}               tien do
  GET    /api/renders/{id}/video         tai mp4

  GET    /api/progress                   tien do job indexer (ngoai 4 buoc)
"""
import random
from pathlib import Path
from typing import Any

from fastapi import (APIRouter, File, HTTPException, Query, Request,
                     UploadFile)
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import (db, music, people, projects, render, select, story, textdraw,
               thumbs)
from .settings import get

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------- schemas
class CreateProject(BaseModel):
    """Yeu cau chi gom: AI, va (tuy chon) TU NGAY NAO DEN NGAY NAO.

    Do dai video khong nam o day — no duoc suy ra tu du lieu. Muon dat tay thi
    gui filters.target_seconds (che do chuyen gia).

      mot nguoi          person_ids: ["c1","c2"]        cac cluster cua ho
      hai nguoi          subjects: [["c1","c2"],["c3"]]
      hai nguoi chup chung  + together: true
    """
    # person_id: mot cluster (cach cu, van dung duoc)
    # person_ids: nhieu cluster cua CUNG mot nguoi -> gop thanh mot video
    person_id: str | None = None
    person_ids: list[str] | None = None
    # subjects: nhieu NGUOI, moi nguoi la mot danh sach cluster
    subjects: list[list[str]] | None = None
    together: bool = False
    name: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    filters: dict[str, Any] | None = None

    def groups(self):
        if self.subjects:
            return [[str(x) for x in g if x] for g in self.subjects if g]
        ids = list(self.person_ids or [])
        if self.person_id and self.person_id not in ids:
            ids.insert(0, self.person_id)
        return [[str(i) for i in ids]] if ids else []


class MakeVideo(CreateProject):
    """Nhu CreateProject, nhung dung luon video thay vi dung lai o buoc chon anh."""
    options: dict[str, Any] | None = None


class Filters(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    date_from: str | None = None
    date_to: str | None = None


class Exclude(BaseModel):
    asset_id: str
    fidx: int
    excluded: bool = True


class RenderOptions(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)


# ----------------------------------------------------------------- health
@router.get("/health")
def health():
    s = get()
    ok, msg = db.indexer_ready()
    fok, fmsg = render.ffmpeg_ok()
    return {"indexer": {"ok": ok, "detail": msg},
            "ffmpeg": {"ok": fok, "detail": fmsg},
            "text": {"ok": textdraw.unicode_ok(), "detail": textdraw.backend()},
            "media_root": s.media_root,
            "media_src": s.media_src,
            "auth": bool(s.api_token),
            "rendering": render.current()}


@router.get("/progress")
def progress():
    """Tien do job indexer: bao nhieu anh da qua tung stage, dang chay stage nao."""
    return db.progress()


@router.get("/defaults")
def defaults():
    """Mac dinh cho UI. KHONG kem danh sach nhac.

    Truoc day tra ve ca music.available(): voi mot nghin bai la ~100KB nhoi vao
    moi lan tai trang, cho mot man hinh (buoc 4) ma co the nguoi dung khong mo.
    Danh sach gio lay rieng qua /api/music, co phan trang.
    """
    s = get()
    return {"filters": select.DEFAULTS, "render": render.DEFAULT_OPTIONS,
            "max_frames": s.max_frames,
            "music": {"configured": bool(s.music_dir),
                      "usage": music.usage(s), "sorts": list(music.SORTS)},
            "story": {**story.describe(), "text_backend": textdraw.backend(),
                      "unicode_text": textdraw.unicode_ok()}}


def _ranged(path: Path, media_type: str, rng: str | None):
    """Tra file kem ho tro HTTP Range, de <audio> keo thanh tua duoc.

    Viet tay chu khong dua vao FileResponse: ho tro Range cua starlette thay doi
    giua cac phien ban, va mot thanh tua khong keo duoc thi khong ai phat hien ra
    ngay — no chi lang le lam viec chon nhac tro thanh 'nghe het ca bai moi biet'.
    """
    try:
        size = path.stat().st_size
    except OSError:
        raise HTTPException(404, "no such track") from None
    start, end, status = 0, size - 1, 200
    headers = {"Accept-Ranges": "bytes",
               "Cache-Control": "private, max-age=3600"}
    if rng and rng.strip().startswith("bytes="):
        spec = rng.split("=", 1)[1].split(",")[0].strip()
        a, _, b = spec.partition("-")
        try:
            if a:
                start = int(a)
                if b:
                    end = min(int(b), size - 1)
            elif b:
                start = max(0, size - int(b))    # 'bytes=-N' = N byte cuoi
        except ValueError:
            start, end = 0, size - 1
        if start > end or start >= size:
            # 416 phai kem Content-Range de client biet do dai thuc.
            return Response(status_code=416,
                            headers={"Content-Range": f"bytes */{size}"})
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = end - start + 1
    headers["Content-Length"] = str(length)

    def body():
        with open(path, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(1 << 16, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(body(), status_code=status, headers=headers,
                             media_type=media_type)


def _music_page(s, q="", offset=0, limit=30, sort="name", folder=None):
    """Vo boc dung chung cho moi endpoint tra ve danh sach nhac.

    Luon tra ve DUNG MOT trang, khong bao gio ca thu vien: o muc mot nghin bai
    thi tra het la ~100KB JSON moi lan mo man hinh, va phia UI khong the dung
    mot <select> mot nghin dong.
    """
    total, items, folders = music.find(s, q, offset, limit, sort, folder)
    return {"configured": bool(s.music_dir), "total": total, "music": items,
            "folders": folders, "offset": offset, "limit": limit,
            "q": q, "sort": sort, "folder": folder, "usage": music.usage(s)}


@router.get("/music")
def list_music(q: str = Query("", max_length=120),
               offset: int = Query(0, ge=0),
               limit: int = Query(30, ge=1, le=200),
               sort: str = Query("name"),
               folder: str | None = Query(None)):
    """Mot trang danh sach nhac. configured=false nghia la chua cau hinh MUSIC_DIR.

    'name' la thu gui lai trong render options: {"music": "cham/piano-01.mp3"}.
    Chi tra ve duong dan TUONG DOI — duong dan tuyet doi tren server khong phai
    viec cua client, va nhan lai duong dan tuyet doi tu client la mot lo hong.

    Tim kiem BO DAU o ca hai phia, vi ten file o day la tieng Viet co dau ma go
    co dau vao o tim kiem thi bat tien den muc o do thanh vo dung.
    """
    s = get()
    if sort not in music.SORTS:
        sort = "name"
    return _music_page(s, q, offset, limit, sort, folder or None)


@router.get("/music/random")
def random_music(q: str = Query("", max_length=120),
                 folder: str | None = Query(None)):
    """Chon ngau nhien mot bai TRONG TAP DA LOC.

    Chon o phia server chu khong phia client, de khong phai tai ca nghin dong ve
    chi de bo di tat ca tru mot. Voi mot nghin bai va mot video ky niem thi
    'chon ho toi mot bai' thuong la thu dung hon la ngoi can mot nghin lua chon.
    """
    s = get()
    total, _items, _f = music.find(s, q, 0, 1, "name", folder or None)
    if not total:
        raise HTTPException(404, "no track matches")
    _t, page, _f2 = music.find(s, q, random.randrange(total), 1, "name",
                               folder or None)
    if not page:
        raise HTTPException(404, "no track matches")
    return {"pick": page[0], "of": total}


@router.get("/music/meta/{name:path}")
def music_meta(name: str, bpm: bool = Query(False)):
    """Do dai, va BPM khi bpm=1.

    BPM tach ra sau mot co rieng vi no DAT: giai ma 120 giay + FFT, khoang 1-3
    giay moi bai. Lam viec do cho ca mot trang danh sach la treo request; cho ca
    nghin bai la 20-50 phut CPU tranh voi Immich. Nen chi do khi nguoi dung thuc
    su chon mot bai.
    """
    s = get()
    m = music.meta(s, name, want_bpm=bool(bpm))
    if m is None:
        raise HTTPException(404, "no such track")
    return m


@router.get("/music/file/{name:path}")
def play_music(name: str, request: Request):
    """Phat thu mot bai trong trinh duyet, co ho tro Range.

    Range khong phai tuy chon: khong co no thi thanh tua cua <audio> khong keo
    duoc, va chon nhac ma khong nhay den doan giua bai thi phai nghe het ca bai
    moi biet no the nao.
    """
    s = get()
    p = music.resolve(s, name)
    if p is None:
        raise HTTPException(404, "no such track")
    return _ranged(p, music.mime(p), request.headers.get("range"))


@router.post("/music")
def upload_music(files: list[UploadFile] = File(...)):
    """Tai mot hoac NHIEU ban nhac len MUSIC_DIR.

    Nhieu file mot lan vi mot file mot lan la khong dung duoc khi ban co ca mot
    thu muc nhac. Tra ve ket qua TUNG FILE: mot file loi khong duoc phep lam mat
    nhung file da vao duoc.

    Kich thuoc lay CHINH XAC tu than request da duoc starlette spool ra dia,
    khong doan qua Content-Length: header do do ca phan boundary cua multipart
    nen mot file dung bang tran se bi tu choi oan.

    Voi ca mot kho nhac thi upload qua trinh duyet VAN la sai cong cu — mount
    read-only thu muc co san, hoac rsync mot lan, la dung hon. Cho nay danh cho
    'toi tim duoc mot bai, thu xem the nao'.
    """
    s = get()
    if not s.music_dir:
        raise HTTPException(409, "MUSIC_DIR is not configured on the server")

    done, failed = [], []
    for f in files:
        declared = 0
        try:
            f.file.seek(0, 2)
            declared = f.file.tell()
            f.file.seek(0)
        except (OSError, AttributeError):
            declared = 0

        def chunks(fh=f.file):
            while True:
                b = fh.read(1 << 20)
                if not b:
                    break
                yield b

        try:
            name, n = music.save(s, f.filename, chunks(), declared)
            done.append({"name": name, "size": n})
        except music.MusicError as e:
            failed.append({"filename": f.filename, "error": str(e)})
        finally:
            try:
                f.file.close()
            except OSError:
                pass

    if not done and failed:
        raise HTTPException(400, "; ".join(x["error"] for x in failed))
    out = _music_page(s, "", 0, 30, "newest", None)
    out.update(uploaded=done, failed=failed)
    return out


@router.delete("/music/{name:path}")
def delete_music(name: str):
    """Xoa mot ban nhac. Di qua dung ham resolve() nhu luc doc, nen khong co
    duong nao xoa duoc file ngoai MUSIC_DIR.

    Co endpoint nay vi khong co no thi upload la mot chieu: them duoc ma khong bo
    duoc, va lai phai ssh vao may — dung cai viec ma upload sinh ra de tranh.
    """
    s = get()
    if not s.music_dir:
        raise HTTPException(409, "MUSIC_DIR is not configured on the server")
    if not music.delete(s, name):
        raise HTTPException(404, "no such track")
    out = _music_page(s, "", 0, 30, "name", None)
    out["deleted"] = name
    return out


# ----------------------------------------------------------------- buoc 1
@router.get("/people")
def list_people(min_ready: int = Query(3, ge=1, le=1000)):
    return {"people": people.listing(min_ready)}


@router.get("/people/{person_id}")
def person_detail(person_id: str):
    return people.detail(person_id)


@router.get("/people/{person_id}/similar")
def similar_people(person_id: str,
                   limit: int = Query(24, ge=1, le=100),
                   min_sim: float = Query(0.25, ge=0.0, le=1.0),
                   seeds: str = Query("", description="cac cluster da chon, "
                                                     "cach nhau bang dau phay")):
    """Cac cluster co ve cung mot nguoi, do bang cosine giua vector trung tam.

    Truyen seeds= danh sach cluster da chon de lan rong dan: goi y se so voi
    trung tam gop cua ca nhom thay vi chi mot cluster.
    """
    ids = [x.strip() for x in seeds.split(",") if x.strip()] or None
    return people.similar(person_id, limit=limit, min_sim=min_sim, seeds=ids)


# ----------------------------------------------------------------- buoc 2, 3
@router.get("/projects")
def list_projects():
    return {"projects": projects.listing()}


@router.post("/projects")
def create_project(body: CreateProject):
    grps = body.groups()
    if not grps:
        raise HTTPException(400, "person_id, person_ids or subjects is required")
    try:
        pid, res = projects.create(grps, body.name, body.date_from,
                                   body.date_to, body.filters, body.together)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"project_id": pid, **_slim(res)}


@router.post("/videos")
def make_video(body: MakeVideo):
    """Duong mot buoc: chon nguoi -> co video. Khong setup gi.

    Tao du an, tu suy nguong loc, chia chuong, tu suy do dai, roi bat dau dung
    ngay trong cung mot request. Tra ve render_id=None khi khong du 2 anh — de UI
    dua nguoi dung sang cho noi nguong thay vi render bua roi bao loi ffmpeg.
    """
    grps = body.groups()
    if not grps:
        raise HTTPException(400, "person_id, person_ids or subjects is required")
    try:
        pid, res = projects.create(grps, body.name, body.date_from,
                                   body.date_to, body.filters, body.together)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    out = {"project_id": pid, "render_id": None, **_slim(res, False)}
    if res["n_selected"] < 2:
        out["detail"] = (f"only {res['n_selected']} photos were selected, at least "
                         f"2 are needed — loosen the thresholds and try again")
        return out
    try:
        out["render_id"] = render.start(pid, body.options)
    except RuntimeError as e:                  # dang co render khac chay
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        out["detail"] = str(e)
    return out


@router.get("/projects/{project_id}")
def get_project(project_id: int):
    try:
        return projects.get_project(project_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.delete("/projects/{project_id}")
def delete_project(project_id: int):
    projects.delete(project_id)
    return {"deleted": project_id}


@router.get("/projects/{project_id}/result")
def project_result(project_id: int,
                   include_rejected: bool = Query(True)):
    try:
        p = projects.get_project(project_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    # subjects, khong phai person_id: du an co the gop nhieu cluster cua cung
    # mot nguoi, hoac gom nhieu nguoi — lay mot cluster la mat phan lon anh.
    cands = select.fetch(p["subjects"], p["date_from"], p["date_to"],
                         p["together"],
                         (p["filters"] or {}).get("use_clips", True))
    res = select.apply(cands, p["filters"], projects.excluded_keys(project_id))
    return _slim(res, include_rejected)


@router.patch("/projects/{project_id}/filters")
def patch_filters(project_id: int, body: Filters):
    try:
        res = projects.recompute(project_id, body.filters, body.date_from,
                                 body.date_to)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    return _slim(res)


@router.post("/projects/{project_id}/exclude")
def exclude(project_id: int, body: Exclude):
    projects.set_excluded(project_id, body.asset_id, body.fidx, body.excluded)
    res = projects.recompute(project_id)
    return _slim(res, include_rejected=False)


@router.post("/projects/{project_id}/rename")
def rename(project_id: int, name: str = Query(min_length=1, max_length=120)):
    projects.rename(project_id, name)
    return {"ok": True}


# ----------------------------------------------------------------- anh
@router.get("/thumb/{asset_id}/{fidx}")
def thumb(asset_id: str, fidx: int, size: int = Query(160, ge=48, le=512)):
    """fidx >= 0 la khuon mat trong anh; fidx AM la doan video thu (-1-fidx)."""
    p = thumbs.face_thumb(asset_id, fidx, size)
    if p is None:
        raise HTTPException(404, "could not read the preview image")
    return FileResponse(p, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/aligned/{asset_id}/{fidx}")
def aligned(asset_id: str, fidx: int,
            size: int = Query(256, ge=64, le=1024),
            aspect: str = Query("4:3"),
            face_frac: float = Query(0.12, ge=0.04, le=0.70),
            eye_y: float = Query(0.33, ge=0.15, le=0.80),
            fill: str = Query("crop", pattern="^(crop|blur)$"),
            level: bool = Query(True)):
    """Xem truoc dung khung se render. Tham so khop voi buoc 4."""
    p = thumbs.aligned_preview(asset_id, fidx, size, aspect=aspect,
                               face_frac=face_frac, eye_y=eye_y,
                               fill=fill, level=level)
    if p is None:
        raise HTTPException(404, "could not align this photo")
    return FileResponse(p, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


# ----------------------------------------------------------------- buoc 4
@router.post("/projects/{project_id}/storyboard")
def storyboard(project_id: int, body: RenderOptions):
    """Cau chuyen se ra sao: bao nhieu chuong, moi shot dai bao nhieu giay.

    Tinh dung bang thuat toan ma render dung, nen con so thoi luong o day la
    con so that — khong phai uoc luong. Goi truoc khi render de khoi dung mot
    video 4 phut roi moi phat hien.
    """
    try:
        return render.plan_for(project_id, body.options)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/projects/{project_id}/render")
def start_render(project_id: int, body: RenderOptions):
    try:
        rid = render.start(project_id, body.options)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"render_id": rid}


@router.get("/projects/{project_id}/renders")
def list_renders(project_id: int):
    return {"renders": render.listing(project_id)}


@router.get("/renders/{render_id}")
def render_status(render_id: int):
    try:
        return render.status(render_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/renders/{render_id}/video")
def render_video(render_id: int):
    p = render.video_path(render_id)
    if p is None:
        raise HTTPException(404, "the video is not ready yet")
    return FileResponse(p, media_type="video/mp4", filename=f"timeline-{render_id}.mp4")


@router.post("/cache/purge")
def purge(days: int = Query(30, ge=0, le=3650)):
    return {"deleted": thumbs.purge(days)}


# ----------------------------------------------------------------- helpers
_KEEP = ("asset_id", "fidx", "fidx2", "taken_at", "ord", "bucket", "label",
         "hero", "n_subject", "score", "reason",
         "kind", "dur_s", "motion", "t_start_ms", "t_end_ms", "t_peak_ms",
         "yaw", "pitch", "roll", "frontality", "ear", "eye_ratio", "sharp",
         "bright", "quality", "age", "n_face", "n_body", "posture",
         "orientation", "body_front", "filename")


def _row(r):
    out = {}
    for k in _KEEP:
        v = r.get(k)
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        elif isinstance(v, float):
            v = round(v, 4)
        out[k] = v
    out["key"] = f"{r['asset_id']}:{r['fidx']}"
    return out


def _slim(res, include_rejected=True):
    out = {k: res[k] for k in ("filters", "n_candidate", "n_pass", "n_selected",
                               "n_rejected", "reasons", "timeline", "gaps",
                               "story")}
    out["selected"] = [_row(r) for r in res["selected"]]
    if include_rejected:
        # sap theo diem giam dan: anh "gan dat" nam tren, de nguoi dung
        # biet nen noi long nguong nao
        rej = sorted(res["rejected"], key=lambda r: -(r.get("score") or 0))
        out["rejected"] = [_row(r) for r in rej[:400]]
    return out
