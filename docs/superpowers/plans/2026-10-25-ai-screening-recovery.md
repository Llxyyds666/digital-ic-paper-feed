# AI Screening Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Explain model validation failures safely and recover valid papers without charging unsent candidates.

**Architecture:** Typed, allowlisted validation errors remain in the AI boundary. The summary coordinator performs binary batch recovery and counts actually submitted candidate identities while preserving existing publication transactions.

**Tech Stack:** Python 3.11+, pytest, existing GitHub Actions and DeepSeek client.

## Global Constraints

- Keep the strict five-journal/ten-conference whitelist, existing topic schema and evidence requirements unchanged.
- Keep the DeepSeek model, full request accounting and token accounting unchanged. Never log model output, credentials, authorization headers or credential-bearing URLs.
- Keep the 100-candidate limit per Beijing day. Count candidate groups once when actually submitted within a run, including failed submissions, not all preselected groups. Retries and split children do not double-charge groups in that run. Across separate runs retain conservative additive accounting.
- Preserve successful prior judgments, summaries, identities and RSS addresses. Failed or unattempted candidates remain pending. No new application-wide request-count cap.

### Task 1: Safe model-response diagnostics

**Files:** Modify `src/ic_feed/ai.py`; test `tests/test_ic_ai.py`.

**Interfaces:** Produce `InvalidModelResponse(ValueError)` with a safe `.code` and a string beginning `invalid model response`. Existing `screen_batch(records, client, config, counter)` remains atomic and retains three schema attempts. Callers may identify this type to split only validation failures.

- [ ] Add table-driven failing tests using the existing paper/decision/response helpers: malformed JSON, duplicate JSON field, missing response envelope, wrong decision fields/types, invalid confidence/category/topics, contradictory scope, missing/duplicate/foreign keys. Assert typed error code and absence of supplied secret/raw output from its printable traceback.
- [ ] Run `python -m pytest tests/test_ic_ai.py -q`; verify new tests fail for the missing diagnostic type/codes, not fixtures.
- [ ] Add the minimal typed exception and fixed-code validation branches; preserve all rejection predicates, token/request totals and retry limits. Replace string-only propagation with typed propagation; preserve compatibility with legacy `ValueError('invalid model response')` from client test doubles.
- [ ] Run the same test file until green and self-review the diff for any weakening of validation or secret-bearing output.
- [ ] Commit the source and test files only, and write the red/green evidence report outside the repo under `ic-publish-tools`.

### Task 2: Submitted-candidate accounting and binary recovery

**Files:** Modify `src/ic_feed/summarize.py`; create `tests/test_ic_recovery.py`.

**Interfaces:** Consume `InvalidModelResponse` from Task 1. Keep `run_summary(...) -> SummaryStats` and usage JSON fields backward compatible.

- [ ] Add failing tests with real summary/state/usage publication and a deterministic client: a bad ten-paper batch succeeds as halves; one bad single-paper leaf leaves its key pending while good siblings and later batches publish; retry/split does not double-charge; a transport failure stops before later preselected papers and accounts only groups actually submitted; same-day next run uses remaining allowance and never requests already completed keys; existing selected historical records remain byte-for-byte equivalent in state.
- [ ] Run `python -m pytest tests/test_ic_recovery.py -q` and record expected failures.
- [ ] Add recursive coordinator logic: call `screen_batch`, track counter changes in a finally clause, bisect only invalid-response errors, accumulate valid sibling results, keep unresolved leaf failures visible, and stop on other fatal errors. Use `len(attempted_keys)` rather than `len(candidate_keys)` in both usage paths.
- [ ] Extend the CLI summary with `candidates={stats.candidates}` while retaining existing fields. Run recovery/publication/AI tests, then the full suite and `python -m ic_feed.validate`.
- [ ] Commit only reviewed recovery code/tests. Obtain read-only task and final branch reviews using a saved diff package.

### Task 3: Deploy and verify bounded recovery

**Files:** Create a dated audit note in `docs/operations`; adjust only today's legacy `ai_usage.json` candidate reservation after checking pinned evidence.

- [ ] Assert local/remote baseline is the diagnosed state (915 papers, 454 selected, 133 pending, today's candidates100/processed70/requests10/tokens58081). Assert the failed run used batch_size10 and stopped on invalid response after seven successful batches. If any check differs, do not adjust data.
- [ ] Preserve every field except today's candidates, changing 100 to80 to release the twenty demonstrably unsent candidates; document run/commit evidence and all unchanged cost fields.
- [ ] Run full tests, validator and final independent review. Publish by fast-forward only, with an expected-parent guard; never overwrite a concurrently advanced remote.
- [ ] Dispatch one normal summarize workflow using existing Secrets. Respect remaining twenty-candidate allowance and inspect diagnostic codes, completion counts, token deltas, pending state and Bark result. Do not print or copy Secrets.
- [ ] Verify Pages deployed the same commit's RSS, GUIDs are unique, prior454 judgments/summaries remain unchanged except explicitly requeued candidates, candidate total stays at most100, and local release matches remote with a clean worktree. Report measured results and any unresolved failures.
