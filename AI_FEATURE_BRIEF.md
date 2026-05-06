# MacSweep ↔ Integrator AI Feature — Brief for Claude Code

> Hand this brief to Claude Code in a fresh MacSweep session. It explains what was just wired (the technical integration with Integrator) and what to build next (the user-facing AI feature). Read top to bottom.

---

## What's already done (in this repo, ready to use)

A new module: `backend/integrator_chat.py`. Read it. Key surface:

```python
from backend import integrator_chat

integrator_chat.is_connected() -> bool
integrator_chat.status()       -> dict   # {connected, base_url, client_id, scope, expires_at, user_email}
integrator_chat.connect()      -> dict   # opens browser, runs PKCE flow on localhost:1717, persists tokens
integrator_chat.disconnect()   -> None

# Drop-in replacement for the existing llm_client.chat_json signature:
integrator_chat.chat_json(system="...", user="...", max_tokens=140, temperature=0.2) -> Optional[dict]

# Lower-level if needed:
integrator_chat.chat(messages, model=None, temperature=0.2, max_tokens=512, response_format=None) -> dict
```

Tokens stored alongside the existing api_key + oauth state in `data/config.json`, under the new `integrator` key. CLI surface for testing:

```bash
python -m backend.integrator_chat connect      # one-time pairing, opens browser
python -m backend.integrator_chat status       # JSON snapshot
python -m backend.integrator_chat test "..."   # smoke-test a prompt
python -m backend.integrator_chat disconnect
```

What's NOT done yet (and what this brief asks you to build):
1. The **opt-in onboarding** with the privacy disclosure
2. The **"Ask Sweeper" button** on file scan rows
3. **Routing** through `integrator_chat` automatically when configured (existing `llm_client.py` is untouched on purpose)

---

## What to build

### A. Privacy onboarding step

A new screen in MacSweep's settings/onboarding flow titled something like **"AI verdicts (premium feature)"**. The screen says, in plain words:

> When you click **Ask Sweeper** on a file, MacSweep sends the file's metadata — full path, name, size, last-accessed date, kind — to ChatGPT through your Integrator account. Your Integrator subscription bills for it; nothing else leaves your Mac. You can disable this any time.

Two buttons: **Connect Integrator** and **Skip for now**.

- **Connect Integrator** → calls `integrator_chat.connect()`. While running, show a dim "Browser opening… sign in with your Integrator account." On success, show "Paired with {email}. AI verdicts are now available."
- **Skip** → records `cfg["ai_verdicts_enabled"] = False`. User never sees the buttons.

The toggle should also live in Settings under a section titled **AI verdicts**. Three states there:
- *Not connected* → button **Connect**
- *Connected, enabled* → status pill **on**, button **Disable** (sets `ai_verdicts_enabled=False`, keeps tokens)
- *Connected, disabled* → status pill **off**, button **Enable** + small **Disconnect** link

Storage: read/write via `llm.load_config()` / `llm.save_config()`. New keys:
- `ai_verdicts_enabled: bool` (default False)
- `integrator: {...}` (already managed by `integrator_chat`)

### B. "Ask Sweeper" button on file rows

For every file shown in:
- The category browse view
- The Tier-2 suggestions list
- The file detail panel
- Any redundancy / quarantine / smart-cleanup view that lists individual files

…render a small **Ask Sweeper** button (◐ icon + tooltip "AI verdict — what to do with this") **only when**:

```python
llm.load_config().get("ai_verdicts_enabled") is True
   and integrator_chat.is_connected()
```

Click flow:
1. Button switches to a small spinner.
2. Frontend calls a new backend route (build it): `POST /api/file/ask-sweeper` with body `{path: "..."}`.
3. Backend route gathers the same metadata `file_verdict._payload(facts)` builds today, then calls `integrator_chat.chat_json(system=SYSTEM_PROMPT, user=json.dumps(payload), max_tokens=140, temperature=0.2)`.
4. Returns `{verdict: "delete"|"keep"|"ambiguous", reason: "...", confidence: float}`.
5. Frontend renders the verdict inline next to the file: a small pill (color-coded delete/keep/ambiguous) + the one-line reason in a quote-styled block beneath the file row.
6. The verdict gets cached in `db.py`'s file row so re-clicks are instant. Schema addition: a new column on the file table — `ai_verdict TEXT, ai_reason TEXT, ai_verdict_at INTEGER`. Idempotent migration.

`SYSTEM_PROMPT` in `backend/file_verdict.py` already exists and is correct — reuse it.

If `integrator_chat.chat_json` raises `IntegratorError`, surface it as a non-fatal toast: "Sweeper unavailable: {e}. Try again or reconnect Integrator in Settings."

### C. Bulk "Ask Sweeper for these" (optional, only after A + B work)

On the suggestion list, add a single button **Ask Sweeper for top 30**. Backend route `POST /api/file/ask-sweeper-bulk` with `{paths: [...]}`. Sends ONE prompt batching all files (Codex Responses API has plenty of context budget). Returns `[{path, verdict, reason}, ...]`. Updates the same DB columns. UI shows a progress bar while waiting.

This is the user's "highly repetitive" wedge — one ChatGPT call gets verdicts for 30 files at once. Do this AFTER A and B work so we're not bundling.

---

## Code review notes (read these before touching anything)

1. **Don't modify `oauth_chatgpt.py` or `llm_client.py`.** They're the existing OpenAI direct + Codex OAuth paths. They stay as fallbacks. The Integrator path is additive.

2. **Single source of truth for "is the AI feature on?"**: `cfg["ai_verdicts_enabled"]` AND `integrator_chat.is_connected()`. Both must be True before any button or route fires. The frontend should hide the buttons; the backend route should still defensively 403 if either is False, in case an old cached frontend tries.

3. **The `chat_json` shape is identical** between `llm_client.chat_json` and `integrator_chat.chat_json` (same kwargs, same return value). Don't duplicate the prompt or the parsing — `file_verdict.py` should just pick one based on config.

4. **PKCE/refresh works automatically.** `integrator_chat.chat()` calls `_refresh_if_needed()` every time. No need to expose refresh in the UI.

5. **Don't touch the LOCAL_PORT (1717) constant.** It's registered as the redirect_uri on the Integrator side. If two MacSweep instances ever try to pair simultaneously, the second one fails with a clear error message ("port in use") — no silent breakage.

6. **Tokens are stored in plaintext in `data/config.json`** with mode 0600 (matching the existing `llm.save_config` convention). If MacSweep ever ships outside the user's own machine, encrypt at rest. Today: same threat model as the existing OAuth tokens — fine.

---

## Test plan

1. **Onboarding flow**: launch MacSweep, hit the new AI screen, click Connect, sign in via Integrator, see "Paired with {email}".
2. **Setting**: Go to Settings → AI verdicts → toggle off → buttons disappear. Toggle on → buttons reappear.
3. **Ask Sweeper happy path**: pick a file in a Tier-2 suggestion, click Ask Sweeper, see verdict + reason inline within ~3 sec.
4. **Cache**: click Ask Sweeper again on the same file — instant (DB-cached).
5. **Disconnect**: Settings → Disconnect → verify buttons disappear and config has no `integrator` key.
6. **Audit**: in the Integrator console at https://integrator.polistician.ai/console/audit, verify each Ask Sweeper click shows up as a `chat_request` row.

---

## Why we're doing this

MacSweep already has a working ChatGPT integration via its own `oauth_chatgpt.py` flow. We're adding **a second path** that goes through Integrator instead, because:

- One pairing covers every polistician.ai app — users who've connected ChatGPT once on Integrator (HablaDaily already wired) get MacSweep AI for free.
- Audit + rate-limiting + token rotation handled centrally, not duplicated per app.
- When the next app comes online (calorie-tracker, voicetype, etc.) it inherits the same plumbing.

The user-facing pitch: **"Already connected your ChatGPT to Integrator? AI verdicts work out of the box. No extra setup."**

---

## Open questions for the implementer

- Where exactly should the AI onboarding step land in the existing settings flow? (After api_key/oauth, alongside them, or as a separate "AI capabilities" section?)
- Should Ask Sweeper auto-fire for ambiguous Tier-2 items on scan completion, or stay strictly user-triggered? (Brief assumes strictly user-triggered. Bulk verdicts in C are the auto-ish escape hatch.)
- Is the `MacSweep.app` bundle's bundled Python able to open `webbrowser`? (Probably yes — stdlib only, but verify on first build.)

If anything in this brief contradicts the existing code or seems off, push back before implementing. Don't blindly do whatever I said. Read the code, react.
