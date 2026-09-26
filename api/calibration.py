"""Fit the unfolded-state factor to a user's own measurements.

interactions.py predicts an m-value from measured interaction potentials and
buried surface area. Its SIGN and its RANKING between solutes are sound; its
MAGNITUDE is not, because the unfolded state is modelled as every residue fully
exposed. That overstates dASA, so |m| comes out high - about 2.4x on lysozyme
against published urea m-values.

That error is one-directional and close to multiplicative, which is exactly the
shape a single scale factor corrects:

    observed  =  f * predicted  +  noise

So f is fitted to whatever calibration-grade pairs the user has recorded for
THEIR biologic. Not a universal constant: the denatured ensemble of an IgG is
not that of insulin, and f absorbs that difference along with anything else
systematically multiplicative.

TWO ESTIMATES ARE REPORTED, DELIBERATELY.

  Weighted least squares through the origin, which is what the data alone says.
  Through the origin because a zero prediction should mean zero effect; an
  intercept would let the fit invent stabilisation from nothing.

  A Bayesian posterior, because with three measurements the least-squares
  number has an error bar wide enough to be useless, and pretending otherwise
  is the failure mode this whole app exists to avoid. The prior is centred on
  f = 1 (the prediction is taken at face value) and is deliberately weak, so a
  handful of points moves it and a single point barely does. With no data at
  all the posterior IS the prior, and the report says so rather than returning
  a number that looks fitted.

HONEST SMALL-n BEHAVIOUR. Nothing here reports R^2 or cross-validated error on
two points. Each statistic states the n it needed and is omitted otherwise.
"""

import math

# Weakly informative prior on f: centred at 1, wide enough that the data leads.
PRIOR_MEAN = 1.0
PRIOR_SD = 0.5

MIN_PAIRS_FOR_FIT = 2      # below this, only the prior
MIN_PAIRS_FOR_TAU = 3      # below this, between-point dispersion cannot be estimated

# Assumed relative disagreement between excipients when there are too few points
# to measure it. The real uncertainty in f is not measurement noise - a single
# well-measured excipient pins f tightly under the likelihood while telling you
# nothing about whether ONE factor transfers to a different excipient. This is
# the floor that keeps a single point from looking like a calibration.
ASSUMED_BETWEEN_POINT_CV = 0.25
MIN_PAIRS_FOR_R2 = 3
MIN_PAIRS_FOR_LOO = 4


def _pairs(rows: list[dict]) -> list[dict]:
    """Rows that can actually be fitted: paired with a prediction, and non-zero.

    A prediction of zero carries no information about a multiplicative factor,
    so it is excluded rather than silently contributing a zero row.
    """
    out = []
    for r in rows:
        pred, obs = r.get("predicted_value"), r.get("value")
        if pred is None or obs is None or pred == 0:
            continue
        sd = r.get("sd")
        out.append({
            "excipient": r.get("excipient", ""),
            "quantity": r.get("quantity", ""),
            "predicted": float(pred),
            "observed": float(obs),
            # Unweighted points still count; they just cannot be weighted, and
            # the report says how many were treated that way.
            "sd": float(sd) if sd not in (None, 0) else None,
            "source": r.get("source", "experiment"),
        })
    return out


def _weighted_ls(pairs: list[dict]) -> tuple[float, float]:
    """f and its standard error, weighted least squares through the origin."""
    # Points without a reported sd get unit weight, which is the same as
    # assuming they share the typical variance of the set.
    sds = [p["sd"] for p in pairs if p["sd"]]
    default = (sum(sds) / len(sds)) if sds else 1.0
    num = den = 0.0
    for p in pairs:
        w = 1.0 / (p["sd"] or default) ** 2
        num += w * p["predicted"] * p["observed"]
        den += w * p["predicted"] ** 2
    f = num / den if den else float("nan")

    # Residual scatter about the fitted line, used for the standard error so a
    # set that disagrees with itself widens the interval rather than hiding it.
    if len(pairs) > 1:
        ss = sum((p["observed"] - f * p["predicted"]) ** 2 for p in pairs)
        sigma2 = ss / (len(pairs) - 1)
        se = math.sqrt(sigma2 / sum(p["predicted"] ** 2 for p in pairs))
    else:
        se = float("inf")
    return f, se


def _tau(pairs: list[dict], f: float, default_sd: float) -> float:
    """Between-point dispersion: scatter beyond what measurement error explains.

    Method of moments, as in a random-effects meta-analysis. Two excipients that
    each have tight error bars but disagree about f are telling you the single
    -factor model does not hold, and that has to widen the interval rather than
    being averaged away.
    """
    if len(pairs) < MIN_PAIRS_FOR_TAU:
        # Not estimable. Assume the model disagrees at the usual level rather
        # than assuming it is perfect.
        typical = sum(abs(p["observed"]) for p in pairs) / max(1, len(pairs))
        return ASSUMED_BETWEEN_POINT_CV * typical

    weights = [1.0 / (p["sd"] or default_sd) ** 2 for p in pairs]
    q = sum(w * (p["observed"] - f * p["predicted"]) ** 2 for w, p in zip(weights, pairs))
    total = sum(weights)
    denom = total - sum(w * w for w in weights) / total
    tau2 = max(0.0, (q - (len(pairs) - 1)) / denom) if denom > 0 else 0.0
    return math.sqrt(tau2)


def _posterior(pairs: list[dict]) -> tuple[float, float]:
    """Normal-normal conjugate posterior on f. Returns (mean, sd).

    Likelihood: observed ~ Normal(f * predicted, sigma^2 + tau^2), where tau is
    the between-point dispersion above. Without tau, one precisely measured
    point collapses the interval onto itself and the report claims a calibration
    that rests on a single excipient.
    """
    sds = [p["sd"] for p in pairs if p["sd"]]
    default = (sum(sds) / len(sds)) if sds else None
    if default is None:
        # No reported uncertainties anywhere: fall back to the observed scatter,
        # so the posterior cannot be made artificially sharp by silence.
        if len(pairs) > 1:
            f0, _ = _weighted_ls(pairs)
            resid = [p["observed"] - f0 * p["predicted"] for p in pairs]
            default = math.sqrt(sum(r * r for r in resid) / (len(pairs) - 1)) or 1.0
        else:
            default = abs(pairs[0]["observed"]) * 0.25 if pairs else 1.0

    f0, _ = _weighted_ls(pairs) if len(pairs) > 1 else (
        pairs[0]["observed"] / pairs[0]["predicted"], 0.0)
    tau = _tau(pairs, f0, default)

    precision = 1.0 / PRIOR_SD ** 2
    weighted = PRIOR_MEAN / PRIOR_SD ** 2
    for p in pairs:
        sigma2 = (p["sd"] or default) ** 2 + tau ** 2
        precision += p["predicted"] ** 2 / sigma2
        weighted += p["predicted"] * p["observed"] / sigma2
    return weighted / precision, math.sqrt(1.0 / precision)


def calibrate(rows: list[dict]) -> dict:
    """Fit f to measurement rows, with the statistics that n actually supports."""
    pairs = _pairs(rows)
    n = len(pairs)
    post_mean, post_sd = _posterior(pairs) if pairs else (PRIOR_MEAN, PRIOR_SD)

    result = {
        "n_pairs": n,
        "n_unweighted": sum(1 for p in pairs if p["sd"] is None),
        "sources": {s: sum(1 for p in pairs if p["source"] == s)
                    for s in {p["source"] for p in pairs}},
        "prior": {"mean": PRIOR_MEAN, "sd": PRIOR_SD,
                  "meaning": "f = 1 means the prediction is taken at face value"},
        "posterior": {
            "factor": round(post_mean, 4),
            "sd": round(post_sd, 4),
            "credible_95": [round(post_mean - 1.96 * post_sd, 4),
                            round(post_mean + 1.96 * post_sd, 4)],
        },
        "usable": n >= MIN_PAIRS_FOR_FIT,
        "between_point_dispersion": (
            "estimated from the residuals" if n >= MIN_PAIRS_FOR_TAU else
            f"not estimable below {MIN_PAIRS_FOR_TAU} pairs; assumed at "
            f"{ASSUMED_BETWEEN_POINT_CV:.0%} of the typical observation, which is why the "
            "interval stays wide"),
    }

    if n == 0:
        result["note"] = (
            "No paired predicted/observed rows, so nothing has been fitted: the posterior above "
            "IS the prior. Record measurements with predicted_value set to calibrate."
        )
        return result

    f_ls, se = _weighted_ls(pairs)
    residuals = [{"excipient": p["excipient"], "predicted": round(p["predicted"], 2),
                  "observed": round(p["observed"], 2),
                  "residual": round(p["observed"] - post_mean * p["predicted"], 2)}
                 for p in pairs]
    result["least_squares"] = {
        "factor": round(f_ls, 4),
        "se": None if math.isinf(se) else round(se, 4),
        "note": ("through the origin: a zero prediction must mean zero effect, and an intercept "
                 "would let the fit invent an effect from nothing"),
    }
    result["residuals"] = residuals

    ss_res = sum(r["residual"] ** 2 for r in residuals)
    result["rmse"] = round(math.sqrt(ss_res / n), 3)

    if n >= MIN_PAIRS_FOR_R2:
        mean_obs = sum(p["observed"] for p in pairs) / n
        ss_tot = sum((p["observed"] - mean_obs) ** 2 for p in pairs)
        # Can go negative: the fit may be worse than predicting the mean, and
        # that is worth seeing rather than clamping to zero.
        result["r_squared"] = round(1 - ss_res / ss_tot, 3) if ss_tot else None
    else:
        result["r_squared"] = None

    if n >= MIN_PAIRS_FOR_LOO:
        # Leave-one-out: the only honest error estimate at this size, since the
        # in-sample RMSE is fitted on the same points it is scored on.
        errs = []
        for i in range(n):
            rest = pairs[:i] + pairs[i + 1:]
            f_i, _ = _posterior(rest)
            errs.append((pairs[i]["observed"] - f_i * pairs[i]["predicted"]) ** 2)
        result["loo_rmse"] = round(math.sqrt(sum(errs) / n), 3)
    else:
        result["loo_rmse"] = None

    result["interpretation"] = _interpret(n, post_mean, post_sd, result)
    return result


def _interpret(n: int, f: float, sd: float, result: dict) -> str:
    lo, hi = result["posterior"]["credible_95"]
    if n < MIN_PAIRS_FOR_FIT:
        return (f"{n} paired measurement(s): far too few to fit. The factor shown is still "
                "essentially the prior, and predictions should be used uncorrected.")
    direction = ("predictions run high and should be scaled down" if hi < 1 else
                 "predictions run low and should be scaled up" if lo > 1 else
                 "no correction is resolved from these data - the interval still includes 1")
    parts = [f"f = {f:.2f} (95% credible {lo:.2f} to {hi:.2f}) from {n} pair(s): {direction}."]
    if result.get("r_squared") is None:
        parts.append(f"R^2 needs {MIN_PAIRS_FOR_R2} pairs and is not reported.")
    if result.get("loo_rmse") is None:
        parts.append(f"Cross-validated error needs {MIN_PAIRS_FOR_LOO} pairs; the RMSE shown is "
                     "in-sample and therefore optimistic.")
    if result["n_unweighted"]:
        parts.append(f"{result['n_unweighted']} point(s) carried no reported sd and were given "
                     "the set's typical weight.")
    sim = result["sources"].get("simulation", 0)
    if sim:
        parts.append(f"{sim} point(s) came from simulation, not measurement, and are currently "
                     "weighted the same as experiment.")
    return " ".join(parts)


def apply_factor(m_value: float, m_sd: float, calibration: dict) -> dict:
    """Correct a prediction, carrying both its own and the factor's uncertainty."""
    f = calibration["posterior"]["factor"]
    f_sd = calibration["posterior"]["sd"]
    corrected = f * m_value
    # Independent relative errors on a product add in quadrature.
    rel = math.sqrt((m_sd / m_value) ** 2 + (f_sd / f) ** 2) if m_value and f else float("inf")
    sd = abs(corrected) * rel if math.isfinite(rel) else float("inf")
    return {
        "uncalibrated": round(m_value, 1),
        "factor": round(f, 4),
        "calibrated": round(corrected, 1),
        "sd": None if math.isinf(sd) else round(sd, 1),
        "interval_95": None if math.isinf(sd) else
                       [round(corrected - 1.96 * sd, 1), round(corrected + 1.96 * sd, 1)],
        "n_pairs_behind_factor": calibration["n_pairs"],
        "note": ("the interval carries the factor's uncertainty as well as the prediction's, so "
                 "it widens when the calibration rests on few measurements"),
    }
