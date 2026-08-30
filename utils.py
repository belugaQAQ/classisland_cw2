from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Callable
from zipfile import ZipFile

try:
    import requests
except ModuleNotFoundError:
    requests = None


def _requests():
    if requests is None:
        raise RuntimeError("The requests package is required to install ClassIsland.")
    return requests


DISTRIBUTION_API_URL = "https://distribution.classisland.tech/api/v1/public/distributions/web"
VERSION_FILE_NAME = ".classisland-version"

class DownloadCancelled(Exception):
    """Raised when the installer is asked to stop an in-progress download."""


def _check_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise DownloadCancelled()


def get_classisland_path() -> Path:
    """Return the per-user ClassIsland installation directory for this platform."""
    home = Path.home()
    if platform.system() == "Windows":
        documents = home / "Documents"
        return (documents if documents.exists() else home) / "ClassIsland"
    if platform.system() == "Darwin":
        return home / "Library" / "Application Support" / "ClassIsland"
    return home / ".local" / "share" / "ClassIsland"


def _valid_release(candidate: object) -> bool:
    return (
        isinstance(candidate, dict)
        and isinstance(candidate.get("latestVersion"), str)
        and isinstance(candidate.get("latestVersionId"), str)
        and bool(re.fullmatch(r"\d+(?:\.\d+)+", candidate["latestVersion"]))
    )


def _get_latest_release() -> tuple[str, str]:
    response = _requests().get(DISTRIBUTION_API_URL, timeout=30)
    response.raise_for_status()
    payload = response.json()
    content = payload.get("content", {}) if isinstance(payload, dict) else {}
    channels = content.get("channels", {}) if isinstance(content, dict) else {}
    if not isinstance(channels, dict):
        raise RuntimeError("ClassIsland release metadata has no channels.")

    configured = channels.get(content.get("defaultChannel"))
    if not _valid_release(configured):
        raise RuntimeError("ClassIsland release metadata has no valid default channel.")
    return configured["latestVersion"], configured["latestVersionId"]


def get_latest_version() -> str:
    """Return the latest version from the default ClassIsland 2 channel."""
    return _get_latest_release()[0]


def _platform_artifact() -> tuple[str, str]:
    system = platform.system()
    machine = platform.machine().casefold()
    if system == "Windows":
        if machine in {"arm64", "aarch64"}:
            architecture = "arm64"
        elif machine in {"x86", "i386", "i686"} or sys.maxsize <= 2**32:
            architecture = "x86"
        elif machine in {"x64", "amd64", "x86_64"}:
            architecture = "x64"
        else:
            raise RuntimeError(
                f"ClassIsland 2 does not support Windows architecture "
                f"{platform.machine() or 'unknown'}."
            )
        return f"ClassIsland_app_windows_{architecture}_full_folder.zip", "ClassIsland.exe"
    if system == "Linux":
        if machine in {"arm64", "aarch64"}:
            architecture = "arm64"
        elif machine in {"x64", "amd64", "x86_64"}:
            architecture = "x64"
        else:
            raise RuntimeError(
                f"ClassIsland 2 does not support Linux architecture "
                f"{platform.machine() or 'unknown'}."
            )
        return f"ClassIsland_app_linux_{architecture}_selfContained_folder.zip", "ClassIsland"
    if system == "Darwin":
        if machine in {"arm64", "aarch64"}:
            architecture = "arm64"
        elif machine in {"x64", "amd64", "x86_64"}:
            architecture = "x64"
        else:
            raise RuntimeError(
                f"ClassIsland 2 does not support macOS architecture "
                f"{platform.machine() or 'unknown'}."
            )
        return f"ClassIsland_app_macos_{architecture}_selfContained_pkg.pkg", "ClassIsland.Desktop"
    raise RuntimeError(
        f"ClassIsland 2 does not support {system or 'this platform'}. "
        "Supported platforms are Windows, Linux, and macOS."
    )


def _sub_channel_from_artifact(artifact_name: str) -> str:
    match = re.fullmatch(r"ClassIsland_app_(.+)\.(zip|pkg)", artifact_name)
    if match is None:
        raise ValueError(f"Invalid ClassIsland artifact name: {artifact_name}")
    return match.group(1)


def _get_download_info(version: str, artifact_name: str) -> str:
    latest_version, version_id = _get_latest_release()
    if latest_version != version:
        raise RuntimeError("ClassIsland release metadata changed while downloading.")
    sub_channel = _sub_channel_from_artifact(artifact_name)
    metadata_url = f"{DISTRIBUTION_API_URL}/{version_id}/{sub_channel}"
    metadata_response = _requests().get(metadata_url, timeout=30)
    metadata_response.raise_for_status()
    payload = metadata_response.json()
    metadata = payload.get("content", {}) if isinstance(payload, dict) else {}
    if not isinstance(metadata, dict) or metadata.get("version") != version:
        raise RuntimeError("ClassIsland download metadata has an unexpected version or platform.")

    try:
        url = metadata["archiveUrl"]
        if not isinstance(url, str) or not url:
            raise TypeError("archiveUrl must be a non-empty string")
    except (KeyError, TypeError) as error:
        raise RuntimeError("ClassIsland download metadata is incomplete or invalid.") from error
    return url


def download_file(
    url: str,
    destination: Path,
    progress_callback: Callable[[int], None],
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    _check_cancelled(should_cancel)
    with _requests().get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        try:
            total = int(response.headers.get("content-length", 0))
        except (TypeError, ValueError):
            total = 0
        downloaded = 0
        progress_callback(0)
        with destination.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                _check_cancelled(should_cancel)
                if chunk:
                    file.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        progress_callback(min(99, downloaded * 100 // total))




def _archive_member_path(root: Path, member_name: str) -> tuple[Path, bool]:
    normalized = member_name.replace("\\", "/")
    is_directory = normalized.endswith("/")
    relative_name = normalized.rstrip("/")
    if not relative_name or "\x00" in normalized:
        raise RuntimeError(f"ClassIsland archive contains an unsafe path: {member_name}")
    relative = PurePosixPath(relative_name)
    if (
        relative.is_absolute()
        or re.match(r"^[A-Za-z]:", relative_name)
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise RuntimeError(f"ClassIsland archive contains an unsafe path: {member_name}")
    target = (root / Path(*relative.parts)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as error:
        raise RuntimeError(f"ClassIsland archive contains an unsafe path: {member_name}") from error
    return target, is_directory


def _extract_archive(archive_path: Path, install_path: Path) -> None:
    root = install_path.resolve()
    install_path.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive_path) as archive:
        for member in archive.infolist():
            mode = (member.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                raise RuntimeError(f"ClassIsland archive contains a symbolic link: {member.filename}")
            target, is_directory = _archive_member_path(root, member.filename)
            if is_directory or member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            file_mode = (member.external_attr >> 16) & 0o777
            if file_mode:
                target.chmod(file_mode)


def _macos_application_paths() -> tuple[Path, ...]:
    return (
        Path("/Applications/ClassIsland.app"),
        Path.home() / "Applications" / "ClassIsland.app",
    )


def _find_launchable(install_path: Path, executable_name: str) -> Path:
    if platform.system() == "Darwin":
        candidates = [
            application / "Contents" / "MacOS" / executable_name
            for application in _macos_application_paths()
        ]
        candidates.append(install_path / "ClassIsland.app" / "Contents" / "MacOS" / executable_name)
    else:
        candidates = [install_path / executable_name]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"ClassIsland launcher was not found after installation: {executable_name}")


def _installed_version(install_path: Path) -> str | None:
    try:
        version = (install_path / VERSION_FILE_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return version or None


def get_installed_executable() -> Path | None:
    """Return the installed ClassIsland launcher, if present."""
    _, launcher_name = _platform_artifact()
    install_path = get_classisland_path()
    if not install_path.is_dir():
        return None
    try:
        return _find_launchable(install_path, launcher_name)
    except RuntimeError:
        return None


def _save_installed_version(install_path: Path, version: str) -> None:
    install_path.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{VERSION_FILE_NAME}.", dir=install_path, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(version)
        temporary_path.replace(install_path / VERSION_FILE_NAME)
    finally:
        temporary_path.unlink(missing_ok=True)


def _replace_folder_installation(
    archive_path: Path,
    install_path: Path,
    launcher_name: str,
    should_cancel: Callable[[], bool] | None,
) -> Path:
    parent = install_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging_path = Path(tempfile.mkdtemp(prefix=f".{install_path.name}.staging-", dir=parent))
    backup_path: Path | None = None
    try:
        _extract_archive(archive_path, staging_path)
        _check_cancelled(should_cancel)
        launcher = _find_launchable(staging_path, launcher_name)
        if platform.system() == "Linux":
            launcher.chmod(launcher.stat().st_mode | 0o755)

        if install_path.exists() or install_path.is_symlink():
            backup_path = Path(tempfile.mkdtemp(prefix=f".{install_path.name}.backup-", dir=parent))
            backup_path.rmdir()
            install_path.rename(backup_path)
        try:
            staging_path.rename(install_path)
        except OSError:
            if backup_path is not None and not install_path.exists():
                backup_path.rename(install_path)
            raise
        if backup_path is not None:
            shutil.rmtree(backup_path, ignore_errors=True)
        return _find_launchable(install_path, launcher_name)
    finally:
        shutil.rmtree(staging_path, ignore_errors=True)
        if backup_path is not None and backup_path.exists() and not install_path.exists():
            backup_path.rename(install_path)


def _install_macos_package(package_path: Path) -> None:
    opener = Path("/usr/bin/open")
    if not opener.is_file():
        raise RuntimeError("macOS package installer is unavailable.")
    try:
        subprocess.run(
            [str(opener), "-W", "-a", "Installer", str(package_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("macOS package installer is unavailable.") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"macOS ClassIsland package installation failed{suffix}") from error


def download_and_extract_classisland(
    progress_callback: Callable[[int], None],
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Install the latest ClassIsland 2 package and return its launcher."""
    _check_cancelled(should_cancel)
    version = get_latest_version()
    artifact_name, launcher_name = _platform_artifact()
    install_path = get_classisland_path()
    install_path.mkdir(parents=True, exist_ok=True)

    if _installed_version(install_path) == version:
        launcher = get_installed_executable()
        if launcher is not None:
            progress_callback(100)
            return launcher

    url = _get_download_info(version, artifact_name)
    suffix = Path(artifact_name).suffix
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{Path(artifact_name).stem}-", suffix=suffix, dir=install_path.parent
    )
    os.close(fd)
    archive_path = Path(temporary_name)
    try:
        download_file(url, archive_path, progress_callback, should_cancel)
        _check_cancelled(should_cancel)

        if artifact_name.endswith(".zip"):
            launcher = _replace_folder_installation(
                archive_path, install_path, launcher_name, should_cancel
            )
        else:
            if platform.system() != "Darwin":
                raise RuntimeError(f"ClassIsland artifact format is unsupported: {artifact_name}")
            _install_macos_package(archive_path)
            launcher = _find_launchable(install_path, launcher_name)
        _save_installed_version(install_path, version)
        progress_callback(100)
        return launcher
    finally:
        archive_path.unlink(missing_ok=True)
