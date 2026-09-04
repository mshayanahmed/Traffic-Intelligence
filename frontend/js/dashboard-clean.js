const API_BASE_URL = window.TI_API_BASE_URL || (
  (location.hostname === 'localhost' || location.hostname === '127.0.0.1')
    ? 'http://127.0.0.1:5000'
    : 'https://traffic-intelligence-lji9.onrender.com'
);
const get = path => fetch(API_BASE_URL + path);
const byId = id => document.getElementById(id);
const setText = (id, value) => { const node = byId(id); if (node) node.textContent = value; };
const percent = value => Number.isFinite(Number(value)) ? `${Math.round(Number(value) * 100)}%` : 'Unavailable';
const kmh = value => Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)} km/h` : 'Unavailable';
let rows = [];
let violations = [];
let page = 1;
let distribution;
let flow;
let state = {};
let reportReady = false;

function setFeedLive(isLive) {
  const feed = document.querySelector('.feed');
  if (feed) feed.classList.toggle('live', isLive);
}

function updateClock() {
  const now = new Date();
  setText('hudClock', now.toUTCString().slice(17, 25));
}

function setTable(hasVideo) {
  setFeedLive(hasVideo);
}

function updateReportStatus(hasData) {
  reportReady = hasData;
  const viewBtn = byId('reportView');
  if (viewBtn) viewBtn.disabled = !hasData;
  const badge = byId('reportBadge');
  if (badge) {
    badge.textContent = hasData ? 'READY' : 'STANDBY';
    badge.className = 'badge ' + (hasData ? 'green' : 'cyan');
  }
  const status = byId('reportStatus');
  if (status) {
    status.textContent = hasData
      ? 'Detection data is available. Reports can be generated now.'
      : 'Reports are generated on demand once frames have been processed.';
  }
}

function setLoadingFeed(loading) {
  const el = byId('feedLoading');
  if (el) el.classList.toggle('hidden', !loading);
  if (loading) byId('feedPlaceholder').classList.add('hidden');
}

function renderTable() {
  const query = byId('tableSearch').value.toLowerCase();
  const type = byId('typeFilter').value;
  const filtered = rows.filter(row => (!query || `${row.id} ${row.type}`.toLowerCase().includes(query)) && (!type || row.type === type));
  const pages = Math.max(1, Math.ceil(filtered.length / 8));
  page = Math.min(page, pages);
  const visible = filtered.slice((page - 1) * 8, page * 8);
  byId('vehicleTableBody').innerHTML = visible.length ? visible.map(row => `<tr tabindex="0"><td><b>${row.id || 'N/A'}</b></td><td>${row.type || 'N/A'}</td><td class="muted">Unavailable</td><td>${kmh(row.speed)}</td><td>${row.capture_time == null ? 'N/A' : Number(row.capture_time).toFixed(2) + ' s'}</td><td>${percent(row.confidence)}</td><td>${violations.some(item => item.vehicle_id === row.id) ? '<span class="severity">Speeding</span>' : '<span class="muted">None</span>'}</td><td><span class="status-chip">Detected</span></td></tr>`).join('') : '<tr><td colspan="8" class="table-empty">No detection data matches the current filters.</td></tr>';
  setText('tableCount', `${filtered.length} vehicle${filtered.length === 1 ? '' : 's'} shown`);
  setText('pageNumber', `${page} / ${pages}`);
  byId('prevPage').disabled = page === 1;
  byId('nextPage').disabled = page === pages;
}

function renderCharts(data, graph) {
  const labels = ['Car', 'Bike', 'Bus', 'Truck', 'Rickshaw'];
  const values = labels.map(label => Number(data?.[label] || 0));
  const total = values.reduce((a, b) => a + b, 0);
  byId('distributionEmpty').classList.toggle('hidden', total > 0);
  if (!distribution) distribution = new Chart(byId('distributionChart'), { type: 'doughnut', data: { labels, datasets: [{ data: values, backgroundColor: ['#27c4c9', '#b9e769', '#f5bb54', '#ef6b63', '#a98bdb'], borderWidth: 0 }] }, options: { cutout: '72%', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } } });
  else { distribution.data.datasets[0].data = values; distribution.update('none'); }
  byId('distributionLegend').innerHTML = labels.map((label, index) => `<div><i></i><span>${label}</span><b>${values[index]} <small>${total ? Math.round(values[index] / total * 100) : 0}%</small></b></div>`).join('');
  const timestamps = graph?.timestamps || [];
  byId('flowEmpty').classList.toggle('hidden', timestamps.length > 0);
  if (!flow) flow = new Chart(byId('flowChart'), { type: 'line', data: { labels: timestamps, datasets: [{ label: 'Vehicles', data: graph?.vehicle_counts || [], borderColor: '#27c4c9', fill: true, backgroundColor: 'rgba(39,196,201,.12)', tension: .35 }, { label: 'Estimated speed', data: graph?.avg_speeds || [], borderColor: '#ef6b63', tension: .35 }] }, options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false } } });
  else { flow.data.labels = timestamps; flow.data.datasets[0].data = graph?.vehicle_counts || []; flow.data.datasets[1].data = graph?.avg_speeds || []; flow.update('none'); }
}

async function refresh() {
  try {
    const responses = await Promise.all([get('/traffic_data'), get('/traffic_data_graph'), get('/traffic_violations'), get('/health'), get('/processing_status'), get('/latest_video')]);
    const data = await responses[0].json();
    const graph = await responses[1].json();
    violations = responses[2].ok ? (await responses[2].json()).violations || [] : [];
    const health = await responses[3].json();
    const status = await responses[4].json();
    const latestVideo = await responses[5].json();
    rows = data.vehicle_details || [];
    const count = Number(data.vehicle_count || rows.length || 0);
    const flowLabel = data.traffic_flow || (count >= 20 ? 'Heavy' : count >= 10 ? 'Moderate' : count ? 'Light' : 'Unavailable');
    const dominant = rows.length
      ? [...new Set(rows.map(r => r.type))].sort((a, b) => rows.filter(r => r.type === b).length - rows.filter(r => r.type === a).length)[0]
      : 'Unavailable';
    setText('statVehicleCount', count);
    setText('statAvgSpeed', data.avg_speed == null ? 'N/A' : Number(data.avg_speed).toFixed(1));
    setText('statDensity', data.traffic_density == null ? '0' : Number(data.traffic_density).toFixed(2));
    setText('statTrafficFlow', flowLabel);
    setText('statViolations', violations.length);
    setText('statFps', data.processing_fps == null ? 'N/A' : Number(data.processing_fps).toFixed(1));
    setText('hudFrame', data.current_frame == null ? 'N/A' : data.current_frame);
    setText('hudVehicles', count);
    setText('hudConfidence', data.average_confidence == null ? 'N/A' : percent(data.average_confidence));
    setText('hudFps', data.processing_fps == null ? 'N/A' : Number(data.processing_fps).toFixed(1));
    setText('sourceLabel', data.no_data ? 'Waiting for input' : 'Processed video');
    // Show/hide video placeholder based on actual video availability
    const hasVideo = !!latestVideo.video_url;
    byId('feedPlaceholder').classList.toggle('hidden', hasVideo);
    if (hasVideo) {
      const stream = byId('videoStream');
      if (stream && !stream.src.includes('video_feed')) stream.src = `${API_BASE_URL}/video_feed`;
      setLoadingFeed(false);
    } else {
      setLoadingFeed(true);
    }
    setTable(hasVideo);
    setText('insightCondition', flowLabel);
    setText('insightConditionNote', data.current_frame == null ? 'No processed frame available' : `Derived from frame ${data.current_frame}`);
    setText('insightVehicles', `${count} vehicles`);
    setText('insightConfidence', data.average_confidence == null ? 'Unavailable' : percent(data.average_confidence));
    setText('insightViolations', violations.length);
    setText('insightDominant', dominant);
    setText('healthBackend', health.data?.backend === 'connected' ? 'Connected' : 'Unavailable');
    setText('healthModel', health.data?.model_loaded ? 'Loaded' : 'Not loaded');
    setText('healthProcessor', status.processing ? 'Processing' : 'Idle');
    setText('systemStatus', health.success ? 'System online' : 'Backend unavailable');
    setText('lastUpdated', `Updated ${new Date().toLocaleTimeString()}`);
    byId('systemDot').className = 'dot ' + (health.success ? 'online' : 'offline');
    byId('feedBadge').textContent = status.processing ? 'PROCESSING' : (data.no_data ? 'IDLE' : 'READY');
    const heatmap = byId('heatmapImg');
    if (heatmap && state.lastHeatmapFrame !== data.current_frame && count > 0) {
      state.lastHeatmapFrame = data.current_frame;
      heatmap.src = `${API_BASE_URL}/live_heatmap?t=${Date.now()}`;
    }
    byId('heatmapEmpty').classList.toggle('hidden', count > 0);
    renderCharts(data.vehicle_distribution, graph);
    renderTable();
    updateReportStatus(count > 0 || rows.length > 0 || violations.length > 0);
    if (violations.length) {
      setText('violationBadge', `${violations.length} FLAGGED`);
      byId('violationsContent').className = 'violation-list';
      byId('violationsContent').innerHTML = violations.slice(-12).reverse().map(item => `<div><span class="severity">DETECTED</span><b>${item.vehicle_id}</b><span>${item.violation || 'Speeding'}</span><span>${kmh(item.max_speed)}</span><span>${item.first_detected_at == null ? 'N/A' : item.first_detected_at + 's - ' + item.last_detected_at + 's'}</span></div>`).join('');
    }
    byId('errorAlert').classList.add('hidden');
  } catch (_) {
    rows = []; violations = [];
    setText('systemStatus', 'Backend unavailable');
    setText('healthBackend', 'Unavailable');
    setText('healthModel', 'Unavailable');
    setText('healthProcessor', 'Unavailable');
    setText('statVehicleCount', 'N/A');
    setText('statAvgSpeed', 'N/A');
    setText('statDensity', 'N/A');
    setText('statTrafficFlow', 'N/A');
    setText('statViolations', 'N/A');
    setText('statFps', 'N/A');
    setText('hudFrame', 'N/A');
    setText('hudVehicles', 'N/A');
    setText('hudConfidence', 'N/A');
    setText('hudFps', 'N/A');
    setText('hudState', 'OFFLINE');
    setText('sourceLabel', 'Backend unavailable');
    setText('insightCondition', 'N/A');
    setText('insightConditionNote', 'Backend unavailable');
    setText('insightVehicles', 'N/A');
    setText('insightConfidence', 'N/A');
    setText('insightViolations', 'N/A');
    setText('insightDominant', 'N/A');
    byId('systemDot').className = 'dot offline';
    setText('violationBadge', 'NORMAL MONITORING');
    byId('feedBadge').textContent = 'OFFLINE';
    byId('vehicleTableBody').innerHTML = '<tr><td colspan="8" class="table-empty">No detection data available.</td></tr>';
    setText('tableCount', '0 vehicles shown');
    setText('pageNumber', '1 / 1');
    byId('violationsContent').className = 'violation-empty';
    byId('violationsContent').innerHTML = '<b>No violations available</b><span>The backend is currently unreachable.</span>';
    byId('distributionEmpty').classList.remove('hidden');
    byId('flowEmpty').classList.remove('hidden');
    byId('heatmapEmpty').classList.remove('hidden');
    updateReportStatus(false);
    setLoadingFeed(false);
    byId('feedPlaceholder').classList.remove('hidden');
    byId('errorAlert').textContent = 'Unable to reach the traffic backend. Start Flask and retry.';
    byId('errorAlert').classList.remove('hidden');
  }
}

async function upload(event) {
  event.preventDefault();
  const file = byId('file').files[0];
  if (!file) { setText('uploadStatus', 'Choose a video file first'); return; }
  byId('uploadBtn').disabled = true;
  byId('progressTrack').classList.remove('hidden');
  setText('uploadStatus', 'Uploading and starting analysis...');
  byId('feedPlaceholder').classList.add('hidden');
  setLoadingFeed(true);
  const body = new FormData();
  body.append('video', file);
  try {
    const response = await fetch(API_BASE_URL + '/upload', { method: 'POST', body });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || 'Upload failed');
    setText('uploadStatus', 'Processing started. Progress is indeterminate.');
    poll(result.job_id);
  } catch (error) {
    setText('uploadStatus', error.message);
    byId('uploadBtn').disabled = false;
    byId('progressTrack').classList.add('hidden');
    setLoadingFeed(false);
    byId('feedPlaceholder').classList.remove('hidden');
  }
}

function showSelectedFile(file) {
  if (!file) return;
  byId('fileSummary').classList.remove('hidden');
  setText('fileName', file.name);
  setText('fileDetails', `${(file.size / 1048576).toFixed(1)} MB | ${file.type || 'video'}`);
  setText('uploadStatus', 'Ready to analyze');
}

async function poll(jobId) {
  const job = await (await get(`/processing_status/${jobId}`)).json();
  setText('uploadStatus', job.status === 'processing' ? `Processing frame ${job.frames_processed || 0}...` : `Analysis ${job.status}`);
  if (job.status === 'processing') return setTimeout(() => poll(jobId), 2000);
  byId('uploadBtn').disabled = false;
  byId('progressTrack').classList.add('hidden');
  refresh();
}

function openFilePicker() { byId('file').click(); }

async function openReport() {
  const btn = byId('reportGenerate');
  const status = byId('reportStatus') || { textContent: '' };
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Generating…';
  status.textContent = 'Contacting backend to generate the report…';
  try {
    await Promise.all([
      fetch(API_BASE_URL + '/traffic_graph').then(r => r.ok ? r : null),
      fetch(API_BASE_URL + '/vehicle_distribution_chart').then(r => r.ok ? r : null),
      fetch(API_BASE_URL + '/download/traffic_report_combined.csv')
    ]);
    byId('downloadCsvBtn').href = `${API_BASE_URL}/download/traffic_report_combined.csv`;
    byId('downloadPdfBtn').href = `${API_BASE_URL}/download/traffic_report_combined.pdf`;
    byId('downloadXlsxBtn').href = `${API_BASE_URL}/download/traffic_report_combined.xlsx`;
    byId('downloadCsvBtn').setAttribute('download', '');
    byId('downloadPdfBtn').setAttribute('download', '');
    byId('downloadXlsxBtn').setAttribute('download', '');
    reportReady = true;
    byId('reportView').disabled = false;
    byId('reportBadge').textContent = 'GENERATED';
    byId('reportBadge').className = 'badge green';
    status.textContent = 'Report generated successfully. Use the download buttons above.';
  } catch (error) {
    status.textContent = 'Report generation failed — the backend may not have processed frames yet.';
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  byId('uploadForm').onsubmit = upload;
  byId('file').onchange = () => showSelectedFile(byId('file').files[0]);
  byId('startProcessing').onclick = openFilePicker;
  byId('placeholderUploadBtn').onclick = openFilePicker;
  byId('stopProcessing').onclick = async () => {
    const result = await (await fetch(API_BASE_URL + '/stop_processing', { method: 'POST' })).json();
    setText('uploadStatus', result.message || 'Stopping processing');
  };
  byId('restartProcessing').onclick = () => {
    byId('file').value = '';
    byId('fileSummary').classList.add('hidden');
    setText('uploadStatus', 'No file selected');
    byId('feedPlaceholder').classList.remove('hidden');
    setLoadingFeed(false);
    const stream = byId('videoStream');
    if (stream) stream.src = '';
    setTable(false);
  };
  byId('fullscreenFeed').onclick = () => byId('videoStream').requestFullscreen?.();
  byId('tableSearch').oninput = () => { page = 1; renderTable(); };
  byId('typeFilter').onchange = () => { page = 1; renderTable(); };
  byId('prevPage').onclick = () => { page--; renderTable(); };
  byId('nextPage').onclick = () => { page++; renderTable(); };
  byId('downloadCSV').onclick = () => { location.href = API_BASE_URL + '/download/traffic_report_combined.csv'; };
  byId('themeToggle').onclick = () => {
    const dark = document.body.dataset.theme !== 'dark';
    document.body.dataset.theme = dark ? 'dark' : 'light';
    localStorage.setItem('traffic-theme', dark ? 'dark' : 'light');
  };
  // Report Center
  byId('reportGenerate').onclick = openReport;
  byId('reportView').onclick = () => window.open('report.html', '_blank');
  // Drag & drop support
  const dropzone = byId('dropzone');
  if (dropzone) {
    dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragging'); });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragging'));
    dropzone.addEventListener('drop', e => {
      e.preventDefault();
      dropzone.classList.remove('dragging');
      const files = e.dataTransfer?.files;
      if (files && files[0]) {
        const dt = new DataTransfer();
        dt.items.add(files[0]);
        byId('file').files = dt.files;
        showSelectedFile(files[0]);
      }
    });
  }
  if (localStorage.getItem('traffic-theme') === 'dark') document.body.dataset.theme = 'dark';
  updateClock();
  setInterval(updateClock, 1000);
  refresh();
  setInterval(refresh, 5000);
});