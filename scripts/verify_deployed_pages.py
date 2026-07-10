#!/usr/bin/env python3
"""Verify the deployed Pages payload against its allowlisted checksum manifest."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import ssl
import time
import urllib.parse
import urllib.request

try:
    import certifi
except ImportError:  # pragma: no cover
    certifi = None


def fetch(url: str, attempts: int = 3) -> bytes:
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else None
    request = urllib.request.Request(url, headers={"User-Agent": "NatureWxLab-climate-outlook-navi-verifier/1.0"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=30, context=context) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}: {url}")
                return response.read()
        except Exception as error:  # noqa: BLE001 - final error is raised without response content
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(2)
    assert last_error is not None
    raise RuntimeError(f"failed to fetch {url} after {attempts} attempts: {last_error}") from last_error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--manifest-attempts", type=int, default=18)
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/") + "/"
    deployment = None
    for attempt in range(args.manifest_attempts):
        candidate = json.loads(fetch(urllib.parse.urljoin(base_url, "deployment.json")).decode("utf-8"))
        if candidate.get("source_commit") == args.source_commit:
            deployment = candidate
            break
        if attempt + 1 < args.manifest_attempts:
            time.sleep(10)
    if deployment is None:
        raise AssertionError("deployed source commit did not advance to the requested commit")

    def verify(entry: tuple[str, dict[str, int | str]]) -> tuple[str, int]:
        path, expected = entry
        try:
            raw = fetch(urllib.parse.urljoin(base_url, path))
        except Exception as error:  # noqa: BLE001 - include the exact failed deployment path
            raise RuntimeError(f"failed to verify deployed file {path}: {error}") from error
        if len(raw) != expected["bytes"]:
            raise AssertionError(f"deployed byte count mismatch: {path}")
        if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
            raise AssertionError(f"deployed checksum mismatch: {path}")
        return path, len(raw)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        verified = list(executor.map(verify, deployment["files"].items()))
    print(json.dumps({
        "source_commit": deployment["source_commit"],
        "climate_dataset_id": deployment["climate_dataset_id"],
        "season_dataset_id": deployment["season_dataset_id"],
        "verified_file_count": len(verified),
        "verified_bytes": sum(size for _, size in verified),
        "status": "ok",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
