"""
Lightweight Monte Carlo simulation layer — runs bounded simulations on top of
deterministic pitcher projections to estimate outcome distributions.

Uses the deterministic projection as the mean, applies practical bounded
distributions, and returns summary percentiles + refined probabilities.
No raw trial data is stored.

Blowup rule (fantasy-focused):
  ER >= 4 AND IP <= 5.0  →  counts as a blowup outing.

Model version: formula-v5-sim
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from app.projection_builder import ProjectionRecord

if TYPE_CHECKING:
    from app.projection_engine import MatchupContext

logger = logging.getLogger(__name__)

DEFAULT_SIM_COUNT = 5000
DEFAULT_SEED = 42


@dataclass
class SimulationSummary:
    """Summary outputs from a pitcher simulation run."""
    win_probability: float
    blowup_probability: float
    k_p20: float
    k_p50: float
    k_p80: float
    era_p20: float
    era_p50: float
    era_p80: float
    whip_p20: float
    whip_p50: float
    whip_p80: float
    sim_count: int


def _clamp_positive(arr: np.ndarray, floor: float = 0.0) -> np.ndarray:
    """Clamp array values to a minimum floor."""
    return np.maximum(arr, floor)


def simulate_pitcher_outcomes(
    proj: ProjectionRecord,
    ctx: MatchupContext,
    sim_count: int = DEFAULT_SIM_COUNT,
    seed: int | None = DEFAULT_SEED,
) -> SimulationSummary:
    """Run a bounded Monte Carlo simulation for a single pitcher.

    Uses the deterministic projection as the center of each distribution.
    Distributions are normal with bounded variance to prevent unrealistic
    extremes while still capturing meaningful spread.

    Args:
        proj: deterministic projection record (mean inputs)
        ctx: matchup context (for win simulation inputs)
        sim_count: number of simulation trials
        seed: random seed for reproducibility (None = random)

    Returns:
        SimulationSummary with percentiles and refined probabilities.
    """
    rng = np.random.default_rng(seed)

    # ── IP distribution ──
    # Normal centered on projected IP, SD ~0.8 IP, floored at 1.0
    ip_sd = 0.8
    sim_ip = _clamp_positive(rng.normal(proj.projected_ip, ip_sd, sim_count), 1.0)
    # Round to nearest 1/3 inning (MLB convention)
    sim_ip = np.round(sim_ip * 3) / 3

    # ── K distribution ──
    # Normal centered on projected K, SD scales with mean (~30% CV)
    k_sd = max(0.8, proj.projected_k * 0.30)
    sim_k = _clamp_positive(rng.normal(proj.projected_k, k_sd, sim_count))
    sim_k = np.round(sim_k)  # whole number Ks

    # ── BB distribution ──
    # Tighter spread (~25% CV), floored at 0
    bb_sd = max(0.5, proj.projected_bb * 0.25)
    sim_bb = _clamp_positive(rng.normal(proj.projected_bb, bb_sd, sim_count))
    sim_bb = np.round(sim_bb)

    # ── H distribution ──
    # Moderate spread (~25% CV), floored at 0
    h_sd = max(0.7, proj.projected_h * 0.25)
    sim_h = _clamp_positive(rng.normal(proj.projected_h, h_sd, sim_count))
    sim_h = np.round(sim_h)

    # ── ER distribution ──
    # Right-skewed: use normal but allow the right tail.
    # SD scales with mean (~40% CV) to capture blowup variance.
    er_sd = max(0.6, proj.projected_er * 0.40)
    sim_er = _clamp_positive(rng.normal(proj.projected_er, er_sd, sim_count))
    sim_er = np.round(sim_er)

    # ── Derived: ERA and WHIP per trial ──
    # ERA = ER * 9 / IP, WHIP = (H + BB) / IP
    safe_ip = np.maximum(sim_ip, 0.333)  # avoid division by zero
    sim_era = sim_er * 9.0 / safe_ip
    sim_whip = (sim_h + sim_bb) / safe_ip

    # Cap extreme ERA/WHIP to prevent outlier distortion on percentiles
    sim_era = np.minimum(sim_era, 27.0)
    sim_whip = np.minimum(sim_whip, 5.0)

    # ── Win probability ──
    # Each trial: pitcher "wins" if team support + pitcher quality beats threshold.
    # Base: team win rate from matchup context.
    # Adjustments per trial: lower ER and deeper IP improve win chance.
    # Bullpen quality helps hold leads.
    base_win = ctx.team_win_rate

    # Per-trial ERA quality: better than 4.20 league avg = positive edge
    trial_era = sim_er * 9.0 / safe_ip
    era_edge = np.clip((4.20 - trial_era) / 4.20 * 0.12, -0.12, 0.12)

    # Per-trial IP depth: going past 5.0 IP helps
    ip_edge = np.clip((sim_ip - 5.0) / 5.0 * 0.06, 0, 0.06)

    # Bullpen quality is constant across trials (team-level)
    bp_edge = (ctx.bullpen_factor - 1.0) * 0.35

    trial_win_prob = np.clip(base_win + era_edge + ip_edge + bp_edge, 0.10, 0.80)

    # Draw win/loss per trial
    win_draws = rng.random(sim_count)
    sim_wins = (win_draws < trial_win_prob).astype(float)
    win_probability = round(float(np.mean(sim_wins)), 3)

    # ── Blowup probability ──
    # Rule: ER >= 4 AND IP <= 5.0
    blowup_mask = (sim_er >= 4.0) & (sim_ip <= 5.0)
    blowup_probability = round(float(np.mean(blowup_mask)), 3)

    # ── Percentiles ──
    k_p20, k_p50, k_p80 = [round(float(v), 1) for v in np.percentile(sim_k, [20, 50, 80])]
    era_p20, era_p50, era_p80 = [round(float(v), 2) for v in np.percentile(sim_era, [20, 50, 80])]
    whip_p20, whip_p50, whip_p80 = [round(float(v), 2) for v in np.percentile(sim_whip, [20, 50, 80])]

    return SimulationSummary(
        win_probability=win_probability,
        blowup_probability=blowup_probability,
        k_p20=k_p20,
        k_p50=k_p50,
        k_p80=k_p80,
        era_p20=era_p20,
        era_p50=era_p50,
        era_p80=era_p80,
        whip_p20=whip_p20,
        whip_p50=whip_p50,
        whip_p80=whip_p80,
        sim_count=sim_count,
    )


def apply_simulation_to_projection(
    proj: ProjectionRecord,
    sim: SimulationSummary,
) -> None:
    """Apply simulation summary outputs onto a ProjectionRecord in-place.

    Overwrites win_probability and blowup_probability with simulation values.
    Populates percentile fields. Does NOT change the central projections
    (projected_ip, projected_k, etc.) — those stay as the formula mean.
    """
    proj.win_probability = sim.win_probability
    proj.blowup_probability = sim.blowup_probability
    proj.k_p20 = sim.k_p20
    proj.k_p50 = sim.k_p50
    proj.k_p80 = sim.k_p80
    proj.era_p20 = sim.era_p20
    proj.era_p50 = sim.era_p50
    proj.era_p80 = sim.era_p80
    proj.whip_p20 = sim.whip_p20
    proj.whip_p50 = sim.whip_p50
    proj.whip_p80 = sim.whip_p80
