// Screen 1 — pick the person, which is the only thing the default path asks for.
//
// Immich splits ONE person into SEVERAL clusters when the span is long: child,
// teenager, adult usually end up as three. So this screen is about selecting a
// set of clusters, and widening that set step by step.

import {$, el, esc, num, toast} from '../core/dom.js';
import {api, imgUrl} from '../core/api.js';
import {AUTO_SIM, S} from '../core/state.js';
import {go, screen, setExpert, unlockSteps} from '../core/router.js';
import {renderResult, setFilterUI} from './thresholds.js';
import {showStep2} from './photos.js';
import {showOut} from './video.js';

screen('1', {
  label: 'Person',
  build: () => `
    <h2>Who is the video about?</h2>
    <div class="bar">
      <label class="muted">At least
        <input id="minReady" type="number" value="10" min="1" max="9999">
        processed photos</label>
      <button id="reload">Reload</button>
      <span id="peopleCount" class="muted"></span>
    </div>
    <p class="muted">Immich usually splits <b>one person</b> into <b>several
      clusters</b> across different ages. Select every cluster belonging to the
      same person, then use "Find clusters of the same person" to widen the net
      step by step.</p>
    <div id="people" class="grid people"></div>

    <div id="simBox" class="hide">
      <div class="bar">
        <h3 id="simHead">Clusters that look like the same person</h3>
        <button id="pickAllSug">Select all suggestions</button>
        <button id="hideSug">Close</button>
      </div>
      <div id="simNote" class="muted"></div>
      <div class="warn">Close relatives still score 43–45%, so do not trust the
        number on its own — look at the photos before selecting. Clusters flagged
        <b>already named as someone else</b> are almost certainly a different
        person and have been pushed to the bottom.</div>
      <div id="simList" class="grid people"></div>
    </div>

    <!-- Fixed to the viewport, so it must stay INSIDE this screen: outside it,
         .screen{display:none} cannot hide it and it floats over every screen. -->
    <div id="pickBar" class="pickbar hide">
      <div id="subjRow" class="subjrow hide">
        <span class="muted">Video of:</span>
        <span id="subjChips"></span>
        <label class="muted chk"><input type="checkbox" id="together">
          only photos containing everyone</label>
      </div>
      <div class="pbmain">
        <div class="pi">
          <div id="pickInfo"></div>
          <div id="pickNames" class="muted"></div>
        </div>
        <label class="muted">From <input type="date" id="dFrom"></label>
        <label class="muted">To <input type="date" id="dTo"></label>
        <label class="muted adv">Similarity ≥ <output id="o_minSim"></output>
          <input type="range" id="minSim" min="0.15" max="0.8" step="0.05"
                 value="0.3"></label>
        <button id="findSim">Find clusters of the same person</button>
        <button id="addSubject">+ Add another person</button>
        <button id="clearPick">Clear selection</button>
        <button id="mkAdvanced" class="adv">Tune step by step →</button>
        <button id="mkVideo" class="primary">Create video</button>
      </div>
    </div>`,
  ready: () => {
    $('reload').onclick = loadPeople;
    $('findSim').onclick = findSimilar;
    $('mkVideo').onclick = makeVideo;
    $('mkAdvanced').onclick = openAdvanced;
    $('pickAllSug').onclick = pickAllSuggestions;
    $('hideSug').onclick = () => $('simBox').classList.add('hide');
    $('addSubject').onclick = addSubject;
    $('clearPick').onclick = () => {
      S.picked.clear(); S.subjects = []; syncPicked();
    };
    $('minSim').oninput = () => {
      $('o_minSim').textContent = $('minSim').value;
    };
    $('minSim').oninput();
    loadPeople();
  },
  mount: syncPicked,
});

export async function loadPeople() {
  if (!$('people')) return;
  $('people').innerHTML = '<p class="muted">loading…</p>';
  try {
    const d = await api(`/people?min_ready=${Number($('minReady').value || 10)}`);
    $('peopleCount').textContent = `${d.people.length} people`;
    $('people').innerHTML = '';
    if (!d.people.length) {
      $('people').innerHTML = `<p class="muted">No people yet. Has Immich finished
        Facial Recognition, and has the indexer job finished?</p>`;
      return;
    }
    S.people = d.people;
    d.people.forEach((p) => $('people').appendChild(personNode(p)));
    syncPicked();
  } catch (e) {
    $('people').innerHTML = `<div class="err">${esc(e.message)}</div>`;
  }
}

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
    + `<div class="nm">${esc(p.name || '(unnamed)')}</div>`
    + `<div class="mt">${p.n_ready} photos · ${p.n_month} months · ${years}</div>`
    + (p.name_conflict
      ? '<div class="clashw">already named as someone else</div>' : '');
  n.onclick = () => togglePick(p);
  return n;
}

function togglePick(p) {
  if (S.picked.has(p.person_id)) S.picked.delete(p.person_id);
  else S.picked.set(p.person_id, p);
  syncPicked();
}

export function syncPicked() {
  if (!$('people')) return;
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
  const first = list.map((p) => p.first_seen).filter(Boolean).sort();
  const last = list.map((p) => p.last_seen).filter(Boolean).sort();
  const span = first.length && last.length
    ? ` · ${first[0].slice(0, 4)}–${last[last.length - 1].slice(0, 4)}` : '';
  $('pickInfo').innerHTML = `<b>${n}</b> clusters · ${num(ready)} photos${span}`;
  $('pickNames').textContent = list.map((p) => p.name || '(unnamed)').join(', ');
}

// "A video of Mr A with Mrs B": each person is their own group of clusters. That
// cannot be inferred from one jumbled pile, so each person is committed in turn.
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
      `${esc(s.name)} <em>${s.ids.length} clusters</em> ×`);
    b.type = 'button';
    b.onclick = () => { S.subjects.splice(i, 1); syncPicked(); };
    $('subjChips').appendChild(b);
  });
  $('together').parentElement.classList.toggle('hide',
    S.subjects.length + (S.picked.size ? 1 : 0) < 2);
}

// The people sent up: those already committed, plus the clusters still
// half-selected counted as one more person. With nobody committed, all the
// selected clusters are one person.
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
  return names.filter((x) => x && x !== '(unnamed)').join(' & ');
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
      + `${Number($('minSim').value || 0.3)}`
      + `&seeds=${encodeURIComponent(seeds.join(','))}`);
    S.sug = d.similar || [];
    if (!S.sug.length) {
      $('simHead').textContent = 'No cluster was similar enough';
      $('simList').innerHTML = '';
      $('simNote').textContent = 'Lower the "Similarity ≥" threshold and search '
        + `again to widen the net. Compared against ${num(d.n_cluster)} clusters.`;
      return;
    }
    // Auto-tick the ones that are certain enough, so they need no clicking.
    const auto = S.sug.filter((p) => !p.name_conflict && p.similarity >= AUTO_SIM);
    auto.forEach((p) => S.picked.set(p.person_id, p));

    $('simHead').textContent =
      `${S.sug.length} clusters look like the same person`;
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
      + ' Select more, then press "Find clusters of the same person" again to keep '
      + 'widening the net.';
    $('simBox').scrollIntoView({behavior: 'smooth', block: 'start'});
    toast(auto.length
      ? `Auto-selected ${auto.length}/${S.sug.length} suggested clusters`
      : `Found ${S.sug.length} suggested clusters, none selected automatically`);
  } catch (e) {
    $('simList').innerHTML = `<div class="err">${esc(e.message)}</div>`;
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

export function requestBody() {
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
  S.person = {
    name: subjectName(),
    first_seen: list.map((p) => p.first_seen).filter(Boolean).sort()[0],
    last_seen: list.map((p) => p.last_seen).filter(Boolean).sort().pop(),
    n_cluster: list.length + S.subjects.reduce((a, s) => a + s.ids.length, 0),
    n_subject: subjectPayload().length || 1,
  };
}

// DEFAULT PATH: one press, no setup. A single request creates the project, selects
// the photos and starts the render — the server infers every threshold and the
// length itself.
async function makeVideo() {
  if (!S.picked.size && !S.subjects.length) return;
  rememberPerson();
  $('mkVideo').disabled = true;
  go('5');
  $('outHead').innerHTML = '<h2>Selecting photos…</h2>';
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

    // ffmpeg needs at least 2 frames. Below that, do not render blindly and then
    // report a cryptic error — turn on expert mode and go to the thresholds.
    if (!r.render_id) {
      setExpert(true);
      go('3');
      renderResult();
      toast(`Only ${r.n_selected} photos were selected, at least 2 are needed. `
        + 'Loosen the thresholds on the left, then press Apply.', true);
      return;
    }
    S.renderId = r.render_id;
    const {pollRender} = await import('./video.js');
    pollRender();
  } catch (e) {
    $('outHead').innerHTML = '';
    $('renderState').innerHTML = `<div class="err">${esc(e.message)}</div>`;
    toast(e.message, true);
  } finally {
    $('mkVideo').disabled = false;
  }
}

// Expert path: create the project only, then stop at the photos that were picked.
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
    go('2');
  } catch (e) {
    toast(e.message, true);
  } finally {
    $('mkAdvanced').disabled = false;
  }
}
