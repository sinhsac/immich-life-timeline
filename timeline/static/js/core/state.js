// One shared state object. Screens read and write it directly rather than
// passing it around: with seven screens and a persistent sidebar, threading a
// context through every call buys nothing here.

export const S = {
  // project + selection
  person: null, projectId: null, filters: {}, defaults: null, result: null,
  picked: new Map(), people: [], sug: [],
  // Each element is ONE PERSON: {name, ids:[cluster...]}. Empty means all the
  // currently selected clusters belong to the same person, the common case.
  subjects: [],
  // render
  renderId: null, sb: null, out: null,
  // background reads
  progress: null, music: null, musicMeta: null,
  // current screen
  view: 1,
  // timers that must be cancellable from outside the screen that started them
  poll: null, statusTimer: null,
};

// Music browser query, kept out of S because the music screen owns it entirely
// and it is the only thing that survives navigating away and back.
export const MQ = {q: '', offset: 0, limit: 40, sort: 'name', folder: ''};

// The reason string the server writes when a photo is dropped by hand. Must match
// select.MANUAL_REASON: only those rejects can be put back by clicking them.
export const MANUAL_REASON = 'dropped by hand';

// Auto-select threshold for cluster suggestions. Measured on a real library:
// clusters of the same person score ~0.55 while a DIFFERENT person who already
// had a name reached 0.44, so 0.5 is a sensible line and name_conflict filters
// on top of it.
export const AUTO_SIM = 0.5;
