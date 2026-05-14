# src/orchestra/core/claude_config.py
"""Claude Code configuration: profiles, provider/model management."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class ClaudeProfile:
    name: str
    provider: str = "anthropic"
    model: str = "sonnet"
    max_turns: int = 50
    permission_mode: str = "bypassPermissions"
    api_base_url: Optional[str] = None
    env: dict[str, str] = field(default_factory=dict)
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    role_models: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict = {
            "provider": self.provider,
            "model": self.model,
            "max_turns": self.max_turns,
            "permission_mode": self.permission_mode,
        }
        if self.api_base_url:
            d["api_base_url"] = self.api_base_url
        if self.env:
            d["env"] = dict(self.env)
        if self.mcp_servers:
            d["mcp_servers"] = dict(self.mcp_servers)
        if self.role_models:
            d["role_models"] = dict(self.role_models)
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict) -> ClaudeProfile:
        return cls(
            name=name,
            provider=d.get("provider", "anthropic"),
            model=d.get("model", "sonnet"),
            max_turns=d.get("max_turns", 50),
            permission_mode=d.get("permission_mode", "bypassPermissions"),
            api_base_url=d.get("api_base_url"),
            env=dict(d.get("env", {})),
            mcp_servers=dict(d.get("mcp_servers", {})),
            role_models=dict(d.get("role_models", {})),
        )


@dataclass
class ClaudeConfig:
    command: str = "claude"
    active_profile: str = "default"
    profiles: dict[str, ClaudeProfile] = field(default_factory=dict)


class ClaudeConfigManager:
    """Load, save, and manage Claude Code configuration profiles."""

    def __init__(self, config_path: Path):
        self._config_path = config_path
        self._raw: dict = {}
        self.config = self._load()

    def _load(self) -> ClaudeConfig:
        if self._config_path.exists():
            self._raw = yaml.safe_load(self._config_path.read_text()) or {}
        else:
            self._raw = {}

        claude = self._raw.get("claude", {})
        command = claude.get("command", "claude")

        if "profiles" in claude:
            profiles = {
                name: ClaudeProfile.from_dict(name, data)
                for name, data in claude["profiles"].items()
            }
            active = claude.get("active_profile", "default")
            if active not in profiles and profiles:
                active = next(iter(profiles))
            return ClaudeConfig(
                command=command,
                active_profile=active,
                profiles=profiles,
            )

        # Legacy format: no profiles section — migrate
        default_profile = ClaudeProfile(
            name="default",
            model=claude.get("model", "sonnet"),
            max_turns=claude.get("max_turns", 50),
        )
        return ClaudeConfig(
            command=command,
            active_profile="default",
            profiles={"default": default_profile},
        )

    def active_profile(self) -> ClaudeProfile:
        return self.config.profiles[self.config.active_profile]

    def create_profile(self, name: str, profile: ClaudeProfile) -> None:
        if name in self.config.profiles:
            raise ValueError(f"Profile '{name}' already exists")
        self.config.profiles[name] = profile
        self.save()

    def update_profile(self, name: str, updates: dict) -> None:
        if name not in self.config.profiles:
            raise KeyError(f"Profile '{name}' not found")
        p = self.config.profiles[name]
        for k, v in updates.items():
            if hasattr(p, k):
                setattr(p, k, v)
        self.save()

    def delete_profile(self, name: str) -> None:
        if name == self.config.active_profile:
            raise ValueError("Cannot delete active profile")
        self.config.profiles.pop(name, None)
        self.save()

    def switch_profile(self, name: str) -> None:
        if name not in self.config.profiles:
            raise KeyError(f"Profile '{name}' not found")
        self.config.active_profile = name
        self.save()

    def save(self) -> None:
        claude_section = {
            "command": self.config.command,
            "active_profile": self.config.active_profile,
            "profiles": {
                name: p.to_dict()
                for name, p in self.config.profiles.items()
            },
        }
        self._raw["claude"] = claude_section
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(yaml.dump(self._raw, default_flow_style=False, sort_keys=False))
