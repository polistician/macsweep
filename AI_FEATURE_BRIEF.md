# MacSweep AI Verdicts — What Shipped

The AI-verdicts feature is live. Pair MacSweep against
[Integrator](https://integrator.polistician.ai) once and the "Ask Sweeper"
buttons start working — file-level, folder-level, and bulk. The original
implementation brief was followed almost verbatim; this file is the post-ship
record.

## End-to-end flow

1. User opens Settings, clicks **Connect Integrator** in the *AI verdicts* card.
2. PKCE OAuth dance opens the browser; Integrator redirects back to
   `http://127.0.0.1:1717/auth/callback`. Tokens land in
   `data/config.json → integrator{}`.
3. `ai_verdicts_enabled` is flipped on automatically. `aiActive()` in the
   frontend gates every "Ask Sweeper" button.
4. Clicking **Ask Sweeper** on a file calls `POST /api/file/verdict` →
   `file_verdict.suggest()` → `integrator_chat.chat_json()` →
   Integrator → user's vaulted ChatGPT subscription → JSON verdict back.
5. Verdicts persist on `files.ai_verdict / ai_reason / ai_confidence /
   ai_verdict_at` so re-clicks are instant.

## Key files

| Layer | Path | Purpose |
|------|------|---------|
| OAuth + chat | `backend/integrator_chat.py` | PKCE flow, token refresh, `chat_json()` mirror of `llm_client` |
| Routing | `backend/file_verdict.py` | Picks Integrator if paired+enabled, falls back to `llm_client` |
| HTTP API | `backend/main.py` | `/api/integrator/{status,connect,disconnect,ai-verdicts}` + `/api/file/verdict[/bulk]` + `/api/group/verdict` |
| Schema | `backend/db.py` | `files.ai_verdict / ai_reason / ai_confidence / ai_verdict_at` migrations |
| Settings UI | `frontend/index.html` (lines ~490-510) | Privacy disclosure + Connect/Disable/Disconnect buttons |
| App UI | `frontend/app.js` | `aiActive()` gate, `askSweeperFor`, `askSweeperBulk`, group verdicts |
| Styles | `frontend/styles.css` | `.ask-sweeper-btn`, `.hidden`, verdict badges |

## Verifying it still works

```bash
cd ~/macsweep
python3 -m backend.integrator_chat status   # connected:true, user_email populated
./run.sh                                    # open http://127.0.0.1:8765
# In the UI: Settings -> AI verdicts -> confirm "Paired with <email>".
# Click Files -> pick any row -> "Ask AI" — verdict appears within ~2s.
# Click again — instant (cached).
```

Audit trail lives on Integrator: `polistician.ai/console -> Activity`.

## Notes for future work

- `/api/v1/auth/me` (Bearer iat_) was added to Integrator on 2026-05-06 so
  the Settings UI can show the paired email. Earlier paired sessions get
  backfilled lazily on the next `/api/integrator/status` call.
- The fallback OpenAI key path (`oauth_chatgpt.py`, `llm_client.py`) is
  unchanged and remains the no-Integrator option.
