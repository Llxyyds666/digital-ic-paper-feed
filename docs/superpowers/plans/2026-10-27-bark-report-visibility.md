# Bark Report Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make daily report/recommendation notifications separately visible and replay two missing reports without AI cost.

**Architecture:** Keep normal two-message plans; alter only Bark presentation and safe receipts. A small separate CLI constructs dated statistics messages from committed usage and calls the same sender in single-report mode.

**Tech Stack:** Existing Python3.11+, pytest and GitHub Actions.

## Global Constraints

- No changes to paper state, usage, RSS/HTML content, selection, model, API budgets or secrets.
- Official Bark JSON endpoint and environment-only BARK_TOKEN; no raw response or notification contents in logs.
- Groups: digital-ic-paper-feed-reports and digital-ic-paper-feed-recommendations; archive="1"; no collapse IDs.
- Manual-only replay, maximum two validated dates, no AI calls; distinguish service acceptance from device receipt.

### Task 1: Grouping, archiving and receipts

Files: src/ic_feed/bark.py, notify.py, notification.py; tests/test_bark.py.

- [ ] Add failing assertions for distinct groups, isArchive1, one-report direct plans using the reports group, successful message receipts and accurate nonfatal CLI result totals without secret leakage.
- [ ] Run focused tests with fresh workspace basetemp and process PYTHONUTF8=1; confirm expected failures.
- [ ] Extract build_statistics_message with the existing validation/format; have build_notification_plan delegate unchanged. Update Bark payload groups/archive and safe success receipts; consume results in notify CLI and print totals without changing regular workflow nonfatal behavior.
- [ ] Focused tests pass, including existing retry/HTTP validation/redirect/secret tests.

### Task 2: Dated report replay

Files: new src/ic_feed/resend_reports.py, .github/workflows/resend_reports.yml, tests/test_report_resend.py.

- [ ] Write failing tests for dated reports based on persisted usage, all input validation before any send, no recommendation or AI call, dry-run, partial delivery independence/nonzero status and no file mutation.
- [ ] Implement the CLI and manual workflow using build_statistics_message and send_plan; normal persisted-plan validation remains unchanged. Each date calls send_plan with a direct one-message plan.
- [ ] Test workflow has only manual trigger, contents:read, dates in env quoted as data, and no AI secrets or write/publish steps. Run focused tests and report red/green evidence.

### Task 3: Verify, deploy, replay

- [ ] Run the full suite with UTF8/fresh basetemp and ic_feed.validate; compare unchanged data-file Git hashes to baseline.
- [ ] Obtain independent whole-diff review. Publish with expected-current-parent/non-force protections.
- [ ] Dispatch only resend_reports.yml with dates2026-10-26,2026-10-27. Inspect two dated acceptance receipts and workflow result; do not trigger summarize.yml.
- [ ] Confirm remote/local clean release, original AI usage/state/feed hashes unchanged by replay. Tell user the report group and what has actually been accepted; request phone receipt confirmation without asserting it.
