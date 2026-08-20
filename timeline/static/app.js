'use strict';
// UI bon buoc. Khong dung framework: mot file, doc tu tren xuong duoc.
// Token lay tu ?token=... tren URL roi nho lai, de mo duoc khi service bat auth.

const TOKEN = new URLSearchParams(location.search).get('token')
  || localStorage.getItem('tl_token') || '';
if (TOKEN) localStorage.setItem('tl_token', TOKEN);

const S = {
  person: null, projectId: null, filters: {}, defaults: null,
  result: null, renderId: null, poll: null, postures: [], orients: [],
  statusTimer: null, prevTimer: null, sbTimer: null, sb: null, out: null,
  picked: new Map(), people: [], sug: [], view: 1,
  // Moi phan tu la MOT NGUOI: {name, ids:[cluster...]}. Rong = tat ca cum dang
  // chon thuoc cung mot nguoi (truong hop pho bien nhat).
  subjects: [],
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
  $('navStats').classList.remove('on');
  // #navStats khong co data-step nen vong nay khong bat/tat no, chi bo .on
  document.querySelectorAll('#steps button').forEach((b) => {
    b.classList.toggle('on', b.dataset.step === String(n));
  });
  S.view = n;
  window.scrollTo(0, 0);
}

// Mo cac buoc 2/3/4 khi da co du an. Truoc day dieu kien la "step <= n" nen
// bam sang buoc 5 la mo luon ca 2/3/4 du chua co gi trong do.
function unlockSteps() {
  document.querySelectorAll('#steps button[data-step]').forEach((b) => {
    if (b.dataset.step !== '1') b.disabled = !S.projectId;
  });
}

// Che do chuyen gia chi la mot lop CSS: cac phan .adv an di khi tat. Nho vay
// duong mac dinh khong co mot thanh truot nao, ma khong phai dung hai UI.
function setExpert(on) {
  document.body.classList.toggle('expert', !!on);
  $('expert').checked = !!on;
  localStorage.setItem('tl_expert', on ? '1' : '0');
}

// Trang thong ke: khong thuoc luong 4 buoc, vao ra khong lam mat tien do dang lam.
function showStats() {
  document.querySelectorAll('.step').forEach((s) => s.classList.remove('on'));
  document.querySelectorAll('#steps button').forEach((b) => b.classList.remove('on'));
  $('sStats').classList.add('on');
  $('navStats').classList.add('on');
  S.view = 'stats';
  window.scrollTo(0, 0);
  pollStatus();                 // lam moi ngay khi mo, khong doi chu ky 20s
}

// ================================================================ khoi dong
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
        ? 'Bấm vào ảnh để bỏ khỏi video.'
        : 'Ảnh gần đạt nằm trên. Nếu thấy nhiều ảnh tốt bị loại, nới ngưỡng tương ứng bên trái.';
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
    $('health').innerHTML = `<div class="err">Không gọi được API: ${e.message}</div>`;
  }
})();

function showHealth(h) {
  const bits = [];
  if (!h.indexer.ok) bits.push(`<div class="err">Indexer: ${h.indexer.detail}</div>`);
  if (!h.ffmpeg.ok) bits.push(`<div class="err">ffmpeg: ${h.ffmpeg.detail}</div>`);
  if (h.text && !h.text.ok) {
    bits.push('<div class="warn">Không tìm thấy font TTF nên nhãn chương và tên '
      + `sẽ bị bỏ dấu (${h.text.detail}). Cài <code>pillow</code> + `
      + '<code>fonts-dejavu-core</code>, hoặc đặt <code>FONT_FILE</code>.</div>');
  }
  if (!h.auth) bits.push('<div class="warn">Service đang không có xác thực (API_TOKEN trống).</div>');
  if (h.indexer.ok && h.ffmpeg.ok) bits.push(`<span class="muted">${h.indexer.detail}</span>`);
  $('health').innerHTML = bits.join('');
}

// ========================================================= tien do indexer
// Job indexer chay ngoai service nay (CronJob rieng), nen UI chi doc trang thai
// tu cac cot state trong fp_asset. Tu dong tat polling khi da xong.
function showProgress(p) {
  // Nhan tren nav: chi mot con so, de nguoi dung biet co can mo trang thong ke
  // hay khong ma khong chiem cho tren cac buoc khac.
  const nav = $('navStats');
  if (p.ready) {
    const done = p.stages.reduce((a, s) => a + s.done, 0);
    const total = p.stages.reduce((a, s) => a + s.total, 0) || 1;
    const pct = Math.round(100 * done / total);
    nav.textContent = `Thống kê · ${pct}%`;
    nav.classList.toggle('warnDot', !!p.running);
  } else {
    nav.textContent = 'Thống kê';
  }

  if (!p.ready) {
    $('idxCards').innerHTML = '';
    $('idxBars').innerHTML = '<p class="muted">Chưa có bảng fp_asset — '
      + 'job indexer chưa chạy lần nào.</p>';
    $('idxRuns').innerHTML = '';
    return;
  }

  $('idxCards').innerHTML = [
    ['n_asset', 'ảnh trong thư viện'],
    ['n_face', 'khuôn mặt'],
    ['n_face_ready', 'face có landmark'],
    ['n_body', 'thân người'],
  ].map(([k, lab]) => `<div class="card"><b>${num(p[k])}</b><span>${lab}</span></div>`)
    .join('')
    + (((p.face_err || 0) + (p.body_err || 0))
      ? `<div class="card"><b class="bad">${num((p.face_err || 0) + (p.body_err || 0))}</b>`
        + '<span>ảnh lỗi đọc</span></div>' : '');

  $('idxBars').innerHTML = p.stages.map((s) => {
    const left = Math.max(0, s.total - s.done);
    return `<div class="prow${p.running === s.name ? ' run' : ''}">`
      + `<span class="plab">${s.label}`
      + (p.running === s.name ? ' <b>đang chạy</b>' : '') + '</span>'
      + `<span class="pbar"><i style="width:${Math.min(100, s.pct)}%"></i></span>`
      + `<span class="pnum">${s.pct}%<em>${num(s.done)}/${num(s.total)}`
      + (left ? ` · còn ${num(left)}` : '') + '</em></span></div>';
  }).join('')
    + (p.running
      ? ''
      : '<p class="muted">Không có stage nào đang chạy. Job kế tiếp sẽ tiếp tục '
        + 'đúng chỗ dở — tiến độ nằm trong database, không mất khi tắt máy.</p>');

  $('idxRuns').innerHTML = (p.runs || []).length
    ? '<table class="runs"><tr><th>Stage</th><th>Bắt đầu</th><th>Xong</th>'
      + '<th>Kết quả</th><th>Lỗi</th><th>Ghi chú</th></tr>'
      + p.runs.map((r) => '<tr>'
        + `<td>${r.stage}</td>`
        + `<td>${(r.started_at || '').slice(0, 19).replace('T', ' ')}</td>`
        + `<td>${r.running ? '<b>đang chạy</b>'
          : (r.finished_at || '').slice(11, 19)}</td>`
        + `<td>${num(r.n_done)}</td>`
        + `<td>${r.n_err ? `<span class="bad">${num(r.n_err)}</span>` : '0'}</td>`
        + `<td class="muted">${r.note || ''}</td></tr>`).join('')
      + '</table>'
    : '<p class="muted">chưa có lần chạy nào</p>';

  $('statsAt').textContent = 'cập nhật ' + new Date().toLocaleTimeString('vi-VN');
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
  $('pickBar').classList.toggle('hide', n === 0 && !S.subjects.length);
  showSubjects();
  $('addSubject').disabled = n === 0;
  $('mkVideo').disabled = n === 0 && !S.subjects.length;
  if (!n) {
    $('pickInfo').innerHTML = '<span class="muted">chọn thêm cụm, hoặc bấm '
      + 'Tạo video</span>';
    $('pickNames').textContent = '';
    return;
  }

  const ready = list.reduce((a, p) => a + (p.n_ready || 0), 0);
  const years = list.map((p) => p.first_seen).filter(Boolean).sort();
  const last = list.map((p) => p.last_seen).filter(Boolean).sort();
  const span = years.length && last.length
    ? ` · ${years[0].slice(0, 4)}–${last[last.length - 1].slice(0, 4)}` : '';
  $('pickInfo').innerHTML = `<b>${n}</b> cụm · ${num(ready)} ảnh${span}`;
  $('pickNames').textContent = list
    .map((p) => p.name || '(chưa tên)').join(', ');
}

// "Video của ông A với bà B": mỗi người là một nhóm cụm riêng. Không suy ra
// được từ một mớ cụm lẫn lộn, nên phải chốt từng người một.
function addSubject() {
  const list = [...S.picked.values()];
  if (!list.length) return;
  S.subjects.push({
    name: list.map((p) => p.name).filter(Boolean)[0] || '(chưa tên)',
    ids: list.map((p) => p.person_id),
  });
  S.picked.clear();
  syncPicked();
  toast(`Đã thêm ${S.subjects[S.subjects.length - 1].name}. `
    + 'Giờ chọn cụm của người tiếp theo.');
}

function showSubjects() {
  const has = S.subjects.length > 0;
  $('subjRow').classList.toggle('hide', !has);
  if (!has) return;
  $('subjChips').innerHTML = '';
  S.subjects.forEach((s, i) => {
    const b = el('button', 'chip',
      `${s.name} <em>${s.ids.length} cụm</em> ×`);
    b.onclick = () => { S.subjects.splice(i, 1); syncPicked(); };
    $('subjChips').appendChild(b);
  });
  $('together').parentElement.classList.toggle('hide',
    S.subjects.length + (S.picked.size ? 1 : 0) < 2);
}

// Danh sách người cuối cùng gửi lên: các người đã chốt + các cụm đang chọn dở
// (coi là một người nữa). Chưa chốt ai thì tất cả cụm đang chọn = một người.
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
  return names.filter((n) => n && n !== '(chưa tên)').join(' & ');
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

// ĐƯỜNG MẶC ĐỊNH: một lần bấm, không setup gì. Một request duy nhất tạo dự án,
// chọn ảnh và bắt đầu dựng — server tự suy mọi ngưỡng và độ dài.
async function makeVideo() {
  if (!S.picked.size && !S.subjects.length) return;
  rememberPerson();
  $('mkVideo').disabled = true;
  step(5);
  $('outHead').innerHTML = '<h3>Đang chọn ảnh…</h3>';
  $('renderState').innerHTML = '<p class="muted">đang đọc dữ liệu khuôn mặt…</p>';
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

    // ffmpeg can it nhat 2 frame. Duoi nguong do thi khong render bua roi bao
    // loi kho hieu — bat che do chuyen gia va day sang cho noi nguong.
    if (!r.render_id) {
      setExpert(true);
      step(3);
      renderResult();
      toast(`Chỉ chọn được ${r.n_selected} ảnh, cần ít nhất 2. `
        + 'Nới ngưỡng bên trái rồi bấm Áp dụng.', true);
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

// Duong chuyen gia: chi tao du an roi dung lai o buoc xem anh da chon.
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

// ------------------------------------------------------------- trang video
function showOut(r) {
  const p = S.person || {};
  const st = r.story;
  const who = p.name || '(chưa đặt tên)';
  const range = [$('dFrom').value, $('dTo').value].filter(Boolean).join(' → ');
  $('outHead').innerHTML = `<h3>${who}</h3>`
    + '<p class="muted">'
    + (st ? `${r.n_selected} shot · ${st.n_chapter} chương`
      + (st.n_clip ? ` · ${st.n_clip} đoạn video` : '') + ` · ${st.grain_label}`
      : `${r.n_selected} ảnh`)
    + (range ? ` · ${range}` : '')
    + '</p>';
  $('outStory').innerHTML = '';
}

// "Ngắn hơn / Dài hơn" thay cho một thanh trượt độ dài: người dùng phản ứng với
// cái đã thấy, không phải đoán một con số trước khi thấy gì.
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
      toast('Không còn đủ ảnh, thử hướng ngược lại.', true);
      return;
    }
    await startRender();
    toast(`Nhắm khoảng ${t} giây, đang dựng lại…`);
  } catch (e) {
    toast(e.message, true);
  } finally {
    $('shorter').disabled = $('longer').disabled = false;
  }
}

// ================================================================ buoc 2
function showStep2(r) {
  const p = S.person;
  const how = r.story
    ? `${r.story.n_chapter} chương · ${r.story.grain_label} · dài ~${r.story.est_seconds}s`
    : `mỗi ${r.filters.bucket_days} ngày lấy ${r.filters.per_bucket} ảnh`;
  $('projHead').innerHTML = `<h3>${p.name || '(chưa đặt tên)'}</h3>`
    + `<p class="muted">${p.first_seen ? p.first_seen.slice(0, 10) : '?'} → `
    + `${p.last_seen ? p.last_seen.slice(0, 10) : '?'}`
    + (p.n_cluster > 1 ? ` · gộp ${p.n_cluster} cụm` : '')
    + ` · ${how}</p>`;
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
  'min_ear', 'min_eye_ratio', 'min_sharp', 'bucket_days', 'per_bucket',
  'target_seconds', 'max_per_chapter', 'max_clip_motion'];
const SELECT_KEYS = ['mode', 'pace', 'chapter_by'];

// mode='story' va mode='even' dung hai bo tham so khac nhau. Hien ca hai cung
// luc thi nguoi dung keo mot thanh khong co tac dung nao ma khong hieu tai sao.
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
  // target_seconds = null nghia la "tu suy", khong phai "khong gui". Thanh truot
  // van giu mot con so hop ly de bo tich la dung duoc ngay.
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

// Tom tat cau chuyen: bao nhieu chuong, chuong nao day chuong nao mong. Day la
// cho nguoi dung nhin ra ngay minh se duoc mot video the nao, truoc khi dung.
function showStory(st) {
  const box = $('storyInfo');
  if (!st) {
    box.innerHTML = '<p class="muted">Chế độ rải đều: thời lượng = số ảnh / '
      + 'số ảnh mỗi giây, đặt ở bước 4.</p>';
    return;
  }
  const max = Math.max(1, ...st.chapters.map((c) => c.n_pick));
  box.innerHTML = `<div class="cards small">`
    + `<div class="card ok"><b>${st.n_chapter}</b><span>chương</span></div>`
    + `<div class="card"><b>${st.n_hero}</b><span>điểm nhấn</span></div>`
    + (st.n_clip ? `<div class="card"><b>${st.n_clip}</b><span>đoạn video</span></div>` : '')
    + `<div class="card"><b>~${st.est_seconds}s</b><span>dài dự kiến</span></div>`
    + (st.auto
      ? '<div class="card"><b>tự suy</b><span>độ dài</span></div>'
      : `<div class="card"><b>${st.target_seconds}s</b><span>đặt tay</span></div>`)
    + '</div>'
    + `<p class="muted">${st.grain_label} · điểm nhấn giữ ${st.hold_hero}s, `
    + `ảnh phụ ${st.hold_beat}s</p>`
    // Tăng ngân sách mà video không dài thêm thì luôn là một trong hai lý do
    // này. Không nói ra thì người dùng kéo thanh trượt vô ích.
    + (st.capped ? '<div class="warn">Mọi chương đã đạt trần '
      + `<b>${st.max_per_chapter} ảnh/chương</b>. Tăng độ dài mong muốn sẽ không `
      + 'thêm được ảnh nữa — nâng trần này, hoặc đặt "Một chương là" mịn hơn '
      + 'để có nhiều chương.</div>' : '')
    + (st.exhausted && !st.capped ? '<div class="warn">Đã dùng hết ảnh đạt '
      + 'ngưỡng. Muốn video dài hơn thì nới ngưỡng lọc bên trái.</div>' : '')
    + '<div class="chapbars">'
    + st.chapters.map((c) => `<div class="cb" title="${c.n_avail} ảnh đạt ngưỡng`
      + ` trong giai đoạn này"><span class="cbl">${c.label}</span>`
      + `<span class="cbb"><i style="width:${Math.round(100 * c.n_pick / max)}%"></i></span>`
      + `<span class="cbn">${c.n_pick}<em>/${num(c.n_avail)}</em></span></div>`).join('')
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
  showStory(r.story);
  $('reasons').innerHTML = Object.keys(r.reasons).length
    ? '<table><tr><th>Lý do loại</th><th>Số ảnh</th></tr>'
      + Object.entries(r.reasons).map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('')
      + '</table>'
    : '<p class="muted">Không ảnh nào bị loại.</p>';

  fillSelected(r);
  const gr = $('gridRej');
  gr.innerHTML = '';
  (r.rejected || []).forEach((f) => gr.appendChild(frameNode(f, false)));
  if (!$('tabHint').textContent) $('tabHint').textContent = 'Bấm vào ảnh để bỏ khỏi video.';
}

// Che do ke chuyen: nhom anh theo CHUONG thay vi mot luoi phang. Nhin thay
// chuong nao chi co mot anh la biet ngay giai doan nao thieu du lieu.
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
      : (f.hero ? '<div class="heroTag">điểm nhấn</div>' : ''))
    + `<div class="dt">${dt}</div>`;
  n.title = clip
    ? [`${dt} · đoạn video`,
      `từ ${((f.t_start_ms || 0) / 1000).toFixed(1)}s dài ${(f.dur_s || 0).toFixed(1)}s`,
      f.t_peak_ms != null
        ? `khoảnh khắc ở giây ${(f.t_peak_ms / 1000).toFixed(1)} của clip gốc`
        : '',
      `chính diện ${f.frontality} · nét ${f.sharp}`,
      `độ rung ${f.motion}`,
      f.reason ? `LOẠI: ${f.reason}` : ''].filter(Boolean).join('\n')
    : [`${dt}`, `yaw ${f.yaw}° pitch ${f.pitch}° roll ${f.roll}°`,
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

// Tham so cua buoc dung. Nhip (pace/target_seconds) KHONG nam o day: no thuoc
// buoc chon anh, vi so anh duoc chon theo dung bo so do. Doi nhip thi phai quay
// lai buoc 3 va tinh lai, neu khong thi video dai khong nhu ngan sach.
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

// Vao buoc 4: kieu dung phai khop kieu da chon anh. Chon anh kieu 'even' thi
// frame khong co chuong, dung kieu story se ra chuoi shot dai bang nhau khong
// nhan — dung ky thuat nhung khong phai cai nguoi dung muon.
function enterStep4() {
  if (S.filters && S.filters.mode) {
    $('r_mode').value = S.filters.mode === 'even' ? 'flip' : 'story';
  }
  syncRenderMode();
  loadRenders();
  renderPreview();
  loadStoryboard();
}

// Storyboard tinh o server bang dung thuat toan cua buoc dung, nen con so thoi
// luong la con so that. Debounce vi moi lan goi phai doc lai frame tu db.
function storyboardSoon() {
  clearTimeout(S.sbTimer);
  S.sbTimer = setTimeout(loadStoryboard, 350);
}

async function loadStoryboard() {
  if (!S.projectId) return;
  $('sbInfo').innerHTML = '<p class="muted">đang tính…</p>';
  try {
    const d = await api(`/projects/${S.projectId}/storyboard`, {
      method: 'POST', body: JSON.stringify({options: renderOpts()}),
    });
    S.sb = d;
    if (d.mode !== 'story') {
      $('sbInfo').innerHTML = `<p class="muted">Rải đều: ${d.n_shots} ảnh ở `
        + `${d.fps} ảnh/giây → ${d.duration_s}s.</p>`;
    } else {
      const mx = Math.max(1, ...d.chapters.map((c) => c.seconds));
      $('sbInfo').innerHTML = '<div class="cards small">'
        + `<div class="card ok"><b>${d.duration_s}s</b><span>độ dài thật</span></div>`
        + `<div class="card"><b>${d.n_shots}</b><span>shot</span></div>`
        + (d.n_clip ? `<div class="card"><b>${d.n_clip}</b><span>đoạn video</span></div>` : '')
        + `<div class="card"><b>${d.chapters.length}</b><span>chương</span></div>`
        + `<div class="card"><b>${d.n_frames}</b><span>frame @${d.fps}fps</span></div>`
        + '</div>'
        + (d.target_seconds
          && Math.abs(d.duration_s - d.target_seconds) > d.target_seconds * 0.35
          ? `<div class="warn">Lệch khá xa ngân sách ${d.target_seconds}s. `
            + 'Thường là do quá nhiều chương mà mỗi chương buộc phải có ít nhất '
            + 'một ảnh. Quay lại bước 3, đặt "Một chương là" thô hơn hoặc tăng '
            + 'độ dài mong muốn.</div>' : '')
        + (d.n_missing ? `<div class="warn">${d.n_missing} ảnh không đọc được `
          + 'file preview, đã bỏ khỏi câu chuyện.</div>' : '')
        + '<div class="chapbars">'
        + d.chapters.map((c) => '<div class="cb">'
          + `<span class="cbl">${c.label}</span>`
          + `<span class="cbb"><i style="width:${Math.round(100 * c.seconds / mx)}%"></i></span>`
          + `<span class="cbn">${c.seconds}s<em>${c.n} ảnh</em></span></div>`).join('')
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
    $('renderEst').textContent = `${d.n_shots} ảnh → video ${d.duration_s} giây`;
    return;
  }
  const n = S.result ? S.result.n_selected : 0;
  const fps = Number($('r_fps').value);
  $('renderEst').textContent = n
    ? `${n} ảnh ở ${fps} ảnh/giây → video khoảng ${(n / fps).toFixed(1)} giây`
    : '';
}

async function startRender() {
  if (!S.projectId) return;
  $('render').disabled = true;
  $('reRender').disabled = true;
  $('r_aspect').value = $('o_aspect').value;      // hai cho, mot gia tri
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
      const label = {queued: 'đang chờ', frames: 'đang dựng frame',
        encoding: 'ffmpeg đang encode', audio: 'đang ghép tiếng',
        done: 'xong', error: 'lỗi'}[r.status] || r.status;
      $('renderState').innerHTML = r.status === 'error'
        ? `<div class="err">Lỗi: ${r.err}</div>`
        : `<p><b>${label}</b> — ${r.n_done}/${r.n_total} frame</p>`
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
        $('renderState').innerHTML = `<p><b>Xong</b> — `
          + `${r.duration_s ? r.duration_s.toFixed(1) : '?'} giây`
          + (st.n_shots ? ` · ${st.n_shots} ảnh` : '')
          + (st.n_chapter ? ` · ${st.n_chapter} chương` : '')
          + `. <a href="${url}" download>Tải mp4</a></p>`
          + (st.n_missing ? `<div class="warn">${st.n_missing} ảnh không đọc `
            + 'được file preview, đã bỏ qua.</div>' : '');
        loadRenders();
        toast('Video đã dựng xong');
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
