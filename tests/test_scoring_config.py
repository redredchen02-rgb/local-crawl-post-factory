import pytest

from core import scoring_config
from core.errors import ValidationError


def test_none_path_returns_defaults():
    assert scoring_config.load(None) == scoring_config.DEFAULTS


def test_in_code_defaults_neutralize_confidence():
    """In-code DEFAULTS mirror configs/scoring.yaml: confidence weight 0, quality 1."""
    assert scoring_config.DEFAULTS["weight_confidence"] == 0.0
    assert scoring_config.DEFAULTS["weight_quality"] == 1.0


def test_missing_file_returns_defaults(tmp_path):
    cfg = scoring_config.load(str(tmp_path / "nope.yaml"))
    assert cfg == scoring_config.DEFAULTS


def test_partial_override_merges_over_defaults(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text("similarity_threshold: 0.9\n", encoding="utf-8")
    cfg = scoring_config.load(str(p))
    assert cfg["similarity_threshold"] == 0.9
    assert cfg["ngram"] == scoring_config.DEFAULTS["ngram"]  # untouched default


def test_unknown_keys_ignored(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text("bogus_key: 123\nngram: 3\n", encoding="utf-8")
    cfg = scoring_config.load(str(p))
    assert "bogus_key" not in cfg
    assert cfg["ngram"] == 3


def test_numeric_coercion_from_strings(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text("ngram: '3'\nsimilarity_threshold: '0.7'\n", encoding="utf-8")
    cfg = scoring_config.load(str(p))
    assert cfg["ngram"] == 3 and isinstance(cfg["ngram"], int)
    assert cfg["similarity_threshold"] == 0.7


def test_non_numeric_value_raises_validation(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text("confidence_source_cap: not-a-number\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        scoring_config.load(str(p))


def test_malformed_yaml_raises_validation(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text("ngram: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        scoring_config.load(str(p))


def test_non_mapping_yaml_raises_validation(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        scoring_config.load(str(p))
