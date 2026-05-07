/* MacSweep frontend — sidebar app, focused modules, sticky cart ─────── */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const CATEGORY_COLORS = {
  cache: '#4ade80', dev_artifact: '#38bdf8', trash: '#a3e635', logs: '#94a3b8',
  installer: '#facc15', downloads: '#fb923c', ios_backup: '#f59e0b',
  media: '#f472b6', documents: '#a78bfa', ml_model: '#818cf8',
  apps: '#60a5fa', other: '#525258',
};

const CATEGORY_LABEL = {
  cache: 'Caches', dev_artifact: 'Dev artifacts', trash: 'Trash',
  logs: 'Logs', installer: 'Installers', downloads: 'Downloads',
  ios_backup: 'iOS backups', media: 'Media', documents: 'Documents',
  ml_model: 'ML weights', apps: 'Apps', other: 'Other',
};

/* ── Skeleton helpers (replace black "Loading…" everywhere) ───────────── */
function skeletonRows(n = 5) {
  return Array.from({ length: n }, () => `
    <div class="skel-row skel">
      <span class="skel-block" style="width:20px;height:20px;border-radius:6px"></span>
      <span class="skel-block" style="width:32px;height:32px"></span>
      <div><span class="skel-line md"></span><span class="skel-line sm" style="margin-top:6px"></span></div>
      <span class="skel-block" style="width:60px;height:14px"></span>
    </div>
  `).join('');
}
function skeletonSuggestionRows(n = 3) {
  return Array.from({ length: n }, () => `
    <div class="suggestion skel">
      <span class="skel-block" style="width:20px;height:20px"></span>
      <div><span class="skel-line md"></span><span class="skel-line sm" style="margin-top:6px"></span></div>
      <span class="skel-block" style="width:54px;height:14px"></span>
      <span></span>
    </div>
  `).join('');
}
function skeletonGroupRows(n = 6) {
  return Array.from({ length: n }, () => `
    <div class="group-row skel" style="margin-bottom:4px">
      <span class="skel-block" style="width:20px;height:20px"></span>
      <span class="skel-block" style="width:36px;height:36px"></span>
      <div><span class="skel-line md"></span><span class="skel-line sm" style="margin-top:6px"></span></div>
      <span class="skel-block" style="width:60px;height:14px"></span>
    </div>
  `).join('');
}
function skeletonSweepCard() {
  return `
    <div class="sweep-card skel">
      <div class="sweep-preview skel-block" style="border-radius:0"></div>
      <div class="sweep-info">
        <span class="skel-line md" style="height:18px"></span>
        <span class="skel-line sm" style="margin-top:8px"></span>
      </div>
      <div class="sweep-meta-grid">
        <div><span class="skel-line sm"></span><span class="skel-line" style="margin-top:6px;width:50%"></span></div>
        <div><span class="skel-line sm"></span><span class="skel-line" style="margin-top:6px;width:50%"></span></div>
        <div><span class="skel-line sm"></span><span class="skel-line" style="margin-top:6px;width:50%"></span></div>
        <div><span class="skel-line sm"></span><span class="skel-line" style="margin-top:6px;width:50%"></span></div>
      </div>
    </div>
  `;
}

/* ── Format ───────────────────────────────────────────────────────────── */
function bytes(n) {
  if (!n || n < 0) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(n < 10 && i > 0 ? 1 : 0) + ' ' + u[i];
}
function bytesNum(n) {
  if (!n || n < 0) return ['0', 'B'];
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return [n.toFixed(n < 10 && i > 0 ? 1 : 0), u[i]];
}
function ago(ts) {
  if (!ts) return '—';
  const s = (Date.now() / 1000) - ts;
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  const d = Math.floor(s / 86400);
  if (d < 30) return d + 'd ago';
  const mo = Math.floor(d / 30);
  if (mo < 12) return mo + 'mo ago';
  return Math.floor(d / 365) + 'y ago';
}
function escapeAttr(s) { return String(s).replaceAll('"', '&quot;'); }

/* ── State ────────────────────────────────────────────────────────────── */
const state = {
  overview: null,
  suggestions: [],
  selectedSuggestions: new Set(),
  selectedGroups: new Map(),
  selectedFiles: new Map(),
  pollTimer: null,
  module: 'scan',
  filesTab: 'smart',
};

/* AI verdicts feature — Integrator-backed. Refreshed on boot, after every
   connect/disconnect/toggle, and when settings open. Single source of truth
   for whether `Ask Sweeper` buttons are visible anywhere in the UI. */
const aiFeature = {
  loaded: false,
  connected: false,
  enabled: false,        // ai_verdicts_enabled in config
  email: null,
  expiresAt: null,
  // Memoize per-path verdict for the current page so re-renders don't re-fetch.
  verdicts: new Map(),
};
function aiActive() { return aiFeature.connected && aiFeature.enabled; }

/* ── API ──────────────────────────────────────────────────────────────── */
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

/* ── Toast ────────────────────────────────────────────────────────────── */
let toastTimer;
function toast(msg, tone = '') {
  const t = $('#toast'); t.textContent = msg; t.className = 'toast ' + tone;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), 3500);
}

/* ── Boot ─────────────────────────────────────────────────────────────── */
function removeInitialSplash() {
  const el = document.getElementById('initial-splash');
  if (!el) return;
  // Smoothly ride to 100% before fading out — no jarring stop mid-animation.
  const pctEl = document.getElementById('splashPercent');
  const msgEl = document.getElementById('splashMsg');
  if (msgEl) msgEl.textContent = 'Ready';
  if (pctEl) {
    let cur = parseInt(pctEl.textContent, 10) || 90;
    const stride = setInterval(() => {
      cur = Math.min(100, cur + 4);
      pctEl.textContent = cur + '%';
      if (cur >= 100) {
        clearInterval(stride);
        el.style.opacity = '0';
        setTimeout(() => el.remove(), 350);
      }
    }, 18);
  } else {
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 350);
  }
}

async function boot() {
  bindGlobalUI();
  // Fire-and-forget — the AI feature gate doesn't block boot. UI listens
  // for `ai-feature-changed` to toggle Ask Sweeper buttons after.
  refreshIntegratorStatus().catch(() => {});
  try {
    // Single-source-of-truth status query
    const s = await api('/api/status');

    // Hydrate from localStorage IMMEDIATELY so renders are instant
    if (s.scan_id) hydrateCacheFromLocalStorage(s.scan_id);

    // Decisive routing on the typed lifecycle state:
    if (s.state === 'no_data') {
      showWelcome();
      return;
    }
    if (s.state === 'scanning' || s.state === 'warming') {
      // No prior data — must wait
      showScanning();
      pollStatus();
      return;
    }

    // 'ready', 'ready_warming', 'ready_warming_bg', 'ready_scanning':
    // we have data; show the app immediately. Any background work
    // (warmup or rescan) shows in the freshness chip; doesn't block UI.
    const overview = await cachedApi('/api/overview');
    await showApp(overview);
    if (s.state !== 'ready') startFreshnessPolling();
  } finally {
    removeInitialSplash();
  }
}

// Replaces pollScan + pollBackgroundRescan + waitForWarmup with one loop.
let _statusTimer = null;
function startFreshnessPolling() {
  clearInterval(_statusTimer);
  _statusTimer = setInterval(async () => {
    try {
      const s = await api('/api/status');
      updateFreshnessChip(s);
      if (s.state === 'ready') {
        clearInterval(_statusTimer);
        _statusTimer = null;
        // New data arrived — refresh
        cacheClear();
        if (s.scan_id) _activeScanId = s.scan_id;
        await refreshOverview();
        await renderModule(state.module);
      }
    } catch (e) { /* keep polling */ }
  }, 1000);
}

function pollStatus() {
  // For first-ever scan: drives the scan-progress UI.
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    const s = await api('/api/status');
    $('#scanPercent').textContent = s.progress + '%';
    $('#scanBarFill').style.width = s.progress + '%';
    $('#scanStat').textContent = s.live_count.toLocaleString() +
      (s.live_total ? ` / ${s.live_total.toLocaleString()}` : '');
    $('#scanSize').textContent = bytes(s.live_size);
    $('#scanMessage').textContent = s.label;
    if (s.state === 'ready' || s.state === 'ready_warming_bg' || s.state === 'ready_warming') {
      clearInterval(state.pollTimer);
      const overview = await api('/api/overview');
      _activeScanId = s.scan_id;
      setTimeout(() => showApp(overview), 250);
    }
  }, 500);
}

let _bgRescanTimer = null;
function pollBackgroundRescan() {
  const banner = $('#rescanBanner');
  const text = $('#rescanBannerText');
  const prog = $('#rescanBannerProgress');
  banner.classList.remove('hidden');
  clearInterval(_bgRescanTimer);
  _bgRescanTimer = setInterval(async () => {
    const s = await api('/api/scan/status').catch(() => null);
    if (!s) return;
    if (s.phase === 'counting' || (!s.total && s.phase !== 'warming')) {
      text.textContent = 'Re-indexing — counting files…';
      prog.textContent = '';
    } else if (s.phase === 'warming') {
      text.textContent = 'Re-indexing — optimizing data…';
      prog.textContent = '';
    } else if (s.running) {
      text.textContent = 'Re-indexing in the background…';
      const pct = s.total ? Math.floor((s.files_indexed / s.total) * 100) : 0;
      prog.textContent = ` ${pct}%`;
    } else {
      // Done — refresh data and hide
      clearInterval(_bgRescanTimer);
      banner.classList.add('hidden');
      cacheClear();
      await refreshOverview();
      await renderModule(state.module);
      toast('Re-indexed. Numbers updated.', 'safe');
    }
  }, 800);
}

async function waitForWarmup() {
  // Drive the inline splash with REAL progress from /api/warmup/status
  const splash = document.getElementById('initial-splash');
  if (!splash) return;
  if (window.__splashTakeover) window.__splashTakeover();
  const pctEl = document.getElementById('splashPercent');
  const msgEl = document.getElementById('splashMsg');

  while (true) {
    const w = await api('/api/warmup/status');
    if (w.is_warm) {
      if (pctEl) pctEl.textContent = '100%';
      if (msgEl) msgEl.textContent = 'Ready';
      return;
    }
    if (w.error) {
      if (msgEl) msgEl.textContent = 'Loading without cache…';
      return;  // proceed even if warmup errored — endpoints fall back to live
    }
    if (pctEl && typeof w.progress === 'number') pctEl.textContent = w.progress + '%';
    if (msgEl && w.step) msgEl.textContent = w.step;
    await new Promise(r => setTimeout(r, 400));
  }
}

function bindGlobalUI() {
  $('#welcomeScanBtn').onclick = startScan;
  $('#rescanBtn').onclick = startScan;

  $$('.nav-item').forEach(n => n.onclick = () => switchModule(n.dataset.module));

  // Drawer: close on X, ESC, or backdrop click
  $('#drawerClose').onclick = () => $('#drawer').classList.add('hidden');
  $('#drawer').addEventListener('click', e => {
    if (e.target.classList.contains('drawer-bg')) $('#drawer').classList.add('hidden');
  });
  // File Detail: close on backdrop click too
  $('#fileDetail').addEventListener('click', e => {
    if (e.target.classList.contains('file-detail-bg')) $('#fileDetail').classList.add('hidden');
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      $('#drawer').classList.add('hidden');
      $('#fileDetail').classList.add('hidden');
    }
    // Spacebar → macOS Quick Look on the focused file (just like Finder)
    if (e.key === ' ' && !e.target.matches('input,textarea,button')) {
      const path = _fdCurrentPath || _hoveredFilePath;
      if (path) {
        e.preventDefault();
        api('/api/file/quicklook', { method: 'POST', body: JSON.stringify({ path }) });
      }
    }
  });

  $('#cartClear').onclick = clearCart;
  $('#cartCommit').onclick = commitCart;

  $$('#filesTabs .tab').forEach(t => t.onclick = () => switchFilesTab(t.dataset.tab));

  // Clickable stat cells — open the explainer
  $$('.stat-cell[data-stat]').forEach(c => c.onclick = () => openStatExplainer(c.dataset.stat));
  $('#statExplainerClose').onclick = () => $('#statExplainer').classList.add('hidden');
  $('#statExplainer').addEventListener('click', e => {
    if (e.target.classList.contains('file-detail-bg')) $('#statExplainer').classList.add('hidden');
  });

  // Click outside any expanded suggestion → collapse it
  document.addEventListener('click', (e) => {
    if (_expandedSuggestion && !_expandedSuggestion.contains(e.target)) {
      _expandedSuggestion.classList.remove('expanded');
      _expandedSuggestion = null;
    }
  });

  // Settings drawer (LLM auth)
  $('#settingsBtn').onclick = openSettings;
  $('#settingsClose').onclick = () => $('#settings').classList.add('hidden');
  $('#settings').addEventListener('click', e => {
    if (e.target.classList.contains('file-detail-bg')) $('#settings').classList.add('hidden');
  });
  $('#llmSave').onclick = async () => {
    const key = $('#llmKeyInput').value.trim();
    if (!key.startsWith('sk-')) { toast("That doesn't look like an OpenAI key.", 'danger'); return; }
    await api('/api/llm/key', { method: 'POST', body: JSON.stringify({ key, model: 'gpt-4o-mini' }) });
    toast('Key saved.', 'safe');
    refreshLLMStatus();
  };
  $('#llmClear').onclick = async () => {
    await api('/api/llm/key', { method: 'DELETE' });
    $('#llmKeyInput').value = '';
    toast('Key cleared.', 'safe');
    refreshLLMStatus();
  };
  $('#oauthLogin').onclick = async () => {
    const btn = $('#oauthLogin');
    const statusEl = $('#oauthStatus');
    btn.disabled = true;
    btn.textContent = 'Opening browser…';
    try {
      const start = await api('/api/oauth/login', { method: 'POST' });
      if (!start.ok) throw new Error(start.error || 'Could not start');
      // Backend opens the URL via macOS `open`. Show a copyable fallback in
      // case the default browser isn't where the user expects it.
      if (statusEl) {
        statusEl.innerHTML = `Browser opened. If it didn't, <a href="${start.auth_url}" target="_blank" style="color:var(--accent)">click here</a> to sign in.`;
      }
      btn.textContent = 'Waiting for sign-in…';
      const result = await pollOAuthStatus(180_000);
      if (result.status === 'ok') {
        toast(`Signed in${result.email ? ` as ${result.email}` : ''}.`, 'safe');
      } else if (result.status === 'error') {
        toast(`Sign-in failed: ${result.error}`, 'danger');
      } else {
        toast('Sign-in timed out.', 'danger');
      }
    } catch (e) {
      toast('Sign-in error: ' + e.message, 'danger');
    }
    btn.disabled = false;
    btn.textContent = 'Sign in with ChatGPT';
    refreshLLMStatus();
  };
  $('#oauthSignOut').onclick = async () => {
    await api('/api/oauth/sign-out', { method: 'POST' });
    toast('Signed out.', 'safe');
    refreshLLMStatus();
  };

}

async function openSettings() {
  $('#settings').classList.remove('hidden');
  await Promise.all([refreshLLMStatus(), refreshIntegratorStatus(), refreshUpdateStatus()]);
}

/* ── Self-update (voicetype-style) ────────────────────────────────────── */
const updateState = { pollTimer: null, latest: null };

async function refreshUpdateStatus() {
  try {
    const v = await api('/api/version');
    $('#appVersion').textContent = v.version || '?';
  } catch (e) { /* ignore */ }
  // Wire button once
  const btn = $('#updateCheckBtn');
  if (btn && !btn._wired) {
    btn._wired = true;
    btn.onclick = checkForUpdate;
  }
  $('#updateStatus').textContent = '';
  $('#updateProgress').classList.add('hidden');
}

async function checkForUpdate() {
  const btn = $('#updateCheckBtn');
  const status = $('#updateStatus');
  btn.disabled = true;
  btn.textContent = 'Checking…';
  status.innerHTML = '';
  try {
    const r = await api('/api/update/check');
    if (!r.ok) {
      status.innerHTML = `<span style="color:var(--danger)">${r.error || 'Check failed'}</span>`;
      btn.disabled = false;
      btn.textContent = 'Check for updates';
      return;
    }
    updateState.latest = r;
    if (!r.has_update) {
      status.innerHTML = `<span style="color:var(--safe)">✓ Up to date</span> · v${r.current}`;
      btn.disabled = false;
      btn.textContent = 'Check for updates';
      return;
    }
    // Update available — swap the button into "Install vX.Y.Z"
    const notes = (r.notes || '').trim();
    status.innerHTML = `
      <div style="color:var(--text-1);font-weight:500;margin-bottom:4px">
        v${r.latest} available <span class="dim" style="font-weight:400">(you have v${r.current})</span>
      </div>
      ${notes ? `<details style="margin-top:6px"><summary style="cursor:pointer;color:var(--text-3);font-size:11.5px">Release notes</summary>
        <pre style="margin:6px 0 0;padding:8px;background:var(--surface-1);border-radius:6px;font-size:11px;line-height:1.5;white-space:pre-wrap;color:var(--text-2);max-height:160px;overflow:auto">${escapeAttr(notes)}</pre>
      </details>` : ''}
    `;
    btn.textContent = `Install v${r.latest}`;
    btn.disabled = false;
    btn.onclick = installUpdate;
  } catch (e) {
    status.innerHTML = `<span style="color:var(--danger)">${e.message}</span>`;
    btn.disabled = false;
    btn.textContent = 'Check for updates';
  }
}

async function installUpdate() {
  const btn = $('#updateCheckBtn');
  btn.disabled = true;
  btn.textContent = 'Installing…';
  $('#updateProgress').classList.remove('hidden');
  try {
    const r = await api('/api/update/install', { method: 'POST', body: JSON.stringify({ force: false }) });
    if (!r.ok) {
      $('#updateStatus').innerHTML = `<span style="color:var(--danger)">${r.error || 'Install failed'}</span>`;
      btn.disabled = false;
      btn.textContent = 'Try again';
      return;
    }
    pollUpdateStatus();
  } catch (e) {
    $('#updateStatus').innerHTML = `<span style="color:var(--danger)">${e.message}</span>`;
    btn.disabled = false;
    btn.textContent = 'Try again';
  }
}

function pollUpdateStatus() {
  clearInterval(updateState.pollTimer);
  updateState.pollTimer = setInterval(async () => {
    let s;
    try { s = await api('/api/update/status'); } catch (e) { return; }
    $('#updateProgressFill').style.width = (s.progress || 0) + '%';
    $('#updateProgressLabel').textContent = s.label || s.phase;
    if (s.phase === 'done') {
      clearInterval(updateState.pollTimer);
      updateState.pollTimer = null;
      $('#updateStatus').innerHTML = `
        <span style="color:var(--safe)">✓ Installed v${s.to_version}</span> · restart to apply
      `;
      const btn = $('#updateCheckBtn');
      btn.disabled = false;
      btn.textContent = 'Restart now';
      btn.onclick = restartAfterUpdate;
    } else if (s.phase === 'error') {
      clearInterval(updateState.pollTimer);
      updateState.pollTimer = null;
      $('#updateStatus').innerHTML = `<span style="color:var(--danger)">${s.error || 'Install failed'}</span>`;
      const btn = $('#updateCheckBtn');
      btn.disabled = false;
      btn.textContent = 'Try again';
    }
  }, 800);
}

async function restartAfterUpdate() {
  const btn = $('#updateCheckBtn');
  btn.disabled = true;
  btn.textContent = 'Restarting…';
  try {
    await api('/api/update/relaunch', { method: 'POST' });
    // Backend will exit in ~1.5s. Show a friendly notice.
    $('#updateStatus').innerHTML = `<span class="dim">App is restarting…</span>`;
  } catch (e) {
    // Once the backend dies, the fetch will fail — that's expected.
    $('#updateStatus').innerHTML = `<span class="dim">Restarting…</span>`;
  }
}

/* ── AI verdicts (Integrator) ─────────────────────────────────────────── */
async function refreshIntegratorStatus() {
  let s;
  try { s = await api('/api/integrator/status'); }
  catch (e) { return; }
  aiFeature.loaded = true;
  aiFeature.connected = !!s.connected;
  aiFeature.enabled = !!s.ai_verdicts_enabled;
  aiFeature.email = s.user_email || null;
  aiFeature.expiresAt = s.expires_at || null;
  renderIntegratorPanel();
  // Tell the rest of the UI (suggestion expansions, file detail) the gate
  // may have flipped.
  document.dispatchEvent(new CustomEvent('ai-feature-changed'));
}

function renderIntegratorPanel() {
  const statusEl = $('#integratorStatus');
  const actionsEl = $('#integratorActions');
  if (!statusEl || !actionsEl) return;

  if (!aiFeature.connected) {
    statusEl.innerHTML = `<span style="color:var(--text-3)">Not paired with Integrator.</span>`;
    actionsEl.innerHTML = `<button class="btn primary grow" id="integratorConnectBtn">Connect Integrator</button>`;
    $('#integratorConnectBtn').onclick = startIntegratorConnect;
    return;
  }

  const dotColor = aiFeature.enabled ? 'var(--safe)' : 'var(--text-3)';
  const stateLabel = aiFeature.enabled ? 'AI verdicts on' : 'AI verdicts off';
  const who = aiFeature.email ? ` · ${aiFeature.email}` : '';
  const days = aiFeature.expiresAt
    ? Math.max(0, Math.floor((aiFeature.expiresAt - Date.now() / 1000) / 86400))
    : null;
  const tokenNote = days !== null ? ` · token valid ${days}d` : '';
  statusEl.innerHTML = `
    <span style="display:inline-flex;align-items:center;gap:6px">
      <span style="display:inline-block;width:8px;height:8px;border-radius:100px;background:${dotColor}"></span>
      <span style="color:var(--text-2);font-weight:500">${stateLabel}</span>
    </span>
    <span style="color:var(--text-3)">${who}${tokenNote}</span>
  `;

  if (aiFeature.enabled) {
    actionsEl.innerHTML = `
      <button class="btn ghost grow" id="integratorDisableBtn">Disable AI verdicts</button>
      <button class="btn ghost" id="integratorDisconnectBtn">Disconnect</button>
    `;
    $('#integratorDisableBtn').onclick = () => setAiVerdicts(false);
  } else {
    actionsEl.innerHTML = `
      <button class="btn primary grow" id="integratorEnableBtn">Enable AI verdicts</button>
      <button class="btn ghost" id="integratorDisconnectBtn">Disconnect</button>
    `;
    $('#integratorEnableBtn').onclick = () => setAiVerdicts(true);
  }
  $('#integratorDisconnectBtn').onclick = disconnectIntegrator;
}

async function startIntegratorConnect() {
  const btn = $('#integratorConnectBtn');
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = 'Opening browser…';
  $('#integratorStatus').innerHTML =
    `<span style="color:var(--text-2)">Browser opening… sign in with your Integrator account.</span>`;
  try {
    const r = await api('/api/integrator/connect', { method: 'POST' });
    aiFeature.connected = !!r.connected;
    aiFeature.enabled = !!r.ai_verdicts_enabled;
    aiFeature.email = r.user_email || null;
    aiFeature.expiresAt = r.expires_at || null;
    if (r.connected) {
      const who = r.user_email ? ` as ${r.user_email}` : '';
      toast(`Paired with Integrator${who}. AI verdicts are now available.`, 'safe');
    } else {
      toast('Pairing failed.', 'danger');
    }
  } catch (e) {
    toast(`Pairing failed: ${e.message}`, 'danger');
  } finally {
    renderIntegratorPanel();
    document.dispatchEvent(new CustomEvent('ai-feature-changed'));
  }
}

async function setAiVerdicts(enabled) {
  try {
    const r = await api('/api/integrator/ai-verdicts', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    });
    aiFeature.enabled = !!r.ai_verdicts_enabled;
    toast(enabled ? 'AI verdicts on.' : 'AI verdicts off.', 'safe');
  } catch (e) {
    toast(`Could not change setting: ${e.message}`, 'danger');
  }
  renderIntegratorPanel();
  document.dispatchEvent(new CustomEvent('ai-feature-changed'));
}

async function disconnectIntegrator() {
  if (!confirm('Disconnect Integrator? Tokens will be deleted; you can re-pair any time.')) return;
  try {
    await api('/api/integrator/disconnect', { method: 'POST' });
    aiFeature.connected = false;
    aiFeature.enabled = false;
    aiFeature.email = null;
    aiFeature.expiresAt = null;
    aiFeature.verdicts.clear();
    toast('Disconnected.', 'safe');
  } catch (e) {
    toast(`Disconnect failed: ${e.message}`, 'danger');
  }
  renderIntegratorPanel();
  document.dispatchEvent(new CustomEvent('ai-feature-changed'));
}

async function pollOAuthStatus(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 1000));
    try {
      const s = await api('/api/oauth/login/status');
      if (s.status === 'ok' || s.status === 'error') return s;
    } catch (e) { /* keep polling */ }
  }
  return { status: 'timeout' };
}

async function refreshLLMStatus() {
  let llmS = {}, oauthS = {};
  try { [llmS, oauthS] = await Promise.all([api('/api/llm/status'), api('/api/oauth/status')]); }
  catch (e) { return; }
  // Track whether a legacy fallback (api_key or direct ChatGPT OAuth) is
  // configured. The file-detail "Ask AI" button stays available if so —
  // even when Integrator is off — for backwards compat.
  aiFeature.fallbackConfigured = !!(llmS.configured || oauthS.signed_in);
  applyAiFeatureGate();
  const oauthEl = $('#oauthStatus');
  if (oauthEl) {
    if (oauthS.signed_in) {
      const days = Math.floor(oauthS.expires_in_days || 0);
      oauthEl.innerHTML = `<span style="color:var(--safe)">✓ Signed in</span>${oauthS.email ? ` as ${oauthS.email}` : ''} · token valid ${days}d`;
      $('#oauthSignOut').classList.remove('hidden');
      $('#oauthLogin').textContent = 'Re-sign in';
    } else {
      oauthEl.innerHTML = `<span style="color:var(--text-3)">Not signed in.</span>`;
      $('#oauthSignOut').classList.add('hidden');
      $('#oauthLogin').textContent = 'Sign in with ChatGPT';
    }
  }
  const el = $('#llmStatus');
  if (el) {
    if (llmS.auth_mode === 'api_key' && llmS.key_preview) {
      el.innerHTML = `<span style="color:var(--safe)">✓ API key set</span> · ${llmS.model} · ${llmS.key_preview}`;
    } else {
      el.innerHTML = `<span style="color:var(--text-3)">No key saved.</span>`;
    }
  }
}

async function openStatExplainer(which) {
  $('#statExplainer').classList.remove('hidden');
  const body = $('#statExplainerBody');
  body.innerHTML = '<div class="empty" style="padding:40px;">Loading…</div>';
  const [disk, overview] = await Promise.all([
    api('/api/disk'),
    api('/api/overview'),
  ]);
  const homeBytes = overview.total_size || 0;
  const gap = disk.used - homeBytes;
  const homePct = disk.used ? Math.round(homeBytes / disk.used * 100) : 0;
  const gapPct = 100 - homePct;

  if (which === 'indexed') {
    $('#statExplainerTitle').textContent = 'What MacSweep indexes';
    body.innerHTML = `
      <div class="fd-section">
        <div class="explain-bar">
          <div class="explain-bar-segment indexed" style="width:${homePct}%" title="Indexed: ${bytes(homeBytes)}"></div>
          <div class="explain-bar-segment system" style="width:${gapPct}%" title="System: ${bytes(gap)}"></div>
        </div>
        <div class="explain-legend">
          <div><span class="dot" style="background:var(--accent)"></span> ${bytes(homeBytes)} indexed (${homePct}%)</div>
          <div><span class="dot" style="background:var(--text-3)"></span> ${bytes(gap)} system / not indexed (${gapPct}%)</div>
        </div>
      </div>
      <div class="fd-section">
        <div class="fd-section-label">What's included</div>
        <ul class="explain-list">
          <li><code>~/</code> — your entire home directory</li>
          <li>Caches, app data, projects, downloads, documents, media</li>
          <li>Hidden files like <code>~/.npm</code>, <code>~/.cargo</code>, <code>~/.cache</code></li>
        </ul>
      </div>
      <div class="fd-section">
        <div class="fd-section-label">What's deliberately skipped</div>
        <ul class="explain-list">
          <li><code>/System</code>, <code>/usr</code>, <code>/Library</code> (system paths) — not user data</li>
          <li><code>~/Library/Mobile Documents</code> — iCloud Drive cloud-only files</li>
          <li><code>~/Library/CloudStorage</code> — Dropbox/OneDrive cloud-only files</li>
          <li><code>~/Applications</code> if a separate volume</li>
          <li>The quarantine folder itself</li>
        </ul>
      </div>
      <div class="fd-section">
        <div class="fd-section-label">Why the gap exists</div>
        <p class="dim" style="font-size:13px;line-height:1.6;margin:0">
          macOS, system frameworks, installed apps, and unindexed cloud-storage placeholders all
          live outside your home directory and aren't safe to clean from a user app. The
          <strong>${bytes(gap)}</strong> in the gap is the OS itself plus things MacSweep
          can't (or shouldn't) touch.
        </p>
      </div>
    `;
  } else if (which === 'used') {
    $('#statExplainerTitle').textContent = 'Disk usage';
    body.innerHTML = `
      <div class="fd-section">
        <div class="fd-row"><span class="key">Total disk</span><span class="val">${bytes(disk.total)}</span></div>
        <div class="fd-row"><span class="key">Used</span><span class="val">${bytes(disk.used)}</span></div>
        <div class="fd-row"><span class="key">Free</span><span class="val">${bytes(disk.free)}</span></div>
      </div>
      <div class="fd-section">
        <div class="fd-section-label">Of the used space</div>
        <div class="explain-bar">
          <div class="explain-bar-segment indexed" style="width:${homePct}%"></div>
          <div class="explain-bar-segment system" style="width:${gapPct}%"></div>
        </div>
        <div class="explain-legend">
          <div><span class="dot" style="background:var(--accent)"></span> ${bytes(homeBytes)} in your home — MacSweep can clean</div>
          <div><span class="dot" style="background:var(--text-3)"></span> ${bytes(gap)} system + apps — outside our scope</div>
        </div>
      </div>
    `;
  }
}

function bindMasterCheckboxes() {
  const t1cb = $('#tier1SelectAll'), t2cb = $('#tier2SelectAll');
  if (t1cb) t1cb.onchange = () => {
    state.suggestions.filter(s => s.tier === 1).forEach(s => {
      if (t1cb.checked) state.selectedSuggestions.add(s.id);
      else state.selectedSuggestions.delete(s.id);
    });
    refreshSuggestionRows();
    renderCart();
  };
  if (t2cb) t2cb.onchange = () => {
    state.suggestions.filter(s => s.tier === 2).forEach(s => {
      if (t2cb.checked) state.selectedSuggestions.add(s.id);
      else state.selectedSuggestions.delete(s.id);
    });
    refreshSuggestionRows();
    renderCart();
  };
}

function refreshSuggestionRows() {
  $$('.suggestion-wrapper').forEach(w => {
    const id = w.dataset.id;
    const cb = w.querySelector('.sg-checkbox');
    const isSelected = state.selectedSuggestions.has(id);
    if (cb) cb.checked = isSelected;
    w.querySelector('.suggestion').classList.toggle('selected', isSelected);
  });
}

/* ── State views ──────────────────────────────────────────────────────── */
function showWelcome() {
  $('#welcome').classList.remove('hidden');
  $('#scanning').classList.add('hidden');
  $('#app').classList.add('hidden');
}
function showScanning() {
  $('#welcome').classList.add('hidden');
  $('#scanning').classList.remove('hidden');
  $('#app').classList.add('hidden');
}
function showAppShell() {
  $('#welcome').classList.add('hidden');
  $('#scanning').classList.add('hidden');
  $('#app').classList.remove('hidden');
}

/* ── Scan ─────────────────────────────────────────────────────────────── */
async function startScan() {
  await api('/api/scan', { method: 'POST' });
  showScanning();
  pollScan();
}

// Rolling scan-status messages — picked dynamically based on what we're seeing
const SCAN_MESSAGES = [
  { match: () => true,                                              text: 'Walking your home directory…' },
  { match: s => s.current?.includes('/Library/Caches'),             text: 'Scanning application caches…' },
  { match: s => s.current?.includes('/Library/Application Support'), text: 'Indexing app data…' },
  { match: s => s.current?.includes('/Downloads'),                  text: 'Sweeping Downloads…' },
  { match: s => s.current?.includes('/Documents'),                  text: 'Indexing Documents…' },
  { match: s => s.current?.includes('/Desktop'),                    text: 'Checking the Desktop…' },
  { match: s => s.current?.includes('/Pictures'),                   text: 'Counting your photos…' },
  { match: s => s.current?.includes('/Movies') || s.current?.endsWith('.mov') || s.current?.endsWith('.mp4'), text: 'Measuring videos…' },
  { match: s => s.current?.includes('node_modules'),                text: 'Found node_modules — they add up fast.' },
  { match: s => s.current?.includes('site-packages') || s.current?.includes('.venv'), text: 'Indexing Python environments…' },
  { match: s => s.current?.includes('/Trash'),                      text: 'Tallying the Trash…' },
  { match: s => s.current?.includes('Xcode') || s.current?.includes('CoreSimulator'), text: 'Inspecting Xcode artifacts…' },
  { match: s => s.current?.includes('Brave') || s.current?.includes('Chrome') || s.current?.includes('Safari'), text: 'Browsing the browser caches…' },
  { match: s => s.current?.includes('MobileSync'),                  text: 'Sizing iOS device backups…' },
];

function pickScanMessage(s, pct) {
  if (pct >= 95) return 'Almost there…';
  if (pct >= 75) return 'Wrapping up the long tail…';
  if (s.files_indexed > 500_000 && pct < 50) return 'Big drive — hang tight.';
  // Walk in reverse so more-specific later entries win
  for (let i = SCAN_MESSAGES.length - 1; i >= 0; i--) {
    if (SCAN_MESSAGES[i].match(s)) return SCAN_MESSAGES[i].text;
  }
  return 'Scanning…';
}

// Real progress: backend counts files in a fast pre-pass first, then walks.
// Phase 'counting' shows indeterminate bar. Phase 'indexing' shows real %.
let _lastMessage = '';

function realProgress(s) {
  if (s.phase === 'counting' || !s.total) return null;  // null → indeterminate
  return Math.min(99, Math.floor((s.files_indexed / s.total) * 100));
}

function pollScan() {
  clearInterval(state.pollTimer);
  _lastMessage = '';
  state.pollTimer = setInterval(async () => {
    const s = await api('/api/scan/status');

    // Phase: counting → indexing → warming → done
    let pct, label;
    if (s.phase === 'counting' || (!s.total && s.phase !== 'warming')) {
      pct = null;
      label = 'Counting files for accurate progress…';
    } else if (s.phase === 'warming') {
      // After scan finishes, warmup has its own progress
      const w = await api('/api/warmup/status').catch(() => null);
      pct = w?.progress ?? 100;
      // Map indexing→0-90% and warming→90-100% in the UI bar
      pct = 90 + Math.floor((pct / 100) * 10);
      label = w?.step ? `Optimizing: ${w.step}…` : 'Optimizing data…';
    } else {
      pct = Math.min(89, Math.floor((s.files_indexed / s.total) * 90));
      label = pickScanMessage(s, pct);
    }

    if (pct === null) {
      $('#scanPercent').textContent = '—';
      $('#scanBarFill').style.width = '15%';
      $('#scanBarFill').style.animation = 'pulse 1.4s ease-in-out infinite';
    } else {
      $('#scanPercent').textContent = pct + '%';
      $('#scanBarFill').style.width = pct + '%';
      $('#scanBarFill').style.animation = '';
    }
    $('#scanStat').textContent = s.files_indexed.toLocaleString() +
      (s.total ? ` / ${s.total.toLocaleString()}` : '');
    $('#scanSize').textContent = bytes(s.size);
    $('#scanElapsed').textContent = Math.floor(s.elapsed || 0) + 's';
    $('#scanCurrent').textContent = (s.current || '').replace(/^\/Users\/[^/]+/, '~');

    if (label !== _lastMessage) {
      _lastMessage = label;
      const el = $('#scanMessage');
      el.style.opacity = '0';
      setTimeout(() => { el.textContent = label; el.style.opacity = '1'; }, 200);
    }

    // Only finish when both scan AND warmup are done
    if (!s.running) {
      clearInterval(state.pollTimer);
      $('#scanPercent').textContent = '100%';
      $('#scanBarFill').style.width = '100%';
      $('#scanMessage').textContent = 'Ready';
      const overview = await api('/api/overview');
      setTimeout(() => showApp(overview), 350);
    }
  }, 500);
}

/* ── App entry ────────────────────────────────────────────────────────── */
// In-memory cache of API responses so module switches feel instant.
// Also persists to localStorage keyed by the active scan_id so reopening
// the app feels INSTANT — we render from localStorage before any fetch.
const _prefetchCache = new Map();
let _activeScanId = null;

function _lsKey(scanId, path) { return `macsweep:${scanId}:${path}`; }

function cacheGet(key) { return _prefetchCache.get(key); }
function _isEmptyValue(val) {
  if (val === null || val === undefined) return true;
  if (Array.isArray(val) && val.length === 0) return true;
  return false;
}
function cacheSet(key, val) {
  _prefetchCache.set(key, val);
  // Don't persist empty results — a transient empty (e.g., warmup not done yet)
  // would otherwise stick around on next launch and the UI would render zero
  // cards before the live fetch lands.
  if (_activeScanId !== null && !_isEmptyValue(val)) {
    try { localStorage.setItem(_lsKey(_activeScanId, key), JSON.stringify(val)); } catch (e) { /* quota? */ }
  }
  return val;
}
function cacheClear() {
  _prefetchCache.clear();
  // Also purge localStorage entries for non-active scan_ids so we don't grow forever
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k && k.startsWith('macsweep:') && _activeScanId !== null && !k.startsWith(`macsweep:${_activeScanId}:`)) {
        localStorage.removeItem(k);
      }
    }
  } catch (e) {}
}

function hydrateCacheFromLocalStorage(scanId) {
  // Fill _prefetchCache from localStorage entries for this scan_id.
  // Lets the UI render instantly on cold launch — no skeleton flash.
  if (scanId === null || scanId === undefined) return;
  _activeScanId = scanId;
  try {
    const prefix = `macsweep:${scanId}:`;
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(prefix)) {
        try {
          const path = k.slice(prefix.length);
          const val = JSON.parse(localStorage.getItem(k));
          if (val !== null && val !== undefined) _prefetchCache.set(path, val);
        } catch (e) {}
      }
    }
  } catch (e) {}
}

async function cachedApi(path) {
  // Stale-while-revalidate: serve cached data immediately and refresh in the
  // background. But treat empty arrays as a cache miss — those usually mean
  // "warmup hadn't written this yet" and we don't want to lock the UI on them.
  if (_prefetchCache.has(path)) {
    const cached = _prefetchCache.get(path);
    if (!_isEmptyValue(cached)) {
      api(path).then(fresh => { if (!_isEmptyValue(fresh)) cacheSet(path, fresh); }).catch(() => {});
      return cached;
    }
    _prefetchCache.delete(path);
  }
  const data = await api(path);
  if (!_isEmptyValue(data)) cacheSet(path, data);
  return data;
}

function prefetch(paths) {
  // Fire-and-forget. Each request fills the cache, so when the user navigates
  // there it's instant. Skip empty results so a transient empty doesn't lock
  // the UI on the next render.
  for (const p of paths) {
    if (_prefetchCache.has(p)) continue;
    fetch(p).then(r => r.json()).then(data => {
      if (!_isEmptyValue(data)) cacheSet(p, data);
    }).catch(() => {});
  }
}

function updateFreshnessChip(s) {
  const chip = document.getElementById('freshnessChip');
  const dot = document.getElementById('fcDot');
  const txt = document.getElementById('fcText');
  if (!chip || !dot || !txt) return;
  let display_state = s.state;
  if (s.is_stale && s.state === 'ready') display_state = 'stale';
  chip.dataset.state = display_state;
  // Human label
  let text;
  if (s.state === 'no_data') text = 'No index — click Scan';
  else if (s.state === 'scanning') text = `Scanning… ${s.progress}%`;
  else if (s.state === 'warming') text = `Optimizing… ${s.progress}%`;
  else if (s.state === 'ready_scanning') text = `Re-indexing… ${s.progress}%`;
  else if (s.state === 'ready_warming' || s.state === 'ready_warming_bg') text = `Warming… ${s.progress}%`;
  else if (s.is_stale) text = 'Stale — Rescan recommended';
  else if (s.last_finished_at) {
    const ago_s = Math.floor((Date.now()/1000) - s.last_finished_at);
    text = `Up to date · ${ago(s.last_finished_at)}`;
  } else text = 'Up to date';
  txt.textContent = text;
}

async function showApp(overview) {
  state.overview = overview;
  showAppShell();
  renderSidebar(overview);
  // Initial freshness chip render
  api('/api/status').then(updateFreshnessChip).catch(() => {});
  // Wait for the active module to finish rendering BEFORE returning, so the
  // splash doesn't fade until Smart Scan actually has its content.
  await renderModule(state.module);
  refreshQuarantineBadge();
  // …and prefetch every other module's main data in the background.
  setTimeout(() => prefetch([
    '/api/suggestions',
    '/api/sunburst?depth=6',
    '/api/projects?limit=40',
    '/api/forgotten?min_size_mb=100&min_age_days=365&limit=200',
    '/api/smart/picks?limit=120&min_size_mb=10',
    '/api/sweep/queue?limit=300',
    '/api/quarantine',
  ]), 100);
}

function renderSidebar(o) {
  $('#sidebarTotal').textContent = bytes(o.total_size);
  $('#sidebarFiles').textContent = (o.total_files || 0).toLocaleString();
  $('#sidebarLast').textContent = o.last_scan?.finished_at
    ? `Scanned ${ago(o.last_scan.finished_at)}`
    : 'Never scanned';
  syncNav();
}

function syncNav() {
  $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.module === state.module));
}

async function switchModule(name) {
  state.module = name;
  syncNav();
  $$('.module').forEach(m => m.classList.toggle('hidden', m.dataset.module !== name));
  await renderModule(name);
  $('.canvas').scrollTo({ top: 0, behavior: 'instant' });
}

async function renderModule(name) {
  if (name === 'scan') return renderScan();
  if (name === 'map') return renderMap();
  if (name === 'files') return renderFiles();
  if (name === 'sweep') return renderSweep();
  if (name === 'audit') return renderAudit();
  if (name === 'redundancies') return renderRedundancies();
  if (name === 'quarantine') return renderQuarantine();
}

/* ── Module: Smart Scan ───────────────────────────────────────────────── */
async function renderScan() {
  // If hydration gave us a non-empty cache, render those cards instantly.
  // Otherwise show skeletons until the live fetch lands. Empty arrays are
  // never stored in the cache, so they can't lock the UI on a blank state.
  const sync = cacheGet('/api/suggestions');
  if (sync && Array.isArray(sync) && sync.length) {
    state.suggestions = sync;
    _renderSuggestions(sync);
  } else if (!state.suggestions || !state.suggestions.length) {
    $('#tier1List').innerHTML = skeletonSuggestionRows(3);
    $('#tier2List').innerHTML = skeletonSuggestionRows(3);
  }

  cachedApi('/api/disk').then(d => {
    if (!d) return;
    $('#statUsed').textContent = bytes(d.used);
    $('#statRecovered').textContent = bytes(d.recovered);
  }).catch(e => console.error('disk fetch failed:', e));
  cachedApi('/api/overview').then(o => {
    if (o) {
      $('#statFiles').textContent = (o.total_files || 0).toLocaleString();
      $('#statIndexed').textContent = bytes(o.total_size || 0);
    }
  }).catch(e => console.error('overview fetch failed:', e));

  let data;
  try {
    data = await cachedApi('/api/suggestions');
  } catch (e) {
    console.error('suggestions fetch failed:', e);
    data = [];
  }
  state.suggestions = Array.isArray(data) ? data : [];
  _renderSuggestions(state.suggestions);
}

function _renderSuggestions(data) {
  const tier1 = data.filter(s => s && s.tier === 1);
  const tier2 = data.filter(s => s && s.tier === 2);

  const recoverable = tier1.reduce((a, b) => a + (b.size || 0), 0);
  const total = data.reduce((a, b) => a + (b.size || 0), 0);
  const [num, unit] = bytesNum(recoverable || total);
  $('#heroNumber').textContent = num;
  $('#heroUnit').textContent = unit;
  $('#heroAreas').textContent = data.length || '0';

  $('#tier1List').innerHTML = '';
  $('#tier2List').innerHTML = '';
  try {
    tier1.forEach(s => $('#tier1List').appendChild(suggestionCard(s)));
    tier2.forEach(s => $('#tier2List').appendChild(suggestionCard(s)));
  } catch (e) {
    console.error('suggestionCard render failed:', e);
    $('#tier1List').innerHTML = '<div class="empty">Render error — try Cmd+Q and relaunch.</div>';
  }

  if (!tier1.length) $('#tier1List').innerHTML = '<div class="empty">Nothing safely recoverable right now.</div>';
  if (!tier2.length) $('#tier2List').innerHTML = '<div class="empty">No personal-files cleanup to review.</div>';

  bindMasterCheckboxes();
  syncMasterCheckboxes();
}

// Track which suggestion is currently expanded (only one at a time)
let _expandedSuggestion = null;

function suggestionCard(s) {
  const wrapper = document.createElement('div');
  wrapper.className = 'suggestion-wrapper';
  wrapper.dataset.id = s.id;

  const head = document.createElement('div');
  head.className = 'suggestion';
  if (state.selectedSuggestions.has(s.id)) head.classList.add('selected');
  head.innerHTML = `
    <label class="sg-check-wrap" onclick="event.stopPropagation()">
      <input type="checkbox" class="sg-checkbox" ${state.selectedSuggestions.has(s.id) ? 'checked' : ''} />
    </label>
    <div class="sg-meta">
      <div class="sg-title">${s.title}</div>
      <div class="sg-detail">${s.detail}</div>
    </div>
    <div class="sg-size">${bytes(s.size)}</div>
    <div class="sg-chevron">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="m6 9 6 6 6-6"/></svg>
    </div>
  `;
  wrapper.appendChild(head);

  const body = document.createElement('div');
  body.className = 'suggestion-body';
  wrapper.appendChild(body);

  // Checkbox toggles selection (but doesn't expand)
  const cb = head.querySelector('.sg-checkbox');
  cb.onchange = () => {
    if (cb.checked) state.selectedSuggestions.add(s.id);
    else state.selectedSuggestions.delete(s.id);
    head.classList.toggle('selected', cb.checked);
    renderCart();
    syncMasterCheckboxes();
  };

  // Click row body (not checkbox) toggles expansion
  head.onclick = (e) => {
    if (e.target.closest('.sg-check-wrap')) return;
    toggleExpansion(s, wrapper, body);
  };

  return wrapper;
}

// Smart truncation thresholds: show top N rows; bundle the rest.
const INLINE_TOP_N = 12;
const INLINE_MAX_ROWS = 60;  // hard cap even after "show all"

async function toggleExpansion(s, wrapper, body) {
  if (_expandedSuggestion && _expandedSuggestion !== wrapper) {
    _expandedSuggestion.classList.remove('expanded');
  }
  if (wrapper.classList.contains('expanded')) {
    wrapper.classList.remove('expanded');
    _expandedSuggestion = null;
    return;
  }
  wrapper.classList.add('expanded');
  _expandedSuggestion = wrapper;

  body.innerHTML = `
    <div class="sg-body-head">
      <label class="check"><input type="checkbox" class="sg-body-select-all" /> <span>Select all visible</span></label>
      <span class="dim sg-body-summary">Loading…</span>
      <span class="grow"></span>
      <button class="btn ghost sg-body-add" disabled>Add to plan</button>
      <button class="btn primary sg-body-quarantine" disabled>Quarantine selected</button>
    </div>
    <div class="sg-body-list">${skeletonGroupRows(4)}</div>
  `;
  const groups = await fetchGroups(s.action);
  if (!groups.length) {
    body.querySelector('.sg-body-list').innerHTML = '<div class="empty">No matching items.</div>';
    return;
  }

  // No pre-selection — let the user decide explicitly what to remove.
  // (Previously auto-selecting all regenerable groups was confusing: clicking
  // "Spotify only" actually toggled Spotify OFF since everything was pre-checked.)
  const sel = new Map();
  let expanded = false;  // "Show all" was clicked?

  function visibleSet() {
    if (expanded) return groups.slice(0, INLINE_MAX_ROWS);
    return groups.slice(0, INLINE_TOP_N);
  }
  function tailSet() {
    if (expanded) return groups.slice(INLINE_MAX_ROWS);
    return groups.slice(INLINE_TOP_N);
  }

  function renderList() {
    const list = body.querySelector('.sg-body-list');
    list.innerHTML = '';
    const visible = visibleSet();
    const tail = tailSet();
    visible.forEach(g => list.appendChild(inlineGroupRow(g, sel, syncBody)));

    if (tail.length) {
      const tailSize = tail.reduce((a, g) => a + g.size, 0);
      const tailFiles = tail.reduce((a, g) => a + g.files, 0);
      const tailSelected = tail.every(g => sel.has(g.path));
      const tailRow = document.createElement('div');
      tailRow.className = 'group-row gr-tail-row';
      if (tailSelected) tailRow.classList.add('selected');
      tailRow.innerHTML = `
        <input type="checkbox" class="gr-checkbox" ${tailSelected ? 'checked' : ''} />
        <div class="gr-meta">
          <div class="gr-name">${tail.length} smaller items</div>
          <div class="gr-detail">${tailFiles.toLocaleString()} files combined · select to include all in plan</div>
        </div>
        <div class="gr-size">${bytes(tailSize)}</div>
        <button class="gr-view" data-act="show-all">${expanded ? 'Hide tail' : 'Show all'}</button>
      `;
      const cb = tailRow.querySelector('input');
      cb.onchange = () => {
        if (cb.checked) tail.forEach(g => sel.set(g.path, g));
        else tail.forEach(g => sel.delete(g.path));
        tailRow.classList.toggle('selected', cb.checked);
        syncBody();
      };
      tailRow.querySelector('[data-act="show-all"]').onclick = (e) => {
        e.stopPropagation();
        expanded = !expanded;
        renderList();
      };
      list.appendChild(tailRow);
    }
  }

  function syncBody() {
    const items = Array.from(sel.values());
    const totalSize = items.reduce((a, x) => a + x.size, 0);
    const totalFiles = items.reduce((a, x) => a + x.files, 0);
    body.querySelector('.sg-body-summary').textContent =
      items.length
        ? `${items.length} of ${groups.length} groups · ${totalFiles.toLocaleString()} files · ${bytes(totalSize)}`
        : `0 of ${groups.length} selected`;
    const qBtn = body.querySelector('.sg-body-quarantine');
    const aBtn = body.querySelector('.sg-body-add');
    qBtn.disabled = items.length === 0;
    aBtn.disabled = items.length === 0;
    qBtn.textContent = items.length ? `Quarantine ${items.length} (${bytes(totalSize)})` : 'Quarantine selected';
    aBtn.textContent = items.length ? `Add ${items.length} to plan` : 'Add to plan';
    const sa = body.querySelector('.sg-body-select-all');
    if (sa) sa.checked = sel.size === groups.length;
  }

  renderList();
  syncBody();
  body.querySelector('.sg-body-select-all').onchange = (e) => {
    if (e.target.checked) groups.forEach(g => sel.set(g.path, g));
    else sel.clear();
    renderList();
    syncBody();
  };
  // "Add to plan" — accumulate in cart for a multi-suggestion sweep
  body.querySelector('.sg-body-add').onclick = () => {
    sel.forEach((g, root) => state.selectedGroups.set(root, g));
    renderCart();
    toast(`Added ${sel.size} ${sel.size === 1 ? 'group' : 'groups'} to plan`, 'safe');
  };
  // "Quarantine selected" — pass group ROOT paths, not individual files.
  // shutil.move on a directory is ONE rename op (~1ms on same volume) instead
  // of N moves of N files (49k files = ~60s for the Spotify cache). Massive win.
  body.querySelector('.sg-body-quarantine').onclick = async () => {
    if (!sel.size) return;
    const btn = body.querySelector('.sg-body-quarantine');
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Quarantining…';
    try {
      await doQuarantine(Array.from(sel.keys()));
    } finally {
      btn.disabled = false;
      btn.textContent = orig;
    }
  };
}

function inlineGroupRow(g, sel, onChange) {
  const wrapper = document.createElement('div');
  wrapper.className = 'group-row-wrapper';

  const row = document.createElement('div');
  row.className = 'group-row';
  if (sel.has(g.path)) row.classList.add('selected');
  const kind = g.kind || inferKind(g.path);
  const displayName = g.display_name || g.name;
  const reason = g.reason || '';
  row.innerHTML = `
    <input type="checkbox" class="gr-checkbox" ${sel.has(g.path) ? 'checked' : ''} />
    <div class="gr-meta">
      <div class="gr-name">${displayName}</div>
      ${reason ? `<div class="gr-reason">${reason}</div>` : ''}
      <div class="gr-detail">${g.files.toLocaleString()} files · ${kind}${g.last_touched ? ` · ${ago(g.last_touched)}` : ''}</div>
      <div class="gr-path mono">${shortPath(g.path)}</div>
      <div class="gr-verdict-slot"></div>
    </div>
    <div class="gr-size">${bytes(g.size)}</div>
    <div class="gr-actions">
      <button class="ask-sweeper-btn gr-ask ${aiActive() ? '' : 'hidden'}" title="AI verdict on this whole folder">
        <span class="glyph">◐</span> Ask Sweeper
      </button>
      <button class="gr-view" title="Show files inside">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12s3-7 9-7 9 7 9 7-3 7-9 7-9-7-9-7"/><circle cx="12" cy="12" r="3"/></svg>
        View
      </button>
    </div>
  `;

  const filesPanel = document.createElement('div');
  filesPanel.className = 'gr-files hidden';
  wrapper.appendChild(row);
  wrapper.appendChild(filesPanel);

  const cb = row.querySelector('.gr-checkbox');
  cb.onchange = () => {
    if (cb.checked) sel.set(g.path, g);
    else sel.delete(g.path);
    row.classList.toggle('selected', cb.checked);
    onChange();
  };

  row.querySelector('.gr-view').onclick = async (e) => {
    e.stopPropagation();
    const isOpen = !filesPanel.classList.contains('hidden');
    if (isOpen) { filesPanel.classList.add('hidden'); return; }
    filesPanel.classList.remove('hidden');
    filesPanel.innerHTML = '<div class="dim" style="padding:12px 16px">Loading…</div>';
    const files = await api(`/api/group/files?root=${encodeURIComponent(g.path)}`);
    if (!files.length) {
      filesPanel.innerHTML = '<div class="dim" style="padding:12px 16px">No files inside.</div>';
      return;
    }
    // Show top 30 — paths only (sizes would need an extra API)
    const items = files.slice(0, 30);
    filesPanel.innerHTML = `
      <div class="gr-files-head">${files.length.toLocaleString()} files inside${files.length > 30 ? ' — showing top 30' : ''}</div>
      ${items.map(p => `<div class="gr-file-row" data-path="${escapeAttr(p)}">
        <span class="gr-file-name mono">${shortPath(p)}</span>
        <button class="gr-file-reveal" title="Reveal in Finder" data-path="${escapeAttr(p)}">↗</button>
      </div>`).join('')}
    `;
    filesPanel.querySelectorAll('.gr-file-row').forEach(fr => {
      fr.onclick = e => {
        if (e.target.closest('.gr-file-reveal')) return;
        openFileDetail(fr.dataset.path);
      };
    });
    filesPanel.querySelectorAll('.gr-file-reveal').forEach(b => {
      b.onclick = e => {
        e.stopPropagation();
        api('/api/file/reveal', { method: 'POST', body: JSON.stringify({ path: b.dataset.path }) });
      };
    });
  };

  // Ask Sweeper on the whole group
  const askBtn = row.querySelector('.gr-ask');
  if (askBtn) {
    askBtn.onclick = (e) => {
      e.stopPropagation();
      askGroupVerdictFor(g.path, askBtn, row.querySelector('.gr-verdict-slot'));
    };
  }
  // If we already cached a verdict for this path in-session, show it.
  const existing = aiFeature.verdicts.get(g.path);
  if (existing && existing.ok) {
    row.querySelector('.gr-verdict-slot').innerHTML = `
      <span class="row-verdict">
        <span class="row-verdict-chip" data-verdict="${existing.verdict}">${existing.verdict}</span>
        <span class="row-verdict-reason" title="${escapeAttr(existing.reason || '')}">${existing.reason || ''}</span>
        <span style="color:var(--text-3);font-size:10.5px">${existing.confidence}%</span>
      </span>
    `;
    askBtn?.classList.add('hidden');
  }

  // Click row body (not checkbox, view, or ask) toggles checkbox
  row.onclick = (e) => {
    if (e.target.tagName === 'INPUT') return;
    if (e.target.closest('.gr-view')) return;
    if (e.target.closest('.gr-ask')) return;
    cb.checked = !cb.checked;
    cb.dispatchEvent(new Event('change'));
  };
  return wrapper;
}

function syncMasterCheckboxes() {
  const t1 = state.suggestions.filter(s => s.tier === 1);
  const t2 = state.suggestions.filter(s => s.tier === 2);
  const t1Sel = t1.filter(s => state.selectedSuggestions.has(s.id)).length;
  const t2Sel = t2.filter(s => state.selectedSuggestions.has(s.id)).length;
  const t1cb = $('#tier1SelectAll'), t2cb = $('#tier2SelectAll');
  if (t1cb) { t1cb.checked = t1.length > 0 && t1Sel === t1.length; t1cb.indeterminate = t1Sel > 0 && t1Sel < t1.length; }
  if (t2cb) { t2cb.checked = t2.length > 0 && t2Sel === t2.length; t2cb.indeterminate = t2Sel > 0 && t2Sel < t2.length; }
}

function decorateSuggestionsWithVerdicts() {
  // No-op — AI verdict feature removed per user feedback.
}

function toggleSuggestion(s) {
  if (state.selectedSuggestions.has(s.id)) {
    state.selectedSuggestions.delete(s.id);
  } else {
    state.selectedSuggestions.add(s.id);
  }
  // sync view
  $$(`.suggestion[data-id="${s.id}"]`).forEach(el => el.classList.toggle('selected', state.selectedSuggestions.has(s.id)));
  renderCart();
}

/* ── Module: Storage Map ──────────────────────────────────────────────── */
async function renderMap() {
  await renderSunburst();
  // Rebuild legend from actual sections in the sunburst (now that colors are assigned).
  const legend = $('#mapLegend');
  legend.innerHTML = '';
  Array.from(sectionColorMap.entries()).slice(0, 8).forEach(([name, color]) => {
    const el = document.createElement('span');
    el.className = 'lg';
    el.style.setProperty('--c', color);
    el.textContent = name;
    legend.appendChild(el);
  });
}

// Vivid palette assigned deterministically to each top-level home subfolder.
const SECTION_PALETTE = [
  '#c4b5fd', '#4ade80', '#facc15', '#f472b6', '#60a5fa',
  '#fb923c', '#a78bfa', '#22d3ee', '#a3e635', '#fda4af',
  '#818cf8', '#f59e0b', '#34d399',
];
const sectionColorMap = new Map();
function sectionColor(name) {
  if (!sectionColorMap.has(name)) {
    sectionColorMap.set(name, SECTION_PALETTE[sectionColorMap.size % SECTION_PALETTE.length]);
  }
  return sectionColorMap.get(name);
}

// Walk into /Users/<you>/ so the boring outer rings vanish.
function findHomeNode(node) {
  let cur = node;
  for (const seg of ['Users']) {
    if (!cur.children) return node;
    const next = cur.children.find(c => c.name === seg);
    if (!next) return node;
    cur = next;
  }
  // Pick the largest user dir (typically the only one)
  if (!cur.children?.length) return node;
  return cur.children.reduce((a, b) => a.size > b.size ? a : b);
}

let _sunburstFocus = null;  // node that the sunburst is rooted on
let _sunburstSelected = null;  // currently clicked/highlighted slice

async function renderSunburst() {
  const container = $('#sunburst');
  container.innerHTML = `
    <div class="skel skel-block" style="width:540px;height:540px;border-radius:50%;animation:skel-shimmer 1.6s ease-in-out infinite;background:linear-gradient(90deg,var(--surface-2),var(--surface-3),var(--surface-2));background-size:200% 100%"></div>
  `;
  sectionColorMap.clear();
  const raw = await cachedApi('/api/sunburst?depth=6');
  const data = findHomeNode(raw);
  _sunburstFocus = data;
  _sunburstSelected = null;
  drawSunburst();
}

function drawSunburst() {
  // Re-renders the sunburst with _sunburstFocus as the root. Cheap to call
  // (~50 SVG paths). Used for click-to-drill-in instead of D3 zoom transitions
  // which were causing UI freezes.
  const container = $('#sunburst');
  container.innerHTML = '';

  const size = 600;
  const radius = size / 2;

  const root = d3.hierarchy(_sunburstFocus)
    .sum(d => d.children ? 0 : d.size)
    .sort((a, b) => b.value - a.value);
  d3.partition().size([2 * Math.PI, radius - 4])(root);

  const arc = d3.arc()
    .startAngle(d => d.x0).endAngle(d => d.x1)
    .innerRadius(d => d.y0).outerRadius(d => d.y1 - 1.5)
    .padAngle(0.005).padRadius(radius / 2);

  const svg = d3.select(container).append('svg')
    .attr('viewBox', `${-radius} ${-radius} ${size} ${size}`)
    .attr('width', size).attr('height', size);

  const colorFor = d => {
    let n = d; while (n.depth > 1 && n.parent) n = n.parent;
    return sectionColor(n.data.name);
  };
  const baseOpacity = d => 0.7 + 0.3 * (1 - (d.depth - 1) / 3);
  const visibleDepth = 4;

  svg.append('g').selectAll('path')
    .data(root.descendants().filter(d => d.depth > 0 && d.depth <= visibleDepth))
    .join('path')
    .attr('d', arc)
    .attr('fill', d => {
      const base = d3.color(colorFor(d));
      const lighten = (d.depth - 1) * -0.25;
      return lighten < 0 ? base.darker(-lighten) : base.brighter(lighten);
    })
    .attr('opacity', baseOpacity)
    .style('cursor', 'pointer')
    .style('transition', 'opacity .12s ease, stroke-width .12s ease')
    .on('mouseenter', function(_e, d) {
      d3.select(this).attr('opacity', 1).attr('stroke', 'rgba(255,255,255,.5)').attr('stroke-width', 1.5);
      if (!_mapStuck) updateMapDetail(d, false);
    })
    .on('mouseleave', function(_e, d) {
      const isSel = _sunburstSelected && _sunburstSelected.data === d.data;
      d3.select(this).attr('opacity', isSel ? 1 : baseOpacity(d))
        .attr('stroke', isSel ? '#fff' : null)
        .attr('stroke-width', isSel ? 2 : null);
      if (!_mapStuck) resetMapDetail();
    })
    .on('click', function(_e, d) {
      _sunburstSelected = d;
      _mapStuck = true;
      // Visual: clear any prior selection mark, mark this one
      svg.selectAll('path').attr('stroke', null).attr('stroke-width', null);
      d3.select(this).attr('stroke', '#fff').attr('stroke-width', 2).attr('opacity', 1);
      updateMapDetail(d, true);
    });

  // Always-visible labels on inner rings (cheap — runs once)
  const labelable = root.descendants().filter(d => {
    if (d.depth < 1 || d.depth > 2) return false;
    const arcLength = (d.x1 - d.x0) * ((d.y0 + d.y1) / 2);
    return arcLength > 28;
  });
  svg.append('g').attr('class', 'sb-labels')
    .selectAll('text').data(labelable).join('text')
    .attr('transform', d => {
      const angle = (d.x0 + d.x1) / 2;
      const r = (d.y0 + d.y1) / 2;
      const x = Math.sin(angle) * r, y = -Math.cos(angle) * r;
      let rot = (angle * 180 / Math.PI) - 90;
      if (rot > 90) rot -= 180;
      if (rot < -90) rot += 180;
      return `translate(${x},${y}) rotate(${rot})`;
    })
    .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
    .attr('class', 'sb-slice-label')
    .text(d => {
      const arcPx = (d.x1 - d.x0) * ((d.y0 + d.y1) / 2);
      const maxChars = Math.max(2, Math.floor(arcPx / 7));
      const name = d.data.name;
      return name.length > maxChars ? name.slice(0, maxChars - 1) + '…' : name;
    });

  // Center — clickable to zoom out one level
  const centerGroup = svg.append('g').style('cursor', 'pointer')
    .on('click', () => sunburstZoomOut());
  centerGroup.append('circle').attr('r', 60).attr('fill', 'transparent');
  centerGroup.append('text').attr('class', 'sb-center').attr('y', -4).text(bytes(root.value));
  centerGroup.append('text').attr('class', 'sb-center dim').attr('y', 16)
    .text(_sunburstFocus.name && _sunburstFocus.name !== '/' ? _sunburstFocus.name : 'home');
}

function sunburstDrillInto(d) {
  // d is a d3 hierarchy node from the current sunburst. Walk up assigning
  // __parent on each ancestor's data so zoom-out works at every level.
  if (!d || !d.data) return;
  let n = d;
  while (n.parent) {
    if (!n.data.__parent) n.data.__parent = n.parent.data;
    n = n.parent;
  }
  _sunburstFocus = d.data;
  _sunburstSelected = null;
  drawSunburst();
  resetMapDetail();
  _mapStuck = false;
}

function sunburstZoomOut() {
  if (!_sunburstFocus || !_sunburstFocus.__parent) {
    _mapStuck = false;
    resetMapDetail();
    return;
  }
  _sunburstFocus = _sunburstFocus.__parent;
  _sunburstSelected = null;
  drawSunburst();
  resetMapDetail();
  _mapStuck = false;
}

let _mapStuck = false;
function resetMapDetail() {
  $('#mapDetail').innerHTML = '<div class="map-detail-empty">Hover a slice to inspect.<br/>Click to add to plan.</div>';
}

function ancestorPath(d) {
  // d is rooted at home node. Prefix with ~ for clarity.
  const segs = d.ancestors().map(n => n.data.name).reverse().slice(1);
  return '~/' + segs.join('/');
}

function updateMapDetail(d, sticky = false) {
  const displayPath = ancestorPath(d);
  const absPath = displayPath.replace(/^~/, '/Users/' + (window.HOME_USER || 'beauregard'));
  const parentSize = d.parent?.value || d.value;
  const pctOfParent = parentSize ? Math.round((d.value / parentSize) * 100) : 100;

  // Breadcrumb: clickable chain from root down to current node
  const crumbs = d.ancestors().reverse();
  const breadcrumb = crumbs.map((n, i) => {
    const label = i === 0 ? '~' : n.data.name;
    return `<span class="md-crumb" data-depth="${n.depth}">${label}</span>`;
  }).join('<span class="md-crumb-sep">›</span>');

  // Top children sorted by size (Finder-column style)
  const childRows = d.children
    ? d.children.slice(0, 12).map(c => {
        const cPct = Math.round((c.value / d.value) * 100);
        return `
          <div class="md-child" data-name="${escapeAttr(c.data.name)}">
            <div class="md-child-name">${c.data.name}</div>
            <div class="md-child-bar"><div class="md-child-bar-fill" style="width:${cPct}%"></div></div>
            <div class="md-child-size">${bytes(c.value)}</div>
          </div>
        `;
      }).join('')
    : '';

  $('#mapDetail').innerHTML = `
    <div class="md-breadcrumb">${breadcrumb}</div>
    <div class="md-summary">
      <div class="md-size-row">
        <div class="md-size">${bytes(d.value)}</div>
        ${d.parent ? `<div class="md-pct">${pctOfParent}% of ${d.parent.data.name === '/' || d.parent === d.parent.parent ? '~' : d.parent.data.name}</div>` : ''}
      </div>
      <div class="md-meta-row">
        ${d.children ? `<span>${d.children.length} ${d.children.length === 1 ? 'child' : 'children'}</span>` : `<span>file</span>`}
        <span>·</span>
        <span class="mono">${shortPath(absPath)}</span>
      </div>
    </div>
    ${childRows ? `
      <div class="md-section-label">Largest children</div>
      <div class="md-children">${childRows}</div>
    ` : ''}
    <div class="md-verdict-slot" id="mapVerdictSlot"></div>
    <div class="md-actions">
      <button class="ask-sweeper-btn ${aiActive() ? '' : 'hidden'}" id="mapAskSweeper" title="AI verdict on this folder">
        <span class="glyph">◐</span> Ask Sweeper
      </button>
      <button class="btn ghost" id="mapReveal">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 20V6a2 2 0 0 1 2-2h6l2 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/></svg>
        Reveal
      </button>
      ${d.children && d.children.length ? `
        <button class="btn ghost" id="mapDrillIn">
          Drill in
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="m9 18 6-6-6-6"/></svg>
        </button>
      ` : ''}
      <button class="btn primary grow" id="mapAdd">
        Add ${bytes(d.value)} to plan
      </button>
    </div>
  `;

  // Sweeper verdict on this sunburst node (cached if we've seen it)
  const mapAsk = $('#mapAskSweeper');
  const mapSlot = $('#mapVerdictSlot');
  if (mapAsk) {
    const cachedV = aiFeature.verdicts.get(absPath);
    if (cachedV && cachedV.ok) {
      mapSlot.innerHTML = `
        <span class="row-verdict">
          <span class="row-verdict-chip" data-verdict="${cachedV.verdict}">${cachedV.verdict}</span>
          <span class="row-verdict-reason" title="${escapeAttr(cachedV.reason || '')}">${cachedV.reason || ''}</span>
          <span style="color:var(--text-3);font-size:10.5px">${cachedV.confidence}%</span>
        </span>
      `;
      mapAsk.classList.add('hidden');
    }
    mapAsk.onclick = () => askGroupVerdictFor(absPath, mapAsk, mapSlot);
  }

  // Crumb navigation: click a crumb to focus that ancestor
  $$('#mapDetail .md-crumb').forEach(c => {
    c.onclick = () => {
      const depth = parseInt(c.dataset.depth, 10);
      const target = d.ancestors().find(n => n.depth === depth);
      if (target) updateMapDetail(target, true);
    };
  });
  // Click a child row to drill in
  $$('#mapDetail .md-child').forEach(row => {
    row.onclick = () => {
      const name = row.dataset.name;
      const child = d.children?.find(c => c.data.name === name);
      if (child) updateMapDetail(child, true);
    };
  });
  $('#mapReveal').onclick = () => {
    api('/api/file/reveal', { method: 'POST', body: JSON.stringify({ path: absPath }) });
  };
  const drillBtn = $('#mapDrillIn');
  if (drillBtn) drillBtn.onclick = () => sunburstDrillInto(d);
  $('#mapAdd').onclick = async () => {
    const files = await api(`/api/group/files?root=${encodeURIComponent(absPath)}`);
    if (!files.length) {
      toast('No indexed files at that path', 'danger');
      return;
    }
    state.selectedGroups.set(absPath, {
      name: displayPath.split('/').pop() || displayPath,
      path: absPath,
      size: d.value,
      files: files.length,
      regenerable: false,
    });
    renderCart();
    toast(`Added ${displayPath.split('/').pop() || displayPath} · ${files.length.toLocaleString()} files`, 'safe');
  };
}

async function openMapBrowser(absPath, displayPath, totalSize) {
  $('#drawerTitle').textContent = displayPath.split('/').pop() || displayPath;
  $('#drawerSub').textContent = `${displayPath} · ${bytes(totalSize)}`;
  $('#drawer').classList.remove('hidden');
  const list = $('#drawerList');
  list.innerHTML = '<div class="empty" style="padding:40px;">Loading files…</div>';
  drawerSelected.clear();

  // Fetch files under this path, render top-100 by size as file rows.
  const files = await api(`/api/group/files?root=${encodeURIComponent(absPath)}`);
  if (!files.length) {
    list.innerHTML = '<div class="empty">No indexed files here.</div>';
    return;
  }
  // Need size info per file — fetch from index by path. Use category endpoint workaround: pull from sunburst children if possible. Simpler: just show paths.
  list.innerHTML = '';
  const top = files.slice(0, 200);
  top.forEach(p => {
    const f = { path: p, size: 0, mtime: 0, atime: 0, category: '' };
    list.appendChild(fileRow(f));
  });

  $('#drawerQuarantine').textContent = `Add all ${files.length} to plan`;
  $('#drawerQuarantine').disabled = false;
  $('#drawerQuarantine').onclick = () => {
    state.selectedGroups.set(absPath, {
      name: displayPath.split('/').pop() || displayPath,
      path: absPath,
      size: totalSize,
      files: files.length,
      regenerable: false,
    });
    $('#drawer').classList.add('hidden');
    renderCart();
  };
}

/* ── Module: Files ────────────────────────────────────────────────────── */
async function renderFiles() {
  await switchFilesTab(state.filesTab);
}

async function switchFilesTab(tab) {
  state.filesTab = tab;
  $$('#filesTabs .tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  const c = $('#filesContent');
  c.innerHTML = skeletonRows(6);
  if (tab === 'smart') return renderSmartTab(c);
  if (tab === 'categories') return renderCatTab(c);
  if (tab === 'projects') return renderProjectsTab(c);
  if (tab === 'forgotten') return renderForgottenTab(c);
  if (tab === 'largest') return renderLargestTab(c);
}

async function renderSmartTab(c) {
  const data = await cachedApi('/api/smart/picks?limit=120&min_size_mb=10');
  c.innerHTML = '';
  if (!data.length) { c.innerHTML = '<div class="empty">No smart picks — index seems empty.</div>'; return; }
  // Header explainer + bulk Ask Sweeper (visible only when AI feature is on)
  const head = document.createElement('div');
  head.className = 'smart-banner';
  head.innerHTML = `
    <div>
      <div class="smart-banner-title">${data.length.toLocaleString()} ranked picks</div>
      <div class="smart-banner-sub">Score = size × idle months × file kind. Top of list = highest confidence.</div>
    </div>
    <button class="ask-sweeper-btn smart-bulk-btn ${aiActive() ? '' : 'hidden'}" id="bulkAskSweeper" style="padding:6px 12px;font-size:12px">
      <span class="glyph">◐</span> Ask Sweeper for top 30
    </button>
  `;
  c.appendChild(head);
  data.forEach(f => c.appendChild(smartFileRow(f)));
  const bulkBtn = head.querySelector('#bulkAskSweeper');
  if (bulkBtn) bulkBtn.onclick = () => askSweeperBulk(data.slice(0, 30), c);
}

async function askSweeperBulk(files, container) {
  if (!aiActive()) return;
  const btn = $('#bulkAskSweeper');
  if (!btn) return;
  btn.disabled = true;
  const total = files.length;
  btn.innerHTML = `<span class="glyph">⋯</span> Asking 0/${total}…`;
  try {
    const r = await api('/api/file/verdict/bulk', {
      method: 'POST',
      body: JSON.stringify({ paths: files.map(f => f.path) }),
    });
    let painted = 0;
    (r.results || []).forEach(item => {
      aiFeature.verdicts.set(item.path, item);
      // Find the row in the container by path and render the verdict.
      const row = Array.from(container.querySelectorAll('.file-row')).find(el => {
        const cb = el.querySelector('input[type=checkbox]');
        return cb && cb.dataset.path === item.path;
      });
      if (row && item.ok) {
        renderRowVerdict(row, item);
        painted++;
      }
    });
    btn.innerHTML = `<span class="glyph">✓</span> ${painted}/${total} verdicts in`;
    btn.disabled = false;
  } catch (e) {
    btn.innerHTML = `<span class="glyph">!</span> ${e.message}`;
    btn.disabled = false;
  }
}

function smartFileRow(f) {
  // Like fileRow but uses server-supplied score + reason.
  const el = fileRow(f);
  // Replace the JS-side score pill with the server-supplied one (more accurate)
  const pill = el.querySelector('.score-pill');
  if (pill) {
    pill.dataset.score = scoreTier(f.score);
    pill.textContent = scoreLabel(f.score);
    pill.title = `${f.score}/100 · ${f.reason}`;
  }
  return el;
}

function renderCatTab(c) {
  c.innerHTML = '';
  state.overview.categories.forEach(cat => {
    const el = document.createElement('div');
    el.className = 'cat-card';
    el.style.setProperty('--c', CATEGORY_COLORS[cat.category] || CATEGORY_COLORS.other);
    el.innerHTML = `
      <div class="cat-rail"></div>
      <div>
        <div class="cat-name">${CATEGORY_LABEL[cat.category] || cat.category}</div>
        <div class="cat-meta">${(cat.files || 0).toLocaleString()} files${cat.recoverable ? ` · ${bytes(cat.recoverable)} recoverable` : ''}</div>
      </div>
      <div class="cat-size">${bytes(cat.size)}</div>
      <svg class="cat-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 18 6-6-6-6"/></svg>
    `;
    el.onclick = () => openDrawer(`category:${cat.category}`, CATEGORY_LABEL[cat.category] || cat.category, `${(cat.files || 0).toLocaleString()} files · ${bytes(cat.size)}`);
    c.appendChild(el);
  });
}

async function renderProjectsTab(c) {
  const data = await cachedApi('/api/projects?limit=40');
  c.innerHTML = '';
  if (!data.length) { c.innerHTML = '<div class="empty">No git projects detected.</div>'; return; }
  data.forEach(p => {
    const el = document.createElement('div');
    el.className = 'project-card';
    el.innerHTML = `
      <div>
        <div class="pj-name">${p.name}</div>
        <div class="pj-path">${p.path}</div>
      </div>
      <div class="pj-stat"><span>Source</span><span class="pj-val">${bytes(p.source_size)}</span></div>
      <div class="pj-stat artifact"><span>Artifacts</span><span class="pj-val">${bytes(p.artifact_size)}</span></div>
      <div class="pj-stat"><span>Touched</span><span class="pj-val">${ago(p.last_activity)}</span></div>
    `;
    if (p.artifact_size > 0) {
      el.style.cursor = 'pointer';
      el.onclick = () => openDrawer(`project:${p.path}`, `${p.name} — artifacts`, p.path);
    }
    c.appendChild(el);
  });
}

async function renderForgottenTab(c) {
  const data = await cachedApi('/api/forgotten?min_size_mb=100&min_age_days=365&limit=200');
  c.innerHTML = '';
  if (!data.length) { c.innerHTML = '<div class="empty">No big forgotten files.</div>'; return; }
  data.forEach(f => c.appendChild(fileRow(f)));
}

async function renderLargestTab(c) {
  // re-use forgotten with looser criteria — sort all files by size
  const data = await api('/api/forgotten?min_size_mb=50&min_age_days=0&limit=200');
  c.innerHTML = '';
  if (!data.length) { c.innerHTML = '<div class="empty">No large files.</div>'; return; }
  data.forEach(f => c.appendChild(fileRow(f)));
}

function deletabilityScore(f) {
  // Quick predictive score 0–100. Higher = more likely user wants to delete.
  // Weights tuned to match user intuition: big + idle + regenerable wins.
  const sizeMB = (f.size || 0) / (1024 * 1024);
  const ageMonths = f.atime ? ((Date.now()/1000 - f.atime) / (30 * 86400)) : 0;
  const kind = inferKind(f.path);
  const kindBoost = {
    archive: 12, installer: 18, video: 8, image: 4, audio: 4,
    pdf: 2, document: 2, model: 6, other: 0,
  }[kind] || 0;
  let s = 0;
  s += Math.min(40, Math.log10(sizeMB + 1) * 12);  // size in log scale, max 40
  s += Math.min(35, ageMonths * 2);                 // idle months, max 35
  s += kindBoost;                                    // type tendency
  if (f.regenerable) s += 15;                        // regenerable = freebie
  if (f.category === 'cache' || f.category === 'dev_artifact' || f.category === 'trash') s += 15;
  return Math.min(100, Math.round(s));
}

function scoreTier(s) { return s >= 65 ? 'high' : s >= 40 ? 'med' : 'low'; }
function scoreLabel(s) { return s >= 65 ? 'sweep' : s >= 40 ? 'review' : 'keep'; }

function fileRow(f) {
  const el = document.createElement('div');
  el.className = 'file-row';
  if (state.selectedFiles.has(f.path)) el.classList.add('selected');
  const kind = inferKind(f.path);
  const score = deletabilityScore(f);
  el.innerHTML = `
    <input type="checkbox" data-path="${escapeAttr(f.path)}" data-size="${f.size}" ${state.selectedFiles.has(f.path) ? 'checked' : ''} />
    <div class="fr-info">
      <div class="fr-name" title="${escapeAttr(f.path)}">
        <span>${f.path.split('/').pop()}</span>
        <span class="score-pill" data-score="${scoreTier(score)}" title="Deletability score: ${score}/100">${scoreLabel(score)}</span>
      </div>
      <div class="fr-path">${shortPath(f.path)} · <span class="fr-kind">${kind}</span></div>
      <div class="fr-verdict-slot"></div>
    </div>
    <div class="fr-size">${bytes(f.size)}</div>
    <div class="fr-age">${ago(f.atime)}</div>
    <div class="fr-actions">
      <button class="ask-sweeper-btn" data-act="ask-sweeper" title="AI verdict — what to do with this">
        <span class="glyph">◐</span> Ask Sweeper
      </button>
      <button class="fr-act" data-act="open" title="Open file">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M21 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h6"/></svg>
      </button>
      <button class="fr-act" data-act="reveal" title="Reveal in Finder">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 20V6a2 2 0 0 1 2-2h6l2 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/></svg>
      </button>
      <button class="fr-act danger" data-act="trash" title="Quarantine">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="m19 6-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
      </button>
    </div>
  `;
  // Ask Sweeper visibility is gated globally — applyAiFeatureGate flips
  // .hidden on every .ask-sweeper-btn whenever the feature toggles.
  const askBtn = el.querySelector('.ask-sweeper-btn');
  if (!aiActive()) askBtn.classList.add('hidden');
  askBtn.onclick = (e) => {
    e.stopPropagation();
    askSweeperFor(f.path, el);
  };
  // If we already have a cached verdict for this path in the page session,
  // render it instantly so re-mounted rows don't lose state.
  const existing = aiFeature.verdicts.get(f.path);
  if (existing) renderRowVerdict(el, existing);
  const cb = el.querySelector('input');
  cb.onclick = e => e.stopPropagation();
  cb.onchange = () => {
    if (cb.checked) state.selectedFiles.set(f.path, f.size);
    else state.selectedFiles.delete(f.path);
    el.classList.toggle('selected', cb.checked);
    renderCart();
  };
  el.querySelectorAll('.fr-act').forEach(b => {
    b.onclick = async (e) => {
      e.stopPropagation();
      const act = b.dataset.act;
      if (act === 'open') {
        await api('/api/file/open', { method: 'POST', body: JSON.stringify({ path: f.path }) });
      } else if (act === 'reveal') {
        await api('/api/file/reveal', { method: 'POST', body: JSON.stringify({ path: f.path }) });
      } else if (act === 'trash') {
        const r = await api('/api/quarantine', { method: 'POST', body: JSON.stringify({ paths: [f.path] }) });
        const ok = r.results.filter(x => x.status === 'quarantined').length;
        toast(ok ? `Quarantined ${f.path.split('/').pop()}` : 'Quarantine failed', ok ? 'safe' : 'danger');
        if (ok) {
          el.style.opacity = '0.4';
          await refreshOverview();
          await refreshQuarantineBadge();
        }
      }
    };
  });
  el.onclick = e => {
    if (e.target.tagName === 'INPUT') return;
    if (e.target.closest('.fr-act')) return;
    openFileDetail(f.path);
  };
  el.addEventListener('mouseenter', () => { _hoveredFilePath = f.path; });
  el.addEventListener('mouseleave', () => { _hoveredFilePath = null; });
  return el;
}

/* Ask Sweeper — row-level verdict request. Replaces the button with a
   colored chip + reason once the model responds. */
async function askSweeperFor(path, rowEl) {
  if (!aiActive()) return;
  const btn = rowEl.querySelector('.ask-sweeper-btn');
  if (!btn) return;
  btn.disabled = true;
  btn.innerHTML = `<span class="glyph">⋯</span> Thinking…`;
  try {
    const r = await api('/api/file/verdict', {
      method: 'POST',
      body: JSON.stringify({ path }),
    });
    if (!r.ok) {
      btn.innerHTML = `<span class="glyph">!</span> ${r.error || 'Failed'}`;
      btn.disabled = false;
      return;
    }
    aiFeature.verdicts.set(path, r);
    renderRowVerdict(rowEl, r);
  } catch (e) {
    btn.innerHTML = `<span class="glyph">!</span> ${e.message}`;
    btn.disabled = false;
  }
}

function renderRowVerdict(rowEl, r) {
  const slot = rowEl.querySelector('.fr-verdict-slot');
  const btn = rowEl.querySelector('.ask-sweeper-btn');
  if (slot) {
    slot.innerHTML = `
      <span class="row-verdict">
        <span class="row-verdict-chip" data-verdict="${r.verdict}">${r.verdict}</span>
        <span class="row-verdict-reason" title="${escapeAttr(r.reason || '')}">${r.reason || ''}</span>
        <span style="color:var(--text-3);font-size:10.5px">${r.confidence}%${r.cached ? ' · cached' : ''}</span>
      </span>
    `;
  }
  if (btn) btn.classList.add('hidden');  // hide the button once we have a verdict
}

/* Group-level Ask Sweeper. Same UX shape as askSweeperFor, but routes
   through /api/group/verdict so it works on directories, project roots,
   sunburst nodes — anything where a folder gets evaluated as a unit. */
async function askGroupVerdictFor(path, btn, slot) {
  if (!aiActive()) return;
  if (!btn || !slot) return;
  btn.disabled = true;
  const orig = btn.innerHTML;
  btn.innerHTML = `<span class="glyph">⋯</span> Thinking…`;
  try {
    const r = await api('/api/group/verdict', {
      method: 'POST',
      body: JSON.stringify({ path }),
    });
    if (!r.ok) {
      slot.innerHTML = `<span class="row-verdict-reason" style="color:var(--danger)">${r.error || 'Failed'}</span>`;
      btn.innerHTML = orig;
      btn.disabled = false;
      return;
    }
    aiFeature.verdicts.set(path, r);
    slot.innerHTML = `
      <span class="row-verdict">
        <span class="row-verdict-chip" data-verdict="${r.verdict}">${r.verdict}</span>
        <span class="row-verdict-reason" title="${escapeAttr(r.reason || '')}">${r.reason || ''}</span>
        <span style="color:var(--text-3);font-size:10.5px">${r.confidence}%${r.cached ? ' · cached' : ''}</span>
      </span>
    `;
    btn.classList.add('hidden');
  } catch (e) {
    slot.innerHTML = `<span class="row-verdict-reason" style="color:var(--danger)">${e.message}</span>`;
    btn.innerHTML = orig;
    btn.disabled = false;
  }
}

/* Toggle visibility of every Ask Sweeper button when the feature gate flips.
   The file-detail "Ask AI" button is left alone — it already handles the
   "no LLM configured" case via its error toast path. */
function applyAiFeatureGate() {
  const on = aiActive();
  document.querySelectorAll('.ask-sweeper-btn').forEach(btn => {
    // Don't un-hide buttons that already revealed a verdict — that .hidden
    // was post-verdict, not pre-feature. Heuristic: if its row's verdict
    // slot has content, leave the button hidden.
    const row = btn.closest('.file-row');
    const hasVerdict = row && row.querySelector('.fr-verdict-slot')?.innerHTML.trim();
    if (hasVerdict) return;
    btn.classList.toggle('hidden', !on);
  });
}
document.addEventListener('ai-feature-changed', applyAiFeatureGate);

/* ── Module: Sweeper Audit (mass batched verdicts) ──────────────────── */
const auditState = {
  selected: new Set(),     // paths
  filter: null,            // null | 'safe' | 'review' | 'keep'
  minConfidence: 85,
  pollTimer: null,
  results: [],             // last fetched results
};

async function renderAudit() {
  // Wire controls once — renderAudit is called every time the user navigates here.
  const runBtn = $('#auditRunBtn');
  if (runBtn && !runBtn._wired) {
    runBtn._wired = true;
    runBtn.onclick = startAudit;
    $('#auditConfSlider').oninput = (e) => {
      auditState.minConfidence = parseInt(e.target.value, 10);
      $('#auditConfVal').textContent = auditState.minConfidence;
      refreshAuditResults();
    };
    document.querySelectorAll('.audit-chip').forEach(chip => {
      chip.onclick = () => {
        const f = chip.dataset.filter;
        auditState.filter = (auditState.filter === f) ? null : f;
        document.querySelectorAll('.audit-chip').forEach(c => {
          c.classList.toggle('active', c.dataset.filter === auditState.filter);
        });
        refreshAuditResults();
      };
    });
    $('#auditSelectAll').onchange = (e) => {
      const checked = e.target.checked;
      auditState.selected = checked
        ? new Set(auditState.results.map(r => r.path))
        : new Set();
      paintAuditRows();
      updateAuditSelectionInfo();
    };
    $('#auditQuarantineBtn').onclick = quarantineAuditSelection;
  }

  // If we have prior results in DB, show them straight away.
  await refreshAuditResults();
  // Sync UI with current backend state in case an audit is mid-run.
  await pollAuditStatus(false);
}

async function startAudit() {
  if (!aiActive()) {
    toast('Connect Integrator and enable AI verdicts in Settings first.', 'danger');
    return;
  }
  const max = parseInt($('#auditMaxFiles').value, 10);
  const min = parseInt($('#auditMinSize').value, 10);
  $('#auditRunBtn').disabled = true;
  $('#auditRunBtn').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg> Starting…';
  try {
    const r = await api('/api/audit/run', {
      method: 'POST',
      body: JSON.stringify({ scope: 'smart_picks', max_files: max, min_size_mb: min }),
    });
    if (!r.ok) {
      toast(r.error || 'Could not start audit', 'danger');
      $('#auditRunBtn').disabled = false;
      $('#auditRunBtn').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 3l14 9-14 9z"/></svg> Run audit';
      return;
    }
    $('#auditProgress').classList.remove('hidden');
    pollAuditStatus(true);
  } catch (e) {
    toast(`Audit failed: ${e.message}`, 'danger');
    $('#auditRunBtn').disabled = false;
    $('#auditRunBtn').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 3l14 9-14 9z"/></svg> Run audit';
  }
}

async function pollAuditStatus(continuous) {
  clearInterval(auditState.pollTimer);
  auditState.pollTimer = null;
  const tick = async () => {
    let s;
    try { s = await api('/api/audit/status'); }
    catch (e) { return; }
    const total = s.total || 0;
    const done = s.done || 0;
    const pct = total ? Math.round((done / total) * 100) : 0;
    $('#auditProgressFill').style.width = pct + '%';
    $('#auditProgressLabel').textContent =
      s.phase === 'running'
        ? `Auditing ${s.scope || ''}… ${pct}%`
        : (s.phase === 'done' ? 'Audit complete.' :
           s.phase === 'error' ? `Error: ${s.error}` : 'Idle');
    $('#auditProgressCount').textContent = `${done} / ${total}`;
    if (s.phase === 'done' || s.phase === 'error' || s.phase === 'idle') {
      clearInterval(auditState.pollTimer);
      auditState.pollTimer = null;
      $('#auditRunBtn').disabled = false;
      $('#auditRunBtn').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 3l14 9-14 9z"/></svg> Run audit';
      if (s.phase === 'done') {
        toast(`Audit done · ${s.verdict_counts?.safe || 0} safe · ${s.verdict_counts?.review || 0} review · ${s.verdict_counts?.keep || 0} keep`, 'safe');
      }
      await refreshAuditResults();
    }
  };
  await tick();
  if (continuous) {
    auditState.pollTimer = setInterval(tick, 1500);
  }
}

async function refreshAuditResults() {
  const params = new URLSearchParams();
  params.set('min_confidence', String(auditState.minConfidence));
  if (auditState.filter) params.set('verdicts', auditState.filter);
  params.set('limit', '500');
  let rows;
  try { rows = await api(`/api/audit/results?${params.toString()}`); }
  catch (e) { return; }
  auditState.results = rows || [];
  // Update verdict counts (across ALL verdicts, regardless of active filter)
  const allCounts = await api(`/api/audit/results?min_confidence=0&limit=2000`);
  const counts = { safe: 0, review: 0, keep: 0 };
  (allCounts || []).forEach(r => { counts[r.ai_verdict] = (counts[r.ai_verdict] || 0) + 1; });
  $('#auditCountSafe').textContent = counts.safe;
  $('#auditCountReview').textContent = counts.review;
  $('#auditCountKeep').textContent = counts.keep;
  if (counts.safe + counts.review + counts.keep > 0) {
    $('#auditSummary').classList.remove('hidden');
  }
  paintAuditRows();
}

function paintAuditRows() {
  const table = $('#auditTable');
  const toolbar = $('#auditToolbar');
  if (!auditState.results.length) {
    table.innerHTML = '<div class="empty" style="padding:60px 20px;text-align:center">No verdicts match the current filters.</div>';
    toolbar.classList.add('hidden');
    return;
  }
  toolbar.classList.remove('hidden');
  table.innerHTML = '';
  auditState.results.forEach(r => {
    const row = document.createElement('div');
    row.className = 'audit-row';
    if (auditState.selected.has(r.path)) row.classList.add('selected');
    row.innerHTML = `
      <input type="checkbox" data-path="${escapeAttr(r.path)}" ${auditState.selected.has(r.path) ? 'checked' : ''} />
      <span class="audit-row-verdict" data-verdict="${r.ai_verdict}">${r.ai_verdict}</span>
      <div class="audit-row-info">
        <div class="audit-row-name" title="${escapeAttr(r.path)}">${r.name}</div>
        <div class="audit-row-reason" title="${escapeAttr(r.ai_reason || '')}">${r.ai_reason || ''}</div>
      </div>
      <div class="audit-row-conf">${r.ai_confidence}%</div>
      <div class="audit-row-size">${bytes(r.size)}</div>
    `;
    const cb = row.querySelector('input');
    cb.onclick = e => e.stopPropagation();
    cb.onchange = () => {
      if (cb.checked) auditState.selected.add(r.path);
      else auditState.selected.delete(r.path);
      row.classList.toggle('selected', cb.checked);
      updateAuditSelectionInfo();
    };
    row.onclick = (e) => {
      if (e.target.tagName === 'INPUT') return;
      openFileDetail(r.path);
    };
    table.appendChild(row);
  });
  updateAuditSelectionInfo();
}

function updateAuditSelectionInfo() {
  const n = auditState.selected.size;
  let totalSize = 0;
  auditState.results.forEach(r => { if (auditState.selected.has(r.path)) totalSize += r.size; });
  $('#auditSelectionInfo').textContent =
    n ? `${n} selected · ${bytes(totalSize)}` : '0 selected';
  $('#auditQuarantineBtn').disabled = n === 0;
  $('#auditQuarantineBtn').textContent = n
    ? `Quarantine ${n} (${bytes(totalSize)})`
    : 'Quarantine selected';
  // Check master state
  const visiblePaths = auditState.results.map(r => r.path);
  const allSelected = visiblePaths.length > 0 && visiblePaths.every(p => auditState.selected.has(p));
  $('#auditSelectAll').checked = allSelected;
}

async function quarantineAuditSelection() {
  const paths = Array.from(auditState.selected);
  if (!paths.length) return;
  // Hard cap as a safety net — backend has its own protected-prefix guards
  // but bound the click anyway.
  if (paths.length > 200) {
    toast('Cap is 200 per click. Narrow the filter.', 'danger');
    return;
  }
  // Surface any 'keep' or low-confidence picks before they slip through.
  const risky = auditState.results.filter(r =>
    auditState.selected.has(r.path) &&
    (r.ai_verdict !== 'safe' || r.ai_confidence < 75)
  );
  let warning = `Quarantine ${paths.length} files? Restorable for 30 days.`;
  if (risky.length) {
    warning += `\n\n${risky.length} of these are NOT high-confidence "safe" — review before continuing.`;
  }
  if (!confirm(warning)) return;
  try {
    const r = await api('/api/quarantine', {
      method: 'POST',
      body: JSON.stringify({ paths }),
    });
    const ok = r.results.filter(x => x.status === 'quarantined');
    const freed = ok.reduce((a, b) => a + (b.size || 0), 0);
    toast(`Quarantined ${ok.length} · freed ${bytes(freed)}`, 'safe');
    auditState.selected.clear();
    cacheClear();
    await refreshOverview();
    await refreshQuarantineBadge();
    await refreshAuditResults();
  } catch (e) {
    toast(`Quarantine failed: ${e.message}`, 'danger');
  }
}

/* ── Module: Quarantine ───────────────────────────────────────────────── */
async function renderQuarantine() {
  const items = await api('/api/quarantine');
  const c = $('#quarantineList');
  c.innerHTML = '';
  if (!items.length) { c.innerHTML = '<div class="empty">Nothing in quarantine.</div>'; return; }
  items.forEach(it => {
    const purgeIn = Math.max(0, Math.floor((it.purge_after - Date.now() / 1000) / 86400));
    const el = document.createElement('div');
    el.className = 'q-row';
    el.innerHTML = `
      <div>
        <div class="q-path" title="${escapeAttr(it.original_path)}">${it.original_path}</div>
        <div class="q-meta">${it.category || 'unclassified'} · quarantined ${ago(it.quarantined_at)} · purges in ${purgeIn}d</div>
      </div>
      <div class="q-size">${bytes(it.size)}</div>
      <div class="q-actions">
        <button class="btn ghost" data-restore="${it.id}">Restore</button>
        <button class="btn danger" data-purge="${it.id}">Purge</button>
      </div>
    `;
    el.querySelector('[data-restore]').onclick = async (e) => {
      const btn = e.currentTarget;
      const orig = btn.textContent;
      btn.disabled = true;
      btn.textContent = 'Restoring…';
      try {
        const r = await api(`/api/quarantine/${it.id}/restore`, { method: 'POST' });
        toast(r.status === 'restored' ? 'Restored' : `Restore failed: ${r.status}`, r.status === 'restored' ? 'safe' : 'danger');
      } finally {
        await renderQuarantine();
        await refreshQuarantineBadge();
        await refreshOverview();
      }
    };
    el.querySelector('[data-purge]').onclick = async (e) => {
      if (!confirm('Permanently delete this item? This cannot be undone.')) return;
      const btn = e.currentTarget;
      const orig = btn.textContent;
      btn.disabled = true;
      btn.textContent = 'Purging…';
      toast(`Purging ${bytes(it.size || 0)}…`, 'danger');
      try {
        const r = await api(`/api/quarantine/${it.id}/purge`, { method: 'POST' });
        toast(`Purged · freed ${bytes(r.size || it.size || 0)}`, 'safe');
      } finally {
        await renderQuarantine();
        await refreshQuarantineBadge();
        await refreshOverview();
      }
    };
    c.appendChild(el);
  });
}

async function refreshQuarantineBadge() {
  const items = await api('/api/quarantine');
  $('#qNavBadge').textContent = items.length;
  // Also keep redundancy badge in sync
  try {
    const reds = await api('/api/redundancies');
    const total = reds.reduce((a, g) => a + g.count, 0);
    const rb = $('#redBadge');
    if (rb) rb.textContent = total;
  } catch (e) { /* ok */ }
}

/* ── Redundancies module ──────────────────────────────────────────────── */
async function renderRedundancies() {
  const list = $('#redundancyList');
  list.innerHTML = skeletonGroupRows(4);
  const groups = await cachedApi('/api/redundancies');
  list.innerHTML = '';
  if (!groups.length) {
    list.innerHTML = '<div class="empty">No redundancies found. Looking clean.</div>';
    return;
  }

  // Per-group selection sets
  const sel = new Map();  // group_key → Set(redundant_path)
  const totalSavings = groups.reduce((a, g) => a + g.total_size, 0);

  // Top summary
  const summary = document.createElement('div');
  summary.className = 'red-summary';
  summary.innerHTML = `
    <div>
      <div class="red-summary-num">${bytes(totalSavings)}</div>
      <div class="red-summary-label">total recoverable across ${groups.length} ${groups.length === 1 ? 'category' : 'categories'}</div>
    </div>
    <button class="btn primary" id="redSweepAll">Sweep all safe</button>
  `;
  list.appendChild(summary);

  groups.forEach(g => list.appendChild(redundancyGroupCard(g, sel)));

  $('#redSweepAll').onclick = async () => {
    // Sweep everything in the high-confidence groups (installer_dupes, tm_snapshots, orphan_caches, archive_pairs, version_clusters)
    const safeTypes = new Set(['installer_dupes', 'orphan_caches', 'archive_pairs', 'version_clusters']);
    const paths = [];
    groups.forEach(g => {
      if (safeTypes.has(g.type)) g.items.forEach(i => paths.push(i.redundant_path));
    });
    if (!paths.length) { toast('Nothing safe to auto-sweep.', 'danger'); return; }
    if (!confirm(`Quarantine ${paths.length} redundant items? Restorable for 30 days.`)) return;
    const r = await api('/api/redundancies/sweep', { method: 'POST', body: JSON.stringify({ paths }) });
    const ok = r.results.filter(x => x.status === 'quarantined');
    const freed = ok.reduce((a, b) => a + (b.size || 0), 0);
    toast(`Quarantined ${ok.length} · freed ${bytes(freed)}`, 'safe');
    cacheClear();
    await refreshOverview();
    await refreshQuarantineBadge();
    renderRedundancies();
  };
}

function redundancyGroupCard(g, sel) {
  const card = document.createElement('div');
  card.className = 'red-group';
  card.dataset.type = g.type;
  const groupSel = new Set();
  // Pre-select all redundants in safe types
  if (['installer_dupes','orphan_caches','archive_pairs','version_clusters'].includes(g.type)) {
    g.items.forEach(i => groupSel.add(i.redundant_path));
  }
  sel.set(g.type, groupSel);

  card.innerHTML = `
    <div class="red-head">
      <div>
        <div class="red-title">${g.title}</div>
        <div class="red-sub">${g.subtitle}</div>
      </div>
      <div class="red-stats">
        <div class="red-size">${bytes(g.total_size)}</div>
        <div class="red-count">${g.count} ${g.count === 1 ? 'item' : 'items'}</div>
      </div>
      <button class="btn ghost red-toggle" data-toggle><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 9 6 6 6-6"/></svg></button>
    </div>
    <div class="red-body hidden">
      <div class="red-items">${g.items.map(i => `
        <label class="red-item" data-red-path="${escapeAttr(i.redundant_path)}">
          <input type="checkbox" data-path="${escapeAttr(i.redundant_path)}" ${groupSel.has(i.redundant_path) ? 'checked' : ''} />
          <div class="red-item-info">
            <div class="red-item-name">${shortPath(i.redundant_path)}</div>
            <div class="red-item-detail">${i.detail || ''}${i.keep_path ? ` · keep ${shortPath(i.keep_path)}` : ''}</div>
            <div class="red-verdict-slot"></div>
          </div>
          <div class="red-item-size">${bytes(i.size_freed)}</div>
          <button class="ask-sweeper-btn red-ask ${aiActive() ? '' : 'hidden'}" data-path="${escapeAttr(i.redundant_path)}" title="AI verdict">
            <span class="glyph">◐</span>
          </button>
        </label>
      `).join('')}</div>
      <div class="red-actions">
        <button class="btn primary" data-sweep>Sweep selected</button>
      </div>
    </div>
  `;

  card.querySelector('[data-toggle]').onclick = () => {
    card.querySelector('.red-body').classList.toggle('hidden');
    card.querySelector('[data-toggle]').classList.toggle('rotated');
  };
  card.querySelectorAll('input[type=checkbox]').forEach(cb => {
    cb.onchange = () => {
      const path = cb.dataset.path;
      if (cb.checked) groupSel.add(path);
      else groupSel.delete(path);
    };
  });
  card.querySelector('[data-sweep]').onclick = async () => {
    const paths = Array.from(groupSel);
    if (!paths.length) { toast('Nothing selected.', 'danger'); return; }
    if (!confirm(`Quarantine ${paths.length} items? Restorable for 30 days.`)) return;
    const r = await api('/api/redundancies/sweep', { method: 'POST', body: JSON.stringify({ paths }) });
    const ok = r.results.filter(x => x.status === 'quarantined');
    const freed = ok.reduce((a, b) => a + (b.size || 0), 0);
    toast(`Quarantined ${ok.length} · freed ${bytes(freed)}`, 'safe');
    cacheClear();
    await refreshOverview();
    await refreshQuarantineBadge();
    renderRedundancies();
  };
  // Per-item Ask Sweeper (file-level verdict on the redundant path).
  card.querySelectorAll('.red-ask').forEach(btn => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      e.preventDefault();
      const path = btn.dataset.path;
      const item = btn.closest('.red-item');
      const slot = item.querySelector('.red-verdict-slot');
      btn.disabled = true;
      btn.innerHTML = `<span class="glyph">⋯</span>`;
      try {
        const r = await api('/api/file/verdict', {
          method: 'POST',
          body: JSON.stringify({ path }),
        });
        if (!r.ok) {
          slot.innerHTML = `<span class="row-verdict-reason" style="color:var(--danger)">${r.error || 'Failed'}</span>`;
          btn.innerHTML = `<span class="glyph">◐</span>`;
          btn.disabled = false;
          return;
        }
        aiFeature.verdicts.set(path, r);
        slot.innerHTML = `
          <span class="row-verdict">
            <span class="row-verdict-chip" data-verdict="${r.verdict}">${r.verdict}</span>
            <span class="row-verdict-reason" title="${escapeAttr(r.reason || '')}">${r.reason || ''}</span>
            <span style="color:var(--text-3);font-size:10.5px">${r.confidence}%</span>
          </span>
        `;
        btn.classList.add('hidden');
      } catch (err) {
        slot.innerHTML = `<span class="row-verdict-reason" style="color:var(--danger)">${err.message}</span>`;
        btn.innerHTML = `<span class="glyph">◐</span>`;
        btn.disabled = false;
      }
    };
  });
  return card;
}

/* ── Drawer (renders GROUPS — Brave / Chrome / npm — not file paths) ── */
const drawerSelected = new Map();  // group_root → {name, size, files}

async function openDrawer(action, title, sub) {
  $('#drawerTitle').textContent = title;
  $('#drawerSub').textContent = sub;
  $('#drawer').classList.remove('hidden');
  const list = $('#drawerList');
  list.innerHTML = skeletonGroupRows(6);
  drawerSelected.clear();

  const groups = await fetchGroups(action);
  list.innerHTML = '';
  if (!groups.length) {
    list.innerHTML = '<div class="empty">No matching items.</div>';
    return;
  }
  // Pre-select all safe (regenerable) groups for one-click cleanup of caches/builds.
  groups.forEach(g => {
    if (g.regenerable) drawerSelected.set(g.path, g);
    list.appendChild(groupRow(g));
  });

  $('#drawerSelectAll').checked = drawerSelected.size === groups.length;
  $('#drawerSelectAll').onchange = () => {
    const checked = $('#drawerSelectAll').checked;
    if (checked) groups.forEach(g => drawerSelected.set(g.path, g));
    else drawerSelected.clear();
    list.innerHTML = '';
    groups.forEach(g => list.appendChild(groupRow(g)));
    syncDrawer();
  };
  $('#drawerQuarantine').onclick = async () => {
    if (!drawerSelected.size) return;
    // Push selected groups into the global cart (sticky bottom bar)
    drawerSelected.forEach((g, root) => {
      state.selectedGroups.set(root, g);
    });
    $('#drawer').classList.add('hidden');
    renderCart();
  };
  syncDrawer();
}

function groupRow(g) {
  const el = document.createElement('div');
  el.className = 'group-row';
  el.dataset.root = g.path;
  if (drawerSelected.has(g.path)) el.classList.add('selected');
  const kind = g.kind || inferKind(g.path);
  const isFile = g.files === 1 && g.path === (g.samples?.[0] || g.path);
  const detail = isFile
    ? `${kind}${g.last_touched ? ` · ${ago(g.last_touched)}` : ''}`
    : `${g.files.toLocaleString()} files · ${kind}${g.last_touched ? ` · ${ago(g.last_touched)}` : ''}`;
  el.innerHTML = `
    <div class="gr-check"></div>
    <div class="gr-meta">
      <div class="gr-name">${g.name}</div>
      <div class="gr-detail">${detail}</div>
      <div class="gr-path mono">${shortPath(g.path)}</div>
      <div class="gr-verdict-slot"></div>
    </div>
    <div class="gr-size">${bytes(g.size)}</div>
    <div class="gr-actions">
      <button class="ask-sweeper-btn gr-ask ${aiActive() ? '' : 'hidden'}" title="${isFile ? 'AI verdict on this file' : 'AI verdict on this whole folder'}">
        <span class="glyph">◐</span> Ask Sweeper
      </button>
      ${isFile ? '<button class="gr-view" title="Show details">↗</button>' : ''}
    </div>
  `;
  // Cached verdict?
  const cached = aiFeature.verdicts.get(g.path);
  if (cached && cached.ok) {
    el.querySelector('.gr-verdict-slot').innerHTML = `
      <span class="row-verdict">
        <span class="row-verdict-chip" data-verdict="${cached.verdict}">${cached.verdict}</span>
        <span class="row-verdict-reason" title="${escapeAttr(cached.reason || '')}">${cached.reason || ''}</span>
        <span style="color:var(--text-3);font-size:10.5px">${cached.confidence}%</span>
      </span>
    `;
    el.querySelector('.gr-ask')?.classList.add('hidden');
  }
  // Ask Sweeper handler — file path → file verdict, folder path → group verdict.
  el.querySelector('.gr-ask').onclick = (e) => {
    e.stopPropagation();
    const btn = e.currentTarget;
    const slot = el.querySelector('.gr-verdict-slot');
    if (isFile) {
      // Single-file path: reuse file verdict, but render via askGroupVerdictFor's
      // shape so it lands in the same slot. Inline the call for simplicity.
      btn.disabled = true;
      btn.innerHTML = `<span class="glyph">⋯</span> Thinking…`;
      api('/api/file/verdict', { method: 'POST', body: JSON.stringify({ path: g.path }) })
        .then(r => {
          if (!r.ok) {
            slot.innerHTML = `<span class="row-verdict-reason" style="color:var(--danger)">${r.error || 'Failed'}</span>`;
            btn.innerHTML = `<span class="glyph">◐</span> Ask Sweeper`;
            btn.disabled = false;
            return;
          }
          aiFeature.verdicts.set(g.path, r);
          slot.innerHTML = `
            <span class="row-verdict">
              <span class="row-verdict-chip" data-verdict="${r.verdict}">${r.verdict}</span>
              <span class="row-verdict-reason" title="${escapeAttr(r.reason || '')}">${r.reason || ''}</span>
              <span style="color:var(--text-3);font-size:10.5px">${r.confidence}%${r.cached ? ' · cached' : ''}</span>
            </span>
          `;
          btn.classList.add('hidden');
        })
        .catch(err => {
          slot.innerHTML = `<span class="row-verdict-reason" style="color:var(--danger)">${err.message}</span>`;
          btn.innerHTML = `<span class="glyph">◐</span> Ask Sweeper`;
          btn.disabled = false;
        });
    } else {
      askGroupVerdictFor(g.path, btn, slot);
    }
  };
  el.onclick = (e) => {
    if (e.target.closest('.gr-view')) {
      e.stopPropagation();
      openFileDetail(g.path);
      return;
    }
    if (e.target.closest('.gr-ask')) return;
    if (drawerSelected.has(g.path)) drawerSelected.delete(g.path);
    else drawerSelected.set(g.path, g);
    el.classList.toggle('selected', drawerSelected.has(g.path));
    syncDrawer();
  };
  return el;
}

function syncDrawer() {
  const sel = Array.from(drawerSelected.values());
  const totalSize = sel.reduce((a, g) => a + g.size, 0);
  const totalFiles = sel.reduce((a, g) => a + g.files, 0);
  $('#drawerSelected').textContent = sel.length
    ? `${sel.length} group${sel.length > 1 ? 's' : ''} · ${totalFiles.toLocaleString()} files · ${bytes(totalSize)}`
    : '0 selected';
  $('#drawerQuarantine').disabled = sel.length === 0;
  $('#drawerQuarantine').textContent = sel.length ? `Add to plan (${sel.length})` : 'Add to plan';
}

function shortPath(p) {
  return p.replace(/^\/Users\/[^/]+/, '~');
}

async function fetchGroups(action) {
  if (action.startsWith('project:')) {
    // Project drill-down — list all build artifacts within a project as one group.
    const path = action.slice('project:'.length);
    const all = await api('/api/action/stale_artifacts');
    const files = all.filter(r => r.path.startsWith(path + '/'));
    if (!files.length) return [];
    return [{
      name: path.split('/').pop(),
      path,
      size: files.reduce((a, b) => a + b.size, 0),
      files: files.length,
      regenerable: true,
      last_touched: Math.max(...files.map(f => f.mtime)),
      samples: files.slice(0, 3).map(f => f.path),
    }];
  }
  return api(`/api/action/${action}/groups?limit=80`);
}

/* ── Cart (sticky bottom bar) ─────────────────────────────────────────── */
async function renderCart() {
  let totalSize = 0;
  let totalFiles = 0;

  state.selectedFiles.forEach(size => { totalSize += size; totalFiles += 1; });
  state.selectedGroups.forEach(g => { totalSize += g.size; totalFiles += g.files; });
  state.selectedSuggestions.forEach(id => {
    const s = state.suggestions.find(x => x.id === id);
    if (s) totalSize += s.size;
  });

  const groupCount = state.selectedGroups.size + state.selectedSuggestions.size;
  const visible = (groupCount + state.selectedFiles.size) > 0;
  $('#cart').classList.toggle('hidden', !visible);
  if (!visible) return;

  const parts = [];
  if (groupCount) parts.push(`${groupCount} group${groupCount > 1 ? 's' : ''}`);
  if (state.selectedFiles.size) parts.push(`${state.selectedFiles.size} item${state.selectedFiles.size > 1 ? 's' : ''}`);
  if (totalFiles && totalFiles !== state.selectedFiles.size) {
    parts.push(`(${totalFiles.toLocaleString()} files)`);
  }
  $('#cartLabel').textContent = parts.join(' · ');
  $('#cartSize').textContent = bytes(totalSize);
}

function clearCart() {
  state.selectedFiles.clear();
  state.selectedGroups.clear();
  state.selectedSuggestions.clear();
  $$('.suggestion.selected').forEach(el => el.classList.remove('selected'));
  $$('.file-row.selected').forEach(el => {
    el.classList.remove('selected');
    const cb = el.querySelector('input');
    if (cb) cb.checked = false;
  });
  renderCart();
}

async function commitCart() {
  // Send group ROOTS, not expanded file lists. The backend renames a directory
  // in one shutil.move; expanding to per-file paths used to make Spotify-sized
  // groups take 30s+ with no UI feedback.
  const roots = new Set();
  state.selectedGroups.forEach((_, root) => roots.add(root));
  for (const id of state.selectedSuggestions) {
    const s = state.suggestions.find(x => x.id === id);
    if (!s) continue;
    const groups = await api(`/api/action/${s.action}/groups?limit=200`);
    for (const g of groups) roots.add(g.path);
  }
  state.selectedFiles.forEach((_, p) => roots.add(p));

  const paths = Array.from(roots);
  if (!paths.length) return;

  const btn = $('#cartCommit');
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Quarantining…';
  try {
    await doQuarantine(paths);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

async function doQuarantine(paths) {
  toast(`Quarantining ${paths.length.toLocaleString()} ${paths.length === 1 ? 'item' : 'items'}…`);
  const r = await api('/api/quarantine', { method: 'POST', body: JSON.stringify({ paths }) });
  const ok = r.results.filter(x => x.status === 'quarantined');
  const failed = r.results.filter(x => x.status === 'error');
  if (failed.length) console.error('quarantine errors:', failed);
  const freedSize = ok.reduce((a, b) => a + (b.size || 0), 0);
  showUndoToast(ok, freedSize);
  clearCart();
  cacheClear();
  await refreshOverview();
  await renderModule(state.module);
  await refreshQuarantineBadge();
}

let _undoTimer = null;
function showUndoToast(items, freedSize) {
  const toastEl = $('#undoToast');
  toastEl.classList.remove('hidden');
  $('#undoToastText').textContent = `Quarantined ${items.length.toLocaleString()} · freed ${bytes(freedSize)}`;
  $('#undoToastBtn').onclick = async () => {
    // Restore each quarantined item via /api/quarantine/{id}/restore
    const all = await api('/api/quarantine');
    const recent = all.slice(0, items.length);
    for (const q of recent) {
      await api(`/api/quarantine/${q.id}/restore`, { method: 'POST' });
    }
    toast('Restored.', 'safe');
    toastEl.classList.add('hidden');
    cacheClear();
    await refreshOverview();
    await refreshQuarantineBadge();
    await renderModule(state.module);
  };
  clearTimeout(_undoTimer);
  _undoTimer = setTimeout(() => toastEl.classList.add('hidden'), 10_000);
}

async function refreshOverview() {
  cacheClear();  // anything we cached may be stale after a quarantine action
  state.overview = await api('/api/overview');
  renderSidebar(state.overview);
}

/* ── File-type icon system (mirrors backend file_detail.KIND_BY_EXT) ──── */
const KIND_GLYPH = {
  image: 'IMG', video: 'MOV', audio: '♪', pdf: 'PDF',
  ebook: 'EPUB', archive: 'ZIP', installer: 'PKG',
  document: 'DOC', spreadsheet: 'XLS', presentation: 'PPT',
  text: 'TXT', code: '<>', model: 'AI', app: 'APP',
  font: 'AA', design: 'PSD', threed: '3D',
  executable: 'EXE', dir: 'DIR', system: 'SYS', other: '·',
};

const KIND_BY_EXT = {
  // image
  jpg:'image', jpeg:'image', png:'image', gif:'image', heic:'image', heif:'image',
  raw:'image', tiff:'image', tif:'image', webp:'image', bmp:'image', svg:'image',
  ico:'image', icns:'image', psd:'image', ai:'image', cr2:'image', nef:'image',
  arw:'image', dng:'image',
  // video
  mp4:'video', mov:'video', mkv:'video', avi:'video', webm:'video', m4v:'video',
  wmv:'video', flv:'video', mpg:'video', mpeg:'video', '3gp':'video', mts:'video',
  m2ts:'video', ts:'video',
  // audio
  mp3:'audio', m4a:'audio', wav:'audio', flac:'audio', aac:'audio', ogg:'audio',
  opus:'audio', wma:'audio', aiff:'audio', aif:'audio', caf:'audio', midi:'audio', mid:'audio',
  // documents
  pdf:'pdf',
  doc:'document', docx:'document', pages:'document', rtf:'document', odt:'document', wpd:'document',
  xls:'spreadsheet', xlsx:'spreadsheet', numbers:'spreadsheet', ods:'spreadsheet',
  csv:'spreadsheet', tsv:'spreadsheet',
  ppt:'presentation', pptx:'presentation', key:'presentation', odp:'presentation',
  epub:'ebook', mobi:'ebook', azw:'ebook', azw3:'ebook', fb2:'ebook',
  // archive / installer
  zip:'archive', tar:'archive', gz:'archive', tgz:'archive', rar:'archive', '7z':'archive',
  bz2:'archive', xz:'archive', lz:'archive', lzma:'archive', z:'archive', cab:'archive',
  deb:'archive', rpm:'archive', apk:'archive', jar:'archive', war:'archive', ear:'archive',
  dmg:'installer', pkg:'installer', iso:'installer', img:'installer', vhd:'installer', vmdk:'installer',
  // text / code
  txt:'text', md:'text', rst:'text', tex:'text', log:'text', ini:'text', conf:'text',
  cfg:'text', toml:'text', json:'text', xml:'text', yaml:'text', yml:'text', plist:'text',
  html:'text', htm:'text', css:'text', scss:'text', sass:'text', less:'text', env:'text',
  py:'code', js:'code', mjs:'code', cjs:'code', ts:'code', tsx:'code', jsx:'code',
  vue:'code', svelte:'code', go:'code', rs:'code', java:'code', kt:'code', scala:'code',
  c:'code', cpp:'code', cc:'code', cxx:'code', h:'code', hpp:'code',
  m:'code', mm:'code', swift:'code', rb:'code', php:'code', pl:'code', lua:'code',
  sh:'code', bash:'code', zsh:'code', fish:'code', ps1:'code', bat:'code', cmd:'code',
  sql:'code', r:'code', jl:'code', ex:'code', exs:'code', erl:'code', hs:'code',
  clj:'code', elm:'code', dart:'code', f90:'code', fpp:'code', asm:'code', s:'code',
  // ml / model
  gguf:'model', safetensors:'model', ckpt:'model', pt:'model', pth:'model', onnx:'model',
  bin:'model', h5:'model', tflite:'model', mlmodel:'model', pb:'model', joblib:'model',
  pickle:'model', pkl:'model',
  // design / 3d / font
  sketch:'design', fig:'design', xd:'design', afdesign:'design', afphoto:'design',
  indd:'design', idml:'design',
  blend:'threed', obj:'threed', fbx:'threed', stl:'threed', dae:'threed',
  '3ds':'threed', max:'threed', ma:'threed', mb:'threed', gltf:'threed', glb:'threed', usdz:'threed',
  ttf:'font', otf:'font', woff:'font', woff2:'font', eot:'font',
  exe:'executable', msi:'executable', appimage:'executable', so:'executable',
  dylib:'executable', dll:'executable', a:'executable', lib:'executable',
  wasm:'executable',
  ds_store:'system', lock:'system', swp:'system', tmp:'system',
};

function inferKind(path) {
  if (path.endsWith('.app')) return 'app';
  const e = (path.split('.').pop() || '').toLowerCase();
  return KIND_BY_EXT[e] || 'other';
}

function kindGlyph(kind, path) {
  const g = KIND_GLYPH[kind];
  if (g) return g;
  // Unknown extension: show its uppercase ext as the icon, capped at 4 chars
  const ext = (path?.split('.').pop() || '').toUpperCase();
  return ext && ext.length <= 5 ? ext : '·';
}

/* ── File Detail panel ────────────────────────────────────────────────── */
let _fdCurrentPath = null;
let _hoveredFilePath = null;  // for spacebar Quick Look

function bindFileDetail() {
  $('#fileDetailClose').onclick = () => $('#fileDetail').classList.add('hidden');
  $('#fileDetailReveal').onclick = async () => {
    if (_fdCurrentPath) await api('/api/file/reveal', { method: 'POST', body: JSON.stringify({ path: _fdCurrentPath }) });
  };
  $('#fileDetailOpen').onclick = async () => {
    if (_fdCurrentPath) await api('/api/file/open', { method: 'POST', body: JSON.stringify({ path: _fdCurrentPath }) });
  };
  $('#fileDetailQuarantine').onclick = async () => {
    if (!_fdCurrentPath) return;
    if (!confirm(`Quarantine ${_fdCurrentPath.split('/').pop()}? Restorable for 30 days.`)) return;
    const r = await api('/api/quarantine', { method: 'POST', body: JSON.stringify({ paths: [_fdCurrentPath] }) });
    const ok = r.results.filter(x => x.status === 'quarantined').length;
    toast(ok ? 'Quarantined' : 'Quarantine failed', ok ? 'safe' : 'danger');
    $('#fileDetail').classList.add('hidden');
    await refreshOverview();
    await renderModule(state.module);
    await refreshQuarantineBadge();
  };
  $('#fileDetailVerdict').onclick = () => askVerdict(_fdCurrentPath);
}

async function askVerdict(path) {
  if (!path) return;
  const panel = $('#fileDetailVerdictPanel');
  const btn = $('#fileDetailVerdict');
  panel.classList.remove('hidden');
  panel.innerHTML = `<div class="dim">Asking the model…</div>`;
  btn.disabled = true;
  try {
    const r = await api('/api/file/verdict', { method: 'POST', body: JSON.stringify({ path }) });
    if (!r.ok) {
      panel.innerHTML = `<div class="fd-verdict-error">${r.error || 'Could not get a verdict.'}</div>
        <div style="margin-top:8px"><button class="btn ghost" id="fdVerdictSettings">Open settings</button></div>`;
      $('#fdVerdictSettings').onclick = () => { $('#settings').classList.remove('hidden'); refreshLLMStatus(); };
      return;
    }
    panel.innerHTML = `
      <div class="fd-verdict-head">
        <span class="fd-verdict-chip" data-verdict="${r.verdict}">${r.verdict}</span>
        <span class="fd-verdict-conf">${r.confidence}% confident</span>
      </div>
      <div class="fd-verdict-reason">${r.reason || ''}</div>
    `;
  } catch (e) {
    panel.innerHTML = `<div class="fd-verdict-error">Request failed: ${e.message}</div>`;
  } finally {
    btn.disabled = false;
  }
}

async function openFileDetail(path) {
  _fdCurrentPath = path;
  $('#fileDetail').classList.remove('hidden');
  $('#fileDetailVerdictPanel').classList.add('hidden');
  $('#fileDetailVerdictPanel').innerHTML = '';
  const body = $('#fileDetailBody');
  body.innerHTML = `
    <div class="fd-preview skel-block" style="border-radius:0"></div>
    <div class="fd-info">
      <span class="skel-line lg" style="height:18px"></span>
      <span class="skel-line sm" style="margin-top:8px"></span>
    </div>
    <div class="fd-section">
      <span class="skel-line sm"></span>
      <span class="skel-line" style="margin-top:10px"></span>
      <span class="skel-line" style="margin-top:6px;width:90%"></span>
      <span class="skel-line" style="margin-top:6px;width:80%"></span>
    </div>
  `;

  const d = await api(`/api/file/detail?path=${encodeURIComponent(path)}`);
  if (!d.exists) {
    body.innerHTML = '<div class="empty" style="padding:40px;">File no longer exists.</div>';
    return;
  }
  const previewable = d.kind === 'image' || d.kind === 'video' || d.kind === 'pdf';
  body.innerHTML = `
    <div class="fd-preview">
      ${previewable
        ? `<img src="/api/file/preview?path=${encodeURIComponent(path)}&size=600" alt="" onerror="this.outerHTML='<div class=&quot;placeholder&quot;><div class=&quot;ph-icon&quot;>${KIND_GLYPH[d.kind] || '·'}</div><div class=&quot;ph-ext&quot;>${(d.ext || '').toUpperCase()}</div></div>'">`
        : `<div class="placeholder"><div class="ph-icon">${KIND_GLYPH[d.kind] || '·'}</div><div class="ph-ext">${(d.ext || 'FILE').toUpperCase()}</div></div>`
      }
    </div>
    <div class="fd-info">
      <div class="fd-name">${d.name}</div>
      <div class="fd-path">${shortPath(d.path)}</div>
    </div>
    <div class="fd-section">
      <div class="fd-section-label">Details</div>
      <div class="fd-row"><span class="key">Size</span><span class="val">${bytes(d.size)}</span></div>
      <div class="fd-row"><span class="key">Kind</span><span class="val">${d.kind}${d.mime ? ' · ' + d.mime : ''}</span></div>
      <div class="fd-row"><span class="key">Created</span><span class="val">${new Date(d.btime * 1000).toLocaleDateString()} · ${ago(d.btime)}</span></div>
      <div class="fd-row"><span class="key">Modified</span><span class="val">${new Date(d.mtime * 1000).toLocaleDateString()} · ${ago(d.mtime)}</span></div>
      <div class="fd-row"><span class="key">Last opened</span><span class="val">${ago(d.atime)}</span></div>
    </div>
    ${d.file_command ? `
      <div class="fd-section">
        <div class="fd-section-label">Format (from <code>file</code>)</div>
        <div class="fd-format mono">${d.file_command}</div>
      </div>
    ` : ''}
    ${d.parent ? `
      <div class="fd-section">
        <div class="fd-section-label">Parent folder</div>
        <div class="fd-parent mono">${shortPath(d.parent)}</div>
        <button class="btn ghost" id="fdParentReveal" style="margin-top:8px">Show parent in Finder</button>
      </div>
    ` : ''}
    ${d.source_urls?.length ? `
      <div class="fd-section">
        <div class="fd-section-label">Source · where it came from</div>
        ${d.source_urls.map(u => `<div class="fd-source-url">${u}</div>`).join('')}
      </div>
    ` : ''}
    <div class="fd-section">
      <div class="fd-section-label">Quick actions</div>
      <button class="btn primary full" id="fdQuickLook">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
        Quick Look (Space)
      </button>
    </div>
  `;
  const ql = $('#fdQuickLook');
  if (ql) ql.onclick = () => api('/api/file/quicklook', { method: 'POST', body: JSON.stringify({ path }) });
  const pr = $('#fdParentReveal');
  if (pr && d.parent) pr.onclick = () => api('/api/file/reveal', { method: 'POST', body: JSON.stringify({ path: d.parent }) });
}

/* ── Sweep mode (the killer feature) ──────────────────────────────────── */
const sweepState = {
  queue: [],
  index: 0,
  trashed: [],
  kept: new Set(),
};

async function renderSweep() {
  const stage = $('#sweepStage');
  stage.innerHTML = skeletonSweepCard();
  if (!sweepState.queue.length) {
    sweepState.queue = await cachedApi('/api/sweep/queue?limit=300');
    sweepState.index = 0;
    sweepState.trashed = [];
    sweepState.kept = new Set();
  }
  showSweepCard();
  bindSweepKeys();
}

function showSweepCard() {
  const stage = $('#sweepStage');
  while (sweepState.index < sweepState.queue.length &&
         sweepState.kept.has(sweepState.queue[sweepState.index].path)) {
    sweepState.index += 1;
  }
  updateSweepProgress();

  if (!sweepState.queue.length) {
    stage.innerHTML = `
      <div class="sweep-empty">
        <div class="sweep-empty-art">✓</div>
        <div>No big idle files to review.</div>
        <div class="dim">Sweep finds &gt;50 MB files you haven't opened in 6+ months.</div>
      </div>
    `;
    return;
  }

  if (sweepState.index >= sweepState.queue.length) {
    const queued = sweepState.trashed.length;
    const totalSize = sweepState.trashed.reduce((a, p) => a + (p.size || 0), 0);
    stage.innerHTML = `
      <div class="sweep-empty">
        <div class="sweep-empty-art">✓</div>
        <div><strong>${sweepState.queue.length}</strong> file${sweepState.queue.length === 1 ? '' : 's'} reviewed.</div>
        ${queued ? `<div>${queued} queued for cleanup · ${bytes(totalSize)}</div>` : '<div class="dim">Nothing to clean.</div>'}
        ${queued ? `<button class="btn primary big" id="sweepCommit">Quarantine ${queued} now</button>` : ''}
      </div>
    `;
    if (queued) {
      $('#sweepCommit').onclick = async () => {
        const r = await api('/api/quarantine', {
          method: 'POST',
          body: JSON.stringify({ paths: sweepState.trashed.map(t => t.path) }),
        });
        const ok = r.results.filter(x => x.status === 'quarantined');
        const freed = ok.reduce((a, b) => a + (b.size || 0), 0);
        toast(`Quarantined ${ok.length} · freed ${bytes(freed)}`, 'safe');
        sweepState.queue = [];
        sweepState.trashed = [];
        await refreshOverview();
        await refreshQuarantineBadge();
        renderSweep();
      };
    }
    return;
  }

  const item = sweepState.queue[sweepState.index];
  const kind = inferKind(item.path);
  const ext = (item.path.split('.').pop() || '').toUpperCase().slice(0, 8);
  const card = document.createElement('div');
  card.className = 'sweep-card';
  card.innerHTML = `
    <div class="sweep-preview">
      ${(kind === 'image' || kind === 'video' || kind === 'pdf')
        ? `<img src="/api/file/preview?path=${encodeURIComponent(item.path)}&size=720" alt="" onerror="this.outerHTML='<div class=&quot;placeholder&quot;><div class=&quot;ph-icon&quot;>${KIND_GLYPH[kind] || '·'}</div><div class=&quot;ph-ext&quot;>${ext}</div></div>'">`
        : `<div class="placeholder"><div class="ph-icon">${KIND_GLYPH[kind] || '·'}</div><div class="ph-ext">${ext}</div></div>`
      }
    </div>
    <div class="sweep-info">
      <div class="si-name">${item.path.split('/').pop()}</div>
      <div class="si-path">${shortPath(item.path)}</div>
    </div>
    <div class="sweep-meta-grid">
      <div><div class="sweep-meta">Size</div><div class="sweep-meta-val mono">${bytes(item.size)}</div></div>
      <div><div class="sweep-meta">Last opened</div><div class="sweep-meta-val">${ago(item.atime)}</div></div>
      <div><div class="sweep-meta">Modified</div><div class="sweep-meta-val">${ago(item.mtime)}</div></div>
      <div><div class="sweep-meta">Kind</div><div class="sweep-meta-val">${item.subcategory || item.category || kind}</div></div>
    </div>
    <div id="sweepSource"></div>
    <div id="sweepVerdictSlot" class="sweep-verdict-slot"></div>
    <div class="sweep-actions">
      <button class="ask-sweeper-btn sweep-ask ${aiActive() ? '' : 'hidden'}" title="What does Sweeper think?">
        <span class="glyph">◐</span> Ask Sweeper
      </button>
      <button class="sweep-action trash" data-act="trash"><span>Trash</span><span class="key">←</span></button>
      <button class="sweep-action skip"  data-act="skip"><span>Skip</span><span class="key">↓</span></button>
      <button class="sweep-action keep"  data-act="keep"><span>Keep</span><span class="key">→</span></button>
    </div>
  `;
  stage.innerHTML = '';
  stage.appendChild(card);

  // Async-load source xattr (where it was downloaded from)
  api(`/api/file/detail?path=${encodeURIComponent(item.path)}`).then(d => {
    if (d.source_urls?.length) {
      const slot = $('#sweepSource');
      if (slot) slot.innerHTML = `
        <div class="sweep-source">
          <span class="sweep-source-label">From</span>
          <span class="sweep-source-url">${d.source_urls[0]}</span>
        </div>
      `;
    }
  }).catch(() => {});

  // Sweeper verdict — prefill from cache if we have one, else show button.
  const verdictSlot = $('#sweepVerdictSlot');
  const askSweepBtn = card.querySelector('.sweep-ask');
  const existingV = aiFeature.verdicts.get(item.path);
  if (existingV && existingV.ok) {
    verdictSlot.innerHTML = `
      <div class="sweep-verdict">
        <span class="row-verdict-chip" data-verdict="${existingV.verdict}">${existingV.verdict}</span>
        <span class="sweep-verdict-reason">${existingV.reason || ''}</span>
        <span class="sweep-verdict-conf">${existingV.confidence}%</span>
      </div>
    `;
    askSweepBtn?.classList.add('hidden');
  }
  if (askSweepBtn) {
    askSweepBtn.onclick = async () => {
      askSweepBtn.disabled = true;
      askSweepBtn.innerHTML = `<span class="glyph">⋯</span> Thinking…`;
      try {
        const r = await api('/api/file/verdict', {
          method: 'POST',
          body: JSON.stringify({ path: item.path }),
        });
        if (!r.ok) {
          verdictSlot.innerHTML = `<div class="sweep-verdict-error">${r.error || 'Failed'}</div>`;
          askSweepBtn.innerHTML = `<span class="glyph">◐</span> Ask Sweeper`;
          askSweepBtn.disabled = false;
          return;
        }
        aiFeature.verdicts.set(item.path, r);
        verdictSlot.innerHTML = `
          <div class="sweep-verdict">
            <span class="row-verdict-chip" data-verdict="${r.verdict}">${r.verdict}</span>
            <span class="sweep-verdict-reason">${r.reason || ''}</span>
            <span class="sweep-verdict-conf">${r.confidence}%</span>
          </div>
        `;
        askSweepBtn.classList.add('hidden');
      } catch (e) {
        verdictSlot.innerHTML = `<div class="sweep-verdict-error">${e.message}</div>`;
        askSweepBtn.innerHTML = `<span class="glyph">◐</span> Ask Sweeper`;
        askSweepBtn.disabled = false;
      }
    };
  }

  card.querySelectorAll('.sweep-action').forEach(b => {
    b.onclick = () => sweepDecision(b.dataset.act, card);
  });
}

function sweepDecision(act, card) {
  const item = sweepState.queue[sweepState.index];
  if (!item) return;
  if (act === 'trash') {
    sweepState.trashed.push(item);
    card.classList.add('swiping-left');
  } else if (act === 'keep') {
    sweepState.kept.add(item.path);
    card.classList.add('swiping-right');
  } else {
    card.classList.add('swiping-down');
  }
  setTimeout(() => {
    sweepState.index += 1;
    showSweepCard();
  }, 240);
}

function updateSweepProgress() {
  const total = sweepState.queue.length;
  const done = Math.min(sweepState.index, total);
  $('#sweepProgressText').textContent = `${done} / ${total}`;
  $('#sweepProgressFill').style.width = total ? `${(done / total) * 100}%` : '0%';
  const queuedSize = sweepState.trashed.reduce((a, p) => a + (p.size || 0), 0);
  $('#sweepQueued').textContent = sweepState.trashed.length
    ? `${sweepState.trashed.length} queued · ${bytes(queuedSize)}`
    : 'Decide one file at a time';
}

let _sweepKeysBound = false;
function bindSweepKeys() {
  if (_sweepKeysBound) return;
  _sweepKeysBound = true;
  document.addEventListener('keydown', e => {
    if (state.module !== 'sweep') return;
    if (sweepState.index >= sweepState.queue.length) return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    const card = $('.sweep-card');
    if (!card) return;
    if (e.key === 'ArrowLeft')  { e.preventDefault(); sweepDecision('trash', card); }
    if (e.key === 'ArrowRight') { e.preventDefault(); sweepDecision('keep', card); }
    if (e.key === 'ArrowDown')  { e.preventDefault(); sweepDecision('skip', card); }
    if (e.key === ' ') {
      e.preventDefault();
      const item = sweepState.queue[sweepState.index];
      if (item) api('/api/file/open', { method: 'POST', body: JSON.stringify({ path: item.path }) });
    }
  });
}

bindFileDetail();

boot();
