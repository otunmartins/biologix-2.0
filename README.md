# Excipient Screen — Next.js + FastAPI + Terraform

A triage tool: give it an excipient, a protein sequence, a route, a dose, and a storage
temperature, and it returns a short dossier of per-endpoint verdicts and protein liability flags.
It says what to test next. It never says an excipient is safe.

## Layout

```
web/         Next.js + TypeScript + Tailwind frontend — app/page.tsx, components/,
             lib/api.ts (typed mirror of the Dossier schema)
api/         FastAPI backend — wraps the PydanticAI agent behind /screen
             main.py the agent and tools, accessibility.py solvent accessibility,
             precedent.py the FDA precedent lookup
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

The first screen you run downloads the FDA Inactive Ingredient file (~380 KB) and caches it for
30 days in the system temp directory; set `IID_CACHE_DIR` to put it somewhere durable. `/health`
reports whether that index is loaded, which is the first thing to check if every dossier suddenly
comes back as a data gap.

The precedent lookup also calls openFDA, which without a key allows about 1,000 requests a day
per IP — roughly a hundred excipient lookups. Set `OPENFDA_API_KEY` (free, from
open.fda.gov/apis/authentication) to lift that to 120,000. When a source does fail, the lookup
says so in `lookup_errors` rather than grading the excipient as if nothing were found, failures
are never cached, and a failed IID download is retried after five minutes (falling back to an
expired copy on disk if there is one).

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

This is Stage 0, Stage 1, and a slice of Stage 2 from the design doc. Deliberately:

- **Real:** PubChem identity resolution (name / CAS / SMILES → SMILES + InChIKey), nine curated
  RDKit structural alerts (Michael acceptor, epoxide, aldehyde, peroxide, polyether chain,
  hydrolysable ester, reducing sugar, organomercury, maleimide), a thirteen-row excipient↔residue
  rule table, plus an advisory screen against published filter catalogs (see below).
- **Real:** regulatory precedent, from the FDA Inactive Ingredient Database and openFDA's
  substance registry. See the section below — including what the IID does *not* cover, which
  matters more than what it does.
- **Surrogates:** polysorbates, poloxamers, PEG and PVP have no single PubChem CID and 404 on a
  name lookup, so `SURROGATES` in `api/main.py` maps them to a short repeat-unit stand-in. That
  captures the reactive features but not chain length, polydispersity, or residual monomers —
  anything derived from a surrogate is capped at grade D and labelled in the rationale. Entering a
  polymer properly (repeat unit + end groups + degree of polymerisation) isn't built yet.
- **Simplified:** label mining reads the DESCRIPTION text only — no structured SPL ingredient
  amounts, so label precedent carries no concentrations. No statistical mutagenicity model, no
  exposure-margin/TTC calculation, no polymer repeat-unit handling.
- **Real:** solvent-accessibility weighting. Give the scan a UniProt accession (AlphaFold model) or
  a 4-character PDB ID (RCSB experimental structure) and every liability is weighted by relative
  solvent accessibility, computed with Shrake-Rupley against Tien et al. 2013 reference max-ASA.
  Without an identifier it falls back to raw sequence counts and labels each flag `not modelled`.
  On intact IgG (1IGT) this is the difference between "22 Met" and "22 Met, 5 exposed".
- **Missing:** the Stage 3 OpenMM compatibility screen. See below.

### Where regulatory precedent comes from, and what it isn't

Three free, keyless federal sources, in `api/precedent.py`, because no one of them is enough:

- The **FDA Inactive Ingredient Database** — the quarterly CDER file, ~9,000 rows of
  (ingredient, route, dosage form) that have appeared in an approved drug product, with the
  maximum potency on record. Fetched once, cached 30 days, rebuilt lazily on first use so a slow
  fda.gov can't take the container down at boot. The current download link is scraped off the
  IID landing page (the media id rotates every quarter) with a pinned URL as the fallback.
- **Approved product labels** (openFDA SPL), full text over the DESCRIPTION section, counted by
  distinct FDA application number. **This is the source that covers biologics**, and it is not
  optional: the IID is a CDER database, so polysorbate 80 subcutaneous is *two rows* there and
  *eighty distinct BLAs* here — Trulicity, Keytruda, Aranesp, Orencia. Without it the tool grades
  half the excipients in every approved mAb formulation as "no precedent at this route", which is
  simply false.
- **openFDA's substance registry**, for UNII resolution and for the CFR citations behind a GRAS
  affirmation. The UNII is what makes the IID join trustworthy; `names.name` there is a
  case-**sensitive** exact-string field and the registry's own casing is inconsistent
  (`TREHALOSE` but `Sucrose`), so the lookup tries a ladder of spellings before a guarded
  free-text fallback.

The two precedent sources have opposite failure modes, which is exactly why both are used. The
IID is a curated assertion with a population gap; the label search is a text match over the right
population. Every finding reports which one carried it (`precedent_basis`), and the label source
carries five guards, because full text is the looser instrument:

1. **DESCRIPTION only.** A hypersensitivity warning naming an excipient lives in WARNINGS or
   CONTRAINDICATIONS, not in the composition section.
2. **Not the active ingredient.** Products whose active substance carries the name are excluded
   in the query: iron dextran is not dextran the excipient, iron sucrose is not sucrose, and an
   amino-acid infusion's glycine is the drug.
3. **An ingredient, not a mention.** The matching labels are read, and one counts only if the
   name sits beside an amount (`albumin (human) (2.5 mg)`) or in a sentence stating contents
   (`each mL contains…`). The composition section also *describes* the drug, so a phrase search
   alone counts semaglutide ("the main protraction mechanism … is albumin binding") and a fusion
   protein built on human serum albumin as albumin precedent. `test_smoke.py` pins seven real
   sentences, both ways.
4. **Approved applications only**, counted as *distinct application numbers* rather than label
   documents, so a product with six label revisions counts once.
5. **At least three verified ones** (`MIN_LABEL_APPLICATIONS`). One stray mention is not a
   formulation. Real excipients clear this by an order of magnitude; calcium chloride
   subcutaneous sits at two and is deliberately *not* promoted — `test_smoke.py` pins that
   boundary.

Labels run to ~200 KB each, so they are read ten at a time and reading stops once the threshold
is met (at most fifty). The payload therefore carries two counts: `n_approved_applications`, the
applications that mention the excipient, and `n_verified_as_ingredient`, a *floor* on how many
list it — not a census. Only the verified count can promote a finding to `route_match`. If openFDA
stops answering part-way through, the label source is reported as not checked rather than as
whatever was verified before it failed.

Application numbers are returned in the payload so a reviewer can check any of it.

The lookup returns one of six levels, and the level — not the model's reading of the data — sets
the grade ceiling:

| level | meaning | ceiling |
|---|---|---|
| `route_match` | approved product at this exact route, from either source | A |
| `route_match_above_record` | route match, but the concentration given is above the highest potency the IID records there | B |
| `systemic_injection_match` | approved at another systemic injection route (IV↔SC↔IM) | B |
| `parenteral_match` | approved only at a local/compartmental injection route (intravitreal, intra-articular) | C |
| `other_route_only` | approved only at non-injected routes | C |
| `gras_only` | no drug precedent; a food citation only | C oral / D otherwise |
| `none`, `unavailable` | nothing found in either source, or the lookup failed | D |

**Concentration.** Give the excipient's concentration (`0.02% w/v`, `10 mg/mL`, `5 mg per dose`)
and a route match is checked against the highest potency the IID records at that route, like for
like: `%w/v` against `%w/v` (mg/mL converts by definition), `mg` per dose against `mg`. Above the
record, the grade caps at B. When nothing can be compared — no concentration given, a molar unit,
or precedent that came only from labels, which carry no concentrations — grade A stays available
and the rationale has to say the concentration was not checked. This errs low for biologics: the
IID's maximum under-represents BLA products, so "above the record" means above what the IID
records, not above what has been approved.

Bridging is directional, and both ends have to be systemic. IV precedent supports a subcutaneous
request; it does **not** support an intravitreal one, where the dose goes into a small closed
compartment with its own tolerance and its own failure mode. Thimerosal is in approved IM and IV
products and still caps at C for an intravitreal request.

**Limits that decide how much a hit is worth.** All are returned in the payload so they land in
the rationale rather than being lost:

1. **The two sources have opposite blind spots.** The IID under-represents BLA biologics; the
   label search is full text and weaker per hit. A finding says which one carried it, and a miss
   in both means "not found", never "never used".
2. **A route is not a dose, and never a protein.** A hit says the excipient has been in an
   approved product at or below the potency shown. It is not clearance at the user's
   concentration and says nothing about this molecule.
3. **GRAS is a food determination.** 21 CFR 182/184/186 covers ingestion. Acrylamide carries a
   21 CFR 173 citation; without the route rule that would have come back as grade C for a
   subcutaneous injection, which is exactly the borrowed authority this tool exists to prevent.

**Name matching is the whole game, and it is not a prefix comparison.** Excipient names are
matched to the IID by UNII **and** by name, unioned. The UNII join is exact but misses hydrate
forms filed under their own code — trehalose dihydrate is what's actually in the approved
products and does not share a UNII with trehalose. The name join catches those, comparing names
as *sets of significant words*, because the IID's own naming is inconsistent in every direction:

| you type | the IID files it as | what that needs |
|---|---|---|
| trehalose | `TREHALOSE DIHYDRATE` | a hydrate suffix |
| PEG 3350 | `POLYETHYLENE GLYCOL 3350` | a trade-name alias, then a grade number |
| sodium phosphate | `SODIUM PHOSPHATE, DIBASIC, DIHYDRATE` | a comma, then two qualifiers |
| potassium phosphate | `MONOBASIC POTASSIUM PHOSPHATE` | the same qualifier, in front |
| disodium edetate | `EDETATE DISODIUM` | reversed word order |
| Captisol | `BETADEX SULFOBUTYL ETHER SODIUM` | a brand alias |

Two names match when their significant words are equal, or when one set contains the other and
every extra word is a qualifier — a grade number, a hydrate, a salt, a basicity. Both halves of
that rule are load-bearing. Without the qualifier restriction, `SUCROSE` reaches
`SUCROSE STEARATE` and `SUCROSE PALMITATE`, which are surfactants, and folding their rows into
sucrose's would invent precedent out of nothing. Without the containment requirement,
`SODIUM CHLORIDE` reaches `POTASSIUM CHLORIDE` — their difference is a qualifier word in both
directions. `test_smoke.py` pins sixteen of these cases, over-matching included.

**Measured coverage:** across a 52-excipient panel — stabilisers, bulking agents, buffers,
surfactants, preservatives, chelators, cyclodextrins and cosolvents — 51 resolve to IID rows and
51 resolve a UNII. `test_breadth.py` asserts a precedent *floor* for 27 (excipient, route) pairs
across five routes rather than an exact level, so the suite doesn't break the next time FDA adds
a row, only when precedent is lost.

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

- **Grade A and `Precedented` require a precedent lookup that actually returned one.** The model has
  read the literature in training and will happily assert precedent from memory for the excipients
  it knows well. The agent's output validator checks the recorded result of the `regulatory_precedent`
  tool *for this run* — not the dossier's account of it — and unless that result was
  `route_match`, a grade A or a `Precedented` verdict is handed straight back as a retry. Before
  Stage 1 this was a blanket ban, since nothing could see precedent; the rule sharpened rather
  than relaxed when the lookup landed. Setting `PRECEDENT_LOOKUP_AVAILABLE = False` in
  `api/main.py` restores the blanket ban, which is what you want if the lookup is ever found to
  be misreporting.
- **`needs_testing` is derived, not reported** — recomputed from the grades and severities on every
  dossier, so the model can't forget to set it.

## Where GPU and OpenMM fit in later

This instance is provisioned with a GPU, but nothing in `api/` uses it yet — the current pipeline
is entirely CPU-bound and runs in seconds. When you add the Stage 3 compatibility simulation:

1. Add a `worker` service to `docker-compose.yml` (separate image — OpenMM needs conda/micromamba,
   not pip, unlike everything in `api/` today) with `deploy.resources` reserving the GPU.
2. Add a `run_compatibility_simulation` tool to the agent, called only when the user explicitly
   asks — never automatically, since it's the expensive, slow step (hours, not seconds).
3. Gate it behind a button in `web/app/page.tsx` showing an estimated runtime before it starts, and
   return a job id rather than blocking the request.

The GPU is already there and paid for once you deploy; that work is additive, not a redeploy.

## Not a safety assessment

Chemistry-only triage on structural alerts and a small rule table. A human checkpoint is required
before anything here goes into a dossier.
