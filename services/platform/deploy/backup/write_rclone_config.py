#!/usr/bin/env python3
"""Write an rclone config with one Cloudflare R2 remote named `r2`, mode 0600.

The access key id and secret are read from the terminal without echo (or, with
--stdin, as two lines from standard input so they can be piped over ssh), and
are never printed or passed on a command line.
"""
import argparse
import getpass
import os
import re
import sys


def render(account_id, key_id, secret):
    if not re.fullmatch(r"[0-9a-f]{32}", account_id):
        raise SystemExit("account id must be 32 hex characters")
    if not re.fullmatch(r"[0-9a-f]{32}", key_id) or not re.fullmatch(r"[0-9a-f]{64}", secret):
        raise SystemExit("R2 access key id is 32 hex characters and the secret 64")
    return ("[r2]\ntype = s3\nprovider = Cloudflare\n"
            f"access_key_id = {key_id}\nsecret_access_key = {secret}\n"
            f"endpoint = https://{account_id}.r2.cloudflarestorage.com\n"
            "acl = private\nno_check_bucket = true\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--stdin", action="store_true")
    args = parser.parse_args()
    if args.stdin:
        key_id, secret = (sys.stdin.readline().strip() for _ in range(2))
    else:
        key_id = getpass.getpass("R2 Access Key ID: ").strip()
        secret = getpass.getpass("R2 Secret Access Key: ").strip()
    text = render(args.account_id, key_id, secret)
    path = os.path.expanduser(args.output)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temporary = path + ".new"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    os.replace(temporary, path)
    print(f"wrote {path} (0600)")


if __name__ == "__main__":
    main()
