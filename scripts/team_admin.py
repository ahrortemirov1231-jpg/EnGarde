#!/usr/bin/env python3
"""Accounts for the team server, from the command line (works even when nobody can sign in).

  python scripts/team_admin.py --data data first-admin          asks for a name and password (only if no admin exists)
  python scripts/team_admin.py --data data add NAME [--role member|admin|viewer]
  python scripts/team_admin.py --data data reset NAME           new password for NAME, signs them out everywhere
  python scripts/team_admin.py --data data list
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from engarde import auth  # noqa: E402
from engarde.store import Store  # noqa: E402


def ask_password() -> str:
    while True:
        a = getpass.getpass(f"Password (at least {auth.MIN_PASSWORD} characters; nothing shows while you type): ")
        if len(a) < auth.MIN_PASSWORD:
            print("Too short.")
            continue
        if a == getpass.getpass("Same password again: "):
            return a
        print("The two did not match. Try again.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("ENGARDE_DATA", os.path.join(os.path.dirname(__file__), "..", "data")))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("first-admin")
    a = sub.add_parser("add")
    a.add_argument("name")
    a.add_argument("--role", default="member", choices=auth.ROLES)
    r = sub.add_parser("reset")
    r.add_argument("name")
    sub.add_parser("list")
    args = ap.parse_args()
    db = Store(args.data).db
    if args.cmd == "first-admin":
        if auth.has_admin(db):
            return 0
        print("No admin account yet. Create yours now: this is the name your labels will carry.")
        name = input("Your name (for example Ahror): ").strip()
        u = auth.create_user(db, name, ask_password(), "admin")
        print(f"Admin account '{u['name']}' created.")
    elif args.cmd == "add":
        u = auth.create_user(db, args.name, ask_password(), args.role)
        print(f"Account '{u['name']}' ({u['role']}) created.")
    elif args.cmd == "reset":
        row = db.one("SELECT user_id FROM users WHERE name=? COLLATE NOCASE", (args.name,))
        if not row:
            print(f"No account called {args.name}.")
            return 1
        auth.update_user(db, row["user_id"], password=ask_password())
        print("Password changed. That person is signed out everywhere.")
    else:
        for u in auth.list_users(db):
            print(f"{u['name']:<24} {u['role']:<7} {'active' if u['active'] else 'OFF':<7} last sign-in: {u['last_login_at'] or 'never'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(f"Could not do that: {e}")
        sys.exit(1)
