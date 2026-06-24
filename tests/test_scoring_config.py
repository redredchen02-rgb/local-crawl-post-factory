import pytest

from cpost.core import scoring_config
from cpost.core.errors import ValidationError


def test_none_path_returns_defaults():
    assert scoring_config.load(None) == scoring_config.DEFAULTS


def test_in_code_defaults_neutralize_confidence():
    """In-code DEFAULTS mirror configs/scoring.yaml: confidence weight 0, quality 1."""
    assert scoring_config.DEFAULTS["weight_confidence"] == 0.0
    assert scoring_config.DEFAULTS["weight_quality"] == 1.0


def test_4d_defaults_present():
    """4D scoring v2 default keys exist with sensible values."""
    d = scoring_config.DEFAULTS
    # freshness
    assert d["freshness_w_recency"] == 0.6
    assert d["freshness_w_velocity"] == 0.4
    assert d["freshness_velocity_window_hours"] == 24
    # importance
    assert d["importance_w_completeness"] == 0.5
    assert d["importance_w_material"] == 0.3
    assert d["importance_w_diversity"] == 0.2
    assert d["importance_diversity_threshold"] == 0.3
    # traffic
    assert d["traffic_w_volume"] == 0.4
    assert d["traffic_w_sources"] == 0.3
    assert d["traffic_w_diversity"] == 0.3
    assert d["traffic_article_cap"] == 50
    assert d["traffic_source_cap"] == 10
    assert d["traffic_diversity_cap"] == 3
    # cross-site coverage
    assert d["cross_site_source_cap"] == 5
    # 4D composite weights
    assert d["weight_freshness"] == 0.25
    assert d["weight_importance"] == 0.25
    assert d["weight_traffic_potential"] == 0.25
    assert d["weight_cross_site_coverage"] == 0.25
    # external search
    assert d["external_search_enabled"] is False


def test_4d_defaults_yaml_roundtrip(tmp_path):
    """All 4D keys from DEFAULTS survive a parse-merge cycle."""
    p = tmp_path / "scoring.yaml"
    p.write_text("# only 4D overrides\n", encoding="utf-8")
    cfg = scoring_config.load(str(p))
    for k in scoring_config.DEFAULTS:
        assert k in cfg, f"missing key after merge: {k}"
        # type should match
        assert type(cfg[k]) is type(scoring_config.DEFAULTS[k]), (
            f"type mismatch for {k}: {type(cfg[k])} != {type(scoring_config.DEFAULTS[k])}"
        )


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
