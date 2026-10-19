# FlowCodex QA Report

- Date: 2026-10-19
- Scope: backend API, workflow state machine, frontend flows, deployment configuration, security boundaries
- Environment: Windows, Python 3.14, Mock LLM, local git repositories
- Automated tests: `pytest` — 17 passed
- Static checks: `compileall`, `node --check`, `bash -n`, Compose YAML parse
- Visual QA: desktop + mobile, empty state, token auth, validation errors
- Health score: **9/10** (ship-ready with environment-specific caveats)

## Passed Functional Areas

- Project creation and repository path validation
- Requirement intake and clarify pause/resume
- Seven-dimension feasibility scoring and weighted total
- Plan approval, rejection, and plan-stage rework
- Isolated worktree creation and patch application
- Critical review blocking and review → rework → completion
- Verification command execution
- Deliverable patch, isolated branch, optional commit, promote
- Knowledge injection, distillation, feedback, and pending review
- Obsidian Markdown export with event timeline
- Auth: missing/invalid token rejected; valid token accepted
- Empty state, token modal, validation error copy, desktop/mobile rendering
- Cancellation of queued runs and in-flight clarification
- Compose host-directory variables and deployment script syntax

## Defects Found and Fixed

1. **Rework failed because the existing branch/worktree was reused.**
   - Fix: force-remove the old worktree, remove the directory, prune metadata, then recreate.
   - Regression test: critical review → rework → completed.

2. **Cancellation could be overwritten by an in-flight stage.**
   - Fix: cancellation guards after LLM calls and before side effects/final completion.
   - Regression test: cancel while clarification is running.

3. **Patch scope was trusted to the model.**
   - Fix: server-side validation of changed files against `allowed_paths` and plan-listed files.
   - Regression test: out-of-scope and unplanned paths.

4. **Verification/publish commands inherited API secrets.**
   - Fix: sanitized subprocess environment removes token/API-key variables.
   - Regression test: verify command cannot read `LLM_API_KEY` or `API_TOKEN`.

5. **Sensitive repository files could enter LLM context.**
   - Fix: `.env`, private keys, credentials, and certificate/key files excluded.
   - Regression test: committed `.env` and `id_rsa` are not listed.

6. **Blank project/requirement/knowledge fields were accepted.**
   - Fix: Pydantic non-blank validators.
   - Regression test: whitespace-only inputs return 422.

7. **Review verdict could block on suggestions only.**
   - Fix: normalize verdict from critical issues and security-check failures.
   - Regression test: suggestions-only → pass; security failure → changes_requested.

8. **Feasibility total trusted model-provided value.**
   - Fix: always recompute weighted total from dimensions.
   - Regression test: deterministic weighted score.

9. **Required clarification questions could pass if model said ready.**
   - Fix: required questions always block progression.
   - Regression test: required vs optional questions.

10. **Empty plans and no-op patches could advance.**
    - Fix: block plans without steps and code stages with no changed files.

11. **Invalid access token showed “saved” success.**
    - Fix: probe the API before persisting the token; show an actionable error.
    - Browser QA: wrong token → “访问令牌无效，请重新输入”.

12. **Validation errors rendered as `[object Object]`.**
    - Fix: flatten FastAPI/Pydantic error arrays into readable Chinese text.
    - Browser QA: whitespace-only project name → “不能为空”.

13. **Skipped verification appeared as passed.**
    - Fix: UI now shows “已跳过”.

14. **Mobile header wrapped the long run title and version.**
    - Fix: single-line ellipsis and hide version at small widths.

15. **Compose host-directory variables were unused.**
    - Fix: wire `DATA_HOST_DIR`, `REPOS_HOST_DIR`, `OBSIDIAN_HOST_DIR`; prepare permissions in installer.

16. **Deployment script did not ensure Compose plugin/daemon.**
    - Fix: check `docker compose`, install the plugin if missing, enable Docker.

## Residual Risks / Not Tested

- Real DeepSeek/OpenAI-compatible calls were not exercised (no API key provided).
- Docker image build was not executed on this host (Docker CLI unavailable).
- `verify_command` and `publish_command` remain trusted administrative commands; they are not a sandbox.
- Single-tenant token auth; no RBAC, tenancy isolation, or audit retention.
- Cross-repository orchestration and remote PR creation are out of scope.

## Commands Run

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app tests scripts
node --check app/static/app.js
bash -n deploy/install-tencent.sh
bash -n deploy/backup.sh
impeccable detect --json app/static/index.html app/static/app.css app/static/app.js
```
