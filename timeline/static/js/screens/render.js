// Screen 4 — render settings, the storyboard, and the framing preview.
//
// The music LIBRARY lives on its own screen; what stays here is the part that
// belongs to one render: which track, how loud, and whether to cut on the beat.

import {$, debounce, esc, num, toast} from '../core/dom.js';
import {alignedUrl, api} from '../core/api.js';
import {S} from '../core/state.js';
import {go, group, screen} from '../core/router.js';
import {renderPick, syncMusic} from './music.js';

// Ranges that mirror into <output id="o_<name>">. The three in SB_RANGE also move
// the duration, so they queue a storyboard; the rest never change the total and
// must not cost a round trip.
const RANGES = ['r_audio_lead', 'r_audio_tail', 'r_audio_fade_in',
  'r_audio_fade_out', 'r_audio_gain', 'r_music_gain', 'r_music_duck',
  'r_music_fade_in', 'r_music_fade_out', 'r_beat_every', 'r_title_seconds',
  'r_card_seconds', 'r_intro_s', 'r_outro_s', 'r_xfade', 'r_anchor_x',
  'r_pair_frac', 'r_crf', 'r_fps', 'r_eye_y', 'r_face_frac'];
const SB_RANGE = new Set(['r_beat_every', 'r_title_seconds', 'r_outro_s']);
const PREV_RANGE = new Set(['r_eye_y', 'r_face_frac']);

// ------------------------------------------------------------ sidebar groups
group({
  key: 'framing', title: 'Framing', screens: ['4'], open: true,
  html: `
    <label>Aspect ratio
      <select id="r_aspect">
        <option value="4:3" selected>4:3 landscape</option>
        <option value="3:2">3:2 landscape</option>
        <option value="16:9">16:9 landscape</option>
        <option value="1:1">1:1 square</option>
        <option value="3:4">3:4 portrait</option>
        <option value="9:16">9:16 portrait (phone)</option>
      </select></label>
    <label>Long edge
      <select id="r_size">
        <option value="720">720</option>
        <option value="900" selected>900</option>
        <option value="1080">1080</option>
        <option value="1440">1440</option>
      </select></label>
    <label>Face width <output id="o_face_frac"></output> of the frame width
      <input type="range" id="r_face_frac" min="0.05" max="0.6" step="0.01"
             value="0.12"></label>
    <p class="muted">Small keeps the whole person and the surroundings, large turns
      it into a tight portrait. The face is only the anchor that stops the video
      from jumping.</p>
    <label>Vertical eye position <output id="o_eye_y"></output>
      <input type="range" id="r_eye_y" min="0.15" max="0.75" step="0.01"
             value="0.33"></label>
    <label>When the photo does not fill the frame
      <select id="r_fill">
        <option value="crop" selected>Scale up to cover, cut the edges</option>
        <option value="blur">Keep the whole photo, blurred background</option>
      </select></label>
    <label class="chk"><input type="checkbox" id="r_level" checked>
      Rotate so the eyes are level</label>
    <details class="sub">
      <summary>Advanced framing</summary>
      <label>Horizontal eye position <output id="o_anchor_x"></output>
        <input type="range" id="r_anchor_x" min="0.2" max="0.8" step="0.01"
               value="0.5"></label>
      <label>Two people: distance between them <output id="o_pair_frac"></output>
        of the frame width
        <input type="range" id="r_pair_frac" min="0.08" max="0.8" step="0.01"
               value="0.30"></label>
      <p class="muted">The two eye centres are <b>not</b> treated as one pair of
        eyes — a tall parent and a short child are 30° apart and levelling that
        wrecks the photo. Two virtual points go on a horizontal line the real
        distance apart, so the further apart they stand the wider the framing
        opens.</p>
      <label>Encoding quality (CRF) <output id="o_crf"></output>
        <input type="range" id="r_crf" min="14" max="32" step="1" value="20"></label>
      <p class="muted">Lower is better looking and larger. 20 is visually clean
        for this material.</p>
    </details>`,
});

group({
  key: 'style', title: 'Editing style', screens: ['4'],
  html: `
    <label>Style
      <select id="r_mode">
        <option value="story" selected>Story — chapters, hero shots, cross-fades</option>
        <option value="flip">Even spread — one frame per photo (legacy)</option>
      </select></label>
    <div id="storyRender">
      <label>Slow zoom within each shot
        <select id="r_motion">
          <option value="none">None — the photo stays still</option>
          <option value="subtle" selected>Subtle</option>
          <option value="normal">Normal</option>
          <option value="strong">Strong</option>
        </select></label>
      <p class="muted">The zoom is centred on the midpoint between the eyes, so
        the face stays in exactly one place — only the surrounding context widens
        and narrows.</p>
      <label class="chk"><input type="checkbox" id="r_title" checked>
        Opening title card (name + year range)</label>
      <label class="chk"><input type="checkbox" id="r_chapter_card" checked>
        Show the chapter label when a new chapter starts</label>
      <label>Birth year — if set, chapter labels also show the age
        <input type="number" id="r_birth_year" min="1900" max="2100"
               placeholder="leave empty to skip"></label>
      <label>Output fps
        <select id="r_out_fps">
          <option value="24" selected>24</option>
          <option value="25">25</option>
          <option value="30">30</option>
        </select></label>
      <details class="sub">
        <summary>Advanced timing</summary>
        <label class="chk"><input type="checkbox" id="r_arc" checked>
          Run the first and last chapters 12% slower</label>
        <label>Title card held <output id="o_title_seconds"></output> s
          <input type="range" id="r_title_seconds" min="0" max="8" step="0.1"
                 value="2.4"></label>
        <p class="muted">The title sits <b>on top of</b> the first photo, not on
          its own black screen. If the first shot is a clip it is not extended
          either — that would freeze a frame before the clip moves.</p>
        <label>Chapter label held <output id="o_card_seconds"></output> s
          <input type="range" id="r_card_seconds" min="0.4" max="6" step="0.1"
                 value="1.8"></label>
        <label>Fade in from black <output id="o_intro_s"></output> s
          <input type="range" id="r_intro_s" min="0" max="4" step="0.1"
                 value="0.8"></label>
        <label>Hold, then fade to black <output id="o_outro_s"></output> s
          <input type="range" id="r_outro_s" min="0" max="6" step="0.1"
                 value="1.6"></label>
        <label class="chk"><input type="checkbox" id="r_xfade_auto" checked>
          Cross-fade length follows the pacing</label>
        <label id="xfWrap" class="off">Cross-fade <output id="o_xfade"></output> s
          <input type="range" id="r_xfade" min="0" max="2" step="0.01"
                 value="0.5"></label>
        <p class="muted">0 means hard cuts, which is a real choice and not the
          same as leaving it unset. A cross-fade never lengthens the video: it
          overlaps the tail of the shot before it.</p>
      </details>
    </div>
    <div id="flipRender" class="hide">
      <label>Photos per second <output id="o_fps"></output>
        <input type="range" id="r_fps" min="1" max="24" step="1" value="6"></label>
      <label>Smoothing
        <select id="r_smooth">
          <option value="blend" selected>Blend (smooth)</option>
          <option value="none">None (hard cuts)</option>
        </select></label>
    </div>
    <label>Time label in the lower corner
      <select id="r_label">
        <option value="none" selected>None — the chapter labels say when</option>
        <option value="year">Year</option>
        <option value="month">Year-month</option>
        <option value="date">Full date</option>
      </select></label>`,
});

group({
  key: 'audio', title: 'Clip audio', screens: ['4'],
  html: `
    <label class="chk"><input type="checkbox" id="r_audio" checked>
      Keep the real audio of each clip</label>
    <p class="muted">Audio comes in <b>before</b> the picture and stays on
      <b>after</b> the picture has cut away — the ear hears the new space before
      the eye sees it. Between clips there is silence, because stills have no
      sound.</p>
    <label>Audio leads the picture by <output id="o_audio_lead"></output> s
      <input type="range" id="r_audio_lead" min="0" max="2" step="0.1"
             value="0.5"></label>
    <label>Audio stays on for <output id="o_audio_tail"></output> s after the cut
      <input type="range" id="r_audio_tail" min="0" max="3" step="0.1"
             value="0.8"></label>
    <label class="chk"><input type="checkbox" id="r_audio_normalize" checked>
      Even out levels across clips</label>
    <details class="sub">
      <summary>Fades and level</summary>
      <label>Fade in <output id="o_audio_fade_in"></output> s
        <input type="range" id="r_audio_fade_in" min="0.02" max="2" step="0.01"
               value="0.35"></label>
      <label>Fade out <output id="o_audio_fade_out"></output> s
        <input type="range" id="r_audio_fade_out" min="0.02" max="3" step="0.01"
               value="0.6"></label>
      <label>Gain <output id="o_audio_gain"></output> dB
        <input type="range" id="r_audio_gain" min="-24" max="12" step="1"
               value="0"></label>
      <p class="muted">A fade is never longer than half its own clip, so these
        are ceilings rather than exact figures.</p>
    </details>`,
});

group({
  key: 'music', title: 'Background music', screens: ['4'],
  html: `
    <!-- The chosen track lives in a hidden input rather than a <select>: a
         thousand <option> rows is not a way to choose music, and everything
         downstream already reads .value. The library is its own screen. -->
    <input type="hidden" id="r_music" value="">
    <div id="musicPick" class="mpick"></div>
    <p class="muted">Photos are silent, clips are not, so a video mixed from both
      is blocks of sound between silences. Music holds a continuous bed under the
      whole thing — <b>a structural fix, not decoration</b>. Pick a track on the
      <b>Music</b> screen.</p>
    <label>Music level <output id="o_music_gain"></output> dB
      <input type="range" id="r_music_gain" min="-40" max="0" step="1"
             value="-14"></label>
    <label>Duck by <output id="o_music_duck"></output> dB while clip audio plays
      <input type="range" id="r_music_duck" min="-40" max="0" step="1"
             value="-11"></label>
    <p class="muted">Ducking is computed, not compressed: the exact seconds where
      real audio plays are known before ffmpeg is called, so the envelope is exact
      and identical between runs.</p>
    <label>Music fades in over <output id="o_music_fade_in"></output> s
      <input type="range" id="r_music_fade_in" min="0" max="8" step="0.1"
             value="1.2"></label>
    <label>Music fades out over <output id="o_music_fade_out"></output> s
      <input type="range" id="r_music_fade_out" min="0" max="10" step="0.1"
             value="2.5"></label>
    <label class="chk"><input type="checkbox" id="r_music_loop" checked>
      Loop the track if it is shorter than the video</label>
    <label class="chk"><input type="checkbox" id="r_beat_sync">
      Cut on the beat</label>
    <p class="muted">This changes the <b>unit</b> rather than replacing the
      hero/chapter structure: each shot keeps its natural length as the target and
      the boundary is pulled to the nearest beat. Clips are only snapped
      <b>down</b>, never up, because snapping up would freeze the last frame
      mid-motion.</p>
    <label>Cut every <output id="o_beat_every"></output> beat(s)
      <input type="range" id="r_beat_every" min="1" max="8" step="1"
             value="1"></label>`,
});

// --------------------------------------------------------------- the screen
screen('4', {
  label: 'Render',
  build: () => `
    <h2>Story structure</h2>
    <p class="muted">Computed with the exact algorithm the render step uses, so
      the durations below are real numbers rather than estimates.</p>
    <div id="sbInfo"></div>
    <h3>Framing preview — exactly what the video will look like</h3>
    <p class="muted">Three photos spread far apart in time. The face has to land
      in the same place in all three; everything else is the context you keep.</p>
    <div id="framePreview" class="fprev"></div>
    <h3 class="adv">Previous renders</h3>
    <div id="renderList" class="adv"></div>`,
  mount: enterStep4,
});

// ------------------------------------------------------------------- wiring
export function initRender() {
  $('r_mode').onchange = () => { syncRenderMode(); storyboardSoon(); };
  // Only controls that change SHOT LENGTHS need a recompute. arc scales holds,
  // title_seconds and outro_s are added to real holds, and beat snapping rewrites
  // all of them. Cross-fade, chapter cards, audio and encoding never move the
  // total.
  ['r_motion', 'r_title', 'r_chapter_card', 'r_birth_year', 'r_out_fps',
    'r_label', 'r_smooth', 'r_audio', 'r_audio_normalize', 'r_arc']
    .forEach((id) => { $(id).onchange = storyboardSoon; });
  $('r_beat_sync').onchange = () => {
    syncMusic(); renderPick(); storyboardSoon();
  };
  $('r_xfade_auto').onchange = syncXfade;
  ['r_aspect', 'r_size', 'r_fill', 'r_level']
    .forEach((id) => { $(id).onchange = previewSoon; });

  RANGES.forEach((id) => {
    const i = $(id);
    const out = $('o_' + id.slice(2));
    i.oninput = () => {
      if (out) out.textContent = i.value;
      if (SB_RANGE.has(id) || id === 'r_fps') storyboardSoon();
      if (PREV_RANGE.has(id)) previewSoon();
    };
    i.oninput();
  });

  syncRenderMode();
  syncXfade();
  $('render').onclick = startRender;
}

function syncRenderMode() {
  const story = $('r_mode').value === 'story';
  $('storyRender').classList.toggle('hide', !story);
  $('flipRender').classList.toggle('hide', story);
}

// null and 0 are different requests: 0 is "hard cuts", unset is "take the length
// from the pacing". A slider cannot express both, so the checkbox carries the
// distinction and the slider greys out while it is inferred.
function syncXfade() {
  $('xfWrap').classList.toggle('off', $('r_xfade_auto').checked);
}

// The framing of the video: the face is only the anchor point, face_frac decides
// whether the crop is wide or tight. The preview must use exactly this set,
// otherwise it lies about the result.
export function framingOpts() {
  return {
    aspect: $('r_aspect').value,
    size: Number($('r_size').value),
    face_frac: Number($('r_face_frac').value),
    eye_y: Number($('r_eye_y').value),
    fill: $('r_fill').value,
    level: $('r_level').checked,
  };
}

// Pace and target_seconds are NOT here: they belong to the photo selection,
// because the number of photos follows exactly those figures. Changing the pace
// means going back to the thresholds and recomputing, or the length will not
// match the budget.
export function renderOpts() {
  const story = $('r_mode').value === 'story';
  const by = Number($('r_birth_year').value);
  return Object.assign(framingOpts(), {
    mode: story ? 'story' : 'flip',
    label: $('r_label').value,
    out_fps: Number($('r_out_fps').value),
    motion: $('r_motion').value,
    title: $('r_title').checked,
    chapter_card: $('r_chapter_card').checked,
    birth_year: (by >= 1900 && by <= 2100) ? by : null,
    // Framing that /api/aligned cannot preview, so deliberately not part of
    // framingOpts(): the preview would silently ignore it and then lie.
    anchor_x: Number($('r_anchor_x').value),
    pair_frac: Number($('r_pair_frac').value),
    crf: Number($('r_crf').value),
    arc: $('r_arc').checked,
    title_seconds: Number($('r_title_seconds').value),
    card_seconds: Number($('r_card_seconds').value),
    intro_s: Number($('r_intro_s').value),
    outro_s: Number($('r_outro_s').value),
    xfade: $('r_xfade_auto').checked ? null : Number($('r_xfade').value),
    audio: $('r_audio').checked,
    audio_lead: Number($('r_audio_lead').value),
    audio_tail: Number($('r_audio_tail').value),
    audio_fade_in: Number($('r_audio_fade_in').value),
    audio_fade_out: Number($('r_audio_fade_out').value),
    audio_gain: Number($('r_audio_gain').value),
    audio_normalize: $('r_audio_normalize').checked,
    music: $('r_music').value || null,
    music_gain: Number($('r_music_gain').value),
    music_duck: Number($('r_music_duck').value),
    music_fade_in: Number($('r_music_fade_in').value),
    music_fade_out: Number($('r_music_fade_out').value),
    music_loop: $('r_music_loop').checked,
    beat_sync: $('r_beat_sync').checked,
    beat_every: Number($('r_beat_every').value),
    fps: Number($('r_fps').value),
    smooth: $('r_smooth').value,
  });
}

// Entering the screen: the render mode has to match the mode the photos were
// picked with. Picking with 'even' leaves frames without chapters, so rendering in
// story mode gives a string of equal-length shots with no labels — technically
// correct, but not what was asked for.
function enterStep4() {
  if (S.filters && S.filters.mode) {
    $('r_mode').value = S.filters.mode === 'even' ? 'flip' : 'story';
  }
  syncRenderMode();
  loadRenders();
  renderPreview();
  loadStoryboard();
}

// ------------------------------------------------------------------ preview
function renderPreview() {
  const sel = (S.result && S.result.selected) || [];
  const box = $('framePreview');
  if (!box) return;
  if (!sel.length) {
    box.innerHTML = '<p class="muted">no photo selected yet</p>';
    return;
  }
  const o = framingOpts();
  const idx = [...new Set([0, Math.floor(sel.length / 2), sel.length - 1])];
  box.innerHTML = idx.map((i) => {
    const f = sel[i];
    return `<figure><img src="${alignedUrl(f.key, 360, o)}" alt="" loading="lazy">`
      + `<figcaption>${(f.taken_at || '').slice(0, 10)}</figcaption></figure>`;
  }).join('');
}

// Every photo is fetched through the Immich API, so debounce rather than firing on
// every step of a slider.
const previewSoon = () => debounce('prev', renderPreview, 450);
export const storyboardSoon = () => debounce('sb', loadStoryboard, 350);

// --------------------------------------------------------------- storyboard
export async function loadStoryboard() {
  if (!S.projectId || !$('sbInfo')) return;
  $('sbInfo').innerHTML = '<p class="muted">computing…</p>';
  try {
    const d = await api(`/projects/${S.projectId}/storyboard`, {
      method: 'POST', body: JSON.stringify({options: renderOpts()}),
    });
    S.sb = d;
    if (d.mode !== 'story') {
      $('sbInfo').innerHTML = `<p class="muted">Even spread: ${d.n_shots} photos `
        + `at ${d.fps} per second → ${d.duration_s}s.</p>`;
    } else {
      const mx = Math.max(1, ...d.chapters.map((c) => c.seconds));
      $('sbInfo').innerHTML = '<div class="cards small">'
        + `<div class="card ok"><b>${d.duration_s}s</b><span>real duration</span></div>`
        + `<div class="card"><b>${d.n_shots}</b><span>shots</span></div>`
        + (d.n_clip ? `<div class="card"><b>${d.n_clip}</b><span>video clips</span></div>` : '')
        + `<div class="card"><b>${d.chapters.length}</b><span>chapters</span></div>`
        + `<div class="card"><b>${d.n_frames}</b><span>frames @${d.fps}fps</span></div>`
        + '</div>'
        + (d.target_seconds
          && Math.abs(d.duration_s - d.target_seconds) > d.target_seconds * 0.35
          ? `<div class="warn">Well off the ${d.target_seconds}s budget. Usually `
            + 'this means too many chapters, since every chapter is forced to have '
            + 'at least one photo. Set "One chapter covers" to something coarser, '
            + 'or raise the target duration.</div>' : '')
        + (d.n_missing ? `<div class="warn">${d.n_missing} photos had no readable `
          + 'preview file and were dropped from the story.</div>' : '')
        + beatLine(d)
        + '<div class="chapbars">'
        + d.chapters.map((c) => '<div class="cb">'
          + `<span class="cbl">${esc(c.label)}</span>`
          + `<span class="cbb"><i style="width:${Math.round(100 * c.seconds / mx)}%"></i></span>`
          + `<span class="cbn">${c.seconds}s<em>${c.n} photos</em></span></div>`).join('')
        + '</div>';
    }
    estimate();
  } catch (e) {
    $('sbInfo').innerHTML = `<div class="err">${esc(e.message)}</div>`;
  }
}

// The server silently drops a track name it cannot resolve and renders silent — a
// deliberate choice, because one bad name should not fail a whole render. But
// silent on the server has to be loud here. Same for a track with no detectable
// pulse: that is a normal result, not an error, and it has to be said out loud.
function beatLine(d) {
  const want = $('r_music').value;
  const out = [];
  if (want && !d.music) {
    out.push(`<div class="warn">The server could not resolve <code>${esc(want)}</code>
      inside <code>MUSIC_DIR</code>, so this render will have no music — only the
      audio of the video clips.</div>`);
  }
  if (!d.beat_sync) return out.join('');
  const b = d.beat;
  if (b && b.found) {
    out.push(`<p class="muted">Cutting on the beat: <b>${b.bpm} BPM</b>, `
      + `${d.n_beat_snap} of ${d.n_shots} shots snapped to the grid`
      + (d.beat_every > 1 ? `, every ${d.beat_every} beats` : '')
      + '. The duration above already accounts for the snapping.</p>');
  } else if (b) {
    out.push('<div class="warn">No clear pulse was found in this track, so the '
      + 'normal storytelling pacing is used. For free piano, rain or spoken word '
      + 'that is the expected result rather than a failure.</div>');
  }
  return out.join('');
}

function estimate() {
  const d = S.sb;
  if (d && d.n_shots) {
    $('renderEst').textContent =
      `${d.n_shots} shots → a ${d.duration_s} second video`;
    return;
  }
  const n = S.result ? S.result.n_selected : 0;
  const fps = Number($('r_fps').value);
  $('renderEst').textContent = n
    ? `${n} photos at ${fps} per second → roughly ${(n / fps).toFixed(1)} seconds`
    : '';
}

// ------------------------------------------------------------------- render
export async function startRender() {
  if (!S.projectId) return;
  $('render').disabled = true;
  go('5');
  try {
    const r = await api(`/projects/${S.projectId}/render`, {
      method: 'POST', body: JSON.stringify({options: renderOpts()}),
    });
    S.renderId = r.render_id;
    const {pollRender} = await import('./video.js');
    pollRender();
  } catch (e) {
    toast(e.message, true);
    $('render').disabled = false;
  }
}

export async function loadRenders() {
  if (!S.projectId || !$('renderList')) return;
  try {
    const d = await api(`/projects/${S.projectId}/renders`);
    const {videoUrl} = await import('../core/api.js');
    $('renderList').innerHTML = d.renders.length
      ? '<table><tr><th>#</th><th>Status</th><th>Frames</th><th>Length</th><th></th></tr>'
        + d.renders.map((r) => `<tr><td>${r.id}</td><td>${esc(r.status)}</td>`
          + `<td>${r.n_done}/${r.n_total}</td>`
          + `<td>${r.duration_s ? `${r.duration_s.toFixed(1)}s` : '—'}</td>`
          + `<td>${r.status === 'done'
            ? `<a href="${videoUrl(r.id)}" download>download</a>`
            : esc(r.err || '')}</td></tr>`).join('')
        + '</table>'
      : '<p class="muted">No renders yet.</p>';
  } catch (e) { /* not important enough to interrupt */ }
}

export {num};
