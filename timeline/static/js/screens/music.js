// Music library — its own screen, like Statistics.
//
// Sized for a library rather than a handful of files: the server pages and
// filters, and the browser never holds the whole list. At a thousand tracks the
// question stops being "which of these" and becomes "how do I narrow this down,
// and how do I hear it".
//
// This module never imports the render screen. It announces changes with a
// 'music:changed' event instead, which keeps the two from importing each other
// just to say "the selected track moved".

import {$, debounce, esc, mmss, num, toast} from '../core/dom.js';
import {api, encPath, musicUrl, upload} from '../core/api.js';
import {MQ, S} from '../core/state.js';
import {screen} from '../core/router.js';

const changed = () => document.dispatchEvent(new CustomEvent('music:changed'));

screen('music', {
  label: 'Music',
  build: () => `
    <h2>Background music</h2>
    <p class="muted">Tracks are read from the <code>MUSIC_DIR</code> directory on
      the server. Subdirectories become a folder filter, so copying in
      <code>slow/</code> and <code>upbeat/</code> gives you categories for free.
      Pick one here, then tune the level and beat sync on the Render screen.</p>
    <div id="musicNote"></div>
    <div class="mrow">
      <input type="search" id="musicQ" placeholder="Search tracks — accents optional"
             autocomplete="off">
      <select id="musicFolder"><option value="">All folders</option></select>
      <select id="musicSort">
        <option value="name" selected>By name</option>
        <option value="newest">Newest first</option>
        <option value="longest">Longest first</option>
        <option value="shortest">Shortest first</option>
        <option value="largest">Largest first</option>
      </select>
      <button id="musicRandom" type="button">Random</button>
    </div>
    <audio id="musicAudio" controls preload="none"></audio>
    <div id="musicResults"></div>
    <div id="musicPager" class="mrow"></div>

    <h3>Add tracks</h3>
    <div class="drop" id="musicDrop">
      <b>Drop audio files here</b>
      <p class="muted">Or choose them below. Several at once is fine, and each file
        reports its own result — one rejection does not hide the rest.</p>
      <input type="file" id="r_musicFile" accept="audio/*" multiple>
      <div class="upl">
        <button id="musicUpload" type="button">Upload</button>
      </div>
    </div>
    <div id="musicUpState" class="muted"></div>
    <div id="musicUsage" class="cards"></div>
    <p class="muted">For a whole collection, mounting it read-only or copying it in
      with <code>rsync</code> beats uploading gigabytes through a browser one file
      at a time. Upload is for "I found one track, let me try it".</p>`,
  ready: () => {
    $('musicRandom').onclick = randomMusic;
    $('musicQ').oninput = () => {
      MQ.q = $('musicQ').value;
      debounce('mq', () => { MQ.offset = 0; loadMusic(); }, 250);
    };
    $('musicSort').onchange = () => {
      MQ.sort = $('musicSort').value; MQ.offset = 0; loadMusic();
    };
    $('musicFolder').onchange = () => {
      MQ.folder = $('musicFolder').value; MQ.offset = 0; loadMusic();
    };
    $('musicUpload').onclick = () => uploadFiles([...$('r_musicFile').files]);
    $('r_musicFile').onchange = () => { $('musicUpState').textContent = ''; };

    // Drag and drop, because dropping a folder of tracks in is most of the point
    // of giving music its own screen.
    const dz = $('musicDrop');
    ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => {
      e.preventDefault(); dz.classList.add('over');
    }));
    ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => {
      e.preventDefault(); dz.classList.remove('over');
    }));
    dz.addEventListener('drop', (e) => {
      const files = [...(e.dataTransfer?.files || [])];
      if (files.length) uploadFiles(files);
    });
  },
  mount: loadMusic,
});

// ------------------------------------------------------------------- listing
export async function loadMusic() {
  if (!$('musicResults')) return;
  const p = new URLSearchParams({
    q: MQ.q, offset: MQ.offset, limit: MQ.limit, sort: MQ.sort,
  });
  if (MQ.folder) p.set('folder', MQ.folder);
  let d = null;
  try {
    d = await api(`/music?${p}`);
  } catch (e) {
    d = null;
  }
  showMusic(d);
}

// Shared by the page load, the upload response and the delete response: all three
// return the same shape, so the view is never rebuilt from a guess about what
// changed.
export function showMusic(d) {
  S.music = d;
  if (!$('musicResults')) return;
  const conf = !!(d && d.configured);
  const list = (d && d.music) ? d.music : [];
  const u = (d && d.usage) || {};
  const total = (d && d.total) || 0;

  $('musicNote').innerHTML = !d
    ? '<div class="warn">Could not read the track list.</div>'
    : (!conf
      ? `<div class="warn"><code>MUSIC_DIR</code> is not set on the server, so
         there is nothing to choose from and nothing can be uploaded.</div>`
      : (u.n ? '' : `<p class="muted">No track yet. Drop a few in below, or copy
         files into <code>MUSIC_DIR</code> on the server.</p>`));

  ['r_musicFile', 'musicUpload', 'musicQ', 'musicRandom', 'musicSort',
    'musicFolder'].forEach((id) => { $(id).disabled = !conf; });

  // Rebuild the folder list from the response rather than keeping a second source
  // of truth; it only changes when files change.
  const folders = (d && d.folders) || [];
  const fsel = $('musicFolder');
  if (fsel.dataset.sig !== folders.join('|')) {
    fsel.dataset.sig = folders.join('|');
    fsel.innerHTML = '<option value="">All folders</option>'
      + folders.map((f) => `<option value="${esc(f)}">${esc(f)}</option>`).join('');
    fsel.value = MQ.folder;
  }

  const cur = $('r_music') ? $('r_music').value : '';
  $('musicResults').innerHTML = list.length
    ? list.map((m) => {
      const bits = [mmss(m.duration), m.bpm ? `${m.bpm} BPM` : '',
        `${(m.size / 1048576).toFixed(1)} MB`].filter(Boolean).join(' · ');
      return `<div class="mitem${m.name === cur ? ' on' : ''}"
          data-name="${esc(m.name)}">
          <button type="button" class="play" title="Listen">▶</button>
          <span class="mn">${esc(m.name)}</span>
          <span class="mm">${bits}</span>
          <button type="button" class="del" title="Delete">×</button></div>`;
    }).join('')
    : (conf && u.n ? '<p class="muted">Nothing matches that search.</p>' : '');

  $('musicResults').querySelectorAll('.mitem').forEach((n) => {
    const name = n.dataset.name;
    n.onclick = (ev) => {
      if (ev.target.closest('button')) return;   // ▶ and × handle themselves
      pickMusic(name);
    };
    n.querySelector('button.play').onclick = () => playMusic(name);
    n.querySelector('button.del').onclick = () => deleteMusic(name);
  });

  const from = total ? MQ.offset + 1 : 0;
  const to = Math.min(MQ.offset + MQ.limit, total);
  $('musicPager').innerHTML = total > MQ.limit
    ? `<button type="button" id="mPrev"${MQ.offset ? '' : ' disabled'}>← previous</button>`
      + `<span class="muted">${from}–${to} of ${num(total)}</span>`
      + `<button type="button" id="mNext"${to >= total ? ' disabled' : ''}>next →</button>`
    : (total ? `<span class="muted">${num(total)} of ${num(u.n || 0)} tracks</span>` : '');
  if ($('mPrev')) {
    $('mPrev').onclick = () => {
      MQ.offset = Math.max(0, MQ.offset - MQ.limit); loadMusic();
    };
    $('mNext').onclick = () => { MQ.offset += MQ.limit; loadMusic(); };
  }

  $('musicUsage').innerHTML = u.used_mb == null ? '' : [
    [`${u.n}`, 'tracks'],
    [`${u.used_mb} MB`, `of ${u.total_mb} MB allowed`],
    [`${u.free_mb} MB`, `free on disk · keep ${u.min_free_mb}`],
    [`${u.max_file_mb} MB`, 'per file'],
  ].map(([b, s]) => `<div class="card"><b>${b}</b><span>${s}</span></div>`).join('');

  renderPick();
  syncMusic();
}

// ------------------------------------------------------------------ selection
export async function pickMusic(name) {
  const hidden = $('r_music');
  if (!hidden) return;
  hidden.value = name || '';
  S.musicMeta = null;
  renderPick();
  syncMusic();
  changed();
  if (!name) { showMusic(S.music); return; }
  // Highlight moves at once; the metadata request is what takes time.
  document.querySelectorAll('#musicResults .mitem').forEach((n) => {
    n.classList.toggle('on', n.dataset.name === name);
  });
  try {
    // bpm=1 only here: beat detection decodes two minutes and runs an FFT, so it
    // runs for the track actually chosen, never for a page of forty.
    S.musicMeta = await api(`/music/meta/${encPath(name)}?bpm=1`);
    renderPick();
    changed();
  } catch (e) { /* metadata is a nicety, not worth a toast */ }
}

// The chosen track, shown in the sidebar. With a searchable list you scroll away
// from your choice, so it has to be visible without hunting for the highlight.
export function renderPick() {
  const box = $('musicPick');
  if (!box) return;
  const name = $('r_music') ? $('r_music').value : '';
  if (!name) {
    box.innerHTML = '<span class="muted">No track — the video will be silent '
      + 'apart from any clip audio.</span>';
    return;
  }
  const m = S.musicMeta || {};
  const bits = [mmss(m.duration), m.bpm ? `${m.bpm} BPM` : ''].filter(Boolean);
  box.innerHTML = `<b>${esc(name)}</b>`
    + (bits.length ? ` <span class="muted">${bits.join(' · ')}</span>` : '')
    + ' <button type="button" id="mPlaySel" title="Listen">▶</button>'
    + ' <button type="button" id="mClear">clear</button>'
    + hintFor(m);
  $('mPlaySel').onclick = () => playMusic(name);
  $('mClear').onclick = () => pickMusic('');
}

// Whether the tempo suits the pacing is the one thing that actually matters for
// beat sync, and it is not obvious from a BPM number alone.
function hintFor(m) {
  const bs = $('r_beat_sync');
  if (!m || !m.bpm || !bs || !bs.checked) return '';
  const every = Number($('r_beat_every').value) || 1;
  const unit = every * 60 / m.bpm;
  const beat = (S.result && S.result.story && S.result.story.hold_beat) || 1.0;
  const close = Math.abs(unit - beat) / beat <= 0.3;
  const better = unit > beat ? Math.max(1, every - 1) : every + 1;
  // Built in one piece rather than opening the tag in a shared prefix and closing
  // it in each branch: markup that only balances once you pick a branch is markup
  // nobody can check by reading.
  const tail = close
    ? ' — a good match.'
    : `. Try <b>cut every ${better}</b> to get closer.`;
  return `<p class="muted">Cut unit ${unit.toFixed(2)}s against a ${beat}s `
    + `supporting shot${tail}</p>`;
}

// Beat sync has nothing to snap to without a track, and the server enforces that
// too. Grey it out rather than let someone tick a box that quietly does nothing.
export function syncMusic() {
  const bs = $('r_beat_sync');
  if (!bs) return;
  const has = !!($('r_music') && $('r_music').value);
  bs.disabled = !has;
  if (!has) bs.checked = false;
  bs.parentElement.classList.toggle('off', !has);
  $('r_beat_every').parentElement.classList.toggle('off', !bs.checked);
}

function playMusic(name) {
  const a = $('musicAudio');
  if (!a) return;
  if (a.dataset.name === name && !a.paused) { a.pause(); return; }
  a.dataset.name = name;
  a.src = musicUrl(name);
  a.play().catch(() => toast('Could not play that file.', true));
}

async function randomMusic() {
  const p = new URLSearchParams({q: MQ.q});
  if (MQ.folder) p.set('folder', MQ.folder);
  try {
    const d = await api(`/music/random?${p}`);
    await pickMusic(d.pick.name);
    toast(`Picked ${d.pick.name} out of ${d.of}`);
  } catch (e) {
    toast(e.message, true);
  }
}

// -------------------------------------------------------------------- write
async function uploadFiles(files) {
  if (!files.length) { toast('Pick one or more audio files first.', true); return; }
  const fd = new FormData();
  files.forEach((f) => fd.append('files', f, f.name));
  const mb = files.reduce((a, f) => a + f.size, 0) / 1048576;
  $('musicUpload').disabled = true;
  $('musicUpState').textContent =
    `uploading ${files.length} file(s), ${mb.toFixed(1)} MB…`;
  try {
    const d = await upload('/music', fd);
    MQ.q = ''; MQ.offset = 0; MQ.sort = 'newest';
    $('musicQ').value = ''; $('musicSort').value = 'newest';
    showMusic(d);
    $('r_musicFile').value = '';
    $('musicUpState').innerHTML = (d.failed || []).length
      ? `<span class="err">${d.failed.length} rejected: `
        + d.failed.map((f) => `${esc(f.filename)} — ${esc(f.error)}`).join('; ')
        + '</span>'
      : '';
    if ((d.uploaded || []).length === 1) await pickMusic(d.uploaded[0].name);
    toast(`Uploaded ${(d.uploaded || []).length} file(s)`
      + ((d.failed || []).length ? `, ${d.failed.length} rejected` : ''));
  } catch (e) {
    $('musicUpState').innerHTML = `<span class="err">${esc(e.message)}</span>`;
    toast(e.message, true);
  } finally {
    $('musicUpload').disabled = false;
  }
}

async function deleteMusic(name) {
  if (!confirm(`Delete ${name}?`)) return;
  try {
    const d = await api(`/music/${encPath(name)}`, {method: 'DELETE'});
    // Deleting the selected track has to clear the selection, or the render
    // quietly comes out with no music and nothing says why.
    if ($('r_music') && $('r_music').value === name) {
      $('r_music').value = '';
      S.musicMeta = null;
      changed();
    }
    showMusic(d);
    toast(`Deleted ${name}`);
  } catch (e) {
    toast(e.message, true);
  }
}
