"""Validate repository/app metadata with the pinned Supervisor schemas.

Run with PYTHONPATH pointing at the exact Supervisor source revision pinned in
.github/workflows/ci.yml. This is schema validation, not an HAOS integration
test.
"""

from pathlib import Path

import yaml
from supervisor.apps.validate import SCHEMA_APP_CONFIG, SCHEMA_APP_TRANSLATIONS
from supervisor.store.validate import SCHEMA_REPOSITORY_CONFIG

ROOT = Path(__file__).resolve().parents[1]


def read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a YAML mapping")
    return value


def main() -> None:
    repository = read_yaml(ROOT / "repository.yaml")
    SCHEMA_REPOSITORY_CONFIG(repository)

    app_dir = ROOT / "wyoming_openai_gateway"
    app_config = read_yaml(app_dir / "config.yaml")
    SCHEMA_APP_CONFIG(app_config)

    translations = read_yaml(app_dir / "translations" / "en.yaml")
    SCHEMA_APP_TRANSLATIONS(translations)
    print("Pinned Home Assistant Supervisor repository/app/translation schemas passed.")


if __name__ == "__main__":
    main()
