# Excipient Screen — Next.js + FastAPI + Terraform

A triage tool: give it an excipient, a protein sequence, a route, a dose, and a storage
temperature, and it returns a short dossier of per-endpoint verdicts and protein liability flags.
It says what to test next. It never says an excipient is safe.

## Layout

```
web/         Next.js frontend — one page (app/page.js), form or natural language
api/         FastAPI backend — wraps the PydanticAI agent behind /screen
terraform/   Provisions one EC2 instance to run it all
docker-compose.yml, Caddyfile   The whole stack on that one instance
```

## Run it locally first (no Docker, no AWS)

Two terminals:

```bash
# terminal 1 — api
cd api
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn main:app --reload --port 8000

# terminal 2 — web
cd web
npm install
npm run dev
```

Open http://localhost:3000. The frontend defaults to `http://localhost:8000` for the API when
`NEXT_PUBLIC_API_URL` isn't set, so this works with no extra config.

## Deploy to AWS

Defaults to a `t3.large` on the current Ubuntu 22.04 AMI — no AMI to look up, and no GPU quota
request to wait on. Nothing in `api/` touches a GPU yet, so this runs the identical stack for
roughly a tenth of a g5's cost. See the GPU section below for when that changes.

**1. Credentials and a key pair** (one time):

```bash
aws configure                 # access key, secret, default region
aws ec2 create-key-pair --key-name excipient-screen \
  --query KeyMaterial --output text > excipient-screen.pem
curl -s ifconfig.me           # your public IP, for the ssh_cidr below
```

**2. Apply:**

```bash
cd terraform
terraform init
terraform apply -var="key_name=excipient-screen" -var="ssh_cidr=YOUR_IP/32"
```

This prints a public IP. It's an Elastic IP, so it survives a stop/start of the instance.

**3. Get the code on the box and bring it up:**

```bash
ssh -i excipient-screen.pem ubuntu@<the-ip>
git clone <your-repo> && cd <your-repo>   # or scp the directory up
cp .env.example .env                       # set ANTHROPIC_API_KEY and put <the-ip> in the last two vars
docker compose up -d --build               # first build is slow: RDKit + a Next.js build
```

Open `http://<the-ip>`. That's plain HTTP — fine for testing, but don't put anything confidential
through it. For HTTPS, point a domain's A record at the IP, set `SITE_ADDRESS`, `WEB_ORIGIN` and
`NEXT_PUBLIC_API_URL` to that hostname (see `.env.example`), and re-run
`docker compose up -d --build`. Caddy gets the certificate on its own.

Two things that bite here:

- `NEXT_PUBLIC_API_URL` is baked into the frontend bundle at **build** time, so changing it in
  `.env` needs a rebuild (`--build`), not just a restart.
- The frontend, API, and Caddy's reverse proxy all share one origin in the deployed stack, so
  there's no CORS issue in production. The CORS middleware in `api/main.py` is there for local dev.

**When you move to a GPU instance later:** request the quota increase first — new AWS accounts
default to a vCPU limit of 0 for the G/VT families, so `terraform apply` fails outright on a fresh
account. Service Quotas → EC2 → "Running On-Demand G and VT instances" → request at least 4 vCPUs
for a g5.xlarge. Approval takes anywhere from minutes to a couple of days. Then apply with
`-var="instance_type=g5.xlarge" -var="ami_id=<a Deep Learning AMI>"`.

**Before relying on GPU passthrough for anything (OpenMM later):** confirm it actually works —
`docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi` should print your GPU.
The Deep Learning AMI usually has the NVIDIA driver and container toolkit preinstalled, but check
before you build anything that depends on it.

## What's real vs. simplified right now

This is Stage 0 + a slice of Stage 2 from the design doc. Deliberately:

- **Real:** PubChem identity resolution (name / CAS / SMILES → SMILES + InChIKey), nine curated
  RDKit structural alerts (Michael acceptor, epoxide, aldehyde, peroxide, polyether chain,
  hydrolysable ester, reducing sugar, organomercury, maleimide), a thirteen-row excipient↔residue
  rule table, plus an advisory screen against published filter catalogs (see below).
- **Surrogates:** polysorbates, poloxamers, PEG and PVP have no single PubChem CID and 404 on a
  name lookup, so `SURROGATES` in `api/main.py` maps them to a short repeat-unit stand-in. That
  captures the reactive features but not chain length, polydispersity, or residual monomers —
  anything derived from a surrogate is capped at grade D and labelled in the rationale. Entering a
  polymer properly (repeat unit + end groups + degree of polymerisation) isn't built yet.
- **Simplified:** no regulatory precedent lookup at all — no FDA IID, GRAS inventory, DailyMed, or
  UNII resolution — which is why the agent is instructed never to award a grade A. No statistical
  mutagenicity model, no exposure-margin/TTC calculation, no polymer repeat-unit handling.
- **Real:** solvent-accessibility weighting. Give the scan a UniProt accession (AlphaFold model) or
  a 4-character PDB ID (RCSB experimental structure) and every liability is weighted by relative
  solvent accessibility, computed with Shrake-Rupley against Tien et al. 2013 reference max-ASA.
  Without an identifier it falls back to raw sequence counts and labels each flag `not modelled`.
  On intact IgG (1IGT) this is the difference between "22 Met" and "22 Met, 5 exposed".
- **Missing:** the Stage 3 OpenMM compatibility screen. See below.

### Why only two of RDKit's published alert catalogs are enabled

RDKit ships eleven published filter sets. Most of them are actively harmful here. They were built
to triage drug-discovery **screening libraries**; excipients are a different population — small,
polar, polyol-rich, often long-chain surfactants. Measured false-positive rate on a 19-excipient
benign panel (sucrose, trehalose, mannitol, glycine, histidine, polysorbates, PEG, …):

| catalog | FP rate | |
|---|---|---|
| CHEMBL_SureChEMBL | 5% | **enabled** |
| CHEMBL_Inpharmatica | 11% | **enabled** |
| ZINC / CHEMBL_Glaxo | 16% | rejected |
| NIH / CHEMBL_BMS | 26% | flag **sucrose and trehalose** (`gte_7_aliphatic_OH`) |
| BRENK / CHEMBL_Dundee | 32% | flag polysorbate and arginine (`Aliphatic_long_chain`) |
| CHEMBL_LINT | 47% | flags every amino-acid excipient for being an amino acid |
| CHEMBL_MLSMR | 63% | flags sucrose and trehalose as acetals |
| PAINS | 0% | no hits either way — assay interference, not reactivity |

Flagging sucrose and trehalose, the two canonical protein stabilisers, is worse than having no
screen. The rejected catalogs stay out; `ADVISORY_CATALOGS` in `api/main.py` records why, and
`test_smoke.py` fails if a noisy one is added back.

What the two enabled catalogs buy: benzalkonium chloride (`benzylic_quaternary_nitrogen`) and
sucralose (`alkyl_halides`) — classes the curated table doesn't cover. Their hits are reported as
a **separate advisory endpoint at grade D**. They are not protein-specific, they never become a
liability flag, and they never raise a severity on their own.

### What accessibility weighting does and doesn't tell you

A buried residue gets its severity **downgraded one step, never deleted**. RSA comes from one
static structure, which says nothing about thermal breathing, partial unfolding, or what happens
to the protein at the air-liquid interface during shaking — all of which expose residues the
model calls buried. An AlphaFold model is also a prediction: sites in regions below pLDDT 70 are
reported as low-confidence rather than trusted.

No alignment is attempted between a pasted sequence and the fetched structure. If both are given
and they differ, the tool says so and reports accessibility for the structure; it does not quietly
pick one. For an antibody, prefer a Fab or Fv structure over an intact IgG.

The four verdicts (`Precedented`, `Supported without precedent`, `Data gap: test`, `Alert: avoid`)
and the A–E grades are enforced by the Pydantic schema in `api/main.py`, so the model can't
invent a fifth verdict or hand back a bare score.

Two things are enforced in code rather than by prompt, because a prompt rule is not a guarantee:

- **No grade A, no `Precedented`** while `PRECEDENT_LOOKUP_AVAILABLE` is `False`. The model has read
  the literature in training and will happily assert precedent from memory; the `Dossier` validator
  rejects it and PydanticAI hands the error back for a retry. Flip the flag when Stage 1 lands.
- **`needs_testing` is derived, not reported** — recomputed from the grades and severities on every
  dossier, so the model can't forget to set it.

## Where GPU and OpenMM fit in later

This instance is provisioned with a GPU, but nothing in `api/` uses it yet — the current pipeline
is entirely CPU-bound and runs in seconds. When you add the Stage 3 compatibility simulation:

1. Add a `worker` service to `docker-compose.yml` (separate image — OpenMM needs conda/micromamba,
   not pip, unlike everything in `api/` today) with `deploy.resources` reserving the GPU.
2. Add a `run_compatibility_simulation` tool to the agent, called only when the user explicitly
   asks — never automatically, since it's the expensive, slow step (hours, not seconds).
3. Gate it behind a button in `web/app/page.js` showing an estimated runtime before it starts, and
   return a job id rather than blocking the request.

The GPU is already there and paid for once you deploy; that work is additive, not a redeploy.

## Not a safety assessment

Chemistry-only triage on structural alerts and a small rule table. A human checkpoint is required
before anything here goes into a dossier.
