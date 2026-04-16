"""
Calibration utilities — evaluates projection quality through probability
calibration and percentile coverage analysis.

Lightweight and summary-only. No storage — returns dicts for API responses.
"""
from __future__ import annotations

import logging

from app.backtesting import ActualResult
from app.projection_builder import ProjectionRecord

logger = logging.getLogger(__name__)


def evaluate_win_probability_calibration(
    projected: list[ProjectionRecord],
    actuals: list[ActualResult],
) -> dict:
    """Evaluate win probability calibration across buckets.

    Groups projected win probabilities into 5 buckets (0-20%, 20-40%, etc.),
    compares predicted probability vs actual win rate per bucket.

    Returns a dict with bucket summaries and overall calibration error.
    """
    actual_map = {a.pitcher_name: a for a in actuals}

    # Buckets: [0, 0.2), [0.2, 0.4), [0.4, 0.6), [0.6, 0.8), [0.8, 1.0]
    bucket_ranges = [
        (0.0, 0.2, "0-20%"),
        (0.2, 0.4, "20-40%"),
        (0.4, 0.6, "40-60%"),
        (0.6, 0.8, "60-80%"),
        (0.8, 1.01, "80-100%"),
    ]

    buckets: list[dict] = []
    total_cal_error = 0.0
    total_count = 0

    for lo, hi, label in bucket_ranges:
        bucket_probs = []
        bucket_wins = []

        for proj in projected:
            actual = actual_map.get(proj.pitcher_name)
            if not actual:
                continue
            wp = proj.win_probability
            if lo <= wp < hi:
                bucket_probs.append(wp)
                bucket_wins.append(1.0 if actual.actual_win else 0.0)

        n = len(bucket_probs)
        if n > 0:
            avg_predicted = round(sum(bucket_probs) / n, 3)
            avg_actual = round(sum(bucket_wins) / n, 3)
            cal_error = round(abs(avg_predicted - avg_actual), 3)
        else:
            avg_predicted = None
            avg_actual = None
            cal_error = None

        buckets.append({
            "bucket": label,
            "count": n,
            "avg_predicted_win_prob": avg_predicted,
            "actual_win_rate": avg_actual,
            "calibration_error": cal_error,
        })

        if n > 0 and cal_error is not None:
            total_cal_error += cal_error * n
            total_count += n

    overall_error = round(total_cal_error / total_count, 3) if total_count > 0 else None

    return {
        "buckets": buckets,
        "overall_calibration_error": overall_error,
        "total_pitchers_evaluated": total_count,
    }


def evaluate_percentile_coverage(
    projected: list[ProjectionRecord],
    actuals: list[ActualResult],
) -> dict:
    """Check whether actual outcomes fall within projected percentile bands.

    For K, ERA, and WHIP, checks:
      - What fraction of actuals fall below the p20 projection (should be ~20%)
      - What fraction fall below p50 (should be ~50%)
      - What fraction fall below p80 (should be ~80%)

    Only evaluates pitchers that have both percentile projections and actuals.
    Returns a summary dict.
    """
    actual_map = {a.pitcher_name: a for a in actuals}

    # Counters for K
    k_below_20, k_below_50, k_below_80, k_total = 0, 0, 0, 0
    # Counters for ERA
    era_below_20, era_below_50, era_below_80, era_total = 0, 0, 0, 0
    # Counters for WHIP
    whip_below_20, whip_below_50, whip_below_80, whip_total = 0, 0, 0, 0

    for proj in projected:
        actual = actual_map.get(proj.pitcher_name)
        if not actual:
            continue

        # K coverage
        if proj.k_p20 is not None and proj.k_p50 is not None and proj.k_p80 is not None:
            k_total += 1
            if actual.actual_k <= proj.k_p20:
                k_below_20 += 1
            if actual.actual_k <= proj.k_p50:
                k_below_50 += 1
            if actual.actual_k <= proj.k_p80:
                k_below_80 += 1

        # ERA coverage (only if enough IP for meaningful ERA)
        if actual.actual_ip >= 1.0 and proj.era_p20 is not None:
            era_total += 1
            # Note: for ERA, lower is better, so p20 is the LOW end (good outcome)
            if actual.actual_era <= proj.era_p20:
                era_below_20 += 1
            if actual.actual_era <= proj.era_p50:
                era_below_50 += 1
            if actual.actual_era <= proj.era_p80:
                era_below_80 += 1

        # WHIP coverage
        if actual.actual_ip >= 1.0 and proj.whip_p20 is not None:
            whip_total += 1
            if actual.actual_whip <= proj.whip_p20:
                whip_below_20 += 1
            if actual.actual_whip <= proj.whip_p50:
                whip_below_50 += 1
            if actual.actual_whip <= proj.whip_p80:
                whip_below_80 += 1

    def _pct(n: int, total: int) -> float | None:
        return round(n / total, 3) if total > 0 else None

    return {
        "k": {
            "count": k_total,
            "below_p20": _pct(k_below_20, k_total),
            "below_p50": _pct(k_below_50, k_total),
            "below_p80": _pct(k_below_80, k_total),
            "ideal": {"below_p20": 0.20, "below_p50": 0.50, "below_p80": 0.80},
        },
        "era": {
            "count": era_total,
            "below_p20": _pct(era_below_20, era_total),
            "below_p50": _pct(era_below_50, era_total),
            "below_p80": _pct(era_below_80, era_total),
            "ideal": {"below_p20": 0.20, "below_p50": 0.50, "below_p80": 0.80},
        },
        "whip": {
            "count": whip_total,
            "below_p20": _pct(whip_below_20, whip_total),
            "below_p50": _pct(whip_below_50, whip_total),
            "below_p80": _pct(whip_below_80, whip_total),
            "ideal": {"below_p20": 0.20, "below_p50": 0.50, "below_p80": 0.80},
        },
    }
