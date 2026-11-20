"""Names for the new specialities Case A groups tags into.

`category_adaptation`'s `group_new_specialities_threshold` groups related tags
under one new speciality, named by default after the group's most central tag.
That name reads well for a tight group (*Non-destructive Testing* over its
inspection methods), but it mislabels a broad one (*Bus Electrical and Electronic
Systems Maintenance* over *Bus Interchange Operations*). The groups loose enough
to reach the depth bar are the broad ones. So the agreed rule is:

1. A group's name is generated from all its member tags and its category, by
   the shared LLM client (`llm.py`).
2. A generated name is held for review (`needs_review`), because nothing
   checks it automatically.
3. The central tag's name stays the fallback when there is no model, the
   answer is unusable, or the generated name repeats an existing speciality or
   another new one. A repeated name is what the integrity guard rail counts,
   so a collision falls back rather than adding a duplicate.

`GroupNamer` is the small protocol `category_adaptation` depends on, so a fake
can stand in for it in tests. `LLMGroupNamer` implements it with one chat request per
group, temperature 0, and answers cached on disk by the exact request, so a
rerun gives the same names without paying again.
"""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .. import llm

# Bump when the prompt changes, so cached names for the old wording aren't reused.
PROMPT_VERSION = 1
MAX_NAME_LENGTH = 80
PROMPT = (
    "You name a speciality in a skills taxonomy (service -> category -> speciality -> tag). "
    "The tags below will all sit under one new speciality in the category {category!r}. "
    "Give that speciality a short name (2-6 words, Title Case) that covers every tag, is no broader than "
    "needed, and reads like the existing specialities' names. Don't name a job title or the category itself.\n"
    "Tags:\n{tags}\n"
    'Answer with JSON only: {{"name": "..."}}'
)


class GroupNamingError(RuntimeError):
    """Naming can't run at all (missing key or dependency); the message says what to fix."""


@dataclass(frozen=True)
class SpecialityGroup:
    """One group of tags to name: the category it sits under and its tags, central tag first."""

    category: str
    tags: tuple[str, ...]


class GroupNamer(Protocol):
    model: str

    def name(self, groups: list[SpecialityGroup]) -> list[str | None]:
        """A name per group, or None where no usable name came back."""


def clean_name(raw: Any) -> str | None:
    """A generated name, trimmed, or None if it is blank, too long or not text."""
    if not isinstance(raw, str):
        return None
    name = " ".join(raw.split()).strip(" .\"'")
    return name if name and len(name) <= MAX_NAME_LENGTH else None


class LLMGroupNamer:
    """Asks a chat model for each group's name, in parallel, with an on-disk cache keyed by request."""

    def __init__(self, model: str, *, cache_path: str | Path | None = None, workers: int = 8,
                 client: Any = None) -> None:
        self.model = model
        self.workers = workers
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache = self._load_cache()
        if client is None:
            try:
                client = llm.openai_client("naming grouped specialities", max_retries=3)
            except llm.LLMError as exc:
                raise GroupNamingError(str(exc)) from exc
        self.client = client

    def _load_cache(self) -> dict[str, str]:
        if not self.cache_path or not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GroupNamingError(f"name cache {self.cache_path} is unreadable ({exc}); delete it") from exc

    def _save_cache(self) -> None:
        if self.cache_path:
            # Write beside the cache, then swap it in, so an interrupted write never truncates paid answers.
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            partial = self.cache_path.with_name(self.cache_path.name + ".tmp")
            partial.write_text(json.dumps(self._cache, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(partial, self.cache_path)

    def _prompt(self, group: SpecialityGroup) -> str:
        return PROMPT.format(category=group.category, tags="\n".join(f"- {tag}" for tag in group.tags))

    def _key(self, prompt: str) -> str:
        payload = json.dumps({"prompt_version": PROMPT_VERSION, "model": self.model, "prompt": prompt})
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _ask(self, prompt: str) -> str | None:
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": prompt}], temperature=0,
            )
            text = (response.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001 - the SDK has already retried transient errors
            if getattr(exc, "status_code", None) in {400, 401, 403, 404}:
                raise GroupNamingError(f"naming request rejected ({exc.status_code}): {exc}") from exc
            return None
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        return clean_name(payload.get("name")) if isinstance(payload, dict) else None

    def name(self, groups: list[SpecialityGroup]) -> list[str | None]:
        prompts = [self._prompt(group) for group in groups]
        keys = [self._key(prompt) for prompt in prompts]
        todo = sorted({index for index, key in enumerate(keys) if key not in self._cache})
        try:
            with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
                for index, name in zip(todo, pool.map(lambda i: self._ask(prompts[i]), todo), strict=True):
                    if name is not None:
                        self._cache[keys[index]] = name
        finally:
            # Keep whatever was paid for, even if a rejected request stops the run.
            if todo:
                self._save_cache()
        return [self._cache.get(key) for key in keys]
