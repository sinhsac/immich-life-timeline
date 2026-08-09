"""REST API. Bon nhom endpoint tuong ung bon buoc cua man hinh.

  GET    /api/people                     chon nguoi
  GET    /api/people/{pid}               phan bo anh theo nam
  POST   /api/projects                   tao du an -> tu dong chon anh
  GET    /api/projects/{id}/result       ket qua loc hien tai (+ ly do loai)
  PATCH  /api/projects/{id}/filters      tinh chinh nguong -> tinh lai ngay
  POST   /api/projects/{id}/exclude      bo / lay lai mot anh bang tay
  POST   /api/projects/{id}/render       dung video
  GET    /api/renders/{id}               tien do
  GET    /api/renders/{id}/video         tai mp4

  GET    /api/progress                   tien do job indexer (ngoai 4 buoc)
"""
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import db, people, projects, render, select, thumbs
from .settings import get

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------- schemas
class CreateProject(BaseModel):
    # person_id: mot cluster (cach cu, van dung duoc)
    # person_ids: nhieu cluster cua cung mot nguoi -> gop lai thanh mot video
    person_id: str | None = None
    person_ids: list[str] | None = None
    name: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    filters: dict[str, Any] | None = None

    def clusters(self):
        ids = list(self.person_ids or [])
        if self.person_id and self.person_id not in ids:
            ids.insert(0, self.person_id)
        return ids


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
            "max_frames": get().max_frames}


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
    ids = body.clusters()
    if not ids:
        raise HTTPException(400, "can person_id hoac person_ids")
    try:
        pid, res = projects.create(ids, body.name, body.date_from,
                                   body.date_to, body.filters)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"project_id": pid, **_slim(res)}


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
    cands = select.fetch(p["person_id"], p["date_from"], p["date_to"])
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
    p = thumbs.face_thumb(asset_id, fidx, size)
    if p is None:
        raise HTTPException(404, "khong doc duoc anh preview")
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
        raise HTTPException(404, "khong align duoc anh nay")
    return FileResponse(p, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


# ----------------------------------------------------------------- buoc 4
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
        raise HTTPException(404, "video chua san sang")
    return FileResponse(p, media_type="video/mp4", filename=f"timeline-{render_id}.mp4")


@router.post("/cache/purge")
def purge(days: int = Query(30, ge=0, le=3650)):
    return {"deleted": thumbs.purge(days)}


# ----------------------------------------------------------------- helpers
_KEEP = ("asset_id", "fidx", "taken_at", "ord", "bucket", "score", "reason",
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
                               "n_rejected", "reasons", "timeline", "gaps")}
    out["selected"] = [_row(r) for r in res["selected"]]
    if include_rejected:
        # sap theo diem giam dan: anh "gan dat" nam tren, de nguoi dung
        # biet nen noi long nguong nao
        rej = sorted(res["rejected"], key=lambda r: -(r.get("score") or 0))
        out["rejected"] = [_row(r) for r in rej[:400]]
    return out
