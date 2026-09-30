"""
Read-only look at how days_availability is stored in your REAL roster table,
and what the agent makes of it. Never writes.

    python dev/check_roster.py                     a few sample rows
    python dev/check_roster.py "Rohit"             one employee (name, e-mail or emp id)
    python dev/check_roster.py "Rohit" --date 2026-09-30

For each row it prints the stored TYPE (map or text), the first characters of
the raw value, how many days were understood, and today's availability as the
agent reads it. If "understood" is 0 for a row that has data, paste the output
back: the raw sample shows the format that is not handled yet.
"""

import argparse
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app" / "dtp_operate_agent"

sys.path.insert(0, str(APP_DIR))

os.environ.setdefault("ENVIRONMENT", "local")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from services import get_roster_service  # noqa: E402
from services.common import scan_items  # noqa: E402
from services.roster_service import decode_days  # noqa: E402


def describe(item, day):

    raw = item.get("days_availability")

    codes, _, kind = decode_days(raw)

    sample = str(raw)[:70].replace("\n", " ")

    print(
        f"  UID={item.get('UID')!r} ({type(item.get('UID')).__name__})  "
        f"{item.get('employee_name')}  {item.get('year')}-{item.get('month')}  team={item.get('team_id')}"
    )
    print(f"    stored as: {kind} ({type(raw).__name__})   raw: {sample}")
    print(f"    understood {len(codes)} day(s); values used: {sorted(set(codes.values())) or '-'}")

    if day:
        print(f"    day {day}: {codes.get(day, '(no entry)')}")


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("employee", nargs="?", help="name, e-mail or emp id (default: sample rows)")
    parser.add_argument("--date", help="YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    service = get_roster_service()

    from services.common import resolve_date
    from datetime import date

    iso = resolve_date(args.date or "today")

    day = date.fromisoformat(iso).day

    print(f"Roster table: {service.table.name}   checking day {day} of {iso}\n")

    if args.employee:

        rows = [
            row for row in scan_items(service.table)
            if args.employee.lower() in " ".join(
                str(row.get(field, "")) for field in ("employee_name", "employee_id", "emp_id")
            ).lower()
        ]

        if not rows:
            print("No roster row matches that.")
            return

        for row in rows[:12]:
            describe(row, day)

        print("\nAgent's answer:")
        print(" ", service.get_employee_availability(args.employee, iso))

        return

    for index, row in enumerate(scan_items(service.table)):

        if index >= 5:
            break

        describe(row, day)


if __name__ == "__main__":
    main()
