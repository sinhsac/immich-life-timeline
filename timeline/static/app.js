'use strict';
// Four-step UI. No framework: a single file you can read top to bottom.
// Token is taken from ?token=... on the URL then remembered, so the page still
// opens when the service has auth switched on.

const TOKEN = new URLSearchParams(location.search).get('token')
  || localStorage.getItem('tl_token') || '';
if (TOKEN) localStorage.setItem('tl_token', TOKEN);

const S = {
  person: null, projectId: null, filters: {}, defaults: null,
  result: null, renderId: null, poll: null, postures: [], orients: [],
  statusTimer: null, prevTimer: null, sbTimer: null, sb: null, out: null,
  picked: new Map(), people: [], sug: [], view: 1,
  // Each element is ONE PERSON: {name, ids:[cluster...]}. Empty = all the
  // currently selected clusters belong to the same person (the common case).
  subjects: [],
};

const num = (n) => (n || 0).toLocaleString('en-US');

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};

async function api(path, opts = {}) {
  const h = Object.assign({'Content-Type': 'application/json'}, opts.headers || {});
  if (TOKEN) h['Authorization'] = 'Bearer ' + TOKEN;
  const r = await fetch('/api' + path, Object.assign({}, opts, {headers: h}));
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* empty body */ }
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
}

const imgUrl = (kind, key, size) => {
  const [a, f] = key.split(':');
  return `/api/${kind}/${a}/${f}?size=${size}` + (TOKEN ? `&token=${TOKEN}` : '');
};

// Framing of the video: the face is only the anchor point, face_frac decides
// whether we crop wide or tight. The preview must use exactly this set of
// parameters, otherwise it lies about the result.
function framingOpts() {
  return {
    aspect: $('r_aspect').value,
    size: Number($('r_size').value),
    face_frac: Number($('r_face_frac').value),
    eye_y: Number($('r_eye_y').value),
    fill: $('r_fill').value,
    level: $('r_level').checked,
  };
}

const alignedUrl = (key, size, o) => {
  const [a, f] = key.split(':');
  const q = new URLSearchParams({
    size: size, aspect: o.aspect, face_frac: o.face_frac,
    eye_y: o.eye_y, fill: o.fill, level: o.level,
  });
  if (TOKEN) q.set('token', TOKEN);
  return `/api/aligned/${a}/${f}?${q.toString()}`;
};

function toast(msg, bad) {
  const n = el('div', bad ? 'bad' : '', msg);
  $('toast').appendChild(n);
  setTimeout(() => n.remove(), bad ? 9000 : 4000);
}

function step(n) {
  document.querySelectorAll('.step').forEach((s) => s.classList.remove('on'));
  $('s' + n).classList.add('on');
  $('navStats').classList.remove('on');
  // #navStats has no data-step so this loop never toggles it, we only drop .on
  document.querySelectorAll('#steps button').forEach((b) => {
    b.classList.toggle('on', b.dataset.step === String(n));
  });
  S.view = n;
  window.scrollTo(0, 0);
}

// Unlock steps 2/3/4 once a project exists. Previously the condition was
// "step <= n", so pressing step 5 also unlocked 2/3/4 even though there was
// nothing in them yet.
function unlockSteps() {
  document.querySelectorAll('#steps button[data-step]').forEach((b) => {
    if (b.dataset.step !== '1') b.disabled = !S.projectId;
  });
}

// Expert mode is just a CSS class: the .adv sections hide when it is off. That
// way the default path shows not a single slider, without having to build two
// separate UIs.
function setExpert(on) {
  document.body.classList.toggle('expert', !!on);
  $('expert').checked = !!on;
  localStorage.setItem('tl_expert', on ? '1' : '0');
}

// Statistics page: not part of the 4-step flow, so entering and leaving it
// never loses the progress already made.
function showStats() {
  document.querySelectorAll('.step').forEach((s) => s.classList.remove('on'));
  document.querySelectorAll('#steps button').forEach((b) => b.classList.remove('on'));
  $('sStats').classList.add('on');
  $('navStats').classList.add('on');
  S.view = 'stats';
  window.scrollTo(0, 0);
  pollStatus();                 // refresh on open, do not wait for the 20s cycle
}

// ================================================================= start up
(async function init() {
  document.querySelectorAll('#steps button').forEach((b) => {
    b.onclick = () => { if (!b.disabled) step(Number(b.dataset.step)); };
  });
  $('reload').onclick = loadPeople;
  $('findSim').onclick = findSimilar;
  $('mkVideo').onclick = makeVideo;
  $('mkAdvanced').onclick = openAdvanced;
  $('navStats').onclick = showStats;
  $('statsReload').onclick = pollStatus;
  $('pickAllSug').onclick = pickAllSuggestions;
  $('hideSug').onclick = () => $('simBox').classList.add('hide');
  $('addSubject').onclick = addSubject;
  $('clearPick').onclick = () => {
    S.picked.clear(); S.subjects = []; syncPicked();
  };
  $('expert').onchange = () => setExpert($('expert').checked);
  setExpert(localStorage.getItem('tl_expert') === '1');
  $('shorter').onclick = () => nudgeLength(0.7);
  $('longer').onclick = () => nudgeLength(1.45);
  $('reRender').onclick = () => startRender();
  $('o_aspect').onchange = () => startRender();
  $('toExpert').onclick = () => {
    setExpert(true); step(3); renderResult();
  };
  $('minSim').oninput = () => { $('o_minSim').textContent = $('minSim').value; };
  $('minSim').oninput();
  $('toStep3').onclick = () => { step(3); renderResult(); };
  $('toStep4').onclick = () => { step(4); enterStep4(); };
  $('f_mode').onchange = syncMode;
  $('f_auto_len').onchange = syncAutoLen;
  $('r_aspect').addEventListener('change', () => {
    $('o_aspect').value = $('r_aspect').value;
  });
  $('r_mode').onchange = () => { syncRenderMode(); storyboardSoon(); };
  ['r_motion', 'r_title', 'r_chapter_card', 'r_birth_year', 'r_out_fps',
    'r_label', 'r_smooth', 'r_audio', 'r_audio_normalize'].forEach((id) => {
    $(id).onchange = storyboardSoon;
  });
  ['r_audio_lead', 'r_audio_tail'].forEach((id) => {
    $(id).oninput = () => { $('o_' + id.slice(2)).textContent = $(id).value; };
    $(id).oninput();
  });
  syncRenderMode();
  $('apply').onclick = applyFilters;
  $('resetF').onclick = () => { setFilterUI(S.defaults.filters); applyFilters(); };
  $('render').onclick = startRender;
  $('f_use_body').onchange = () => {
    $('bodyOpts').classList.toggle('hide', !$('f_use_body').checked);
  };
  document.querySelectorAll('.tabs button').forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll('.tabs button').forEach((x) => x.classList.remove('on'));
      b.classList.add('on');
      const sel = b.dataset.tab === 'sel';
      $('gridSel').classList.toggle('hide', !sel);
      $('gridRej').classList.toggle('hide', sel);
      $('tabHint').textContent = sel
        ? 'Click a photo to drop it from the video.'
        : 'Near misses come first. If you see good photos being rejected, loosen '
          + 'the matching threshold on the left.';
    };
  });
  ['r_fps', 'r_eye_y', 'r_face_frac'].forEach((id) => {
    $(id).oninput = () => {
      $('o_' + id.slice(2)).textContent = $(id).value;
      if (id === 'r_fps') storyboardSoon();
      else previewSoon();
    };
    $(id).oninput();
  });
  ['r_aspect', 'r_size', 'r_fill', 'r_level'].forEach((id) => {
    $(id).onchange = previewSoon;
  });

  try {
    const h = await api('/health');
    showHealth(h);
    pollStatus();
    S.defaults = await api('/defaults');
    S.postures = ['standing', 'sitting', 'lying', 'unknown'];
    S.orients = ['front', 'side', 'back', 'unknown'];
    setFilterUI(S.defaults.filters);
    await loadPeople();
  } catch (e) {
    $('health').innerHTML = `<div class="err">Cannot reach the API: ${e.message}</div>`;
  }
})();

function showHealth(h) {
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
      + '(API_TOKEN is empty).</div>');
  }
  if (h.indexer.ok && h.ffmpeg.ok) bits.push(`<span class="muted">${h.indexer.detail}</span>`);
  $('health').innerHTML = bits.join('');
}

// ======================================================== indexer progress
// The indexer job runs outside this service (its own CronJob), so the UI only
// reads status from the state columns in fp_asset. Polling switches itself off
// once everything is done.
function showProgress(p) {
  // Label on the nav: just one number, so the user can tell whether the
  // statistics page is worth opening, without it taking room from the other
  // steps.
  const nav = $('navStats');
  if (p.ready) {
    const done = p.stages.reduce((a, s) => a + s.done, 0);
    const total = p.stages.reduce((a, s) => a + s.total, 0) || 1;
    const pct = Math.round(100 * done / total);
    nav.textContent = `Statistics · ${pct}%`;
    nav.classList.toggle('warnDot', !!p.running);
  } else {
    nav.textContent = 'Statistics';
  }

  if (!p.ready) {
    $('idxCards').innerHTML = '';
    $('idxBars').innerHTML = '<p class="muted">No fp_asset table yet — the '
      + 'indexer job has never run.</p>';
    $('idxRuns').innerHTML = '';
    return;
  }

  $('idxCards').innerHTML = [
    ['n_asset', 'photos in the library'],
    ['n_face', 'faces'],
    ['n_face_ready', 'faces with landmarks'],
    ['n_body', 'bodies'],
  ].map(([k, lab]) => `<div class="card"><b>${num(p[k])}</b><span>${lab}</span></div>`)
    .join('')
    + (((p.face_err || 0) + (p.body_err || 0))
      ? `<div class="card"><b class="bad">${num((p.face_err || 0) + (p.body_err || 0))}</b>`
        + '<span>photos that failed to read</span></div>' : '');

  $('idxBars').innerHTML = p.stages.map((s) => {
    const left = Math.max(0, s.total - s.done);
    return `<div class="prow${p.running === s.name ? ' run' : ''}">`
      + `<span class="plab">${s.label}`
      + (p.running === s.name ? ' <b>running</b>' : '') + '</span>'
      + `<span class="pbar"><i style="width:${Math.min(100, s.pct)}%"></i></span>`
      + `<span class="pnum">${s.pct}%<em>${num(s.done)}/${num(s.total)}`
      + (left ? ` · ${num(left)} left` : '') + '</em></span></div>';
  }).join('')
    + (p.running
      ? ''
      : '<p class="muted">No stage is running. The next job picks up exactly '
        + 'where this one stopped — progress lives in the database, so nothing '
        + 'is lost when the machine goes down.</p>');

  $('idxRuns').innerHTML = (p.runs || []).length
    ? '<table class="runs"><tr><th>Stage</th><th>Started</th><th>Finished</th>'
      + '<th>Processed</th><th>Errors</th><th>Note</th></tr>'
      + p.runs.map((r) => '<tr>'
        + `<td>${r.stage}</td>`
        + `<td>${(r.started_at || '').slice(0, 19).replace('T', ' ')}</td>`
        + `<td>${r.running ? '<b>running</b>'
          : (r.finished_at || '').slice(11, 19)}</td>`
        + `<td>${num(r.n_done)}</td>`
        + `<td>${r.n_err ? `<span class="bad">${num(r.n_err)}</span>` : '0'}</td>`
        + `<td class="muted">${r.note || ''}</td></tr>`).join('')
      + '</table>'
    : '<p class="muted">no runs yet</p>';

  $('statsAt').textContent = 'updated ' + new Date().toLocaleTimeString('en-US');
}

const progressDone = (p) => p.ready && p.stages.every((s) => s.done >= s.total);

async function pollStatus() {
  let wait = 20000;
  try {
    const [h, p] = await Promise.all([api('/health'), api('/progress')]);
    showHealth(h);
    showProgress(p);
    if (progressDone(p)) return;            // all done, so stop polling for good
  } catch (e) {
    wait = 60000;                           // on error back off, do not hammer it
  }
  clearTimeout(S.statusTimer);
  S.statusTimer = setTimeout(pollStatus, wait);
}

// ================================================================ step 1
async function loadPeople() {
  $('people').innerHTML = '<p class="muted">loading…</p>';
  try {
    const d = await api('/people?min_ready=' + Number($('minReady').value || 10));
    $('peopleCount').textContent = `${d.people.length} people`;
    $('people').innerHTML = '';
    if (!d.people.length) {
      $('people').innerHTML = '<p class="muted">No people yet. Has Immich '
        + 'finished Facial Recognition, and has the indexer job finished?</p>';
      return;
    }
    S.people = d.people;
    d.people.forEach((p) => $('people').appendChild(personNode(p)));
    syncPicked();
  } catch (e) {
    $('people').innerHTML = `<div class="err">${e.message}</div>`;
  }
}

// Auto-select threshold. Measured on a real library: clusters of the same
// person score ~0.55, while a DIFFERENT person who already had a name reached
// 0.44 -> 0.5 is a sensible line, and name_conflict filters on top of it.
// Anything above this line is ticked for you, the user only has to untick.
const AUTO_SIM = 0.5;

// The reason string the server writes when a photo is dropped by hand. Must match
// select.MANUAL_REASON: only these rejects can be put back by clicking them.
const MANUAL_REASON = 'dropped by hand';

function personNode(p, sim, isSug) {
  const n = el('div', 'person' + (isSug ? ' sug' : ''));
  n.dataset.pid = p.person_id;
  const img = p.cover
    ? `<img loading="lazy" src="${imgUrl('thumb', p.cover.replace('/', ':'), 160)}" alt="">`
    : '<img alt="">';
  const years = [p.first_seen, p.last_seen]
    .map((x) => (x ? x.slice(0, 4) : '?')).join('–');
  n.innerHTML = img
    + '<div class="tick">✓</div>'
    + (sim !== undefined
      ? `<div class="sim${p.name_conflict ? ' clash' : ''}">${Math.round(sim * 100)}%</div>`
      : '')
    + `<div class="nm">${p.name || '(unnamed)'}</div>`
    + `<div class="mt">${p.n_ready} photos · ${p.n_month} months · ${years}</div>`
    + (p.name_conflict ? '<div class="clashw">already named as someone else</div>' : '');
  n.onclick = () => togglePick(p);
  return n;
}

// Immich splits one person into several clusters by age, so here you can pick
// several clusters. S.picked is a Map person_id -> cluster info.
function togglePick(p) {
  if (S.picked.has(p.person_id)) S.picked.delete(p.person_id);
  else S.picked.set(p.person_id, p);
  syncPicked();
}

function syncPicked() {
  document.querySelectorAll('#people .person, #simList .person')
    .forEach((n) => n.classList.toggle('on', S.picked.has(n.dataset.pid)));

  const list = [...S.picked.values()];
  const n = list.length;
  $('pickBar').classList.toggle('hide', n === 0 && !S.subjects.length);
  showSubjects();
  $('addSubject').disabled = n === 0;
  $('mkVideo').disabled = n === 0 && !S.subjects.length;
  if (!n) {
    $('pickInfo').innerHTML = '<span class="muted">select more clusters, or press '
      + 'Create video</span>';
    $('pickNames').textContent = '';
    return;
  }

  const ready = list.reduce((a, p) => a + (p.n_ready || 0), 0);
  const years = list.map((p) => p.first_seen).filter(Boolean).sort();
  const last = list.map((p) => p.last_seen).filter(Boolean).sort();
  const span = years.length && last.length
    ? ` · ${years[0].slice(0, 4)}–${last[last.length - 1].slice(0, 4)}` : '';
  $('pickInfo').innerHTML = `<b>${n}</b> clusters · ${num(ready)} photos${span}`;
  $('pickNames').textContent = list
    .map((p) => p.name || '(unnamed)').join(', ');
}

// "A video of Mr A with Mrs B": each person is their own group of clusters.
// That cannot be inferred from one jumbled pile of clusters, so each person has
// to be committed one at a time.
function addSubject() {
  const list = [...S.picked.values()];
  if (!list.length) return;
  S.subjects.push({
    name: list.map((p) => p.name).filter(Boolean)[0] || '(unnamed)',
    ids: list.map((p) => p.person_id),
  });
  S.picked.clear();
  syncPicked();
  toast(`Added ${S.subjects[S.subjects.length - 1].name}. `
    + 'Now select the clusters of the next person.');
}

function showSubjects() {
  const has = S.subjects.length > 0;
  $('subjRow').classList.toggle('hide', !has);
  if (!has) return;
  $('subjChips').innerHTML = '';
  S.subjects.forEach((s, i) => {
    const b = el('button', 'chip',
      `${s.name} <em>${s.ids.length} clusters</em> ×`);
    b.onclick = () => { S.subjects.splice(i, 1); syncPicked(); };
    $('subjChips').appendChild(b);
  });
  $('together').parentElement.classList.toggle('hide',
    S.subjects.length + (S.picked.size ? 1 : 0) < 2);
}

// Final list of people sent up: the people already committed + the clusters
// still half-selected (counted as one more person). With nobody committed yet,
// all the selected clusters = one person.
function subjectPayload() {
  const groups = S.subjects.map((s) => s.ids);
  const cur = [...S.picked.keys()];
  if (cur.length) groups.push(cur);
  return groups;
}

function subjectName() {
  const names = S.subjects.map((s) => s.name);
  const cur = [...S.picked.values()].map((p) => p.name).filter(Boolean);
  if (cur.length) names.push(cur[0]);
  return names.filter((n) => n && n !== '(unnamed)').join(' & ');
}

async function findSimilar() {
  if (!S.picked.size) return;
  const seeds = [...S.picked.keys()];
  $('findSim').disabled = true;
  $('simList').innerHTML = '<p class="muted">matching embeddings…</p>';
  $('simNote').textContent = '';
  $('simBox').classList.remove('hide');
  try {
    const d = await api(`/people/${seeds[0]}/similar?min_sim=`
      + Number($('minSim').value || 0.3)
      + '&seeds=' + encodeURIComponent(seeds.join(',')));
    S.sug = d.similar || [];
    if (!S.sug.length) {
      $('simHead').textContent = 'No cluster was similar enough';
      $('simList').innerHTML = '';
      $('simNote').textContent = 'Lower the "Similarity ≥" threshold and search '
        + `again to widen the net. Compared against ${num(d.n_cluster)} clusters.`;
      return;
    }

    // Auto-tick the clusters that are certain enough, so there is no need to
    // click them one by one.
    const auto = S.sug.filter((p) => !p.name_conflict && p.similarity >= AUTO_SIM);
    auto.forEach((p) => S.picked.set(p.person_id, p));

    $('simHead').textContent = `${S.sug.length} clusters look like the same person`;
    $('simList').innerHTML = '';
    S.sug.forEach((p) => $('simList').appendChild(personNode(p, p.similarity, true)));
    syncPicked();

    const pct = Math.round(AUTO_SIM * 100);
    $('simNote').innerHTML = `Compared against ${num(d.n_cluster)} clusters. `
      + (auto.length
        ? `<b>${auto.length} clusters were ticked automatically</b> — they scored `
          + `${pct}% or above and carry no conflicting name. Untick any that look `
          + 'wrong.'
        : `No cluster reached ${pct}%, so nothing was ticked automatically — `
          + 'select them yourself by looking at the photos.')
      + ' Select more, then press "Find clusters of the same person" again to '
      + 'keep widening the net.';
    $('simBox').scrollIntoView({behavior: 'smooth', block: 'start'});
    toast(auto.length
      ? `Auto-selected ${auto.length}/${S.sug.length} suggested clusters`
      : `Found ${S.sug.length} suggested clusters, none selected automatically`);
  } catch (e) {
    $('simList').innerHTML = `<div class="err">${e.message}</div>`;
  } finally {
    $('findSim').disabled = false;
  }
}

function pickAllSuggestions() {
  const ok = (S.sug || []).filter((p) => !p.name_conflict);
  if (!ok.length) return;
  ok.forEach((p) => S.picked.set(p.person_id, p));
  syncPicked();
  toast(`Selected ${ok.length} suggested clusters `
    + '(skipping the ones already named as someone else)');
}

function requestBody() {
  const groups = subjectPayload();
  return {
    subjects: groups,
    together: groups.length > 1 && $('together').checked,
    name: subjectName() || null,
    date_from: $('dFrom').value || null,
    date_to: $('dTo').value || null,
  };
}

function rememberPerson() {
  const list = [...S.picked.values()];
  const all = list.concat(S.subjects.map((s) => ({name: s.name})));
  S.person = {
    name: subjectName(),
    first_seen: list.map((p) => p.first_seen).filter(Boolean).sort()[0],
    last_seen: list.map((p) => p.last_seen).filter(Boolean).sort().pop(),
    n_cluster: list.length + S.subjects.reduce((a, s) => a + s.ids.length, 0),
    n_subject: all.length ? subjectPayload().length : 1,
  };
}

// DEFAULT PATH: one press, no setup at all. A single request creates the
// project, selects the photos and starts the render — the server infers every
// threshold and the length itself.
async function makeVideo() {
  if (!S.picked.size && !S.subjects.length) return;
  rememberPerson();
  $('mkVideo').disabled = true;
  step(5);
  $('outHead').innerHTML = '<h3>Selecting photos…</h3>';
  $('renderState').innerHTML = '<p class="muted">reading face data…</p>';
  $('player').classList.add('hide');
  $('outStory').innerHTML = '';
  try {
    const r = await api('/videos', {
      method: 'POST',
      body: JSON.stringify(Object.assign(requestBody(), {
        options: {aspect: $('o_aspect').value},
      })),
    });
    S.projectId = r.project_id;
    S.result = r;
    S.filters = r.filters;
    setFilterUI(r.filters);
    showStep2(r);
    unlockSteps();
    showOut(r);

    // ffmpeg needs at least 2 frames. Below that, do not render blindly and
    // then report a cryptic error — turn on expert mode and push the user over
    // to where the thresholds are.
    if (!r.render_id) {
      setExpert(true);
      step(3);
      renderResult();
      toast(`Only ${r.n_selected} photos were selected, at least 2 are needed. `
        + 'Loosen the thresholds on the left, then press Apply.', true);
      return;
    }
    S.renderId = r.render_id;
    pollRender();
  } catch (e) {
    $('outHead').innerHTML = '';
    $('renderState').innerHTML = `<div class="err">${e.message}</div>`;
    toast(e.message, true);
  } finally {
    $('mkVideo').disabled = false;
  }
}

// Expert path: only create the project, then stop at the step that shows the
// photos that were selected.
async function openAdvanced() {
  if (!S.picked.size && !S.subjects.length) return;
  rememberPerson();
  $('mkAdvanced').disabled = true;
  try {
    const r = await api('/projects', {
      method: 'POST', body: JSON.stringify(requestBody()),
    });
    S.projectId = r.project_id;
    S.result = r;
    S.filters = r.filters;
    setFilterUI(r.filters);
    showStep2(r);
    unlockSteps();
    setExpert(true);
    step(2);
  } catch (e) {
    toast(e.message, true);
  } finally {
    $('mkAdvanced').disabled = false;
  }
}

// -------------------------------------------------------------- video page
function showOut(r) {
  const p = S.person || {};
  const st = r.story;
  const who = p.name || '(unnamed)';
  const range = [$('dFrom').value, $('dTo').value].filter(Boolean).join(' → ');
  $('outHead').innerHTML = `<h3>${who}</h3>`
    + '<p class="muted">'
    + (st ? `${r.n_selected} shots · ${st.n_chapter} chapters`
      + (st.n_clip ? ` · ${st.n_clip} video clips` : '') + ` · ${st.grain_label}`
      : `${r.n_selected} photos`)
    + (range ? ` · ${range}` : '')
    + '</p>';
  $('outStory').innerHTML = '';
}

// "Shorter / Longer" instead of a length slider: the user reacts to what they
// have already seen, rather than guessing a number before seeing anything.
async function nudgeLength(k) {
  const cur = (S.out && S.out.duration_s)
    || (S.sb && S.sb.duration_s) || 60;
  const t = Math.max(15, Math.min(240, Math.round(cur * k)));
  $('shorter').disabled = $('longer').disabled = true;
  try {
    const r = await api(`/projects/${S.projectId}/filters`, {
      method: 'PATCH',
      body: JSON.stringify({filters: {target_seconds: t}}),
    });
    S.result = r;
    S.filters = r.filters;
    setFilterUI(r.filters);
    showOut(r);
    if (r.n_selected < 2) {
      toast('Not enough photos left, try the other direction.', true);
      return;
    }
    await startRender();
    toast(`Aiming for about ${t} seconds, re-rendering…`);
  } catch (e) {
    toast(e.message, true);
  } finally {
    $('shorter').disabled = $('longer').disabled = false;
  }
}

// ================================================================ step 2
function showStep2(r) {
  const p = S.person;
  const how = r.story
    ? `${r.story.n_chapter} chapters · ${r.story.grain_label} · ~${r.story.est_seconds}s long`
    : `${r.filters.per_bucket} photos every ${r.filters.bucket_days} days`;
  $('projHead').innerHTML = `<h3>${p.name || '(unnamed)'}</h3>`
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
      + r.gaps.slice(0, 5).map((g) => `${g.from.slice(0, 7)} → ${g.to.slice(0, 7)} (${g.days} days)`).join(', ')
      + '. The video will jump at these points.</div>'
    : '';
}

function drawChart(tl) {
  const max = Math.max(1, ...tl.map((t) => t.n));
  $('chart').innerHTML = '';
  tl.forEach((t) => {
    const d = el('div');
    d.style.height = Math.round(100 * t.n / max) + '%';
    d.innerHTML = `<b>${t.n}</b><span>${t.year}</span>`;
    $('chart').appendChild(d);
  });
}

// ================================================================ step 3
const RANGE_KEYS = ['max_yaw', 'max_pitch', 'max_roll', 'min_frontality',
  'min_ear', 'min_eye_ratio', 'min_sharp', 'bucket_days', 'per_bucket',
  'target_seconds', 'max_per_chapter', 'max_clip_motion'];
const SELECT_KEYS = ['mode', 'pace', 'chapter_by'];

// mode='story' and mode='even' use two different sets of parameters. Showing
// both at once means the user drags a slider that has no effect at all, with no
// idea why.
function syncMode() {
  const story = $('f_mode').value === 'story';
  $('storyOpts').classList.toggle('hide', !story);
  $('evenOpts').classList.toggle('hide', story);
}

function syncAutoLen() {
  $('lenWrap').classList.toggle('off', $('f_auto_len').checked);
}

function setFilterUI(f) {
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
  // target_seconds = null means "infer it", not "do not send it". The slider
  // still holds a sensible number so unticking the box works straight away.
  const auto = f.target_seconds === null || f.target_seconds === undefined;
  $('f_auto_len').checked = auto;
  if (!auto) $('f_target_seconds').value = f.target_seconds;
  else if (S.result && S.result.story) {
    $('f_target_seconds').value = Math.max(15, Math.min(240,
      Math.round(S.result.story.est_seconds || 60)));
  }
  $('o_target_seconds').textContent = $('f_target_seconds').value;
  syncAutoLen();
  syncMode();
  $('f_use_clips').checked = !!f.use_clips;
  $('f_allow_others').checked = !!f.allow_others;
  $('f_max_faces').value = f.max_faces ?? 0;
  $('f_use_body').checked = !!f.use_body;
  $('f_allow_missing_body').checked = !!f.allow_missing_body;
  $('bodyOpts').classList.toggle('hide', !f.use_body);
  chips('c_postures', S.postures, f.postures || []);
  chips('c_orientations', S.orients, f.orientations || []);
}

function fmt(k, v) {
  if (k === 'min_eye_ratio') return (Number(v) * 100).toFixed(1) + '%';
  if (k === 'min_frontality' || k === 'min_ear') return Number(v).toFixed(2);
  return String(v);
}

// Story summary: how many chapters, which ones are full and which are thin.
// This is where the user sees straight away what kind of video they will get,
// before rendering.
function showStory(st) {
  const box = $('storyInfo');
  if (!st) {
    box.innerHTML = '<p class="muted">Even spread mode: duration = photo count / '
      + 'photos per second, set in the render settings.</p>';
    return;
  }
  const max = Math.max(1, ...st.chapters.map((c) => c.n_pick));
  box.innerHTML = `<div class="cards small">`
    + `<div class="card ok"><b>${st.n_chapter}</b><span>chapters</span></div>`
    + `<div class="card"><b>${st.n_hero}</b><span>hero shots</span></div>`
    + (st.n_clip ? `<div class="card"><b>${st.n_clip}</b><span>video clips</span></div>` : '')
    + `<div class="card"><b>~${st.est_seconds}s</b><span>estimated length</span></div>`
    + (st.auto
      ? '<div class="card"><b>inferred</b><span>duration</span></div>'
      : `<div class="card"><b>${st.target_seconds}s</b><span>set by hand</span></div>`)
    + '</div>'
    + `<p class="muted">${st.grain_label} · hero shots held ${st.hold_hero}s, `
    + `supporting shots ${st.hold_beat}s</p>`
    // Raising the budget without the video getting longer is always one of these
    // two reasons. Say which one, or the user drags the slider for nothing.
    + (st.capped ? '<div class="warn">Every chapter has hit the ceiling of '
      + `<b>${st.max_per_chapter} photos per chapter</b>. Raising the target `
      + 'duration will not add any more photos — raise this ceiling, or set "One '
      + 'chapter covers" to something finer to get more chapters.</div>' : '')
    + (st.exhausted && !st.capped ? '<div class="warn">Every photo that passes '
      + 'the thresholds has been used. For a longer video, loosen the filter '
      + 'thresholds on the left.</div>' : '')
    + '<div class="chapbars">'
    + st.chapters.map((c) => `<div class="cb" title="${c.n_avail} photos pass the`
      + ` thresholds in this period"><span class="cbl">${c.label}</span>`
      + `<span class="cbb"><i style="width:${Math.round(100 * c.n_pick / max)}%"></i></span>`
      + `<span class="cbn">${c.n_pick}<em>of ${num(c.n_avail)}</em></span></div>`).join('')
    + '</div>';
}

function chips(box, all, active) {
  const n = $(box);
  n.innerHTML = '';
  const set = new Set(active);
  all.forEach((v) => {
    const b = el('button', set.has(v) ? 'on' : '', v);
    b.onclick = () => b.classList.toggle('on');
    n.appendChild(b);
  });
}

function readChips(box) {
  return Array.from($(box).querySelectorAll('button.on')).map((b) => b.textContent);
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
  f.use_clips = $('f_use_clips').checked;
  f.allow_others = $('f_allow_others').checked;
  f.max_faces = Number($('f_max_faces').value || 0);
  f.use_body = $('f_use_body').checked;
  f.allow_missing_body = $('f_allow_missing_body').checked;
  f.postures = readChips('c_postures');
  f.orientations = readChips('c_orientations');
  return f;
}

async function applyFilters() {
  $('apply').disabled = true;
  $('applying').textContent = 'recomputing…';
  try {
    const r = await api(`/projects/${S.projectId}/filters`, {
      method: 'PATCH',
      body: JSON.stringify({filters: readFilterUI()}),
    });
    S.result = r;
    S.filters = r.filters;
    renderResult();
    showStep2(r);
    $('applying').textContent = '';
  } catch (e) {
    $('applying').innerHTML = `<span class="err">${e.message}</span>`;
  } finally {
    $('apply').disabled = false;
  }
}

function renderResult() {
  const r = S.result;
  if (!r) return;
  $('r_selected').textContent = r.n_selected;
  $('r_rejected').textContent = r.n_rejected;
  showStory(r.story);
  $('reasons').innerHTML = Object.keys(r.reasons).length
    ? '<table><tr><th>Rejection reason</th><th>Photos</th></tr>'
      + Object.entries(r.reasons).map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('')
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

// Storytelling mode: group photos by CHAPTER instead of one flat grid. Seeing a
// chapter that holds only one photo tells you at once which period is short on
// data.
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
      gs.appendChild(el('div', 'chapHead', `<b>${f.label || ''}</b>`));
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
    + (selected ? '' : `<div class="why">${f.reason || ''}</div>`)
    + (clip ? `<div class="clipTag">▶ ${(f.dur_s || 0).toFixed(1)}s</div>`
      : (f.hero ? '<div class="heroTag">hero</div>' : ''))
    + `<div class="dt">${dt}</div>`;
  n.title = clip
    ? [`${dt} · video clip`,
      `from ${((f.t_start_ms || 0) / 1000).toFixed(1)}s, ${(f.dur_s || 0).toFixed(1)}s long`,
      f.t_peak_ms != null
        ? `peak moment at ${(f.t_peak_ms / 1000).toFixed(1)}s of the source clip`
        : '',
      `frontality ${f.frontality} · sharpness ${f.sharp}`,
      `shake ${f.motion}`,
      f.reason ? `REJECTED: ${f.reason}` : ''].filter(Boolean).join('\n')
    : [`${dt}`, `yaw ${f.yaw}° pitch ${f.pitch}° roll ${f.roll}°`,
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
    // keep the old rejected list so the tab being viewed is not lost
    S.result = Object.assign({}, r, {rejected: S.result.rejected});
    renderResult();
    toast(exclude ? 'Photo dropped from the video' : 'Photo put back');
  } catch (e) {
    toast(e.message, true);
  }
}

// ================================================================ step 4
// Preview 3 frames spread far apart in time. Every photo has to be fetched
// through the Immich API, so debounce it instead of firing on every step of the
// slider.
function renderPreview() {
  const sel = (S.result && S.result.selected) || [];
  const box = $('framePreview');
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

function previewSoon() {
  clearTimeout(S.prevTimer);
  S.prevTimer = setTimeout(renderPreview, 450);
}

// Parameters of the render step. The pace (pace/target_seconds) is NOT here: it
// belongs to the photo selection step, because the number of photos selected
// follows exactly those figures. Changing the pace means going back to step 3
// and recomputing, otherwise the video length will not match the budget.
function renderOpts() {
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
    audio: $('r_audio').checked,
    audio_lead: Number($('r_audio_lead').value),
    audio_tail: Number($('r_audio_tail').value),
    audio_normalize: $('r_audio_normalize').checked,
    fps: Number($('r_fps').value),
    smooth: $('r_smooth').value,
  });
}

function syncRenderMode() {
  const story = $('r_mode').value === 'story';
  $('storyRender').classList.toggle('hide', !story);
  $('flipRender').classList.toggle('hide', story);
}

// Entering step 4: the render mode has to match the mode the photos were picked
// with. Picking with 'even' leaves the frames without chapters, so rendering in
// story mode gives a string of equal-length shots with no labels — technically
// correct, but not what the user wanted.
function enterStep4() {
  if (S.filters && S.filters.mode) {
    $('r_mode').value = S.filters.mode === 'even' ? 'flip' : 'story';
  }
  syncRenderMode();
  loadRenders();
  renderPreview();
  loadStoryboard();
}

// The storyboard is computed on the server with exactly the render step's own
// algorithm, so the duration figure is the real one. Debounced because every
// call has to re-read the frames from the db.
function storyboardSoon() {
  clearTimeout(S.sbTimer);
  S.sbTimer = setTimeout(loadStoryboard, 350);
}

async function loadStoryboard() {
  if (!S.projectId) return;
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
            + 'at least one photo. Go back to the thresholds step and set "One '
            + 'chapter covers" to something coarser, or raise the target '
            + 'duration.</div>' : '')
        + (d.n_missing ? `<div class="warn">${d.n_missing} photos had no readable `
          + 'preview file and were dropped from the story.</div>' : '')
        + '<div class="chapbars">'
        + d.chapters.map((c) => '<div class="cb">'
          + `<span class="cbl">${c.label}</span>`
          + `<span class="cbb"><i style="width:${Math.round(100 * c.seconds / mx)}%"></i></span>`
          + `<span class="cbn">${c.seconds}s<em>${c.n} photos</em></span></div>`).join('')
        + '</div>';
    }
    estimate();
  } catch (e) {
    $('sbInfo').innerHTML = `<div class="err">${e.message}</div>`;
  }
}

function estimate() {
  const d = S.sb;
  if (d && d.n_shots) {
    $('renderEst').textContent = `${d.n_shots} shots → a ${d.duration_s} second video`;
    return;
  }
  const n = S.result ? S.result.n_selected : 0;
  const fps = Number($('r_fps').value);
  $('renderEst').textContent = n
    ? `${n} photos at ${fps} per second → roughly ${(n / fps).toFixed(1)} seconds`
    : '';
}

async function startRender() {
  if (!S.projectId) return;
  $('render').disabled = true;
  $('reRender').disabled = true;
  $('r_aspect').value = $('o_aspect').value;      // two places, one value
  step(5);
  try {
    const r = await api(`/projects/${S.projectId}/render`, {
      method: 'POST',
      body: JSON.stringify({options: renderOpts()}),
    });
    S.renderId = r.render_id;
    $('player').classList.add('hide');
    pollRender();
  } catch (e) {
    toast(e.message, true);
    $('render').disabled = false;
    $('reRender').disabled = false;
  }
}

function pollRender() {
  clearInterval(S.poll);
  const tick = async () => {
    try {
      const r = await api('/renders/' + S.renderId);
      const label = {queued: 'queued', frames: 'building frames',
        encoding: 'ffmpeg encoding', audio: 'muxing audio',
        done: 'done', error: 'error'}[r.status] || r.status;
      $('renderState').innerHTML = r.status === 'error'
        ? `<div class="err">Error: ${r.err}</div>`
        : `<p><b>${label}</b> — ${r.n_done}/${r.n_total} frames</p>`
          + `<progress max="100" value="${r.pct}"></progress>`;
      if (r.status === 'done') {
        clearInterval(S.poll);
        $('render').disabled = false;
        $('reRender').disabled = false;
        S.out = r;
        const url = `/api/renders/${S.renderId}/video` + (TOKEN ? `?token=${TOKEN}` : '');
        $('player').src = url;
        $('player').classList.remove('hide');
        const st = r.story || {};
        $('renderState').innerHTML = `<p><b>Done</b> — `
          + `${r.duration_s ? r.duration_s.toFixed(1) : '?'} seconds`
          + (st.n_shots ? ` · ${st.n_shots} shots` : '')
          + (st.n_chapter ? ` · ${st.n_chapter} chapters` : '')
          + `. <a href="${url}" download>Download mp4</a></p>`
          + (st.n_missing ? `<div class="warn">${st.n_missing} photos had no `
            + 'readable preview file and were skipped.</div>' : '');
        loadRenders();
        toast('Video rendered');
      } else if (r.status === 'error') {
        clearInterval(S.poll);
        $('render').disabled = false;
        $('reRender').disabled = false;
      }
    } catch (e) {
      clearInterval(S.poll);
      $('render').disabled = false;
      $('reRender').disabled = false;
      toast(e.message, true);
    }
  };
  tick();
  S.poll = setInterval(tick, 2000);
}

async function loadRenders() {
  try {
    const d = await api(`/projects/${S.projectId}/renders`);
    $('renderList').innerHTML = d.renders.length
      ? '<table><tr><th>#</th><th>Status</th><th>Frames</th><th>Length</th><th></th></tr>'
        + d.renders.map((r) => `<tr><td>${r.id}</td><td>${r.status}</td>`
          + `<td>${r.n_done}/${r.n_total}</td>`
          + `<td>${r.duration_s ? r.duration_s.toFixed(1) + 's' : '—'}</td>`
          + `<td>${r.status === 'done'
            ? `<a href="/api/renders/${r.id}/video${TOKEN ? '?token=' + TOKEN : ''}" download>download</a>`
            : (r.err || '')}</td></tr>`).join('')
        + '</table>'
      : '<p class="muted">No renders yet.</p>';
  } catch (e) { /* not important */ }
}
