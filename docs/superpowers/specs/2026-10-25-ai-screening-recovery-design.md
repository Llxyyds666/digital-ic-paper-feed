# AI screening diagnostics and safe recovery

User approved fixing diagnostics and failed-run recovery after run 40168274844.

## Constraints

- Keep the strict five-journal/ten-conference whitelist, existing topic schema and evidence requirements unchanged.
- Keep the DeepSeek model, full request accounting and token accounting unchanged. Never log model output, credentials, authorization headers or credential-bearing URLs.
- Keep the 100-candidate limit per Beijing day. Count candidate groups once when actually submitted within a run, including failed submissions, not all preselected groups. Retries and split children do not double-charge groups in that run. Across separate runs retain conservative additive accounting.
- Preserve successful prior judgments, summaries, identities and RSS addresses. Failed or unattempted candidates remain pending. No new application-wide request-count cap.

## Design

Expose an `InvalidModelResponse(ValueError)` with allowlisted diagnostic codes. Preserve the existing JSON/schema rejection rules and retry policy, but retain a safe code through the client and screening validators. Unknown values and raw model text must not appear in exception strings.

When a batch still returns an invalid model response after existing retries, bisect it and apply the same strict screening to each half. Save valid sibling decisions even if one single-paper leaf remains invalid; keep failed leaves pending and report the run as partial failure. Do not split authentication, HTTP or transport failures: stop that run and preserve completed work. This is finite recovery bounded by the original candidate slice and binary tree, not unlimited retry.

Track actually submitted group keys in a set using the request counter before/after each call. Use its size for usage and notification candidate counts. The new logs include attempted, processed, selected and remaining counts; partial failures remain visible rather than being mislabeled successful.

For today's legacy overreservation only, correct candidates from 100 to 80 if pinned run/commit evidence still matches: 70 completed in seven ten-paper batches, then one ten-paper invalid batch with three requests; the final twenty were never submitted. Preserve all request/token totals and every other day's entries. Record this one-time adjustment in a committed audit note, fail closed on drift, then run remaining allowance through the normal workflow. Do not globally recompute old usage from processed counts.

## Validation

Use failing tests first for safe error classes, malformed JSON/fields/topics/keys, recursive splitting, failed single leaves, fatal transport short-circuiting, accurate per-run attempted counts, same-day resume, and historical preservation. Run the full suite and RSS validator; obtain independent review before publishing. Verify the deployed workflow and public RSS after the real bounded rerun.
