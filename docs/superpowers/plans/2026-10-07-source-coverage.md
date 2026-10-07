# Strict source coverage implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Discover the missing flagship digital IC conference papers and IEEE journal metadata without weakening selection.

**Architecture:** Add bounded-page metadata adapters and integrate them with the existing durable collection state. Verified edition catalog gates conference membership independently of publication date. Reuse existing merge, enrichment, AI budget, atomic publication and RSS identities.

**Tech Stack:** Python 3.11+, pytest, urllib safe no-redirect metadata transport, GitHub Actions, Crossref/DataCite/IEEE APIs.

## Global Constraints

- Retain only the approved five journals and ten flagship conferences.
- Backfill 2026 conference editions; journals remain 30-day incremental.
- At most 100 AI candidates per Beijing day; no application-level API request-count cap.
- Preserve all previously judged records and summaries, existing RSS addresses, and stable DOI/IEEE identities.
- `IEEE_API_KEY` is environment-only; no credential-bearing URL may enter errors, logs, state or continuations.
- Only a completed harvest may advance a source watermark. Failed and incomplete sources are not reported as complete.
- Exclude workshops, companion, tutorials, invited talks, frontmatter and student forums.

### Task 1: IEEE discovery page adapter

**Files:** Create `src/ic_feed/sources/ieee.py`, `tests/test_ieee_source.py`.

**Interfaces:** Consume `PaperRecord`, `enrich.MetadataTransport`, `enrich._default_transport`, `enrich._valid_abstract`, `normalize.normalize_doi`, existing retry utilities. Produce:

```python
@dataclass(frozen=True)
class IeeePage:
    records: list[PaperRecord]
    item_count: int
    total_records: int

class IeeeFetchError(Exception):
    category: str
    detail: str

def request_page(query: str, from_date: date, until_date: date,
                 start_record: int, max_records: int, key: str, *,
                 timeout_seconds: float = 20, attempts: int = 3,
                 transport: MetadataTransport | None = None,
                 wait: Callable[[float], None] = time.sleep) -> IeeePage: ...
```

- [ ] Write failing behavioral tests for journal ISSN discovery, conference title + publication year discovery, explicit start_record paging, date window, Early Access admission, official article IDs, zero-vs-invalid envelopes, metadata/date parsing and credential-safe retries.

```python
def test_positive_total_without_articles_is_not_empty_success():
    calls = []
    def transport(method, url, headers, body, timeout):
        calls.append(url)
        return MetadataResponse(200, b'{"total_records":12}')
    with pytest.raises(IeeeFetchError) as error:
        request_page('journal:0018-9340', date(2026,9,7), date(2026,10,7),
                     1, 200, 'private-fixture-key', transport=transport, wait=lambda _: None)
    assert len(calls) == 3
    assert 'private-fixture-key' not in str(error.value)
```

- [ ] Run `python -m pytest tests/test_ieee_source.py -q`; confirm RED for missing adapter.
- [ ] Implement the page adapter. Endpoint is `https://ieeexploreapi.ieee.org/api/v1/search/articles`. Queries are `journal:<ISSN>` or `conference:<year>:<publication title>`. Send apikey, format=json, start_record (one-based), max_records<=200, sort_field=article_number, sort_order=asc, start_date/end_date=YYYYMMDD. Journal uses issn without content_type so Early Access is included. Conference uses publication_title, publication_year and content_type=Conferences. Never send doi/article_number discovery filters.
- [ ] Accept `total_records` and documented `totalfound` numeric nonnegative aliases; missing/invalid totals, missing/empty articles with positive total, malformed JSON and provider error envelopes fail and retry. Explicit zero with missing/empty articles succeeds. Raw item_count drives offsets. Parse publication_date full/month/year (UTC day/month defaults one), title, authors.full_name, abstract, doi, publication_title and official document URL/article_number identities. Skip individually unusable records without claiming they were unexamined.
- [ ] Reuse safe no-redirect transport. Retry network/429/5xx/invalid successful protocol up to attempts; fatal 4xx stop. Exceptions expose sanitized category/provider/status only; never chain credential URLs into reported details. Keep records in the adapter ungated; collection handles strict venue membership.
- [ ] Run the covering tests GREEN, self-review and commit only the two scoped files. Write the full report to the dispatched report path, returning status/commit/test summary only.

### Task 2: Edition-aware sources and collection integration

**Files:** Add `src/ic_feed/sources/conferences.py` and `config/conference_editions.json`; modify `src/ic_feed/collect.py`, `src/ic_feed/state.py`, `src/ic_feed/venues.py`, `src/ic_feed/summarize.py`, `config/scholarly_queries.json`, `.github/workflows/collect.yml`; add collection/edition and Beijing-day budget tests.

**Interfaces:** IEEE page adapter from Task 1; existing `ScholarlyHarvest`, `SourceContinuation`, `PaperRecord`, `FetchError`, `fetch_bytes`, `merge_into_state`.

- [ ] Write failing tests proving resumed IEEE offsets retain a frozen upper date, positive-total empty pages do not advance watermarks, API failure preserves earlier pages, official IEEE aliases deduplicate, IEEE/JSAP VLSI passes only the known main conference, and a 2025-published ASPLOS 2026 paper is admitted while 2025 edition/workshop papers are rejected.

```python
def test_2026_edition_is_not_calendar_year_filter():
    assert edition_accepts('asplos', '10.1145/3760250.3762220',
        'Proceedings of the 31st ACM International Conference on Architectural Support for Programming Languages and Operating Systems, Volume 1')
    assert not edition_accepts('asplos', '10.1145/2025.123', 'ASPLOS 2025 Companion')
```

- [ ] Add exact catalog entries for verified 2026 main proceedings and safe discovery for not-yet-published editions. FMCAD uses DataCite root wildcard enumeration and exact child DOI/type gate, excludes suffixes 1–5. CAV uses exact Springer book DOI roots and verifies chapter membership. ASPLOS uses exact volume DOI roots and publication container names, without Jan-2026 lower publication bound. Other conferences use exact year-bearing official metadata and existing venue matcher. Do not treat fuzzy query results as exact membership.
- [ ] Add robust paginated progress that stores upper bound/offset in opaque continuation cursor, retains initial window after retries/restarts, and advances completed watermark only to the frozen upper bound. New IEEE journal discovery initially overlaps 30 days; conference backfill starts at the 2026 edition's earliest publication window. Retain successful partial records and diagnose incomplete pages separately from completed empty sources.
- [ ] Configure IEEE API sources with workflow env `IEEE_API_KEY: ${{ secrets.IEEE_API_KEY }}`. Keep official RSS/Crossref fallback coverage, avoid redundant blocked IEEE RSS requests when API primary is configured, and remove broad obsolete conference score scans from active configuration. Do not discard paper history or change AI limits.
- [ ] Extend the allowed cursor-source identifiers to `ieee` and `conference` without changing the version-2 state field layout; budget day uses `now.astimezone(timezone(timedelta(hours=8))).date()` so the 100-candidate cap follows Beijing midnight.
- [ ] Run targeted tests and full pytest/validate, inspect source metadata from primary endpoints and record counts. Update README with backfill semantics and unchanged RSS addresses.

### Task 3: Review, publish and verify live behavior

**Files:** Reviewed code/config/docs and normal generated publication outputs only.

- [ ] Review the complete branch against global constraints. Resolve important findings and re-run covering tests.
- [ ] Safely synchronize current GitHub main without replacing newer bot data; commit the reviewed change and publish with non-force exact-parent checks.
- [ ] Dispatch the collection workflow with repository Secrets. Inspect completion, source candidate counts, pending queue, unique identities, and failure/incomplete status. Use normal daily AI budget for a real summary run; never requeue judged history en masse.
- [ ] Verify Actions, Pages and RSS XML. Report actual additional papers, remaining queue and any conference edition awaiting publication rather than asserting complete coverage without evidence.
