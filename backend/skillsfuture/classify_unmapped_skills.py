"""Use OpenAI to propose classifications for currently unused SkillsFuture tags.

Proposal-only: this script writes JSON and CSV review files. It never writes to
MySQL and never changes the generated SQL.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from jsonschema import Draft202012Validator
from openai import AsyncOpenAI
from openpyxl import load_workbook
from validate_skillsfuture_taxonomy import load_rows, parse_value_tuple

ROOT = Path(__file__).parent
load_dotenv(ROOT.parent / ".env")
DEBUG_RESPONSE = False
VALID_ACTIONS = {"assign_existing", "propose_new_speciality", "propose_new_category"}
SCHEMA = {
    "type": "object",
    "properties": {"proposals": {"type": "array", "items": {"type": "object", "properties": {
        "tag": {"type": "string"}, "tag_id": {"type": "string"}, "action": {"type": "string", "enum": ["assign_existing", "propose_new_speciality", "propose_new_category"]},
        "category": {"type": "string"}, "speciality": {"type": "string"}, "confidence": {"type": "number"}, "reason": {"type": "string"}
    }, "required": ["tag", "tag_id", "action", "category", "speciality", "confidence", "reason"], "additionalProperties": False}}},
    "required": ["proposals"], "additionalProperties": False,
}


def parse_json(text: str) -> dict:
    """Parse plain JSON or JSON wrapped in a Markdown code fence."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def proposal_is_well_formed(proposal: dict) -> bool:
    required = {"tag", "tag_id", "action", "category", "speciality", "confidence", "reason"}
    if not required.issubset(proposal) or proposal.get("action") not in VALID_ACTIONS:
        return False
    return isinstance(proposal.get("confidence"), (int, float)) and 0 <= proposal["confidence"] <= 1


async def classify_batch(client: AsyncOpenAI, prompt: str, batch: list[dict], model: str) -> list[dict]:
    """Call the model, retrying when it omits or invents tag IDs."""
    expected = {item["tag_id"] for item in batch}
    collected: dict[str, dict] = {}
    for attempt in range(1, 4):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            raw = response.choices[0].message.content or ""
            if DEBUG_RESPONSE:
                print("  Raw model response:")
                print(raw[:4000] if raw else "  <empty response>")
            payload = parse_json(raw)
            proposals = payload.get("proposals", [])
            valid = [p for p in proposals if isinstance(p, dict) and p.get("tag_id") in expected and proposal_is_well_formed(p)]
            for proposal in valid:
                collected[proposal["tag_id"]] = proposal
            if set(collected) == expected:
                return [collected[item["tag_id"]] for item in batch]
            missing = sorted(expected - set(collected))
            prompt += f"\nYour last response was incomplete. Return exactly one proposal for these missing tag_id values: {missing}. Do not include any other tag IDs."
            print(f"  Incomplete response; retrying ({attempt}/3), missing {len(missing)}")
        except Exception as exc:  # noqa: BLE001 - retry transient API and response errors
            print(f"  Invalid model response; retrying ({attempt}/3): {exc}")
    raise ValueError(f"Model failed to return one valid proposal for every tag in batch: {sorted(expected)}")


def source_skills(path: Path) -> dict[str, dict[str, str]]:
    ws = load_workbook(path, read_only=True, data_only=True)["Unique Skills List"]
    it = ws.iter_rows(values_only=True); headers = [str(x or "").strip() for x in next(it)]
    result = {}
    for values in it:
        row = dict(zip(headers, values)); sid = str(row.get("skill_id") or "").strip()
        if sid: result[sid] = {"title": str(row.get("skill_title") or "").strip(), "description": str(row.get("skill_description") or "").strip(), "type": str(row.get("skill_type") or "").strip()}
    return result


def original_hierarchy(sql_dir: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Load Ankit's categories and specialities as placement candidates."""
    categories: dict[str, str] = {}
    specialities: list[dict[str, str]] = []
    for table, path in (("categories", sql_dir / "categories.sql"), ("specialities", sql_dir / "specialities.sql")):
        current = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("INSERT INTO") and f"`{table}`" in line:
                current = True
                continue
            if not current:
                continue
            values = parse_value_tuple(line)
            if values:
                if table == "categories" and len(values) > 9:
                    categories[values[0]] = values[1]
                elif table == "specialities" and len(values) > 8:
                    specialities.append({"category": categories.get(values[8], values[8]), "name": values[1]})
            if line.strip().endswith(";"):
                current = False
    return sorted(categories.values()), specialities


def report_tag_ids(path: Path, actions: set[str], parent_status: str | None = None) -> list[str]:
    """Read only the requested saturation candidates from the report."""
    report = json.loads(path.read_text(encoding="utf-8"))
    saturation = next(result for result in report["results"] if result.get("metric_id") == "saturation")
    concepts = saturation["details"]["by_concept_level"]["tags"]["ambiguous_concepts"]
    tag_ids = []
    for concept in concepts:
        if concept.get("recommended_action") in actions and (
            parent_status is None or concept.get("parent_status") == parent_status
        ):
            tag_ids.extend(str(tag_id) for tag_id in concept.get("incoming_ids", []))
    return list(dict.fromkeys(tag_ids))


def case_b_tag_ids(path: Path, tags: dict[str, str]) -> list[str]:
    """Return only saturation-approved Case B tags.

    Case B must use the saturation report as its source of truth. Falling back
    to SQL-derived unused tags could send duplicates or ambiguous records to
    the paid classifier.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Case B report not found: {path}. Generate taxonomy_pipeline/report.json first."
        )

    try:
        report_ids = report_tag_ids(path, {"ADD_CANDIDATE"}, "NO_PARENT")
    except (json.JSONDecodeError, KeyError, StopIteration, TypeError) as exc:
        raise ValueError(f"Invalid Case B report: {path} ({exc})") from exc
    input_tag_ids = [tag_id for tag_id in report_ids if tag_id in tags]
    if not input_tag_ids:
        raise ValueError(
            f"No Case B tags found in {path}; expected recommended_action=ADD_CANDIDATE "
            "and parent_status=NO_PARENT."
        )
    return input_tag_ids


async def main() -> int:
    global DEBUG_RESPONSE
    parser = argparse.ArgumentParser()
    parser.add_argument("--sql", type=Path, default=ROOT / "skillsfuture_taxonomy.sql")
    parser.add_argument("--unique", type=Path, default=ROOT / "jobsandskills-skillsfuture-unique-skills-list.xlsx")
    parser.add_argument("--target-sql-dir", type=Path, default=ROOT.parent / "sql-files-nus", help="Ankit taxonomy SQL directory used for proposed placements")
    parser.add_argument("--report", type=Path, default=ROOT.parent / "taxonomy_pipeline" / "report.json", help="taxonomy pipeline report used to select input tags")
    parser.add_argument("--output", type=Path, default=ROOT / "classification_proposals.json")
    parser.add_argument("--schema", type=Path, default=ROOT / "classification_output.schema.json")
    parser.add_argument("--checkpoint", type=Path, default=None, help="checkpoint file for resuming after a failed run")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), help="OpenAI model name")
    parser.add_argument("--debug-response", action="store_true", help="print up to 4,000 characters of each raw response")
    args = parser.parse_args()
    checkpoint = args.checkpoint or args.output.with_name(f".{args.output.stem}.checkpoint.json")
    DEBUG_RESPONSE = args.debug_response
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key or api_key in {"your-openai-api-key", "your-api-key"}:
        raise SystemExit("Set a real OPENAI_API_KEY in backend/.env.")

    data = load_rows(args.sql)
    tags = {row[0]: row[1] for row in data["sf_tags"]}
    skills = source_skills(args.unique)
    unique_ids = sorted(skills)
    skill_to_tag = {skill_id: str(3000 + index) for index, skill_id in enumerate(unique_ids)}
    tag_to_skill = {tag_id: skill_id for skill_id, tag_id in skill_to_tag.items()}
    categories, specialities = original_hierarchy(args.target_sql_dir)
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    if args.debug_response:
        print(f"OpenAI endpoint: {base_url}")
        print("API key loaded: yes")
        print(f"Model: {args.model}")
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    try:
        input_tag_ids = case_b_tag_ids(args.report, tags)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Using {len(input_tag_ids)} Case B tag(s) from {args.report} (ADD_CANDIDATE + NO_PARENT)")
    proposals = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else []
    input_tag_set = set(input_tag_ids)
    proposals = [proposal for proposal in proposals if str(proposal.get("tag_id")) in input_tag_set]
    completed = {str(proposal.get("tag_id")) for proposal in proposals}
    for start in range(0, len(input_tag_ids), args.batch_size):
        batch = []
        for tag_id in input_tag_ids[start:start + args.batch_size]:
            skill_id = tag_to_skill.get(tag_id)
            source = skills.get(skill_id, {})
            batch.append({"tag_id": tag_id, "title": tags[tag_id], "skill_id": skill_id, **source})
        if all(item["tag_id"] in completed for item in batch):
            print(f"Skipped completed batch {min(start + args.batch_size, len(input_tag_ids))}/{len(input_tag_ids)}")
            continue
        prompt = "Classify each incoming SkillsFuture skill into Ankit's taxonomy. Use only the Ankit services/categories/specialities listed below for existing placements. Propose a new category or speciality only when no suitable Ankit placement exists. The source tag remains a SkillsFuture tag; the proposed placement must target Ankit's taxonomy. Return one proposal per tag_id and copy the original skill title into the tag field exactly.\n\nAnkit categories:\n" + json.dumps(categories) + "\nAnkit specialities:\n" + json.dumps(specialities) + "\nSkillsFuture skills:\n" + json.dumps(batch)
        prompt += "\nReturn ONLY valid JSON matching this shape (no Markdown): " + json.dumps(SCHEMA)
        proposals.extend(await classify_batch(client, prompt, batch, args.model))
        checkpoint.write_text(json.dumps(proposals, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Processed {min(start + args.batch_size, len(input_tag_ids))}/{len(input_tag_ids)}")

    proposals = [{**proposal, "tag": tags.get(proposal["tag_id"], "")} for proposal in proposals]
    classification_output = []
    for proposal in proposals:
        unresolved = not proposal.get("category") or not proposal.get("speciality")
        classification_output.append({
            "action": proposal["action"],
            "source_dataset": "skillsfuture",
            "tag_id": str(proposal["tag_id"]),
            "tag": proposal["tag"],
            "original_classification": "unclassified",
            "service": None if unresolved else "Industry",
            "category": None if unresolved else proposal["category"],
            "speciality": None if unresolved else proposal["speciality"],
            "confidence": None if unresolved else proposal.get("confidence"),
            "reason": proposal.get("reason") or "No classification returned.",
        })
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    schema_errors = sorted(Draft202012Validator(schema).iter_errors(classification_output), key=lambda error: list(error.path))
    if schema_errors:
        details = "; ".join(error.message for error in schema_errors[:5])
        raise ValueError(f"Generated classification output does not match schema: {details}")
    args.output.write_text(json.dumps(classification_output, indent=2, ensure_ascii=False), encoding="utf-8")
    with args.output.with_suffix(".csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["action", "source_dataset", "tag_id", "tag", "original_classification", "service", "category", "speciality", "confidence", "reason"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(classification_output)
    print(f"Wrote {len(classification_output)} schema-compliant classifications to {args.output} and {args.output.with_suffix('.csv')}")
    if checkpoint.exists():
        checkpoint.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
