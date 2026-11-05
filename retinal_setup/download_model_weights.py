#!/usr/bin/env python3
"""Download and checksum-verify three SLOctolyzer model weights from a versioned
organizational GitHub Release. Requires only the Python standard library.

This module and model_weights_manifest.json are installed with the package. The
release tag referenced in the manifest must be published before downloading.

Usage:
    python download_model_weights.py --check-only
    python download_model_weights.py
    python download_model_weights.py --cache-dir "C:/alternate/cache"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).resolve().with_name("model_weights_manifest.json")
CHUNK_SIZE = 1024 * 1024


def pytorch_checkpoints_dir() -> Path:
    """Return PyTorch's default hub/checkpoints location (without importing torch)."""
    explicit = os.environ.get("TORCH_HOME")
    if explicit:
        torch_home = Path(explicit).expanduser()
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        torch_home = (Path(xdg).expanduser() if xdg else Path.home() / ".cache") / "torch"
    return torch_home / "hub" / "checkpoints"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as infile:
        manifest = json.load(infile)
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported model manifest schema version")
    repository = manifest.get("repository", "")
    tag = manifest.get("release_tag", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository in model manifest")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", tag):
        raise ValueError("Invalid release tag in model manifest")
    models = manifest.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("Model manifest does not contain models")
    seen = set()
    for entry in models:
        fname = entry.get("filename")
        digest = entry.get("sha256")
        if not isinstance(fname, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.pth", fname):
            raise ValueError(f"Invalid checkpoint filename: {fname!r}")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid SHA-256 digest for {fname}")
        if fname in seen:
            raise ValueError(f"Duplicate model filename: {fname}")
        seen.add(fname)
    return manifest


def asset_url(manifest: dict, filename: str) -> str:
    return ("https://github.com/" + manifest["repository"] + "/releases/download/"
            + urllib.parse.quote(manifest["release_tag"]) + "/" + urllib.parse.quote(filename))


def ensure_model(path: Path, expected_hash: str, url: str, check_only: bool) -> bool:
    if path.is_file():
        actual = sha256_file(path)
        if actual == expected_hash:
            print(f"[OK] {path.name}: SHA-256 verified (cached)", flush=True)
            return True
        print(f"[INVALID] {path.name}: checksum mismatch ({actual})", flush=True)
    else:
        print(f"[MISSING] {path.name}", flush=True)

    if check_only:
        return False

    print(f"[DOWNLOAD] {path.name} from {url}", flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=path.name + ".", suffix=".part", dir=path.parent,
            delete=False,
        ) as temporary:
            temp_path = Path(temporary.name)
            req = urllib.request.Request(url, headers={"User-Agent": "retinal-vasculature-quantification/1.0"})
            with urllib.request.urlopen(req, timeout=60) as response:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    temporary.write(chunk)
        actual = sha256_file(temp_path)
        if actual != expected_hash:
            raise RuntimeError(f"SHA-256 mismatch for {path.name}: expected {expected_hash}, got {actual}")
        os.replace(temp_path, path)
        temp_path = None
        print(f"[OK] {path.name}: downloaded and SHA-256 verified", flush=True)
        return True
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cache-dir", type=Path, default=pytorch_checkpoints_dir())
    parser.add_argument("--check-only", action="store_true", help="Only verify local files; never download")
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    cache = args.cache_dir.expanduser()
    print(f"Checkpoint cache: {cache}")
    success = True
    for entry in manifest["models"]:
        filename = entry["filename"]
        try:
            valid = ensure_model(cache / filename, entry["sha256"],
                                 asset_url(manifest, filename), args.check_only)
        except (OSError, RuntimeError, urllib.error.URLError) as error:
            valid = False
            print(f"[ERROR] {filename}: {error}", file=sys.stderr, flush=True)
        success = valid and success

    if success:
        print("All required checkpoints are present and verified.")
        return 0
    print("One or more checkpoints are missing, invalid, or unavailable.", file=sys.stderr)
    if not args.check_only:
        print("Note: the organizational GitHub Release must be published before downloading.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
