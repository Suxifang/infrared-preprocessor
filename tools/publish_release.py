"""Publish validated release assets; credentials stay in memory, outside the repository."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from infrared_desktop import __version__
from infrared_desktop.updates import API_ROOT, REPOSITORY


def token():
    if os.environ.get("GH_TOKEN"):
        return os.environ["GH_TOKEN"]
    result = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                            text=True, capture_output=True, timeout=20,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"})
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if not fields.get("password"):
        raise RuntimeError("Sign in to GitHub with Git Credential Manager, or set GH_TOKEN")
    return fields["password"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path, default=ROOT / "发布" / "更新包")
    args = parser.parse_args()
    manifest = json.loads((args.assets / "update.json").read_text(encoding="utf-8"))
    if manifest["version"] != __version__:
        raise RuntimeError("Release manifest version differs from source version")
    archive = args.assets / manifest["asset"]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest["sha256"]:
        raise RuntimeError("Release package checksum mismatch")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    headers = {"Authorization": "Bearer " + token(), "Accept": "application/vnd.github+json",
               "User-Agent": "InfraredPreprocessor-publisher", "X-GitHub-Api-Version": "2022-11-28"}

    def api(url, method="GET", data=None):
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.load(response)

    tag = "v" + __version__
    try:
        release = api(API_ROOT + "/releases/tags/" + tag)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        release = api(API_ROOT + "/releases", "POST", {
            "tag_name": tag, "target_commitish": commit, "name": f"红外预处理 {tag} 测试版",
            "body": (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), "draft": True, "prerelease": True})
    if not release["draft"]:
        raise RuntimeError("Published versions are immutable; increase the version number before publishing again")
    existing = {asset["name"]: asset for asset in api(API_ROOT + f"/releases/{release['id']}/assets")}
    for file in [archive, args.assets / (archive.name + ".sha256"), args.assets / "update.json"]:
        if file.name in existing:
            saved = existing[file.name]
            digest = "sha256:" + hashlib.sha256(file.read_bytes()).hexdigest()
            if saved["size"] != file.stat().st_size or saved.get("digest") != digest:
                raise RuntimeError(f"An existing draft asset differs: {file.name}")
            print("Already uploaded:", file.name, flush=True)
            continue
        upload = release["upload_url"].split("{")[0] + "?name=" + urllib.parse.quote(file.name)
        if urllib.parse.urlsplit(upload).hostname != "uploads.github.com":
            raise RuntimeError("Unexpected release upload host")
        upload_headers = {**headers, "Content-Type": "application/zip" if file.suffix == ".zip" else "application/octet-stream",
                          "Content-Length": str(file.stat().st_size)}
        print(f"Uploading {file.name} ({file.stat().st_size / 1024**2:.1f} MB)", flush=True)
        with file.open("rb") as body:
            request = urllib.request.Request(upload, data=body, headers=upload_headers, method="POST")
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.load(response)
        if result["size"] != file.stat().st_size:
            raise RuntimeError("Uploaded release asset size mismatch")
        print("Uploaded:", file.name, flush=True)
    release = api(API_ROOT + f"/releases/{release['id']}", "PATCH", {"draft": False})
    print(json.dumps({"url": release["html_url"], "tag": tag, "prerelease": release["prerelease"],
                      "source_commit": commit}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
