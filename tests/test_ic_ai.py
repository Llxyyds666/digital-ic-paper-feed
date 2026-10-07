from datetime import datetime, timezone
import json
from dataclasses import asdict
from urllib.error import HTTPError

import pytest

from ic_feed.ai import DeepSeekClient, RequestCounter, _screening_messages, screen_batch
from ic_feed.config import AiConfig, load_config
from ic_feed.models import PaperRecord
from ic_feed.normalize import record_key


def config():
    return AiConfig("https://api.deepseek.com", "deepseek-v4-flash-vision-exp", 100, 10, 1200, 4096, 8192, 512)


def paper(abstract="We implement a RISC-V core in RTL and report synthesis timing."):
    return PaperRecord("RTL implementation of a RISC-V core", abstract, ["A. Author"], "IEEE TCAD", datetime(2026, 10, 7, tzinfo=timezone.utc), "10.1000/ic.1", "https://doi.org/10.1000/ic.1", ["crossref"], ["1"])


def decision(record, **changes):
    return {"key": record_key(record), "relevant": True, "confidence": 0.94, "category": "rtl-microarchitecture", "matched_topics": ["digital-design"], "summary_zh": "本文实现 RTL 微架构并报告综合时序。", "reason": "实际数字电路设计贡献。", **changes}


def response(raw):
    return {"choices": [{"message": {"content": json.dumps(raw)}}], "usage": {"prompt_tokens": 30, "completion_tokens": 20}}


def test_screening_prompt_targets_actual_digital_hardware_and_excludes_software_only():
    messages = _screening_messages([paper("")], config())
    prompt = messages[0]["content"]
    for phrase in ("RTL", "UVM", "CDC", "RDC", "DFT", "STA", "HLS", "pure software", "analog", "缺少摘要"):
        assert phrase in prompt
    payload = json.loads(messages[1]["content"])
    assert payload["papers"][0]["abstract_missing"] is True
    assert len(payload["categories"]) == 8
    assert "diamond" not in prompt.lower()


def test_neural_network_verification_accelerator_is_not_chip_verification_methodology():
    prompt = _screening_messages([paper()], config())[0]["content"]
    assert "neural-network robustness" in prompt
    assert "digital-design, not digital-verification" in prompt


def test_truncated_abstract_is_explicitly_marked_and_not_claimed_complete():
    messages = _screening_messages([paper("a" * 1300)], config())
    payload = json.loads(messages[1]["content"])["papers"][0]
    assert payload["abstract_truncated"] is True
    assert len(payload["abstract"]) == config().max_abstract_chars
    assert "do not claim the full paper or abstract lacks" in messages[0]["content"]


@pytest.mark.parametrize("topics", [["diamond-power-rf-detectors"], ["digital-design", "digital-design"], ["not-a-topic"]])
def test_strict_topic_schema_retries_invalid_responses_and_rejects_legacy_labels(topics):
    record = paper()
    calls = []
    def transport(*args):
        calls.append(args)
        return response({"decisions": [decision(record, matched_topics=topics)]})
    client = DeepSeekClient("not-a-real-secret", config(), transport=transport, wait=lambda _: None)
    counter = RequestCounter()
    with pytest.raises(ValueError, match="invalid model response"):
        screen_batch([record], client, config(), counter)
    assert counter.used == 3


def test_missing_abstract_never_publishes_model_invented_results():
    record = paper("")
    client = DeepSeekClient("test-key", config(), transport=lambda *args: response({"decisions": [decision(record, summary_zh="实测芯片功耗降低80%。")]}), wait=lambda _: None)
    result = screen_batch([record], client, config(), RequestCounter())[0]
    assert "仅据标题，缺少摘要" in result.summary_zh
    assert "80%" not in result.summary_zh
    assert result.confidence <= 0.7


def test_transient_deepseek_failure_retries_and_accounts_for_successful_tokens():
    record = paper()
    calls = 0
    def transport(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(args[0], 503, "busy", None, None)
        return response({"decisions": [decision(record)]})
    client = DeepSeekClient("test-key", config(), transport=transport, wait=lambda _: None)
    counter = RequestCounter()
    assert screen_batch([record], client, config(), counter)[0].matched_topics == ["digital-design"]
    assert counter.used == 2
    assert client.total_tokens == 50


def test_model_environment_override_keeps_user_default_when_unset(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"collection": {"lookback_days": 30, "raw_feed_max_items": 2000, "http_timeout_seconds": 30, "http_attempts": 3}, "ai": asdict(config()), "publication": {"title": "Digital IC Paper Feed", "base_url": "https://example.com"}}), encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    assert load_config(path).ai.model == "deepseek-v4-flash-vision-exp"
    monkeypatch.setenv("DEEPSEEK_MODEL", "another-deepseek-model")
    assert load_config(path).ai.model == "another-deepseek-model"
