"""Configuration loading and validation."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
TOPICS_DIR = CONFIG_DIR / "topics"

DEFAULT_SETTINGS: dict[str, Any] = {
    "proxy": {
        "url": "",
    },
    "paths": {
        "reports_dir": "~/reports/intel",
        "state_dir": "~/.local/state/intel-agent",
        "cache_dir": "~/.cache/intel-agent",
    },
    "http": {
        "timeout_seconds": 25,
        "retries": 2,
        "user_agent": "intel-agent/0.1",
    },
    "browser": {
        "mode": "cdp",
        "cdp_port": 9222,
        "chrome_bin": "/usr/bin/google-chrome",
        "chrome_profile": "~/.local/state/intel-agent/chrome-profile",
        "headed": False,
        "x_profile": "~/.local/state/intel-agent/x-profile",
        "yandex_profile": "~/.local/state/intel-agent/yandex-profile",
    },
    "x": {
        "min_interval_seconds": 45,
        "max_scrolls": 2,
    },
    "yandex": {
        "min_interval_seconds": 40,
        "lr": "213",
    },
    "delivery": {
        "local": True,
        "primary": "local",
        "wechat": {
            "enabled": False,
            "format": "text",
            "script": "",
            "max_chars": 1200,
        },
        "email": {
            "enabled": False,
            "mode": "smtp",
            "host": "",
            "port": 587,
            "user": "",
            "password_env": "SMTP_PASSWORD",
            "tls": True,
            "to": "",
            "subject_prefix": "[intel]",
            "cli": "",
        },
        "feishu": {
            "enabled": False,
            "as": "bot",
            "open_id": "",
            "recall_previous": True,
        },
    },
    "llm": {
        "provider": "auto",
        "claude_bin": "claude",
        "model": "",
        "max_budget_usd": 0.30,
        "max_tokens": 4096,
        "timeout_seconds": 300,
        "preflight_host": "127.0.0.1",
        "preflight_port": 15721,
    },
    "sources": {
        "learning": {
            "enabled": True,
            "auto_apply": False,
            "runs": 10,
        },
    },
}


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(os.path.expanduser(value))
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def _deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config must be a YAML object: {path}")
    return data


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """Load project settings.yaml, then deep-merge an optional override file.

    A `--settings` override must not discard the browser/x/yandex sections, so
    the project file is always the base.
    """
    base_path = CONFIG_DIR / "settings.yaml"
    base = _deep_merge(DEFAULT_SETTINGS, load_yaml(base_path)) if base_path.exists() else copy.deepcopy(DEFAULT_SETTINGS)
    if path:
        override = Path(path)
        if override.resolve() != base_path.resolve():
            base = _deep_merge(base, load_yaml(override))
    return _expand(base)


def _merge_topic_overlay(topic: dict, overlay: dict) -> dict:
    """Merge user/learned source additions and disables without touching the base YAML."""
    add = overlay.get("add") or {}
    queries = topic.setdefault("queries", {})
    for key in ("rss", "telegram_channels"):
        values = queries.setdefault(key, [])
        for value in add.get(key) or []:
            if value not in values:
                values.append(value)
    for engine, values in (add.get("queries") or {}).items():
        if isinstance(values, dict):
            target = queries.setdefault(engine, {})
            for lang, items in values.items():
                bucket = target.setdefault(lang, [])
                for item in items or []:
                    if item not in bucket:
                        bucket.append(item)
        elif isinstance(values, list):
            target = queries.setdefault(engine, [])
            for item in values:
                if item not in target:
                    target.append(item)
    disable = (overlay.get("disable") or {}).get("sources") or []
    topic["disabled_sources"] = sorted(set(topic.get("disabled_sources", [])) | {str(x) for x in disable})
    return topic


def load_topic(topic_id: str, path: Path | None = None) -> dict[str, Any]:
    path = path or (TOPICS_DIR / f"{topic_id}.yaml")
    topic = load_yaml(path)
    overlay_path = path.with_name(f"{path.stem}.sources.yaml")
    if overlay_path.exists():
        topic = _merge_topic_overlay(topic, load_yaml(overlay_path))
    topic = _expand(topic)
    required = ("id", "name", "facets", "queries")
    missing = [key for key in required if not topic.get(key)]
    if missing:
        raise ValueError(f"topic {topic_id} missing required keys: {missing}")
    if str(topic["id"]) != topic_id:
        raise ValueError(f"topic id mismatch: file={topic_id} id={topic['id']}")
    unknown = set(topic.get("facets", {})) - {"contracts", "industry", "technology", "players"}
    if unknown:
        raise ValueError(f"topic {topic_id} has unknown facets: {sorted(unknown)}")
    return topic


def load_glossary(path: Path | None = None) -> dict[str, str]:
    path = path or (CONFIG_DIR / "glossary.ru-zh.yaml")
    if not path.exists():
        return {}
    data = load_yaml(path)
    return {str(k): str(v) for k, v in data.items()}


def ensure_dirs(settings: dict[str, Any]) -> None:
    for key in ("reports_dir", "state_dir", "cache_dir"):
        Path(settings["paths"][key]).mkdir(parents=True, exist_ok=True)
