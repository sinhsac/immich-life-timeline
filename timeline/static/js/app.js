// Boot. Registration happens as a side effect of importing each screen, so the
// order of these imports is the order of the sidebar groups and nothing else
// decides it.

import {$, esc} from './core/dom.js';
import {api} from './core/api.js';
import {S} from './core/state.js';
import {buildSidebar, go, initShell} from './core/router.js';

import './screens/people.js';
import './screens/photos.js';
import {initFilters, setFilterUI} from './screens/thresholds.js';
import {initRender, storyboardSoon} from './screens/render.js';
import './screens/video.js';
import {renderPick, syncMusic} from './screens/music.js';
import {pollStatus} from './screens/stats.js';

(async function boot() {
  initShell();
  // The sidebar has to exist before any init* runs: they wire controls by id, and
  // the controls live in the groups.
  buildSidebar();
  initFilters();
  initRender();

  // The music screen owns the selected track and announces changes rather than
  // reaching into the render screen. Both sides stay importable on their own.
  document.addEventListener('music:changed', () => {
    renderPick();
    syncMusic();
    storyboardSoon();
  });

  go('1');

  try {
    S.defaults = await api('/defaults');
    setFilterUI(S.defaults.filters);
  } catch (e) {
    $('health').innerHTML =
      `<div class="err">Cannot reach the API: ${esc(e.message)}</div>`;
    return;
  }
  pollStatus();
}());
