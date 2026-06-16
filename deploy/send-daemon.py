"""Auto-send daemon: polls Airtable for Approved prospects, sends via Gmail SMTP.

Runs every 5 minutes via launchd. When it finds prospects with status "Approved",
it sends the draft email and flips the status to "Sent".

Run manually:  python3 ~/dnd/deploy/send-daemon.py
Schedule:      managed by launchd (com.dnd.senddaemon.plist)
"""
import os
import sys
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

from dd_defense.airtable.send import send_approved


def main():
    result = send_approved(limit=5, dry_run=False, pace=3.0)
    if result["sent"] > 0:
        print(f"Auto-sent {result['sent']} email(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
