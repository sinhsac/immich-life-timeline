# One person's journey as a video, built from an Immich library

[![build images](https://github.com/sinhsac/immich-life-timeline/actions/workflows/build-images.yml/badge.svg)](https://github.com/sinhsac/immich-life-timeline/actions/workflows/build-images.yml)
[![release](https://img.shields.io/github/v/release/sinhsac/immich-life-timeline?sort=semver)](https://github.com/sinhsac/immich-life-timeline/releases/latest)
[![license: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue)](LICENSE.md)

Pick one person from your [Immich](https://immich.app) library and this tool
splits their journey into chapters over time, takes the single most worthwhile
moment from each chapter, anchors the face to the same spot in every frame, and
renders it into a short video with an opening, a rhythm, and an ending.

What makes it different from stitching photos together by hand: **the face stays
anchored**. Order photos by date alone and the face jumps around every frame,
which is unwatchable. Here every frame is rotated, scaled, and shifted so the two
eyes always land on the same position at the same size.

And this matters: **anchoring is not the same as cropping tight on the face**. The
face is only the anchor point; the frame still keeps the context — the whole
person, the surroundings, the people nearby. That is what gives the video feeling
instead of turning it into a row of ID photos.

## Storytelling, not a photo reel

The first version spread photos perfectly evenly and gave each one 1/6 of a
second. The result was a reel running start to finish where every frame mattered
equally and the face changed constantly — ten seconds in you were tired, because
there was nowhere for the eye to rest and nothing distinguished an ordinary
afternoon from a graduation day.

The default is now **storytelling** mode:

- **You set nothing.** The request is just *who* and (optionally) *what date
  range*. Duration is an **outcome**: chapters with more photos get more screen
  time, a longer journey gets more chapters, and the total is clamped to 16–150
  seconds so you never end up with a 20-minute video. Once you have watched it,
  two buttons — *Shorter / Longer* — let you react to what you actually saw
  instead of guessing a number before seeing anything.
- **Chapters split by time** (two years / year / half-year / quarter / month — it
  picks the finest granularity that still fits the duration). Each chapter opens
  with a time label that fades in and out.
- **Every chapter has one hero shot** held nearly twice as long as the supporting
  shots. This is what creates the rhythm; split into chapters without it and you
  still have a photo reel.
- **Every chapter always gets at least one photo.** Covering the whole span is
  worth more than density: losing a year from the video means losing a piece of
  the story.
- **Cross-fades between shots**, fade in from black, fade out to black, and an
  opening title card with the name and the year range.
- **A very slow zoom within each shot** — but the zoom is centred *on the midpoint
  between the eyes*, so the face does not drift at all; only the context widens
  and narrows. The anchor stays an anchor, the frame never goes static and dead.
- **Real video clips too, with sound.** The indexer job scans every video frame in
  the library, finds the person you picked, then cuts out the best clip — sharp
  enough, bright enough, face large enough, no shake, and with context. Any
  chapter that has a clip gets one slot for it, because a moving clip is worth
  more than a photo that is only marginally better. The audio comes in *before*
  the picture and lingers *after* the picture has cut away, so it feels like
  reliving the moment rather than having sound pasted on top.
- **Faces inside video clips are anchored too.** The person moves inside the
  frame, the frame does not: the indexer stores the path the face travels through
  a series of samples, and the render step interpolates between the two nearest
  samples.

The old flipbook style is still there if you prefer it: switch to *Even spread*
mode in steps 3 and 4. Parameter details are in
[`timeline/README.md`](timeline/README.md).

## Core idea: do not redo the work Immich already did

Immich has already run Face Detection and Facial Recognition across your library,
and stored in Postgres the bbox of every face, the ArcFace vector, and who that
person is. That is the expensive part of the problem.

This tool **reads those results back** instead of detecting anything itself. That
drops the two heaviest models (SCRFD and ArcFace) and runs only two light models
for the parts Immich does not cover:

| Task | Model | Who does it |
|---|---|---|
| Find faces in **photos**, recognition vector | SCRFD + ArcFace | **Immich** already did it, just copy the results |
| Head pose, 68 3D landmarks | `1k3d68` in `buffalo_l` | this tool |
| Body pose, 17 keypoints | `yolov8n-pose` | this tool |
| Find faces in **video** over time | `det_10g` + `w600k_r50` | this tool |
| Anchor the face for rendering | no model needed | reuses the stored `kps` |

The fourth row is the only exception, and there is a specific reason for it:
Immich runs face detection on video against **exactly one thumbnail frame**.
Knowing that a clip contains person A is enough to list it, but not enough to cut
out "the best clip containing person A". The good news is that those two models
**already ship inside the `buffalo_l` bundle** the model download step pulls —
they simply went unused until now. Nothing extra to download.

All new data is written to **separate tables prefixed `fp_`** inside Immich's own
database. Immich's own tables are **read-only and never written to**.

## What you need before starting

- Immich running, with **Facial Recognition already finished** (Administration →
  Jobs). Until it finishes there is nothing to read.
- Access to Immich's Postgres (same machine or over the network).
- Immich's `UPLOAD_LOCATION` directory to mount read-only — or an API key if you
  cannot mount it.
- `ffmpeg` — already in the image, nothing to install.

No GPU required. The design targets modest machines: 4 cores, 8GB RAM, running
alongside Immich.

## Two components

| Directory | Type | Job |
|---|---|---|
| [`indexer/`](indexer/) | periodic job | Scans Immich photos, indexes head pose + body pose into Postgres |
| [`timeline/`](timeline/) | web service | 4-step UI: pick person → collect photos → tune → render video |

```
Immich (Facial Recognition already run)
        │
        ├─ indexer — run once, then periodically for new photos
        │    1 assets     → fp_asset   list of photos + videos + capture dates
        │    2 faces      → fp_face    bbox + vector, copied from Immich
        │    3 landmarks  → fp_face    yaw/pitch/roll, quality, anchor point
        │    4 bodies     → fp_body    17 keypoints, posture, torso orientation
        │    5 clips      → fp_vface   scan video frames, find the person
        │                  fp_vclip    cut the best clip + the face path
        │
        └─ timeline — long-running service
             pick person (+ date range)  →  VIDEO
               infer thresholds · split chapters · infer duration · anchor face · ffmpeg

             the "Expert" switch reopens each stage:
             1 pick clusters   one person across many clusters, or many people
             2 chosen photos   distribution by year and by chapter
             3 thresholds      drag the thresholds, every rejected photo has a reason
             4 parameters      inspect the story structure + real duration before rendering
```

## Running with Docker Compose

The fastest route. Images are prebuilt on GHCR, nothing to build.

```bash
git clone https://github.com/sinhsac/immich-life-timeline.git
cd immich-life-timeline
cp .env.example .env
# Edit .env: PG_PASSWORD and UPLOAD_LOCATION (or IMMICH_URL + IMMICH_API_KEY)

# 1. Download models — runs once, about 300MB
docker compose --profile setup up

# 2. Scan the library. The first run is slow: a few hours for tens of thousands of photos on CPU.
#    Stopping midway loses nothing, rerun and it picks up where it left off.
docker compose run --rm indexer

# 3. Open the UI
docker compose up -d timeline
# http://localhost:8080
```

Check things before a real scan:

```bash
docker compose run --rm indexer --dry-run   # test pg, test reading photos, test loading models
docker compose run --rm indexer --stats     # show progress
```

For photos uploaded to Immich later, just run `docker compose run --rm indexer`
again — it only processes what is missing rather than starting over.

## Running on Kubernetes / k3s

Sample manifests are in [`indexer/deploy/k3s.yaml`](indexer/deploy/k3s.yaml) and
[`timeline/deploy/k3s.yaml`](timeline/deploy/k3s.yaml). Fix `hostPath` for your
machine, then:

```bash
kubectl create ns media
kubectl -n media create secret generic immich-db --from-literal=password='...'
kubectl apply -f indexer/deploy/k3s.yaml
kubectl apply -f timeline/deploy/k3s.yaml

# Run a scan right now, without waiting for the schedule
kubectl -n media create job --from=cronjob/fp-indexer fp-run-1
kubectl -n media logs -f job/fp-run-1
```

`indexer` is a CronJob (`concurrencyPolicy: Forbid`), `timeline` is a
single-replica Deployment with `strategy: Recreate` — renders write into a volume,
so two pods would trample each other.

## Images

```
ghcr.io/sinhsac/immich-life-timeline/fp-indexer:latest
ghcr.io/sinhsac/immich-life-timeline/fp-timeline:latest
```

Available tags:

| Tag | Meaning |
|---|---|
| `latest` | newest build on `main` |
| `1.2.3` | released version, **use this for real deployments** |
| `v1.2.3` | same build, keeping the `v` prefix |
| `sha-<commit>` | pin to an exact commit |

If you use `latest` you must set `imagePullPolicy: Always`. With `IfNotPresent`
the node sees it already has `latest` cached and keeps using it forever, so new
builds never reach the machine — `latest` becomes meaningless. Pinning to a
version is the opposite: `IfNotPresent` is the right choice, because the tag is
immutable so there is no need to ask the registry on every startup.

If you fork, remember to switch the package to **public** under Packages →
Package settings, since GHCR defaults to private even for public repos.

To build them yourself:

```bash
DOCKER_BUILDKIT=1 docker build -f indexer/deploy/Dockerfile -t fp-indexer indexer/
DOCKER_BUILDKIT=1 docker build -f timeline/deploy/Dockerfile -t fp-timeline timeline/
```

`insightface` publishes no wheels, so it has to be compiled from source — the
indexer image is multi-stage, with the compiler confined to the build stage.

## How to use it: pick a person, press one button

The request is just **who** and (optionally) **what date range**:

- a video of person A → select A's clusters, press *Create video*
- a video of person A with person B → select A's clusters, press *+ Add another
  person*, select B's clusters. Tick *only photos containing everyone* if you want
  just the photos of them together
- a video of person A from 2000-01-01 to 2020-12-12 → add the two date fields

There is not a single slider on this path. The service infers the thresholds,
splits the chapters, infers the duration, and renders straight away.

## Four steps, behind the "Expert" switch

Flip the switch in the top corner to reopen each stage. With it off, they do not
appear in the interface at all.

**1. Pick the person.** The list comes from the person clusters Immich already
classified. Immich usually splits **one person into several clusters** across
different ages — child, teenager, adult end up as three separate clusters. You can
select several clusters at once, and the "Find clusters of the same person" button
compares centroid vectors to suggest more; select those too and search again to
widen the net step by step.

An honest warning: cosine similarity alone **cannot separate close relatives**. On
a real library, a cluster of the same person scored 0.55 while a different person
in the same household scored 0.44 — the margin is very thin. So any cluster that
already carries a **different name** is flagged and pushed to the bottom; that
signal is more trustworthy than the number. You still have to look at the photos
before choosing.

**2. Collect photos automatically.** Time is split into chapters, then a duration
budget is allocated per chapter. Without that, one 200-photo holiday takes over the
whole video and the sparse years disappear entirely.

**3. Tune.** Set the target length, the pacing, how long a chapter runs. Drag the
thresholds for head angle, sharpness, brightness, posture. The photos entering the
video are **grouped by chapter** with the hero shot marked distinctly, so you see
immediately which chapter is thin. **Every rejected photo shows a concrete
reason**, so you know which threshold to loosen instead of guessing.

**4. Render the video.** Review the story structure with the **real duration down
to the frame** before rendering, preview three frames spread far apart in time,
then render to mp4.

## Framing: the single most important parameter

`face_frac` is the distance between the eyes expressed as a fraction of the frame
width:

| `face_frac` | Result |
|---|---|
| 0.50–0.60 | Tight portrait, all context lost |
| **0.10–0.15** | Whole person and context visible — **default** |
| 0.06–0.08 | Wide shot, person small |

Because every frame uses the same `face_frac` and the same `eye_y`, the face sits
in exactly one place at exactly one size throughout the video.

If a photo is not large enough to cover the frame, the function scales it up
further. When it has to choose between "exact anchor point" and "no empty
borders", it prioritises covering the frame — the face drifts slightly off
`eye_y`. Switch to `fill=blur` if you would rather keep the full photo and accept
a blurred background in the missing area.

Photos with several people are still anchored on the person you picked, because
the anchor point comes from the specific face in `fp_face` rather than from any
face.

## Data tables

The default prefix is `fp_`; change it with `TABLE_PREFIX`.

| Table | Contents |
|---|---|
| `fp_asset` | one row per photo: capture date, preview path, processing state |
| `fp_face` | one row per face: bbox, vector, yaw/pitch/roll, quality, anchor point |
| `fp_body` | one row per person: 17 keypoints, posture, torso orientation |
| `fp_vface` | one row per face matched to a person, on each scanned video frame |
| `fp_vclip` | the chosen video clip: person, time range, score, face path |
| `fp_project` | video project: people + threshold set + frame list with chapters and hero shots |
| `fp_run` | log of every stage run |

An example of querying it directly — photos with exactly one person, facing
forward, face sharp:

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

## Runs on modest hardware

Designed for a machine with 8GB RAM / 4 CPU cores / no GPU, shared with Immich:

- The indexer is fully sequential: no threads, no process pool
- **Only one model in RAM at a time** — stage 3 releases its model before stage 4
  loads a different one. Peak RAM is around 700MB
- `ONNX_THREADS=2` caps both onnxruntime and BLAS
- `SLEEP_MS` pauses between photos to give CPU back to Immich
- The timeline service **loads no models at all** (anchoring uses the stored `kps`)
  so it needs only ~300MB RAM
- Only one render is allowed at a time

**Resumable.** Progress lives in the state column of `fp_asset`, committed per
batch. After a power cut or a kill, rerunning picks up where it left off, losing
at most one batch. On SIGTERM it commits the current batch and exits cleanly. And
a Postgres advisory lock guarantees two indexers never run in parallel.

## Security

This service **can see every photo in the library**. There is no authentication by
default — it prints a warning and the UI shows a yellow banner when `API_TOKEN` is
empty.

Set `API_TOKEN` before exposing the port beyond your own machine. Authenticate
with `?token=...` (the service sets a cookie, so once is enough) or the
`Authorization: Bearer ...` header.

The sample ingress does not enable TLS. Do not put this service on the internet
without TLS and a token.

## Known limitations

- **Video scanning is the most expensive stage.** A rough estimate on 4 CPU cores:
  a one-minute video at `VIDEO_FPS=2` takes 10–15 seconds, so 500 one-minute
  videos land around 1.5–2 hours. The job is resumable per video. Set
  `DO_VIDEO=0` if you do not need it.
- **Video scanning requires `MEDIA_ROOT`.** The `IMMICH_URL` mode cannot be used:
  downloading the entire video library over HTTP just to scan it does not make
  sense.
- **There is silence between video clips**, because still photos have no sound.
  Each clip's audio starts before its picture and lingers after the picture has
  cut away to soften the transition, but this is not a seamless mix. Turn it off
  with `audio: false`.
- **Wrong capture dates mean a wrong video.** Photos without an EXIF
  `DateTimeOriginal` fall back to the file date, which tends to clump everything
  on the scan date. The indexer prints a warning when it detects this — fix it in
  Immich, then rerun the `assets` stage.
- **A person with fewer than ~20 photos** spread over time ends up with one photo
  per chapter, which is a slideshow rather than a story.
- **No background music.** Only the real audio from the video clips, no music
  track.
- The render reads Immich's **preview** images (usually 1440px), not the
  originals. That is enough for a 720–1080 frame.
- Head pose is computed with a 3D-3D affine fit against the model's mean shape,
  not a camera-calibrated PnP. Accurate enough for filtering by angle, not for
  measurement.
- EAR (eye openness) is computed from 3D landmarks projected back to 2D, so treat
  it as a hint only.
- `posture` is inferred from geometric ratios; half-body photos where the legs are
  not visible can be guessed wrong.
- Reading photos through the API is noticeably slower than reading files, and
  **each stage downloads the entire library again**. Mount the volume if you can.

## Detailed configuration

Everything goes through environment variables. See
[`indexer/README.md`](indexer/README.md) and
[`timeline/README.md`](timeline/README.md) for the full list, the filter
thresholds, and the API reference. The UI also serves OpenAPI at `/api/docs`.

## License

[PolyForm Noncommercial License 1.0.0](LICENSE.md) — free for any
**noncommercial** purpose.

**Allowed:** personal and family use, study, research, hobby projects; modify it
freely; redistribute your modified version; use in schools, charities, public
research institutes, and government agencies.

**Not allowed:** selling it, renting it, charging for it, using it in a commercial
product or service, or anything done for business purposes.

The noncommercial condition **applies to modified versions as well**: this license
only grants rights for noncommercial purposes, so whoever receives it from you
gets exactly the same rights — nobody can fork it and relicense it under terms
that permit selling.

If you want commercial use, open an issue to discuss it.

### Three things to know before forking

**This is not open source by the OSI definition.** Licenses with a noncommercial
restriction are not OSI-approved, and GitHub will display "Other" instead of a
license name. Anyone who only accepts OSI licenses will not be able to use this
project. That is the price of the noncommercial condition, not a flaw that can be
fixed.

**Why PolyForm and not CC BY-NC-SA.** Creative Commons itself
[recommends against using CC licenses for software](https://creativecommons.org/faq/#can-i-apply-a-creative-commons-license-to-software):
they grant no patent rights and say nothing about distributing source versus
compiled binaries. PolyForm was drafted by licensing lawyers specifically for
software, includes a patent clause, and is written in plain language.

**There is no ShareAlike clause.** This license constrains *how you may use it*,
not whether you publish source. Someone can modify it privately and never share
back, as long as they stay noncommercial. If you need to force them to publish
their changes, this license will not do that for you.

*This section paraphrases the linked sources and is not a legal document. The
binding text is [LICENSE.md](LICENSE.md).*
