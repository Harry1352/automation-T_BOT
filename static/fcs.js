/* Tess Flight Control System v13.2 */

const CHART_MAP = {
    'SOL/USDT': { container: 'tv-sol', symbol: 'BINANCE:SOLUSDT', hud: 'hud-sol' },
    'ETH/USDT': { container: 'tv-eth', symbol: 'BINANCE:ETHUSDT', hud: 'hud-eth' },
    'XAUT/USDT': { container: 'tv-gold', symbol: 'BINANCE:PAXGUSDT', hud: 'hud-xaut' },
    'BTC/USDT': { container: 'tv-btc', symbol: 'BINANCE:BTCUSDT', hud: 'hud-btc' },
};

let chartsInitialized = false;
let archiveOffset = 0;
const ARCHIVE_LIMIT = 50;
let tradeCache = {};

async function apiFetch(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`API error ${res.status} on ${url}`);
    return res.json();
}

function showError(elId, msg) {
    const el = document.getElementById(elId);
    if (el) el.innerHTML = `<div class="error-banner">${msg}</div>`;
}

function num(val, fallback = 0) {
    const n = parseFloat(val);
    return Number.isFinite(n) ? n : fallback;
}

// ── Navigation ──
function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(btn => {
        btn.addEventListener('click', () => {
            const view = btn.dataset.view;
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById(`view-${view}`).classList.add('active');

            if (view === 'archive') loadArchive();
            if (view === 'system') loadSystem();
            if (view === 'intel') loadIntel();
            if (view === 'config') loadConfig();
        });
    });
}

// ── TradingView ──
function initCharts() {
    if (chartsInitialized || typeof TradingView === 'undefined') return;
    Object.values(CHART_MAP).forEach(cfg => {
        new TradingView.widget({
            autosize: true,
            symbol: cfg.symbol,
            interval: '5',
            timezone: 'Etc/UTC',
            theme: 'dark',
            style: '1',
            locale: 'en',
            toolbar_bg: '#0c1019',
            enable_publishing: false,
            hide_side_toolbar: true,
            hide_top_toolbar: false,
            allow_symbol_change: false,
            container_id: cfg.container,
            studies: ['MASimple@tv-basicstudies', 'Volume@tv-basicstudies', 'RSI@tv-basicstudies'],
            overrides: {
                'paneProperties.background': '#0c1019',
                'paneProperties.backgroundType': 'solid',
                'mainSeriesProperties.candleStyle.upColor': '#22c55e',
                'mainSeriesProperties.candleStyle.downColor': '#ef4444',
            },
        });
    });
    chartsInitialized = true;
}

function updateChartHUD(positions, prices) {
    Object.values(CHART_MAP).forEach(cfg => {
        const hud = document.getElementById(cfg.hud);
        if (hud) hud.style.display = 'none';
    });

    for (const [pair, pos] of Object.entries(positions || {})) {
        const base = pair.split('/')[0].toLowerCase();
        const hudId = base === 'xaut' ? 'hud-xaut' : `hud-${base}`;
        const hud = document.getElementById(hudId);
        if (!hud) continue;

        hud.style.display = 'block';
        const upnl = num(pos.unrealized_pnl);
        hud.innerHTML = `
            <div style="color:${pos.direction === 'long' ? '#22c55e' : '#ef4444'}">${String(pos.direction).toUpperCase()} @ ${pos.leverage}x</div>
            <div>Mark: <span style="color:#f1f5f9">$${num(pos.mark_price)}</span></div>
            <div style="color:#22c55e">TP: $${pos.tp}</div>
            <div style="color:#ef4444">SL: $${pos.sl}</div>
            <div style="color:${upnl >= 0 ? '#22c55e' : '#ef4444'}">PnL: ${upnl >= 0 ? '+' : ''}$${upnl.toFixed(2)}</div>`;

        const priceEl = document.getElementById(`price-${base}`);
        if (priceEl && prices && prices[pair]) priceEl.textContent = `$${Number(prices[pair]).toLocaleString()}`;
    }
}

// ── Cockpit ──
async function updateDashboard() {
    try {
        const data = await apiFetch('/api/status');
        const stats = data.stats || {};

        document.getElementById('bal-usd').textContent = `$${num(data.balance).toFixed(2)}`;
        document.getElementById('bal-inr').textContent = `(₹${Math.round(num(data.balance_inr, num(data.balance) * 85)).toLocaleString()})`;
        document.getElementById('profit-val').textContent = `$${num(data.total_profit_td).toFixed(2)}`;
        document.getElementById('loss-val').textContent = `$${num(data.total_loss_td).toFixed(2)}`;
        const net = num(data.net_pnl, num(stats.net_pnl));
        document.getElementById('net-pnl').textContent = `${net >= 0 ? '+' : ''}$${net.toFixed(2)}`;
        document.getElementById('net-pnl').className = `val ${net >= 0 ? 'pnl-pos' : 'pnl-neg'}`;
        document.getElementById('win-rate').textContent = `${num(stats.win_rate)}%`;
        document.getElementById('open-count').textContent = data.open_count ?? Object.keys(data.positions || {}).length;
        document.getElementById('next-planned').textContent = data.next_planned || 'Scanning markets...';

        if (data.prices) {
            for (const [pair, price] of Object.entries(data.prices)) {
                const base = pair.split('/')[0].toLowerCase();
                const el = document.getElementById(`price-${base}`);
                if (el) el.textContent = `$${Number(price).toLocaleString()}`;
            }
        }

        renderActiveFlights(data.positions || {});
        renderRecentTrades(data.trades || []);
        updateChartHUD(data.positions, data.prices);
    } catch (e) {
        console.error('Dashboard error:', e);
        document.getElementById('active-body').innerHTML =
            `<tr><td colspan="12" class="error-banner">Cockpit offline — ${e.message}. Restart scanner or web_gui.py.</td></tr>`;
    }
}

function renderActiveFlights(positions) {
    const body = document.getElementById('active-body');
    const entries = Object.entries(positions);

    if (!entries.length) {
        body.innerHTML = '<tr><td colspan="12" class="empty-state">No active flights — standby mode.</td></tr>';
        return;
    }

    body.innerHTML = entries.map(([pair, p]) => {
        const upnl = num(p.unrealized_pnl);
        const pnlClass = upnl >= 0 ? 'pnl-pos' : 'pnl-neg';
        const dirClass = p.direction === 'long' ? 'tag-long' : 'tag-short';
        const modeClass = p.trade_type === 'scalp' ? 'tag-scalp' : 'tag-swing';
        return `<tr class="flight-row">
            <td><strong>${pair}</strong></td>
            <td><span class="tag ${dirClass}">${p.direction}</span></td>
            <td><span class="tag ${modeClass}">${p.trade_type || 'swing'}</span></td>
            <td class="mono">$${num(p.entry).toFixed(2)}</td>
            <td class="mono">$${num(p.mark_price, p.entry).toFixed(2)}</td>
            <td class="mono" style="color:#ef4444">$${p.sl}</td>
            <td class="mono" style="color:#22c55e">$${p.tp}</td>
            <td class="mono">${p.leverage}x</td>
            <td class="mono">$${num(p.capital).toFixed(2)}</td>
            <td class="mono">$${num(p.risk_at_sl).toFixed(2)}</td>
            <td class="${pnlClass}">${upnl >= 0 ? '+' : ''}$${upnl.toFixed(2)}</td>
            <td class="mono" style="font-size:10px;color:#64748b">${p.elapsed || '—'}${p.breakeven_set ? ' · BE' : ''}${p.trailing_set ? ' · TRAIL' : ''}</td>
        </tr>`;
    }).join('');
}

function renderRecentTrades(trades) {
    const body = document.getElementById('recent-body');
    if (!trades.length) {
        body.innerHTML = '<tr><td colspan="8" class="empty-state">No completed trades yet.</td></tr>';
        return;
    }
    body.innerHTML = cacheTrades(trades.slice(0, 15)).map((t, i) =>
        tradeRowHtml(t, i, `<td class="mono">$${num(t.close).toFixed(2)}</td>`)
    ).join('');
}

function cacheTrades(trades) {
    tradeCache = {};
    trades.forEach((t, i) => { tradeCache[i] = t; });
    return trades;
}

function tradeRowHtml(t, idx, cols) {
    const pnl = num(t.pnl);
    const cls = pnl > 0 ? 'pnl-pos' : pnl < 0 ? 'pnl-neg' : '';
    const sign = pnl > 0 ? '+' : '';
    return `<tr class="row-click" data-trade-idx="${idx}">
        <td style="color:#64748b;font-size:10px">${t.time}</td>
        <td><strong>${t.pair}</strong></td>
        <td><span class="tag ${t.direction === 'long' ? 'tag-long' : 'tag-short'}">${t.direction}</span></td>
        <td><span class="tag ${t.mode === 'scalp' ? 'tag-scalp' : 'tag-swing'}">${t.mode || '—'}</span></td>
        <td class="mono">$${num(t.entry).toFixed(2)}</td>
        ${cols}
        <td class="${cls}">${sign}$${pnl.toFixed(2)}</td>
        <td style="color:#64748b;font-size:10px;max-width:200px;overflow:hidden;text-overflow:ellipsis">${t.reason || '—'}</td>
    </tr>`;
}

function showTradeModal(trade) {
    if (!trade) return;
    const pnl = num(trade.pnl);
    document.getElementById('modal-title').textContent = `${trade.pair} — ${String(trade.direction).toUpperCase()} ${trade.mode || ''}`;
    document.getElementById('modal-body').innerHTML = `
        <div class="detail-grid">
            <div class="detail-item"><label>Timestamp</label><span>${trade.time}</span></div>
            <div class="detail-item"><label>Status</label><span>${trade.status}</span></div>
            <div class="detail-item"><label>Entry</label><span>$${num(trade.entry).toFixed(4)}</span></div>
            <div class="detail-item"><label>Exit</label><span>$${num(trade.close).toFixed(4)}</span></div>
            <div class="detail-item"><label>Stop Loss</label><span style="color:#ef4444">$${num(trade.sl).toFixed(4)}</span></div>
            <div class="detail-item"><label>Take Profit</label><span style="color:#22c55e">$${num(trade.tp).toFixed(4)}</span></div>
            <div class="detail-item"><label>PnL</label><span class="${pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</span></div>
            <div class="detail-item"><label>Exit Reason</label><span>${trade.reason || '—'}</span></div>
        </div>
        ${trade.reason && trade.reason.length > 30 ? `<div class="reason-block">${escapeHtml(trade.reason)}</div>` : ''}`;
    document.getElementById('trade-modal').classList.add('open');
}

// ── Archive ──
async function loadArchive() {
    const body = document.getElementById('archive-body');
    body.innerHTML = '<tr><td colspan="10" class="empty-state">Loading archive...</td></tr>';

    try {
        const status = document.getElementById('filter-status').value;
        const pair = document.getElementById('filter-pair').value;
        const q = document.getElementById('filter-search').value;
        const params = new URLSearchParams({ limit: ARCHIVE_LIMIT, offset: archiveOffset, status, pair, q });
        const data = await apiFetch(`/api/trades?${params}`);
        const s = data.stats || {};

        document.getElementById('arch-total').textContent = s.total ?? 0;
        document.getElementById('arch-wins').textContent = s.wins ?? 0;
        document.getElementById('arch-losses').textContent = s.losses ?? 0;
        document.getElementById('arch-winrate').textContent = `${s.win_rate ?? 0}%`;
        document.getElementById('arch-net').textContent = `${num(s.net_pnl) >= 0 ? '+' : ''}$${num(s.net_pnl).toFixed(2)}`;
        document.getElementById('arch-net').className = `num ${num(s.net_pnl) >= 0 ? 'pnl-pos' : 'pnl-neg'}`;
        document.getElementById('arch-best').textContent = `+$${num(s.best).toFixed(2)}`;

        const pairSelect = document.getElementById('filter-pair');
        if (pairSelect.options.length <= 1 && data.pairs) {
            data.pairs.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p;
                opt.textContent = p;
                pairSelect.appendChild(opt);
            });
        }

        if (!data.trades.length) {
            body.innerHTML = '<tr><td colspan="10" class="empty-state">No trades match your filters.</td></tr>';
        } else {
            body.innerHTML = cacheTrades(data.trades).map((t, i) =>
                tradeRowHtml(t, i,
                    `<td class="mono" style="color:#ef4444">$${num(t.sl).toFixed(2)}</td>
                     <td class="mono" style="color:#22c55e">$${num(t.tp).toFixed(2)}</td>
                     <td class="mono">$${num(t.close).toFixed(2)}</td>`)
            ).join('');
        }

        document.getElementById('arch-page-info').textContent =
            `${archiveOffset + 1}–${archiveOffset + data.trades.length} of ${data.total}`;
    } catch (e) {
        showError('archive-body', `Archive failed: ${e.message}`);
    }
}

// ── System ──
async function loadSystem() {
    document.getElementById('file-list').innerHTML = '<div class="empty-state">Loading files...</div>';
    document.getElementById('log-lines').innerHTML = 'Loading log...';

    try {
        const [filesData, logsData] = await Promise.all([
            apiFetch('/api/system/files'),
            apiFetch('/api/system/logs?lines=80'),
        ]);

        const list = document.getElementById('file-list');
        if (!filesData.files.length) {
            list.innerHTML = '<div class="empty-state">No source files found.</div>';
        } else {
            list.innerHTML = filesData.files.map(f => `
                <div class="file-item" data-file="${f.name}">
                    <div class="name">${f.name}</div>
                    <div class="meta">${f.size_kb} KB · ${f.modified}</div>
                </div>`).join('');

            list.querySelectorAll('.file-item').forEach(el => {
                el.addEventListener('click', () => loadFile(el.dataset.file, el));
            });
            loadFile(filesData.files[0].name, list.querySelector('.file-item'));
        }

        document.getElementById('log-lines').innerHTML = (logsData.lines || [])
            .map(l => `<div>${escapeHtml(l)}</div>`).join('') || '<div class="empty-state">Log empty.</div>';
    } catch (e) {
        showError('file-list', `System load failed: ${e.message}`);
        document.getElementById('log-lines').innerHTML = `<div class="error-banner">${e.message}</div>`;
    }
}

async function loadFile(name, el) {
    document.querySelectorAll('.file-item').forEach(i => i.classList.remove('active'));
    if (el) el.classList.add('active');
    document.getElementById('code-viewer').textContent = 'Loading...';
    try {
        const data = await apiFetch(`/api/system/file/${encodeURIComponent(name)}`);
        document.getElementById('code-viewer').textContent = data.content;
        document.getElementById('code-filename').textContent = `${data.name} (${data.lines} lines)`;
    } catch (e) {
        document.getElementById('code-viewer').textContent = `Failed: ${e.message}`;
    }
}

// ── Intel ──
async function loadIntel() {
    document.getElementById('news-list').innerHTML = '<div class="empty-state">Loading news...</div>';
    document.getElementById('scan-feed').innerHTML = '<div class="empty-state">Loading decisions...</div>';

    try {
        const [newsData, logsData] = await Promise.all([
            apiFetch('/api/market/news?limit=15'),
            apiFetch('/api/system/logs?lines=150'),
        ]);

        const newsEl = document.getElementById('news-list');
        if (newsData.news && newsData.news.length) {
            newsEl.innerHTML = newsData.news.map(n => {
                const title = n.url
                    ? `<a href="${n.url}" target="_blank" rel="noopener">${escapeHtml(n.title)}</a>`
                    : `<span>${escapeHtml(n.title)}</span>`;
                return `<div class="news-item">${title}
                    <div class="news-meta">${escapeHtml(n.source)} · ${escapeHtml(n.time)}${n.categories ? ' · ' + escapeHtml(n.categories) : ''}</div></div>`;
            }).join('');
        } else {
            newsEl.innerHTML = '<div class="empty-state">No news available right now.</div>';
        }

        const feedEl = document.getElementById('scan-feed');
        if (logsData.feed && logsData.feed.length) {
            feedEl.innerHTML = logsData.feed.map(f => `
                <div class="feed-item">
                    <span class="feed-badge ${f.type}">${f.type}</span>
                    <div>
                        <div class="feed-time">${f.time}</div>
                        <div class="feed-text">${escapeHtml(f.text)}</div>
                    </div>
                </div>`).join('');
        } else if (logsData.lines && logsData.lines.length) {
            feedEl.innerHTML = logsData.lines.slice(-30).reverse().map(l =>
                `<div class="feed-item"><span class="feed-badge system">log</span><div class="feed-text">${escapeHtml(l)}</div></div>`
            ).join('');
        } else {
            feedEl.innerHTML = '<div class="empty-state">No scan activity in thinking log.</div>';
        }
    } catch (e) {
        showError('news-list', `Intel failed: ${e.message}`);
        showError('scan-feed', `Decision feed failed: ${e.message}`);
    }
}

// ── Config ──
async function loadConfig() {
    document.getElementById('cfg-json').textContent = 'Loading...';
    try {
        const data = await apiFetch('/api/config');
        const c = data.config || {};
        document.getElementById('cfg-balance').textContent = `$${num(data.balance).toFixed(2)}`;
        document.getElementById('cfg-risk-usd').textContent = `$${num(data.risk_per_trade_usd).toFixed(2)}`;
        document.getElementById('cfg-risk-pct').textContent = `${data.risk_per_trade_pct}%`;
        document.getElementById('cfg-daily-limit').textContent = `$${num(data.daily_loss_limit).toFixed(2)}`;
        document.getElementById('cfg-soft-limit').textContent = `$${num(data.soft_loss_limit).toFixed(2)}`;
        document.getElementById('cfg-rr').textContent = `${data.reward_to_risk}:1`;
        document.getElementById('cfg-max-pos').textContent = data.max_open_positions;
        document.getElementById('cfg-pairs').textContent = (c.active_pairs || []).join(', ');
        document.getElementById('cfg-json').textContent = JSON.stringify(c, null, 2);
    } catch (e) {
        document.getElementById('cfg-json').textContent = `Error: ${e.message}`;
    }
}

function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ── Voice ──
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const SpeechSynthesis = window.speechSynthesis;
let recognition = null;
let isRecording = false;

function initVoice() {
    const micBtn = document.getElementById('mic-btn');
    const popover = document.getElementById('voice-popover');

    micBtn.addEventListener('click', e => {
        e.stopPropagation();
        popover.classList.toggle('open');
        if (!recognition) return;
        if (isRecording) recognition.stop();
        else recognition.start();
    });

    document.addEventListener('click', () => popover.classList.remove('open'));

    if (SpeechSynthesis) {
        SpeechSynthesis.onvoiceschanged = populateVoiceList;
        populateVoiceList();
    }

    if (!SpeechRecognition) return;

    recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = 'en-US';

    recognition.onstart = () => {
        isRecording = true;
        micBtn.classList.add('recording');
        popover.classList.add('open');
        document.getElementById('voice-status').textContent = 'Listening...';
    };
    recognition.onend = () => {
        isRecording = false;
        micBtn.classList.remove('recording');
    };
    recognition.onresult = e => {
        const cmd = e.results[0][0].transcript;
        document.getElementById('voice-status').textContent = `You: "${cmd}"`;
        sendVoice(cmd);
    };
}

function populateVoiceList() {
    if (!SpeechSynthesis) return;
    const select = document.getElementById('voice-select');
    select.innerHTML = '';
    SpeechSynthesis.getVoices().forEach(voice => {
        const opt = document.createElement('option');
        opt.value = voice.name;
        opt.textContent = `${voice.name} (${voice.lang})`;
        if (voice.name.includes('Google US English') || voice.name.includes('Samantha')) opt.selected = true;
        select.appendChild(opt);
    });
}

async function sendVoice(text) {
    document.getElementById('voice-status').textContent = 'Analyzing...';
    try {
        const res = await fetch('/api/voice_command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: text }),
        });
        const data = await res.json();
        document.getElementById('voice-status').textContent = data.reply;
        speakBack(data.reply);
        updateDashboard();
    } catch {
        document.getElementById('voice-status').textContent = 'Core offline.';
    }
}

function speakBack(text) {
    if (!SpeechSynthesis) return;
    const u = new SpeechSynthesisUtterance(text.split('[CONFIG_UPDATE]')[0].trim());
    const v = SpeechSynthesis.getVoices().find(x => x.name.includes('Google') || x.name.includes('Samantha'));
    if (v) u.voice = v;
    SpeechSynthesis.speak(u);
}

// ── Boot ──
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initVoice();

    document.addEventListener('click', e => {
        const row = e.target.closest('[data-trade-idx]');
        if (row) showTradeModal(tradeCache[row.dataset.tradeIdx]);
    });

    document.getElementById('modal-close').addEventListener('click', () => {
        document.getElementById('trade-modal').classList.remove('open');
    });
    document.getElementById('trade-modal').addEventListener('click', e => {
        if (e.target.id === 'trade-modal') e.target.classList.remove('open');
    });

    document.getElementById('filter-status').addEventListener('change', () => { archiveOffset = 0; loadArchive(); });
    document.getElementById('filter-pair').addEventListener('change', () => { archiveOffset = 0; loadArchive(); });
    document.getElementById('filter-search').addEventListener('input', debounce(() => { archiveOffset = 0; loadArchive(); }, 400));
    document.getElementById('arch-prev').addEventListener('click', () => { archiveOffset = Math.max(0, archiveOffset - ARCHIVE_LIMIT); loadArchive(); });
    document.getElementById('arch-next').addEventListener('click', () => { archiveOffset += ARCHIVE_LIMIT; loadArchive(); });
    document.getElementById('export-btn').addEventListener('click', () => { window.location.href = '/api/trades/export'; });

    setInterval(() => {
        document.getElementById('clock').textContent = new Date().toUTCString().split(' ')[4] + ' UTC';
    }, 1000);

    updateDashboard();
    setInterval(updateDashboard, 5000);

    if (typeof TradingView !== 'undefined') initCharts();
    else {
        const s = document.querySelector('script[src*="tradingview"]');
        if (s) s.addEventListener('load', initCharts);
    }
});
