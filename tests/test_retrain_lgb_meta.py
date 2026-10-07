"""retrain_lgb 模型溯源 sidecar 测试 (2026-10-07)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "retrain_lgb", ROOT / "systems/lynx_vnpy/vnpy_bridge/retrain_lgb.py"
)
retrain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(retrain)


def test_write_model_meta_content_and_hash(tmp_path):
    model = tmp_path / "alpha_lgb_model.txt"
    model.write_bytes(b"tree\nversion=v4\n")
    meta_path = retrain.write_model_meta(model, n_samples=5412, n_features=58)

    assert meta_path.name == "alpha_lgb_model.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["n_samples"] == 5412
    assert meta["n_features"] == 58
    assert meta["num_boost_round"] == 200
    assert meta["params"]["num_leaves"] == 8
    assert meta["model_file"] == "alpha_lgb_model.txt"
    assert meta["sha256"] == hashlib.sha256(b"tree\nversion=v4\n").hexdigest()
    assert "generated_at" in meta and "lgb_version" in meta


def test_write_model_meta_hash_changes_with_content(tmp_path):
    m1 = tmp_path / "m1.txt"
    m1.write_bytes(b"A")
    h1 = json.loads(retrain.write_model_meta(m1, n_samples=1, n_features=1).read_text())["sha256"]
    m2 = tmp_path / "m2.txt"
    m2.write_bytes(b"B")
    h2 = json.loads(retrain.write_model_meta(m2, n_samples=1, n_features=1).read_text())["sha256"]
    assert h1 != h2
