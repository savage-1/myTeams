#!/usr/bin/env python3
"""stamp-config.py — render the one-place config into rig-facing files.

Sources (edit THESE):
  shared/models.yaml    seat model pins (`seats:` = pi seats, `claude_seats:` = claude seats)
  shared/topology.yaml  work-connection contract (delegates_to / forbidden)
  ~/.config/myteams/config.local.yaml   machine-local API keys + optional graphify

Targets (generated — do not hand-edit):
  agents/<seat>/models.json                      pi seat pins
  rig.yaml                                       `model:` lines + stamp-marked edges blocks
  agents/shared/runtime/claude-mcp.fragment.json MCP wiring for claude seats

Usage:
  stamp-config.py            render (writes only where content differs)
  stamp-config.py --check    exit 1 on any drift (verification)
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

OPENRIG = Path(__file__).resolve().parents[1]
MYTEAMS = OPENRIG.parent
MODELS_YAML = OPENRIG / "shared" / "models.yaml"
TOPOLOGY_YAML = OPENRIG / "shared" / "topology.yaml"
RIG_YAML = OPENRIG / "rig.yaml"
FRAGMENT = OPENRIG / "agents" / "shared" / "runtime" / "claude-mcp.fragment.json"
CONFIG_LOCAL = Path.home() / ".config" / "myteams" / "config.local.yaml"

PI_SEATS = ["researcher-local", "code-reader", "vision", "context-manager"]


def die(msg):
    sys.exit(f"stamp: error: {msg}")


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def resolve_api_key(prov, cfg):
    """local pins keep literal api_key ("dummy"); online providers pull the key
    from config.local.yaml via api_key_env. Never prints the value."""
    if "api_key" in prov:
        return prov["api_key"]
    env_name = prov.get("api_key_env")
    if env_name:
        key = (cfg.get("api_keys") or {}).get(env_name)
        if not key:
            die(f"{CONFIG_LOCAL} is missing api_keys.{env_name} — run install.sh or add it")
        return key
    return "dummy"


def render_models_json(seat_name, seat, providers, cfg):
    prov = providers.get(seat["provider"])
    if not prov:
        die(f"seats.{seat_name}.provider '{seat['provider']}' not found under providers:")
    if not seat.get("provider_id"):
        die(f"seats.{seat_name} needs provider_id (the provider key inside models.json)")
    entry = {
        "baseUrl": prov["api_base"],
        "api": prov.get("api", "openai-completions"),
        "apiKey": resolve_api_key(prov, cfg),
        "compat": prov.get(
            "compat", {"supportsDeveloperRole": False, "supportsReasoningEffort": False}
        ),
        "models": [],
    }
    for m in seat["models"]:
        item = {"id": m["id"], "name": m["name"], "maxTokens": m["maxTokens"]}
        if "samplingParams" in m:
            item["samplingParams"] = m["samplingParams"]
        entry["models"].append(item)
    return json.dumps({"providers": {seat["provider_id"]: entry}}, indent=2) + "\n"


def render_fragment(cfg):
    mcp = {
        "exa": {"type": "http", "url": "https://mcp.exa.ai/mcp"},
        "context7": {"type": "http", "url": "https://mcp.context7.com/mcp"},
        "yttranscript": {
            "command": f"{MYTEAMS}/openRig-team/research/.venv/bin/yttranscript-mcp",
            "args": [],
        },
    }
    g = cfg.get("graphify") or {}
    if g.get("command"):
        entry = {"command": g["command"]}
        if g.get("graph"):
            entry["args"] = [g["graph"]]
        entry["env"] = {"GRAPHIFY_ALLOW_EDIT": "1"}
        mcp["graphify"] = entry
    return json.dumps({"mcpServers": mcp}, indent=2) + "\n"


def scan_members(lines):
    """rig.yaml structure: pod ids at 2-space '- id:', member ids at 6-space."""
    pod = None
    member_pod = {}
    for ln in lines:
        m = re.match(r"^  - id: (\S+)$", ln)
        if m:
            pod = m.group(1)
            continue
        m = re.match(r"^      - id: (\S+)$", ln)
        if m:
            member_pod[m.group(1)] = pod
    return member_pod


def check_topology(topo, member_pod):
    tms = {m for m in member_pod if m.startswith("tm-")}
    edges = topo.get("delegates_to") or {}
    for src, tgts in edges.items():
        if src not in member_pod:
            die(f"topology delegates_to source '{src}' is not a rig member")
        if src != "director" and src not in tms:
            die(f"only director/TMs assign work — bad source '{src}'")
        for tgt in tgts:
            if tgt not in member_pod:
                die(f"topology delegates_to target '{tgt}' is not a rig member")
            if src == "director" and tgt not in tms:
                die(f"director delegates to TMs only — bad target '{tgt}'")
            if src in tms and (tgt in tms or tgt == "director"):
                die(f"TMs never assign to director/TMs ({src} -> {tgt})")
    rendered = {(s, t) for s, tgts in edges.items() for t in tgts}
    for pair in topo.get("forbidden") or []:
        a, b = pair[0], pair[1]
        if (a, b) in rendered or (b, a) in rendered:
            die(f"forbidden edge {a} <-> {b} appears in delegates_to")


def build_edge_blocks(topo, member_pod):
    pod_edges = {}
    top_edges = []
    for src, tgts in (topo.get("delegates_to") or {}).items():
        for tgt in tgts:
            if member_pod[src] == member_pod[tgt]:
                pod_edges.setdefault(member_pod[src], []).append((src, tgt))
            else:
                top_edges.append((f"{member_pod[src]}.{src}", f"{member_pod[tgt]}.{tgt}"))
    return pod_edges, top_edges


def edge_items(pairs, indent):
    out = []
    for src, tgt in pairs:
        out.append(f"{indent}- kind: delegates_to")
        out.append(f"{indent}  from: {src}")
        out.append(f"{indent}  to: {tgt}")
    return out


def replace_region(lines, begin_pat, end_pat, content, what):
    begin = end = None
    for i, ln in enumerate(lines):
        if begin is None and re.match(begin_pat, ln):
            begin = i
        elif begin is not None and re.match(end_pat, ln):
            end = i
            break
    if begin is None or end is None:
        die(f"rig.yaml missing stamp markers for {what} (expected '{begin_pat}' ... '{end_pat}')")
    return lines[:begin] + [lines[begin]] + content + [lines[end]] + lines[end + 1 :]


def rewrite_rig(text, claude_seats, topo, member_pod):
    had_final_nl = text.endswith("\n")
    lines = text.splitlines()
    out = []
    member = None
    model_seen = set()
    for ln in lines:
        m = re.match(r"^      - id: (\S+)$", ln)
        if m:
            member = m.group(1)
        mm = re.match(r"^(        model: )(\S+)$", ln)
        if mm:
            if member in claude_seats:
                out.append(mm.group(1) + claude_seats[member])
                model_seen.add(member)
                continue
        out.append(ln)
    for seat in claude_seats:
        if seat not in model_seen:
            die(f"rig.yaml has no 'model:' line for claude seat '{seat}' (stamp rewrites values, not structure)")
        if seat not in member_pod:
            die(f"claude_seats key '{seat}' is not a rig member")

    pod_edges, top_edges = build_edge_blocks(topo, member_pod)
    for pod, pairs in pod_edges.items():
        indent = "    "
        content = [f"{indent}edges:" + (" []" if not pairs else "")]
        content += edge_items(pairs, indent + "  ")
        out = replace_region(
            out,
            rf"^    # stamp:begin pod-edges {re.escape(pod)}$",
            r"^    # stamp:end pod-edges$",
            content,
            f"pod-edges {pod}",
        )
    top_content = ["edges:" + (" []" if not top_edges else "")]
    top_content += edge_items(top_edges, "  ")
    out = replace_region(
        out,
        r"^# stamp:begin edges$",
        r"^# stamp:end edges$",
        top_content,
        "top-level edges",
    )
    return "\n".join(out) + ("\n" if had_final_nl else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="report drift, write nothing")
    args = ap.parse_args()

    models = load_yaml(MODELS_YAML)
    topo = load_yaml(TOPOLOGY_YAML)
    cfg = load_yaml(CONFIG_LOCAL) if CONFIG_LOCAL.exists() else {}
    providers = models.get("providers") or {}

    rig_text = RIG_YAML.read_text()
    member_pod = scan_members(rig_text.splitlines())
    check_topology(topo, member_pod)

    targets = []
    for seat_name in PI_SEATS:
        seat = (models.get("seats") or {}).get(seat_name)
        if not seat:
            die(f"models.yaml seats: is missing '{seat_name}'")
        path = OPENRIG / "agents" / seat_name / "models.json"
        targets.append((path, render_models_json(seat_name, seat, providers, cfg)))
    targets.append((RIG_YAML, rewrite_rig(rig_text, models.get("claude_seats") or {}, topo, member_pod)))
    targets.append((FRAGMENT, render_fragment(cfg)))

    drift = 0
    for path, rendered in targets:
        rel = path.relative_to(MYTEAMS)
        current = path.read_text() if path.exists() else None
        if current == rendered:
            print(f"  ok     {rel}")
        elif args.check:
            drift += 1
            print(f"  drift  {rel}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered)
            print(f"  wrote  {rel}")
    where = "config.local.yaml loaded" if cfg else "config.local.yaml absent"
    if args.check:
        print(f"check: {len(targets)} targets, {drift} drift ({where})")
        sys.exit(1 if drift else 0)
    print(f"stamp: done ({where})")


if __name__ == "__main__":
    main()
