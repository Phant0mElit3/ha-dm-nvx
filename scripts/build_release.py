"""Build a version-checked manual-install ZIP using only integration files."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def build_release(root: Path = ROOT) -> Path:
    integration = root / "custom_components" / "crestron_nvx"
    version = (root / "VERSION").read_text().strip()
    manifest = json.loads((integration / "manifest.json").read_text())
    if manifest["version"] != version:
        raise ValueError("VERSION and manifest.json disagree")
    destination = root / "dist" / f"crestron_nvx-{version}.zip"
    destination.parent.mkdir(exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path in sorted(integration.rglob("*")):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix in {".py", ".json", ".png", ".yaml"}
            ):
                archive.write(path, path.relative_to(root))
        for name in ("LICENSE", "NOTICE"):
            archive.write(root / name, name)
    return destination


if __name__ == "__main__":
    print(build_release())
