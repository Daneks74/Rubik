"""
Backtesting module — rebuilds historical projections and compares against
actual results to measure projection accuracy and ranking quality.

Uses the MLB Stats API to fetch actual starter game logs for past dates.
Rebuilds projections using the current pipeline with pregame-available inputs.
Scores the comparison with MAE, Brier score, and fantasy-ranking metrics.

Storage: only writes one lightweight summary row per backtest run to
backtest_runs. No per-pitcher historical storage.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.baselines import get_pitcher_baselines_for_starters
from app.http_client import resilient_get
from app.models import BacktestRun
from app.probable_starters import fetch_probable_starters_from_mlb
from app.projection_builder import BaselineRecord, ProjectionRecord, StarterRecord
from app.projection_engine import MODEL_VERSION, build_daily_projections_for_starters
from app.team_context import get_team_context_map

logger = logging.getLogger(__name__)

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

# ── Team abbreviation normalization (shared pattern) ──

_ABBREV_MAP: dict[str, str] = {
    "AZ": "Ari", "ARI": "Ari",
    "WSH": "Wsh", "CWS": "ChW", "CHC": "ChC",
    "KC": "KC", "SD": "SD", "SF": "SF", "TB": "TB",
    "STL": "StL", "NYY": "NYY", "NYM": "NYM",
    "LAD": "LAD", "LAA": "LAA",
}


def _normalize_abbrev(abbrev: str) -> str:
    upper = abbrev.strip().upper()
    if upper in _ABBREV_MAP:
        return _ABBREV_MAP[upper]
    return abbrev.strip().capitalize() if len(abbrev) <= 3 else abbrev.strip()


def _parse_mlb_ip(ip_str: str) -> float:
    """Parse MLB API innings pitched string. '6.2' = 6 + 2/3 innings."""
    if not ip_str:
        return 0.0
    try:
        parts = str(ip_str).split(".")
        whole = int(parts[0])
        thirds = int(parts[1]) if len(parts) > 1 else 0
        return whole + thirds / 3.0
    except (ValueError, IndexError):
        return 0.0


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ── Data classes ──

@dataclass
class ActualResult:
    """Actual starter game result for backtesting comparison."""
    pitcher_name: str
    pitcher_team: str
    game_date: date
    actual_ip: float
    actual_k: int
    actual_bb: int
    actual_h: int
    actual_er: int
    actual_era: float
    actual_whip: float
    actual_win: bool


@dataclass
class ScoringResult:
    """Summary metrics from comparing projections vs actuals."""
    matched_count: int
    mae_k: float
    mae_era: float
    mae_whip: float
    brier_win: float


@dataclass
class BacktestSummary:
    """Aggregate summary from a multi-date backtest run."""
    start_date: date
    end_date: date
    dates_processed: int
    dates_failed: int
    total_starters: int
    total_matched: int
    mae_k: float | None
    mae_era: float | None
    mae_whip: float | None
    brier_win: float | None
    top_streamer_hit_rate: float | None
    model_version: str
    failed_dates: list[str]


# ── A. Fetch actual starter results ──

def fetch_actual_starter_results(target_date: date) -> list[ActualResult]:
    """Fetch actual starting pitcher results for a historical date from MLB API.

    Uses the schedule endpoint with boxscore hydration to get each starting
    pitcher's actual game line. Only includes completed games.
    """
    date_str = target_date.isoformat()
    logger.info("Fetching actual starter results for %s", date_str)

    resp = resilient_get(
        f"{MLB_API_BASE}/schedule",
        params={
            "date": date_str,
            "sportId": 1,
            "hydrate": "boxscore",
        },
        label="backtest_actuals",
    )

    results: list[ActualResult] = []

    for date_entry in resp.json().get("dates", []):
        for game in date_entry.get("games", []):
            status = game.get("status", {}).get("abstractGameState", "")
            if status != "Final":
                continue

            boxscore = game.get("boxscore")
            if not boxscore:
                continue

            teams = boxscore.get("teams", {})

            # Check if there's a decisions block for the win
            decisions = game.get("decisions", {})
            winner_id = decisions.get("winner", {}).get("id") if decisions else None

            for side in ("away", "home"):
                team_data = teams.get(side, {})
                team_info = team_data.get("team", {})
                team_abbrev = team_info.get("abbreviation", "")

                pitchers = team_data.get("pitchers", [])
                players = team_data.get("players", {})

                # Find the starting pitcher — first pitcher in list, or check
                # each player for gs (games started)
                starter_id = None
                for pid in pitchers:
                    player_key = f"ID{pid}"
                    p_data = players.get(player_key, {})
                    p_stats = p_data.get("stats", {}).get("pitching", {})
                    if _safe_float(p_stats.get("gamesStarted", 0)) >= 1:
                        starter_id = pid
                        break

                if starter_id is None and pitchers:
                    starter_id = pitchers[0]

                if starter_id is None:
                    continue

                player_key = f"ID{starter_id}"
                p_data = players.get(player_key, {})
                p_person = p_data.get("person", {})
                p_stats = p_data.get("stats", {}).get("pitching", {})

                name = p_person.get("fullName", "")
                if not name:
                    continue

                ip = _parse_mlb_ip(str(p_stats.get("inningsPitched", "0")))
                k = int(_safe_float(p_stats.get("strikeOuts", 0)))
                bb = int(_safe_float(p_stats.get("baseOnBalls", 0)))
                h = int(_safe_float(p_stats.get("hits", 0)))
                er = int(_safe_float(p_stats.get("earnedRuns", 0)))

                era = round(er * 9.0 / ip, 2) if ip > 0 else 0.0
                whip = round((h + bb) / ip, 2) if ip > 0 else 0.0
                won = (starter_id == winner_id) if winner_id else False

                results.append(ActualResult(
                    pitcher_name=name,
                    pitcher_team=_normalize_abbrev(team_abbrev),
                    game_date=target_date,
                    actual_ip=round(ip, 1),
                    actual_k=k,
                    actual_bb=bb,
                    actual_h=h,
                    actual_er=er,
                    actual_era=era,
                    actual_whip=whip,
                    actual_win=won,
                ))

    logger.info("Fetched %d actual starter results for %s", len(results), date_str)
    return results


# ── B. Rebuild historical projections ──

def rebuild_historical_projections_for_date(
    session: Session,
    target_date: date,
    season_year: int | None = None,
) -> list[ProjectionRecord]:
    """Rebuild projections for a historical date using pregame-style inputs.

    Uses the same pipeline as forward-looking projections:
    starters from MLB API, baselines from season stats, team context from DB.
    Does NOT write to DB — returns projections in memory only.
    """
    if season_year is None:
        season_year = target_date.year

    logger.info("Rebuilding projections for historical date %s", target_date)

    # Fetch starters that were listed for that date
    try:
        starters = fetch_probable_starters_from_mlb(target_date)
    except Exception as e:
        logger.warning("Failed to fetch starters for %s: %s", target_date, e)
        return []

    if not starters:
        logger.warning("No starters found for %s", target_date)
        return []

    # Build baselines
    baselines, _ = get_pitcher_baselines_for_starters(starters, season_year)

    # Load team context (uses DB, auto-seeds if needed)
    team_ctx_map = get_team_context_map(session, season_year)

    # Build projections (no lineup aggregates for historical — not stored)
    projections, _ = build_daily_projections_for_starters(
        starters, baselines, team_ctx_map, None,
    )

    logger.info("Rebuilt %d projections for %s", len(projections), target_date)
    return projections


# ── C. Score projection set ──

def score_projection_set(
    projected: list[ProjectionRecord],
    actuals: list[ActualResult],
) -> ScoringResult | None:
    """Compare projected vs actual results. Returns scoring metrics.

    Matches pitchers by name. Only scores pitchers that appear in both sets.
    Returns None if no matches found.
    """
    actual_map = {a.pitcher_name: a for a in actuals}

    k_errors = []
    era_errors = []
    whip_errors = []
    brier_terms = []

    for proj in projected:
        actual = actual_map.get(proj.pitcher_name)
        if not actual:
            continue

        # MAE components
        k_errors.append(abs(proj.projected_k - actual.actual_k))

        # Only score ERA/WHIP when pitcher threw enough to have meaningful rate stats
        if actual.actual_ip >= 1.0:
            era_errors.append(abs(proj.projected_era - actual.actual_era))
            whip_errors.append(abs(proj.projected_whip - actual.actual_whip))

        # Brier score: (predicted_prob - actual_outcome)^2
        actual_win = 1.0 if actual.actual_win else 0.0
        brier_terms.append((proj.win_probability - actual_win) ** 2)

    matched = len(k_errors)
    if matched == 0:
        return None

    return ScoringResult(
        matched_count=matched,
        mae_k=round(sum(k_errors) / len(k_errors), 3),
        mae_era=round(sum(era_errors) / len(era_errors), 3) if era_errors else 0.0,
        mae_whip=round(sum(whip_errors) / len(whip_errors), 3) if whip_errors else 0.0,
        brier_win=round(sum(brier_terms) / len(brier_terms), 4) if brier_terms else 0.0,
    )


# ── D. Score streamer rank quality ──

def score_streamer_rank_quality(
    projected: list[ProjectionRecord],
    actuals: list[ActualResult],
    top_n: int = 5,
) -> float | None:
    """Measure whether the top-ranked streamers performed well.

    A "hit" = top-N streamer who in the actual game:
      - actual K >= 5 AND
      - actual ERA <= 5.00

    Returns hit rate (0.0-1.0) or None if not enough data.
    """
    # Sort projections by stream_score desc (they should already be sorted)
    ranked = sorted(projected, key=lambda p: p.stream_score, reverse=True)
    actual_map = {a.pitcher_name: a for a in actuals}

    hits = 0
    evaluated = 0

    for proj in ranked[:top_n]:
        actual = actual_map.get(proj.pitcher_name)
        if not actual:
            continue

        evaluated += 1
        if actual.actual_k >= 5 and actual.actual_era <= 5.00:
            hits += 1

    if evaluated == 0:
        return None

    return round(hits / evaluated, 3)


# ── E. Run backtest ──

def run_backtest(
    session: Session,
    start_date: date,
    end_date: date,
    season_year: int | None = None,
) -> BacktestSummary:
    """Run a backtest across a date range.

    For each date: rebuild projections, fetch actuals, score.
    Aggregates results across all dates. Writes one summary row
    to backtest_runs. Returns a summary object.
    """
    logger.info("Starting backtest: %s to %s", start_date, end_date)

    all_k_errors: list[float] = []
    all_era_errors: list[float] = []
    all_whip_errors: list[float] = []
    all_brier_terms: list[float] = []
    all_streamer_hits: list[tuple[int, int]] = []  # (hits, evaluated)

    total_starters = 0
    total_matched = 0
    dates_processed = 0
    failed_dates: list[str] = []

    current = start_date
    while current <= end_date:
        date_str = current.isoformat()
        logger.info("Backtesting date: %s", date_str)

        try:
            # Rebuild projections
            projections = rebuild_historical_projections_for_date(
                session, current, season_year,
            )
            if not projections:
                logger.warning("No projections rebuilt for %s — skipping", date_str)
                failed_dates.append(f"{date_str}:no_projections")
                current += timedelta(days=1)
                continue

            total_starters += len(projections)

            # Fetch actual results
            actuals = fetch_actual_starter_results(current)
            if not actuals:
                logger.warning("No actual results for %s — skipping", date_str)
                failed_dates.append(f"{date_str}:no_actuals")
                current += timedelta(days=1)
                continue

            # Score
            scores = score_projection_set(projections, actuals)
            if scores:
                total_matched += scores.matched_count

                # Collect raw errors for aggregation
                actual_map = {a.pitcher_name: a for a in actuals}
                for proj in projections:
                    actual = actual_map.get(proj.pitcher_name)
                    if not actual:
                        continue
                    all_k_errors.append(abs(proj.projected_k - actual.actual_k))
                    if actual.actual_ip >= 1.0:
                        all_era_errors.append(abs(proj.projected_era - actual.actual_era))
                        all_whip_errors.append(abs(proj.projected_whip - actual.actual_whip))
                    actual_win = 1.0 if actual.actual_win else 0.0
                    all_brier_terms.append((proj.win_probability - actual_win) ** 2)

            # Streamer quality
            streamer = score_streamer_rank_quality(projections, actuals)
            if streamer is not None:
                # We count hits/evaluated from top-5
                ranked = sorted(projections, key=lambda p: p.stream_score, reverse=True)
                evaluated = sum(1 for p in ranked[:5] if p.pitcher_name in actual_map)
                hits = int(streamer * evaluated) if evaluated > 0 else 0
                all_streamer_hits.append((hits, evaluated))

            dates_processed += 1

        except Exception as e:
            logger.error("Backtest failed for %s: %s", date_str, e)
            failed_dates.append(f"{date_str}:error:{e}")

        current += timedelta(days=1)

    # Aggregate
    mae_k = round(sum(all_k_errors) / len(all_k_errors), 3) if all_k_errors else None
    mae_era = round(sum(all_era_errors) / len(all_era_errors), 3) if all_era_errors else None
    mae_whip = round(sum(all_whip_errors) / len(all_whip_errors), 3) if all_whip_errors else None
    brier_win = round(sum(all_brier_terms) / len(all_brier_terms), 4) if all_brier_terms else None

    total_hits = sum(h for h, _ in all_streamer_hits)
    total_eval = sum(e for _, e in all_streamer_hits)
    top_hit_rate = round(total_hits / total_eval, 3) if total_eval > 0 else None

    summary = BacktestSummary(
        start_date=start_date,
        end_date=end_date,
        dates_processed=dates_processed,
        dates_failed=len(failed_dates),
        total_starters=total_starters,
        total_matched=total_matched,
        mae_k=mae_k,
        mae_era=mae_era,
        mae_whip=mae_whip,
        brier_win=brier_win,
        top_streamer_hit_rate=top_hit_rate,
        model_version=MODEL_VERSION,
        failed_dates=failed_dates,
    )

    # Write to DB
    notes_text = "; ".join(failed_dates[:20]) if failed_dates else None
    session.add(BacktestRun(
        start_date=start_date,
        end_date=end_date,
        model_version=MODEL_VERSION,
        run_status="success" if dates_processed > 0 else "error",
        starter_count=total_starters,
        mae_k=mae_k,
        mae_era=mae_era,
        mae_whip=mae_whip,
        brier_win=brier_win,
        top_streamer_hit_rate=top_hit_rate,
        notes=notes_text,
    ))
    session.flush()

    logger.info(
        "Backtest complete: %d dates processed, %d failed, %d starters, "
        "%d matched, MAE_K=%s, MAE_ERA=%s, Brier=%s, TopHit=%s",
        dates_processed, len(failed_dates), total_starters, total_matched,
        mae_k, mae_era, brier_win, top_hit_rate,
    )

    return summary
