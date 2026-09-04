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

## Background music, and why it is structural

Photos are silent, video segments are not. Two segments separated by four photos
means five seconds of silence and then sound slamming back in. `audio_lead` and
`audio_tail` soften the two edges; they cannot fill the hole in between. Music is
what holds a continuous bed of sound under the whole thing — that is a structural
fix, not decoration, and it is the reason to add it before anything to do with
beats.

Point `MUSIC_DIR` at a directory of tracks (mounted read-only) and the tracks
appear in a dropdown in step 4, refreshed each time you open that step so
dropping a file into the directory needs no browser reload. `GET /api/music`
lists the same thing.

### Choosing a track out of a thousand

The picker is built for a library rather than a handful of files, because at any
real size the question stops being *"which of these"* and becomes *"how do I
narrow this down, and how do I hear it"*. A `<select>` with a thousand options is
not a way to choose music.

So: the server pages and filters, and the browser never holds the whole list.

| | |
|---|---|
| **Search** | Accent-insensitive on both sides, so `nhac cham` finds `Nhạc Chậm`. Multiple words must all appear but need not be adjacent, so `piano cham` finds `cham/piano-02.mp3` |
| **Folders** | Subdirectories become a filter. Copy in `slow/`, `upbeat/` and you have categories for free |
| **Sort** | name · newest · longest · shortest · largest |
| **Audition** | ▶ on any row, served with HTTP `Range` so the scrub bar works. Choosing music you cannot hear is choosing blind, and without `Range` you would have to listen from the start to judge a track |
| **Random** | Picks from the *current filter*, server-side. With a thousand tracks and one memory video, "choose one for me" is usually better than weighing a thousand options |

**Duration is measured for the page you are looking at; BPM only for the track you
select.** That split is not fussiness. Duration is an `ffprobe` header read at
20–50 ms, so thirty of them is fine. Beat detection decodes two minutes and runs
an FFT at 1–3 s per track — doing that for a listing would hang the request, and
for a thousand tracks it is 20–50 minutes of CPU competing with Immich. Once
measured, a BPM is cached and shown in the list.

With `beat_sync` on, the selected track also gets a plain-language verdict: the
cut unit it implies against your pacing, and which way to move `beat_every` if it
is off.

### Uploading

**Upload is for "I found one track, let me try it". It is the wrong tool for a
library** — pushing gigabytes through a single-worker service, one file at a time,
through a browser. For a whole collection, mount it read-only or `rsync` once.
Both mechanisms exist because they answer different needs.

Uploading means the directory has to be mounted read-write, so the tradeoff is
paid for rather than waved away:

| Guard | Why it is not optional |
|---|---|
| `MUSIC_MAX_MB` per file (30), checked **while writing** | A `Content-Length` can be absent or wrong, so the header check alone decides nothing |
| `MUSIC_MAX_TOTAL_MB` for the directory (6000) | A ceiling you can reason about |
| `MUSIC_MIN_FREE_MB` free space (3000) | **The one that matters.** The two fixed ceilings know nothing about how much disk is left, and `MUSIC_DIR` usually shares a partition with the container runtime — 6 GB of music is harmless with 200 GB free and a disaster with 19 GB. Filling the disk does not fail the upload, it takes the node down |
| `ffprobe` must find an audio stream | A `.mp3` can be anything. The only proof it is music is that a decoder can read it — and this catches corrupt files at upload rather than mid-render |
| Written as `.part`, then renamed atomically | The UI re-reads the list, so a half-uploaded file must never appear as a selectable track |

Several files at once, with **per-file results**: one rejected file must not hide
the ones that went in.

Names are sanitised down to the basename before anything else, so
`a/../../b.mp3` becomes `b.mp3` rather than something creative. Uploads always
land directly in `MUSIC_DIR`; subdirectories are still read if you copy them in
by hand. An existing name is never overwritten silently — a second `01.mp3`
becomes `01-2.mp3`, because a project pointing at the old track must not quietly
change music.

Reading, writing and deleting all go through the same `resolve()`, so there is no
second code path with weaker checks. `tl/music.py` rejects absolute paths, `..`
segments, and anything that resolves outside the directory after symlinks.

**Set `API_TOKEN` if you enable uploads.** The upload and delete endpoints sit
behind the same middleware as everything else, so a token protects them — but with
no token, anything on your network can write files to the host.

An unresolvable name is **dropped rather than raising**, because one bad name
should not fail a whole render. Silent on the server would be silent for the
user too, so step 4 says so explicitly when the track it sent back does not
match the one selected.

| Parameter | Default | Notes |
|---|---|---|
| `music` | null | Track name relative to `MUSIC_DIR`. Unresolvable → rendered silent, not an error |
| `music_gain` | -14 dB | Music is a bed, not the subject |
| `music_duck` | -11 dB | How far music drops while real segment audio plays |
| `music_fade_in` / `music_fade_out` | 1.2 / 2.5 | |
| `music_loop` | true | Track shorter than the video repeats, via `-stream_loop` |

**Ducking is computed, not compressed.** The textbook tool is
`sidechaincompress`, but here the exact seconds where real audio plays are already
known — `audio_plan()` works them out before ffmpeg is ever called. So the
envelope is built directly as a `volume` expression: a trapezoid per interval
ramping over `DUCK_RAMP` (0.6s), combined with `max()` so two nearby intervals do
not stack into a double drop. A compressor has to infer all of that from signal
amplitude, and the amount it ducks depends on threshold and ratio, so "drop by
exactly 11 dB" is not something you can ask it for. The computed envelope is
exact, identical between runs, and checkable with a few lines of Python.

Intervals are merged when they are closer than `2 × DUCK_RAMP`, and merged harder
until there are at most 40 of them — ffmpeg evaluates that expression on every
audio frame.

## Cutting on the beat

`beat_sync: true` (requires `music`) snaps shot boundaries onto the beat grid.

It does **not** replace the hero/beat structure — it changes the *unit*. Each
shot's natural length (`hold_hero`, `hold_beat`, or a segment's own duration) is
kept as the target, and the real boundary is pulled to the nearest beat. Fast
music gives quick cuts, slow music gives long ones, and chapters and hero shots
survive intact. Expect the duration to drift a little from the estimate; that is
inherent to snapping.

`beat_every: 2` cuts every second beat — at 140 BPM a single beat is 0.43s, too
fast for a memory video.

Video segments are only ever snapped **down** to the previous beat, never up.
Snapping up would ask for frames the segment does not have, and `_ClipSrc` holds
the last frame instead — a freeze in the middle of a moving shot.

Beat detection has two paths, same output either way:

- **librosa**, if installed. Its beat tracker follows tempo changes within a track
  using dynamic programming, which the fallback cannot.
- **numpy + ffmpeg** otherwise, which is what ships. Spectral flux → autocorrelation
  for tempo → best phase offset. Assumes tempo is roughly constant, true for most
  background music.

librosa is not a hard dependency because it drags in numba, scipy and soundfile —
heavier than the rest of this service put together, on a box already shared with
Immich. `pip install librosa` and it gets used automatically.

Known limitation of the fallback: **octave errors.** On a synthetic 120 BPM click
track it reports 60 BPM — a real ambiguity in tempo estimation, not a bug in the
arithmetic. Cuts still land on beats, just every second one. `beat_every`
compensates, and librosa mostly avoids it. Tracks with no clear pulse (free piano,
rain, spoken word) return no beats at all, and rendering falls back to normal
storytelling pacing.

Grids are cached per (file, mtime): a track's beats never change, and detection
decodes the whole file and runs an FFT.

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

All five stages appear there, `clips` included. It is worth stating why that
matters: `clips` is the most expensive stage in the whole job by a wide margin,
and for a long time it was the only one the page said nothing about — every
figure it needed was already sitting in `fp_asset`, just never read. Its
denominator is the **number of videos**, not the number of assets: a library of
20,000 photos and 500 videos reads 2% forever if you divide by the wrong total.
The `smiles` stage is counted against the number of faces that have landmarks,
since that is what it derives the score from.

The two features that can be switched on while the data behind them does not yet
exist — smile preference and video clips — say so where the switch is rather than
quietly doing nothing.

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

### Pose is scored, not gated

With `soft_pose` on (the default), the pose and sharpness numbers below are
**knees, not walls**: cross one and the photo starts losing points, on a ramp up
to the matching `hard_*` limit. Only the `hard_*` limits actually reject.

This changed because the old behaviour cut at `max_yaw = 22°`, and 22° is still
very nearly looking straight at the lens. Photos of someone grinning back over
their shoulder, mid-run, blowing out candles — often the best photos anyone owns
— were thrown out before they were ever scored, and what survived was passport
shots and static selfies. Scoring lets them compete: they just have to make up
the deficit with sharpness, face size, or a smile.

Set `soft_pose: false` for the old cut-hard behaviour.

| Threshold | Knee | Hard | Meaning |
|---|---|---|---|
| `max_yaw` / `hard_yaw` | 22° | 55° | Turned left/right |
| `max_pitch` / `hard_pitch` | 18° | 42° | Tilted up/down |
| `max_roll` / `hard_roll` | 20° | 45° | Head tilted sideways |
| `min_frontality` / `hard_frontality` | 0.45 | 0.15 | Head pose + symmetry, 0..1 |
| `min_sharp` / `hard_sharp` | 60 | 18 | Laplacian variance on a 128px crop |
| `min_ear` | 0.15 | — | Rejects closed eyes. Still a hard gate |

Maximum deductions: yaw 18, frontality 14, sharpness 16, pitch 12, roll 8 — on
the roughly 120-point quality scale.

### Smiling

`fp_face.smile` is a 0..1 score derived from the 68 landmarks already stored —
mouth-corner lift, mouth width against interocular distance, and lip opening. No
new model, and no re-scan: `job.py --stage smiles` fills the column from the
`lmk68` blobs already in the database.

It is worth its own row because everything else here measures a *technically
clean face*, and nothing measured whether the moment was worth keeping. A smile
is the closest thing to "a moment" this metric set can reach.

| Threshold | Default | Meaning |
|---|---|---|
| `prefer_smile` | true | Adds up to 22 points for a smile |
| `min_smile` | 0.0 | Hard gate, 0 = off. A solemn portrait is part of the story too |

A photo turned 35° with a 0.9 smile now outscores a dead-straight, unsmiling one.
That is the intended change.

### Duplicate shots

| Threshold | Default | Meaning |
|---|---|---|
| `dedup_seconds` | 8.0 | Photos this close together are one moment — keep the best. 0 = off |

Bursts collapse; a burst is capped at 3× `dedup_seconds` so a whole afternoon of
photos taken 5s apart does not shrink to one frame. Video segments are never
collapsed: every segment from one video shares the video's `taken_at`, and the
indexer has already guaranteed they do not overlap.

This deliberately does **not** use cosine distance on `fp_face.emb`. That is an
*identity* vector — two photos of the same person five years apart still score
high, which is exactly what it is for. The right signal for "same scene" is
Immich's CLIP embedding in the `smart_search` table, which this does not read yet.

### Image quality

| Threshold | Default | Meaning |
|---|---|---|
| `min_eye_ratio` | 0.030 | Eyes at least 3% of the long edge apart — rejects faces that are too small |
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
