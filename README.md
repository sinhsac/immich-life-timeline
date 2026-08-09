# Video hành trình một người từ thư viện Immich

Hai thành phần, chạy trên k3s cùng node với Immich. Dùng chung database
PostgreSQL của Immich nhưng bảng riêng prefix `fp_` — bảng của Immich chỉ đọc.

| Thư mục | Loại | Việc |
|---|---|---|
| [`indexer/`](indexer/) | CronJob | Quét toàn bộ ảnh Immich, index head pose + body pose vào Postgres |
| [`timeline/`](timeline/) | Deployment | Web UI 4 bước: chọn người → lấy ảnh → tinh chỉnh pose → ffmpeg dựng video |

## Luồng

```
Immich đã chạy Face Detection + Facial Recognition
        │
        ├─ indexer (chạy 1 lần, rồi CronJob cho ảnh mới)
        │    stage assets     → fp_asset    danh sách ảnh + ngày chụp
        │    stage faces      → fp_face     bbox + embedding từ Immich
        │    stage landmarks  → fp_face     yaw/pitch/roll, EAR, chất lượng
        │    stage bodies     → fp_body     17 keypoint, tư thế, hướng thân
        │
        └─ timeline (service thường trú)
             1 chọn người      từ cluster Immich đã phân loại
             2 lấy ảnh         rải đều theo thời gian
             3 tinh chỉnh      lọc pose, mỗi ảnh loại có lý do
             4 dựng video      align khuôn mặt + ffmpeg → mp4
```

## Bắt đầu

```bash
# 1. chuẩn bị model (một lần, trên máy có mạng)
cd indexer
python tools/fetch_models.py --all --out ./models

# 2. index
python job.py --dry-run       # kiểm tra pg / ảnh / model
python job.py                 # chạy 4 stage tuần tự, resumable

# 3. service
cd ../timeline
python app.py --check
python app.py                 # http://localhost:8080
```

Chi tiết cấu hình, deploy k3s, các ngưỡng lọc: xem README trong từng thư mục.

## Image

GitHub Actions build sẵn mỗi lần push vào `main`:

```
ghcr.io/sinhsac/immich-plugin/fp-indexer:latest
ghcr.io/sinhsac/immich-plugin/fp-timeline:latest
```

Kèm tag `sha-<commit>` để ghim một bản cụ thể. Máy đích không cần build gì —
`deploy/k3s.yaml` trỏ thẳng vào đây, k3s tự pull.

Lý do build trên CI: máy đích là OptiPlex 3010 4 core đi WiFi, mà `insightface`
không có wheel nên phải compile từ source. Build tại chỗ mất 15–20 phút và hay
chết giữa đường vì `Read timeout` khi pip tải wheel.

**Sau lần build đầu, phải đổi package sang public**, nếu không k3s bị
`ImagePullBackOff` vì GHCR mặc định để private kể cả với repo public:

> GitHub → tab Packages → chọn package → Package settings → Change visibility → Public

Muốn giữ private thì tạo pull secret trên máy đích:

```bash
kubectl -n media create secret docker-registry ghcr \
    --docker-server=ghcr.io \
    --docker-username=<github-user> \
    --docker-password=<personal-access-token-co-quyen-read:packages>
```

rồi thêm vào `spec.template.spec` của cả hai manifest:

```yaml
      imagePullSecrets:
        - name: ghcr
```

## Model

| Việc | Model | Ở đâu |
|---|---|---|
| Detect mặt + embedding | SCRFD + ArcFace | Immich đã làm, indexer chỉ copy kết quả |
| Head pose, 68 điểm 3D | `1k3d68` trong `buffalo_l` | indexer, stage landmarks |
| Body pose 17 keypoint | `yolov8n-pose.onnx` | indexer, stage bodies |
| Align để dựng video | không cần model | timeline dùng `kps` đã lưu |

`timeline` không load model ML nào nên chạy thường trú rất nhẹ (~300MB RAM).

## Máy đích

Thiết kế cho máy 8GB RAM / i5 / không GPU, chạy chung với Immich:

- indexer tuần tự hoàn toàn, chỉ một model trong RAM tại một thời điểm
- giới hạn thread ONNX, có `SLEEP_MS` để nhường CPU
- `resources.limits` trong cả hai `deploy/k3s.yaml` chặn trần RAM
- chỉ cho phép một render video cùng lúc

## `_backup/`

Pipeline SQLite cũ (chạy tay trên máy dev, có phần seeds + identity propagation).
Đã bị hai thư mục trên thay thế. Giữ tạm để đối chiếu, xoá được khi không cần.

Một thứ trong đó chưa được port: `identity.py` nối các cluster của cùng một
người ở những độ tuổi khác nhau. Immich hay tách một người thành nhiều cluster
khi khoảng thời gian dài, mà `timeline` hiện chỉ cho chọn một cluster.
