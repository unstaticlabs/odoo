#!/usr/bin/env python3
"""Stream a verified cohort into authenticated age encryption."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


class EncryptionError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encrypt(bundle: Path, output: Path, recipient: str) -> dict[str, object]:
    bundle = bundle.expanduser().resolve()
    output = output.expanduser().resolve()
    if not bundle.is_dir():
        raise EncryptionError("cohort bundle is not a directory")
    if not re.fullmatch(r"age1[0-9a-z]{20,100}", recipient):
        raise EncryptionError("recipient must be an age public key")
    if output.exists() or output.with_suffix(output.suffix + ".sha256").exists():
        raise EncryptionError("encrypted cohort output already exists")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.chmod(0o600)
    try:
        with subprocess.Popen(
            ("tar", "-C", str(bundle.parent), "-cf", "-", bundle.name),
            stdout=subprocess.PIPE,
        ) as archive:
            encrypted = subprocess.run(
                ("age", "--encrypt", "--recipient", recipient, "--output", str(temporary)),
                stdin=archive.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                check=False,
            )
            if archive.stdout:
                archive.stdout.close()
            archive_status = archive.wait()
        if archive_status or encrypted.returncode:
            message = encrypted.stderr.decode("utf-8", errors="replace").strip()
            raise EncryptionError(message or "cohort encryption failed")
        temporary.replace(output)
        output.chmod(0o600)
        digest = sha256(output)
        checksum = output.with_suffix(output.suffix + ".sha256")
        checksum.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
        checksum.chmod(0o600)
        return {
            "schema": "usl-encrypted-cohort-v1",
            "path": str(output),
            "sha256": digest,
            "size": output.stat().st_size,
        }
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: encrypt_cohort.py BUNDLE OUTPUT AGE_RECIPIENT", file=sys.stderr)
        return 2
    try:
        value = encrypt(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except (EncryptionError, OSError) as error:
        print(f"Cohort encryption refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
