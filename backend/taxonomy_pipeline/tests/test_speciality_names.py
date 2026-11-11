"""Grouped Case A specialities get a name generated from all their tags, cached and checked (#132)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from taxonomy_pipeline.enrichment.speciality_names import (
    GroupNamingError,
    LLMGroupNamer,
    SpecialityGroup,
    clean_name,
)

GROUP = SpecialityGroup("Aerospace & Defence", ("Aircraft Instrument Systems Maintenance",
                                                "Aircraft Hydraulic Components Maintenance"))


class _FakeClient:
    """Answers every chat request with `reply` (or raises it), counting the requests."""

    def __init__(self, reply):
        self.reply, self.prompts = reply, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, *, model, messages, temperature):
        assert temperature == 0
        self.prompts.append(messages[0]["content"])
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


def test_asks_for_a_name_covering_every_tag_under_the_groups_category():
    client = _FakeClient('{"name": "Aircraft Systems Maintenance"}')

    names = LLMGroupNamer("gpt-test", client=client).name([GROUP])

    assert names == ["Aircraft Systems Maintenance"]
    assert "'Aerospace & Defence'" in client.prompts[0]
    assert "- Aircraft Hydraulic Components Maintenance" in client.prompts[0]


def test_reads_a_name_wrapped_in_a_code_fence():
    client = _FakeClient('```json\n{"name": "Aircraft Systems Maintenance"}\n```')

    assert LLMGroupNamer("gpt-test", client=client).name([GROUP]) == ["Aircraft Systems Maintenance"]


@pytest.mark.parametrize("reply", ["not json", '{"title": "x"}', '{"name": ""}', '{"name": "' + "x" * 81 + '"}',
                                   RuntimeError("timeout")])
def test_returns_none_for_an_unusable_answer_so_the_central_tag_name_stays(reply):
    assert LLMGroupNamer("gpt-test", client=_FakeClient(reply)).name([GROUP]) == [None]


def test_stops_on_a_rejected_request_since_every_other_one_would_fail_too():
    rejected = RuntimeError("bad key")
    rejected.status_code = 401

    with pytest.raises(GroupNamingError, match="rejected"):
        LLMGroupNamer("gpt-test", client=_FakeClient(rejected)).name([GROUP])


def test_reuses_cached_names_instead_of_asking_again(tmp_path):
    cache = tmp_path / "names.json"
    first = _FakeClient('{"name": "Aircraft Systems Maintenance"}')
    LLMGroupNamer("gpt-test", cache_path=cache, client=first).name([GROUP])
    again = _FakeClient('{"name": "Something Else"}')

    names = LLMGroupNamer("gpt-test", cache_path=cache, client=again).name([GROUP])

    assert names == ["Aircraft Systems Maintenance"] and again.prompts == []
    assert list(json.loads(cache.read_text()).values()) == ["Aircraft Systems Maintenance"]


def test_a_different_model_is_a_different_cache_entry(tmp_path):
    cache = tmp_path / "names.json"
    LLMGroupNamer("gpt-test", cache_path=cache, client=_FakeClient('{"name": "A Name"}')).name([GROUP])
    other = _FakeClient('{"name": "B Name"}')

    assert LLMGroupNamer("gpt-other", cache_path=cache, client=other).name([GROUP]) == ["B Name"]


def test_cleans_whitespace_and_quotes_from_a_name():
    assert clean_name('  "Aircraft   Systems Maintenance." ') == "Aircraft Systems Maintenance"
    assert clean_name(42) is None
