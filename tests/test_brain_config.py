"""数据配置（brain-config.v1）读取与校验。"""
from __future__ import annotations

import json

import pytest

from server import brain_config


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch):
    path = tmp_path / "brain_config.json"
    monkeypatch.setattr(brain_config, "CONFIG_PATH", str(path))
    monkeypatch.setattr(brain_config, "_state", {"mtime": None, "data": None})
    return path


def test_missing_file_falls_back_to_defaults(tmp_config):
    cfg = brain_config.load(force=True)
    assert cfg["crosscheck"]["number_tolerance"] == 0.5
    assert brain_config.get("crosscheck.sector_top_n") == 3


def test_dot_path_and_default(tmp_config):
    tmp_config.write_text(json.dumps({"crosscheck": {"number_tolerance": 1.25}}), encoding="utf-8")
    brain_config.load(force=True)
    assert brain_config.get("crosscheck.number_tolerance") == 1.25
    assert brain_config.get("crosscheck.ratio_tolerance") == 0.5      # 未覆盖 → 默认
    assert brain_config.get("no.such.key", "x") == "x"


def test_partial_file_is_deep_merged(tmp_config):
    tmp_config.write_text(json.dumps({"sentiment": {"min_coverage": 0.8}}), encoding="utf-8")
    brain_config.load(force=True)
    assert brain_config.get("sentiment.min_coverage") == 0.8
    # 同节其它键保留默认
    assert brain_config.get("sentiment.bands.hot") == 80


def test_broken_json_keeps_working(tmp_config):
    tmp_config.write_text("{not json", encoding="utf-8")
    cfg = brain_config.load(force=True)
    assert cfg["version"] == "brain-config.v1"


def test_validate_flags_bad_values(tmp_config):
    tmp_config.write_text(json.dumps({"crosscheck": {"number_tolerance": -1, "ratio_tolerance": 0.5}}),
                          encoding="utf-8")
    result = brain_config.validate()
    assert result["ok"] is False
    assert any(item["path"] == "crosscheck.number_tolerance" for item in result["issues"])
    # 非法值被回落到默认
    assert brain_config.get("crosscheck.number_tolerance") == 0.5


def test_validate_ok_on_repo_config():
    result = brain_config.validate()
    assert result["ok"] is True, result["issues"]


def test_layer_labels():
    labels = brain_config.layer_labels()
    assert labels.get("fact") == "事实层"
    assert labels.get("decision") == "决策层"
