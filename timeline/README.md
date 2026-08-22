# fp-timeline — build a life-journey video of one person from an Immich library

A long-running web service on k3s.

## The request is only ever: who, and when

```
"make a video of Mr. A"
"make a video of Mr. A with Ms. B"
"make a video of Mr. A from 1/1/2000 to 12/12/2020"
```

That is everything the UI asks for. No duration, no photo count, no sliders —
**duration is an outcome, not an input**. A single request (`POST /api/videos`)
creates the project, infers the filter thresholds, splits the timeline into
chapters, infers the duration, and starts rendering.

Once you have watched the result, two buttons — **Shorter / Longer** — let you
react to what you actually saw instead of guessing a number before seeing
anything.

The four fine-tuning steps are all still there, but they live **behind the
"Expert" toggle** in the top corner. With the toggle off they simply do not exist
in the interface — done with a single CSS class (`.adv` + `body.expert`) rather
than two separate UIs, so the two can never drift apart.

## A video that tells a story, not a photo reel

The first version spaced photos out perfectly evenly — one photo every 30 days —
and gave each one exactly 1/6 second. The result was a reel running end to end
where every frame mattered equally and the face changed constantly. Ten seconds
in you are tired, because there is nowhere for the eye to rest and nothing
distinguishing an ordinary afternoon from a graduation day.

`story` mode (the default) builds it differently:

| | Old way (`even` / `flip`) | Storytelling (`story`) |
|---|---|---|
| Photo count | you set `bucket_days` and live with whatever comes out | **derived from `target_seconds`** |
| Duration | = photo count / fps, uncontrolled | = the budget you set |
| Structure | flat | split into **chapters** by time |
| Pacing | every photo 1/6 second | **hero** shots 1.7s, supporting shots 1.0s |
| Transitions | hard cuts (or blending the entire video) | cross-dissolve between every shot |
| Framing | static | very slow zoom **around the eye anchor** |
| Time | a year label runs the whole video | **chapter label cards** fade in, then fade away |
| Videos in the library | dropped entirely | **best segment cut out** and spliced in |

Four things worth calling out:

**Photo count follows from duration, not the other way around.** You say "60
seconds", the service works out how many photos that needs and divides them
across the chapters. A 500-photo library and a 50,000-photo library both give you
a 60-second video — only the thickness of each chapter differs.

**Every chapter gets at least one photo.** Covering the whole span is worth more
than density: losing a year from the video means losing a stretch of the story.
What is left of the budget is split by `sqrt(eligible photos) / photos already
assigned` — the square root so a 300-photo holiday does not eat everything, and
the divisor so nothing clumps up.

**Every chapter has one hero shot** (its highest-scoring photo) held for nearly
twice as long. This is what creates the rhythm. Without it, splitting into
chapters still leaves you with a photo reel.

**Zoom does not break the anchor.** The transform always puts the midpoint
between the eyes in exactly the same spot, so the zoom happens around that very
point: the face does not shift at all, only the surrounding context widens and
narrows. The anchor stays an anchor, but the framing stops being static.

The old modes are untouched (`mode: even` in step 3, `mode: flip` in step 4) for
anyone who genuinely wants the flipbook look.

## Real video segments, not just photos

The indexer job (stage `clips`) scans every frame of every video in the library,
finds the person you selected, cuts out the best segment, and stores it in
`fp_vclip`. This service reads those results back and splices the clips in
between the photos.

**Clips go through exactly the same filters as photos.** The query returns
identically named columns (`sharp`, `bright`, `frontality`, `eye_ratio`) so
`_reject()` is one shared function — no two parallel filtering branches that
slowly drift apart. The only difference is a **negative** `fidx` (`-1 - cidx`) to
tell them apart, plus two thresholds of their own:

| Threshold | Default | Meaning |
|---|---|---|
| `use_clips` | true | Off means the video is photos only |
| `max_clip_motion` | 2.6 | Maximum shake, measured in "face widths per second" |
| `min_clip_seconds` | 0.8 | Shorter segments are dropped |

**A chapter that has a clip gets a guaranteed slot.** This cannot be left to
scoring: in a 25-photo chapter the top photo almost always beats an average video
segment, and the whole video feature would then never show up at all. The rule
is: if a chapter has a clip that has not been picked, replace its lowest-scoring
photo with the best clip — unless the clip is more than 25% worse than the photo
it would replace (`CLIP_TRADE = 0.75`). Give up some quality score to gain motion,
but never trade an outstanding photo for a mediocre clip.

**A clip carries its own length.** No squeezing a 3.2-second clip into one
second, and no Ken Burns zoom on top of it (it already moves on its own). So once
selection is done, `story.trim_to()` recomputes the real total and trims
low-scoring supporting photos if it overshoots the ceiling — trimming at the
selection step rather than letting the render step quietly drop things, so what
you see in step 3 is exactly what ends up in the video.

Two exceptions to the pacing:

- **An opening clip is not extended for the title card.** Extending it would show
  the viewer a frozen frame for 2.4 seconds before the clip starts moving.
  Instead the title appears *over* the running clip.
- **A closing clip is the opposite**: hold longer, then fade to black. For a video
  segment that means a frozen frame at the end — the conventional way to close
  out, and it costs no seconds of actual content.

### Anchoring a moving face

This is where clips differ fundamentally from stills: **the face moves throughout
the segment**. Anchor to a position taken from a single sample point and by the
end of the clip the face has drifted out of place.

The indexer stores a `track` — `kps` at each sampled point — and `_ClipSrc`
linearly interpolates between the two nearest samples for every output frame. The
result: the face holds one position for the whole segment, just like with stills.
**The person inside the frame moves, the framing does not.**

Reading is sequential, not a seek per frame: seeking back to a keyframe for every
frame would be many times slower. There is exactly **one** seek, to the start of
the segment (40ms early, because with B-frame codecs `POS_MSEC` lands on the
nearest preceding keyframe).

**`MEDIA_ROOT` is required.** `IMMICH_URL` mode cannot read video files;
`preflight()` drops every clip before duration is computed, so you still get a
video, just photos only.

`flip` mode ignores clips entirely — it is a sequence of stills, there is no place
for them.

### Audio: J-cuts and L-cuts

Stills have no audio, so the gaps between clips are silent. Pasting each clip's
audio over exactly its own picture range would turn every clip into a block of
sound with a door opening and closing around it — literally "inserted audio".

So audio **comes in before the picture** (`audio_lead`, default 0.5s) and **stays
on after the picture has cut away** (`audio_tail`, 0.8s). In editing terms these
are the J-cut and the L-cut: the ear hears the new space before the eye sees it,
and that space does not stop dead at the same instant as the picture.

| Parameter | Default | Notes |
|---|---|---|
| `audio` | true | Off means a completely silent video |
| `audio_lead` | 0.5 | How many seconds audio leads the picture |
| `audio_tail` | 0.8 | How long audio stays on after the picture has cut |
| `audio_fade_in` / `audio_fade_out` | 0.35 / 0.6 | Never longer than half the segment |
| `audio_normalize` | true | `dynaudnorm` evens out levels across segments |
| `audio_gain` | 0 | dB |

Three places get clamped, and skipping any one of them puts audio out of sync
with the picture **for the entire segment** without ffmpeg reporting anything:

- A clip at the very start of the video cannot start its audio before second 0 →
  `lead` is clamped to the clip's position, otherwise `adelay` goes negative.
- A clip starting at the beginning of its *source file* cannot read audio from
  before second 0 of that file → `lead` is clamped to `t_start`, otherwise `atrim`
  goes negative.
- `tail` must not read past the end of the source file (`src_dur_ms`).

The trimming is done by `-ss` / `-t` **on the input**, not by `atrim` in the
filter graph — trimming at the input means ffmpeg only decodes the part it needs,
and there is no risk of trimming twice.

The filter chain: each input goes through `aformat` (clips differ in sample rate
and channel count, and `amix` demands they match), then `afade` at both ends plus
`adelay` to place it at the right second, then `amix` with
**`normalize=0`** — `amix` divides volume by the number of inputs by default, so
with 8 clips each one drops to 1/8 and everything sounds like whispering. The
segments barely overlap, so summing them straight is correct, and `alimiter`
catches the peaks downstream. `apad=whole_dur` covers the full video length
because the last clip usually ends before the picture does.

The audio mux runs **after** the picture has finished encoding, with `-c:v copy`
so nothing is re-encoded. If this step fails for any reason you still have the
silent video intact — instead of losing the video altogether. Clips with no audio
track are removed from the plan using `ffprobe`: one input without audio makes the
whole `filter_complex` fail, taking the audio of every other segment with it.

Expert mode brings back the four steps:

1. **Select person** — pick several clusters of the same person, with suggestions to widen the net
2. **Selected photos** — see the distribution by year and by chapter
3. **Filter thresholds** — drag the thresholds; every rejected photo has a concrete reason
4. **Render settings** — preview the framing and the story structure before rendering

The default path turns expert mode on by itself and jumps straight to step 3 when
**fewer than 2 photos** are selected — ffmpeg needs at least 2 frames, so instead
of rendering and then reporting a cryptic error, the UI takes you to the exact
place where you can loosen thresholds, with the reason attached.

## Videos of several people

Selecting several clusters means **the same person** by default — Immich often
splits one person into several clusters by age. For a video of two people, pick
the first person's clusters, hit **+ Add another person**, then pick the second
person's clusters. There is no way to infer the grouping from one mixed bag of
clusters, so each person has to be committed one at a time.

Tick **only photos containing all of them** to keep just the photos where both
appear; untick it to take photos of any of them, merged into one shared timeline.

A single photo produces **one** frame even if it contains several faces belonging
to selected people — otherwise the same photo would show up twice in the video.

### Anchoring two faces

In a photo with both people the anchor is no longer a single face. But you
**cannot** treat the two eye centres as "a pair of eyes" and let `level` rotate
them flat: a tall parent and a short child are 30° apart, which wrecks the photo.

`media.pair_kps()` returns two **virtual points on a horizontal line**, separated
by the real distance between the two people, placed around their midpoint. The
result: rotation angle 0, the midpoint of the pair always in the same spot, and
the distance between them always a fixed fraction of the frame width
(`pair_frac`, default 0.30) — the further apart they stand, the wider the framing
opens to fit them. Photos containing only one person from the group still anchor
normally on that one face instead of being rejected.

Indexing progress lives on its own **Statistics page** on the nav bar. It used to
sit in the header, where it took up room across all four steps even when
collapsed. The nav label shows the overall percentage so you know when it is worth
opening, plus a green dot while a stage is running.

Run this after `../indexer` has finished. The service **loads no ML models at
all** — alignment uses the `kps` already stored in `fp_face`, so RAM stays around
300MB.

## Why you have to select several clusters

Immich splits **one person into several clusters** when the time span is long —
child, teenager, adult usually end up as three different clusters. Pick only one
cluster and the video loses those other stretches entirely.

`fp_project.person_ids` holds the list of clusters and `select.fetch()` filters
with `person_id = ANY(...)`. `GET /api/people/{id}/similar` suggests clusters of
the same person: it averages the ArcFace embeddings of the 16 highest-scoring
faces per cluster (enough for a stable centroid without pulling 89k vectors),
normalises, then compares by cosine. Pass `seeds=` with the clusters already
selected and it compares against the combined centroid of the whole group — select
more, call again, and the net widens step by step.

**A real limitation, worth knowing before you trust the results:** cosine alone
cannot separate family members. On a real library, clusters of the same person
scored around 0.54 while a different, already-named person scored 0.435 — a very
narrow margin. So any cluster carrying a **different name** from the group being
selected is flagged `name_conflict` and pushed to the bottom; that is a more
trustworthy signal than the cosine number. You still have to look at the photos
before selecting.

Centroids are cached for 10 minutes because computing them takes tens of seconds.

## Why the face has to be anchored

Splice photos together in date order and the face jumps all over the place,
unwatchable. Anchoring puts the eyes in the same position at the same scale in
every frame. This is what decides whether the video works at all.

But anchoring **does not mean cropping tight to the face**. The face is only the
anchor point; the framing should still keep as much context as possible. The
parameter that decides this is `face_frac` — the eye-to-eye distance as a fraction
of the frame width:

| `face_frac` | Result |
|---|---|
| 0.50–0.60 | Tight portrait, all context gone |
| **0.10–0.15** | Whole body plus surroundings visible — **default** |
| 0.06–0.08 | Wide shot, person small |

Photos containing other people still anchor on the person you selected, because
`kps` comes from that person's specific `fidx` in `fp_face`.

When a photo is not large enough to cover the frame the function scales it up
further, and when it has to choose between "exact anchor position" and "no empty
borders" it prioritises covering the frame — the face ends up slightly off
`eye_y`. Switch to `fill=blur` if you would rather keep the whole photo and accept
a blurred background where it falls short.

## Architecture

```
Immich (docker compose)          k3s (namespace media)
┌──────────────────┐            ┌───────────────────────┐
│ immich-server    │            │ Job  fp-indexer       │  CronJob
│ immich-machine-  │            │  → fp_asset/face/body │
│   learning       │            ├───────────────────────┤
│ postgres  ───────┼────────────┤ Svc  fp-timeline      │  Deployment
└──────────────────┘  same db   │  → fp_project/render  │
      │                         └───────────────────────┘
      └── UPLOAD_LOCATION ──── mounted read-only into both
```

Tables read: `fp_asset`, `fp_face`, `fp_body` (created by the indexer).
Tables written: `fp_project`, `fp_project_frame`, `fp_render`.
Immich tables: read only, never written.

## Configuration

Env vars, see `.env.example`. Required: `PG_PASSWORD`, `MEDIA_ROOT`.
Strongly recommended: `API_TOKEN`.

## Running locally

```bash
pip install -r requirements.txt
python app.py --check          # check pg / photos / ffmpeg, then exit
python app.py                  # http://localhost:8080
```

Needs `ffmpeg` on the PATH. Without it the first three steps still work, only the
render step fails.

## Deploying to k3s

```bash
kubectl -n media create secret generic fp-timeline \
    --from-literal=pg-password='...' \
    --from-literal=api-token="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
docker build -f deploy/Dockerfile -t fp-timeline:1.0.0 .
kubectl apply -f deploy/k3s.yaml
```

Point `hostPath` at Immich's upload directory. `replicas: 1` +
`strategy: Recreate` is deliberate: rendering runs on a background thread and
writes to the PVC, so two pods would trample each other.

## Security

This service can see **every family photo you have**. There is no authentication
by default — the service prints a warning and the UI shows a yellow banner when
`API_TOKEN` is empty.

Set `API_TOKEN` before exposing the port beyond your local network. Access it with
`?token=...` or the `Authorization: Bearer ...` header.
The Ingress in `deploy/k3s.yaml` has TLS off — add cert-manager if it goes out to
the internet.

The token is accepted from three sources: the header, `?token=`, and a **cookie**.
The cookie is not for convenience: the browser loads `style.css` / `app.js` via
`<link>` and `<script>` tags, which cannot send a header, and `index.html` does
not append `?token=` to them either. Come in once with `?token=`, the service sets
a cookie (HttpOnly, 30 days), and every request after that goes through on its
own. Remove the cookie and turning the token on gets you the page as bare HTML.

## Filter thresholds

Head pose group (from `1k3d68`):

| Threshold | Default | Meaning |
|---|---|---|
| `max_yaw` | 22° | Turned left/right |
| `max_pitch` | 18° | Tilted up/down |
| `max_roll` | 20° | Head tilted sideways |
| `min_frontality` | 0.45 | Combines head pose + symmetry, 0..1 |
| `min_ear` | 0.15 | Rejects photos with closed eyes |

Image quality group:

| Threshold | Default | Meaning |
|---|---|---|
| `min_eye_ratio` | 0.030 | Eyes at least 3% of the long edge apart — rejects faces that are too small |
| `min_sharp` | 60 | Laplacian variance on a 128px crop |
| `bright_min/max` | 45 / 215 | Rejects blown-out or too-dark photos |

Other people in the photo group:

| Threshold | Default | Meaning |
|---|---|---|
| `allow_others` | **true** | Accept photos containing other people |
| `max_faces` | 0 | Face count limit, 0 = no limit |

Body pose group (from `yolov8n-pose`):

| Threshold | Default |
|---|---|
| `postures` | standing, sitting, unknown |
| `orientations` | front, side, unknown |
| `allow_missing_body` | true — accept even when no body was detected |
| `use_body` | true — off means body data is ignored entirely |

Storytelling group — decides **which photos and how many**:

| Threshold | Default | Meaning |
|---|---|---|
| `mode` | `story` | `story` splits into chapters \| `even` spaces evenly like the old version |
| `target_seconds` | **null** | `null` = inferred from the data. A number = forced duration |
| `pace` | `normal` | `slow` 2.4/1.5s · `normal` 1.7/1.0s · `quick` 1.2/0.7s · `snap` 0.8/0.45s (hero/supporting) |
| `chapter_by` | `auto` | `years2` \| `year` \| `half` \| `quarter` \| `month` |
| `max_per_chapter` | 6 | Photo ceiling per chapter, stops one trip from taking over the video |

### How the duration is inferred

Each chapter decides its own thickness from how many photos it has that pass the
thresholds, growing by `log2`: 1 photo → 1, 3 photos → 2, 7 → 3, 15 → 4, 31 → 5.
Logarithmic because the weight of a memory is not proportional to how many photos
got taken — a 300-photo trip is not worth 100 times a 3-photo afternoon, it is
worth a few times as much. Add it all up and that is the video length, then cap it
at 150 seconds by shaving down the chapters that got the most.

Measured on generated data: 40 photos / 4 years → 15 photos, 8 quarterly
chapters, **24 seconds**. 3000 photos / 14 years → 83 photos, 15 yearly chapters,
**97 seconds**. Same algorithm, nobody has to type anything.

Set `target_seconds` to a number and it switches to the budget path: photos are
allocated to fit that number. Send `null` again to go back to inference.

### How `chapter_by: auto` decides

Three constraints pull against each other, all considered together:

1. **Fit the budget** — every chapter needs at least one hero shot.
2. **As fine-grained as possible** within what is left, not as coarse: a 13-year
   journey becomes 13 one-year chapters rather than being merged into 7 two-year
   chapters — merging means some years get skipped entirely.
3. **Chapters must be continuous.** This is the easiest thing to get wrong: 40
   photos spread over 4 years split by month gives 13 chapters across 48 months
   (35 empty months) — the labels read like a scatter of disconnected dates,
   *"March 2019, July 2019, November 2020"*, rather than a progression. Split by
   quarter and 12 of 16 quarters have photos, far more continuous. The threshold is
   `MIN_DENSITY = 0.4`.

Chapter count is clamped to 3–18; more than that and the chapter labels flicker
past as noise.

If you push `target_seconds` up and the video does not get any longer there are
only two possible reasons, and the UI says which one: every chapter has hit its
`max_per_chapter` ceiling, or the photos passing the filter thresholds have all
been used.

Even-spacing-over-time group — only used when `mode: even`:

| Threshold | Default | Meaning |
|---|---|---|
| `bucket_days` | inferred | Splits the timeline into equal buckets |
| `per_bucket` | 1 | Keep the N highest-scoring photos per bucket |

The first time you open a person, the service infers `bucket_days` from the
density of the data to land somewhere around 150–400 frames, so anyone switching
to this mode is not staring at a meaningless number.

## Video settings

Framing, used by both modes:

| Parameter | Default | Notes |
|---|---|---|
| `size` | 900 | **Long edge**, rounded to even for libx264 |
| `aspect` | 4:3 | 1:1, 4:3, 3:2, 16:9, 3:4, 2:3, 9:16 |
| `face_frac` | 0.12 | Eye-to-eye distance / frame width. See the table above |
| `eye_y` | 0.33 | Vertical eye position — raise it to show more of the body |
| `anchor_x` | 0.5 | Horizontal eye position |
| `fill` | crop | `crop` scales up just enough to cover, cutting the edges. `blur` keeps the whole photo with a blurred background |
| `level` | true | Rotate so the eyes are level |
| `pair_frac` | 0.30 | Two-person video: distance **between the two people** / frame width |
| `label` | none | Time label in the lower corner: none, year, month, date |

`mode: story` only:

| Parameter | Default | Notes |
|---|---|---|
| `out_fps` | 24 | The video's actual fps |
| `motion` | subtle | Ken Burns zoom: none 0 · subtle 3.5% · normal 7% · strong 12% |
| `title` | true | Opening card: person's name + year range |
| `title_seconds` | 2.4 | The opening card sits **on top of the first photo**, not on its own black screen |
| `chapter_card` | true | Show the chapter label when a new chapter starts |
| `card_seconds` | 1.8 | How long the chapter label stays before fading |
| `birth_year` | — | If set, chapter labels also show "age N" |
| `arc` | true | First and last chapters run 12% slower |
| `intro_s` | 0.8 | Fade up from black |
| `outro_s` | 1.6 | Hold longer, then close out to black |
| `xfade` | follows `pace` | Overrides the cross-dissolve length |

`mode: flip` only (the old way):

| Parameter | Default | Notes |
|---|---|---|
| `fps` | 6 | Photos per second |
| `smooth` | blend | Blending via the `framerate` filter — far cheaper than `minterpolate` |

The old `eye_dx` is still accepted, converted as `face_frac = 2 × eye_dx`.

Only **one render at a time** is allowed (an in-process lock) so it does not
overwhelm Immich.

### Cross-dissolves do not make the video longer

Each shot occupies `hold` frames on the timeline, and the next shot **starts
overlapping** the last `xfade` frames of the previous one:

```
shot i occupies  [start_i, start_i + hold_i + xfade_i)
start_(i+1) = start_i + hold_i
total frames = Σ hold_i
```

That is what makes the total duration derivable to the exact frame before a single
pixel is rendered — `POST /api/projects/{id}/storyboard` returns precisely that
number, computed by the same function the render step uses.

### Why frames are generated with numpy instead of ffmpeg filters

ffmpeg's `xfade` + `zoompan` can do this, but a chain of 60 clips produces an
enormous filtergraph: heavy on RAM, hard to read the log when something breaks,
and next to impossible to derive an exact frame count from. Here every output
frame is **one `warpAffine`** — easy to control, countable, and still fast because
the preview images are only 1440px. Frames are pushed straight into ffmpeg's stdin
as rawvideo: no jpgs written to disk, no double encode.

A shot with no zoom (`motion: none`) is warped **once** and reused for all hundred
or so of its frames.

### Accented text

Chapter labels ("March 2019"), ages ("age 6") and people's names need a real font
— OpenCV's HERSHEY fonts are ASCII only. `tl/textdraw.py` uses Pillow with a TTF
font located via `FONT_FILE` and then the usual system paths; the image ships
`fonts-dejavu-core`. With no font available it strips accents automatically and
keeps running — `/api/health` reports `text.ok = false` and the UI shows a yellow
banner.

Text is rendered into a **sprite cache keyed by (string, size)** and then pasted
repeatedly at different alphas, so a fade is just an alpha multiply rather than
drawing the text hundreds of times over.

## API

`GET /api/docs` has the full OpenAPI spec. The main endpoints:

```
GET    /api/health                     indexer / ffmpeg / auth status
GET    /api/people                     list of clusters
GET    /api/people/{id}/similar        suggest clusters of the same person (seeds= to widen)
GET    /api/progress                   indexer job progress
POST   /api/videos                     ONE-STEP PATH: pick a person → get a video
POST   /api/projects                   create a project, selecting photos immediately
GET    /api/projects/{id}/result        filter results + rejection reasons + chapter summary
PATCH  /api/projects/{id}/filters       change thresholds, recompute
POST   /api/projects/{id}/exclude       drop / restore a photo
POST   /api/projects/{id}/storyboard    story structure + real duration
POST   /api/projects/{id}/render        render the video
GET    /api/renders/{id}                progress
GET    /api/renders/{id}/video          download the mp4
GET    /api/thumb/{asset}/{fidx}        face thumbnail (negative fidx = video clip)
GET    /api/aligned/{asset}/{fidx}      preview an aligned frame
```

## Dependency on the indexer

The service needs `fp_face.kps` stored as **normalised 0..1 coordinates**. The
first version of the indexer stored pixels of the resized image — impossible to
re-align at a different size. Fixed, along with a new `eye_ratio` column.

If you ran the indexer before that fix:

```bash
cd ../indexer
python job.py --reset landmarks
python job.py --stage landmarks
```

`app.py --check` and `/api/health` will tell you if `kps` is still missing.

## Known limitations

- `MAX_FRAMES` caps out at 1200 frames. Above that, alignment takes a long time
  and the video is too long anyway.
- Rendering reads Immich's **preview** images (usually 1440px), not the originals.
  Enough for a 720–1024 frame. For anything higher the indexer would have to store
  `originalPath` as well.
- Photos with no EXIF capture date fall back to the file date, which tends to
  clump everything onto the scan date. The indexer job prints a warning when it
  detects this; fix it in Immich and run again.
- A person with fewer than roughly 20 photos spread over time gives a jumpy video,
  not a smooth one.

## Testing

```bash
python selftest.py                    # no Postgres / Immich / ffmpeg needed
python selftest.py --dump /tmp/frames  # also writes a few sample frames to eyeball
```

This works because the most fragile part happens to be pure computation. The
script stuffs an empty module into `sys.modules` for `psycopg` to get past the
import, then swaps `media.load` for generated images and the ffmpeg pipe for a
counter. It verifies:

- chapter splitting, and that `auto` picks the finest granularity that still fits
  the budget
- 30/60/120-second budgets produce matching durations, with no empty chapter
- storyboard: `Σ hold = total frames`, `hold ≥ xfade` (never three layers
  overlapping), consecutive shot `start` values line up, hero shots held longer
  than supporting shots
- duration inference: a dense library gives a longer video than a thin one, both
  under the 150-second ceiling; setting `target_seconds` by hand turns inference
  off, sending `null` turns it back on
- chapter continuity: no splitting by month when most months are empty
- person grouping: `["a","b"]` is one person with two clusters while
  `[["a","b"],["c"]]` is two people; `together` keeps only photos containing all of
  them; one photo yields one frame
- two-face anchoring: the two anchor points are horizontal (no image rotation), the
  midpoint sits exactly between the two people, the separation matches the real
  distance, and there is no division by zero when the two faces coincide
- audio: J-cuts/L-cuts land in the right place, `adelay` is never negative,
  `atrim` is never negative, `tail` never reads past the end of the file,
  `amix normalize=0`, `apad` covers the full duration
- clips: they get selected into the video, shaky clips are rejected with a reason,
  `use_clips=false` lets none through, clips keep their real length, no Ken Burns
  zoom is applied, the total duration is capped, and an opening clip is not
  extended for the title card
- **anchoring a moving face**: generates a real mp4 with `cv2.VideoWriter`
  containing a bright block moving at a constant rate, builds the matching `track`,
  then checks the subject sits on the anchor point in every frame and **does not
  drift** across the segment
- accented text rendering, and that lower alpha comes out fainter
- the frame-building loop writes **exactly** the frame count the storyboard
  promised, the first and last frames are near black (fade up/close out), and the
  title card / chapter labels really do produce a darkened layer plus white
  lettering on real frames

CI runs this script before building images (the `check` job in
`.github/workflows/build-images.yml`), so broken logic means no image gets pushed
to GHCR.

The parts that need Postgres/ffmpeg get verified on the target machine with
`python app.py --check` and `GET /api/health`.
