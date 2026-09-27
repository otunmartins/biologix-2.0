"""Starting the simulation worker's RunPod pod when there is approved work.

The worker (worker/) runs on a RunPod GPU pod and stops that pod itself once the
queue has been empty for IDLE_STOP_MINUTES. This is the other half: approving a
job starts the pod again, so a GPU is up only while there is work for it.

    RUNPOD_API_KEY   a RunPod API key allowed to start the pod
    RUNPOD_POD_ID    the worker's pod

Either unset and this does nothing: the pod is then started by hand.
"""

import os

import httpx

API = "https://rest.runpod.io/v1"


def configured() -> bool:
    return bool(os.environ.get("RUNPOD_API_KEY") and os.environ.get("RUNPOD_POD_ID"))


def start_pod() -> str:
    """Ask RunPod to start the worker's pod. Never raises: the approval stands
    whether or not this works, and the answer says what to do if it did not."""
    if not configured():
        return "not configured: start the worker's pod by hand"
    pod = os.environ["RUNPOD_POD_ID"]
    try:
        r = httpx.post(f"{API}/pods/{pod}/start", timeout=30,
                       headers={"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"})
    except httpx.HTTPError as e:
        return f"could not reach RunPod ({type(e).__name__}); start pod {pod} by hand"
    if r.is_success:
        return f"starting pod {pod}"
    # Starting a pod that is already running is refused; it will claim the job anyway.
    return f"RunPod answered {r.status_code} for pod {pod}: {r.text[:200]}"
