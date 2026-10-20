# myTeams — AI software factory on OpenRig

A portable, standalone AI engineering team. One rig (`aet`), 12 seats, one work
queue: Director → 3 Technical Managers → engineers (PD/SE/JD) → fail-closed
gates (auditor/verifier) → local specialists (LR/CR/vision). Work moves as
qitems (`rig queue`). No file here references any other project — clone it and
point it at any repo.

| Seat | Alias | Model | Role |
|---|---|---|---|
| director, tm-a, tm-b, tm-c | `tm` | openrouter/xiaomi/mimo-v2.6-pro | drive/delegate — never do the work |
| principal_dev | `pd` | openrouter/anthropic/claude-sonnet-5.5 | hard reasoning, architecture |
| senior_engineer | `se` | openrouter/deepseek/deepseek-v4.1-flash | online deep reasoning |
| junior_dev | `jd` | openrouter/z-ai/glm-5.3-flash | implement clear specs ("the hands") |
| auditor / verifier | `gate` | openrouter/deepseek/deepseek-v4-flash-0731 | 6-lens audit / mechanical verify |
| local_researcher | `lr` | local (see below) | fetch/discover-first research |
| code_reader | `cr` | local (see below) | code-graph-grounded reading, never writing |
| vision | `vision` | local (see below) | image analysis, OCR |

## Fresh macOS machine setup

Everything a new machine (e.g. a MacBook Air) needs before cloning. Local models
are NOT required — flip LR/CR/vision online (below) and the local-server stack is
never touched.

```bash
# 1. base tools
brew install tmux jq node python@3.11

# 2. CLIs
npm i -g @openrig/cli @anthropic-ai/claude-code

# 3. clone + one-shot install (prompts for your OpenRouter key, hidden input)
git clone https://github.com/savage-1/myTeams && cd myTeams
./install.sh
#    -> ~/.config/myteams/config.local.yaml (mode 600, never committed)
#    -> research/.venv from requirements.txt
#    -> trs symlinked to ~/.local/bin  ·  config rendered + verified
```

Then flip LR/CR/vision to online (next section) **before the first `trs up`** —
doing it up front means no seat bounce is needed:

```bash
trs up ~/path/to/project && trs attach director
```

Not needed on a fresh machine: local models / vLLM, graphify MCP (optional
config.local.yaml add-on), gh (only for publishing the repo).

Notes: arm64 macOS + python 3.11 is the known-good combo for `requirements.txt`.
`install.sh` is idempotent — an existing key file or venv is never touched.

## Quickstart

```bash
git clone <this-repo> myTeams && cd myTeams
./install.sh                       # deps check -> venv -> key prompt -> trs on PATH -> stamp
trs up ~/path/to/project           # boots all 12 seats INTO that project dir
trs attach director
```

`install.sh` registers `trs` by symlinking it to `~/.local/bin/trs` (it
self-locates `team/` from there). It is idempotent: an existing
`~/.config/myteams/config.local.yaml` and an existing venv are never touched.

## One-place config

| Edit this | Defines | Rendered by `scripts/stamp-config.py` into |
|---|---|---|
| `openRig-team/roles/*.md` | every seat's role prompt (one folder) | `agents/<seat>/guidance/role.md` are **symlinks** into it — no render needed |
| `openRig-team/shared/topology.yaml` | who hands work to whom (e.g. TMs never assign to each other) | `rig.yaml` `edges:` blocks |
| `openRig-team/shared/models.yaml` | providers + per-seat model pins | `agents/<seat>/models.json` + `rig.yaml` `model:` lines |
| `~/.config/myteams/config.local.yaml` | **machine-local**: API keys, optional MCP extras (e.g. graphify) | never committed; consumed at stamp time |

```bash
openRig-team/research/.venv/bin/python openRig-team/scripts/stamp-config.py          # render
openRig-team/research/.venv/bin/python openRig-team/scripts/stamp-config.py --check  # verify (exit 0 = clean)
```

Generated (gitignored — do not hand-edit): `agents/*/models.json`,
`agents/shared/runtime/claude-mcp.fragment.json`. After an online flip they can
contain real keys — that is why they are generated, not committed.

## Flip LR / CR / vision from local models to online

Defaults assume local OpenAI-compat endpoints (`shared/models.yaml` providers:
`local` → `http://127.0.0.1:8800/v1`, `vlm-local` → `:8765`). No local models?
Run these seats on OpenRouter instead — one place:

1. Key: `~/.config/myteams/config.local.yaml` → `api_keys: OPENROUTER_API_KEY: <yours>`
   (already there if install.sh wrote it).
2. Edit `openRig-team/shared/models.yaml` → the `seats:` entry, e.g. for LR:

   ```yaml
   seats:
     researcher-local:
       provider: openrouter
       provider_id: openrouter
       models:
         - id: <openrouter-model-slug>
           name: <display name>
           maxTokens: 8192
   ```

   Same shape for `code-reader` and `vision`. (Drop `samplingParams` — those
   are local-server knobs.)
3. Render: `openRig-team/research/.venv/bin/python openRig-team/scripts/stamp-config.py`
4. **Bounce the seat** — pi reads `models.json` once at session creation:

   ```bash
   rig seat launch specialists.researcher-local@aet --fresh --stop
   ```

Repeat per seat (`specialists.code-reader@aet`, `specialists.vision@aet`).

## Day-to-day

```bash
trs up [--resume]     # rig at $PWD (--resume = latest snapshot)
trs attach <seat>     # director tm-a tm-b tm-c pd se jd auditor verifier cr lr vision
trs kill [--all]      # kill rig at $PWD (--all = every running rig, auto-snapshot)
trs ls                # rigs + snapshots
trs restore           # resume latest snapshot
team health           # per-seat OK | BARE_SHELL | JUNK_FLOOD | DEAD_SESSION
team unjam <seat|all> # managed relaunch (posture + model pin)
```

All seats boot `--dangerously-skip-permissions` and wait for work (PASSIVE
MODE) until a qitem or message arrives. Dispatch contract: `rig queue add/claim`
… `rig queue update <id> --state done --closure-reason no-follow-on`.
