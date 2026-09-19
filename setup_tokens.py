"""Run once on your own machine to create Garmin tokens (supports MFA). See CLAUDE.md §5.

Outputs:
  ~/.garminconnect/garmin_tokens.json  — for local runs
  garmin_tokens.b64                     — paste into the GARMINTOKENS_BASE64 secret, then delete
"""

from __future__ import annotations

import base64
import getpass
import logging
import os
import re
import sys
from pathlib import Path

from garminconnect import Garmin, GarminConnectAuthenticationError

TOKEN_DIR = "~/.garminconnect"
B64_FILE = "garmin_tokens.b64"


MFA_ATTEMPTS = 3


def _resume_with_mfa(api, client_state) -> None:
    """Prompt for the MFA code, allowing retries on the same pending login session."""
    print("🔐 Garmin needs an MFA code. Use the NEWEST code (a new one may have just been sent;")
    print("   earlier codes stop working once a new one is issued).")
    for attempt in range(1, MFA_ATTEMPTS + 1):
        code = re.sub(r"[\s-]", "", input("MFA code: "))
        try:
            api.resume_login(client_state, code)
            return
        except GarminConnectAuthenticationError as e:
            print(f"❌ Code rejected ({e}). Attempt {attempt}/{MFA_ATTEMPTS}.")
    raise SystemExit("MFA failed. Wait a few minutes, then run setup_tokens.py again.")


def main() -> int:
    if "--debug" in sys.argv:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")

    api = Garmin(email, password, return_on_mfa=True)
    r1, r2 = api.login()
    if r1 == "needs_mfa":
        _resume_with_mfa(api, r2)

    api.client.dump(TOKEN_DIR)
    print(f"✅ Saved tokens to {Path(TOKEN_DIR).expanduser()}/ for local runs")

    encoded = base64.b64encode(api.client.dumps().encode("utf-8")).decode("ascii")
    fd = os.open(B64_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(encoded)

    print(f"✅ Wrote {B64_FILE}")
    print("   1. Copy its contents into the GitHub Secret GARMINTOKENS_BASE64")
    print(f"      (macOS: pbcopy < {B64_FILE})")
    print(f"   2. Delete the file: rm {B64_FILE}")
    print("   The file is in .gitignore — never commit it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
