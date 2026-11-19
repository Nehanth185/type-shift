/* Work Glasses companion v0. Plain JS, no build step.
   Talks to the answer server over the contract in ../API.md (real server) or mock_server.py (canned). */
(() => {
'use strict';
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const HUD_LINES = 3, HUD_WORDS = 7, HUD_CHARS = 40, TIMEOUT_MS = 30000, MAX_IMG_PX = 1024;  // HUD_CHARS = server HUD_CHARS

/* ---------------- settings ---------------- */
function defaultServer() {
  return /^https?:$/.test(location.protocol) ? location.origin : 'http://localhost:8000';
}
const S = {
  get server() { return (localStorage.getItem('wg.server') || '').trim().replace(/\/+$/, '') || defaultServer(); },
  get workspace() { return localStorage.getItem('wg.workspace') || 'default'; },
  get worker() { return localStorage.getItem('wg.worker') || 'w1'; },
  get token() { return (localStorage.getItem('wg.token') || '').trim(); },
};
const state = { photoB64: null, stream: null, guideActive: false, lastAsk: null, mockServer: false };
window.__app = state; // for tests / debugging

/* ---------------- API ---------------- */
async function api(method, path, body, { form = false } = {}) {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  const opts = { method, signal: ctl.signal, headers: {} };
  if (S.token) opts.headers['Authorization'] = 'Bearer ' + S.token;
  if (body !== undefined) {
    if (form) opts.body = body; else { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  }
  let res;
  try { res = await fetch(S.server + path, opts); }
  catch (e) { throw new Error(e.name === 'AbortError' ? 'Server took too long' : `Can't reach server at ${S.server}`); }
  finally { clearTimeout(t); }
  if (res.headers.get('X-Mock') === 'true') markMock(true);
  let data = null; const txt = await res.text();
  try { data = txt ? JSON.parse(txt) : {}; } catch { data = { raw: txt }; }
  if (!res.ok) throw new Error((data && (data.error || data.detail)) ? `${res.status}: ${typeof data.detail === 'string' ? data.detail : (data.error || JSON.stringify(data.detail))}` : `HTTP ${res.status}`);
  if (data && data.mock === true) markMock(true);
  return data;
}
const listOf = (d, key) => Array.isArray(d) ? d : (d && Array.isArray(d[key]) ? d[key] : []);

function markMock(on) {
  state.mockServer = on;
  $('#conn').classList.toggle('mock', on);
  $('#hud').classList.toggle('mock', on);
}
function setConn(ok, msg) {
  const c = $('#conn'); c.classList.toggle('ok', ok); c.classList.toggle('bad', !ok);
  $('#connTxt').textContent = msg;
}
function banner(msg) { const b = $('#banner'); if (!msg) { b.classList.add('hidden'); return; } b.textContent = msg; b.classList.remove('hidden'); }
function status(sel, msg, err = false) { const el = $(sel); el.textContent = msg || ''; el.classList.toggle('err', !!err); }

async function checkConn() {
  try {
    let health = null, me = null;
    try { health = await api('GET', '/health'); } catch { /* older servers: /docs below decides */ }
    if (health && health.auth) {  // real server: ask who the token says we are
      me = await api('GET', '/whoami');
      if (me && me.worker_id && me.worker_id !== S.worker) localStorage.setItem('wg.worker', me.worker_id);
    }
    const d = await api('GET', `/docs?workspace_id=${encodeURIComponent(S.workspace)}`);
    state.server = { provider: health && health.provider, auth: health && health.auth, worker: me && me.worker_id };
    let msg = state.mockServer || (d && d.mock) ? `MOCK server · ${hostOf(S.server)}` : `connected · ${hostOf(S.server)}`;
    if (!state.mockServer && state.server.provider) msg += ` · ${state.server.provider} LLM`;
    if (state.server.worker) msg += ` · as ${state.server.worker}`;
    setConn(true, msg);
    banner(null); return true;
  } catch (e) { setConn(false, 'offline'); banner(e.message); return false; }
}
const hostOf = (u) => { try { return new URL(u).host; } catch { return u; } };

/* ---------------- tabs ---------------- */
function showTab(name) {
  $$('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  $$('.view').forEach(v => v.classList.toggle('on', v.id === 'view-' + name));
  if (name === 'docs') loadDocs();
  if (name === 'memory') loadMemory();
  if (name === 'settings') fillSettings();
  history.replaceState(null, '', '#' + name);
}
$$('.tabs button').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));

/* ---------------- HUD ---------------- */
function clampWords(line) {
  const w = String(line ?? '').trim().split(/\s+/).filter(Boolean);
  let text = w.slice(0, HUD_WORDS).join(' '), cut = w.length > HUD_WORDS;
  if (text.length > HUD_CHARS) { text = text.slice(0, HUD_CHARS - 1); cut = true; }
  return { text: cut ? text.replace(/[\s.,;:…]+$/, '') + '…' : text, cut };
}
function showHUD(lines, { tag = 'ANSWER', step = '' } = {}) {
  const arr = Array.isArray(lines) ? lines : String(lines || '').split('\n');
  let cut = arr.length > HUD_LINES;
  const out = arr.slice(0, HUD_LINES).map(l => { const c = clampWords(l); cut = cut || c.cut; return c.text; });
  ['#hud1', '#hud2', '#hud3'].forEach((s, i) => { $(s).textContent = out[i] || ''; });
  $('#hudTag').textContent = (state.mockServer ? 'MOCK · ' : '') + tag;
  $('#hudStep').textContent = step;
  const w = $('#hudWarn'); const msgs = [];
  if (cut) msgs.push(`Server sent ${arr.length} line(s), some over ${HUD_WORDS} words or ${HUD_CHARS} characters. Trimmed to fit the display.`);
  const wide = ['#hud1', '#hud2', '#hud3'].filter(s => { const el = $(s); return el.scrollWidth > el.clientWidth + 1; });
  if (wide.length) msgs.push(`${wide.length} line(s) too wide for the display; the end is cut off on the glasses.`);
  state.hudOverflow = wide.length;
  if (msgs.length) { w.textContent = msgs.join(' '); w.classList.remove('hidden'); } else w.classList.add('hidden');
  return out;
}

/* ---------------- photo ---------------- */
function fileToB64(file) {
  return new Promise((resolve, reject) => {
    const img = new Image(); const url = URL.createObjectURL(file);
    img.onload = () => { try { resolve(drawToB64(img, img.naturalWidth, img.naturalHeight)); } catch (e) { reject(e); } finally { URL.revokeObjectURL(url); } };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('That file is not an image')); };
    img.src = url;
  });
}
function drawToB64(src, w, h) {
  const k = Math.min(1, MAX_IMG_PX / Math.max(w, h)); const c = document.createElement('canvas');
  c.width = Math.round(w * k); c.height = Math.round(h * k);
  c.getContext('2d').drawImage(src, 0, 0, c.width, c.height);
  return c.toDataURL('image/jpeg', 0.8);
}
function setPhoto(dataUrl) {
  state.photoB64 = dataUrl ? dataUrl.split(',')[1] : null;
  const p = $('#photoPreview');
  if (dataUrl) { p.src = dataUrl; p.classList.remove('hidden'); $('#clearPhoto').classList.remove('hidden'); }
  else { p.removeAttribute('src'); p.classList.add('hidden'); $('#clearPhoto').classList.add('hidden'); }
}
$('#photoIn').addEventListener('change', async (e) => {
  const f = e.target.files[0]; if (!f) return;
  try { stopCam(); setPhoto(await fileToB64(f)); status('#askStatus', 'Photo attached'); }
  catch (err) { status('#askStatus', err.message, true); }
  e.target.value = '';
});
$('#clearPhoto').addEventListener('click', () => { setPhoto(null); status('#askStatus', ''); });
$('#camBtn').addEventListener('click', async () => {
  if (state.stream) { stopCam(); return; }
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
    const v = $('#camVideo'); v.srcObject = state.stream; v.classList.remove('hidden'); await v.play();
    $('#snapBtn').classList.remove('hidden'); $('#camBtn').textContent = 'Close camera';
  } catch (e) { status('#askStatus', 'Camera not available: ' + (e.message || e.name), true); }
});
$('#snapBtn').addEventListener('click', () => {
  const v = $('#camVideo'); if (!v.videoWidth) { status('#askStatus', 'Camera not ready yet', true); return; }
  setPhoto(drawToB64(v, v.videoWidth, v.videoHeight)); stopCam(); status('#askStatus', 'Photo taken');
});
function stopCam() {
  if (state.stream) state.stream.getTracks().forEach(t => t.stop());
  state.stream = null; $('#camVideo').classList.add('hidden'); $('#snapBtn').classList.add('hidden'); $('#camBtn').textContent = 'Use camera';
}

/* ---------------- ask + answer ---------------- */
function renderAnswer(d, { tag } = {}) {
  const g = d.guide;
  state.guideActive = !!g;
  showHUD(d.answer_hud || (g ? [g.text] : []), { tag: tag || (g ? 'GUIDE' : 'ANSWER'), step: g ? `${g.step}/${g.total}` : '' });
  $('#guideBtns').classList.toggle('hidden', !g);
  if (g) {
    // Last step: Next turns into Finish (the server answers finished:true and keeps the guide until "done, log it").
    const last = g.step >= g.total;
    $('#guideCount').textContent = d.finished ? `All ${g.total} steps done` : `Step ${g.step} of ${g.total}`;
    $('#prevBtn').disabled = g.step <= 1;
    $('#nextBtn').disabled = !!d.finished;
    $('#nextBtn').textContent = last ? 'Finish ✓' : 'Next ▸';
  }
  state.pendingId = d.needs_confirm ? d.pending_id : null;
  $('#confirmBtns').classList.toggle('hidden', !d.needs_confirm);
  const full = $('#answerFull'); full.textContent = d.answer_full || '(no full answer)'; full.classList.remove('muted');
  $('#latency').textContent = Number.isFinite(d.latency_ms) ? `· ${d.latency_ms} ms` : '';
  renderSources(d.sources || []);
}
function renderSources(src) {
  const ul = $('#sources'); ul.replaceChildren(); $('#sourceDetail').classList.add('hidden');
  if (!src.length) { const li = document.createElement('li'); li.className = 'muted'; li.textContent = 'No source. Treat this answer as unverified.'; ul.append(li); return; }
  src.forEach((s, i) => {
    const li = document.createElement('li'); const b = document.createElement('button');
    b.type = 'button'; b.className = 'src'; b.dataset.i = i;
    b.textContent = `${s.title || s.doc_id}${s.page != null ? ' · p.' + s.page : ''}`;
    b.addEventListener('click', () => {
      $$('#sources button').forEach(x => x.classList.toggle('sel', x === b));
      const det = $('#sourceDetail'); det.replaceChildren();
      const h = document.createElement('b'); h.textContent = `${s.title || s.doc_id}${s.page != null ? ', page ' + s.page : ''}`;
      const sn = document.createElement('div'); sn.className = 'snip'; sn.textContent = s.snippet ? `“${s.snippet}”` : '(no snippet returned)';
      const id = document.createElement('div'); id.className = 'hint'; id.textContent = `doc_id: ${s.doc_id}`;
      det.append(h, sn, id); det.classList.remove('hidden');
    });
    li.append(b); ul.append(li);
  });
}
$('#askForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = $('#question').value.trim(); const mode = $('input[name=mode]:checked').value;
  if (!q && !state.photoB64) { status('#askStatus', 'Type a question or add a photo', true); return; }
  const body = { workspace_id: S.workspace, worker_id: S.worker, question: q, mode };
  if (state.photoB64) body.image_b64 = state.photoB64;
  state.lastAsk = { ...body, image_b64: body.image_b64 ? body.image_b64.length : undefined };
  $('#askBtn').disabled = true; status('#askStatus', 'Asking…'); showHUD(['…'], { tag: 'THINKING' });
  try { const d = await api('POST', '/ask', body); renderAnswer(d); status('#askStatus', ''); }
  catch (err) {  // G43: a 429 (rate limit) is not a connection problem
    showHUD(/^429\b/.test(err.message) ? ['Too many questions', 'Wait a few seconds'] : ["Can't reach answers", 'Check server in Settings'], { tag: 'ERROR' });
    status('#askStatus', err.message, true);
  }
  finally { $('#askBtn').disabled = false; }
});
$('#question').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#askForm').requestSubmit(); } });

async function guideMove(dir) {
  try { const d = await api('POST', `/guide/${dir}`, { worker_id: S.worker }); renderAnswer(d, { tag: 'GUIDE' }); }
  catch (err) { status('#askStatus', err.message, true); }
}
$('#nextBtn').addEventListener('click', () => guideMove('next'));
$('#prevBtn').addEventListener('click', () => guideMove('prev'));
document.addEventListener('keydown', (e) => {
  if (!state.guideActive || /INPUT|TEXTAREA/.test(document.activeElement.tagName)) return;
  if (e.key === 'ArrowRight' && !$('#nextBtn').disabled) guideMove('next');
  if (e.key === 'ArrowLeft' && !$('#prevBtn').disabled) guideMove('prev');
});

/* ---------------- ready card ---------------- */
$$('.trig').forEach(b => b.addEventListener('click', async () => {
  const trigger = b.dataset.trigger; const label = $('#ctxLabel').value.trim();
  try {
    const d = await api('POST', '/context', { worker_id: S.worker, trigger, label });
    state.guideActive = false; $('#guideBtns').classList.add('hidden');
    showHUD(d.lines || [], { tag: 'READY CARD', step: trigger.replace('_', ' ') });
    const info = $('#ctxInfo'); info.replaceChildren();
    const add = (k, v) => { if (v == null || (Array.isArray(v) && !v.length)) return; const p = document.createElement('div'); const b2 = document.createElement('b'); b2.textContent = k + ': '; p.append(b2, document.createTextNode(Array.isArray(v) ? v.join('; ') : v)); info.append(p); };
    add('Last answer', d.last_answer); add('Usual next step', d.usual_next_step); add('Unfinished', d.unfinished);
    if (!info.childElementCount) info.textContent = 'Nothing remembered for this yet.';
    info.classList.remove('hidden');
  } catch (err) { status('#askStatus', err.message, true); }
}));

/* ---------------- docs ---------------- */
async function loadDocs() {
  const tb = $('#docRows');
  try {
    const docs = listOf(await api('GET', `/docs?workspace_id=${encodeURIComponent(S.workspace)}`), 'docs');
    tb.replaceChildren();
    if (!docs.length) { tb.innerHTML = '<tr><td colspan="4" class="muted">No docs yet. Upload a manual to start.</td></tr>'; return; }
    docs.forEach(d => {
      const tr = document.createElement('tr'); tr.dataset.docId = d.doc_id;
      [d.title || d.doc_id, d.pages ?? '?', d.chunks ?? '?'].forEach(v => { const td = document.createElement('td'); td.textContent = v; tr.append(td); });
      const td = document.createElement('td'); const del = document.createElement('button'); del.className = 'ghost del'; del.textContent = 'Delete';
      del.addEventListener('click', async () => {
        if (!confirm(`Delete "${d.title}" for everyone in this workspace?`)) return;
        try { await api('DELETE', `/docs/${encodeURIComponent(d.doc_id)}`); status('#uploadStatus', `Deleted ${d.title}`); loadDocs(); }
        catch (err) { status('#uploadStatus', err.message, true); }
      });
      td.append(del); tr.append(td); tb.append(tr);
    });
  } catch (err) { tb.innerHTML = ''; const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = 4; td.className = 'status err'; td.textContent = err.message; tr.append(td); tb.append(tr); }
}
$('#uploadForm').addEventListener('submit', async (e) => {
  e.preventDefault(); const f = $('#docFile').files[0];
  if (!f) { status('#uploadStatus', 'Pick a file first', true); return; }
  const fd = new FormData(); fd.append('file', f); fd.append('workspace_id', S.workspace);
  $('#uploadBtn').disabled = true; status('#uploadStatus', `Uploading ${f.name}…`);
  try { const d = await api('POST', '/docs', fd, { form: true }); status('#uploadStatus', `Added "${d.title}": ${d.pages} page${d.pages == 1 ? '' : 's'}, ${d.chunks} chunk${d.chunks == 1 ? '' : 's'}`); $('#docFile').value = ''; loadDocs(); }
  catch (err) { status('#uploadStatus', err.message, true); }
  finally { $('#uploadBtn').disabled = false; }
});

/* ---------------- memory ---------------- */
async function loadMemory() {
  const ul = $('#memList');
  try {
    const items = listOf(await api('GET', `/memory?worker_id=${encodeURIComponent(S.worker)}`), 'items');
    ul.replaceChildren();
    if (!items.length) { ul.innerHTML = '<li class="muted">Nothing remembered. The glasses start fresh.</li>'; return; }
    items.forEach(it => {
      const li = document.createElement('li'); li.dataset.memId = it.id;
      const k = document.createElement('span'); k.className = 'kind'; k.textContent = (it.kind || 'fact').replace('_', ' ');
      const t = document.createElement('span'); t.className = 'txt'; t.textContent = it.text ?? it.value ?? JSON.stringify(it);
      const b = document.createElement('button'); b.className = 'ghost del'; b.textContent = 'Delete'; b.setAttribute('aria-label', 'Delete memory: ' + t.textContent);
      b.addEventListener('click', async () => {
        try { await api('DELETE', `/memory/${encodeURIComponent(it.id)}?worker_id=${encodeURIComponent(S.worker)}`); status('#memStatus', 'Deleted'); loadMemory(); }
        catch (err) { status('#memStatus', err.message, true); }
      });
      li.append(k, t, b); ul.append(li);
    });
  } catch (err) { ul.innerHTML = ''; const li = document.createElement('li'); li.className = 'status err'; li.textContent = err.message; ul.append(li); }
}
$('#memRefresh').addEventListener('click', loadMemory);
$('#forgetAll').addEventListener('click', async () => {
  if (!confirm('Forget everything the glasses remember about you? This cannot be undone.')) return;
  try { const d = await api('DELETE', `/memory?worker_id=${encodeURIComponent(S.worker)}`); status('#memStatus', `Forgot ${d.deleted ?? 'all'} item(s)`); loadMemory(); }
  catch (err) { status('#memStatus', err.message, true); }
});

/* ---------------- settings ---------------- */
function fillSettings() {
  $('#serverUrl').value = localStorage.getItem('wg.server') || ''; $('#serverUrl').placeholder = defaultServer();
  $('#workspaceId').value = S.workspace; $('#workerId').value = S.worker; $('#workerToken').value = S.token;
}
$('#settingsForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const url = $('#serverUrl').value.trim();
  if (url && !/^https?:\/\/[^\s]+$/i.test(url)) { status('#settingsStatus', 'Server URL must start with http:// or https://', true); return; }
  localStorage.setItem('wg.server', url);
  localStorage.setItem('wg.workspace', $('#workspaceId').value.trim() || 'default');
  localStorage.setItem('wg.worker', $('#workerId').value.trim() || 'w1');
  localStorage.setItem('wg.token', $('#workerToken').value.trim());
  markMock(false); status('#settingsStatus', 'Saved'); await checkConn();
  if (state.server && state.server.worker) $('#workerId').value = S.worker;  // the token decides the worker
});
$('#testConn').addEventListener('click', async () => { status('#settingsStatus', 'Testing…'); const ok = await checkConn(); status('#settingsStatus', ok ? 'Connected' : 'Could not connect', !ok); });

/* ---------------- boot ---------------- */
const start = (location.hash || '#ask').slice(1);
showTab(['ask', 'docs', 'memory', 'settings'].includes(start) ? start : 'ask');
checkConn();
async function confirmReading(ok) {
  if (!state.pendingId) return;
  try { const d = await api('POST', '/confirm', { pending_id: state.pendingId, worker_id: S.worker, ok }); renderAnswer(d); }
  catch (e) { status('#askStatus', 'Confirm expired, ask again', true); $('#confirmBtns').classList.add('hidden'); state.pendingId = null; }
}
$('#confirmYes').addEventListener('click', () => confirmReading(true));
$('#confirmNo').addEventListener('click', () => confirmReading(false));
})();

