"""Per-biologic store of measured results: what was actually observed in the lab.

Predictions in this app carry honest error bars but an uncalibrated magnitude -
interactions.py runs 2-2.5x high because the unfolded state is modelled as
fully exposed. No amount of theory fixes that. Measurement does. So this is the
other half of the loop: a user records what their own biologic actually did,
and the calibration layer fits to it.

POSTGRES, AS PROMISED. This used to be a sqlite file, and the note here said the
SQL was deliberately portable -- TEXT uuid primary keys, explicit timestamps, no
SQLite-only types -- so the move would be a connection change rather than a
migration of the data model. That turned out to be true: the table definitions
below are unchanged, and what moved is in db.py. The one substantive edit was
REAL -> DOUBLE PRECISION, because Postgres REAL is four bytes where sqlite's was
eight. See db.py for the one behavioural difference that does matter: a rejected
statement poisons the transaction until someone rolls back.

DESIGN NOTES FOR WHAT IS COMING:

  Authentication. observed_by is a free-text string and nullable. When auth
  lands it becomes a foreign key to a users table; nothing else changes, and
  existing rows keep whatever was typed. observed_by stays as the record of who
  was named, not of who was authenticated.

  OpenMM. A simulated result is an observation like any other, so source
  distinguishes experiment / simulation / literature. Calibration can then
  weight a measured Tm above a computed one instead of pretending they are the
  same evidence, and a GPU run slots in without a schema change.

REPRODUCIBILITY IS PART OF THE ROW, NOT A COMMENT. An observation that cannot
be traced back to its conditions is not evidence. Every row carries the buffer,
pH, concentration, method, instrument, replicate count and protocol, and the
app version that recorded it. A row missing its conditions can be stored, but
is_calibration_grade returns False and the calibration layer will skip it.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

import psycopg
from pydantic import BaseModel, Field, field_validator

import db

# What can be recorded. Constrained deliberately: a free-text quantity cannot be
# compared across runs, and calibration needs to know what it is fitting.
Quantity = Literal[
    "tm_c",              # melting temperature, degrees C
    "delta_tm_c",        # shift vs excipient-free control, degrees C
    "m_value",           # cal/mol/molal, the quantity interactions.py predicts
    "tg_c",              # glass transition of a dried cake, degrees C
    "monomer_percent",   # SEC-HPLC main peak
    "activity_percent",  # retained activity after stress
    "aggregation_rate",  # percent per month
    # Preferential interaction coefficient of the excipient with the NATIVE
    # protein, from an OpenMM run (worker/). Negative: excluded from the surface
    # (preferential hydration, the classic stabilising signature). Positive:
    # accumulates at it. Not an m-value: that needs the unfolded state too.
    "gamma23",
]

UNITS = {
    "tm_c": "C", "delta_tm_c": "C", "m_value": "cal/mol/molal", "tg_c": "C",
    "monomer_percent": "%", "activity_percent": "%", "aggregation_rate": "%/month",
    "gamma23": "chains/protein",
}

# Quantities the prediction side can currently be scored against. Recording
# anything else is fine and useful; it just cannot calibrate yet.
CALIBRATABLE = {"m_value", "delta_tm_c", "tg_c"}


class Biologic(BaseModel):
    """The molecule being stabilised. One row per distinct construct."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(min_length=1, max_length=200)
    # Either is enough to be useful; both is better. structure_id drives the
    # ASA calculation, sequence lets a later run detect it changed.
    sequence: str = Field(default="", max_length=100000)
    structure_id: str = Field(default="", max_length=40)
    modality: str = Field(default="", max_length=60)   # mAb, enzyme, vaccine, peptide...
    notes: str = Field(default="", max_length=2000)

    @field_validator("sequence")
    @classmethod
    def _clean(cls, v: str) -> str:
        return "".join(v.split()).upper()


class Measurement(BaseModel):
    """One observation, with the conditions that make it meaningful."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    biologic_id: str

    # What was added. smiles is set for a designed polymer so a measurement can
    # be matched back to the exact structure that was proposed.
    excipient: str = Field(min_length=1, max_length=200)
    excipient_smiles: str = Field(default="", max_length=4000)
    concentration: str = Field(default="", max_length=60)   # as written: "0.5 M", "5 % w/v"

    quantity: Quantity
    value: float
    # Standard deviation across replicates. Without it a point cannot be
    # weighted, so calibration treats it as unweighted and says so.
    sd: float | None = Field(default=None, ge=0)
    n_replicates: int = Field(default=1, ge=1, le=1000)

    # Conditions. Absent these the number is not comparable to anything.
    buffer: str = Field(default="", max_length=200)
    ph: float | None = Field(default=None, ge=0, le=14)
    protein_concentration: str = Field(default="", max_length=60)
    format: Literal["liquid", "lyophilised", ""] = ""
    stress: str = Field(default="", max_length=200)  # "40 C, 4 weeks", "5 freeze-thaw"

    method: str = Field(default="", max_length=120)      # nanoDSF, DSC, SEC-HPLC...
    instrument: str = Field(default="", max_length=120)
    source: Literal["experiment", "simulation", "literature"] = "experiment"
    # Set when source is simulation, e.g. "OpenMM 8.1, CHARMM36m, 300 ns".
    simulation_detail: str = Field(default="", max_length=500)

    # Provenance. observed_by becomes a user id once auth exists.
    observed_by: str | None = Field(default=None, max_length=200)
    observed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    protocol: str = Field(default="", max_length=4000)
    raw_data_ref: str = Field(default="", max_length=500)   # file, LIMS id, notebook page

    # Links this observation to the prediction it tests, so predicted/observed
    # pairs can be formed without guessing.
    predicted_value: float | None = None
    prediction_ref: str = Field(default="", max_length=200)

    def is_calibration_grade(self) -> tuple[bool, list[str]]:
        """Can this row be fitted against? Reasons returned either way."""
        missing = []
        if self.quantity not in CALIBRATABLE:
            missing.append(f"{self.quantity} has no prediction to compare against yet")
        if not self.concentration.strip():
            missing.append("no excipient concentration")
        if not self.buffer.strip():
            missing.append("no buffer")
        if self.ph is None:
            missing.append("no pH")
        if not self.method.strip():
            missing.append("no method")
        return (not missing), missing


SCHEMA = """
CREATE TABLE IF NOT EXISTS biologic (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    sequence      TEXT NOT NULL DEFAULT '',
    structure_id  TEXT NOT NULL DEFAULT '',
    modality      TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS measurement (
    id                     TEXT PRIMARY KEY,
    biologic_id            TEXT NOT NULL REFERENCES biologic(id),
    excipient              TEXT NOT NULL,
    excipient_smiles       TEXT NOT NULL DEFAULT '',
    concentration          TEXT NOT NULL DEFAULT '',
    quantity               TEXT NOT NULL,
    value                  DOUBLE PRECISION NOT NULL,
    sd                     DOUBLE PRECISION,
    n_replicates           INTEGER NOT NULL DEFAULT 1,
    buffer                 TEXT NOT NULL DEFAULT '',
    ph                     DOUBLE PRECISION,
    protein_concentration  TEXT NOT NULL DEFAULT '',
    format                 TEXT NOT NULL DEFAULT '',
    stress                 TEXT NOT NULL DEFAULT '',
    method                 TEXT NOT NULL DEFAULT '',
    instrument             TEXT NOT NULL DEFAULT '',
    source                 TEXT NOT NULL DEFAULT 'experiment',
    simulation_detail      TEXT NOT NULL DEFAULT '',
    observed_by            TEXT,
    observed_at            TEXT NOT NULL,
    protocol               TEXT NOT NULL DEFAULT '',
    raw_data_ref           TEXT NOT NULL DEFAULT '',
    predicted_value        DOUBLE PRECISION,
    prediction_ref         TEXT NOT NULL DEFAULT '',
    -- Reproducibility: which build of this app wrote the row.
    recorded_by_version    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS measurement_by_biologic ON measurement(biologic_id);
CREATE INDEX IF NOT EXISTS measurement_by_quantity ON measurement(biologic_id, quantity);
"""

APP_VERSION = os.environ.get("APP_VERSION", "dev")


def connect(url: str | None = None) -> psycopg.Connection:
    conn = db.connect(url)
    db.init_schema(conn, SCHEMA)
    return conn


def add_biologic(conn: psycopg.Connection, b: Biologic) -> str:
    with db.tx(conn):
        conn.execute(
            "INSERT INTO biologic (id, name, sequence, structure_id, modality, notes, created_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (b.id, b.name, b.sequence, b.structure_id, b.modality, b.notes,
             datetime.now(timezone.utc).isoformat()))
    return b.id


def add_measurement(conn: psycopg.Connection, m: Measurement) -> str:
    row = m.model_dump()
    row["recorded_by_version"] = APP_VERSION
    cols = ", ".join(row)
    with db.tx(conn):
        conn.execute(f"INSERT INTO measurement ({cols}) VALUES ({', '.join(['%s'] * len(row))})",
                     tuple(row.values()))
    return m.id


def measurements_for(conn: psycopg.Connection, biologic_id: str,
                     quantity: str | None = None) -> list[dict]:
    sql = "SELECT * FROM measurement WHERE biologic_id = %s"
    args: list = [biologic_id]
    if quantity:
        sql += " AND quantity = %s"
        args.append(quantity)
    return [dict(r) for r in conn.execute(sql + " ORDER BY observed_at", args)]


def summary(conn: psycopg.Connection, biologic_id: str) -> dict:
    """What this biologic has on record, and how much of it can be fitted.

    Reported per quantity rather than as one total, because a pile of
    measurements of something uncalibratable is not progress toward calibration.
    """
    rows = measurements_for(conn, biologic_id)
    by_quantity: dict[str, dict] = {}
    usable = 0
    blockers: dict[str, int] = {}

    for row in rows:
        entry = by_quantity.setdefault(
            row["quantity"], {"n": 0, "calibration_grade": 0, "units": UNITS[row["quantity"]],
                              "sources": {}})
        entry["n"] += 1
        entry["sources"][row["source"]] = entry["sources"].get(row["source"], 0) + 1
        ok, missing = Measurement(**{k: v for k, v in row.items()
                                     if k in Measurement.model_fields}).is_calibration_grade()
        if ok:
            entry["calibration_grade"] += 1
            usable += 1
        for reason in missing:
            blockers[reason] = blockers.get(reason, 0) + 1

    paired = [r for r in rows if r["predicted_value"] is not None]
    return {
        "biologic_id": biologic_id,
        "n_measurements": len(rows),
        "n_calibration_grade": usable,
        "by_quantity": by_quantity,
        "n_paired_with_prediction": len(paired),
        "why_rows_are_not_calibration_grade": dict(
            sorted(blockers.items(), key=lambda kv: -kv[1])),
        "note": (
            "calibration_grade means the row carries the conditions needed to compare it with a "
            "prediction: a calibratable quantity, a concentration, a buffer, a pH and a method. "
            "Rows below that bar are kept and shown, never silently dropped, but the calibration "
            "layer will not fit to them."
        ),
    }


def export(conn: psycopg.Connection, biologic_id: str) -> str:
    """Everything recorded for one biologic, as JSON, for reproduction elsewhere."""
    bio = conn.execute("SELECT * FROM biologic WHERE id = %s", (biologic_id,)).fetchone()
    return json.dumps({
        "biologic": dict(bio) if bio else None,
        "measurements": measurements_for(conn, biologic_id),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "quantity_units": UNITS,
    }, indent=2)
