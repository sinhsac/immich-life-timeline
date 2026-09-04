// Screen registry, navigation, and the left panel.
//
// Two rules that shape everything else:
//
// 1. Every screen stays MOUNTED once built, hidden with a class. Rebuilding on
//    each visit would throw away a filled photo grid and the scroll position,
//    and those are expensive to refill.
// 2. Every sidebar group is built ONCE at boot, no matter which screen is open.
//    readFilterUI() and renderOpts() read controls by id, so the controls have
//    to exist even while you are looking at a different screen. Navigation only
//    changes which groups are open and highlighted.

import {$, el} from './dom.js';
import {S} from './state.js';

const screens = new Map();      // key -> {label, build, mount, sidebar:[keys]}
const groups = [];              // {key, title, screens:[], html, open}

export function screen(key, def) {
  screens.set(String(key), def);
}

export function group(def) {
  groups.push(def);
}

// ------------------------------------------------------------------ sidebar
export function buildSidebar() {
  const body = $('sideBody');
  body.innerHTML = groups.map((g) => `<details class="grp" data-grp="${g.key}"
      data-screens="${(g.screens || []).join(' ')}"${g.open ? ' open' : ''}>
      <summary>${g.title}</summary><div class="in">${g.html}</div></details>`)
    .join('');
}

// Groups for the screen you are on are opened and highlighted; the rest are
// collapsed rather than hidden, so nothing disappears and the panel does not
// become a different panel per screen.
function syncSidebar(key) {
  let n = 0;
  document.querySelectorAll('#sideBody details.grp').forEach((d) => {
    const mine = (d.dataset.screens || '').split(' ').includes(String(key));
    d.classList.toggle('here', mine);
    if (mine) { d.open = true; n += 1; } else { d.open = false; }
  });
  const def = screens.get(String(key)) || {};
  $('sideCtx').textContent = n
    ? `${def.label || ''} — ${n} group${n > 1 ? 's' : ''}`
    : 'No settings for this screen';
  // The footer buttons belong to specific screens: Apply recomputes the photo
  // selection, Render starts an encode. Showing both everywhere invites pressing
  // the wrong one.
  $('apply').classList.toggle('hide', String(key) !== '3');
  $('resetF').classList.toggle('hide', String(key) !== '3');
  $('render').classList.toggle('hide', String(key) !== '4');
}

// ---------------------------------------------------------------- navigate
export function go(key) {
  key = String(key);
  const def = screens.get(key);
  if (!def) return;

  // Build on first visit only.
  let node = $('scr-' + key);
  if (!node) {
    node = el('section', 'screen');
    node.id = 'scr-' + key;
    node.innerHTML = def.build ? def.build() : '';
    $('screens').appendChild(node);
    if (def.ready) def.ready(node);
  }

  document.querySelectorAll('#screens .screen')
    .forEach((s) => s.classList.remove('on'));
  node.classList.add('on');

  document.querySelectorAll('#topbar #steps button').forEach((b) => {
    const mine = b.dataset.step === key || b.dataset.nav === key;
    b.classList.toggle('on', mine);
  });

  S.view = key;
  syncSidebar(key);
  closeDrawer();
  window.scrollTo(0, 0);
  if (def.mount) def.mount(node);
}

// Steps 2/3/4/5 only become reachable once a project exists. Previously the
// condition was "step <= n", so opening the video screen also unlocked the
// tuning screens even though there was nothing in them yet.
export function unlockSteps() {
  document.querySelectorAll('#steps button[data-step]').forEach((b) => {
    if (b.dataset.step !== '1') b.disabled = !S.projectId;
  });
}

export function setExpert(on) {
  document.body.classList.toggle('expert', !!on);
  $('expert').checked = !!on;
  localStorage.setItem('tl_expert', on ? '1' : '0');
  if (!on) closeDrawer();
}

// ------------------------------------------------------------------ drawer
export function toggleDrawer() {
  document.body.classList.toggle('sideOpen');
}

export function closeDrawer() {
  document.body.classList.remove('sideOpen');
}

export function initShell() {
  document.querySelectorAll('#steps button[data-step]').forEach((b) => {
    b.onclick = () => { if (!b.disabled) go(b.dataset.step); };
  });
  $('navMusic').dataset.nav = 'music';
  $('navStats').dataset.nav = 'stats';
  $('navMusic').onclick = () => go('music');
  $('navStats').onclick = () => go('stats');
  $('sideToggle').onclick = toggleDrawer;
  $('scrim').onclick = closeDrawer;
  $('expert').onchange = () => setExpert($('expert').checked);
  setExpert(localStorage.getItem('tl_expert') === '1');
  // Escape closes the drawer: on a phone it covers the content, and hunting for
  // the exact edge of the scrim is worse than a key.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDrawer();
  });
}
