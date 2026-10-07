# Digital IC Literature Feed Implementation Plan

> **For agentic workers:** Execute tasks with independent ownership and review between tasks. Steps use checkbox syntax for tracking.

**Goal:** Create and deploy an independent digital IC design and verification literature feed in Llxyyds666/digital-ic-paper-feed.

**Architecture:** Reuse the tested paper-feed modules under ic_feed, starting with empty state and domain-specific queries. Domain decisions, collection and publication each keep explicit interfaces; workflow secrets remain environment-only.

**Tech Stack:** Python 3.11+, feedparser, curl-cffi, pytest, official journal RSS/Crossref, optional Semantic Scholar, DeepSeek, GitHub Actions/Pages, official Bark.

## Global Constraints

- Daily AI candidate limit 100; batch size 10; no daily AI request limit.
- Three attempts per transient network failure; state updates are atomic.
- First database lookback 30 days; independent query progress.
- Only actual digital IC design and verification contributions qualify.
- Only 15 approved flagship publication venues qualify; no arXiv, workshop, news or near-name conference admission. User confirmed JSSC/ISSCC/VLSI additions.
- Credentials are never committed or logged; no fabricated AI results.

## Task 1: Domain and collection

- [x] Copy reusable src modules into src/ic_feed and change import namespace mechanically; do not copy historical state or summaries.
- [x] Create config/queries.json with strong digital terms and hardware-context weak terms, config/scholarly_queries.json with independent source/query entries, and verified config/rss_sources.tsv.
- [x] Change ic_feed.collect.main to iterate each configured source/query independently and report added/filtered/pending counts. Preserve generic adapters without enabling arXiv in the strict feed.
- [x] Test word boundaries, positive digital papers, negative unrelated/software/analog papers, all independent query watermarks and partial failures; run python -m pytest -q tests/test_collection.py tests/test_domain.py.

## Task 2: AI and publication

- [x] Define IC categories, digital-design/digital-verification labels, Chinese category labels and evidence-first AI scope.
- [x] Preserve original abstracts for recommendation, missing-abstract disclosure, strict response validation, transient retries and cumulative history.
- [x] Produce combined, design and verification RSS through the same atomic summary publication transaction. Update Bark wording and grouping.
- [x] Test relevant/excluded/missing-abstract decisions, topic subsets, both labels, same-title duplicate publication and recommendation payloads.

## Task 3: Automation and delivery

- [x] Add initial empty state/feeds and a clear HTML landing page, README with source rationale, subscription URLs, secret setup and exact schedule.
- [x] Add CI, six-hour collection, 09:17 Beijing daily summary and Pages deployment workflows. Missing DeepSeek key skips AI with explicit status.
- [ ] Run full local pytest and generated-output validation, and actual no-AI collection.
- [ ] Create the public repository, push reviewed files, enable Pages, dispatch CI/collection and verify published RSS and workflow conclusions.
- [ ] Report actual live URLs and any credential-dependent work still waiting for user configuration.
