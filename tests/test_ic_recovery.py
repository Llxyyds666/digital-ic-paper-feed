"""Real coordinator/client regressions for invalid responses and partial runs."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from urllib.error import HTTPError

from ic_feed.ai import DeepSeekClient
from ic_feed.normalize import record_key
from ic_feed.state import FeedState, load_state, save_state
from ic_feed.summarize import run_summary
from test_ic_publication import config, paper, titles


NOW = datetime(2026, 10, 25, 6, tzinfo=timezone.utc)


def queue(tmp_path, count, *, history=False):
    records = [paper(index, []) for index in range(1, count + 1)]
    for record in records:
        record.ai_relevant = None
        record.summary_zh = None
        record.categories = []
    keys = [record_key(record) for record in records]
    if history:
        records.append(paper(999, ["digital-design"]))
    path = tmp_path / "state.json"
    save_state(path, FeedState(papers={record_key(p): p for p in records}, pending_ai=keys))
    return path, keys


class Provider:
    def __init__(self, *, maximum_batch=None, bad_key=None, fatal_key=None):
        self.maximum_batch = maximum_batch
        self.bad_key = bad_key
        self.fatal_key = fatal_key
        self.calls = []

    def transport(self, url, headers, payload, timeout):
        data = json.loads(payload["messages"][-1]["content"])
        if "papers" not in data:
            result = {"html": "<section><p>概览</p></section>"}
        else:
            keys = [item["key"] for item in data["papers"]]
            self.calls.append(keys)
            if self.fatal_key in keys:
                raise HTTPError(url, 401, "unauthorized", None, None)
            if (self.maximum_batch is not None and len(keys) > self.maximum_batch) or self.bad_key in keys:
                result = {"decisions": []}
            else:
                result = {"decisions": [{
                    "key": key, "relevant": True, "confidence": 0.9,
                    "category": "rtl-microarchitecture", "matched_topics": ["digital-design"],
                    "summary_zh": "提出数字 RTL 设计。报告硬件实现结果。", "reason": "数字硬件研究。",
                } for key in keys]}
        return {"choices": [{"message": {"content": json.dumps(result)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    def client(self):
        return DeepSeekClient("test-only-credential", config().ai,
                              transport=self.transport, wait=lambda _: None)


def run(tmp_path, path, provider):
    return run_summary(config(), path, provider.client(), NOW, output_dir=tmp_path)


def test_invalid_large_batches_split_without_double_charging_candidates(tmp_path, capsys):
    path, keys = queue(tmp_path, 20)
    provider = Provider(maximum_batch=5)
    stats = run(tmp_path, path, provider)
    assert (stats.candidates, stats.processed, stats.selected, stats.failed) == (20, 20, 20, False)
    assert load_state(path).pending_ai == []
    assert len(titles((tmp_path / "ai_summary_feed.xml").read_text(encoding="utf-8"))) == 20
    assert len(provider.calls) == 10  # three failed parents + two valid halves, twice
    usage = json.loads((tmp_path / "ai_usage.json").read_text(encoding="utf-8"))[0]
    assert usage["candidates"] == 20 and usage["requests"] == 11  # includes overview
    assert usage["total_tokens"] == 165
    assert "split" in capsys.readouterr().err.lower()


def test_bad_single_leaf_stays_pending_without_blocking_siblings_or_later_batches(tmp_path):
    path, keys = queue(tmp_path, 12, history=True)
    original_history = deepcopy(load_state(path).papers["doi:10.1000/ic.999"])
    provider = Provider(bad_key=keys[2])
    stats = run(tmp_path, path, provider)
    assert (stats.candidates, stats.processed, stats.selected, stats.failed) == (12, 11, 11, True)
    state = load_state(path)
    assert state.pending_ai == [keys[2]]
    assert state.papers[keys[2]].ai_relevant is None
    assert state.papers["doi:10.1000/ic.999"] == original_history
    assert len(titles((tmp_path / "design_feed.xml").read_text(encoding="utf-8"))) == 12
    assert len(provider.calls) < 3 * (2 * len(keys) - 1)


def test_transport_failure_does_not_split_or_charge_unsent_candidates(tmp_path):
    path, keys = queue(tmp_path, 30)
    provider = Provider(fatal_key=keys[10])
    stats = run(tmp_path, path, provider)
    assert (stats.candidates, stats.processed, stats.failed) == (20, 10, True)
    assert provider.calls == [keys[:10], keys[10:20]]
    assert load_state(path).pending_ai == keys[10:]
    usage = json.loads((tmp_path / "ai_usage.json").read_text(encoding="utf-8"))[0]
    assert usage["candidates"] == 20 and usage["processed"] == 10


def test_same_day_resume_uses_remaining_allowance_and_skips_completed_keys(tmp_path):
    path, keys = queue(tmp_path, 30, history=True)
    first = run(tmp_path, path, Provider(fatal_key=keys[10]))
    assert first.candidates == 20
    before = load_state(path)
    history = {key: deepcopy(value) for key, value in before.papers.items() if key not in before.pending_ai}
    provider = Provider()
    second = run(tmp_path, path, provider)
    assert (second.candidates, second.processed, second.failed) == (20, 20, False)
    assert not set(sum(provider.calls, [])).intersection(keys[:10])
    state = load_state(path)
    assert state.pending_ai == []
    assert all(state.papers[key] == value for key, value in history.items())
    usage = json.loads((tmp_path / "ai_usage.json").read_text(encoding="utf-8"))[0]
    assert usage["candidates"] == 40 and usage["processed"] == 30


def test_failure_before_any_request_does_not_consume_candidate_allowance(tmp_path):
    path, keys = queue(tmp_path, 30)

    class NoRequestClient:
        def complete_json(self, *args):
            raise RuntimeError("client initialization failed")

    stats = run_summary(config(), path, NoRequestClient(), NOW, output_dir=tmp_path)
    assert (stats.candidates, stats.requests, stats.processed, stats.failed) == (0, 0, 0, True)
    assert load_state(path).pending_ai == keys


def test_existing_daily_allowance_is_respected_with_split_recovery(tmp_path):
    path, keys = queue(tmp_path, 30)
    previous = {"date": "2026-10-25", "candidates": 80, "processed": 70, "selected": 45,
                "requests": 10, "prompt_tokens": 47483, "completion_tokens": 10598,
                "total_tokens": 58081, "token_usage_complete": True}
    (tmp_path / "ai_usage.json").write_text(json.dumps([previous]), encoding="utf-8")
    stats = run(tmp_path, path, Provider(maximum_batch=5))
    assert (stats.candidates, stats.processed, stats.failed) == (20, 20, False)
    assert load_state(path).pending_ai == keys[20:]
    usage = json.loads((tmp_path / "ai_usage.json").read_text(encoding="utf-8"))[0]
    assert usage["candidates"] == 100 and usage["processed"] == 90
