// Screen 3 — filter thresholds, and the sidebar groups that drive them.
//
// The screen owns both halves on purpose: a threshold slider and the rejection
// reason it produces are one feature, and splitting them across files is how the
// two drift apart.

import {$, el, esc, num, toast} from '../core/dom.js';
import {api, imgUrl} from '../core/api.js';
import {MANUAL_REASON, S} from '../core/state.js';
import {group, screen, go} from '../core/router.js';

const RANGE_KEYS = ['max_yaw', 'max_pitch', 'max_roll', 'min_frontality',
  'min_ear', 'min_eye_ratio', 'min_sharp', 'bucket_days', 'per_bucket',
  'target_seconds', 'max_per_chapter', 'max_clip_motion',
  // Hard walls. The sliders above them are only knees once soft_pose is on, so
  // leaving these out made those sliders read as something they are not.
  'hard_yaw', 'hard_pitch', 'hard_roll', 'hard_frontality', 'hard_sharp',
  'min_smile', 'dedup_seconds', 'min_clip_seconds'];
const SELECT_KEYS = ['mode', 'pace', 'chapter_by'];
// Listed once so setFilterUI and readFilterUI cannot drift; the old code spelled
// each of them out twice.
const BOOL_KEYS = ['soft_pose', 'prefer_smile', 'use_clips', 'allow_others',
  'use_body', 'allow_missing_body'];

// ------------------------------------------------------------ sidebar groups
group({
  key: 'story', title: 'Storytelling', screens: ['3'], open: true,
  html: `
    <label>Video style
      <select id="f_mode">
        <option value="story" selected>Story — chapters with hero shots</option>
        <option value="even">Even spread — one frame per photo (legacy)</option>
      </select></label>
    <div id="storyOpts">
      <label class="chk"><input type="checkbox" id="f_auto_len" checked>
        Infer the duration from the data</label>
      <p class="muted">By default the duration is an <b>outcome</b>, not a
        request: chapters with more photos get more screen time, and a longer
        journey gets more chapters. Untick this to force a number.</p>
      <label id="lenWrap" class="off">Target duration
        <output id="o_target_seconds"></output> s
        <input type="range" id="f_target_seconds" min="15" max="240" step="5"></label>
      <label>Pacing
        <select id="f_pace">
          <option value="slow">Slow — time to take it in</option>
          <option value="normal" selected>Normal</option>
          <option value="quick">Quick</option>
          <option value="snap">Very quick</option>
        </select></label>
      <label>One chapter covers
        <select id="f_chapter_by">
          <option value="auto" selected>Auto — based on the span of the journey</option>
          <option value="years2">Two years</option>
          <option value="year">One year</option>
          <option value="half">Half a year</option>
          <option value="quarter">One quarter</option>
          <option value="month">One month</option>
        </select></label>
      <label>At most <output id="o_max_per_chapter"></output> photos per chapter
        <input type="range" id="f_max_per_chapter" min="1" max="16" step="1"></label>
      <p class="muted"><b>The photo count follows from the duration</b>, not the
        other way around. Every chapter always gets at least one photo so no
        stretch of time disappears, and each chapter's highest-scoring photo is
        held longer as the hero shot.</p>
    </div>
    <div id="evenOpts" class="hide">
      <label>Every <output id="o_bucket_days"></output> days, take
        <output id="o_per_bucket"></output> photos
        <input type="range" id="f_bucket_days" min="7" max="365" step="1">
        <input type="range" id="f_per_bucket" min="1" max="6" step="1"></label>
    </div>`,
});

group({
  key: 'pose', title: 'Head pose', screens: ['3'],
  html: `
    <label class="chk"><input type="checkbox" id="f_soft_pose" checked>
      Score the pose instead of rejecting on it</label>
    <p class="muted" id="softNote"></p>
    <label>Max head turn <output id="o_max_yaw"></output>°
      <input type="range" id="f_max_yaw" min="5" max="60" step="1"></label>
    <label>Max tilt up/down <output id="o_max_pitch"></output>°
      <input type="range" id="f_max_pitch" min="5" max="50" step="1"></label>
    <label>Max sideways tilt <output id="o_max_roll"></output>°
      <input type="range" id="f_max_roll" min="5" max="60" step="1"></label>
    <label>Min frontality <output id="o_min_frontality"></output>
      <input type="range" id="f_min_frontality" min="0" max="1" step="0.05"></label>
    <label>Eye-openness threshold (EAR) <output id="o_min_ear"></output>
      <input type="range" id="f_min_ear" min="0" max="0.35" step="0.01"></label>
    <p class="muted">EAR stays a hard gate either way — a photo with the eyes
      shut cannot be rescued by any amount of sharpness.</p>
    <details class="sub">
      <summary>Hard limits — where a photo is rejected outright</summary>
      <p class="muted">With scoring on, the sliders above are only <b>knees</b>.
        These are the walls. The default used to cut at 22° of head turn, and 22°
        is still very nearly looking straight at the lens: photos of someone
        grinning back over their shoulder, mid-run, blowing out candles were
        thrown away before they were ever scored, and what survived was passport
        shots.</p>
      <label>Reject past <output id="o_hard_yaw"></output>° of head turn
        <input type="range" id="f_hard_yaw" min="10" max="90" step="1"></label>
      <label>Reject past <output id="o_hard_pitch"></output>° of tilt up/down
        <input type="range" id="f_hard_pitch" min="10" max="80" step="1"></label>
      <label>Reject past <output id="o_hard_roll"></output>° of sideways tilt
        <input type="range" id="f_hard_roll" min="10" max="90" step="1"></label>
      <label>Reject below frontality <output id="o_hard_frontality"></output>
        <input type="range" id="f_hard_frontality" min="0" max="1" step="0.05"></label>
      <label>Reject below sharpness <output id="o_hard_sharp"></output>
        <input type="range" id="f_hard_sharp" min="0" max="200" step="2"></label>
      <p class="muted">The server keeps every wall on the far side of its own
        knee, so an inconsistent pair is corrected rather than inverting the
        score.</p>
    </details>`,
});

group({
  key: 'quality', title: 'Image quality', screens: ['3'],
  html: `
    <label>Min face size <output id="o_min_eye_ratio"></output> of the long edge
      <input type="range" id="f_min_eye_ratio" min="0" max="0.15" step="0.005"></label>
    <label>Min sharpness <output id="o_min_sharp"></output>
      <input type="range" id="f_min_sharp" min="0" max="400" step="10"></label>`,
});

group({
  key: 'smile', title: 'Smiling', screens: ['3'],
  html: `
    <label class="chk"><input type="checkbox" id="f_prefer_smile" checked>
      Give smiling photos a higher score</label>
    <p class="muted">Everything else here measures a <b>technically clean
      face</b>; nothing measured whether the moment was worth keeping. A smile is
      the closest this metric set gets to "a moment", and it is the largest single
      bonus in the score — a photo turned 35° with a broad smile now outscores a
      dead-straight, unsmiling one.</p>
    <div id="smileNote"></div>
    <label>Reject below smile <output id="o_min_smile"></output>
      <input type="range" id="f_min_smile" min="0" max="0.9" step="0.05"></label>
    <p class="muted">Leave at 0. A solemn portrait is part of the story too, so
      this is off by default and the preference is applied by adding points
      rather than by rejecting.</p>`,
});

group({
  key: 'dedup', title: 'Duplicate shots', screens: ['3'],
  html: `
    <label>Photos within <output id="o_dedup_seconds"></output> s are one moment
      <input type="range" id="f_dedup_seconds" min="0" max="60" step="1"></label>
    <p class="muted">Bursts collapse to their best frame. 0 turns it off. A burst
      is capped at three times this figure, so a whole afternoon of photos taken
      5 s apart does not shrink to a single frame. Video clips are never
      collapsed — they all share the video's timestamp, and the indexer already
      guaranteed they do not overlap.</p>`,
});

group({
  key: 'clips', title: 'Video clips', screens: ['3'],
  html: `
    <label class="chk"><input type="checkbox" id="f_use_clips" checked>
      Splice in real video clips</label>
    <p class="muted">The indexer scans every video frame, finds the person you
      selected, then cuts out the best clip. Any chapter that has a clip gets one
      slot for it — a moving clip is worth more than a photo that is only
      marginally better.</p>
    <label>Max shake <output id="o_max_clip_motion"></output>
      <input type="range" id="f_max_clip_motion" min="0.5" max="8" step="0.1"></label>
    <p class="muted">Measured in "face widths per second" — divided by face size,
      not frame size, because a large face moving 50 px is normal while a small
      face moving 50 px is a jump cut.</p>
    <label>Drop clips shorter than <output id="o_min_clip_seconds"></output> s
      <input type="range" id="f_min_clip_seconds" min="0" max="4" step="0.1"></label>
    <div id="clipNote"></div>`,
});

group({
  key: 'others', title: 'Other people', screens: ['3'],
  html: `
    <label class="chk"><input type="checkbox" id="f_allow_others">
      Accept photos containing other people</label>
    <label>Max faces per photo (0 = no limit)
      <input type="number" id="f_max_faces" min="0" max="30" step="1"></label>`,
});

group({
  key: 'body', title: 'Body pose', screens: ['3'],
  html: `
    <label class="chk"><input type="checkbox" id="f_use_body">
      Use body pose data</label>
    <div id="bodyOpts">
      <div class="chips" id="c_postures"></div>
      <div class="chips" id="c_orientations"></div>
      <label class="chk"><input type="checkbox" id="f_allow_missing_body">
        Accept photos with no body detected</label>
    </div>`,
});

// --------------------------------------------------------------- the screen
screen('3', {
  label: 'Thresholds',
  build: () => `
    <h2>Which photos make the video</h2>
    <p class="muted">Every rejected photo carries a concrete reason, so you know
      which threshold to loosen instead of guessing.</p>
    <div class="cards small">
      <div class="card ok"><b id="r_selected">0</b><span>in the video</span></div>
      <div class="card"><b id="r_rejected">0</b><span>rejected</span></div>
    </div>
    <div id="storyInfo"></div>
    <div id="reasons"></div>
    <div class="tabs">
      <button data-tab="sel" class="on">Photos in the video</button>
      <button data-tab="rej">Rejected photos</button>
    </div>
    <p class="muted" id="tabHint"></p>
    <div id="gridSel" class="grid frames"></div>
    <div id="gridRej" class="grid frames hide"></div>
    <div class="bar"><button id="toStep4" class="primary">Render the video →</button></div>`,
  ready: (node) => {
    node.querySelectorAll('.tabs button').forEach((b) => {
      b.onclick = () => {
        node.querySelectorAll('.tabs button')
          .forEach((x) => x.classList.remove('on'));
        b.classList.add('on');
        const sel = b.dataset.tab === 'sel';
        $('gridSel').classList.toggle('hide', !sel);
        $('gridRej').classList.toggle('hide', sel);
        $('tabHint').textContent = sel
          ? 'Click a photo to drop it from the video.'
          : 'Near misses come first. If you see good photos being rejected, '
            + 'loosen the matching threshold on the left.';
      };
    });
    $('toStep4').onclick = () => go('4');
  },
  mount: renderResult,
});

// ------------------------------------------------------------------- wiring
export function initFilters() {
  $('f_mode').onchange = syncMode;
  $('f_auto_len').onchange = syncAutoLen;
  $('f_soft_pose').onchange = syncSoftPose;
  $('f_use_body').onchange = () => {
    $('bodyOpts').classList.toggle('hide', !$('f_use_body').checked);
  };
  $('apply').onclick = applyFilters;
  $('resetF').onclick = () => {
    setFilterUI(S.defaults.filters);
    applyFilters();
  };
}

// mode='story' and mode='even' use two different sets of parameters. Showing both
// at once means dragging a slider that has no effect, with no clue why.
function syncMode() {
  const story = $('f_mode').value === 'story';
  $('storyOpts').classList.toggle('hide', !story);
  $('evenOpts').classList.toggle('hide', story);
}

function syncAutoLen() {
  $('lenWrap').classList.toggle('off', $('f_auto_len').checked);
}

// The knee sliders only mean "start losing points here" while scoring is on. With
// it off they are walls again and the hard limits stop applying, so say which of
// the two you are looking at rather than leaving both on screen meaning different
// things on different days.
function syncSoftPose() {
  $('softNote').innerHTML = $('f_soft_pose').checked
    ? 'The sliders below are <b>knees</b>: cross one and the photo starts losing '
      + 'points on a ramp up to the hard limit. Only the hard limits reject.'
    : '<b>Off:</b> the sliders below reject outright, and the hard limits are '
      + 'ignored. This is the old behaviour.';
}

function fmt(k, v) {
  if (k === 'min_eye_ratio') return `${(Number(v) * 100).toFixed(1)}%`;
  if (['min_frontality', 'min_ear', 'hard_frontality', 'min_smile'].includes(k)) {
    return Number(v).toFixed(2);
  }
  if (k === 'dedup_seconds') return Number(v) ? String(v) : 'off';
  return String(v);
}

export function setFilterUI(f) {
  RANGE_KEYS.forEach((k) => {
    const i = $('f_' + k);
    if (!i) return;
    i.value = f[k];
    const o = $('o_' + k);
    if (o) o.textContent = fmt(k, f[k]);
    i.oninput = () => { if (o) o.textContent = fmt(k, i.value); };
  });
  SELECT_KEYS.forEach((k) => {
    const i = $('f_' + k);
    if (i && f[k]) i.value = f[k];
  });
  // target_seconds = null means "infer it", not "do not send it". The slider still
  // holds a sensible number so unticking the box works straight away.
  const auto = f.target_seconds === null || f.target_seconds === undefined;
  $('f_auto_len').checked = auto;
  if (!auto) {
    $('f_target_seconds').value = f.target_seconds;
  } else if (S.result && S.result.story) {
    $('f_target_seconds').value = Math.max(15, Math.min(240,
      Math.round(S.result.story.est_seconds || 60)));
  }
  $('o_target_seconds').textContent = $('f_target_seconds').value;
  syncAutoLen();
  syncMode();
  BOOL_KEYS.forEach((k) => {
    const i = $('f_' + k);
    if (i) i.checked = !!f[k];
  });
  $('f_max_faces').value = f.max_faces ?? 0;
  $('bodyOpts').classList.toggle('hide', !f.use_body);
  syncSoftPose();
  showDataNotes();
  chips('c_postures', ['standing', 'sitting', 'lying', 'unknown'], f.postures || []);
  chips('c_orientations', ['front', 'side', 'back', 'unknown'], f.orientations || []);
}

function readFilterUI() {
  const f = {};
  RANGE_KEYS.forEach((k) => {
    const i = $('f_' + k);
    if (i) f[k] = Number(i.value);
  });
  SELECT_KEYS.forEach((k) => {
    const i = $('f_' + k);
    if (i) f[k] = i.value;
  });
  f.target_seconds = $('f_auto_len').checked
    ? null : Number($('f_target_seconds').value);
  BOOL_KEYS.forEach((k) => {
    const i = $('f_' + k);
    if (i) f[k] = i.checked;
  });
  f.max_faces = Number($('f_max_faces').value || 0);
  f.postures = readChips('c_postures');
  f.orientations = readChips('c_orientations');
  return f;
}

function chips(box, all, active) {
  const n = $(box);
  if (!n) return;
  n.innerHTML = '';
  const set = new Set(active);
  all.forEach((v) => {
    const b = el('button', set.has(v) ? 'on' : '', v);
    b.type = 'button';
    b.onclick = () => b.classList.toggle('on');
    n.appendChild(b);
  });
}

const readChips = (box) =>
  Array.from($(box).querySelectorAll('button.on')).map((b) => b.textContent);

export async function applyFilters() {
  $('apply').disabled = true;
  $('applying').textContent = 'recomputing…';
  try {
    const r = await api(`/projects/${S.projectId}/filters`, {
      method: 'PATCH', body: JSON.stringify({filters: readFilterUI()}),
    });
    S.result = r;
    S.filters = r.filters;
    // Show what the server ACTUALLY used, not what was sent. merge() rewrites
    // values: every hard limit is pushed to the far side of its own knee, and the
    // pacing figures go through story.plan(). Without this the hard-limit sliders
    // sit at a number the filtering never saw.
    setFilterUI(r.filters);
    renderResult();
    $('applying').textContent = '';
  } catch (e) {
    $('applying').innerHTML = `<span class="err">${esc(e.message)}</span>`;
  } finally {
    $('apply').disabled = false;
  }
}

// Two features can be switched on while the data behind them does not exist, and
// both then do nothing without saying so: smile scores live in a column the
// 'smiles' stage fills, and clips come from a stage that may never have run.
export function showDataNotes() {
  const p = S.progress;
  const sn = $('smileNote');
  const cn = $('clipNote');
  if (!sn || !cn) return;
  if (!p || !p.ready) { sn.innerHTML = ''; cn.innerHTML = ''; return; }

  sn.innerHTML = (p.has_smile && p.n_smile)
    ? (p.n_smile < p.n_face_ready
      ? `<div class="warn">Only ${num(p.n_smile)} of ${num(p.n_face_ready)} faces
         carry a smile score. Run <code>job.py --stage smiles</code> to fill the
         rest — it reads the landmarks already stored, so not one photo is
         re-read.</div>`
      : '')
    : `<div class="warn">No face has a smile score yet, so this preference does
       nothing. Run <code>job.py --stage smiles</code>: it derives the score from
       the <code>lmk68</code> blobs already in the database, without re-reading a
       photo or loading a model.</div>`;

  cn.innerHTML = !p.has_video
    ? '<div class="warn">No video in the library, so there is nothing to splice in.</div>'
    : (p.n_vclip ? ''
      : `<div class="warn">${num(p.n_video)} videos are indexed but not one clip
         has been cut. Run <code>job.py --stage clips</code>; if it has already
         run, check that at least one person has an embedding to match
         against.</div>`);
}

// ------------------------------------------------------------------ results
export function renderResult() {
  const r = S.result;
  if (!r || !$('r_selected')) return;
  $('r_selected').textContent = r.n_selected;
  $('r_rejected').textContent = r.n_rejected;
  showStory(r.story);
  $('reasons').innerHTML = Object.keys(r.reasons).length
    ? '<table><tr><th>Rejection reason</th><th>Photos</th></tr>'
      + Object.entries(r.reasons)
        .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join('')
      + '</table>'
    : '<p class="muted">No photo was rejected.</p>';
  fillSelected(r);
  const gr = $('gridRej');
  gr.innerHTML = '';
  (r.rejected || []).forEach((f) => gr.appendChild(frameNode(f, false)));
  if (!$('tabHint').textContent) {
    $('tabHint').textContent = 'Click a photo to drop it from the video.';
  }
}

function showStory(st) {
  const box = $('storyInfo');
  if (!st) {
    box.innerHTML = '<p class="muted">Even spread mode: duration = photo count / '
      + 'photos per second, set in the render settings.</p>';
    return;
  }
  const max = Math.max(1, ...st.chapters.map((c) => c.n_pick));
  box.innerHTML = '<div class="cards small">'
    + `<div class="card ok"><b>${st.n_chapter}</b><span>chapters</span></div>`
    + `<div class="card"><b>${st.n_hero}</b><span>hero shots</span></div>`
    + (st.n_clip ? `<div class="card"><b>${st.n_clip}</b><span>video clips</span></div>` : '')
    + `<div class="card"><b>~${st.est_seconds}s</b><span>estimated length</span></div>`
    + (st.auto
      ? '<div class="card"><b>inferred</b><span>duration</span></div>'
      : `<div class="card"><b>${st.target_seconds}s</b><span>set by hand</span></div>`)
    + '</div>'
    + `<p class="muted">${esc(st.grain_label)} · hero shots held ${st.hold_hero}s, `
    + `supporting shots ${st.hold_beat}s</p>`
    // Raising the budget without the video getting longer is always one of these
    // two, so say which one or the slider gets dragged for nothing.
    + (st.capped ? '<div class="warn">Every chapter has hit the ceiling of '
      + `<b>${st.max_per_chapter} photos per chapter</b>. Raising the target `
      + 'duration will not add photos — raise this ceiling, or set "One chapter '
      + 'covers" to something finer.</div>' : '')
    + (st.exhausted && !st.capped ? '<div class="warn">Every photo that passes '
      + 'the thresholds has been used. For a longer video, loosen the '
      + 'thresholds on the left.</div>' : '')
    + '<div class="chapbars">'
    + st.chapters.map((c) => `<div class="cb" title="${c.n_avail} photos pass the`
      + ` thresholds in this period"><span class="cbl">${esc(c.label)}</span>`
      + `<span class="cbb"><i style="width:${Math.round(100 * c.n_pick / max)}%"></i></span>`
      + `<span class="cbn">${c.n_pick}<em>of ${num(c.n_avail)}</em></span></div>`).join('')
    + '</div>';
}

// Grouped by CHAPTER rather than one flat grid: a chapter holding a single photo
// tells you at once which period is short on data.
function fillSelected(r) {
  const gs = $('gridSel');
  gs.innerHTML = '';
  const story = r.story && (!r.filters || r.filters.mode === 'story');
  gs.className = story ? 'chapters' : 'grid frames';
  if (!story) {
    r.selected.forEach((f) => gs.appendChild(frameNode(f, true)));
    return;
  }
  let cur = null;
  let box = null;
  r.selected.forEach((f) => {
    if (f.bucket !== cur || box === null) {
      cur = f.bucket;
      gs.appendChild(el('div', 'chapHead', `<b>${esc(f.label || '')}</b>`));
      box = el('div', 'grid frames');
      gs.appendChild(box);
    }
    box.appendChild(frameNode(f, true));
  });
}

function frameNode(f, selected) {
  const clip = f.kind === 'clip';
  const n = el('div', 'frame ' + (selected ? 'sel' : 'rej')
    + (f.hero ? ' hero' : '') + (clip ? ' clip' : ''));
  const dt = (f.taken_at || '').slice(0, 10);
  n.innerHTML = `<img loading="lazy" src="${imgUrl('thumb', f.key, 104)}" alt="">`
    + (selected ? '' : `<div class="why">${esc(f.reason || '')}</div>`)
    + (clip ? `<div class="clipTag">▶ ${(f.dur_s || 0).toFixed(1)}s</div>`
      : (f.hero ? '<div class="heroTag">hero</div>' : ''))
    + `<div class="dt">${dt}</div>`;
  n.title = clip
    ? [`${dt} · video clip`,
      `from ${((f.t_start_ms || 0) / 1000).toFixed(1)}s, ${(f.dur_s || 0).toFixed(1)}s long`,
      f.t_peak_ms != null
        ? `peak moment at ${(f.t_peak_ms / 1000).toFixed(1)}s of the source clip` : '',
      `frontality ${f.frontality} · sharpness ${f.sharp}`,
      `shake ${f.motion}`,
      f.reason ? `REJECTED: ${f.reason}` : ''].filter(Boolean).join('\n')
    : [dt, `yaw ${f.yaw}° pitch ${f.pitch}° roll ${f.roll}°`,
      `frontality ${f.frontality}`, `sharpness ${f.sharp}`,
      `${f.n_face} faces in the photo`,
      f.posture ? `posture ${f.posture}/${f.orientation}` : 'no body pose',
      f.reason ? `REJECTED: ${f.reason}` : ''].filter(Boolean).join('\n');
  if (selected) n.onclick = () => toggle(f, true);
  else if (f.reason === MANUAL_REASON) n.onclick = () => toggle(f, false);
  return n;
}

async function toggle(f, exclude) {
  const [asset_id, fidx] = f.key.split(':');
  try {
    const r = await api(`/projects/${S.projectId}/exclude`, {
      method: 'POST',
      body: JSON.stringify({asset_id, fidx: Number(fidx), excluded: exclude}),
    });
    // Keep the old rejected list so the tab being viewed is not emptied.
    S.result = Object.assign({}, r, {rejected: S.result.rejected});
    renderResult();
    toast(exclude ? 'Photo dropped from the video' : 'Photo put back');
  } catch (e) {
    toast(e.message, true);
  }
}
