"""Configuration loader for Gold Silver Live Trading System."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

CONFIG_PATH = Path(__file__).parent / "settings.json"


class Config:
    """Singleton configuration manager."""
    _instance: Optional["Config"] = None
    _config: dict[str, Any] = {}

    def __new__(cls) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def load(cls, path: Optional[Path] = None) -> dict[str, Any]:
        """Load configuration from JSON file with environment variable resolution."""
        if path is None:
            path = CONFIG_PATH
        
        with open(path) as f:
            config = json.load(f)
        
        cls._config = cls._resolve_env_vars(config)
        return cls._config

    @classmethod
    def _resolve_env_vars(cls, obj: Any) -> Any:
        """Recursively resolve ${ENV_VAR} placeholders."""
        if isinstance(obj, dict):
            return {k: cls._resolve_env_vars(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [cls._resolve_env_vars(item) for item in obj]
        if isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
            env_key = obj[2:-1]
            return os.environ.get(env_key, "")
        return obj

    @classmethod
    def get(cls, key: str = None, default: Any = None) -> Any:
        """Get configuration value by dot-separated key path."""
        if not cls._config:
            cls.load()
        
        if key is None:
            return cls._config
        
        keys = key.split(".")
        val = cls._config
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
        return val if val is not None else default

    @classmethod
    def instrument(cls, name: str) -> dict[str, Any]:
        """Get instrument configuration."""
        return cls.get(f"instruments.{name}") or {}

    @classmethod
    def strategy(cls, name: str) -> dict[str, Any]:
        """Get strategy configuration."""
        return cls.get(f"strategies.{name}") or {}
