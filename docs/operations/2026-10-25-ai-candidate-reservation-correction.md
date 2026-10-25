# Legacy candidate-reservation correction

Scope: only the candidate reservation in the 2026-10-25 usage entry.

Evidence was pinned before correction:

- Failed workflow: https://github.com/Llxyyds666/digital-ic-paper-feed/actions/runs/40168274844
- Workflow source: `11c43a90fde7891e76be9950f85e2929b80d455b`.
- Published partial-result baseline: `dab2e1c2eec1325bacfd5d13568068e1e9816b99`.
- Configured screening batch size10; original summary code precharged all100 selected candidate groups before any call.
- Run reported `processed=70 selected=45 requests=10 remaining=133`, with `invalid model response` terminating screening. Its seven completed ten-paper batches require at least seven requests. The final invalid batch exhausted the three schema attempts, accounting for the other three requests; only80 candidate groups were sent. No later candidate batch, overview or recommendation ran.
- Pinned state contains915 records,454 selected and133 pending. It is not modified by this correction.

Change today's `candidates` from100 to80, releasing only the20 demonstrably unsent groups. Do not substitute processed70 for attempted80. Do not infer or repair raw model decisions, which were not retained.

All other fields are preserved: processed70, selected45, requests10, prompt_tokens47483, completion_tokens10598, total_tokens58081 and token_usage_complete=true. All previous dates remain unchanged. Costs are neither removed nor estimated.

The replacement coordinator charges actually submitted group keys once per run, including failures, retries and split children. Separate runs conservatively add their attempted groups, and the existing Beijing-day limit remains100. A single normal workflow rerun may now process at most20 additional candidate groups; already completed records remain excluded unless they were explicitly requeued by existing abstract enrichment.
