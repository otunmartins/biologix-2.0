"""Record once, replay always: the smoke test's HTTP, without the internet.

The smoke test exercises real logic on real data -- PubChem identity, openFDA
substance and label lookups, the FDA Inactive Ingredient file, RCSB and
AlphaFold structures -- and calling those live made it fail whenever one of them
was slow, which also blocks a deploy. Every outbound call in this API goes
through the module-level ``httpx.get``, so wrapping that one function covers all
of them:

    python test_smoke.py                  # replays testdata/http_cassette.json.gz
    HTTP_RECORD=1 python test_smoke.py    # calls the live services, rewrites it

A call missing from the cassette fails like a network outage and is named on
stderr, so a test that starts making a new request says so instead of silently
passing or silently going live. Re-record when the code asks for something new
or the FDA data should be refreshed; the cassette pins the data, so the tests
see the same answers every run. test_breadth.py stays live on purpose.

TEST SUPPORT ONLY. Nothing in the app imports this.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import sys

import httpx

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata", "http_cassette.json.gz")


def _key(url, params=None) -> str:
    # httpx.URL(url, params=None) drops the query already in url, which would key
    # every openFDA search as the same bare endpoint: merge params only if given.
    u = httpx.URL(str(url))
    if params:
        u = u.copy_merge_params(params)
    # The openFDA key is a credential and varies by machine: never part of the key.
    return str(u.copy_remove_param("api_key"))


def install(record: bool = False, path: str = PATH):
    """Replace httpx.get for the rest of the process. Returns save(), which a
    recording calls once every check has passed -- a failed run must never
    leave a half-recorded cassette behind. Replaying, save() does nothing."""
    tape: dict[str, dict] = {}
    if os.path.exists(path):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            tape = json.load(fh)
    if record:
        tape = {}  # a recording is a clean snapshot, not an accumulation
    real_get = httpx.get

    def get(url, *, params=None, **kwargs):
        key = _key(url, params)
        if record:
            response = real_get(url, params=params, **kwargs)
            tape[key] = {"status": response.status_code,
                         "content_type": response.headers.get("content-type", ""),
                         "body": base64.b64encode(response.content).decode("ascii")}
            return response
        entry = tape.get(key)
        if entry is None:
            print(f"http_replay: not in the cassette, re-record with HTTP_RECORD=1: {key}",
                  file=sys.stderr)
            raise httpx.ConnectError(f"not in the cassette: {key}",
                                     request=httpx.Request("GET", key))
        return httpx.Response(entry["status"], content=base64.b64decode(entry["body"]),
                              headers={"content-type": entry["content_type"]},
                              request=httpx.Request("GET", key))

    httpx.get = get

    def save():
        if not record:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # mtime=0: the same responses give byte-identical files, so a re-record
        # that changed nothing is not a diff.
        with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
            fh.write(json.dumps(tape, sort_keys=True).encode("utf-8"))
        print(f"http_replay: recorded {len(tape)} responses to {path}", file=sys.stderr)

    return save
