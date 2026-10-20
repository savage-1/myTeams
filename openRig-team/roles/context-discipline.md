# Context Discipline — all seats

**Why:** every session dies from raw tool output accumulating in context. AWS/IBM: 214KB in context = failure; 52-byte pointer = success. Violations killed 2 LR sessions (32k, then 64k wall). This applies to every agent — local model or online.

## HARD RULES

1. **Output → file, NOT context.** Any tool output > 500 chars gets written to a findings file BEFORE your next thought. The file is your memory; context is for reasoning.

2. **Return pointers, not payloads.** Reply body = summary + pointer to the file. Never paste the full output.

3. **Truncate or store — never both-nothing.** > 500 chars: summarize before next thought. > 2000 chars: write to file, use pointer. No third option.

4. **After every tool call:** "did I need to see that much?" If no → next call must be tighter.

5. **Context health check** every 3 tool calls:
   - Past 50% of usable context? → write checkpoint to file
   - Re-reading things already summarized? → stop, use the file
   - Next output > 500 chars? → write to file first

6. **Context feels heavy** (slow, losing track)? Write structured checkpoint:
   ```
   ### Session Intent / ### Key Decisions / ### Evidence So Far
   ### Current State / ### Next Steps
   ```
   Then handover or relaunch. A fresh occupant with a pointer file beats a dead session.

Pattern: **work → write file → reply with summary + pointer.**
Never: **work → paste everything → die.**
