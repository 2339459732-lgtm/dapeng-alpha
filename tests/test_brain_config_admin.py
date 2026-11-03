"""配置升级 / 回滚 / 留痕（scripts/brain_config_admin.py）的护栏测试。

要点：预检不通过**绝不落盘**、写入后热重载生效、回滚可回到上一版。
所有用例都在临时目录里跑，不碰仓库里真正生效的 data/brain_config.json。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import brain_config as bc  # noqa: E402
import scripts.brain_config_admin as admin  # noqa: E402


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把配置路径整体挪到临时目录，并清掉模块内的 mtime 缓存。"""
    config_path = tmp_path / "brain_config.json"
    payload = {
        "version": "brain-config.v1",
        "crosscheck": {
            "number_tolerance": 0.5,
            "min_comparable_ratio": 0.5,
            "warn_factor": 2.0,
            "sector_taxonomy": {"default": "em", "labels": {"em": "东财板块", "sw": "申万行业"}},
        },
        "sentiment": {
            "bands": {"freezing": 0, "cool": 20, "neutral": 40, "warm": 60, "hot": 80},
            "min_coverage": 0.7,
        },
        "layers": {"fact": {"label": "事实层"}},
    }
    config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bc, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr(bc, "_state", {"mtime": None, "data": None})
    yield config_path
    bc._state = {"mtime": None, "data": None}


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_apply_writes_and_reloads(sandbox):
    code = admin.main(["apply", "--set", "crosscheck.number_tolerance=0.8",
                       "--note", "放宽数值容差"])
    assert code == 0
    assert _read(sandbox)["crosscheck"]["number_tolerance"] == 0.8
    # 热重载：同一个进程里立刻读到新值
    assert bc.get("crosscheck.number_tolerance") == 0.8
    rows = bc.history_tail(5)
    assert rows and rows[0]["action"] == "apply"
    assert rows[0]["note"] == "放宽数值容差"
    assert rows[0]["from_sha"] and rows[0]["to_sha"] and rows[0]["from_sha"] != rows[0]["to_sha"]


def test_invalid_change_never_touches_disk(sandbox):
    before = sandbox.read_text(encoding="utf-8")
    code = admin.main(["apply", "--set", "crosscheck.min_comparable_ratio=1.8"])
    assert code == 2
    assert sandbox.read_text(encoding="utf-8") == before
    assert bc.history_tail(5) == []


def test_unknown_key_warns_but_applies(sandbox):
    code = admin.main(["apply", "--set", "crosscheck.number_tolerence=0.9"])
    assert code == 0  # 拼错键不阻断，但要留告警
    assert _read(sandbox)["crosscheck"]["number_tolerence"] == 0.9
    rows = bc.history_tail(3)
    assert rows[0]["warnings"] == 1


def test_rollback_restores_previous_version(sandbox):
    original = _read(sandbox)["crosscheck"]["number_tolerance"]
    assert admin.main(["apply", "--set", "crosscheck.number_tolerance=0.9"]) == 0
    assert _read(sandbox)["crosscheck"]["number_tolerance"] == 0.9

    assert admin.main(["rollback", "--steps", "1"]) == 0
    assert _read(sandbox)["crosscheck"]["number_tolerance"] == original
    rows = bc.history_tail(5)
    assert rows[0]["action"] == "rollback"


def test_rollback_refuses_bad_snapshot(sandbox):
    """留档里若有非法配置，回滚必须拒绝，不能把坏配置带回来。"""
    snapshot_dir = bc.history_dir()
    os.makedirs(snapshot_dir, exist_ok=True)
    bad = {"version": "brain-config.v1", "sentiment": {"bands": {"freezing": 0}}}
    with open(os.path.join(snapshot_dir, "20260101-000000-bad.json"), "w", encoding="utf-8") as fh:
        json.dump(bad, fh)

    before = sandbox.read_text(encoding="utf-8")
    assert admin.main(["rollback", "--steps", "1"]) == 2
    assert sandbox.read_text(encoding="utf-8") == before


def test_dry_run_does_not_write(sandbox):
    before = sandbox.read_text(encoding="utf-8")
    assert admin.main(["apply", "--set", "crosscheck.warn_factor=2.5", "--dry-run"]) == 0
    assert sandbox.read_text(encoding="utf-8") == before


def test_apply_file_replaces_whole_config(sandbox, tmp_path):
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({
        "version": "brain-config.v1",
        "crosscheck": {"number_tolerance": 0.2},
    }, ensure_ascii=False), encoding="utf-8")
    assert admin.main(["apply", "--file", str(candidate)]) == 0
    assert _read(sandbox)["crosscheck"]["number_tolerance"] == 0.2
    # 整份替换后，未声明的项由内置默认值补齐（深合并），不是丢失
    assert bc.get("sentiment.min_coverage") == 0.7


def test_apply_requires_a_change(sandbox):
    assert admin.main(["apply"]) == 1


def test_history_command_prints(sandbox, capsys):
    assert admin.main(["apply", "--set", "crosscheck.warn_factor=3.0", "--note", "告警倍数"]) == 0
    capsys.readouterr()
    assert admin.main(["history", "-n", "5"]) == 0
    out = capsys.readouterr().out
    assert "apply" in out and "告警倍数" in out
