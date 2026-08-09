# fp-indexer — index head pose + body pose của ảnh Immich vào Postgres

Job độc lập, chạy một lệnh, ghi vào **bảng riêng có prefix** trong chính database
của Immich. Bảng của Immich chỉ đọc, không bao giờ bị ghi.

Khác với `../pipeline` (SQLite, chạy tay trên máy dev), folder này thiết kế để
deploy như Job/CronJob trên k3s cùng node với Immich.

## Làm gì

| Stage | Việc | Model | Ghi vào |
|---|---|---|---|
| 1 `assets` | Đọc danh sách ảnh + ngày chụp + đường dẫn preview | — | `fp_asset` |
| 2 `faces` | Copy bbox + embedding + person đã gán | — | `fp_face` |
| 3 `landmarks` | 68 điểm 3D → yaw/pitch/roll, EAR, age, chỉ số chất lượng | `buffalo_l` / 1k3d68 + genderage | `fp_face` |
| 4 `bodies` | Detect người + 17 keypoint COCO → tư thế, hướng, góc thân | `yolov8n-pose.onnx` | `fp_body` |

Stage 2 lấy sẵn bbox + embedding từ Immich nên **bỏ được SCRFD và ArcFace** —
hai model tốn nhất. Chỉ 1k3d68 và yolov8n-pose phải chạy thật.

## Vì sao tách hai loại pose

`buffalo_l` chỉ cho **head pose** (hướng quay của đầu). Muốn biết người đang
đứng/ngồi/nằm, quay lưng hay chính diện thì cần model body pose riêng —
ở đây là `yolov8n-pose`, chọn bản nano vì máy đích 8GB RAM / i5 không GPU.

## Cấu hình

Toàn bộ qua biến môi trường, xem `.env.example`. Bắt buộc:

- `PG_PASSWORD` — mật khẩu Postgres của Immich
- `MEDIA_ROOT` — đường dẫn tới `UPLOAD_LOCATION` của Immich (mount read-only),
  hoặc `IMMICH_URL` + `IMMICH_API_KEY` nếu không mount được volume

Đọc file trực tiếp nhanh hơn nhiều và không tạo tải cho Immich server.

## Chuẩn bị model

Chạy một lần trên máy có mạng, rồi copy thư mục `models/` sang máy đích:

```bash
pip install ultralytics            # chỉ cần để export yolov8n-pose
python tools/fetch_models.py --all --out ./models
```

Kết quả:

```
models/
  models/buffalo_l/1k3d68.onnx  genderage.onnx  det_10g.onnx  w600k_r50.onnx
  yolov8n-pose.onnx
```

## Chạy

```bash
pip install -r requirements.txt

python job.py --dry-run          # kiểm tra pg + ảnh + model, không ghi gì
python job.py                    # chạy cả 4 stage tuần tự
python job.py --stage bodies     # chạy riêng một stage
python job.py --stats            # xem tiến độ
python job.py --reset errors     # thử lại các ảnh lỗi đọc
```

Job **resumable**: state nằm trong cột `face_state` / `body_state` của
`fp_asset`, commit theo lô `BATCH_COMMIT`. Sập giữa đường thì chạy lại là tiếp
tục chỗ cũ, không làm lại từ đầu. Nhận SIGTERM thì dừng gọn sau khi commit lô
đang chạy — an toàn khi k8s evict.

## Chạy nhẹ để không đè Immich

Cùng node với Immich trên máy 8GB nên job cố tình chạy chậm và gọn:

- Tuần tự hoàn toàn, không thread, không process pool
- **Chỉ một model trong RAM tại một thời điểm** — stage 3 giải phóng model
  trước khi stage 4 load model khác. RAM peak ~700MB
- `ONNX_THREADS=2` giới hạn cả onnxruntime lẫn BLAS
- `SLEEP_MS` nghỉ giữa mỗi ảnh để nhường CPU
- `MAX_SIDE=1600` hạ ảnh preview trước khi infer (bbox lưu chuẩn hoá 0..1 nên
  không sai lệch)
- `resources.limits` trong `deploy/k3s.yaml` chặn 2 CPU / 2GB RAM

## Deploy k3s

```bash
kubectl create ns media
kubectl -n media create secret generic immich-db --from-literal=password='...'
docker build -f deploy/Dockerfile -t fp-indexer:1.0.0 .
kubectl apply -f deploy/k3s.yaml
kubectl -n media create job --from=cronjob/fp-indexer fp-run-1   # chạy tay ngay
```

Sửa hai `hostPath` trong `deploy/k3s.yaml` cho đúng máy bạn: thư mục model và
thư mục upload của Immich. Job dùng `hostNetwork: true` để gọi Postgres của
Immich qua `127.0.0.1` (Immich chạy bằng docker compose, ngoài k3s).

`concurrencyPolicy: Forbid` đảm bảo không bao giờ có hai job chạy cùng lúc.

## Bảng kết quả

Prefix mặc định `fp_`, đổi bằng `TABLE_PREFIX`.

**`fp_asset`** — một dòng mỗi ảnh: `taken_at`, `date_src`, `preview_path`,
`n_face`, `n_body`, `face_state`, `body_state`, `err`.

**`fp_face`** — một dòng mỗi khuôn mặt:

- Từ Immich: `immich_face`, `person_id`, `person_name`, `x1..y2` (0..1), `emb`, `emb_norm`
- Từ 1k3d68: `yaw`, `pitch`, `roll`, `frontality`, `ear`, `age`, `kps`, `lmk68`
- Chỉ số ảnh: `eye_px`, `sharp`, `bright`, `symm`, `quality`

**`fp_body`** — một dòng mỗi người:

- `x1..y2` (0..1), `det`, `kps` (float32[17][3] chuẩn hoá, 204 byte)
- `orientation` front/back/side/unknown
- `posture` standing/sitting/lying/unknown
- `torso_deg` góc thân so với trục dọc, `body_front` 0..1
- `face_fidx` khớp với `fp_face.fidx` nếu tìm được

**`fp_run`** — log mỗi lần chạy stage. **`fp_state`** — checkpoint.

`emb`, `kps`, `lmk68`, `kps` là `bytea` float32 little-endian. Đọc lại:

```python
np.frombuffer(row["kps"], np.float32).reshape(17, 3)
```

## Truy vấn ví dụ

```sql
-- ảnh có đúng một người, đứng chính diện, mặt nét, mắt mở
SELECT a.id, a.taken_at, f.quality, b.posture
FROM fp_asset a
JOIN fp_face f ON f.asset_id = a.id AND f.state = 1
JOIN fp_body b ON b.asset_id = a.id AND b.face_fidx = f.fidx
WHERE a.n_body = 1
  AND f.frontality > 0.6 AND f.ear > 0.16 AND f.sharp > 120
  AND b.posture = 'standing' AND b.orientation = 'front'
ORDER BY a.taken_at;

-- phân bố tư thế theo năm
SELECT date_part('year', a.taken_at) y, b.posture, COUNT(*)
FROM fp_body b JOIN fp_asset a ON a.id = b.asset_id
GROUP BY y, b.posture ORDER BY y;
```

## Giới hạn đã biết

- Pose từ 1k3d68 là khớp affine 3D-3D với hình dáng trung bình của model, không
  phải PnP có hiệu chuẩn camera. Đủ chính xác để lọc góc, không dùng để đo.
- EAR tính từ 68 điểm 3D chiếu về 2D nên chỉ là tín hiệu gợi ý — dùng để gán cờ,
  đừng dùng để loại thẳng.
- `posture` suy từ tỉ lệ hình học, ảnh nửa người (không thấy chân) sẽ đoán theo
  độ dọc của thân, có thể sai.
- Đổi `FACE_MODEL` thì phải `--reset landmarks`; embedding từ Immich vẫn dùng
  được vì Immich cũng chạy ArcFace của `buffalo_l`.

## Kiểm thử

Trên máy dev không có Immich/Postgres/model thì chỉ verify được phần logic
thuần (metrics, bodyfeat, sinh SQL). Phần còn lại verify trên máy đích:

```bash
python job.py --dry-run
```

Lệnh này kiểm tra kết nối Postgres, đọc thử ảnh preview, load thử cả hai model,
rồi thoát mà không ghi gì.
