# AET

AET = AI Engineering Team.

This folder is the reusable AI engineering powerhouse for OpenRig.

## Core principle

The AET team definition stays centralized here (this folder, wherever it is cloned —
all scripts self-locate, no absolute paths).

Project repositories remain independent and decoupled from this folder.

AET is launchable against any project directory by dynamically setting that project's
working directory (`rig.yaml` per-member `cwd`).

Example target projects:

~/Desktop/ProjectA
~/Desktop/ProjectB
~/any/other/repo

The team files, role definitions, policies, skills, and wrappers remain in this
central AET folder. Role prompts live in `roles/` (one folder; `agents/*/guidance/role.md`
are symlinks into it). Work connections live in `shared/topology.yaml`. Model bindings
live in `shared/models.yaml` — `scripts/stamp-config.py` renders them into rig-facing files.

## OpenRig

Rig name:

aet

## Initial team structure

Director
└── Technical Managers
    ├── tm-a
    ├── tm-b
    ├── tm-c
    └── more as needed

The Director may delegate stories to Technical Managers.

Technical Managers own delegated stories end-to-end and report completion,
blockers, validation, and risks back to the Director.

Technical Managers are peers: they never assign work to each other
(see `shared/topology.yaml` for the full connection contract).

## Current milestone

Prove that the centralized AET rig can work against arbitrary external project
folders without copying AET files into those repositories. (PROVEN — see root
CLAUDE.md for the standing setup.)
