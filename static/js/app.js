/* ============================================================
   Bark Extractor – Frontend Logic
   ============================================================ */

'use strict';

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

function renderDownloadItem(d) {
  const { job, logLines } = d;
  const statusCls = `status-${job.status}`;
  const isActive  = ['pending','running'].includes(job.status);
  const pct       = Math.min(100, Math.max(0, job.progress || 0)).toFixed(1);

  let barClass = 'progress-bar';
  if (job.status === 'completed') barClass += ' bg-success';
  else if (job.status === 'failed' || job.status === 'cancelled') barClass += ' bg-danger';

  const metaParts = [];
  if (job.speed)        metaParts.push(`<i class="bi bi-speedometer2"></i> ${escHtml(job.speed)}`);
  if (job.eta)          metaParts.push(`<i class="bi bi-clock"></i> ETA ${escHtml(job.eta)}`);
  if (job.current_file) metaParts.push(`<i class="bi bi-file-earmark-music"></i> ${escHtml(job.current_file)}`);

  return `
  <div class="download-item ${statusCls}" id="item-${job.download_id}">
    <div class="d-flex align-items-start gap-3">
      <div class="flex-grow-1 min-w-0">
        <div class="d-flex align-items-center gap-2 mb-1 flex-wrap">
          <span class="status-badge ${statusCls}">${job.status}</span>
          <span class="download-url flex-grow-1">${escHtml(job.url)}</span>
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
        ${isActive ? `<button class="btn btn-outline btn-stop" data-stop="${job.download_id}" title="Stop download">
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
  fetch(`/api/download/${downloadId}/stop`, { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.ok) toast('Download cancelled – cleaning up partial files…', 'warning');
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
  const modal = new bootstrap.Modal(document.getElementById('logModal'));
  modal.show();

  // Keep scrolled to bottom when log is live
  document.getElementById('logModal').addEventListener('shown.bs.modal', () => {
    pre.scrollTop = pre.scrollHeight;
  }, { once: true });
}

// ----------------------------------------------------------------
// SSE – subscribe to a download stream
// ----------------------------------------------------------------

function subscribeToDownload(downloadId) {
  const entry = { job: { download_id: downloadId, status: 'pending', progress: 0, url: '' }, logLines: [], sse: null };
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
        Object.assign(d.job, data);
        sse.close();
        renderActiveSection();
        // Reload file list so new MP3s appear immediately
        if (data.status === 'completed') {
          toast('Download complete! 🎵', 'success');
          loadFileList();
        } else if (data.status === 'cancelled') {
          toast('Download cancelled.', 'secondary');
        } else {
          toast(`Download failed: ${data.error || 'unknown error'}`, 'danger');
        }
        // Remove from map after a short delay so user can read the state
        setTimeout(() => {
          activeDownloads.delete(downloadId);
          renderActiveSection();
        }, 8000);
        break;

      case 'close':
        sse.close();
        break;
    }
  };

  sse.onerror = () => {
    const d = activeDownloads.get(downloadId);
    if (d && ['pending','running'].includes(d.job.status)) {
      // Poll status as fallback
      pollJobStatus(downloadId);
    }
    sse.close();
  };
}

function pollJobStatus(downloadId) {
  fetch(`/api/download/${downloadId}/status`)
    .then(r => r.json())
    .then(job => {
      const d = activeDownloads.get(downloadId);
      if (!d) return;
      d.job = job;
      renderActiveSection();
      if (['pending','running'].includes(job.status)) {
        setTimeout(() => pollJobStatus(downloadId), 1500);
      } else {
        if (job.status === 'completed') loadFileList();
        setTimeout(() => { activeDownloads.delete(downloadId); renderActiveSection(); }, 8000);
      }
    });
}

// ----------------------------------------------------------------
// Download Form
// ----------------------------------------------------------------

const form        = document.getElementById('downloadForm');
const urlInput    = document.getElementById('urlInput');
const qualitySel  = document.getElementById('qualitySelect');
const playlistChk = document.getElementById('playlistCheck');
const organizeRow = document.getElementById('organizeRow');
const organizeChk = document.getElementById('organizeCheck');
const downloadBtn = document.getElementById('downloadBtn');

playlistChk.addEventListener('change', () => {
  organizeRow.style.setProperty('display', playlistChk.checked ? '' : 'none', 'important');
});

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
        is_playlist:        playlistChk.checked,
        organize_playlist:  organizeChk.checked,
      }),
    });

    const data = await res.json();
    if (!res.ok || data.error) {
      toast(data.error || 'Failed to start download', 'danger');
    } else {
      // Pre-populate URL in the entry so it shows immediately
      const entry = activeDownloads.get(data.download_id);
      if (entry) entry.job.url = url;
      subscribeToDownload(data.download_id);
      urlInput.value = '';
      toast('Download queued! 🐾', 'success');
    }
  } catch {
    toast('Network error – could not reach server', 'danger');
  } finally {
    downloadBtn.disabled = false;
    downloadBtn.innerHTML = '<i class="bi bi-music-note-beamed me-1"></i> Extract MP3';
  }
});

// ----------------------------------------------------------------
// File Manager
// ----------------------------------------------------------------

let allFiles = [];

async function loadFileList() {
  const loading     = document.getElementById('filesLoading');
  const empty       = document.getElementById('filesEmpty');
  const tableWrap   = document.getElementById('filesTableWrap');
  const tbody       = document.getElementById('filesTbody');
  const fileCount   = document.getElementById('fileCount');

  loading.style.display = '';
  empty.style.display   = 'none';
  tableWrap.style.display = 'none';

  try {
    const res = await fetch('/api/files');
    allFiles = await res.json();
  } catch {
    toast('Could not load file list', 'danger');
    loading.style.display = 'none';
    return;
  }

  loading.style.display = 'none';
  fileCount.textContent = allFiles.length;

  if (allFiles.length === 0) {
    empty.style.display = '';
    updateSelectionToolbar();
    return;
  }

  tableWrap.style.display = '';
  tbody.innerHTML = allFiles.map(f => renderFileRow(f)).join('');

  // Wire row checkboxes
  tbody.querySelectorAll('.row-chk').forEach(chk => {
    chk.addEventListener('change', updateSelectionToolbar);
  });

  // Wire per-file download buttons
  tbody.querySelectorAll('[data-dl]').forEach(btn => {
    btn.addEventListener('click', () => downloadFile(btn.dataset.dl, btn.dataset.name));
  });

  // Wire per-file delete buttons
  tbody.querySelectorAll('[data-del]').forEach(btn => {
    btn.addEventListener('click', () => deleteFile(btn.dataset.del, btn.closest('tr')));
  });

  updateSelectionToolbar();
}

function renderFileRow(f) {
  const folder = f.folder && f.folder !== '.' ? `<span class="folder-badge">${escHtml(f.folder)}</span>` : '';
  return `
  <tr data-id="${f.id}">
    <td><input type="checkbox" class="form-check-input row-chk" value="${f.id}" /></td>
    <td>
      <div class="file-name" title="${escHtml(f.name)}">
        <i class="bi bi-file-earmark-music text-muted me-1"></i>${escHtml(f.name)}
      </div>
    </td>
    <td class="d-none d-md-table-cell">${folder}</td>
    <td class="d-none d-sm-table-cell text-end text-muted small">${escHtml(f.size_human)}</td>
    <td class="d-none d-lg-table-cell text-muted small">${escHtml(f.modified_human)}</td>
    <td class="text-end">
      <button class="btn btn-sm btn-outline-primary me-1" data-dl="${f.id}" data-name="${escHtml(f.name)}" title="Download to your computer">
        <i class="bi bi-download"></i>
      </button>
      <button class="btn btn-sm btn-outline-danger" data-del="${f.id}" title="Delete from server">
        <i class="bi bi-trash"></i>
      </button>
    </td>
  </tr>`;
}

function updateSelectionToolbar() {
  const checked = $$('.row-chk:checked');
  const delBtn  = document.getElementById('deleteSelectedBtn');
  const dlBtn   = document.getElementById('downloadSelectedBtn');

  if (checked.length > 0) {
    delBtn.style.display = '';
    dlBtn.style.display  = '';
    dlBtn.textContent    = `Download Selected (${checked.length})`;
  } else {
    delBtn.style.display = 'none';
    dlBtn.style.display  = 'none';
  }
}

function downloadFile(fileId, fileName) {
  // Trigger a browser download via a temporary <a>
  const a = document.createElement('a');
  a.href = `/api/files/${fileId}/download`;
  a.download = fileName || 'audio.mp3';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function deleteFile(fileId, rowEl) {
  if (!confirm('Delete this file from the server?')) return;
  try {
    const res = await fetch(`/api/files/${fileId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.ok) {
      rowEl.remove();
      allFiles = allFiles.filter(f => f.id !== fileId);
      document.getElementById('fileCount').textContent = allFiles.length;
      if (allFiles.length === 0) {
        document.getElementById('filesTableWrap').style.display = 'none';
        document.getElementById('filesEmpty').style.display     = '';
      }
      updateSelectionToolbar();
      toast('File deleted.', 'secondary');
    } else {
      toast(data.error || 'Could not delete file', 'danger');
    }
  } catch {
    toast('Network error', 'danger');
  }
}

// Select-all header checkbox
document.getElementById('selectAllChk').addEventListener('change', function () {
  $$('.row-chk').forEach(c => { c.checked = this.checked; });
  updateSelectionToolbar();
});

// Select-all toolbar button
document.getElementById('selectAllBtn').addEventListener('click', () => {
  const allChecked = $$('.row-chk').every(c => c.checked);
  $$('.row-chk').forEach(c => { c.checked = !allChecked; });
  document.getElementById('selectAllChk').checked = !allChecked;
  updateSelectionToolbar();
});

// Batch download selected
document.getElementById('downloadSelectedBtn').addEventListener('click', () => {
  const checked = $$('.row-chk:checked');
  checked.forEach(chk => {
    const f = allFiles.find(x => x.id === chk.value);
    if (f) {
      // Stagger downloads slightly so browser doesn't block them
      setTimeout(() => downloadFile(f.id, f.name), 100 * checked.indexOf(chk));
    }
  });
  toast(`Downloading ${checked.length} file(s)…`, 'info');
});

// Batch delete selected
document.getElementById('deleteSelectedBtn').addEventListener('click', async () => {
  const checked = $$('.row-chk:checked');
  if (!confirm(`Delete ${checked.length} file(s) from the server?`)) return;
  for (const chk of checked) {
    const row = chk.closest('tr');
    await deleteFile(chk.value, row);
  }
});

// Refresh button
document.getElementById('refreshBtn').addEventListener('click', () => loadFileList());

// ----------------------------------------------------------------
// Init
// ----------------------------------------------------------------
loadFileList();

// Refresh file list every 30 s (other users may be downloading)
setInterval(loadFileList, 30_000);
