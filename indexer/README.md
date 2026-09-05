# fp-indexer — index head pose + body pose of Immich photos into Postgres

A standalone job, run with a single command, that writes to **its own
prefixed tables** inside Immich's own database. Immich's tables are read-only here
and are never written to.

Unlike `../pipeline` (SQLite, run by hand on a dev box), this folder is designed to
be deployed as a Job/CronJob on k3s, on the same node as Immich.

## What it does

| Stage | Work | Model | Writes to |
|---|---|---|---|
| 1 `assets` | Read the list of photos **and videos** + capture date + paths | — | `fp_asset` |
| 2 `faces` | Copy bbox + embedding + already-assigned person | — | `fp_face` |
| 3 `landmarks` | 68 3D points → yaw/pitch/roll, EAR, age, quality metrics | `buffalo_l` / 1k3d68 + genderage | `fp_face` |
| 4 `bodies` | Detect people + 17 COCO keypoints → posture, orientation, torso angle | `yolov8n-pose.onnx` | `fp_body` |
| 5 `clips` | Scan video frames, index **every** face + body found, cut out the best clip | `det_10g` + `w600k_r50` (+ `1k3d68`, `yolov8n-pose`) | `fp_vface`, `fp_vbody`, `fp_vclip` |
| — `rematch` | Re-assign people from **stored** vectors and re-cut clips. No decoding, no models | — | `fp_vface`, `fp_vclip` |

`rematch` is not part of `all`; call it by name. It only has work to do after person
names change in Immich.

For **photos**, stage 2 gets bbox + embedding straight from Immich, which lets us
skip SCRFD and ArcFace — the two most expensive models.

For **video** we can't skip them, and this is the only exception to the "never redo
work Immich already did" principle. The concrete reason: Immich only runs face
detection on video for **exactly one thumbnail frame**. Knowing that a clip contains
person A is enough to list it, but not enough to cut out "the best clip featuring
person A" — to know which second person A appears at, how large the face is, whether
they're looking at the camera, you have to scan the video yourself.

The reassuring part: **both models are already on disk.**
`fetch_models.py --face` downloads the whole `buffalo_l` bundle, which contains
`1k3d68`, `genderage`, `det_10g` *and* `w600k_r50` — the last two have always been
downloaded and never used. Nothing extra to fetch, no Dockerfile change.

It's the same recognition model Immich uses (`w600k_r50` from `buffalo_l`) with the
same 5-point template, so the vectors produced in stage 5 are directly comparable to
the `emb` values copied from Immich into `fp_face`. That's what makes it possible to
assign a `person_id` to faces found in video.

## Stage 5: how video clips get cut

Three steps, and the order matters:

**1. Scan frames.** Sample `VIDEO_FPS` frames per second (default 2; set 0 to take
*every* frame). Skipped frames are consumed with `grab()` rather than `retrieve()` —
`grab` decodes only the minimum needed to advance, without building a BGR image.
That's the difference between "scan 2 frames per second" costing 1/15 versus "decode
everything and throw most of it away" costing 1. No seeking to jump ahead: with
B-frame codecs, a seek has to re-decode from the nearest keyframe, so reading
sequentially is actually faster.

**2. Index every face, then assign people.** Every detected face is compared by
cosine similarity against each person's centroid vector (computed from
`fp_face.emb`). Two conditions, not one: `sim ≥ VIDEO_SIM` **and** at least
`VIDEO_MARGIN` ahead of the runner-up person. Relatives who look alike hit 0.43–0.45
on a real library, so a single absolute threshold amounts to guessing.

Faces that match nothing are **still written** (`person_id` NULL). Dropping them, as
an earlier version did, closes off the road ahead: someone not yet named in Immich —
or someone entirely new — leaves no trace at all, so you can never ask "is this a new
person?". Set `VIDEO_KEEP_UNMATCHED=0` for the old, smaller behaviour.

Each face also carries a `track_id`: greedy IoU matching against the previous frame,
numbered within one video. Not Kalman, no velocity model — at 2 frames/second a
person has moved half a step between samples and any motion model is wrong. Grouping
is all it's for; the clustering that decides "these 40 detections are one new person"
works on embeddings, and `track_id` just cuts down the work.

What gets stored per face, so a video never needs re-decoding:

| Column | Why |
|---|---|
| `emb`, `emb_norm` | Cluster unknown faces or assign names later via SQL. Turn off with `VIDEO_STORE_EMB=0` |
| `lmk68` | 68 points, needed to align the face when rendering — same as stills. `VIDEO_LMK68=0` to skip |
| `age`, `ear`, `quality` | Same metrics as `fp_face`, so the two are comparable |
| `track_id` | Group detections of one person within one video |

With `VIDEO_LMK68=1` (default), `1k3d68` runs per face per frame, giving a real
`pitch` plus `age` and `ear`. Turn it off and pose falls back to the 5-point estimate
(`pose_from_kps5`): the angle between the eyes gives an accurate `roll`, the nose
offset from the eye midpoint gives `yaw`, and where the nose sits between the eye and
mouth lines gives `pitch`. Those last two are estimates — good enough to filter and
rank, not to measure. The trade is real: it's another model run on every face of
every frame, in the most expensive stage.

**2b. Body pose.** With `DO_VBODY=1` (default) `yolov8n-pose` runs on the *same*
decoded frame, writing 17 keypoints plus posture / orientation / torso angle to
`fp_vbody` — the video counterpart of `fp_body`. It shares one decode pass on
purpose: decoding video (and in HTTP mode downloading up to `VIDEO_MAX_MB` per file)
dominates the cost, so a second pass would double the expensive part to save the
cheap part. The price is three models resident at once, roughly 700MB.

If the row count gets out of hand, `VIDEO_MIN_FACE_PX` is the knob: a crowd scene can
produce dozens of 10px faces per frame that are useless but account for most of the
rows. It's a threshold on eye-to-eye distance in pixels of the resized frame.

**3. Slide a window.** Group frames into continuous runs (allowing the face to drop
out for up to `VIDEO_GAP_MS`), then within each run try every window from
`CLIP_MIN_SECONDS` to `CLIP_MAX_SECONDS` long and score it:

```
score = mean per-frame score × frame coverage × closeness to CLIP_SECONDS
        ÷ (1 + 0.8 × jitter)
```

Jitter is the thing per-frame scoring can't catch: six frames that are all sharp and
all frontal, but with the face bouncing all over the frame, make a clip nobody wants
to watch. It's measured as "how many *face widths* the face moves per second" —
divided by face size, not frame size, because a large face moving 50px is normal
while a small face moving 50px is a jump cut.

The per-frame score is made up of: frontality 28, face size 23, sharpness 23,
exposure 10, detection score 10, and **context 6** — a clip with other people in
frame is usually a real moment (playing, eating, posing with someone) rather than a
stretch of someone staring into the lens. Only a little weight, so video doesn't turn
into nothing but crowd shots.

Keep the `CLIP_PER_PERSON` best non-overlapping clips per person per video.

**Prefers `MEDIA_ROOT`.** It reads `encodedVideoPath` (Immich's transcoded copy,
usually H.264 mp4 — always decodable) and falls back to `originalPath`. Without a
mounted volume it can still download over HTTP, but `cv2.VideoCapture` needs a
seekable local file, so the *whole* file comes down before scanning; `VIDEO_MAX_MB`
(default 200) caps that.

## Why the two kinds of pose are separate

`buffalo_l` only gives **head pose** (which way the head is turned). To know whether
a person is standing/sitting/lying, facing away or facing the camera, you need a
separate body pose model — here that's `yolov8n-pose`, the nano variant, chosen
because the target machine has 8GB RAM and an i5 with no GPU.

## Configuration

Everything goes through environment variables, see `.env.example`. Required:

- `PG_PASSWORD` — Immich's Postgres password
- `MEDIA_ROOT` — path to Immich's `UPLOAD_LOCATION` (mounted read-only),
  or `IMMICH_URL` + `IMMICH_API_KEY` if you can't mount the volume

Reading files directly is much faster and puts no load on the Immich server.

## Preparing the models

Run this once on a machine with network access, then copy the `models/` folder to the
target machine:

```bash
pip install ultralytics            # only needed to export yolov8n-pose
python tools/fetch_models.py --all --out ./models
```

Result:

```
models/
  models/buffalo_l/1k3d68.onnx  genderage.onnx  det_10g.onnx  w600k_r50.onnx
  yolov8n-pose.onnx
```

## Running

```bash
pip install -r requirements.txt

python job.py --dry-run          # check pg + photos + video + models, write nothing
python job.py                    # run all 5 stages in sequence
python job.py --stage clips      # run a single stage
python job.py --stage rematch    # re-assign people + re-cut clips, no decoding
python job.py --reset clips      # rescan every video
python job.py --stats            # show progress
python job.py --reset errors     # retry photos that failed to read
```

After naming a new person in Immich, run `--stage faces`, `--stage landmarks`, then
`--stage rematch`. That reads the vectors already in `fp_vface`, assigns the new
person, and re-cuts their clips — no video decoding and no models loaded. Before
`emb` was stored this needed a full `--reset clips`, meaning hours of CPU to redo work
that had just been done. `rematch` needs no `MEDIA_ROOT` at all.

The job is **resumable**: state lives in the `face_state` / `body_state` /
`clip_state` columns of `fp_asset`, committed in `BATCH_COMMIT` batches (the `clips`
stage commits **per video**). If it dies halfway through, re-running picks up where it
left off instead of starting over. On SIGTERM it shuts down cleanly after committing
the batch in flight — safe when k8s evicts it.

### Stopping on time, on purpose

`MAX_MINUTES` makes the job stop itself cleanly and **exit 0** once its budget is
spent. Nothing is lost, for the same reason a SIGTERM loses nothing.

Set it **below** the Job's `activeDeadlineSeconds` — say 210 minutes against 225 —
so the job always beats Kubernetes to the punch. That matters more than it looks,
because letting the deadline fire is not equivalent:

| | job stops itself | `activeDeadlineSeconds` fires |
|---|---|---|
| Job result | `Succeeded` | `Failed / DeadlineExceeded` |
| ArgoCD | healthy | **Degraded every morning** |
| `lastSuccessfulTime` | updated | never moves |
| Pod, and its logs | kept | **deleted by the controller**, and `/var/log/pods` cleaned with it |
| `fp_run` row | closed by `finally` | left open, so the UI shows a stage "running" with no pod |

Kubernetes has no notion of "do not cut it while it is making progress": a Job's
lifetime is bounded by `activeDeadlineSeconds` (wall clock), `backoffLimit`, and
exiting 0 — none of which consult health or progress. A `livenessProbe` works in a
Job pod but can only kill it *sooner*. So the only way to stop at a good moment is
for the job to know the time itself, which is what this is.

With it set, the deadline goes back to being what it should be: a safety net for a
job that has genuinely hung.

`clips` is the most expensive stage. Rough estimate on a 4-core CPU: each sampled
frame costs about 60–120ms for detection + recognition, so a 1-minute video at
`VIDEO_FPS=2` is 120 frames ≈ 10–15 seconds. 500 one-minute videos is roughly
1.5–2 hours. `VIDEO_LMK68=1` and `DO_VBODY=1` each add to that, so budget closer to
2–3× if you leave both on. Storage is roughly 0.5–1MB per minute of video with `emb`
and `lmk68` stored at 2 fps. Setting `VIDEO_FPS=0` (every frame) multiplies both
figures by the video's `fps`
— only use it when you really need it. If you don't want video scanned at all, set
`DO_VIDEO=0`.

## Running light so Immich isn't starved

It shares a node with Immich on an 8GB machine, so the job is deliberately slow and
small:

- Fully sequential, no threads, no process pool
- **Only one model in RAM at a time** — stage 3 releases its model before stage 4
  loads a different one. Peak RAM ~700MB
- `ONNX_THREADS=2` caps both onnxruntime and BLAS
- `SLEEP_MS` sleeps between photos to yield CPU
- `MAX_SIDE=1600` downscales the preview image before inference (bboxes are stored
  normalized 0..1, so nothing shifts)
- `resources.limits` in `deploy/k3s.yaml` caps it at 2 CPU / 2GB RAM

## Deploying on k3s

```bash
kubectl create ns media
kubectl -n media create secret generic immich-db --from-literal=password='...'
docker build -f deploy/Dockerfile -t fp-indexer:1.0.0 .
kubectl apply -f deploy/k3s.yaml
kubectl -n media create job --from=cronjob/fp-indexer fp-run-1   # run it right now, by hand
```

Fix the two `hostPath` entries in `deploy/k3s.yaml` to match your machine: the models
directory and Immich's upload directory. The job uses `hostNetwork: true` so it can
reach Immich's Postgres over `127.0.0.1` (Immich runs under docker compose, outside
k3s).

`concurrencyPolicy: Forbid` guarantees two jobs never run at the same time.

## Result tables

The prefix defaults to `fp_`, change it with `TABLE_PREFIX`.

**`fp_asset`** — one row per photo or video: `taken_at`, `date_src`, `preview_path`,
`n_face`, `n_body`, `face_state`, `body_state`, `err`. For video also `video_path`,
`dur_ms`, `clip_state`, and the counts from the scan: `n_vframe`, `n_vface`,
`n_vbody`, `n_clip`.

**`fp_face`** — one row per face:

- From Immich: `immich_face`, `person_id`, `person_name`, `x1..y2` (0..1), `emb`, `emb_norm`
- From 1k3d68: `yaw`, `pitch`, `roll`, `frontality`, `ear`, `age`, `kps`, `lmk68`
- Image metrics: `eye_px`, `sharp`, `bright`, `symm`, `quality`

**`fp_body`** — one row per person:

- `x1..y2` (0..1), `det`, `kps` (float32[17][3] normalized, 204 bytes)
- `orientation` front/back/side/unknown
- `posture` standing/sitting/lying/unknown
- `torso_deg` torso angle relative to vertical, `body_front` 0..1
- `face_fidx` matched to `fp_face.fidx` when a match is found

**`fp_vface`** — one row per face detected on each sampled frame of a video,
**including faces that match no one** (`person_id` NULL):

- Position: `t_ms`, bbox (0..1), `kps`, `lmk68`, `n_face` (total faces in that frame)
- Identity: `person_id`, `person_name`, `sim`, `sim2`, `track_id`, `emb`, `emb_norm`
- Metrics: `yaw`, `pitch`, `roll`, `frontality`, `sharp`, `bright`, `symm`,
  `eye_ratio`, `ear`, `age`, `quality`

Keeping the unmatched rows plus `emb` is what makes person discovery and `rematch`
possible: identity is recomputed from stored vectors instead of from the video.

**`fp_vbody`** — the video counterpart of `fp_body`: same columns plus `t_ms`, keyed
on `(asset_id, t_ms, pidx)`. `face_fidx` matches `fp_vface.fidx` **at the same
`t_ms`**.

**`fp_vclip`** — one row per **selected clip**: `person_id`, `cidx` (0 = best),
`t_start_ms`, `t_end_ms`, `score`, `sim`, `face_ratio`, `sharp`, `bright`,
`frontality`, `motion`, and `track`.

`track` is a `float32[n][11]`: each row is `t_seconds` followed by 5 normalized
`(x, y)` pairs. The rendering step needs to know where the face is at **every**
timestamp to anchor on, not just at a single one — a 3-second clip sampled at 2 fps
has only 6–7 timestamps, which fits in a 300-byte blob, and linear interpolation
between two of them is smooth enough. That way the rendering step never has to join
back against `fp_vface`.

**`fp_run`** — a log of every stage run. **`fp_state`** — checkpoints.

`emb`, `kps`, `lmk68`, `kps` are little-endian float32 `bytea`. To read them back:

```python
np.frombuffer(row["kps"], np.float32).reshape(17, 3)
```

## Example queries

```sql
-- photos with exactly one person, standing, facing forward, sharp face, eyes open
SELECT a.id, a.taken_at, f.quality, b.posture
FROM fp_asset a
JOIN fp_face f ON f.asset_id = a.id AND f.state = 1
JOIN fp_body b ON b.asset_id = a.id AND b.face_fidx = f.fidx
WHERE a.n_body = 1
  AND f.frontality > 0.6 AND f.ear > 0.16 AND f.sharp > 120
  AND b.posture = 'standing' AND b.orientation = 'front'
ORDER BY a.taken_at;

-- posture distribution by year
SELECT date_part('year', a.taken_at) y, b.posture, COUNT(*)
FROM fp_body b JOIN fp_asset a ON a.id = b.asset_id
GROUP BY y, b.posture ORDER BY y;
```

## Known limitations

- Pose from 1k3d68 is a 3D-3D affine fit against the model's mean shape, not a PnP
  solve with a calibrated camera. Accurate enough to filter by angle, not to measure
  with.
- EAR is computed from the 68 3D points projected back to 2D, so treat it as a hint —
  use it to set a flag, don't use it to reject outright.
- `posture` is inferred from geometric ratios; for half-body photos (legs not
  visible) it falls back to how vertical the torso is, which can be wrong.
- Changing `FACE_MODEL` requires `--reset landmarks`; the embeddings from Immich stay
  usable because Immich also runs ArcFace from `buffalo_l`.
- **Head pose for video frames is estimated from the 5 points**, not from `1k3d68`.
  `roll` is accurate; `yaw`/`pitch` are only good enough to filter and rank. Running
  `1k3d68` on every face of every frame would double the cost of the most expensive
  stage.
- **Person matching in video relies on cosine similarity between two instances of the
  same model.** The assumption is that Immich really does use `w600k_r50` from
  `buffalo_l` with the standard 5-point template. If your instance is configured with
  a different recognition model, `sim` will be unusually low — `--stats` will show a
  selected-clip count of nearly zero even though the scan finished.
- **Codecs.** OpenCV bundles FFmpeg, so H.264 mp4 (what Immich transcodes to) always
  decodes. Original HEVC/AV1 videos may fail to open; when that happens you get
  `clip_state=-1` with a reason, and enabling transcoding in Immich fixes it.
- Portrait video, upside-down video, or video with several people at once all still
  work, but `person_id` is only assigned to faces that clear both the `VIDEO_SIM` and
  `VIDEO_MARGIN` thresholds.

## Testing

```bash
python selftest.py     # no Postgres / Immich / models / video needed
```

This verifies the pure logic of the `clips` stage: per-frame scoring, grouping frames
into continuous runs, jitter detection, sliding-window clip selection (a steady clip
must beat a jittery one when per-frame scores are equal), the `track` blob, and head
pose estimation from the 5 points. CI runs this script before building the image.

The parts that need Immich/Postgres/models get verified on the target machine:

```bash
python job.py --dry-run
```

That command checks the Postgres connection, tries reading a preview image, loads all
three model bundles (1k3d68, yolov8n-pose, det_10g + w600k_r50), counts how many
people have a centroid vector, opens a video file and samples its first 2 seconds —
then exits without writing anything.
