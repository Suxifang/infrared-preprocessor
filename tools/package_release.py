"""Create the exact release ZIP and integrity manifest consumed by the desktop updater."""
from __future__ import annotations

import hashlib
import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from infrared_desktop import __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "发布" / "红外预处理")
    args = parser.parse_args()
    source = args.source.resolve()
    if not (source / "红外预处理.exe").is_file() or not (source / "_internal").is_dir():
        raise SystemExit("Build the desktop application first")
    output = ROOT / "发布" / "更新包"
    output.mkdir(exist_ok=True)
    archive = output / f"infrared-preprocessor-{__version__}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.write(path, str(Path("InfraredPreprocessor") / path.relative_to(source)))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = dict(schema=1, app_id="infrared-preprocessor", version=__version__, platform="windows-x64",
                    asset=archive.name, entrypoint="红外预处理.exe", sha256=digest, size=archive.stat().st_size)
    (output / "update.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
