"""GitHub release updates. Measurement files are never read or transmitted here."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import __version__

REPOSITORY = "Suxifang/infrared-preprocessor"
RELEASE_PAGE = f"https://github.com/{REPOSITORY}/releases"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
APP_ID = "infrared-preprocessor"
MAX_DOWNLOAD = 512 * 1024**2
MAX_EXPANDED = 1024**3


class UpdateError(ValueError):
    pass


class UpdateCancelled(UpdateError):
    pass


def version_key(text):
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc)\.(\d+))?", str(text))
    if not match:
        raise UpdateError("无法识别发布版本号。")
    return (*map(int, match.group(1, 2, 3)), {None: 3, "alpha": 0, "beta": 1, "rc": 2}[match[4]], int(match[5] or 0))


def app_data_root():
    return Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "InfraredPreprocessor"


def github_json(url, limit=1024**2):
    if not url.startswith(API_ROOT + "/"):
        raise UpdateError("更新地址与当前软件仓库不一致。")
    request = urllib.request.Request(url, headers={
        "User-Agent": f"InfraredPreprocessor/{__version__}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Cache-Control": "no-cache",
    })
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise UpdateError("更新信息超过允许大小。")
        return json.loads(data)
    except urllib.error.HTTPError as error:
        message = {403: "GitHub暂时限制请求次数，请稍后重试。", 404: "发布页尚不可访问或还没有新版。"}.get(error.code)
        raise UpdateError(message or f"GitHub返回错误 {error.code}，请稍后重试。") from error
    except (OSError, ValueError) as error:
        if isinstance(error, UpdateError):
            raise
        raise UpdateError("暂时无法连接GitHub，请检查网络后重试。") from error


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    notes: str
    manifest_url: str
    assets: tuple


def select_release(releases, current=__version__):
    candidates = []
    for release in releases:
        if release.get("draft") or not release.get("published_at"):
            continue
        try:
            key = version_key(release.get("tag_name", ""))
        except UpdateError:
            continue
        assets = release.get("assets", [])
        manifest = next((a for a in assets if a.get("name") == "update.json"), None)
        if manifest and key > version_key(current):
            candidates.append((key, release, manifest))
    if not candidates:
        return None
    _, release, manifest = max(candidates, key=lambda item: item[0])
    return ReleaseInfo(release["tag_name"].removeprefix("v"), str(release.get("body") or "本次未附更新说明。"),
                       asset_api_url(manifest), tuple(release["assets"]))


def check_release(current=__version__):
    releases = github_json(API_ROOT + "/releases?per_page=20")
    if not isinstance(releases, list):
        raise UpdateError("发布信息格式不正确。")
    if not releases:
        raise UpdateError("仓库已连接，首个安装包尚未发布。")
    return select_release(releases, current)


def asset_api_url(asset):
    identifier = asset.get("id")
    if not isinstance(identifier, int) or identifier <= 0:
        raise UpdateError("发布附件缺少有效标识。")
    return f"{API_ROOT}/releases/assets/{identifier}"


def open_asset(url):
    if not re.fullmatch(re.escape(API_ROOT) + r"/releases/assets/[1-9]\d*", url):
        raise UpdateError("下载地址与当前软件仓库不一致。")
    # No credentials are embedded or sent. GitHub redirects public assets to its CDN.
    request = urllib.request.Request(url, headers={
        "User-Agent": f"InfraredPreprocessor/{__version__}", "Accept": "application/octet-stream",
        "Cache-Control": "no-cache", "X-GitHub-Api-Version": "2022-11-28",
    })
    return urllib.request.urlopen(request, timeout=25)


def load_manifest(info, opener=open_asset):
    try:
        with opener(info.manifest_url) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise UpdateError("更新清单过大。")
        manifest = json.loads(raw)
        required_name = f"infrared-preprocessor-{info.version}-windows-x64.zip"
        if (manifest.get("schema") != 1 or manifest.get("app_id") != APP_ID
                or manifest.get("version") != info.version or manifest.get("platform") != "windows-x64"
                or manifest.get("asset") != required_name or manifest.get("entrypoint") != "红外预处理.exe"):
            raise UpdateError("更新清单与软件、版本或Windows平台不一致。")
        if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("sha256", ""))):
            raise UpdateError("更新清单缺少SHA256校验值。")
        size = manifest.get("size")
        if type(size) is not int or not 0 < size <= MAX_DOWNLOAD:
            raise UpdateError("更新包大小不在允许范围内。")
        matches = [a for a in info.assets if a.get("name") == required_name]
        if len(matches) != 1 or matches[0].get("size") != size:
            raise UpdateError("发布附件与更新清单不一致。")
        digest = matches[0].get("digest")
        if digest and digest != "sha256:" + manifest["sha256"]:
            raise UpdateError("GitHub附件摘要与更新清单不一致。")
        return manifest, asset_api_url(matches[0])
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, UpdateError):
            raise
        raise UpdateError("无法读取更新清单，请稍后重试。") from error


def unpack_archive(archive, destination, cancel=None):
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as bundle:
        records = bundle.infolist()
        if len(records) > 12000 or sum(i.file_size for i in records) > MAX_EXPANDED:
            raise UpdateError("更新包展开大小超过允许范围。")
        seen = set()
        for item in records:
            # Windows ZipInfo normalizes backslashes when reading. Inspect the
            # original archive path before that normalization as well.
            name = item.orig_filename
            path = PurePosixPath(name)
            if ("\\" in name or path.is_absolute() or ".." in path.parts or ":" in name
                    or not path.parts or path.parts[0] != "InfraredPreprocessor"
                    or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1
                    or any(p.endswith((" ", ".")) for p in path.parts)):
                raise UpdateError("更新包含不安全的文件路径。")
            key = str(path).casefold()
            if key in seen:
                raise UpdateError("更新包包含重复文件。")
            seen.add(key)
            target = root.joinpath(*path.parts).resolve()
            if not target.is_relative_to(root):
                raise UpdateError("更新包路径超出目标目录。")
            if cancel and cancel.is_set():
                raise UpdateCancelled("更新已取消，当前软件保持可用。")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
    executable = root / "InfraredPreprocessor" / "红外预处理.exe"
    if not executable.is_file() or not (executable.parent / "_internal").is_dir():
        raise UpdateError("更新包缺少程序或运行库。")
    return executable


def prepare_update(info, root=None, progress=None, cancel=None, opener=open_asset):
    root = Path(root or app_data_root()).resolve()
    manifest, asset_url = load_manifest(info, opener)
    (root / "staging").mkdir(parents=True, exist_ok=True)
    (root / "versions").mkdir(exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="update-", dir=root / "staging")).resolve()
    try:
        archive = staging / "download.zip"
        digest, received = hashlib.sha256(), 0
        if progress:
            progress(0, "正在下载新版…")
        with opener(asset_url) as response, archive.open("xb") as output:
            while True:
                if cancel and cancel.is_set():
                    raise UpdateCancelled("更新已取消，当前软件保持可用。")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > manifest["size"]:
                    raise UpdateError("下载内容超出更新清单大小。")
                output.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(int(received * 90 / manifest["size"]), f"正在下载 {received / 1024**2:.1f} / {manifest['size'] / 1024**2:.1f} MB")
        if received != manifest["size"] or digest.hexdigest() != manifest["sha256"]:
            raise UpdateError("下载包校验失败，当前版本保持不变；请重新下载。")
        if progress:
            progress(94, "校验通过，正在准备新版…")
        executable = unpack_archive(archive, staging / "unpacked", cancel)
        final = root / "versions" / f"{info.version}-{manifest['sha256'][:12]}"
        if final.exists():
            marker = final / ".update.json"
            if not marker.exists() or json.loads(marker.read_text(encoding="utf-8")) != manifest:
                raise UpdateError("版本目录已存在其他内容，程序没有覆盖它。")
            if not (final / manifest["entrypoint"]).is_file():
                raise UpdateError("已下载版本的程序缺失，请保留当前版本。")
        else:
            (executable.parent / ".update.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            os.replace(executable.parent, final)
        if progress:
            progress(100, "新版已准备好，当前项目会先保存。")
        return final / manifest["entrypoint"]
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        if isinstance(error, UpdateError):
            raise
        raise UpdateError(f"更新未完成：{error}") from error
    finally:
        if staging.parent == (root / "staging").resolve() and staging.name.startswith("update-"):
            shutil.rmtree(staging, ignore_errors=True)
