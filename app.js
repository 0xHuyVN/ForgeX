/* ─── app.js - Tool Review Master V2.1.1 ─── */

/* ─── DOM helpers (XSS-safe) ─── */
function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text == null ? '' : String(text);
  return d.innerHTML;
}

/**
 * el(tag, props, children) — minimal XSS-safe element factory.
 *
 * - Sets attributes via setAttribute (never direct property assignment for
 *   string-typed attrs that can carry script).
 * - textContent for string children; never builds an HTML string.
 * - Listeners are attached via addEventListener, not via the on* shortcut.
 *
 * Usage:
 *   el('div', { class: 'row' }, [
 *     el('span', { class: 'name' }, [userControlledName]),
 *     el('button', { class: 'btn', onclick: () => doIt() }, ['Delete']),
 *   ])
 */
function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null) continue;
    if (key === 'class' || key === 'className') {
      node.className = String(value);
    } else if (key === 'style' && typeof value === 'object') {
      Object.assign(node.style, value);
    } else if (key === 'dataset' && typeof value === 'object') {
      for (const [dk, dv] of Object.entries(value)) node.dataset[dk] = dv;
    } else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'html') {
      // Explicit opt-in: caller is asserting the string is trusted.
      node.innerHTML = String(value);
    } else {
      node.setAttribute(key, String(value));
    }
  }
  for (const child of [].concat(children)) {
    if (child == null || child === false) continue;
    if (child instanceof Node) {
      node.appendChild(child);
    } else {
      node.appendChild(document.createTextNode(String(child)));
    }
  }
  return node;
}

/**
 * clearChildren(node) — empties an element without dropping its identity
 * (useful for re-rendering into a select/container that other code still
 * references by reference).
 */
function clearChildren(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}
window.el = el;
window.escapeHtml = escapeHtml;
window.clearChildren = clearChildren;

/* ─── API Layer ─── */
const API_BASE = window.location.origin + '/api';

const UI = {
  cache: new Map(),
  id(id) {
    if (!this.cache.has(id) || !document.body.contains(this.cache.get(id))) {
      this.cache.set(id, document.getElementById(id));
    }
    return this.cache.get(id);
  },
  get videoPath() { return this.id('inp-video-path'); },
  get srtPath() { return this.id('inp-srt-path'); },
  get outputPath() { return this.id('inp-output-path'); },
};

function getInputMediaPath() {
  return UI.videoPath?.value || UI.srtPath?.value || '';
}

async function ensureCurrentProject() {
  if (currentProjectId) return currentProjectId;
  const project = await apiPost('/projects', {
    name: 'project_' + Date.now(),
    project_preset: document.getElementById('sel-project-preset')?.value || 'Movie Review',
  });
  if (project?.id) {
    currentProjectId = project.id;
    setTimeout(() => loadTimeline(currentProjectId), 500);
  }
  return currentProjectId;
}

/* ─── Toast notification system ─── */
function showToast(message, type = 'error', duration = 4000) {
  try {
    const existing = document.getElementById('toast-container');
    let container = existing;
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      container.style.cssText = 'position:fixed;right:16px;bottom:34px;z-index:99999;pointer-events:none;display:flex;flex-direction:column;gap:8px;max-width:360px;max-height:320px;overflow:hidden';
      document.body.appendChild(container);
    }
    while (container.children.length >= 3) {
      container.firstElementChild?.remove();
    }
    const tone = type === 'error'
      ? '#ef4444'
      : type === 'success'
        ? '#22c55e'
        : type === 'warn'
          ? '#e3b341'
          : '#8aa0b8';
    const toast = document.createElement('div');
    toast.style.cssText = `pointer-events:auto;background:#111820;color:#f5f7fa;padding:10px 14px;border-radius:8px;border:1px solid rgba(255,255,255,0.1);border-left:3px solid ${tone};font-size:12px;box-shadow:0 18px 45px rgba(0,0,0,0.42);animation:slideIn 0.3s ease;cursor:pointer;word-break:break-word`;
    toast.textContent = message;
    toast.onclick = () => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); };
    container.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, duration);
  } catch (_) { }
}

let latestQueueData = null;
const queueListeners = new Set();
let currentProjectId = null;

function onQueueChange(fn) {
  if (typeof fn !== 'function') return () => {};
  queueListeners.add(fn);
  return () => {
    queueListeners.delete(fn);
  };
}

class QueueSSEManager {
  constructor() {
    this.eventSource = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
    this.reconnectDelay = 3000;
    this.reconnectTimer = null;
  }

  connect() {
    this.disconnect(false);
    try {
      this.eventSource = new EventSource(API_BASE + '/queue/events');
      this.eventSource.onopen = () => {
        this.reconnectAttempts = 0;
      };
      this.eventSource.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          this.handleMessage(msg);
        } catch (err) {
          addClientLog('error', 'SSE parse error', err.message || String(err));
        }
      };
      this.eventSource.onerror = () => {
        this.scheduleReconnect();
      };
    } catch (err) {
      addClientLog('error', 'SSE connection error', err.message || String(err));
      this.scheduleReconnect();
    }
  }

  handleMessage(msg) {
    if (msg.type === 'queue_changed') {
      latestQueueData = msg.data || [];
      queueListeners.forEach(fn => {
        try { fn(latestQueueData); } catch (err) { console.error(err); }
      });
      return;
    }

    if (msg.type === 'timeline_updated') {
      const projectId = msg.data?.project_id;
      if (!projectId || !currentProjectId || Number(projectId) === Number(currentProjectId)) {
        loadTimeline(currentProjectId || projectId).catch(() => { });
      }
      return;
    }

    if (msg.type === 'subtitle_updated') {
      const projectId = msg.data?.project_id;
      if (!projectId || !currentProjectId || Number(projectId) === Number(currentProjectId)) {
        if (msg.data?.path) {
          const srtInput = document.getElementById('inp-srt-path');
          if (srtInput) srtInput.value = msg.data.path;
        }
        loadTimeline(currentProjectId || projectId).catch(() => { });
        showToast('Phu de da cap nhat', 'success', 2500);
      }
      return;
    }

    if (msg.type === 'download_updated') {
      updateDownloadProgressUI(msg.data || {});
    }
  }

  scheduleReconnect() {
    this.disconnect(false);
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      addClientLog('error', 'SSE max reconnect attempts reached', '');
      showToast('Mat ket noi realtime. Hay tai lai tool neu tien trinh khong cap nhat.', 'warn', 6000);
      return;
    }
    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  disconnect(clearListeners = true) {
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    if (this.eventSource) {
      try { this.eventSource.close(); } catch (_) { }
      this.eventSource = null;
    }
    if (clearListeners) queueListeners.clear();
  }
}

const queueSSEManager = new QueueSSEManager();
window.queueSSEManager = queueSSEManager;
queueSSEManager.connect();
window.addEventListener('beforeunload', () => queueSSEManager.disconnect());

/* ─── Client-side error log ─── */
const clientLogs = [];
const MAX_CLIENT_LOGS = 200;

function addClientLog(level, message, detail) {
  clientLogs.unshift({
    timestamp: new Date().toISOString(),
    level: level || 'info',
    message: message || '',
    detail: detail || '',
    source: 'client',
  });
  if (clientLogs.length > MAX_CLIENT_LOGS) clientLogs.length = MAX_CLIENT_LOGS;
  // Also try to send to backend for persistence
  try {
    fetch(API_BASE + '/queue/log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level, message: message + (detail ? ' | ' + detail : '') }),
    }).catch(() => { });
  } catch (_) { }
}

// Capture global errors
window.onerror = function (msg, source, lineno, colno, err) {
  const detail = err ? err.stack : `${source}:${lineno}:${colno}`;
  addClientLog('error', `[GLOBAL] ${msg}`, detail);
};
window.addEventListener('unhandledrejection', function (e) {
  const msg = e.reason?.message || e.reason || 'Unknown';
  addClientLog('error', `[PROMISE] ${msg}`, e.reason?.stack || '');
});

async function api(method, path, body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  try {
    const res = await fetch(API_BASE + path, opts);
    if (!res.ok) {
      const errBody = await res.text().catch(() => '');
      throw new Error(`${res.status}${errBody ? ': ' + errBody.slice(0, 200) : ''}`);
    }
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) return await res.json();
    return await res.text();
  } catch (e) {
    const msg = `API ${method} ${path} failed: ${e.message}`;
    console.warn(msg);
    addClientLog('error', msg, e.stack);
    showToast(msg, 'error', 5000);
    return null;
  }
}

function apiGet(path) { return api('GET', path); }
async function apiPost(path, body) {
  if (path === '/ai/summary' && body && body.model && !body.engine) {
    body = { ...body, engine: body.model };
    delete body.model;
  }
  if ((path === '/ai/characters' || path === '/ai/speakers') && body && !body.video_path) {
    body = { ...body, video_path: getInputMediaPath() };
  }
  if (path === '/ai/hashtags' && body && !body.text) {
    body = { ...body, text: document.getElementById('inp-ai-summary')?.value || '' };
  }
  const res = await api('POST', path, body);
  if (path && path.startsWith('/ai/') && res && res.id) {
    const queuedText = `Queued job #${res.id}. Open Queue to view output when completed.`;
    if (path === '/ai/summary') return { ...res, summary: queuedText };
    if (path === '/ai/recap') return { ...res, recap: queuedText };
    if (path === '/ai/characters') return { ...res, characters: [queuedText] };
    if (path === '/ai/speakers') return { ...res, speakers: { queued: queuedText } };
    if (path === '/ai/title') return { ...res, titles: [queuedText] };
    if (path === '/ai/hashtags') return { ...res, hashtags: [queuedText] };
  }
  return res;
}
function apiPut(path, body) { return api('PUT', path, body); }
function apiDel(path) { return api('DELETE', path); }

const API_ERROR_MESSAGES = {
  400: 'Yeu cau khong hop le',
  401: 'Can dang nhap lai',
  403: 'Khong co quyen truy cap',
  404: 'Khong tim thay tai nguyen',
  429: 'Qua nhieu yeu cau, hay thu lai sau',
  500: 'Loi server, hay thu lai sau',
  503: 'Server dang ban, hay thu lai sau',
};

function getErrorMessage(status, defaultMsg) {
  return API_ERROR_MESSAGES[status] || defaultMsg || 'Loi khong xac dinh';
}

async function apiWithErrorHandling(method, path, body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== null && body !== undefined) opts.body = JSON.stringify(body);
  try {
    const res = await fetch(API_BASE + path, opts);
    if (!res.ok) {
      const errBody = await res.text().catch(() => '');
      const error = new Error(getErrorMessage(res.status, errBody.slice(0, 120)));
      error.status = res.status;
      error.body = errBody;
      throw error;
    }
    return await res.json();
  } catch (e) {
    const msg = `API ${method} ${path} failed: ${e.message}`;
    console.warn(msg);
    addClientLog('error', msg, e.stack || '');
    showToast(e.message || msg, 'error', 5000);
    return null;
  }
}

class ValidationError extends Error {
  constructor(message, rule, value) {
    super(message);
    this.name = 'ValidationError';
    this.rule = rule;
    this.value = value;
  }
}

const ValidationRules = {
  filePath: {
    validate(value) {
      if (!value || typeof value !== 'string') return false;
      const trimmed = value.trim();
      if (!trimmed) return false;
      const pathWithoutDrive = trimmed.replace(/^[a-zA-Z]:[\\/]/, '');
      return !/[<>"|?*\n\r\t]/.test(pathWithoutDrive);
    },
    message: 'Duong dan file khong hop le',
  },
  videoFile: {
    validate(path) {
      if (!ValidationRules.filePath.validate(path)) return false;
      return /\.(mp4|avi|mov|mkv|flv|webm|m4v|mpg|mpeg)$/i.test(path);
    },
    message: 'File phai la video hop le',
  },
  timestamp: {
    validate(value, maxDuration = 86400) {
      const num = Number(value);
      return Number.isFinite(num) && num >= 0 && num <= maxDuration;
    },
    message: 'Thoi gian khong hop le',
  },
  timeRange: {
    validate(value, endValue, maxDuration = 86400) {
      const start = Array.isArray(value) ? value[0] : value;
      const end = Array.isArray(value) ? value[1] : endValue;
      const s = Number(start);
      const e = Number(end);
      return Number.isFinite(s) && Number.isFinite(e) && s >= 0 && e > s && e <= maxDuration;
    },
    message: 'Thoi gian bat dau phai nho hon thoi gian ket thuc',
  },
  language: {
    validate(lang) {
      return ['vi', 'en', 'zh', 'es', 'fr', 'de', 'ja', 'ko', 'ar', 'pt', 'ru'].includes(lang);
    },
    message: 'Ngon ngu khong duoc ho tro',
  },
  projectName: {
    validate(name) {
      return typeof name === 'string' && /^[a-zA-Z0-9_\- ]{1,100}$/.test(name.trim());
    },
    message: 'Ten du an phai tu 1-100 ky tu',
  },
  pathArray: {
    validate(paths) {
      return Array.isArray(paths) && paths.length >= 2 && paths.every(p => ValidationRules.filePath.validate(p));
    },
    message: 'Can it nhat 2 duong dan file hop le',
  },
};

function validate(value, rule, customMessage, ...args) {
  const ruleObj = ValidationRules[rule];
  if (!ruleObj) throw new Error(`Unknown validation rule: ${rule}`);
  if (!ruleObj.validate(value, ...args)) {
    throw new ValidationError(customMessage || ruleObj.message, rule, value);
  }
  return true;
}

/* ─── Health check & Stats ─── */
async function loadDashboard() {
  const health = await apiGet('/health');
  const stats = await apiGet('/stats');
  if (stats) {
    const q = stats.queue || {};
    document.querySelectorAll('.queue-stat').forEach(el => {
      const key = el.dataset.stat;
      if (key in q) el.textContent = q[key];
    });
    const u = stats.api_usage || {};
    document.querySelectorAll('.api-stat').forEach(el => {
      const key = el.dataset.stat;
      if (key in u) el.textContent = u[key];
    });
  }
  /* Load real asset counts */
  const counts = await apiGet('/assets/counts');
  if (counts) {
    document.querySelectorAll('.asset-item').forEach(item => {
      const label = item.querySelector('span')?.textContent?.toLowerCase();
      if (label && counts[label] !== undefined) {
        const countEl = item.querySelector('.asset-count');
        if (countEl) countEl.textContent = counts[label];
      } else if (label === 'watermark' && counts['branding'] !== undefined) {
        const countEl = item.querySelector('.asset-count');
        if (countEl) countEl.textContent = counts['branding'];
      }
    });
  }
  /* Load real preset list */
  const presets = await apiGet('/presets');
  if (presets && typeof presets === 'object') {
    const names = Object.keys(presets).filter(k => presets[k] && presets[k].name);
    if (names.length) {
      const presetSelects = document.querySelectorAll('#sel-project-preset, #sel-export-preset');
      presetSelects.forEach(sel => {
        const currentVal = sel.value;
        sel.innerHTML = names.map(n => `<option>${escapeHtml(presets[n].name || n)}</option>`).join('');
        if ([...sel.options].some(o => o.value === currentVal)) sel.value = currentVal;
      });
    };
  }
}

/* Tab switching – Processing Panel */
(function () {
  var tabs = document.querySelectorAll('#processing-tabs .tab');
  var contents = document.querySelectorAll('.tab-content');
  function switchTab(btn) {
    try {
      tabs.forEach(function (t) { t.classList.remove('active'); });
      contents.forEach(function (c) { c.classList.remove('active'); });
      btn.classList.add('active');
      var target = btn.dataset.target;
      if (target) {
        var el = document.getElementById(target);
        if (el) el.classList.add('active');
      }
    } catch (e) { console.warn('Tab switch error:', e); }
  }
  tabs.forEach(function (btn) {
    btn.addEventListener('click', function () { switchTab(btn); });
  });
  // Expose for nav click simulation
  window._switchTab = switchTab;
})();

/* Asset Library Item click handlers */
(function() {
  document.querySelectorAll('.asset-item').forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      document.querySelectorAll('.asset-item').forEach(n => n.classList.remove('active'));
      item.classList.add('active');
      const text = item.textContent.trim().toLowerCase();
      if (text.includes('video')) {
        document.getElementById('work-mode-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else if (text.includes('nhạc nền')) {
        const tabBtn = document.querySelector(`#processing-tabs .tab[data-target="tab-music"]`);
        if (tabBtn) {
          if (window._switchTab) window._switchTab(tabBtn);
          else tabBtn.click();
        }
        document.getElementById('processing-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else if (text.includes('giọng đọc')) {
        const tabBtn = document.querySelector(`#processing-tabs .tab[data-target="tab-voice"]`);
        if (tabBtn) {
          if (window._switchTab) window._switchTab(tabBtn);
          else tabBtn.click();
        }
        document.getElementById('processing-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else if (text.includes('phụ đề')) {
        const tabBtn = document.querySelector(`#processing-tabs .tab[data-target="tab-subtitle"]`);
        if (tabBtn) {
          if (window._switchTab) window._switchTab(tabBtn);
          else tabBtn.click();
        }
        document.getElementById('processing-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else if (text.includes('đóng dấu')) {
        const tabBtn = document.querySelector(`#processing-tabs .tab[data-target="tab-enhance"]`);
        if (tabBtn) {
          if (window._switchTab) window._switchTab(tabBtn);
          else tabBtn.click();
        }
        document.getElementById('processing-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else if (text.includes('mẫu thiết lập')) {
        document.getElementById('work-mode-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });
})();

/* Sub-tabs inside feature panels */
document.querySelectorAll('.sub-tab-bar').forEach(bar => {
  bar.querySelectorAll('.sub-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      const contentRoot = bar.parentElement;
      contentRoot.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
      contentRoot.querySelectorAll('.sub-content').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      const target = document.getElementById(btn.dataset.subtarget);
      if (target) target.classList.add('active');
    });
  });
});

/* Live range values */
document.querySelectorAll('.live-range').forEach(range => {
  const valueEl = range.nextElementSibling;
  const hasPercent = valueEl && valueEl.textContent.includes('%');
  range.addEventListener('input', () => {
    if (valueEl) valueEl.textContent = `${range.value}${hasPercent ? '%' : ''}`;
  });
});

/* ─── Load button (API) ─── */
document.getElementById('btn-browse-video')?.addEventListener('click', async () => {
  const res = await apiGet('/system/browse?type=file&ext=video');
  if (res && res.path) {
    document.getElementById('inp-video-path').value = res.path;
    loadVideoPreview();
  }
});

document.getElementById('btn-browse-srt')?.addEventListener('click', async () => {
  const res = await apiGet('/system/browse?type=file&ext=srt');
  if (res && res.path) {
    document.getElementById('inp-srt-path').value = res.path;
  }
});

document.getElementById('btn-browse-output')?.addEventListener('click', async () => {
  const res = await apiGet('/system/browse?type=folder');
  if (res && res.path) {
    document.getElementById('inp-output-path').value = res.path;
  }
});

async function resolveOutputFolderPath(path) {
  if (path) return path;

  const outputInputPath = document.getElementById('inp-output-path')?.value || '';
  if (outputInputPath) return outputInputPath;

  const latestExportPath = currentProjectId
    ? '/export/latest?project_id=' + encodeURIComponent(currentProjectId)
    : '/export/latest';
  const latestExport = await apiGet(latestExportPath);
  if (latestExport?.path) return latestExport.path;

  const jobs = await apiGet('/queue');
  const completedOutput = (jobs || [])
    .filter(job => job?.status === 'completed' && job?.output_path)
    .sort((a, b) => Number(b.id || 0) - Number(a.id || 0))[0]?.output_path;
  return completedOutput || '';
}

async function openOutputFolder(path) {
  const targetPath = await resolveOutputFolderPath(path);
  if (!targetPath) {
    showToast('Chua co thu muc output de mo', 'warn', 2200);
    return;
  }
  const res = await apiGet('/system/open-folder?path=' + encodeURIComponent(targetPath));
  if (res?.ok) {
    showToast('Da mo folder ket qua', 'success', 1800);
  } else {
    showToast(res?.error || 'Khong mo duoc folder ket qua', 'error', 2500);
  }
}

document.getElementById('btn-open-output-folder')?.addEventListener('click', () => {
  openOutputFolder();
});

document.getElementById('btn-load')?.addEventListener('click', async () => {
  const srtPath = document.getElementById('inp-srt-path')?.value;
  const videoPath = document.getElementById('inp-video-path')?.value;
  const preset = document.getElementById('sel-project-preset')?.value || 'Movie Review';

  const loadProgress = document.getElementById('load-progress');
  const loadPct = document.getElementById('load-pct');
  if (loadProgress) loadProgress.style.width = '10%';
  if (loadPct) loadPct.textContent = '10%';

  try {
    // 1. Create project
    const project = await apiPost('/projects', {
      name: 'project_' + Date.now(),
      project_preset: preset,
    });
    if (!project) throw new Error('Không thể tạo dự án');
    currentProjectId = project.id;

    if (loadProgress) loadProgress.style.width = '35%';
    if (loadPct) loadPct.textContent = '35%';

    // 2. Sync video if exists
    if (videoPath) {
      await apiPost(`/timeline/${currentProjectId}/video`, { path: videoPath });
    }

    if (loadProgress) loadProgress.style.width = '65%';
    if (loadPct) loadPct.textContent = '65%';

    // 3. Import subtitle if exists
    if (srtPath) {
      await apiPost('/subtitle/import-path', { path: srtPath, project_id: currentProjectId });
    }

    if (loadProgress) loadProgress.style.width = '100%';
    if (loadPct) loadPct.textContent = '100%';

    // 4. Load the populated timeline
    await loadTimeline(currentProjectId);
  } catch (e) {
    console.error('[Load] Error loading project data:', e);
    if (loadProgress) loadProgress.style.width = '0%';
    if (loadPct) loadPct.textContent = '0%';
    alert('Lỗi khi load dữ liệu: ' + (e.message || e));
  }
});

/* ─── Execute button (API) ─── */
const executeBtn = document.getElementById('btn-execute');
const executeCountMax = 5;
let executeCount = executeCountMax;

// Intentionally a no-op: the render button is now a plain static button and
// does not change appearance based on job state. Callers still invoke this
// for backwards-compatibility; queue progress is shown in the queue panel.
function updateExecuteBtnState(statusText, label = '', state = 'idle') {
  if (!executeBtn) return;
  executeBtn.classList.remove('executing', 'failed');
}

const LANG_MAP_EXEC = {
  en: 'en',
  zh: 'zh',
  ja: 'ja',
  ko: 'ko',
  vi: 'vi',
  'Tiếng Anh': 'en',
  'Tiếng Trung': 'zh',
  'Tiếng Nhật': 'ja',
  'Tiếng Hàn': 'ko',
  'Tiếng Việt': 'vi',
};

function getPipelineTranslateEngine(sourceLang, targetLang) {
  return document.getElementById('sel-pipeline-translate-engine')?.value || '';
}

function getPipelineTranslateModel(engine) {
  return getSubtitleModel(engine || getPipelineTranslateEngine());
}

const AI_PROVIDER_PRESETS = {
  openrouter: { base_url: 'https://openrouter.ai/api/v1', model: 'google/gemma-4-31b' },
  openai: { base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  gemini: { base_url: 'https://generativelanguage.googleapis.com/v1beta', model: 'gemini-2.0-flash' },
  nvidia: { base_url: 'https://integrate.api.nvidia.com/v1', model: 'nvidia/nemotron-3-super' },
  ollama: { base_url: 'http://127.0.0.1:11434/v1', model: 'qwen2.5:7b' },
  custom: { base_url: 'http://127.0.0.1:8000/v1', model: '' },
};

function getAIProviderConfigFromUI() {
  return {
    provider: document.getElementById('sel-ai-provider')?.value || 'openrouter',
    api_key: document.getElementById('inp-ai-api-key')?.value || '',
    base_url: document.getElementById('inp-ai-base-url')?.value || '',
    model: document.getElementById('inp-ai-model')?.value || '',
    temperature: Number(document.getElementById('inp-ai-temperature')?.value || 0.2),
    max_tokens: Number(document.getElementById('inp-ai-max-tokens')?.value || 4096),
    fallback: document.getElementById('inp-ai-fallback')?.value || '',
  };
}

function refreshAIProviderModelLabel() {
  const label = document.getElementById('ai-provider-active-model');
  if (!label) return;
  const cfg = getAIProviderConfigFromUI();
  label.textContent = `${cfg.provider || 'provider'} / ${cfg.model || 'no model selected'}`;
}

function applyAIProviderPreset(overwriteModel = true) {
  const provider = document.getElementById('sel-ai-provider')?.value || 'openrouter';
  const preset = AI_PROVIDER_PRESETS[provider] || AI_PROVIDER_PRESETS.custom;
  const baseInput = document.getElementById('inp-ai-base-url');
  const modelInput = document.getElementById('inp-ai-model');
  if (baseInput && (!baseInput.value || overwriteModel)) baseInput.value = preset.base_url || '';
  if (modelInput && (!modelInput.value || overwriteModel)) modelInput.value = preset.model || '';
  refreshAIProviderModelLabel();
}

document.getElementById('sel-ai-provider')?.addEventListener('change', () => applyAIProviderPreset(true));
['inp-ai-model', 'inp-ai-base-url', 'inp-ai-temperature', 'inp-ai-max-tokens', 'inp-ai-fallback'].forEach(id => {
  document.getElementById(id)?.addEventListener('input', refreshAIProviderModelLabel);
});

document.getElementById('btn-ai-provider-test')?.addEventListener('click', async () => {
  const status = document.getElementById('ai-provider-test-status');
  const btn = document.getElementById('btn-ai-provider-test');
  const old = btn?.innerHTML;
  if (status) status.textContent = 'Ping...';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Testing';
  }
  try {
    const result = await apiPost('/ai/providers/test', getAIProviderConfigFromUI());
    if (result?.ok) {
      const quota = result.quota && Object.keys(result.quota).length ? ` | quota ${JSON.stringify(result.quota)}` : '';
      if (status) status.textContent = `Connected: ${result.provider} / ${result.model} | ${result.latency_ms}ms${quota}`;
      showToast('AI Provider connected', 'success');
    } else {
      if (status) status.textContent = `Failed: ${result?.error || 'unknown error'}`;
      showToast(result?.error || 'AI Provider test failed', 'error');
    }
  } catch (error) {
    if (status) status.textContent = `Failed: ${error.message || error}`;
    showToast(error.message || String(error), 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = old;
    }
  }
});

function getEnhanceSettings() {
  const watermarkText = document.getElementById('inp-enhance-watermark-text')?.value?.trim() || '';
  return {
    lut: document.getElementById('sel-enhance-lut')?.value || 'None',
    brightness: parseInt(document.getElementById('slider-enhance-brightness')?.value || '50'),
    contrast: parseInt(document.getElementById('slider-enhance-contrast')?.value || '55'),
    saturation: parseInt(document.getElementById('slider-enhance-saturation')?.value || '60'),
    vignette: parseInt(document.getElementById('slider-enhance-vignette')?.value || '0'),
    watermark: document.getElementById('chk-enhance-watermark')?.checked || false,
    watermark_text: watermarkText,
  };
}

function getWorkModeRenderParams() {
  const resizeEnabled = document.getElementById('chk-resize')?.checked || false;
  const cropEnabled = document.getElementById('chk-crop-video')?.checked || false;
  const keepAspect = document.getElementById('chk-keep-ratio')?.checked ?? true;
  const width = Number(document.getElementById('inp-width')?.value || 0);
  const height = Number(document.getElementById('inp-height')?.value || 0);
  const hasTargetSize = Number.isFinite(width) && Number.isFinite(height) && width >= 16 && height >= 16;
  const params = {
    work_mode: document.getElementById('sel-work-mode')?.value || 'Mặc Định',
    resize_enabled: resizeEnabled,
    crop_enabled: cropEnabled,
    keep_aspect: keepAspect,
    crop_position: (document.getElementById('inp-crop-pos')?.value || 'center').trim() || 'center',
  };
  if ((resizeEnabled || cropEnabled) && hasTargetSize) {
    params.width = Math.round(width);
    params.height = Math.round(height);
  }
  return params;
}

executeBtn.addEventListener('click', async () => {
  if (isRunning) return;

  const inputPath = getInputMediaPath();
  if (!inputPath) {
    alert('Vui lòng chọn file video hoặc subtitle trước!');
    return;
  }

  isRunning = true;
  executeCount--;
  if (executeCount <= 0) executeCount = executeCountMax;
  updateExecuteBtnState('Đang xử lý...', 'RENDER ENGINE', 'executing');

  const inputName = inputPath.split(/[\\/]/).pop().replace(/\.[^.]+$/, '') || 'output';
  const selFrom = document.getElementById('sel-lang-from')?.value;
  const selTo = document.getElementById('sel-lang-to')?.value;

  const region = getSubBoxRegion();
  const exportParams = typeof getExportRenderParams === 'function' ? getExportRenderParams().params : {};
  const subtitleStyle = getSubtitleStyleOptions();
  const enhanceSettings = getEnhanceSettings();
  const sourceLang = getLangCodeFromSelect('sel-lang-from', 'zh');
  const targetLang = getLangCodeFromSelect('sel-lang-to', 'vi');
  const translateEngine = getPipelineTranslateEngine(sourceLang, targetLang);
  const translateEnabled = document.getElementById('chk-translate-enable')?.checked ?? true;
  const rewriteEnabled = document.getElementById('chk-rewrite-enable')?.checked ?? false;
  if (translateEnabled && !translateEngine) {
    alert('Vui long chon engine dich trong tab Phu de > Dich thuat.');
    isRunning = false;
    updateExecuteBtnState('Sẵn sàng', '', 'idle');
    return;
  }

  const params = {
    ...exportParams,
    source_lang: sourceLang,
    target_lang: targetLang,
    translate_enabled: translateEnabled,
    translate_engine: translateEngine,
    translate_model: getPipelineTranslateModel(translateEngine),
    rewrite_enabled: rewriteEnabled,
    rewrite_style: 'review',
    tts_provider: document.getElementById('sel-tts-provider')?.value === 'FPT.AI TTS' ? 'fpt' : (document.getElementById('sel-tts-provider')?.value?.toLowerCase().replace(' tts', '').replace(' (free)', '') || 'edge'),
    tts_voice: document.getElementById('sel-voice-type')?.value || 'vi-VN-HoaiMyNeural',
    tts_align: document.getElementById('chk-tts-align')?.checked ?? true,
    tts_optimize_subtitles: rewriteEnabled,
    tts_allow_shorten: rewriteEnabled,
    tts_target_cps: 13,
    tts_optimize_engine: 'auto',
    tts_naturalize: rewriteEnabled,
    tts_timeline_strategy: 'subtitle_fit',
    tts_max_tempo: 3.5,
    tts_trim_overflow: false,
    extend_video_to_tts: false,
    ...getVoiceModeOptions(),
    tts_enabled: (document.getElementById('chk-auto-voice')?.checked ?? true) && getVoiceModeOptions().tts_enabled,
    fpt_api_key: document.getElementById('inp-fpt-key')?.value || undefined,
    burn_subtitle: document.getElementById('chk-sub-burn')?.checked ?? true,
    hardsub_blur_strength: 12,
    hardsub_blur_padding: 0.02,
    output_name: inputName,
    output_dir: UI.outputPath?.value || undefined,
    project_preset: document.getElementById('sel-project-preset')?.value || 'Movie Review',
    subtitle_region: region,
    ...subtitleStyle,
    remove_hardsub: subBlurEnabled,
    enhance: enhanceSettings,
  };

  const item = await apiPost('/pipeline/start', {
    project_id: currentProjectId || 1,
    input_path: inputPath,
    type: 'pipeline',
    params,
  });

  startCountdown(600);
  addTaskRow();

  if (!item || !item.id) {
    addClientLog('error', 'Backend không tạo được job. Kiểm tra API key và file input.');
    isRunning = false;
    updateExecuteBtnState('RENDER FAILED', 'RENDER ENGINE', 'failed');
    setTimeout(() => {
      if (!isRunning) {
        updateExecuteBtnState(`THỰC HIỆN (${executeCount})`, 'RENDER ENGINE', 'idle');
      }
    }, 3000);
    updateLastRow(0, 'failed');
    return;
  }


  function onTrackUpdate(data) {
    const running = data.find(r => r.id === item.id);
    if (running) {
      progressVal = running.progress || 0;
      queueFill.style.height = progressVal + '%';
      updateLastRow(progressVal);
      if (running.status === 'completed' || running.status === 'failed') {
        stopTracking();
        finishExecute();
        if (running.status === 'completed' && currentProjectId) {
          loadTimeline(currentProjectId);
        }
      }
    }
  }

  function stopTracking() {
    queueListeners.delete(onTrackUpdate);
  }

  onQueueChange(onTrackUpdate);

  if (latestQueueData) onTrackUpdate(latestQueueData);
  pollQueueUntilDone(item.id, onTrackUpdate, stopTracking);
});

function pollQueueUntilDone(jobId, onTrackUpdate, stopTracking) {
  const started = Date.now();
  const interval = setInterval(async () => {
    const jobs = await apiGet('/queue');
    if (jobs && Array.isArray(jobs)) {
      onTrackUpdate(jobs);
      const job = jobs.find(j => j.id === jobId);
      if (!job || job.status === 'completed' || job.status === 'failed') {
        clearInterval(interval);
      }
    }
    if (Date.now() - started > 3 * 60 * 60 * 1000) {
      clearInterval(interval);
      stopTracking();
      addClientLog('warn', 'Dung theo doi job sau 3 gio, backend co the van dang chay.');
    }
  }, 3000);
}

function finishExecute() {
  isRunning = false;
  progressVal = 0;
  updateExecuteBtnState(`THỰC HIỆN (${executeCount})`, 'RENDER ENGINE', 'idle');
  remainingEl.textContent = '00:00:00';
}

/* ─── Shared state ─── */
let progressVal = 0;
let isRunning = false;
let timerInterval = null;
let remainingSeconds = 0;
const queueFill = document.getElementById('queue-accent-fill');
const remainingEl = document.getElementById('remaining-time') || { textContent: '' };

function formatTime(s) {
  const hh = String(Math.floor(s / 3600)).padStart(2, '0');
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

function startCountdown(seconds) {
  remainingSeconds = seconds;
  clearInterval(timerInterval);
  timerInterval = setInterval(() => {
    if (remainingSeconds <= 0) { clearInterval(timerInterval); remainingEl.textContent = '00:00:00'; return; }
    remainingSeconds--;
    remainingEl.textContent = formatTime(remainingSeconds);
  }, 1000);
}

/* ─── Preset Save ─── */
document.getElementById('btn-save-preset')?.addEventListener('click', async () => {
  const name = document.getElementById('sel-project-preset')?.value || 'Custom';
  const preset = {
    name: name,
    voice: {
      provider: document.getElementById('sel-tts-provider')?.value || 'edge',
      voice: document.getElementById('sel-voice-type')?.value || 'Mặc Định',
      speed: 1.0,
      keep_bgm: document.getElementById('chk-keep-bgm')?.checked || false,
      bgm_volume: parseFloat(document.getElementById('inp-bgm-vol')?.value || '0.1'),
    },
    subtitle: {
      font: 'Arial',
      size: 42,
      color: '#FFFFFF',
      stroke: 2,
      shadow: 'soft',
      position: 'bottom',
      burn: true,
      region: getSubBoxRegion() || { x: 0.1, y: 0.78, width: 0.8, height: 0.15 },
    },
    export: {
      resolution: document.getElementById('inp-width')?.value + 'x' + document.getElementById('inp-height')?.value || '1920x1080',
      fps: parseInt(document.getElementById('sel-export-fps')?.value || '30'),
      codec: document.getElementById('sel-export-codec')?.value || 'h264',
      bitrate: document.getElementById('sel-export-bitrate')?.value || '8M',
      format: (document.getElementById('sel-export-format')?.value || 'mp4').toLowerCase(),
      gpu: document.getElementById('sel-export-gpu')?.value?.toLowerCase() || 'cpu',
    },
    enhance: {
      lut: 'Cinematic',
      brightness: 50,
      contrast: 55,
      saturation: 60,
      vignette: 12,
      watermark: document.querySelector('#tab-enhance .custom-checkbox input')?.checked || false,
    },
  };
  await apiPost('/presets?name=' + encodeURIComponent(name), preset);
});

/* ─── Scene Detect ─── */
document.getElementById('btn-detect-scenes')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-detect-scenes');
  btn.textContent = '⏳ Đang phân tích...';
  btn.disabled = true;
  const videoPath = document.getElementById('inp-video-path')?.value || document.getElementById('inp-srt-path')?.value || '';
  if (videoPath) {
    await apiPost('/edit/scene-detect', {
      project_id: currentProjectId || 1,
      video_path: videoPath,
      threshold: 27,
    });
  }
  setTimeout(() => {
    btn.innerHTML = '<i class="ri-scissors-cut-line"></i> Phân tích';
    btn.disabled = false;
  }, 2000);
});

/* ─── Task Queue Rows ─── */
const resultBody = document.getElementById('result-table-body');
let rowCount = 0;

function addTaskRow() {
  rowCount++;
  if (latestQueueData && latestQueueData.length > 0) {
    renderQueueRows(latestQueueData);
  } else {
    apiGet('/queue').then(jobs => { if (jobs) renderQueueRows(jobs); });
  }
}

function updateLastRow(pct) {
  if (latestQueueData && latestQueueData.length > 0) {
    const last = latestQueueData[latestQueueData.length - 1];
    if (last && last.id) {
      const row = document.querySelector(`[data-job-id="${last.id}"]`);
      if (row) {
        const fillId = row.id?.replace('task-row-', 'mini-fill-');
        const fill = document.getElementById(fillId);
        if (fill) fill.style.width = pct + '%';
        const timeCell = row.querySelectorAll('.result-cell')[4];
        if (timeCell) {
          timeCell.textContent = formatTime(Math.round(pct * 3));
          timeCell.style.color = pct >= 100 ? '#22c55e' : '#facc15';
        }
      }
    }
  }
}

/* ─── Crop checkbox toggle ─── */
document.getElementById('chk-crop-video')?.addEventListener('change', function () {
  const pos = document.getElementById('inp-crop-pos');
  const btn = document.getElementById('btn-chon-vi-tri');
  pos.disabled = !this.checked;
  btn.disabled = !this.checked;
  pos.style.opacity = this.checked ? '1' : '0.4';
  btn.style.opacity = this.checked ? '1' : '0.4';
});
// Init state
const cropPos = document.getElementById('inp-crop-pos');
const chonViTri = document.getElementById('btn-chon-vi-tri');
if (cropPos) { cropPos.disabled = true; cropPos.style.opacity = '0.4'; }
if (chonViTri) { chonViTri.disabled = true; chonViTri.style.opacity = '0.4'; }
chonViTri?.addEventListener('click', () => {
  const current = cropPos?.value && cropPos.value !== '........' ? cropPos.value : 'center';
  const next = prompt('Vi tri crop: center, top, bottom, left, right, top-left, top-right, bottom-left, bottom-right hoac x,y (0-1)', current);
  if (next === null) return;
  const value = next.trim() || 'center';
  if (cropPos) cropPos.value = value;
  showToast(`Crop position: ${value}`, 'info', 1800);
});

/* ─── Resize checkbox toggle ─── */
document.getElementById('chk-resize')?.addEventListener('change', function () {
  ['inp-height', 'inp-width', 'chk-keep-ratio'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.disabled = !this.checked;
      const opacityTarget = el.closest ? (el.closest('.custom-checkbox') || el) : el;
      opacityTarget.style.opacity = this.checked ? '1' : '0.4';
    }
  });
});

/* ─── Social button tooltips / links (demo) ─── */
document.querySelectorAll('.social-btn').forEach(btn => {
  btn.title = btn.title || btn.textContent;
});

/* ─── Drag-over file drop on SRT path ─── */
const srtInput = document.getElementById('inp-srt-path');
if (srtInput) {
  srtInput.addEventListener('dragover', e => { e.preventDefault(); srtInput.style.borderColor = 'var(--accent)'; });
  srtInput.addEventListener('dragleave', () => { srtInput.style.borderColor = ''; });
  srtInput.addEventListener('drop', e => {
    e.preventDefault();
    srtInput.style.borderColor = '';
    const files = e.dataTransfer.files;
    if (files.length) srtInput.value = files[0].name;
  });
}
const videoInput = document.getElementById('inp-video-path');
if (videoInput) {
  videoInput.addEventListener('dragover', e => { e.preventDefault(); videoInput.style.borderColor = 'var(--accent)'; });
  videoInput.addEventListener('dragleave', () => { videoInput.style.borderColor = ''; });
  videoInput.addEventListener('drop', e => {
    e.preventDefault();
    videoInput.style.borderColor = '';
    const files = e.dataTransfer.files;
    if (files.length) {
      videoInput.value = files[0].name;
      loadVideoPreview();
    }
  });
}

/* ═══════════════ SUBTITLE TREE CONTROL ═══════════════ */
/* Tree leaf switching */
document.querySelectorAll('.tree-node.leaf').forEach(leaf => {
  leaf.addEventListener('click', () => {
    const treeLayout = leaf.closest('.subtitle-tree-layout');
    if (!treeLayout) return;
    treeLayout.querySelectorAll('.tree-node.leaf').forEach(l => l.classList.remove('active'));
    leaf.classList.add('active');
    treeLayout.querySelectorAll('.tree-leaf-content').forEach(c => c.classList.remove('active'));
    const target = document.getElementById('leaf-' + leaf.dataset.leaf);
    if (target) target.classList.add('active');
  });
});

/* Tree branch toggle */
document.querySelectorAll('.tree-node.parent').forEach(parent => {
  parent.addEventListener('click', (e) => {
    if (e.target.closest('.tree-node') !== parent) return;
    const branchId = parent.dataset.branch;
    const children = document.getElementById('branch-' + branchId);
    const toggle = parent.querySelector('.tree-toggle');
    if (children) {
      children.classList.toggle('collapsed');
      if (toggle) toggle.classList.toggle('collapsed');
    }
  });
});

/* ═══════════════ QUEUE CONTROLS (API) ═══════════════ */
document.getElementById('btn-retry-failed')?.addEventListener('click', async () => {
  const failedCount = document.querySelectorAll('.result-row[data-status="failed"], .queue-job[data-status="failed"]').length;
  if (failedCount === 0) {
    showToast('Khong co task loi nao de thu lai', 'warn', 2000);
    return;
  }
  const res = await apiPost('/queue/retry-all');
  if (res) {
    showToast('Dang thu lai ' + failedCount + ' task loi', 'success', 2000);
    const jobs = await apiGet('/queue');
    if (jobs && Array.isArray(jobs)) renderQueueRows(jobs);
  } else {
    showToast('Khong the thu lai task loi', 'error', 3000);
  }
});

document.getElementById('btn-pause-queue')?.addEventListener('click', async () => {
  const res = await apiPost('/queue/pause-all');
  showToast(res?.message || 'Da tam dung hang doi', 'info', 2500);
  const jobs = await apiGet('/queue');
  if (jobs && Array.isArray(jobs)) renderQueueRows(jobs);
});

document.getElementById('btn-resume-queue')?.addEventListener('click', async () => {
  const res = await apiPost('/queue/resume-all');
  showToast(res?.message || 'Da tiep tuc hang doi', 'success', 2000);
  const jobs = await apiGet('/queue');
  if (jobs && Array.isArray(jobs)) renderQueueRows(jobs);
});

document.getElementById('btn-clear-queue')?.addEventListener('click', async () => {
  if (confirm('Bạn có chắc muốn xóa sạch danh sách hàng đợi?')) {
    await apiPost('/queue/clear-all');
    const resultBody = document.getElementById('result-table-body');
    if (resultBody) resultBody.innerHTML = '';
    rowCount = 0;
  }
});

/* ── Log Viewer ── */
const logModal = document.getElementById('log-modal');
const logContainer = document.getElementById('log-container');
const logCount = document.getElementById('log-count');

// Note: ``escapeHtml`` is declared near the top of this file (with the other
// DOM helpers). The previous duplicate at this location has been removed.

function renderLogEntries(entries) {
  if (!entries || entries.length === 0) {
    return '<div class="log-placeholder">Không tìm thấy bản ghi log nào.</div>';
  }
  return entries.map(e => {
    const cls = `log-level ${e.level || 'info'}`;
    const ts = (e.timestamp || '').slice(11, 19) || (e.source === 'client' ? new Date().toLocaleTimeString() : '--:--:--');
    const srcTag = e.source === 'client' ? '<span class="log-source client" title="Client-side">CLI</span>' : '<span class="log-source server" title="Server-side">SRV</span>';
    const msg = e.message || '';
    const detail = e.detail ? `<div style="font-size:9px;color:var(--text-dim);padding:2px 0 0 52px;white-space:pre-wrap;font-family:var(--font-mono)">${escapeHtml(String(e.detail).slice(0, 300))}</div>` : '';
    return `<div class="log-entry">
      ${srcTag}
      <span class="log-time">${ts}</span>
      <span class="${cls}">${(e.level || 'info').toUpperCase()}</span>
      <span class="log-message">${escapeHtml(msg)}${detail}</span>
    </div>`;
  }).join('');
}

async function fetchLogs() {
  const filterId = document.getElementById('inp-log-filter')?.value;
  const filterLevel = document.getElementById('sel-log-level')?.value;

  // Fetch backend logs
  let url = '/queue/logs?limit=500';
  if (filterId) url += `&queue_item_id=${filterId}`;
  const backendLogs = await apiGet(url) || [];

  // Merge with client logs
  let allLogs = [];
  backendLogs.forEach(l => allLogs.push({ ...l, source: 'server' }));
  clientLogs.forEach(l => allLogs.push({ ...l, source: 'client' }));

  // Sort by timestamp descending
  allLogs.sort((a, b) => {
    const ta = a.timestamp || '';
    const tb = b.timestamp || '';
    return tb.localeCompare(ta);
  });

  // Apply level filter
  if (filterLevel) {
    allLogs = allLogs.filter(l => l.level === filterLevel);
  }

  if (logCount) logCount.textContent = `${allLogs.length} bản ghi`;
  if (!logContainer) return;

  if (allLogs.length === 0) {
    logContainer.innerHTML = '<div class="log-placeholder">Không tìm thấy bản ghi log nào.</div>';
    return;
  }

  logContainer.innerHTML = renderLogEntries(allLogs);
}

document.getElementById('btn-log-queue')?.addEventListener('click', () => {
  logModal?.classList.add('show');
  fetchLogs();
});

document.getElementById('btn-log-refresh')?.addEventListener('click', fetchLogs);

document.getElementById('inp-log-filter')?.addEventListener('change', fetchLogs);
document.getElementById('sel-log-level')?.addEventListener('change', fetchLogs);

document.getElementById('btn-log-copy')?.addEventListener('click', () => {
  const text = [...logContainer.querySelectorAll('.log-entry')].map(row => {
    const src = row.querySelector('.log-source')?.textContent || '';
    const time = row.querySelector('.log-time')?.textContent || '';
    const level = row.querySelector('.log-level')?.textContent || '';
    const msg = row.querySelector('.log-message')?.textContent || '';
    return `[${src}] [${time}] [${level}] ${msg}`;
  }).join('\n');
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('btn-log-copy');
    const orig = btn.innerHTML;
    btn.innerHTML = '<i class="ri-check-line"></i> Đã sao chép';
    setTimeout(() => btn.innerHTML = orig, 1500);
  }).catch(() => {
    // fallback
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
  });
});

/* ═══════════════ AI PANEL HANDLERS ═══════════════ */

async function waitQueueJobResult(itemId, btn, label = 'job') {
  for (;;) {
    const jobs = await apiGet('/queue');
    const job = (jobs || []).find(item => Number(item.id) === Number(itemId));
    if (job) {
      const pct = Math.max(0, Math.min(100, Number(job.progress || 0)));
      if (btn) btn.innerHTML = `<i class="ri-loader-4-line ri-spin"></i> ${pct}%`;
      if (job.status === 'completed' || job.status === 'done') {
        let raw = '';
        const outputPath = job.output_path || '';
        const readableOutput = !outputPath || /\.(json|txt|srt|ass|vtt)$/i.test(outputPath);
        if (readableOutput) {
          try { raw = await apiGet(`/queue/${itemId}/output`); } catch (_) { raw = ''; }
        }
        if (!raw && job.output_path) raw = job.output_path;
        return { job, raw };
      }
      if (job.status === 'failed' || job.status === 'error') {
        throw new Error(job.error || `${label} failed`);
      }
    }
    await new Promise(resolve => setTimeout(resolve, 900));
  }
}

function parseAiJobOutput(raw) {
  if (!raw) return {};
  if (typeof raw === 'object') return raw;
  try { return JSON.parse(raw); } catch (_) { return { text: String(raw) }; }
}

function renderAiResult(elId, payload, key) {
  const el = document.getElementById(elId);
  if (!el) return;
  const data = payload || {};
  const value = data[key] ?? data.text ?? data.summary ?? data.recap ?? data.titles ?? data.hashtags ?? data.characters ?? data.speakers ?? data;
  if (Array.isArray(value)) {
    if (key === 'hashtags') {
      el.innerHTML = value.map(h => `<span style="display:inline-block;background:var(--bg-input);padding:1px 6px;border-radius:2px;margin:1px">${escapeHtml(String(h))}</span>`).join(' ');
    } else {
      el.innerHTML = value.map(item => `<div>${escapeHtml(typeof item === 'object' ? JSON.stringify(item) : String(item))}</div>`).join('');
    }
  } else if (value && typeof value === 'object') {
    el.textContent = JSON.stringify(value, null, 2);
  } else {
    el.textContent = String(value || 'No result');
  }
  el.style.color = 'var(--text)';
}

async function runAiQueueButton(btnId, endpoint, payload, resultElId, resultKey) {
  const btn = document.getElementById(btnId);
  const old = btn?.innerHTML;
  try {
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Queued';
    }
    const response = await apiPost(endpoint, payload);
    if (!response?.id) throw new Error(response?.error || 'Backend did not return job id');
    addTaskRow();
    const { raw } = await waitQueueJobResult(response.id, btn, btnId);
    renderAiResult(resultElId, parseAiJobOutput(raw), resultKey);
    showToast('AI task completed', 'success');
  } catch (error) {
    showToast(error.message || String(error), 'error');
    const el = document.getElementById(resultElId);
    if (el) {
      el.textContent = error.message || String(error);
      el.style.color = 'var(--yellow-warn)';
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = old;
    }
  }
}

document.getElementById('btn-ai-detect-scenes')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-ai-detect-scenes');
  const old = btn?.innerHTML;
  try {
    const videoPath = getInputMediaPath();
    if (!videoPath) throw new Error('Chưa chọn video');
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Đang quét'; }
    const response = await apiPost('/edit/scene-detect', {
      project_id: currentProjectId || 1,
      video_path: videoPath,
      threshold: parseInt(document.getElementById('inp-ai-threshold')?.value || '27'),
    });
    if (response?.id) await waitQueueJobResult(response.id, btn, 'scene detect');
    await updateSceneList();
    addTaskRow();
  } catch (error) {
    showToast(error.message || String(error), 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = old; }
  }
});

document.getElementById('btn-ai-summary')?.addEventListener('click', async () => {
  const text = document.getElementById('inp-ai-summary')?.value || '';
  if (!text.trim()) { showToast('Chưa nhập nội dung tóm tắt', 'warn'); return; }
  await runAiQueueButton('btn-ai-summary', '/ai/summary', {
    project_id: currentProjectId || 1,
    text,
    max_length: 200,
    engine: document.getElementById('sel-ai-summary-model')?.value || 'BART',
  }, 'ai-summary-result', 'summary');
});

document.getElementById('btn-ai-recap')?.addEventListener('click', async () => {
  const videoPath = getInputMediaPath();
  await runAiQueueButton('btn-ai-recap', '/ai/recap', {
    project_id: currentProjectId || 1,
    video_path: videoPath || undefined,
    text: document.getElementById('inp-ai-summary')?.value || undefined,
  }, 'ai-recap-result', 'text');
});

document.getElementById('btn-ai-characters')?.addEventListener('click', async () => {
  const videoPath = getInputMediaPath();
  if (!videoPath) { showToast('Chưa chọn video', 'warn'); return; }
  await runAiQueueButton('btn-ai-characters', '/ai/characters', {
    project_id: currentProjectId || 1,
    video_path: videoPath,
  }, 'ai-characters-result', 'characters');
});

document.getElementById('btn-ai-speakers')?.addEventListener('click', async () => {
  const videoPath = getInputMediaPath();
  if (!videoPath) { showToast('Chưa chọn video', 'warn'); return; }
  await runAiQueueButton('btn-ai-speakers', '/ai/speakers', {
    project_id: currentProjectId || 1,
    video_path: videoPath,
  }, 'ai-speakers-result', 'speakers');
});

// AI thumbnail generation is not exposed in the current layout.

document.getElementById('btn-ai-title')?.addEventListener('click', async () => {
  await runAiQueueButton('btn-ai-title', '/ai/title', {
    project_id: currentProjectId || 1,
    video_path: getInputMediaPath() || undefined,
    style: 'review',
  }, 'ai-summary-result', 'titles');
});
document.getElementById('btn-ai-hashtag')?.addEventListener('click', async () => {
  const text = document.getElementById('inp-ai-summary')?.value
    || document.getElementById('ai-recap-result')?.textContent
    || '';
  await runAiQueueButton('btn-ai-hashtag', '/ai/hashtags', {
    project_id: currentProjectId || 1,
    text,
    count: 5,
  }, 'ai-recap-result', 'hashtags');
});
document.getElementById('btn-ai-prompt')?.addEventListener('click', () => {
  const el = document.getElementById('ai-speakers-result');
  el.textContent = 'Thư viện Prompt: Sử dụng các câu lệnh hỗ trợ bởi AI để chỉnh sửa video sáng tạo. Sắp ra mắt.';
  el.style.color = 'var(--text-muted)';
});

/* ═══════════════ EXPORT PANEL HANDLERS ═══════════════ */
function getExportRenderParams() {
  const format = (document.getElementById('sel-export-format')?.value || 'MP4').toLowerCase();
  const codec = (document.getElementById('sel-export-codec')?.value || 'H264').toLowerCase();
  const rawBitrate = document.getElementById('sel-export-bitrate')?.value || '8M';
  const bitrate = /auto|tự|tu dong/i.test(rawBitrate) ? 'auto' : rawBitrate;
  const fps = document.getElementById('sel-export-fps')?.value || '30';
  const gpu = (document.getElementById('sel-export-gpu')?.value || 'CPU').toLowerCase();
  const presetName = document.getElementById('sel-export-preset')?.value || 'Movie Review';
  const workMode = getWorkModeRenderParams();
  const speedPreset = {
    'Draft Fast': { preset: 'veryfast', quality: 'draft', bitrate: 'auto', width: 1280, height: 720, gpu: 'auto' },
    'NVENC Fast': { preset: 'fast', quality: 'fast', gpu: 'nvenc' },
    'Quality': { preset: 'slow', quality: 'quality', bitrate: 'auto', crf: '18' },
  }[presetName] || {};
  const inputPath = getInputMediaPath();
  const inputName = inputPath.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '') || `project_${currentProjectId || 1}`;
  const subtitleStyle = getSubtitleStyleOptions();
  return {
    inputPath,
    params: {
      format,
      codec,
      bitrate: speedPreset.bitrate || bitrate,
      fps,
      gpu: speedPreset.gpu || gpu,
      preset: speedPreset.preset || undefined,
      quality: speedPreset.quality || undefined,
      crf: speedPreset.crf || undefined,
      width: workMode.width || speedPreset.width || undefined,
      height: workMode.height || speedPreset.height || undefined,
      resize_enabled: workMode.resize_enabled,
      crop_enabled: workMode.crop_enabled,
      keep_aspect: workMode.keep_aspect,
      crop_position: workMode.crop_position,
      work_mode: workMode.work_mode,
      copy_if_possible: true,
      output_name: inputName,
      output_dir: UI.outputPath?.value || undefined,
      burn_subtitle: document.getElementById('chk-sub-burn')?.checked ?? true,
      subtitle_region: getSubBoxRegion() || undefined,
      remove_hardsub: subBlurEnabled,
      ...subtitleStyle,
      ...getVoiceModeOptions(),
      enhance: getEnhanceSettings(),
      tts_enabled: false,
    },
  };
}

function getSubtitleStyleOptions() {
  return {
    subtitle_font: UI.id('sel-sub-font')?.value || 'Arial',
    subtitle_size: Number(UI.id('inp-sub-size')?.value || 42),
    subtitle_color: UI.id('inp-sub-color')?.value || '#ffffff',
    subtitle_shadow: (UI.id('sel-sub-shadow')?.value || 'Soft').toLowerCase(),
    subtitle_stroke: Number(UI.id('inp-sub-stroke')?.value || 2),
    subtitle_position: (UI.id('sel-sub-position')?.value || 'Bottom').toLowerCase(),
  };
}

document.getElementById('btn-export-render')?.addEventListener('click', async () => {
  const render = getExportRenderParams();
  if (!render.inputPath) {
    alert('Vui long chon video truoc khi xuat.');
    return;
  }
  const item = await apiPost('/pipeline/start', {
    project_id: currentProjectId || 1,
    type: 'render',
    input_path: render.inputPath,
    params: render.params,
  });
  if (item) {
    if (item.project_id) currentProjectId = item.project_id;
    addTaskRow();
  } else {
    showToast('Khong the dua job render vao hang doi. Mo Log de xem loi chi tiet.', 'error', 6000);
  }
});

document.getElementById('btn-export-audio')?.addEventListener('click', async () => {
  const inputPath = document.getElementById('inp-video-path')?.value || document.getElementById('inp-srt-path')?.value || '';
  if (!inputPath) {
    alert('Vui long chon video truoc khi xuat am thanh.');
    return;
  }
  const result = await apiPost('/export/audio', { project_id: currentProjectId || 1, input_path: inputPath });
  if (result && result.path) {
    addTaskRow();
  }
});

document.getElementById('btn-export-queue')?.addEventListener('click', async () => {
  const priorityMap = { High: 2, Normal: 1, Low: 0 };
  const pVal = document.getElementById('sel-export-priority')?.value || 'Normal';
  const render = getExportRenderParams();
  if (!render.inputPath) {
    alert('Vui long chon video truoc khi them vao hang cho.');
    return;
  }
  await apiPost('/queue', {
    project_id: currentProjectId || 1,
    type: 'render',
    input_path: render.inputPath,
    params: render.params,
    priority: priorityMap[pVal] ?? 1,
  });
  addTaskRow();
});

document.getElementById('btn-export-save-preset')?.addEventListener('click', async () => {
  const name = document.getElementById('sel-export-preset')?.value || 'Custom';
  await apiPost('/presets?name=' + encodeURIComponent(name), {
    resolution: '1920x1080',
    fps: document.getElementById('sel-export-fps')?.value || 30,
    codec: document.getElementById('sel-export-codec')?.value || 'h264',
    bitrate: document.getElementById('sel-export-bitrate')?.value || '8M',
  });
});

document.getElementById('btn-export-load-preset')?.addEventListener('click', async () => {
  const name = document.getElementById('sel-export-preset')?.value || 'Movie Review';
  const preset = await apiGet('/presets/' + encodeURIComponent(name));
  const exportPreset = preset?.export || preset;
  if (exportPreset) {
    if (exportPreset.codec) document.getElementById('sel-export-codec').value = String(exportPreset.codec).toUpperCase();
    if (exportPreset.bitrate) {
      const bitrate = String(exportPreset.bitrate).toLowerCase() === 'auto' ? 'Tự động' : exportPreset.bitrate;
      document.getElementById('sel-export-bitrate').value = bitrate;
    }
    if (exportPreset.fps) document.getElementById('sel-export-fps').value = exportPreset.fps;
    if (exportPreset.gpu) document.getElementById('sel-export-gpu').value = String(exportPreset.gpu).toUpperCase() === 'AUTO' ? 'Auto' : String(exportPreset.gpu).toUpperCase();
  }
});

/* ═══════════════ VOICE CLONE HANDLERS ═══════════════ */
let uploadedVoiceSamplePath = '';

document.getElementById('btn-voice-upload')?.addEventListener('click', async () => {
  const fileInput = document.getElementById('inp-voice-sample');
  const file = fileInput?.files?.[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch(API_BASE + `/voice/clone/upload?project_id=${currentProjectId || 1}`, { method: 'POST', body: form });
    if (res.ok) {
      const data = await res.json();
      uploadedVoiceSamplePath = data.path || '';
      fileInput.value = '';
      showToast('Da tai len mau giong', 'success');
    }
  } catch (e) {
    console.warn('Voice upload failed:', e.message);
  }
});

document.getElementById('btn-voice-train')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-voice-train');
  if (!uploadedVoiceSamplePath) {
    showToast('Hay tai len file mau giong truoc', 'warn');
    return;
  }
  const name = document.getElementById('inp-f5-clone-name')?.value?.trim() || 'default';
  const refText = document.getElementById('inp-f5-ref-text')?.value?.trim() || '';
  btn.textContent = '⏳ Đang huấn luyện...';
  btn.disabled = true;
  await apiPost('/voice/clone/train', {
    project_id: currentProjectId || 1,
    engine: 'f5',
    sample_path: uploadedVoiceSamplePath,
    name,
    ref_text: refText,
  });
  document.getElementById('sel-tts-provider').value = 'f5';
  await updateVoiceDropdown();
  const voiceType = document.getElementById('sel-voice-type');
  if (voiceType) voiceType.value = name;
  setTimeout(() => {
    btn.innerHTML = '<i class="ri-user-voice-line"></i> Huấn luyện Giọng đọc';
    btn.disabled = false;
  }, 3000);
});

document.getElementById('btn-voice-export-clone')?.addEventListener('click', async () => {
  const result = await apiGet('/voice/clone/export');
  if (result && result.path) {
    addTaskRow();
    const fill = document.getElementById(`mini-fill-${rowCount}`);
    if (fill) fill.style.width = '100%';
  }
});

document.getElementById('btn-voice-clone-oneclick')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-voice-clone-oneclick');
  if (!uploadedVoiceSamplePath) {
    showToast('Hãy tải lên mẫu giọng trước (10 giây là đủ)', 'warn');
    return;
  }
  const engine = document.getElementById('sel-clone-engine')?.value || 'f5';
  const name = document.getElementById('inp-f5-clone-name')?.value?.trim() || 'oneclick';
  const refText = document.getElementById('inp-f5-ref-text')?.value?.trim() || '';
  const inputPath = (typeof getInputMediaPath === 'function' && getInputMediaPath()) || '';
  const url = document.getElementById('inp-download-url')?.value?.trim() || '';
  const selFrom = document.getElementById('sel-lang-from')?.value;
  const selTo = document.getElementById('sel-lang-to')?.value;

  if (!inputPath && !url) {
    showToast('Hãy chọn video nguồn hoặc nhập URL YouTube/TikTok/Facebook', 'warn');
    return;
  }
  const cloneTranslateEnabled = document.getElementById('chk-translate-enable')?.checked ?? true;
  const cloneTranslateEngine = getPipelineTranslateEngine(
    getLangCodeFromSelect('sel-lang-from', 'zh'),
    getLangCodeFromSelect('sel-lang-to', 'vi'),
  );
  if (cloneTranslateEnabled && !cloneTranslateEngine) {
    showToast('Chon engine dich trong tab Phu de > Dich thuat truoc khi chay 1-Click Clone.', 'warn');
    return;
  }

  const oldText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Đang gửi...';
  try {
    const res = await apiPost('/voice/clone/oneclick', {
      project_id: currentProjectId || 1,
      engine,
      name,
      sample_path: uploadedVoiceSamplePath,
      ref_text: refText,
      url,
      source_lang: getLangCodeFromSelect('sel-lang-from', 'zh'),
      target_lang: getLangCodeFromSelect('sel-lang-to', 'vi'),
      translate_enabled: cloneTranslateEnabled,
      translate_engine: cloneTranslateEngine,
      translate_model: getPipelineTranslateModel(cloneTranslateEngine),
      rewrite_enabled: document.getElementById('chk-rewrite-enable')?.checked ?? false,
      rewrite_style: 'review',
      tts_optimize_subtitles: document.getElementById('chk-rewrite-enable')?.checked ?? false,
      tts_allow_shorten: document.getElementById('chk-rewrite-enable')?.checked ?? false,
      tts_target_cps: 13,
      tts_optimize_engine: 'auto',
      tts_naturalize: document.getElementById('chk-rewrite-enable')?.checked ?? false,
      tts_align: document.getElementById('chk-tts-align')?.checked ?? true,
      burn_subtitle: document.getElementById('chk-sub-burn')?.checked ?? false,
    });
    if (res?.id) {
      showToast(`Đã đưa 1-Click Clone (${engine}:${name}) vào hàng đợi`, 'success', 4000);
      addTaskRow();
    } else {
      showToast(res?.error || 'Không tạo được job', 'error', 5000);
    }
  } catch (e) {
    showToast('1-Click Clone thất bại: ' + (e?.message || e), 'error', 5000);
  } finally {
    btn.disabled = false;
    btn.innerHTML = oldText;
  }
});

document.getElementById('btn-voice-speakers')?.addEventListener('click', async () => {
  const result = await apiPost('/ai/speakers', { project_id: currentProjectId || 1 });
  if (result && result.speakers) {
    alert('Đã phát hiện người nói: ' + Object.keys(result.speakers).join(', '));
  } else {
    alert('Nhận diện người nói sẽ chạy sau khi chuyển âm.');
  }
});

document.getElementById('chk-auto-diarize')?.addEventListener('change', function () {
  if (this.checked) {
    apiPost('/ai/speakers', { project_id: currentProjectId || 1, auto_diarize: true });
  }
});

/* ═══════════════ TIMELINE WIRING ═══════════════ */
async function loadTimeline(projectId) {
  const data = await apiGet(`/timeline/${projectId}`);
  if (!data) return;
  const tracksContainer = document.querySelector('.tracks-container');
  if (!tracksContainer) return;
  const trackTypes = [
    { type: 'video', icon: 'ri-film-line', label: 'Video', clipClass: 'track-clip' },
    { type: 'subtitle', icon: 'ri-closed-captioning-line', label: 'Phụ đề', clipClass: 'track-clip music-clip' },
    { type: 'voice', icon: 'ri-mic-line', label: 'Thoại', clipClass: 'track-clip voice-clip' },
    { type: 'music', icon: 'ri-music-2-line', label: 'Nhạc nền', clipClass: 'track-clip subtitle-clip' },
  ];
  const totalFrames = Math.max(
    ...(data.tracks || []).flatMap(t => (t.clips || []).map(c => (c.position_frame || 0) + Math.max((c.end_frame || 0) - (c.start_frame || 0), 1))),
    900
  );
  tracksContainer.innerHTML = trackTypes.map((tt, ti) => {
    const track = (data.tracks || []).find(t => t.type === tt.type);
    const clipsHtml = track
      ? (track.clips || []).map(c => {
        const startFrame = c.start_frame || 0;
        const endFrame = c.end_frame || 0;
        const positionFrame = c.position_frame || 0;
        const duration = Math.max(endFrame - startFrame, 1);
        const w = totalFrames > 0 ? (duration / totalFrames * 100) : 0;
        const l = totalFrames > 0 ? ((c.position_frame || 0) / totalFrames * 100) : 0;
        const title = `${c.name || 'Clip'} | ${formatTime(Math.floor(positionFrame / 30))} | ${duration} frames`;
        return `<div class="${tt.clipClass}" data-clip-id="${c.id}" data-track-id="${track.id}" data-total-frames="${totalFrames}" data-start-frame="${startFrame}" data-end-frame="${endFrame}" data-position-frame="${positionFrame}" style="width:${Math.max(w, 2)}%;left:${l}%" title="${escapeHtml(title)}"><span class="clip-resize clip-resize-left" data-side="left"></span><span class="clip-title">${escapeHtml(c.name || 'Clip')}</span><span class="clip-resize clip-resize-right" data-side="right"></span></div>`;
      }).join('')
      : '<span class="track-lane-empty">—</span>';
    return `
      <div class="track-row">
        <div class="track-label"><i class="${tt.icon}"></i> ${tt.label}</div>
        <div class="track-lane">${clipsHtml || '<span class="track-lane-empty">—</span>'}</div>
      </div>
    `;
  }).join('');
  tracksContainer.querySelectorAll('.track-lane').forEach((lane, idx) => {
    const tt = trackTypes[idx];
    const track = (data.tracks || []).find(t => t.type === tt?.type);
    lane.dataset.trackId = track?.id || '';
    lane.dataset.totalFrames = String(totalFrames);
  });
  const ruler = document.querySelector('.timeline-ruler');
  if (ruler && data.tracks?.length) {
    const lastClip = data.tracks.flatMap(t => t.clips || []).slice(-1)[0];
    if (lastClip) {
      ruler.innerHTML = `<span>00:00</span><span>${formatTime(Math.floor((lastClip.end_frame || 0) / 30))}</span>`;
    }
  }
  setTimeout(makeTimelineInteractive, 100);
}


/* ═══════════════ MUSIC TAB HANDLERS ═══════════════ */
let selectedMusicFolder = '';
let selectedMusicPath = '';
let selectedMusicPathB = '';
let selectedMusicFiles = [];

function setSelectedMusicTrack(path, secondPath = '') {
  selectedMusicPath = path || '';
  selectedMusicPathB = secondPath || selectedMusicPathB || '';
  if (selectedMusicPath) {
    const name = selectedMusicPath.split(/[\\/]/).pop();
    showToast('Selected music: ' + name, 'success', 2500);
  }
}

function requireSelectedMusic(promptLabel = 'Nhap duong dan file nhac:') {
  if (selectedMusicPath) return selectedMusicPath;
  const typed = prompt(promptLabel, '');
  if (typed) setSelectedMusicTrack(typed);
  return selectedMusicPath;
}

document.getElementById('btn-music-apply')?.addEventListener('click', async () => {
  const mVol = parseInt(document.getElementById('slider-music-volume')?.value || '35') / 100;
  const fIn = document.getElementById('chk-music-fade-in')?.checked ? 2 : 0;
  const fOut = document.getElementById('chk-music-fade-out')?.checked ? 2 : 0;
  const norm = document.getElementById('chk-music-normalize')?.checked || false;
  const inp = requireSelectedMusic();
  if (!inp) {
    showToast('Chua chon file nhac', 'warn');
    return;
  }
  const qs = new URLSearchParams({
    input_path: inp,
    volume: String(mVol),
    fade_in: String(fIn),
    fade_out: String(fOut),
    normalize: String(norm),
    project_id: String(currentProjectId || 0),
  });
  const res = await apiPost('/music/process?' + qs.toString());
  if (res?.id) {
    addTaskRow();
    showToast('Music processing queued', 'success');
  }
});

document.getElementById('chk-music-duck')?.addEventListener('change', async function () {
  if (this.checked) {
    const musicPath = requireSelectedMusic();
    if (!musicPath) {
      this.checked = false;
      showToast('Chua chon file nhac de duck', 'warn');
      return;
    }
    const res = await apiPost('/music/duck', {
      project_id: currentProjectId || 1,
      music_path: musicPath,
    });
    if (res?.id) {
      addTaskRow();
      showToast('Auto ducking queued', 'success');
    }
  }
});

document.getElementById('btn-music-folder')?.addEventListener('click', async () => {
  const chosen = await apiGet('/system/browse?type=folder');
  if (!chosen?.path) return;
  selectedMusicFolder = chosen.path;
  const files = await apiGet('/music/files?folder=' + encodeURIComponent(selectedMusicFolder));
  selectedMusicFiles = Array.isArray(files) ? files : [];
  if (selectedMusicFiles.length) {
    setSelectedMusicTrack(selectedMusicFiles[0].path, selectedMusicFiles[1]?.path || '');
  }
  const names = selectedMusicFiles.map((f, idx) => `${idx === 0 ? '* ' : '- '}${f.name}`).join('\n') || '(empty)';
  alert(`Thu muc nhac:\n${selectedMusicFolder}\n\n${names}\n\n* = track dang chon`);
});

/* ═══════════════ ENHANCE TAB HANDLERS ═══════════════ */
document.getElementById('btn-enhance-apply')?.addEventListener('click', async () => {
  const lut = document.getElementById('sel-enhance-lut')?.value;
  const r = await apiPost('/enhance/apply', {
    project_id: currentProjectId || 1,
    video_path: getInputMediaPath(),
    lut: lut === 'None' ? null : lut,
    brightness: parseInt(document.getElementById('slider-enhance-brightness')?.value || '50'),
    contrast: parseInt(document.getElementById('slider-enhance-contrast')?.value || '55'),
    saturation: parseInt(document.getElementById('slider-enhance-saturation')?.value || '60'),
    temperature: parseInt(document.getElementById('slider-enhance-temperature')?.value || '48'),
    vignette: parseInt(document.getElementById('slider-enhance-vignette')?.value || '12'),
    film_look: document.getElementById('sel-enhance-film-look')?.value || 'Review phim',
    watermark: document.getElementById('chk-enhance-watermark')?.checked || false,
    transition: document.getElementById('chk-enhance-transition')?.checked || false,
    motion_blur: document.getElementById('chk-enhance-motion')?.checked || false,
    zoom: document.getElementById('chk-enhance-zoom')?.checked || false,
    shake: document.getElementById('chk-enhance-shake')?.checked || false,
    particles: document.getElementById('chk-enhance-particles')?.checked || false,
    speed_ramp: document.getElementById('chk-enhance-speed')?.checked || false,
    slow_motion: document.getElementById('chk-enhance-slow')?.checked || false,
    fast_motion: document.getElementById('chk-enhance-fast')?.checked || false,
    watermark_text: document.getElementById('inp-enhance-watermark-text')?.value?.trim() || '',
  });
  if (r) addTaskRow();
});

/* ═══════════════ EDIT TAB HANDLERS ═══════════════ */
const EDIT_PREVIEW_DEFAULTS = {
  speed: 100,
  brightness: 100,
  contrast: 100,
  saturation: 100,
  volume: 100,
  rotate: 0,
  flipHorizontal: false,
  flipVertical: false,
};

function readEditNumber(id, fallback) {
  const value = Number(document.getElementById(id)?.value);
  return Number.isFinite(value) ? value : fallback;
}

function getEditPreviewSettings() {
  return {
    speed: readEditNumber('slider-edit-speed', EDIT_PREVIEW_DEFAULTS.speed) / 100,
    brightness: readEditNumber('slider-edit-brightness', EDIT_PREVIEW_DEFAULTS.brightness),
    contrast: readEditNumber('slider-edit-contrast', EDIT_PREVIEW_DEFAULTS.contrast),
    saturation: readEditNumber('slider-edit-saturation', EDIT_PREVIEW_DEFAULTS.saturation),
    volume: readEditNumber('slider-edit-volume', EDIT_PREVIEW_DEFAULTS.volume),
    rotate: readEditNumber('sel-edit-rotate', EDIT_PREVIEW_DEFAULTS.rotate),
    flipHorizontal: document.getElementById('chk-edit-flip-h')?.checked || false,
    flipVertical: document.getElementById('chk-edit-flip-v')?.checked || false,
  };
}

function hasEditPreviewAdjustment(settings = getEditPreviewSettings()) {
  return Math.abs(settings.speed - 1) > 0.001
    || Math.abs(settings.brightness - 100) > 0.001
    || Math.abs(settings.contrast - 100) > 0.001
    || Math.abs(settings.saturation - 100) > 0.001
    || Math.abs(settings.volume - 100) > 0.001
    || Number(settings.rotate || 0) !== 0
    || settings.flipHorizontal
    || settings.flipVertical;
}

function syncEditPreviewLabels(settings = getEditPreviewSettings()) {
  const setText = (id, text) => {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  };
  setText('val-edit-speed', `${settings.speed.toFixed(2)}x`);
  setText('val-edit-brightness', `${Math.round(settings.brightness)}%`);
  setText('val-edit-contrast', `${Math.round(settings.contrast)}%`);
  setText('val-edit-saturation', `${Math.round(settings.saturation)}%`);
  setText('val-edit-volume', `${Math.round(settings.volume)}%`);
}

function applyEditPreviewSettings() {
  const settings = getEditPreviewSettings();
  syncEditPreviewLabels(settings);
  const video = document.getElementById('video-player');
  if (!video) return settings;
  video.playbackRate = Math.max(0.25, Math.min(4, settings.speed || 1));
  video.volume = Math.max(0, Math.min(1, settings.volume / 100));
  video.muted = settings.volume <= 0;
  video.style.filter = `brightness(${settings.brightness}%) contrast(${settings.contrast}%) saturate(${settings.saturation}%)`;
  const scaleX = settings.flipHorizontal ? -1 : 1;
  const scaleY = settings.flipVertical ? -1 : 1;
  video.style.transform = `rotate(${settings.rotate || 0}deg) scale(${scaleX}, ${scaleY})`;
  video.style.transformOrigin = 'center center';
  return settings;
}

function resetEditPreviewSettings() {
  const setValue = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.value = String(value);
  };
  setValue('slider-edit-speed', EDIT_PREVIEW_DEFAULTS.speed);
  setValue('slider-edit-brightness', EDIT_PREVIEW_DEFAULTS.brightness);
  setValue('slider-edit-contrast', EDIT_PREVIEW_DEFAULTS.contrast);
  setValue('slider-edit-saturation', EDIT_PREVIEW_DEFAULTS.saturation);
  setValue('slider-edit-volume', EDIT_PREVIEW_DEFAULTS.volume);
  setValue('sel-edit-rotate', EDIT_PREVIEW_DEFAULTS.rotate);
  setValue('inp-edit-rotate', EDIT_PREVIEW_DEFAULTS.rotate);
  const flipH = document.getElementById('chk-edit-flip-h');
  const flipV = document.getElementById('chk-edit-flip-v');
  if (flipH) flipH.checked = false;
  if (flipV) flipV.checked = false;
  applyEditPreviewSettings();
}

document.querySelectorAll('.edit-preview-control').forEach(control => {
  control.addEventListener('input', applyEditPreviewSettings);
  control.addEventListener('change', applyEditPreviewSettings);
});

document.getElementById('inp-edit-rotate')?.addEventListener('input', () => {
  const angle = Math.round(readEditNumber('inp-edit-rotate', 0) / 90) * 90;
  const select = document.getElementById('sel-edit-rotate');
  if (select) select.value = String(((angle % 360) + 360) % 360);
  applyEditPreviewSettings();
});

document.getElementById('btn-edit-preview-reset')?.addEventListener('click', resetEditPreviewSettings);

document.getElementById('btn-edit-adjust-apply')?.addEventListener('click', async (event) => {
  const btn = event.currentTarget;
  const originalHTML = btn.innerHTML;
  try {
    const videoPath = getInputMediaPath();
    if (!videoPath) {
      showToast('Chua chon video de chinh sua', 'warn');
      return;
    }
    const settings = applyEditPreviewSettings();
    if (!hasEditPreviewAdjustment(settings)) {
      showToast('Chua co tinh chinh nao de render', 'warn');
      return;
    }
    btn.disabled = true;
    btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Dang tao job...';
    const outputPath = document.getElementById('inp-output-path')?.value?.trim() || '';
    const result = await apiPost('/edit/adjust', {
      project_id: currentProjectId || 1,
      video_path: videoPath,
      output_path: outputPath || undefined,
      speed: settings.speed,
      brightness: settings.brightness,
      contrast: settings.contrast,
      saturation: settings.saturation,
      volume: settings.volume,
      rotate: settings.rotate,
      flip_horizontal: settings.flipHorizontal,
      flip_vertical: settings.flipVertical,
    });
    if (result) {
      showToast('Da dua lenh chinh sua vao hang doi', 'success');
      addTaskRow();
      const note = document.getElementById('edit-preview-note');
      if (note && result.output) note.textContent = 'Se render ra: ' + result.output;
    }
  } catch (error) {
    showToast(error.message || String(error), 'error');
    addClientLog?.('error', 'Edit adjust failed', error.stack || error.message || String(error));
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHTML;
  }
});

document.getElementById('btn-edit-rotate')?.addEventListener('click', () => {
  const select = document.getElementById('sel-edit-rotate');
  const numberInput = document.getElementById('inp-edit-rotate');
  const requested = readEditNumber('inp-edit-rotate', Number(select?.value || 90));
  const next = ((Math.round(requested / 90) * 90) % 360 + 360) % 360 || 90;
  if (select) select.value = String(next);
  if (numberInput) numberInput.value = String(next);
  applyEditPreviewSettings();
  showToast('Da xoay trong preview. Bam Ap dung render neu thay on.', 'info', 2200);
});

document.getElementById('btn-edit-flip')?.addEventListener('click', () => {
  const flip = document.getElementById('chk-edit-flip-h');
  if (flip) flip.checked = !flip.checked;
  applyEditPreviewSettings();
  showToast('Da lat ngang trong preview. Bam Ap dung render neu thay on.', 'info', 2200);
});

document.getElementById('btn-edit-split')?.addEventListener('click', async (event) => {
  const btn = event.currentTarget;
  const originalHTML = btn.innerHTML;
  try {
    const start = prompt('Thoi gian bat dau (giay):', '0');
    if (start === null) return;
    const end = prompt('Thoi gian ket thuc (giay):', '10');
    if (end === null) return;

    validate(start, 'timestamp', 'Thoi gian bat dau khong hop le');
    validate(end, 'timestamp', 'Thoi gian ket thuc khong hop le');
    validate([start, end], 'timeRange');

    const videoPath = document.getElementById('inp-video-path')?.value?.trim() || '';
    validate(videoPath, 'videoFile', 'Vui long chon file video hop le');

    btn.disabled = true;
    btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Dang cat...';
    const res = await apiPost('/edit/split', {
      video_path: videoPath,
      operations: [{ type: 'split', start: Number(start), end: Number(end) }],
    });
    if (res) {
      showToast('Da gui lenh cat video', 'success');
      addTaskRow();
    }
  } catch (error) {
    showToast(error.message || String(error), error instanceof ValidationError ? 'warn' : 'error');
    addClientLog('error', 'Split video failed', error.stack || error.message || String(error));
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHTML;
  }
});

document.getElementById('btn-edit-merge')?.addEventListener('click', async (event) => {
  const btn = event.currentTarget;
  const originalHTML = btn.innerHTML;
  try {
    const inp = prompt('Nhap duong dan cac video, cach nhau bang dau phay:', '');
    if (!inp) return;
    const paths = inp.split(',').map(p => p.trim()).filter(Boolean);
    validate(paths, 'pathArray', 'Can it nhat 2 video hop le');

    btn.disabled = true;
    btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Dang ghep...';
    const res = await apiPost('/edit/merge', {
      video_paths: paths,
      output_format: 'mp4',
    });
    if (res) {
      showToast('Da gui lenh ghep video', 'success');
      addTaskRow();
    }
  } catch (error) {
    showToast(error.message || String(error), error instanceof ValidationError ? 'warn' : 'error');
    addClientLog('error', 'Merge video failed', error.stack || error.message || String(error));
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHTML;
  }
});

/* ═══════════════ SUBTITLE TAB HANDLERS ═══════════════ */
async function getSubtitleText() {
  // Priority 1: try to get already-imported subtitle from DB
  try {
    const subs = await apiGet('/subtitle/' + (currentProjectId || 1));
    if (subs && subs.length > 0 && subs[0].content) return subs[0].content;
  } catch (_) { }
  // Priority 2: read file content from local path via backend
  const srtPath = document.getElementById('inp-srt-path')?.value?.trim();
  if (srtPath) {
    try {
      const r = await apiPost('/subtitle/read-file', { path: srtPath });
      if (r && r.content) return r.content;
    } catch (_) { }
    return srtPath; // fallback: send the path and let backend open it
  }
  return '';
}

function normalizeSrtTime(timeText) {
  return String(timeText || '').trim().replace('.', ',');
}

function parseSrtCues(srtText) {
  const text = String(srtText || '').replace(/\r/g, '').trim();
  if (!text) return [];
  return text.split(/\n{2,}/).map((block, blockIndex) => {
    const lines = block.split('\n').map(line => line.trimEnd()).filter(Boolean);
    const timeLineIndex = lines.findIndex(line => line.includes('-->'));
    if (timeLineIndex < 0) return null;
    const indexText = timeLineIndex > 0 && /^\d+$/.test(lines[0].trim()) ? lines[0].trim() : String(blockIndex + 1);
    const [startRaw, endRaw = ''] = lines[timeLineIndex].split('-->');
    const endClean = endRaw.split(/\s+/)[0];
    return {
      index: indexText,
      start: normalizeSrtTime(startRaw),
      end: normalizeSrtTime(endClean),
      text: lines.slice(timeLineIndex + 1).join('\n').trim(),
    };
  }).filter(Boolean);
}

function renderSubtitleCueTable(originalText, translatedText = '') {
  const body = document.getElementById('subtitle-cue-body');
  const empty = document.getElementById('subtitle-cue-empty');
  const count = document.getElementById('sub-cue-count');
  if (!body) return;

  const originalCues = parseSrtCues(originalText);
  const translatedCues = parseSrtCues(translatedText);
  const translatedByIndex = new Map(translatedCues.map(cue => [String(cue.index), cue.text]));

  body.innerHTML = '';
  if (!originalCues.length) {
    if (empty) {
      empty.style.display = 'block';
      empty.textContent = originalText ? 'Không parse được SRT. Kiểm tra định dạng Start --> End.' : 'Chọn file SRT hoặc chạy nhận diện/dịch, rồi bấm Tải bảng sub.';
    }
    if (count) count.textContent = '0 dòng';
    return;
  }

  if (empty) empty.style.display = 'none';
  if (count) count.textContent = `${originalCues.length} dòng`;

  const rows = originalCues.map((cue, i) => {
    const translatedCue = translatedByIndex.get(String(cue.index)) || translatedCues[i]?.text || '';
    return `
      <div class="subtitle-cue-row">
        <div class="subtitle-cue-index">${escapeHtml(cue.index)}</div>
        <div class="subtitle-cue-time">${escapeHtml(cue.start)}</div>
        <div class="subtitle-cue-time">${escapeHtml(cue.end)}</div>
        <div class="subtitle-cue-text">${escapeHtml(cue.text)}</div>
        <div class="subtitle-cue-text subtitle-cue-translation">${escapeHtml(translatedCue)}</div>
      </div>
    `;
  }).join('');
  body.innerHTML = rows;
}

async function refreshSubtitleCueTable() {
  const btn = document.getElementById('btn-sub-cue-refresh');
  const oldHtml = btn?.innerHTML;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Đang tải';
  }
  try {
    const originalText = await getSubtitleText();
    const translatedText = document.getElementById('trans-result-text')?.value || '';
    renderSubtitleCueTable(originalText, translatedText);
  } catch (e) {
    alert('Không tải được bảng sub: ' + (e.message || e));
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = oldHtml;
    }
  }
}

document.getElementById('btn-sub-cue-refresh')?.addEventListener('click', refreshSubtitleCueTable);

function getLangCodeFromValueOrText(val) {
  if (!val) return 'vi';
  const clean = val.toLowerCase().trim();
  if (clean === 'en' || clean.includes('anh') || clean.includes('english')) return 'en';
  if (clean === 'zh' || clean.includes('trung') || clean.includes('china') || clean.includes('chinese')) return 'zh';
  if (clean === 'ja' || clean.includes('nhat') || clean.includes('japan') || clean.includes('japanese')) return 'ja';
  if (clean === 'ko' || clean.includes('han') || clean.includes('korea') || clean.includes('korean')) return 'ko';
  if (clean === 'vi' || clean.includes('viet') || clean.includes('vietnam') || clean.includes('vietnamese')) return 'vi';
  if (clean === 'es' || clean.includes('tay ban nha') || clean.includes('spanish')) return 'es';
  if (clean === 'fr' || clean.includes('phap') || clean.includes('french')) return 'fr';
  if (clean === 'de' || clean.includes('duc') || clean.includes('german')) return 'de';
  if (clean === 'ru' || clean.includes('nga') || clean.includes('russian')) return 'ru';
  if (clean === 'pt' || clean.includes('bo dao nha') || clean.includes('portuguese')) return 'pt';
  if (clean === 'ar' || clean.includes('a rap') || clean.includes('arabic')) return 'ar';
  if (clean === 'it' || clean.includes('y') || clean.includes('italian')) return 'it';
  if (clean === 'th' || clean.includes('thai')) return 'th';
  return clean;
}

function getLangCodeFromSelect(id, fallback = 'vi') {
  const select = document.getElementById(id);
  if (!select) return fallback;
  const val = select.value || select.options[select.selectedIndex]?.text || '';
  return getLangCodeFromValueOrText(val) || fallback;
}

function getSubtitleModel(engine) {
  if (engine === 'gpt') return document.getElementById('sel-sub-gpt-model')?.value || 'GPT-4';
  if (engine === 'gemini') return document.getElementById('sel-sub-gemini-model')?.value || 'gemini-2.0-flash';
  if (engine === 'ai_provider') return JSON.stringify(getAIProviderConfigFromUI());
  if (engine === 'deeplx') return document.getElementById('inp-deeplx-url')?.value?.trim() || undefined;
  if (engine === 'nllb') return document.getElementById('sel-sub-nllb-model')?.value || 'facebook/nllb-200-distilled-1.3B';
  if (engine === 'seamless') return document.getElementById('sel-sub-seamless-model')?.value || 'facebook/hf-seamless-m4t-medium';
  if (engine === 'm2m100') return document.getElementById('sel-sub-m2m100-model')?.value || 'facebook/m2m100_418M';
  return undefined;
}

function getVoiceModeOptions() {
  const modeSelect = document.getElementById('sel-voice-mode');
  const voiceEnabled = (modeSelect?.selectedIndex ?? 0) !== 1;
  return {
    tts_enabled: voiceEnabled,
    tts_volume: Number(document.getElementById('slider-voice-volume')?.value || 85) / 100,
    original_audio_volume: Number(document.getElementById('slider-orig-audio')?.value || 10) / 100,
  };
}

document.getElementById('btn-sub-transcribe')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-sub-transcribe');
  const videoPath = document.getElementById('inp-video-path')?.value || '';
  if (!videoPath) {
    alert('Vui lòng chọn tệp video ở phần "Nguồn vào" trước!');
    return;
  }

  btn.disabled = true;
  const oldText = btn.innerHTML;
  btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Đang nhận diện...';

  try {
    if (!currentProjectId) {
      const project = await apiPost('/projects', {
        name: 'project_' + Date.now(),
        project_preset: document.getElementById('sel-project-preset')?.value || 'Movie Review',
      });
      if (project) {
        currentProjectId = project.id;
        setTimeout(() => loadTimeline(currentProjectId), 500);
      }
    }

    const lang = document.getElementById('sel-sub-transcribe-lang')?.value || 'vi';
    const vocalSep = document.getElementById('chk-vocal-sep')?.checked ?? false;
    const useWhisperX = document.getElementById('chk-sub-whisperx')?.checked ?? false;

    const res = await apiPost('/queue', {
      project_id: currentProjectId || 1,
      type: 'transcribe',
      input_path: videoPath,
      params: {
        language: lang,
        vocal_separation: vocalSep,
        whisperx: useWhisperX,
      }
    });

    if (res && res.id) {
      alert('Đã thêm tiến trình nhận dạng phụ đề gốc (Whisper STT) vào hàng chờ thành công!');
      addTaskRow();
    } else {
      alert('Tạo tiến trình hàng chờ thất bại.');
    }
  } catch (e) {
    alert('Lỗi nhận dạng phụ đề: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = oldText;
  }
});

document.getElementById('btn-sub-ocr')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-sub-ocr');
  const videoPath = document.getElementById('inp-video-path')?.value || '';
  if (!videoPath) {
    alert('Vui long chon video truoc khi chay RapidOCR.');
    return;
  }

  btn.disabled = true;
  const oldText = btn.innerHTML;
  btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> OCR...';
  try {
    const res = await apiPost('/subtitle/ocr-video', {
      path: videoPath,
      project_id: currentProjectId || 1,
      region: getSubBoxRegion() || { ...subBoxRegion, alignment: 'bottom-center' },
    });
    if (res) {
      alert('Da bat dau RapidOCR sub cung. Mo log de xem tien trinh; SRT se luu vao project khi xong.');
      addTaskRow();
    }
  } catch (e) {
    alert('Loi RapidOCR: ' + (e.message || e));
  } finally {
    btn.disabled = false;
    btn.innerHTML = oldText;
  }
});

/* ─── Translation Result Modal ─── */
const transResultModal = document.getElementById('trans-result-modal');
const transResultText = document.getElementById('trans-result-text');
const transResultInfo = document.getElementById('trans-result-info');

function isSubtitleSemanticEnabled() {
  return document.getElementById('chk-sub-semantic')?.checked ?? true;
}

function showTransResult(text, filename) {
  if (transResultText) transResultText.value = text;
  if (transResultInfo) transResultInfo.textContent = `NLLB-200 — ${filename || 'subtitle.srt'} (${(text || '').split('\n').length} dòng)`;
  refreshSubtitleCueTable();
  transResultModal?.classList.add('show');
}

document.getElementById('btn-trans-copy')?.addEventListener('click', () => {
  if (!transResultText?.value) return;
  navigator.clipboard.writeText(transResultText.value).then(() => {
    const btn = document.getElementById('btn-trans-copy');
    const orig = btn.innerHTML;
    btn.innerHTML = '<i class="ri-check-line"></i> Đã sao chép';
    setTimeout(() => btn.innerHTML = orig, 1500);
  });
});

document.getElementById('btn-trans-download')?.addEventListener('click', () => {
  const text = transResultText?.value;
  if (!text) return;
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'translated_nllb.srt';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

/* ─── Init: load dashboard & settings ─── */
loadDashboard();
setTimeout(async () => {
  const settings = await apiGet('/settings');
  if (settings) {
    if (settings.openai_key) document.getElementById('inp-set-openai').value = settings.openai_key;
    if (settings.elevenlabs_key) document.getElementById('inp-set-eleven').value = settings.elevenlabs_key;
    if (settings.ffmpeg_path) document.getElementById('inp-set-ffmpeg').value = settings.ffmpeg_path;
    if (settings.proxy) document.getElementById('inp-set-proxy').value = settings.proxy;
    if (settings.cookie_file) document.getElementById('inp-set-cookie').value = settings.cookie_file;
    if (settings.youtube_cookie) document.getElementById('inp-set-yt-cookie').value = settings.youtube_cookie;
    if (settings.chatgpt_cookies) document.getElementById('inp-set-chatgpt-cookie').value = settings.chatgpt_cookies;
    if (settings.gemini_cookies) document.getElementById('inp-set-gemini-cookie').value = settings.gemini_cookies;
    if (settings.deeplx_url) document.getElementById('inp-deeplx-url').value = settings.deeplx_url;
    if (settings.ai_provider) document.getElementById('sel-ai-provider').value = settings.ai_provider;
    if (settings.ai_api_key) document.getElementById('inp-ai-api-key').value = settings.ai_api_key;
    if (settings.ai_base_url) document.getElementById('inp-ai-base-url').value = settings.ai_base_url;
    if (settings.ai_model) document.getElementById('inp-ai-model').value = settings.ai_model;
    if (settings.ai_temperature) document.getElementById('inp-ai-temperature').value = settings.ai_temperature;
    if (settings.ai_max_tokens) document.getElementById('inp-ai-max-tokens').value = settings.ai_max_tokens;
    if (settings.ai_fallback) document.getElementById('inp-ai-fallback').value = settings.ai_fallback;
    applyAIProviderPreset(false);
    refreshAIProviderModelLabel();
  }
}, 200);

/* ═══════════════ SETTINGS SAVE ═══════════════ */
document.getElementById('btn-save-settings')?.addEventListener('click', async () => {
  const data = {
    openai_key: document.getElementById('inp-set-openai')?.value || '',
    elevenlabs_key: document.getElementById('inp-set-eleven')?.value || '',
    ffmpeg_path: document.getElementById('inp-set-ffmpeg')?.value || '',
    proxy: document.getElementById('inp-set-proxy')?.value || '',
    cookie_file: document.getElementById('inp-set-cookie')?.value || '',
    youtube_cookie: document.getElementById('inp-set-yt-cookie')?.value || '',
    chatgpt_cookies: document.getElementById('inp-set-chatgpt-cookie')?.value || '',
    gemini_cookies: document.getElementById('inp-set-gemini-cookie')?.value || '',
    deeplx_url: document.getElementById('inp-deeplx-url')?.value || '',
    ai_provider: document.getElementById('sel-ai-provider')?.value || 'openrouter',
    ai_api_key: document.getElementById('inp-ai-api-key')?.value || '',
    ai_base_url: document.getElementById('inp-ai-base-url')?.value || '',
    ai_model: document.getElementById('inp-ai-model')?.value || '',
    ai_temperature: document.getElementById('inp-ai-temperature')?.value || '0.2',
    ai_max_tokens: document.getElementById('inp-ai-max-tokens')?.value || '4096',
    ai_fallback: document.getElementById('inp-ai-fallback')?.value || '',
  };
  const result = await apiPut('/settings', data);
  if (result) {
    alert('Đã lưu cài đặt!');
    document.getElementById('settings-modal')?.classList.remove('show');
  }
});

document.getElementById('btn-open-settings')?.addEventListener('click', () => {
  document.getElementById('settings-modal')?.classList.add('show');
});

document.getElementById('btn-open-download')?.addEventListener('click', () => {
  document.getElementById('download-modal')?.classList.add('show');
  if (typeof loadDownloadHistory === 'function') loadDownloadHistory();
});

/* ═══════════════ BROWSER COOKIES AUTO-GRAB & CHECK ═══════════════ */
const setupCookieControl = (provider, grabChromeBtnId, grabEdgeBtnId, checkBtnId, statusSpanId, textareaId) => {
  const checkStatus = async (usePlaywright = false) => {
    const span = document.getElementById(statusSpanId);
    if (!span) return;
    span.textContent = 'Checking...';
    span.style.color = '#718096';

    const res = await apiPost('/ai/cookies/check', { provider, use_playwright: usePlaywright });
    if (!res) return;
    if (res.status === 'live') {
      span.textContent = 'LIVE';
      span.style.color = '#48bb78';
    } else if (res.status === 'die') {
      span.textContent = 'DIE';
      span.style.color = '#f56565';
    } else if (res.status === 'empty') {
      span.textContent = 'NOT SET';
      span.style.color = '#718096';
    } else {
      span.textContent = 'ERROR: ' + (res.message || 'unknown');
      span.style.color = '#ed8936';
    }
  };

  const ensureDeleteButton = () => {
    const checkBtn = document.getElementById(checkBtnId);
    if (!checkBtn || document.getElementById(`btn-delete-${provider}-cookie`)) return;
    const del = document.createElement('button');
    del.id = `btn-delete-${provider}-cookie`;
    del.className = 'action-btn';
    del.style.cssText = 'height:20px;padding:0 6px;font-size:10px;text-transform:none;background:#7f1d1d;color:#fff;border:none';
    del.innerHTML = '<i class="ri-delete-bin-line"></i> Delete';
    del.addEventListener('click', async () => {
      if (!confirm(`Delete stored ${provider.toUpperCase()} cookies from this app?`)) return;
      const res = await apiDel(`/ai/cookies/${provider}`);
      if (res) {
        const textarea = document.getElementById(textareaId);
        if (textarea) textarea.value = '';
        showToast(`${provider.toUpperCase()} cookies deleted`, 'success');
        checkStatus(false);
      }
    });
    checkBtn.insertAdjacentElement('afterend', del);
  };

  const grabCookies = async (browser) => {
    const textarea = document.getElementById(textareaId);
    if (!textarea) return;
    const ok = confirm(
      `This will read ${provider.toUpperCase()} cookies from ${browser}. Only continue if you own this browser profile and agree to store them encrypted on this device.`
    );
    if (!ok) return;

    const originalText = textarea.placeholder;
    textarea.placeholder = 'Reading browser cookies with your consent...';
    try {
      const res = await apiPost('/ai/cookies/grab', { browser, provider, consent: true });
      if (res?.status === 'success' && (res.grabbed?.[provider] || 0) > 0) {
        const settings = await apiGet('/settings');
        if (settings?.[`${provider}_cookies`]) textarea.value = settings[`${provider}_cookies`];
        showToast(`Imported ${res.grabbed[provider]} ${provider.toUpperCase()} cookies`, 'success');
        checkStatus(false);
      } else {
        showToast(`No active ${provider.toUpperCase()} cookies found in ${browser}`, 'warn');
      }
    } finally {
      textarea.placeholder = originalText;
    }
  };

  ensureDeleteButton();
  document.getElementById(grabChromeBtnId)?.addEventListener('click', () => grabCookies('chrome'));
  document.getElementById(grabEdgeBtnId)?.addEventListener('click', () => grabCookies('edge'));
  document.getElementById(checkBtnId)?.addEventListener('click', () => checkStatus(false));
};

setupCookieControl('chatgpt', 'btn-grab-chatgpt-chrome', 'btn-grab-chatgpt-edge', 'btn-check-chatgpt-cookie', 'chatgpt-cookie-status', 'inp-set-chatgpt-cookie');
setupCookieControl('gemini', 'btn-grab-gemini-chrome', 'btn-grab-gemini-edge', 'btn-check-gemini-cookie', 'gemini-cookie-status', 'inp-set-gemini-cookie');

document.getElementById('btn-sub-trans-batch')?.addEventListener('click', async () => {
  const text = await getSubtitleText();
  if (!text) { alert('Chưa tải phụ đề.'); return; }
  await apiPost('/subtitle/translate', {
    text, engine: 'gpt',
    model: getSubtitleModel('gpt'),
    source_lang: getLangCodeFromSelect('sel-lang-from', 'zh'),
    target_lang: getLangCodeFromSelect('sel-lang-to', 'vi'),
    project_id: currentProjectId || null,
    semantic_segmentation: isSubtitleSemanticEnabled(),
  });
  addTaskRow();
});

document.getElementById('btn-sub-export-srt')?.addEventListener('click', async () => {
  const r = await apiPost('/subtitle/export?project_id=' + (currentProjectId || 1) + '&fmt=srt');
  if (r) addTaskRow();
});

document.getElementById('btn-sub-export-ass')?.addEventListener('click', async () => {
  const style = getSubtitleStyleOptions();
  const query = new URLSearchParams({
    project_id: String(currentProjectId || 1),
    fmt: 'ass',
    font: style.subtitle_font,
    size: String(style.subtitle_size),
    color: style.subtitle_color,
    shadow: style.subtitle_shadow,
  });
  const r = await apiPost('/subtitle/export?' + query.toString());
  if (r) addTaskRow();
});

document.getElementById('btn-sub-export-burn')?.addEventListener('click', async () => {
  const r = await apiPost('/pipeline/start', {
    project_id: currentProjectId || 1,
    input_path: document.getElementById('inp-video-path')?.value || '',
    type: 'pipeline',
    params: {
      source_lang: 'vi', target_lang: 'vi',
      translate_engine: getPipelineTranslateEngine('vi', 'vi'),
      translate_model: getPipelineTranslateModel(getPipelineTranslateEngine('vi', 'vi')),
      tts_provider: 'edge',
      tts_voice: 'vi-VN-NamMinhNeural',
      burn_subtitle: true,
      subtitle_region: getSubBoxRegion() || undefined,
      remove_hardsub: subBlurEnabled,
      ...getSubtitleStyleOptions(),
      tts_enabled: false,
    },
  });
  if (r) addTaskRow();
});

/* ═══════════════ MODAL LOGIC ═══════════════ */
document.querySelectorAll('.close-modal').forEach(btn => {
  btn.addEventListener('click', (e) => {
    e.target.closest('.modal-overlay')?.classList.remove('show');
  });
});

function updateDownloadProgressUI(data) {
  if (!data || !data.id) return;
  const fill = document.getElementById('download-progress');
  const pct = document.getElementById('download-pct');
  const row = document.getElementById('download-output-row');
  const pathInput = document.getElementById('inp-downloaded-path');
  const progress = Math.max(0, Math.min(100, Math.round(data.progress || 0)));
  if (fill) fill.style.width = progress + '%';
  if (pct) pct.textContent = progress + '%';
  if (data.output_path && pathInput) {
    pathInput.value = data.output_path;
    if (row) row.style.display = 'flex';
    const videoInput = document.getElementById('inp-video-path');
    if (videoInput) {
      videoInput.value = data.output_path;
      loadVideoPreview();
    }
  }
  if (data.status === 'failed' && data.error) {
    showToast('Download loi: ' + data.error, 'error', 6000);
  }
}

document.getElementById('btn-browse-download-output')?.addEventListener('click', async () => {
  const res = await apiGet('/system/browse?type=folder');
  if (res && res.path) document.getElementById('inp-download-output').value = res.path;
});

document.getElementById('btn-start-download')?.addEventListener('click', async () => {
  const url = document.getElementById('inp-download-url')?.value;
  if (!url) return;
  const quality = document.getElementById('sel-download-quality')?.value || 'best';
  const proxy = document.getElementById('inp-download-proxy')?.value || '';
  const outputDir = document.getElementById('inp-download-output')?.value || '';

  document.getElementById('download-progress-container').style.display = 'flex';
  document.getElementById('download-output-row').style.display = 'none';
  const fill = document.getElementById('download-progress');
  const pct = document.getElementById('download-pct');
  fill.style.width = '0%'; pct.textContent = '0%';

  const res = await apiPost('/download/', { url, quality, proxy, output_dir: outputDir, project_id: currentProjectId || 0 });
  if (res && res.id) {
    pollDownload(res.id);
  } else {
    showToast('Khong tao duoc job download', 'error');
  }
});

async function pollDownload(downloadId) {
  const interval = setInterval(async () => {
    const data = await apiGet('/download/' + downloadId);
    if (data) {
      updateDownloadProgressUI(data);
      if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
        clearInterval(interval);
        loadDownloadHistory();
        if (data.status === 'completed') {
          showToast('Download xong', 'success', 3000);
        }
      }
    }
  }, 1500);
}


function loadVideoPreview() {
  const path = document.getElementById('inp-video-path')?.value?.trim() || '';
  const video = document.getElementById('video-player');
  const msg = document.getElementById('no-video-msg');
  const canvas = document.getElementById('preview-canvas');
  if (!video || !msg) return;
  const isVideo = /\.(mp4|mkv|avi|mov)$/i.test(path);

  if (path && isVideo) {
    video.src = '/api/video/serve?path=' + encodeURIComponent(path);
    video.style.display = 'block';
    msg.style.display = 'none';
    canvas?.classList.add('has-media');
    canvas?.classList.remove('no-media');
    video.load();
    applyEditPreviewSettings?.();
  } else {
    video.pause?.();
    video.removeAttribute('src');
    video.load();
    video.style.display = 'none';
    msg.style.display = 'flex';
    canvas?.classList.add('no-media');
    canvas?.classList.remove('has-media');
  }
}

/* ─── Video preview from browse ─── */
document.getElementById('inp-video-path')?.addEventListener('change', loadVideoPreview);
document.getElementById('inp-video-path')?.addEventListener('paste', () => setTimeout(loadVideoPreview, 100));
document.getElementById('inp-srt-path')?.addEventListener('change', loadVideoPreview);
document.getElementById('inp-srt-path')?.addEventListener('paste', () => setTimeout(loadVideoPreview, 100));
document.getElementById('inp-srt-path')?.addEventListener('change', refreshSubtitleCueTable);
document.getElementById('inp-srt-path')?.addEventListener('paste', () => setTimeout(refreshSubtitleCueTable, 150));
document.getElementById('video-player')?.addEventListener('loadedmetadata', () => {
  if (subBoxVisible) subBoxSyncPosition();
});
loadVideoPreview();

/* ═══════════════ SUBTITLE BOX OVERLAY ═══════════════ */

// Default region as fraction of video size (0-1)
const SUB_BOX_DEFAULTS = { x: 0.1, y: 0.78, width: 0.8, height: 0.15 };
let subBoxVisible = false;
let subBoxRegion = { ...SUB_BOX_DEFAULTS };
let subBoxDrag = null; // { type: 'move'|'resize', startX, startY, startRect }
let subBlurEnabled = false;
let subBoxMode = 'subtitle';
let subBoxOpenedByBlur = false;

const ALIGNMENT_PRESETS = {
  'bottom-center': { x: 0.1, y: 0.78, width: 0.8, height: 0.15 },
  'top-center': { x: 0.1, y: 0.05, width: 0.8, height: 0.15 },
  'center': { x: 0.1, y: 0.42, width: 0.8, height: 0.15 },
  'bottom-left': { x: 0.05, y: 0.78, width: 0.45, height: 0.15 },
  'bottom-right': { x: 0.5, y: 0.78, width: 0.45, height: 0.15 },
  'top-left': { x: 0.05, y: 0.05, width: 0.45, height: 0.15 },
  'top-right': { x: 0.5, y: 0.05, width: 0.45, height: 0.15 },
};

function getPreviewMediaRect() {
  const canvas = document.getElementById('preview-canvas');
  const video = document.getElementById('video-player');
  if (!canvas) return { left: 0, top: 0, width: 1, height: 1 };
  const cw = Math.max(1, canvas.clientWidth);
  const ch = Math.max(1, canvas.clientHeight);
  const vw = video?.videoWidth || 0;
  const vh = video?.videoHeight || 0;
  if (!vw || !vh) return { left: 0, top: 0, width: cw, height: ch };

  const canvasRatio = cw / ch;
  const videoRatio = vw / vh;
  if (videoRatio > canvasRatio) {
    const height = cw / videoRatio;
    return { left: 0, top: (ch - height) / 2, width: cw, height };
  }
  const width = ch * videoRatio;
  return { left: (cw - width) / 2, top: 0, width, height: ch };
}

function updateSubBoxModeUi() {
  const overlay = document.getElementById('sub-box-overlay');
  const label = overlay?.querySelector('.sub-box-label');
  const title = document.querySelector('#sub-region-controls .sub-region-title');
  const blurBtn = document.getElementById('btn-preview-sub-blur');
  overlay?.classList.toggle('blur-mode', subBoxMode === 'blur');
  if (label) label.textContent = subBoxMode === 'blur' ? 'Vung lam mo' : 'Vung phu de';
  if (title) {
    title.innerHTML = subBoxMode === 'blur'
      ? '<i class="ri-blur-off-line"></i> Vung lam mo'
      : '<i class="ri-layout-bottom-line"></i> Vung phu de';
  }
  blurBtn?.classList.toggle('active', subBlurEnabled);
}

function subBoxSyncPosition(skipInputs = false) {
  const overlay = document.getElementById('sub-box-overlay');
  const canvas = document.getElementById('preview-canvas');
  if (!overlay || !canvas) return;
  const media = getPreviewMediaRect();
  overlay.style.left = (media.left + subBoxRegion.x * media.width) + 'px';
  overlay.style.top = (media.top + subBoxRegion.y * media.height) + 'px';
  overlay.style.width = (subBoxRegion.width * media.width) + 'px';
  overlay.style.height = (subBoxRegion.height * media.height) + 'px';

  if (!skipInputs) {
    const ix = document.getElementById('inp-sub-x');
    const iy = document.getElementById('inp-sub-y');
    const iw = document.getElementById('inp-sub-w');
    const ih = document.getElementById('inp-sub-h');
    if (ix) ix.value = Math.round(subBoxRegion.x * 100);
    if (iy) iy.value = Math.round(subBoxRegion.y * 100);
    if (iw) iw.value = Math.round(subBoxRegion.width * 100);
    if (ih) ih.value = Math.round(subBoxRegion.height * 100);
  }
}

function subBoxShow(mode = 'subtitle') {
  const overlay = document.getElementById('sub-box-overlay');
  const controls = document.getElementById('sub-region-controls');
  if (!overlay) return;
  subBoxMode = mode;
  updateSubBoxModeUi();
  overlay.style.display = 'block';
  if (controls) controls.style.display = 'flex';
  subBoxVisible = true;
  subBoxSyncPosition();
  document.getElementById('btn-preview-sub-box')?.classList.add('active');
}

function subBoxHide() {
  const overlay = document.getElementById('sub-box-overlay');
  const controls = document.getElementById('sub-region-controls');
  if (!overlay) return;
  overlay.style.display = 'none';
  if (controls) controls.style.display = 'none';
  subBoxVisible = false;
  subBoxOpenedByBlur = false;
  document.getElementById('btn-preview-sub-box')?.classList.remove('active');
}

function subBoxToggle() {
  if (subBoxVisible) subBoxHide();
  else subBoxShow('subtitle');
}

document.getElementById('btn-preview-sub-box')?.addEventListener('click', subBoxToggle);
document.getElementById('btn-preview-sub-blur')?.addEventListener('click', function () {
  subBlurEnabled = !subBlurEnabled;
  if (subBlurEnabled) {
    subBoxOpenedByBlur = !subBoxVisible;
    subBoxShow('blur');
  } else if (subBoxOpenedByBlur) {
    subBoxHide();
  } else {
    subBoxMode = 'subtitle';
    updateSubBoxModeUi();
  }
  showToast(subBlurEnabled ? 'Da bat khung lam mo. Keo khung vao phan sub goc can che.' : 'Da tat lam mo phu de goc', 'info', 2500);
});

// Inputs to Box sync
function handleSubInput() {
  const ix = parseFloat(document.getElementById('inp-sub-x')?.value || 0) / 100;
  const iy = parseFloat(document.getElementById('inp-sub-y')?.value || 0) / 100;
  const iw = parseFloat(document.getElementById('inp-sub-w')?.value || 5) / 100;
  const ih = parseFloat(document.getElementById('inp-sub-h')?.value || 5) / 100;

  subBoxRegion.x = Math.max(0, Math.min(1 - iw, ix));
  subBoxRegion.y = Math.max(0, Math.min(1 - ih, iy));
  subBoxRegion.width = Math.max(0.05, Math.min(1, iw));
  subBoxRegion.height = Math.max(0.05, Math.min(1, ih));

  const sel = document.getElementById('sel-sub-alignment');
  if (sel) sel.value = 'custom';

  subBoxSyncPosition(true); // skip updating the inputs we are typing into
}

['inp-sub-x', 'inp-sub-y', 'inp-sub-w', 'inp-sub-h'].forEach(id => {
  document.getElementById(id)?.addEventListener('input', handleSubInput);
});

// Alignment selector
document.getElementById('sel-sub-alignment')?.addEventListener('change', function (e) {
  const val = e.target.value;
  if (val && val !== 'custom') {
    const preset = ALIGNMENT_PRESETS[val];
    if (preset) {
      subBoxRegion = { ...preset };
      subBoxSyncPosition();
    }
  }
});

// Reset button
document.getElementById('btn-sub-region-reset')?.addEventListener('click', function () {
  subBoxRegion = { ...SUB_BOX_DEFAULTS };
  const sel = document.getElementById('sel-sub-alignment');
  if (sel) sel.value = 'bottom-center';
  subBoxSyncPosition();
});

// Drag to move
document.getElementById('sub-box-overlay')?.addEventListener('mousedown', function (e) {
  if (e.target.classList.contains('sub-box-handle')) return;
  const rect = this.getBoundingClientRect();
  const canvas = document.getElementById('preview-canvas');
  subBoxDrag = { type: 'move', startX: e.clientX, startY: e.clientY, startRect: { ...subBoxRegion } };
  this.classList.add('active');
  e.preventDefault();
});

// Resize via handles
document.querySelectorAll('.sub-box-handle').forEach(h => {
  h.addEventListener('mousedown', function (e) {
    const overlay = document.getElementById('sub-box-overlay');
    const canvas = document.getElementById('preview-canvas');
    const rect = overlay.getBoundingClientRect();
    subBoxDrag = {
      type: 'resize',
      corner: this.className.match(/sub-box-(\w+)/)?.[1] || 'se',
      startX: e.clientX,
      startY: e.clientY,
      startRect: { ...subBoxRegion },
    };
    overlay.classList.add('active');
    e.stopPropagation();
    e.preventDefault();
  });
});

document.addEventListener('mousemove', function (e) {
  if (!subBoxDrag) return;
  const canvas = document.getElementById('preview-canvas');
  if (!canvas) return;
  const media = getPreviewMediaRect();
  const dx = (e.clientX - subBoxDrag.startX) / Math.max(1, media.width);
  const dy = (e.clientY - subBoxDrag.startY) / Math.max(1, media.height);
  const s = subBoxDrag.startRect;

  if (subBoxDrag.type === 'move') {
    subBoxRegion.x = Math.max(0, Math.min(1 - subBoxRegion.width, s.x + dx));
    subBoxRegion.y = Math.max(0, Math.min(1 - subBoxRegion.height, s.y + dy));
  } else if (subBoxDrag.type === 'resize') {
    const c = subBoxDrag.corner;
    let nx = s.x, ny = s.y, nw = s.width, nh = s.height;
    if (c.includes('e')) { nw = Math.max(0.05, s.width + dx); }
    if (c.includes('w')) { nw = Math.max(0.05, s.width - dx); nx = s.x + (s.width - nw); }
    if (c.includes('s')) { nh = Math.max(0.05, s.height + dy); }
    if (c.includes('n')) { nh = Math.max(0.05, s.height - dy); ny = s.y + (s.height - nh); }
    subBoxRegion.x = Math.max(0, Math.min(1 - nw, nx));
    subBoxRegion.y = Math.max(0, Math.min(1 - nh, ny));
    subBoxRegion.width = Math.min(1 - subBoxRegion.x, nw);
    subBoxRegion.height = Math.min(1 - subBoxRegion.y, nh);
  }
  subBoxSyncPosition();
});

document.addEventListener('mouseup', function () {
  if (subBoxDrag) {
    document.getElementById('sub-box-overlay')?.classList.remove('active');
    subBoxDrag = null;
  }
});

// Also update position on window resize
window.addEventListener('resize', function () {
  if (subBoxVisible) subBoxSyncPosition();
});

// Expose region for preset saving
function getSubBoxRegion() {
  const sel = document.getElementById('sel-sub-alignment');
  const alignment = sel ? sel.value : 'bottom-center';
  if (subBoxVisible || subBlurEnabled) {
    return { ...subBoxRegion, alignment };
  }
  return null;
}
function setSubBoxRegion(region) {
  if (region && region.x !== undefined) {
    subBoxRegion = {
      x: region.x,
      y: region.y,
      width: region.width ?? region.w ?? 0.8,
      height: region.height ?? region.h ?? 0.15
    };
    const sel = document.getElementById('sel-sub-alignment');
    if (sel) sel.value = region.alignment || 'custom';
  } else {
    subBoxRegion = { ...SUB_BOX_DEFAULTS };
    const sel = document.getElementById('sel-sub-alignment');
    if (sel) sel.value = 'bottom-center';
  }
  if (subBoxVisible) subBoxSyncPosition();
}

/* ═══════════════ PROMPT LIBRARY ═══════════════ */
document.querySelectorAll('.prompt-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const prompt = btn.dataset.prompt;
    const textarea = document.getElementById('inp-ai-summary');
    if (textarea) {
      textarea.value = prompt;
      textarea.focus();
    }
  });
});

/* ═══════════════ PUBLISH HANDLERS ═══════════════ */
let publishPlatform = '';

document.getElementById('btn-publish-youtube')?.addEventListener('click', () => showPublishInputs('youtube'));
document.getElementById('btn-publish-tiktok')?.addEventListener('click', () => showPublishInputs('tiktok'));
document.getElementById('btn-publish-facebook')?.addEventListener('click', () => showPublishInputs('facebook'));

function showPublishInputs(platform) {
  publishPlatform = platform;
  const container = document.getElementById('publish-inputs');
  const titleInp = document.getElementById('inp-publish-title');
  const descInp = document.getElementById('inp-publish-desc');
  if (container) {
    container.style.display = 'flex';
    if (titleInp) titleInp.value = (document.getElementById('inp-video-path')?.value || document.getElementById('inp-srt-path')?.value)?.split(/[\\\\/]/).pop()?.replace(/\.[^.]+$/, '') || 'My Video';
    if (descInp) descInp.value = '';
    titleInp?.focus();
  }
}

document.getElementById('btn-publish-confirm')?.addEventListener('click', async () => {
  const title = document.getElementById('inp-publish-title')?.value || 'My Video';
  const desc = document.getElementById('inp-publish-desc')?.value || '';
  const videoPath = document.getElementById('inp-video-path')?.value || document.getElementById('inp-srt-path')?.value || '';
  if (!videoPath) { alert('No video selected'); return; }
  const result = await apiPost(`/publish/${publishPlatform}`, { project_id: currentProjectId || 1, video_path: videoPath, title, description: desc });
  if (result) {
    addTaskRow();
    document.getElementById('publish-inputs').style.display = 'none';
  }
});

/* ═══════════════ BATCH URL DOWNLOAD ═══════════════ */
document.getElementById('btn-batch-download')?.addEventListener('click', async () => {
  const urlsInput = document.getElementById('inp-batch-urls')?.value;
  if (!urlsInput) return;
  const urls = urlsInput.split('\n').map(u => u.trim()).filter(Boolean);
  if (urls.length === 0) return;
  const quality = document.getElementById('sel-download-quality')?.value || 'best';
  const proxy = document.getElementById('inp-download-proxy')?.value || '';
  const cookie = document.getElementById('inp-set-cookie')?.value || '';
  const result = await apiPost('/batch/download', { urls, quality, proxy, cookie_file: cookie, project_id: currentProjectId || 1 });
  if (result) {
    for (let i = 0; i < urls.length; i++) addTaskRow();
    document.getElementById('inp-batch-urls').value = '';
    setTimeout(loadDownloadHistory, 500);
  }
});

/* ═══════════════ TEMPLATE HANDLERS ═══════════════ */

function openTemplateModal() {
  const modal = document.getElementById('template-modal');
  if (modal) modal.classList.add('show');
  loadTemplateList();
}

async function loadTemplateList() {
  const container = document.getElementById('template-list');
  if (!container) return;
  const templates = await apiGet('/templates');
  clearChildren(container);
  if (!templates || templates.length === 0) {
    container.appendChild(el('div', { class: 'log-placeholder' }, ['Chưa có template nào.']));
    const cnt = document.getElementById('template-count');
    if (cnt) cnt.textContent = '0 mẫu sẵn';
    return;
  }
  for (const t of templates) {
    const safeName = escapeHtml(t.name || '');
    const row = el('div', {
      class: 'result-table-header',
      style: { padding: '2px 6px', fontSize: '9px', borderBottom: '1px solid var(--border)', cursor: 'default' },
    }, [
      el('span', { class: 'col-hdr', style: { flex: '2' } }, [t.name || 'Chưa đặt tên']),
      el('span', { class: 'col-hdr', style: { flex: '1' } }, [t.preset || '-']),
      el('span', { class: 'col-hdr', style: { width: '60px' } }, [t.resolution || '-']),
      el('span', { class: 'col-hdr', style: { width: '40px' } }, [String(t.fps || '-')]),
      el('span', { class: 'col-hdr', style: { width: '150px', display: 'flex', gap: '4px' } }, [
        el('button', {
          class: 'action-btn tmpl-load',
          dataset: { name: safeName },
          style: { height: '18px', padding: '0 6px', fontSize: '8px' },
          onclick: () => applyTemplateByName(t.name),
        }, ['Tải']),
        el('button', {
          class: 'action-btn tmpl-render',
          dataset: { name: safeName },
          style: { height: '18px', padding: '0 6px', fontSize: '8px', background: '#2563eb', color: '#fff', border: 'none' },
          onclick: () => renderTemplateByName(t.name),
        }, ['Render']),
        el('button', {
          class: 'action-btn tmpl-delete',
          dataset: { name: safeName },
          style: { height: '18px', padding: '0 6px', fontSize: '8px', background: '#ef4444', color: '#fff', border: 'none' },
          onclick: async () => {
            await apiDel('/templates/' + encodeURIComponent(t.name));
            loadTemplateList();
          },
        }, ['Xóa']),
      ]),
    ]);
    container.appendChild(row);
  }
}

async function applyTemplateByName(name) {
  const tmpl = await apiGet('/templates/' + encodeURIComponent(name));
  if (tmpl && tmpl.export) {
    if (tmpl.export.resolution) {
      const parts = tmpl.export.resolution.split('x');
      if (parts.length === 2) {
        const w = document.getElementById('inp-width'); if (w) w.value = parts[0];
        const h = document.getElementById('inp-height'); if (h) h.value = parts[1];
      }
    }
    if (tmpl.export.fps) { const el = document.getElementById('sel-export-fps'); if (el) el.value = tmpl.export.fps; }
    if (tmpl.export.codec) { const el = document.getElementById('sel-export-codec'); if (el) el.value = tmpl.export.codec.toUpperCase(); }
  }
  if (currentProjectId && name) {
    await apiPost(`/templates/${encodeURIComponent(name)}/apply?project_id=${currentProjectId}`);
  }
  document.getElementById('template-modal')?.classList.remove('show');
}

async function renderTemplateByName(name) {
  const inputPath = getInputMediaPath();
  if (!inputPath) {
    showToast('Chua chon video dau vao', 'warn');
    return;
  }
  const res = await apiPost(`/templates/${encodeURIComponent(name)}/render`, {
    project_id: currentProjectId || 0,
    input_path: inputPath,
    queue: true,
    overrides: { auto_reframe: true },
  });
  if (res?.id) {
    addTaskRow();
    showToast(`Da dua render template ${name} vao hang doi`, 'success');
  }
}

document.getElementById('btn-save-template')?.addEventListener('click', async () => {
  const name = document.getElementById('inp-template-name')?.value || prompt('Tên template:', 'Mẫu của tôi');
  if (!name) return;
  const config = {
    name,
    project_preset: document.getElementById('sel-project-preset')?.value || 'Movie Review',
    voice: { provider: document.getElementById('sel-tts-provider')?.value?.toLowerCase() || 'edge' },
    subtitle: { font: 'Arial', size: 42, color: '#FFFFFF', burn: true },
    export: {
      resolution: (document.getElementById('inp-width')?.value || '1920') + 'x' + (document.getElementById('inp-height')?.value || '1080'),
      fps: parseInt(document.getElementById('sel-export-fps')?.value || '30'),
      codec: document.getElementById('sel-export-codec')?.value || 'h264',
    },
  };
  await apiPost('/templates?name=' + encodeURIComponent(name), config);
  loadTemplateList();
  if (document.getElementById('inp-template-name')) document.getElementById('inp-template-name').value = '';
});

document.getElementById('btn-load-template')?.addEventListener('click', openTemplateModal);

document.getElementById('btn-template-save')?.addEventListener('click', () => {
  document.getElementById('btn-save-template')?.click();
});

document.getElementById('btn-template-refresh')?.addEventListener('click', loadTemplateList);

// Wire template modal close to also close when clicking background
document.getElementById('template-modal')?.addEventListener('click', function (e) {
  if (e.target === this) this.classList.remove('show');
});

/* ═══════════════ PUBLISH HISTORY ═══════════════ */
document.getElementById('btn-publish-history')?.addEventListener('click', async () => {
  const modal = document.getElementById('publish-history-modal');
  if (modal) modal.classList.add('show');
  await loadPublishHistory();
});

document.getElementById('btn-pub-history-refresh')?.addEventListener('click', loadPublishHistory);

document.getElementById('publish-history-modal')?.addEventListener('click', function (e) {
  if (e.target === this) this.classList.remove('show');
});

async function loadPublishHistory() {
  const container = document.getElementById('publish-history-list');
  const countEl = document.getElementById('pub-history-count');
  if (!container) return;
  const items = await apiGet('/publish/history');
  if (!items || items.length === 0) {
    container.innerHTML = '<div class="log-placeholder">Chưa có lịch sử publish.</div>';
    if (countEl) countEl.textContent = '0 items';
    return;
  }
  container.innerHTML = items.map(item => `
    <div class="result-table-header" style="padding:2px 6px;font-size:9px;border-bottom:1px solid var(--border)">
      <span class="col-hdr" style="flex:2">${item.title || item.input_path?.split(/[\\/]/).pop() || 'Unknown'}</span>
      <span class="col-hdr" style="flex:1">${item.format || '-'}</span>
      <span class="col-hdr" style="width:70px"><span class="queue-status-${item.status || 'exported'}">${item.status || 'exported'}</span></span>
      <span class="col-hdr" style="width:120px">${item.created_at ? item.created_at.slice(0, 19) : '-'}</span>
    </div>
  `).join('');
  if (countEl) countEl.textContent = items.length + ' items';
}

/* ═══════════════ DOWNLOAD HISTORY ═══════════════ */
async function loadDownloadHistory() {
  const container = document.getElementById('download-history-list');
  if (!container) return;
  const items = await apiGet('/download');
  if (!items || items.length === 0) {
    container.innerHTML = '<div class="log-placeholder">Chưa có download nào.</div>';
    return;
  }
  container.innerHTML = items.slice().reverse().map(item => `
    <div class="result-table-header" style="padding:2px 6px;font-size:9px;border-bottom:1px solid var(--border)">
      <span class="col-hdr" style="flex:2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${item.url || item.id}</span>
      <span class="col-hdr" style="flex:2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${item.output_path || item.error || ''}">${item.output_path ? item.output_path.split(/[\\/]/).pop() : (item.error || '-')}</span>
      <span class="col-hdr" style="width:60px">${item.platform || '-'}</span>
      <span class="col-hdr" style="width:60px"><span class="queue-status-${item.status || 'unknown'}">${item.status || '?'}</span></span>
      <span class="col-hdr" style="width:50px">
        ${item.status === 'running' || item.status === 'waiting' ? `<button class="action-btn dl-cancel" data-id="${item.id}" style="height:16px;padding:0 4px;font-size:7px;background:#ef4444;color:#fff;border:none">Hủy</button>` : ''}
      </span>
    </div>
  `).join('');
  container.querySelectorAll('.dl-cancel').forEach(btn => {
    btn.addEventListener('click', async () => {
      await apiPost(`/download/${btn.dataset.id}/cancel`);
      loadDownloadHistory();
    });
  });
}

document.getElementById('btn-dl-history-refresh')?.addEventListener('click', loadDownloadHistory);

// Load download history when the download modal opens
document.querySelector('[data-tab="download"]')?.addEventListener('click', function () {
  setTimeout(loadDownloadHistory, 300);
});

/* ═══════════════ MUSIC CROSSFADE / PLAYLIST ═══════════════ */
document.getElementById('btn-music-crossfade')?.addEventListener('click', async () => {
  const audioA = requireSelectedMusic('Nhap duong dan track A:');
  if (!audioA) {
    showToast('Chua chon track A', 'warn');
    return;
  }
  let audioB = selectedMusicPathB;
  if (!audioB || audioB === audioA) {
    audioB = prompt('Nhap duong dan track B:', '') || '';
    selectedMusicPathB = audioB;
  }
  if (!audioB) {
    showToast('Chua chon track B', 'warn');
    return;
  }
  const dur = prompt('Thoi gian chong mo (giay):', '2');
  if (dur === null) return;
  const qs = new URLSearchParams({
    audio_a: audioA,
    audio_b: audioB,
    duration: String(parseFloat(dur) || 2),
    project_id: String(currentProjectId || 0),
  });
  const res = await apiPost('/music/crossfade?' + qs.toString());
  if (res?.id) {
    addTaskRow();
    showToast('Crossfade queued', 'success');
  }
});

document.getElementById('btn-music-playlist')?.addEventListener('click', async () => {
  const playlists = await apiGet('/music/playlist');
  const lines = (playlists || []).map(p => `- ${p.name} (${p.count || 0} tracks)`).join('\n') || '(chua co playlist)';
  if (!playlists || playlists.length === 0) {
    alert(`Danh sach phat:\n${lines}`);
    return;
  }
  const name = prompt(`Chon playlist theo ten:\n${lines}`, playlists[0].name);
  if (!name) return;
  const playlist = playlists.find(p => (p.name || '').toLowerCase() === name.toLowerCase()) || playlists[0];
  const tracks = (playlist.tracks || [])
    .map(track => typeof track === 'string' ? { path: track, name: track.split(/[\\/]/).pop() } : track)
    .filter(track => track?.path);
  if (!tracks.length) {
    showToast('Playlist khong co track hop le', 'warn');
    return;
  }
  selectedMusicFiles = tracks;
  setSelectedMusicTrack(tracks[0].path, tracks[1]?.path || '');
  alert(`Da chon playlist: ${playlist.name}\nTrack hien tai: ${tracks[0].name || tracks[0].path}`);
});

/* ═══════════════ KEEP BGM TOGGLE ═══════════════ */
document.getElementById('chk-keep-bgm')?.addEventListener('change', function () {
  const vol = document.getElementById('inp-bgm-vol');
  if (vol) {
    vol.disabled = !this.checked;
    vol.style.opacity = this.checked ? '1' : '0.4';
  }
});
// Init state
document.getElementById('inp-bgm-vol') && (() => {
  const chk = document.getElementById('chk-keep-bgm');
  if (chk && !chk.checked) {
    document.getElementById('inp-bgm-vol').disabled = true;
    document.getElementById('inp-bgm-vol').style.opacity = '0.4';
  }
})();

/* ═══════════════ AUTO VOICE TOGGLE ═══════════════ */
function syncVoiceControls() {
  const autoEnabled = document.getElementById('chk-auto-voice')?.checked ?? true;
  const voiceModeEnabled = getVoiceModeOptions().tts_enabled;
  const ttsControlsEnabled = autoEnabled && voiceModeEnabled;
  const ttsInputs = ['sel-tts-provider', 'sel-voice-lang', 'sel-voice-type', 'btn-play-voice', 'chk-tts-align'];
  ttsInputs.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.disabled = !ttsControlsEnabled;
      el.style.opacity = ttsControlsEnabled ? '1' : '0.4';
    }
  });
  const mode = document.getElementById('sel-voice-mode');
  if (mode) {
    mode.disabled = !autoEnabled;
    mode.style.opacity = autoEnabled ? '1' : '0.4';
  }
}

document.getElementById('chk-auto-voice')?.addEventListener('change', function () {
  const inputs = ['chk-keep-bgm', 'inp-bgm-vol'];
  inputs.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.disabled = !this.checked;
      el.style.opacity = this.checked ? '1' : '0.4';
    }
  });
  syncVoiceControls();
});
document.getElementById('sel-voice-mode')?.addEventListener('change', syncVoiceControls);
syncVoiceControls();

/* ═══════════════ SPEED RAMP / MOTION EFFECTS HANDLERS ═══════════════ */
document.querySelectorAll('#tab-enhance .custom-checkbox input').forEach(chk => {
  chk.addEventListener('change', function () {
    const label = this.closest('.feature-item')?.querySelector('.field-label')?.textContent?.toLowerCase().trim();
    if (label === 'slow motion' || label === 'fast motion' || label === 'speed ramp' || label === 'particle effects') {
      // These are handled by the main enhance apply button
    }
  });
});

/* ═══════════════ TIMELINE INTERACTIVE ═══════════════ */
function makeTimelineInteractive() {
  const lanes = Array.from(document.querySelectorAll('.track-lane'));
  const clips = Array.from(document.querySelectorAll('.track-clip[data-clip-id]'));
  const minFrames = 3;

  function framesFromLaneX(lane, clientX) {
    const rect = lane.getBoundingClientRect();
    const totalFrames = Number(lane.dataset.totalFrames || 900);
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(rect.width, 1)));
    return Math.round(ratio * totalFrames);
  }

  function laneUnderPoint(clientX, clientY) {
    return lanes.find(lane => {
      const rect = lane.getBoundingClientRect();
      return clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
    });
  }

  function paintClip(clip, positionFrame, startFrame, endFrame, totalFrames) {
    const duration = Math.max(endFrame - startFrame, minFrames);
    clip.style.left = `${positionFrame / totalFrames * 100}%`;
    clip.style.width = `${Math.max(duration / totalFrames * 100, 2)}%`;
    clip.dataset.positionFrame = String(positionFrame);
    clip.dataset.startFrame = String(startFrame);
    clip.dataset.endFrame = String(endFrame);
  }

  async function persistClip(clip, lane, positionFrame, startFrame, endFrame) {
    const clipId = Number(clip.dataset.clipId);
    const trackId = Number(lane?.dataset.trackId || clip.dataset.trackId || 0);
    if (!clipId) return;
    const query = new URLSearchParams({
      start_frame: String(startFrame),
      end_frame: String(endFrame),
      position_frame: String(positionFrame),
    });
    await apiPut(`/timeline/clips/${clipId}?${query.toString()}`, {});
    if (trackId && trackId !== Number(clip.dataset.trackId || 0)) {
      await apiPut(`/timeline/clips/${clipId}/move?track_id=${trackId}&position=${positionFrame}`, {});
    }
    showToast('Timeline clip updated', 'success', 1400);
    if (currentProjectId) loadTimeline(currentProjectId).catch(() => {});
  }

  clips.forEach(clip => {
    if (clip.dataset.boundTimeline === '1') return;
    clip.dataset.boundTimeline = '1';
    clip.addEventListener('pointerdown', (e) => {
      const handle = e.target.closest('.clip-resize');
      const lane = clip.closest('.track-lane');
      if (!lane) return;
      e.preventDefault();
      e.stopPropagation();

      const startX = e.clientX;
      const totalFrames = Number(lane.dataset.totalFrames || clip.dataset.totalFrames || 900);
      const original = {
        position: Number(clip.dataset.positionFrame || 0),
        start: Number(clip.dataset.startFrame || 0),
        end: Number(clip.dataset.endFrame || 0),
        lane,
      };
      const duration = Math.max(original.end - original.start, minFrames);
      const mode = handle?.dataset.side || 'move';
      let activeLane = lane;
      clip.classList.add('timeline-editing');
      clip.setPointerCapture?.(e.pointerId);

      const onMove = (moveEvent) => {
        activeLane = mode === 'move' ? (laneUnderPoint(moveEvent.clientX, moveEvent.clientY) || activeLane) : lane;
        const deltaFrames = Math.round((moveEvent.clientX - startX) / Math.max(lane.getBoundingClientRect().width, 1) * totalFrames);
        let nextPosition = original.position;
        let nextStart = original.start;
        let nextEnd = original.end;

        if (mode === 'left') {
          const maxStart = original.end - minFrames;
          nextStart = Math.max(0, Math.min(maxStart, original.start + deltaFrames));
          nextPosition = Math.max(0, original.position + (nextStart - original.start));
        } else if (mode === 'right') {
          nextEnd = Math.max(original.start + minFrames, original.end + deltaFrames);
        } else {
          nextPosition = Math.max(0, Math.min(totalFrames - duration, framesFromLaneX(activeLane, moveEvent.clientX) - Math.round(duration / 2)));
        }
        paintClip(clip, nextPosition, nextStart, nextEnd, totalFrames);
        lanes.forEach(l => l.classList.toggle('timeline-drop-target', l === activeLane && mode === 'move'));
      };

      const onUp = async () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        lanes.forEach(l => l.classList.remove('timeline-drop-target'));
        clip.classList.remove('timeline-editing');
        try {
          await persistClip(
            clip,
            activeLane,
            Number(clip.dataset.positionFrame || 0),
            Number(clip.dataset.startFrame || 0),
            Number(clip.dataset.endFrame || 0),
          );
        } catch (err) {
          showToast(err.message || 'Timeline update failed', 'error');
          if (currentProjectId) loadTimeline(currentProjectId).catch(() => {});
        }
      };

      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp, { once: true });
    });
  });
}

/* ═══════════════ VERSION HISTORY ═══════════════ */
document.getElementById('btn-version-history')?.addEventListener('click', async () => {
  if (!currentProjectId) { alert('Vui lòng tải một dự án trước.'); return; }
  const versions = await apiGet(`/projects/${currentProjectId}/versions`);
  const modal = document.getElementById('version-modal');
  const list = document.getElementById('version-list');
  if (!modal || !list) return;
  list.innerHTML = '';
  if (versions && versions.length > 0) {
    versions.forEach(v => {
      const row = el('div', { class: 'result-row' });
      row.appendChild(el('span', { style: { flex: '1' } }, [`v${escapeHtml(String(v.version))}`]));
      row.appendChild(el('span', { style: { flex: '1' } }, [new Date(v.saved_at * 1000).toLocaleString()]));
      row.appendChild(el('span', {}, [`${(v.size / 1024).toFixed(1)}KB`]));
      row.appendChild(el('button', {
        class: 'action-btn',
        style: { height: '18px', padding: '1px 6px', fontSize: '9px' },
        dataset: { version: v.file || '' },
        onclick: async () => {
          await apiPost(`/projects/${currentProjectId}/restore?version_file=${encodeURIComponent(v.file)}`);
          alert(`Đã khôi phục về phiên bản v${v.version}`);
          modal.classList.remove('show');
        },
      }, ['Khôi phục']));
      list.appendChild(row);
    });
  } else {
    list.innerHTML = '<div style="color:var(--text-dim);padding:8px;text-align:center">Chưa có phiên bản nào được lưu. Hãy lưu dự án để tạo phiên bản.</div>';
  }
  modal.classList.add('show');
});

/* ═══════════════ LINK HANDLERS ═══════════════ */
document.getElementById('link-search-sample')?.addEventListener('click', (e) => {
  e.preventDefault();
  const preset = document.getElementById('sel-project-preset')?.value || 'Movie Review';
  apiGet('/presets/' + encodeURIComponent(preset)).then(data => {
    if (data) {
      alert(`Preset: ${data.name || preset}\nResolution: ${data.export?.resolution || 'N/A'}\nFPS: ${data.export?.fps || 'N/A'}\nCodec: ${data.export?.codec || 'N/A'}`);
    }
  });
});

document.getElementById('link-save-preset')?.addEventListener('click', (e) => {
  e.preventDefault();
  document.getElementById('btn-save-preset')?.click();
});

document.getElementById('link-setup-voice')?.addEventListener('click', (e) => {
  e.preventDefault();
  document.querySelector('#processing-tabs .tab[data-target="tab-voice"]')?.click();
});

/* ═══════════════ INFO BUTTONS ═══════════════ */
document.querySelectorAll('.info-btn').forEach(btn => {
  if (btn.id === 'btn-info-srt') {
    btn.addEventListener('click', () => {
      alert('Select an SRT subtitle file and its corresponding video file.\nBoth files should be in the same directory.\nSupported: .srt, .ass');
    });
  } else if (btn.id === 'btn-help-execute') {
    btn.addEventListener('click', () => {
      alert('Render sẽ chạy toàn bộ pipeline:\n1. Tải video (nếu có URL)\n2. Chuyển giọng nói (STT)\n3. Dịch subtitle\n4. Tạo giọng đọc (TTS)\n5. Render video cuối cùng');
    });
  } else if (!btn.id) {
    btn.addEventListener('click', () => {
      const parentLabel = btn.closest('.tab-row')?.querySelector('.field-label')?.textContent || '';
      alert(`Thêm thông tin về: ${parentLabel || 'tính năng này'}`);
    });
  }
});

/* Voice preview WAV player. */
(function attachVoicePreviewPlayer() {
  const btn = document.getElementById('btn-play-voice');
  if (!btn || btn.dataset.previewPlayerAttached === '1') return;
  btn.dataset.previewPlayerAttached = '1';

  const providerId = () => {
    const value = document.getElementById('sel-tts-provider')?.value || 'Edge TTS';
    if (value === 'FPT.AI TTS') return 'fpt';
    return value.toLowerCase().replace(' tts', '').replace(' (free)', '') || 'edge';
  };

  const waitForOutput = async (itemId, knownOutputPath, timeoutMs = 180000) => {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const jobs = await apiGet('/queue');
      const job = (jobs || []).find(j => Number(j.id) === Number(itemId));
      if (job?.status === 'completed') return job.output_path || knownOutputPath;
      if (job?.status === 'failed') throw new Error(job.error || 'Tao WAV nghe thu that bai');
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw new Error('Tao WAV nghe thu qua lau');
  };

  btn.addEventListener('click', async (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();

    const audioEl = document.getElementById('voice-preview-audio');
    const text = 'Xin chao, day la giong doc thu nghiem de kiem tra co hop voi video hay khong.';
    const voice = document.getElementById('sel-voice-type')?.value || 'vi-VN-HoaiMyNeural';
    const fptKey = document.getElementById('inp-fpt-key')?.value || '';
    const oldHtml = btn.innerHTML;

    btn.disabled = true;
    btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i>';
    try {
      const result = await apiPost('/voice/play', {
        text,
        provider: providerId(),
        voice,
        fpt_api_key: fptKey || undefined,
        project_id: currentProjectId || 0,
      });
      const outputPath = result?.ready ? result.output : await waitForOutput(result.id, result.output);
      if (!outputPath) throw new Error('Backend khong tao duoc file nghe thu');
      const src = `/api/video/serve?path=${encodeURIComponent(outputPath)}&t=${Date.now()}`;
      if (audioEl) {
        audioEl.src = src;
        audioEl.style.display = 'block';
        audioEl.load();
        await audioEl.play().catch(() => {});
      } else {
        await new Audio(src).play().catch(() => {});
      }
    } catch (e) {
      alert('Nghe thu giong noi that bai: ' + (e.message || e));
    } finally {
      btn.disabled = false;
      btn.innerHTML = oldHtml || '<i class="ri-volume-up-line"></i>';
    }
  }, true);
})();

/* ═══════════════ TRANSLATE SETTINGS ═══════════════ */
document.getElementById('btn-translate-settings')?.addEventListener('click', () => {
  alert('Translation engines (best to normal):\n1. GPT - API, best natural rewrite\n2. Gemini - API, strong natural translation\n3. AI Provider - OpenRouter/NVIDIA NIM/Ollama/Custom OpenAI API\n4. DeepLX - free/API endpoint, DeepL-style quality\n5. Google Translate - free unofficial endpoint\n6. SeamlessM4T - free local, heavy multilingual model\n7. NLLB-200 - free local, reliable multilingual model\n8. M2M100 - free local multilingual model\n9. MarianMT - free local, light but narrower quality\nAI Provider supports provider + base URL + model + fallback.');
});

/* ═══════════════ ENHANCE BRANDING BUTTONS ═══════════════ */
function getBrandingUiOptions() {
  const posIndex = document.getElementById('sel-enhance-brand-pos')?.selectedIndex ?? 1;
  const position = ['top_right', 'bottom_right', 'center'][posIndex] || 'bottom_right';
  const opacityRaw = Number(document.getElementById('inp-enhance-opacity')?.value || 70);
  const opacity = Math.max(0, Math.min(1, opacityRaw / 100));
  return { position, opacity };
}

document.getElementById('btn-branding-logo')?.addEventListener('click', async () => {
  const videoPath = document.getElementById('inp-video-path')?.value || document.getElementById('inp-srt-path')?.value || '';
  if (!videoPath) { alert('Chưa chọn video'); return; }
  const logoPath = prompt('Đường dẫn hình ảnh logo:', '');
  if (!logoPath) return;
  await apiPost('/enhance/branding/logo', { video_path: videoPath, logo_path: logoPath, ...getBrandingUiOptions(), project_id: currentProjectId || 1 });
  addTaskRow();
});

document.getElementById('btn-branding-text')?.addEventListener('click', async () => {
  const videoPath = document.getElementById('inp-video-path')?.value || document.getElementById('inp-srt-path')?.value || '';
  if (!videoPath) { alert('Chưa chọn video'); return; }
  const text = prompt('Văn bản chèn:', '0xForge');
  if (!text) return;
  const watermarkInput = document.getElementById('inp-enhance-watermark-text');
  if (watermarkInput) watermarkInput.value = text;
  const watermarkCheck = document.getElementById('chk-enhance-watermark');
  if (watermarkCheck) watermarkCheck.checked = true;
  const opts = getBrandingUiOptions();
  const textPosition = opts.position === 'top_right' ? 'top' : opts.position === 'center' ? 'center' : 'bottom';
  await apiPost('/enhance/branding/text', { video_path: videoPath, text, position: textPosition, font_size: 48, project_id: currentProjectId || 1 });
  addTaskRow();
});

document.getElementById('btn-branding-qr')?.addEventListener('click', async () => {
  const videoPath = document.getElementById('inp-video-path')?.value || document.getElementById('inp-srt-path')?.value || '';
  if (!videoPath) { alert('Chưa chọn video'); return; }
  const content = prompt('Nội dung QR (URL):', 'https://example.com');
  if (!content) return;
  await apiPost('/enhance/branding/qr', { video_path: videoPath, content, position: getBrandingUiOptions().position, size: 120, project_id: currentProjectId || 1 });
  addTaskRow();
});

/* ═══════════════ EXTRACT SUBTITLE FROM VIDEO ═══════════════ */
document.getElementById('btn-extract-srt')?.addEventListener('click', async () => {
  const videoPath = document.getElementById('inp-video-path')?.value;
  if (!videoPath) {
    alert('Vui lòng chọn file video ở mục Path Video trước!');
    return;
  }

  // Show loading indicator
  const modal = document.getElementById('extract-sub-modal');
  modal.classList.add('show');

  const streamsSection = document.getElementById('sub-streams-section');
  const streamsList = document.getElementById('sub-streams-list');
  streamsSection.style.display = 'none';
  streamsList.innerHTML = '<div style="color:#718096; text-align:center; padding:10px;">Đang quét phụ đề trong video...</div>';

  try {
    const res = await apiPost('/subtitle/detect-streams', { path: videoPath });
    if (res && res.streams && res.streams.length > 0) {
      streamsList.innerHTML = '';
      res.streams.forEach((stream, idx) => {
        const item = document.createElement('label');
        item.style.display = 'flex';
        item.style.alignItems = 'center';
        item.style.gap = '8px';
        item.style.cursor = 'pointer';
        item.style.padding = '4px 0';

        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'srt-stream-choice';
        radio.value = stream.index;
        if (idx === 0) radio.checked = true;

        const labelText = document.createElement('span');
        labelText.textContent = `Track ${stream.index}: ${stream.title}`;

        item.appendChild(radio);
        item.appendChild(labelText);
        streamsList.appendChild(item);
      });
      streamsSection.style.display = 'block';
    } else {
      streamsList.innerHTML = '<div style="color:#e53e3e; text-align:center; padding:10px;">Không tìm thấy phụ đề mềm nào tích hợp sẵn. Hãy sử dụng Whisper STT ở dưới.</div>';
    }
  } catch (err) {
    streamsList.innerHTML = '<div style="color:#e53e3e; text-align:center; padding:10px;">Lỗi khi quét phụ đề.</div>';
  }
});

document.getElementById('btn-extract-selected-stream')?.addEventListener('click', async () => {
  const videoPath = document.getElementById('inp-video-path')?.value;
  const selectedRadio = document.querySelector('input[name="srt-stream-choice"]:checked');
  if (!selectedRadio) {
    alert('Vui lòng chọn một track phụ đề để trích xuất!');
    return;
  }

  const index = parseInt(selectedRadio.value);
  const extractBtn = document.getElementById('btn-extract-selected-stream');
  const originalText = extractBtn.textContent;
  extractBtn.textContent = 'Đang trích xuất...';
  extractBtn.disabled = true;

  try {
    const projectId = await ensureCurrentProject();
    if (!projectId) {
      alert('Khong the tao du an de trich xuat phu de.');
      return;
    }
    const res = await apiPost('/subtitle/extract-stream', {
      path: videoPath,
      index: index,
      project_id: projectId
    });
    if (res && res.path) {
      document.getElementById('inp-srt-path').value = res.path;
      document.getElementById('extract-sub-modal').classList.remove('show');
      alert('Đã trích xuất phụ đề thành công! Click OK để tự động nạp phụ đề.');
      // Auto trigger load
      document.getElementById('btn-load')?.click();
    } else {
      alert('Không thể trích xuất phụ đề.');
    }
  } catch (err) {
    alert('Có lỗi xảy ra: ' + (err.message || err));
  } finally {
    extractBtn.textContent = originalText;
    extractBtn.disabled = false;
  }
});

document.getElementById('btn-run-whisper-stt')?.addEventListener('click', async () => {
  const videoPath = document.getElementById('inp-video-path')?.value;
  if (!videoPath) {
    alert('Vui lòng chọn video trước!');
    return;
  }

  const language = document.getElementById('sel-stt-lang')?.value || 'vi';
  const vocalSep = document.getElementById('chk-vocal-sep')?.checked ?? false;
  const useWhisperX = document.getElementById('chk-modal-whisperx')?.checked ?? false;
  const runBtn = document.getElementById('btn-run-whisper-stt');
  const originalText = runBtn.textContent;
  runBtn.textContent = 'Đang kích hoạt Whisper...';
  runBtn.disabled = true;

  try {
    const projectId = await ensureCurrentProject();
    if (!projectId) {
      alert('Khong the tao du an de chay Whisper STT.');
      return;
    }
    const res = await apiPost('/subtitle/transcribe-video', {
      path: videoPath,
      language: language,
      project_id: projectId,
      vocal_separation: vocalSep,
      whisperx: useWhisperX,
    });
    if (res) {
      document.getElementById('extract-sub-modal').classList.remove('show');
      alert('Đã khởi chạy tiến trình Whisper STT chạy ngầm thành công!\nBạn có thể mở Tab Phụ Đề / nhấn Xem Log để theo dõi tiến trình.');

      // Periodically check subtitles list to auto-load when done
      const checkInterval = setInterval(async () => {
        try {
          const subs = await apiGet(`/subtitle/${projectId}`);
          const whisperSub = subs.find(s => s.source === `whisper_${language}` || s.source === `whisper_${language}_aligned`);
          if (whisperSub) {
            clearInterval(checkInterval);
            alert('Nhận dạng giọng nói (Whisper STT) đã hoàn thành! Đang nạp lại phụ đề...');
            // Put it into the input path and click load
            document.getElementById('inp-srt-path').value = `data/subtitles/project_${projectId}_stt.srt`;
            document.getElementById('btn-load')?.click();
          }
        } catch (e) {
          console.error(e);
        }
      }, 5000);
    }
  } catch (err) {
    alert('Lỗi kích hoạt Whisper: ' + (err.message || err));
  } finally {
    runBtn.textContent = originalText;
    runBtn.disabled = false;
  }
});

/* ═══════════════ EDIT TAB - CROP/RESIZE CHECKBOXES ═══════════════ */
document.getElementById('chk-edit-crop')?.addEventListener('change', function () {
  if (this.checked) {
    const x = prompt('Tọa độ X cắt:', '0'); if (x === null) { this.checked = false; return; }
    const y = prompt('Tọa độ Y cắt:', '0'); if (y === null) { this.checked = false; return; }
    const w = prompt('Chiều rộng cắt:', '1920'); if (w === null) { this.checked = false; return; }
    const h = prompt('Chiều cao cắt:', '1080'); if (h === null) { this.checked = false; return; }
    apiPost('/edit/crop', {
      video_path: document.getElementById('inp-video-path')?.value || document.getElementById('inp-srt-path')?.value || '',
      operations: [{ type: 'crop', x: parseInt(x), y: parseInt(y), w: parseInt(w), h: parseInt(h) }],
    }).then(() => addTaskRow());
  }
});

document.getElementById('chk-edit-scene-detect')?.addEventListener('change', function () {
  if (this.checked) {
    const videoPath = document.getElementById('inp-video-path')?.value || document.getElementById('inp-srt-path')?.value || '';
    if (videoPath) {
      apiPost('/edit/scene-detect', { project_id: currentProjectId || 1, video_path: videoPath, threshold: 27 });
      setTimeout(updateSceneList, 3000);
    }
  }
});

document.getElementById('chk-edit-resize')?.addEventListener('change', function () {
  if (this.checked) {
    const w = prompt('Chiều rộng:', '1280'); if (w === null) { this.checked = false; return; }
    const h = prompt('Chiều cao:', '720'); if (h === null) { this.checked = false; return; }
    apiPost('/edit/resize', {
      video_path: document.getElementById('inp-video-path')?.value || document.getElementById('inp-srt-path')?.value || '',
      operations: [{ type: 'resize', width: parseInt(w), height: parseInt(h) }],
    }).then(() => addTaskRow());
  }
});

/* ═══════════════ SCENE LIST DYNAMIC ═══════════════ */
async function updateSceneList() {
  if (!currentProjectId) return;
  const scenes = await apiGet(`/edit/scenes/${currentProjectId}`);
  const container = document.querySelector('.scene-list');
  if (!container) return;
  if (scenes && scenes.length > 0) {
    container.innerHTML = scenes.map(s => `
      <div class="scene-row" draggable="true">
        <span>Scene ${s.scene_index}</span>
        <strong>${formatTime(Math.floor(s.start_time))}-${formatTime(Math.floor(s.end_time))}</strong>
      </div>
    `).join('');
  } else {
    container.innerHTML = `
      <div class="scene-row" draggable="true"><span>Scene 1</span><strong>00:00-00:23</strong></div>
      <div class="scene-row" draggable="true"><span>Scene 2</span><strong>00:24-00:45</strong></div>
      <div class="scene-row" draggable="true"><span>Scene 3</span><strong>00:46-01:12</strong></div>
    `;
  }
}

// Override scene detect button to also refresh list
document.getElementById('btn-detect-scenes')?.addEventListener('click', async () => {
  setTimeout(updateSceneList, 2000);
});

/* ═══════════════ ASSET TEMPLATES BUTTON ═══════════════ */
document.querySelector('.asset-item:last-child')?.addEventListener('click', () => {
  openTemplateModal();
});

/* ═══════════════ CLOSE MODALS ON OVERLAY CLICK ═══════════════ */
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.classList.remove('show');
  });
});

/* ─── Wire timeline after load ─── */
const origLoadTimeline = loadTimeline;
loadTimeline = async function (projectId) {
  await origLoadTimeline(projectId);
  setTimeout(makeTimelineInteractive, 100);
};

/* ═══════════════ TIMELINE EDITOR BUTTON ═══════════════ */
document.getElementById('btn-edit-timeline')?.addEventListener('click', () => {
  if (currentProjectId) {
    loadTimeline(currentProjectId);
    alert('Dòng thời gian đã tải từ dữ liệu dự án.');
  } else {
    alert('Vui lòng tải một dự án trước.');
  }
});

/* ═══════════════ GPU AUTO DETECT ═══════════════ */
async function detectGPU() {
  const info = await apiGet('/system/gpu');
  if (info) {
    const autoRadio = document.getElementById('radio-auto');
    const nvidiaRadio = document.getElementById('radio-nvidia');
    const amdRadio = document.getElementById('radio-amd');
    if (info.primary === 'nvidia' && nvidiaRadio) nvidiaRadio.checked = true;
    else if (info.primary === 'amd' && amdRadio) amdRadio.checked = true;
    else if (autoRadio) autoRadio.checked = true;
    const gpuInfo = document.querySelector('.warning-text');
    if (gpuInfo && info.details?.length) {
      const names = info.details.map(d => d.name || d.type).join(', ');
      gpuInfo.textContent = `Detected GPU: ${names} | Driver: ${info.details[0]?.driver || 'N/A'}`;
      gpuInfo.style.color = '#22c55e';
    }
  }
}
setTimeout(detectGPU, 500);

/* ═══════════════ INIT QUEUE TABLE ═══════════════
   Render rows vào bảng kết quả từ queue jobs.
   Ưu tiên: API /queue → fallback: .queue-job trong HTML
════════════════════════════════════════════════ */
const STATUS_COLOR = {
  running: '#a78bfa',
  completed: '#22c55e',
  done: '#22c55e',
  failed: '#ef4444',
  error: '#ef4444',
  paused: '#f59e0b',
  waiting: '#94a3b8',
  pending: '#94a3b8',
};

function statusBadge(status) {
  const color = STATUS_COLOR[status] || '#94a3b8';
  const label = status ? status.toUpperCase() : '-';
  return `<span style="color:${color};font-size:9px;font-weight:600">${label}</span>`;
}

function updateQueueListUI(jobs) {
  const queueList = document.querySelector('.queue-list');
  if (!queueList) return;
  updateWorkspaceStats(jobs || []);
  if (!jobs || jobs.length === 0) {
    queueList.innerHTML = '<div class="queue-empty">Hàng đợi trống</div>';
    return;
  }
  queueList.innerHTML = jobs.map(item => {
    const statusIcon = item.status === 'running' ? 'RUN' : item.status === 'paused' ? 'PAUSE' : item.status === 'completed' ? 'OK' : item.status === 'failed' ? 'ERR' : 'WAIT';
    const activeClass = item.status === 'running' ? 'active' : '';
    const name = item.input_path ? item.input_path.split(/[\\/]/).pop() : `${item.type.toUpperCase()} #${item.id || 'N/A'}`;
    return `
      <div class="queue-job ${activeClass}" data-id="${item.id || ''}" data-status="${item.status || 'pending'}" style="cursor:pointer" onclick="showJobLogs(${item.id || 'null'})">
        <span class="queue-status-icon ${item.status || 'pending'}">${statusIcon}</span>
        <span class="queue-name" title="${name}">${name}</span>
      </div>
    `;
  }).join('');
}

function updateWorkspaceStats(jobs) {
  const counts = (jobs || []).reduce((acc, job) => {
    const status = job.status || 'pending';
    acc.total += 1;
    acc[status] = (acc[status] || 0) + 1;
    return acc;
  }, { total: 0 });

  const running = counts.running || 0;
  const completed = (counts.completed || 0) + (counts.done || 0);
  const failed = (counts.failed || 0) + (counts.error || 0);
  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = String(value);
  };

  setText('ws-queue-count', counts.total || 0);
  setText('ws-running-count', running);
  setText('ws-failed-count', failed);
  setText('queue-running-total', running);
  setText('queue-completed-total', completed);
  setText('queue-failed-total', failed);

  const health = document.getElementById('queue-health-text');
  if (health) {
    if (running) health.textContent = `${running} task đang chạy`;
    else if (failed) health.textContent = `${failed} task cần xử lý`;
    else if (completed) health.textContent = `${completed} task đã hoàn tất`;
    else health.textContent = 'Chưa có task đang chạy';
  }
}

window.showJobLogs = function (jobId) {
  const filterInput = document.getElementById('inp-log-filter');
  if (filterInput) {
    filterInput.value = jobId !== null ? jobId : '';
  }
  const logModal = document.getElementById('log-modal');
  if (logModal) {
    logModal.classList.add('show');
  }
  if (typeof fetchLogs === 'function') {
    fetchLogs();
  }
};

function fileBaseName(path, fallback = '-') {
  return path ? String(path).split(/[\\/]/).pop() : fallback;
}

function queueElapsed(job) {
  if (job.elapsed) return formatTime(job.elapsed);
  if (job.started_at) {
    const seconds = Math.max(0, Number(job.updated_at || Date.now() / 1000) - Number(job.started_at));
    return formatTime(seconds);
  }
  return '--:--';
}

function queueLatestMessage(job) {
  return job.error || job.last_log || job.message || (job.status === 'completed' ? 'Output ready' : 'Waiting for worker');
}

statusBadge = function (status) {
  const color = STATUS_COLOR[status] || '#94a3b8';
  const label = status ? String(status).toUpperCase() : '-';
  return `<span class="queue-status-badge queue-status-${status || 'pending'}" style="color:${color}">${label}</span>`;
};

updateQueueListUI = function (jobs) {
  const queueList = document.querySelector('.queue-list');
  if (!queueList) return;
  updateWorkspaceStats(jobs || []);
  if (!jobs || jobs.length === 0) {
    queueList.innerHTML = '<div class="queue-empty">Chưa có job nào trong hàng chờ</div>';
    return;
  }
  queueList.innerHTML = jobs.map(item => {
    const statusIcon = item.status === 'running' ? 'RUN' : item.status === 'paused' ? 'PAUSE' : item.status === 'completed' ? 'OK' : item.status === 'failed' ? 'ERR' : 'WAIT';
    const activeClass = item.status === 'running' ? 'active' : '';
    const name = fileBaseName(item.input_path, `${String(item.type || 'job').toUpperCase()} #${item.id || 'N/A'}`);
    const output = fileBaseName(item.output_path, item.status === 'completed' ? 'Output missing' : 'Output pending');
    const pct = Math.max(0, Math.min(100, Number(item.progress || 0)));
    const latest = queueLatestMessage(item);
    const duration = queueElapsed(item);
    const type = item.type || 'render';
    const retryButton = item.status === 'failed'
      ? `<button class="queue-card-action" data-queue-action="retry" data-id="${item.id || ''}" title="Retry job"><i class="ri-refresh-line"></i></button>`
      : '';
    const openButton = item.output_path
      ? `<button class="queue-card-action" data-queue-action="open-output" data-path="${escapeHtml(item.output_path)}" title="Mo folder ket qua"><i class="ri-folder-open-line"></i></button>`
      : '';
    return `
      <div class="queue-job ${activeClass}" data-id="${item.id || ''}" data-status="${item.status || 'pending'}" style="cursor:pointer" onclick="showJobLogs(${item.id || 'null'})">
        <div class="queue-card-main">
          <span class="queue-status-icon ${item.status || 'pending'}">${statusIcon}</span>
          <div class="queue-card-copy">
            <span class="queue-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
            <span class="queue-latest" title="${escapeHtml(latest)}">${escapeHtml(latest)}</span>
          </div>
          <div class="queue-card-meta">
            <span>${escapeHtml(type)}</span>
            <span>${duration}</span>
          </div>
        </div>
        <div class="queue-card-progress" aria-label="Job progress">
          <span style="width:${pct}%"></span>
        </div>
        <div class="queue-card-footer">
          <span class="queue-output" title="${escapeHtml(output)}">${escapeHtml(output)}</span>
          <div class="queue-card-actions">${retryButton}${openButton}<button class="queue-card-action" data-queue-action="logs" data-id="${item.id || ''}" title="Open logs"><i class="ri-file-list-3-line"></i></button></div>
        </div>
      </div>
    `;
  }).join('');
};

function renderQueueRows(jobs) {
  const body = document.getElementById('result-table-body');
  if (!body) return;
  body.innerHTML = '';
  rowCount = 0;

  updateQueueListUI(jobs);

  if (!jobs || jobs.length === 0) {
    body.innerHTML = '<div style="padding:10px 12px;color:var(--text-muted);font-size:11px">Chua co task nao trong hang doi</div>';
    return;
  }

  jobs.forEach((job, idx) => {
    rowCount++;
    const pct = job.progress || 0;
    const inputName = job.input_path ? job.input_path.split(/[\\/]/).pop() : (job.name || `video_${rowCount}.mp4`);
    const outputName = job.output_path ? job.output_path.split(/[\\/]/).pop() : `output_${rowCount}.mp4`;
    const elapsed = job.elapsed ? formatTime(job.elapsed) : '--:--';
    const subGoc = job.sub_source || '-';
    const subDich = job.sub_translated || '-';
    const status = job.status || 'pending';
    const isFailed = status === 'failed';
    const retryBtn = isFailed
      ? `<button class="queue-row-retry" data-queue-action="retry" data-id="${job.id || ''}" title="Th\u1eed l\u1ea1i" style="background:none;border:1px solid #ef4444;color:#ef4444;cursor:pointer;padding:1px 6px;border-radius:4px;font-size:9px;margin-left:4px;display:inline-flex;align-items:center;gap:2px"><i class="ri-refresh-line"></i></button>`
      : '';
    const subGocColor = subGoc !== '-' ? '#22c55e' : '#8892a4';
    const subDichColor = subDich !== '-' ? '#3b82f6' : '#8892a4';

    const row = document.createElement('div');
    row.className = 'result-row';
    row.id = `task-row-${rowCount}`;
    row.dataset.jobId = job.id || '';
    row.dataset.status = status;
    row.innerHTML = `
      <div class="result-cell" style="width:110px;color:#a78bfa;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${inputName}">${inputName}</div>
      <div class="result-cell" style="width:130px;color:#94a3b8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${outputName}">${outputName}</div>
      <div class="result-cell" style="width:100px">
        <div class="mini-progress"><div class="mini-progress-fill" id="mini-fill-${rowCount}" style="width:${pct}%"></div></div>
        <span style="font-size:9px;color:var(--text-muted);margin-left:4px">${pct}%</span>
      </div>
      <div class="result-cell" style="width:50px">${statusBadge(status)}${retryBtn}</div>
      <div class="result-cell" style="width:100px;color:#facc15">${elapsed}</div>
      ${renderSubtitleCells(subGoc, subDich)}
    `;
    body.appendChild(row);
  });
}

function renderSubtitleCells(subGoc, subDich) {
  const originalCues = parseSrtCues(subGoc);
  const translatedCues = parseSrtCues(subDich);

  if (originalCues.length > 0 || translatedCues.length > 0) {
    const cuesToUse = originalCues.length > 0 ? originalCues : translatedCues;
    const originalMap = new Map(originalCues.map(c => [String(c.index), c.text]));
    const translatedMap = new Map(translatedCues.map(c => [String(c.index), c.text]));

    return `
      <div class="result-cell flex2 subtitle-scroll-cell" style="display: flex; flex-direction: column; gap: 4px; padding: 6px; max-height: 140px; overflow-y: auto; width: 100%; white-space: normal;">
        ${cuesToUse.map((cue, i) => {
          const origText = originalMap.get(String(cue.index)) || (originalCues.length > 0 ? cue.text : '');
          const transText = translatedMap.get(String(cue.index)) || (translatedCues.length > 0 ? cue.text : '');

          const transStyle = transText ? 'color:#60a5fa;' : 'color:#64748b;font-style:italic;font-size:9px;';
          const transDisplay = transText ? escapeHtml(transText) : '-';
          const origDisplay = origText ? escapeHtml(origText) : '-';

          return `
            <div class="subtitle-cue-pair" style="display: flex; width: 100%; border-bottom: 1px solid rgba(255,255,255,0.05); padding: 4px 0; font-size: 11px; line-height: 1.4;">
              <div class="sub-goc-part" style="flex: 1; padding-right: 8px; border-right: 1px solid rgba(255,255,255,0.08); text-align: left; word-break: break-word; color: #e2e8f0; white-space: normal;">
                <span style="color: #64748b; font-size: 9px; margin-right: 4px; font-family: monospace;">#${cue.index} [${cue.start.split(',')[0]}]</span>
                <span>${origDisplay}</span>
              </div>
              <div class="sub-dich-part" style="flex: 1; padding-left: 8px; text-align: left; word-break: break-word; white-space: normal; ${transStyle}">
                ${transDisplay}
              </div>
            </div>
          `;
        }).join('')}
      </div>
    `;
  } else {
    const subGocDisp = subGoc || '-';
    const subDichDisp = subDich || '-';
    return `
      <div class="result-cell flex2" style="display: flex; gap: 0; padding: 6px; font-size: 11px; width: 100%;">
        <div style="flex: 1; padding-right: 8px; border-right: 1px solid rgba(255,255,255,0.08); text-align: left; color: #8892a4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(subGocDisp)}">${escapeHtml(subGocDisp)}</div>
        <div style="flex: 1; padding-left: 8px; text-align: left; color: #8892a4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(subDichDisp)}">${escapeHtml(subDichDisp)}</div>
      </div>
    `;
  }
}

async function initQueueTable() {
  const apiJobs = await apiGet('/queue');
  if (apiJobs && Array.isArray(apiJobs) && apiJobs.length > 0) {
    renderQueueRows(apiJobs);
    return;
  }
  renderQueueRows([]);
}

let queueTableInitialized = false;
function initQueueTableOnce() {
  if (queueTableInitialized) return;
  queueTableInitialized = true;
  initQueueTable();
  onQueueChange(sseQueueRefresh);
}

document.addEventListener('DOMContentLoaded', initQueueTableOnce);

if (document.readyState === 'complete' || document.readyState === 'interactive') {
  initQueueTableOnce();
}

function sseQueueRefresh(jobs) {
  if (!jobs || jobs.length === 0) return;
  const body = document.getElementById('result-table-body');
  if (!body) return;
  const currentRows = body.querySelectorAll('.result-row').length;
  const hasStatusChange = jobs.some(j => {
    const row = document.querySelector(`[data-job-id="${j.id}"]`);
    return row && row.dataset.status !== j.status;
  });
  if (jobs.length !== currentRows || hasStatusChange) {
    renderQueueRows(jobs);
  } else {
    jobs.forEach(j => {
      const row = document.querySelector(`[data-job-id="${j.id}"]`);
      if (row) {
        const fillId = row.id?.replace('task-row-', 'mini-fill-');
        const fill = document.getElementById(fillId);
        const pct = Math.round(j.progress || 0);
        if (fill) fill.style.width = pct + '%';
        const pctText = row.querySelector('.result-cell span');
        if (pctText) pctText.textContent = pct + '%';
      }
    });
  }
}

let allEdgeVoices = [];

async function loadEdgeVoices() {
  try {
    const res = await fetch('/api/voice/edge-voices');
    if (res.ok) {
      allEdgeVoices = await res.json();
      updateVoiceDropdown();
    }
  } catch (err) {
    console.error('Failed to load Edge voices:', err);
  }
}

async function updateVoiceDropdown() {
  const providerSel = document.getElementById('sel-tts-provider');
  const langSel = document.getElementById('sel-voice-lang');
  const typeSel = document.getElementById('sel-voice-type');
  if (!langSel || !typeSel) return;

  const provider = providerSel?.value;
  if (provider === 'FPT.AI TTS') {
    const fptVoices = [
      { value: 'banmai', text: 'Ban Mai (Nữ miền Bắc)' },
      { value: 'lannhi', text: 'Lan Nhi (Nữ miền Nam)' },
      { value: 'leminh', text: 'Lê Minh (Nam miền Bắc)' },
      { value: 'myan', text: 'Mỹ An (Nữ miền Trung)' },
      { value: 'thuminh', text: 'Thu Minh (Nữ miền Bắc)' },
      { value: 'giahuy', text: 'Gia Huy (Nam miền Trung)' },
      { value: 'linhsan', text: 'Linh San (Nữ miền Nam)' }
    ];
    typeSel.innerHTML = '';
    fptVoices.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.value;
      opt.textContent = v.text;
      typeSel.appendChild(opt);
    });
    return;
  }

  if (provider === 'Valtec TTS') {
    const valtecVoices = [
      { value: 'NF', text: 'NF - Nữ miền Bắc' },
      { value: 'SF', text: 'SF - Nữ miền Nam' },
      { value: 'NM1', text: 'NM1 - Nam miền Bắc' },
      { value: 'SM', text: 'SM - Nam miền Nam' },
      { value: 'NM2', text: 'NM2 - Nam miền Bắc 2' }
    ];
    typeSel.innerHTML = '';
    valtecVoices.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.value;
      opt.textContent = v.text;
      typeSel.appendChild(opt);
    });
    return;
  }

  if (provider === 'CapCut TTS') {
    const capcutVoices = [
      { value: 'BV074_streaming_dsp|7550087831092251920|sami', text: 'Cô gái hoạt ngôn (CapCut)' }
    ];
    typeSel.innerHTML = '';
    capcutVoices.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.value;
      opt.textContent = v.text;
      typeSel.appendChild(opt);
    });
    return;
  }

  if (provider === 'f5') {
    typeSel.innerHTML = '';
    let voices = ['default'];
    try {
      const status = await apiGet('/voice/f5/status');
      voices = status?.voices?.length ? status.voices : voices;
    } catch (_) { }
    voices.forEach(name => {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = `${name} (F5 local clone)`;
      typeSel.appendChild(opt);
    });
    return;
  }

  if (!allEdgeVoices.length) return;
  const selectedLang = langSel.value;
  const currentVal = typeSel.value;
  typeSel.innerHTML = '';

  let filtered = allEdgeVoices;
  if (selectedLang === 'Tiếng Việt') {
    filtered = allEdgeVoices.filter(v => v.locale.startsWith('vi'));
  } else if (selectedLang === 'Tiếng Anh') {
    filtered = allEdgeVoices.filter(v => v.locale.startsWith('en'));
  }

  filtered.forEach(v => {
    const opt = document.createElement('option');
    opt.value = v.short_name;
    const cleanName = v.friendly_name.replace('Microsoft ', '').replace(' Online (Natural)', '').replace(' - Vietnamese (Vietnam)', '').replace(' - English (United States)', '');
    opt.textContent = `${cleanName} (${v.gender})`;
    typeSel.appendChild(opt);
  });

  if (currentVal && Array.from(typeSel.options).some(o => o.value === currentVal)) {
    typeSel.value = currentVal;
  }
}

document.getElementById('sel-tts-provider')?.addEventListener('change', (e) => {
  const provider = e.target.value;
  const fptRow = document.getElementById('row-fpt-key');
  if (fptRow) {
    fptRow.style.display = (provider === 'FPT.AI TTS') ? 'flex' : 'none';
  }
  updateVoiceDropdown();
});

document.getElementById('sel-voice-lang')?.addEventListener('change', updateVoiceDropdown);
document.addEventListener('DOMContentLoaded', loadEdgeVoices);
if (document.readyState === 'complete' || document.readyState === 'interactive') {
  loadEdgeVoices();
}

/* Translation engine registry: one handler covers every subtitle engine button. */
const TRANSLATION_ENGINES = {
  gpt: { name: 'GPT (API)', supportsLanguages: ['en', 'vi', 'zh', 'es', 'fr', 'de', 'ja', 'ko', 'ru', 'pt', 'ar', 'it', 'th'] },
  gemini: { name: 'Gemini (API)', supportsLanguages: ['en', 'vi', 'zh', 'es', 'fr', 'de', 'ja', 'ko', 'ru', 'pt', 'ar', 'it', 'th'] },
  ai_provider: { name: 'AI Provider (OpenRouter/NIM/Custom)', supportsLanguages: ['en', 'vi', 'zh', 'es', 'fr', 'de', 'ja', 'ko', 'ru', 'pt', 'ar', 'it', 'th'] },
  deeplx: { name: 'DeepLX (Free/API)', supportsLanguages: ['en', 'vi', 'zh', 'es', 'fr', 'de', 'ja', 'ko', 'ru', 'pt', 'ar', 'it', 'th'] },
  google: { name: 'Google Translate (Free)', supportsLanguages: ['en', 'vi', 'zh', 'es', 'fr', 'de', 'ja', 'ko', 'ru', 'pt', 'ar', 'it', 'th'] },
  seamless: { name: 'SeamlessM4T (Free Local)', supportsLanguages: ['en', 'vi', 'zh', 'es', 'fr', 'de', 'ja', 'ko', 'ar', 'pt', 'ru', 'it', 'th'] },
  nllb: { name: 'NLLB-200 (Free Local)', supportsLanguages: ['en', 'vi', 'zh', 'es', 'fr', 'de', 'ja', 'ko', 'ar', 'pt', 'ru', 'it', 'th'] },
  m2m100: { name: 'M2M100 (Free Local)', supportsLanguages: ['en', 'vi', 'zh', 'es', 'fr', 'de', 'ja', 'ko', 'ar', 'pt', 'ru', 'it', 'th'] },
  marian: { name: 'MarianMT (Free Local)', supportsLanguages: ['en', 'vi', 'zh'] },
};

function getSelectedLanguageCode(selectId, fallback) {
  const select = document.getElementById(selectId);
  if (!select) return fallback;
  const val = select.value || select.options[select.selectedIndex]?.text || '';
  return getLangCodeFromValueOrText(val) || fallback;
}

async function waitTranslationJob(jobId, btn, outputName) {
  return new Promise(resolve => {
    const poll = setInterval(async () => {
      const prog = await apiGet(`/subtitle/translate-progress/${jobId}`);
      if (!prog) return;
      const pct = Math.max(0, Math.min(100, Number(prog.progress || 0)));
      btn.innerHTML = `<i class="ri-loader-4-line ri-spin"></i> ${pct}%`;
      if (prog.status === 'done') {
        clearInterval(poll);
        resolve(prog.translated || prog.translated_text || '');
      } else if (prog.status === 'error') {
        clearInterval(poll);
        showToast(prog.error || 'Dich subtitle that bai', 'error');
        resolve('');
      }
    }, 600);
  });
}

async function handleSubtitleTranslation(engine) {
  const config = TRANSLATION_ENGINES[engine];
  if (!config) throw new Error(`Unknown translation engine: ${engine}`);

  const btn = document.getElementById(`btn-sub-trans-${engine}`);
  if (!btn) return;
  const originalHTML = btn.innerHTML;

  try {
    const text = await getSubtitleText();
    if (!text || !text.trim()) {
      showToast('Chua tai phu de.', 'warn');
      return;
    }

    const sourceLang = getSelectedLanguageCode('sel-lang-from', 'en');
    const targetLang = getSelectedLanguageCode('sel-lang-to', 'vi');
    validate(sourceLang, 'language');
    validate(targetLang, 'language');

    if (!config.supportsLanguages.includes(sourceLang) || !config.supportsLanguages.includes(targetLang)) {
      showToast(`${config.name} không hỗ trợ cặp ngôn ngữ này`, 'warn');
      return;
    }

    btn.disabled = true;
    btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Dang dich...';
    const response = await apiPost('/subtitle/translate', {
      text,
      engine,
      model: getSubtitleModel(engine),
      source_lang: sourceLang,
      target_lang: targetLang,
      project_id: currentProjectId || null,
      semantic_segmentation: isSubtitleSemanticEnabled(),
    });
    if (!response) return;

    let translated = response.translated_text || response.translated || '';
    if (response.job_id) {
      translated = await waitTranslationJob(response.job_id, btn, `translated_${engine}.srt`);
    }

    if (translated) {
      showTransResult(translated, `translated_${engine}.srt`);
    }
    addTaskRow();
    showToast(`Da gui lenh dich ${config.name}`, 'success');
  } catch (error) {
    showToast(error.message || String(error), error instanceof ValidationError ? 'warn' : 'error');
    addClientLog('error', `Translation ${engine} failed`, error.stack || error.message || String(error));
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHTML;
  }
}

Object.keys(TRANSLATION_ENGINES).forEach(engine => {
  const btn = document.getElementById(`btn-sub-trans-${engine}`);
  btn?.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    handleSubtitleTranslation(engine);
  }, true);
});

function activateWorkspaceView(action) {
  document.querySelectorAll('.workspace-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.workspaceAction === action);
  });
}

function switchWorkspaceTab(tabId) {
  const tabBtn = document.querySelector(`#processing-tabs .tab[data-target="${tabId}"]`);
  if (tabBtn) {
    if (window._switchTab) window._switchTab(tabBtn);
    else tabBtn.click();
  }
  document.getElementById('processing-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function handleWorkspaceAction(action) {
  if (!action) return;
  if (action === 'home') {
    activateWorkspaceView('home');
    document.getElementById('top-panels-row')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } else if (action === 'preview') {
    activateWorkspaceView('preview');
    document.getElementById('video-preview')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } else if (action === 'queue') {
    activateWorkspaceView('queue');
    document.getElementById('task-queue-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } else if (action === 'load') {
    document.getElementById('btn-browse-video')?.click();
  } else if (action === 'subtitle') {
    switchWorkspaceTab('tab-subtitle');
  } else if (action === 'voice') {
    switchWorkspaceTab('tab-voice');
  } else if (action === 'execute') {
    document.getElementById('btn-execute')?.click();
  } else if (action === 'settings') {
    document.getElementById('settings-modal')?.classList.add('show');
  } else if (action === 'logs') {
    document.getElementById('btn-log-queue')?.click();
  } else if (action === 'timeline-snap') {
    document.querySelector('[data-workspace-action="timeline-snap"]')?.classList.toggle('active');
    showToast('Snap timeline da doi trang thai', 'info');
  } else if (action === 'timeline-zoom-in' || action === 'timeline-zoom-out') {
    const timeline = document.getElementById('scene-timeline');
    if (timeline) {
      const current = Number(timeline.dataset.zoom || 1);
      const next = action === 'timeline-zoom-in' ? Math.min(1.5, current + 0.1) : Math.max(0.8, current - 0.1);
      timeline.dataset.zoom = next.toFixed(1);
      timeline.style.setProperty('--timeline-zoom', next.toFixed(1));
      timeline.style.setProperty('--timeline-width', `${Math.round(next * 100)}%`);
    }
  }
}

document.querySelectorAll('[data-workspace-action]').forEach(btn => {
  btn.addEventListener('click', (event) => {
    const action = event.currentTarget.dataset.workspaceAction;
    if (action && !action.startsWith('timeline-')) event.preventDefault();
    handleWorkspaceAction(action);
  });
});

document.addEventListener('click', async (event) => {
  const presetCard = event.target.closest?.('.preset-card');
  if (presetCard) {
    document.querySelectorAll('.preset-card').forEach(card => card.classList.remove('active'));
    presetCard.classList.add('active');
    const presetName = presetCard.dataset.presetName || presetCard.querySelector('.preset-card-title')?.textContent || 'Custom';
    const presetSelect = document.getElementById('sel-project-preset');
    if (presetSelect) {
      const existing = [...presetSelect.options].find(option => option.value === presetName || option.textContent === presetName);
      if (existing) {
        presetSelect.value = existing.value;
      } else {
        presetSelect.add(new Option(presetName, presetName));
        presetSelect.value = presetName;
      }
      presetSelect.dispatchEvent(new Event('change', { bubbles: true }));
    }
    const summary = document.getElementById('preset-summary');
    if (summary) {
      summary.textContent = `${presetCard.dataset.resolution || '-'} · ${presetCard.dataset.fps || '-'}fps · ${presetCard.dataset.codec || '-'} · ${presetCard.dataset.voice || '-'} · ${presetCard.dataset.subtitle || '-'}`;
    }
    return;
  }

  const queueAction = event.target.closest?.('[data-queue-action]');
  if (!queueAction) return;
  event.preventDefault();
  event.stopPropagation();
  const action = queueAction.dataset.queueAction;
  const id = queueAction.dataset.id;
  if (action === 'open-output') {
    await openOutputFolder(queueAction.dataset.path || '');
    return;
  }
  if (action === 'logs') {
    window.showJobLogs(id || null);
  } else if (action === 'retry' && id) {
    await apiPost(`/queue/${id}/retry`);
    showToast('Dang thu lai job #' + id, 'success', 2000);
    const jobs = await apiGet('/queue');
    renderQueueRows(jobs || []);
  } else if (action === 'open-output') {
    const path = queueAction.dataset.path || '';
    if (path && navigator.clipboard) {
      await navigator.clipboard.writeText(path);
      showToast('Đã copy đường dẫn output', 'success', 1800);
    } else if (path) {
      showToast(path, 'info', 3000);
    }
  }
});

function updateWorkspaceMetadata() {
  const videoPath = document.getElementById('inp-video-path')?.value || '';
  const outputPath = document.getElementById('inp-output-path')?.value || '';
  const preset = document.getElementById('sel-project-preset')?.value || 'Movie Review';
  const fps = document.getElementById('sel-export-fps')?.value || '30';
  const provider = document.getElementById('sel-tts-provider')?.value || 'Edge TTS';
  const subtitleEnabled = document.getElementById('chk-translate')?.checked;
  const videoName = videoPath ? videoPath.split(/[\\/]/).pop() : 'Chưa chọn video';

  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };

  setText('workspace-project-name', preset);
  setText('workspace-video-state', videoName);
  setText('preview-file-name', videoPath ? videoName : 'No media loaded');
  setText('preview-render-profile', outputPath ? outputPath.split(/[\\/]/).pop() : 'MP4 H264');
  setText('preview-fps', fps);
  setText('preview-sub-state', subtitleEnabled ? 'Auto' : 'Off');
  setText('preview-voice-state', provider.replace(' TTS', '').replace(' (free)', ''));
}

['inp-video-path', 'inp-output-path', 'sel-project-preset', 'sel-tts-provider', 'chk-translate'].forEach(id => {
  document.getElementById(id)?.addEventListener('change', updateWorkspaceMetadata);
  document.getElementById(id)?.addEventListener('input', updateWorkspaceMetadata);
});


const commandActions = [
  { id: 'load', icon: 'ri-folder-video-line', title: 'Load media', hint: 'Chọn video nguồn', key: 'L' },
  { id: 'subtitle', icon: 'ri-closed-captioning-line', title: 'Subtitle tools', hint: 'Import, dịch, burn sub', key: 'S' },
  { id: 'voice', icon: 'ri-mic-line', title: 'Voice tools', hint: 'TTS, voice clone, preview', key: 'V' },
  { id: 'execute', icon: 'ri-play-fill', title: 'Render now', hint: 'Chạy pipeline hiện tại', key: 'R' },
  { id: 'queue', icon: 'ri-list-check-3', title: 'Open queue', hint: 'Xem job monitor', key: 'Q' },
  { id: 'logs', icon: 'ri-file-list-3-line', title: 'Open logs', hint: 'Xem log theo task', key: 'G' },
  { id: 'settings', icon: 'ri-settings-4-line', title: 'Settings', hint: 'API keys, FFmpeg, proxy', key: ',' },
];

function ensureCommandPalette() {
  let overlay = document.getElementById('command-palette-overlay');
  if (overlay) return overlay;

  overlay = document.createElement('div');
  overlay.id = 'command-palette-overlay';
  overlay.className = 'command-palette-overlay';
  overlay.innerHTML = `
    <div class="command-palette" role="dialog" aria-label="Command palette">
      <div class="command-search">
        <i class="ri-search-line"></i>
        <input id="command-search-input" type="text" placeholder="Tìm lệnh..." autocomplete="off" />
      </div>
      <div class="command-list" id="command-list"></div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (event) => {
    if (event.target === overlay) closeCommandPalette();
  });
  overlay.querySelector('#command-search-input')?.addEventListener('input', renderCommandList);
  return overlay;
}

function renderCommandList() {
  const overlay = ensureCommandPalette();
  const input = overlay.querySelector('#command-search-input');
  const list = overlay.querySelector('#command-list');
  const query = (input?.value || '').trim().toLowerCase();
  const actions = commandActions.filter(action => {
    return action.title.toLowerCase().includes(query) || action.hint.toLowerCase().includes(query);
  });
  list.innerHTML = actions.map((action, index) => `
    <button class="command-item ${index === 0 ? 'active' : ''}" data-command-action="${action.id}">
      <i class="${action.icon}"></i>
      <span><strong>${action.title}</strong><small>${action.hint}</small></span>
      <kbd>${action.key}</kbd>
    </button>
  `).join('');
  list.querySelectorAll('.command-item').forEach(item => {
    item.addEventListener('click', () => {
      closeCommandPalette();
      handleWorkspaceAction(item.dataset.commandAction);
    });
  });
}

function openCommandPalette() {
  const overlay = ensureCommandPalette();
  overlay.classList.add('show');
  const input = overlay.querySelector('#command-search-input');
  if (input) {
    input.value = '';
  }
  renderCommandList();
  input?.focus();
}

function closeCommandPalette() {
  document.getElementById('command-palette-overlay')?.classList.remove('show');
}

document.getElementById('btn-command-palette')?.addEventListener('click', openCommandPalette);
document.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    openCommandPalette();
  } else if (event.key === 'Escape') {
    closeCommandPalette();
  }
});

updateWorkspaceMetadata();

/* UI bridge for replaced layouts: connects presentational controls that are not part of the core pipeline. */
(function attachLayoutBridge() {
  const onceKey = '__layoutBridgeAttached';
  if (window[onceKey]) return;
  window[onceKey] = true;

  const toast = (message, type = 'info') => {
    if (typeof showToast === 'function') showToast(message, type, 2200);
    else console.log(`[${type}] ${message}`);
  };

  const click = (id) => document.getElementById(id)?.click();
  const scrollTo = (id) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  const switchTab = (tabId) => {
    const tab = document.querySelector(`#processing-tabs .tab[data-target="${tabId}"]`);
    if (tab) {
      if (window._switchTab) window._switchTab(tab);
      else tab.click();
      scrollTo('processing-panel');
    }
  };


  document.getElementById('toggle-mode')?.addEventListener('change', (event) => {
    document.body.classList.toggle('light-mode', Boolean(event.target.checked));
    try { localStorage.setItem('ui.lightMode', event.target.checked ? '1' : '0'); } catch {}
  });

  try {
    const light = localStorage.getItem('ui.lightMode') === '1';
    document.body.classList.toggle('light-mode', light);
    const toggle = document.getElementById('toggle-mode');
    if (toggle) toggle.checked = light;
  } catch {}

  document.getElementById('btn-preview-crop')?.addEventListener('click', () => {
    const crop = document.getElementById('chk-crop-video');
    if (crop) {
      crop.checked = !crop.checked;
      crop.dispatchEvent(new Event('change', { bubbles: true }));
      toast(crop.checked ? 'Da bat crop video' : 'Da tat crop video');
    }
    scrollTo('work-mode-panel');
  });

  document.getElementById('btn-preview-image')?.addEventListener('click', () => {
    switchTab('tab-enhance');
    click('btn-branding-logo');
  });

  document.getElementById('btn-preview-gallery')?.addEventListener('click', () => {
    switchTab('tab-edit');
    toast('Mo tab chinh sua de quan ly canh va media.');
  });

  document.getElementById('btn-preview-text')?.addEventListener('click', () => {
    switchTab('tab-enhance');
    click('btn-branding-text');
  });

  document.querySelectorAll('.live-range').forEach(range => {
    const update = () => {
      const value = range.closest('.slider-row')?.querySelector('.range-val');
      if (value) value.textContent = range.id.includes('volume') || range.id.includes('audio') ? `${range.value}%` : range.value;
    };
    range.addEventListener('input', update);
    update();
  });

  ['chk-music-auto-add', 'chk-music-random', 'chk-enhance-hdr', 'chk-enhance-denoise', 'chk-sub-dual'].forEach(id => {
    document.getElementById(id)?.addEventListener('change', (event) => {
      const label = event.target.closest('label')?.textContent?.trim() || id;
      toast(`${label}: ${event.target.checked ? 'bat' : 'tat'}`);
    });
  });

  document.getElementById('sel-work-mode')?.addEventListener('change', (event) => {
    const modeText = String(event.target.value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    const custom = /tuy|custom/i.test(modeText);
    const resize = document.getElementById('chk-resize');
    if (resize) {
      resize.checked = custom;
      resize.dispatchEvent(new Event('change', { bubbles: true }));
    }
    toast(`Che do lam viec: ${event.target.value}`);
  });

  document.getElementById('sel-export-fps')?.addEventListener('change', (event) => {
    const previewFps = document.getElementById('preview-fps');
    if (previewFps) previewFps.textContent = event.target.value || '30';
  });

  document.getElementById('chk-keep-ratio')?.addEventListener('change', (event) => {
    toast(event.target.checked ? 'Giu ty le khung hinh' : 'Cho phep doi ty le tu do');
  });

  const refreshQueueHealth = () => {
    const health = document.getElementById('queue-health-text');
    if (!health) return;
    const running = Number(document.getElementById('queue-running-total')?.textContent || 0);
    const failed = Number(document.getElementById('queue-failed-total')?.textContent || 0);
    if (running > 0) health.textContent = `${running} task dang chay`;
    else if (failed > 0) health.textContent = `${failed} task loi can kiem tra`;
    else health.textContent = 'Chua co task dang chay';
  };
  window.refreshQueueHealth = refreshQueueHealth;
  setInterval(refreshQueueHealth, 2500);
  refreshQueueHealth();
})();
