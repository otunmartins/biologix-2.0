"""AI orchestration for a design campaign — the controller that sits on top of the
active-learning engine and decides what to do next.

WHERE THE INTELLIGENCE IS, AND WHERE IT IS NOT. This follows the same division of
labour as the rest of the app: the deterministic engine (active.py) proposes and
screens candidates and reports honest benchmark metrics; the orchestrator reads
those metrics and decides the NEXT MOVE — keep going, shift from exploring to
exploiting, or stop because the campaign has converged — and says why. It never
proposes chemistry and never changes a grade. Its recommendation is ADVISORY: a
human clicks to run each iteration and, above all, to queue a candidate for the
expensive OpenMM run, because that hand-off is where cost and commitment are.

WHY THE DECISION RULE IS DETERMINISTIC. A stop/continue decision has to be
reproducible and testable, and it has to work on the deploy box whether or not a
model key is configured — the same reason the evidence gate in main.py lives in
code, not in a prompt. So the recommendation is computed from the metric history
by the rule below. narrate() is the optional layer that turns that decision into a
sentence with the existing agent; the decision itself does not depend on it.
"""

# How many recent iterations of near-flat best-score before a campaign is called
# converged, and how small a gain counts as flat.
PATIENCE = 2
MIN_GAIN = 0.05


def recommend(metrics_history: list[dict]) -> dict:
    """Read the benchmark history and recommend the next move.

    metrics_history is candidates.metrics_history output: one row per iteration,
    each carrying best_score_so_far, batch_mean_score, batch_diversity,
    surrogate_cv_mae and seeded.
    """
    if not metrics_history:
        return {
            "action": "continue", "phase": "seed", "suggest_queue": False,
            "reason": "No iterations yet — run the first batch to seed the search.",
        }

    last = metrics_history[-1]
    n = len(metrics_history)
    seeded_now = last.get("seeded", False)

    # Still space-filling: the surrogate has nothing to exploit yet.
    if seeded_now:
        return {
            "action": "continue", "phase": "seed", "suggest_queue": False,
            "reason": (f"Iteration {n} is still a space-filling seed — the surrogate needs more "
                       "screened candidates before it can guide the search. Run another batch."),
        }

    # Has the best score stopped moving over the last PATIENCE+1 iterations?
    # A flat stretch only counts once the surrogate is driving: the best score is
    # naturally flat during the space-filling seed, and calling that convergence
    # would stop the run just before exploitation starts paying off.
    bests = [m.get("best_score_so_far") for m in metrics_history
             if m.get("best_score_so_far") is not None]
    recent = metrics_history[-(PATIENCE + 1):]
    warming_up = any(m.get("seeded", False) for m in recent)
    plateaued = False
    if len(bests) > PATIENCE and not warming_up:
        recent_gain = bests[-1] - bests[-(PATIENCE + 1)]
        plateaued = recent_gain < MIN_GAIN

    cv = last.get("surrogate_cv_mae")
    diversity = last.get("batch_diversity")

    if plateaued:
        return {
            "action": "stop", "phase": "converged", "suggest_queue": True, "top_k_to_queue": 5,
            "reason": (f"Best triage score has gained under {MIN_GAIN} over the last {PATIENCE} "
                       "iterations — the search has converged on this motif table. Queue the top "
                       "candidates for an OpenMM run rather than spending more iterations."),
        }

    # Still improving: which way to lean next. A low, settled CV error means the
    # surrogate is trustworthy enough to exploit; otherwise keep the batch diverse.
    if cv is not None and cv < 1.0:
        phase, hint = "exploit", ("the surrogate now predicts the triage score to within "
                                  f"{cv} — lean on it and refine the best region")
    else:
        phase, hint = "explore", "the surrogate is still coarse — keep the batch diverse"
    return {
        "action": "continue", "phase": phase, "suggest_queue": False,
        "reason": (f"Best score is still climbing ({bests[-1]}); {hint}. Run another iteration."
                   + (f" Batch diversity is {diversity}." if diversity is not None else "")),
    }


ORCHESTRATION_NOTE = (
    "The campaign controller reads each iteration's benchmark metrics and recommends whether to "
    "keep iterating, shift to exploiting the surrogate, or stop and queue for simulation. The "
    "recommendation is advisory and deterministic; it never proposes chemistry and never changes "
    "a grade. Queuing for an OpenMM run is always a human decision."
)
