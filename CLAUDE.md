# myTeams — AI team setups (master orientation)

**Read this first.** It exists so any Claude session opened under `myTeams/` knows the setup,
all important decisions, and exactly where development continues. Crisp, complete, no fluff.

## What this folder is

Home of the reusable AI team setups. **The setup is READY TO USE** and still under active
development. **myTeams is the AI powerhouse / software factory** — the 12 identities below are
its factory floor. It is a **standalone project: no file here references any other project.**

| Path | What |
|---|---|
| `openRig-team/` | **AET** (AI Engineering Team) — OpenRig rig `aet`. Centralized team definition: Director → TMs + specialists. Launchable against ANY project dir (`rig.yaml` per-member `cwd`). |
| `_parked/` | Temporarily unused: `herder/` (job supervisor). Reversible — move back when needed. |

**Active setup = OpenRig + AET seats only.** Everything else is parked.

## How to drive the rig — `trs` (on PATH)

```bash
trs up [--resume]        # rig at $PWD — existing stays; --resume = latest snapshot
trs kill [--all]         # kill the rig at $PWD · --all = EVERY running rig (auto-snapshot)
trs attach <seat>        # director | tm-a | lr | cr — any terminal, nested-tmux safe
trs ls                   # rigs + snapshots
trs restore              # resume latest snapshot
```
`trs` = `~/.local/bin/trs` → `myTeams/trs` (self-locating project surface over `myTeams/team`,
the seat/boot engine — install via `install.sh`). All seats boot `--dangerously-skip-permissions`
(permission_policy builtin:yolo). Human-opened seats wait for input (PASSIVE MODE in role.md).

## The three single-homes (config contract)

| File | What it defines | Renders into |
|---|---|---|
| `openRig-team/roles/` | **every seat's role prompt** — the one prompt folder | `agents/<seat>/guidance/role.md` are SYMLINKS into it (seat boot reads through them) |
| `openRig-team/shared/topology.yaml` | **who can hand work to whom** — edges + rules (e.g. TMs never assign to each other) | `rig.yaml` `edges:` blocks |
| `openRig-team/shared/models.yaml` | **what each seat runs** — providers, per-seat pins, claude-seat model slugs | `agents/<seat>/models.json` pins + `rig.yaml` `model:` lines |

`scripts/stamp-config.py` renders models + topology into the rig-facing files; `--check` fails
on drift (run before any config commit). Machine-local values — API keys, optional extra MCP
tools — live in `~/.config/myteams/config.local.yaml`, **never in this repo**.

Wire format (claude-code seats): `model: openrouter/<vendor>/<model>` — emitted at launch as
`--model <slug>`. Pi seats pin via `agents/<seat>/models.json` (providers → OpenAI-compat
endpoints). **Pi reads models.json ONCE at session creation** — a pin edit needs a seat bounce
(`rig seat launch <seat>@aet --fresh --stop`) to take effect. Never bake one-off values into
source configs — env vars at launch, defaults stay in code.

## Team topology — 12 identity seats (factory floor)

| Identity | Alias | Model | Runtime | When to use |
|---|---|---|---|---|
| technical_manager / director | `tm` | xiaomi/mimo-v2.6-pro | openrouter | drive/delegate, never do the work |
| principal_dev (PD) | `pd` | anthropic/claude-sonnet-5.5 | openrouter | hard reasoning, architecture, root-cause ONLY |
| senior_engineer (SE) | `se` | deepseek/deepseek-v4.1-flash (reasoning high) | openrouter | online deep reasoning |
| junior_dev (JD) | `jd` | z-ai/glm-5.3-flash | openrouter | implement clear specs — "the hands". **It's JD, not LD.** |
| auditor | `gate` | deepseek/deepseek-v4-flash-0731 | openrouter | ONE prompt, 6 lenses (see fold below) |
| verifier | `gate` | deepseek/deepseek-v4-flash-0731 | openrouter | mechanical L1/L2 + golden drift; gates, never ships |
| code_reader (CR) | `coder` | agent:coder = gemma-4-e4b-it-4bit (primary) · qwen3-coder-30b (reserve) | coder_local `:8800` | code-graph-grounded code reading; reader never writer |
| local_researcher (LR) | `coder` | agent:coder = Qwen3.6-35B-A3B-4bit (rep-guard pin) | coder_local `:8800` | local research, fetch/discover-first |
| context_manager *(PARKED 2026-10-12)* | `coder` | agent:coder | coder_local `:8800` | Cognee context layer (FEED/FETCH) — seat unprovisioned, re-add later |
| vision / vision_model (VM) | `vision` | agent:vlm = Qwen3-VL-8B | vision_local `:8765` | image analysis, OCR, collage count |

Clone users may flip LR/CR/vision to online models at ONE place (`shared/models.yaml` → stamp →
bounce). Defaults stay as above.

## ⚠ Gates folded 7→2 (decided 2026-10-06)

Before: 7 review seats. After: **2 fail-closed gate seats:**
1. **verifier** — mechanical: L1/L2 fixtures + golden drift. Fail-closed.
2. **auditor** — ONE system prompt, lens chosen from task keywords:
   `correctness` (default, re-derive) | `dod` | `policy` | `provenance` | `perf` | `fixtures-from-spec`.

Folded seat → auditor lens: dod_gatekeeper→dod · policy_security→policy ·
ground_truth→provenance · perf_scale→perf · testcase_writer→fixtures-from-spec.

- Safety invariants all preserved; project-specific policy invariants belong to the TARGET
  project (its own policy docs), not to this repo.
- The fold is the recorded exception to additive-only; when configs change seats again:
  remove stale entries (P46 checks every key), then re-seed.

## Context layer (Cognee) — the `context-manager` seat (`specialists.context-manager`, role.md)

**PARKED 2026-10-12** (remove for now, re-add later) — seat unprovisioned in `rig.yaml`;
`agents/context-manager/` kept. Design below stays as the re-add record.

One Cognee dataset per project. **FEED:** `index` (bulk: `kb/**/*.md` + `kb/**/*.jsonl` +
`memory/**/*.md`, each stamped `[source: path]`) or `remember "text"` (live). **FETCH:**
`get_context --role X --task Y` / `query "text"` (k=5, confidence 0.8 if items else 0.0, gaps
listed). Router keywords `knowledge/lesson/recall/remember` → context_manager identity.

Hard rules: read-only for project code · provenance-cited · **no disallowed content** ·
no counter side effects · empty + confidence=0 when uncertain (never invented filler).

## Dispatch — how work moves (rig queue, standalone)

Work moves as **qitems on the rig queue** — no external dispatcher.

```bash
rig queue add <seat> "task"       # TM/human routes work to a seat
rig queue list --owned           # seat sees what is pending/its
rig queue claim <id>              # claim ONLY a pending item
rig send <session> "result …"     # return evidence to the origin seat
rig queue update <id> --state done --closure-reason no-follow-on
```

- Seat names: `director tm-a tm-b tm-c pd se jd auditor verifier cr lr vision`
  (ctx parked 2026-10-12; attach via `trs attach <seat>`; work via the queue lifecycle above).
- **Wake channel = `rig queue create --destination <seat> …`** (the create-path nudge wakes the
  seat — verified). `--note` does NOT wake. `state=done` REQUIRES `--closure-reason`.
- **LR works the queue like every other seat** — never nohup / ad-hoc background files. LR is
  fetch/discover-first.
- Child asks stay small: smallest vertical slice, tests at public seams, no speculative
  abstractions, evidence-only report.
- PASSIVE MODE on every seat: no work until a qitem/directed message arrives.

## Verification discipline — done only when verified

- **Factory floor (12 identities):** the verifier identity's `verify.py` — L1 +
  `golden_baseline.json` drift = FAIL. Re-seed (`--seed`) ONLY on an intentional, user-ruled
  change. Note: `l2_tm skipped` in seed output = live-gate noise, not a failure.
- **Target project:** its own declared bar (its verifier entry script + fixture suite) gates any
  code change made there. Ground every per-line claim against the actual file (reports can be
  imagined mappings — the paper-report trap). Evidence before action.

## Standing rules & hard rules (do not violate)

1. **Never fire/send anything externally without the driver's approval gate.** Standing user
   approval ("dont wait for my go. u have all my approvals") covers LOCAL bounded zero-spend
   work only; outward/irreversible actions still gated.
2. **Ponytail mode (full):** shortest working diff, root-cause fixes, YAGNI, reuse over rewrite,
   deletion over addition. Mark deliberate ceilings with `ponytail:` comments (ceiling + upgrade
   path). Non-trivial logic leaves one runnable check behind.
3. **Context discipline:** tool output >500 chars → write to file first, reply with a pointer.
   Never paste payloads into the window.
4. **Team routing (WST):** CR for code deep dives · PD for hard/ambiguous reasoning · LR for
   online research · loop them repeatedly. "Its JD not LD, remember critically."
5. **Portable paths:** every script self-locates (`ROOT="$(cd "$(dirname "$0")" && pwd)"`) —
   never hardcode absolute paths. Shell cwd resets between calls — use absolute paths.
   Every Edit needs a Read of that file in the CURRENT session first.
6. **Local LLM gotchas:** vLLM/MLX `/v1/chat/completions` returns `content=null` with text in
   `reasoning` unless consumers pass `chat_template_kwargs enable_thinking:false`;
   gpt-researcher FAST/SMART_LLM = `provider:model`. LR pins carry rep-guard
   (`repetition_penalty: 1.2, repetition_context_size: 256`) — emit only for `provider: local`.
7. Driver-specific rules for live projects (policy invariants, verification bars, entity names,
   ops commands) intentionally do **not** live in this repo — they belong to the driver's
   session memory / the target project's own docs.

## Where development continues

Config or prompt change → edit the single-home file (`roles/`, `shared/topology.yaml`,
`shared/models.yaml`) → `python3 scripts/stamp-config.py --check` clean → bounce affected seats.
Seat prompt change needs no stamp (symlinked). New machine → `./install.sh`. Publish state:
git repo + GitHub (private). Keep this file updated when a decision changes — it is the single
orientation doc for myTeams.
