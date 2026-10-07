from datetime import datetime, timezone
import json
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from ic_feed.config import AiConfig, AppConfig, CollectionConfig, PublicationConfig
from ic_feed import focus
from ic_feed.models import PaperRecord
from ic_feed.notification import load_notification_plan
from ic_feed.normalize import record_key
from ic_feed.state import FeedState, load_state, save_state
from ic_feed.summarize import run_summary
from ic_feed import summarize


def config():
    return AppConfig(CollectionConfig(30, 2000, 30, 3), AiConfig("https://api.deepseek.com", "deepseek-v4-flash-vision-exp", 100, 10, 1200, 4096, 8192, 512), PublicationConfig("Digital IC Paper Feed", "https://example.com/digital-ic-paper-feed"))


def paper(index, topics, doi=None):
    return PaperRecord(f"RTL research {index}", "Actual digital hardware results.", ["A"], "IEEE Transactions on Computers", datetime(2026, 10, 7, tzinfo=timezone.utc), doi or f"10.1000/ic.{index}", f"https://example.com/{index}", ["crossref"], [str(index)], ["rtl-microarchitecture", *topics], "数字硬件研究摘要。", True, 0.9)


def titles(xml):
    return {item.findtext("title") for item in ElementTree.fromstring(xml).findall("./channel/item")}


def test_design_and_verification_feeds_are_precise_subsets_with_stable_guids():
    records = [paper(1, ["digital-design"]), paper(2, ["digital-verification"]), paper(3, ["digital-design", "digital-verification"])]
    state = FeedState(papers={record_key(p): p for p in records})
    feeds = focus.render_focus_feeds(state, config(), set(), {})
    assert set(feeds) == {"design_verification_feed.xml", "design_feed.xml", "verification_feed.xml"}
    assert titles(feeds["design_feed.xml"]) == {records[0].title, records[2].title}
    assert titles(feeds["verification_feed.xml"]) == {records[1].title, records[2].title}
    assert titles(feeds["design_verification_feed.xml"]) == {p.title for p in records}
    first = ElementTree.fromstring(feeds["design_feed.xml"]).find("./channel/item/guid").text
    records[0].summary_zh = "更新中文摘要。"
    again = focus.render_focus_feeds(state, config(), set(), {})
    assert ElementTree.fromstring(again["design_feed.xml"]).find("./channel/item/guid").text == first


def test_focus_overrides_apply_to_individual_topic_feeds():
    record = paper(1, [])
    state = FeedState(papers={record_key(record): record})
    feeds = focus.render_focus_feeds(state, config(), set(), {record_key(record): frozenset({"digital-verification"})})
    assert titles(feeds["verification_feed.xml"]) == {record.title}
    assert not titles(feeds["design_feed.xml"])
    assert record.categories == ["rtl-microarchitecture"]


def test_no_ai_quarantine_cleanup_regenerates_all_three_topic_feeds(tmp_path):
    artifact = paper(1, ["digital-design"], "10.6084/m9.figshare.123")
    state_path = tmp_path / "state.json"
    save_state(state_path, FeedState(papers={record_key(artifact): artifact}, pending_ai=[record_key(artifact)]))
    class NoClient:
        def complete_json(self, *args):
            raise AssertionError("quarantine must not request AI")
    stats = run_summary(config(), state_path, NoClient(), datetime(2026, 10, 7, tzinfo=timezone.utc), output_dir=tmp_path)
    assert stats.requests == 0
    for filename in ("design_verification_feed.xml", "design_feed.xml", "verification_feed.xml"):
        assert not titles((tmp_path / filename).read_text(encoding="utf-8"))
    assert load_state(state_path).pending_ai == []


class ScreeningClient:
    def complete_json(self, messages, max_tokens, counter):
        counter.consume()
        payload = json.loads(messages[-1]["content"])
        if "papers" in payload:
            return {"decisions": [{"key": item["key"], "relevant": True, "confidence": 0.95, "category": "rtl-microarchitecture", "matched_topics": ["digital-design"], "summary_zh": "论文研究数字 RTL 设计。", "reason": "数字硬件实现研究。"} for item in payload["papers"]]}
        if "candidates" in payload:
            return {"key": payload["candidates"][0]["key"], "reason": "RTL实现方法适合入门学习。"}
        return {"html": "<section><h2>数字 IC 论文概览</h2></section>"}


def pending_state(tmp_path):
    record = paper(1, [])
    record.ai_relevant = None
    record.summary_zh = None
    record.categories = []
    state_path = tmp_path / "state.json"
    save_state(state_path, FeedState(papers={record_key(record): record}, pending_ai=[record_key(record)]))
    return state_path, record


@pytest.mark.parametrize('previous_day,processed',[('2026-10-07',1),('2026-10-08',0)])
def test_daily_backfill_budget_resets_at_beijing_midnight(tmp_path,previous_day,processed):
    state_path,_ = pending_state(tmp_path)
    usage = {'date':previous_day,'candidates':100,'processed':100,'selected':0,'requests':10,
        'prompt_tokens':0,'completion_tokens':0,'total_tokens':0,'token_usage_complete':True}
    (tmp_path/'ai_usage.json').write_text(json.dumps([usage]),encoding='utf-8')
    stats = run_summary(config(),state_path,ScreeningClient(),
        datetime(2026,10,7,16,1,tzinfo=timezone.utc),output_dir=tmp_path)
    assert stats.processed == processed
    if processed:
        assert json.loads((tmp_path/'ai_usage.json').read_text(encoding='utf-8'))[-1]['date'] == '2026-10-08'


def test_online_summary_publishes_all_topic_feeds_and_recommendation_plan(tmp_path):
    state_path, record = pending_state(tmp_path)
    plan_path = tmp_path / "notification.json"
    stats = run_summary(config(), state_path, ScreeningClient(), datetime(2026, 10, 7, tzinfo=timezone.utc), output_dir=tmp_path, bark_enabled=True, notification_plan_path=plan_path)
    assert (stats.processed, stats.selected, stats.requests, stats.failed) == (1, 1, 3, False)
    assert titles((tmp_path / "design_feed.xml").read_text(encoding="utf-8")) == {record.title}
    assert titles((tmp_path / "design_verification_feed.xml").read_text(encoding="utf-8")) == {record.title}
    assert not titles((tmp_path / "verification_feed.xml").read_text(encoding="utf-8"))
    assert load_state(state_path).pending_ai == []
    plan = load_notification_plan(plan_path)
    assert "数字 IC 设计" in plan.messages[1].body
    assert plan.messages[1].url == record.url


def test_topic_feed_cannot_be_overwritten_by_notification_plan(tmp_path):
    state_path, _ = pending_state(tmp_path)
    with pytest.raises(ValueError):
        run_summary(config(), state_path, ScreeningClient(), datetime(2026, 10, 7, tzinfo=timezone.utc), output_dir=tmp_path, bark_enabled=True, notification_plan_path=tmp_path / "verification_feed.xml")
    assert len(load_state(state_path).pending_ai) == 1
    assert not (tmp_path / "verification_feed.xml").exists()


def test_new_topic_feed_staging_failure_preserves_all_previous_publication(tmp_path, monkeypatch):
    state_path, _ = pending_state(tmp_path)
    original_state = state_path.read_bytes()
    filenames = ("ai_summary_feed.xml", "ai_summary.html", "design_verification_feed.xml", "design_feed.xml", "verification_feed.xml")
    for filename in filenames:
        (tmp_path / filename).write_text("last good publication", encoding="utf-8")
    real_stage = summarize.stage_text
    def fail_one(path, contents):
        if path.name == "verification_feed.xml":
            raise OSError("simulated disk failure")
        return real_stage(path, contents)
    monkeypatch.setattr(summarize, "stage_text", fail_one)
    with pytest.raises(OSError, match="simulated disk failure"):
        run_summary(config(), state_path, ScreeningClient(), datetime(2026, 10, 7, tzinfo=timezone.utc), output_dir=tmp_path)
    assert state_path.read_bytes() == original_state
    assert all((tmp_path / filename).read_text(encoding="utf-8") == "last good publication" for filename in filenames)
    assert not (tmp_path / "ai_usage.json").exists()


def test_empty_queue_generates_no_recommendation_bark_plan_without_ai(tmp_path):
    state_path = tmp_path / "state.json"
    save_state(state_path, FeedState.empty())
    class NoClient:
        def complete_json(self, *args):
            raise AssertionError("empty queue must not request AI")
    plan_path = tmp_path / "notification.json"
    stats = run_summary(config(), state_path, NoClient(), datetime(2026, 10, 7, tzinfo=timezone.utc), output_dir=tmp_path, bark_enabled=True, notification_plan_path=plan_path)
    assert (stats.candidates, stats.requests, stats.failed) == (0, 0, False)
    plan = load_notification_plan(plan_path)
    assert "今日候选：0 篇" in plan.messages[0].body
    assert plan.messages[1].body == "今日无数字 IC 设计与验证方向推荐"


class MetadataEnricher:
    def __init__(self, *, resolved_to_history=False):
        self.resolved_to_history = resolved_to_history

    def enrich(self, state, keys):
        old_key = keys[0]
        record = state.papers.pop(old_key)
        record.doi = "10.1109/tc.2026.123456"
        state.papers[record_key(record)] = record
        state.pending_ai = [record_key(record)]
        if self.resolved_to_history:
            record.ai_relevant = True
            record.summary_zh = "历史摘要，不重新请求 AI。"
            record.categories = ["rtl-microarchitecture", "digital-design"]
            state.pending_ai = []
        return SimpleNamespace(enriched=0, metadata_enriched=1, ieee_queried=1)


def test_ieee_doi_promotion_updates_raw_rss_in_summary_transaction(tmp_path):
    state_path, record = pending_state(tmp_path)
    stats = run_summary(config(), state_path, ScreeningClient(), datetime(2026, 10, 7, tzinfo=timezone.utc), output_dir=tmp_path, enricher=MetadataEnricher())
    assert stats.processed == 1
    raw = ElementTree.fromstring((tmp_path / "filtered_feed.xml").read_text(encoding="utf-8"))
    assert raw.findtext("channel/item/guid") == "doi:10.1109/tc.2026.123456"
    assert raw.findtext("channel/item/description") == record.abstract


def test_metadata_only_history_merge_is_persisted_without_ai(tmp_path):
    state_path, record = pending_state(tmp_path)
    class NoClient:
        def complete_json(self, *args):
            raise AssertionError("history collision must not request AI")
    stats = run_summary(config(), state_path, NoClient(), datetime(2026, 10, 7, tzinfo=timezone.utc), output_dir=tmp_path, enricher=MetadataEnricher(resolved_to_history=True))
    assert stats.requests == 0
    assert "doi:10.1109/tc.2026.123456" in load_state(state_path).papers
    assert titles((tmp_path / "filtered_feed.xml").read_text(encoding="utf-8")) == {record.title}
    assert titles((tmp_path / "design_feed.xml").read_text(encoding="utf-8")) == {record.title}


def test_failed_ai_keeps_completed_metadata_enrichment_for_retry(tmp_path):
    state_path, record = pending_state(tmp_path)
    class FailedClient:
        def complete_json(self, *args):
            raise RuntimeError("temporary upstream failure")
    stats = run_summary(config(), state_path, FailedClient(), datetime(2026, 10, 7, tzinfo=timezone.utc), output_dir=tmp_path, enricher=MetadataEnricher())
    assert stats.failed
    state = load_state(state_path)
    assert state.pending_ai == ["doi:10.1109/tc.2026.123456"]
    assert titles((tmp_path / "filtered_feed.xml").read_text(encoding="utf-8")) == {record.title}
    assert not titles((tmp_path / "ai_summary_feed.xml").read_text(encoding="utf-8"))
