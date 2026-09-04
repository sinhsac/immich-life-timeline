"""Detect + nhan dang khuon mat tren frame video: SCRFD (det_10g) + ArcFace.

Day la ngoai le duy nhat cua nguyen tac "khong lam lai viec Immich da lam", va
no co ly do cu the: Immich chi chay face detection cho video tren DUNG MOT frame
thumbnail. Biet mot clip co ong A la du de liet ke, nhung khong du de cat ra
"doan dep nhat co ong A" — muon biet ong A xuat hien o giay thu bao nhieu, mat
to nho ra sao, co nhin vao may khong, thi phai tu quet.

Diem nhe nhom: hai model nay DA NAM SAN tren dia. `fetch_models.py --face` tai
ca bo buffalo_l gom 1k3d68, genderage, det_10g VA w600k_r50 — hai file cuoi truoc
gio tai ve roi khong dung. Khong phai tai them gi, khong phai doi Dockerfile.

Cung dung model recognition voi Immich (w600k_r50 cua buffalo_l) va cung template
5 diem, nen vector sinh ra o day so sanh truc tiep duoc voi emb da copy tu Immich
trong fp_face. Do la co so de gan person_id cho mat trong video.
"""
import os
from pathlib import Path

import numpy as np


class FaceDetector:
    """Detect + embed. Mot instance giu hai session onnx, ~200MB RAM."""

    def __init__(self, s):
        self.s = s
        os.environ.setdefault("INSIGHTFACE_HOME", s.model_dir)
        Path(s.model_dir).mkdir(parents=True, exist_ok=True)
        self.app = self._build()
        self.det = self.app.models.get("detection")
        self.rec = self.app.models.get("recognition")
        if self.det is None:
            raise SystemExit(
                f"Bo model {s.face_model} khong co detection.\n"
                f"Kiem tra {s.model_dir}/models/{s.face_model}/det_10g.onnx")
        if self.rec is None:
            raise SystemExit(
                f"Bo model {s.face_model} khong co recognition (w600k_r50.onnx).\n"
                f"Chay: python tools/fetch_models.py --face --out {s.model_dir}")

    def _build(self):
        import onnxruntime as ort
        from insightface.app import FaceAnalysis
        ort.set_default_logger_severity(3)
        providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                     if self.s.use_gpu else ["CPUExecutionProvider"])
        app = FaceAnalysis(name=self.s.face_model, root=self.s.model_dir,
                          providers=providers,
                          allowed_modules=["detection", "recognition"])
        d = max(160, int(self.s.video_det_size))
        app.prepare(ctx_id=0 if self.s.use_gpu else -1,
                    det_size=(d, d), det_thresh=float(self.s.video_det_conf))
        return app

    def __call__(self, frame, embed=True):
        """Tra ve list dict: bbox px, kps px (5,2), det, emb chuan hoa L2, emb_norm.

        emb_norm la do dai vector TRUOC khi chuan hoa. Chuan hoa xong thi thong
        tin do mat, ma no lai la tin hieu chat luong dung duoc: ArcFace cho vector
        ngan tren mat mo / bi che. fp_face cung luu cot nay nen hai ben so duoc.
        """
        try:
            faces = self.app.get(frame) if embed else self.det.detect(frame)
        except Exception:                                 # noqa: BLE001
            return []
        out = []
        for f in faces:
            box = np.asarray(getattr(f, "bbox", None), np.float32)
            kps = getattr(f, "kps", None)
            if box is None or box.size < 4 or kps is None:
                continue
            norm = None
            raw = getattr(f, "embedding", None)
            if raw is not None:
                raw = np.asarray(raw, np.float32)
                norm = float(np.linalg.norm(raw))
            emb = getattr(f, "normed_embedding", None)
            if emb is None and raw is not None:
                emb = raw / max(norm or 0.0, 1e-6)
            out.append({
                "bbox": box[:4],
                "kps": np.asarray(kps, np.float32)[:5],
                "det": float(getattr(f, "det_score", 0.0) or 0.0),
                "emb": None if emb is None else np.asarray(emb, np.float32),
                "emb_norm": norm,
            })
        out.sort(key=lambda r: -(r["bbox"][2] - r["bbox"][0])
                 * (r["bbox"][3] - r["bbox"][1]))
        return out

    def close(self):
        self.det = self.rec = None
        self.app = None


class PersonIndex:
    """Vector trung tam cua tung person, dung de gan ten cho mat trong video.

    Lay tu fp_face.emb — chinh la embedding Immich da tinh, da chuan hoa L2 o
    stage faces. Chi lay per_person mat diem cao nhat moi person: du de tam on
    dinh ma khong phai keo ca tram nghin vector qua duong day.
    """

    def __init__(self, conn, s):
        self.ids, self.mat, self.names = self._load(conn, s)
        self.sim_min = float(s.video_sim)
        self.margin = float(s.video_margin)

    @staticmethod
    def _load(conn, s):
        sql = f"""
        SELECT person_id, person_name, emb FROM (
            SELECT f.person_id, f.person_name, f.emb,
                   row_number() OVER (PARTITION BY f.person_id
                                      ORDER BY f.quality DESC NULLS LAST) rn
            FROM {s.table('face')} f
            WHERE f.person_id IS NOT NULL AND f.emb IS NOT NULL AND f.state = 1
        ) t WHERE rn <= %s
        """
        acc, cnt, names, dim = {}, {}, {}, None
        with conn.cursor() as cur:
            cur.execute(sql, (int(s.video_centroid_per),))
            for pid, pname, blob in cur:
                v = np.frombuffer(blob, np.float32)
                if dim is None:
                    dim = v.size
                if v.size != dim or v.size == 0:
                    continue
                key = str(pid)
                if pname and key not in names:
                    names[key] = pname
                if key in acc:
                    acc[key] += v
                    cnt[key] += 1
                else:
                    acc[key] = v.astype(np.float64)
                    cnt[key] = 1
        conn.rollback()
        ids = sorted(acc)
        if not ids:
            return [], np.zeros((0, 0), np.float32), {}
        mat = np.stack([acc[p] / cnt[p] for p in ids])
        mat /= np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9)
        return ids, mat.astype(np.float32), names

    def name_of(self, pid):
        return self.names.get(str(pid)) if pid else None

    def __len__(self):
        return len(self.ids)

    def match(self, emb):
        """(person_id, sim, sim2) — None neu khong du chac.

        Hai dieu kien, khong phai mot: cosine >= nguong VA cach person xep thu
        hai mot khoang margin. Nguoi than co net giong nhau dat 0.43-0.45 tren
        thu vien that, nen chi mot nguong tuyet doi la gan bua.
        """
        if emb is None or not self.ids or self.mat.size == 0:
            return None, None, None
        if emb.shape[0] != self.mat.shape[1]:
            return None, None, None
        sims = self.mat @ emb
        order = np.argsort(-sims)
        best = float(sims[order[0]])
        second = float(sims[order[1]]) if len(order) > 1 else -1.0
        if best < self.sim_min or (best - second) < self.margin:
            return None, best, second
        return self.ids[order[0]], best, second
