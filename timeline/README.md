# fp-timeline — dựng video hành trình một người từ thư viện Immich

Service web chạy thường trú trên k3s.

## Yêu cầu chỉ gồm: ai, và khi nào

```
"dựng video của ông A"
"dựng video của ông A với bà B"
"dựng video của ông A từ 1/1/2000 đến 12/12/2020"
```

Đó là toàn bộ những gì UI hỏi. Không hỏi độ dài, không hỏi số ảnh, không có
thanh trượt nào — **độ dài là kết quả, không phải yêu cầu**. Một request duy
nhất (`POST /api/videos`) tạo dự án, tự suy ngưỡng lọc, chia chương, tự suy thời
lượng và bắt đầu dựng.

Xem xong video thì có hai nút **Ngắn hơn / Dài hơn** để phản ứng với cái đã thấy,
thay vì phải đoán một con số trước khi thấy gì.

Bốn bước tinh chỉnh từng khâu vẫn còn nguyên nhưng **nằm sau công tắc "Chuyên
gia"** ở góc trên. Tắt công tắc thì chúng không tồn tại trên giao diện — làm bằng
một lớp CSS (`.adv` + `body.expert`) chứ không phải hai UI riêng, nên không có
chuyện hai bên lệch nhau.

## Video kể chuyện, không phải băng ảnh

Bản đầu tiên rải ảnh đều tuyệt đối — một ảnh mỗi 30 ngày — rồi cho mỗi ảnh đúng
1/6 giây. Kết quả là một băng ảnh chạy từ đầu đến cuối, mọi frame quan trọng như
nhau, mặt đổi liên tục. Xem được mười giây là mệt, vì không có chỗ nào để mắt
nghỉ và không có gì phân biệt một buổi chiều bình thường với ngày tốt nghiệp.

Chế độ `story` (mặc định) dựng khác:

| | Cách cũ (`even` / `flip`) | Kể chuyện (`story`) |
|---|---|---|
| Số ảnh | bạn đặt `bucket_days`, ra bao nhiêu thì chịu | **suy ra từ `target_seconds`** |
| Thời lượng | = số ảnh / fps, không kiểm soát | = ngân sách bạn đặt |
| Cấu trúc | phẳng | chia **chương** theo thời gian |
| Nhịp | mọi ảnh 1/6 giây | ảnh **điểm nhấn** 1,7s, ảnh phụ 1,0s |
| Chuyển cảnh | cắt cứng (hoặc blend cả video) | chồng mờ giữa từng ảnh |
| Khung | bất động | zoom rất chậm **quanh điểm neo mắt** |
| Thời gian | nhãn năm chạy suốt video | **thẻ nhãn chương** hiện ra rồi tan đi |
| Video trong thư viện | bỏ hẳn | **cắt đoạn đẹp nhất** ghép vào |

Bốn điểm đáng nói:

**Số ảnh suy ra từ thời lượng, không phải ngược lại.** Bạn nói "60 giây", service
tính ra cần bao nhiêu ảnh rồi chia cho các chương. Thư viện 500 ảnh hay 50 nghìn
ảnh thì video vẫn 60 giây — chỉ khác độ dày của từng chương.

**Mỗi chương được ít nhất một ảnh.** Phủ kín thời gian đáng giá hơn độ dày: mất
một năm khỏi video là mất một đoạn câu chuyện. Phần ngân sách còn lại chia theo
`sqrt(số ảnh đạt) / số ảnh đã có` — căn bậc hai để một chuyến du lịch 300 ảnh
không ăn hết, chia cho số đã có để không dồn cục.

**Mỗi chương có một ảnh điểm nhấn** (điểm cao nhất) được giữ lâu gần gấp đôi. Đây
là thứ tạo ra nhịp. Không có nó thì chia chương xong vẫn là băng ảnh.

**Zoom không phá điểm neo.** Phép biến đổi luôn đưa điểm giữa hai mắt về đúng một
chỗ, nên zoom quanh chính điểm đó: khuôn mặt không hề xê dịch, chỉ bối cảnh rộng
ra hẹp vào. Neo vẫn là neo, nhưng khung hết bất động.

Chế độ cũ vẫn còn nguyên (`mode: even` ở bước 3, `mode: flip` ở bước 4) cho ai
muốn kiểu flipbook thật.

## Đoạn video thật, không chỉ ảnh

Job indexer (stage `clips`) quét từng frame video trong thư viện, tìm đúng người
bạn chọn, rồi cắt ra đoạn đẹp nhất và lưu vào `fp_vclip`. Service này đọc lại kết
quả đó và ghép đoạn vào giữa các bức ảnh.

**Đoạn video đi qua đúng bộ lọc với ảnh.** Query trả về các cột cùng tên (`sharp`,
`bright`, `frontality`, `eye_ratio`) nên `_reject()` dùng chung một hàm — không có
hai nhánh lọc song song rồi lệch nhau. Khác biệt duy nhất là `fidx` **âm**
(`-1 - cidx`) để phân biệt, cộng thêm hai ngưỡng riêng:

| Ngưỡng | Mặc định | Ý nghĩa |
|---|---|---|
| `use_clips` | true | Tắt thì video ra chỉ có ảnh |
| `max_clip_motion` | 2.6 | Độ rung tối đa, tính bằng "chiều rộng mặt mỗi giây" |
| `min_clip_seconds` | 0.8 | Đoạn ngắn hơn thì bỏ |

**Mỗi chương có đoạn thì được giành một suất.** Không thể để việc này cho điểm số:
một chương 25 ảnh thì ảnh cao nhất gần như luôn thắng một đoạn video trung bình, và
thế là cả tính năng video không bao giờ xuất hiện. Luật là: nếu chương có đoạn mà
chưa được chọn, thay bức ảnh điểm thấp nhất bằng đoạn tốt nhất — trừ khi đoạn kém
hơn 25% so với bức bị thay (`CLIP_TRADE = 0.75`). Chịu mất một phần điểm chất lượng
để đổi lấy một đoạn động, nhưng không đổi một bức ảnh xuất sắc lấy một đoạn tầm
thường.

**Đoạn mang độ dài của chính nó.** Không ép một đoạn 3,2 giây thành 1 giây, và
không zoom Ken Burns lên nó (nó đã tự chuyển động). Vì thế sau khi chọn xong,
`story.trim_to()` tính lại tổng thật và tỉa bớt ảnh phụ điểm thấp nếu vượt trần —
tỉa ở bước chọn ảnh, không để bước dựng tự ý bỏ, để những gì bạn thấy ở bước 3
đúng là những gì vào video.

Hai ngoại lệ về nhịp:

- **Đoạn mở đầu không bị kéo dài cho thẻ tiêu đề.** Nếu kéo dài thì người xem nhìn
  một frame đứng hình 2,4 giây rồi clip mới chạy. Tiêu đề hiện *lên trên* đoạn đang
  chạy.
- **Đoạn kết thúc thì ngược lại**: giữ thêm rồi mờ dần về đen. Với đoạn video đó là
  một frame đứng hình ở cuối — cách đóng màn thông thường, và không ăn mất giây nào
  của nội dung.

### Neo khuôn mặt đang di chuyển

Đây là chỗ khác bản chất so với ảnh tĩnh: **khuôn mặt di chuyển suốt đoạn**. Neo
vào một vị trí lấy từ một mốc thì đến cuối đoạn mặt đã trôi ra khỏi chỗ.

Indexer lưu `track` — `kps` tại từng mốc lấy mẫu — và `_ClipSrc` nội suy tuyến tính
giữa hai mốc gần nhất cho mỗi frame đầu ra. Kết quả: khuôn mặt vẫn đứng một chỗ
xuyên suốt cả đoạn, giống như với ảnh tĩnh. **Người trong khung cử động, còn khung
thì không.**

Đọc tuần tự, không seek từng frame: seek lại từ keyframe cho mỗi frame sẽ chậm gấp
nhiều lần. Chỉ seek **một** lần đến đầu đoạn (lùi lại 40ms vì với codec có B-frame
`POS_MSEC` lãng ở keyframe gần nhất trước đó).

**Cần `MEDIA_ROOT`.** Chế độ `IMMICH_URL` không đọc được video; `preflight()` sẽ bỏ
mọi đoạn video trước khi tính thời lượng, nên video vẫn ra, chỉ có ảnh.

Chế độ `flip` bỏ qua đoạn video hoàn toàn — nó là chuỗi ảnh tĩnh, không có chỗ.

### Tiếng: J-cut và L-cut

Ảnh tĩnh không có tiếng, nên giữa các đoạn là im lặng. Nếu chỉ dán đúng tiếng của
đoạn vào đúng khoảng hình của nó thì mỗi đoạn thành một khối tiếng bị đóng mở cửa
— đúng nghĩa "chèn tiếng".

Nên tiếng **vào trước hình** (`audio_lead`, mặc định 0,5s) và **còn lại sau khi
hình đã cắt** (`audio_tail`, 0,8s). Trong dựng phim đó là J-cut và L-cut: tai nghe
thấy không gian mới trước khi mắt thấy nó, và không gian đó không tắt đột ngột cùng
lúc với hình.

| Tham số | Mặc định | Ghi chú |
|---|---|---|
| `audio` | true | Tắt thì video im lặng hoàn toàn |
| `audio_lead` | 0.5 | Tiếng vào trước hình bao nhiêu giây |
| `audio_tail` | 0.8 | Tiếng còn lại sau khi hình đã cắt |
| `audio_fade_in` / `audio_fade_out` | 0.35 / 0.6 | Không bao giờ dài quá nửa đoạn |
| `audio_normalize` | true | `dynaudnorm` cân mức giữa các đoạn |
| `audio_gain` | 0 | dB |

Ba chỗ bị cắt, và bỏ chỗ nào cũng làm tiếng lệch với hình **suốt cả đoạn** mà
ffmpeg không báo lỗi gì:

- Đoạn nằm ở đầu video không thể cho tiếng vào trước giây 0 → `lead` bị cắt theo
  vị trí của đoạn, nếu không thì `adelay` âm.
- Đoạn bắt đầu ở đầu *file* không thể đọc tiếng trước giây 0 của file → `lead` bị
  cắt theo `t_start`, nếu không thì `atrim` âm.
- `tail` không được đọc quá cuối file nguồn (`src_dur_ms`).

Việc cắt do `-ss` / `-t` **ở input** làm, không phải `atrim` trong filter — cắt ở
input thì ffmpeg chỉ decode đúng phần cần, và không có nguy cơ cắt hai lần.

Chuỗi filter: mỗi input qua `aformat` (các clip khác sample rate và số kênh, mà
`amix` đòi giống nhau), rồi `afade` hai đầu + `adelay` đặt vào đúng giây, rồi
`amix` với
**`normalize=0`** — `amix` mặc định chia âm lượng cho số input nên 8 đoạn thì mỗi
đoạn chỉ còn 1/8, nghe như thì thầm. Các đoạn gần như không chồng nhau nên cộng
thẳng lại là đúng, và `alimiter` chặn đỉnh phía sau. `apad=whole_dur` phủ hết độ
dài video vì đoạn cuối thường kết thúc trước khi hình hết.

Ghép tiếng chạy **sau** khi đã encode xong hình, và `-c:v copy` nên không encode
lại. Bước này thất bại vì bất kỳ lý do gì thì vẫn còn nguyên video im lặng — thay
vì mất cả video. Clip không có track tiếng bị loại khỏi kế hoạch bằng `ffprobe`:
một input không có audio sẽ làm cả `filter_complex` thất bại, kéo theo mất tiếng
của tất cả đoạn khác.

Chế độ chuyên gia mở lại bốn bước:

1. **Chọn cụm** — chọn nhiều cluster của cùng một người, có gợi ý lan rộng
2. **Ảnh đã chọn** — xem phân bố theo năm và theo chương
3. **Ngưỡng lọc** — kéo ngưỡng, mỗi ảnh bị loại đều có lý do cụ thể
4. **Thông số dựng** — xem trước khung và cấu trúc chuyện rồi mới dựng

Đường mặc định tự bật chế độ chuyên gia và nhảy vào bước 3 khi **không chọn đủ 2
ảnh** — ffmpeg cần tối thiểu 2 frame, nên thay vì render rồi báo lỗi khó hiểu, UI
đưa bạn tới đúng chỗ nới ngưỡng kèm lý do.

## Video của nhiều người

Chọn nhiều cụm mặc định nghĩa là **cùng một người** — Immich hay tách một người
thành nhiều cluster theo độ tuổi. Muốn video của hai người thì chọn cụm của người
thứ nhất, bấm **+ Thêm người nữa**, rồi chọn cụm của người thứ hai. Không suy ra
được từ một mớ cụm lẫn lộn, nên phải chốt từng người một.

Tích **chỉ ảnh có mặt đủ tất cả** thì chỉ lấy ảnh hai người chụp chung; bỏ tích
thì lấy ảnh của bất kỳ ai trong số họ, ghép thành một dòng thời gian chung.

Một tấm ảnh chỉ ra **một** frame dù trong đó có nhiều mặt thuộc những người đã
chọn — nếu không thì cùng một bức xuất hiện hai lần trong video.

### Neo hai khuôn mặt

Ảnh có cả hai người thì điểm neo không còn là một khuôn mặt. Nhưng **không** thể
lấy hai tâm mắt làm "hai mắt" rồi để `level` xoay cho chúng nằm ngang: bố cao con
thấp là lệch 30°, hỏng ảnh.

`media.pair_kps()` trả về hai **điểm ảo nằm ngang**, cách nhau đúng khoảng cách
thật giữa hai người, đặt quanh trung điểm của họ. Kết quả: góc xoay 0, trung điểm
hai người luôn ở một chỗ, và khoảng cách giữa họ luôn bằng một tỉ lệ khung
(`pair_frac`, mặc định 0,30) — ai xa nhau thì khung tự rộng ra để chứa hết. Ảnh
chỉ có một người trong nhóm vẫn neo bình thường theo một mặt, không bị loại.

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

Nhóm kể chuyện — quyết định **lấy ảnh nào và bao nhiêu**:

| Ngưỡng | Mặc định | Ý nghĩa |
|---|---|---|
| `mode` | `story` | `story` chia chương \| `even` rải đều như bản cũ |
| `target_seconds` | **null** | `null` = tự suy từ dữ liệu. Một con số = ép thời lượng |
| `pace` | `normal` | `slow` 2,4/1,5s · `normal` 1,7/1,0s · `quick` 1,2/0,7s · `snap` 0,8/0,45s (điểm nhấn/ảnh phụ) |
| `chapter_by` | `auto` | `years2` \| `year` \| `half` \| `quarter` \| `month` |
| `max_per_chapter` | 6 | Trần ảnh mỗi chương, chặn một chuyến đi chiếm hết video |

### Độ dài tự suy ra sao

Mỗi chương tự quyết định độ dày của nó theo số ảnh đạt ngưỡng mà nó có, tăng theo
`log2`: 1 ảnh → 1, 3 ảnh → 2, 7 → 3, 15 → 4, 31 → 5. Logarit vì độ dày của ký ức
không tỉ lệ thuận với số ảnh chụp được — một chuyến đi 300 ảnh không đáng gấp 100
lần một buổi chiều 3 ảnh, nó chỉ đáng gấp vài lần. Cộng lại được bao nhiêu thì
video dài bấy nhiêu, rồi chặn trên 150 giây bằng cách bớt dần từ chương đang được
nhiều nhất.

Đo thật trên dữ liệu sinh: 40 ảnh / 4 năm → 15 ảnh, 8 chương quý, **24 giây**.
3000 ảnh / 14 năm → 83 ảnh, 15 chương năm, **97 giây**. Cùng một thuật toán, không
ai phải nhập gì.

Đặt `target_seconds` bằng một con số thì chuyển sang đường ngân sách: ảnh được
phân bổ để vừa con số đó. Gửi lại `null` là quay về tự suy.

### `chapter_by: auto` chọn thế nào

Ba ràng buộc kéo nhau, xét đồng thời:

1. **Vừa ngân sách** — mỗi chương tối thiểu một ảnh điểm nhấn.
2. **Càng mịn càng tốt** trong phạm vi còn lại, không phải càng thô: hành trình 13
   năm ra 13 chương một-năm chứ không gộp thành 7 chương hai-năm — gộp thì có năm
   bị bỏ qua hẳn.
3. **Chương phải liền mạch.** Đây là chỗ dễ sai nhất: 40 ảnh rải trong 4 năm mà
   chia theo tháng thì ra 13 chương trên 48 tháng (35 tháng rỗng) — nhãn đọc ra
   như ngày tháng rời rạc *"Tháng 3 2019, Tháng 7 2019, Tháng 11 2020"* chứ không
   ra một tiến trình. Chia theo quý thì 12/16 quý có ảnh, liền mạch hơn nhiều.
   Ngưỡng là `MIN_DENSITY = 0.4`.

Số chương bị chặn trong khoảng 3–18; nhiều hơn thì nhãn chương nhảy liên tục thành
tiếng ồn.

Nếu ép `target_seconds` lên mà video không dài thêm thì chỉ có hai lý do, và UI
nói rõ lý do nào: mọi chương đã đạt trần `max_per_chapter`, hoặc đã dùng hết ảnh
đạt ngưỡng lọc.

Nhóm rải đều theo thời gian — chỉ dùng khi `mode: even`:

| Ngưỡng | Mặc định | Ý nghĩa |
|---|---|---|
| `bucket_days` | tự suy | Chia timeline thành ô bằng nhau |
| `per_bucket` | 1 | Mỗi ô giữ N ảnh điểm cao nhất |

Lần đầu mở một người, service tự suy `bucket_days` từ độ dày dữ liệu để ra khoảng
150–400 frame, để ai chuyển sang chế độ này không phải mở một con số vô nghĩa.

## Thông số video

Khung hình, dùng cho cả hai chế độ:

| Tham số | Mặc định | Ghi chú |
|---|---|---|
| `size` | 900 | **Cạnh dài**, tự làm chẵn cho libx264 |
| `aspect` | 4:3 | 1:1, 4:3, 3:2, 16:9, 3:4, 2:3, 9:16 |
| `face_frac` | 0.12 | Khoảng cách hai mắt / chiều ngang khung. Xem bảng ở trên |
| `eye_y` | 0.33 | Vị trí mắt theo chiều dọc — kéo lên nếu muốn thấy nhiều thân hơn |
| `anchor_x` | 0.5 | Vị trí mắt theo chiều ngang |
| `fill` | crop | `crop` phóng vừa đủ phủ kín, cắt rìa. `blur` giữ trọn ảnh, nền mờ |
| `level` | true | Xoay cho hai mắt nằm ngang |
| `pair_frac` | 0.30 | Video hai người: khoảng cách **giữa hai người** / chiều ngang khung |
| `label` | none | Nhãn thời gian góc dưới: none, year, month, date |

Riêng `mode: story`:

| Tham số | Mặc định | Ghi chú |
|---|---|---|
| `out_fps` | 24 | fps thật của video |
| `motion` | subtle | Zoom Ken Burns: none 0 · subtle 3,5% · normal 7% · strong 12% |
| `title` | true | Thẻ mở đầu: tên người + khoảng năm |
| `title_seconds` | 2.4 | Thẻ mở đầu nằm **trên ảnh đầu tiên**, không phải màn đen riêng |
| `chapter_card` | true | Hiện nhãn chương khi sang chương mới |
| `card_seconds` | 1.8 | Nhãn chương hiện bao lâu rồi tan |
| `birth_year` | — | Có thì nhãn chương hiện thêm "N tuổi" |
| `arc` | true | Chương đầu và chương cuối chậm hơn 12% |
| `intro_s` | 0.8 | Mở màn từ đen |
| `outro_s` | 1.6 | Giữ thêm rồi đóng màn về đen |
| `xfade` | theo `pace` | Ghi đè độ dài chồng mờ |

Riêng `mode: flip` (cách cũ):

| Tham số | Mặc định | Ghi chú |
|---|---|---|
| `fps` | 6 | Số ảnh mỗi giây |
| `smooth` | blend | Pha trộn qua filter `framerate` — rẻ hơn `minterpolate` nhiều |

`eye_dx` cũ vẫn nhận được, quy đổi `face_frac = 2 × eye_dx`.

Chỉ cho phép **một render cùng lúc** (lock trong process) để không đè Immich.

### Chồng mờ không làm video dài ra

Mỗi shot chiếm `hold` frame trên dòng thời gian, và shot sau **bắt đầu chồng lên**
`xfade` frame cuối của shot trước:

```
shot i chiếm  [start_i, start_i + hold_i + xfade_i)
start_(i+1) = start_i + hold_i
tổng frame  = Σ hold_i
```

Nhờ vậy tổng thời lượng suy ra được chính xác đến từng frame trước khi render một
pixel nào — `POST /api/projects/{id}/storyboard` trả về đúng con số đó, tính bằng
đúng hàm mà bước dựng dùng.

### Vì sao sinh frame bằng numpy thay vì filter của ffmpeg

`xfade` + `zoompan` của ffmpeg làm được việc này, nhưng chuỗi 60 clip sinh ra một
filtergraph khổng lồ: tốn RAM, khó đọc log khi lỗi, và gần như không suy ra được
số frame chính xác. Ở đây mỗi frame đầu ra là **một phép `warpAffine`** — dễ kiểm
soát, đếm được, và vẫn nhanh vì ảnh preview chỉ 1440px. Frame đẩy thẳng vào stdin
của ffmpeg dạng rawvideo: không ghi jpg ra đĩa, không encode hai lần.

Shot không zoom (`motion: none`) chỉ warp **một lần** rồi dùng lại cho cả trăm
frame của nó.

### Chữ có dấu

Nhãn chương ("Tháng 3 2019"), tuổi ("6 tuổi") và tên người cần font thật — font
HERSHEY của OpenCV chỉ có ASCII. `tl/textdraw.py` dùng Pillow + một font TTF tìm
theo `FONT_FILE` rồi đến các đường dẫn hệ thống thông dụng; image đã cài
`fonts-dejavu-core`. Thiếu font thì tự động bỏ dấu và vẫn chạy — `/api/health`
báo `text.ok = false` và UI hiện banner vàng.

Chữ được sinh thành **sprite cache theo (chuỗi, cỡ)** rồi dán lại nhiều lần với
alpha khác nhau, nên hiệu ứng mờ dần chỉ là nhân alpha chứ không vẽ lại chữ hàng
trăm lần.

## API

`GET /api/docs` có OpenAPI đầy đủ. Các endpoint chính:

```
GET    /api/health                     tình trạng indexer / ffmpeg / auth
GET    /api/people                     danh sách cụm
GET    /api/people/{id}/similar        gợi ý cụm cùng người (seeds= để lan rộng)
GET    /api/progress                   tiến độ job indexer
POST   /api/videos                     ĐƯỜNG MỘT BƯỚC: chọn người → có video
POST   /api/projects                   tạo dự án, tự chọn ảnh ngay
GET    /api/projects/{id}/result        kết quả lọc + lý do loại + tóm tắt chương
PATCH  /api/projects/{id}/filters       đổi ngưỡng, tính lại
POST   /api/projects/{id}/exclude       bỏ / lấy lại một ảnh
POST   /api/projects/{id}/storyboard    cấu trúc chuyện + thời lượng thật
POST   /api/projects/{id}/render        dựng video
GET    /api/renders/{id}                tiến độ
GET    /api/renders/{id}/video          tải mp4
GET    /api/thumb/{asset}/{fidx}        thumbnail mặt (fidx âm = đoạn video)
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

```bash
python selftest.py                    # không cần Postgres / Immich / ffmpeg
python selftest.py --dump /tmp/frames  # ghi thêm vài frame mẫu để nhìn bằng mắt
```

Chạy được vì phần dễ vỡ nhất lại là phần thuần tính toán. Script nhồi module rỗng
vào `sys.modules` cho `psycopg` để qua bước import, rồi thay `media.load` bằng ảnh
sinh sẵn và thay pipe ffmpeg bằng bộ đếm. Verify:

- chia chương, `auto` chọn đúng mức mịn nhất còn vừa ngân sách
- ngân sách 30/60/120 giây ra thời lượng tương ứng, không chương nào trống
- storyboard: `Σ hold = tổng frame`, `hold ≥ xfade` (không bao giờ chồng ba lớp),
  `start` các shot nối tiếp đúng, điểm nhấn giữ lâu hơn ảnh phụ
- tự suy độ dài: thư viện dày cho video dài hơn thư viện mỏng, cả hai đều dưới
  trần 150 giây; đặt tay `target_seconds` thì tắt tự suy, gửi `null` thì bật lại
- chương liền mạch: không chia theo tháng khi phần lớn tháng rỗng
- nhóm người: `["a","b"]` là một người hai cụm còn `[["a","b"],["c"]]` là hai
  người; `together` chỉ lấy ảnh có mặt đủ; một ảnh chỉ ra một frame
- neo hai mặt: hai điểm neo nằm ngang (không xoay ảnh), trung điểm đúng giữa hai
  người, khoảng cách bằng khoảng cách thật, và không chia cho 0 khi hai mặt trùng
- tiếng: J-cut/L-cut đặt đúng chỗ, `adelay` không bao giờ âm, `atrim` không bao
  giờ âm, `tail` không đọc quá cuối file, `amix normalize=0`, `apad` phủ hết độ dài
- đoạn video: được chọn vào video, đoạn rung bị loại kèm lý do, `use_clips=false`
  thì không đoạn nào vào, đoạn giữ đúng độ dài thật, không zoom Ken Burns, tổng
  thời lượng bị chặn, và đoạn mở đầu không bị kéo dài cho thẻ tiêu đề
- **neo khuôn mặt đang di chuyển**: sinh một mp4 thật bằng `cv2.VideoWriter` với
  một khối sáng di chuyển đều, dựng `track` tương ứng, rồi kiểm chủ thể có nằm
  đúng điểm neo ở mọi frame và **không trôi** trong cả đoạn
- vẽ chữ có dấu, alpha thấp thì mờ hơn
- vòng lặp dựng frame ghi **đúng** số frame mà storyboard hứa, frame đầu và cuối
  gần như đen (mở/đóng màn), và thẻ tiêu đề / nhãn chương thật sự có lớp tối cùng
  nét chữ trắng trên frame thật

CI chạy script này trước khi build image (job `check` trong
`.github/workflows/build-images.yml`), nên logic vỡ thì không có image nào được
đẩy lên GHCR.

Phần cần Postgres/ffmpeg thì verify trên máy đích bằng `python app.py --check` và
`GET /api/health`.
