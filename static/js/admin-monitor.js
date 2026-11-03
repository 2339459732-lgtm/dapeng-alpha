/**
 * API Call Monitor — admin-monitor.js (v5.1)
 *
 * Provides the frontend logic for the "调用追踪" page in admin.html.
 * Fetches data from /api/admin/stats, /api/admin/calls, /api/admin/error-ranking
 * and renders stat cards, error ranking table, and paginated calls detail table.
 *
 * Polling: auto-refresh every 30 seconds while the page is active.
 */

// ================================================================
//  State
// ================================================================

var monitorState = {
  currentPage: 1,
  pageSize: 50,
  timeRange: '24h',
  filterEndpoint: '',
  pollingTimer: null,
  initialized: false,
};

// ================================================================
//  Time Range Helpers
// ================================================================

/**
 * Convert a time-range selector value to an ISO 8601 UTC start timestamp.
 * @param {string} range - One of: '1h', '24h', '7d', '30d'
 * @returns {string} ISO 8601 UTC string, or empty string if range is unknown
 */
function _monitorRangeToStart(range) {
  var now = new Date();
  var start = new Date();
  switch (range) {
    case '1h':   start.setHours(now.getHours() - 1); break;
    case '24h':  start.setDate(now.getDate() - 1); break;
    case '7d':   start.setDate(now.getDate() - 7); break;
    case '30d':  start.setDate(now.getDate() - 30); break;
    default: return '';
  }
  return start.toISOString();
}

/**
 * Get the current time range {start, end} based on the selector.
 * @returns {{start: string, end: string}}
 */
function _monitorGetTimeParams() {
  var start = _monitorRangeToStart(monitorState.timeRange);
  var end = new Date().toISOString();
  return { start: start, end: end };
}

// ================================================================
//  Initialization
// ================================================================

/**
 * Initialize the API monitor page — called by switchPage('api-monitor').
 * Sets up the time-range selector, loads initial data, and starts polling.
 */
function initMonitor() {
  // Reset to first page
  monitorState.currentPage = 1;
  monitorState.filterEndpoint = '';

  // Restore selector state (in case returning to the page)
  var rangeSelect = document.getElementById('monitor-time-range');
  if (rangeSelect) {
    rangeSelect.value = monitorState.timeRange;
  }
  var endpointSelect = document.getElementById('monitor-filter-endpoint');
  if (endpointSelect) {
    endpointSelect.value = '';
  }

  // Fetch initial data
  refreshMonitor();

  // Start 30-second polling
  startMonitorPolling();

  monitorState.initialized = true;
}

/**
 * Start the 30-second auto-refresh polling timer.
 */
function startMonitorPolling() {
  stopMonitorPolling();
  monitorState.pollingTimer = setInterval(function() {
    refreshMonitor();
  }, 30000);
}

/**
 * Stop the auto-refresh polling timer.
 * Called when leaving the api-monitor page.
 */
function stopMonitorPolling() {
  if (monitorState.pollingTimer) {
    clearInterval(monitorState.pollingTimer);
    monitorState.pollingTimer = null;
  }
}

/**
 * Handle time-range selector change — reload all data from page 1.
 */
function onMonitorTimeRangeChange() {
  var select = document.getElementById('monitor-time-range');
  if (select) {
    monitorState.timeRange = select.value;
  }
  monitorState.currentPage = 1;
  refreshMonitor();
}

/**
 * Handle endpoint filter change — reload calls table from page 1.
 */
function onMonitorEndpointFilter() {
  var select = document.getElementById('monitor-filter-endpoint');
  if (select) {
    monitorState.filterEndpoint = select.value;
  }
  monitorState.currentPage = 1;
  loadMonitorCalls();
}

// ================================================================
//  Main Refresh — fetch all 3 endpoints
// ================================================================

/**
 * Refresh all monitor data: stats, error ranking, and calls table.
 */
async function refreshMonitor() {
  var timeParams = _monitorGetTimeParams();

  // Fire all requests in parallel
  await Promise.all([
    loadMonitorStats(timeParams),
    loadMonitorErrorRanking(timeParams),
    loadMonitorCalls(timeParams),
  ]);
}

// ================================================================
//  Stats Cards
// ================================================================

/**
 * Fetch /api/admin/stats and render 4 stat cards.
 * @param {{start: string, end: string}} timeParams
 */
async function loadMonitorStats(timeParams) {
  var params = timeParams || _monitorGetTimeParams();
  var url = '/api/admin/stats?start=' + encodeURIComponent(params.start) +
            '&end=' + encodeURIComponent(params.end);

  try {
    var resp = await fetch(url);
    var json = await resp.json();
    if (!json.ok) return;
    renderMonitorStats(json.data);
  } catch (err) {
    console.error('Monitor stats error:', err);
  }
}

/**
 * Render the 4 stat cards.
 * @param {Object} data - {total_calls, success_count, error_count, success_rate, avg_response_ms}
 */
function renderMonitorStats(data) {
  var el = document.getElementById('monitor-stats');
  if (!el) return;

  var total = (data.total_calls || 0).toLocaleString();
  var rate = (data.success_rate || 0).toFixed(1);
  var avg = (data.avg_response_ms || 0).toFixed(1);
  var errors = (data.error_count || 0).toLocaleString();

  // Color: green if rate >= 95, gold if >= 80, rose if < 80
  var rateColor = data.success_rate >= 95 ? 'var(--green)' :
                  data.success_rate >= 80 ? 'var(--gold)' : 'var(--red)';

  el.innerHTML = [
    '<div class="stat-card blue">' +
      '<div class="stat-card-top"><div class="stat-label">总调用</div>' +
      '<div class="stat-icon blue"><i class="fa-solid fa-arrow-trend-up"></i></div></div>' +
      '<div class="stat-value" style="color:var(--blue)">' + total + '</div>' +
      '<div class="stat-sub">时间范围内</div>' +
    '</div>',
    '<div class="stat-card green">' +
      '<div class="stat-card-top"><div class="stat-label">成功率</div>' +
      '<div class="stat-icon green"><i class="fa-solid fa-check-circle"></i></div></div>' +
      '<div class="stat-value" style="color:' + rateColor + '">' + rate + '%</div>' +
      '<div class="stat-sub">成功 ' + (data.success_count || 0).toLocaleString() + ' 次</div>' +
    '</div>',
    '<div class="stat-card cyan">' +
      '<div class="stat-card-top"><div class="stat-label">平均响应</div>' +
      '<div class="stat-icon cyan"><i class="fa-solid fa-gauge-high"></i></div></div>' +
      '<div class="stat-value" style="color:var(--cyan)">' + avg + ' ms</div>' +
      '<div class="stat-sub">全端点平均</div>' +
    '</div>',
    '<div class="stat-card rose">' +
      '<div class="stat-card-top"><div class="stat-label">错误数</div>' +
      '<div class="stat-icon rose"><i class="fa-solid fa-triangle-exclamation"></i></div></div>' +
      '<div class="stat-value" style="color:var(--red)">' + errors + '</div>' +
      '<div class="stat-sub">非2xx状态码</div>' +
    '</div>',
  ].join('');
}

// ================================================================
//  Error Ranking Table
// ================================================================

/**
 * Fetch /api/admin/error-ranking and render the TOP 10 error ranking table.
 * @param {{start: string, end: string}} timeParams
 */
async function loadMonitorErrorRanking(timeParams) {
  var params = timeParams || _monitorGetTimeParams();
  var url = '/api/admin/error-ranking?start=' + encodeURIComponent(params.start) +
            '&end=' + encodeURIComponent(params.end);

  try {
    var resp = await fetch(url);
    var json = await resp.json();
    if (!json.ok) return;
    renderMonitorErrorRanking(json.data || []);
    updateMonitorEndpointFilter(json.data || []);
  } catch (err) {
    console.error('Monitor error-ranking error:', err);
  }
}

/**
 * Render the error ranking table rows.
 * @param {Array} items - [{endpoint, total_calls, error_count, error_rate, avg_response_ms}]
 */
function renderMonitorErrorRanking(items) {
  var tbody = document.getElementById('monitor-error-ranking');
  if (!tbody) return;

  if (items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text2);padding:24px">' +
      '<i class="fa-solid fa-inbox text-2xl mb-2 block opacity-20"></i>暂无数据</td></tr>';
    return;
  }

  var rows = items.map(function(item) {
    var rateColor = item.error_rate >= 10 ? 'var(--red)' :
                    item.error_rate >= 5  ? 'var(--gold)' : 'var(--green)';
    var rateBar = '<div style="display:flex;align-items:center;gap:6px">' +
      '<span style="font-weight:600;color:' + rateColor + '">' + item.error_rate.toFixed(2) + '%</span>' +
      '<div style="width:60px;height:4px;background:rgba(255,255,255,0.05);border-radius:2px;overflow:hidden">' +
        '<div style="width:' + Math.min(item.error_rate, 100) + '%;height:100%;background:' + rateColor + ';border-radius:2px"></div>' +
      '</div></div>';

    return '<tr>' +
      '<td style="font-family:monospace;font-size:11px">' + _escapeHtml(item.endpoint) + '</td>' +
      '<td>' + (item.total_calls || 0).toLocaleString() + '</td>' +
      '<td style="color:var(--red)">' + (item.error_count || 0) + '</td>' +
      '<td>' + rateBar + '</td>' +
      '<td>' + (item.avg_response_ms || 0).toFixed(1) + ' ms</td>' +
    '</tr>';
  }).join('');

  tbody.innerHTML = rows;
}

/**
 * Update the endpoint filter dropdown with endpoints from error ranking data.
 * Preserves the current selection if possible.
 * @param {Array} items - Error ranking items with endpoint field
 */
function updateMonitorEndpointFilter(items) {
  var select = document.getElementById('monitor-filter-endpoint');
  if (!select) return;

  var currentVal = select.value;
  var endpoints = items.map(function(i) { return i.endpoint; });

  // Build options
  var html = '<option value="">全部端点</option>';
  endpoints.forEach(function(ep) {
    html += '<option value="' + _escapeHtml(ep) + '"' +
            (ep === currentVal ? ' selected' : '') + '>' +
            _escapeHtml(ep) + '</option>';
  });
  select.innerHTML = html;
}

// ================================================================
//  Calls Detail Table
// ================================================================

/**
 * Fetch /api/admin/calls and render the paginated calls detail table.
 * @param {{start: string, end: string}} timeParams
 */
async function loadMonitorCalls(timeParams) {
  var params = timeParams || _monitorGetTimeParams();
  var url = '/api/admin/calls?page=' + monitorState.currentPage +
            '&size=' + monitorState.pageSize +
            '&start=' + encodeURIComponent(params.start) +
            '&end=' + encodeURIComponent(params.end);

  if (monitorState.filterEndpoint) {
    url += '&endpoint=' + encodeURIComponent(monitorState.filterEndpoint);
  }

  try {
    var resp = await fetch(url);
    var json = await resp.json();
    if (!json.ok) return;
    renderMonitorCallsTable(json.data || { items: [], total: 0, page: 1, size: 50 });
  } catch (err) {
    console.error('Monitor calls error:', err);
  }
}

/**
 * Render the calls detail table rows and pagination controls.
 * @param {Object} data - {total, page, size, items: [...]}
 */
function renderMonitorCallsTable(data) {
  var tbody = document.getElementById('monitor-calls-table');
  if (!tbody) return;

  var items = data.items || [];

  if (items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text2);padding:24px">' +
      '<i class="fa-solid fa-inbox text-2xl mb-2 block opacity-20"></i>暂无调用记录</td></tr>';
  } else {
    tbody.innerHTML = items.map(function(item) {
      var isSuccess = item.is_success === 1 || item.is_success === true;
      var statusBadge = isSuccess
        ? '<span class="badge badge-green">' + item.status_code + '</span>'
        : '<span class="badge badge-rose">' + item.status_code + '</span>';

      // Format timestamp to local readable time
      var ts = item.timestamp || '';
      var timeDisplay = ts;
      try {
        var d = new Date(ts);
        if (!isNaN(d.getTime())) {
          timeDisplay = d.toLocaleString('zh-CN', {
            month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit',
          });
        }
      } catch (e) { /* keep original */ }

      // Color response time: green < 100ms, cyan < 500ms, gold < 1s, red >= 1s
      var ms = item.response_time_ms || 0;
      var msColor = ms < 100 ? 'var(--green)' :
                    ms < 500 ? 'var(--cyan)' :
                    ms < 1000 ? 'var(--gold)' : 'var(--red)';

      // Truncate params summary
      var params = item.params_summary || '';
      if (params.length > 50) params = params.substring(0, 50) + '...';

      return '<tr>' +
        '<td style="font-size:11px;white-space:nowrap">' + _escapeHtml(timeDisplay) + '</td>' +
        '<td style="font-family:monospace;font-size:11px">' + _escapeHtml(item.endpoint || '') + '</td>' +
        '<td><span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(255,255,255,0.05)">' +
          _escapeHtml(item.method || '') + '</span></td>' +
        '<td>' + statusBadge + '</td>' +
        '<td style="color:' + msColor + ';font-weight:600">' + ms.toFixed(1) + '</td>' +
        '<td style="font-size:10px;color:var(--text2);max-width:200px;overflow:hidden;text-overflow:ellipsis">' +
          _escapeHtml(params) + '</td>' +
      '</tr>';
    }).join('');
  }

  // Update pagination
  var total = data.total || 0;
  var page = data.page || 1;
  var size = data.size || 50;
  var totalPages = Math.ceil(total / size);

  var info = document.getElementById('monitor-pagination-info');
  if (info) {
    var startIdx = total > 0 ? (page - 1) * size + 1 : 0;
    var endIdx = Math.min(page * size, total);
    info.textContent = '第 ' + startIdx + '-' + endIdx + ' 条，共 ' + total.toLocaleString() + ' 条';
  }

  var prevBtn = document.getElementById('monitor-prev-btn');
  var nextBtn = document.getElementById('monitor-next-btn');
  if (prevBtn) prevBtn.disabled = page <= 1;
  if (nextBtn) nextBtn.disabled = page >= totalPages;
}

// ================================================================
//  Pagination Controls
// ================================================================

/**
 * Go to the previous page of calls detail.
 */
function monitorPrevPage() {
  if (monitorState.currentPage > 1) {
    monitorState.currentPage--;
    loadMonitorCalls();
  }
}

/**
 * Go to the next page of calls detail.
 */
function monitorNextPage() {
  monitorState.currentPage++;
  loadMonitorCalls();
}

// ================================================================
//  Utilities
// ================================================================

/**
 * Escape HTML special characters to prevent XSS in table cells.
 * @param {string} str
 * @returns {string}
 */
function _escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
