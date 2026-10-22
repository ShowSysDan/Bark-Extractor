/* ============================================================
   Bark Extractor – Frontend Logic
   ============================================================ */

'use strict';

// ----------------------------------------------------------------
// Auth: if the session expires, any API call returns 401 – send the
// user to the login page (preserving where they were) instead of
// letting the UI silently fail.
// ----------------------------------------------------------------
(() => {
  const nativeFetch = window.fetch.bind(window);
  let redirecting = false;
  window.fetch = async (...args) => {
    const res = await nativeFetch(...args);
    if (res.status === 401 && !redirecting) {
      redirecting = true;
      const next = encodeURIComponent(location.pathname + location.search);
      location.href = `/login?next=${next}`;
    }
    return res;
  };
})();

// ----------------------------------------------------------------
// Utilities
// ----------------------------------------------------------------

const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

function toast(msg, type = 'info') {
  const el = document.getElementById('toast');
  const body = document.getElementById('toastBody');
  body.textContent = msg;
  el.className = `toast align-items-center text-bg-${type} border-0`;
  bootstrap.Toast.getOrCreateInstance(el, { delay: 3500 }).show();
}

function escHtml(str) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(str));
  return d.innerHTML;
}

function humanSize(bytes) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let n = bytes;
  for (const u of units) {
    if (n < 1024) return `${n.toFixed(1)} ${u}`;
    n /= 1024;
  }
  return `${n.toFixed(1)} TB`;
}

function updateDiskUsage() {
  const total = allFiles.reduce((sum, f) => sum + (f.size || 0), 0);
  const el = document.getElementById('diskUsage');
  el.textContent = allFiles.length > 0 ? `· ${humanSize(total)} used` : '';
}

// ----------------------------------------------------------------
// Active Downloads Manager
// ----------------------------------------------------------------

const activeDownloads = new Map();   // download_id -> { job, logLines, sse }

function renderActiveSection() {
  const section = document.getElementById('activeSection');
  const list    = document.getElementById('activeList');
  const count   = document.getElementById('activeCount');

  const items = [...activeDownloads.values()];
  const active = items.filter(d => ['pending','running'].includes(d.job.status));
  count.textContent = `${active.length} active`;

  if (items.length === 0) {
    section.style.display = 'none';
    return;
  }
  section.style.display = '';

  list.innerHTML = items.map(d => renderDownloadItem(d)).join('');

  // Wire stop buttons
  list.querySelectorAll('[data-stop]').forEach(btn => {
    btn.addEventListener('click', () => stopDownload(btn.dataset.stop));
  });

  // Wire log buttons
  list.querySelectorAll('[data-log]').forEach(btn => {
    btn.addEventListener('click', () => showLog(btn.dataset.log));
  });
}

// Jobs other than downloads: normalize / trim / convert (run on library files)
const JOB_ICONS = { normalize: 'bi-soundwave', trim: 'bi-scissors', convert: 'bi-arrow-left-right' };
const isProcessJob = job => !!job.kind && job.kind !== 'download';
const jobLabel = job => job.label || (job.kind ? job.kind[0].toUpperCase() + job.kind.slice(1) : 'Job');

function renderDownloadItem(d) {
  const { job, logLines } = d;
  const statusCls = `status-${job.status}`;
  const isActive  = ['pending','running'].includes(job.status);
  const pct       = Math.min(100, Math.max(0, job.progress || 0)).toFixed(1);

  let barClass = 'progress-bar';
  if (job.status === 'completed') barClass += ' bg-success';
  else if (job.status === 'failed' || job.status === 'cancelled') barClass += ' bg-danger';

  const isProc = isProcessJob(job);
  const metaParts = [];
  if (isProc && job.stage) metaParts.push(`<i class="bi bi-gear"></i> ${escHtml(job.stage)}…`);
  if (job.speed)        metaParts.push(`<i class="bi bi-speedometer2"></i> ${escHtml(job.speed)}`);
  if (job.eta)          metaParts.push(`<i class="bi bi-clock"></i> ETA ${escHtml(job.eta)}`);
  if (job.current_file && !isProc) metaParts.push(`<i class="bi bi-file-earmark-music"></i> ${escHtml(job.current_file)}`);

  return `
  <div class="download-item ${statusCls}" id="item-${job.download_id}">
    <div class="d-flex align-items-start gap-3">
      <div class="flex-grow-1 min-w-0">
        <div class="d-flex align-items-center gap-2 mb-1 flex-wrap">
          <span class="status-badge ${statusCls}">${job.status}</span>
          ${isProc
            ? `<span class="download-url flex-grow-1"><i class="bi ${JOB_ICONS[job.kind] || 'bi-gear'} me-1"></i>${escHtml(jobLabel(job))}: ${escHtml(job.title || '')}</span>`
            : `<span class="download-url flex-grow-1">${escHtml(job.url || '')}</span>`}
        </div>

        ${metaParts.length ? `<div class="meta-info d-flex mb-1">${metaParts.join(' &nbsp;')}</div>` : ''}

        <div class="progress mb-1">
          <div class="${barClass}" style="width:${pct}%" role="progressbar"
               aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"></div>
        </div>
        <div class="meta-info" style="font-size:.72rem">${pct}%
          ${job.status === 'failed' && job.error ? `<span class="text-danger ms-2">${escHtml(job.error)}</span>` : ''}
        </div>
      </div>

      <div class="d-flex flex-column gap-1 flex-shrink-0">
        ${isActive ? `<button class="btn btn-outline btn-stop" data-stop="${job.download_id}" title="${isProc ? 'Stop' : 'Stop download'}">
          <i class="bi bi-stop-fill"></i> Stop
        </button>` : ''}
        <button class="btn btn-outline btn-log" data-log="${job.download_id}" title="View log">
          <i class="bi bi-terminal"></i> Log
        </button>
      </div>
    </div>
  </div>`;
}

function stopDownload(downloadId) {
  const job = activeDownloads.get(downloadId)?.job;
  const isProc = job && isProcessJob(job);
  fetch(`/api/download/${downloadId}/stop`, { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.ok) toast(isProc ? `${jobLabel(job)} cancelled.` : 'Download cancelled – cleaning up partial files…', 'warning');
      else toast(data.message || 'Could not stop download', 'danger');
    })
    .catch(() => toast('Network error', 'danger'));
}

function showLog(downloadId) {
  const d = activeDownloads.get(downloadId);
  const pre = document.getElementById('logContent');
  if (d) {
    pre.textContent = d.logLines.join('\n');
  } else {
    pre.textContent = '(Log not available)';
  }

  // Mark which download the modal is showing so live SSE lines can be appended
  pre.dataset.activeId = downloadId;

  const modalEl = document.getElementById('logModal');
  const modal = new bootstrap.Modal(modalEl);
  modal.show();

  // Keep scrolled to bottom when log is live
  modalEl.addEventListener('shown.bs.modal', () => {
    pre.scrollTop = pre.scrollHeight;
  }, { once: true });

  // Clear active tracking when modal is closed
  modalEl.addEventListener('hidden.bs.modal', () => {
    pre.dataset.activeId = '';
  }, { once: true });
}

// ----------------------------------------------------------------
// SSE – subscribe to a download stream
// ----------------------------------------------------------------

// jobInit seeds the card before the first SSE event (url, kind, title).
// autoDownload: when a normalize job completes, download its output file.
function subscribeToDownload(downloadId, jobInit = {}, { autoDownload = false } = {}) {
  const existing = activeDownloads.get(downloadId);
  if (existing) {
    // Already tracking this job (e.g. normalize requested twice) – just
    // upgrade it to auto-download if asked.
    if (autoDownload) existing.autoDownload = true;
    return;
  }

  const entry = {
    job: { status: 'pending', progress: 0, url: '', ...jobInit, download_id: downloadId },
    logLines: [], sse: null, autoDownload, finished: false,
  };
  activeDownloads.set(downloadId, entry);
  renderActiveSection();

  const sse = new EventSource(`/api/download/${downloadId}/stream`);
  entry.sse = sse;

  sse.onmessage = (e) => {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }

    const d = activeDownloads.get(downloadId);
    if (!d) { sse.close(); return; }

    switch (data.type) {
      case 'progress':
        Object.assign(d.job, data);
        renderActiveSection();
        break;

      case 'log':
        d.logLines.push(data.line);
        // If log modal open for this id, append live
        const logPre = document.getElementById('logContent');
        if (logPre && logPre.dataset.activeId === downloadId) {
          logPre.textContent += data.line + '\n';
          logPre.scrollTop = logPre.scrollHeight;
        }
        break;

      case 'done':
        sse.close();
        finishJob(downloadId, data);
        break;

      case 'error':
        sse.close();
        activeDownloads.delete(downloadId);
        renderActiveSection();
        break;

      case 'close':
        sse.close();
        break;
    }
  };

  sse.onerror = () => {
    const d = activeDownloads.get(downloadId);
    if (d && !d.finished) {
      // Poll status as fallback
      pollJobStatus(downloadId);
    }
    sse.close();
  };
}

// Final state for a job – reached via SSE 'done' or the polling fallback.
function finishJob(downloadId, job) {
  const d = activeDownloads.get(downloadId);
  if (!d || d.finished) return;
  d.finished = true;
  Object.assign(d.job, job);
  renderActiveSection();

  const isProc = isProcessJob(d.job);
  if (job.status === 'completed') {
    if (isProc) {
      toast(`${jobLabel(d.job)} done: ${job.result_name || d.job.title || 'file'}`, 'success');
      if (d.autoDownload && job.result_file_id) downloadFile(job.result_file_id, job.result_name);
    } else {
      toast('Download complete! 🎵', 'success');
    }
    // Reload file list so new files appear immediately
    loadFileList({ silent: true });
  } else if (job.status === 'cancelled') {
    toast(isProc ? `${jobLabel(d.job)} cancelled.` : 'Download cancelled.', 'secondary');
  } else {
    toast(`${isProc ? jobLabel(d.job) : 'Download'} failed: ${job.error || 'unknown error'}`, 'danger');
  }
  // Remove from map after a short delay so user can read the state
  setTimeout(() => {
    activeDownloads.delete(downloadId);
    renderActiveSection();
  }, 8000);
}

function pollJobStatus(downloadId) {
  fetch(`/api/download/${downloadId}/status`)
    .then(r => {
      if (r.status === 404) return null;
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then(job => {
      const d = activeDownloads.get(downloadId);
      if (!d) return;
      if (!job) {
        // Server no longer knows this job (e.g. restarted) – drop the card.
        activeDownloads.delete(downloadId);
        renderActiveSection();
        return;
      }
      if (['pending','running'].includes(job.status)) {
        Object.assign(d.job, job);
        renderActiveSection();
        setTimeout(() => pollJobStatus(downloadId), 1500);
      } else {
        finishJob(downloadId, job);
      }
    })
    .catch(() => setTimeout(() => pollJobStatus(downloadId), 5000));
}

// Re-attach to this session's running jobs after a page reload.
async function restoreActiveJobs() {
  try {
    const res = await fetch('/api/queue/mine');
    if (!res.ok) return;
    const jobs = await res.json();
    jobs.filter(j => ['pending','running'].includes(j.status))
        .forEach(j => subscribeToDownload(j.download_id, j));
  } catch { /* non-fatal */ }
}

// ----------------------------------------------------------------
// Download Form
// ----------------------------------------------------------------

const form        = document.getElementById('downloadForm');
const urlInput    = document.getElementById('urlInput');
const qualitySel  = document.getElementById('qualitySelect');
const formatSel   = document.getElementById('formatSelect');
const playlistChk = document.getElementById('playlistCheck');
const organizeRow = document.getElementById('organizeRow');
const organizeChk = document.getElementById('organizeCheck');
const downloadBtn = document.getElementById('downloadBtn');

playlistChk.addEventListener('change', () => {
  organizeRow.style.setProperty('display', playlistChk.checked ? '' : 'none', 'important');
});

function resetDownloadBtn() {
  const fmt = formatSel.value.toUpperCase();
  downloadBtn.innerHTML = `<i class="bi bi-music-note-beamed me-1"></i> Extract ${fmt}`;
}

formatSel.addEventListener('change', resetDownloadBtn);
resetDownloadBtn();

form.addEventListener('submit', async (e) => {
  e.preventDefault();

  const url = urlInput.value.trim();
  if (!url) {
    urlInput.classList.add('is-invalid');
    return;
  }
  urlInput.classList.remove('is-invalid');

  downloadBtn.disabled = true;
  downloadBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Starting…';

  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url,
        quality:            qualitySel.value,
        format:             formatSel.value,
        is_playlist:        playlistChk.checked,
        organize_playlist:  organizeChk.checked,
      }),
    });

    const data = await res.json();
    if (!res.ok || data.error) {
      toast(data.error || 'Failed to start download', 'danger');
    } else {
      // Pass URL so it shows immediately in the download card
      subscribeToDownload(data.download_id, { url });
      urlInput.value = '';
      toast('Download queued! 🐾', 'success');
    }
  } catch {
    toast('Network error – could not reach server', 'danger');
  } finally {
    downloadBtn.disabled = false;
    resetDownloadBtn();
  }
});

// ----------------------------------------------------------------
// File Manager
// ----------------------------------------------------------------

let allFiles = [];
let lastFilesJson = null;

// Target formats offered by "Convert to"
const CONVERT_FORMATS = [
  { ext: '.mp3',  label: 'MP3' },
  { ext: '.m4a',  label: 'M4A (AAC)' },
  { ext: '.opus', label: 'Opus' },
  { ext: '.ogg',  label: 'OGG (Vorbis)' },
  { ext: '.flac', label: 'FLAC' },
  { ext: '.wav',  label: 'WAV (16-bit)' },
  { ext: '.aiff', label: 'AIFF (16-bit)' },
];

// silent: background refresh – no spinner, no error toast, and the table is
// only rebuilt when the list actually changed (keeps open menus and checks).
async function loadFileList({ silent = false } = {}) {
  const loading     = document.getElementById('filesLoading');
  const empty       = document.getElementById('filesEmpty');
  const tableWrap   = document.getElementById('filesTableWrap');
  const tbody       = document.getElementById('filesTbody');
  const fileCount   = document.getElementById('fileCount');

  if (!silent) {
    loading.style.display = '';
    empty.style.display   = 'none';
    tableWrap.style.display = 'none';
  }

  let files, json;
  try {
    const res = await fetch('/api/files');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    json = await res.text();
    files = JSON.parse(json);
  } catch {
    if (!silent) {
      toast('Could not load file list', 'danger');
      loading.style.display = 'none';
      if (allFiles.length) tableWrap.style.display = '';
    }
    return;
  }

  loading.style.display = 'none';
  if (silent && json === lastFilesJson) return;
  lastFilesJson = json;
  allFiles = files;

  fileCount.textContent = allFiles.length;
  updateDiskUsage();

  if (allFiles.length === 0) {
    empty.style.display = '';
    tableWrap.style.display = 'none';
    tbody.innerHTML = '';
    updateSelectionToolbar();
    return;
  }
  empty.style.display = 'none';

  // Keep the user's selection across re-renders
  const checkedIds = new Set($$('.row-chk:checked').map(c => c.value));

  tableWrap.style.display = '';
  tbody.innerHTML = allFiles.map(f => renderFileRow(f)).join('');

  // Wire row checkboxes
  tbody.querySelectorAll('.row-chk').forEach(chk => {
    if (checkedIds.has(chk.value)) chk.checked = true;
    chk.addEventListener('change', updateSelectionToolbar);
  });

  // Wire per-file play buttons
  tbody.querySelectorAll('[data-play]').forEach(btn => {
    btn.addEventListener('click', () => playFile(btn.dataset.play, btn.dataset.name));
  });

  // Wire per-file download buttons
  tbody.querySelectorAll('[data-dl]').forEach(btn => {
    btn.addEventListener('click', () => downloadFile(btn.dataset.dl, btn.dataset.name));
  });

  // Wire per-file "more actions" menu items
  tbody.querySelectorAll('[data-action]').forEach(btn => {
    btn.addEventListener('click', () => {
      const fileId = btn.closest('[data-file]').dataset.file;
      switch (btn.dataset.action) {
        case 'normalize': downloadNormalized(fileId); break;
        case 'trim':      openTrim(fileId); break;
        case 'rename':    openRename(fileId); break;
        case 'convert':   convertFile(fileId, btn.dataset.format); break;
      }
    });
  });

  // Wire per-file delete buttons
  tbody.querySelectorAll('[data-del]').forEach(btn => {
    btn.addEventListener('click', () => deleteFile(btn.dataset.del, btn.closest('tr')));
  });

  filterFiles(); // re-apply any active search after reload (also syncs the toolbar)
}

function renderFileRow(f) {
  const folder = f.folder && f.folder !== '.' ? `<span class="folder-badge">${escHtml(f.folder)}</span>` : '';
  const ext    = f.name.split('.').pop().toUpperCase();
  const normBadge = f.normalized ? '<span class="norm-badge" title="Peak-normalized copy">NORM</span>' : '';
  const fileExt = '.' + f.name.split('.').pop().toLowerCase();
  const convertItems = CONVERT_FORMATS
    .filter(fmt => fmt.ext !== fileExt)
    .map(fmt => `<li><button class="dropdown-item" data-action="convert" data-format="${fmt.ext}">
        <i class="bi bi-arrow-left-right me-2"></i>${fmt.label}</button></li>`)
    .join('');
  // A normalized copy can't be normalized again, so it gets no such option.
  const moreMenu = `<div class="dropdown">
        <button type="button" class="btn btn-sm file-btn-more dropdown-toggle" data-bs-toggle="dropdown"
                aria-expanded="false" title="More actions">
          <i class="bi bi-three-dots"></i>
        </button>
        <ul class="dropdown-menu dropdown-menu-end" data-file="${f.id}">
          ${f.normalized ? '' : `<li><button class="dropdown-item" data-action="normalize">
            <i class="bi bi-soundwave me-2"></i>Download normalized (-5dB peak)</button></li>`}
          <li><button class="dropdown-item" data-action="trim"><i class="bi bi-scissors me-2"></i>Trim / fade…</button></li>
          <li><button class="dropdown-item" data-action="rename"><i class="bi bi-pencil me-2"></i>Rename…</button></li>
          <li><hr class="dropdown-divider" /></li>
          <li><h6 class="dropdown-header">Convert to</h6></li>
          ${convertItems}
        </ul>
      </div>`;
  return `
  <tr data-id="${f.id}">
    <td><input type="checkbox" class="form-check-input row-chk" value="${f.id}" /></td>
    <td>
      <div class="file-name" title="${escHtml(f.name)}">
        <span class="fmt-badge">${escHtml(ext)}</span>${normBadge}${escHtml(f.name)}
      </div>
    </td>
    <td class="d-none d-md-table-cell">${folder}</td>
    <td class="d-none d-sm-table-cell text-end file-meta">${escHtml(f.size_human)}</td>
    <td class="d-none d-lg-table-cell file-meta">${escHtml(f.modified_human)}</td>
    <td class="text-end" style="white-space:nowrap">
      <div class="d-inline-flex gap-1">
        <button class="btn btn-sm file-btn-play" data-play="${f.id}" data-name="${escHtml(f.name)}" title="Play in browser">
          <i class="bi bi-play-fill"></i>
        </button>
        <button class="btn btn-sm file-btn-dl" data-dl="${f.id}" data-name="${escHtml(f.name)}" title="Download">
          <i class="bi bi-download"></i>
        </button>
        ${moreMenu}
        <button class="btn btn-sm file-btn-del" data-del="${f.id}" title="Delete from server">
          <i class="bi bi-trash"></i>
        </button>
      </div>
    </td>
  </tr>`;
}

// Row checkboxes not hidden by the search filter
function visibleRowChks() {
  return $$('#filesTbody tr')
    .filter(r => r.style.display !== 'none')
    .map(r => r.querySelector('.row-chk'))
    .filter(Boolean);
}

function updateSelectionToolbar() {
  const checked  = $$('.row-chk:checked');
  const delBtn   = document.getElementById('deleteSelectedBtn');
  const dlGroup  = document.getElementById('downloadSelectedGroup');
  const dlBtn    = document.getElementById('downloadSelectedBtn');

  if (checked.length > 0) {
    delBtn.style.display  = '';
    dlGroup.style.display = '';
    dlBtn.innerHTML = `<i class="bi bi-download"></i> Download Selected (${checked.length})`;
  } else {
    delBtn.style.display  = 'none';
    dlGroup.style.display = 'none';
  }

  const visible = visibleRowChks();
  document.getElementById('selectAllChk').checked =
    visible.length > 0 && visible.every(c => c.checked);
}

function downloadFile(fileId, fileName) {
  // Trigger a browser download via a temporary <a>
  const a = document.createElement('a');
  a.href = `/api/files/${fileId}/download`;
  a.download = fileName || 'audio';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// Download the peak-normalized copy of a file. If it doesn't exist yet it is
// generated as a background job (with progress in Active Jobs) and the
// download starts when it finishes.
async function downloadNormalized(fileId) {
  const f = allFiles.find(x => x.id === fileId);
  if (f && f.normalized) {
    // Already a normalized copy – never normalize the output again.
    downloadFile(f.id, f.name);
    return;
  }
  try {
    const res = await fetch(`/api/files/${fileId}/normalize`, { method: 'POST' });
    let data = {};
    try { data = await res.json(); } catch { /* non-JSON body */ }
    if (!res.ok || data.error) {
      toast(data.error || `Could not normalize (${res.status})`, 'danger');
    } else if (data.ready) {
      downloadFile(data.file_id, data.name);
    } else {
      subscribeToDownload(data.download_id,
        { kind: 'normalize', label: 'Normalize', title: f ? f.name : '' }, { autoDownload: true });
    }
  } catch {
    toast('Network error', 'danger');
  }
}

// Start a processing job (convert / trim) and track it in Active Jobs.
async function startFileJob(url, body) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch { /* non-JSON body */ }
    if (!res.ok || data.error) {
      toast(data.error || `Request failed (${res.status})`, 'danger');
      return false;
    }
    subscribeToDownload(data.download_id, data);
    return true;
  } catch {
    toast('Network error', 'danger');
    return false;
  }
}

function convertFile(fileId, ext) {
  return startFileJob(`/api/files/${fileId}/convert`, { format: ext });
}

async function deleteFile(fileId, rowEl, { confirmFirst = true, quiet = false } = {}) {
  if (confirmFirst && !confirm('Delete this file from the server?')) return false;
  try {
    const res = await fetch(`/api/files/${fileId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.ok) {
      rowEl.remove();
      allFiles = allFiles.filter(f => f.id !== fileId);
      lastFilesJson = null; // local list now differs from the last fetch
      document.getElementById('fileCount').textContent = allFiles.length;
      updateDiskUsage();
      if (allFiles.length === 0) {
        document.getElementById('filesTableWrap').style.display = 'none';
        document.getElementById('filesEmpty').style.display     = '';
      }
      updateSelectionToolbar();
      if (!quiet) toast('File deleted.', 'secondary');
      return true;
    }
    toast(data.error || 'Could not delete file', 'danger');
  } catch {
    toast('Network error', 'danger');
  }
  return false;
}

// Select-all header checkbox (only rows visible under the current filter)
document.getElementById('selectAllChk').addEventListener('change', function () {
  visibleRowChks().forEach(c => { c.checked = this.checked; });
  updateSelectionToolbar();
});

// Select-all toolbar button
document.getElementById('selectAllBtn').addEventListener('click', () => {
  const visible = visibleRowChks();
  const allChecked = visible.length > 0 && visible.every(c => c.checked);
  visible.forEach(c => { c.checked = !allChecked; });
  updateSelectionToolbar();
});

// Batch download selected – original (several files arrive as one ZIP)
document.getElementById('downloadSelectedBtn').addEventListener('click', () => {
  const ids = $$('.row-chk:checked').map(c => c.value);
  if (ids.length === 1) {
    const f = allFiles.find(x => x.id === ids[0]);
    if (f) downloadFile(f.id, f.name);
    return;
  }
  const a = document.createElement('a');
  a.href = `/api/files/zip?ids=${ids.join(',')}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  toast(`Zipping ${ids.length} files…`, 'info');
});

// Batch convert selected
document.getElementById('convertSelectedItems').outerHTML = CONVERT_FORMATS
  .map(fmt => `<li><button class="dropdown-item" data-convert-selected="${fmt.ext}">
      <i class="bi bi-arrow-left-right me-2"></i>${fmt.label}</button></li>`)
  .join('');
$$('[data-convert-selected]').forEach(btn => {
  btn.addEventListener('click', () => {
    const ext = btn.dataset.convertSelected;
    const targets = $$('.row-chk:checked')
      .map(c => allFiles.find(x => x.id === c.value))
      .filter(f => f && !f.name.toLowerCase().endsWith(ext));
    if (!targets.length) { toast('Selected files are already in that format.', 'secondary'); return; }
    targets.forEach((f, i) => setTimeout(() => convertFile(f.id, ext), 100 * i));
    toast(`Converting ${targets.length} file(s)…`, 'info');
  });
});

// Batch download selected – normalized
document.getElementById('downloadSelectedNormBtn').addEventListener('click', () => {
  const checked = $$('.row-chk:checked');
  checked.forEach((chk, i) => {
    setTimeout(() => downloadNormalized(chk.value), 150 * i);
  });
  toast(`Preparing ${checked.length} normalized file(s)…`, 'info');
});

// Batch delete selected
document.getElementById('deleteSelectedBtn').addEventListener('click', async () => {
  const checked = $$('.row-chk:checked');
  if (!confirm(`Delete ${checked.length} file(s) from the server?`)) return;
  let deleted = 0;
  for (const chk of checked) {
    const row = chk.closest('tr');
    if (await deleteFile(chk.value, row, { confirmFirst: false, quiet: true })) deleted++;
  }
  toast(`Deleted ${deleted} file(s).`, 'secondary');
});

// Refresh button
document.getElementById('refreshBtn').addEventListener('click', () => loadFileList());

// ----------------------------------------------------------------
// File search / filter
// ----------------------------------------------------------------

function filterFiles() {
  const q = document.getElementById('fileSearch').value.toLowerCase().trim();
  $$('#filesTbody tr').forEach(row => {
    const name = row.querySelector('.file-name')?.textContent.toLowerCase() || '';
    const show = !q || name.includes(q);
    row.style.display = show ? '' : 'none';
    // Never leave hidden rows selected – bulk delete would hit files the
    // user can't see.
    if (!show) {
      const chk = row.querySelector('.row-chk');
      if (chk) chk.checked = false;
    }
  });
  updateSelectionToolbar();
}

document.getElementById('fileSearch').addEventListener('input', filterFiles);

// ----------------------------------------------------------------
// Settings modal
// ----------------------------------------------------------------

const settingsModal = document.getElementById('settingsModal');

settingsModal.addEventListener('show.bs.modal', async () => {
  try {
    const res = await fetch('/api/settings');
    const cfg = await res.json();
    document.getElementById('syslogEnabled').checked = !!cfg.syslog_enabled;
    document.getElementById('syslogHost').value      = cfg.syslog_host || '';
    document.getElementById('syslogPort').value      = cfg.syslog_port || 514;
    toggleSyslogFields(!!cfg.syslog_enabled);
  } catch {
    toast('Could not load settings', 'danger');
  }
});

function toggleSyslogFields(enabled) {
  document.getElementById('syslogFields').style.opacity  = enabled ? '1' : '0.4';
  document.getElementById('syslogFields').style.pointerEvents = enabled ? '' : 'none';
  document.getElementById('syslogTestBtn').disabled = !enabled;
}

document.getElementById('syslogEnabled').addEventListener('change', function () {
  toggleSyslogFields(this.checked);
});

document.getElementById('settingsSaveBtn').addEventListener('click', async () => {
  const btn = document.getElementById('settingsSaveBtn');
  btn.disabled = true;
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        syslog_enabled: document.getElementById('syslogEnabled').checked,
        syslog_host:    document.getElementById('syslogHost').value.trim(),
        syslog_port:    parseInt(document.getElementById('syslogPort').value, 10),
      }),
    });
    let data = {};
    try { data = await res.json(); } catch { /* non-JSON body */ }
    if (!res.ok || data.error) {
      toast(data.error || `Server error (${res.status})`, 'danger');
    } else {
      toast('Settings saved.', 'success');
      bootstrap.Modal.getInstance(settingsModal).hide();
    }
  } catch {
    toast('Could not reach server — check your connection.', 'danger');
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('syslogTestBtn').addEventListener('click', async () => {
  const btn = document.getElementById('syslogTestBtn');
  btn.disabled = true;
  try {
    const res = await fetch('/api/settings/test', { method: 'POST' });
    let data = {};
    try { data = await res.json(); } catch { /* non-JSON body */ }
    if (!res.ok || data.error) {
      toast(data.error || `Server error (${res.status})`, 'danger');
    } else {
      toast(data.message, 'info');
    }
  } catch {
    toast('Could not reach server — check your connection.', 'danger');
  } finally {
    btn.disabled = false;
  }
});

// ----------------------------------------------------------------
// Waveforms
// ----------------------------------------------------------------

const WAVE_COLORS = {
  active:   '#D4872A',              // played / selected
  inactive: '#5a4a3a',
  dim:      '#3a3029',              // outside the trim selection
  select:   'rgba(212,135,42,.12)',
  edge:     '#E8D8C0',
  playhead: '#4493F8',
  fade:     'rgba(232,216,192,.55)',
};

const peaksCache = new Map();   // "id:modified:n" -> Promise<{duration, peaks}>

function loadPeaks(fileId, n = 1000) {
  const f = allFiles.find(x => x.id === fileId);
  const key = `${fileId}:${f ? f.modified : ''}:${n}`;
  if (!peaksCache.has(key)) {
    const p = fetch(`/api/files/${fileId}/peaks?n=${n}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)));
    p.catch(() => peaksCache.delete(key));
    peaksCache.set(key, p);
  }
  return peaksCache.get(key);
}

// Size the canvas backing store to its CSS box (crisp on HiDPI screens).
function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

/**
 * Draw a peak waveform. Options (all in seconds):
 *   played    – colour bars before this time as played (player)
 *   selStart / selEnd – highlight a selection (trim editor)
 *   fadeIn / fadeOut  – draw fade ramps inside the selection
 *   playhead  – vertical marker
 */
function drawWave(canvas, wave, opts = {}) {
  const { ctx, w, h } = fitCanvas(canvas);
  ctx.clearRect(0, 0, w, h);
  const peaks = wave ? wave.peaks : [];
  const duration = wave ? wave.duration : 0;
  const xOf = t => (duration ? (t / duration) * w : 0);
  const hasSel = opts.selStart != null && opts.selEnd != null;

  if (hasSel) {
    ctx.fillStyle = WAVE_COLORS.select;
    ctx.fillRect(xOf(opts.selStart), 0, xOf(opts.selEnd) - xOf(opts.selStart), h);
  }

  if (!peaks.length) {
    // No waveform (yet) – plain progress track
    ctx.fillStyle = WAVE_COLORS.inactive;
    ctx.fillRect(0, h / 2 - 1.5, w, 3);
    if (opts.played != null && opts.total) {
      ctx.fillStyle = WAVE_COLORS.active;
      ctx.fillRect(0, h / 2 - 1.5, (opts.played / opts.total) * w, 3);
    }
  } else {
    // Scale to the loudest bar so quiet files still read clearly
    const scale = 1 / Math.max(0.01, Math.max(...peaks));
    const barW = w / peaks.length;
    const gap = barW > 3 ? 1 : 0;
    for (let i = 0; i < peaks.length; i++) {
      const t = ((i + 0.5) / peaks.length) * duration;
      let color = WAVE_COLORS.inactive;
      if (opts.played != null) color = t <= opts.played ? WAVE_COLORS.active : WAVE_COLORS.inactive;
      if (hasSel) color = t >= opts.selStart && t <= opts.selEnd ? WAVE_COLORS.active : WAVE_COLORS.dim;
      const barH = Math.max(1, peaks[i] * scale * h * 0.92);
      ctx.fillStyle = color;
      ctx.fillRect(i * barW, (h - barH) / 2, Math.max(1, barW - gap), barH);
    }
  }

  if (hasSel) {
    const x0 = xOf(opts.selStart), x1 = xOf(opts.selEnd);
    // Fade ramps
    ctx.strokeStyle = WAVE_COLORS.fade;
    ctx.lineWidth = 1.5;
    if (opts.fadeIn > 0) {
      ctx.beginPath(); ctx.moveTo(x0, h); ctx.lineTo(xOf(opts.selStart + opts.fadeIn), 2); ctx.stroke();
    }
    if (opts.fadeOut > 0) {
      ctx.beginPath(); ctx.moveTo(xOf(opts.selEnd - opts.fadeOut), 2); ctx.lineTo(x1, h); ctx.stroke();
    }
    // Edges with grab handles
    ctx.fillStyle = WAVE_COLORS.edge;
    for (const x of [x0, x1]) {
      ctx.fillRect(x - 1, 0, 2, h);
      ctx.fillRect(x - 4, h / 2 - 12, 8, 24);
    }
  }

  if (opts.playhead != null && duration) {
    ctx.fillStyle = WAVE_COLORS.playhead;
    ctx.fillRect(xOf(opts.playhead) - 1, 0, 2, h);
  }
}

function fmtTime(s) {
  if (!isFinite(s)) return '0:00';
  const m   = Math.floor(s / 60);
  const sec = Math.floor(s % 60).toString().padStart(2, '0');
  return `${m}:${sec}`;
}

// m:ss.s (tenths) – used by the trim editor
function fmtTimePrecise(s) {
  if (!isFinite(s)) s = 0;
  const m = Math.floor(s / 60);
  const rest = (s - m * 60).toFixed(1).padStart(4, '0');
  return `${m}:${rest}`;
}

// Accepts "83.5", "1:23.5" or "0:01:23.5"; returns seconds or NaN.
function parseTime(str) {
  const parts = String(str).trim().split(':');
  if (!parts.length || parts.length > 3 || parts.some(p => p.trim() === '' || isNaN(p))) return NaN;
  return parts.reduce((acc, p) => acc * 60 + parseFloat(p), 0);
}

// ----------------------------------------------------------------
// Web Player
// ----------------------------------------------------------------

const playerAudio    = document.getElementById('playerAudio');
const playerBar      = document.getElementById('playerBar');
const playerTitle    = document.getElementById('playerTitle');
const playerPlayBtn  = document.getElementById('playerPlayBtn');
const playerPlayIcon = document.getElementById('playerPlayIcon');
const playerWave     = document.getElementById('playerWave');
const playerWaveWrap = document.getElementById('playerWaveWrap');
const playerTime     = document.getElementById('playerTime');
const playerVolume   = document.getElementById('playerVolume');
const playerCloseBtn = document.getElementById('playerCloseBtn');

let playerWaveData = null;   // peaks for the file in the player
let playerFileId   = null;

function drawPlayerWave() {
  if (playerBar.style.display === 'none') return;
  drawWave(playerWave, playerWaveData, {
    played: playerAudio.currentTime || 0,
    total: playerAudio.duration || 0,
  });
}

function playFile(fileId, fileName) {
  playerFileId             = fileId;
  playerWaveData           = null;
  playerAudio.src          = `/api/files/${fileId}/stream`;
  playerTitle.textContent  = fileName;
  playerBar.style.display  = '';
  document.body.classList.add('player-open');
  playerAudio.play().catch(() => {});
  drawPlayerWave();
  loadPeaks(fileId, 600)
    .then(w => { if (playerFileId === fileId) { playerWaveData = w; drawPlayerWave(); } })
    .catch(() => { /* waveform is optional – the plain track still seeks */ });
}

playerAudio.addEventListener('play',  () => { playerPlayIcon.className = 'bi bi-pause-fill'; });
playerAudio.addEventListener('pause', () => { playerPlayIcon.className = 'bi bi-play-fill';  });
playerAudio.addEventListener('ended', () => { playerPlayIcon.className = 'bi bi-play-fill';  });

playerAudio.addEventListener('timeupdate', () => {
  if (playerAudio.duration) {
    playerTime.textContent  = `${fmtTime(playerAudio.currentTime)} / ${fmtTime(playerAudio.duration)}`;
  }
  drawPlayerWave();
});

playerAudio.addEventListener('loadedmetadata', () => {
  playerTime.textContent = `0:00 / ${fmtTime(playerAudio.duration)}`;
  drawPlayerWave();
});

playerPlayBtn.addEventListener('click', () => {
  playerAudio.paused ? playerAudio.play() : playerAudio.pause();
});

// Click or drag on the waveform to seek
function seekPlayerTo(clientX) {
  if (!playerAudio.duration) return;
  const rect = playerWaveWrap.getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
  playerAudio.currentTime = frac * playerAudio.duration;
  drawPlayerWave();
}
playerWaveWrap.addEventListener('pointerdown', (e) => {
  playerWaveWrap.setPointerCapture(e.pointerId);
  seekPlayerTo(e.clientX);
});
playerWaveWrap.addEventListener('pointermove', (e) => {
  if (playerWaveWrap.hasPointerCapture(e.pointerId)) seekPlayerTo(e.clientX);
});

window.addEventListener('resize', () => { drawPlayerWave(); if (trim.wave) renderTrim(); });

playerVolume.addEventListener('input', () => {
  playerAudio.volume = playerVolume.value;
});

playerCloseBtn.addEventListener('click', () => {
  playerAudio.pause();
  playerAudio.src         = '';
  playerFileId            = null;
  playerBar.style.display = 'none';
  document.body.classList.remove('player-open');
});

// ----------------------------------------------------------------
// Trim / fade editor
// ----------------------------------------------------------------

const trimModalEl  = document.getElementById('trimModal');
const trimWave     = document.getElementById('trimWave');
const trimWaveWrap = document.getElementById('trimWaveWrap');
const trimWaveMsg  = document.getElementById('trimWaveMsg');
const trimStartIn  = document.getElementById('trimStart');
const trimEndIn    = document.getElementById('trimEnd');
const trimFadeIn   = document.getElementById('trimFadeIn');
const trimFadeOut  = document.getElementById('trimFadeOut');
const trimNameIn   = document.getElementById('trimName');
const trimInfo     = document.getElementById('trimInfo');
const trimPreviewBtn = document.getElementById('trimPreviewBtn');
const trimPlayBtn  = document.getElementById('trimPlayBtn');

const MIN_SELECTION = 0.1;   // seconds
const trim = {
  file: null, wave: null, start: 0, end: 0, playhead: 0,
  drag: null, anchor: 0, moved: false,
  audio: new Audio(), mode: null, raf: null,   // mode: 'preview' | 'play' | null
};
trim.audio.preload = 'auto';

const trimFades = () => ({
  fadeIn:  Math.max(0, parseFloat(trimFadeIn.value) || 0),
  fadeOut: Math.max(0, parseFloat(trimFadeOut.value) || 0),
});

function renderTrim() {
  if (!trim.wave) return;
  const { fadeIn, fadeOut } = trimFades();
  drawWave(trimWave, trim.wave, {
    selStart: trim.start, selEnd: trim.end, fadeIn, fadeOut, playhead: trim.playhead,
  });
  const len = trim.end - trim.start;
  trimInfo.textContent = `Selection ${fmtTimePrecise(trim.start)} – ${fmtTimePrecise(trim.end)}`
    + ` · length ${len.toFixed(1)} s · playhead ${fmtTimePrecise(trim.playhead)}`;
}

function syncTrimInputs() {
  trimStartIn.value = fmtTimePrecise(trim.start);
  trimEndIn.value   = fmtTimePrecise(trim.end);
}

function setTrimRange(start, end) {
  const dur = trim.wave ? trim.wave.duration : 0;
  start = Math.min(Math.max(0, start), dur);
  end   = Math.min(Math.max(0, end), dur);
  if (end - start < MIN_SELECTION) return false;
  trim.start = start;
  trim.end = end;
  syncTrimInputs();
  renderTrim();
  return true;
}

function openTrim(fileId) {
  const f = allFiles.find(x => x.id === fileId);
  if (!f) return;
  const dot = f.name.lastIndexOf('.');
  const base = dot > 0 ? f.name.slice(0, dot) : f.name;
  trim.file = f;
  trim.wave = null;
  trim.start = trim.end = trim.playhead = 0;
  document.getElementById('trimTitle').textContent = f.name;
  document.getElementById('trimExt').textContent = dot > 0 ? f.name.slice(dot) : '';
  trimNameIn.value = '';
  trimNameIn.placeholder = `${base}_trimmed`;
  trimFadeIn.value = 0;
  trimFadeOut.value = 0;
  trimStartIn.value = trimEndIn.value = '';
  trimInfo.textContent = '';
  trimWaveMsg.innerHTML = '<div class="spinner-border spinner-border-sm me-2"></div> Loading waveform…';
  trimWaveMsg.style.display = '';

  playerAudio.pause();   // don't play two things at once
  trim.audio.src = `/api/files/${fileId}/stream`;
  bootstrap.Modal.getOrCreateInstance(trimModalEl).show();

  loadPeaks(fileId, 1500).then(w => {
    if (trim.file !== f) return;
    trim.wave = w;
    trimWaveMsg.style.display = 'none';
    setTrimRange(0, w.duration);
  }).catch(() => {
    if (trim.file !== f) return;
    trimWaveMsg.textContent = 'Could not load the waveform for this file.';
  });
}

// Canvas pointer interaction: drag edges, drag out a new selection, or click
// to move the playhead.
function trimTimeAt(clientX) {
  const rect = trimWaveWrap.getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
  return frac * trim.wave.duration;
}
function trimEdgeAt(clientX) {
  const rect = trimWaveWrap.getBoundingClientRect();
  const px = t => rect.left + (t / trim.wave.duration) * rect.width;
  if (Math.abs(clientX - px(trim.start)) <= 8) return 'start';
  if (Math.abs(clientX - px(trim.end)) <= 8) return 'end';
  return null;
}

trimWaveWrap.addEventListener('pointerdown', (e) => {
  if (!trim.wave) return;
  trimWaveWrap.setPointerCapture(e.pointerId);
  trim.drag = trimEdgeAt(e.clientX) || 'new';
  trim.anchor = trimTimeAt(e.clientX);
  trim.anchorX = e.clientX;
  trim.moved = false;
});

trimWaveWrap.addEventListener('pointermove', (e) => {
  if (!trim.wave) return;
  if (!trim.drag) {
    trimWaveWrap.style.cursor = trimEdgeAt(e.clientX) ? 'ew-resize' : 'crosshair';
    return;
  }
  const t = trimTimeAt(e.clientX);
  if (trim.drag === 'start') {
    trim.moved = true;
    setTrimRange(Math.min(t, trim.end - MIN_SELECTION), trim.end);
  } else if (trim.drag === 'end') {
    trim.moved = true;
    setTrimRange(trim.start, Math.max(t, trim.start + MIN_SELECTION));
  } else if (Math.abs(e.clientX - trim.anchorX) > 3) {
    trim.moved = true;
    setTrimRange(Math.min(trim.anchor, t), Math.max(trim.anchor, t));
  }
});

trimWaveWrap.addEventListener('pointerup', () => {
  if (trim.drag === 'new' && !trim.moved) {
    trim.playhead = trim.anchor;
    if (trim.mode === 'play') trim.audio.currentTime = trim.playhead;
    renderTrim();
  }
  trim.drag = null;
});

for (const [input, which] of [[trimStartIn, 'start'], [trimEndIn, 'end']]) {
  input.addEventListener('change', () => {
    const t = parseTime(input.value);
    const ok = !isNaN(t) && (which === 'start' ? setTrimRange(t, trim.end) : setTrimRange(trim.start, t));
    if (!ok) {
      toast('Enter a time like 1:23.5 inside the file, keeping start before end.', 'warning');
      syncTrimInputs();
    }
  });
}
trimFadeIn.addEventListener('input', renderTrim);
trimFadeOut.addEventListener('input', renderTrim);

// Preview playback (fades simulated with the element's volume)
function stopTrimAudio() {
  trim.audio.pause();
  trim.audio.volume = 1;
  trim.mode = null;
  cancelAnimationFrame(trim.raf);
  trimPreviewBtn.innerHTML = '<i class="bi bi-play-fill me-1"></i>Preview selection';
  trimPlayBtn.innerHTML = '<i class="bi bi-play me-1"></i>Play from playhead';
}

function trimTick() {
  if (!trim.mode) return;
  const t = trim.audio.currentTime;
  trim.playhead = t;
  if (trim.mode === 'preview') {
    const { fadeIn, fadeOut } = trimFades();
    let vol = 1;
    if (fadeIn > 0 && t - trim.start < fadeIn) vol = (t - trim.start) / fadeIn;
    if (fadeOut > 0 && trim.end - t < fadeOut) vol = Math.min(vol, (trim.end - t) / fadeOut);
    trim.audio.volume = Math.min(1, Math.max(0, vol));
    if (t >= trim.end) { stopTrimAudio(); trim.playhead = trim.end; }
  }
  renderTrim();
  if (trim.mode) trim.raf = requestAnimationFrame(trimTick);
}

function startTrimAudio(mode, from) {
  stopTrimAudio();
  trim.mode = mode;
  trim.audio.currentTime = from;
  trim.audio.volume = mode === 'preview' && trimFades().fadeIn > 0 ? 0 : 1;
  trim.audio.play().then(() => { trim.raf = requestAnimationFrame(trimTick); })
    .catch(() => { stopTrimAudio(); toast('Could not play audio', 'danger'); });
  const stopHtml = '<i class="bi bi-stop-fill me-1"></i>Stop';
  (mode === 'preview' ? trimPreviewBtn : trimPlayBtn).innerHTML = stopHtml;
}

trim.audio.addEventListener('ended', stopTrimAudio);
trimPreviewBtn.addEventListener('click', () => {
  if (!trim.wave) return;
  trim.mode === 'preview' ? stopTrimAudio() : startTrimAudio('preview', trim.start);
});
trimPlayBtn.addEventListener('click', () => {
  if (!trim.wave) return;
  trim.mode === 'play' ? stopTrimAudio() : startTrimAudio('play', trim.playhead);
});

trimModalEl.addEventListener('shown.bs.modal', renderTrim);
trimModalEl.addEventListener('hidden.bs.modal', () => {
  stopTrimAudio();
  trim.audio.removeAttribute('src');
  trim.audio.load();
  trim.file = null;
  trim.wave = null;
});

document.getElementById('trimSaveBtn').addEventListener('click', async () => {
  if (!trim.wave || !trim.file) return;
  const { fadeIn, fadeOut } = trimFades();
  if (fadeIn + fadeOut > trim.end - trim.start) {
    toast('Fades are longer than the selection.', 'warning');
    return;
  }
  const btn = document.getElementById('trimSaveBtn');
  btn.disabled = true;
  const ok = await startFileJob(`/api/files/${trim.file.id}/trim`, {
    start: +trim.start.toFixed(3),
    end: +trim.end.toFixed(3),
    fade_in: fadeIn,
    fade_out: fadeOut,
    name: trimNameIn.value.trim(),
  });
  btn.disabled = false;
  if (ok) bootstrap.Modal.getInstance(trimModalEl).hide();
});

// ----------------------------------------------------------------
// Rename
// ----------------------------------------------------------------

const renameModalEl = document.getElementById('renameModal');
const renameInput   = document.getElementById('renameInput');
let renameFileId = null;

function openRename(fileId) {
  const f = allFiles.find(x => x.id === fileId);
  if (!f) return;
  const dot = f.name.lastIndexOf('.');
  renameFileId = fileId;
  renameInput.value = dot > 0 ? f.name.slice(0, dot) : f.name;
  document.getElementById('renameExt').textContent = dot > 0 ? f.name.slice(dot) : '';
  document.getElementById('renameHint').textContent = f.normalized
    ? 'Normalized copies keep their "_normalized" ending so they are never normalized twice.'
    : '';
  bootstrap.Modal.getOrCreateInstance(renameModalEl).show();
}

renameModalEl.addEventListener('shown.bs.modal', () => { renameInput.focus(); renameInput.select(); });

document.getElementById('renameForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('renameSaveBtn');
  btn.disabled = true;
  try {
    const res = await fetch(`/api/files/${renameFileId}/rename`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: renameInput.value }),
    });
    let data = {};
    try { data = await res.json(); } catch { /* non-JSON body */ }
    if (!res.ok || data.error) {
      toast(data.error || `Rename failed (${res.status})`, 'danger');
    } else {
      bootstrap.Modal.getInstance(renameModalEl).hide();
      toast(`Renamed to ${data.name}`, 'success');
      loadFileList({ silent: true });
    }
  } catch {
    toast('Network error', 'danger');
  } finally {
    btn.disabled = false;
  }
});

// ----------------------------------------------------------------
// Admin / system panel (Settings → System)
// ----------------------------------------------------------------

let adminPollTimer = null;

function fmtDateTime(iso) {
  return iso ? new Date(iso).toLocaleString() : '–';
}

async function loadAdminStatus() {
  clearTimeout(adminPollTimer);
  let st;
  try {
    const res = await fetch('/api/admin/status');
    if (!res.ok) throw new Error();
    st = await res.json();
  } catch {
    document.getElementById('adminEvents').innerHTML =
      '<div class="text-danger small">Could not load system status.</div>';
    return;
  }
  const set = (id, text) => { document.getElementById(id).textContent = text; };
  set('adminAppVersion', st.app_version);
  set('adminYtdlpVersion', st.ytdlp_version || 'not available');
  set('adminYtdlpUpdated', fmtDateTime(st.ytdlp_last_update));
  const u = st.ytdlp_update || {};
  set('adminAutoUpdate', u.auto_update
    ? `every ${u.interval_days} day(s) around ${String(u.update_hour).padStart(2, '0')}:00 (${u.channel})`
    : `off (${u.channel || 'nightly'} channel)`);
  set('adminDenoVersion', st.deno_version || 'not available');
  set('adminFfmpegVersion', (st.ffmpeg_version || 'not found').replace(/ Copyright.*/, ''));
  set('adminLibrary', `${st.library.files} file(s), ${humanSize(st.library.bytes)}`);
  set('adminActiveJobs', st.active_jobs);
  set('adminDownloadsDir', st.downloads_dir);
  if (st.disk) {
    const usedPct = (st.disk.used / st.disk.total) * 100;
    set('adminDiskFree', `${humanSize(st.disk.free)} of ${humanSize(st.disk.total)}`);
    const bar = document.getElementById('adminDiskBar');
    bar.style.width = `${usedPct.toFixed(1)}%`;
    bar.className = 'progress-bar' + (usedPct > 90 ? ' bg-danger' : usedPct > 75 ? ' bg-warning' : '');
  } else {
    set('adminDiskFree', 'unknown');
  }

  const updating = st.ytdlp_updating || st.manual_update.running;
  const updBtn = document.getElementById('adminUpdateBtn');
  updBtn.disabled = updating;
  const mu = st.manual_update;
  set('adminUpdateStatus', updating ? 'Updating…'
    : mu.result ? `Last manual update: ${mu.result} (${fmtDateTime(mu.finished_at)})` : '');

  const events = st.events || [];
  document.getElementById('adminEvents').innerHTML = events.length
    ? events.map(ev => `<div class="admin-event level-${escHtml(ev.level)}">
        <time>${escHtml(fmtDateTime(ev.time))}</time>${escHtml(ev.message)}</div>`).join('')
    : '<div class="text-muted small">No events since the service started.</div>';

  // Keep refreshing while an update runs and the panel is open
  if (updating && settingsModal.classList.contains('show')) {
    adminPollTimer = setTimeout(loadAdminStatus, 2000);
  }
}

document.getElementById('adminRefreshBtn').addEventListener('click', loadAdminStatus);
document.getElementById('adminUpdateBtn').addEventListener('click', async () => {
  try {
    const res = await fetch('/api/admin/update-ytdlp', { method: 'POST' });
    let data = {};
    try { data = await res.json(); } catch { /* non-JSON body */ }
    if (!res.ok || data.error) toast(data.error || `Update failed (${res.status})`, 'danger');
    else toast('yt-dlp update started…', 'info');
  } catch {
    toast('Network error', 'danger');
  }
  loadAdminStatus();
});
settingsModal.addEventListener('show.bs.modal', loadAdminStatus);
settingsModal.addEventListener('hidden.bs.modal', () => clearTimeout(adminPollTimer));

// ----------------------------------------------------------------
// Init
// ----------------------------------------------------------------
loadFileList();
restoreActiveJobs();

// Refresh file list every 30 s (other users may be downloading)
setInterval(() => loadFileList({ silent: true }), 30_000);
