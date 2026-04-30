// ── Fantasy Baseball Dashboard ──

const WIZARD_API_BASE = document.querySelector('meta[name="wizard-api-base"]')?.content || '';

const state = {
  currentPage: 'sp-picker',
  hasEspn: false,
  leagues: [],
  activeLeagueId: null,
  leagueInfo: null,
  spData: null,
  rankingsData: null,
  rosterData: null,
  freeAgentsData: null,
  rankingsMode: 'current',
  faPlayerType: null,
  faPosition: null,
  faStatView: 'current',
  selectedTeam: null,
  projSource: 'ESPN',
  streamersData: null,
  backendStatus: null,
  streamerFilter: 'all',
  expandedStreamer: null,
};

// ── API helpers ──

async function api(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    if (body.setup_required) throw new Error('SETUP_REQUIRED');
    throw new Error(body.error || `API error: ${resp.status}`);
  }
  return resp.json();
}

async function apiPost(url) {
  const resp = await fetch(url, { method: 'POST' });
  return resp.json();
}

async function wizardApi(path) {
  const resp = await fetch(`${WIZARD_API_BASE}${path}`);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || body.error || `Backend error: ${resp.status}`);
  }
  return resp.json();
}

async function wizardApiPost(path) {
  const resp = await fetch(`${WIZARD_API_BASE}${path}`, { method: 'POST' });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || body.error || `Backend error: ${resp.status}`);
  }
  return resp.json();
}

// ── Navigation ──

function navigate(page) {
  state.currentPage = page;
  // account/settings are not sidebar pages, deselect all nav items for those
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });
  renderPage();
}

// ── Page renderer ──

async function renderPage() {
  const content = document.getElementById('content');
  content.innerHTML = '<div class="loading"><div class="spinner"></div><p>Loading data...</p></div>';

  try {
    switch (state.currentPage) {
      case 'sp-picker': await renderSPPicker(content); break;
      case 'streamers': await renderStreamers(content); break;
      case 'rankings': await renderRankings(content); break;
      case 'my-roster': await renderMyRoster(content); break;
      case 'free-agents': await renderFreeAgents(content); break;
      case 'account': renderAccount(content); break;
      case 'settings': renderSettings(content); break;
    }
  } catch (err) {
    if (err.message === 'SETUP_REQUIRED') {
      content.innerHTML = `<div class="empty-state" style="max-width:560px;margin:40px auto;text-align:left;padding:20px">
        <h2 style="color:var(--accent);margin-bottom:16px;font-size:20px">Setup Required</h2>
        <p style="margin-bottom:16px;font-size:14px">Create a <code>.env</code> file in the project root:</p>
        <div style="background:var(--bg-secondary);border:1px solid var(--border);border-radius:8px;padding:14px;font-family:monospace;font-size:12px;margin-bottom:16px;line-height:2;overflow-x:auto">
          ESPN_LEAGUE_ID=<span style="color:var(--accent)">your_league_id</span><br>
          ESPN_S2=<span style="color:var(--accent)">your_espn_s2_cookie</span><br>
          ESPN_SWID=<span style="color:var(--accent)">{your_swid_cookie}</span><br>
          SEASON_YEAR=2026
        </div>
        <p style="color:var(--text-secondary);font-size:13px"><strong>How to get ESPN cookies:</strong><br>Open ESPN Fantasy in your browser &rarr; F12 &rarr; Application &rarr; Cookies &rarr; espn.com &rarr; copy <code>espn_s2</code> and <code>SWID</code></p>
        <p style="color:var(--text-muted);font-size:12px;margin-top:12px">Vegas lines are optional. Add <code>ODDS_API_KEY</code> later if you want them.</p>
        <p style="color:var(--text-secondary);font-size:13px;margin-top:16px">Then restart the server and refresh.</p>
      </div>`;
    } else {
      content.innerHTML = `<div class="empty-state"><p style="color:var(--red)">Error loading data</p><p style="font-size:12px;margin-top:8px;color:var(--text-secondary)">${err.message}</p></div>`;
    }
    console.error(err);
  }
}

// ── SP Picker ──

async function renderSPPicker(el) {
  if (!state.spData) state.spData = await api('/api/sp-picker');
  const data = state.spData;
  const dates = Object.keys(data.dates).sort();
  const today = new Date().toISOString().split('T')[0];
  const tmrw = nextDay(today);

  const isPublic = data.mode === 'public';
  const subtitle = isPublic ? 'All probable starters — next 7 days' : 'Free agent starters — next 7 days';

  let html = `
    <div class="page-header">
      <h2>SP Picker</h2>
      <p>${subtitle}</p>
    </div>
    <div class="summary-row">
      ${!isPublic ? `<div class="summary-card"><div class="label">Free Agent SPs</div><div class="value accent">${data.total_free_agents}</div></div>` : ''}
      <div class="summary-card"><div class="label">${isPublic ? 'Starters' : 'With Starts'}</div><div class="value green">${data.total_with_starts}</div></div>
      <div class="summary-card"><div class="label">Days</div><div class="value">${dates.length}</div></div>
      <div class="summary-card"><div class="label">Data</div><div class="value" style="font-size:14px">${data.has_odds ? 'Vegas + Records' : data.has_records ? 'Team Records' : 'Projections'}</div></div>
    </div>`;

  if (dates.length === 0) {
    html += '<div class="empty-state"><p>No free agent SPs with scheduled starts found.</p></div>';
    el.innerHTML = html;
    return;
  }

  for (const d of dates) {
    const pitchers = data.dates[d];
    const dateObj = new Date(d + 'T12:00:00');
    const dayName = dateObj.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
    let labelClass = 'day-future', labelText = '';
    if (d === today) { labelClass = 'day-today'; labelText = 'Today'; }
    else if (d === tmrw) { labelClass = 'day-tomorrow'; labelText = 'Tomorrow'; }

    html += `<div class="day-section"><div class="day-header"><h3>${dayName}</h3>`;
    if (labelText) html += `<span class="day-label ${labelClass}">${labelText}</span>`;
    html += `<span style="color:var(--text-muted);font-size:12px">${pitchers.length}</span></div>`;

    // Desktop table
    html += `<div class="table-wrap sp-table"><table>
      <thead><tr>
        <th>#</th><th>Pitcher</th><th>Matchup</th>
        <th>ERA</th><th>WHIP</th>`;
    if (isPublic) {
      html += '<th>K/9</th>';
    } else {
      html += '<th>Proj</th><th>Own%</th>';
    }
    if (data.has_odds) html += '<th>ML</th><th>O/U</th>';
    html += '<th>Opp Rec</th><th>Score</th></tr></thead><tbody>';

    pitchers.forEach((p, i) => {
      const sc = p.score >= 65 ? 'score-hot' : p.score >= 45 ? 'score-warm' : 'score-cold';
      const mu = p.is_home ? `vs ${p.opponent}` : `@ ${p.opponent}`;
      html += `<tr>
        <td>${i + 1}</td>
        <td><span class="player-name">${p.name}</span><span class="player-team">${p.team}</span>${p.game_proj ? ' <span class="proj-badge">Game</span>' : ''}</td>
        <td>${mu}</td>
        <td>${fmtStat(p.era, 'ERA')}</td>
        <td>${fmtStat(p.whip, 'WHIP')}</td>`;
      if (isPublic) {
        html += `<td>${fmtStat(p.k9, 'K/9')}</td>`;
      } else {
        html += `<td>${p.projected_pts || '—'}</td><td>${p.pct_owned}%</td>`;
      }
      if (data.has_odds) html += `<td>${fmtML(p.moneyline)}</td><td>${p.over_under || '—'}</td>`;
      html += `<td>${p.opp_record || '—'}</td>
        <td><span class="score-badge ${sc}">${p.score}</span></td></tr>`;
    });
    html += '</tbody></table></div>';

    // Mobile cards
    html += '<div class="pitcher-cards">';
    pitchers.forEach((p, i) => {
      const sc = p.score >= 65 ? 'score-hot' : p.score >= 45 ? 'score-warm' : 'score-cold';
      const mu = p.is_home ? `vs ${p.opponent}` : `@ ${p.opponent}`;
      html += `<div class="pitcher-card">
        <div class="pitcher-card-left">
          <div class="pc-name">${p.name} <span style="color:var(--text-muted);font-weight:400">${p.team}</span></div>
          <div class="pc-meta">${mu}${p.opp_record ? ' (' + p.opp_record + ')' : ''}</div>
          <div class="pc-details">
            <span>ERA: ${fmtStat(p.era, 'ERA')}</span><span>WHIP: ${fmtStat(p.whip, 'WHIP')}</span>
            ${isPublic
              ? `<span>K/9: ${fmtStat(p.k9, 'K/9')}</span>`
              : `<span>Proj: ${p.projected_pts || '—'}</span>`}
            ${p.win_prob ? `<span>Win: ${p.win_prob}%</span>` : ''}
            ${data.has_odds && p.over_under ? `<span>O/U: ${p.over_under}</span>` : ''}
          </div>
        </div>
        <div class="pitcher-card-right">
          <span class="score-badge ${sc}" style="font-size:16px;padding:6px 14px">${p.score}</span>
        </div>
      </div>`;
    });
    html += '</div></div>';
  }

  el.innerHTML = html;
}

// ── Streamers ──

async function renderStreamers(el) {
  const [streamersRes, statusRes] = await Promise.allSettled([
    state.streamersData ? Promise.resolve(state.streamersData) : wizardApi('/streamers/tomorrow'),
    wizardApi('/admin/refresh-status'),
  ]);

  if (streamersRes.status === 'rejected') {
    el.innerHTML = `<div class="page-header"><h2>Streamers</h2><p>Tomorrow's streaming pitchers</p></div>
      <div class="empty-state"><p style="color:var(--red)">Failed to load streamers</p>
      <p style="font-size:12px;margin-top:8px;color:var(--text-secondary)">${streamersRes.reason?.message || 'Backend unreachable'}</p>
      <button class="refresh-btn" style="width:auto;padding:8px 20px;margin-top:16px" onclick="refreshStreamers()">Retry</button></div>`;
    return;
  }

  const data = streamersRes.value;
  state.streamersData = data;
  const status = statusRes.status === 'fulfilled' ? statusRes.value : null;
  state.backendStatus = status;

  const pitchers = data.streamers || [];
  const tags = new Set();
  pitchers.forEach(p => (p.tags || []).forEach(t => tags.add(t)));

  const filtered = state.streamerFilter === 'all'
    ? pitchers
    : pitchers.filter(p => (p.tags || []).includes(state.streamerFilter));

  let html = `
    <div class="page-header">
      <h2>Streamers</h2>
      <p>Tomorrow's streaming pitcher rankings — ${data.game_date || 'upcoming'}</p>
    </div>`;

  // Status card
  html += '<div class="streamer-status-card">';
  if (status) {
    const freshness = status.tomorrow_slate || {};
    const stale = freshness.stale_stages || [];
    const statusClass = stale.length === 0 ? 'status-fresh' : stale.length <= 2 ? 'status-stale' : 'status-old';
    const statusLabel = stale.length === 0 ? 'Fresh' : stale.length <= 2 ? 'Partially Stale' : 'Stale';
    html += `
      <div class="status-item">
        <span class="status-dot ${statusClass}"></span>
        <span class="status-label">Data: <strong>${statusLabel}</strong></span>
      </div>
      <div class="status-item">
        <span class="status-label">Model: <strong>${data.model_version || '—'}</strong></span>
      </div>
      <div class="status-item">
        <span class="status-label">Pitchers: <strong>${pitchers.length}</strong></span>
      </div>`;
    if (stale.length > 0) {
      html += `<div class="status-item"><span class="status-label" style="color:var(--yellow)">Stale: ${stale.join(', ')}</span></div>`;
    }
  } else {
    html += '<div class="status-item"><span class="status-label" style="color:var(--text-muted)">Status unavailable</span></div>';
  }
  html += `<button class="refresh-btn streamer-refresh-btn" onclick="refreshStreamers()" title="Refresh backend data">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
      Refresh
    </button>`;
  html += '</div>';

  // Filter controls
  const allTags = ['all', ...Array.from(tags).sort()];
  html += `<div class="controls">
    <div class="control-group">
      <label>Filter</label>
      <div class="pill-group">
        ${allTags.map(t => `<button class="pill ${state.streamerFilter === t ? 'active' : ''}" onclick="setStreamerFilter('${t}')">${t === 'all' ? 'All' : formatTag(t)}</button>`).join('')}
      </div>
    </div>
  </div>`;

  if (filtered.length === 0) {
    html += '<div class="empty-state"><p>No streamers match this filter.</p></div>';
    el.innerHTML = html;
    return;
  }

  // Summary row
  const topPick = pitchers.find(p => (p.tags || []).includes('top-pick'));
  const avgScore = pitchers.length > 0 ? (pitchers.reduce((s, p) => s + (p.stream_score || 0), 0) / pitchers.length).toFixed(1) : '—';
  const simCount = pitchers.filter(p => (p.tags || []).includes('sim')).length;
  html += `<div class="summary-row">
    <div class="summary-card"><div class="label">Top Pick</div><div class="value accent">${topPick ? topPick.pitcher_name : '—'}</div></div>
    <div class="summary-card"><div class="label">Avg Score</div><div class="value green">${avgScore}</div></div>
    <div class="summary-card"><div class="label">Simulated</div><div class="value">${simCount}</div></div>
    <div class="summary-card"><div class="label">Total</div><div class="value">${pitchers.length}</div></div>
  </div>`;

  // Desktop table
  html += `<div class="table-wrap streamer-table"><table data-table="streamers">
    <thead><tr>
      <th onclick="sortStreamerTable('rank')">#</th>
      <th onclick="sortStreamerTable('name')">Pitcher</th>
      <th>Matchup</th>
      <th onclick="sortStreamerTable('score')">Score</th>
      <th onclick="sortStreamerTable('k')">K</th>
      <th onclick="sortStreamerTable('era')">ERA</th>
      <th onclick="sortStreamerTable('whip')">WHIP</th>
      <th onclick="sortStreamerTable('ip')">IP</th>
      <th onclick="sortStreamerTable('win')">Win%</th>
      <th>Tags</th>
      <th></th>
    </tr></thead><tbody>`;

  filtered.forEach((p, i) => {
    const sc = (p.stream_score || 0) >= 65 ? 'score-hot' : (p.stream_score || 0) >= 45 ? 'score-warm' : 'score-cold';
    const mu = p.home_away === 'home' ? `vs ${p.opponent_team}` : `@ ${p.opponent_team}`;
    const expanded = state.expandedStreamer === p.pitcher_name;
    const blowup = p.blowup_probability != null ? p.blowup_probability : null;

    html += `<tr class="streamer-row${expanded ? ' expanded' : ''}" onclick="toggleStreamerDetail('${p.pitcher_name.replace(/'/g, "\\'")}')">
      <td>${i + 1}</td>
      <td><span class="player-name">${p.pitcher_name}</span><span class="player-team">${p.pitcher_team}</span></td>
      <td>${mu}</td>
      <td><span class="score-badge ${sc}">${(p.stream_score || 0).toFixed(1)}</span></td>
      <td>${p.projected_k != null ? p.projected_k.toFixed(1) : '—'}</td>
      <td>${p.projected_era != null ? p.projected_era.toFixed(2) : '—'}</td>
      <td>${p.projected_whip != null ? p.projected_whip.toFixed(2) : '—'}</td>
      <td>${p.projected_ip != null ? p.projected_ip.toFixed(1) : '—'}</td>
      <td>${p.win_probability != null ? (p.win_probability * 100).toFixed(0) + '%' : '—'}</td>
      <td>${(p.tags || []).map(t => `<span class="streamer-tag tag-${t}">${formatTag(t)}</span>`).join(' ')}</td>
      <td class="expand-icon">${expanded ? '▼' : '▶'}</td>
    </tr>`;

    if (expanded) {
      html += `<tr class="streamer-detail-row"><td colspan="11"><div class="streamer-detail">`;

      // Projection details
      html += '<div class="detail-grid">';
      html += `<div class="detail-col">
        <h4>Projections</h4>
        <div class="detail-stat"><span>IP</span><strong>${p.projected_ip != null ? p.projected_ip.toFixed(1) : '—'}</strong></div>
        <div class="detail-stat"><span>K</span><strong>${p.projected_k != null ? p.projected_k.toFixed(1) : '—'}</strong></div>
        <div class="detail-stat"><span>BB</span><strong>${p.projected_bb != null ? p.projected_bb.toFixed(1) : '—'}</strong></div>
        <div class="detail-stat"><span>H</span><strong>${p.projected_h != null ? p.projected_h.toFixed(1) : '—'}</strong></div>
        <div class="detail-stat"><span>ER</span><strong>${p.projected_er != null ? p.projected_er.toFixed(1) : '—'}</strong></div>
      </div>`;

      html += `<div class="detail-col">
        <h4>Rate Stats</h4>
        <div class="detail-stat"><span>ERA</span><strong>${p.projected_era != null ? p.projected_era.toFixed(2) : '—'}</strong></div>
        <div class="detail-stat"><span>WHIP</span><strong>${p.projected_whip != null ? p.projected_whip.toFixed(2) : '—'}</strong></div>
        <div class="detail-stat"><span>Win%</span><strong>${p.win_probability != null ? (p.win_probability * 100).toFixed(0) + '%' : '—'}</strong></div>
        <div class="detail-stat"><span>Blowup%</span><strong style="color:${blowup != null && blowup > 0.25 ? 'var(--red)' : 'inherit'}">${blowup != null ? (blowup * 100).toFixed(0) + '%' : '—'}</strong></div>
        <div class="detail-stat"><span>Confidence</span><strong>${p.confidence != null ? p.confidence.toFixed(2) : '—'}</strong></div>
      </div>`;

      // Simulation percentiles (if available)
      if (p.k_p50 != null) {
        html += `<div class="detail-col">
          <h4>Simulation (P20 / P50 / P80)</h4>
          <div class="detail-stat"><span>K</span><strong>${fmtPercentiles(p.k_p20, p.k_p50, p.k_p80)}</strong></div>
          <div class="detail-stat"><span>ERA</span><strong>${fmtPercentiles(p.era_p20, p.era_p50, p.era_p80, 2)}</strong></div>
          <div class="detail-stat"><span>WHIP</span><strong>${fmtPercentiles(p.whip_p20, p.whip_p50, p.whip_p80, 2)}</strong></div>
        </div>`;
      }

      html += '</div></div></td></tr>';
    }
  });

  html += '</tbody></table></div>';

  // Mobile cards
  html += '<div class="streamer-cards">';
  filtered.forEach((p, i) => {
    const sc = (p.stream_score || 0) >= 65 ? 'score-hot' : (p.stream_score || 0) >= 45 ? 'score-warm' : 'score-cold';
    const mu = p.home_away === 'home' ? `vs ${p.opponent_team}` : `@ ${p.opponent_team}`;
    const expanded = state.expandedStreamer === p.pitcher_name;

    html += `<div class="streamer-card${expanded ? ' expanded' : ''}" onclick="toggleStreamerDetail('${p.pitcher_name.replace(/'/g, "\\'")}')">
      <div class="pitcher-card-left">
        <div class="pc-name">${p.pitcher_name} <span style="color:var(--text-muted);font-weight:400">${p.pitcher_team}</span></div>
        <div class="pc-meta">${mu}</div>
        <div class="pc-details">
          <span>K: ${p.projected_k != null ? p.projected_k.toFixed(1) : '—'}</span>
          <span>ERA: ${p.projected_era != null ? p.projected_era.toFixed(2) : '—'}</span>
          <span>WHIP: ${p.projected_whip != null ? p.projected_whip.toFixed(2) : '—'}</span>
          <span>Win: ${p.win_probability != null ? (p.win_probability * 100).toFixed(0) + '%' : '—'}</span>
        </div>
        <div class="pc-tags">${(p.tags || []).map(t => `<span class="streamer-tag tag-${t}">${formatTag(t)}</span>`).join(' ')}</div>
      </div>
      <div class="pitcher-card-right">
        <span class="score-badge ${sc}" style="font-size:16px;padding:6px 14px">${(p.stream_score || 0).toFixed(1)}</span>
      </div>`;

    if (expanded) {
      html += `<div class="streamer-card-detail" onclick="event.stopPropagation()">
        <div class="detail-grid">
          <div class="detail-col">
            <div class="detail-stat"><span>IP</span><strong>${p.projected_ip != null ? p.projected_ip.toFixed(1) : '—'}</strong></div>
            <div class="detail-stat"><span>BB</span><strong>${p.projected_bb != null ? p.projected_bb.toFixed(1) : '—'}</strong></div>
            <div class="detail-stat"><span>Blowup%</span><strong>${p.blowup_probability != null ? (p.blowup_probability * 100).toFixed(0) + '%' : '—'}</strong></div>
          </div>`;
      if (p.k_p50 != null) {
        html += `<div class="detail-col">
            <h4 style="font-size:11px">Sim P20/P50/P80</h4>
            <div class="detail-stat"><span>K</span><strong>${fmtPercentiles(p.k_p20, p.k_p50, p.k_p80)}</strong></div>
            <div class="detail-stat"><span>ERA</span><strong>${fmtPercentiles(p.era_p20, p.era_p50, p.era_p80, 2)}</strong></div>
          </div>`;
      }
      html += '</div></div>';
    }

    html += '</div>';
  });
  html += '</div>';

  el.innerHTML = html;
}

function formatTag(tag) {
  return tag.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function fmtPercentiles(p20, p50, p80, decimals = 1) {
  const fmt = v => v != null ? v.toFixed(decimals) : '—';
  return `${fmt(p20)} / ${fmt(p50)} / ${fmt(p80)}`;
}

function toggleStreamerDetail(name) {
  state.expandedStreamer = state.expandedStreamer === name ? null : name;
  renderPage();
}

function setStreamerFilter(filter) {
  state.streamerFilter = filter;
  renderPage();
}

async function refreshStreamers() {
  const btn = document.querySelector('.streamer-refresh-btn');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;margin:0;border-width:2px"></span> Refreshing...'; }
  try {
    await wizardApiPost('/admin/refresh-all?force=true');
    state.streamersData = null;
    state.backendStatus = null;
    await renderPage();
  } catch (e) {
    console.error('Refresh failed:', e);
    if (btn) btn.innerHTML = 'Refresh Failed';
    setTimeout(() => { if (btn) btn.innerHTML = 'Refresh'; btn.disabled = false; }, 2000);
  }
}

let streamerSortState = {};
function sortStreamerTable(key) {
  const table = document.querySelector('table[data-table="streamers"]');
  if (!table) return;
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr.streamer-row'));

  const prev = streamerSortState.key;
  const asc = (prev === key) ? !streamerSortState.asc : (key === 'name');
  streamerSortState = { key, asc };

  const colMap = { rank: 0, name: 1, score: 3, k: 4, era: 5, whip: 6, ip: 7, win: 8 };
  const colIdx = colMap[key] ?? 0;

  rows.sort((a, b) => {
    const aText = a.cells[colIdx]?.textContent?.trim() || '';
    const bText = b.cells[colIdx]?.textContent?.trim() || '';
    const aNum = parseFloat(aText.replace(/[^0-9.\-]/g, ''));
    const bNum = parseFloat(bText.replace(/[^0-9.\-]/g, ''));
    if (!isNaN(aNum) && !isNaN(bNum)) return asc ? aNum - bNum : bNum - aNum;
    return asc ? aText.localeCompare(bText) : bText.localeCompare(aText);
  });

  rows.forEach(r => {
    const detail = r.nextElementSibling;
    tbody.appendChild(r);
    if (detail && detail.classList.contains('streamer-detail-row')) tbody.appendChild(detail);
  });
}

// ── Rankings ──

async function renderRankings(el) {
  state.rankingsData = await api(`/api/rankings?mode=${state.rankingsMode}`);
  const data = state.rankingsData;
  const cats = data.categories || [];
  const offCats = cats.filter(c => c.type === 'offense');
  const pitCats = cats.filter(c => c.type === 'pitching');
  const source = state.rankingsMode === 'current' ? 'current_stats' : 'projected_stats';

  let html = `
    <div class="page-header">
      <h2>Rankings</h2>
      <p>${state.rankingsMode === 'current' ? 'Current season' : 'Projected'} stats</p>
    </div>
    <div class="controls">
      <div class="control-group">
        <label>View</label>
        <div class="pill-group">
          <button class="pill ${state.rankingsMode === 'current' ? 'active' : ''}" onclick="setRankingsMode('current')">Current</button>
          <button class="pill ${state.rankingsMode === 'projected' ? 'active' : ''}" onclick="setRankingsMode('projected')">Projected</button>
        </div>
      </div>
    </div>`;

  html += '<h3 style="margin-bottom:12px;font-size:15px;font-weight:600">Overall</h3>';
  html += buildRankingsTable(data.teams, cats, source, 'all');

  if (offCats.length > 0) {
    html += '<h3 style="margin:24px 0 12px;font-size:15px;font-weight:600">Offense</h3>';
    html += buildRankingsTable(data.teams, offCats, source, 'offense');
  }
  if (pitCats.length > 0) {
    html += '<h3 style="margin:24px 0 12px;font-size:15px;font-weight:600">Pitching</h3>';
    html += buildRankingsTable(data.teams, pitCats, source, 'pitching');
  }

  el.innerHTML = html;
}

function buildRankingsTable(teams, cats, source, section) {
  let html = `<div class="table-wrap"><table>
    <thead><tr><th>Rank</th><th>Team</th>`;
  for (const c of cats) html += `<th title="${c.display_name || c.name}">${c.name}</th>`;
  if (section === 'all') html += '<th>Total</th>';
  html += '</tr></thead><tbody>';

  const sorted = [...teams].sort((a, b) => {
    if (section === 'all') return a.overall_rank - b.overall_rank;
    const sumA = cats.reduce((s, c) => s + (a.ranks?.[c.name] || 99), 0);
    const sumB = cats.reduce((s, c) => s + (b.ranks?.[c.name] || 99), 0);
    return sumA - sumB;
  });

  sorted.forEach((t, idx) => {
    const rc = idx === 0 ? 'rank-1' : idx === 1 ? 'rank-2' : idx === 2 ? 'rank-3' : 'rank-default';
    html += `<tr><td><span class="rank-badge ${rc}">${idx + 1}</span></td>
      <td><span class="player-name">${t.team_name}</span></td>`;
    for (const c of cats) {
      const val = t[source]?.[c.name];
      const rank = t.ranks?.[c.name] || '—';
      const gc = rank <= 3 ? 'grade-elite' : rank <= 6 ? 'grade-good' : rank <= 9 ? 'grade-avg' : 'grade-poor';
      html += `<td><span class="${gc}">${fmtStat(val, c.name)}</span> <span style="font-size:10px;color:var(--text-muted)">#${rank}</span></td>`;
    }
    if (section === 'all') html += `<td>${t.total_rank_score}</td>`;
    html += '</tr>';
  });

  html += '</tbody></table></div>';
  return html;
}

function setRankingsMode(mode) {
  state.rankingsMode = mode;
  renderPage();
}

// ── My Roster ──

// Position color map
const POS_COLORS = {
  'C': '#e879f9', '1B': '#f97316', '2B': '#22d3ee', '3B': '#facc15',
  'SS': '#a78bfa', 'IF': '#6366f1', '1B/3B': '#fb923c', '2B/SS': '#818cf8',
  'LF': '#4ade80', 'CF': '#34d399', 'RF': '#2dd4bf', 'OF': '#22c55e',
  'DH': '#f472b6', 'UTIL': '#94a3b8',
  'SP': '#ef4444', 'RP': '#f97316', 'P': '#ef4444',
  'BE': '#64748b', 'IL': '#dc2626',
};

async function renderMyRoster(el) {
  const url = state.selectedTeam
    ? `/api/my-roster?team_name=${encodeURIComponent(state.selectedTeam)}`
    : '/api/my-roster';
  state.rosterData = await api(url);
  const data = state.rosterData;

  if (data.needs_selection) {
    let html = `<div class="page-header"><h2>My Roster</h2><p>Select your team</p></div><div class="team-grid">`;
    for (const t of data.teams) {
      html += `<div class="team-select-card" onclick="selectTeam('${t.name.replace(/'/g, "\\'")}')">
        <div class="label">Team</div><div class="tname">${t.name}</div></div>`;
    }
    html += '</div>';
    el.innerHTML = html;
    return;
  }

  const cats = data.categories || [];
  const players = data.players || [];
  const teams = data.teams || [];
  const projSources = new Set();
  players.forEach(p => Object.keys(p.projections || {}).forEach(s => projSources.add(s)));
  const sources = ['ESPN', ...Array.from(projSources).filter(s => s !== 'ESPN')];
  if (!sources.includes(state.projSource)) state.projSource = sources[0] || 'ESPN';

  const offCats = cats.filter(c => c.type === 'offense');
  const pitCats = cats.filter(c => c.type === 'pitching');

  // Group players by lineup slot into sections
  const ilPlayers = players.filter(p => p.lineup_slot === 'IL');
  const benchHitters = players.filter(p => p.lineup_slot === 'BE' && !isPitcherPos(p.position));
  const benchPitchers = players.filter(p => p.lineup_slot === 'BE' && isPitcherPos(p.position));
  const activeHitters = players.filter(p => p.lineup_slot !== 'IL' && p.lineup_slot !== 'BE' && !isPitcherPos(p.lineup_slot));
  const activePitchers = players.filter(p => p.lineup_slot !== 'IL' && p.lineup_slot !== 'BE' && isPitcherPos(p.lineup_slot));

  // Sort active hitters by slot order
  const hitterSlotOrder = ['C', '1B', '2B', 'SS', '3B', '1B/3B', '2B/SS', 'IF', 'LF', 'CF', 'RF', 'OF', 'DH', 'UTIL'];
  activeHitters.sort((a, b) => {
    const ai = hitterSlotOrder.indexOf(a.lineup_slot);
    const bi = hitterSlotOrder.indexOf(b.lineup_slot);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });

  // Team dropdown
  const teamOptions = teams.map(t =>
    `<option value="${t.name.replace(/"/g, '&quot;')}" ${t.name === data.team_name ? 'selected' : ''}>${t.name}</option>`
  ).join('');

  let html = `
    <div class="page-header">
      <h2>${data.team_name}</h2>
      <p>Color coded by league percentile</p>
    </div>
    <div class="controls">
      <div class="control-group">
        <label>Team</label>
        <select class="control-select" onchange="selectTeam(this.value)">${teamOptions}</select>
      </div>
      <div class="control-group">
        <label>Proj</label>
        <div class="pill-group">
          ${sources.map(s => `<button class="pill ${state.projSource === s ? 'active' : ''}" onclick="setProjSource('${s}')">${s}</button>`).join('')}
        </div>
      </div>
    </div>`;

  if (activeHitters.length > 0) {
    html += '<h3 style="margin-bottom:12px;font-size:15px;font-weight:600">Hitters</h3>';
    html += buildRosterTable(activeHitters, offCats, 'hitters');
  }
  if (activePitchers.length > 0) {
    html += '<h3 style="margin:24px 0 12px;font-size:15px;font-weight:600">Pitchers</h3>';
    html += buildRosterTable(activePitchers, pitCats, 'pitchers');
  }
  if (benchHitters.length > 0 || benchPitchers.length > 0) {
    html += '<h3 style="margin:24px 0 12px;font-size:15px;font-weight:600">Bench</h3>';
    const benchAll = [...benchHitters, ...benchPitchers];
    const benchCats = benchHitters.length > benchPitchers.length ? offCats : pitCats;
    html += buildRosterTable(benchAll, benchCats, 'bench');
  }
  if (ilPlayers.length > 0) {
    html += '<h3 style="margin:24px 0 12px;font-size:15px;font-weight:600;color:var(--red)">Injured List</h3>';
    const ilHitters = ilPlayers.filter(p => !isPitcherPos(p.position));
    const ilPitchersList = ilPlayers.filter(p => isPitcherPos(p.position));
    const ilCats = ilHitters.length >= ilPitchersList.length ? offCats : pitCats;
    html += buildRosterTable(ilPlayers, ilCats, 'il');
  }

  el.innerHTML = html;
}

function isPitcherPos(pos) {
  return ['SP', 'RP', 'P'].includes(pos);
}

function posColor(pos) {
  return POS_COLORS[pos] || 'var(--text-secondary)';
}

function vbrBadge(vbr) {
  if (vbr >= 2) return 'score-hot';
  if (vbr >= 0) return 'score-warm';
  return 'score-cold';
}

function buildRosterTable(players, cats, tableId) {
  let html = `<div class="table-wrap"><table data-table="${tableId}">
    <thead><tr>
      <th onclick="sortRosterTable('${tableId}','slot')">Slot</th>
      <th onclick="sortRosterTable('${tableId}','name')">Player</th>
      <th onclick="sortRosterTable('${tableId}','vbr')" title="Value Based Ranking: sum of z-scores across league categories">VBR</th>`;
  for (const c of cats) {
    const label = c.display_name || c.name;
    html += `<th onclick="sortRosterTable('${tableId}','${c.name}')" title="${label}">${label}</th>`;
  }
  html += '</tr></thead><tbody>';

  for (const p of players) {
    const inj = p.injury_status !== 'ACTIVE'
      ? `<span class="injury-badge ${p.injury_status === 'DAY_TO_DAY' ? 'dtd' : 'il'}">${p.injury_status.replace(/_/g, ' ')}</span>` : '';

    const slot = p.lineup_slot || p.position;
    const color = posColor(slot);
    html += `<tr>
      <td><span class="pos-badge" style="background:${color}20;color:${color};border:1px solid ${color}40">${slot}</span></td>
      <td><span class="player-name">${p.name}</span><span class="player-team">${p.team} · ${p.position}</span> ${inj}</td>
      <td><span class="score-badge ${vbrBadge(p.vbr)}" style="font-size:11px;padding:2px 8px">${p.vbr > 0 ? '+' : ''}${p.vbr}</span></td>`;

    for (const c of cats) {
      const val = p.current_stats?.[c.name];
      const grade = p.grades?.[c.name] || 'avg';
      const proj = p.projections?.[state.projSource]?.[c.name];
      const projStr = proj !== undefined ? `<span style="font-size:10px;color:var(--text-muted)"> (${fmtStat(proj, c.name)})</span>` : '';
      html += `<td><span class="grade-${grade}">${fmtStat(val, c.name)}</span>${projStr}</td>`;
    }
    html += '</tr>';
  }

  html += '</tbody></table></div>';
  return html;
}

// Sortable roster tables
let rosterSortState = {};
function sortRosterTable(tableId, key) {
  const table = document.querySelector(`table[data-table="${tableId}"]`);
  if (!table) return;
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));

  // Toggle direction
  const prev = rosterSortState[tableId];
  const asc = (prev && prev.key === key) ? !prev.asc : (key === 'name' || key === 'slot');
  rosterSortState[tableId] = { key, asc };

  // Find column index
  const headers = Array.from(table.querySelectorAll('thead th'));
  let colIdx;
  if (key === 'slot') colIdx = 0;
  else if (key === 'name') colIdx = 1;
  else if (key === 'vbr') colIdx = 2;
  else colIdx = headers.findIndex(h => h.textContent === key || h.getAttribute('title') === key);
  if (colIdx < 0) colIdx = headers.findIndex(h => h.onclick?.toString().includes(key));

  rows.sort((a, b) => {
    const aText = a.cells[colIdx]?.textContent?.trim() || '';
    const bText = b.cells[colIdx]?.textContent?.trim() || '';
    const aNum = parseFloat(aText.replace(/[^0-9.\-]/g, ''));
    const bNum = parseFloat(bText.replace(/[^0-9.\-]/g, ''));
    if (!isNaN(aNum) && !isNaN(bNum)) {
      return asc ? aNum - bNum : bNum - aNum;
    }
    return asc ? aText.localeCompare(bText) : bText.localeCompare(aText);
  });

  rows.forEach(r => tbody.appendChild(r));
}

function selectTeam(name) {
  state.selectedTeam = name;
  state.rosterData = null;
  renderPage();
}

function setProjSource(source) {
  state.projSource = source;
  renderPage();
}

// ── Free Agents ──

async function renderFreeAgents(el) {
  const typeParam = state.faPlayerType ? `&player_type=${state.faPlayerType}` : '';
  const posParam = state.faPosition ? `&position=${state.faPosition}` : '';
  state.freeAgentsData = await api(`/api/free-agents?limit=150${typeParam}${posParam}`);
  const data = state.freeAgentsData;
  const cats = data.categories || [];
  const players = data.players || [];

  // Determine which cats to show based on filter
  let displayCats;
  if (state.faPlayerType === 'pitcher' || (state.faPosition && ['SP','RP','P'].includes(state.faPosition))) {
    displayCats = cats.filter(c => c.type === 'pitching');
  } else if (state.faPlayerType === 'batter' || (state.faPosition && !['SP','RP','P'].includes(state.faPosition))) {
    displayCats = cats.filter(c => c.type === 'offense');
  } else {
    displayCats = cats;
  }

  const projSources = new Set();
  players.forEach(p => Object.keys(p.projections || {}).forEach(s => projSources.add(s)));
  const sources = ['ESPN', ...Array.from(projSources).filter(s => s !== 'ESPN')];

  let html = `
    <div class="page-header">
      <h2>Free Agents</h2>
      <p>Players available in your league</p>
    </div>
    <div class="controls">
      <div class="control-group">
        <label>Type</label>
        <div class="pill-group">
          <button class="pill ${!state.faPlayerType ? 'active' : ''}" onclick="setFAPlayerType(null)">All</button>
          <button class="pill ${state.faPlayerType === 'batter' ? 'active' : ''}" onclick="setFAPlayerType('batter')">Batters</button>
          <button class="pill ${state.faPlayerType === 'pitcher' ? 'active' : ''}" onclick="setFAPlayerType('pitcher')">Pitchers</button>
        </div>
      </div>
      <div class="control-group">
        <label>Pos</label>
        <select class="control-select" onchange="setFAPosition(this.value)">
          <option value="">Any</option>
          ${['C','1B','2B','3B','SS','OF','DH','SP','RP'].map(p =>
            `<option value="${p}" ${state.faPosition === p ? 'selected' : ''}>${p}</option>`
          ).join('')}
        </select>
      </div>
      <div class="control-group">
        <label>View</label>
        <div class="pill-group">
          <button class="pill ${state.faStatView === 'current' ? 'active' : ''}" onclick="setFAStatView('current')">Current</button>
          <button class="pill ${state.faStatView === 'projected' ? 'active' : ''}" onclick="setFAStatView('projected')">Projected</button>
          <button class="pill ${state.faStatView === 'research' ? 'active' : ''}" onclick="setFAStatView('research')">Research</button>
        </div>
      </div>
      ${state.faStatView === 'projected' ? `
      <div class="control-group">
        <label>Source</label>
        <div class="pill-group">
          ${sources.map(s => `<button class="pill ${state.projSource === s ? 'active' : ''}" onclick="setFAProjSource('${s}')">${s}</button>`).join('')}
        </div>
      </div>` : ''}
    </div>`;

  if (players.length === 0) {
    html += '<div class="empty-state"><p>No free agents found.</p></div>';
    el.innerHTML = html;
    return;
  }

  if (state.faStatView === 'research') {
    html += buildFAResearchTable(players);
  } else {
    html += buildFAStatsTable(players, displayCats);
  }

  el.innerHTML = html;
}

function buildFAStatsTable(players, cats) {
  const isProj = state.faStatView === 'projected';
  let html = `<div class="table-wrap"><table data-table="fa">
    <thead><tr>
      <th onclick="sortFATable('name')">Player</th>
      <th onclick="sortFATable('pos')">Pos</th>
      <th onclick="sortFATable('vbr')" title="Value Based Ranking">${isProj ? 'pVBR' : 'VBR'}</th>
      <th onclick="sortFATable('own')">Own%</th>`;
  for (const c of cats) {
    const label = c.display_name || c.name;
    html += `<th onclick="sortFATable('${c.name}')" title="${label}">${label}</th>`;
  }
  html += '</tr></thead><tbody>';

  for (const p of players) {
    const inj = p.injury_status !== 'ACTIVE'
      ? ` <span class="injury-badge ${p.injury_status === 'DAY_TO_DAY' ? 'dtd' : 'il'}">${p.injury_status.replace(/_/g, ' ')}</span>` : '';

    const vbr = isProj ? p.proj_vbr : p.vbr;
    const upgradeClass = p.is_upgrade ? ' fa-upgrade' : '';
    const color = posColor(p.position);

    let displayStats = isProj ? (p.projected_stats || {}) : (p.current_stats || {});
    if (isProj && state.projSource !== 'ESPN' && p.projections?.[state.projSource]) {
      displayStats = p.projections[state.projSource];
    }

    html += `<tr class="${upgradeClass}">
      <td><span class="player-name">${p.name}</span><span class="player-team">${p.team}</span>${inj}${p.is_upgrade ? ' <span class="upgrade-badge">UPGRADE</span>' : ''}</td>
      <td><span class="pos-badge" style="background:${color}20;color:${color};border:1px solid ${color}40">${p.position}</span></td>
      <td><span class="score-badge ${vbrBadge(vbr)}" style="font-size:11px;padding:2px 8px">${vbr > 0 ? '+' : ''}${vbr}</span></td>
      <td>${p.pct_owned}%</td>`;

    for (const c of cats) {
      html += `<td>${fmtStat(displayStats?.[c.name], c.name)}</td>`;
    }
    html += '</tr>';
  }

  html += '</tbody></table></div>';
  return html;
}

function buildFAResearchTable(players) {
  let html = `<div class="table-wrap"><table data-table="fa">
    <thead><tr>
      <th onclick="sortFATable('name')">Player</th>
      <th onclick="sortFATable('pos')">Pos</th>
      <th onclick="sortFATable('vbr')">VBR</th>
      <th onclick="sortFATable('pvbr')">pVBR</th>
      <th onclick="sortFATable('own')">Own%</th>
      <th onclick="sortFATable('start')">Start%</th>
      <th onclick="sortFATable('team')">Team</th>
      <th>Eligible</th>
    </tr></thead><tbody>`;

  for (const p of players) {
    const inj = p.injury_status !== 'ACTIVE'
      ? ` <span class="injury-badge ${p.injury_status === 'DAY_TO_DAY' ? 'dtd' : 'il'}">${p.injury_status.replace(/_/g, ' ')}</span>` : '';
    const color = posColor(p.position);
    const upgradeClass = p.is_upgrade ? ' fa-upgrade' : '';

    // Ownership heat color
    const ownPct = p.pct_owned;
    const ownColor = ownPct >= 50 ? 'var(--green)' : ownPct >= 20 ? 'var(--yellow)' : ownPct >= 5 ? 'var(--orange)' : 'var(--text-muted)';

    html += `<tr class="${upgradeClass}">
      <td><span class="player-name">${p.name}</span>${inj}${p.is_upgrade ? ' <span class="upgrade-badge">UPGRADE</span>' : ''}</td>
      <td><span class="pos-badge" style="background:${color}20;color:${color};border:1px solid ${color}40">${p.position}</span></td>
      <td><span class="score-badge ${vbrBadge(p.vbr)}" style="font-size:11px;padding:2px 8px">${p.vbr > 0 ? '+' : ''}${p.vbr}</span></td>
      <td><span class="score-badge ${vbrBadge(p.proj_vbr)}" style="font-size:11px;padding:2px 8px">${p.proj_vbr > 0 ? '+' : ''}${p.proj_vbr}</span></td>
      <td><span style="color:${ownColor};font-weight:600">${ownPct}%</span></td>
      <td>${p.pct_started}%</td>
      <td>${p.team}</td>
      <td style="font-size:11px;color:var(--text-muted)">${(p.eligible_slots || []).filter(s => !['BE','IL','UTIL'].includes(s)).join(', ')}</td>
    </tr>`;
  }

  html += '</tbody></table></div>';
  return html;
}

// FA table sorting
let faSortState = {};
function sortFATable(key) {
  const table = document.querySelector('table[data-table="fa"]');
  if (!table) return;
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));

  const prev = faSortState.key;
  const asc = (prev === key) ? !faSortState.asc : (key === 'name' || key === 'pos' || key === 'team');
  faSortState = { key, asc };

  const headers = Array.from(table.querySelectorAll('thead th'));
  let colIdx = headers.findIndex(h => {
    const oc = h.getAttribute('onclick') || '';
    return oc.includes(`'${key}'`);
  });
  if (colIdx < 0) colIdx = 0;

  rows.sort((a, b) => {
    const aText = a.cells[colIdx]?.textContent?.trim() || '';
    const bText = b.cells[colIdx]?.textContent?.trim() || '';
    const aNum = parseFloat(aText.replace(/[^0-9.\-]/g, ''));
    const bNum = parseFloat(bText.replace(/[^0-9.\-]/g, ''));
    if (!isNaN(aNum) && !isNaN(bNum)) {
      return asc ? aNum - bNum : bNum - aNum;
    }
    return asc ? aText.localeCompare(bText) : bText.localeCompare(aText);
  });

  rows.forEach(r => tbody.appendChild(r));
}

function setFAPlayerType(type) {
  state.faPlayerType = type;
  state.freeAgentsData = null;
  renderPage();
}

function setFAPosition(pos) {
  state.faPosition = pos || null;
  state.freeAgentsData = null;
  renderPage();
}

function setFAStatView(view) {
  state.faStatView = view;
  renderPage();
}

function setFAProjSource(source) {
  state.projSource = source;
  renderPage();
}

// ── Helpers ──

function fmtStat(val, statName) {
  if (val === undefined || val === null) return '—';
  const ratioStats = ['AVG', 'OBP', 'SLG', 'OPS', 'WHIP'];
  const eraStats = ['ERA', 'K/9', 'BB/9', 'HR/9'];
  if (ratioStats.includes(statName)) return Number(val).toFixed(3);
  if (eraStats.includes(statName)) return Number(val).toFixed(2);
  if (statName === 'IP') return Number(val).toFixed(1);
  if (Number.isInteger(val)) return val.toString();
  return Number(val).toFixed(1);
}

function fmtML(ml) {
  if (!ml) return '—';
  return ml > 0 ? `+${ml}` : ml.toString();
}

function nextDay(dateStr) {
  const d = new Date(dateStr + 'T12:00:00');
  d.setDate(d.getDate() + 1);
  return d.toISOString().split('T')[0];
}

async function refreshData() {
  const btn = event?.target || document.querySelector('.sidebar-footer .refresh-btn');
  if (btn) btn.textContent = 'Refreshing...';
  try {
    await apiPost('/api/refresh-espn');
    await apiPost('/api/refresh-projections');
    state.spData = null;
    state.rankingsData = null;
    state.rosterData = null;
    state.freeAgentsData = null;
    await renderPage();
  } finally {
    if (btn) btn.textContent = 'Refresh Data';
  }
}

// ── Account menu ──

function toggleAccountMenu() {
  const dropdown = document.getElementById('account-dropdown');
  if (dropdown) dropdown.classList.toggle('open');
}

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
  const menu = document.getElementById('account-menu');
  const dropdown = document.getElementById('account-dropdown');
  if (dropdown && menu && !menu.contains(e.target)) {
    dropdown.classList.remove('open');
  }
});

// ── Account (mock) ──

function renderAccount(el) {
  const leagueName = state.leagues.length > 0 ? state.leagues.find(l => l.id === state.activeLeagueId)?.name || 'Unknown' : 'Not connected';

  el.innerHTML = `
    <div class="page-header">
      <h2>Profile <span class="mock-badge">Mock</span></h2>
      <p>Account details and connected services</p>
    </div>

    <div class="settings-section">
      <div style="display:flex;align-items:center;gap:20px;padding:8px 0">
        <div class="avatar-circle">F</div>
        <div>
          <div style="font-size:18px;font-weight:700">Fantasy Manager</div>
          <div style="font-size:13px;color:var(--text-muted);margin-top:2px">fantasy@example.com</div>
        </div>
      </div>
    </div>

    <div class="settings-section">
      <h3>Connected Services</h3>
      <div class="setting-row">
        <div class="setting-label">
          ESPN Fantasy
          <small>League access via cookies</small>
        </div>
        <div class="setting-value" style="color:var(--green);font-weight:600">${state.hasEspn ? 'Connected' : 'Not Connected'}</div>
      </div>
      <div class="setting-row">
        <div class="setting-label">
          Active League
          <small>Current league selection</small>
        </div>
        <div class="setting-value">${leagueName}</div>
      </div>
      <div class="setting-row">
        <div class="setting-label">
          Leagues Configured
          <small>Total leagues in your config</small>
        </div>
        <div class="setting-value">${state.leagues.length}</div>
      </div>
    </div>

    <div class="settings-section">
      <h3>Data Sources</h3>
      <div class="setting-row">
        <div class="setting-label">
          MLB Stats API
          <small>Probable pitchers, schedules</small>
        </div>
        <div class="setting-value" style="color:var(--green);font-weight:600">Active</div>
      </div>
      <div class="setting-row">
        <div class="setting-label">
          Vegas Odds
          <small>Moneylines, over/unders</small>
        </div>
        <div class="setting-value" style="color:var(--text-muted)">API Key Required</div>
      </div>
      <div class="setting-row">
        <div class="setting-label">
          FanGraphs Projections
          <small>Steamer, ZiPS, ATC</small>
        </div>
        <div class="setting-value" style="color:var(--text-muted)">Coming Soon</div>
      </div>
    </div>

    <div class="settings-section" style="opacity:0.6">
      <h3>Danger Zone</h3>
      <div class="setting-row">
        <div class="setting-label">
          Clear All Cached Data
          <small>Force refresh from ESPN</small>
        </div>
        <button class="refresh-btn" style="width:auto;padding:6px 16px" onclick="refreshData()">Clear Cache</button>
      </div>
    </div>`;
}

// ── Settings (mock) ──

function renderSettings(el) {
  el.innerHTML = `
    <div class="page-header">
      <h2>Settings <span class="mock-badge">Mock</span></h2>
      <p>App preferences and configuration</p>
    </div>

    <div class="settings-section">
      <h3>Display</h3>
      <div class="setting-row">
        <div class="setting-label">
          Theme
          <small>App color scheme</small>
        </div>
        <div class="setting-value">Dark</div>
      </div>
      <div class="setting-row">
        <div class="setting-label">
          Default Page
          <small>Page shown on app launch</small>
        </div>
        <div class="setting-value">SP Picker</div>
      </div>
      <div class="setting-row">
        <div class="setting-label">
          Compact Tables
          <small>Reduce row height in tables</small>
        </div>
        <div class="toggle-switch" onclick="this.classList.toggle('on')"></div>
      </div>
      <div class="setting-row">
        <div class="setting-label">
          Show Projected Stats
          <small>Display projections alongside current stats</small>
        </div>
        <div class="toggle-switch on" onclick="this.classList.toggle('on')"></div>
      </div>
    </div>

    <div class="settings-section">
      <h3>SP Picker</h3>
      <div class="setting-row">
        <div class="setting-label">
          Days Ahead
          <small>Number of days to look ahead for starts</small>
        </div>
        <div class="setting-value">7</div>
      </div>
      <div class="setting-row">
        <div class="setting-label">
          Minimum Score
          <small>Hide pitchers below this score</small>
        </div>
        <div class="setting-value">0</div>
      </div>
      <div class="setting-row">
        <div class="setting-label">
          Include Vegas Lines
          <small>Factor moneylines into scoring</small>
        </div>
        <div class="toggle-switch on" onclick="this.classList.toggle('on')"></div>
      </div>
    </div>

    <div class="settings-section">
      <h3>Free Agents</h3>
      <div class="setting-row">
        <div class="setting-label">
          Default View
          <small>Stats view when opening Free Agents</small>
        </div>
        <div class="setting-value">Current</div>
      </div>
      <div class="setting-row">
        <div class="setting-label">
          Highlight Upgrades
          <small>Show green highlight for potential upgrades</small>
        </div>
        <div class="toggle-switch on" onclick="this.classList.toggle('on')"></div>
      </div>
      <div class="setting-row">
        <div class="setting-label">
          Results Per Page
          <small>Number of free agents to load</small>
        </div>
        <div class="setting-value">150</div>
      </div>
    </div>

    <div class="settings-section">
      <h3>Notifications</h3>
      <div class="setting-row">
        <div class="setting-label">
          Roster Alerts
          <small>Notify when IL-eligible players are in lineup</small>
        </div>
        <div class="toggle-switch" onclick="this.classList.toggle('on')"></div>
      </div>
      <div class="setting-row">
        <div class="setting-label">
          Waiver Wire Alerts
          <small>Notify when high-value FAs become available</small>
        </div>
        <div class="toggle-switch" onclick="this.classList.toggle('on')"></div>
      </div>
    </div>`;
}

// ── League switching ──

async function loadLeagues() {
  try {
    const data = await api('/api/leagues');
    state.hasEspn = data.has_espn;
    state.leagues = data.leagues || [];
    state.activeLeagueId = data.active_id;
  } catch (e) {
    console.error('Failed to load leagues:', e);
  }
}

function renderLeagueSwitcher() {
  const switcher = document.getElementById('league-switcher');
  const nameEl = document.getElementById('league-name');
  const select = document.getElementById('league-select');

  if (!state.hasEspn || state.leagues.length === 0) {
    if (switcher) switcher.style.display = 'none';
    if (nameEl) nameEl.textContent = 'SP Tracker (Public)';
    return;
  }

  if (state.leagues.length === 1) {
    if (switcher) switcher.style.display = 'none';
    if (nameEl) nameEl.textContent = state.leagues[0].name;
    return;
  }

  // Multiple leagues — show dropdown
  if (switcher) switcher.style.display = 'block';
  if (nameEl) nameEl.style.display = 'none';
  if (select) {
    select.innerHTML = state.leagues.map(l =>
      `<option value="${l.id}" ${l.id === state.activeLeagueId ? 'selected' : ''}>${l.name}</option>`
    ).join('');
  }
}

async function switchLeague(leagueId) {
  const id = parseInt(leagueId);
  if (id === state.activeLeagueId) return;
  await apiPost(`/api/switch-league?league_id=${id}`);
  state.activeLeagueId = id;
  state.leagueInfo = null;
  state.spData = null;
  state.rankingsData = null;
  state.rosterData = null;
  state.freeAgentsData = null;
  state.selectedTeam = null;
  renderLeagueSwitcher();
  renderPage();
}

function updateNavVisibility() {
  const espnPages = ['rankings', 'my-roster', 'free-agents'];
  document.querySelectorAll('.nav-item').forEach(el => {
    if (espnPages.includes(el.dataset.page)) {
      el.style.display = state.hasEspn ? '' : 'none';
    }
  });
  if (!state.hasEspn && espnPages.includes(state.currentPage)) {
    state.currentPage = 'sp-picker';
    document.querySelectorAll('.nav-item').forEach(el => {
      el.classList.toggle('active', el.dataset.page === 'sp-picker');
    });
  }
}

// ── Init ──

document.addEventListener('DOMContentLoaded', async () => {
  await loadLeagues();
  renderLeagueSwitcher();
  updateNavVisibility();

  if (state.hasEspn) {
    try {
      state.leagueInfo = await api('/api/league');
    } catch (e) {
      console.error('Failed to load league info:', e);
    }
  }

  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => navigate(item.dataset.page));
  });

  renderPage();
});
