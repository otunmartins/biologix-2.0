"""Glass transition temperature from a repeat unit, learned from experiment.

Replaces a hand-typed lookup of five homopolymers with a model trained on the
LAMALAB curated benchmark (Zenodo 14980914): 7367 PSMILES-Tg pairs assembled
from nine sources, of which 7204 featurize.

MEASURED PERFORMANCE, both numbers reported because they mean different things:

    random split     R2 0.885   MAE 26.3 C   RMSE 38.4 C
    scaffold split   R2 0.704   MAE 36.3 C   RMSE 49.6 C

The random split reproduces the published reference for this task (R2 0.886,
MAE 25.2 C) and is the wrong number to quote here. A designed glycopolymer is
chemistry the training set has not seen, so the scaffold split - whole Murcko
families held out - is the honest estimate, and MAE 36.3 C is what a caller
should plan around. The published work reports the same effect, 1.4-3.0x MAE
degradation on family holdout; this reproduces it at 1.38x.

SIZE WAS TRADED FOR A LITTLE ACCURACY, DELIBERATELY. Fully grown trees pickle
to 157 MB; min_samples_leaf=4 with 100 trees gives a 16.8 MB forest and costs
0.8 C of MAE, which is well inside the 36.3 C error it is measured at. The
pickle on disk is larger than the forest - about 20 MB - because it also carries
the training-set fingerprints that the applicability check needs. The numbers
above are for the configuration actually shipped, not the larger one.

UNCERTAINTY IS PER PREDICTION, NOT A CONSTANT. The forest's trees disagree more
on unfamiliar chemistry, so the spread across trees is reported per query and
widens where the model is out of its depth. It is a model-spread interval, not
a calibrated confidence interval: it says how much the ensemble disagrees, not
how often the truth lands inside.

APPLICABILITY IS CHECKED, NOT ASSUMED. Every prediction reports its distance to
the nearest training polymer. A repeat unit unlike anything in the set gets a
prediction and a warning, never silent confidence.
"""

import csv
import os
import pickle

import numpy as np
from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors, rdFingerprintGenerator

import polymer

RDLogger.DisableLog("rdApp.*")

DATA_URL = ("https://zenodo.org/records/14980914/files/"
            "LAMALAB_CURATED_Tg_structured.csv?download=1")
CACHE_DIR = os.environ.get("IID_CACHE_DIR") or os.path.dirname(__file__)
# Beside this file, NOT in CACHE_DIR: the pickle is committed and COPY'd into the
# image at /app, while docker-compose points IID_CACHE_DIR at an empty /cache
# volume. Looking there, production never found the model and served the
# handbook fallback. The training data can stay a cache; it is re-fetchable.
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg_model.pkl")
DATA_PATH = os.path.join(CACHE_DIR, "tg_data.csv")

# Held-out performance, measured in training and carried with every prediction
# so a caller never has to go looking for how good this actually is.
PERFORMANCE = {
    "random_split": {"r2": 0.885, "mae_c": 26.3, "rmse_c": 38.4},
    "scaffold_split": {"r2": 0.704, "mae_c": 36.3, "rmse_c": 49.6},
    "n_training_polymers": 7204,
    "model": ("ExtraTrees, 100 trees, min_samples_leaf=4, 131 RDKit descriptors on a "
              "2-unit oligomer"),
    "source": "LAMALAB curated Tg benchmark, Zenodo 14980914",
    "use_which": ("scaffold_split applies to novel chemistry, which is the case for any "
                  "designed candidate; random_split flatters the model by testing it on "
                  "near-duplicates of what it trained on"),
}

# fr_ counts and Ipc are dropped: fragment counts are sparse and Ipc overflows.
DESCRIPTORS = [(n, f) for n, f in Descriptors.descList
               if not n.startswith(("fr_", "Ipc"))]
IN_DOMAIN_TANIMOTO = 0.4     # below this, the query is unlike the training set

_model = None
_train_fps = None


def represent(psmiles: str):
    """Two linked repeat units, which is what the polymer actually looks like.

    Capping the attachment points with hydrogen instead LOSES the connectivity
    that distinguishes real polymers: [*]CC([*])c1ccccc1 (polystyrene, Tg 96 C)
    and [*]CCc1cccc([*])c1 (Tg 9 C) both cap to ethylbenzene, and PMMA collides
    the same way with a 121 C gap in label. Across this dataset capping collides
    106 distinct polymers onto shared representations, with up to 228 C of Tg
    spread inside one collision; building the dimer reduces that to 6.
    """
    try:
        return polymer.build(polymer.PolymerSpec(repeat_unit=psmiles), 2)
    except Exception:
        return None


def features(mol) -> list[float] | None:
    vals = []
    for _, fn in DESCRIPTORS:
        try:
            v = fn(mol)
        except Exception:
            return None
        if v is None or not np.isfinite(v):
            return None
        vals.append(v)
    return vals


def _fingerprint(mol):
    return rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=1024).GetFingerprint(mol)


def fetch_data(data_path: str = DATA_PATH) -> str:
    """Download the benchmark if it is not already on disk, and return the path.

    The CSV is gitignored, so this is the only route back to it: without a fetch
    the model could be retrained by whoever still had the file and nobody else.
    """
    if os.path.exists(data_path):
        return data_path
    import httpx

    with httpx.stream("GET", DATA_URL, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        # Written to a temporary name first, so an interrupted download cannot
        # leave a half file that looks like a valid dataset to the next caller.
        tmp = data_path + ".part"
        with open(tmp, "wb") as fh:
            for chunk in r.iter_bytes():
                fh.write(chunk)
    os.replace(tmp, data_path)
    return data_path


def train(data_path: str = DATA_PATH, save: bool = True) -> dict:
    """Fit the forest. Run once; the result is pickled beside the IID cache."""
    from sklearn.ensemble import ExtraTreesRegressor

    data_path = fetch_data(data_path)
    X, y, fps = [], [], []
    with open(data_path, encoding="utf8") as fh:
        for row in csv.DictReader(fh):
            smi, tg = row.get("PSMILES", ""), row.get("labels.Exp_Tg(K)", "")
            if not smi or not tg:
                continue
            try:
                tg_c = float(tg) - 273.15
            except ValueError:
                continue
            mol = represent(smi)
            if mol is None:
                continue
            f = features(mol)
            if f is None:
                continue
            X.append(f)
            y.append(tg_c)
            fps.append(_fingerprint(mol))

    # See the module docstring: this configuration is 9x smaller than fully
    # grown trees for 0.8 C of MAE, which is noise at this error level.
    model = ExtraTreesRegressor(n_estimators=100, min_samples_leaf=4,
                                n_jobs=-1, random_state=0)
    model.fit(np.array(X, dtype=float), np.array(y))
    if save:
        with open(MODEL_PATH, "wb") as fh:
            pickle.dump({"model": model, "fps": fps}, fh)
    return {"n": len(y), "path": MODEL_PATH}


def available() -> bool:
    """Whether a prediction can be served at all.

    The pickle is a build artefact, not source: it is gitignored, so a fresh
    checkout - which is what the deploy box has - does not carry it, and callers
    have to have something to fall back to. Unpickling also needs scikit-learn,
    so a missing dependency reads the same way as a missing file.
    """
    try:
        _load()
        return True
    except Exception:
        return False


def _load():
    global _model, _train_fps
    if _model is None:
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(
                f"no trained Tg model at {MODEL_PATH}; run tg_model.train() once")
        with open(MODEL_PATH, "rb") as fh:
            blob = pickle.load(fh)
        _model, _train_fps = blob["model"], blob["fps"]
    return _model, _train_fps


def predict(psmiles: str) -> dict:
    """Tg in C for one repeat unit, with spread and an applicability check."""
    mol = represent(psmiles)
    if mol is None:
        return {"available": False, "reason": f"could not parse repeat unit {psmiles!r}"}
    f = features(mol)
    if f is None:
        return {"available": False,
                "reason": "descriptors could not be computed for this repeat unit"}

    try:
        model, train_fps = _load()
    except Exception as exc:
        # A missing pickle is the ordinary state of a fresh checkout, not a bug.
        # It reads like any other reason a prediction is unavailable so a caller
        # has one path to handle instead of two.
        return {"available": False, "reason": f"Tg model unavailable: {exc}"}
    x = np.array([f], dtype=float)
    # Every tree's own answer: their disagreement is the model's own doubt.
    votes = np.array([t.predict(x)[0] for t in model.estimators_])
    tg, sd = float(votes.mean()), float(votes.std())

    from rdkit import DataStructs
    sims = DataStructs.BulkTanimotoSimilarity(_fingerprint(mol), train_fps)
    nearest = float(max(sims))

    return {
        "available": True,
        "tg_c": round(tg, 1),
        "model_spread_sd": round(sd, 1),
        "expected_error_mae_c": PERFORMANCE["scaffold_split"]["mae_c"],
        "nearest_training_similarity": round(nearest, 3),
        "in_domain": nearest >= IN_DOMAIN_TANIMOTO,
        "performance": PERFORMANCE,
        "note": (
            f"Predicted from the repeat unit by a forest trained on "
            f"{PERFORMANCE['n_training_polymers']} experimental polymers. Plan around the "
            f"scaffold-split error ({PERFORMANCE['scaffold_split']['mae_c']} C), not the "
            "tree spread: the spread shows where the ensemble disagrees, it is not a "
            "calibrated interval."
            + ("" if nearest >= IN_DOMAIN_TANIMOTO else
               f" OUT OF DOMAIN: the closest training polymer is only {nearest:.2f} similar, "
               "so this prediction is an extrapolation and the stated error does not apply.")
        ),
    }
