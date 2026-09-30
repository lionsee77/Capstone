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

from openai import AsyncOpenAI
from openpyxl import load_workbook
from dotenv import load_dotenv

from validate_skillsfuture_taxonomy import load_rows


ROOT = Path(__file__).parent
load_dotenv(ROOT.parent / ".env", override=True)
DEBUG_RESPONSE = False
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


async def classify_batch(client: AsyncOpenAI, prompt: str, batch: list[dict], model: str) -> list[dict]:
    """Call the model, retrying when it omits or invents tag IDs."""
    expected = {item["tag_id"] for item in batch}
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
            valid = [p for p in proposals if isinstance(p, dict) and p.get("tag_id") in expected]
            returned = {p["tag_id"] for p in valid}
            if returned == expected and len(valid) == len(batch):
                return valid
            missing = sorted(expected - returned)
            prompt += f"\nYour last response was incomplete. Return exactly one proposal for these missing tag_id values: {missing}. Do not include any other tag IDs."
            print(f"  Incomplete response; retrying ({attempt}/3), missing {len(missing)}")
        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
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


async def main() -> int:
    global DEBUG_RESPONSE
    parser = argparse.ArgumentParser()
    parser.add_argument("--sql", type=Path, default=ROOT / "skillsfuture_taxonomy.sql")
    parser.add_argument("--unique", type=Path, default=ROOT / "jobsandskills-skillsfuture-unique-skills-list.xlsx")
    parser.add_argument("--output", type=Path, default=ROOT / "classification_proposals.json")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), help="OpenAI model name")
    parser.add_argument("--debug-response", action="store_true", help="print up to 4,000 characters of each raw response")
    args = parser.parse_args()
    DEBUG_RESPONSE = args.debug_response
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key or api_key in {"your-openai-api-key", "your-api-key"}:
        raise SystemExit("Set a real OPENAI_API_KEY in backend/.env.")

    data = load_rows(args.sql)
    used = {row[2] for row in data["sf_speciality_tags"]}
    tags = {row[0]: row[1] for row in data["sf_tags"]}
    unused = [tag_id for tag_id in tags if tag_id not in used]
    skills = source_skills(args.unique)
    categories = sorted({row[1] for row in data["sf_categories"]})
    specialities = [{"category": next(row[1] for row in data["sf_categories"] if row[0] == s[2]), "name": s[1]} for s in data["sf_specialities"]]
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    if args.debug_response:
        print(f"OpenAI endpoint: {base_url}")
        print(f"API key loaded: yes ({len(api_key)} characters, prefix {api_key[:7]})")
        print(f"Model: {args.model}")
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    proposals = []
    for start in range(0, len(unused), args.batch_size):
        batch = [{"tag_id": i, "title": tags[i], **skills.get(i, {})} for i in unused[start:start + args.batch_size]]
        prompt = "Classify each SkillsFuture skill into the taxonomy. Prefer an existing category/speciality. Propose a new one only when no existing option fits. Do not invent a new service; use Industry. Return one proposal per tag_id and copy the original skill title into the tag field exactly.\n\nExisting categories:\n" + json.dumps(categories) + "\nExisting specialities:\n" + json.dumps(specialities) + "\nSkills:\n" + json.dumps(batch)
        prompt += "\nReturn ONLY valid JSON matching this shape (no Markdown): " + json.dumps(SCHEMA)
        proposals.extend(await classify_batch(client, prompt, batch, args.model))
        print(f"Processed {min(start + args.batch_size, len(unused))}/{len(unused)}")

    proposals = [{"tag": tags.get(proposal["tag_id"], ""), **proposal} for proposal in proposals]
    args.output.write_text(json.dumps(proposals, indent=2, ensure_ascii=False), encoding="utf-8")
    with args.output.with_suffix(".csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["tag_id", "tag", "action", "category", "speciality", "confidence", "reason", "review_status"])
        writer.writeheader()
        for p in proposals: writer.writerow({**p, "tag": tags.get(p["tag_id"], ""), "review_status": "pending"})
    print(f"Wrote {len(proposals)} proposals to {args.output} and {args.output.with_suffix('.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
