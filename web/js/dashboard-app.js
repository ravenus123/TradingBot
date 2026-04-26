// dashboard logic
// NOTE: perfModeEnabled, enablePerfMode, shouldEnablePerfMode, runPerfProbe
// are declared in main.js which loads first — do NOT redeclare here.

const API_BASE_URL = 'http://localhost:5000/api';
let equityChart = null;
let startStopPendingAction = null;
let statusSnapshot = null;
let heartbeatTicker = null;

// backtest animation state
let btChart = null;
let btSeries = null;
let btAnimTimer = null;
let btPaused = false;
let btStopped = false;
let btCandles = [];
let btTrades = [];
let btMarkers = [];
let btIdx = 0;
let btStats = { trades: 0, wins: 0, pnl: 0, balance: 10000 };
let btSlLine = null;
let btTpLine = null;
let btEntryLine = null;
let btTradeLines = [];
let btManualPanUntil = 0;
let btEquityChart = null;
let btRunInProgress = false;
let btLiveEquityCurve = [];
let btPairAnalyticsRows = [];
let btReportSymbol = 'EURUSD';
let btReportDays = 60;
let btGrossProfit = 0;
let btGrossLoss = 0;
let btEquitySampleCounter = 0;

function getToken() {
    // Support both old and new key
    return localStorage.getItem('zenith_token') || localStorage.getItem('ultima_token');
}

function requireAuth() {
    if (!getToken()) window.location.href = 'login.html';
}

async function apiFetch(path, options = {}) {
    const token = getToken();
    const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    if (token) headers['Authorization'] = `Bearer ${token}`;

    try {
        const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
        if (response.status === 401) {
            localStorage.removeItem('zenith_token');
            localStorage.removeItem('ultima_token');
            window.location.href = 'login.html';
            return null;
        }
        if (!response.ok) return null;
        return await response.json();
    } catch (e) {
        console.error('API error:', path, e);
        return null;
    }
}

async function apiFetchWithMeta(path, options = {}) {
    const token = getToken();
    const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    if (token) headers['Authorization'] = `Bearer ${token}`;

    try {
        const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
        if (response.status === 401) {
            localStorage.removeItem('zenith_token');
            localStorage.removeItem('ultima_token');
            window.location.href = 'login.html';
            return { ok: false, status: 401, data: null };
        }

        let data = null;
        try {
            data = await response.json();
        } catch (_) {
            data = null;
        }
        return { ok: response.ok, status: response.status, data };
    } catch (e) {
        console.error('API error:', path, e);
        return { ok: false, status: 0, data: null };
    }
}

// MT5 help popup removed — bot runs independently on local machine
function isMt5UnavailableError() { return false; }
function openMt5HelpPopup() {}
function closeMt5HelpPopup() {}

function setStatusText(el, message, isError = false) {
    if (!el) return;
    el.textContent = message;
    el.style.color = isError ? '#ff6b6b' : '#6b7a8d';
}

let botConfigCache = null;

function num(v, digits = 2) {
    const n = Number(v || 0);
    return Number.isFinite(n) ? n.toFixed(digits) : '0.00';
}

function formatTime(value) {
    if (!value) return '—';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function getAgeSeconds(value) {
    if (!value) return null;
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return null;
    return Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
}

function renderStartStopButton({ isRunning = false, loading = false, action = null } = {}) {
    const btn = document.getElementById('startStopBtn');
    if (!btn) return;

    btn.classList.add('start-stop-btn');
    const icon = isRunning ? 'stop' : 'play';
    const label = isRunning ? 'Stop Bot' : 'Start Bot';
    const loadingLabel = action === 'stop' ? 'Stopping…' : 'Starting…';

    btn.innerHTML = `
        <span class="btn-icon"><i class="fas fa-${icon}"></i></span>
        <span class="btn-label">${loading ? loadingLabel : label}</span>
        <span class="btn-loader" aria-hidden="true"></span>
    `;

    btn.classList.toggle('is-loading', loading);
    btn.classList.toggle('is-stealth-loading', loading);
    if (loading) btn.setAttribute('aria-busy', 'true');
    else btn.removeAttribute('aria-busy');
}

function tickLiveStatusClock() {
    if (!statusSnapshot) return;

    const botLastUpdate = document.getElementById('botLastUpdate');
    const heartbeat = document.getElementById('botHeartbeat');
    const processStatus = document.getElementById('botProcessStatus');

    const statusAge = getAgeSeconds(statusSnapshot.timestamp);
    if (botLastUpdate) {
        if (statusSnapshot.timestamp && statusAge !== null) {
            botLastUpdate.textContent = `${formatTime(statusSnapshot.timestamp)} · ${statusAge}s ago`;
        } else {
            botLastUpdate.textContent = '—';
        }
    }

    const hbSource = statusSnapshot.last_scan_time || statusSnapshot.timestamp;
    const hbAge = getAgeSeconds(hbSource);
    if (heartbeat) {
        if (hbSource && hbAge !== null) {
            heartbeat.textContent = `${formatTime(hbSource)} · ${hbAge}s ago`;
        } else {
            heartbeat.textContent = '—';
        }
    }

    if (processStatus && statusSnapshot.process_alive) {
        if (statusSnapshot.last_scan_time && hbAge !== null) {
            processStatus.textContent = `Active · last scan ${hbAge}s ago`;
        } else {
            processStatus.textContent = 'Active';
        }
    }
}

function startHeartbeatTicker() {
    if (heartbeatTicker) return;
    heartbeatTicker = setInterval(tickLiveStatusClock, 1000);
}

// render backtest results
function renderRunResult(result) {
    if (!result) return 'No result data yet.';

    if (result.mode === 'walk_forward') {
        return `<div class="result-grid">
            <div class="result-item"><span class="label">Mode</span><span class="value">Walk-Forward</span></div>
            <div class="result-item"><span class="label">Periods</span><span class="value">${result.total_periods || 0}</span></div>
            <div class="result-item"><span class="label">Profitable</span><span class="value">${result.profitable_periods || 0}</span></div>
            <div class="result-item"><span class="label">Consistency</span><span class="value">${num(result.consistency)}%</span></div>
            <div class="result-item"><span class="label">Avg Profit / Period</span><span class="value">$${num(result.average_profit_per_period)}</span></div>
        </div>`;
    }

    if (result.mode === 'split') {
        const m = result.test?.metrics || {};
        return `<div class="result-grid">
            <div class="result-item"><span class="label">Mode</span><span class="value">Split (${num(result.split_ratio, 2)})</span></div>
            <div class="result-item"><span class="label">Test Trades</span><span class="value">${m.total_trades || 0}</span></div>
            <div class="result-item"><span class="label">Test Win Rate</span><span class="value">${num(m.win_rate)}%</span></div>
            <div class="result-item"><span class="label">Test Profit</span><span class="value">$${num(m.total_profit)}</span></div>
            <div class="result-item"><span class="label">Profit Factor</span><span class="value">${num(m.profit_factor)}</span></div>
        </div>`;
    }

    if (result.mode === 'monte_carlo') {
        const mc = result.monte_carlo || {};
        const sm = result.standard?.metrics || {};
        return `<div class="result-grid">
            <div class="result-item"><span class="label">Mode</span><span class="value">Monte Carlo</span></div>
            <div class="result-item"><span class="label">Iterations</span><span class="value">${mc.iterations || 0}</span></div>
            <div class="result-item"><span class="label">P10 Balance</span><span class="value">$${num(mc.p10)}</span></div>
            <div class="result-item"><span class="label">P50 Balance</span><span class="value">$${num(mc.p50)}</span></div>
            <div class="result-item"><span class="label">P90 Balance</span><span class="value">$${num(mc.p90)}</span></div>
            <div class="result-item"><span class="label">Base Profit</span><span class="value">$${num(sm.total_profit)}</span></div>
        </div>`;
    }

    const m = result.metrics || {};
    return `<div class="result-grid">
        <div class="result-item"><span class="label">Mode</span><span class="value">Standard</span></div>
        <div class="result-item"><span class="label">Trades</span><span class="value">${m.total_trades || 0}</span></div>
        <div class="result-item"><span class="label">Win Rate</span><span class="value">${num(m.win_rate)}%</span></div>
        <div class="result-item"><span class="label">Total Profit</span><span class="value">$${num(m.total_profit)}</span></div>
        <div class="result-item"><span class="label">Profit Factor</span><span class="value">${num(m.profit_factor)}</span></div>
        <div class="result-item"><span class="label">Max Drawdown</span><span class="value">$${num(m.max_drawdown)}</span></div>
    </div>`;
}

// api calls
async function loadStatus() {
    const status = await apiFetch('/status');
    if (!status) return;
    statusSnapshot = status;

    const indicator = document.querySelector('.status-indicator');
    const statusText = document.getElementById('botStatusText');

    const activityState = document.getElementById('botActivityState');
    const engineStatus = document.getElementById('botEngineStatus');
    const processStatus = document.getElementById('botProcessStatus');
    const strategy = document.getElementById('botStrategy');
    const pid = document.getElementById('botPid');
    const lastUpdate = document.getElementById('lastUpdate');

    const isRunning = status.status === 'running';

    if (isRunning) {
        indicator?.classList.add('status-active');
        indicator?.classList.remove('status-inactive');
        if (statusText) statusText.textContent = 'Bot Running';
    } else {
        indicator?.classList.remove('status-active');
        indicator?.classList.add('status-inactive');
        if (statusText) statusText.textContent = 'Bot Stopped';
    }

    if (!startStopPendingAction) {
        renderStartStopButton({ isRunning, loading: false });
    }

    if (activityState) {
        activityState.textContent = isRunning ? 'Live' : 'Idle';
        activityState.classList.toggle('is-live', isRunning);
    }
    if (engineStatus) {
        if (status.engine_status === 'running_stale') {
            engineStatus.textContent = 'Running (No recent scan)';
        } else if (status.engine_status === 'running') {
            engineStatus.textContent = 'Running';
        } else if (status.engine_status === 'process_only') {
            engineStatus.textContent = 'Process alive (state mismatch)';
        } else {
            engineStatus.textContent = 'Stopped';
        }
    }
    if (processStatus) {
        if (!status.process_alive) {
            processStatus.textContent = 'Not running';
        } else if (typeof status.last_scan_age_s === 'number') {
            processStatus.textContent = `Active · last scan ${status.last_scan_age_s}s ago`;
        } else {
            processStatus.textContent = 'Active';
        }
    }
    if (strategy) strategy.textContent = status.active_strategy || '—';
    if (pid) pid.textContent = status.pid ? String(status.pid) : (isRunning ? 'External' : '—');
    if (lastUpdate) lastUpdate.textContent = formatTime(new Date().toISOString());

    // Show enabled symbols in status line area
    const symbolsEl = document.getElementById('botSymbols');
    if (symbolsEl) {
        const syms = status.enabled_symbols;
        symbolsEl.textContent = Array.isArray(syms) && syms.length ? syms.join(', ') : '—';
    }
    const msgEl = document.getElementById('botMessage');
    if (msgEl) msgEl.textContent = status.runtime_message || '—';

    tickLiveStatusClock();
}

function refreshStatCardColors() {
    document.querySelectorAll('.stat-card').forEach(card => {
        const valEl = card.querySelector('.stat-value [id]') || card.querySelector('.stat-value span');
        if (!valEl) return;
        const raw = valEl.textContent.replace(/[^0-9.\-]/g, '');
        const num = parseFloat(raw);
        const isZero = isNaN(num) || num === 0;
        card.classList.toggle('zero-val', isZero);
    });
}

async function loadMetrics() {
    const m = await apiFetch('/metrics');
    if (!m) return;
    const $ = id => document.getElementById(id);
    if ($('balance')) $('balance').textContent = Number(m.balance || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if ($('balanceChange')) $('balanceChange').textContent = `${Number(m.pnl_percentage || 0).toFixed(2)}%`;
    if ($('totalTrades')) $('totalTrades').textContent = Number(m.total_trades || 0);
    if ($('winRate')) $('winRate').textContent = Number(m.win_rate || 0).toFixed(1);
    if ($('pnl')) $('pnl').textContent = Number(m.profit_factor || 0).toFixed(2);
    if ($('sharpeRatio')) $('sharpeRatio').textContent = Number(m.sharpe_ratio || 0).toFixed(2);
    if ($('profitFactor')) $('profitFactor').textContent = Number(m.profit_factor || 0).toFixed(2);
    if ($('maxDrawdown')) $('maxDrawdown').textContent = `${Number(m.max_drawdown_pct ?? m.max_drawdown ?? 0).toFixed(2)}`;
    if ($('avgWin')) $('avgWin').textContent = `+$${Number(m.average_win || 0).toFixed(2)}`;
    if ($('avgLoss')) $('avgLoss').textContent = `-$${Number(m.average_loss || 0).toFixed(2)}`;
    if ($('largestWin')) $('largestWin').textContent = `+$${Number(m.largest_win || m.best_trade || 0).toFixed(2)}`;
    const wins = Number(m.wins || 0);
    const losses = Number(m.losses || (m.total_trades || 0) - wins);
    if ($('winLossRatio')) $('winLossRatio').textContent = losses > 0 ? (wins / losses).toFixed(2) : wins > 0 ? '∞' : '0.00';
    refreshStatCardColors();
}

async function loadTrades() {
    const trades = await apiFetch('/trades?limit=50');
    if (!trades) return;
    const table = document.getElementById('historyTable');
    if (!table) return;
    if (!trades.length) { table.innerHTML = '<tr><td colspan="8">No trades yet</td></tr>'; return; }
    table.innerHTML = trades.map(t => `<tr>
        <td>${t.timestamp}</td><td><strong>${t.symbol}</strong></td>
        <td><span class="badge badge-${t.type === 'BUY' ? 'success' : 'danger'}">${t.type}</span></td>
        <td>${t.quantity}</td>
        <td>${Number(t.entry_price || 0).toFixed(5)}</td>
        <td>${(t.exit_price == null) ? '—' : Number(t.exit_price || 0).toFixed(5)} ${(t.status === 'open' || t.exit_price == null) ? '<span class="badge badge-neutral" style="margin-left:8px">OPEN</span>' : ''}</td>
        <td class="${(t.pnl == null) ? '' : (t.pnl >= 0 ? 'positive' : 'negative')}">${(t.pnl == null) ? '—' : Number(t.pnl || 0).toFixed(2)}</td>
        <td>${(t.pnl == null) ? '—' : ((t.pnl >= 0 ? '+' : '') + Number(t.pnl || 0).toFixed(2))}</td>
    </tr>`).join('');

    // Populate history summary stats
    const closedTrades = trades.filter(t => t.pnl != null);
    const total = trades.length;
    const wins = closedTrades.filter(t => t.pnl > 0).length;
    const closedTotal = closedTrades.length;
    const wr = total > 0 ? (wins / total * 100).toFixed(1) : '0.0';
    const totalPnl = closedTrades.reduce((s, t) => s + (t.pnl || 0), 0);
    const avgReturn = closedTotal > 0 ? (totalPnl / closedTotal).toFixed(2) : '0.00';
    if ($('histTotalTrades')) $('histTotalTrades').textContent = total;
    if ($('histWinRate')) $('histWinRate').textContent = (closedTotal > 0 ? (wins / closedTotal * 100).toFixed(1) : '0.0') + '%';
    if ($('histTotalPnl')) $('histTotalPnl').textContent = (totalPnl >= 0 ? '+' : '') + '$' + totalPnl.toFixed(2);
    if ($('histAvgReturn')) $('histAvgReturn').textContent = (avgReturn >= 0 ? '+' : '') + '$' + avgReturn;

    // --- Recent Trades Preview (Overview section) ---
    const preview = document.getElementById('recentTradesPreview');
    if (preview) {
        if (!trades.length) {
            preview.innerHTML = '<div class="empty-state-inline"><i class="fas fa-inbox"></i> No trades yet</div>';
        } else {
            const recent = trades.slice(0, 5);
            preview.innerHTML = recent.map(t => `<div class="recent-trade-row">
                <span class="rt-symbol"><strong>${t.symbol}</strong></span>
                <span class="badge badge-${t.type === 'BUY' ? 'success' : 'danger'}" style="font-size:0.65rem;padding:2px 6px;">${t.type}</span>
                <span class="rt-pnl ${(t.pnl == null) ? '' : (t.pnl >= 0 ? 'positive' : 'negative')}">${(t.pnl == null) ? 'OPEN' : ((t.pnl >= 0 ? '+' : '') + '$' + Number(t.pnl || 0).toFixed(2))}</span>
                <span class="rt-time" style="color:var(--z-text-3);font-size:0.7rem;">${t.timestamp || ''}</span>
            </div>`).join('');
        }
    }
}

async function loadEquity() {
    const data = await apiFetch('/equity');
    if (!data) return;
    const ctx = document.getElementById('equityChart');
    if (!ctx) return;

    if (!equityChart) {
        equityChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.labels,
                datasets: [{
                    label: 'Equity',
                    data: data.data,
                    borderColor: '#00ff87',
                    backgroundColor: (context) => {
                        const chart = context.chart;
                        const { ctx: c, chartArea } = chart;
                        if (!chartArea) return 'transparent';
                        const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                        g.addColorStop(0, 'rgba(0, 255, 135, 0.12)');
                        g.addColorStop(1, 'rgba(0, 255, 135, 0)');
                        return g;
                    },
                    tension: 0.3, fill: true, pointRadius: 0, borderWidth: 2
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#4a5568', maxTicksLimit: 8, font: { family: "'JetBrains Mono'", size: 10 } }, grid: { color: 'rgba(255,255,255,0.03)' } },
                    y: { ticks: { color: '#4a5568', font: { family: "'JetBrains Mono'", size: 10 } }, grid: { color: 'rgba(255,255,255,0.03)' } }
                }
            }
        });
    } else {
        equityChart.data.labels = data.labels;
        equityChart.data.datasets[0].data = data.data;
        equityChart.update('none');
    }
}

async function handleStartStop() {
    const status = await apiFetch('/status');
    if (!status) return;

    const btn = document.getElementById('startStopBtn');
    const action = status.status === 'running' ? 'stop' : 'start';
    startStopPendingAction = action;
    renderStartStopButton({ isRunning: status.status === 'running', loading: true, action });
    if (btn) {
        btn.disabled = true;
    }

    let resp;
    if (status.status === 'running') {
        resp = await apiFetchWithMeta('/bot/stop', { method: 'POST' });
    } else {
        resp = await apiFetchWithMeta('/bot/start', { method: 'POST', body: JSON.stringify({ strategy: 'ict_smc' }) });
    }

    startStopPendingAction = null;
    if (btn) {
        btn.disabled = false;
    }

    if (!resp?.ok) {
        renderStartStopButton({ isRunning: status.status === 'running', loading: false });
        if (btn) btn.classList.add('is-error');
        setTimeout(() => btn?.classList.remove('is-error'), 500);
        if (action === 'start' && isMt5UnavailableError(resp?.data)) {
            openMt5HelpPopup(resp.data);
        }
        return;
    }
    
    if (btn) {
        btn.classList.add('is-success');
        setTimeout(() => btn?.classList.remove('is-success'), 600);
    }
    
    await loadStatus();
}

    async function loadTelegramConfig() {
        const cfg = await apiFetch('/telegram/config');
        if (!cfg) return;

        const tokenInput = document.getElementById('telegramBotToken');
        const chatInput = document.getElementById('telegramChatId');
        const statusEl = document.getElementById('telegramConfigStatus');

        if (tokenInput) tokenInput.value = '';
        if (chatInput) chatInput.value = cfg.chat_id || '';

        if (statusEl) {
            if (cfg.configured) {
                setStatusText(statusEl, `Telegram connected (${cfg.bot_token_masked || 'token saved'})`);
            } else if (cfg.has_bot_token || cfg.has_chat_id) {
                setStatusText(statusEl, 'Telegram partially configured. Save both token and chat ID.', true);
            } else {
                setStatusText(statusEl, 'Telegram not configured yet.');
            }
        }
    }

    async function saveTelegramConfig() {
        const tokenInput = document.getElementById('telegramBotToken');
        const chatInput = document.getElementById('telegramChatId');
        const statusEl = document.getElementById('telegramConfigStatus');
        const btn = document.getElementById('saveTelegramConfigBtn');

        const botToken = (tokenInput?.value || '').trim();
        const chatId = (chatInput?.value || '').trim();

        if (!botToken || !chatId) {
            setStatusText(statusEl, 'Both Bot Token and Chat ID are required.', true);
            return;
        }

        if (btn) {
            btn.disabled = true;
            btn.classList.add('is-loading');
        }

        const res = await apiFetch('/telegram/config', {
            method: 'POST',
            body: JSON.stringify({ bot_token: botToken, chat_id: chatId }),
        });

        if (btn) {
            btn.disabled = false;
            btn.classList.remove('is-loading');
        }

        if (!res || res.success === false) {
            setStatusText(statusEl, 'Failed to save Telegram login.', true);
            return;
        }

        if (tokenInput) tokenInput.value = '';
        setStatusText(statusEl, 'Telegram login saved. Restart bot to apply.');
    }

    // ═══════════════════════════════════════════════════════════════════
    // BOT API KEY (Remote Connection)
    // ═══════════════════════════════════════════════════════════════════
    async function loadBotApiKey() {
        const keyInput = document.getElementById('botApiKeyDisplay');
        const urlInput = document.getElementById('dashboardUrlDisplay');
        const statusEl = document.getElementById('botApiKeyStatus');
        const hintEl = document.getElementById('botApiKeyHint');

        // Auto-fill dashboard URL
        if (urlInput) {
            urlInput.value = window.location.origin;
        }

        const res = await apiFetch('/bot/api-key');
        if (!res) return;

        if (res.has_key) {
            if (keyInput) keyInput.value = res.key_masked || '••••••••';
            if (statusEl) {
                const lastUsed = res.last_used_at ? `Last used: ${new Date(res.last_used_at).toLocaleString()}` : 'Never used';
                setStatusText(statusEl, `Key active — ${lastUsed}`);
            }
        } else {
            if (keyInput) keyInput.value = '';
            if (keyInput) keyInput.placeholder = 'No key generated yet';
            if (statusEl) setStatusText(statusEl, 'Generate a key to connect a remote bot.');
        }
    }

    async function generateBotApiKey() {
        const keyInput = document.getElementById('botApiKeyDisplay');
        const statusEl = document.getElementById('botApiKeyStatus');
        const btn = document.getElementById('generateBotApiKeyBtn');

        if (btn) { btn.disabled = true; btn.classList.add('is-loading'); }

        const res = await apiFetch('/bot/api-key/generate', { method: 'POST' });

        if (btn) { btn.disabled = false; btn.classList.remove('is-loading'); }

        if (!res || !res.success) {
            setStatusText(statusEl, 'Failed to generate key.', true);
            return;
        }

        // Show full key once so user can copy it
        if (keyInput) {
            keyInput.value = res.api_key;
            keyInput.select();
        }
        setStatusText(statusEl, '⚠ Copy this key now! It won\'t be shown in full again.');
    }

    async function revokeBotApiKey() {
        const statusEl = document.getElementById('botApiKeyStatus');
        const keyInput = document.getElementById('botApiKeyDisplay');

        if (!confirm('Revoke this API key? The remote bot will stop sending data.')) return;

        const res = await apiFetch('/bot/api-key', { method: 'DELETE' });
        if (res && res.success) {
            if (keyInput) keyInput.value = '';
            setStatusText(statusEl, 'API key revoked.');
        } else {
            setStatusText(statusEl, 'Failed to revoke key.', true);
        }
    }

    function copyToClipboard(inputId) {
        const input = document.getElementById(inputId);
        if (!input || !input.value) return;
        navigator.clipboard.writeText(input.value).then(() => {
            const orig = input.value;
            input.value = '✓ Copied!';
            setTimeout(() => { input.value = orig; }, 1200);
        });
    }

    function copyText(text, btn) {
        navigator.clipboard.writeText(text).then(() => {
            if (btn) {
                const orig = btn.innerHTML;
                btn.innerHTML = '<i class="fas fa-check"></i> Copied!';
                setTimeout(() => { btn.innerHTML = orig; }, 1200);
            }
        });
    }

    // ═══════════════════════════════════════════════════════════════════
    // CREDENTIALS MODAL (next to Download button)
    // ═══════════════════════════════════════════════════════════════════
    let _credsFullKey = null;  // store full key after generate

    async function openCredsModal() {
        const backdrop = document.getElementById('credsModalBackdrop');
        const urlInput = document.getElementById('credsDashUrl');
        const keyInput = document.getElementById('credsApiKey');
        const hintEl = document.getElementById('credsKeyHint');
        if (!backdrop) return;

        // Show modal
        backdrop.style.display = 'flex';
        const dashUrl = window.location.origin;
        if (urlInput) urlInput.value = dashUrl;

        // Load existing key
        const res = await apiFetch('/bot/api-key');
        if (res && res.has_key) {
            if (keyInput) keyInput.value = res.key_masked || '••••••••';
            if (hintEl) hintEl.textContent = res.last_used_at
                ? `Last used: ${new Date(res.last_used_at).toLocaleString()}`
                : 'Key active — never used yet.';
            _credsFullKey = null;
        } else {
            // Auto-generate a key for convenience
            if (keyInput) keyInput.value = '';
            if (keyInput) keyInput.placeholder = 'Generating...';
            if (hintEl) hintEl.textContent = '';
            const gen = await apiFetch('/bot/api-key/generate', { method: 'POST' });
            if (gen && gen.success && gen.api_key) {
                _credsFullKey = gen.api_key;
                if (keyInput) keyInput.value = gen.api_key;
                if (hintEl) hintEl.textContent = '⚠ Copy this key now — it won\'t be shown in full again.';
                loadBotApiKey();
            } else {
                if (keyInput) { keyInput.value = ''; keyInput.placeholder = 'Failed to generate'; }
            }
        }
    }

    function closeCredsModal() {
        const backdrop = document.getElementById('credsModalBackdrop');
        if (backdrop) backdrop.style.display = 'none';
    }

    async function regenCredsKey() {
        const keyInput = document.getElementById('credsApiKey');
        const hintEl = document.getElementById('credsKeyHint');
        const btn = document.getElementById('credsRegenKeyBtn');

        if (btn) { btn.disabled = true; btn.classList.add('is-loading'); }
        const gen = await apiFetch('/bot/api-key/generate', { method: 'POST' });
        if (btn) { btn.disabled = false; btn.classList.remove('is-loading'); }

        if (gen && gen.success && gen.api_key) {
            _credsFullKey = gen.api_key;
            if (keyInput) keyInput.value = gen.api_key;
            if (hintEl) hintEl.textContent = '⚠ New key generated — copy it now!';
            loadBotApiKey();
        }
    }

async function runBacktest() {
    // kill any running animation
    if (btAnimTimer) { clearTimeout(btAnimTimer); btAnimTimer = null; }

    const symbol = document.getElementById('btSymbol').value;
    const days = parseInt(document.getElementById('btDays').value) || 60;
    const status = document.getElementById('btStatus');
    const runBtn = document.getElementById('runBacktestBtn');
    const pauseBtn = document.getElementById('btPauseBtn');
    const stopBtn = document.getElementById('btStopBtn');

    btRunInProgress = true;
    if (pauseBtn) { pauseBtn.disabled = true; pauseBtn.innerHTML = '<i class="fas fa-pause"></i>'; }
    if (stopBtn) stopBtn.disabled = true;

    setStatusText(status, 'Running real MT5 no-lookahead backtest...');
    if (runBtn) { 
        runBtn.disabled = true; 
        runBtn.classList.add('is-loading');
        runBtn.innerHTML = '<i class="fas fa-hourglass-half"></i> Loading data…'; 
    }

    const riskPct = parseFloat(document.getElementById('btRiskPct')?.value) || 1.0;

    // Always enqueue a REAL persisted run via backtest_improved.py
    try {
        const queued = await apiFetch('/backtest/run', {
            method: 'POST',
            body: JSON.stringify({ symbol, mode: 'standard', split_ratio: 0.7, mc_iterations: 200 })
        });
        if (queued?.success && queued?.run_id) {
            setStatusText(status, `Queued real engine run: ${queued.run_id} (backtest_improved.py)`);
            setTimeout(async () => {
                await loadRuns();
                await loadRobustness();
            }, 1200);
        }
    } catch (e) {
        console.warn('Could not queue real persisted backtest run:', e);
    }

    let data = null;
    try {
        data = await apiFetch('/backtest/simulate', {
            method: 'POST',
            body: JSON.stringify({ symbol, days, risk_pct: riskPct })
        });
        if (!data) {
            throw new Error('No response from server');
        }
    } catch (e) {
        console.error('Backtest fetch failed:', e);
        setStatusText(status, `Error: ${e.message || 'Server connection failed'}`, true);
    }

    if (runBtn) { 
        runBtn.disabled = false; 
        runBtn.innerHTML = '<i class="fas fa-play"></i> Run Backtest';
        runBtn.classList.remove('is-loading'); 
    }

    if (!data || !data.candles || !data.candles.length) {
        btRunInProgress = false;
        const reason = data?.meta?.message || 'Failed to generate backtest data — check configuration';
        setStatusText(status, reason, true);
        if (runBtn) {
            runBtn.classList.add('is-error');
            setTimeout(() => runBtn?.classList.remove('is-error'), 500);
        }
        return;
    }
    
    if (runBtn) {
        runBtn.classList.add('is-success');
        setTimeout(() => runBtn?.classList.remove('is-success'), 600);
    }

    btCandles = data.candles;
    btTrades = data.trades || [];
    btMarkers = [];
    btIdx = 0;
    btPaused = false;
    btStopped = false;
    btStats = { trades: 0, wins: 0, pnl: 0, balance: 10000 };
    btSlLine = null;
    btTpLine = null;
    btEntryLine = null;
    btTradeLines = [];
    btLiveEquityCurve = [10000];
    btPairAnalyticsRows = [];
    btReportSymbol = symbol;
    btReportDays = days;
    btGrossProfit = 0;
    btGrossLoss = 0;
    btEquitySampleCounter = 0;

    // show chart area
    const chartArea = document.getElementById('btChartArea');
    chartArea.style.display = 'block';
    document.getElementById('btChartSymbol').textContent = symbol;
    document.getElementById('btProgress').textContent = '0%';

    // reset controls
    if (pauseBtn) { pauseBtn.innerHTML = '<i class="fas fa-pause"></i>'; pauseBtn.disabled = false; }
    if (stopBtn) stopBtn.disabled = false;

    updateBtLiveStats();
    const backtestNote = data?.meta?.message || '';
    setStatusText(status, backtestNote ? `Data loaded · ${backtestNote}` : 'Data loaded');
    initBtChart();
    await renderBacktestReport(data);
    setStatusText(status, 'Animating candles...');

    setTimeout(animateNextCandle, 300);
}

function renderBtEquityCurve(equityCurve = []) {
    const canvas = document.getElementById('btEquityChart');
    if (!canvas) return;

    const labels = equityCurve.map((_, i) => i + 1);

    if (!btEquityChart) {
        btEquityChart = new Chart(canvas, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: 'Equity',
                    data: equityCurve,
                    borderColor: '#00ff87',
                    backgroundColor: 'rgba(0,255,135,0.12)',
                    fill: true,
                    tension: 0.22,
                    pointRadius: 0,
                    borderWidth: 2,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: {
                        ticks: { color: '#6b7a8d', maxTicksLimit: 8, font: { family: "'JetBrains Mono'", size: 10 } },
                        grid: { color: 'rgba(255,255,255,0.03)' },
                    },
                    y: {
                        ticks: { color: '#6b7a8d', font: { family: "'JetBrains Mono'", size: 10 } },
                        grid: { color: 'rgba(255,255,255,0.03)' },
                    }
                }
            }
        });
    } else {
        btEquityChart.data.labels = labels;
        btEquityChart.data.datasets[0].data = equityCurve;
        btEquityChart.update('none');
    }
}

function renderPairAnalyticsTable(rows = [], selectedSymbol = '', selectedMetrics = null) {
    const table = document.getElementById('btPairAnalyticsTable');
    if (!table) return;

    const merged = [...rows];
    if (selectedSymbol && selectedMetrics) {
        const idx = merged.findIndex((r) => r.symbol === selectedSymbol);
        const row = {
            symbol: selectedSymbol,
            total_trades: Number(selectedMetrics.total_trades || 0),
            win_rate: Number(selectedMetrics.win_rate || 0),
            total_profit: Number(selectedMetrics.total_profit || 0),
            profit_factor: Number(selectedMetrics.profit_factor || 0),
            max_drawdown: Number(selectedMetrics.max_drawdown || 0),
            return_pct: Number(selectedMetrics.return_pct || 0),
            mode: 'live-runner',
        };
        if (idx >= 0) merged[idx] = row;
        else merged.unshift(row);
    }

    if (!merged.length) {
        table.innerHTML = '<tr><td colspan="8">No pair analytics available yet.</td></tr>';
        return;
    }

    table.innerHTML = merged.map((r) => {
        const p = Number(r.total_profit || 0);
        const dd = Number(r.max_drawdown || 0);
        const ret = Number(r.return_pct || 0);
        const isSel = r.symbol === selectedSymbol;
        return `<tr${isSel ? ' style="background: rgba(0,255,135,0.06);"' : ''}>
            <td><strong>${r.symbol || '—'}</strong></td>
            <td>${Number(r.total_trades || 0)}</td>
            <td>${num(r.win_rate)}%</td>
            <td class="${p >= 0 ? 'positive' : 'negative'}">${p >= 0 ? '+' : ''}$${num(p)}</td>
            <td>${num(r.profit_factor)}</td>
            <td class="negative">$${num(dd)}</td>
            <td class="${ret >= 0 ? 'positive' : 'negative'}">${ret >= 0 ? '+' : ''}${num(ret)}%</td>
            <td>${r.mode || '—'}</td>
        </tr>`;
    }).join('');
}

function calculateBtMaxDrawdown(equityCurve = []) {
    if (!Array.isArray(equityCurve) || equityCurve.length < 2) return 0;
    let peak = Number(equityCurve[0] || 0);
    let maxDd = 0;
    for (const point of equityCurve) {
        const value = Number(point || 0);
        if (value > peak) peak = value;
        const dd = peak - value;
        if (dd > maxDd) maxDd = dd;
    }
    return maxDd;
}

function buildLiveBacktestMetrics() {
    const totalTrades = Number(btStats.trades || 0);
    const winRate = totalTrades > 0 ? (btStats.wins / totalTrades * 100) : 0;
    const totalProfit = Number(btStats.pnl || 0);
    const maxDrawdown = calculateBtMaxDrawdown(btLiveEquityCurve);
    const returnPct = ((Number(btStats.balance || 10000) - 10000) / 10000) * 100;
    const profitFactor = btGrossLoss > 0 ? (btGrossProfit / btGrossLoss) : (btGrossProfit > 0 ? 99 : 0);

    return {
        total_trades: totalTrades,
        win_rate: winRate,
        total_profit: totalProfit,
        profit_factor: profitFactor,
        max_drawdown: maxDrawdown,
        return_pct: returnPct,
    };
}

function updateBacktestReportLive(statusLabel = 'Running') {
    const report = document.getElementById('btReport');
    if (!report) return;

    const m = buildLiveBacktestMetrics();
    const set = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };

    set('btRepTrades', Number(m.total_trades || 0));
    set('btRepWinRate', `${num(m.win_rate)}%`);
    set('btRepProfit', `${Number(m.total_profit || 0) >= 0 ? '+' : ''}$${num(m.total_profit)}`);
    set('btRepPF', num(m.profit_factor));
    set('btRepDD', `$${num(m.max_drawdown)}`);
    set('btRepReturn', `${Number(m.return_pct || 0) >= 0 ? '+' : ''}${num(m.return_pct)}%`);

    const metaEl = document.getElementById('btReportMeta');
    if (metaEl) {
        metaEl.textContent = `${btReportSymbol} · ${btReportDays}d · ${Number(m.total_trades || 0)} trades · ${statusLabel}`;
    }

    const curve = btLiveEquityCurve.length ? btLiveEquityCurve : [10000];
    renderBtEquityCurve(curve);
    renderPairAnalyticsTable(btPairAnalyticsRows, btReportSymbol, m);
    report.style.display = 'block';
}

async function renderBacktestReport(data) {
    const report = document.getElementById('btReport');
    if (!report || !data) return;

    try {
        const pairData = await apiFetch('/backtest/pairs-analytics');
        btPairAnalyticsRows = pairData?.analytics || [];
    } catch (e) {
        btPairAnalyticsRows = [];
    }

    btReportSymbol = data.symbol || btReportSymbol;
    btReportDays = Number(data.days || btReportDays || 60);
    report.style.display = 'block';
    updateBacktestReportLive('Running');
}

function initBtChart() {
    const container = document.getElementById('btChartContainer');
    if (!container) return;
    container.innerHTML = '';
    btManualPanUntil = 0;

    if (btChart) { btChart.remove(); btChart = null; }

    const chartHeight = Math.max(420, Math.round(container.clientHeight || 560));

    btChart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: chartHeight,
        layout: {
            background: { type: 'solid', color: 'transparent' },
            textColor: '#6b7a8d',
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11
        },
        grid: {
            vertLines: { color: 'rgba(255, 255, 255, 0.03)' },
            horzLines: { color: 'rgba(255, 255, 255, 0.03)' }
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: { color: 'rgba(96, 239, 255, 0.3)', width: 1, labelBackgroundColor: '#1a2535' },
            horzLine: { color: 'rgba(96, 239, 255, 0.3)', width: 1, labelBackgroundColor: '#1a2535' }
        },
        rightPriceScale: { borderColor: 'rgba(255, 255, 255, 0.06)' },
        timeScale: {
            borderColor: 'rgba(255, 255, 255, 0.06)',
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 18,
            barSpacing: 6,
            minBarSpacing: 2
        },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true }
    });

    btSeries = btChart.addCandlestickSeries({
        upColor: '#00ff87',
        downColor: '#ff6b6b',
        borderDownColor: '#ff6b6b',
        borderUpColor: '#00ff87',
        wickDownColor: 'rgba(255, 107, 107, 0.5)',
        wickUpColor: 'rgba(0, 255, 135, 0.5)'
    });

    try {
        btSeries.priceScale().applyOptions({
            autoScale: true,
            scaleMargins: { top: 0.14, bottom: 0.16 },
        });
    } catch (e) {}

    // auto-resize
    new ResizeObserver(entries => {
        if (btChart) {
            const nextHeight = Math.max(420, Math.round(entries[0].contentRect.height || 560));
            btChart.applyOptions({ width: entries[0].contentRect.width, height: nextHeight });
        }
    }).observe(container);

    const markManualPan = () => {
        btManualPanUntil = Date.now() + 3500;
    };
    ['wheel', 'mousedown', 'touchstart'].forEach((eventName) => {
        container.addEventListener(eventName, markManualPan, { passive: true });
    });
}

function trackBacktestPrice(force = false) {
    if (!btSeries) return;
    if (!force && Date.now() < btManualPanUntil) return;

    try {
        btSeries.priceScale().applyOptions({ autoScale: true });
    } catch (e) {}
}

function animateNextCandle() {
    if (!btSeries || !btChart || !btCandles.length) {
        btRunInProgress = false;
        const status = document.getElementById('btStatus');
        setStatusText(status, 'Backtest animation could not start (chart init failed).', true);
        return;
    }

    if (btPaused || btStopped || btIdx >= btCandles.length) {
        if (btIdx >= btCandles.length) finishBacktest();
        return;
    }

    btSeries.update(btCandles[btIdx]);
    checkTradeMarkers(btIdx);

    // progress
    const pct = Math.round((btIdx / btCandles.length) * 100);
    const progEl = document.getElementById('btProgress');
    if (progEl) progEl.textContent = pct + '%';

    btEquitySampleCounter++;
    if (btEquitySampleCounter % 8 === 0) {
        btLiveEquityCurve.push(Number(btStats.balance || 10000));
        updateBacktestReportLive('Running');
    }

    if (Date.now() >= btManualPanUntil) {
        btChart.timeScale().scrollToRealTime();
    }
    trackBacktestPrice();
    btIdx++;

    const speed = parseInt(document.getElementById('btSpeed').value) || 40;
    const delay = Math.max(1, Math.round(300 / speed));
    btAnimTimer = setTimeout(animateNextCandle, delay);
}

function checkTradeMarkers(barIdx) {
    for (const t of btTrades) {
        if (t.entryBar === barIdx) {
            // entry arrow marker
            btMarkers.push({
                time: btCandles[barIdx].time,
                position: t.type === 'BUY' ? 'belowBar' : 'aboveBar',
                color: t.type === 'BUY' ? '#00ff87' : '#ff6b6b',
                shape: t.type === 'BUY' ? 'arrowUp' : 'arrowDown',
                text: t.type
            });
            btSeries.setMarkers(btMarkers.slice());

            // draw SL / TP / Entry price lines
            try {
                btEntryLine = btSeries.createPriceLine({
                    price: t.entryPrice,
                    color: 'rgba(96, 239, 255, 0.5)',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dotted,
                    axisLabelVisible: false,
                    title: '',
                });
                btSlLine = btSeries.createPriceLine({
                    price: t.sl,
                    color: 'rgba(255, 107, 107, 0.7)',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: true,
                    title: 'SL',
                });
                btTpLine = btSeries.createPriceLine({
                    price: t.tp,
                    color: 'rgba(0, 255, 135, 0.7)',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: true,
                    title: 'TP',
                });
            } catch (e) { /* price line creation can fail silently */ }
        }

        if (t.exitBar === barIdx) {
            // exit marker
            const isWin = t.profit > 0;
            btMarkers.push({
                time: btCandles[barIdx].time,
                position: isWin ? 'aboveBar' : 'belowBar',
                color: isWin ? '#00ff87' : '#ff6b6b',
                shape: 'circle',
                text: t.reason + (isWin ? ' ✓' : ' ✗')
            });
            btSeries.setMarkers(btMarkers.slice());

            // remove SL/TP/entry price lines
            try {
                if (btSlLine) { btSeries.removePriceLine(btSlLine); btSlLine = null; }
                if (btTpLine) { btSeries.removePriceLine(btTpLine); btTpLine = null; }
                if (btEntryLine) { btSeries.removePriceLine(btEntryLine); btEntryLine = null; }
            } catch (e) {}

            // draw trade result line (entry → exit)
            try {
                const lineColor = isWin ? 'rgba(0, 255, 135, 0.4)' : 'rgba(255, 107, 107, 0.4)';
                const tradeLine = btChart.addLineSeries({
                    color: lineColor,
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Dotted,
                    crosshairMarkerVisible: false,
                    priceLineVisible: false,
                    lastValueVisible: false,
                });
                tradeLine.setData([
                    { time: btCandles[t.entryBar].time, value: t.entryPrice },
                    { time: btCandles[t.exitBar].time, value: t.exitPrice },
                ]);
                btTradeLines.push(tradeLine);
            } catch (e) {}

            // floating profit/loss popup
            showTradePopup(t.profit, isWin, t.reason);

            // update stats
            btStats.trades++;
            if (t.profit > 0) btStats.wins++;
            btStats.pnl += t.profit;
            btStats.balance = t.balance;
            if (t.profit > 0) btGrossProfit += Number(t.profit || 0);
            else btGrossLoss += Math.abs(Number(t.profit || 0));
            btLiveEquityCurve.push(Number(btStats.balance || 10000));
            updateBtLiveStats();
            updateBacktestReportLive('Running');
        }
    }
}

function showTradePopup(profit, isWin, reason) {
    const container = document.getElementById('btChartContainer');
    if (!container) return;

    const el = document.createElement('div');
    el.className = isWin ? 'bt-popup-win' : 'bt-popup-loss';

    const sign = profit >= 0 ? '+' : '';
    const icon = isWin ? '💰' : '🔻';
    el.innerHTML = `<span>${icon} ${sign}$${Math.abs(profit).toFixed(2)}</span>`;

    // position randomly in the right half of the chart
    el.style.right = (20 + Math.random() * 80) + 'px';
    el.style.top = (30 + Math.random() * 50) + '%';
    container.style.position = 'relative';
    container.appendChild(el);

    setTimeout(() => { if (el.parentNode) el.remove(); }, 2200);
}

function updateBtLiveStats() {
    const wr = btStats.trades > 0 ? (btStats.wins / btStats.trades * 100).toFixed(1) : null;
    const el = id => document.getElementById(id);

    if (el('btLiveTrades')) el('btLiveTrades').textContent = btStats.trades;
    if (el('btLiveWinRate')) el('btLiveWinRate').textContent = wr ? wr + '%' : '\u2014';

    const pnlEl = el('btLivePnl');
    if (pnlEl) {
        pnlEl.textContent = (btStats.pnl >= 0 ? '+' : '') + '$' + btStats.pnl.toFixed(2);
        pnlEl.className = 'bt-stat-value ' + (btStats.pnl >= 0 ? 'positive' : 'negative');
    }

    if (el('btLiveBalance')) {
        el('btLiveBalance').textContent = '$' + btStats.balance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    const headBalance = el('btHeadBalance');
    if (headBalance) {
        headBalance.textContent = '$' + btStats.balance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
}

function toggleBtPause() {
    btPaused = !btPaused;
    const btn = document.getElementById('btPauseBtn');
    if (btn) btn.innerHTML = btPaused ? '<i class="fas fa-play"></i>' : '<i class="fas fa-pause"></i>';
    if (!btPaused && !btStopped) animateNextCandle();
}

function skipToEnd() {
    if (btAnimTimer) { clearTimeout(btAnimTimer); btAnimTimer = null; }
    btStopped = true;

    // remove any active price lines before bulk dump
    try {
        if (btSlLine) { btSeries.removePriceLine(btSlLine); btSlLine = null; }
        if (btTpLine) { btSeries.removePriceLine(btTpLine); btTpLine = null; }
        if (btEntryLine) { btSeries.removePriceLine(btEntryLine); btEntryLine = null; }
    } catch (e) {}

    // suppress popups during bulk render
    const origShow = showTradePopup;
    showTradePopup = () => {};

    // dump all remaining candles at once
    while (btIdx < btCandles.length) {
        btSeries.update(btCandles[btIdx]);
        checkTradeMarkers(btIdx);
        btIdx++;
    }
    btChart.timeScale().scrollToRealTime();
    trackBacktestPrice(true);

    showTradePopup = origShow;
    finishBacktest();
}

function finishBacktest() {
    btRunInProgress = false;
    btLiveEquityCurve.push(Number(btStats.balance || 10000));
    updateBacktestReportLive('Complete');
    const progEl = document.getElementById('btProgress');
    if (progEl) progEl.textContent = 'Complete';
    const btn = document.getElementById('btPauseBtn');
    if (btn) btn.disabled = true;
    const stopBtn = document.getElementById('btStopBtn');
    if (stopBtn) stopBtn.disabled = true;
    const status = document.getElementById('btStatus');
    setStatusText(status, 'Backtest animation complete.');
}

async function runSuite() {
    const s = document.getElementById('btStatus');
    setStatusText(s, 'Starting full suite (7 symbols × 5 modes)...');
    const r = await apiFetch('/backtest/run-suite', { method: 'POST', body: JSON.stringify({ modes: ['standard', 'walk_forward', 'split', 'monte_carlo', 'robustness_20'], periods: 20 }) });
    if (r && r.success) { setStatusText(s, `Started ${r.count} runs`); setTimeout(async () => { await loadRuns(); await loadRobustness(); }, 1200); }
    else setStatusText(s, 'Failed to start suite', true);
}

async function runRobustness20() {
    const status = document.getElementById('btStatus');
    const symbolSel = document.getElementById('btSymbol');
    const symbol = symbolSel ? symbolSel.value : 'EURUSD';

    setStatusText(status, `Starting 20-period robustness for ${symbol}...`);
    const res = await apiFetch('/backtest/run-robustness-20', {
        method: 'POST',
        body: JSON.stringify({ symbol, periods: 20 })
    });

    if (res && res.success) {
        setStatusText(status, `Robustness run queued: ${res.run_id}`);
        setTimeout(async () => { await loadRuns(); await loadRobustness(); }, 1500);
    } else {
        setStatusText(status, 'Failed to start 20-period robustness run.', true);
    }
}

async function downloadPresentationPackage() {
    const status = document.getElementById('btStatus');
    const symbolSel = document.getElementById('btSymbol');
    const symbol = symbolSel ? symbolSel.value : 'EURUSD';
    const token = getToken();
    if (!token) {
        setStatusText(status, 'Please log in again.', true);
        return;
    }

    setStatusText(status, `Preparing presentation package for ${symbol}...`);
    try {
        const url = `${API_BASE_URL}/backtest/presentation-package?symbol=${encodeURIComponent(symbol)}`;
        const response = await fetch(url, {
            method: 'GET',
            headers: { Authorization: `Bearer ${token}` }
        });

        if (!response.ok) {
            setStatusText(status, 'Could not generate package. Run a completed backtest first.', true);
            return;
        }

        const blob = await response.blob();
        const cd = response.headers.get('content-disposition') || '';
        let filename = `zenith_presentation_${symbol}.zip`;
        const m = cd.match(/filename="?([^";]+)"?/i);
        if (m && m[1]) filename = m[1];

        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(objectUrl);

        setStatusText(status, `Downloaded ${filename}`);
    } catch (e) {
        console.error('Download package failed:', e);
        setStatusText(status, 'Package download failed.', true);
    }
}

// ═══════════ STORED BACKTEST RESULTS + EQUITY CURVES ═══════════

let storedEquityChartInstance = null;

async function loadStoredResults() {
    const sel = document.getElementById('storedResultSelect');
    const emptyEl = document.getElementById('storedResultsEmpty');
    if (!sel) return;

    try {
        const data = await apiFetch('/backtest/stored-results');
        if (!data || !data.results || data.results.length === 0) {
            sel.innerHTML = '<option value="">No results available</option>';
            if (emptyEl) emptyEl.style.display = '';
            return;
        }

        sel.innerHTML = data.results.map(r =>
            `<option value="${r.file}">${r.symbol} — ${r.mode} (${r.total_trades} trades, ${r.return_pct}% return)</option>`
        ).join('');

        if (emptyEl) emptyEl.style.display = 'none';

        // Auto-load first result
        sel.addEventListener('change', () => loadStoredResultDetail(sel.value));
        loadStoredResultDetail(data.results[0].file);
    } catch (e) {
        console.error('Failed to load stored results:', e);
    }
}

async function loadStoredResultDetail(filename) {
    if (!filename) return;

    const strip = document.getElementById('storedMetricsStrip');
    const eqWrap = document.getElementById('storedEquityWrap');
    const trWrap = document.getElementById('storedTradesWrap');

    try {
        const data = await apiFetch(`/backtest/stored-results/${encodeURIComponent(filename)}`);
        if (!data) return;

        const m = data.metrics || {};

        // Show metrics
        if (strip) {
            strip.style.display = '';
            setText('srTrades', m.total_trades ?? '—');
            setText('srWinRate', m.win_rate != null ? m.win_rate.toFixed(1) + '%' : '—');
            const profitEl = document.getElementById('srProfit');
            if (profitEl) {
                profitEl.textContent = '$' + (m.total_profit ?? 0).toFixed(2);
                profitEl.className = 'stored-metric-value ' + ((m.total_profit || 0) >= 0 ? 'positive' : 'negative');
            }
            setText('srPF', m.profit_factor != null ? m.profit_factor.toFixed(2) : '—');
            setText('srDD', '$' + (m.max_drawdown ?? 0).toFixed(2));
            const retEl = document.getElementById('srReturn');
            if (retEl) {
                retEl.textContent = (m.return_pct ?? 0).toFixed(2) + '%';
                retEl.className = 'stored-metric-value ' + ((m.return_pct || 0) >= 0 ? 'positive' : 'negative');
            }
            setText('srBalance', '$' + (m.final_balance ?? 10000).toFixed(2));
        }

        // Equity Curve
        if (data.equity_curve && data.equity_curve.length > 0 && eqWrap) {
            eqWrap.style.display = '';
            renderStoredEquityCurve(data.labels, data.equity_curve, data.symbol);
        }

        // Trades sample
        if (data.trades_sample && data.trades_sample.length > 0 && trWrap) {
            trWrap.style.display = '';
            const tbody = document.getElementById('storedTradesTable');
            if (tbody) {
                tbody.innerHTML = data.trades_sample.map(t => `<tr>
                    <td>${t.entry_time || ''}</td>
                    <td>${t.exit_time || ''}</td>
                    <td>${t.direction || ''}</td>
                    <td>${(t.entry_price || 0).toFixed(5)}</td>
                    <td>${(t.exit_price || 0).toFixed(5)}</td>
                    <td class="${(t.profit || 0) >= 0 ? 'positive' : 'negative'}">$${(t.profit || 0).toFixed(2)}</td>
                    <td>${t.exit_reason || ''}</td>
                </tr>`).join('');
            }
        }
    } catch (e) {
        console.error('Failed to load stored result detail:', e);
    }
}

async function downloadSelectedStoredResult() {
    const sel = document.getElementById('storedResultSelect');
    const status = document.getElementById('btStatus');
    const token = getToken();
    if (!sel || !sel.value) {
        setStatusText(status, 'Select a stored result first.', true);
        return;
    }
    if (!token) {
        setStatusText(status, 'Please log in again.', true);
        return;
    }

    try {
        const url = `${API_BASE_URL}/backtest/stored-results/${encodeURIComponent(sel.value)}/download`;
        const response = await fetch(url, {
            method: 'GET',
            headers: { Authorization: `Bearer ${token}` }
        });
        if (!response.ok) {
            setStatusText(status, 'Stored result download failed.', true);
            return;
        }

        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = sel.value;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(objectUrl);

        setStatusText(status, `Downloaded ${sel.value}`);
    } catch (e) {
        console.error('Stored result download failed:', e);
        setStatusText(status, 'Stored result download failed.', true);
    }
}

function renderStoredEquityCurve(labels, equityData, symbol) {
    const ctx = document.getElementById('storedEquityChart');
    if (!ctx) return;

    // Downsample if > 500 points for performance
    let dl = labels, dd = equityData;
    if (equityData.length > 500) {
        const step = Math.ceil(equityData.length / 500);
        dl = []; dd = [];
        for (let i = 0; i < equityData.length; i += step) {
            dl.push(labels[i] || '');
            dd.push(equityData[i]);
        }
        // Always include last point
        if (dd[dd.length - 1] !== equityData[equityData.length - 1]) {
            dl.push(labels[labels.length - 1] || '');
            dd.push(equityData[equityData.length - 1]);
        }
    }

    if (storedEquityChartInstance) {
        storedEquityChartInstance.data.labels = dl;
        storedEquityChartInstance.data.datasets[0].data = dd;
        storedEquityChartInstance.data.datasets[0].label = `${symbol || 'Backtest'} Equity`;
        storedEquityChartInstance.update('none');
        return;
    }

    storedEquityChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: dl,
            datasets: [{
                label: `${symbol || 'Backtest'} Equity`,
                data: dd,
                borderColor: '#00ff87',
                backgroundColor: (context) => {
                    const chart = context.chart;
                    const { ctx: c, chartArea } = chart;
                    if (!chartArea) return 'transparent';
                    const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                    g.addColorStop(0, 'rgba(0, 255, 135, 0.15)');
                    g.addColorStop(1, 'rgba(0, 255, 135, 0)');
                    return g;
                },
                tension: 0.2,
                fill: true,
                pointRadius: 0,
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: true, labels: { color: '#94a3b8', font: { family: "'JetBrains Mono'", size: 11 } } },
                tooltip: {
                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                    titleColor: '#e2e8f0',
                    bodyColor: '#94a3b8',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    callbacks: {
                        label: (ctx) => `Balance: $${ctx.parsed.y.toFixed(2)}`
                    }
                }
            },
            scales: {
                x: {
                    ticks: { color: '#4a5568', maxTicksLimit: 10, font: { family: "'JetBrains Mono'", size: 9 } },
                    grid: { color: 'rgba(255,255,255,0.03)' }
                },
                y: {
                    ticks: {
                        color: '#4a5568',
                        font: { family: "'JetBrains Mono'", size: 10 },
                        callback: (v) => '$' + v.toLocaleString()
                    },
                    grid: { color: 'rgba(255,255,255,0.03)' }
                }
            }
        }
    });
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

// ═══════════ END STORED BACKTEST RESULTS ═══════════

async function downloadOverfitProofPackage() {
    const status = document.getElementById('btStatus');
    const token = getToken();
    if (!token) {
        setStatusText(status, 'Please log in again.', true);
        return;
    }

    setStatusText(status, 'Preparing overfit-proof ZIP...');
    try {
        const url = `${API_BASE_URL}/backtest/overfit-proof/download`;
        const response = await fetch(url, {
            method: 'GET',
            headers: { Authorization: `Bearer ${token}` }
        });

        if (!response.ok) {
            setStatusText(status, 'Overfit-proof ZIP not found. Generate runs first.', true);
            return;
        }

        const blob = await response.blob();
        const cd = response.headers.get('content-disposition') || '';
        let filename = 'overfit_proof_20_package.zip';
        const m = cd.match(/filename="?([^";]+)"?/i);
        if (m && m[1]) filename = m[1];

        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(objectUrl);

        setStatusText(status, `Downloaded ${filename}`);
    } catch (e) {
        console.error('Download overfit-proof package failed:', e);
        setStatusText(status, 'Overfit-proof package download failed.', true);
    }
}

async function downloadBotPackage() {
    const status = document.getElementById('btStatus');
    const token = getToken();
    if (!token) {
        setStatusText(status, 'Please log in again.', true);
        return;
    }

    setStatusText(status, 'Preparing bot package ZIP...');
    try {
        const response = await fetch(`${API_BASE_URL}/bot/download-package`, {
            method: 'GET',
            headers: { Authorization: `Bearer ${token}` }
        });

        if (!response.ok) {
            setStatusText(status, 'Bot package download failed.', true);
            return;
        }

        const blob = await response.blob();
        const cd = response.headers.get('content-disposition') || '';
        let filename = 'zenith_bot_package.zip';
        const m = cd.match(/filename="?([^";]+)"?/i);
        if (m && m[1]) filename = m[1];

        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(objectUrl);

        setStatusText(status, `Downloaded ${filename}`);
    } catch (e) {
        console.error('Download bot package failed:', e);
        setStatusText(status, 'Bot package download failed.', true);
    }
}

async function loadRuns() {
    const table = document.getElementById('runsTable');
    if (!table) return;

    const [runsData, storedData] = await Promise.all([
        apiFetch('/backtest/runs'),
        apiFetch('/backtest/stored-results')
    ]);

    const dbRuns = (runsData?.runs || []).map(r => ({
        key: `run-${r.id}`,
        source: 'Live',
        sourceClass: 'source-live',
        id: r.id,
        symbol: r.symbol || '—',
        mode: r.mode || 'standard',
        status: r.status || 'unknown',
        created_at: r.created_at || '',
        isStored: false
    }));

    const storedRuns = (storedData?.results || []).map(r => ({
        key: `stored-${r.file}`,
        source: 'Stored',
        sourceClass: 'source-stored',
        id: r.file,
        symbol: r.symbol || '—',
        mode: r.mode || 'unknown',
        status: 'stored',
        created_at: r.timestamp || '',
        isStored: true,
        metrics: {
            total_trades: r.total_trades,
            win_rate: r.win_rate,
            total_profit: r.total_profit,
            profit_factor: r.profit_factor,
            max_drawdown: r.max_drawdown,
            return_pct: r.return_pct,
            final_balance: r.final_balance
        }
    }));

    const allRuns = [...dbRuns, ...storedRuns].sort((a, b) => {
        const ta = new Date(a.created_at || 0).getTime() || 0;
        const tb = new Date(b.created_at || 0).getTime() || 0;
        return tb - ta;
    });

    const totalRunsCountEl = document.getElementById('totalRunsCount');
    const completedRunsCountEl = document.getElementById('completedRunsCount');
    const runningRunsCountEl = document.getElementById('runningRunsCount');
    const bestRunPFEl = document.getElementById('bestRunPF');

    const completedCount = allRuns.filter(r => ['done', 'completed', 'stored'].includes(String(r.status).toLowerCase())).length;
    const runningCount = allRuns.filter(r => ['running', 'queued', 'pending'].includes(String(r.status).toLowerCase())).length;
    const bestPf = storedRuns.reduce((max, r) => {
        const pf = Number(r.metrics?.profit_factor || 0);
        return pf > max ? pf : max;
    }, 0);

    if (totalRunsCountEl) totalRunsCountEl.textContent = String(allRuns.length);
    if (completedRunsCountEl) completedRunsCountEl.textContent = String(completedCount);
    if (runningRunsCountEl) runningRunsCountEl.textContent = String(runningCount);
    if (bestRunPFEl) bestRunPFEl.textContent = bestPf > 0 ? num(bestPf, 2) : '—';

    if (!allRuns.length) {
        table.innerHTML = '<tr class="empty-state-row"><td colspan="7">No runs yet</td></tr>';
        return;
    }

    table.innerHTML = allRuns.map(r => {
        const prettyDate = formatRunDate(r.created_at);
        const statusClass = getRunStatusClass(r.status);
        const safeId = String(r.id).replace(/"/g, '&quot;');
        return `<tr>
            <td>${r.id}</td>
            <td><span class="run-source-badge ${r.sourceClass}">${r.source}</span></td>
            <td>${r.symbol}</td>
            <td>${String(r.mode || '').replace(/_/g, ' ')}</td>
            <td><span class="run-status-badge ${statusClass}">${r.status}</span></td>
            <td>${prettyDate}</td>
            <td><button class="btn btn-sm btn-outline" data-runid="${safeId}" data-stored="${r.isStored ? '1' : '0'}">View</button></td>
        </tr>`;
    }).join('');

    table.querySelectorAll('button[data-runid]').forEach(btn => {
        btn.addEventListener('click', () => loadRunResult(btn.dataset.runid, btn.dataset.stored === '1'));
    });
}

function formatRunDate(value) {
    if (!value) return '—';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    return d.toLocaleString([], {
        year: 'numeric',
        month: 'short',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function getRunStatusClass(status) {
    const val = String(status || '').toLowerCase();
    if (val === 'done' || val === 'completed' || val === 'stored') return 'status-ok';
    if (val === 'running' || val === 'pending' || val === 'queued') return 'status-running';
    if (val === 'failed' || val === 'error') return 'status-fail';
    return 'status-neutral';
}

function extractPeriodRows(result) {
    if (!result || typeof result !== 'object') return [];
    const candidates = [];
    const queue = [result];

    while (queue.length) {
        const node = queue.shift();
        if (!node || typeof node !== 'object') continue;

        Object.entries(node).forEach(([k, v]) => {
            if (Array.isArray(v) && v.length && v.every(item => item && typeof item === 'object')) {
                const looksLikePeriods = /period|window|segment|slice/i.test(k) ||
                    v.some(item => item.period != null || item.period_id != null || item.start || item.end || item.profit != null || item.total_profit != null);
                if (looksLikePeriods) candidates.push(v);
            }
            if (v && typeof v === 'object' && !Array.isArray(v)) queue.push(v);
        });
    }

    if (!candidates.length) return [];
    return candidates.sort((a, b) => b.length - a.length)[0].slice(0, 60);
}

function renderPeriodBreakdown(result) {
    const rows = extractPeriodRows(result);
    if (!rows.length) return '';

    const profitableCount = rows.filter(p => Number(p.profit ?? p.total_profit ?? 0) > 0).length;
    const tableRows = rows.map((p, idx) => {
        const label = p.period ?? p.period_id ?? p.name ?? p.label ?? `P${idx + 1}`;
        const trades = p.trades ?? p.total_trades ?? p.n_trades ?? '—';
        const winRate = p.win_rate ?? p.wr ?? null;
        const profit = Number(p.profit ?? p.total_profit ?? p.pnl ?? 0);
        const drawdown = Number(p.max_drawdown ?? p.drawdown ?? p.dd ?? 0);
        return `<tr>
            <td>${label}</td>
            <td>${trades}</td>
            <td>${winRate == null ? '—' : `${num(winRate)}%`}</td>
            <td class="${profit >= 0 ? 'positive' : 'negative'}">$${num(profit)}</td>
            <td>$${num(drawdown)}</td>
        </tr>`;
    }).join('');

    return `
        <div class="run-periods-wrap">
            <div class="run-periods-head">
                <h4><i class="fas fa-layer-group"></i> Period Breakdown</h4>
                <div class="run-periods-meta">${rows.length} periods • ${profitableCount} profitable</div>
            </div>
            <div class="table-scroll-wrap">
                <table class="data-table run-periods-table">
                    <thead>
                        <tr><th>Period</th><th>Trades</th><th>Win Rate</th><th>Profit</th><th>Max DD</th></tr>
                    </thead>
                    <tbody>${tableRows}</tbody>
                </table>
            </div>
        </div>`;
}

async function loadRunResult(runId, isStored = false) {
    const panel = document.getElementById('runDetailPanel');
    const el = document.getElementById('runDetailContent');
    if (!panel || !el) return;

    panel.style.display = 'block';
    el.innerHTML = '<div class="empty-state-block"><div class="empty-state-title">Loading result...</div></div>';

    if (isStored) {
        const data = await apiFetch(`/backtest/stored-results/${encodeURIComponent(runId)}`);
        if (!data) {
            el.innerHTML = '<div class="empty-state-block"><div class="empty-state-title">Failed to load stored result</div></div>';
            return;
        }

        const metricsPayload = {
            mode: data.mode || 'standard',
            metrics: data.metrics || {}
        };
        const periodsHtml = renderPeriodBreakdown(data);

        el.innerHTML = `
            <div class="run-detail-head">
                <div><strong>${data.symbol || 'Stored Result'}</strong> · ${String(data.mode || 'unknown').replace(/_/g, ' ')}</div>
                <div class="run-detail-sub">${formatRunDate(data.timestamp)} · file: ${data.file}</div>
            </div>
            ${renderRunResult(metricsPayload)}
            ${periodsHtml}
            <div class="run-detail-actions">
                <button class="btn btn-outline btn-sm" id="downloadRunDetailBtn"><i class="fas fa-download"></i> Download JSON</button>
            </div>
        `;

        const dlBtn = document.getElementById('downloadRunDetailBtn');
        if (dlBtn) {
            dlBtn.addEventListener('click', async () => {
                const token = getToken();
                if (!token) return;
                const url = `${API_BASE_URL}/backtest/stored-results/${encodeURIComponent(runId)}/download`;
                const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
                if (!resp.ok) return;
                const blob = await resp.blob();
                const objUrl = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = objUrl;
                a.download = runId;
                document.body.appendChild(a);
                a.click();
                a.remove();
                URL.revokeObjectURL(objUrl);
            });
        }
        return;
    }

    const data = await apiFetch(`/backtest/results/${runId}`);
    if (!data) {
        el.innerHTML = '<div class="empty-state-block"><div class="empty-state-title">Failed to load run result</div></div>';
        return;
    }
    if (!data.result) {
        el.innerHTML = `<div class="empty-state-block"><div class="empty-state-title">No result yet</div><div class="empty-state-desc">${data.log || data.status || 'Run is still processing.'}</div></div>`;
        return;
    }

    el.innerHTML = `${renderRunResult(data.result)}${renderPeriodBreakdown(data.result)}`;
}

async function loadRobustness() {
    const el = document.getElementById('robustnessResult');
    if (!el) return;
    const data = await apiFetch('/backtest/robustness');
    if (!data || !data.summary) { el.textContent = 'Robustness: no data yet'; return; }
    const s = data.summary;
    const rows = (data.details || []).slice(0, 28).map(d =>
        `<tr><td>${d.symbol}</td><td>${d.mode}</td><td>${num(d.value)}</td><td class="${d.passed ? 'positive' : 'negative'}">${d.passed ? 'PASS' : 'FAIL'}</td></tr>`
    ).join('');
    el.innerHTML = `<div><strong>Robustness Score:</strong> ${s.score}% | <strong>Passed:</strong> ${s.passed}/${s.checks} | <strong>Verdict:</strong> ${s.verdict}</div>
        <table class="robust-table"><thead><tr><th>Symbol</th><th>Mode</th><th>Value</th><th>Status</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="4">No checks yet</td></tr>'}</tbody></table>`;
}

async function exportReportPdf() {
    const report = await apiFetch('/backtest/report-data');
    if (!report) return;
    const html = `<html><head><title>Zenith Backtest Report</title>
        <style>body{font-family:'Inter',Arial,sans-serif;padding:24px;background:#0a1018;color:#e8ecf1;}
        h1{background:linear-gradient(135deg,#00ff87,#60efff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:4px;}
        .meta{color:#6b7a8d;font-size:0.9rem;} table{width:100%;border-collapse:collapse;margin-top:16px;}
        td,th{border:1px solid #1a2535;padding:10px;text-align:left;font-size:0.9rem;} th{background:#111a25;color:#6b7a8d;text-transform:uppercase;font-size:0.75rem;letter-spacing:0.5px;}
        .ok{color:#00ff87;} .bad{color:#ff6b6b;}</style></head>
        <body><h1>Zenith Backtest Report</h1>
        <div class="meta">Generated: ${report.generated_at}</div>
        <div class="meta">User: ${report.user?.email || ''}</div>
        <p style="margin:16px 0;">Total runs: <b>${report.runs_total}</b> | Completed: <b>${report.runs_done}</b></p>
        <h3 style="color:#60efff;">Robustness</h3>
        <p>Score: <b style="color:#00ff87;">${report.robustness?.summary?.score || 0}%</b> | Verdict: <b>${report.robustness?.summary?.verdict || 'n/a'}</b></p>
        <table><thead><tr><th>Symbol</th><th>Mode</th><th>Value</th><th>Status</th></tr></thead><tbody>
        ${(report.robustness?.details || []).map(d => `<tr><td>${d.symbol}</td><td>${d.mode}</td><td>${num(d.value)}</td><td class="${d.passed ? 'ok' : 'bad'}">${d.passed ? 'PASS' : 'FAIL'}</td></tr>`).join('')}
        </tbody></table><script>window.onload=()=>window.print();<\/script></body></html>`;
    const w = window.open('', '_blank');
    if (!w) return;
    w.document.write(html);
    w.document.close();
}

async function loadMt5Status() {
    // Load bot control panel from /api/status (same endpoint)
    const status = statusSnapshot || await apiFetch('/status');
    if (!status) return;

    const isRunning = status.status === 'running';
    const iconEl = document.getElementById('mt5StatusIcon');
    const titleEl = document.getElementById('mt5StatusTitle');
    const subEl = document.getElementById('mt5StatusSubtitle');
    const badgeEl = document.getElementById('mt5StatusBadge');
    const healthIcon = document.getElementById('mt5HealthIcon');
    const healthStatus = document.getElementById('mt5HealthStatus');

    if (isRunning) {
        if (iconEl) { iconEl.classList.remove('disconnected'); iconEl.classList.add('connected'); iconEl.innerHTML = '<i class="fas fa-robot"></i>'; }
        if (titleEl) titleEl.textContent = 'Bot Running';
        if (subEl) subEl.textContent = status.runtime_message || 'Scanning markets...';
        if (badgeEl) { badgeEl.classList.remove('disconnected'); badgeEl.classList.add('connected'); badgeEl.innerHTML = '<span class="status-dot"></span> Online'; }
        if (healthIcon) { healthIcon.classList.remove('warning'); healthIcon.classList.add('success'); healthIcon.innerHTML = '<i class="fas fa-check-circle"></i>'; }
        if (healthStatus) healthStatus.textContent = 'Running';
    } else {
        if (iconEl) { iconEl.classList.remove('connected'); iconEl.classList.add('disconnected'); iconEl.innerHTML = '<i class="fas fa-robot"></i>'; }
        if (titleEl) titleEl.textContent = 'Bot Stopped';
        if (subEl) subEl.textContent = 'Use the Start Bot button above to begin trading';
        if (badgeEl) { badgeEl.classList.remove('connected'); badgeEl.classList.add('disconnected'); badgeEl.innerHTML = '<span class="status-dot"></span> Offline'; }
        if (healthIcon) { healthIcon.classList.remove('success'); healthIcon.classList.add('warning'); healthIcon.innerHTML = '<i class="fas fa-times-circle"></i>'; }
        if (healthStatus) healthStatus.textContent = 'Stopped';
    }

    // --- System Health: API Connection ---
    const apiIcon = document.getElementById('apiHealthIcon');
    const apiStat = document.getElementById('apiHealthStatus');
    // If we got here, API responded successfully
    if (apiIcon) { apiIcon.classList.remove('warning'); apiIcon.classList.add('success'); }
    if (apiStat) apiStat.textContent = 'Online';

    // --- System Health: Data Storage ---
    const dbIcon = document.getElementById('dbHealthIcon');
    const dbStat = document.getElementById('dbHealthStatus');
    if (dbIcon) { dbIcon.classList.remove('warning'); dbIcon.classList.add('success'); }
    if (dbStat) dbStat.textContent = 'Healthy';

    // --- System Health: Risk Manager ---
    const riskIcon = document.getElementById('riskHealthIcon');
    const riskStat = document.getElementById('riskHealthStatus');
    if (botConfigCache) {
        if (riskIcon) { riskIcon.classList.remove('warning'); riskIcon.classList.add('success'); }
        if (riskStat) riskStat.textContent = 'Active';
    } else {
        if (riskIcon) { riskIcon.classList.remove('success'); riskIcon.classList.add('warning'); }
        if (riskStat) riskStat.textContent = 'Not loaded';
    }

    // Populate bot control detail fields
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val || '\u2014'; };
    set('botCtrlState', status.runtime_state || (isRunning ? 'running' : 'stopped'));
    const syms = status.enabled_symbols;
    set('botCtrlSymbols', Array.isArray(syms) && syms.length ? syms.join(', ') : '\u2014');
    set('botCtrlPositions', status.open_positions ?? '\u2014');
    set('botCtrlSignals', status.signals_detected ?? '\u2014');
    set('botCtrlOpened', status.positions_opened ?? '\u2014');
    set('botCtrlFailed', status.failed_orders ?? '\u2014');
    set('botCtrlMessage', status.runtime_message || '\u2014');
    if (typeof status.last_scan_age_s === 'number') {
        set('botCtrlLastScan', status.last_scan_age_s < 60 ? `${status.last_scan_age_s}s ago` : `${Math.round(status.last_scan_age_s / 60)}m ago`);
    } else {
        set('botCtrlLastScan', '\u2014');
    }
}

// Legacy stubs — MT5 connect/disconnect removed (bot runs independently)
async function connectMt5() {}
async function disconnectMt5() {}

function renderBotSymbols(cfg) {
    const grid = document.getElementById('botSymbolsGrid');
    if (!grid || !cfg) return;

    const enabled = new Set(cfg.enabled_symbols || []);
    const symbols = cfg.available_symbols || [];

    grid.innerHTML = symbols.map((symbol) => {
        const active = enabled.has(symbol);
        return `<button type="button" class="bot-symbol-chip ${active ? 'active' : 'inactive'}" data-symbol="${symbol}" aria-pressed="${active ? 'true' : 'false'}">
            <span class="chip-main">
                <span class="chip-dot"></span>
                <span class="chip-symbol">${symbol}</span>
            </span>
            <span class="chip-state">${active ? 'ON' : 'OFF'}</span>
        </button>`;
    }).join('');

    grid.querySelectorAll('.bot-symbol-chip').forEach((el) => {
        el.addEventListener('click', async () => {
            const symbol = el.getAttribute('data-symbol');
            if (!symbol || !botConfigCache) return;
            if (el.classList.contains('is-saving')) return;

            const prevEnabled = [...(botConfigCache.enabled_symbols || [])];
            const enabledSet = new Set(botConfigCache.enabled_symbols || []);
            const nextActive = !enabledSet.has(symbol);

            if (nextActive) enabledSet.add(symbol);
            else enabledSet.delete(symbol);

            el.classList.add('is-saving');
            el.classList.toggle('active', nextActive);
            el.classList.toggle('inactive', !nextActive);
            el.setAttribute('aria-pressed', nextActive ? 'true' : 'false');
            const state = el.querySelector('.chip-state');
            if (state) state.textContent = nextActive ? 'ON' : 'OFF';

            botConfigCache.enabled_symbols = [...enabledSet];
            const saved = await saveBotConfig(
                { enabled_symbols: [...enabledSet] },
                `${symbol} ${nextActive ? 'enabled' : 'disabled'}`,
                { silentRender: true }
            );

            el.classList.remove('is-saving');
            if (!saved) {
                botConfigCache.enabled_symbols = prevEnabled;
                renderBotSymbols(botConfigCache);
            }
        });
    });
}

async function loadBotConfig() {
    const cfg = await apiFetch('/bot/config');
    if (!cfg) return;

    botConfigCache = cfg;
    const riskInput = document.getElementById('botRiskInput');
    if (riskInput) riskInput.value = Number(cfg.risk_percent || 1).toFixed(2);
    const dailyDdInput = document.getElementById('botDailyDdInput');
    if (dailyDdInput) dailyDdInput.value = Number(cfg.max_daily_drawdown_pct || 5).toFixed(2);
    const marginCapInput = document.getElementById('botMarginCapInput');
    if (marginCapInput) marginCapInput.value = Number(cfg.max_margin_usage_pct || 20).toFixed(2);
    const ddAdjInput = document.getElementById('botDdAdjustmentInput');
    if (ddAdjInput) ddAdjInput.value = Number(cfg.daily_drawdown_adjustment_usd || 0).toFixed(2);
    renderBotSymbols(cfg);

    // --- Update Risk Management meters ---
    updateRiskMeters(cfg);
}

function updateRiskMeters(cfg) {
    if (!cfg) return;
    // Position risk meter: shows configured risk_percent as % of max (2.0%)
    const riskPct = Number(cfg.risk_percent || 0);
    const maxRiskPct = 2.0; // MAX_BOT_RISK_PERCENT
    const positionFill = document.getElementById('riskPositionFill');
    const positionValue = document.getElementById('riskPositionValue');
    if (positionFill) positionFill.style.width = Math.min(100, (riskPct / maxRiskPct) * 100) + '%';
    if (positionValue) positionValue.textContent = riskPct > 0 ? `${riskPct.toFixed(2)}% per trade` : 'No data';

    // Daily drawdown meter: shows usage relative to max_daily_drawdown_pct
    const maxDD = Number(cfg.max_daily_drawdown_pct || 5);
    const ddAdj = Math.abs(Number(cfg.daily_drawdown_adjustment_usd || 0));
    // We can estimate usage from adjustment; if adjustment is 0 it means no drawdown today
    const dailyFill = document.getElementById('riskDailyFill');
    const dailyValue = document.getElementById('riskDailyValue');
    if (dailyFill) dailyFill.style.width = ddAdj > 0 ? Math.min(100, 30) + '%' : '0%'; // placeholder — actual usage would need balance
    if (dailyValue) dailyValue.textContent = `Limit: ${maxDD.toFixed(1)}%`;
}

async function saveBotConfig(payload, successMessage = 'Saved', options = {}) {
    const statusEl = document.getElementById('botConfigStatus');
    const res = await apiFetch('/bot/config', {
        method: 'POST',
        body: JSON.stringify(payload),
    });

    if (!res || res.success === false) {
        setStatusText(statusEl, 'Failed to save bot config', true);
        return false;
    }

    botConfigCache = res;
    const riskInput = document.getElementById('botRiskInput');
    if (riskInput) riskInput.value = Number(res.risk_percent || 1).toFixed(2);
    const dailyDdInput = document.getElementById('botDailyDdInput');
    if (dailyDdInput) dailyDdInput.value = Number(res.max_daily_drawdown_pct || 5).toFixed(2);
    const marginCapInput = document.getElementById('botMarginCapInput');
    if (marginCapInput) marginCapInput.value = Number(res.max_margin_usage_pct || 20).toFixed(2);
    const ddAdjInput = document.getElementById('botDdAdjustmentInput');
    if (ddAdjInput) ddAdjInput.value = Number(res.daily_drawdown_adjustment_usd || 0).toFixed(2);
    if (!options.silentRender) renderBotSymbols(res);
    setStatusText(statusEl, successMessage);
    return true;
}

async function saveBotRisk() {
    const riskInput = document.getElementById('botRiskInput');
    if (!riskInput) return;
    const risk = Number(riskInput.value);
    if (!Number.isFinite(risk) || risk < 0.10 || risk > 2.00) {
        setStatusText(document.getElementById('botConfigStatus'), 'Risk must be 0.10 - 2.00', true);
        return;
    }
    await saveBotConfig({ risk_percent: risk }, `Risk saved (${risk.toFixed(2)}%)`);
}

async function saveBotGuards() {
    const dailyDdInput = document.getElementById('botDailyDdInput');
    const marginCapInput = document.getElementById('botMarginCapInput');
    const ddAdjInput = document.getElementById('botDdAdjustmentInput');
    if (!dailyDdInput || !marginCapInput || !ddAdjInput) return;

    const dailyDd = Number(dailyDdInput.value);
    const marginCap = Number(marginCapInput.value);
    const ddAdj = Number(ddAdjInput.value);

    if (!Number.isFinite(dailyDd) || dailyDd < 0.5 || dailyDd > 25) {
        setStatusText(document.getElementById('botConfigStatus'), 'Daily DD must be 0.5 - 25.0', true);
        return;
    }
    if (!Number.isFinite(marginCap) || marginCap < 5 || marginCap > 95) {
        setStatusText(document.getElementById('botConfigStatus'), 'Margin cap must be 5 - 95', true);
        return;
    }
    if (!Number.isFinite(ddAdj)) {
        setStatusText(document.getElementById('botConfigStatus'), 'Daily DD adjustment must be a valid number', true);
        return;
    }

    await saveBotConfig({
        max_daily_drawdown_pct: dailyDd,
        max_margin_usage_pct: marginCap,
        daily_drawdown_adjustment_usd: ddAdj,
    }, `Guard limits saved (DD ${dailyDd.toFixed(2)}%, Margin ${marginCap.toFixed(1)}%)`);
}

async function resetDrawdownAdjustment() {
    const statusEl = document.getElementById('botConfigStatus');
    const res = await apiFetch('/bot/config/reset-drawdown', { method: 'POST' });
    if (!res || res.success === false) {
        setStatusText(statusEl, 'Failed to reset daily drawdown adjustment', true);
        return;
    }
    botConfigCache = res;
    const ddAdjInput = document.getElementById('botDdAdjustmentInput');
    if (ddAdjInput) ddAdjInput.value = Number(res.daily_drawdown_adjustment_usd || 0).toFixed(2);
    setStatusText(statusEl, `Daily DD reset applied (PnL estimate: ${Number(res.daily_pnl_estimate || 0).toFixed(2)} USD)`);
}

async function setAllSymbols(enabled) {
    if (!botConfigCache) await loadBotConfig();
    if (!botConfigCache) return;

    const symbols = enabled ? (botConfigCache.available_symbols || []) : [];
    const prevEnabled = [...(botConfigCache.enabled_symbols || [])];
    botConfigCache.enabled_symbols = [...symbols];
    renderBotSymbols(botConfigCache);

    const saved = await saveBotConfig(
        { enabled_symbols: symbols },
        enabled ? 'All symbols enabled' : 'All symbols disabled'
    );
    if (!saved) {
        botConfigCache.enabled_symbols = prevEnabled;
        renderBotSymbols(botConfigCache);
    }
}

async function loadBotMonitor() {
    if (typeof window.__liveMonitorAuto === 'boolean' && window.__liveMonitorAuto === false) {
        return;
    }

    const [activity, positionsRes, logsRes] = await Promise.all([
        apiFetch('/bot/activity?lines=220'),
        apiFetch('/bot/positions'),
        apiFetch('/bot/logs?lines=140'),
    ]);

    const summary = activity?.summary || {};
    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = String(value);
    };

    setText('liveSignalsCount', summary.signals || 0);
    setText('liveOpenedCount', summary.opened || 0);
    setText('liveFailedCount', summary.failed || 0);

    const positions = positionsRes?.positions || [];
    const posMsg = positionsRes?.message || '';
    const openPosCount = (typeof positionsRes?.open_positions_count === 'number')
        ? positionsRes.open_positions_count
        : positions.length;
    setText('liveOpenPositionsCount', openPosCount);

    const posTable = document.getElementById('liveOpenPositionsTable');
    if (posTable) {
        if (!positions.length) {
            const hint = posMsg ? `<br><small style="color:var(--z-text-3);font-size:0.75rem">${posMsg}</small>` : '';
            posTable.innerHTML = `<tr class="empty-state-row"><td colspan="7">No open positions${hint}</td></tr>`;
        } else {
            posTable.innerHTML = positions.map((p) => `
                <tr>
                    <td>${p.ticket}</td>
                    <td><strong>${p.symbol}</strong></td>
                    <td><span class="badge badge-${p.direction === 'BUY' ? 'success' : 'danger'}">${p.direction}</span></td>
                    <td>${Number(p.volume).toFixed(2)}</td>
                    <td>${Number(p.open_price).toFixed(5)}</td>
                    <td>${Number(p.current_price).toFixed(5)}</td>
                    <td class="${Number(p.profit) >= 0 ? 'positive' : 'negative'}">${Number(p.profit).toFixed(2)}</td>
                </tr>
            `).join('');
        }
    }

    const events = activity?.events || [];
    const eventTable = document.getElementById('botActivityTable');
    if (eventTable) {
        if (!events.length) {
            eventTable.innerHTML = '<tr class="empty-state-row"><td colspan="3">No activity events yet</td></tr>';
        } else {
            eventTable.innerHTML = events.slice(-70).reverse().map((ev) => `
                <tr>
                    <td><span class="badge badge-${ev.type === 'opened' ? 'success' : (ev.type === 'failed' ? 'danger' : 'neutral')}">${ev.type}</span></td>
                    <td>${ev.symbol || '—'}</td>
                    <td>${ev.message || ''}</td>
                </tr>
            `).join('');
        }
    }

    const rawEl = document.getElementById('botRawLog');
    if (rawEl) {
        const next = logsRes?.logs || 'No logs yet.';
        if (window.__lastBotRawLog !== next) {
            rawEl.textContent = next;
            window.__lastBotRawLog = next;
        }
    }

    // --- Trading Activity Timeline (Overview section) ---
    const timeline = document.getElementById('tradingTimeline');
    if (timeline) {
        if (!events.length) {
            timeline.innerHTML = `<div class="timeline-item">
                <div class="timeline-icon"><i class="fas fa-clock"></i></div>
                <div class="timeline-content">
                    <h4>Waiting for Bot</h4>
                    <p>Start the bot to see live trading events</p>
                </div>
            </div>`;
        } else {
            timeline.innerHTML = events.slice(-5).reverse().map(ev => {
                const iconClass = ev.type === 'opened' ? 'fa-arrow-up' : ev.type === 'closed' ? 'fa-arrow-down' : ev.type === 'signal' ? 'fa-search' : ev.type === 'failed' ? 'fa-exclamation-triangle' : 'fa-info-circle';
                const colorCls = ev.type === 'opened' ? 'success' : ev.type === 'failed' ? 'warning' : '';
                return `<div class="timeline-item">
                    <div class="timeline-icon ${colorCls}"><i class="fas ${iconClass}"></i></div>
                    <div class="timeline-content">
                        <h4>${ev.symbol || ev.type}</h4>
                        <p>${ev.message || ev.type}</p>
                    </div>
                </div>`;
            }).join('');
        }
    }

    // --- Market Overview (Overview section) ---
    // Populate from open positions / bridge data if available
    const mktSymbols = { 'EURUSD': 'mktEURUSD', 'GBPUSD': 'mktGBPUSD', 'USDJPY': 'mktUSDJPY', 'XAUUSD': 'mktXAUUSD' };
    if (positions.length) {
        for (const pos of positions) {
            const sym = (pos.symbol || '').replace(/[^A-Z]/g, '').substring(0, 6);
            const elId = mktSymbols[sym];
            if (elId) {
                const priceEl = document.getElementById(elId);
                if (priceEl) priceEl.textContent = Number(pos.current_price || pos.open_price || 0).toFixed(sym === 'XAUUSD' ? 2 : sym === 'USDJPY' ? 3 : 5);
                const chgEl = document.getElementById(elId + 'chg');
                if (chgEl) {
                    const pnl = Number(pos.profit || 0);
                    chgEl.textContent = (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
                    chgEl.className = 'market-change ' + (pnl >= 0 ? 'positive' : 'negative');
                }
            }
        }
    }
}

async function logout() {
    await apiFetch('/auth/logout', { method: 'POST' });
    localStorage.removeItem('zenith_token');
    localStorage.removeItem('ultima_token');
    localStorage.removeItem('zenith_email');
    window.location.href = 'index.html';
}

// init
function initEvents() {
    const bind = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener('click', fn); };

    bind('refreshBtn', async () => {
        const btn = document.getElementById('refreshBtn');
        btn.querySelector('i').classList.add('fa-spin');
        await Promise.all([loadMetrics(), loadStatus(), loadRuns(), loadTrades(), loadEquity(), loadRobustness(), loadMt5Status(), loadBotConfig(), loadBotMonitor()]);
        setTimeout(() => btn.querySelector('i').classList.remove('fa-spin'), 700);
    });

    bind('downloadBotPackageBtnTop', downloadBotPackage);
    bind('showCredsBtn', openCredsModal);
    bind('showCredsBtn2', openCredsModal);
    bind('credsModalCloseBtn', closeCredsModal);
    bind('credsModalBackdrop', (e) => { if (e.target.id === 'credsModalBackdrop') closeCredsModal(); });
    bind('credsCopyUrlBtn', () => { const v = document.getElementById('credsDashUrl')?.value; if (v) copyText(v, document.getElementById('credsCopyUrlBtn')); });
    bind('credsCopyKeyBtn', () => { const v = document.getElementById('credsApiKey')?.value; if (v) copyText(v, document.getElementById('credsCopyKeyBtn')); });
    bind('credsRegenKeyBtn', regenCredsKey);
    bind('runBacktestBtn', () => {
        if (btRunInProgress) return;
        runBacktest();
    });
    bind('btPauseBtn', toggleBtPause);
    bind('btStopBtn', skipToEnd);
    bind('mt5ConnectBtn', connectMt5);
    bind('mt5DisconnectBtn', disconnectMt5);
    bind('runMonteCarloBtn', runMonteCarlo);
    bind('runRobustness20Btn', runRobustness20);
    bind('downloadPresentationBtn', downloadPresentationPackage);
    bind('downloadOverfitProofBtn', downloadOverfitProofPackage);
    bind('downloadBotPackageBtn', downloadBotPackage);
    bind('logoutBtn', logout);
    bind('saveBotRiskBtn', saveBotRisk);
    bind('saveBotGuardsBtn', saveBotGuards);
    bind('resetDrawdownAdjBtn', resetDrawdownAdjustment);
    bind('enableAllSymbolsBtn', () => setAllSymbols(true));
    bind('disableAllSymbolsBtn', () => setAllSymbols(false));
    bind('reloadBotConfigBtn', loadBotConfig);
    bind('refreshLiveMonitorBtn', loadBotMonitor);
    bind('toggleLiveMonitorAutoBtn', () => {
        window.__liveMonitorAuto = !(window.__liveMonitorAuto ?? true);
        const stateEl = document.getElementById('liveMonitorAutoState');
        if (stateEl) stateEl.textContent = (window.__liveMonitorAuto ? 'On' : 'Off');
    });
    bind('startStopBtn', handleStartStop);
    bind('saveTelegramConfigBtn', saveTelegramConfig);
    bind('generateBotApiKeyBtn', generateBotApiKey);
    bind('revokeBotApiKeyBtn', revokeBotApiKey);
    bind('copyBotApiKeyBtn', () => copyToClipboard('botApiKeyDisplay'));
    bind('copyDashUrlBtn', () => copyToClipboard('dashboardUrlDisplay'));
    bind('mt5HelpCloseBtn', closeMt5HelpPopup);
    bind('mt5HelpOkBtn', closeMt5HelpPopup);
    bind('mt5HelpBackdrop', closeMt5HelpPopup);
}

// ─── Monte Carlo Simulation ───
async function runMonteCarlo() {
    const btn = document.getElementById('runMonteCarloBtn');
    const resultsEl = document.getElementById('mcResults');
    const loadingEl = document.getElementById('mcLoading');
    const emptyEl = document.getElementById('mcEmpty');

    // need trades from the last backtest
    if (!btTrades || btTrades.length === 0) {
        if (emptyEl) {
            emptyEl.querySelector('.empty-state-title').textContent = 'No Trades Available';
            emptyEl.querySelector('.empty-state-desc').innerHTML = 'Run a backtest first so there are trades to shuffle.';
        }
        return;
    }

    // show loading
    if (emptyEl) emptyEl.style.display = 'none';
    if (resultsEl) resultsEl.style.display = 'none';
    if (loadingEl) loadingEl.style.display = 'flex';
    if (btn) { btn.classList.add('is-loading'); btn.disabled = true; }

    // run client-side monte carlo (fast, no server needed)
    const ITERATIONS = 200;
    const INITIAL = 10000;
    const profits = btTrades.map(t => t.profit || 0);
    const endingBalances = [];
    const drawdowns = [];

    await new Promise(resolve => setTimeout(resolve, 50)); // let UI update

    for (let i = 0; i < ITERATIONS; i++) {
        // fisher-yates shuffle
        const shuffled = profits.slice();
        for (let j = shuffled.length - 1; j > 0; j--) {
            const k = Math.floor(Math.random() * (j + 1));
            [shuffled[j], shuffled[k]] = [shuffled[k], shuffled[j]];
        }

        let balance = INITIAL;
        let peak = INITIAL;
        let maxDD = 0;
        for (const p of shuffled) {
            balance += p;
            if (balance > peak) peak = balance;
            const dd = (peak - balance) / peak;
            if (dd > maxDD) maxDD = dd;
        }
        endingBalances.push(balance);
        drawdowns.push(maxDD * 100);
    }

    endingBalances.sort((a, b) => a - b);
    const p10 = endingBalances[Math.floor(ITERATIONS * 0.10)];
    const p50 = endingBalances[Math.floor(ITERATIONS * 0.50)];
    const p90 = endingBalances[Math.floor(ITERATIONS * 0.90)];
    const avgDD = drawdowns.reduce((a, b) => a + b, 0) / drawdowns.length;
    const baseProfit = btStats.pnl;

    // hide loading, show results
    if (loadingEl) loadingEl.style.display = 'none';
    if (resultsEl) resultsEl.style.display = 'block';
    if (btn) { btn.classList.remove('is-loading'); btn.disabled = false; btn.classList.add('is-success'); setTimeout(() => btn.classList.remove('is-success'), 1500); }

    // populate percentile values
    const minBal = endingBalances[0];
    const maxBal = endingBalances[endingBalances.length - 1];
    const range = maxBal - minBal || 1;

    const setEl = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
    const setBar = (id, pct) => { const e = document.getElementById(id); if (e) e.style.width = Math.max(4, pct) + '%'; };

    setEl('mcP10', '$' + num(p10));
    setEl('mcP50', '$' + num(p50));
    setEl('mcP90', '$' + num(p90));
    setBar('mcP10Bar', ((p10 - minBal) / range) * 100);
    setBar('mcP50Bar', ((p50 - minBal) / range) * 100);
    setBar('mcP90Bar', ((p90 - minBal) / range) * 100);

    setEl('mcAvgDD', num(avgDD) + '%');
    setEl('mcIterations', ITERATIONS.toString());
    setEl('mcBaseProfit', (baseProfit >= 0 ? '+$' : '-$') + num(Math.abs(baseProfit)));
    
    const verdictEl = document.getElementById('mcVerdict');
    if (verdictEl) {
        if (p10 >= INITIAL) {
            verdictEl.textContent = 'Robust ✓';
            verdictEl.className = 'mc-stat-value positive';
        } else if (p50 >= INITIAL) {
            verdictEl.textContent = 'Moderate';
            verdictEl.className = 'mc-stat-value';
        } else {
            verdictEl.textContent = 'Weak ✗';
            verdictEl.className = 'mc-stat-value negative';
        }
    }

    setEl('mcHistMin', '$' + num(minBal));
    setEl('mcHistMax', '$' + num(maxBal));

    // build histogram
    const histEl = document.getElementById('mcHistogram');
    if (histEl) {
        const BINS = 25;
        const binSize = range / BINS;
        const bins = new Array(BINS).fill(0);
        for (const b of endingBalances) {
            let idx = Math.floor((b - minBal) / binSize);
            if (idx >= BINS) idx = BINS - 1;
            bins[idx]++;
        }
        const maxBin = Math.max(...bins);
        histEl.innerHTML = bins.map((count, i) => {
            const pct = maxBin > 0 ? (count / maxBin) * 100 : 0;
            const binVal = minBal + (i + 0.5) * binSize;
            const colorClass = binVal < INITIAL ? 'mc-hist-red' : binVal < INITIAL * 1.05 ? 'mc-hist-yellow' : 'mc-hist-green';
            return `<div class="mc-hist-bar ${colorClass}" style="height: ${Math.max(2, pct)}%;" title="${count} outcomes around $${num(binVal)}"></div>`;
        }).join('');
    }
}

// sidebar nav
function initSidebarNav() {
    const navLinks = Array.from(document.querySelectorAll('.sidebar-nav a[data-section]'));
    const sections = Array.from(document.querySelectorAll('.dashboard-main > section'));
    const sidebar = document.querySelector('.sidebar');
    const toggleBtn = document.getElementById('mobileNavToggle');
    const validSections = new Set(sections.map(sec => sec.id));
    
    function switchToSection(sectionId) {
        if (!sectionId || !validSections.has(sectionId)) return;

        sections.forEach(sec => {
            sec.style.display = 'none';
            sec.classList.remove('active');
        });
        
        const target = document.getElementById(sectionId);
        if (target) {
            target.style.display = 'flex';
            target.classList.add('active');
        }
        
        navLinks.forEach(link => link.classList.remove('active'));
        const activeLink = document.querySelector(`.sidebar-nav a[data-section="${sectionId}"]`);
        if (activeLink) activeLink.classList.add('active');
        
        if (sidebar && toggleBtn) {
            sidebar.classList.remove('active');
            toggleBtn.classList.remove('active');
        }
    }

    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const sectionId = link.getAttribute('data-section');
            if (sectionId) {
                switchToSection(sectionId);
                if (window.location.hash !== `#${sectionId}`) {
                    history.replaceState(null, '', `#${sectionId}`);
                }
            }
        });
    });

    // Also wire up any other data-section links in the page (e.g. "View All", "New Backtest")
    document.querySelectorAll('[data-section]').forEach(el => {
        if (navLinks.indexOf(el) !== -1) return; // skip sidebar links already bound
        el.addEventListener('click', (e) => {
            const sectionId = el.getAttribute('data-section');
            if (sectionId && validSections.has(sectionId)) {
                e.preventDefault();
                switchToSection(sectionId);
                history.replaceState(null, '', `#${sectionId}`);
            }
        });
    });

    window.addEventListener('hashchange', () => {
        const sectionId = (window.location.hash || '').replace('#', '');
        if (sectionId) switchToSection(sectionId);
    });

    if (toggleBtn) {
        toggleBtn.addEventListener('click', () => {
            if (sidebar) sidebar.classList.toggle('active');
            toggleBtn.classList.toggle('active');
        });
    }

    sections.forEach(sec => sec.style.display = 'none');
    const initialSection = (window.location.hash || '').replace('#', '');
    if (initialSection && validSections.has(initialSection)) {
        switchToSection(initialSection);
    } else {
        switchToSection('overview');
    }
}

// === Dashboard cosmic effects ===
function initDashNebula() {
    const container = document.getElementById('dashNebulaCanvas');
    if (!container) return;
    if (perfModeEnabled) return;
    const canvas = document.createElement('canvas');
    canvas.style.cssText = 'width:100%;height:100%;';
    container.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    let w, h;
    const stars = [];
    const STAR_COUNT = 70;

    function resize() {
        w = canvas.width = window.innerWidth;
        h = canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    const colors = ['rgba(0,255,135,', 'rgba(96,239,255,', 'rgba(167,139,250,'];
    for (let i = 0; i < STAR_COUNT; i++) {
        stars.push({
            x: Math.random() * w,
            y: Math.random() * h,
            r: Math.random() * 1.2 + 0.3,
            c: colors[Math.floor(Math.random() * 3)],
            speed: Math.random() * 0.15 + 0.02,
            phase: Math.random() * Math.PI * 2
        });
    }

    let lastFrame = 0;
    function draw(t) {
        if (perfModeEnabled) return;
        if (t - lastFrame < 40) {
            requestAnimationFrame(draw);
            return;
        }
        lastFrame = t;
        ctx.clearRect(0, 0, w, h);
        for (const s of stars) {
            const twinkle = 0.4 + 0.6 * Math.sin(t * 0.001 * s.speed * 10 + s.phase);
            ctx.beginPath();
            ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
            ctx.fillStyle = s.c + twinkle.toFixed(2) + ')';
            ctx.fill();
            // faint glow
            ctx.beginPath();
            ctx.arc(s.x, s.y, s.r * 3, 0, Math.PI * 2);
            ctx.fillStyle = s.c + (twinkle * 0.08).toFixed(3) + ')';
            ctx.fill();
        }
        requestAnimationFrame(draw);
    }
    requestAnimationFrame(draw);
}

function initDashGhostPairs() {
    const layer = document.getElementById('ghostPairsLayer');
    if (!layer) return;
    if (perfModeEnabled) return;
    const pairs = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'BTC/USD', 'AUD/USD', 'XAU/USD', 'ETH/USD', 'USD/CHF'];
    const colorClasses = ['', 'alt-color', 'purple-color'];

    function spawn() {
        const el = document.createElement('span');
        el.className = 'ghost-pair ' + colorClasses[Math.floor(Math.random() * 3)];
        el.textContent = pairs[Math.floor(Math.random() * pairs.length)];
        el.style.left = Math.random() * 90 + 5 + '%';
        const size = (Math.random() * 1.2 + 0.6).toFixed(2);
        el.style.fontSize = size + 'rem';
        const dur = (Math.random() * 30 + 25).toFixed(1);
        el.style.setProperty('--dur', dur + 's');
        el.style.setProperty('--rot', (Math.random() * 10 - 5).toFixed(1) + 'deg');
        el.style.opacity = '0';
        layer.appendChild(el);
        setTimeout(() => el.remove(), parseFloat(dur) * 1000);
    }

    // initial burst
    for (let i = 0; i < 6; i++) setTimeout(spawn, i * 3000);
    setInterval(spawn, 4000);
}

document.addEventListener('DOMContentLoaded', () => {
    window.__liveMonitorAuto = true;
    requireAuth();
    initSidebarNav();
    initEvents();
    startHeartbeatTicker();
    renderStartStopButton({ isRunning: false, loading: false });
    Promise.all([loadStatus(), loadMetrics(), loadTrades(), loadEquity(), loadRuns(), loadRobustness(), loadMt5Status(), loadBotConfig(), loadBotMonitor(), loadTelegramConfig(), loadBotApiKey()]);
    setInterval(loadRuns, 5000);
    setInterval(loadStatus, 5000);
    setInterval(loadBotMonitor, 7000);
});
