"""The simulation worker: pull a queued candidate from the API, simulate it, report.

    python worker.py            # run forever
    python worker.py --once     # one job (or none), then exit

Talks to the API over HTTP only (api/main.py /worker/*), authenticated by
WORKER_TOKEN, so it holds no database credentials and can run on any machine
that can reach the API: the GPU box itself, or a rented GPU elsewhere.

    API_URL        where the API is, e.g. http://api:8000 inside docker-compose
    WORKER_TOKEN   the same secret the API has
    WORKER_NAME    shown on each job; defaults to the hostname
    POLL_SECONDS   how long to sleep when the queue is empty (default 30)
    IDLE_STOP_MINUTES  stop after this long with an empty queue (default 0: never).
                   On a RunPod pod it stops the pod itself, via the pod-scoped
                   RUNPOD_API_KEY RunPod injects, so an idle GPU stops billing;
                   anywhere else the process just exits.
    SIM_*          simulation settings, see simulate.Settings

While a job runs, a background thread heartbeats every minute with progress.
If the API answers 409 -- the job was requeued after this worker went quiet,
and may now be someone else's -- the run is abandoned without reporting. If
this process dies, the heartbeats stop and the API requeues the job itself.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback

import httpx

import simulate

API_URL = os.environ.get("API_URL", "http://api:8000").rstrip("/")
TOKEN = os.environ.get("WORKER_TOKEN", "")
NAME = os.environ.get("WORKER_NAME") or socket.gethostname()
POLL = float(os.environ.get("POLL_SECONDS", "30"))
IDLE_STOP = float(os.environ.get("IDLE_STOP_MINUTES", "0")) * 60
BEAT = 60.0


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Job:
    def __init__(self, client: httpx.Client, job: dict):
        self.client, self.job, self.id = client, job, job["job_id"]
        self.progress = "claimed"
        self.lost = threading.Event()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._beat, daemon=True)

    def _beat(self) -> None:
        while not self.done.wait(BEAT):
            try:
                r = self.client.post(f"/worker/jobs/{self.id}/heartbeat",
                                     json={"worker": NAME, "progress": self.progress})
                if r.status_code == 409:
                    log("job was taken back by the API; abandoning it")
                    self.lost.set()
                    return
            except httpx.HTTPError as e:
                # A blip is not a reason to drop hours of work; the API allows
                # many missed beats before it requeues.
                log(f"heartbeat failed ({type(e).__name__}); will retry")

    def set_progress(self, msg: str) -> None:
        self.progress = msg
        log(f"  {msg}")


def run_one(client: httpx.Client) -> bool:
    """Claim and run one job. False if the queue was empty."""
    r = client.post("/worker/claim", json={"worker": NAME})
    if r.status_code == 204:
        return False
    r.raise_for_status()
    j = Job(client, r.json())
    c = j.job["candidate"]
    log(f"job {j.id}: {c.get('name')} against {j.job['structure']['id']} "
        f"at {j.job['temperature_c']} C (attempt {j.job.get('attempt')})")
    j.thread.start()
    try:
        result = simulate.run(j.job, simulate.Settings.from_env(), j.set_progress, j.lost.is_set)
    except simulate.Abandoned:
        return True
    except Exception as e:
        j.done.set()
        err = f"{type(e).__name__}: {e}"
        log(f"job {j.id} failed: {err}\n{traceback.format_exc()}")
        client.post(f"/worker/jobs/{j.id}/fail", json={"worker": NAME, "error": err[:2000]})
        return True
    j.done.set()
    r = client.post(f"/worker/jobs/{j.id}/result", json={"worker": NAME, "result": result})
    if r.status_code == 409:
        log("finished, but the job is no longer ours; result dropped")
    else:
        r.raise_for_status()
        log(f"job {j.id} done: Gamma23 = {result['gamma23']} +/- {result['gamma23_se']} "
            f"in {result['wall_seconds']:.0f} s")
    return True


def stop() -> None:
    """The queue has been empty for IDLE_STOP_MINUTES: stop paying for the GPU."""
    pod, key = os.environ.get("RUNPOD_POD_ID"), os.environ.get("RUNPOD_API_KEY")
    if pod and key:
        log(f"idle for {IDLE_STOP / 60:g} min; stopping RunPod pod {pod}")
        try:
            r = httpx.post(f"https://rest.runpod.io/v1/pods/{pod}/stop",
                           headers={"Authorization": f"Bearer {key}"}, timeout=60)
            r.raise_for_status()
            # The container is killed from outside; wait for it rather than
            # exit, which RunPod would answer by restarting us.
            time.sleep(600)
        except httpx.HTTPError as e:
            log(f"could not stop the pod ({type(e).__name__}: {e}); "
                "stop it in the RunPod console or it keeps billing")
    else:
        log(f"idle for {IDLE_STOP / 60:g} min; exiting")
    sys.exit(0)


def main() -> None:
    if not TOKEN:
        sys.exit("WORKER_TOKEN is not set")
    once = "--once" in sys.argv
    import openmm
    platforms = [openmm.Platform.getPlatform(i).getName()
                 for i in range(openmm.Platform.getNumPlatforms())]
    log(f"worker {NAME}: OpenMM {openmm.__version__}, platforms {platforms}, API {API_URL}")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    idle_since = time.monotonic()
    with httpx.Client(base_url=API_URL, headers=headers, timeout=120) as client:
        while True:
            try:
                had_job = run_one(client)
            except httpx.HTTPError as e:
                log(f"API unreachable ({type(e).__name__}: {e}); retrying")
                had_job = False
            if once:
                return
            if had_job:
                idle_since = time.monotonic()
            elif IDLE_STOP and time.monotonic() - idle_since >= IDLE_STOP:
                stop()
            else:
                time.sleep(POLL)


if __name__ == "__main__":
    main()
