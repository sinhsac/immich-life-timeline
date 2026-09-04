// Screen 5 — the result. This is where the default path ends: no sliders, no
// thresholds, just the video and two buttons to react to what you have seen.

import {$, esc, toast} from '../core/dom.js';
import {api, videoUrl} from '../core/api.js';
import {S} from '../core/state.js';
import {go, screen, setExpert} from '../core/router.js';

screen('5', {
  label: 'Video',
  build: () => `
    <div id="outHead"></div>
    <div id="renderState"></div>
    <video id="player" controls class="hide"></video>
    <div class="bar" id="outBar">
      <button id="shorter">Shorter</button>
      <button id="longer">Longer</button>
      <!-- Must carry the SAME options as #r_aspect: the two mirror each other's
           value, and a missing option means the assignment does not match and the
           select renders empty. -->
      <label class="muted">Frame
        <select id="o_aspect">
          <option value="4:3" selected>4:3 landscape</option>
          <option value="3:2">3:2 landscape</option>
          <option value="16:9">16:9 landscape</option>
          <option value="1:1">1:1 square</option>
          <option value="3:4">3:4 portrait</option>
          <option value="9:16">9:16 portrait (phone)</option>
        </select></label>
      <button id="reRender" class="primary">Re-render</button>
      <button id="toExpert">Expert tuning →</button>
    </div>
    <div id="outStory"></div>`,
  ready: () => {
    $('shorter').onclick = () => nudgeLength(0.7);
    $('longer').onclick = () => nudgeLength(1.45);
    $('reRender').onclick = reRender;
    $('o_aspect').onchange = reRender;
    $('toExpert').onclick = () => { setExpert(true); go('3'); };
  },
});

export function showOut(r) {
  if (!$('outHead')) return;
  const p = S.person || {};
  const st = r.story;
  const range = [$('dFrom') ? $('dFrom').value : '',
    $('dTo') ? $('dTo').value : ''].filter(Boolean).join(' → ');
  $('outHead').innerHTML = `<h2>${esc(p.name || '(unnamed)')}</h2>`
    + '<p class="muted">'
    + (st ? `${r.n_selected} shots · ${st.n_chapter} chapters`
      + (st.n_clip ? ` · ${st.n_clip} video clips` : '')
      + ` · ${esc(st.grain_label)}`
      : `${r.n_selected} photos`)
    + (range ? ` · ${range}` : '')
    + '</p>';
  $('outStory').innerHTML = '';
}

async function reRender() {
  // r_aspect lives in the sidebar; the two controls hold one value.
  if ($('r_aspect')) $('r_aspect').value = $('o_aspect').value;
  const {startRender} = await import('./render.js');
  startRender();
}

// "Shorter / Longer" instead of a length slider: react to what you have already
// seen, rather than guessing a number before seeing anything.
async function nudgeLength(k) {
  const cur = (S.out && S.out.duration_s) || (S.sb && S.sb.duration_s) || 60;
  const t = Math.max(15, Math.min(240, Math.round(cur * k)));
  $('shorter').disabled = true;
  $('longer').disabled = true;
  try {
    const r = await api(`/projects/${S.projectId}/filters`, {
      method: 'PATCH', body: JSON.stringify({filters: {target_seconds: t}}),
    });
    S.result = r;
    S.filters = r.filters;
    const {setFilterUI} = await import('./thresholds.js');
    setFilterUI(r.filters);
    showOut(r);
    if (r.n_selected < 2) {
      toast('Not enough photos left, try the other direction.', true);
      return;
    }
    await reRender();
    toast(`Aiming for about ${t} seconds, re-rendering…`);
  } catch (e) {
    toast(e.message, true);
  } finally {
    $('shorter').disabled = false;
    $('longer').disabled = false;
  }
}

export function pollRender() {
  clearInterval(S.poll);
  const tick = async () => {
    try {
      const r = await api(`/renders/${S.renderId}`);
      const label = {
        queued: 'queued', frames: 'building frames', encoding: 'ffmpeg encoding',
        audio: 'muxing audio', done: 'done', error: 'error',
      }[r.status] || r.status;
      $('renderState').innerHTML = r.status === 'error'
        ? `<div class="err">Error: ${esc(r.err)}</div>`
        : `<p><b>${label}</b> — ${r.n_done}/${r.n_total} frames</p>`
          + `<progress max="100" value="${r.pct}"></progress>`;
      if (r.status === 'done') {
        clearInterval(S.poll);
        done(r);
      } else if (r.status === 'error') {
        clearInterval(S.poll);
        free();
      }
    } catch (e) {
      clearInterval(S.poll);
      free();
      toast(e.message, true);
    }
  };
  tick();
  S.poll = setInterval(tick, 2000);
}

function free() {
  if ($('render')) $('render').disabled = false;
  if ($('reRender')) $('reRender').disabled = false;
}

async function done(r) {
  free();
  S.out = r;
  const url = videoUrl(S.renderId);
  $('player').src = url;
  $('player').classList.remove('hide');
  const st = r.story || {};
  $('renderState').innerHTML = '<p><b>Done</b> — '
    + `${r.duration_s ? r.duration_s.toFixed(1) : '?'} seconds`
    + (st.n_shots ? ` · ${st.n_shots} shots` : '')
    + (st.n_chapter ? ` · ${st.n_chapter} chapters` : '')
    + `. <a href="${url}" download>Download mp4</a></p>`
    + (st.n_missing ? `<div class="warn">${st.n_missing} photos had no readable `
      + 'preview file and were skipped.</div>' : '');
  const {loadRenders} = await import('./render.js');
  loadRenders();
  toast('Video rendered');
}
