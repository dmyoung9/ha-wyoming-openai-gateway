"""Validate the Home Assistant app metadata contract used by this repository.

This checks required fields and cross-file invariants against the current HA
developer documentation. It is not a substitute for Supervisor validation or
an installation test on Home Assistant OS.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "wyoming_openai_gateway"


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a YAML mapping")
    return value


def main() -> None:
    repository = load_yaml(ROOT / "repository.yaml")
    for key in ("name", "url", "maintainer"):
        if not isinstance(repository.get(key), str) or not repository[key].strip():
            raise ValueError(f"repository.yaml requires non-empty string {key!r}")

    config = load_yaml(APP_DIR / "config.yaml")
    required = ("name", "version", "slug", "description", "arch")
    for key in required:
        if key not in config:
            raise ValueError(f"app config.yaml is missing required field {key!r}")
    if config["arch"] != ["amd64", "aarch64"]:
        raise ValueError("arch must list the two tested HA architectures: amd64, aarch64")
    if config.get("image"):
        raise ValueError("image must remain unset until an explicitly approved image publication")
    if config.get("init") is not False:
        raise ValueError("init must be false because the Python launcher is the PID 1 supervisor")
    if config.get("hassio_api") is not True or config.get("hassio_role") != "default":
        raise ValueError(
            "Supervisor access must use hassio_api and the least-privileged default role"
        )
    if config.get("ports", {}).get("8555/tcp") != 8555:
        raise ValueError("port 8555/tcp must be published on host port 8555")
    if config.get("options", {}).keys() != config.get("schema", {}).keys():
        raise ValueError("every default option must have a schema entry and vice versa")

    translation = load_yaml(APP_DIR / "translations" / "en.yaml")
    translated = set(translation.get("configuration", {}))
    if translated != set(config["schema"]):
        raise ValueError(
            "English configuration translations must cover each schema option exactly once"
        )
    translated_ports = set(translation.get("network", {}))
    expected_ports = {key.upper() for key in config.get("ports", {})}
    if translated_ports != expected_ports:
        raise ValueError(
            "English network translations must cover each configured port exactly once"
        )

    print("Home Assistant metadata checks passed (not a Supervisor/HAOS integration test).")


if __name__ == "__main__":
    main()
