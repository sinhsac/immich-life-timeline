# fp-timeline — dựng video hành trình một người từ thư viện Immich

Service web chạy thường trú trên k3s.

**Đường nhanh:** chọn người → bấm **Tạo video ngay**. Service tự suy ngưỡng lọc
từ độ dày dữ liệu, chọn ảnh rải đều theo thời gian, rồi dựng video luôn.

**Đường nâng cao** (nút *Tinh chỉnh từng bước*) giữ nguyên bốn bước:

1. **Chọn cụm** — chọn nhiều cluster của cùng một người, có gợi ý lan rộng
2. **Tự động lấy ảnh** — rải đều theo thời gian, xem phân bố theo năm
3. **Tinh chỉnh pose** — kéo ngưỡng, mỗi ảnh bị loại đều có lý do cụ thể
4. **Dựng video** — xem trước khung rồi ffmpeg ghép thành mp4

Đường nhanh chỉ dừng lại ở bước 3 khi **không chọn đủ 2 ảnh** — ffmpeg cần tối
thiểu 2 frame, nên thay vì render rồi báo lỗi khó hiểu, UI đưa bạn tới đúng chỗ
nới ngưỡng kèm lý do.

Tiến độ index nằm ở **trang Thống kê** riêng trên thanh nav. Trước đây khối đó ở
header nên chiếm chỗ trên cả bốn bước dù đã gập lại. Nhãn nav hiện phần trăm tổng
để biết khi nào cần mở, kèm dấu tròn xanh khi đang có stage chạy.

Chạy sau khi `../indexer` đã xong. Service **không load model ML nào** — align
dùng `kps` đã lưu trong `fp_face`, nên RAM chỉ ~300MB.

## Vì sao phải chọn nhiều cụm

Immich tách **một người thành nhiều cluster** khi khoảng thời gian dài — bé,
thiếu niên, trưởng thành thường thành ba cluster khác nhau. Chỉ chọn một cluster
thì video mất hẳn những giai đoạn còn lại.

`fp_project.person_ids` giữ danh sách cluster, `select.fetch()` lọc bằng
`person_id = ANY(...)`. `GET /api/people/{id}/similar` gợi ý cụm cùng người:
lấy trung bình embedding ArcFace của 16 face điểm cao nhất mỗi cluster (đủ để
tâm ổn định mà không phải kéo 89k vector), chuẩn hoá rồi so cosine. Truyền
`seeds=` các cluster đã chọn thì nó so với tâm gộp của cả nhóm — chọn thêm rồi
gọi lại là lan rộng dần.

**Giới hạn thật, cần biết trước khi tin kết quả:** cosine một mình không tách
được người thân. Trên thư viện thật, cụm cùng người đạt ~0.54 còn một người
khác đã đặt tên đạt 0.435 — biên rất hẹp. Vì vậy cụm nào đã có **tên khác** với
nhóm đang chọn sẽ bị đánh dấu `name_conflict` và đẩy xuống cuối; đây là tín hiệu
đáng tin hơn con số cosine. Vẫn phải nhìn ảnh rồi mới chọn.

Centroid được cache 10 phút vì tính một lần mất vài chục giây.

## Vì sao phải neo khuôn mặt

Nếu chỉ ghép ảnh theo thứ tự thời gian thì mặt nhảy loạn, không xem được. Neo
đưa hai mắt về đúng một vị trí và đúng một độ lớn trong mọi frame. Đây là điểm
quyết định video có ra được hay không.

Nhưng neo **không có nghĩa là crop sát mặt**. Khuôn mặt chỉ là điểm neo; khung
hình vẫn nên giữ càng nhiều bối cảnh càng tốt. Tham số quyết định là
`face_frac` — khoảng cách hai mắt tính theo chiều ngang khung ra:

| `face_frac` | Kết quả |
|---|---|
| 0.50–0.60 | Chân dung sát mặt, mất hết bối cảnh |
| **0.10–0.15** | Thấy cả người và bối cảnh — **mặc định** |
| 0.06–0.08 | Toàn cảnh, người nhỏ |

Ảnh có người khác vẫn neo theo người bạn chọn, vì `kps` lấy từ đúng `fidx` của
người đó trong `fp_face`.

Ảnh không đủ lớn để phủ kín khung thì hàm tự phóng to thêm, và khi buộc phải
chọn giữa "đúng điểm neo" và "không có viền trống" thì nó ưu tiên phủ kín —
khuôn mặt lệch khỏi `eye_y` một chút. Đổi sang `fill=blur` nếu muốn giữ trọn
khung ảnh và chấp nhận nền mờ ở phần thiếu.

## Kiến trúc

```
Immich (docker compose)          k3s (namespace media)
┌──────────────────┐            ┌───────────────────────┐
│ immich-server    │            │ Job  fp-indexer       │  CronJob
│ immich-machine-  │            │  → fp_asset/face/body │
│   learning       │            ├───────────────────────┤
│ postgres  ───────┼────────────┤ Svc  fp-timeline      │  Deployment
└──────────────────┘  cùng db   │  → fp_project/render  │
      │                         └───────────────────────┘
      └── UPLOAD_LOCATION ──── mount read-only vào cả hai
```

Bảng đọc: `fp_asset`, `fp_face`, `fp_body` (do indexer tạo).
Bảng ghi: `fp_project`, `fp_project_frame`, `fp_render`.
Bảng Immich: chỉ đọc, không bao giờ bị ghi.

## Cấu hình

Env var, xem `.env.example`. Bắt buộc: `PG_PASSWORD`, `MEDIA_ROOT`.
Rất nên đặt: `API_TOKEN`.

## Chạy local

```bash
pip install -r requirements.txt
python app.py --check          # kiểm tra pg / ảnh / ffmpeg rồi thoát
python app.py                  # http://localhost:8080
```

Cần `ffmpeg` trong PATH. Không có thì ba bước đầu vẫn chạy, chỉ bước render lỗi.

## Deploy k3s

```bash
kubectl -n media create secret generic fp-timeline \
    --from-literal=pg-password='...' \
    --from-literal=api-token="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
docker build -f deploy/Dockerfile -t fp-timeline:1.0.0 .
kubectl apply -f deploy/k3s.yaml
```

Sửa `hostPath` trỏ vào thư mục upload của Immich. `replicas: 1` +
`strategy: Recreate` là cố ý: render chạy ở thread nền và ghi vào PVC, hai pod
sẽ đạp nhau.

## Bảo mật

Service này xem được **toàn bộ ảnh gia đình**. Mặc định không có xác thực —
service sẽ in cảnh báo và UI hiện banner vàng nếu `API_TOKEN` trống.

Đặt `API_TOKEN` trước khi mở cổng ra ngoài mạng nội bộ. Truy cập bằng
`?token=...` hoặc header `Authorization: Bearer ...`.
Ingress trong `deploy/k3s.yaml` chưa bật TLS — thêm cert-manager nếu ra internet.

Token nhận từ ba nguồn: header, `?token=`, và **cookie**. Cookie không phải cho
tiện: trình duyệt tải `style.css` / `app.js` bằng thẻ `<link>` và `<script>` nên
không gửi được header, mà `index.html` cũng không tự thêm `?token=` vào đó. Vào
bằng `?token=` một lần, service ghi cookie (HttpOnly, 30 ngày), các request sau
tự đi qua. Bỏ cookie đi thì bật token lên là trang hiện ra dạng HTML trần.

## Các ngưỡng lọc

Nhóm pose đầu (từ `1k3d68`):

| Ngưỡng | Mặc định | Ý nghĩa |
|---|---|---|
| `max_yaw` | 22° | Quay trái/phải |
| `max_pitch` | 18° | Ngửa/cúi |
| `max_roll` | 20° | Nghiêng đầu |
| `min_frontality` | 0.45 | Gộp pose + đối xứng, 0..1 |
| `min_ear` | 0.15 | Loại ảnh nhắm mắt |

Nhóm chất lượng ảnh:

| Ngưỡng | Mặc định | Ý nghĩa |
|---|---|---|
| `min_eye_ratio` | 0.030 | Hai mắt cách nhau ≥ 3% cạnh dài — loại mặt quá nhỏ |
| `min_sharp` | 60 | Laplacian variance trên crop 128px |
| `bright_min/max` | 45 / 215 | Loại ảnh cháy sáng hoặc quá tối |

Nhóm người khác trong ảnh:

| Ngưỡng | Mặc định | Ý nghĩa |
|---|---|---|
| `allow_others` | **true** | Chấp nhận ảnh có người khác |
| `max_faces` | 0 | Giới hạn số mặt, 0 = không giới hạn |

Nhóm body pose (từ `yolov8n-pose`):

| Ngưỡng | Mặc định |
|---|---|
| `postures` | standing, sitting, unknown |
| `orientations` | front, side, unknown |
| `allow_missing_body` | true — không detect được thân vẫn nhận |
| `use_body` | true — tắt thì bỏ qua hoàn toàn dữ liệu body |

Nhóm rải đều theo thời gian:

| Ngưỡng | Mặc định | Ý nghĩa |
|---|---|---|
| `bucket_days` | tự suy | Chia timeline thành ô bằng nhau |
| `per_bucket` | 1 | Mỗi ô giữ N ảnh điểm cao nhất |

`bucket_days` là ngưỡng quan trọng nhất. Không có nó thì một chuyến du lịch
200 ảnh sẽ chiếm hết video, còn những năm ít ảnh bị mất. Lần đầu mở, service tự
suy `bucket_days` từ độ dày dữ liệu để ra khoảng 150–400 frame.

## Thông số video

| Tham số | Mặc định | Ghi chú |
|---|---|---|
| `size` | 900 | **Cạnh dài**, tự làm chẵn cho libx264 |
| `aspect` | 4:3 | 1:1, 4:3, 3:2, 16:9, 3:4, 2:3, 9:16 |
| `face_frac` | 0.12 | Khoảng cách hai mắt / chiều ngang khung. Xem bảng ở trên |
| `eye_y` | 0.33 | Vị trí mắt theo chiều dọc — kéo lên nếu muốn thấy nhiều thân hơn |
| `anchor_x` | 0.5 | Vị trí mắt theo chiều ngang |
| `fill` | crop | `crop` phóng vừa đủ phủ kín, cắt rìa. `blur` giữ trọn ảnh, nền mờ |
| `level` | true | Xoay cho hai mắt nằm ngang |
| `fps` | 6 | Số ảnh mỗi giây |
| `smooth` | blend | Pha trộn frame qua filter `framerate` — rẻ hơn `minterpolate` nhiều |
| `out_fps` | 30 | fps đầu ra khi bật blend |
| `label` | year | Nhãn thời gian, vẽ bằng cv2 nên không cần mount font |

`eye_dx` cũ vẫn nhận được, quy đổi `face_frac = 2 × eye_dx`.

Chỉ cho phép **một render cùng lúc** (lock trong process) để không đè Immich.

## API

`GET /api/docs` có OpenAPI đầy đủ. Các endpoint chính:

```
GET    /api/health                     tình trạng indexer / ffmpeg / auth
GET    /api/people                     danh sách cụm
GET    /api/people/{id}/similar        gợi ý cụm cùng người (seeds= để lan rộng)
GET    /api/progress                   tiến độ job indexer
POST   /api/projects                   tạo dự án, tự chọn ảnh ngay
GET    /api/projects/{id}/result        kết quả lọc + lý do loại từng ảnh
PATCH  /api/projects/{id}/filters       đổi ngưỡng, tính lại
POST   /api/projects/{id}/exclude       bỏ / lấy lại một ảnh
POST   /api/projects/{id}/render        dựng video
GET    /api/renders/{id}                tiến độ
GET    /api/renders/{id}/video          tải mp4
GET    /api/thumb/{asset}/{fidx}        thumbnail mặt
GET    /api/aligned/{asset}/{fidx}      xem trước frame đã align
```

## Phụ thuộc vào indexer

Service cần `fp_face.kps` lưu **toạ độ chuẩn hoá 0..1**. Bản indexer đầu tiên
lưu pixel của ảnh đã resize — không align lại được ở kích thước khác. Đã sửa,
kèm cột mới `eye_ratio`.

Nếu bạn đã chạy indexer trước bản sửa này:

```bash
cd ../indexer
python job.py --reset landmarks
python job.py --stage landmarks
```

`app.py --check` và `/api/health` sẽ báo nếu `kps` còn thiếu.

## Giới hạn đã biết

- `MAX_FRAMES` chặn ở 1200 frame. Cao hơn thì align tốn nhiều thời gian và
  video quá dài.
- Render đọc ảnh **preview** của Immich (thường 1440px), không phải ảnh gốc.
  Đủ cho khung 720–1024. Muốn cao hơn thì indexer phải lưu thêm `originalPath`.
- Ảnh không có EXIF ngày chụp sẽ dùng ngày file, dễ dồn cục vào ngày scan.
  Job indexer in cảnh báo khi phát hiện; sửa trong Immich rồi chạy lại.
- Người có ít hơn ~20 ảnh trải theo thời gian thì video sẽ nhảy, không mượt.

## Kiểm thử

Trên máy dev không có Postgres/Immich/ffmpeg thì chỉ verify được logic thuần:
lọc theo ngưỡng, rải bucket, align, clamp tham số render. Phần còn lại verify
trên máy đích bằng `python app.py --check` và `GET /api/health`.
