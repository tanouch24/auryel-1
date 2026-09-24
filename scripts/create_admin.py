#!/usr/bin/env python3
"""Create one AURYEL CONTROL admin without putting credentials in Git."""
import argparse
import getpass
import os
import sys
import uuid

import psycopg2
from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    email = args.email.strip().lower()
    if "@" not in email:
        raise SystemExit("invalid email")
    password = getpass.getpass("Admin password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if not password or password != confirmation or len(password) < 12:
        raise SystemExit("password must match and contain at least 12 characters")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is required")
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM admin_accounts WHERE email_normalized=%s", (email,))
            if cur.fetchone():
                raise SystemExit("admin already exists")
            cur.execute("INSERT INTO admin_accounts (id,email,email_normalized,password_hash) VALUES (%s,%s,%s,%s)", (str(uuid.uuid4()), email, email, generate_password_hash(password)))
        conn.commit()
    finally:
        conn.close()
    print("admin created; keep credentials out of shell history and logs")
    print("Next: log in to Auryel Control, call POST /api/admin/auth/mfa/enroll,")
    print("scan the returned otpauth URI, then confirm with the first TOTP code.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
