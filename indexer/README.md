# fp-indexer — index head pose + body pose của ảnh Immich vào Postgres

Job độc lập, chạy một lệnh, ghi vào **bảng riêng có prefix** trong chính database
của Immich. Bảng của Immich chỉ đọc, không bao giờ bị ghi.

Khác với `../pipeline` (SQLite, chạy tay trên máy dev), folder này thiết kế để
deploy như Job/CronJob trên k3s cùng node với Immich.

## Làm gì

| Stage | Việc | Model | Ghi vào |
|---|---|---|---|
| 1 `assets` | Đọc danh sách ảnh **và video** + ngày chụp + đường dẫn | — | `fp_asset` |
| 2 `faces` | Copy bbox + embedding + person đã gán | — | `fp_face` |
| 3 `landmarks` | 68 điểm 3D → yaw/pitch/roll, EAR, age, chỉ số chất lượng | `buffalo_l` / 1k3d68 + genderage | `fp_face` |
| 4 `bodies` | Detect người + 17 keypoint COCO → tư thế, hướng, góc thân | `yolov8n-pose.onnx` | `fp_body` |
| 5 `clips` | Quét frame video, tìm người, cắt ra đoạn đẹp nhất | `det_10g` + `w600k_r50` | `fp_vface`, `fp_vclip` |

Với **ảnh**, stage 2 lấy sẵn bbox + embedding từ Immich nên bỏ được SCRFD và
ArcFace — hai model tốn nhất.

Với **video** thì không bỏ được, và đây là ngoại lệ duy nhất của nguyên tắc
"không làm lại việc Immich đã làm". Lý do cụ thể: Immich chỉ chạy face detection
cho video trên **đúng một frame thumbnail**. Biết một clip có ông A là đủ để liệt
kê, nhưng không đủ để cắt ra "đoạn đẹp nhất có ông A" — muốn biết ông A xuất hiện
ở giây thứ bao nhiêu, mặt to nhỏ ra sao, có nhìn vào máy không, thì phải tự quét.

Điểm nhẹ nhõm: **hai model đó đã nằm sẵn trên đĩa.**
`fetch_models.py --face` tải cả bộ `buffalo_l` gồm `1k3d68`, `genderage`,
`det_10g` *và* `w600k_r50` — hai file cuối từ trước giờ tải về rồi không dùng.
Không phải tải thêm gì, không phải đổi Dockerfile.

Cùng model recognition với Immich (`w600k_r50` của `buffalo_l`) và cùng template
5 điểm, nên vector sinh ra ở stage 5 so sánh trực tiếp được với `emb` đã copy từ
Immich trong `fp_face`. Đó là cơ sở để gán `person_id` cho mặt trong video.

## Stage 5: cắt đoạn video ra sao

Ba bước, và thứ tự quan trọng:

**1. Quét frame.** Lấy mẫu `VIDEO_FPS` frame mỗi giây (mặc định 2; đặt 0 để lấy
*tất cả* frame). Frame bị bỏ qua được đọc bằng `grab()` chứ không `retrieve()` —
`grab` chỉ giải mã tối thiểu để nhảy tiếp, không dựng ra ảnh BGR. Đó là khác biệt
giữa "quét 2 frame mỗi giây" tốn bằng 1/15 và tốn bằng 1 của "decode hết rồi bỏ".
Không dùng seek để nhảy: với codec có B-frame, seek phải decode lại từ keyframe
gần nhất nên đọc tuần tự còn nhanh hơn.

**2. Gán người.** Mỗi mặt detect được đem so cosine với vector trung tâm của từng
person (tính từ `fp_face.emb`). Hai điều kiện, không phải một: `sim ≥ VIDEO_SIM`
**và** cách person xếp thứ hai ít nhất `VIDEO_MARGIN`. Người thân có nét giống
nhau đạt 0,43–0,45 trên thư viện thật, nên một ngưỡng tuyệt đối là gán bừa.

Head pose ở đây **không** dùng `1k3d68` — suy từ 5 điểm (`pose_from_kps5`): góc
đường nối hai mắt cho `roll` chính xác, mũi lệch khỏi trung điểm hai mắt cho `yaw`,
mũi nằm ở đâu giữa đường mắt và đường miệng cho `pitch`. Hai giá trị sau là ước
lượng, đủ để lọc và xếp hạng, không dùng để đo. Chạy thêm một model nữa cho từng
mặt của từng frame là nhân đôi chi phí của stage đắt nhất.

**3. Trượt cửa sổ.** Gom frame thành các đoạn liên tục (cho phép mặt hụt trong
`VIDEO_GAP_MS`), rồi trong từng đoạn thử mọi cửa sổ dài `CLIP_MIN_SECONDS` đến
`CLIP_MAX_SECONDS` và chấm điểm:

```
điểm = trung bình điểm từng frame × độ đầy frame × độ gần CLIP_SECONDS
       ÷ (1 + 0,8 × độ rung)
```

Độ rung là thứ mà chấm điểm từng frame không bắt được: sáu frame đều nét và đều
chính diện nhưng khuôn mặt nhảy khắp khung thì đoạn đó không xem được. Đo bằng
"khuôn mặt dịch chuyển bao nhiêu *chiều rộng mặt* mỗi giây" — chia cho kích thước
mặt chứ không cho kích thước khung, vì mặt to đi 50px là bình thường còn mặt nhỏ
đi 50px là giật.

Điểm từng frame gồm: độ chính diện 28, mặt to 23, độ nét 23, phơi sáng 10, điểm
detect 10, và **ngữ cảnh 6** — đoạn có người khác trong khung thường là một khoảnh
khắc thật (đang chơi, đang ăn, đang chụp cùng ai) chứ không phải một đoạn mặt nhìn
vào máy. Ít thôi, để không biến video thành toàn cảnh đông người.

Giữ `CLIP_PER_PERSON` đoạn tốt nhất, không chồng nhau, cho mỗi người mỗi video.

**Cần `MEDIA_ROOT`.** Chế độ `IMMICH_URL` không dùng được cho stage này: tải cả
thư viện video qua HTTP chỉ để quét là không hợp lý. Ưu tiên đọc
`encodedVideoPath` (bản Immich đã transcode, thường H.264 mp4 — luôn decode được)
rồi mới đến `originalPath`.

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

python job.py --dry-run          # kiểm tra pg + ảnh + video + model, không ghi gì
python job.py                    # chạy cả 5 stage tuần tự
python job.py --stage clips      # chạy riêng một stage
python job.py --reset clips      # quét lại toàn bộ video
python job.py --stats            # xem tiến độ
python job.py --reset errors     # thử lại các ảnh lỗi đọc
```

Job **resumable**: state nằm trong cột `face_state` / `body_state` / `clip_state`
của `fp_asset`, commit theo lô `BATCH_COMMIT` (stage `clips` commit theo **từng
video**). Sập giữa đường thì chạy lại là tiếp tục chỗ cũ, không làm lại từ đầu.
Nhận SIGTERM thì dừng gọn sau khi commit lô đang chạy — an toàn khi k8s evict.

Stage `clips` là stage đắt nhất. Ước lượng thô trên CPU 4 core: mỗi frame lấy mẫu
mất cỡ 60–120ms cho detection + recognition, nên một video 1 phút ở `VIDEO_FPS=2`
là 120 frame ≈ 10–15 giây. 500 video một phút là khoảng 1,5–2 giờ. Đặt
`VIDEO_FPS=0` (mọi frame) thì nhân thêm với `fps` của video — chỉ dùng khi thật
cần. Không muốn quét video thì `DO_VIDEO=0`.

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

**`fp_vface`** — một dòng mỗi khuôn mặt **khớp được với một person** trên mỗi frame
đã lấy mẫu của video: `t_ms`, bbox (0..1), `kps`, `person_id`, `sim`, `sim2`,
`n_face` (tổng số mặt trong frame đó), `yaw`, `roll`, `frontality`, `sharp`,
`bright`, `symm`, `eye_ratio`. Mặt của người lạ không lưu — không ai truy vấn, và
lưu hết thì bảng phình ra vô ích.

**`fp_vclip`** — một dòng mỗi **đoạn đã chọn**: `person_id`, `cidx` (0 = tốt nhất),
`t_start_ms`, `t_end_ms`, `score`, `sim`, `face_ratio`, `sharp`, `bright`,
`frontality`, `motion`, và `track`.

`track` là `float32[n][11]`: mỗi dòng là `t_giây` rồi 5 cặp `(x, y)` chuẩn hoá.
Bước dựng video cần biết khuôn mặt ở đâu tại **từng** thời điểm để neo, không chỉ
ở một mốc — một đoạn 3 giây lấy mẫu 2 fps chỉ có 6–7 mốc, nhét vào một blob 300
byte là xong, và nội suy tuyến tính giữa hai mốc là đủ mượt. Nhờ vậy bước dựng
không phải join lại `fp_vface`.

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
- **Head pose của frame video là ước lượng từ 5 điểm**, không phải `1k3d68`.
  `roll` chính xác, `yaw`/`pitch` chỉ đủ để lọc và xếp hạng. Chạy `1k3d68` cho
  từng mặt của từng frame là nhân đôi chi phí của stage đắt nhất.
- **Khớp person trong video dựa vào cosine giữa hai model cùng loại.** Giả thiết
  là Immich dùng đúng `w600k_r50` của `buffalo_l` với template 5 điểm chuẩn. Nếu
  instance của bạn cấu hình model recognition khác thì `sim` sẽ thấp bất thường —
  `--stats` sẽ cho thấy `doan da chon` gần bằng 0 dù đã quét xong.
- **Codec.** OpenCV bundle FFmpeg nên H.264 mp4 (bản Immich transcode ra) luôn
  decode được. Video HEVC/AV1 gốc có thể không mở được; lúc đó `clip_state=-1`
  kèm lý do, và bật transcode trong Immich là xong.
- Video quay dọc, quay ngược, hoặc có nhiều người cùng lúc thì vẫn chạy, nhưng
  `person_id` chỉ gán cho mặt vượt cả hai ngưỡng `VIDEO_SIM` và `VIDEO_MARGIN`.

## Kiểm thử

```bash
python selftest.py     # không cần Postgres / Immich / model / video
```

Verify phần logic thuần của stage `clips`: chấm điểm frame, gom đoạn liên tục,
phát hiện rung, trượt cửa sổ chọn đoạn (đoạn êm phải thắng đoạn rung khi điểm
từng frame bằng nhau), `track` blob, và ước lượng hướng đầu từ 5 điểm. CI chạy
script này trước khi build image.

Phần cần Immich/Postgres/model thì verify trên máy đích:

```bash
python job.py --dry-run
```

Lệnh này kiểm tra kết nối Postgres, đọc thử ảnh preview, load cả ba bộ model
(1k3d68, yolov8n-pose, det_10g + w600k_r50), đếm số person có vector trung tâm,
mở thử một file video và lấy mẫu 2 giây đầu — rồi thoát mà không ghi gì.
