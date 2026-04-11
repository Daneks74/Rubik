// ── Fantasy Baseball Dashboard ──

const state = {
  currentPage: 'sp-picker',
  leagueInfo: null,
  spData: null,
  rankingsData: null,
  rosterData: null,
  freeAgentsData: null,
  rankingsMode: 'current',
  faPosition: null,
  faStatView: 'current',
  selectedTeam: null,
  projSource: 'ESPN',
  sortCol: null,
  sortDir: 'desc',
};

// ── API helpers ──

async function api(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    if (body.setup_required) {
      throw new Error('SETUP_REQUIRED');
    }
    throw new Error(body.error || `API error: ${resp.status}`);
  }
  return resp.json();
}

async function apiPost(url) {
  const resp = await fetch(url, { method: 'POST' });
  return resp.json();
}

// ── Navigation ──

function navigate(page) {
  state.currentPage = page;
  state.sortCol = null;
  state.sortDir = 'desc';
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
      case 'rankings': await renderRankings(content); break;
      case 'my-roster': await renderMyRoster(content); break;
      case 'free-agents': await renderFreeAgents(content); break;
    }
  } catch (err) {
    if (err.message === 'SETUP_REQUIRED') {
      content.innerHTML = `<div class="empty-state" style="max-width:560px;margin:80px auto;text-align:left">
        <h2 style="color:var(--accent);margin-bottom:16px">Setup Required</h2>
        <p style="margin-bottom:16px">To connect to your ESPN Fantasy league, create a <code>.env</code> file in the project root:</p>
        <div style="background:var(--bg-secondary);border:1px solid var(--border);border-radius:8px;padding:16px;font-family:monospace;font-size:13px;margin-bottom:16px;line-height:1.8">
          ESPN_LEAGUE_ID=<span style="color:var(--accent)">your_league_id</span><br>
          ESPN_S2=<span style="color:var(--accent)">your_espn_s2_cookie</span><br>
          ESPN_SWID=<span style="color:var(--accent)">{your_swid_cookie}</span><br>
          SEASON_YEAR=2026<br>
          ODDS_API_KEY=<span style="color:var(--accent)">your_odds_api_key</span>
        </div>
        <p style="color:var(--text-secondary);font-size:13px"><strong>ESPN cookies:</strong> Open ESPN Fantasy in Chrome → F12 → Application → Cookies → espn.com → copy <code>espn_s2</code> and <code>SWID</code></p>
        <p style="color:var(--text-secondary);font-size:13px;margin-top:8px"><strong>Odds API:</strong> Get a free key at <code>the-odds-api.com</code></p>
        <p style="color:var(--text-secondary);font-size:13px;margin-top:16px">After creating <code>.env</code>, restart the server and refresh this page.</p>
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

  let html = `
    <div class="page-header">
      <h2>Starting Pitcher Picker</h2>
      <p>Free agent SPs with scheduled starts over the next 7 days, scored by projections${data.has_odds ? ' & Vegas lines' : ''}</p>
    </div>
    <div class="summary-row">
      <div class="summary-card"><div class="label">Free Agent SPs</div><div class="value accent">${data.total_free_agents}</div></div>
      <div class="summary-card"><div class="label">With Starts</div><div class="value green">${data.total_with_starts}</div></div>
      <div class="summary-card"><div class="label">Days Covered</div><div class="value">${dates.length}</div></div>
      <div class="summary-card"><div class="label">Vegas Lines</div><div class="value">${data.has_odds ? 'Yes' : 'No'}</div></div>
    </div>`;

  if (dates.length === 0) {
    html += '<div class="empty-state"><p>No free agent SPs with scheduled starts found.</p></div>';
    el.innerHTML = html;
    return;
  }

  for (const d of dates) {
    const pitchers = data.dates[d];
    const dateObj = new Date(d + 'T12:00:00');
    const dayName = dateObj.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
    let labelClass = 'day-future';
    let labelText = '';
    if (d === today) { labelClass = 'day-today'; labelText = 'Today'; }
    else if (dates.indexOf(d) === 0 || d === nextDay(today)) { labelClass = 'day-tomorrow'; labelText = 'Tomorrow'; }

    html += `<div class="day-section"><div class="day-header"><h3>${dayName}</h3>`;
    if (labelText) html += `<span class="day-label ${labelClass}">${labelText}</span>`;
    html += `<span style="color:var(--text-muted);font-size:12px">${pitchers.length} pitcher${pitchers.length !== 1 ? 's' : ''}</span></div>`;

    html += `<div class="table-wrap"><table>
      <thead><tr>
        <th>#</th><th>Pitcher</th><th>Matchup</th><th>Proj Pts</th><th>Own%</th>
        ${data.has_odds ? '<th>ML</th><th>Win%</th><th>O/U</th>' : ''}
        <th>Score</th>
      </tr></thead><tbody>`;

    pitchers.forEach((p, i) => {
      const scoreClass = p.score >= 65 ? 'score-hot' : p.score >= 45 ? 'score-warm' : 'score-cold';
      const matchup = p.is_home ? `vs ${p.opponent}` : `@ ${p.opponent}`;
      html += `<tr>
        <td>${i + 1}</td>
        <td><span class="player-name">${p.name}</span><span class="player-team">${p.team}</span></td>
        <td>${matchup}</td>
        <td>${p.projected_pts || '—'}</td>
        <td>${p.pct_owned}%</td>
        ${data.has_odds ? `
          <td>${fmtML(p.moneyline)}</td>
          <td>${p.win_prob ? p.win_prob + '%' : '—'}</td>
          <td>${p.over_under || '—'}</td>
        ` : ''}
        <td><span class="score-badge ${scoreClass}">${p.score}</span></td>
      </tr>`;
    });
    html += '</tbody></table></div></div>';
  }

  el.innerHTML = html;
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
      <h2>League Rankings</h2>
      <p>Teams ranked by stat categories — ${state.rankingsMode === 'current' ? 'current season stats' : 'projected stats'}</p>
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

  // Overall rankings table
  html += '<h3 style="margin-bottom:12px;font-size:15px;font-weight:600">Overall Rankings</h3>';
  html += buildRankingsTable(data.teams, cats, source, 'all');

  if (offCats.length > 0) {
    html += '<h3 style="margin:28px 0 12px;font-size:15px;font-weight:600">Offensive Categories</h3>';
    html += buildRankingsTable(data.teams, offCats, source, 'offense');
  }
  if (pitCats.length > 0) {
    html += '<h3 style="margin:28px 0 12px;font-size:15px;font-weight:600">Pitching Categories</h3>';
    html += buildRankingsTable(data.teams, pitCats, source, 'pitching');
  }

  el.innerHTML = html;
}

function buildRankingsTable(teams, cats, source, section) {
  let html = `<div class="table-wrap"><table>
    <thead><tr>
      <th>Rank</th><th>Team</th>`;
  for (const c of cats) {
    html += `<th title="${c.display_name || c.name}">${c.name}</th>`;
  }
  if (section === 'all') html += '<th>Total</th>';
  html += '</tr></thead><tbody>';

  // Sort teams by rank sum for this section
  const sorted = [...teams].sort((a, b) => {
    if (section === 'all') return a.overall_rank - b.overall_rank;
    const sumA = cats.reduce((s, c) => s + (a.ranks?.[c.name] || 99), 0);
    const sumB = cats.reduce((s, c) => s + (b.ranks?.[c.name] || 99), 0);
    return sumA - sumB;
  });

  sorted.forEach((t, idx) => {
    const rankCls = idx === 0 ? 'rank-1' : idx === 1 ? 'rank-2' : idx === 2 ? 'rank-3' : 'rank-default';
    html += `<tr><td><span class="rank-badge ${rankCls}">${idx + 1}</span></td>
      <td><span class="player-name">${t.team_name}</span></td>`;
    for (const c of cats) {
      const val = t[source]?.[c.name];
      const rank = t.ranks?.[c.name] || '—';
      const rClass = rank <= 3 ? 'grade-elite' : rank <= 6 ? 'grade-good' : rank <= 9 ? 'grade-avg' : 'grade-poor';
      html += `<td><span class="${rClass}">${fmtStat(val, c.name)}</span> <span style="font-size:10px;color:var(--text-muted)">#${rank}</span></td>`;
    }
    if (section === 'all') {
      html += `<td>${t.total_rank_score}</td>`;
    }
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

async function renderMyRoster(el) {
  if (!state.selectedTeam) {
    const data = await api('/api/my-roster');
    if (data.needs_selection) {
      let html = `<div class="page-header"><h2>My Roster</h2><p>Select your team to review your roster</p></div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px">`;
      for (const t of data.teams) {
        html += `<div class="summary-card" style="cursor:pointer" onclick="selectTeam('${t.name.replace(/'/g, "\\'")}')">
          <div class="label">Team</div><div class="value" style="font-size:16px">${t.name}</div></div>`;
      }
      html += '</div>';
      el.innerHTML = html;
      return;
    }
  }

  state.rosterData = await api(`/api/my-roster?team_name=${encodeURIComponent(state.selectedTeam)}`);
  const data = state.rosterData;
  const cats = data.categories || [];
  const players = data.players || [];
  const projSources = new Set();
  players.forEach(p => Object.keys(p.projections || {}).forEach(s => projSources.add(s)));
  const sources = ['ESPN', ...Array.from(projSources).filter(s => s !== 'ESPN')];
  if (!sources.includes(state.projSource)) state.projSource = sources[0] || 'ESPN';

  // Split by position type
  const hitters = players.filter(p => !['SP', 'RP', 'P'].includes(p.position));
  const pitchers = players.filter(p => ['SP', 'RP', 'P'].includes(p.position));
  const offCats = cats.filter(c => c.type === 'offense');
  const pitCats = cats.filter(c => c.type === 'pitching');

  let html = `
    <div class="page-header">
      <h2>${data.team_name}</h2>
      <p>Player-by-player performance review — color coded by league percentile</p>
    </div>
    <div class="controls">
      <div class="control-group">
        <label>Projections</label>
        <div class="pill-group">
          ${sources.map(s => `<button class="pill ${state.projSource === s ? 'active' : ''}" onclick="setProjSource('${s}')">${s}</button>`).join('')}
        </div>
      </div>
      <button class="refresh-btn" onclick="selectTeam(null)" style="width:auto;padding:6px 14px">Change Team</button>
    </div>`;

  if (hitters.length > 0) {
    html += '<h3 style="margin-bottom:12px;font-size:15px;font-weight:600">Hitters</h3>';
    html += buildRosterTable(hitters, offCats, sources);
  }
  if (pitchers.length > 0) {
    html += '<h3 style="margin:28px 0 12px;font-size:15px;font-weight:600">Pitchers</h3>';
    html += buildRosterTable(pitchers, pitCats, sources);
  }

  el.innerHTML = html;
}

function buildRosterTable(players, cats, sources) {
  let html = `<div class="table-wrap"><table>
    <thead><tr><th>Player</th><th>Pos</th><th>Pts</th>`;
  for (const c of cats) html += `<th>${c.name}</th>`;
  html += '<th>Proj Pts</th></tr></thead><tbody>';

  for (const p of players) {
    const injBadge = p.injury_status !== 'ACTIVE'
      ? `<span class="injury-badge ${p.injury_status === 'DAY_TO_DAY' ? 'dtd' : 'il'}">${p.injury_status.replace('_', ' ')}</span>` : '';

    html += `<tr><td><span class="player-name">${p.name}</span><span class="player-team">${p.team}</span> ${injBadge}</td>
      <td><span class="player-pos">${p.position}</span></td>
      <td>${p.total_points}</td>`;

    for (const c of cats) {
      const val = p.current_stats?.[c.name];
      const grade = p.grades?.[c.name] || 'avg';
      // Show projected value from selected source if available
      const proj = p.projections?.[state.projSource]?.[c.name];
      const projStr = proj !== undefined ? `<span style="font-size:10px;color:var(--text-muted)"> (${fmtStat(proj, c.name)})</span>` : '';
      html += `<td><span class="grade-${grade}">${fmtStat(val, c.name)}</span>${projStr}</td>`;
    }

    const projPts = p.projections?.[state.projSource]?.['total_points'] || p.projected_points || '—';
    html += `<td>${projPts}</td></tr>`;
  }

  html += '</tbody></table></div>';
  return html;
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
  const posParam = state.faPosition ? `&position=${state.faPosition}` : '';
  state.freeAgentsData = await api(`/api/free-agents?stat_view=${state.faStatView}&limit=100${posParam}`);
  const data = state.freeAgentsData;
  const cats = data.categories || [];
  const players = data.players || [];

  // Determine which cats to show based on position filter
  let displayCats = cats;
  if (state.faPosition) {
    const pitPositions = ['SP', 'RP', 'P'];
    if (pitPositions.includes(state.faPosition)) {
      displayCats = cats.filter(c => c.type === 'pitching');
    } else {
      displayCats = cats.filter(c => c.type === 'offense');
    }
  }

  const projSources = new Set();
  players.forEach(p => Object.keys(p.projections || {}).forEach(s => projSources.add(s)));
  const sources = ['ESPN', ...Array.from(projSources).filter(s => s !== 'ESPN')];

  let html = `
    <div class="page-header">
      <h2>Free Agents</h2>
      <p>Best available players — ${data.stat_view === 'current' ? 'current stats' : 'projected stats'}</p>
    </div>
    <div class="controls">
      <div class="control-group">
        <label>Position</label>
        <select class="control-select" onchange="setFAPosition(this.value)">
          <option value="">All</option>
          <option value="C" ${state.faPosition === 'C' ? 'selected' : ''}>C</option>
          <option value="1B" ${state.faPosition === '1B' ? 'selected' : ''}>1B</option>
          <option value="2B" ${state.faPosition === '2B' ? 'selected' : ''}>2B</option>
          <option value="3B" ${state.faPosition === '3B' ? 'selected' : ''}>3B</option>
          <option value="SS" ${state.faPosition === 'SS' ? 'selected' : ''}>SS</option>
          <option value="OF" ${state.faPosition === 'OF' ? 'selected' : ''}>OF</option>
          <option value="DH" ${state.faPosition === 'DH' ? 'selected' : ''}>DH</option>
          <option value="SP" ${state.faPosition === 'SP' ? 'selected' : ''}>SP</option>
          <option value="RP" ${state.faPosition === 'RP' ? 'selected' : ''}>RP</option>
        </select>
      </div>
      <div class="control-group">
        <label>Stats</label>
        <div class="pill-group">
          <button class="pill ${state.faStatView === 'current' ? 'active' : ''}" onclick="setFAStatView('current')">Current</button>
          <button class="pill ${state.faStatView === 'projected' ? 'active' : ''}" onclick="setFAStatView('projected')">Projected</button>
        </div>
      </div>
      <div class="control-group">
        <label>Source</label>
        <div class="pill-group">
          ${sources.map(s => `<button class="pill ${state.projSource === s ? 'active' : ''}" onclick="setFAProjSource('${s}')">${s}</button>`).join('')}
        </div>
      </div>
    </div>`;

  if (players.length === 0) {
    html += '<div class="empty-state"><p>No free agents found for this filter.</p></div>';
    el.innerHTML = html;
    return;
  }

  html += `<div class="table-wrap"><table>
    <thead><tr>
      <th>Player</th><th>Pos</th><th>Team</th><th>Own%</th><th>Pts</th>`;
  for (const c of displayCats) html += `<th>${c.name}</th>`;
  html += '</tr></thead><tbody>';

  for (const p of players) {
    const injBadge = p.injury_status !== 'ACTIVE'
      ? ` <span class="injury-badge ${p.injury_status === 'DAY_TO_DAY' ? 'dtd' : 'il'}">${p.injury_status.replace('_', ' ')}</span>` : '';

    // If showing projected and a source is selected, use that source's projections
    let displayStats = p.stats;
    if (state.faStatView === 'projected' && state.projSource !== 'ESPN' && p.projections?.[state.projSource]) {
      displayStats = p.projections[state.projSource];
    }

    html += `<tr>
      <td><span class="player-name">${p.name}</span>${injBadge}</td>
      <td><span class="player-pos">${p.position}</span></td>
      <td>${p.team}</td>
      <td>${p.pct_owned}%</td>
      <td>${state.faStatView === 'projected' ? p.projected_points : p.total_points}</td>`;

    for (const c of displayCats) {
      const val = displayStats?.[c.name];
      html += `<td>${fmtStat(val, c.name)}</td>`;
    }
    html += '</tr>';
  }

  html += '</tbody></table></div>';
  el.innerHTML = html;
}

function setFAPosition(pos) {
  state.faPosition = pos || null;
  state.freeAgentsData = null;
  renderPage();
}

function setFAStatView(view) {
  state.faStatView = view;
  state.freeAgentsData = null;
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
  const btn = document.querySelector('.refresh-btn');
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

// ── Init ──

document.addEventListener('DOMContentLoaded', async () => {
  // Load league info
  try {
    state.leagueInfo = await api('/api/league');
    const nameEl = document.getElementById('league-name');
    if (nameEl) nameEl.textContent = state.leagueInfo.settings?.league_name || '';
  } catch (e) {
    console.error('Failed to load league info:', e);
  }

  // Set up nav
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => navigate(item.dataset.page));
  });

  renderPage();
});
