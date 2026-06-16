"""Daily prospect discovery: find new freight forwarders, draft emails, queue in Airtable.

Runs every morning via launchd. Uses Claude to discover 5-10 new prospects,
drafts personalized outreach emails, and drops them in Airtable as "Needs Approval".
Deduplication is built in — companies already in Airtable are skipped.

Schedule: 10:00 Athens (07:00 UTC) daily via launchd
"""
import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("daily-prospects")

# Rotate through search queries daily
SEARCH_QUERIES = [
    "small freight forwarder perishable reefer Los Angeles Long Beach",
    "freight forwarder refrigerated cargo New York Newark NJ",
    "freight forwarder cold chain produce Savannah GA",
    "customs broker seafood import Miami FL",
    "freight forwarder produce import Houston TX",
    "NVOCC perishable freight forwarder USA",
    "drayage carrier container detention Los Angeles",
]


def _today_query():
    return SEARCH_QUERIES[date.today().toordinal() % len(SEARCH_QUERIES)]


def discover_prospects():
    """Use Claude to find 5-10 new freight forwarder prospects."""
    try:
        import anthropic
    except ImportError:
        log.error("anthropic not installed — pip install anthropic")
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set")
        return []

    query = _today_query()
    log.info(f"Discovery query: {query}")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""You are a B2B lead researcher for D&D Defense, a tool that audits ocean-carrier demurrage & detention invoices.

Search for: {query}

Find 5-8 real freight forwarding companies that:
- Handle ocean freight (FCL/LCL containers)
- Deal with perishables, reefer, or cold chain cargo
- Operate at major US ports (LA/LB, NY/NJ, Savannah, Miami, Houston)
- Are small to mid-size (5-200 employees)

For each company, provide:
- company: full legal name
- type: "forwarder" or "broker" or "drayage" or "importer"
- contact_name: a real person if findable (CEO, VP Ops, etc.), else ""
- title: their title, else ""
- email: their business email if findable, else ""
- phone: business phone if findable, else ""
- url: company website
- location: city, state
- containers_per_mo: estimated monthly container volume (number), else 0
- source: "AI Research"
- notes: why they're a good fit (perishable specialist, reefer focus, etc.)

Return ONLY a JSON array of objects. No prose, no markdown fences."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        prospects = json.loads(text)
        if not isinstance(prospects, list):
            log.error(f"Expected list, got {type(prospects)}")
            return []
        log.info(f"Discovered {len(prospects)} prospects")
        return prospects
    except Exception as e:
        log.exception(f"Discovery failed: {e}")
        return []


def main():
    log.info(f"=== Daily Prospect Discovery — {date.today()} ===")

    prospects = discover_prospects()
    if not prospects:
        log.warning("No prospects discovered today")
        return 0

    from dd_defense.outreach import queue_prospects
    try:
        result = queue_prospects(
            prospects,
            sender_name="Nick",
            sender_phone="",
            use_llm=False,
            status="Needs Approval",
            dedupe=True,
        )
        created = len(result.get("created", []))
        skipped = len(result.get("skipped", []))
        log.info(f"Queued {created} new prospect(s), skipped {skipped} dupes")
    except Exception as e:
        log.exception(f"Queue failed: {e}")

    log.info("=== Done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
