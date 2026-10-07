"""Case B: propose placements for new tags that have no category of their own.

Saturation marks these tags `NO_PARENT` (see `routing.py`), so there is nothing
in their source to adapt; an LLM proposes a category and
speciality in the baseline taxonomy instead, using the SkillsFuture workbook's
skill descriptions as context. Tags saturation could not confirm as new
(`REVIEW_AMBIGUOUS`) are classified too, and their records flagged for review.

Proposal-only: writes JSON and CSV review files and never touches a database.
Taxonomies come from the pipeline config, the same datasets saturation and Case A
use. The OpenAI client is an optional dependency (`pip install -e ".[llm]"`) and
is imported only when a run needs it, so this module imports without it.

Known gap (#52): every resolved placement is written with `service="Industry"`.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

from ..datasets.base import TaxonomyDataset
from .models import CLASSIFICATION_OUTPUTS
from .routing import RUN_ID_FIELD, case_b_tag_ids, load_saturation, mark_ambiguous

ACTIONS = ["assign_existing", "propose_new_speciality", "propose_new_category"]
VALID_ACTIONS = set(ACTIONS)
PROPOSAL_FIELDS = ("tag", "tag_id", "action", "category", "speciality", "confidence", "reason")
CSV_FIELDS = [
    "action", "source_dataset", "tag_id", "tag", "original_classification", "service", "category",
    "speciality", "confidence", "reason", "needs_review", RUN_ID_FIELD,
]
# The response shape the model is asked to return, embedded verbatim in the prompt.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"proposals": {"type": "array", "items": {"type": "object", "properties": {
        "tag": {"type": "string"}, "tag_id": {"type": "string"},
        "action": {"type": "string", "enum": ACTIONS},
        "category": {"type": "string"}, "speciality": {"type": "string"}, "confidence": {"type": "number"},
        "reason": {"type": "string"},
    }, "required": list(PROPOSAL_FIELDS), "additionalProperties": False}}},
    "required": ["proposals"], "additionalProperties": False,
}
# The tag IDs the SkillsFuture generator assigns: 3000 + position in the sorted unique-skills list.
TAG_ID_OFFSET = 3000
ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


class CaseBError(RuntimeError):
    """A run could not start or finish; the message says what to fix."""


def parse_json(text: str) -> dict[str, Any]:
    """Parse plain JSON or JSON wrapped in a Markdown code fence."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def proposal_is_well_formed(proposal: dict[str, Any]) -> bool:
    if not set(PROPOSAL_FIELDS).issubset(proposal) or proposal.get("action") not in VALID_ACTIONS:
        return False
    confidence = proposal.get("confidence")
    return isinstance(confidence, int | float) and not isinstance(confidence, bool) and 0 <= confidence <= 1


async def classify_batch(
    client: Any, prompt: str, batch: list[dict[str, Any]], model: str, *, debug: bool = False,
) -> list[dict[str, Any]]:
    """Call the model, retrying when it omits or invents tag IDs."""
    expected = {item["tag_id"] for item in batch}
    collected: dict[str, dict[str, Any]] = {}
    for attempt in range(1, 4):
        try:
            response = await client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}], temperature=0,
            )
            raw = response.choices[0].message.content or ""
            if debug:
                print("  Raw model response:")
                print(raw[:4000] if raw else "  <empty response>")
            proposals = parse_json(raw).get("proposals", [])
            for proposal in proposals:
                if isinstance(proposal, dict) and proposal.get("tag_id") in expected and proposal_is_well_formed(
                    proposal
                ):
                    collected[proposal["tag_id"]] = proposal
            if set(collected) == expected:
                return [collected[item["tag_id"]] for item in batch]
            missing = sorted(expected - set(collected))
            prompt += (
                f"\nYour last response was incomplete. Return exactly one proposal for these missing tag_id "
                f"values: {missing}. Do not include any other tag IDs."
            )
            print(f"  Incomplete response; retrying ({attempt}/3), missing {len(missing)}")
        except Exception as exc:  # noqa: BLE001 - retry transient API and response errors
            print(f"  Invalid model response; retrying ({attempt}/3): {exc}")
    raise CaseBError(f"Model failed to return one valid proposal for every tag in batch: {sorted(expected)}")


def source_skills(path: str | Path) -> dict[str, dict[str, str]]:
    """Skill title, description and type by SkillsFuture skill_id, for prompt context."""
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = workbook["Unique Skills List"].iter_rows(values_only=True)
        headers = [str(value or "").strip() for value in next(rows)]
        result = {}
        for values in rows:
            row = dict(zip(headers, values, strict=False))
            skill_id = str(row.get("skill_id") or "").strip()
            if skill_id:
                result[skill_id] = {
                    field: str(row.get(f"skill_{field}") or "").strip() for field in ("title", "description", "type")
                }
        return result
    finally:
        workbook.close()


def baseline_hierarchy(baseline: TaxonomyDataset) -> tuple[list[str], list[dict[str, str]]]:
    """Baseline category names and (category, speciality) pairs the model may place into."""
    categories = {str(row["id"]): str(row["name"]) for row in baseline.categories}
    specialities = [
        {"category": categories.get(str(row["category_id"]), str(row["category_id"])), "name": str(row["name"])}
        for row in baseline.specialities
    ]
    return sorted(categories.values()), specialities


def incoming_tags(incoming: TaxonomyDataset) -> dict[str, str]:
    return {str(row["id"]): str(row["name"]) for row in incoming.tags}


def build_prompt(categories: list[str], specialities: list[dict[str, str]], batch: list[dict[str, Any]]) -> str:
    return (
        "Classify each incoming SkillsFuture skill into Ankit's taxonomy. Use only the Ankit "
        "services/categories/specialities listed below for existing placements. Propose a new category or "
        "speciality only when no suitable Ankit placement exists. The source tag remains a SkillsFuture tag; "
        "the proposed placement must target Ankit's taxonomy. Return one proposal per tag_id and copy the "
        "original skill title into the tag field exactly.\n\nAnkit categories:\n" + json.dumps(categories)
        + "\nAnkit specialities:\n" + json.dumps(specialities) + "\nSkillsFuture skills:\n" + json.dumps(batch)
        + "\nReturn ONLY valid JSON matching this shape (no Markdown): " + json.dumps(RESPONSE_SCHEMA)
    )


def to_contract_records(
    proposals: list[dict[str, Any]], tags: dict[str, str], *, source_dataset: str, run_id: str | None,
) -> list[dict[str, Any]]:
    """Map model proposals onto the nine-field contract, keeping `action` and the run stamp as extras."""
    records = []
    for proposal in proposals:
        unresolved = not proposal.get("category") or not proposal.get("speciality")
        records.append({
            "action": proposal["action"],
            "source_dataset": source_dataset,
            "tag_id": str(proposal["tag_id"]),
            "tag": tags.get(str(proposal["tag_id"]), ""),
            "original_classification": "unclassified",
            "service": None if unresolved else "Industry",
            "category": None if unresolved else proposal["category"],
            "speciality": None if unresolved else proposal["speciality"],
            "confidence": None if unresolved else proposal.get("confidence"),
            "reason": proposal.get("reason") or "No classification returned.",
            RUN_ID_FIELD: run_id,
        })
    CLASSIFICATION_OUTPUTS.validate_python(records)
    return records


def write_outputs(records: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    with output.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(records)


def _openai_client(debug: bool) -> Any:
    try:
        from dotenv import load_dotenv
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise CaseBError('Case B needs the optional LLM dependencies: pip install -e ".[llm]"') from exc
    load_dotenv(ENV_FILE)
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key or api_key in {"your-openai-api-key", "your-api-key"}:
        raise CaseBError(f"Set a real OPENAI_API_KEY in {ENV_FILE}.")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    if debug:
        print(f"OpenAI endpoint: {base_url}")
        print("API key loaded: yes")
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


def _load_checkpoint(path: Path, provenance: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Completed proposals by tag ID, but only from a checkpoint written for this exact run.

    A checkpoint from another saturation run, source or model holds paid results
    for a different question; reusing it would stamp old placements with the new
    run ID. It is refused rather than discarded, so deleting paid work is the
    user's decision.
    """
    if not path.exists():
        return {}
    saved = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(saved, dict):
        raise CaseBError(
            f"Checkpoint {path} has no record of its saturation run or model (written by an older version). "
            "Delete it to start this run from scratch."
        )
    for field, expected in provenance.items():
        if saved.get(field) != expected:
            label = "saturation run" if field == "saturation_run_id" else field
            raise CaseBError(
                f"Checkpoint {path} was written for a different {label} ({field} {saved.get(field)!r}, "
                f"this run {expected!r}). Rerun against the matching report and model, or delete the checkpoint."
            )
    return {str(proposal["tag_id"]): proposal for proposal in saved.get("proposals", [])}


def _save_checkpoint(path: Path, provenance: dict[str, Any], proposals: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**provenance, "proposals": list(proposals.values())}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def default_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


async def run_case_b(
    datasets: dict[str, Any],
    report_path: str | Path,
    *,
    baseline: str,
    incoming: str,
    unique_skills: str | Path,
    output: str | Path,
    checkpoint: str | Path | None = None,
    batch_size: int = 5,
    model: str | None = None,
    debug: bool = False,
    client: Any = None,
) -> list[dict[str, Any]]:
    """Classify every Case B tag and write `output` (JSON) plus a CSV next to it.

    Completed batches are checkpointed, so an interrupted run resumes where it
    stopped, with any batch size; a successful run removes the checkpoint. The
    checkpoint records the saturation run, source and model it was written for,
    and a mismatched one is refused instead of reused.
    """
    for name in (baseline, incoming):
        if not isinstance(datasets.get(name), TaxonomyDataset):
            raise CaseBError(f"Dataset {name!r} is not a taxonomy loaded by this config")
    output = Path(output)
    checkpoint = Path(checkpoint) if checkpoint else output.with_name(f".{output.stem}.checkpoint.json")
    model = model or default_model()

    saturation = load_saturation(report_path, incoming)
    tags = incoming_tags(datasets[incoming])
    input_tag_ids = [tag_id for tag_id in map(str, case_b_tag_ids(saturation)) if tag_id in tags]
    if not input_tag_ids:
        raise CaseBError(f"None of the Case B tags in {report_path} exist in dataset {incoming!r}")
    ambiguous = sum(tag_id in saturation.ambiguous_matches() for tag_id in input_tag_ids)
    print(f"Using {len(input_tag_ids)} Case B tag(s) from {report_path} (NO_PARENT; {ambiguous} need review)")

    skills = source_skills(unique_skills)
    tag_to_skill = {str(TAG_ID_OFFSET + index): skill_id for index, skill_id in enumerate(sorted(skills))}
    categories, specialities = baseline_hierarchy(datasets[baseline])
    if client is None:
        client = _openai_client(debug)
    if debug:
        print(f"Model: {model}")

    provenance = {"saturation_run_id": saturation.run_id, "incoming": incoming, "model": model}
    wanted = set(input_tag_ids)
    proposals = {
        tag_id: proposal for tag_id, proposal in _load_checkpoint(checkpoint, provenance).items() if tag_id in wanted
    }
    # Batch only what is still missing, so resuming with a different --batch-size never resends finished tags.
    pending = [tag_id for tag_id in input_tag_ids if tag_id not in proposals]
    if proposals:
        print(f"Resuming from {checkpoint}: {len(proposals)}/{len(input_tag_ids)} already classified")
    for start in range(0, len(pending), batch_size):
        batch = []
        for tag_id in pending[start:start + batch_size]:
            skill_id = tag_to_skill.get(tag_id)
            batch.append({"tag_id": tag_id, "title": tags[tag_id], "skill_id": skill_id, **skills.get(skill_id, {})})
        prompt = build_prompt(categories, specialities, batch)
        for proposal in await classify_batch(client, prompt, batch, model, debug=debug):
            proposals[proposal["tag_id"]] = proposal
        _save_checkpoint(checkpoint, provenance, proposals)
        print(f"Processed {len(proposals)}/{len(input_tag_ids)}")

    ordered = [proposals[tag_id] for tag_id in input_tag_ids]
    records = mark_ambiguous(
        to_contract_records(ordered, tags, source_dataset=incoming, run_id=saturation.run_id), saturation,
    )
    CLASSIFICATION_OUTPUTS.validate_python(records)
    write_outputs(records, output)
    print(f"Wrote {len(records)} classifications to {output} and {output.with_suffix('.csv')}")
    if checkpoint.exists():
        checkpoint.unlink()
    return records
