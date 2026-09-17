"""Contrat non sensible des métriques d'usage LLM."""
from pathlib import Path


SRC = (Path(__file__).parent / "auryel_bot.py").read_text()


def test_usage_records_mode_model_tokens_and_timestamp_without_content():
    block = SRC[SRC.index("def _record_llm_usage"):SRC.index("# Filtre de sortie")]
    for field in ("model", "mode", "prompt_tokens", "completion_tokens", "total_tokens", "timestamp"):
        assert field in block
    assert "prompt complet" not in block
    assert "réponse utilisateur" not in block
    assert "OPENAI_API_KEY" not in block


def test_rewarded_micro_mode_is_explicit_and_bounded():
    assert 'mode="rewarded_micro"' in SRC
    assert "max_tokens=140 if rewarded_micro else 320" in SRC
    assert "rewarded_micro=rewarded_micro" in SRC
