"""Write latest.json for an installer and sign it with the owner's Ed25519 key.

    python tools/sign_release.py dist/installer/AionDex-Setup-1.0.0.exe [--notes "..."]

The private key comes from the RELEASE_SIGNING_KEY environment variable (PEM text, set as a GitHub
Actions secret) or from %USERPROFILE%\\.aiondex\\release_signing_key.pem. The app only installs updates
whose latest.json verifies against the matching public key (pet_overlay/updater.py PUBLIC_KEY).
"""
import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pet_overlay import __version__  # noqa: E402
from pet_overlay.updater import PUBLIC_KEY, verify_manifest  # noqa: E402

KEY_FILE = Path.home() / ".aiondex" / "release_signing_key.pem"


def load_key():
    pem = os.environ.get("RELEASE_SIGNING_KEY")
    data = pem.encode() if pem else KEY_FILE.read_bytes()
    return serialization.load_pem_private_key(data, password=None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("installer", type=Path)
    parser.add_argument("--notes", default="")
    parser.add_argument("--out", type=Path, default=None, help="folder for latest.json(.sig)")
    args = parser.parse_args()

    blob = args.installer.read_bytes()
    manifest = json.dumps({
        "version": __version__,
        "installer": args.installer.name,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "size": len(blob),
        "notes": args.notes,
    }, indent=2).encode()
    signature = base64.b64encode(load_key().sign(manifest)).decode()
    verify_manifest(manifest, signature, PUBLIC_KEY)     # refuse to publish with a key the app won't trust

    out = args.out or args.installer.parent
    (out / "latest.json").write_bytes(manifest)
    (out / "latest.json.sig").write_text(signature)
    print(f"latest.json firmado para {args.installer.name} (v{__version__})")


if __name__ == "__main__":
    main()
