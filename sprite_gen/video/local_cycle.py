# SPDX-License-Identifier: Apache-2.0
"""Select an observed local repeat after camera/subject drift analysis.

A changing cadence can contain a good cycle without one period fitting the
whole clip. Require a local lag minimum, repeat depth, motion context, and the
same two-step duration prior; an isolated matching endpoint is insufficient.
"""
from __future__ import annotations
import math
from sprite_gen._deps import np


def detect(distances, trajectory, *, min_len, max_len, gait_floor,
           periodicity_min, double_tolerance, double_search):
    n = len(distances)
    lo, hi = max(6, min_len), min(max_len, n//2)
    if hi < lo:
        raise ValueError(f"automatic motion cycle window [{lo}, {hi}] has no repeated cycle")
    candidates = []
    for length in range(max(lo, gait_floor), hi+1):
        radius = max(2, length//4)
        for start in range(n-length-radius-1):
            js = np.arange(max(0, start-radius), start+radius+1)
            profile = {}
            for lag in range(max(2, lo//2), hi+2):
                observed = js[js+lag < n]
                if len(observed) >= radius+1:
                    profile[lag] = float(distances[observed, observed+lag].mean())
            minima = [lag for lag in profile if lag-1 in profile and lag+1 in profile
                      and profile[lag] <= min(profile[lag-1], profile[lag+1]) and lag <= hi]
            if not minima:
                continue
            deepest = min(profile[lag] for lag in minima)
            period = min(lag for lag in minima if profile[lag] <= deepest*1.15+1e-4)
            guard = {'applied': False}
            if period < gait_floor:
                doubles = [lag for lag in minima if abs(lag-2*period) <= double_search and lag >= gait_floor]
                if not doubles:
                    continue
                doubled = min(doubles, key=profile.get)
                if profile[doubled] > profile[period]*(1+double_tolerance)+1e-4:
                    continue
                guard = {'applied': True, 'from': period, 'to': doubled,
                         'gait_floor': gait_floor, 'reason': 'local-two-step-repeat'}
                period = doubled
            # Allow one frame of cut quantization around the measured local lag.
            if abs(length-period) > 1 or profile.get(length, math.inf) > profile[period]*1.15+1e-4:
                continue
            baseline = float(np.mean(list(profile.values())))
            error = profile[length]
            depth = 1-error/baseline if baseline > 0 else 0
            step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
            if depth < periodicity_min or step <= 1e-10 or error/step > 2:
                continue
            # A still/rest segment matching another still is not a repeated gait.
            first_motion = float(distances[js[:-1], js[1:]].mean())
            second_motion = float(distances[js[:-1]+length, js[1:]+length].mean())
            if min(first_motion, second_motion) < .25*step:
                continue
            ratio = float(distances[start+length-1, start])/step
            x = np.arange(length)
            segment = trajectory[start:start+length]
            residual = float(np.abs(segment-np.polyval(np.polyfit(x, segment, 1), x)).mean())
            score = error/step + .3*abs(math.log(max(.01, ratio))) + residual
            candidates.append({
                'start': start, 'length': length, 'period_local': period, 'score': score,
                'ratio': ratio, 'periodicity': depth, 'context_repeat_over_step': error/step,
                'context_pair_range': [int(js[0]), int(js[-1])+1],
                'context_motion_over_step': [first_motion/step, second_motion/step],
                'drift_line_residual_analysis_px': residual, 'half_period_guard': guard,
            })
    if not candidates:
        raise ValueError("no periodic cycle found — no supported local two-step repeat after drift analysis")
    candidates.sort(key=lambda row: (row['score'], row['start'], row['length']))
    # Equivalent-quality candidates: prefer the earliest demonstrated repeat.
    cutoff = candidates[0]['score']*1.15+1e-8
    chosen = min((r for r in candidates if r['score'] <= cutoff),
                 key=lambda row: (row['start'], row['score'], row['length']))
    return {
        **chosen, 'kind': 'periodic', 'method': 'local-repeat-drift-v1',
        'period_global': None, 'periodicity_min': periodicity_min,
        'review_recommended': True, 'candidate_count': len(candidates),
        'best_score': candidates[0]['score'], 'equivalent_score_tolerance': .15,
        'candidates': candidates[:12],
    }
