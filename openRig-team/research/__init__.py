"""research — local-research worker (re-homed 2026-09-04).

Package layout mirrors the golden module convention:
config.py (env-overridable DEFAULTS) + search_client + deep_research + mcp_server
+ cli.py (self-invocable JSON) + verifier/ (L1 offline fixtures + golden drift,
L2 live). Imports are absolute (``research.*``).
"""
