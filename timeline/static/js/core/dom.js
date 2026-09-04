// Small DOM helpers. No framework: the whole app is readable top to bottom, and
// the container ships without a build step, which matters for a tool you edit on
// the host and reload.

export const $ = (id) => document.getElementById(id);

export const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};

export const num = (n) => (n || 0).toLocaleString('en-US');

export const mmss = (sec) => {
  if (sec == null) return '';
  const s = Math.round(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

// Anything that reaches HTML from the database or from a filename goes through
// this. Track names in particular also end up on an ffmpeg command line: the
// server sanitises its side, this escapes ours.
export const esc = (v) => String(v).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

export function toast(msg, bad) {
  const n = el('div', bad ? 'bad' : '', esc(msg));
  $('toast').appendChild(n);
  setTimeout(() => n.remove(), bad ? 9000 : 4000);
}

// Debounce keyed by name, so a search box and a storyboard request cannot cancel
// each other by sharing one timer.
const timers = {};
export function debounce(key, fn, ms) {
  clearTimeout(timers[key]);
  timers[key] = setTimeout(fn, ms);
}

// Wire a range input to the <output id="o_<name>"> beside it. Returns nothing;
// call it once per control at boot.
export function bindRange(id, after) {
  const i = $(id);
  if (!i) return;
  const out = $('o_' + id.replace(/^[rf]_/, ''));
  const show = () => {
    if (out) out.textContent = i.value;
    if (after) after(i.value);
  };
  i.oninput = show;
  show();
}
