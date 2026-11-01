"""Locks the repo's Langfuse keys again for the CI key and every laptop listed.

Runs in the CI job on the branch mate-record-keys, from the root of its checkout:

    MATE_RECORD_CI_KEY=<the CI key's private half> python mate-record/relock.py

    needs        Python 3.10+ with pyrage 1.4.0
    reads        mate-record/langfuse-keys.age, mate-record/ci.pub, and every
                 mate-record/laptops/**/*.pub (one age public half per file)
    writes       mate-record/langfuse-keys.age, in age's text form, for exactly those
    stdout       one line naming the laptop files it was locked for
    exit         0 done; a Python error when MATE_RECORD_CI_KEY is missing or does not
                 open the file, or a public half cannot be read

The setup script imports this file for the branch's layout (ROOT, LOCK, CI_PUB,
LAPTOPS) and for lock(plain, recipients), the age text form of plain for recipients.
"""

import os
import sys
from pathlib import Path
from typing import List

from pyrage import decrypt, encrypt, x25519

ROOT = Path("mate-record")
LOCK = ROOT / "langfuse-keys.age"
CI_PUB = ROOT / "ci.pub"
LAPTOPS = ROOT / "laptops"


def lock(plain: bytes, recipients: List[x25519.Recipient]) -> bytes:
    locked = encrypt(plain, recipients, armored=True)
    return locked if isinstance(locked, bytes) else locked.encode("ascii")


def main() -> int:
    ci = x25519.Identity.from_str(os.environ["MATE_RECORD_CI_KEY"].strip())
    plain = decrypt(LOCK.read_bytes(), [ci])
    laptops = sorted(LAPTOPS.rglob("*.pub"))
    recipients = [x25519.Recipient.from_str(CI_PUB.read_text(encoding="utf-8").strip())]
    recipients += [x25519.Recipient.from_str(path.read_text(encoding="utf-8").strip()) for path in laptops]
    LOCK.write_bytes(lock(plain, recipients))
    names = ", ".join(path.relative_to(ROOT).as_posix() for path in laptops)
    print(f"Locked for the CI key and {len(laptops)} laptops: {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
