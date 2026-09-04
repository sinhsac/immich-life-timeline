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
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
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
    return {"filters": select.DEFAULTS, "render": render.DEFAULT_OPTIONS,
            "max_frames": get().max_frames,
            "music": music.available(get()),
            "story": {**story.describe(), "text_backend": textdraw.backend(),
                      "unicode_text": textdraw.unicode_ok()}}


@router.get("/music")
def list_music():
    """Cac ban nhac trong MUSIC_DIR. Rong nghia la chua cau hinh MUSIC_DIR.

    'name' la thu gui lai trong render options: {"music": "cham/piano-01.mp3"}.
    Chi tra ve duong dan TUONG DOI — duong dan tuyet doi tren server khong phai
    viec cua client, va nhan lai duong dan tuyet doi tu client la mot lo hong.
    """
    s = get()
    return {"configured": bool(s.music_dir), "music": music.available(s)}


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
