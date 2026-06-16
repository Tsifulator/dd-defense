"""
D&D Defense — Daily prospect scraper + outreach scheduler.

Runs on Railway as a cron job. Pipeline:
  1. Use Firecrawl to discover new freight forwarders at US ports
  2. Draft personalized cold emails via outreach.queue_prospects()
  3. Sync cases to Airtable

Schedule: Daily at 07:00 UTC (10:00 Athens / 3:00 AM ET)
"""

import json
import logging
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Prospect Discovery ──────────────────────────────────────────────────────

# Target: small freight forwarders handling perishables/reefer at US ports
SEARCH_QUERIES = [
    "freight forwarder perishable reefer Los Angeles Long Beach",
    "freight forwarder refrigerated cargo New York Newark NJ",
    "freight forwarder cold chain Savannah GA",
    "customs broker seafood import Miami FL",
    "freight forwarder produce import Houston TX",
    "NVOCC perishable freight forwarder USA",
]

# Rotate through queries (one per day to avoid spam patterns)
def _today_query() -> str:
    day_index = date.today().toordinal() % len(SEARCH_QUERIES)
    return SEARCH_QUERIES[day_index]


def scrape_prospects() -> list[dict]:
    """Use Claude to find and qualify freight forwarder prospects."""
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic not installed — can't scrape prospects")
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set")
        return []

    query = _today_query()
    logger.info(f"Prospect discovery query: {query}")

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""You are a B2B lead researcher for D&D Defense, a tool that audits ocean-carrier demurrage & detention invoices.

Search for: {query}

Find 5-10 real freight forwarding companies that:
- Handle ocean freight (FCL/LCL containers)
- Deal with perishables, reefer, or cold chain cargo
- Operate at major US ports (LA/LB, NY/NJ, Savannah, Miami, Houston)
- Are small to mid-size (5-100 employees)

For each company, provide:
- company: full legal name
- type: "forwarder" or "broker" or "importer"
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
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()

        # Parse JSON (handle markdown fences)
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        prospects = json.loads(text)
        if not isinstance(prospects, list):
            logger.error(f"Expected list, got {type(prospects)}")
            return []

        logger.info(f"Discovered {len(prospects)} prospects")
        return prospects

    except Exception as e:
        logger.exception(f"Prospect scraping failed: {e}")
        return []


# ─── Daily Pipeline ──────────────────────────────────────────────────────────

def run_daily():
    """Full daily pipeline: scrape → draft → queue in Airtable."""
    logger.info(f"=== D&D Defense Daily Pipeline — {date.today()} ===")

    # Step 1: Discover new prospects
    prospects = scrape_prospects()
    if not prospects:
        logger.warning("No prospects discovered today")
        return

    # Step 2: Queue them into Airtable via outreach module
    try:
        from dd_defense.outreach import queue_prospects
        result = queue_prospects(
            prospects,
            sender_name=os.getenv("SENDER_NAME", "Nick"),
            sender_phone=os.getenv("SENDER_PHONE", ""),
            use_llm=True,
            status="Needs Approval",
            dedupe=True,
        )
        logger.info(
            f"Queued {len(result.get('created', []))} prospects, "
            f"skipped {len(result.get('skipped', []))} dupes"
        )
    except Exception as e:
        logger.exception(f"Queue prospects failed: {e}")

    # Step 3: Sync cases (if any local cases exist)
    try:
        db_path = os.getenv("DD_DB_PATH", "data/cases.db")
        if Path(db_path).exists():
            from dd_defense.airtable.sync import sync
            result = sync(db_path=db_path)
            logger.info(f"Synced {result['total']} cases to Airtable")
    except Exception as e:
        logger.warning(f"Case sync skipped: {e}")

    logger.info("=== D&D Defense Daily Pipeline Complete ===")


if __name__ == "__main__":
    run_daily()

    # Send confirmation
    try:
        import requests
        key = os.getenv("RESEND_API_KEY")
        if key:
            requests.post("https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "from": os.getenv("FROM_EMAIL", "nick@kuroshiotrade.com"),
                    "to": [os.getenv("OPERATOR_EMAIL", "tsiflik@bc.edu")],
                    "subject": f"[D&D Defense] Daily prospects — {date.today()}",
                    "text": f"D&D Defense prospect pipeline finished for {date.today()}.\nCheck Airtable for new prospects to review.",
                })
            logger.info("Confirmation email sent")
    except Exception as e:
        logger.warning(f"Notification failed: {e}")
