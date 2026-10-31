from __future__ import annotations

import pytest

from taxonomy_pipeline.providers.base import SourceProvider
from taxonomy_pipeline.providers.registry import PROVIDERS, get_provider
from taxonomy_pipeline.providers.skillsfuture import SkillsFutureProvider


def test_registers_each_provider_under_its_own_name() -> None:
    assert all(name == provider.name for name, provider in PROVIDERS.items())
    assert isinstance(get_provider("skillsfuture"), SkillsFutureProvider)


def test_an_unknown_provider_lists_the_registered_ones() -> None:
    with pytest.raises(ValueError, match=r"No source provider 'nope'; registered: \['skillsfuture', 'tag_context'\]"):
        get_provider("nope")


def test_a_provider_without_extra_data_needs_only_a_name_and_its_context_loader() -> None:
    class Plain(SourceProvider):
        name = "plain"
        display_name = "Plain"

        def load_tag_context(self, path):
            return {"1": {"note": "known"}}

    provider = Plain()
    contexts = provider.load_tag_context(None)
    assert provider.default_context_path() is None
    assert provider.context_for(contexts, "1") == {"note": "known"}
    assert provider.context_for(contexts, "2") == {}
