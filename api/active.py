"""Active learning over the copolymer design space.

WHAT THIS DOES. The exhaustive designer (design.py) can only rank the 50
backbone x pendant homopolymers, and once it has, there is nothing left to
propose. This searches the far larger space of COPOLYMERS — a backbone plus a
weighted mixture of pendants — which cannot be enumerated, and does it the way a
laboratory would: propose a batch, screen it, learn which regions score well,
and propose a better batch next time.

WHAT IT OPTIMISES, AND WHAT IT EMPHATICALLY DOES NOT. There are no stabilisation
labels yet: the OpenMM simulation (worker/) runs only on queued candidates, hours
each, and yields a native-state Gamma23, not a stabilisation label. So
the surrogate here learns to predict design.py's TRANSPARENT TRIAGE SCORE from a
candidate's composition, and the acquisition function chases a high triage score.
That score is a laboratory-triage ordering built from hydration, glass transition,
charge and the structural alerts that fired — it is NOT a prediction that a
candidate will stabilise anything, and neither is anything this module returns.
The surrogate models the cheap deterministic proxy so the loop can decide what to
BUILD AND SCREEN NEXT without enumerating the whole space; the real label is the
simulation, which every promising candidate is queued for (see candidates.py).

WHY A SURROGATE ON A SCORE WE CAN JUST COMPUTE. Because the point is the machinery,
not the shortcut. The score is cheap now, but the moment the label becomes an
OpenMM run, the same loop — cheap features in, expensive label out, acquire the
most promising unseen candidates — is what makes a GPU-bound screen tractable.
The proxy stands in for that label so the loop is built, tested and honest before
the GPU work lands, and swapping the label source is the only change needed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import numpy as np

import design
from design import BACKBONES, PENDANTS, Backbone, Composition, DesignGoal, Pendant

# Composition granularity: fractions are multiples of 1/STEP, so a candidate is a
# choice of backbone, of up to MAX_PENDANTS pendants, and of how the STEP shares
# are split between them. Coarse on purpose — synthesis cannot hit an arbitrary
# ratio, and a finer grid only inflates the space without adding real candidates.
STEP = 4
MAX_PENDANTS = 3
KAPPA = 1.0            # UCB exploration weight: mean + KAPPA * ensemble spread
POOL_SIZE = 400        # fresh candidates sampled and ranked per iteration
MIN_TRAIN = 12         # below this the surrogate cannot be trusted; seed instead

_BACKBONE_INDEX = {b.key: i for i, b in enumerate(BACKBONES)}
_PENDANT_INDEX = {p.key: i for i, p in enumerate(PENDANTS)}


# ---------------------------------------------------------------------------
# Composition identity and featurisation
# ---------------------------------------------------------------------------

def comp_key(backbone_key: str, components: Composition) -> str:
    """A canonical string for a composition, so the same blend dedupes across
    iterations however its pendants happened to be ordered."""
    parts = sorted(f"{p.key}:{round(frac, 2):g}" for p, frac in components)
    return f"{backbone_key}|{'|'.join(parts)}"


def featurize(backbone: Backbone, components: Composition) -> np.ndarray:
    """A cheap, composition-only feature vector — no structure is built.

    That is the whole economy of the loop: the surrogate scores a large pool from
    this alone, and only the chosen batch pays for a build and a screen. Pendant
    identity is one-hot by fraction, so a tree can learn 'high glucose fraction ->
    low score' without ever seeing the molecule.
    """
    v = np.zeros(len(BACKBONES) + len(PENDANTS) + 5, dtype=float)
    v[_BACKBONE_INDEX[backbone.key]] = 1.0
    charge = {"zwitterion": 0.0, "anionic": 0.0, "cationic": 0.0, "neutral": 0.0}
    for pendant, frac in components:
        v[len(BACKBONES) + _PENDANT_INDEX[pendant.key]] = frac
        charge[pendant.charge] = charge.get(pendant.charge, 0.0) + frac
    tail = len(BACKBONES) + len(PENDANTS)
    v[tail] = len(components)
    v[tail + 1] = charge["zwitterion"]
    v[tail + 2] = charge["anionic"]
    v[tail + 3] = charge["cationic"]
    v[tail + 4] = charge["neutral"]
    return v


# ---------------------------------------------------------------------------
# The search space
# ---------------------------------------------------------------------------

class SearchSpace:
    """Samples compositions: a backbone, 1..MAX_PENDANTS distinct pendants, and a
    split of STEP shares between them."""

    def __init__(self, backbones=BACKBONES, pendants=PENDANTS):
        self.backbones = backbones
        self.pendants = pendants

    def _fractions(self, m: int, rng: random.Random) -> list[float]:
        # STEP shares across m pendants, each pendant getting at least one share.
        cuts = sorted(rng.sample(range(1, STEP), m - 1)) if m > 1 else []
        bounds = [0, *cuts, STEP]
        return [(bounds[i + 1] - bounds[i]) / STEP for i in range(m)]

    def sample_one(self, rng: random.Random) -> tuple[Backbone, Composition]:
        backbone = rng.choice(self.backbones)
        m = rng.randint(1, min(MAX_PENDANTS, len(self.pendants)))
        pendants = rng.sample(self.pendants, m)
        fracs = self._fractions(m, rng)
        return backbone, list(zip(pendants, fracs))

    def sample(self, n: int, rng: random.Random) -> list[tuple[Backbone, Composition]]:
        """n distinct compositions (deduped by canonical key)."""
        out, seen = [], set()
        for _ in range(n * 6):
            b, c = self.sample_one(rng)
            k = comp_key(b.key, c)
            if k not in seen:
                seen.add(k)
                out.append((b, c))
            if len(out) >= n:
                break
        return out


# ---------------------------------------------------------------------------
# Surrogate: an ExtraTrees ensemble, tree spread as its own uncertainty
# ---------------------------------------------------------------------------
#
# The same idiom as tg_model.py: the trees' disagreement is the model's doubt, and
# it is what the acquisition function trades exploration against. It is a surrogate
# for the TRIAGE SCORE, not for stabilisation.

class Surrogate:
    def __init__(self, X: np.ndarray, y: np.ndarray):
        from sklearn.ensemble import ExtraTreesRegressor
        self.model = ExtraTreesRegressor(n_estimators=100, min_samples_leaf=1,
                                         n_jobs=-1, random_state=0)
        self.model.fit(X, y)

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        per_tree = np.array([t.predict(X) for t in self.model.estimators_])
        return per_tree.mean(axis=0), per_tree.std(axis=0)


def _cv_mae(X: np.ndarray, y: np.ndarray) -> float | None:
    """Cross-validated mean absolute error of the surrogate against the proxy.

    This is the loop's own learning curve: how well the surrogate predicts the
    triage score on points it did not see. Reported once there are enough points
    to fold; None below that, never a fabricated number.
    """
    if len(y) < 5:
        return None
    from sklearn.ensemble import ExtraTreesRegressor
    from sklearn.model_selection import cross_val_score
    model = ExtraTreesRegressor(n_estimators=100, min_samples_leaf=1,
                                n_jobs=-1, random_state=0)
    folds = min(5, len(y))
    scores = cross_val_score(model, X, y, cv=folds,
                             scoring="neg_mean_absolute_error")
    return round(float(-scores.mean()), 3)


# ---------------------------------------------------------------------------
# Batch selection
# ---------------------------------------------------------------------------

def _diverse_pick(pool: list[tuple[Backbone, Composition]], k: int,
                  rng: random.Random) -> list[tuple[Backbone, Composition]]:
    """Farthest-point sampling in feature space: a space-filling batch with no
    scores, for the first iteration when the surrogate has nothing to learn from."""
    if len(pool) <= k:
        return pool
    feats = [featurize(b, c) for b, c in pool]
    chosen = [rng.randrange(len(pool))]
    while len(chosen) < k:
        best_i, best_d = None, -1.0
        for i in range(len(pool)):
            if i in chosen:
                continue
            d = min(np.linalg.norm(feats[i] - feats[j]) for j in chosen)
            if d > best_d:
                best_i, best_d = i, d
        chosen.append(best_i)
    return [pool[i] for i in chosen]


def _diverse_ucb_pick(pool: list[tuple[Backbone, Composition]], ucb: np.ndarray,
                      k: int) -> list[tuple[Backbone, Composition]]:
    """Exploit then diversify: keep the high-UCB head of the pool, then farthest-
    point sample within it so a batch is promising without being near-duplicates."""
    if len(pool) <= k:
        return pool
    feats = [featurize(b, c) for b, c in pool]
    head = list(np.argsort(ucb)[::-1][: max(k * 4, k)])
    chosen = [head[0]]                       # the single most promising candidate
    while len(chosen) < k and len(chosen) < len(head):
        best_i, best_d = None, -1.0
        for i in head:
            if i in chosen:
                continue
            d = min(np.linalg.norm(feats[i] - feats[j]) for j in chosen)
            if d > best_d:
                best_i, best_d = i, d
        chosen.append(best_i)
    return [pool[i] for i in chosen]


# ---------------------------------------------------------------------------
# One iteration
# ---------------------------------------------------------------------------

@dataclass
class PriorCandidate:
    """A benchmarked candidate carried between iterations: enough to refit the
    surrogate and to dedupe, without rebuilding anything."""
    backbone_key: str
    components: list[tuple[str, float]]      # (pendant_key, fraction)
    score: float

    def key(self) -> str:
        b = next(x for x in BACKBONES if x.key == self.backbone_key)
        comps = [(next(p for p in PENDANTS if p.key == pk), f) for pk, f in self.components]
        return comp_key(b.key, comps)

    def resolved(self) -> tuple[Backbone, Composition]:
        b = next(x for x in BACKBONES if x.key == self.backbone_key)
        comps = [(next(p for p in PENDANTS if p.key == pk), f) for pk, f in self.components]
        return b, comps


@dataclass
class Proposal:
    candidates: list[design.Candidate]
    metrics: dict = field(default_factory=dict)


def propose_batch(goal: DesignGoal, prior: list[PriorCandidate], k: int = 8,
                  rng: random.Random | None = None) -> Proposal:
    """Propose, screen and score one batch of new candidates.

    prior is everything benchmarked so far. Below MIN_TRAIN points the batch is a
    space-filling seed; above it, the surrogate is fit on prior and a fresh pool is
    ranked by UCB, then diversified. Either way the chosen batch is deduped against
    prior and labelled with the real deterministic screen (design.evaluate).
    """
    rng = rng or random.Random()
    space = SearchSpace()
    seen = {p.key() for p in prior}

    surrogate_cv_mae = None
    if len(prior) >= MIN_TRAIN:
        X = np.array([featurize(*p.resolved()) for p in prior])
        y = np.array([p.score for p in prior])
        surrogate_cv_mae = _cv_mae(X, y)
        model = Surrogate(X, y)

        pool = [(b, c) for b, c in space.sample(POOL_SIZE, rng)
                if comp_key(b.key, c) not in seen]
        if pool:
            feats = np.array([featurize(b, c) for b, c in pool])
            mean, spread = model.predict(feats)
            chosen = _diverse_ucb_pick(pool, mean + KAPPA * spread, k)
        else:
            chosen = []
    else:
        pool = [(b, c) for b, c in space.sample(POOL_SIZE, rng)
                if comp_key(b.key, c) not in seen]
        chosen = _diverse_pick(pool, k, rng)

    cands = [c for c in (design.evaluate(b, comp, goal) for b, comp in chosen)
             if c is not None]

    new_scores = [c.score for c in cands]
    prior_scores = [p.score for p in prior]
    best = max(prior_scores + new_scores) if (prior_scores + new_scores) else None
    metrics = {
        "batch_size": len(cands),
        "best_score_so_far": round(best, 2) if best is not None else None,
        "batch_mean_score": round(sum(new_scores) / len(new_scores), 2) if new_scores else None,
        "batch_diversity": _batch_diversity(chosen),
        "surrogate_cv_mae": surrogate_cv_mae,
        "frac_alert_free": (round(sum(1 for c in cands if not c.alerts) / len(cands), 2)
                            if cands else None),
        "n_total": len(prior) + len(cands),
        "seeded": len(prior) < MIN_TRAIN,
    }
    return Proposal(candidates=cands, metrics=metrics)


def _batch_diversity(chosen: list[tuple[Backbone, Composition]]) -> float | None:
    """Mean pairwise feature distance within the batch — a batch of near-identical
    candidates has a low number and is a wasted iteration."""
    if len(chosen) < 2:
        return None
    feats = [featurize(b, c) for b, c in chosen]
    dists = [float(np.linalg.norm(feats[i] - feats[j]))
             for i in range(len(feats)) for j in range(i + 1, len(feats))]
    return round(sum(dists) / len(dists), 3)


ACTIVE_LIMITS = (
    "Each iteration proposes a batch of copolymer candidates, screens them with the same "
    "structural alerts and rule table as every other excipient here, and learns which "
    "compositions score well so the next batch is better. The surrogate models design.py's "
    "TRANSPARENT TRIAGE SCORE — hydration, glass transition, charge and the alerts that "
    "fired — NOT stabilisation: there is no molecular dynamics and no free-energy calculation "
    "in this loop, and 'improvement' means a higher triage score, not a candidate shown to "
    "protect a protein. Copolymer Tg is a Fox-equation blend of homopolymer estimates and does "
    "not model sequence or reactivity ratios. Every candidate is grade D, verdict 'Data gap: "
    "test', until a real measurement or an OpenMM simulation exists — which is exactly what the "
    "queue is for. Being queued is a triage decision, not evidence."
)
