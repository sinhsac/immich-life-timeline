"""Doc du lieu tu cac bang cua Immich. CHI SELECT, khong bao gio ghi.

Immich doi ten bang giua cac phien ban (asset/assets, asset_exif/exif,
asset_file/asset_files, asset_face/asset_faces). Module nay do ten bang mot lan
roi cache lai, thay vi hardcode.

Phan trang bang keyset (id > last_id) chu khong OFFSET: chay on dinh tren bang
lon va khong can server-side cursor nen tuong thich ca psycopg2 lan psycopg3.
"""
_ASSET = ("asset", "assets")
_EXIF = ("asset_exif", "exif")
_FILE = ("asset_file", "asset_files")
_FACE = ("asset_face", "asset_faces")
_PERSON = ("person", "persons")
_SEARCH = "face_search"


def _exists(cur, schema, name):
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema=%s AND table_name=%s", (schema, name))
    return cur.fetchone() is not None


def _pick(cur, schema, candidates, what):
    for n in candidates:
        if _exists(cur, schema, n):
            return n
    raise SystemExit(
        f"Khong tim thay bang {what} cua Immich trong schema {schema}. "
        f"Da thu: {', '.join(candidates)}")


class Tables:
    """Ten bang thuc te cua Immich tren instance dang chay."""

    def __init__(self, conn, schema="public"):
        self.schema = schema
        with conn.cursor() as cur:
            self.asset = _pick(cur, schema, _ASSET, "asset")
            self.exif = _pick(cur, schema, _EXIF, "exif")
            self.file = _pick(cur, schema, _FILE, "asset_file")
            self.face = _pick(cur, schema, _FACE, "asset_face")
            self.person = _pick(cur, schema, _PERSON, "person")
            self.search = _SEARCH if _exists(cur, schema, _SEARCH) else None
        conn.rollback()

    def q(self, name):
        return f'{self.schema}."{name}"'

    def describe(self):
        return ("immich: " + ", ".join([self.asset, self.exif, self.file,
                                        self.face, self.person])
                + (f", {self.search}" if self.search
                   else "  (KHONG co face_search -> se khong copy embedding)"))


def _date_expr():
    return 'COALESCE(e."dateTimeOriginal", a."localDateTime", a."fileCreatedAt")'


def asset_page(t, after_id=None, taken_after=None, taken_before=None, limit=1000):
    """Mot trang asset: id, ten, ngay, kich thuoc, duong dan preview."""
    where = ["a.type = 'IMAGE'", 'a."deletedAt" IS NULL']
    params = []
    if after_id:
        where.append("a.id > %s::uuid")
        params.append(after_id)
    if taken_after:
        where.append(f"{_date_expr()} >= %s::timestamptz")
        params.append(taken_after)
    if taken_before:
        where.append(f"{_date_expr()} <= %s::timestamptz")
        params.append(taken_before)
    sql = f"""
SELECT a.id,
       a."originalFileName",
       e."dateTimeOriginal",
       a."localDateTime",
       a."fileCreatedAt",
       e."exifImageWidth",
       e."exifImageHeight",
       pf.path
FROM {t.q(t.asset)} a
LEFT JOIN {t.q(t.exif)} e ON e."assetId" = a.id
LEFT JOIN LATERAL (
    SELECT f.path FROM {t.q(t.file)} f
    WHERE f."assetId" = a.id AND f.type = 'preview'
    ORDER BY f."isEdited" DESC NULLS LAST
    LIMIT 1
) pf ON true
WHERE {' AND '.join(where)}
ORDER BY a.id
LIMIT {int(limit)}
"""
    return sql, params


def count_assets(conn, t, taken_after=None, taken_before=None):
    where = ["a.type = 'IMAGE'", 'a."deletedAt" IS NULL']
    params = []
    if taken_after:
        where.append(f"{_date_expr()} >= %s::timestamptz")
        params.append(taken_after)
    if taken_before:
        where.append(f"{_date_expr()} <= %s::timestamptz")
        params.append(taken_before)
    sql = (f"SELECT COUNT(*) FROM {t.q(t.asset)} a "
           f"LEFT JOIN {t.q(t.exif)} e ON e.\"assetId\" = a.id "
           f"WHERE {' AND '.join(where)}")
    with conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.fetchone()[0]
    conn.rollback()
    return n


def faces_for(t, with_embedding=True):
    """bbox + personId (+ embedding neu instance co bang face_search).

    Immich luu embedding CHUA chuan hoa L2 -> chuan hoa o phia job, dong thoi
    giu lai do dai goc lam tin hieu chat luong (y tuong MagFace).
    """
    use_emb = bool(with_embedding and t.search)
    emb = "fs.embedding" if use_emb else "NULL"
    join = (f'LEFT JOIN {t.q(t.search)} fs ON fs."faceId" = af.id'
            if use_emb else "")
    return f"""
SELECT af."assetId", af.id, af."imageWidth", af."imageHeight",
       af."boundingBoxX1", af."boundingBoxY1",
       af."boundingBoxX2", af."boundingBoxY2",
       af."personId", p.name, {emb}
FROM {t.q(t.face)} af
LEFT JOIN {t.q(t.person)} p ON p.id = af."personId"
{join}
WHERE af."deletedAt" IS NULL AND af."assetId" = ANY(%s::uuid[])
ORDER BY af."assetId", af.id
"""
