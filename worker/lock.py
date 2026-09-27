"""Turn a micromamba dry-run solve into conda-linux-64.lock.

    python lock.py solve.json

See environment.yml for the whole procedure. Packages are written in the
solver's link order, which is dependency order: an explicit file installs top
to bottom, so python has to come before anything noarch that needs it.
"""

import json
import sys

HEADER = [
    "# Generated from environment.yml -- do not edit by hand; see there to regenerate.",
    "# platform: linux-64",
    "@EXPLICIT",
]

solve = json.load(open(sys.argv[1]))
packages = solve["actions"]["LINK"]
with open("conda-linux-64.lock", "w", newline="\n") as f:
    f.write("\n".join(HEADER + [f"{p['url']}#{p['md5']}" for p in packages]) + "\n")
print(f"{len(packages)} packages locked")
