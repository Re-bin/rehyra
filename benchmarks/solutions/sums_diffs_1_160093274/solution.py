# EVOLVE-BLOCK-START
import math
import numpy as np


def _as_integer_set(value):
    """Extract the injected construction without depending on snapshot files."""
    if isinstance(value, dict):
        for key in ("construction", "sequence", "set", "A"):
            if key in value:
                return _as_integer_set(value[key])
        return None
    if isinstance(value, tuple) and len(value) == 2:
        value = value[0]
    try:
        result = sorted(set(int(x) for x in value))
    except Exception:
        return None
    return result if len(result) >= 2 else None


def _fft_score(values):
    """Exact support counting by zero-padded convolution/autocorrelation."""
    a = np.asarray(values, dtype=np.int64)
    n = int(a.size)
    if n < 2:
        return -math.inf, 0, 0

    shifted = a - int(a.min())
    width = int(shifted.max()) + 1
    fft_len = 1 << max(1, (2 * width - 2).bit_length())
    indicator = np.zeros(fft_len, dtype=np.float64)
    indicator[shifted] = 1.0
    spectrum = np.fft.rfft(indicator)

    sum_size = int(
        np.count_nonzero(np.fft.irfft(spectrum * spectrum, fft_len) > 0.5)
    )
    diff_size = int(
        np.count_nonzero(
            np.fft.irfft(spectrum * np.conjugate(spectrum), fft_len) > 0.5
        )
    )
    if sum_size <= n or diff_size <= n:
        return -math.inf, sum_size, diff_size
    score = math.log(sum_size / n) / math.log(diff_size / n)
    return score, sum_size, diff_size


def _incumbent_geometry(incumbent):
    """Normalize the dominant interval layer and recover the two odd offsets."""
    if not incumbent:
        return None
    residue_groups = [
        sorted(x for x in incumbent if x % 4 == residue) for residue in range(4)
    ]
    core = max(residue_groups, key=len)
    if len(core) < 2 or any(y - x != 4 for x, y in zip(core, core[1:])):
        return None

    # Translate the first core point to 2. Translation leaves C(A) unchanged.
    translated = [x - core[0] + 2 for x in incumbent]
    length = len(core)
    threes = sorted((x - 3) // 4 for x in translated if x % 4 == 3)
    if len(threes) >= 2:
        u = max(0, threes[0])
        v = max(0, length - 1 - threes[-1])
    else:
        u, v = 5, 8
    return length, u, v


def _make_layered_set(length, spacing, fringe, phase, u, v):
    """Core 2 mod 4, fringed/progression 1 mod 4, and two 3 mod 4 points."""
    left = range(-1, fringe - 1)
    right = range(length - fringe - 1, length - 1)

    first_interior = fringe - 1
    first_progression = phase
    if first_progression < first_interior:
        first_progression += (
            (first_interior - first_progression + spacing - 1) // spacing
        ) * spacing
    interior = range(first_progression, length - fringe - 1, spacing)
    quotient_ones = set(left)
    quotient_ones.update(right)
    quotient_ones.update(interior)

    result = [4 * q + 2 for q in range(length)]
    result.extend(4 * q + 1 for q in quotient_ones)
    result.append(4 * u + 3)
    result.append(4 * (length - 1 - v) + 3)
    return result


def _filling_phases(length, spacing):
    """Solve 2*p == length-3 (mod spacing), including even spacings."""
    divisor = math.gcd(2, spacing)
    target = length - 3
    if target % divisor:
        return ()

    reduced_modulus = spacing // divisor
    reduced_target = target // divisor
    # After division the coefficient of p is 2/divisor.  It is coprime to
    # reduced_modulus, so pow(..., -1, ...) gives the unique reduced solution.
    coefficient = 2 // divisor
    base = (
        reduced_target * pow(coefficient, -1, reduced_modulus)
    ) % reduced_modulus
    return tuple(base + k * reduced_modulus for k in range(divisor))


def _place_in_domain(values):
    """Translate a bounded-diameter candidate into the public integer domain."""
    if len(values) < 2:
        return None
    low = min(values)
    high = max(values)
    if high - low > 2_000_000:
        return None

    # Pin the left endpoint.  Translation preserves both support sizes and
    # mutating the trial avoids retaining a second half-million-element list.
    shift = -1_000_000 - low
    if shift:
        for index in range(len(values)):
            values[index] += shift
    return values


def construct_set():
    incumbent = _as_integer_set(globals().get("GLOBAL_BEST_CONSTRUCTION"))
    if incumbent is None:
        incumbent = [0, 2, 3, 4, 7, 10, 12, 14]

    best_set = incumbent
    best_score, _, _ = _fft_score(best_set)

    def consider(candidate):
        nonlocal best_set, best_score
        candidate = _place_in_domain(candidate)
        if candidate is None:
            return -math.inf
        score, _, _ = _fft_score(candidate)
        if score > best_score:
            best_score = score
            best_set = candidate
        return score

    # With the q=-1 point in the left fringe, length 499999 has raw endpoints
    # -3 and 1999994: diameter 1999997, which fits after translation.  Length
    # 500000 would exceed the public diameter with this unchanged family.
    final_length = 499_999

    # Stage 1: rank both parities across the same broad sqrt-scale band.
    # Even spacings have two solutions of the filling congruence.  Retaining
    # shortlists separately prevents their close scores from being crowded out
    # by the more numerous odd trials.
    spacing_by_parity = {0: [], 1: []}
    for spacing in range(430, 701):
        u = max(0, (spacing - 5) // 2)
        v = u + 3
        for phase in _filling_phases(final_length, spacing):
            score = consider(
                _make_layered_set(
                    final_length,
                    spacing,
                    spacing,
                    phase,
                    u,
                    v,
                )
            )
            spacing_by_parity[spacing & 1].append(
                (score, final_length, spacing, spacing, phase, u, v)
            )
    for finalists in spacing_by_parity.values():
        finalists.sort(reverse=True)

    # Very small floor/parity effects can make a boundary-near length better
    # than the absolute maximum.  Challenge that assumption using only the
    # leading spacings from each parity of the maximum-length sweep.
    scale_by_parity = {
        parity: list(finalists[:10])
        for parity, finalists in spacing_by_parity.items()
    }
    for parity, broad_finalists in spacing_by_parity.items():
        leading_spacings = []
        for entry in broad_finalists:
            spacing = entry[2]
            if spacing not in leading_spacings:
                leading_spacings.append(spacing)
            if len(leading_spacings) == 6:
                break
        for length in range(final_length - 9, final_length):
            for spacing in leading_spacings:
                u = max(0, (spacing - 5) // 2)
                v = u + 3
                for phase in _filling_phases(length, spacing):
                    score = consider(
                        _make_layered_set(
                            length,
                            spacing,
                            spacing,
                            phase,
                            u,
                            v,
                        )
                    )
                    scale_by_parity[parity].append(
                        (score, length, spacing, spacing, phase, u, v)
                    )
        scale_by_parity[parity].sort(reverse=True)

    # Stage 2: the dense fringe and sparse progression have separate jobs, so
    # retune fringe width for leading candidates from each parity.
    fringe_by_parity = {0: [], 1: []}
    for parity, scale_finalists in scale_by_parity.items():
        for (
            _,
            length,
            spacing,
            _,
            phase,
            base_u,
            base_v,
        ) in scale_finalists[:4]:
            for fringe in range(spacing - 12, spacing + 13):
                score = consider(
                    _make_layered_set(
                        length,
                        spacing,
                        fringe,
                        phase,
                        base_u,
                        base_v,
                    )
                )
                fringe_by_parity[parity].append(
                    (score, length, spacing, fringe, phase, base_u, base_v)
                )
        fringe_by_parity[parity].sort(reverse=True)

    # Stage 3: explicitly challenge the exact filling congruence with nearby
    # phases, but only for the best spacing/fringe pairs of each parity.
    phase_by_parity = {0: [], 1: []}
    for parity, fringe_finalists in fringe_by_parity.items():
        for (
            _,
            length,
            spacing,
            fringe,
            filling,
            base_u,
            base_v,
        ) in fringe_finalists[:3]:
            for phase_offset in range(-3, 4):
                phase = (filling + phase_offset) % spacing
                score = consider(
                    _make_layered_set(
                        length,
                        spacing,
                        fringe,
                        phase,
                        base_u,
                        base_v,
                    )
                )
                phase_by_parity[parity].append(
                    (score, length, spacing, fringe, phase, base_u, base_v)
                )
        phase_by_parity[parity].sort(reverse=True)

    # Stage 4: independently perturb the two residue-3 offsets for only the
    # best candidates of each parity.  Independent movement is important:
    # their useful alignment is not assumed in the search.
    for phase_finalists in phase_by_parity.values():
        for (
            _,
            length,
            spacing,
            fringe,
            phase,
            base_u,
            base_v,
        ) in phase_finalists[:2]:
            for u_offset in range(-2, 3):
                for v_offset in range(-2, 3):
                    consider(
                        _make_layered_set(
                            length,
                            spacing,
                            fringe,
                            phase,
                            base_u + u_offset,
                            base_v + v_offset,
                        )
                    )

    return best_set
# EVOLVE-BLOCK-END

def run_code():
    return construct_set(), None
