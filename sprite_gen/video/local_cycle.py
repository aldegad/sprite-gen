# SPDX-License-Identifier: Apache-2.0
"""Select an observed local repeat after camera/subject drift analysis.

A changing cadence can contain a good cycle without one period fitting the
whole clip. Require a local lag minimum, repeat depth, motion context, and the
same two-step duration prior; an isolated matching endpoint is insufficient.
The local period taken is screened for two or three cycles and the finding
recorded (`fundamental`, sprite_gen/video/period.py), never cut shorter; the
legs are read off `signals` (`legs.strike_signals` of the analysed frames).
The cut taken is screened the other way too, for one step of a cycle twice as
long that the window could not hold (`step_screen`), never cut longer on it:
only a count of the steps (`video-loop --steps`) cuts again.
"""
from __future__ import annotations
import math
from sprite_gen._deps import np
from sprite_gen.video import period as period_mod

REFUSED_SHOWN = 12  # refused windows a failed search reports, as many as a found cycle's candidates


def _around(start, length):
    """The frames whose repeats are compared for a cut at `start`: a quarter of the cut either side."""
    radius = max(2, length//4)
    return radius, np.arange(max(0, start-radius), start+radius+1)


def _profile(distances, js, radius, lo, hi):
    n = len(distances)
    profile = {}
    for lag in range(max(2, lo//2), hi+2):
        observed = js[js+lag < n]
        if len(observed) >= radius+1:
            profile[lag] = float(distances[observed, observed+lag].mean())
    return profile


def _measure(distances, trajectory, start, length, profile):
    """The parts of a window's score, for a window the search refused (`refused`): the same sums as
    a candidate's, so a refused window and a candidate read alike."""
    step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
    if step <= 1e-10 or length not in profile:
        return None
    ratio = float(distances[start+length-1, start])/step
    x = np.arange(length)
    segment = trajectory[start:start+length]
    residual = float(np.abs(segment-np.polyval(np.polyfit(x, segment, 1), x)).mean())
    error = profile[length]
    return {'start': start, 'length': length, 'score': error/step + .3*abs(math.log(max(.01, ratio))) + residual,
            'ratio': ratio, 'context_repeat_over_step': error/step, 'drift_line_residual_analysis_px': residual}


def _closure(distances, start, length):
    """How far a cut's wrap is from the clip's own way on there: its last frame against the clip's frame
    before its first, and the frame before its last against the one before that (the speed into the
    wrap), over the cut's mean playback step. None for a cut with fewer than two clip frames before it."""
    step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
    if start < 2 or step <= 1e-10:
        return None
    return float(distances[start+length-1, start-1] + distances[start+length-2, start-2])/2/step


def _read(measured):
    """A cut's pop as `cycle.seam_pop` records it: the jump, or why its top band could not be read."""
    return {'skipped': measured['skipped']} if 'skipped' in measured else {'pop': measured['pop']}


def _mark(row, measured):
    """A window row's pop: `seam_pop`, or None with `seam_pop_skipped` (why) when its top band could
    not be read — an unread window is neither taken as closing nor as popping."""
    row['seam_pop'] = measured.get('pop')
    if 'skipped' in measured:
        row['seam_pop_skipped'] = measured['skipped']


def _held(measured):
    """A window's held side as `cycle.seam_pop.held_edge` records it: its seam, or why it could not be read."""
    return {'skipped': measured['skipped']} if 'skipped' in measured else {'seam': measured['seam'], 'closes': measured['closes']}


def _held_rank(candidates, calm, chosen, wrap_pop, held_edge, length_cap=None, double=True):
    """Below the top band (`held_edge`, repair.held_edge on the side the popping first choice holds its
    part): among the calm candidates, those whose held side closes, the same score tolerance, then the
    smallest held seam. When no candidate closes it, windows twice a candidate's length: the candidate
    was one step of a walk whose arm, and the part it holds, swing once a cycle — two steps — and close
    only there (`steps` 2: the window is one cycle, `step_lengths` the candidates it is two of — each one
    step). Such a window is two lengths of a repeat the search confirmed, not itself seen repeating; it
    is taken when its held side closes and its top does not pop, the smallest held seam first. A window longer than `length_cap`
    (an explicit --max-len) is not one, and none is read without `double` (the steps were counted,
    `video-loop --steps`). Returns (picked or None, record)."""
    side = held_edge.side(chosen['start'], chosen['length'])
    if 'skipped' in side:
        return None, {'method': 'held-side-edge-v1', 'skipped': side['skipped']}
    side = side['side']
    record = {'method': 'held-side-edge-v1', 'side': side, 'reference': held_edge.reference}
    closing = []
    unread = 0
    for row in calm:
        measured = held_edge.seam(row['start'], row['length'], side)
        row['held_edge'] = measured.get('seam')
        if 'skipped' in measured:
            row['held_edge_skipped'] = measured['skipped']
            unread += 1
        elif measured['closes']:
            closing.append(row)
    record.update(measured=len(calm)-unread, unread=unread)
    if closing:
        cutoff = min(row['score'] for row in closing)*1.15+1e-8
        picked = min((row for row in closing if row['score'] <= cutoff),
                     key=lambda row: (row['held_edge'], row['seam_pop'], row['score'], row['start'], row['length']))
        record.update(doubled=False, chosen={'start': picked['start'], 'length': picked['length'], 'seam': picked['held_edge'], 'closes': True})
        return picked, record
    if not double:
        record['two_step'] = {'read': False, 'why': 'the steps were counted (--steps): no window twice as long is read'}
        return None, record
    lengths = sorted({2*row['length']+d for row in candidates for d in (-1, 0, 1)})
    if length_cap is not None:
        over = [length for length in lengths if length > length_cap]
        lengths = [length for length in lengths if length <= length_cap]
        if over:
            record['two_step_capped'] = {'max_len': length_cap, 'lengths_over': [over[0], over[-1]]}
        if not lengths:
            return None, record
    windows = []
    counts = {'measured': 0, 'unread': 0, 'top_pops': 0}
    for length in lengths:
        for start in range(held_edge.frames-length+1):
            measured = held_edge.seam(start, length, side)
            if 'skipped' in measured:
                counts['unread'] += 1
                continue
            counts['measured'] += 1
            if not measured['closes']:
                continue
            top = wrap_pop(start, length)
            if 'skipped' in top or top['pops']:
                counts['top_pops'] += 1  # a top that pops, or one that could not be read, is not taken
                continue
            windows.append({'start': start, 'length': length, 'period_local': length,
                            'steps': {'count': 2, 'by': 'held-edge',
                                      'step_lengths': sorted({row['length'] for row in candidates if abs(2*row['length']-length) <= 1})},
                            'held_edge': measured['seam'], 'seam_pop': top['pop'],
                            'repeat': 'two lengths of a confirmed repeat (two steps, one cycle), not itself seen repeating'})
    record['two_step'] = {'read': True, 'lengths': [lengths[0], lengths[-1]], **counts, 'closing': len(windows)}
    if windows:
        picked = min(windows, key=lambda row: (row['held_edge'], row['seam_pop'], row['start'], row['length']))
        record.update(doubled=True, chosen={'start': picked['start'], 'length': picked['length'], 'seam': picked['held_edge'], 'closes': True})
        return picked, record
    return None, record


def _shape_rank(candidates, chosen, wrap_pop, held_edge=None, length_cap=None, double=True):
    """Choose again when the chosen cut's top pops at the wrap (`wrap_pop`, repair.seam_pop on the
    analysed frames): among the candidates whose top was read and does not pop, the same score
    tolerance, then the smallest pop. Nothing is measured past the first choice when it does not pop,
    or when its top could not be read (kept, `skipped`), so a cut without a thin part swinging on its
    own beat is chosen exactly as before. A candidate whose top could not be read is not chosen.

    With `held_edge` (loop.HeldEdge), a popping first choice has its held part read below the top too
    (`_held_rank`): a cut whose held side closes is taken first, one or two lengths long. Where none
    closes, the choice is the top band's, and `held_edge.chosen` records that it does not close (the
    jolt warns, repair.held_edge_verdict). A window two lengths long is never longer than `length_cap`,
    nor read without `double`."""
    first = wrap_pop(chosen['start'], chosen['length'])
    record = {'method': 'top-band-wrap-v1', 'reference': first['reference'],
              'first_choice': {'start': chosen['start'], 'length': chosen['length'], **_read(first)},
              'applied': False}
    if 'skipped' in first or not first['pops']:
        return chosen, record
    calm = []
    unread = 0
    for row in candidates:
        measured = wrap_pop(row['start'], row['length'])
        _mark(row, measured)
        if 'skipped' in measured:
            unread += 1
        elif not measured['pops']:
            calm.append(row)
    record.update(measured=len(candidates)-unread, unread=unread)
    held = None
    if held_edge is not None:
        picked, held = _held_rank(candidates, calm, chosen, wrap_pop, held_edge, length_cap, double)
        record['held_edge'] = held
        if picked is not None:
            record.update(applied=True, chosen={'start': picked['start'], 'length': picked['length'], 'pop': picked['seam_pop'],
                                                **({'steps': 2} if 'steps' in picked else {})})
            return picked, record
    if not calm:
        record['why'] = 'every candidate whose top was read pops at the wrap; the first choice is kept'
        picked = chosen
    else:
        cutoff = min(row['score'] for row in calm)*1.15+1e-8
        picked = min((row for row in calm if row['score'] <= cutoff),
                     key=lambda row: (row['seam_pop'], row['score'], row['start'], row['length']))
        record.update(applied=True, chosen={'start': picked['start'], 'length': picked['length'], 'pop': picked['seam_pop']})
    if held is not None and 'side' in held:
        measured = held_edge.seam(picked['start'], picked['length'], held['side'])
        held['chosen'] = {'start': picked['start'], 'length': picked['length'], **_held(measured)}
    return picked, record


def counted(distances, trajectory, *, min_len, max_len, periodicity_min, signals=None):
    """A cut of a length someone counted (`video-loop --steps`) where the clip is too short to see a
    window that long repeat (`detect` found none): the counted length stands for the repeat. Every
    window [min_len, max_len] long with two clip frames before it is read, and the one whose wrap is
    nearest the clip's own way on there is taken (`_closure`, the earliest on a tie); whether it closes
    is the seam gate's word. Raises ValueError where no window has room."""
    n = len(distances)
    rows = []
    for length in range(min_len, max_len+1):
        for start in range(2, n-length+1):
            measured = _closure(distances, start, length)
            if measured is None:
                continue
            step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
            x = np.arange(length)
            segment = trajectory[start:start+length]
            rows.append({'start': start, 'length': length, 'period_local': length, 'closure': measured,
                         'ratio': float(distances[start+length-1, start])/step,
                         'drift_line_residual_analysis_px': float(np.abs(segment-np.polyval(np.polyfit(x, segment, 1), x)).mean()),
                         'half_period_guard': {'applied': False}})
    if not rows:
        raise ValueError(f"no window {min_len}-{max_len} frames long has two clip frames before it in {n}")
    rows.sort(key=lambda row: (row['closure'], row['start'], row['length']))
    chosen = rows[0]
    return {**chosen, 'kind': 'periodic', 'method': 'counted-window-v1',
            'repeat': 'not seen in the clip: the counted length stands for it', 'choice': 'closure',
            'period_global': None, 'periodicity_min': periodicity_min, 'review_recommended': True,
            'candidate_count': len(rows), 'candidates': rows[:REFUSED_SHOWN],
            'step_screen': period_mod.step_screen(distances, start=chosen['start'], length=chosen['length'],
                                                  periodicity_min=periodicity_min, signals=signals)}


def detect(distances, trajectory, *, min_len, max_len, gait_floor,
           periodicity_min, double_tolerance, double_search, max_fraction=.5, signals=None, wrap_pop=None,
           held_edge=None, length_cap=None, double=True, closure=False):
    """`wrap_pop(start, length)`, when given, is the top band's jump at that cut's wrap
    (repair.seam_pop): a chosen cut that pops is chosen again (`_shape_rank`), and a search that finds
    nothing raises with the windows it refused, popping ones last (`ValueError.diagnostics`).
    `held_edge` (loop.HeldEdge) reads the held side below the top of a cut chosen again; the cut it
    takes may be two lengths long (`steps` in the result: two steps, one cycle), but never longer than
    `length_cap`, nor with `double` false.

    `length_cap` is the caller's own ceiling on the cut — an explicit --max-len, or the window a count
    of steps sets (`video-loop --steps`) — which `max_len` alone does not mark (the state's default
    window is one too). It is the window's top as given: the share of the clip (`max_fraction`) bounds
    only a window the caller did not set. Each window still needs its repeat seen in the clip.

    Among the equivalent candidates the earliest is taken; with `closure` (a length the caller counted,
    `video-loop --steps`), the one whose wrap is nearest the clip's own way on there (`_closure`). The
    cut taken is screened for one step of a cycle twice as long (`step_screen`, period.step_screen,
    over the whole clip); nothing is cut on it."""
    n = len(distances)
    # A cycle has to be seen repeating: half the clip by default, more for the gait fallback, and as
    # long as the caller's own ceiling allows.
    lo = max(6, min_len)
    hi = min(max_len, length_cap) if length_cap is not None else min(max_len, int(n*max_fraction))
    if hi < lo:
        raise ValueError(f"automatic motion cycle window [{lo}, {hi}] has no repeated cycle")
    candidates = []
    refused = []
    for length in range(max(lo, gait_floor), hi+1):
        radius = max(2, length//4)
        for start in range(n-length-radius-1):
            _, js = _around(start, length)
            profile = _profile(distances, js, radius, lo, hi)

            def refuse(why, start=start, length=length, profile=profile):
                row = _measure(distances, trajectory, start, length, profile)
                if row is not None:
                    refused.append({**row, 'refused': why})
            minima = [lag for lag in profile if lag-1 in profile and lag+1 in profile
                      and profile[lag] <= min(profile[lag-1], profile[lag+1]) and lag <= hi]
            if not minima:
                refuse('no local repeat minimum')
                continue
            deepest = min(profile[lag] for lag in minima)
            period = min(lag for lag in minima if profile[lag] <= deepest*1.15+1e-4)
            guard = {'applied': False}
            if period < gait_floor:
                doubles = [lag for lag in minima if abs(lag-2*period) <= double_search and lag >= gait_floor]
                if not doubles:
                    refuse('one step: no repeat near twice it')
                    continue
                doubled = min(doubles, key=profile.get)
                if profile[doubled] > profile[period]*(1+double_tolerance)+1e-4:
                    refuse('one step: twice it repeats worse')
                    continue
                guard = {'applied': True, 'from': period, 'to': doubled,
                         'gait_floor': gait_floor, 'reason': 'local-two-step-repeat'}
                period = doubled
            # Allow one frame of cut quantization around the measured local lag.
            if abs(length-period) > 1 or profile.get(length, math.inf) > profile[period]*1.15+1e-4:
                refuse(f'the local repeat is {period} frames')
                continue
            baseline = float(np.mean(list(profile.values())))
            error = profile[length]
            depth = 1-error/baseline if baseline > 0 else 0
            step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
            if depth < periodicity_min or step <= 1e-10 or error/step > 2:
                refuse('repeat too shallow' if depth < periodicity_min else 'repeat error over two steps')
                continue
            # A still/rest segment matching another still is not a repeated gait.
            first_motion = float(distances[js[:-1], js[1:]].mean())
            second_motion = float(distances[js[:-1]+length, js[1:]+length].mean())
            if min(first_motion, second_motion) < .25*step:
                refuse('a still segment')
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
        exc = ValueError("no periodic cycle found — no supported local two-step repeat after drift analysis")
        # The windows the search measured and refused, best first: a caller that must deliver anyway
        # (a forced cut) takes one the engine measured instead of the whole clip.
        refused.sort(key=lambda row: (row['score'], row['start'], row['length']))
        shown = refused[:REFUSED_SHOWN]
        if wrap_pop is not None:
            # Those whose top closes first, then those whose top could not be read, then those that pop.
            rank = {}
            for row in shown:
                measured = wrap_pop(row['start'], row['length'])
                _mark(row, measured)
                rank[row['start'], row['length']] = 1 if 'skipped' in measured else 2 if measured['pops'] else 0
            shown.sort(key=lambda row: (rank[row['start'], row['length']], row['score'], row['start'], row['length']))
        exc.diagnostics = {'kind': 'periodic', 'method': 'local-repeat-drift-v1', 'status': 'refused',
                           'window': [lo, hi], 'refused_count': len(refused),
                           'order': 'score' + (', windows whose top could not be read after those whose top closes,'
                                               ' windows whose top pops at the wrap last' if wrap_pop is not None else ''),
                           'candidates': shown}
        raise exc
    candidates.sort(key=lambda row: (row['score'], row['start'], row['length']))
    # Equivalent-quality candidates: prefer the earliest demonstrated repeat.
    cutoff = candidates[0]['score']*1.15+1e-8
    equivalent = [r for r in candidates if r['score'] <= cutoff]
    if closure:
        # A length counted, not found: its window was never the search's, and the earliest of the
        # equivalent cuts need not be the one that closes — the one whose wrap the clip itself plays is.
        for row in equivalent:
            row['closure'] = _closure(distances, row['start'], row['length'])
        chosen = min(equivalent, key=lambda row: (row['closure'] is None, row['closure'] or 0.0, row['start'], row['score'], row['length']))
    else:
        chosen = min(equivalent, key=lambda row: (row['start'], row['score'], row['length']))
    shape = None
    if wrap_pop is not None:
        chosen, shape = _shape_rank(candidates, chosen, wrap_pop, held_edge, length_cap, double)
    if 'ratio' not in chosen:
        # A window two lengths long (`_held_rank`) was not a candidate: its wrap over its step, as a candidate's.
        s, L = chosen['start'], chosen['length']
        chosen['ratio'] = float(distances[s+L-1, s])/float(np.diag(distances[s:s+L, s:s+L], 1).mean())
    radius, js = _around(chosen['start'], chosen['length'])
    profile = _profile(distances, js, radius, lo, hi)
    fundamental = period_mod.screen(
        chosen['period_local'], D=distances, prof=profile, minima=period_mod.local_minima(profile),
        mean=float(np.mean(list(profile.values()))), lowest=max(lo, gait_floor), periodicity_min=periodicity_min,
        signals=signals, gait=True, js=js, floor_why='the gait floor' if gait_floor >= lo else 'the window')
    steps = period_mod.step_screen(distances, start=chosen['start'], length=chosen['length'],
                                   periodicity_min=periodicity_min, signals=signals)
    return {
        **chosen, 'fundamental': fundamental, 'kind': 'periodic', 'method': 'local-repeat-drift-v1',
        'period_global': None, 'periodicity_min': periodicity_min,
        'review_recommended': True, 'candidate_count': len(candidates),
        'best_score': candidates[0]['score'], 'equivalent_score_tolerance': .15,
        'candidates': candidates[:12],
        **({'choice': 'closure'} if closure else {}),
        **({'seam_pop': shape} if shape is not None else {}),
        'step_screen': steps,
    }
