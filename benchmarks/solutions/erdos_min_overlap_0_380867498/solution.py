# EVOLVE-BLOCK-START
import json
import time
from pathlib import Path

import numpy as np
from scipy.optimize import linprog


def _decode_predecessor(value):
    if isinstance(value, list):
        return [_decode_predecessor(item) for item in value]
    if not isinstance(value, dict):
        return value
    tag = value.get("__simpletes_type__", value.get("__simpleevolve_type__"))
    if tag == "ndarray":
        return np.asarray(value["data"], dtype=value.get("dtype", "float64"))
    if tag == "tuple":
        return tuple(_decode_predecessor(item) for item in value["items"])
    if "construction" in value:
        return _decode_predecessor(value["construction"])
    if "sequence" in value and len(value) == 1:
        return _decode_predecessor(value["sequence"])
    return {key: _decode_predecessor(item) for key, item in value.items()}


def _load_predecessor():
    path = Path(__file__).with_name("predecessor_construction.json")
    return _decode_predecessor(json.loads(path.read_text(encoding="utf-8")))


def _project_box_sum(v, target):
    """Euclidean projection onto 0 <= x <= 1, sum(x) == target."""
    v = np.asarray(v, dtype=np.float64)
    tau_lo = float(np.min(v) - 1.0)
    tau_hi = float(np.max(v))
    for _ in range(80):
        tau = (tau_lo + tau_hi) * 0.5
        if float(np.sum(np.clip(v - tau, 0.0, 1.0),
                        dtype=np.float64)) > target:
            tau_lo = tau
        else:
            tau_hi = tau
    return np.clip(v - tau_hi, 0.0, 1.0)


def _initial_vector(n):
    """Prefer the evaluator's frozen incumbent, with a file fallback."""
    candidate = None
    try:
        frozen = GLOBAL_BEST_CONSTRUCTION
        # Captured states normally have the form (h, score, n).
        if isinstance(frozen, (tuple, list)) and len(frozen) >= 3:
            frozen_n = int(frozen[2])
            frozen_h = np.asarray(frozen[0], dtype=np.float64).reshape(-1)
            if frozen_n == n and frozen_h.size == n:
                candidate = frozen_h
        else:
            frozen_h = np.asarray(frozen, dtype=np.float64).reshape(-1)
            if frozen_h.size == n:
                candidate = frozen_h
    except Exception:
        candidate = None

    if candidate is None:
        saved = _load_predecessor()
        candidate = np.asarray(saved[0], dtype=np.float64).reshape(-1)
    if candidate.size != n or not np.all(np.isfinite(candidate)):
        raise ValueError("No valid length-2400 predecessor construction")
    return _project_box_sum(candidate, n / 2.0)


def _full_correlation(h):
    """The evaluator's exact scaled linear correlation."""
    return np.correlate(h, 1.0 - h, mode="full") * (2.0 / h.size)


def _active_gradients(h, indices):
    """Exact gradients of selected np.correlate outputs."""
    n = h.size
    scale = 2.0 / n
    gradients = np.zeros((len(indices), n), dtype=np.float64)
    for row, output_index in enumerate(indices):
        lag = int(output_index) - (n - 1)
        if lag >= 0:
            # +(1-h[q-lag]) for q >= lag
            gradients[row, lag:] = 1.0 - h[:n - lag]
            # -h[q+lag] for q < n-lag
            gradients[row, :n - lag] -= h[lag:]
        else:
            offset = -lag
            gradients[row, :n - offset] = 1.0 - h[offset:]
            gradients[row, offset:] -= h[:n - offset]
    gradients *= scale
    return gradients


def construct_h():
    n = 2400
    target = n / 2.0
    started = time.monotonic()
    # Kept as a module global so short local validation runs can override it
    # without changing the submitted optimization path.
    deadline = started + globals().get("_OPTIMIZATION_SECONDS", 1048.0)

    h = _initial_vector(n)
    correlation = _full_correlation(h)
    current_max = float(np.max(correlation))
    best_h = h.copy()
    best_max = current_max

    # The predecessor has already reached the scale at which an LP expressed
    # directly in d is numerically ineffective.  Optimize z=d/radius instead:
    # z remains order one even when a useful d is only 1e-10 or smaller.
    radius = 1.0e-7
    max_radius = 2.0e-6
    min_radius = 5.0e-13
    consecutive_failures = 0

    while (time.monotonic() < deadline - 8.0 and
           radius >= min_radius and consecutive_failures < 12):
        order = np.argsort(correlation)[::-1]

        # The frozen incumbent has hundreds of nearly tied shifts.  Start with
        # at least 384 and augment that set by an adaptive near-maximum band.
        gap = min(1.0e-8, max(2.0e-10, 5.0e-2 * radius))
        near = np.flatnonzero(correlation >= current_max - gap)
        selected = list(map(int, order[:384]))
        selected_set = set(selected)
        for idx in near:
            idx = int(idx)
            if idx not in selected_set:
                selected.append(idx)
                selected_set.add(idx)
        if len(selected) > 768:
            selected = list(map(int, order[:768]))
            selected_set = set(selected)

        accepted = False
        accepted_scale = 1.0
        lp_direction_z = None
        lp_ok = False
        best_generated = None
        best_generated_corr = None
        best_generated_max = current_max
        best_generated_worst = None

        # Cutting planes are added from each exact candidate correlation.  In
        # particular, a newly exposed worst shift is included and the LP is
        # resolved before accepting that candidate.
        for generation in range(10):
            remaining = deadline - time.monotonic()
            if remaining <= 8.0:
                break

            chosen = np.asarray(selected, dtype=np.int64)
            gradients = _active_gradients(h, chosen)
            a_ub = np.empty((chosen.size, n + 1), dtype=np.float64)
            a_ub[:, :n] = gradients
            a_ub[:, n] = -1.0
            b_ub = (current_max - correlation[chosen]) / radius

            objective = np.zeros(n + 1, dtype=np.float64)
            objective[n] = 1.0
            a_eq = np.zeros((1, n + 1), dtype=np.float64)
            a_eq[0, :n] = 1.0
            bounds = [
                (-min(1.0, max(0.0, float(value)) / radius),
                 min(1.0, max(0.0, float(1.0 - value)) / radius))
                for value in h
            ]
            bounds.append((None, None))

            try:
                result = linprog(
                    objective,
                    A_ub=a_ub,
                    b_ub=b_ub,
                    A_eq=a_eq,
                    b_eq=np.array([0.0]),
                    bounds=bounds,
                    method="highs",
                    options={
                        "presolve": True,
                        "time_limit": max(0.5, min(45.0, remaining - 7.0)),
                        "primal_feasibility_tolerance": 1.0e-9,
                        "dual_feasibility_tolerance": 1.0e-9,
                        "ipm_optimality_tolerance": 1.0e-10,
                    },
                )
            except Exception:
                result = None

            if (result is None or not result.success or result.x is None or
                    not np.all(np.isfinite(result.x))):
                lp_ok = False
                break

            lp_ok = True
            lp_direction_z = np.asarray(result.x[:n], dtype=np.float64)
            candidate = _project_box_sum(
                h + radius * lp_direction_z, target)
            candidate_corr = _full_correlation(candidate)
            candidate_max = float(np.max(candidate_corr))
            worst = int(np.argmax(candidate_corr))

            if candidate_max < best_generated_max:
                best_generated = candidate
                best_generated_corr = candidate_corr
                best_generated_max = candidate_max
                best_generated_worst = worst

            if worst not in selected_set:
                # Add the exact offender first, followed by a few nearby
                # exposed constraints, then resolve the normalized LP.
                additions = [worst]
                for idx in np.argsort(candidate_corr)[::-1]:
                    idx = int(idx)
                    if idx not in selected_set and idx not in additions:
                        additions.append(idx)
                    if len(additions) >= 24:
                        break
                for idx in additions:
                    selected.append(idx)
                    selected_set.add(idx)
                continue

            # Only the complete nonlinear objective can authorize a step.
            if candidate_max < current_max - 5.0e-15:
                h = candidate
                correlation = candidate_corr
                current_max = candidate_max
                accepted = True
                break

            # The exact worst shift was modeled, so a failure here is due to
            # curvature/scale rather than a missing cutting plane.
            break

        # If constraint generation found an improving exact candidate on its
        # final pass, its worst shift has already been inserted.  Retain that
        # verified gain rather than discarding it solely due to the pass cap.
        if (not accepted and best_generated is not None and
                best_generated_max < current_max - 5.0e-15 and
                best_generated_worst in selected_set):
            h = best_generated
            correlation = best_generated_corr
            current_max = best_generated_max
            accepted = True

        # Backtrack along the normalized LP direction after nonlinear failure.
        # Every trial is projected only for floating residue and is authorized
        # by a complete np.correlate evaluation.
        if not accepted and lp_ok and lp_direction_z is not None:
            best_line_h = None
            best_line_corr = None
            best_line_max = current_max
            scale = 0.5
            for _ in range(16):
                trial = _project_box_sum(
                    h + scale * radius * lp_direction_z, target)
                trial_corr = _full_correlation(trial)
                trial_max = float(np.max(trial_corr))
                trial_worst = int(np.argmax(trial_corr))
                if (trial_max < best_line_max - 5.0e-15 and
                        trial_worst in selected_set):
                    best_line_h = trial
                    best_line_corr = trial_corr
                    best_line_max = trial_max
                    accepted_scale = scale
                scale *= 0.5
            if best_line_h is not None:
                h = best_line_h
                correlation = best_line_corr
                current_max = best_line_max
                accepted = True

        if accepted:
            if current_max < best_max:
                best_h = h.copy()
                best_max = current_max
            if accepted_scale >= 0.5:
                radius = min(max_radius, radius * 1.18)
            else:
                radius *= max(0.25, min(0.8, 1.5 * accepted_scale))
            consecutive_failures = 0
        else:
            radius *= 0.25
            consecutive_failures += 1

    # Never return a failed LP candidate or merely the last iterate.
    return best_h, n
# EVOLVE-BLOCK-END


def run_code():
    """Run the Erdős minimum overlap optimization.
    
    Returns:
        tuple: (h_values, c5_bound, n_points)
            h_values: np.ndarray, shape (n_points,), discretized step function h
            c5_bound: float, max overlap computed from this h_values
            n_points: int, number of bins used to discretize [0, 2]
    """
    h_values, n_points = construct_h()

    n = int(n_points)
    target_sum = n / 2.0

    # Keep post-processing fixed and robust:
    # - cast to float64 (avoid float32 bound spillover)
    # - project to the feasible set {0<=h<=1, sum(h)=n/2}
    h_values = np.asarray(h_values, dtype=np.float64).reshape(-1)
    assert isinstance(n_points, int), TypeError(f"n_points must be an integer, got {type(n_points)}")
    if h_values.shape[0] != n:
        raise ValueError(f"Expected h_values shape ({n},), got {h_values.shape}")

    def _project_box_sum(v: np.ndarray, s: float, lo: float = 0.0, hi: float = 1.0) -> np.ndarray:
        if not np.all(np.isfinite(v)):
            raise ValueError("h_values contain NaN or inf values")
        # Bisection on tau for x = clip(v - tau, lo, hi) such that sum(x)=s.
        tau_lo = float(np.min(v) - hi)
        tau_hi = float(np.max(v) - lo)
        for _ in range(80):
            tau = (tau_lo + tau_hi) / 2.0
            x = np.clip(v - tau, lo, hi)
            if float(np.sum(x, dtype=np.float64)) > s:
                tau_lo = tau
            else:
                tau_hi = tau
        return np.clip(v - tau_hi, lo, hi)

    h_values = _project_box_sum(h_values, target_sum)
    
    dx = 2.0 / n_points
    j_values = 1.0 - h_values
    correlation = np.correlate(h_values, j_values, mode="full") * dx
    c5_bound = np.max(correlation)
    
    return h_values, c5_bound, n_points
