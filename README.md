# Biologix 2.0 — Next.js + FastAPI + Terraform

By [Algonix AI](#). Excipient triage, polymer design and molecular dynamics for biologic formulations.

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
docker-compose.yml, caddy/   The whole stack on that one instance (caddy/Caddyfile routes it)
```

## Run it locally first (no Docker, no AWS)

Two terminals:

```bash
# terminal 1 — api
cd api
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
export DATABASE_URL=postgresql://biologix:devpass@localhost:5432/biologix
uvicorn main:app --reload --port 8000

# terminal 2 — web
cd web
npm install
npm run dev
```

The web app needs its own `web/.env.local` for sign-in (see [Sign-in](#sign-in) for where the
Google values come from):

```bash
AUTH_SECRET=<openssl rand -base64 32>
AUTH_GOOGLE_ID=...
AUTH_GOOGLE_SECRET=...
DATABASE_URL=postgresql://biologix:devpass@localhost:5432/biologix
```

Open http://localhost:3000 and sign in with Google. The frontend defaults to
`http://localhost:8000` for the API when `NEXT_PUBLIC_API_URL` isn't set.

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

## Sign-in

Two ways in, on one page: **Google** (Auth.js, `web/auth.ts`) and **email and password**
(`web/lib/password-auth.ts`). Anyone can sign up either way; each user sees only their own
campaigns and queue. `/health` is the only thing open without signing in.

Sessions are rows in the `sessions` table of the same Postgres the API uses. The browser holds
only a random token, and the API checks it against that table on every request
(`api/users.py`), so signing out takes effect everywhere at once. The API creates the
sign-in tables when it starts.

Password sign-in is not an Auth.js provider: Auth.js's credentials provider always issues a JWT
cookie, which the API cannot check. Instead it verifies the password (scrypt, from Node's
crypto) and writes the same session row and cookie a Google sign-in gets, so nothing
downstream can tell them apart. Wrong guesses are limited per email and per address, and an
unknown email gets the same answer, in the same time, as a wrong password.

**There is no email verification yet** (no mail service), so a password account does not prove
its owner has that inbox. Two rules follow: an email that already has an account cannot be
registered again, so nobody can put a password on your Google account; and signing in with
Google to an email that has a password links to that account and **removes the password**,
because Google has proved who owns the address and the password may have been set by someone
else. There is no "forgot password" either: if the address is also a Google account, signing in with
Google gets you back in; otherwise the password has to be reset by hand in the database.
Both need a mail service (e.g. Resend) to lift.

**Google credentials** (one time, about five minutes):

1. [Google Cloud Console](https://console.cloud.google.com) → create a project (or pick one).
2. **APIs & Services → OAuth consent screen**: External, app name, your email. Add the
   `email` and `profile` scopes. Publish it when you're ready for people outside your test users.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID** → Web application.
   Authorised redirect URIs, one for each place the app runs:
   - `https://<your-domain>/api/auth/callback/google`
   - `http://localhost:3000/api/auth/callback/google` (local dev)
4. Copy the client ID and secret into `AUTH_GOOGLE_ID` and `AUTH_GOOGLE_SECRET`.

The callback Auth.js sends Google is built from `WEB_ORIGIN` (passed to the web container as
`AUTH_URL`). If it doesn't exactly match a redirect URI above, sign-in fails with
`redirect_uri_mismatch`.

## History and provenance

Every screen and design campaign is kept in the user's **History** tab, and each record says how
the result was produced (`api/history.py`):

- **Screens**: the prompt and form exactly as sent, the dossier, the model, the build
  (`APP_VERSION`, the git commit the deploy stamps), timings and token use, the order the tools
  ran in, and what each data source returned during that run: the PubChem resolution or
  stand-in structure, the FDA Inactive Ingredient rows, the label counts with application
  numbers, and any lookup errors. Failed runs are kept as failed.
- **Campaigns**: an append-only event log of started (with the words typed and the model that
  read the goal from them), each iteration's metrics, candidates queued, ended, and reopened.

Every screen also records **where it came from** (`screen_run.origin`): the Screen tab, or a
designed candidate handed over with **Screen this candidate** (with that candidate's id). The two
are different questions, so they are reported apart: a designed candidate's screen counts as
polymer-design work in Results and on the admin dashboard, never as excipient screening, and
History labels it. Screens saved before the field existed were sorted once, by name: a designed
candidate is screened under its own generated name.

Nothing in history is edited or deleted. **New experiment** ends the campaign on screen (an
event, and `ended_at`); continuing it from History reopens it. If a screen's record cannot be
written, the user still gets the dossier, with a notice that it was not saved.

Structures are drawn by the API's own RDKit (`GET /structure.svg`), so the picture is of exactly
what was screened. That endpoint is public: a drawing reveals nothing about any user, and a session
check per thumbnail would cost a database round trip. It is size-limited, cached, and rate-limited
per address instead.

## Deploy

Production is one small Ubuntu server running Docker Compose: Caddy (HTTPS), the web app and the
API. Today that is a DigitalOcean droplet (2 vCPU, 2 GB, New York, next to the Neon database in
AWS us-east-2) at **https://studio.biologix.app**. Any Ubuntu 24.04 box with SSH works the same.
Simulations need a GPU and run elsewhere (see "OpenMM simulations" below).

The server never compiles anything. On every merge to `main`, GitHub Actions runs the tests,
builds the API and web images for that commit, pushes them to GHCR
(`ghcr.io/otunmartins/biologix-api` and `-web`, tagged with the commit), then SSHes in, checks out
that commit, pulls those two images and restarts. It checks the result and rolls back to the
previous commit's images if the new ones fail.

**1. The server** (one time). Ubuntu 24.04, your SSH key and the deploy key added at creation. As
root: updates, 2 GB of swap, `ufw` allowing only OpenSSH, 80 and 443, Docker from Docker's own apt
repository with log rotation (`/etc/docker/daemon.json`: json-file, 10 MB × 3), and a `deploy` user
in the `docker` group (no sudo) holding the same `authorized_keys`. Deploys log in as `deploy`.

**2. DNS.** An A record for the site's name (`studio` under `biologix.app`) pointing at the server.
`.app` domains are HTTPS-only in every browser; Caddy gets the certificate on its own.

**3. The code and `.env` on the box**, as `deploy`:

```bash
git clone https://github.com/otunmartins/biologix-2.0.git ~/biologix-2.0
cd ~/biologix-2.0 && cp .env.example .env   # then fill it in, below
```

In `.env`: `ANTHROPIC_API_KEY`, Neon's **pooled** production `DATABASE_URL`, the sign-in values
(`AUTH_SECRET` — its own, not your laptop's — `AUTH_GOOGLE_ID`, `AUTH_GOOGLE_SECRET`),
`ADMIN_EMAILS`, `WORKER_TOKEN`, `RUNPOD_API_KEY`, `RUNPOD_POD_ID`, and the address:
`SITE_ADDRESS=studio.biologix.app`, `WEB_ORIGIN=https://studio.biologix.app`. In the Google Cloud
console, add `https://studio.biologix.app/api/auth/callback/google` to the OAuth client's
authorised redirect URIs.

**4. Turn on deploys from `main`:**

```bash
gh secret set EC2_HOST --body <the-ip>
gh secret set EC2_USER --body deploy
gh secret set EC2_APP_DIR --body /home/deploy/biologix-2.0
gh secret set EC2_SSH_KEY < ~/.ssh/excipient-screen
gh secret set EC2_HOST_KEY --body "$(ssh-keyscan <the-ip> 2>/dev/null)"
gh variable set PUBLIC_API_URL --body https://studio.biologix.app/_api
gh variable set SITE_URL --body https://studio.biologix.app
gh variable set DEPLOY_ENABLED --body true
```

(The secrets keep their `EC2_` names from when the box was on AWS; they mean any server.)

From then on every merge to `main` runs the tests, builds, deploys, and checks the result: the
API's `/health` on the box, then `SITE_URL` from outside, including that Google will call back to
that address. If the new build fails any of it, the box rolls back to the previous one.

Things that bite here:

- The API lives under **`/_api`** in production (caddy/Caddyfile): the app's pages `/screen`, `/design`
  and `/history` share names with API routes. `PUBLIC_API_URL` must end in `/_api`; the image
  build refuses anything else. `/health` and `/worker*` stay at the root for the deploy check and
  the GPU worker.
- `PUBLIC_API_URL` is baked into the web image at build time: changing it needs a new build (re-run
  the Deploy workflow), not a restart.
- The frontend, API and Caddy share one origin in production, so there's no CORS there. The CORS
  middleware in `api/main.py` is for local dev.
- By hand on the box, `docker compose up -d` runs the `latest` images; the deploy pins the exact
  commit with `IMAGE_TAG`. `docker compose up --build` still builds locally if you ever need to.

**AWS instead.** `terraform/` creates an EC2 box for the same setup. This account's EC2 and
Lightsail quotas were too small, which is why production is on DigitalOcean.

**No GPU here.** This box never runs simulations: the OpenMM worker runs on a RunPod GPU pod and
pulls its work from this box over HTTPS (see "OpenMM simulations" below).

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
  anything derived from a surrogate is capped at grade D and labelled in the rationale.
- **Real:** described polymers. Give `/screen` a repeat unit, end groups, an approximate DP and
  any residual impurities, and it screens that chemistry instead of the surrogate — see below.
- **Real:** impurity exposure margins. A residual impurity's specification level becomes µg per
  dose and is compared with the ICH M7 acceptable intake for the treatment duration — see below.
- **Simplified:** label mining reads the DESCRIPTION text only — no structured SPL ingredient
  amounts, so label precedent carries no concentrations. No statistical mutagenicity model.
  Exposure margins cover residual impurities only, against a generic benchmark, never the
  excipient itself. Polymers are linear chains of one repeat unit: no polydispersity, branching
  or block copolymers.
- **Real:** solvent-accessibility weighting. Give the scan a UniProt accession (AlphaFold model) or
  a 4-character PDB ID (RCSB experimental structure) and every liability is weighted by relative
  solvent accessibility, computed with Shrake-Rupley against Tien et al. 2013 reference max-ASA.
  Without an identifier it falls back to raw sequence counts and labels each flag `not modelled`.
  On intact IgG (1IGT) this is the difference between "22 Met" and "22 Met, 5 exposed".
- **Real, needs a GPU to be useful:** the Stage 3 OpenMM compatibility simulation of queued
  design candidates. See "OpenMM simulations" below.

### Describing a polymer

`POST /screen` takes an optional `polymer` object alongside the prompt (`api/polymer.py`):

```json
{
  "prompt": "Screen excipient 'Polysorbate 80' ...",
  "polymer": {
    "repeat_unit": "[*]CCO[*]",
    "end_group_a": "[*]OC(=O)CCCCCCCC=CCCCCCCCC",
    "end_group_b": "[*][H]",
    "dp": 20,
    "impurities": [{ "name": "ethylene oxide", "level": "<= 1 ppm" }]
  }
}
```

SMILES use `[*]` for attachment points: two on the repeat unit (head first), one on each end
group. A malformed one is a 422 at the door, not a failed agent run. The screen builds a
representative chain of at most ten repeat units — structural alerts are substructure-presence
tests, so a longer chain fires the same ones — and computes Mn from the full DP (PEG 3350 at DP
75.4 comes out at 3340).

What a description buys, and where it stops:

- **Ceiling C instead of D** on structure-derived endpoints: an in-domain prediction on the
  chemistry the user actually has, never A or B.
- **A description can refine the surrogate, never quietly drop one of its alerts.** Describe
  "Polysorbate 80" with methoxy caps and the ester alert the real material fires disappears; that
  is reported as a description conflict and the ceiling stays at D, so a wrong description can't
  make an excipient look cleaner than the stand-in did.
- **Residual impurities are screened as their own molecules**, resolved through PubChem. Residual
  ethylene oxide trips the epoxide alert and adds a His alkylation flag tagged
  `from residual ethylene oxide (<= 1 ppm)`. The level is quoted and feeds the exposure margin
  below; it never scales a severity.
- **Architecture is linear.** A polysorbate's four sorbitan arms are written as one chain with a
  sorbitan-ester end group, which carries the same reactive groups but not the same shape.
  Poloxamers (block copolymers) and the cellulosics have no linear preset and stay on the surrogate
  unless described by hand.

### Impurity exposure margins

A level of "≤ 1 ppm" is a specification, not an exposure. With an `exposure` object alongside a
polymer's impurities, `api/exposure.py` makes it one, from the request alone and before the agent
runs, so the numbers never pass through the model:

```json
"exposure": {
  "excipient_concentration": "0.02% w/v",
  "dose_volume_ml": 1,
  "dosing_interval_days": 14,
  "treatment_duration_days": 365
}
```

Excipient per dose (0.02 %w/v × 1 mL = 0.2 mg) × level (1 µg/g) = 0.0002 µg ethylene oxide per
dose, against the ICH M7 Table 2 acceptable intake for an individual mutagenic impurity:

| dosing days | acceptable intake |
|---|---|
| ≤ 30 (≤ 1 month) | 120 µg/day |
| ≤ 365 (> 1–12 months) | 20 µg/day |
| ≤ 3650 (> 1–10 years) | 10 µg/day |
| more, or duration not given | 1.5 µg/day |

The duration category follows ICH M7's rule for intermittent dosing: it is set by the number of
*dosing days*, not the calendar span, so every two weeks for a year is 27 dosing days and the
≤ 1 month tier. That is the case that matters for biologics. Levels parse as ppm, ppb, µg/g,
mg/kg or % w/w; a peroxide value in meq/kg is refused rather than guessed, since it is not a mass
fraction. Anything that can't be computed is reported with its reason, never as zero.

What it is not, and says so in every result:

- **A benchmark, not a limit.** ICH M7 formally excludes biotechnological products and covers
  only DNA-reactive impurities. It is used here because it is the most conservative generic limit
  there is.
- **Compound-specific limits are not applied.** Where the M7 addendum or a published PDE gives
  one, it supersedes these tiers; none are built in.
- **"Above" means the specification allows more than the benchmark**, not that a lot contains it.
  The verdict for such an impurity is `Data gap: test`: lot data or a compound-specific
  assessment is needed.
- **One impurity at a time**, and residual impurities only — never the excipient itself.

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

**The smoke test never goes online.** `test_smoke.py` pins real answers from PubChem, openFDA, the
FDA Inactive Ingredient file, RCSB and AlphaFold, but replays them from
`api/testdata/http_cassette.json.gz` (`api/http_replay.py`), so an outage at any of them cannot
fail CI or block a deploy, and every run sees the same data. When the code starts asking for
something new, the missing request is named on stderr; re-record with
`HTTP_RECORD=1 python test_smoke.py` (the recording is saved only if every check passes). To see
whether live FDA data has drifted, run `test_breadth.py`, which stays live on purpose.

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

Three things are enforced in code rather than by prompt, because a prompt rule is not a guarantee:

- **Grade A and `Precedented` require a precedent lookup that actually returned one.** The model has
  read the literature in training and will happily assert precedent from memory for the excipients
  it knows well. The agent's output validator checks the recorded result of the `regulatory_precedent`
  tool *for this run* — not the dossier's account of it — and unless that result was
  `route_match`, a grade A or a `Precedented` verdict is handed straight back as a retry. Before
  Stage 1 this was a blanket ban, since nothing could see precedent; the rule sharpened rather
  than relaxed when the lookup landed. Setting `PRECEDENT_LOOKUP_AVAILABLE = False` in
  `api/main.py` restores the blanket ban, which is what you want if the lookup is ever found to
  be misreporting.
- **The structure sets a grade ceiling.** `resolve_identity` records what it actually screened —
  a PubChem structure (no ceiling), a described polymer (C, or D on a description conflict), a
  surrogate (D) or nothing (E) — and the output validator bounces any endpoint graded above the
  strictest ceiling of the run. Only the endpoint named `Regulatory precedent, <route>` is exempt,
  because precedent is a name lookup. The same record fills the dossier's `structure_basis`, so
  the frontend reports what was screened from the tool, not from the model. Before this the D cap
  on surrogates was a prompt instruction, and a route match could license grade A on a structural
  endpoint of a 3-mer stand-in.
- **`needs_testing` is derived, not reported** — recomputed from the grades and severities on every
  dossier, so the model can't forget to set it.

## OpenMM simulations (Stage 3)

Queued design candidates are simulated against **the user's own biologic** by a separate worker
(`worker/`), and the result comes back onto the candidate card, into the campaign's history, and
into the measurement store as a `source='simulation'` observation.

**What is computed.** Gamma23, the preferential interaction coefficient of the polymer with the
native protein (two-domain Kirkwood-Buff method, `worker/analysis.py`). Several copies of the
candidate's screened oligomer are put in a water box with the protein and simulated at the
temperature the goal says the biologic must survive. Negative Gamma23 means the polymer is excluded
from the surface and the protein is preferentially hydrated, the signature of sucrose-like
stabilisers. Positive means it accumulates at the surface, and the residues it touches are listed.
It is **not an m-value**: that needs the unfolded state as well. A result is still grade D until an
experiment agrees.

**The system** (`worker/simulate.py`): the structure from RCSB or AlphaFold (low-confidence
AlphaFold termini trimmed, internal gaps filled by PDBFixer), Amber ff14SB for the protein, OpenFF
Sage 2.2 with NAGL AM1-BCC charges for the polymer, TIP3P water, at least 4 polymer chains, C-alpha
atoms restrained to hold the native fold, 4 fs with hydrogen mass repartitioning, 1 ns
equilibration and 20 ns production by default.

**The conditions follow the request** (`api/conditions.py`). Temperature, pH, salt and polymer
concentration are read from what the user wrote ("40 °C", "pH 5.5", "in PBS", "2% polymer") and
the worker runs at them: the thermostat, the protein's protonation, the NaCl added and the chains
packed. Anything unstated takes a default (25 °C, pH 7, 150 mM NaCl, 5% w/v) and is shown as a
default on the card before the run. A value beyond what a simulation can take is held to the limit
(0 to 95 °C, pH 2 to 12, up to 1 M salt, 0.5 to 20% w/v) with a note, and shelf life, route and a
freeze-dried form are noted as not simulated. The worker reports what it applied
(`SimulationResult.conditions`), and that, not the request, is what the measurement records.

**The target structure** (`api/structures.py`). Name the biologic in any words ("adalimumab",
"Humira, a TNF antibody", "human insulin") and the API finds its structure: an ID in the text wins;
otherwise the PDB entries whose every protein chain is that biologic (not bound to its target, not a
relative such as its receptor, not a hybrid), unmodified and human first, then the smallest; otherwise
the reviewed UniProt entry, run as its AlphaFold model. Nothing found means the user is asked for an
ID, never a stand-in. The queue box shows the pick, why, and alternatives, and the campaign remembers
it. A crystal holding several copies (4NYL, the adalimumab Fab, has four) is sent to the worker as one
copy when the whole is over the 1,500-residue cap.

**How the worker runs.** It pulls jobs from the API over HTTP (`/worker/*`), authenticated by
`WORKER_TOKEN`, so it needs no database credentials and can run on this box or anywhere else that
can reach the API. It heartbeats every minute; a job whose worker goes quiet for 20 minutes is
requeued, and failed after three attempts. Jobs run highest triage score first.

**Spending controls.** Sign-up is open and each run is hours of paid GPU time, so queueing is a
request, not a run:

- **An admin approves every run.** A queued job waits as "Waiting for approval" and the worker
  cannot claim it. Admins are the emails in `ADMIN_EMAILS` on the server; they get an
  **Approvals** tab listing every user's waiting and running jobs, with Approve and Decline. A
  declined job shows the user the reason and can be queued again. Queuing again always needs a
  fresh approval.
- **One at a time.** A user can have one simulation waiting or running; queueing another is
  refused with the name of the one in the way. Admins are exempt, since they approve their own.
- **GPU run or CPU preview, chosen at approval.** A full GPU run is 20 ns on the RunPod GPU.
  When no GPU is available, approve it as a **CPU preview** instead: the API sends a short run
  (`SIM_CPU_PREVIEW_NS`, default 1 ns) to a CPU worker. A preview is not converged, is labelled
  as such everywhere, and is never written to the measurement store, whatever the worker reports.
  Each worker states its OpenMM platform when it asks for work and only gets jobs of its kind.
- **The GPU runs only while there is approved work.** Approving starts the RunPod pod (when the
  API has `RUNPOD_API_KEY` and `RUNPOD_POD_ID`), and the worker stops it after
  `IDLE_STOP_MINUTES` with nothing to claim.

**The admin screen, `/admin`.** For the app's owner (the emails in `ADMIN_EMAILS`, which both
the API and the web server read). Anyone else gets a 404 from the server, not a hidden tab; the
API checks every admin request on its own as well. It has two parts:

- **Dashboard.** Platform-wide numbers for tracking growth: polymers designed, excipient
  screens, campaigns, simulations, users (dated by first activity) and model tokens, each with
  its trend against the previous period; daily activity and **events per day by kind** (screens,
  campaigns started and iterated, simulations sent, approved, run and failed); screen outcomes,
  evidence grades and coverage, liabilities by severity and routes; the designer's pipeline,
  backbones, campaign goals and depth (candidates and iterations per campaign), and designed
  candidates screened in full; simulation states and the Γ23 distribution with its
  two-standard-error calls. **Counts only**: `api/stats.py` aggregates over the app's own
  vocabulary and never selects an excipient, protein, prompt, structure or email, and the smoke
  test checks the whole response for them.
- **Approvals.** Every user's waiting, approved and running simulations. Approve as a GPU run
  or a CPU preview, switch an approved job's kind before a worker takes it, deny it, or stop it
  while it runs (the worker drops it at its next heartbeat). Shows which workers are online.

**Following them: the Results tab.** Each user sees only their own work (`api/results.py`), as
two separate reports:

- **Excipient screening**: their Screen-tab runs, with **evidence coverage** (the share of a
  dossier's endpoints that are Precedented or Supported: how much is known, not how safe), the
  worst verdict and evidence grade of each, liabilities, and every excipient ranked by coverage.
- **Polymer design**: the candidates their campaigns generated, per-campaign triage summaries
  (never pooled: the score is only comparable within a campaign), the candidates screened in
  full, and their **simulations**: a Γ23 forest plot with ±2 SE bars and the candidate card's
  call (excluded, accumulates, no clear preference), then every run, each of which opens with
  its result and its own slice of history: queued, approved (and as what), each attempt, and
  how it ended.

**The 3D view.** Every candidate can be seen in 3D (3Dmol.js, loaded on demand). Before any
simulation, it is one RDKit conformer of the screened chain set *beside* the biologic's ribbon,
labelled as an illustration. After a run, it is the run's last frame: the worker saves protein and
polymer heavy atoms, each chain at its periodic image nearest the protein (`worker/snapshot.py`),
the API keeps it in its own table and serves it only to the run's owner, and the residues the
polymer touched most are picked out. Structures come through the API behind sign-in
(`/structure/model/<id>`), first model only, so an NMR entry is not drawn as a stack.

**A CPU worker on your own machine**, for previews with no GPU. It reuses the worker image and
reaches the API on the host:

```bash
docker run -d --name biologix-cpu-worker --restart unless-stopped \
  -e API_URL=http://host.docker.internal:8000 -e WORKER_TOKEN=<the API's token> \
  -e WORKER_NAME=local-cpu ghcr.io/otunmartins/biologix-worker:latest
docker logs -f biologix-cpu-worker     # "running on CPU (takes CPU previews)"
```

On the server, the compose `worker` profile does the same (`COMPOSE_PROFILES=worker`).

**Turning it on: a RunPod GPU pod.** The EC2 box has no GPU (AWS's G-instance quota is not
available on this account), so the worker runs on RunPod and reaches the API through its public
URL; Caddy routes `/worker*` to the API. The app must already be deployed on its domain with HTTPS.

1. **A token and an admin.** On the server, add `WORKER_TOKEN=<openssl rand -hex 32>` and
   `ADMIN_EMAILS=<the email you sign in with>` to `.env` and run `docker compose up -d api` so the
   API picks them up. Check it:
   `curl -X POST https://<your-domain>/worker/claim` should now answer 401 (bad token), not 503.
2. **The image.** Merging to main runs `.github/workflows/worker-image.yml`, which publishes
   `ghcr.io/otunmartins/biologix-worker:latest` (or run it by hand from the Actions tab). The
   first time, open the package on GitHub (profile → Packages → biologix-worker → Package
   settings) and set its visibility to **Public**, so RunPod can pull it without credentials.
3. **A template.** RunPod → Templates → New Template, type Pod:
   - Container image: `ghcr.io/otunmartins/biologix-worker:latest`
   - Container disk: 20 GB (the image is about 6.5 GB unpacked). No volume, no exposed ports.
   - Environment variables: `API_URL=https://<your-domain>`, `WORKER_TOKEN=<the same token>`,
     `OPENMM_PLATFORM=CUDA`, and `IDLE_STOP_MINUTES=30`. Mark `WORKER_TOKEN` as a secret.
4. **A pod.** Deploy a Pod from the template on one GPU, with **On-Demand** pricing, not Spot:
   a pre-empted spot pod loses the run in progress. An RTX 4090, L4 or A40 is plenty, since OpenMM
   runs one small system per GPU. Use the CUDA filter to pick hosts with CUDA 12.x or newer
   (the image needs an NVIDIA driver ≥ 525).
5. **Let approvals start it.** Copy the pod's id (on its card in the console) and create an API
   key under RunPod → Settings → API Keys. Put them on the server as `RUNPOD_POD_ID` and
   `RUNPOD_API_KEY` in `.env`, then `docker compose up -d api`. Stop the pod; from now on
   approving a run starts it, and the worker stops it again when the queue is empty.
6. **Check it.** Queue one candidate, approve it in the Approvals tab, and watch the pod start.
   Its logs should show `platforms [..., 'CUDA']` on the first line, then claim the job. A queued candidate's card switches to "Running: ..." with the
   current stage.

**Cost control.** A pod bills every hour it is up, busy or not. With `IDLE_STOP_MINUTES` set,
the worker stops its own pod once the queue has been empty that long, using the pod-scoped API
key RunPod puts in every pod. Stopped pods cost only a little for disk. After queueing new
candidates, start the pod again from the RunPod console. If the stop call fails, the log says so;
stop the pod by hand then, or it keeps billing.

**Timing.** A 20 ns run of a Fab-sized system (about 100,000 atoms) takes roughly 3 to 6 hours on
an A10G-class GPU. On a CPU it would take days, so the CPU is used only for the smoke test
(`python worker/smoke.py`, and in CI), which proves every stage runs: about 15 minutes on two
cores, 12,817 atoms for a few picoseconds.

## The polymer designer

The app's second workflow (`POST /design`, the **Design** tab). Describe a biologic and the
temperature it has to survive, and it returns a ranked shortlist of polymer candidates to take
into the laboratory.

```
"Lyophilised enzyme for tropical distribution — must hold at 30 °C for a year."
   -> 50 motif pairings built, screened and ranked
   -> #1 Poly(methacrylamide) bearing trehalose · Tg ≈ 104 °C predicted · no alerts · grade D
```

**Candidates are enumerated, not invented.** A model asked to propose stabilising polymers will
produce fluent, unfalsifiable suggestions — exactly what the evidence gate exists to stop. So the
chemistry comes from a curated table in `api/design.py`: five backbones × eight pendant groups,
each with its mechanism stated. The model's only job is reading the goal out of the user's words
(temperature, duration, liquid or lyophilised, which residues are exposed), and its prompt forbids
it from inventing a value for a field the user left empty. Hand over a structured `goal` instead
and no model is called at all.

**The screens do the discriminating.** Each pairing is built into a real 4-unit chain and put
through the same structural alerts and rule table as any other excipient here, so the ranking is
driven by chemistry the app can see:

| what fires | on what | consequence |
|---|---|---|
| Reducing sugar | glucose pendant | glycates Lys — ranked to the bottom, below every clean candidate |
| Polyether chain | oligo(ethylene glycol) pendant | peroxides oxidise Met/Trp on storage |
| Michael acceptor | acrylamide backbone | residual monomer alkylates Cys |
| Hydrolysable ester | methacrylate backbone | pH drift promotes deamidation |

The sugar regiochemistry is load-bearing and easy to get wrong. Pendants attach through the
**6-hydroxyl**, as real glycopolymer chemistry does, which leaves the anomeric centre's true
character intact: glucose keeps its free anomeric OH and is rejected, trehalose and sucrose keep
both anomeric carbons glycosidic and are not. Attach a sugar through its anomeric carbon instead
and this silently inverts — glucose reads as safe. `test_smoke.py` pins it.

**Scoring is transparent and additive**, and every term states its own reason: hydroxyl density
(hydrogen bonding, the basis of both preferential exclusion and water replacement), zwitterionic
hydration, glass transition, the alerts that actually fired, and the stated caution on each motif.
Two rules worth knowing:

- **Vitrification only counts for a dried product.** A matrix has to be a glass at the storage
  temperature to immobilise the protein, so Tg is scored continuously in its margin over the
  target — residual moisture plasticises a real cake by tens of degrees, so headroom keeps paying.
  PEG (Tg ≈ −60 °C) is a poor lyophilisation matrix for exactly this reason. For a liquid
  formulation Tg is ignored entirely.
- **An exposed residue makes the matching alert cost more.** Tell it the protein exposes Met and
  every polyether candidate is penalised harder.

**Tg is predicted for the repeat unit, and judged on its lower bound.** `api/tg_model.py` is a
forest trained on 7204 experimental polymers (LAMALAB curated benchmark, Zenodo 14980914),
measured at R² 0.885 / MAE 26.3 °C on a random split but **R² 0.704 / MAE 36.3 °C on a scaffold
split** — whole chemical families held out, which is the honest number for designed chemistry and
the one the app plans around. Each prediction carries the forest's spread on that query and its
Tanimoto distance to the nearest training polymer; a candidate outside the model's domain is
labelled an extrapolation.

The screen then scores the prediction **minus its uncertainty**, not the prediction itself,
because the two ways of being wrong do not cost the same: understating Tg drops a candidate the
laboratory would have measured anyway, while overstating it recommends a matrix that is not a
glass at storage temperature. For the same reason the backbone's handbook value is *not* treated
as safer — it cannot see the pendant, and a flexible side chain plasticises a stiff backbone by
60–100 °C — so it carries the same uncertainty, and an out-of-domain prediction is allowed to
argue a candidate down but never up.

Either way this is a **homopolymer** number. A real copolymer or cake depends on composition,
moisture and processing, and has to be measured by modulated DSC — which is why that assay is in
every dried candidate's suggested experiments. `GET /health` reports `tg_model`: false there means
the trained model is absent and the designer is running on the backbone handbook table.

**What it does not do.** The designer itself runs no molecular dynamics and predicts no Tm. The
preferential-interaction simulation is a separate, opt-in step per queued candidate (see "OpenMM
simulations" above), and its result is still grade D. The ranking is a triage ordering for laboratory work, not a prediction that any candidate
will stabilise anything. Every candidate is **grade D**, verdict **"Data gap: test"**, and each
carries the experiments that would settle it (nanoDSF/DSC for Tm shift, accelerated stability with
SEC, modulated DSC for cake Tg, plus an assay for whatever alert fired). Nothing here addresses
synthesis feasibility, polydispersity, endotoxin, immunogenicity or clearance, any of which can
rule out a candidate on its own.

**The designer proposes; the screen judges.** Every candidate carries a *Screen this candidate*
action that drops its repeat unit into the polymer description on the Screen tab, where it goes
through identity, precedent, alerts and the liability map like any other excipient.

## Not a safety assessment

Chemistry-only triage on structural alerts and a small rule table. A human checkpoint is required
before anything here goes into a dossier.
