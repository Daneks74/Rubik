// ── Fantasy Baseball Dashboard ──

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
  faPosition: null,
  faStatView: 'current',
  selectedTeam: null,
  projSource: 'ESPN',
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

// ── Navigation ──

function navigate(page) {
  state.currentPage = page;
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
        <th>#</th><th>Pitcher</th><th>Matchup</th>`;
    if (isPublic) {
      html += '<th>ERA</th><th>WHIP</th><th>K/9</th>';
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
        <td><span class="player-name">${p.name}</span><span class="player-team">${p.team}</span></td>
        <td>${mu}</td>`;
      if (isPublic) {
        html += `<td>${fmtStat(p.era, 'ERA')}</td><td>${fmtStat(p.whip, 'WHIP')}</td><td>${fmtStat(p.k9, 'K/9')}</td>`;
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
            ${isPublic
              ? `<span>ERA: ${fmtStat(p.era, 'ERA')}</span><span>WHIP: ${fmtStat(p.whip, 'WHIP')}</span><span>K/9: ${fmtStat(p.k9, 'K/9')}</span>`
              : `<span>Proj: ${p.projected_pts || '—'}</span><span>Own: ${p.pct_owned}%</span>`}
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

async function renderMyRoster(el) {
  if (!state.selectedTeam) {
    const data = await api('/api/my-roster');
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
  }

  state.rosterData = await api(`/api/my-roster?team_name=${encodeURIComponent(state.selectedTeam)}`);
  const data = state.rosterData;
  const cats = data.categories || [];
  const players = data.players || [];
  const projSources = new Set();
  players.forEach(p => Object.keys(p.projections || {}).forEach(s => projSources.add(s)));
  const sources = ['ESPN', ...Array.from(projSources).filter(s => s !== 'ESPN')];
  if (!sources.includes(state.projSource)) state.projSource = sources[0] || 'ESPN';

  const hitters = players.filter(p => !['SP', 'RP', 'P'].includes(p.position));
  const pitchers = players.filter(p => ['SP', 'RP', 'P'].includes(p.position));
  const offCats = cats.filter(c => c.type === 'offense');
  const pitCats = cats.filter(c => c.type === 'pitching');

  let html = `
    <div class="page-header">
      <h2>${data.team_name}</h2>
      <p>Color coded by league percentile</p>
    </div>
    <div class="controls">
      <div class="control-group">
        <label>Proj</label>
        <div class="pill-group">
          ${sources.map(s => `<button class="pill ${state.projSource === s ? 'active' : ''}" onclick="setProjSource('${s}')">${s}</button>`).join('')}
        </div>
      </div>
      <button class="refresh-btn" onclick="selectTeam(null)" style="width:auto;padding:6px 12px;font-size:11px">Change</button>
    </div>`;

  if (hitters.length > 0) {
    html += '<h3 style="margin-bottom:12px;font-size:15px;font-weight:600">Hitters</h3>';
    html += buildRosterTable(hitters, offCats);
  }
  if (pitchers.length > 0) {
    html += '<h3 style="margin:24px 0 12px;font-size:15px;font-weight:600">Pitchers</h3>';
    html += buildRosterTable(pitchers, pitCats);
  }

  el.innerHTML = html;
}

function buildRosterTable(players, cats) {
  let html = `<div class="table-wrap"><table>
    <thead><tr><th>Player</th><th>Pos</th><th>Pts</th>`;
  for (const c of cats) html += `<th>${c.name}</th>`;
  html += '</tr></thead><tbody>';

  for (const p of players) {
    const inj = p.injury_status !== 'ACTIVE'
      ? `<span class="injury-badge ${p.injury_status === 'DAY_TO_DAY' ? 'dtd' : 'il'}">${p.injury_status.replace(/_/g, ' ')}</span>` : '';

    html += `<tr><td><span class="player-name">${p.name}</span><span class="player-team">${p.team}</span> ${inj}</td>
      <td><span class="player-pos">${p.position}</span></td>
      <td>${p.total_points}</td>`;

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

  let displayCats = cats;
  if (state.faPosition) {
    if (['SP', 'RP', 'P'].includes(state.faPosition)) {
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
      <p>${data.stat_view === 'current' ? 'Current' : 'Projected'} stats</p>
    </div>
    <div class="controls">
      <div class="control-group">
        <label>Pos</label>
        <select class="control-select" onchange="setFAPosition(this.value)">
          <option value="">All</option>
          ${['C','1B','2B','3B','SS','OF','DH','SP','RP'].map(p =>
            `<option value="${p}" ${state.faPosition === p ? 'selected' : ''}>${p}</option>`
          ).join('')}
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
    html += '<div class="empty-state"><p>No free agents found.</p></div>';
    el.innerHTML = html;
    return;
  }

  html += `<div class="table-wrap"><table>
    <thead><tr><th>Player</th><th>Pos</th><th>Team</th><th>Own%</th><th>Pts</th>`;
  for (const c of displayCats) html += `<th>${c.name}</th>`;
  html += '</tr></thead><tbody>';

  for (const p of players) {
    const inj = p.injury_status !== 'ACTIVE'
      ? ` <span class="injury-badge ${p.injury_status === 'DAY_TO_DAY' ? 'dtd' : 'il'}">${p.injury_status.replace(/_/g, ' ')}</span>` : '';

    let displayStats = p.stats;
    if (state.faStatView === 'projected' && state.projSource !== 'ESPN' && p.projections?.[state.projSource]) {
      displayStats = p.projections[state.projSource];
    }

    html += `<tr>
      <td><span class="player-name">${p.name}</span>${inj}</td>
      <td><span class="player-pos">${p.position}</span></td>
      <td>${p.team}</td>
      <td>${p.pct_owned}%</td>
      <td>${state.faStatView === 'projected' ? p.projected_points : p.total_points}</td>`;

    for (const c of displayCats) {
      html += `<td>${fmtStat(displayStats?.[c.name], c.name)}</td>`;
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
