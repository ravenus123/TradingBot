/**
 * Zenith Dashboard - Clean JavaScript
 * Minimal, focused functionality
 */

const API_BASE = '';

// State
let currentSection = 'overview';
let botStatus = { running: false, lastHeartbeat: null };
let backtestData = null;
let btChart = null;
let btSeries = null;
let btAnimationId = null;

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initOverview();
    initBacktest();
    initBotControl();
    initHistory();
    initRobustness();
    
    // Set default dates
    const today = new Date();
    const sixtyDaysAgo = new Date(today - 60 * 24 * 60 * 60 * 1000);
    document.getElementById('backtestEndDate').valueAsDate = today;
    document.getElementById('backtestStartDate').valueAsDate = sixtyDaysAgo;
    
    // Start polling
    setInterval(pollBotStatus, 3000);
    pollBotStatus();
});

// Navigation
function initNavigation() {
    document.querySelectorAll('.sidebar-link[data-section]').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const section = link.dataset.section;
            showSection(section);
            
            document.querySelectorAll('.sidebar-link').forEach(l => l.classList.remove('active'));
            link.classList.add('active');
        });
    });
    
    document.getElementById('logoutBtn')?.addEventListener('click', logout);
}

function showSection(sectionId) {
    document.querySelectorAll('.section').forEach(s => s.classList.add('hidden'));
    document.getElementById(sectionId)?.classList.remove('hidden');
    currentSection = sectionId;
}

// Overview
function initOverview() {
    document.getElementById('refreshBtn')?.addEventListener('click', refreshOverview);
    document.getElementById('saveBotRiskBtn')?.addEventListener('click', saveBotSettings);
    document.getElementById('saveBotGuardsBtn')?.addEventListener('click', saveBotGuards);
    document.getElementById('enableAllSymbolsBtn')?.addEventListener('click', () => setAllSymbols(true));
    document.getElementById('disableAllSymbolsBtn')?.addEventListener('click', () => setAllSymbols(false));
    document.getElementById('reloadBotConfigBtn')?.addEventListener('click', loadBotConfig);
    document.getElementById('toggleLiveMonitorAutoBtn')?.addEventListener('click', toggleLiveMonitor);
    document.getElementById('downloadBotPackageBtnTop')?.addEventListener('click', downloadBotPackage);
    
    loadBotConfig();
    refreshOverview();
}

async function refreshOverview() {
    try {
        const [metrics, positions] = await Promise.all([
            fetch(`${API_BASE}/api/metrics`).then(r => r.json()).catch(() => ({})),
            fetch(`${API_BASE}/api/bot/positions`).then(r => r.json()).catch(() => ({}))
        ]);
        
        updateStats(metrics);
        updatePositions(positions);
    } catch (e) {
        console.error('Refresh error:', e);
    }
}

function updateStats(m) {
    document.getElementById('balance').textContent = `$${(m.balance || 10000).toLocaleString()}`;
    document.getElementById('totalTrades').textContent = m.total_trades || 0;
    document.getElementById('winRate').textContent = `${(m.win_rate || 0).toFixed(1)}%`;
    document.getElementById('profitFactor').textContent = (m.profit_factor || 0).toFixed(2);
    document.getElementById('maxDrawdown').textContent = `${(m.max_drawdown_pct || 0).toFixed(1)}%`;
}

function updatePositions(data) {
    const list = document.getElementById('openPositionsList');
    const positions = data.positions || [];
    
    if (positions.length === 0) {
        list.innerHTML = '<span class="text-muted">No open positions</span>';
        return;
    }
    
    list.innerHTML = positions.map(p => `
        <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border);">
            <span>${p.symbol} ${p.direction}</span>
            <span class="${p.pnl >= 0 ? 'text-success' : 'text-danger'}">${p.pnl >= 0 ? '+' : ''}$${p.pnl.toFixed(2)}</span>
        </div>
    `).join('');
}

async function loadBotConfig() {
    try {
        const config = await fetch(`${API_BASE}/api/bot/config`).then(r => r.json());
        document.getElementById('botRiskInput').value = config.risk_pct || 1;
        document.getElementById('botDailyDdInput').value = config.daily_dd_limit || 5;
        document.getElementById('botMarginCapInput').value = config.margin_cap || 20;
    } catch (e) {
        console.error('Config load error:', e);
    }
}

async function saveBotSettings() {
    const risk = parseFloat(document.getElementById('botRiskInput').value);
    try {
        await fetch(`${API_BASE}/api/bot/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ risk_pct: risk })
        });
        alert('Settings saved');
    } catch (e) {
        alert('Failed to save');
    }
}

async function saveBotGuards() {
    const dd = parseFloat(document.getElementById('botDailyDdInput').value);
    const margin = parseFloat(document.getElementById('botMarginCapInput').value);
    try {
        await fetch(`${API_BASE}/api/bot/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ daily_dd_limit: dd, margin_cap: margin })
        });
        alert('Guards saved');
    } catch (e) {
        alert('Failed to save');
    }
}

async function setAllSymbols(enabled) {
    try {
        await fetch(`${API_BASE}/api/bot/symbols`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
        loadBotConfig();
    } catch (e) {
        alert('Failed to update symbols');
    }
}

let liveMonitorAuto = true;
function toggleLiveMonitor() {
    liveMonitorAuto = !liveMonitorAuto;
    document.getElementById('liveMonitorAutoState').textContent = liveMonitorAuto ? 'On' : 'Off';
}

async function pollBotStatus() {
    try {
        const status = await fetch(`${API_BASE}/api/status`).then(r => r.json());
        botStatus = status;
        
        const indicator = document.getElementById('botActivityState');
        if (status.running) {
            indicator.className = 'badge badge-success';
            indicator.textContent = 'Running';
        } else {
            indicator.className = 'badge badge-neutral';
            indicator.textContent = 'Idle';
        }
        
        document.getElementById('botEngineStatus').textContent = status.running ? 'Active' : 'Stopped';
        document.getElementById('botStrategy').textContent = status.strategy || 'Smart Money';
        document.getElementById('botSymbols').textContent = (status.symbols || []).join(', ') || '—';
        document.getElementById('botLastUpdate').textContent = status.last_heartbeat || '—';
        
        if (liveMonitorAuto && currentSection === 'overview') {
            refreshOverview();
        }
    } catch (e) {
        console.error('Status poll error:', e);
    }
}

async function downloadBotPackage() {
    try {
        const response = await fetch(`${API_BASE}/api/bot/download-package`, { method: 'POST' });
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'zenith-trading-bot.zip';
        a.click();
        window.URL.revokeObjectURL(url);
    } catch (e) {
        alert('Download failed');
    }
}

function logout() {
    localStorage.removeItem('token');
    window.location.href = 'index.html';
}

// Backtest
function initBacktest() {
    document.getElementById('runBacktestBtn')?.addEventListener('click', runBacktest);
    document.getElementById('resetBacktestBtn')?.addEventListener('click', resetBacktest);
    document.getElementById('mcOpenBtn')?.addEventListener('click', runMonteCarloFromBacktest);
    document.getElementById('btPlayPauseBtn')?.addEventListener('click', togglePlayback);
    document.getElementById('btStopBtn')?.addEventListener('click', stopPlayback);
    document.getElementById('btProgressSlider')?.addEventListener('input', seekPlayback);
}

async function runBacktest() {
    const btn = document.getElementById('runBacktestBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Running...';
    
    const symbol = document.getElementById('backtestSymbol').value;
    const start = document.getElementById('backtestStartDate').value;
    const end = document.getElementById('backtestEndDate').value;
    const risk = parseFloat(document.getElementById('backtestRisk').value);
    
    try {
        const response = await fetch(`${API_BASE}/api/backtest/simulate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, start_date: start, end_date: end, risk_pct: risk })
        });
        
        const data = await response.json();
        backtestData = data;
        
        document.getElementById('backtestResults').classList.remove('hidden');
        document.getElementById('btReportTrades').textContent = data.metrics?.trades || data.trades?.length || 0;
        document.getElementById('btReportWinRate').textContent = `${(data.metrics?.win_rate || 0).toFixed(1)}%`;
        document.getElementById('btReportProfit').textContent = `$${(data.metrics?.profit || 0).toFixed(2)}`;
        document.getElementById('btReportPF').textContent = (data.metrics?.profit_factor || 0).toFixed(2);
        document.getElementById('btReportDD').textContent = `$${(data.metrics?.max_drawdown || 0).toFixed(2)}`;
        document.getElementById('btReportReturn').textContent = `${(data.metrics?.return_pct || 0).toFixed(2)}%`;
        
        initChart(data.candles, data.trades);
        startPlayback();
    } catch (e) {
        alert('Backtest failed: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-play"></i> Run Backtest';
    }
}

function resetBacktest() {
    document.getElementById('backtestResults').classList.add('hidden');
    stopPlayback();
    if (btChart) {
        btChart.remove();
        btChart = null;
    }
}

function initChart(candles, trades) {
    const container = document.getElementById('btChartContainer');
    container.innerHTML = '';
    
    btChart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 400,
        layout: { background: { color: 'transparent' }, textColor: '#9ca3af' },
        grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
        timeScale: { borderColor: 'rgba(255,255,255,0.1)', timeVisible: true }
    });
    
    btSeries = btChart.addCandlestickSeries({
        upColor: '#00d084',
        downColor: '#ef4444',
        borderUpColor: '#00d084',
        borderDownColor: '#ef4444',
        wickUpColor: '#00d084',
        wickDownColor: '#ef4444'
    });
    
    btSeries.setData(candles);
    
    // Add trade markers
    if (trades && trades.length > 0) {
        const markers = trades.map(t => ({
            time: t.entryBar,
            position: t.type === 'BUY' ? 'belowBar' : 'aboveBar',
            color: t.type === 'BUY' ? '#00d084' : '#ef4444',
            shape: t.type === 'BUY' ? 'arrowUp' : 'arrowDown',
            text: t.type
        }));
        btSeries.setMarkers(markers);
    }
}

let playbackIndex = 0;
let isPlaying = false;

function startPlayback() {
    if (!backtestData?.candles) return;
    
    isPlaying = true;
    playbackIndex = 0;
    
    function step() {
        if (!isPlaying || playbackIndex >= backtestData.candles.length) {
            isPlaying = false;
            document.getElementById('btPlayPauseBtn').innerHTML = '<i class="fas fa-play"></i>';
            return;
        }
        
        // Update chart view
        const visibleRange = {
            from: backtestData.candles[Math.max(0, playbackIndex - 50)]?.time,
            to: backtestData.candles[playbackIndex]?.time
        };
        btChart?.timeScale().setVisibleLogicalRange({
            from: Math.max(0, playbackIndex - 50),
            to: playbackIndex + 10
        });
        
        // Update progress
        const progress = (playbackIndex / backtestData.candles.length) * 100;
        document.getElementById('btProgressSlider').value = progress;
        document.getElementById('btProgressText').textContent = `${progress.toFixed(0)}%`;
        
        playbackIndex++;
        btAnimationId = setTimeout(step, 50);
    }
    
    step();
    document.getElementById('btPlayPauseBtn').innerHTML = '<i class="fas fa-pause"></i>';
}

function togglePlayback() {
    if (isPlaying) {
        isPlaying = false;
        clearTimeout(btAnimationId);
        document.getElementById('btPlayPauseBtn').innerHTML = '<i class="fas fa-play"></i>';
    } else {
        startPlayback();
    }
}

function stopPlayback() {
    isPlaying = false;
    clearTimeout(btAnimationId);
    playbackIndex = 0;
    document.getElementById('btPlayPauseBtn').innerHTML = '<i class="fas fa-play"></i>';
}

function seekPlayback(e) {
    if (!backtestData?.candles) return;
    const progress = parseFloat(e.target.value);
    playbackIndex = Math.floor((progress / 100) * backtestData.candles.length);
    document.getElementById('btProgressText').textContent = `${progress.toFixed(0)}%`;
}

async function runMonteCarloFromBacktest() {
    if (!backtestData?.trades?.length) {
        alert('No trades to analyze');
        return;
    }
    
    const profits = backtestData.trades.map(t => t.profit || 0);
    const iterations = 200;
    const balances = [];
    
    for (let i = 0; i < iterations; i++) {
        let balance = 10000;
        const shuffled = [...profits].sort(() => Math.random() - 0.5);
        for (const p of shuffled) {
            balance += p;
        }
        balances.push(balance);
    }
    
    balances.sort((a, b) => a - b);
    const p5 = balances[Math.floor(iterations * 0.05)];
    const p95 = balances[Math.floor(iterations * 0.95)];
    const mean = balances.reduce((a, b) => a + b, 0) / iterations;
    
    alert(`Monte Carlo Results:\nMean: $${mean.toFixed(2)}\nP5 (worst): $${p5.toFixed(2)}\nP95 (best): $${p95.toFixed(2)}`);
}

// Bot Control
function initBotControl() {
    document.getElementById('startBotBtn')?.addEventListener('click', () => controlBot('start'));
    document.getElementById('stopBotBtn')?.addEventListener('click', () => controlBot('stop'));
    document.getElementById('generateBotApiKeyBtn')?.addEventListener('click', generateApiKey);
    document.getElementById('revokeBotApiKeyBtn')?.addEventListener('click', revokeApiKey);
    document.getElementById('copyBotApiKeyBtn')?.addEventListener('click', () => copyToClipboard('botApiKeyDisplay'));
    document.getElementById('copyDashUrlBtn')?.addEventListener('click', () => copyToClipboard('dashboardUrlDisplay'));
    
    document.getElementById('dashboardUrlDisplay').value = window.location.origin;
    loadApiKey();
}

async function controlBot(action) {
    try {
        await fetch(`${API_BASE}/api/bot/${action}`, { method: 'POST' });
        pollBotStatus();
        document.getElementById('botControlStatus').innerHTML = `<span class="text-success">Bot ${action}ed</span>`;
    } catch (e) {
        document.getElementById('botControlStatus').innerHTML = `<span class="text-danger">Failed to ${action}</span>`;
    }
}

async function loadApiKey() {
    try {
        const data = await fetch(`${API_BASE}/api/bot/api-key`).then(r => r.json());
        document.getElementById('botApiKeyDisplay').value = data.key || '';
    } catch (e) {
        console.error('API key load error:', e);
    }
}

async function generateApiKey() {
    try {
        const data = await fetch(`${API_BASE}/api/bot/api-key`, { method: 'POST' }).then(r => r.json());
        document.getElementById('botApiKeyDisplay').value = data.key;
    } catch (e) {
        alert('Failed to generate key');
    }
}

async function revokeApiKey() {
    try {
        await fetch(`${API_BASE}/api/bot/api-key`, { method: 'DELETE' });
        document.getElementById('botApiKeyDisplay').value = '';
    } catch (e) {
        alert('Failed to revoke key');
    }
}

function copyToClipboard(elementId) {
    const el = document.getElementById(elementId);
    el.select();
    document.execCommand('copy');
    alert('Copied to clipboard');
}

// History
function initHistory() {
    loadBacktestRuns();
    loadTradeHistory();
}

async function loadBacktestRuns() {
    try {
        const data = await fetch(`${API_BASE}/api/backtest/runs`).then(r => r.json());
        const tbody = document.getElementById('backtestRunsTableBody');
        
        if (!data.runs?.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">No runs yet</td></tr>';
            return;
        }
        
        tbody.innerHTML = data.runs.map(r => `
            <tr>
                <td>${new Date(r.created_at).toLocaleDateString()}</td>
                <td>${r.symbol}</td>
                <td>${r.days}d</td>
                <td>${r.trades}</td>
                <td class="${r.return_pct >= 0 ? 'text-success' : 'text-danger'}">${r.return_pct >= 0 ? '+' : ''}${r.return_pct.toFixed(2)}%</td>
                <td><span class="badge ${r.status === 'completed' ? 'badge-success' : 'badge-neutral'}">${r.status}</span></td>
            </tr>
        `).join('');
    } catch (e) {
        console.error('Runs load error:', e);
    }
}

async function loadTradeHistory() {
    try {
        const data = await fetch(`${API_BASE}/api/trades?limit=100`).then(r => r.json());
        const tbody = document.getElementById('storedTradesTable');
        
        if (!data.trades?.length) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">No trades yet</td></tr>';
            return;
        }
        
        tbody.innerHTML = data.trades.map(t => `
            <tr>
                <td>${new Date(t.entry_time).toLocaleString()}</td>
                <td>${new Date(t.exit_time).toLocaleString()}</td>
                <td>${t.symbol}</td>
                <td>${t.direction}</td>
                <td>${t.entry_price?.toFixed(5) || '-'}</td>
                <td>${t.exit_price?.toFixed(5) || '-'}</td>
                <td class="${t.profit >= 0 ? 'text-success' : 'text-danger'}">${t.profit >= 0 ? '+' : ''}$${t.profit?.toFixed(2) || '0.00'}</td>
                <td>${t.exit_reason || '-'}</td>
            </tr>
        `).join('');
    } catch (e) {
        console.error('Trades load error:', e);
    }
}

// Robustness
function initRobustness() {
    document.getElementById('runRobustnessBtn')?.addEventListener('click', runCalendarWalk);
    document.getElementById('runMonteCarloBtn')?.addEventListener('click', runMonteCarloRobustness);
    loadRobustnessSummary();
}

async function loadRobustnessSummary() {
    try {
        const data = await fetch(`${API_BASE}/api/backtest/robustness`).then(r => r.json());
        document.getElementById('robustnessScore').textContent = data.score || '—';
        document.getElementById('robustnessQuality').textContent = data.quality || '—';
        document.getElementById('robustnessWindows').textContent = data.windows_passed || '—';
    } catch (e) {
        console.error('Robustness load error:', e);
    }
}

async function runCalendarWalk() {
    const btn = document.getElementById('runRobustnessBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Running...';
    
    try {
        const data = await fetch(`${API_BASE}/api/backtest/calendar-walk`, { method: 'POST' }).then(r => r.json());
        
        const results = document.getElementById('calendarWalkResults');
        results.innerHTML = `
            <div style="margin-top:16px;">
                <div class="stats-grid" style="margin-bottom:16px;">
                    <div class="stat-card">
                        <div class="stat-label">Windows</div>
                        <div class="stat-value">${data.summary?.windows || 0}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Profitable</div>
                        <div class="stat-value">${(data.summary?.profitable_pct || 0).toFixed(0)}%</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Mean Return</div>
                        <div class="stat-value ${(data.summary?.mean_return_pct || 0) >= 0 ? 'text-success' : 'text-danger'}">${(data.summary?.mean_return_pct || 0).toFixed(2)}%</div>
                    </div>
                </div>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr><th>Period</th><th>Return</th><th>Trades</th><th>DD</th></tr>
                        </thead>
                        <tbody>
                            ${(data.per_window || []).map(w => `
                                <tr>
                                    <td>${w.start?.slice(0,10)} to ${w.end?.slice(0,10)}</td>
                                    <td class="${w.return_pct >= 0 ? 'text-success' : 'text-danger'}">${w.return_pct >= 0 ? '+' : ''}${w.return_pct.toFixed(2)}%</td>
                                    <td>${w.trades}</td>
                                    <td>${w.max_dd_pct.toFixed(2)}%</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
        
        loadRobustnessSummary();
    } catch (e) {
        alert('Calendar walk failed');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-play"></i> Run Calendar Walk';
    }
}

async function runMonteCarloRobustness() {
    const btn = document.getElementById('runMonteCarloBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Running...';
    
    try {
        const data = await fetch(`${API_BASE}/api/backtest/monte-carlo`, { method: 'POST' }).then(r => r.json());
        
        const results = document.getElementById('monteCarloResults');
        const html = Object.entries(data).map(([sym, d]) => `
            <div style="margin-top:16px;padding:16px;background:var(--bg-elevated);border-radius:6px;">
                <h4 style="font-size:0.875rem;font-weight:600;margin-bottom:12px;">${sym}</h4>
                <div class="stats-grid" style="margin-bottom:0;">
                    <div class="stat-card">
                        <div class="stat-label">Trades</div>
                        <div class="stat-value">${d.trades}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Profitable</div>
                        <div class="stat-value">${d.profitable_pct.toFixed(0)}%</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Mean Return</div>
                        <div class="stat-value ${d.mean_return_pct >= 0 ? 'text-success' : 'text-danger'}">${d.mean_return_pct.toFixed(2)}%</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">P5 (Worst)</div>
                        <div class="stat-value ${d.p5_return_pct >= 0 ? 'text-success' : 'text-danger'}">${d.p5_return_pct.toFixed(2)}%</div>
                    </div>
                </div>
            </div>
        `).join('');
        
        results.innerHTML = html;
    } catch (e) {
        alert('Monte Carlo failed');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-random"></i> Run Simulation';
    }
}

// Handle resize
window.addEventListener('resize', () => {
    if (btChart) {
        btChart.resize(
            document.getElementById('btChartContainer').clientWidth,
            400
        );
    }
});
