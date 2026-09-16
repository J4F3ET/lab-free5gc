# Fix the NRF per-NF registration check pattern, then audit the repo for any remaining pipe+grep-q SIGPIPE risks

**Date:** 2026-09-16
**Status:** PLANNED
**Bead(s):** none
**Epic:** none
**Chain:** `standalone-b8404157` seq `1`
**Context:** See `HANDOFF_standalone-b8404157_ci-pipefail-fix_2026-09-16.md` for full session data, the three-instance pipefail bug fix history, live CI run numbers, and the exact evidence for what's below.

---

## Problem Statement

The previous session fixed three instances of a SIGPIPE-under-`pipefail` bug in `ci/wait-ready.sh` and `ci/smoke-ue.sh`, merged via [PR #3](https://github.com/J4F3ET/lab-free5gc/pull/3) (commit `2caa425`), and verified live in CI run `34798235193` — smoke test steps 1/6 and 2/6 now pass in under a second each. While fixing the third instance (the per-NF NRF-registration loop in `ci/wait-ready.sh`, lines ~60-64), a SEPARATE bug was found: the pattern `${nf}.*register` can never match NRF's actual log line for a successful registration (`[INFO][NRF][NFM] Handle NFRegisterRequest` — no NF name or type anywhere in that line). This means the check has ALWAYS silently reported TIMEOUT for every one of the 8 NFs on every CI run, on top of the now-fixed pipefail issue — it was non-fatal only because of a trailing `|| true`, so nobody noticed. This was deliberately deferred out of the pipefail-fix PR as flagged background task `task_6f6e2c59`, and is the one concrete piece of unfinished work from that session. See Evidence & Data in the handoff for the exact log line and the full profile/pipefail investigation this builds on.

## Key Findings

- The pipefail/SIGPIPE bug class is now fixed in all 3 instances found this session, plus the 1 fixed in a prior session (4 total) — confirmed live via exit-code reproduction (255 vs 0) and via the actual CI run. → drives Phase 2 (repo-wide audit to make sure no 5th instance exists elsewhere).
- The NRF per-NF check's pattern (`${nf}.*register`) is unrelated to pipefail and has been silently wrong since it was written — NRF's log for a registration event never includes the NF's name or type. → drives Phase 1.
- Each of the 8 target NFs (AMF, SMF, AUSF, UDM, UDR, PCF, NSSF, UPF) DOES successfully register — proven independently by the fact that `ci/wait-ready.sh`'s other checks (AMF NGAP, PFCP association) pass and the full smoke test succeeds end-to-end. The check is checking something real, it's just checking it the wrong way. → informs Phase 1's fix approach (the functionality being verified is not in doubt, only the verification method).
- Neither `deploy.yml` nor `deploy-observability.yml` has a `pull_request` trigger, and `ci/**` is not in `deploy.yml`'s push-trigger path list — a PR touching only `ci/wait-ready.sh` will get ZERO CI signal automatically, on the PR or after merge. → drives an explicit `workflow_dispatch` requirement in Phase 1's validation step.
- Only `ci/wait-ready.sh` and `ci/smoke-ue.sh` were checked for the pipe+`grep -q` pattern this session (via `grep -rn 'logs.*|.*grep' ci/`, scoped to `ci/`). Other scripts referencing `docker compose logs` (e.g. `deploy/bootstrap-runner.sh`, `ci/collect-logs.sh`, `ci/preflight.sh`, `ci/check-plmn.py`) were never checked. → drives Phase 2.

## Anti-Goals (What NOT To Do)

- Do NOT try to fix the NRF pattern by just tweaking the regex (e.g. `.*register.*NF_TYPE.*`) — the log line genuinely does not contain the NF's identity anywhere, no regex against NRF's log alone can distinguish which NF registered. A regex-only fix would still silently pass/fail incorrectly.
- Do NOT remove the `|| true` from the per-NF loop as part of this fix without first confirming the new check logic is solid — the loop is currently non-fatal by design (informational, not blocking), and making it fatal on a first attempt risks turning a previously-invisible bug into a new source of false CI failures, the exact class of problem this whole investigation has been fixing.
- Do NOT bundle the Phase 2 audit into the same commit as the Phase 1 fix — keep them as separate commits/PRs like the pipefail fix was kept separate from the workflow split, so each change has a clear, reviewable, single-purpose diff (this matches the user's own preference for two-workflow separation of concerns seen in the handoff's Key Decisions).
- Do NOT assume `workflow_dispatch` alone is sufficient validation — per the handoff's Risks & Blockers, a PR/branch gets no automatic CI, so Phase 1 MUST end with an explicit manual trigger (`gh workflow run deploy-5g-lab.yml` or via the Actions UI) against the merged change, the same way run `34798235193` was used to verify the pipefail fix.

## Plan

### Phase 1: Fix the NRF per-NF registration check

**Goal:** Make `ci/wait-ready.sh`'s per-NF registration loop actually verify what it claims to verify, instead of silently always timing out.

**Why this approach:** The handoff's Key Findings establish the check's target behavior (each NF registers with NRF) is real and already indirectly proven by other passing checks — the fix only needs to change HOW that's observed, not add new functionality. Two approaches were identified in the deferred task; pick based on what's actually available:

1. **Investigate first**: SSH to the LXC (`COMPOSE_PROJECT_NAME=lab5g` required, per the handoff's Risks & Blockers) and check what NRF's SBI actually exposes — `docker compose exec -T nrf curl -s http://localhost:8000/nnrf-nfm/v1/nf-instances` (or the correct free5GC NRF discovery path — check `nrf/config/nrfcfg.yaml` and free5GC's NRF API docs for the exact endpoint) to see if a live list of registered NF instances (with type/status) is queryable directly.
2. **If the NRF discovery API is easily queryable from a NF-less container** (e.g. via `docker compose exec -T mongodb curl` or similar, matching the existing pattern in `wait-ready.sh`'s "NRF (SBI)" check which already does a raw TCP check from the mongodb container): replace the per-NF log-grep loop with per-NF API queries checking each NF's registration status directly. This is more correct (queries ground truth, not a log scrape) but adds a new dependency (an HTTP client available in whichever container issues the query).
3. **If the API route is awkward to query from CI** (auth, TLS, or no HTTP client available in any running container): fall back to checking each NF's OWN log for ITS OWN registration confirmation instead of NRF's log — e.g. `grep -qi 'registered\|NF Registration' < <(docker compose logs "${nf,,}" 2>&1)` for each NF, using process substitution from the start (don't reintroduce the pipefail bug). Requires finding what each of the 8 NFs actually logs on successful registration — likely NOT uniform across all 8, so this may need one pattern per NF rather than one shared pattern.
4. Whichever approach is chosen, keep the loop's `|| true` (non-fatal) until the new check has been observed to pass reliably across at least 2 live CI runs — only then consider making it a hard failure if that's desired.
5. Update the comment block above the loop in `ci/wait-ready.sh` (currently documents the SIGPIPE fix from this session) to also explain the pattern-matching fix and WHY the old approach couldn't work.
6. Update `docs/07-cicd.md` section 7 if this becomes a documented "6th fallo" — decide whether it merits its own subsection given it was never actually failing CI (just silently uninformative), unlike the pipefail bugs which caused real false failures.

**Files:** `ci/wait-ready.sh` (the per-NF loop, lines ~60-64 as of this handoff — re-check line numbers, they will have shifted), possibly `docs/07-cicd.md`.
**Validates with:** Live SSH check that the new per-NF check reports something other than TIMEOUT for at least one NF while NRF is known-healthy; then a full CI run via `workflow_dispatch` (`gh workflow run deploy-5g-lab.yml --repo J4F3ET/lab-free5gc`) and `gh run view --log --job=<wait-ready step>` to confirm the new check's output during a real deploy, matching the verification style used for the pipefail fix (see handoff's step 18).
**Rollback:** Revert to the previous (known-non-fatal, silently-wrong) pattern — since the check is `|| true`, reverting is zero-risk to the actual pipeline; worst case is just going back to uninformative-but-harmless output.

### Phase 2: Repo-wide audit for remaining pipe+grep-q SIGPIPE risks

**Goal:** Confirm (not assume) that no 5th instance of the pipefail/SIGPIPE bug pattern exists outside the two files already checked, closing out the bug class definitively.

**Why this approach:** The handoff's own stated lesson from this bug class ("a pipefail bug in a reusable pattern isn't fixed once — it's searched for across the whole repo") was applied only to `ci/`, scoped by the specific grep used. The bug's PRECONDITIONS (a script with `set -o pipefail`, using `docker compose logs X | grep -q PATTERN` or equivalent) could exist in any shell script in the repo, not just the two already found.

- Run a broader search than the one used this session: `grep -rn 'pipefail' --include='*.sh' .` to find every script with `pipefail` active, then for each, `grep -n '| *grep' <file>` to find every pipe-into-grep in those files specifically (narrower false-positive rate than searching all of `ci/` for any pipe-grep, which would also flag pipes that don't have the SIGPIPE precondition).
- Check `deploy/bootstrap-runner.sh`, `ci/collect-logs.sh`, `ci/preflight.sh`, and `ci/provision-subscriber.sh` specifically — these exist in the repo per the handoff's file list but were not checked this session for this bug pattern.
- For each match found, determine if `grep`'s output is used for its exit code (i.e., a `-q`/`-c` flag followed by `&&`, `||`, or an `if`) — only THOSE are actually vulnerable; a pipe whose grep output is captured as text (not just exit-code-checked) doesn't hit this bug.
- Fix any genuine instance found using the same process-substitution pattern as the three fixes in this session's handoff.
- If none are found, document that explicitly (e.g. a one-line note in `docs/07-cicd.md`'s pipefail subsection: "audited repo-wide on {date}, no further instances found") so a future session doesn't re-run the same audit from scratch.

**Files:** Whatever files the audit's `grep -rn` turns up — cannot be enumerated in advance; likely candidates are `deploy/bootstrap-runner.sh`, `ci/collect-logs.sh`, `ci/preflight.sh`, `ci/provision-subscriber.sh` based on the file list visible in the handoff, but the audit itself is the source of truth.
**Validates with:** The `grep -rn 'pipefail'` / `grep -n '| *grep'` two-step search producing a complete, reviewed list; any fix found validated the same way as Phase 1 (live reproduction of the exit-code difference before and after, per the handoff's Evidence & Data table).
**Rollback:** Each fix is an isolated one-line-pattern change (process substitution instead of a pipe) — revert per-file if any fix introduces a regression.

## Dependencies & Order

- Phase 1 and Phase 2 are independent — Phase 2's audit scope does not depend on Phase 1's fix. They can be done in either order or in parallel if using separate branches, though keeping them as separate PRs (per Anti-Goals) means they don't block each other's review.
- **Parallelizable**: Phase 1 and Phase 2 can run concurrently in separate worktrees/branches (e.g. `fix/nrf-registration-pattern` and `audit/pipefail-sigpipe-scan`) since neither reads or writes files the other touches. If parallelizing, merge whichever finishes first and rebase the other before its own PR to avoid stacking unrelated diffs.
- Within Phase 1, step 1 (investigate what NRF's API actually exposes) must complete before deciding between the API-query approach (step 2) and the per-NF-log approach (step 3) — this is a genuine fork in the plan, not just implementation detail.
- No beads/epic tracker is in use for this repo (`bd` not installed, confirmed in the handoff) — phase tracking for this plan is manual; use `TaskCreate` in the executing session instead of `bd update --claim`.

## Risks & Mitigations

- **Risk: the NRF discovery API requires auth/TLS setup not readily available from within the docker compose network.** Likelihood: moderate — free5GC's SBI interfaces sometimes require client certs depending on config. Mitigation: fall back to Phase 1 step 3 (per-NF own-log check) if step 1's investigation shows this.
- **Risk: each of the 8 NFs logs its own registration confirmation differently, making a single shared pattern (like the current one) impossible even for the fallback approach.** Likelihood: fairly high given free5GC NFs are separate codebases. Mitigation: budget for one pattern per NF rather than a shared one — this is more verbose but was already anticipated in Phase 1 step 3.
- **Risk: Phase 2's audit finds a genuine 5th bug instance in a script not covered by this session's CI verification path (e.g. `deploy/bootstrap-runner.sh`, which runs at LXC provisioning time, not per-deploy).** Likelihood: low but nonzero. Mitigation: any fix found still gets the same live-exit-code-reproduction verification discipline used throughout the handoff, regardless of which script it's in.
- **Risk: no CI signal on the PR for either phase** (per the handoff's Risks & Blockers — `ci/**` isn't a `deploy.yml` trigger path). Mitigation: both phases' "Validates with" steps explicitly call for a manual `workflow_dispatch` run after merge, not just a clean PR merge.

## Success Criteria

- Phase 1: the per-NF registration check in `ci/wait-ready.sh` reports something other than a blanket TIMEOUT for all 8 NFs on a live, healthy deploy — confirmed by reading the actual step output in a `workflow_dispatch`-triggered CI run's log, the same verification style as run `34798235193` in the handoff.
- Phase 1 (minimum viable): even if a per-NF API-based check isn't feasible, the check at minimum stops being SILENTLY wrong — either it correctly reports each NF's status, or it's removed/replaced with an honest "not implemented" marker rather than a misleading always-TIMEOUT loop.
- Phase 2: a documented, repo-wide `grep`-based audit result exists (either "N more instances found and fixed" with the same live-verification evidence as this session's 3 fixes, or an explicit "audited, none found" note) — not just "I looked at ci/ again."
- Both phases: no regression to the now-green `deploy-5g-lab` pipeline — a `workflow_dispatch` run after each phase's merge completes with `smoke: success`, matching the baseline established by run `34798235193`.

## Quick Start

```bash
# Restore full context
cat plans/handoffs/HANDOFF_standalone-b8404157_ci-pipefail-fix_2026-09-16.md

# Key source files for Phase 1
ci/wait-ready.sh              # the per-NF loop, ~line 60-64
nrf/config/nrfcfg.yaml        # check what SBI endpoint/auth NRF actually exposes
docs/07-cicd.md               # section 7, full bug history for context/tone match

# Baseline data to reference
# See handoff's Evidence & Data: CI run history table, profile behavior matrix,
# and the exact NRF log line (`Handle NFRegisterRequest`) that can't be matched today.

# Verify starting state (confirm the pipefail fixes are still in place and green)
gh run list --repo J4F3ET/lab-free5gc --branch master --limit 3

# First concrete action
# SSH to the LXC (COMPOSE_PROJECT_NAME=lab5g required) and check what NRF's SBI
# exposes for NF-instance discovery, to decide between Phase 1 step 2 (API-based
# check) and step 3 (per-NF own-log fallback):
#   ssh root@192.168.1.50
#   cd <repo path> && COMPOSE_PROJECT_NAME=lab5g docker compose exec -T nrf sh -c \
#     'curl -sk https://localhost:8000/nnrf-nfm/v1/nf-instances || echo "not reachable this way"'
```
