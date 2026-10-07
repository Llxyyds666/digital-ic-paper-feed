from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from ic_feed.collect import merge_into_state
from ic_feed.filtering import load_rules
from ic_feed.models import PaperRecord
from ic_feed.normalize import group_records, record_key
from ic_feed.state import FeedState


def paper(doi=None, **changes):
    record = PaperRecord(
        "RTL synthesis for a digital accelerator", "An evaluated RTL implementation.",
        ["Author"], "IEEE Transactions on Computer-Aided Design of Integrated Circuits and Systems",
        datetime(2026, 10, 1, tzinfo=timezone.utc), doi,
        "http://ieeexplore.ieee.org/document/11354530", ["rss"],
        ["http://ieeexplore.ieee.org/document/11354530"],
    )
    return replace(record, **changes)


def test_same_official_ieee_document_merges_after_doi_enrichment():
    known = paper("10.1109/tcad.2026.3654920", url="https://doi.org/10.1109/tcad.2026.3654920")
    rss = paper(published_at=datetime(2026, 1, 15, tzinfo=timezone.utc))
    groups = group_records([known, rss])
    assert list(groups) == [record_key(known)]


def test_processed_ieee_rss_alias_does_not_requeue_or_count_as_new():
    known = paper("10.1109/tcad.2026.3654920", ai_relevant=True,
                  summary_zh="已有中文摘要。", categories=["digital-design"])
    rss = paper()
    state = FeedState(papers={record_key(known): known})
    stats = merge_into_state(state, [rss], load_rules(Path("config/queries.json")))
    assert stats.added == 0
    assert stats.merged == 1
    assert len(state.papers) == 1
    assert state.pending_ai == []
    assert state.papers[record_key(known)].summary_zh == "已有中文摘要。"


def test_legacy_pending_rss_alias_is_removed_without_rerunning_existing_ai():
    known = paper("10.1109/tcad.2026.3654920", ai_relevant=False, summary_zh="排除。")
    rss = paper()
    state = FeedState(papers={record_key(known): known, record_key(rss): rss},
                      pending_ai=[record_key(rss)])
    merge_into_state(state, [], load_rules(Path("config/queries.json")))
    assert len(state.papers) == 1
    assert state.pending_ai == []
    assert state.papers[record_key(known)].ai_relevant is False


def test_explicit_requeue_of_processed_record_is_preserved():
    known = paper("10.1109/tcad.2026.3654920", ai_relevant=True)
    state = FeedState(papers={record_key(known): known}, pending_ai=[record_key(known)])
    merge_into_state(state, [paper()], load_rules(Path("config/queries.json")))
    assert state.pending_ai == [record_key(known)]
    assert len(state.papers) == 1


def test_untrusted_document_host_and_conflicting_dois_do_not_merge():
    known = paper("10.1109/tcad.2026.3654920")
    spoof = paper(url="https://example.org/document/11354530", source_ids=[])
    assert len(group_records([known, spoof])) == 2
    other = paper("10.1109/tcad.2026.9999999")
    assert len(group_records([known, other, paper()])) == 3
