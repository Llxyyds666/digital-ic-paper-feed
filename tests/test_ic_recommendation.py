from datetime import datetime, timezone
import json

from ic_feed.ai import RequestCounter
from ic_feed.bark import BARK_GROUP, BARK_ICON_URL
from ic_feed.config import AiConfig
from ic_feed.models import PaperRecord
from ic_feed.normalize import record_key
from ic_feed.notification import build_notification_plan
from ic_feed.recommend import recommend_one


def paper(abstract):
    return PaperRecord("Formal verification of an RTL CPU", abstract, ["A"], "IEEE TCAD", datetime(2026, 10, 7, tzinfo=timezone.utc), "10.1000/formal.1", "https://example.com/paper", ["crossref"], ["1"], ["formal-assertions-equivalence", "digital-verification"], "通过断言验证数字CPU。", True, 0.9)


def config():
    return AiConfig("https://api.deepseek.com", "deepseek-v4-flash-vision-exp", 100, 10, 1200, 4096, 8192, 512)


def test_recommendation_preserves_full_original_abstract_and_retries_schema_failures():
    record = paper("Full original abstract. " * 200)
    class Client:
        calls = 0
        def complete_json(self, messages, max_tokens, counter):
            counter.consume()
            self.calls += 1
            payload = json.loads(messages[1]["content"])
            assert payload["candidates"][0]["abstract"] == record.abstract
            assert payload["candidates"][0]["abstract_missing"] is False
            return {"key": "wrong", "reason": "坏响应"} if self.calls == 1 else {"key": record_key(record), "reason": "RTL断言验证方法适合入门学习。"}
    client = Client()
    result = recommend_one([record], client, config(), RequestCounter())
    assert result.key == record_key(record)
    assert client.calls == 2


def test_missing_abstract_recommendation_does_not_claim_measured_results():
    record = paper("")
    class Client:
        def complete_json(self, messages, max_tokens, counter):
            counter.consume()
            assert json.loads(messages[1]["content"])["candidates"][0]["abstract_missing"] is True
            return {"key": record_key(record), "reason": "实测覆盖率达100%。"}
    result = recommend_one([record], Client(), config(), RequestCounter())
    assert "缺少摘要" in result.reason
    assert "100%" not in result.reason


def test_bark_names_and_empty_recommendation_are_digital_ic_specific():
    plan = build_notification_plan(candidates=12, processed=12, selected=4, focus_selected=0, base_url="https://example.com", recommendation=None, recommendation_record=None, recommendation_failed=False)
    assert plan.messages[0].title == "数字 IC 文献日报"
    assert "设计与验证方向" in plan.messages[0].body
    assert plan.messages[1].body == "今日无数字 IC 设计与验证方向推荐"
    assert BARK_GROUP == "digital-ic-paper-feed"
    assert BARK_ICON_URL.endswith("/assets/ic-bark-icon.png")
