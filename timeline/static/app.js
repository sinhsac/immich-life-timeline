'use strict';
// UI bon buoc. Khong dung framework: mot file, doc tu tren xuong duoc.
// Token lay tu ?token=... tren URL roi nho lai, de mo duoc khi service bat auth.

const TOKEN = new URLSearchParams(location.search).get('token')
  || localStorage.getItem('tl_token') || '';
if (TOKEN) localStorage.setItem('tl_token', TOKEN);

const S = {
  person: null, projectId: null, filters: {}, defaults: null,
  result: null, renderId: null, poll: null, postures: [], orients: [],
  statusTimer: null, prevTimer: null,
  picked: new Map(), people: [], sug: [],
};

const num = (n) => (n || 0).toLocaleString('vi-VN');

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
    try { msg = (await r.json()).detail || msg; } catch (e) { /* body rong */ }
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
}

const imgUrl = (kind, key, size) => {
  const [a, f] = key.split(':');
  return `/api/${kind}/${a}/${f}?size=${size}` + (TOKEN ? `&token=${TOKEN}` : '');
};

// Khung hinh cua video: khuon mat chi la diem neo, face_frac quyet dinh lay
// rong hay hep. Preview phai dung dung bo tham so nay moi khong noi doi.
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
  document.querySelectorAll('#steps button').forEach((b) => {
    b.classList.toggle('on', b.dataset.step === String(n));
    if (Number(b.dataset.step) <= n) b.disabled = false;
  });
  window.scrollTo(0, 0);
}

// ================================================================ khoi dong
(async function init() {
  document.querySelectorAll('#steps button').forEach((b) => {
    b.onclick = () => { if (!b.disabled) step(Number(b.dataset.step)); };
  });
  $('reload').onclick = loadPeople;
  $('findSim').onclick = findSimilar;
  $('mkProject').onclick = buildProject;
  $('pickAllSug').onclick = pickAllSuggestions;
  $('hideSug').onclick = () => $('simBox').classList.add('hide');
  $('clearPick').onclick = () => { S.picked.clear(); syncPicked(); };
  $('minSim').oninput = () => { $('o_minSim').textContent = $('minSim').value; };
  $('minSim').oninput();
  $('toStep3').onclick = () => { step(3); renderResult(); };
  $('toStep4').onclick = () => {
    step(4); loadRenders(); estimate(); renderPreview();
  };
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
        ? 'Bấm vào ảnh để bỏ khỏi video.'
        : 'Ảnh gần đạt nằm trên. Nếu thấy nhiều ảnh tốt bị loại, nới ngưỡng tương ứng bên trái.';
    };
  });
  ['r_fps', 'r_eye_y', 'r_face_frac'].forEach((id) => {
    $(id).oninput = () => {
      $('o_' + id.slice(2)).textContent = $(id).value;
      estimate();
      if (id !== 'r_fps') previewSoon();
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
    $('health').innerHTML = `<div class="err">Không gọi được API: ${e.message}</div>`;
  }
})();

function showHealth(h) {
  const bits = [];
  if (!h.indexer.ok) bits.push(`<div class="err">Indexer: ${h.indexer.detail}</div>`);
  if (!h.ffmpeg.ok) bits.push(`<div class="err">ffmpeg: ${h.ffmpeg.detail}</div>`);
  if (!h.auth) bits.push('<div class="warn">Service đang không có xác thực (API_TOKEN trống).</div>');
  if (h.indexer.ok && h.ffmpeg.ok) bits.push(`<span class="muted">${h.indexer.detail}</span>`);
  $('health').innerHTML = bits.join('');
}

// ========================================================= tien do indexer
// Job indexer chay ngoai service nay (CronJob rieng), nen UI chi doc trang thai
// tu cac cot state trong fp_asset. Tu dong tat polling khi da xong.
function showProgress(p) {
  if (!p.ready) { $('progress').innerHTML = ''; return; }

  const bars = p.stages.map((s) => {
    const left = Math.max(0, s.total - s.done);
    return `<div class="prow${p.running === s.name ? ' run' : ''}">`
      + `<span class="plab">${s.label}</span>`
      + `<span class="pbar"><i style="width:${Math.min(100, s.pct)}%"></i></span>`
      + `<span class="pnum">${s.pct}%<em>${num(s.done)}/${num(s.total)}`
      + (left ? ` · còn ${num(left)}` : '') + '</em></span></div>';
  }).join('');

  const bits = [];
  bits.push(p.running
    ? `đang chạy <b>${p.running}</b>`
    : 'không có stage nào đang chạy');
  bits.push(`${num(p.n_face_ready)} face có landmark`);
  if (p.n_body) bits.push(`${num(p.n_body)} thân người`);
  const nerr = (p.face_err || 0) + (p.body_err || 0);
  if (nerr) bits.push(`<span class="bad">${num(nerr)} ảnh lỗi đọc</span>`);

  $('progress').innerHTML =
    `<details class="idx"${progressDone(p) ? '' : ' open'}>`
    + `<summary>Index: ${bits.join(' · ')}</summary>${bars}</details>`;
}

const progressDone = (p) => p.ready && p.stages.every((s) => s.done >= s.total);

async function pollStatus() {
  let wait = 20000;
  try {
    const [h, p] = await Promise.all([api('/health'), api('/progress')]);
    showHealth(h);
    showProgress(p);
    if (progressDone(p)) return;            // xong roi thi thoi, khong poll nua
  } catch (e) {
    wait = 60000;                           // loi thi giãn ra, dung dap lien tuc
  }
  clearTimeout(S.statusTimer);
  S.statusTimer = setTimeout(pollStatus, wait);
}

// ================================================================ buoc 1
async function loadPeople() {
  $('people').innerHTML = '<p class="muted">đang tải…</p>';
  try {
    const d = await api('/people?min_ready=' + Number($('minReady').value || 10));
    $('peopleCount').textContent = `${d.people.length} người`;
    $('people').innerHTML = '';
    if (!d.people.length) {
      $('people').innerHTML = '<p class="muted">Chưa có người nào. Immich đã chạy '
        + 'Facial Recognition và job indexer đã xong chưa?</p>';
      return;
    }
    S.people = d.people;
    d.people.forEach((p) => $('people').appendChild(personNode(p)));
    syncPicked();
  } catch (e) {
    $('people').innerHTML = `<div class="err">${e.message}</div>`;
  }
}

// Nguong tu dong chon. Do tren thu vien thuc: cum cung nguoi ~0.55, con mot
// nguoi KHAC da dat ten dat 0.44 -> 0.5 la vach hop ly, va con loc them
// name_conflict. Cao hon vach nay thi tu tich san, nguoi dung chi viec bo ra.
const AUTO_SIM = 0.5;

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
    + `<div class="nm">${p.name || '(chưa đặt tên)'}</div>`
    + `<div class="mt">${p.n_ready} ảnh · ${p.n_month} tháng · ${years}</div>`
    + (p.name_conflict ? '<div class="clashw">đã có tên khác</div>' : '');
  n.onclick = () => togglePick(p);
  return n;
}

// Immich tach mot nguoi thanh nhieu cluster theo do tuoi, nen o day chon
// duoc nhieu cluster. S.picked la Map person_id -> thong tin cluster.
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
  $('pickBar').classList.toggle('hide', n === 0);
  if (!n) return;

  const ready = list.reduce((a, p) => a + (p.n_ready || 0), 0);
  const years = list.map((p) => p.first_seen).filter(Boolean).sort();
  const last = list.map((p) => p.last_seen).filter(Boolean).sort();
  const span = years.length && last.length
    ? ` · ${years[0].slice(0, 4)}–${last[last.length - 1].slice(0, 4)}` : '';
  $('pickInfo').innerHTML = `<b>${n}</b> cụm · ${num(ready)} ảnh${span}`;
  $('pickNames').textContent = list
    .map((p) => p.name || '(chưa tên)').join(', ');
}

async function findSimilar() {
  if (!S.picked.size) return;
  const seeds = [...S.picked.keys()];
  $('findSim').disabled = true;
  $('simList').innerHTML = '<p class="muted">đang so khớp embedding…</p>';
  $('simNote').textContent = '';
  $('simBox').classList.remove('hide');
  try {
    const d = await api(`/people/${seeds[0]}/similar?min_sim=`
      + Number($('minSim').value || 0.3)
      + '&seeds=' + encodeURIComponent(seeds.join(',')));
    S.sug = d.similar || [];
    if (!S.sug.length) {
      $('simHead').textContent = 'Không thấy cụm nào đủ giống';
      $('simList').innerHTML = '';
      $('simNote').textContent = 'Hạ ngưỡng "Giống nhau ≥" xuống rồi tìm lại '
        + `nếu muốn rộng hơn. Đã so với ${num(d.n_cluster)} cụm.`;
      return;
    }

    // Tu chon nhung cum du chac, de khong phai bam tung cai.
    const auto = S.sug.filter((p) => !p.name_conflict && p.similarity >= AUTO_SIM);
    auto.forEach((p) => S.picked.set(p.person_id, p));

    $('simHead').textContent = `${S.sug.length} cụm có vẻ cùng người này`;
    $('simList').innerHTML = '';
    S.sug.forEach((p) => $('simList').appendChild(personNode(p, p.similarity, true)));
    syncPicked();

    const pct = Math.round(AUTO_SIM * 100);
    $('simNote').innerHTML = `Đã so với ${num(d.n_cluster)} cụm. `
      + (auto.length
        ? `<b>Đã tự tích ${auto.length} cụm</b> đạt từ ${pct}% trở lên và không `
          + 'trùng tên khác — bỏ tích nếu thấy sai.'
        : `Không cụm nào đạt ${pct}% để tự tích, bạn tự chọn theo ảnh.`)
      + ' Chọn thêm rồi bấm "Tìm cụm cùng người" lần nữa để lan tiếp.';
    $('simBox').scrollIntoView({behavior: 'smooth', block: 'start'});
    toast(auto.length
      ? `Tự chọn ${auto.length}/${S.sug.length} cụm gợi ý`
      : `Thấy ${S.sug.length} cụm gợi ý, chưa tự chọn cụm nào`);
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
  toast(`Đã chọn ${ok.length} cụm gợi ý (bỏ qua cụm đã có tên khác)`);
}

async function buildProject() {
  const list = [...S.picked.values()];
  if (!list.length) return;
  S.person = {
    name: list.map((p) => p.name).filter(Boolean)[0] || '',
    first_seen: list.map((p) => p.first_seen).filter(Boolean).sort()[0],
    last_seen: list.map((p) => p.last_seen).filter(Boolean).sort().pop(),
    n_cluster: list.length,
  };
  $('mkProject').disabled = true;
  toast(`Đang lấy ảnh từ ${list.length} cụm…`);
  try {
    const r = await api('/projects', {
      method: 'POST',
      body: JSON.stringify({
        person_ids: list.map((p) => p.person_id),
        name: S.person.name || null,
      }),
    });
    S.projectId = r.project_id;
    S.result = r;
    S.filters = r.filters;
    setFilterUI(r.filters);
    showStep2(r);
    step(2);
  } catch (e) {
    toast(e.message, true);
  } finally {
    $('mkProject').disabled = false;
  }
}

// ================================================================ buoc 2
function showStep2(r) {
  const p = S.person;
  $('projHead').innerHTML = `<h3>${p.name || '(chưa đặt tên)'}</h3>`
    + `<p class="muted">${p.first_seen ? p.first_seen.slice(0, 10) : '?'} → `
    + `${p.last_seen ? p.last_seen.slice(0, 10) : '?'}`
    + (p.n_cluster > 1 ? ` · gộp ${p.n_cluster} cụm` : '')
    + ` · mỗi ${r.filters.bucket_days} ngày lấy ${r.filters.per_bucket} ảnh</p>`;
  $('n_candidate').textContent = r.n_candidate;
  $('n_pass').textContent = r.n_pass;
  $('n_selected').textContent = r.n_selected;
  $('n_years').textContent = r.timeline.length;
  drawChart(r.timeline);
  $('gaps').innerHTML = r.gaps.length
    ? '<div class="warn">Khoảng trống dài: '
      + r.gaps.slice(0, 5).map((g) => `${g.from.slice(0, 7)} → ${g.to.slice(0, 7)} (${g.days} ngày)`).join(', ')
      + '. Video sẽ nhảy ở những chỗ này.</div>'
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

// ================================================================ buoc 3
const RANGE_KEYS = ['max_yaw', 'max_pitch', 'max_roll', 'min_frontality',
  'min_ear', 'min_eye_ratio', 'min_sharp', 'bucket_days', 'per_bucket'];

function setFilterUI(f) {
  RANGE_KEYS.forEach((k) => {
    const i = $('f_' + k);
    if (!i) return;
    i.value = f[k];
    const o = $('o_' + k);
    if (o) o.textContent = fmt(k, f[k]);
    i.oninput = () => { if (o) o.textContent = fmt(k, i.value); };
  });
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
  $('applying').textContent = 'đang tính lại…';
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
  $('reasons').innerHTML = Object.keys(r.reasons).length
    ? '<table><tr><th>Lý do loại</th><th>Số ảnh</th></tr>'
      + Object.entries(r.reasons).map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('')
      + '</table>'
    : '<p class="muted">Không ảnh nào bị loại.</p>';

  const gs = $('gridSel');
  gs.innerHTML = '';
  r.selected.forEach((f) => gs.appendChild(frameNode(f, true)));
  const gr = $('gridRej');
  gr.innerHTML = '';
  (r.rejected || []).forEach((f) => gr.appendChild(frameNode(f, false)));
  if (!$('tabHint').textContent) $('tabHint').textContent = 'Bấm vào ảnh để bỏ khỏi video.';
}

function frameNode(f, selected) {
  const n = el('div', 'frame ' + (selected ? 'sel' : 'rej'));
  const dt = (f.taken_at || '').slice(0, 10);
  n.innerHTML = `<img loading="lazy" src="${imgUrl('thumb', f.key, 104)}" alt="">`
    + (selected ? '' : `<div class="why">${f.reason || ''}</div>`)
    + `<div class="dt">${dt}</div>`;
  n.title = [`${dt}`, `yaw ${f.yaw}° pitch ${f.pitch}° roll ${f.roll}°`,
    `chính diện ${f.frontality}`, `nét ${f.sharp}`,
    `${f.n_face} mặt trong ảnh`,
    f.posture ? `tư thế ${f.posture}/${f.orientation}` : 'không có body pose',
    f.reason ? `LOẠI: ${f.reason}` : ''].filter(Boolean).join('\n');
  if (selected) n.onclick = () => toggle(f, true);
  else if (f.reason === 'bo tay') n.onclick = () => toggle(f, false);
  return n;
}

async function toggle(f, exclude) {
  const [asset_id, fidx] = f.key.split(':');
  try {
    const r = await api(`/projects/${S.projectId}/exclude`, {
      method: 'POST',
      body: JSON.stringify({asset_id, fidx: Number(fidx), excluded: exclude}),
    });
    // giu lai danh sach rejected cu de khong mat tab dang xem
    S.result = Object.assign({}, r, {rejected: S.result.rejected});
    renderResult();
    toast(exclude ? 'Đã bỏ ảnh khỏi video' : 'Đã lấy lại ảnh');
  } catch (e) {
    toast(e.message, true);
  }
}

// ================================================================ buoc 4
// Xem truoc 3 frame cach xa nhau ve thoi gian. Moi anh phai tai qua Immich API
// nen debounce, khong goi theo tung buoc keo thanh truot.
function renderPreview() {
  const sel = (S.result && S.result.selected) || [];
  const box = $('framePreview');
  if (!sel.length) {
    box.innerHTML = '<p class="muted">chưa có ảnh nào được chọn</p>';
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

function estimate() {
  const n = S.result ? S.result.n_selected : 0;
  const fps = Number($('r_fps').value);
  $('renderEst').textContent = n
    ? `${n} ảnh ở ${fps} ảnh/giây → video khoảng ${(n / fps).toFixed(1)} giây`
    : '';
}

async function startRender() {
  $('render').disabled = true;
  try {
    const r = await api(`/projects/${S.projectId}/render`, {
      method: 'POST',
      body: JSON.stringify({options: Object.assign(framingOpts(), {
        fps: Number($('r_fps').value),
        smooth: $('r_smooth').value,
        label: $('r_label').value,
      })}),
    });
    S.renderId = r.render_id;
    $('player').classList.add('hide');
    pollRender();
  } catch (e) {
    toast(e.message, true);
    $('render').disabled = false;
  }
}

function pollRender() {
  clearInterval(S.poll);
  const tick = async () => {
    try {
      const r = await api('/renders/' + S.renderId);
      const label = {queued: 'đang chờ', frames: 'đang align ảnh',
        encoding: 'ffmpeg đang encode', done: 'xong', error: 'lỗi'}[r.status] || r.status;
      $('renderState').innerHTML = r.status === 'error'
        ? `<div class="err">Lỗi: ${r.err}</div>`
        : `<p><b>${label}</b> — ${r.n_done}/${r.n_total} frame</p>`
          + `<progress max="100" value="${r.pct}"></progress>`;
      if (r.status === 'done') {
        clearInterval(S.poll);
        $('render').disabled = false;
        const url = `/api/renders/${S.renderId}/video` + (TOKEN ? `?token=${TOKEN}` : '');
        $('player').src = url;
        $('player').classList.remove('hide');
        $('renderState').innerHTML = `<p><b>Xong</b> — ${r.n_done} frame, `
          + `${r.duration_s ? r.duration_s.toFixed(1) : '?'} giây. `
          + `<a href="${url}" download>Tải mp4</a></p>`;
        loadRenders();
        toast('Video đã dựng xong');
      } else if (r.status === 'error') {
        clearInterval(S.poll);
        $('render').disabled = false;
      }
    } catch (e) {
      clearInterval(S.poll);
      $('render').disabled = false;
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
      ? '<table><tr><th>#</th><th>Trạng thái</th><th>Frame</th><th>Dài</th><th></th></tr>'
        + d.renders.map((r) => `<tr><td>${r.id}</td><td>${r.status}</td>`
          + `<td>${r.n_done}/${r.n_total}</td>`
          + `<td>${r.duration_s ? r.duration_s.toFixed(1) + 's' : '—'}</td>`
          + `<td>${r.status === 'done'
            ? `<a href="/api/renders/${r.id}/video${TOKEN ? '?token=' + TOKEN : ''}" download>tải</a>`
            : (r.err || '')}</td></tr>`).join('')
        + '</table>'
      : '<p class="muted">Chưa có lần dựng nào.</p>';
  } catch (e) { /* khong quan trong */ }
}
