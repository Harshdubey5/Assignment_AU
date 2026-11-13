import json
from pathlib import Path


def load_config(path: str | Path) -> dict:
    """Read the campaign settings JSON and return it as Python data."""
    with Path(path).open(encoding="utf-8") as file:
        return json.load(file)