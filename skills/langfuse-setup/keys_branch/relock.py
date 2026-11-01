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
LAPTOPS), for lock(plain, recipients), the age text form of plain for recipients, and
for recipient_files(paths), the branch paths, as given, that the file is locked for:
CI_PUB and every .pub under LAPTOPS at any depth, sorted; a ValueError when
CI_PUB is not among them, because the job could not open the file again.
"""

import os
import sys
from pathlib import Path
from typing import Iterable, List

from pyrage import decrypt, encrypt, x25519

ROOT = Path("mate-record")
LOCK = ROOT / "langfuse-keys.age"
CI_PUB = ROOT / "ci.pub"
LAPTOPS = ROOT / "laptops"


def lock(plain: bytes, recipients: List[x25519.Recipient]) -> bytes:
    locked = encrypt(plain, recipients, armored=True)
    return locked if isinstance(locked, bytes) else locked.encode("ascii")


def recipient_files(paths: Iterable[str]) -> List[str]:
    laptops_prefix = f"{LAPTOPS.as_posix()}/"
    picked = [
        path for path in paths
        if path == CI_PUB.as_posix() or (path.startswith(laptops_prefix) and path.endswith(".pub"))
    ]
    if CI_PUB.as_posix() not in picked:
        raise ValueError(f"{CI_PUB.as_posix()} is missing")
    return sorted(picked)


def main() -> int:
    ci = x25519.Identity.from_str(os.environ["MATE_RECORD_CI_KEY"].strip())
    plain = decrypt(LOCK.read_bytes(), [ci])
    picked = recipient_files(path.as_posix() for path in ROOT.rglob("*") if path.is_file())
    recipients = [x25519.Recipient.from_str(Path(path).read_text(encoding="utf-8").strip()) for path in picked]
    LOCK.write_bytes(lock(plain, recipients))
    laptops = [path for path in picked if path != CI_PUB.as_posix()]
    names = ", ".join(path[len(f"{ROOT.as_posix()}/"):] for path in laptops)
    print(f"Locked for the CI key and {len(laptops)} laptops: {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
