# Video hành trình một người, dựng từ thư viện Immich

Chọn một người trong thư viện ảnh [Immich](https://immich.app) của bạn, công cụ
này lọc ra những khung hình hợp lý trải đều theo thời gian, neo khuôn mặt về
cùng một vị trí rồi ghép thành video — xem lại như thấy người đó lớn dần qua
nhiều năm.

Điểm khác biệt so với việc ghép ảnh thủ công: **khuôn mặt được neo cố định**.
Nếu chỉ xếp ảnh theo thứ tự thời gian thì mặt nhảy loạn mỗi frame, không xem
được. Ở đây mỗi frame được xoay, phóng và dịch sao cho hai mắt luôn nằm đúng một
chỗ với cùng một kích thước.

Và quan trọng: **neo không có nghĩa là crop sát mặt**. Khuôn mặt chỉ là điểm
neo, còn khung hình vẫn giữ bối cảnh — cả người, cảnh vật, những người xung
quanh. Đó là thứ làm video có cảm xúc thay vì thành dãy ảnh thẻ.

## Ý tưởng cốt lõi: không làm lại việc Immich đã làm

Immich đã chạy Face Detection và Facial Recognition trên thư viện của bạn, và
lưu vào Postgres bbox từng khuôn mặt, vector ArcFace, và người đó là ai. Đó là
phần tốn kém nhất của bài toán.

Công cụ này **đọc lại kết quả đó** thay vì tự detect. Nhờ vậy bỏ được hai model
nặng nhất (SCRFD và ArcFace) và chỉ chạy thêm hai model nhẹ cho phần Immich
không có:

| Việc | Model | Ai làm |
|---|---|---|
| Tìm mặt, vector nhận dạng | SCRFD + ArcFace | **Immich** đã làm, chỉ copy kết quả |
| Hướng đầu, 68 điểm 3D | `1k3d68` trong `buffalo_l` | công cụ này |
| Tư thế cơ thể, 17 keypoint | `yolov8n-pose` | công cụ này |
| Neo mặt để dựng video | không cần model | dùng lại `kps` đã lưu |

Toàn bộ dữ liệu mới ghi vào **bảng riêng prefix `fp_`** trong chính database của
Immich. Bảng của Immich **chỉ đọc, không bao giờ bị ghi**.

## Cần gì trước khi bắt đầu

- Immich đang chạy, và **đã chạy xong Facial Recognition** (Administration →
  Jobs). Chưa xong thì chưa có gì để đọc.
- Truy cập được Postgres của Immich (cùng máy hoặc qua mạng).
- Thư mục `UPLOAD_LOCATION` của Immich để mount read-only — hoặc một API key nếu
  không mount được.
- `ffmpeg` — đã có sẵn trong image, không phải cài.

Không cần GPU. Thiết kế nhằm vào máy yếu: 4 core, 8GB RAM, chạy chung với Immich.

## Hai thành phần

| Thư mục | Loại | Việc |
|---|---|---|
| [`indexer/`](indexer/) | job chạy định kỳ | Quét ảnh Immich, index hướng đầu + tư thế vào Postgres |
| [`timeline/`](timeline/) | web service | UI 4 bước: chọn người → lấy ảnh → tinh chỉnh → dựng video |

```
Immich (đã chạy Facial Recognition)
        │
        ├─ indexer — chạy một lần, rồi định kỳ cho ảnh mới
        │    1 assets     → fp_asset   danh sách ảnh + ngày chụp
        │    2 faces      → fp_face    bbox + vector, copy từ Immich
        │    3 landmarks  → fp_face    yaw/pitch/roll, chất lượng, điểm neo
        │    4 bodies     → fp_body    17 keypoint, tư thế, hướng thân
        │
        └─ timeline — service thường trú
             1 chọn người   gộp nhiều cụm của cùng một người
             2 lấy ảnh      rải đều theo thời gian, không dồn cục
             3 tinh chỉnh   kéo ngưỡng, mỗi ảnh bị loại đều có lý do
             4 dựng video   neo mặt + ffmpeg → mp4
```

## Chạy bằng Docker Compose

Cách nhanh nhất. Image build sẵn trên GHCR, không phải build gì.

```bash
git clone https://github.com/sinhsac/immich-plugin.git
cd immich-plugin
cp .env.example .env
# Sua .env: PG_PASSWORD va UPLOAD_LOCATION (hoac IMMICH_URL + IMMICH_API_KEY)

# 1. Tai model — chay mot lan, khoang 300MB
docker compose --profile setup up

# 2. Quét thư viện. Lần đầu lâu: vài giờ cho vài chục nghìn ảnh trên CPU.
#    Dừng giữa đường không mất gì, chạy lại là tiếp tục chỗ cũ.
docker compose run --rm indexer

# 3. Mở UI
docker compose up -d timeline
# http://localhost:8080
```

Kiểm tra trước khi quét thật:

```bash
docker compose run --rm indexer --dry-run   # thử pg, thử đọc ảnh, thử load model
docker compose run --rm indexer --stats     # xem tiến độ
```

Ảnh mới upload vào Immich về sau chỉ cần chạy lại `docker compose run --rm
indexer` — nó chỉ xử lý phần chưa có, không làm lại từ đầu.

## Chạy trên Kubernetes / k3s

Manifest mẫu trong [`indexer/deploy/k3s.yaml`](indexer/deploy/k3s.yaml) và
[`timeline/deploy/k3s.yaml`](timeline/deploy/k3s.yaml). Sửa `hostPath` cho đúng
máy bạn rồi:

```bash
kubectl create ns media
kubectl -n media create secret generic immich-db --from-literal=password='...'
kubectl apply -f indexer/deploy/k3s.yaml
kubectl apply -f timeline/deploy/k3s.yaml

# Chạy quét ngay, không đợi lịch
kubectl -n media create job --from=cronjob/fp-indexer fp-run-1
kubectl -n media logs -f job/fp-run-1
```

`indexer` là CronJob (`concurrencyPolicy: Forbid`), `timeline` là Deployment một
replica với `strategy: Recreate` — render ghi vào volume nên hai pod sẽ đạp nhau.

## Image

```
ghcr.io/sinhsac/immich-plugin/fp-indexer:latest
ghcr.io/sinhsac/immich-plugin/fp-timeline:latest
```

Các tag có sẵn:

| Tag | Nghĩa |
|---|---|
| `latest` | build mới nhất trên `main` |
| `1.2.3` | phiên bản release, **nên dùng cho máy chạy thật** |
| `v1.2.3` | cùng bản đó, giữ nguyên chữ `v` |
| `sha-<commit>` | ghim chính xác một commit |

Dùng `latest` thì phải đặt `imagePullPolicy: Always`. Để `IfNotPresent` là node
thấy đã có `latest` trong cache rồi dùng mãi, build mới không bao giờ tới máy —
`latest` thành vô nghĩa. Ghim theo phiên bản thì ngược lại: `IfNotPresent` mới
đúng, vì tag bất biến nên không cần hỏi registry mỗi lần khởi động.

Nếu bạn fork, nhớ đổi package sang **public** ở Packages → Package settings, vì
GHCR để private kể cả với repo public.

Muốn tự build:

```bash
DOCKER_BUILDKIT=1 docker build -f indexer/deploy/Dockerfile -t fp-indexer indexer/
DOCKER_BUILDKIT=1 docker build -f timeline/deploy/Dockerfile -t fp-timeline timeline/
```

`insightface` không phát hành wheel nên phải compile từ source — image indexer
dùng multi-stage, compiler chỉ nằm ở stage build.

## Bốn bước trên UI

**1. Chọn người.** Danh sách lấy từ cụm khuôn mặt Immich đã phân loại. Immich
thường tách **một người thành nhiều cụm** ở các mốc tuổi khác nhau — bé, thiếu
niên, trưởng thành thành ba cụm riêng. Chọn được nhiều cụm cùng lúc, và nút "Tìm
cụm cùng người" so vector trung tâm để gợi ý thêm, chọn thêm rồi tìm lại là lan
rộng dần.

Cảnh báo thật: cosine một mình **không tách được người thân**. Trên một thư viện
thật, cụm cùng người đạt 0,55 còn một người khác trong nhà đạt 0,44 — biên rất
hẹp. Vì vậy cụm nào đã được đặt **tên khác** sẽ bị đánh dấu và đẩy xuống cuối,
tín hiệu đó đáng tin hơn con số. Vẫn phải nhìn ảnh rồi mới chọn.

**2. Lấy ảnh tự động.** Đây là bước quyết định video có "đều" hay không. Chia
timeline thành các ô thời gian bằng nhau rồi mỗi ô chỉ giữ vài ảnh điểm cao
nhất. Không làm vậy thì một chuyến du lịch 200 ảnh chiếm hết video, còn những
năm ít ảnh mất hẳn.

**3. Tinh chỉnh.** Kéo ngưỡng góc đầu, độ nét, độ sáng, tư thế. **Mỗi ảnh bị
loại đều hiện lý do cụ thể**, nên biết phải nới ngưỡng nào chứ không phải đoán.

**4. Dựng video.** Xem trước ba frame cách xa nhau về thời gian để kiểm tra
khung, rồi ffmpeg ghép thành mp4.

## Khung hình: tham số quan trọng nhất

`face_frac` là khoảng cách hai mắt tính theo chiều ngang khung ra:

| `face_frac` | Kết quả |
|---|---|
| 0,50–0,60 | Chân dung sát mặt, mất hết bối cảnh |
| **0,10–0,15** | Thấy cả người và bối cảnh — **mặc định** |
| 0,06–0,08 | Toàn cảnh, người nhỏ |

Vì mọi frame dùng cùng `face_frac` và cùng `eye_y`, khuôn mặt nằm đúng một chỗ
với cùng độ lớn xuyên suốt video.

Ảnh không đủ lớn để phủ kín khung thì hàm tự phóng to thêm. Khi buộc phải chọn
giữa "đúng điểm neo" và "không có viền trống", nó ưu tiên phủ kín — mặt lệch khỏi
`eye_y` một chút. Đổi sang `fill=blur` nếu muốn giữ trọn khung ảnh và chấp nhận
nền mờ ở phần thiếu.

Ảnh có nhiều người vẫn neo theo đúng người bạn chọn, vì điểm neo lấy từ khuôn mặt
cụ thể trong `fp_face` chứ không phải mặt bất kỳ.

## Bảng dữ liệu

Prefix mặc định `fp_`, đổi bằng `TABLE_PREFIX`.

| Bảng | Nội dung |
|---|---|
| `fp_asset` | một dòng mỗi ảnh: ngày chụp, đường dẫn preview, trạng thái xử lý |
| `fp_face` | một dòng mỗi mặt: bbox, vector, yaw/pitch/roll, chất lượng, điểm neo |
| `fp_body` | một dòng mỗi người: 17 keypoint, tư thế, hướng thân |
| `fp_project` | dự án video: người + bộ ngưỡng + danh sách frame |
| `fp_run` | log mỗi lần chạy stage |

Ví dụ truy vấn trực tiếp — ảnh có đúng một người, đứng chính diện, mặt nét:

```sql
SELECT a.id, a.taken_at, f.quality, b.posture
FROM fp_asset a
JOIN fp_face f ON f.asset_id = a.id AND f.state = 1
JOIN fp_body b ON b.asset_id = a.id AND b.face_fidx = f.fidx
WHERE a.n_body = 1
  AND f.frontality > 0.6 AND f.ear > 0.16 AND f.sharp > 120
  AND b.posture = 'standing' AND b.orientation = 'front'
ORDER BY a.taken_at;
```

## Chạy được trên máy yếu

Thiết kế cho máy 8GB RAM / CPU 4 core / không GPU, dùng chung với Immich:

- Indexer tuần tự hoàn toàn, không thread, không process pool
- **Chỉ một model trong RAM tại một thời điểm** — stage 3 giải phóng model trước
  khi stage 4 load model khác. RAM đỉnh khoảng 700MB
- `ONNX_THREADS=2` giới hạn cả onnxruntime lẫn BLAS
- `SLEEP_MS` nghỉ giữa mỗi ảnh để nhường CPU cho Immich
- Timeline **không load model nào** (neo dùng `kps` đã lưu) nên chỉ ~300MB RAM
- Chỉ cho phép một render cùng lúc

**Resumable.** Tiến độ nằm trong cột trạng thái của `fp_asset`, commit theo lô.
Mất điện hay bị kill thì chạy lại là tiếp tục chỗ cũ, mất nhiều nhất một lô. Nhận
SIGTERM thì commit lô đang chạy rồi thoát gọn. Và advisory lock trong Postgres
đảm bảo không bao giờ có hai indexer chạy song song.

## Bảo mật

Service này **xem được toàn bộ ảnh trong thư viện**. Mặc định không có xác thực
— nó sẽ in cảnh báo và UI hiện banner vàng nếu `API_TOKEN` trống.

Đặt `API_TOKEN` trước khi mở cổng ra ngoài máy của bạn. Truy cập bằng
`?token=...` (service ghi cookie nên chỉ cần một lần) hoặc header
`Authorization: Bearer ...`.

Ingress mẫu chưa bật TLS. Đừng đưa service này ra internet mà không có TLS và
token.

## Giới hạn đã biết

- **Ngày chụp sai thì video sai.** Ảnh không có EXIF `DateTimeOriginal` sẽ dùng
  ngày file, dễ dồn cục vào ngày scan. Indexer in cảnh báo khi phát hiện — sửa
  trong Immich rồi chạy lại stage `assets`.
- **Người có ít hơn ~20 ảnh** trải theo thời gian thì video nhảy, không mượt.
- Render đọc ảnh **preview** của Immich (thường 1440px), không phải ảnh gốc. Đủ
  cho khung 720–1080.
- Hướng đầu tính bằng khớp affine 3D-3D với hình dáng trung bình của model, không
  phải PnP có hiệu chuẩn camera. Đủ chính xác để lọc góc, không dùng để đo.
- EAR (độ mở mắt) tính từ điểm 3D chiếu về 2D nên chỉ là tín hiệu gợi ý.
- `posture` suy từ tỉ lệ hình học; ảnh nửa người không thấy chân có thể đoán sai.
- Đọc ảnh qua API chậm hơn đọc file rõ rệt, và **mỗi stage tải lại toàn bộ ảnh
  một lượt**. Mount volume nếu có thể.

## Cấu hình chi tiết

Toàn bộ qua biến môi trường. Xem [`indexer/README.md`](indexer/README.md) và
[`timeline/README.md`](timeline/README.md) cho danh sách đầy đủ, các ngưỡng lọc,
và API reference. UI cũng có OpenAPI ở `/api/docs`.

## License

[PolyForm Noncommercial License 1.0.0](LICENSE.md) — miễn phí cho mọi mục đích
**phi thương mại**.

**Được phép:** dùng cho cá nhân và gia đình, học tập, nghiên cứu, dự án sở thích;
tải về sửa đổi thoải mái; phân phối lại bản đã sửa; dùng trong trường học, tổ
chức từ thiện, viện nghiên cứu công, cơ quan nhà nước.

**Không được phép:** bán, cho thuê, tính phí, dùng trong sản phẩm hay dịch vụ
thương mại, hoặc bất cứ việc gì nhằm mục đích kinh doanh.

Điều kiện phi thương mại **áp dụng cả cho bản sửa đổi**: license này chỉ cấp
quyền cho mục đích phi thương mại, nên ai nhận lại từ bạn cũng chỉ có đúng quyền
đó — không ai có thể fork rồi đổi sang giấy phép cho phép bán.

Muốn dùng thương mại thì mở một issue để trao đổi.

### Ba điều nên biết trước khi fork

**Đây không phải open source theo định nghĩa OSI.** Các giấy phép giới hạn phi
thương mại không được OSI phê duyệt, và GitHub sẽ hiện là "Other" thay vì tên
license. Ai chỉ nhận giấy phép OSI sẽ không dùng được project này. Đó là cái giá
của điều kiện phi thương mại, không phải nhược điểm có thể sửa.

**Vì sao PolyForm mà không phải CC BY-NC-SA.** Chính Creative Commons
[khuyến nghị không dùng giấy phép CC cho phần mềm](https://creativecommons.org/faq/#can-i-apply-a-creative-commons-license-to-software):
chúng không cấp quyền bằng sáng chế và không xử lý chuyện phân phối mã nguồn hay
bản biên dịch. PolyForm do các luật sư về giấy phép soạn riêng cho phần mềm, có
điều khoản bằng sáng chế, và viết bằng ngôn ngữ dễ đọc.

**Không có điều khoản ShareAlike.** License này ràng buộc *mục đích sử dụng*, chứ
không buộc công khai mã nguồn. Ai đó có thể sửa riêng mà không chia sẻ lại, miễn
là phi thương mại. Nếu bạn cần buộc họ công khai thay đổi thì license này không
làm được điều đó.

*Nội dung mục này diễn giải lại từ các nguồn được dẫn link, không phải văn bản
pháp lý. Bản có hiệu lực là [LICENSE.md](LICENSE.md).*
