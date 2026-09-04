// Every call to the service goes through here, so the token is handled in one
// place rather than at each call site.

// Taken from ?token=... once, then remembered: the page still opens after a
// reload when the service has auth switched on.
export const TOKEN = new URLSearchParams(location.search).get('token')
  || localStorage.getItem('tl_token') || '';
if (TOKEN) localStorage.setItem('tl_token', TOKEN);

export async function api(path, opts = {}) {
  const h = Object.assign({'Content-Type': 'application/json'},
    opts.headers || {});
  if (TOKEN) h.Authorization = 'Bearer ' + TOKEN;
  const r = await fetch('/api' + path, Object.assign({}, opts, {headers: h}));
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* empty body */ }
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
}

// Multipart upload cannot go through api(): the browser has to set the boundary
// itself, and forcing application/json leaves the server unable to parse a body
// it otherwise understands.
export async function upload(path, form) {
  const h = TOKEN ? {Authorization: 'Bearer ' + TOKEN} : {};
  const r = await fetch('/api' + path, {method: 'POST', headers: h, body: form});
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* empty body */ }
    throw new Error(msg);
  }
  return r.json();
}

// <img>/<audio> cannot send an Authorization header, so these URLs carry the
// token as a query parameter instead.
const tok = (sep) => (TOKEN ? `${sep}token=${TOKEN}` : '');

export const imgUrl = (kind, key, size) => {
  const [a, f] = key.split(':');
  return `/api/${kind}/${a}/${f}?size=${size}` + tok('&');
};

export const alignedUrl = (key, size, o) => {
  const [a, f] = key.split(':');
  const q = new URLSearchParams({
    size, aspect: o.aspect, face_frac: o.face_frac, eye_y: o.eye_y,
    fill: o.fill, level: o.level,
  });
  if (TOKEN) q.set('token', TOKEN);
  return `/api/aligned/${a}/${f}?${q}`;
};

// Path segments are encoded one at a time: a track may sit in a subdirectory and
// the slashes have to survive.
export const encPath = (name) =>
  name.split('/').map(encodeURIComponent).join('/');

export const musicUrl = (name) => `/api/music/file/${encPath(name)}` + tok('?');

export const videoUrl = (renderId) =>
  `/api/renders/${renderId}/video` + tok('?');
