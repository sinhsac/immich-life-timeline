// Statistics — a read of the state columns in fp_asset. The indexer runs outside
// this service (its own CronJob), so nothing here can start or stop work.

import {$, num} from '../core/dom.js';
import {api} from '../core/api.js';
import {S} from '../core/state.js';
import {screen} from '../core/router.js';
import {showDataNotes} from './thresholds.js';

screen('stats', {
  label: 'Statistics',
  build: () => `
    <h2>Indexing progress</h2>
    <div class="bar">
      <button id="statsReload">Reload</button>
      <span id="statsAt" class="muted"></span>
    </div>
    <p class="muted">The indexer job runs outside this service, so this is only a
      read of the state columns in <code>fp_asset</code>.</p>
    <div id="idxCards" class="cards"></div>
    <div id="idxBars"></div>
    <h3>Recent runs</h3>
    <div id="idxRuns"></div>`,
  ready: () => { $('statsReload').onclick = pollStatus; },
  mount: pollStatus,
});

export function showHealth(h) {
  const bits = [];
  if (!h.indexer.ok) bits.push(`<div class="err">Indexer: ${h.indexer.detail}</div>`);
  if (!h.ffmpeg.ok) bits.push(`<div class="err">ffmpeg: ${h.ffmpeg.detail}</div>`);
  if (h.text && !h.text.ok) {
    bits.push('<div class="warn">No TTF font found, so chapter labels and names '
      + `will have their accents stripped (${h.text.detail}). Install `
      + '<code>pillow</code> + <code>fonts-dejavu-core</code>, or set '
      + '<code>FONT_FILE</code>.</div>');
  }
  if (!h.auth) {
    bits.push('<div class="warn">This service is running with no authentication '
      + '(<code>API_TOKEN</code> is empty), and it can both read every photo and '
      + 'write files to the host.</div>');
  }
  if (h.indexer.ok && h.ffmpeg.ok) {
    bits.push(`<span class="muted">${h.indexer.detail}</span>`);
  }
  $('health').innerHTML = bits.join('');
}

export function showProgress(p) {
  S.progress = p;
  showDataNotes();

  // One number on the nav, so you can tell whether the page is worth opening
  // without it taking room from the other screens.
  const nav = $('navStats');
  if (p.ready) {
    const done = p.stages.reduce((a, s) => a + s.done, 0);
    const total = p.stages.reduce((a, s) => a + s.total, 0) || 1;
    nav.textContent = `Statistics · ${Math.round(100 * done / total)}%`;
    nav.classList.toggle('warnDot', !!p.running);
  } else {
    nav.textContent = 'Statistics';
  }

  if (!$('idxCards')) return;               // screen not built yet
  if (!p.ready) {
    $('idxCards').innerHTML = '';
    $('idxBars').innerHTML = '<p class="muted">No fp_asset table yet — the '
      + 'indexer job has never run.</p>';
    $('idxRuns').innerHTML = '';
    return;
  }

  const cards = [
    ['n_asset', 'assets in the library'],
    ['n_face', 'faces'],
    ['n_face_ready', 'faces with landmarks'],
    ['n_body', 'bodies'],
  ];
  // The clips stage is the most expensive one in the job and used to be the only
  // one showing nothing here, though every figure was already in fp_asset.
  if (p.has_video) {
    cards.push(['n_video', 'videos'], ['n_vframe', 'video frames scanned'],
      ['n_vface', 'faces found in video'], ['n_vclip', 'clips cut']);
  }
  const errs = (p.face_err || 0) + (p.body_err || 0);
  $('idxCards').innerHTML = cards
    .map(([k, lab]) => `<div class="card"><b>${num(p[k])}</b><span>${lab}</span></div>`)
    .join('')
    + (errs ? `<div class="card"><b class="bad">${num(errs)}</b>`
      + '<span>photos that failed to read</span></div>' : '')
    + (p.clip_err ? `<div class="card"><b class="bad">${num(p.clip_err)}</b>`
      + '<span>videos that failed to scan</span></div>' : '');

  $('idxBars').innerHTML = p.stages.map((s) => {
    const left = s.left != null ? s.left : Math.max(0, s.total - s.done);
    return `<div class="prow${p.running === s.name ? ' run' : ''}">`
      + `<span class="plab">${s.label}`
      + (p.running === s.name ? ' <b>running</b>' : '') + '</span>'
      + `<span class="pbar"><i style="width:${Math.min(100, s.pct)}%"></i></span>`
      + `<span class="pnum">${s.pct}%<em>${num(s.done)}/${num(s.total)}`
      + (left ? ` · ${num(left)} left` : '') + '</em></span></div>';
  }).join('')
    + (p.clip_err ? `<div class="warn">${num(p.clip_err)} videos could not be `
      + 'scanned. Usually a file over <code>VIDEO_MAX_MB</code> in HTTP mode, or '
      + 'an original in HEVC/AV1 that OpenCV cannot open. They are not retried '
      + 'automatically; run <code>job.py --reset clips</code> after changing '
      + 'anything.</div>' : '')
    + (p.running ? ''
      : '<p class="muted">No stage is running. The next job picks up exactly where '
        + 'this one stopped — progress lives in the database, so nothing is lost '
        + 'when the machine goes down.</p>');

  $('idxRuns').innerHTML = (p.runs || []).length
    ? '<table class="runs"><tr><th>Stage</th><th>Started</th><th>Finished</th>'
      + '<th>Processed</th><th>Errors</th><th>Note</th></tr>'
      + p.runs.map((r) => '<tr>'
        + `<td>${r.stage}</td>`
        + `<td>${(r.started_at || '').slice(0, 19).replace('T', ' ')}</td>`
        + `<td>${r.running ? '<b>running</b>' : (r.finished_at || '').slice(11, 19)}</td>`
        + `<td>${num(r.n_done)}</td>`
        + `<td>${r.n_err ? `<span class="bad">${num(r.n_err)}</span>` : '0'}</td>`
        + `<td class="muted">${r.note || ''}</td></tr>`).join('')
      + '</table>'
    : '<p class="muted">no runs yet</p>';

  $('statsAt').textContent = `updated ${new Date().toLocaleTimeString('en-US')}`;
}

const progressDone = (p) => p.ready && p.stages.every((s) => s.done >= s.total);

export async function pollStatus() {
  let wait = 20000;
  try {
    const [h, p] = await Promise.all([api('/health'), api('/progress')]);
    showHealth(h);
    showProgress(p);
    if (progressDone(p)) return;        // all done, stop polling for good
    // Nothing running but something incomplete: a stage that will never run on its
    // own — clips with DO_VIDEO=0, or smiles — would otherwise hold the 20s cycle
    // open forever without producing a new number.
    if (!p.running) wait = 60000;
  } catch (e) {
    wait = 60000;                        // back off on error, do not hammer it
  }
  clearTimeout(S.statusTimer);
  S.statusTimer = setTimeout(pollStatus, wait);
}
