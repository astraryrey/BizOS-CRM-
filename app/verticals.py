"""Loader for vertical configs. A vertical is a YAML file, not code."""
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"


class VerticalNotFound(Exception):
    def __init__(self, name: str, available: list[str]):
        self.name = name
        self.available = available
        super().__init__(f"Vertical '{name}' not found. Available: {available}")


@lru_cache
def load_vertical(name: str) -> dict:
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise VerticalNotFound(name, [p.stem for p in sorted(CONFIG_DIR.glob("*.yaml"))])
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_verticals() -> list[dict]:
    out = []
    for p in sorted(CONFIG_DIR.glob("*.yaml")):
        cfg = load_vertical(p.stem)
        out.append({"key": p.stem, "title": cfg.get("title", p.stem)})
    return out


def render_template(cfg: dict, key: str, ctx: dict) -> str:
    tpl: str = (cfg.get("templates") or {}).get(key, "")
    for k, v in ctx.items():
        tpl = tpl.replace("{" + k + "}", str(v))
    return tpl
