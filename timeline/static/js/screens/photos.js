// Screen 2 — what was selected, and how it is spread over time.
//
// This screen answers one question the numbers alone cannot: is the coverage even,
// or is the video about to jump across a five-year hole.

import {$, el, esc} from '../core/dom.js';
import {S} from '../core/state.js';
import {go, screen} from '../core/router.js';

screen('2', {
  label: 'Photos',
  build: () => `
    <div id="projHead"></div>
    <div class="cards">
      <div class="card"><b id="n_candidate">0</b><span>photos of this person</span></div>
      <div class="card"><b id="n_pass">0</b><span>pass the pose thresholds</span></div>
      <div class="card ok"><b id="n_selected">0</b><span>in the video</span></div>
      <div class="card"><b id="n_years">0</b><span>years covered</span></div>
    </div>
    <h3>Distribution by year</h3>
    <div id="chart" class="chart"></div>
    <div id="gaps"></div>
    <div class="bar">
      <button id="toStep3" class="primary">Tune the thresholds →</button>
    </div>`,
  ready: () => { $('toStep3').onclick = () => go('3'); },
  mount: () => { if (S.result) showStep2(S.result); },
});

export function showStep2(r) {
  if (!$('projHead')) return;
  const p = S.person || {};
  const how = r.story
    ? `${r.story.n_chapter} chapters · ${esc(r.story.grain_label)} · ~${r.story.est_seconds}s long`
    : `${r.filters.per_bucket} photos every ${r.filters.bucket_days} days`;
  $('projHead').innerHTML = `<h2>${esc(p.name || '(unnamed)')}</h2>`
    + `<p class="muted">${p.first_seen ? p.first_seen.slice(0, 10) : '?'} → `
    + `${p.last_seen ? p.last_seen.slice(0, 10) : '?'}`
    + (p.n_cluster > 1 ? ` · ${p.n_cluster} clusters merged` : '')
    + ` · ${how}</p>`;
  $('n_candidate').textContent = r.n_candidate;
  $('n_pass').textContent = r.n_pass;
  $('n_selected').textContent = r.n_selected;
  $('n_years').textContent = r.timeline.length;
  drawChart(r.timeline);
  $('gaps').innerHTML = r.gaps.length
    ? '<div class="warn">Long gaps: '
      + r.gaps.slice(0, 5)
        .map((g) => `${g.from.slice(0, 7)} → ${g.to.slice(0, 7)} (${g.days} days)`)
        .join(', ')
      + '. The video will jump at these points.</div>'
    : '';
}

function drawChart(tl) {
  const max = Math.max(1, ...tl.map((t) => t.n));
  $('chart').innerHTML = '';
  tl.forEach((t) => {
    const d = el('div');
    d.style.height = `${Math.round(100 * t.n / max)}%`;
    d.innerHTML = `<b>${t.n}</b><span>${t.year}</span>`;
    $('chart').appendChild(d);
  });
}
