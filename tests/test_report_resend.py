"""Read-only, statistics-only replay of explicitly requested historical reports."""

import ast
import builtins
import importlib
import json
from pathlib import Path

import pytest
import yaml

from ic_feed import ai, bark
from ic_feed.bark import BARK_URL, BarkResponse
from ic_feed.notification import build_statistics_message


ROOT = Path(__file__).resolve().parents[1]
DATES = "2026-10-26,2026-10-27"
TOKEN = "unit-test-replay-secret"


def replay_module():
    return importlib.import_module("ic_feed.resend_reports")


def usage_entry(day, candidates, processed, selected):
    return {
        "date": day,
        "candidates": candidates,
        "processed": processed,
        "selected": selected,
        "requests": 3,
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
        "token_usage_complete": True,
    }


@pytest.fixture
def replay_files(tmp_path, monkeypatch):
    def no_ai_initialization(*args, **kwargs):
        pytest.fail("report replay must not initialize DeepSeek")

    monkeypatch.setattr(ai.DeepSeekClient, "__init__", no_ai_initialization)
    usage = tmp_path / "ai_usage.json"
    usage.write_text(
        json.dumps(
            [usage_entry("2026-10-26", 100, 100, 69), usage_entry("2026-10-27", 15, 15, 11)]
        ),
        encoding="utf-8",
    )
    config = tmp_path / "paper_feed_config.json"
    config.write_bytes((ROOT / "paper_feed_config.json").read_bytes())
    for name in ("state.json", "filtered_feed.xml", "ai_summary.html", "notification.json"):
        (tmp_path / name).write_text(f"untouched {name}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BARK_TOKEN", TOKEN)
    return usage, config


def install_transport(monkeypatch, outcomes=None):
    payloads = []
    pending = list(outcomes) if outcomes is not None else None

    def transport(url, headers, body, timeout):
        assert url == BARK_URL
        assert headers == {"Content-Type": "application/json"}
        assert timeout > 0
        payloads.append(json.loads(body))
        if pending is None:
            return BarkResponse(200, b'{"code":200}')
        outcome = pending.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(bark, "_urllib_transport", transport)
    return payloads


def test_replays_persisted_counts_as_two_dated_report_messages_without_writes(
    replay_files, tmp_path, monkeypatch, capsys
):
    payloads = install_transport(monkeypatch)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    assert replay_module().main(["--dates", DATES]) == 0

    assert len(payloads) == 2
    for payload, day, counts in zip(
        payloads, DATES.split(","), [(100, 100, 69), (15, 15, 11)], strict=True
    ):
        expected = build_statistics_message(
            candidates=counts[0],
            processed=counts[1],
            selected=counts[2],
            focus_selected=counts[2],
            base_url="https://llxyyds666.github.io/digital-ic-paper-feed",
        )
        assert day in payload["title"] and "(补发)" in payload["title"]
        assert day in payload["body"] and "补发" in payload["body"]
        assert expected.body in payload["body"]
        assert "当前累计" in payload["body"] and "非历史快照" in payload["body"]
        assert payload["url"] == expected.url
        assert payload["group"] == "digital-ic-paper-feed-reports"
        assert payload["isArchive"] == "1"
        assert payload["device_key"] == TOKEN
        assert "推荐" not in payload["title"]
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    captured = capsys.readouterr()
    logs = captured.out + captured.err
    assert "accepted=2" in logs and "failed=0" in logs
    assert all(f"date={day}" in logs for day in DATES.split(","))
    assert TOKEN not in logs and "https://" not in logs
    assert "今日候选" not in logs and "数字 IC 文献日报" not in logs


def test_explicit_usage_and_config_paths_are_read(replay_files, monkeypatch):
    usage, config = replay_files
    raw = json.loads(usage.read_text(encoding="utf-8"))
    raw[0].update(candidates=7, processed=4, selected=0)
    usage.write_text(json.dumps(raw), encoding="utf-8")
    app_config = json.loads(config.read_text(encoding="utf-8"))
    app_config["publication"]["base_url"] = "https://example.test/custom/"
    config.write_text(json.dumps(app_config), encoding="utf-8")
    payloads = install_transport(monkeypatch)

    assert replay_module().main(
        ["--dates", "2026-10-26", "--usage", str(usage), "--config", str(config)]
    ) == 0

    assert len(payloads) == 1
    assert "今日候选：7 篇\n完成筛选：4 篇\n综合入选：0 篇\n设计与验证方向：0 篇" in payloads[0]["body"]
    assert payloads[0]["url"] == "https://example.test/custom/ai_summary.html"


@pytest.mark.parametrize(
    "requested",
    [
        "",
        "2026-10-26,",
        ",2026-10-26",
        "2026-10-26,,2026-10-27",
        "2026-10-26,2026-10-26",
        "2026-10-25,2026-10-26,2026-10-27",
        "2026-10-25",
        "2026-02-30",
        "20261026",
        "2026-W44-1",
        "2026-1-26",
        "2026-10-26, 2026-10-27",
        "2026-10-26;echo unsafe",
    ],
)
def test_invalid_requested_dates_send_nothing(replay_files, monkeypatch, requested):
    payloads = install_transport(monkeypatch)

    assert replay_module().main(["--dates", requested]) != 0

    assert payloads == []


@pytest.mark.parametrize("field", ["candidates", "processed", "selected"])
@pytest.mark.parametrize("value", [-1, True, False, 1.5, "15", None, [], {}])
def test_every_requested_count_is_strictly_validated_before_first_send(
    replay_files, monkeypatch, field, value
):
    usage, _ = replay_files
    raw = json.loads(usage.read_text(encoding="utf-8"))
    raw[1][field] = value
    usage.write_text(json.dumps(raw), encoding="utf-8")
    payloads = install_transport(monkeypatch)

    assert replay_module().main(["--dates", DATES]) != 0

    assert payloads == []


@pytest.mark.parametrize("field", ["candidates", "processed", "selected", "date"])
def test_missing_required_usage_field_prevents_all_sends(replay_files, monkeypatch, field):
    usage, _ = replay_files
    raw = json.loads(usage.read_text(encoding="utf-8"))
    del raw[1][field]
    usage.write_text(json.dumps(raw), encoding="utf-8")
    payloads = install_transport(monkeypatch)

    assert replay_module().main(["--dates", DATES]) != 0

    assert payloads == []


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {},
        None,
        ["not an object"],
        [usage_entry("2026-10-26", 100, 100, 69)],
        [
            usage_entry("2026-10-26", 100, 100, 69),
            usage_entry("2026-10-27", 15, 15, 11),
            usage_entry("2026-10-26", 100, 100, 69),
        ],
        [usage_entry("20261026", 100, 100, 69), usage_entry("2026-10-27", 15, 15, 11)],
        [usage_entry(None, 100, 100, 69), usage_entry("2026-10-27", 15, 15, 11)],
    ],
)
def test_missing_duplicate_or_invalid_usage_dates_prevent_all_sends(replay_files, monkeypatch, raw):
    usage, _ = replay_files
    usage.write_text(json.dumps(raw), encoding="utf-8")
    payloads = install_transport(monkeypatch)

    assert replay_module().main(["--dates", DATES]) != 0

    assert payloads == []


@pytest.mark.parametrize("source", ["usage", "config"])
@pytest.mark.parametrize("content", [None, b"{invalid", b"\xff", b"null", b"[]", b"{}"])
def test_unreadable_or_invalid_inputs_fail_without_delivery(
    replay_files, monkeypatch, source, content
):
    usage, config = replay_files
    path = usage if source == "usage" else config
    if content is None:
        path.unlink()
    else:
        path.write_bytes(content)
    payloads = install_transport(monkeypatch)

    assert replay_module().main(["--dates", DATES]) != 0

    assert payloads == []


def test_duplicate_count_keys_are_not_silently_overwritten(replay_files, monkeypatch):
    usage, _ = replay_files
    contents = usage.read_text(encoding="utf-8").replace(
        '"selected": 11', '"selected": -1, "selected": 11'
    )
    usage.write_text(contents, encoding="utf-8")
    payloads = install_transport(monkeypatch)

    assert replay_module().main(["--dates", DATES]) != 0

    assert payloads == []


@pytest.mark.parametrize("base_url", ["", "not-a-url", "file:///tmp/report", "https://bad.test\n"])
def test_invalid_publication_url_prevents_all_sends(replay_files, monkeypatch, base_url):
    _, config = replay_files
    raw = json.loads(config.read_text(encoding="utf-8"))
    raw["publication"]["base_url"] = base_url
    config.write_text(json.dumps(raw), encoding="utf-8")
    payloads = install_transport(monkeypatch)

    assert replay_module().main(["--dates", DATES]) != 0

    assert payloads == []


def test_overflowing_config_is_a_safe_configuration_failure(replay_files, monkeypatch, capsys):
    _, config = replay_files
    raw = json.loads(config.read_text(encoding="utf-8"))
    raw["collection"]["lookback_days"] = float("inf")
    config.write_text(json.dumps(raw), encoding="utf-8")
    payloads = install_transport(monkeypatch)

    assert replay_module().main(["--dates", DATES]) != 0

    assert payloads == []
    captured = capsys.readouterr()
    assert "Invalid report replay configuration" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("token", [None, "", " \t\n"])
def test_missing_token_is_nonzero_without_network(replay_files, monkeypatch, token):
    if token is None:
        monkeypatch.delenv("BARK_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BARK_TOKEN", token)
    payloads = install_transport(monkeypatch)

    assert replay_module().main(["--dates", DATES]) != 0

    assert payloads == []


def test_token_cannot_be_supplied_on_command_line(replay_files, monkeypatch, capsys):
    payloads = install_transport(monkeypatch)

    assert replay_module().main(["--dates", DATES, "--token", TOKEN]) != 0

    assert payloads == []
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err


def test_dry_run_needs_no_token_network_or_ai_and_prints_only_metadata(
    replay_files, tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv("BARK_TOKEN")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    payloads = install_transport(monkeypatch)
    original_import = builtins.__import__

    def no_ai_initialization(*args, **kwargs):
        pytest.fail("report replay must not initialize DeepSeek")

    def guarded_import(name, *args, **kwargs):
        if name in {"ic_feed.summarize", "ic_feed.enrich"}:
            pytest.fail(f"report replay must not import {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(ai.DeepSeekClient, "__init__", no_ai_initialization)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert replay_module().main(["--dates", DATES, "--dry-run"]) == 0

    assert payloads == []
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 2
    for line, day, counts in zip(lines, DATES.split(","), [(100, 100, 69), (15, 15, 11)], strict=True):
        assert "dry-run" in line.lower() and f"date={day}" in line
        for key, value in zip(("candidates", "processed", "selected"), counts, strict=True):
            assert f"{key}={value}" in line
        assert f"focus_selected={counts[2]}" in line
    assert all(value not in captured.out for value in (TOKEN, "https://", "今日候选", "文献日报"))


def test_dry_run_still_validates_every_requested_date(replay_files, monkeypatch):
    payloads = install_transport(monkeypatch)

    assert replay_module().main(["--dates", "2026-10-26,2026-10-25", "--dry-run"]) != 0

    assert payloads == []


@pytest.mark.parametrize("failed_index", [0, 1])
def test_failed_delivery_does_not_block_other_date_and_returns_nonzero(
    replay_files, monkeypatch, capsys, failed_index
):
    outcomes = [BarkResponse(200, b'{"code":200}')] * 2
    outcomes[failed_index] = BarkResponse(403, b'{"code":403,"message":"private"}')
    payloads = install_transport(monkeypatch, outcomes)

    assert replay_module().main(["--dates", DATES]) != 0

    assert len(payloads) == 2
    assert all(day in payload["title"] for day, payload in zip(DATES.split(","), payloads, strict=True))
    captured = capsys.readouterr()
    logs = captured.out + captured.err
    assert "accepted=1" in logs and "failed=1" in logs
    assert TOKEN not in logs and "private" not in logs


def test_unexpected_delivery_exception_still_attempts_second_date(replay_files, monkeypatch, capsys):
    replay = replay_module()
    attempts = []
    real_send = replay.send_plan

    def raise_once(token, plan):
        attempts.append(plan)
        if len(attempts) == 1:
            raise RuntimeError(f"secret https://api.day.app/{TOKEN}")
        return real_send(token, plan)

    payloads = install_transport(monkeypatch)
    monkeypatch.setattr(replay, "send_plan", raise_once)

    assert replay.main(["--dates", DATES]) != 0

    assert len(attempts) == 2 and len(payloads) == 1
    assert "2026-10-27" in payloads[0]["title"]
    captured = capsys.readouterr()
    logs = captured.out + captured.err
    assert "accepted=1" in logs and "failed=1" in logs
    assert TOKEN not in logs and "https://" not in logs


@pytest.mark.parametrize("arguments", [[], ["--dates"], ["--other", "value"]])
def test_missing_or_invalid_cli_arguments_return_nonzero(replay_files, monkeypatch, arguments):
    payloads = install_transport(monkeypatch)

    assert replay_module().main(arguments) != 0

    assert payloads == []


def test_replay_module_has_no_ai_or_publication_generation_imports():
    source = ROOT / "src/ic_feed/resend_reports.py"
    assert source.exists(), "the independent report replay CLI is missing"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    assert not imports.intersection({"ic_feed.ai", "ic_feed.summarize", "ic_feed.enrich"})
    assert not imports.intersection({"ic_feed.render", "ic_feed.publication", "ic_feed.state"})


def test_manual_workflow_is_read_only_secret_scoped_and_quotes_dates_as_data():
    path = ROOT / ".github/workflows/resend_reports.yml"
    assert path.exists(), "the independent manual replay workflow is missing"
    source = path.read_text(encoding="utf-8")
    workflow = yaml.load(source, Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"report_dates"}
    assert inputs["report_dates"]["type"] == "string"
    assert inputs["report_dates"]["required"] == "true"
    assert "default" not in inputs["report_dates"]
    assert workflow["permissions"] == {"contents": "read"}
    assert "BARK_TOKEN" not in workflow.get("env", {})
    assert len(workflow["jobs"]) == 1
    job = next(iter(workflow["jobs"].values()))
    assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}
    assert "BARK_TOKEN" not in job.get("env", {})
    steps = job["steps"]
    checkout = next(item for item in steps if item.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["persist-credentials"] == "false"
    assert any(item.get("uses", "").startswith("actions/setup-python@") for item in steps)
    assert any("python -m pip install" in item.get("run", "") for item in steps)
    sends = [item for item in steps if "python -m ic_feed.resend_reports" in item.get("run", "")]
    assert len(sends) == 1
    send = sends[0]
    assert send["env"]["REPORT_DATES"] == "${{ inputs.report_dates }}"
    assert send["env"]["BARK_TOKEN"] == "${{ secrets.BARK_TOKEN }}"
    assert '--dates "$REPORT_DATES"' in send["run"]
    assert send.get("continue-on-error", "false") == "false"
    for item in steps:
        assert "${{ inputs.report_dates }}" not in item.get("run", "")
        if item is not send:
            assert "BARK_TOKEN" not in item.get("env", {})
    assert source.count("secrets.") == 1
    for forbidden in ("DEEPSEEK", "SEMANTIC_SCHOLAR", "IEEE_API_KEY", "ic_feed.summarize", "git push", "git commit", "upload-pages", "deploy-pages"):
        assert forbidden not in source
