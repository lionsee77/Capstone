from __future__ import annotations

import builtins
import sys
import types
from pathlib import Path

import pytest

from taxonomy_pipeline import env, llm


@pytest.fixture
def no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(env, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("openai")

    class OpenAI:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class AsyncOpenAI(OpenAI):
        pass

    module.OpenAI, module.AsyncOpenAI = OpenAI, AsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", module)
    return module


@pytest.mark.usefixtures("no_env_file")
def test_a_missing_key_names_the_caller_and_the_env_file() -> None:
    with pytest.raises(llm.LLMError, match=r"Case B needs a real OPENAI_API_KEY in .*missing\.env"):
        llm.openai_client("Case B")


@pytest.mark.usefixtures("no_env_file")
@pytest.mark.parametrize("placeholder", sorted(llm.PLACEHOLDER_KEYS))
def test_the_example_placeholder_key_counts_as_missing(monkeypatch: pytest.MonkeyPatch, placeholder: str) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", placeholder)
    with pytest.raises(llm.LLMError, match="OPENAI_API_KEY"):
        llm.openai_client("the judge")


@pytest.mark.usefixtures("no_env_file")
def test_a_missing_sdk_explains_the_llm_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    real_import = builtins.__import__

    def without_openai(name: str, *args: object, **kwargs: object) -> object:
        if name == "openai":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_openai)
    with pytest.raises(llm.LLMError, match=r"\.\[llm\]"):
        llm.openai_client("the judge")


@pytest.mark.usefixtures("no_env_file")
def test_builds_a_sync_or_async_client_with_the_key_base_url_and_extra_settings(
    monkeypatch: pytest.MonkeyPatch, fake_openai: types.ModuleType,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", " sk-test ")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1 ")
    sync = llm.openai_client("the judge", max_retries=5)
    async_client = llm.openai_client("Case B", async_client=True)
    assert type(sync) is fake_openai.OpenAI
    assert sync.kwargs == {"api_key": "sk-test", "base_url": "https://proxy.example/v1", "max_retries": 5}
    assert type(async_client) is fake_openai.AsyncOpenAI


@pytest.mark.usefixtures("no_env_file")
def test_reads_the_key_from_the_env_file_without_overriding_the_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_openai: types.ModuleType,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-from-file\nOPENAI_BASE_URL=https://file.example/v1\n", encoding="utf-8")
    monkeypatch.setattr(env, "ENV_FILE", env_file)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://shell.example/v1")
    client = llm.openai_client("the judge")
    assert client.kwargs["api_key"] == "sk-from-file"
    assert client.kwargs["base_url"] == "https://shell.example/v1"


@pytest.mark.usefixtures("no_env_file")
def test_the_base_url_defaults_to_openai() -> None:
    assert llm.base_url() == llm.DEFAULT_BASE_URL
