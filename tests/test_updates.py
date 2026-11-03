import hashlib
import io
import json
import threading
import zipfile
from dataclasses import replace

import pytest

from infrared_desktop.updates import (APP_ID, API_ROOT, ReleaseInfo, UpdateCancelled, UpdateError,
                                     load_manifest, prepare_update, select_release, unpack_archive, version_key)


def package(extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("InfraredPreprocessor/红外预处理.exe", b"test executable; never launched")
        bundle.writestr("InfraredPreprocessor/_internal/runtime.dll", b"test runtime")
        for name, content in (extra or {}).items():
            entry = zipfile.ZipInfo("placeholder")
            # ZipInfo's Windows constructor normalizes backslashes. Set the stored
            # filename afterwards to model an archive created on another OS.
            entry.filename = name
            bundle.writestr(entry, content)
    return stream.getvalue()


def release_payload(archive=None, **overrides):
    archive = package() if archive is None else archive
    name = "infrared-preprocessor-0.3.0-windows-x64.zip"
    manifest = dict(schema=1, app_id=APP_ID, version="0.3.0", platform="windows-x64", asset=name,
                    entrypoint="红外预处理.exe", sha256=hashlib.sha256(archive).hexdigest(), size=len(archive))
    manifest.update(overrides)
    assets = ({"id": 1, "name": "update.json"}, {"id": 2, "name": name, "size": len(archive)})
    info = ReleaseInfo("0.3.0", "Test release", API_ROOT + "/releases/assets/1", assets)

    def opener(url):
        if url.endswith("/1"):
            return io.BytesIO(json.dumps(manifest).encode())
        if url.endswith("/2"):
            return io.BytesIO(archive)
        raise AssertionError("Unexpected URL")
    return info, opener


def test_versions_include_prerelease_order_and_prevent_downgrade():
    assert version_key("v0.3.0-beta.2") < version_key("0.3.0-rc.1") < version_key("0.3.0")
    assert version_key("0.10.0") > version_key("0.9.9")
    release = dict(tag_name="v0.1.0", published_at="2026-11-03", assets=[{"name": "update.json", "id": 1}])
    assert select_release([release], "0.2.0") is None
    release["tag_name"] = "v0.3.0"
    assert select_release([release], "0.2.0").version == "0.3.0"
    release["draft"] = True
    assert select_release([release], "0.2.0") is None


@pytest.mark.parametrize("change", [{"sha256": "bad"}, {"app_id": "other-app"}, {"platform": "linux"},
                                    {"version": "0.3.1"}, {"size": 0}, {"asset": "unexpected.zip"}])
def test_wrong_manifest_is_rejected(change):
    info, opener = release_payload(**change)
    with pytest.raises(UpdateError):
        load_manifest(info, opener)


def test_download_and_install_preserve_existing_data_and_current_version(tmp_path):
    user_data = tmp_path / "测量.dat"
    user_data.write_bytes(b"original measurement")
    current = tmp_path / "current" / "红外预处理.exe"
    current.parent.mkdir()
    current.write_bytes(b"old executable")
    info, opener = release_payload()
    progress = []
    installed = prepare_update(info, tmp_path / "updates", lambda *x: progress.append(x), opener=opener)
    assert installed.is_file()
    assert user_data.read_bytes() == b"original measurement"
    assert current.read_bytes() == b"old executable"
    assert progress[-1][0] == 100
    assert list((tmp_path / "updates" / "staging").iterdir()) == []


def test_checksum_failure_never_installs(tmp_path):
    info, opener = release_payload(sha256="0" * 64)
    with pytest.raises(UpdateError, match="校验失败"):
        prepare_update(info, tmp_path, opener=opener)
    assert not list((tmp_path / "versions").iterdir())
    assert not list((tmp_path / "staging").iterdir())


@pytest.mark.parametrize("name", ["../escape.txt", "InfraredPreprocessor/../../escape.txt",
                                 "C:/escape.txt", "InfraredPreprocessor/a\\b.txt",
                                 "other-app/file.txt", "InfraredPreprocessor/stream:bad"])
def test_zip_paths_cannot_escape_or_replace_other_app(tmp_path, name):
    archive = tmp_path / "package.zip"
    archive.write_bytes(package({name: b"bad"}))
    with pytest.raises(UpdateError, match="路径"):
        unpack_archive(archive, tmp_path / "extract")
    assert not (tmp_path / "escape.txt").exists()


def test_cancelled_download_keeps_current_version(tmp_path):
    info, opener = release_payload()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(UpdateCancelled):
        prepare_update(info, tmp_path, cancel=cancel, opener=opener)
    assert not list((tmp_path / "versions").iterdir())


def test_github_asset_digest_must_agree_with_manifest():
    info, opener = release_payload()
    assets = (info.assets[0], {**info.assets[1], "digest": "sha256:" + "0" * 64})
    with pytest.raises(UpdateError, match="摘要"):
        load_manifest(replace(info, assets=assets), opener)


def test_previously_prepared_same_version_can_be_reused(tmp_path):
    info, opener = release_payload()
    first = prepare_update(info, tmp_path, opener=opener)
    second = prepare_update(info, tmp_path, opener=opener)
    assert first == second


def test_existing_unrecognized_version_directory_is_never_overwritten(tmp_path):
    info, opener = release_payload()
    manifest, _ = load_manifest(info, opener)
    final = tmp_path / "versions" / f"0.3.0-{manifest['sha256'][:12]}"
    final.mkdir(parents=True)
    user_file = final / "keep.txt"
    user_file.write_text("keep", encoding="utf-8")
    with pytest.raises(UpdateError, match="没有覆盖"):
        prepare_update(info, tmp_path, opener=opener)
    assert user_file.read_text(encoding="utf-8") == "keep"
