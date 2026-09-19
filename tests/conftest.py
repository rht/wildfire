"""Suite-wide guard: no test may reach a live LLM provider.

`fireline.env.load_env` reads the repository's `.env`, so on a developer machine with a
NEBIUS_API_KEY (or ANTHROPIC_API_KEY) `ui_state.llm_available()` would otherwise return True and an
investigation would go to the network. The tests are meant to be offline and reproducible against
`llm.FakeLLM` (VALIDATION.md), so strip both keys and mark the `.env` as already loaded, which makes
`load_env()` return without putting them back.
"""

import pytest

from fireline import env


@pytest.fixture(autouse=True)
def no_llm_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(env, "_loaded", [tmp_path / "no-such.env"])
    for name in ("NEBIUS_API_KEY", "ANTHROPIC_API_KEY", "FIRELINE_MODEL"):
        monkeypatch.delenv(name, raising=False)
