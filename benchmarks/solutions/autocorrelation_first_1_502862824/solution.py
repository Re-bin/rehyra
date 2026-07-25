# EVOLVE-BLOCK-START
import json
import time
from pathlib import Path

import numpy as np


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


def _valid_start(value, n=30000):
    try:
        x = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if x.ndim != 1 or x.size != n:
        return None
    if not np.all(np.isfinite(x)) or np.any(x < 0.0):
        return None
    total = float(np.sum(x, dtype=np.float64))
    if not np.isfinite(total) or total <= 0.0:
        return None
    return x / total


def _simplex_projection(v):
    """Euclidean projection onto {x >= 0, sum(x) = 1}."""
    u = np.sort(v)[::-1]
    cssv = np.cumsum(u, dtype=np.float64) - 1.0
    scale = np.arange(1, v.size + 1, dtype=np.float64)
    feasible = np.flatnonzero(u - cssv / scale > 0.0)
    if feasible.size == 0:  # Defensive only; this cannot occur for finite v.
        return np.full(v.size, 1.0 / v.size, dtype=np.float64)
    rho = int(feasible[-1])
    return np.maximum(v - cssv[rho] / (rho + 1.0), 0.0)


def _fft_state(x, fft_len):
    spectrum = np.fft.rfft(x, fft_len)
    convolution = np.fft.irfft(spectrum * spectrum, fft_len)[: 2 * x.size - 1]
    return spectrum, convolution


def _exact_value(x, convolution):
    """Scale-invariant objective component max(x*x)/(sum(x)**2)."""
    total = float(np.sum(x, dtype=np.float64))
    if not np.isfinite(total) or total <= 0.0:
        return np.inf
    peak = float(np.max(convolution))
    return peak / (total * total) if np.isfinite(peak) else np.inf


def _working_lags(convolution, base_count, relative_gap):
    """Top lags union every lag in the specified relative peak band."""
    count = min(int(base_count), convolution.size)
    if count == convolution.size:
        chosen = np.arange(convolution.size)
    else:
        chosen = np.argpartition(convolution, -count)[-count:]
    peak = float(np.max(convolution))
    band = np.flatnonzero(convolution >= peak * (1.0 - float(relative_gap)))
    active = np.union1d(chosen, band)
    # Height order is deterministic and makes the initial uniform bundle easy
    # to inspect without changing the mathematical working set.
    return active[np.argsort(-convolution[active], kind="stable")]


def _bundle_gradient(x_spectrum, active, weights, convolution_size, fft_len):
    """Return sum_k weights[k] grad(c_active[k]) by FFT correlation."""
    lag_weights = np.zeros(convolution_size, dtype=np.float64)
    lag_weights[active] = weights
    n = (convolution_size + 1) // 2
    return 2.0 * np.fft.irfft(
        np.fft.rfft(lag_weights, fft_len) * np.conj(x_spectrum), fft_len
    )[:n]


def _single_lag_gradient(x, lag):
    """Exact gradient g_lag[j] = 2*x[lag-j], without a dense bundle."""
    n = x.size
    gradient = np.zeros(n, dtype=np.float64)
    first = max(0, int(lag) - (n - 1))
    last = min(n - 1, int(lag))
    if first <= last:
        coordinates = np.arange(first, last + 1)
        gradient[first : last + 1] = 2.0 * x[int(lag) - coordinates]
    return gradient


def _project_tangent(vector, positive):
    """Project onto sum(d)=0 and d[j]>=0 where the incumbent is zero."""
    vector = np.asarray(vector, dtype=np.float64)
    zero = ~positive
    positive_count = int(np.count_nonzero(positive))
    if positive_count == 0:
        return np.zeros_like(vector)

    # If m constrained coordinates are released, the multiplier is the mean
    # over the positive coordinates and those m entries.  Sorting only the
    # zero face (normally a small part of the construction) finds the exact
    # self-consistent released set.
    zero_values = np.sort(vector[zero])[::-1]
    prefix = np.empty(zero_values.size + 1, dtype=np.float64)
    prefix[0] = 0.0
    prefix[1:] = np.cumsum(zero_values, dtype=np.float64)
    released = np.arange(zero_values.size + 1, dtype=np.float64)
    multipliers = (
        float(np.sum(vector[positive], dtype=np.float64)) + prefix
    ) / (positive_count + released)
    left_ok = np.ones(multipliers.size, dtype=bool)
    right_ok = np.ones(multipliers.size, dtype=bool)
    if zero_values.size:
        left_ok[1:] = zero_values >= multipliers[1:]
        right_ok[:-1] = zero_values <= multipliers[:-1]
    valid = np.flatnonzero(left_ok & right_ok)
    multiplier = float(multipliers[int(valid[0])]) if valid.size else float(
        multipliers[-1]
    )

    direction = vector - multiplier
    direction[zero] = np.maximum(direction[zero], 0.0)
    # Remove the last few ulps of the equality residual on unconstrained
    # coordinates; this cannot violate the tangent-cone signs.
    direction[positive] -= float(
        np.sum(direction, dtype=np.float64)
    ) / positive_count
    return direction


def _directional_derivatives(x_spectrum, direction, convolution_size, fft_len):
    """All grad(c_k).direction values, using one zero-padded FFT."""
    return 2.0 * np.fft.irfft(
        x_spectrum * np.fft.rfft(direction, fft_len), fft_len
    )[:convolution_size]


def _minimum_projected_bundle(
    x,
    x_spectrum,
    convolution,
    active,
    fft_len,
    initial_weights,
    deadline,
):
    """Converge a lag-indexed gradient bundle by pairwise Frank-Wolfe.

    The returned dictionary makes the sparse convex combination reusable when
    a newly exposed peak changes the working set.  ``gap`` is the final
    pairwise Frank-Wolfe gap, not merely an iteration-count proxy.
    """
    count = active.size
    weights = np.zeros(count, dtype=np.float64)
    if initial_weights:
        for index, lag in enumerate(active):
            weight = initial_weights.get(int(lag), 0.0)
            if np.isfinite(weight) and weight > 0.0:
                weights[index] = float(weight)
    weight_sum = float(np.sum(weights, dtype=np.float64))
    if not np.isfinite(weight_sum) or weight_sum <= 64.0 * np.finfo(float).eps:
        weights.fill(1.0 / count)
    else:
        weights /= weight_sum

    gradient = _bundle_gradient(
        x_spectrum, active, weights, convolution.size, fft_len
    )
    positive = x > 0.0
    direction = _project_tangent(-gradient, positive)
    objective = 0.5 * float(np.dot(direction, direction))
    reconstruction_objective = objective
    gap = np.inf
    iteration = 0
    stagnant = 0

    while time.monotonic() < deadline:
        derivatives = _directional_derivatives(
            x_spectrum, direction, convolution.size, fft_len
        )[active]
        toward = int(np.argmax(derivatives))
        supported = np.flatnonzero(weights > 8.0 * np.finfo(float).eps)
        if supported.size == 0:
            break
        away = int(supported[np.argmin(derivatives[supported])])
        gap = float(derivatives[toward] - derivatives[away])
        derivative_scale = max(
            float(np.max(np.abs(derivatives))),
            np.sqrt(max(2.0 * objective, 0.0))
            * float(np.max(np.abs(gradient))),
            np.finfo(float).tiny,
        )
        gap_tolerance = max(
            32.0 * np.finfo(float).eps * derivative_scale, 2.0e-25
        )
        if toward == away or not np.isfinite(gap) or gap <= gap_tolerance:
            break

        limit = float(weights[away])
        difference = (
            _single_lag_gradient(x, int(active[toward]))
            - _single_lag_gradient(x, int(active[away]))
        )

        # h(g)=.5||Proj_T(-g)||^2 has line derivative
        # -Proj_T(-g).difference.  Convex bisection is therefore an exact
        # one-dimensional line search up to floating-point resolution.
        end_direction = _project_tangent(
            -(gradient + limit * difference), positive
        )
        if -float(np.dot(end_direction, difference)) <= 0.0:
            step = limit
            next_direction = end_direction
        else:
            low = 0.0
            high = limit
            next_direction = direction
            # Forty-two bisections resolve scales well below those reachable
            # by the parent's 28-step search without wasting work at the
            # final few non-representable bits.
            for _ in range(42):
                midpoint = 0.5 * (low + high)
                midpoint_direction = _project_tangent(
                    -(gradient + midpoint * difference), positive
                )
                line_derivative = -float(
                    np.dot(midpoint_direction, difference)
                )
                if line_derivative <= 0.0:
                    low = midpoint
                    next_direction = midpoint_direction
                else:
                    high = midpoint
            step = 0.5 * (low + high)
            next_direction = _project_tangent(
                -(gradient + step * difference), positive
            )

        next_objective = 0.5 * float(np.dot(next_direction, next_direction))
        if (
            not np.isfinite(next_objective)
            or next_objective > objective + 64.0 * np.finfo(float).eps * max(
                objective, np.finfo(float).tiny
            )
            or step <= 4.0 * np.finfo(float).eps * max(limit, 1.0)
        ):
            break
        weights[toward] += step
        weights[away] -= step
        if weights[away] < 16.0 * np.finfo(float).eps:
            weights[away] = 0.0
        gradient += step * difference
        direction = next_direction
        objective = next_objective
        iteration += 1

        # Periodic reconstruction prevents thousands of rank-one updates from
        # accumulating enough drift to manufacture a spurious descent vector.
        if iteration % 64 == 0:
            weights /= float(np.sum(weights, dtype=np.float64))
            gradient = _bundle_gradient(
                x_spectrum, active, weights, convolution.size, fft_len
            )
            direction = _project_tangent(-gradient, positive)
            rebuilt_objective = 0.5 * float(np.dot(direction, direction))
            if reconstruction_objective - rebuilt_objective <= max(
                2.0e-30,
                32.0
                * np.finfo(float).eps
                * max(reconstruction_objective, 1.0e-30),
            ):
                stagnant += 1
            else:
                stagnant = 0
            objective = rebuilt_objective
            reconstruction_objective = rebuilt_objective
            # Reaching the same floating-point bundle repeatedly is another
            # genuine convergence condition, unlike a small fixed iteration
            # cap.  Require several exact reconstructions before declaring it.
            if stagnant >= 4 and gap <= 1.0e-8 * derivative_scale:
                break

    weights /= float(np.sum(weights, dtype=np.float64))
    gradient = _bundle_gradient(
        x_spectrum, active, weights, convolution.size, fft_len
    )
    direction = _project_tangent(-gradient, positive)
    derivatives = _directional_derivatives(
        x_spectrum, direction, convolution.size, fft_len
    )[active]
    supported = np.flatnonzero(weights > 8.0 * np.finfo(float).eps)
    if supported.size:
        gap = float(
            np.max(derivatives) - np.min(derivatives[supported])
        )
    lag_weights = {
        int(active[index]): float(weight)
        for index, weight in enumerate(weights)
        if weight > 8.0 * np.finfo(float).eps
    }
    return direction, lag_weights, gap


def _adaptive_displacements(previous_scale):
    """Coarse-to-microscopic scales, with extra density near the last win."""
    scales = list(np.geomspace(1.0e-8, 1.0e-17, 37))
    if previous_scale is not None and np.isfinite(previous_scale):
        scales.extend(
            previous_scale
            * np.asarray(
                (2.0, 1.6, 1.3, 1.15, 0.875, 0.75, 0.625, 0.5),
                dtype=np.float64,
            )
        )
    return np.asarray(
        sorted(
            {
                float(scale)
                for scale in scales
                if np.isfinite(scale) and 1.0e-18 <= scale <= 2.0e-8
            },
            reverse=True,
        ),
        dtype=np.float64,
    )


def _gradient_orientation_ok(x, spectrum, convolution, fft_len):
    """Finite-difference-check an aggregate matrix-free bundle gradient."""
    active = _working_lags(convolution, 128, 2.0e-9)
    weights = np.full(active.size, 1.0 / active.size, dtype=np.float64)
    gradient = _bundle_gradient(
        spectrum, active, weights, convolution.size, fft_len
    )
    eps = 1.0e-9
    coordinate = x.size // 3
    trial = x.copy()
    trial[coordinate] += eps
    _, changed = _fft_state(trial, fft_len)
    finite_difference = float(
        np.dot(weights, changed[active] - convolution[active])
    ) / eps
    return (
        np.isfinite(finite_difference)
        and abs(finite_difference - gradient[coordinate]) <= 2.0e-8
    )


def _propose_candidate_impl(seed=42, budget_s=1000, **kwargs):
    del seed, kwargs

    # The injected state is authoritative.  The checked-in predecessor keeps
    # the candidate executable when the optional runtime state is absent.
    start = _valid_start(globals().get("GLOBAL_BEST_CONSTRUCTION"))
    if start is None:
        start = _valid_start(_load_predecessor())
    if start is None:
        raise ValueError("no finite nonnegative length-30000 construction")

    x = start.copy()
    n = x.size
    fft_len = 1 << (2 * n - 2).bit_length()
    spectrum, convolution = _fft_state(x, fft_len)
    value = _exact_value(x, convolution)
    best_x = x.copy()
    best_value = value

    # Verify the only easy-to-get-wrong FFT indexing before doing any work.
    # On failure, returning the validated incumbent is safer than optimizing
    # with a reversed gradient.
    if not _gradient_orientation_ok(x, spectrum, convolution, fft_len):
        return best_x.tolist()

    try:
        allowed = max(0.0, min(float(budget_s), 1000.0))
    except (TypeError, ValueError):
        allowed = 1000.0
    deadline = time.monotonic() + max(0.0, allowed - min(5.0, 0.05 * allowed))
    if allowed < 0.25:
        return best_x.tolist()

    # The parent already resolved the coarse 128/256-lag phase.  Spend this
    # polishing budget on the bands that continued to expose competing peaks.
    working_sets = (
        (512, 1.6e-8, 0.16),
        (1024, 3.2e-8, 0.24),
        (4096, 7.0e-8, 0.58),
    )
    # One lag-indexed state follows the exchange problem across both changed
    # peak sets and bundle sizes; newly admitted lags begin at zero weight.
    weight_state = {}
    winning_scales = {base_count: None for base_count, _, _ in working_sets}

    unproductive_cycles = 0
    while time.monotonic() < deadline and unproductive_cycles < 2:
        productive = False
        for base_count, relative_gap, time_fraction in working_sets:
            if time.monotonic() >= deadline:
                break
            active = _working_lags(convolution, base_count, relative_gap)
            remaining = max(0.0, deadline - time.monotonic())
            solve_deadline = min(
                deadline,
                time.monotonic() + max(0.25, time_fraction * remaining),
            )
            direction, lag_weights, pairwise_gap = _minimum_projected_bundle(
                x,
                spectrum,
                convolution,
                active,
                fft_len,
                weight_state,
                solve_deadline,
            )
            weight_state = lag_weights
            infinity_norm = float(np.max(np.abs(direction)))
            if (
                not np.isfinite(infinity_norm)
                or not np.isfinite(pairwise_gap)
                or infinity_norm <= 1.0e-30
            ):
                continue
            unit_direction = direction / infinity_norm

            trial_best = None
            displacements = _adaptive_displacements(
                winning_scales[base_count]
            )
            for displacement in displacements:
                if time.monotonic() >= deadline:
                    break
                candidate = _simplex_projection(
                    x + displacement * unit_direction
                )
                candidate_spectrum, candidate_convolution = _fft_state(
                    candidate, fft_len
                )
                candidate_value = _exact_value(
                    candidate, candidate_convolution
                )
                if (
                    np.isfinite(candidate_value)
                    and candidate_value < value
                    and (
                        trial_best is None
                        or candidate_value < trial_best[0]
                    )
                ):
                    trial_best = (
                        candidate_value,
                        candidate,
                        candidate_spectrum,
                        candidate_convolution,
                        float(displacement),
                    )

            # Refine geometrically between the neighboring coarse scales.
            if trial_best is not None and time.monotonic() < deadline:
                center = trial_best[4]
                refinement = center * np.geomspace(
                    10.0 ** (-0.125), 10.0 ** 0.125, 11
                )
                for displacement in refinement:
                    if time.monotonic() >= deadline:
                        break
                    candidate = _simplex_projection(
                        x + float(displacement) * unit_direction
                    )
                    candidate_spectrum, candidate_convolution = _fft_state(
                        candidate, fft_len
                    )
                    candidate_value = _exact_value(
                        candidate, candidate_convolution
                    )
                    if (
                        np.isfinite(candidate_value)
                        and candidate_value < value
                        and candidate_value < trial_best[0]
                    ):
                        trial_best = (
                            candidate_value,
                            candidate,
                            candidate_spectrum,
                            candidate_convolution,
                            float(displacement),
                        )

            if trial_best is not None:
                (
                    trial_value,
                    trial_x,
                    trial_spectrum,
                    trial_convolution,
                    trial_scale,
                ) = trial_best
                # Recompute the serialized candidate before accepting even a
                # one-ulp improvement in the true nonsmooth objective.
                checked_spectrum, checked_convolution = _fft_state(
                    trial_x, fft_len
                )
                checked_value = _exact_value(trial_x, checked_convolution)
                if not np.isfinite(checked_value) or checked_value >= value:
                    continue
                value = checked_value
                x = trial_x
                spectrum = checked_spectrum
                convolution = checked_convolution
                winning_scales[base_count] = trial_scale
                productive = True
                if value < best_value:
                    best_value = value
                    best_x = x.copy()
                # The new exact summit defines a new bundle. Restarting from
                # the tightest set is the exchange step.
                break

        if productive:
            unproductive_cycles = 0
        else:
            unproductive_cycles += 1

    best_x = np.maximum(best_x, 0.0)
    best_x /= float(np.sum(best_x, dtype=np.float64))
    # Final exact guard: serialization must never replace the incumbent with a
    # candidate whose measured nonsmooth maximum is worse.
    _, final_convolution = _fft_state(best_x, fft_len)
    _, start_convolution = _fft_state(start, fft_len)
    if _exact_value(best_x, final_convolution) >= _exact_value(
        start, start_convolution
    ):
        best_x = start
    return best_x.tolist()


def propose_candidate(seed=42, budget_s=1000, **kwargs):
    """Optimize defensively, rolling back on any runtime numerical failure."""
    try:
        return _propose_candidate_impl(seed=seed, budget_s=budget_s, **kwargs)
    except Exception:
        fallback = _valid_start(globals().get("GLOBAL_BEST_CONSTRUCTION"))
        if fallback is None:
            fallback = _valid_start(_load_predecessor())
        if fallback is None:
            raise ValueError("no finite nonnegative length-30000 construction")
        return fallback.tolist()
# EVOLVE-BLOCK-END

def run_code():
    return propose_candidate(), None
