"""Self-update from GitHub Releases, accepting only releases signed with the owner's key.

Every release carries three assets:
  AionDex-Setup-<version>.exe   the installer
  latest.json                   {"version", "installer", "sha256", "size", "notes"}
  latest.json.sig               base64 Ed25519 signature of latest.json

The app trusts nothing from GitHub by itself: latest.json must verify against PUBLIC_KEY (whose private
half only the owner holds), its version must be newer than the running one, and the downloaded installer
must match the signed size and SHA-256 before it is run.
"""
import base64
import hashlib
import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import APP_NAME, REPO, __version__

PUBLIC_KEY = "M4zmw9/aR3jCiLwUs09tFj4RanARf8VFt0qsURWnRuU="
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
MANIFEST, SIGNATURE = "latest.json", "latest.json.sig"
UA = f"{APP_NAME}/{__version__} (+https://github.com/{REPO})"


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    installer_name: str
    installer_url: str
    sha256: str
    size: int
    notes: str = ""


def parse_version(text):
    parts = str(text).strip().lstrip("vV").split(".")
    try:
        return tuple(int(p) for p in parts[:3]) + (0,) * (3 - len(parts[:3]))
    except ValueError:
        raise UpdateError(f"versión inválida: {text!r}") from None


def is_newer(release, current=__version__):
    return parse_version(release.version) > parse_version(current)


def _get(url, accept=None, timeout=20):
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
        return resp.read()


def verify_manifest(data, signature_b64, public_key_b64=PUBLIC_KEY):
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    try:
        key.verify(base64.b64decode(signature_b64), data)
    except (InvalidSignature, ValueError):
        raise UpdateError("la firma de la actualización no es válida") from None


def check_latest():
    """Return the latest signed Release, or None if the repo has no release yet."""
    try:
        info = json.loads(_get(API_LATEST, accept="application/vnd.github+json"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise UpdateError(f"GitHub respondió {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"sin conexión con GitHub ({exc})") from None
    assets = {a["name"]: a["browser_download_url"] for a in info.get("assets", [])}
    if MANIFEST not in assets or SIGNATURE not in assets:
        raise UpdateError("la release no trae manifiesto firmado")
    manifest = _get(assets[MANIFEST])
    verify_manifest(manifest, _get(assets[SIGNATURE]).decode().strip())
    m = json.loads(manifest)
    if parse_version(m["version"]) != parse_version(info.get("tag_name", "")):
        raise UpdateError("el manifiesto no corresponde a la release")
    if m["installer"] not in assets:
        raise UpdateError("falta el instalador en la release")
    return Release(m["version"], m["installer"], assets[m["installer"]], m["sha256"].lower(), int(m["size"]),
                   m.get("notes", ""))


def download(release, folder, progress=None):
    """Download (or reuse a cached copy of) the installer and check it against the signed manifest."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / release.installer_name
    if target.exists() and _sha256(target) == release.sha256:
        return target
    partial = target.with_suffix(".part")
    digest = hashlib.sha256()
    done = 0
    req = urllib.request.Request(release.installer_url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(partial, "wb") as out:
            while chunk := resp.read(256 * 1024):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress and release.size:
                    progress(min(100, done * 100 // release.size))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"la descarga falló ({exc})") from None
    if done != release.size or digest.hexdigest() != release.sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError("el instalador descargado no coincide con el firmado")
    partial.replace(target)
    return target


def launch_installer(path):
    """Run the installer silently; it closes this app, updates it and starts it again."""
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                      "/UPDATE"], creationflags=flags, close_fds=True)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
