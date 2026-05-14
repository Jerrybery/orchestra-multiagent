# Claude Code Configuration Manager — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a profile-based Claude Code configuration system to Orchestra, with encrypted secret storage (Vault), backend API, AgentSpawner integration, and a Web Dashboard configuration tab.

**Architecture:** New `claude_config.py` manages named profiles (provider, model, env, MCP servers, role_models) loaded from/saved to `orchestra.yaml`. A `Vault` class encrypts secrets with Fernet. `AgentSpawner.spawn()` reads the active profile to build CLI flags and subprocess env. Web API exposes CRUD endpoints; the frontend adds a Configuration tab with profile cards and edit forms.

**Tech Stack:** Python 3.11+, FastAPI, `cryptography` (Fernet), vanilla JS/HTML/CSS, aiosqlite (existing), PyYAML (existing)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/orchestra/core/claude_config.py` | Create | `ClaudeProfile`, `ClaudeConfig`, `ClaudeConfigManager` — load/save/migrate YAML, profile CRUD |
| `src/orchestra/core/vault.py` | Create | `Vault` — Fernet encrypt/decrypt, store/delete/list/resolve |
| `src/orchestra/core/agent_spawner.py` | Modify | Accept `ClaudeConfigManager` + `Vault`, use active profile in `spawn()` |
| `src/orchestra/core/orchestrator.py` | Modify | Instantiate `ClaudeConfigManager` + `Vault`, pass to spawner |
| `src/orchestra/web/api.py` | Modify | Add `/api/claude-config/*` endpoints |
| `src/orchestra/web/static/index.html` | Modify | Add Configuration side-tab button |
| `src/orchestra/web/static/app.js` | Modify | Add Configuration tab UI (profile cards, editor, vault panel) |
| `src/orchestra/web/static/style.css` | Modify | Styles for config tab |
| `src/orchestra/main.py` | Modify | Load extended claude config, init vault, pass to OrchestraConfig |
| `pyproject.toml` | Modify | Add `cryptography` dependency |
| `tests/test_claude_config.py` | Create | Unit tests for ClaudeConfigManager |
| `tests/test_vault.py` | Create | Unit tests for Vault |
| `tests/test_claude_config_api.py` | Create | Integration tests for API endpoints |

---

### Task 1: Vault — Encrypted Secret Storage

**Files:**
- Create: `src/orchestra/core/vault.py`
- Test: `tests/test_vault.py`

- [ ] **Step 1: Write failing tests for Vault**

```python
# tests/test_vault.py
"""Tests for the encrypted Vault."""

import pytest
from pathlib import Path
from orchestra.core.vault import Vault


@pytest.fixture
def vault(tmp_path):
    vault_path = tmp_path / "vault.enc"
    key_path = tmp_path / "vault.key"
    return Vault(vault_path, key_path)


class TestVault:
    def test_store_and_list(self, vault):
        vault.store("my-key", "my-secret")
        assert "my-key" in vault.list_keys()

    def test_resolve_vault_ref(self, vault):
        vault.store("api-key", "sk-ant-123")
        assert vault.resolve("vault:api-key") == "sk-ant-123"

    def test_resolve_plain_passthrough(self, vault):
        assert vault.resolve("plain-value") == "plain-value"

    def test_resolve_missing_key_raises(self, vault):
        with pytest.raises(KeyError):
            vault.resolve("vault:nonexistent")

    def test_delete(self, vault):
        vault.store("temp", "val")
        vault.delete("temp")
        assert "temp" not in vault.list_keys()

    def test_delete_nonexistent_is_noop(self, vault):
        vault.delete("nope")  # should not raise

    def test_persistence_across_instances(self, tmp_path):
        vault_path = tmp_path / "vault.enc"
        key_path = tmp_path / "vault.key"
        v1 = Vault(vault_path, key_path)
        v1.store("persistent", "secret-value")
        v2 = Vault(vault_path, key_path)
        assert v2.resolve("vault:persistent") == "secret-value"

    def test_key_file_permissions(self, vault):
        vault.store("x", "y")  # triggers key generation
        import os, stat
        mode = os.stat(vault._key_path).st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_empty_vault_list(self, vault):
        assert vault.list_keys() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jerry/code/orchestra && python -m pytest tests/test_vault.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.core.vault'`

- [ ] **Step 3: Add `cryptography` dependency**

In `pyproject.toml`, add `"cryptography>=42.0"` to the `dependencies` list:

```toml
dependencies = [
    "aiosqlite>=0.20.0",
    "pyyaml>=6.0",
    "fastapi>=0.115.0",
    "uvicorn>=0.32.0",
    "cryptography>=42.0",
]
```

Run: `cd /Users/jerry/code/orchestra && pip install -e .`

- [ ] **Step 4: Implement Vault**

```python
# src/orchestra/core/vault.py
"""Encrypted secret storage using Fernet (AES-128-CBC + HMAC-SHA256)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet


class Vault:
    """Store and retrieve secrets encrypted on disk.

    Secrets are kept in a single Fernet-encrypted JSON blob at `vault_path`.
    The Fernet key lives at `key_path` (auto-generated, chmod 0600).
    """

    VAULT_REF_PREFIX = "vault:"

    def __init__(self, vault_path: Path, key_path: Path):
        self._vault_path = vault_path
        self._key_path = key_path
        self._fernet: Fernet | None = None
        self._secrets: dict[str, str] = {}
        self._load()

    def _ensure_fernet(self) -> Fernet:
        if self._fernet:
            return self._fernet
        if self._key_path.exists():
            key = self._key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            self._key_path.parent.mkdir(parents=True, exist_ok=True)
            self._key_path.write_bytes(key)
            os.chmod(self._key_path, stat.S_IRUSR | stat.S_IWUSR)
        self._fernet = Fernet(key)
        return self._fernet

    def _load(self) -> None:
        if not self._vault_path.exists():
            self._secrets = {}
            return
        f = self._ensure_fernet()
        encrypted = self._vault_path.read_bytes()
        decrypted = f.decrypt(encrypted)
        self._secrets = json.loads(decrypted)

    def _save(self) -> None:
        f = self._ensure_fernet()
        plaintext = json.dumps(self._secrets).encode()
        self._vault_path.parent.mkdir(parents=True, exist_ok=True)
        self._vault_path.write_bytes(f.encrypt(plaintext))

    def store(self, name: str, secret: str) -> None:
        self._secrets[name] = secret
        self._save()

    def delete(self, name: str) -> None:
        self._secrets.pop(name, None)
        self._save()

    def list_keys(self) -> list[str]:
        return list(self._secrets.keys())

    def resolve(self, value: str) -> str:
        if not value.startswith(self.VAULT_REF_PREFIX):
            return value
        name = value[len(self.VAULT_REF_PREFIX):]
        if name not in self._secrets:
            raise KeyError(f"Vault key not found: {name}")
        return self._secrets[name]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/jerry/code/orchestra && python -m pytest tests/test_vault.py -v`
Expected: All 9 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/orchestra/core/vault.py tests/test_vault.py pyproject.toml
git commit -m "feat: add Vault encrypted secret storage"
```

---

### Task 2: ClaudeConfig Data Model & Manager

**Files:**
- Create: `src/orchestra/core/claude_config.py`
- Test: `tests/test_claude_config.py`

- [ ] **Step 1: Write failing tests for ClaudeConfigManager**

```python
# tests/test_claude_config.py
"""Tests for ClaudeConfig data model and manager."""

import pytest
import yaml
from pathlib import Path
from orchestra.core.claude_config import ClaudeProfile, ClaudeConfig, ClaudeConfigManager


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "orchestra.yaml"


@pytest.fixture
def manager(config_path):
    return ClaudeConfigManager(config_path)


class TestClaudeProfile:
    def test_defaults(self):
        p = ClaudeProfile(name="test")
        assert p.provider == "anthropic"
        assert p.model == "sonnet"
        assert p.max_turns == 50
        assert p.env == {}
        assert p.mcp_servers == {}
        assert p.role_models == {}

    def test_to_dict_roundtrip(self):
        p = ClaudeProfile(name="x", model="opus", env={"K": "V"})
        d = p.to_dict()
        p2 = ClaudeProfile.from_dict("x", d)
        assert p2.model == "opus"
        assert p2.env == {"K": "V"}


class TestClaudeConfigManager:
    def test_load_empty_creates_default_profile(self, manager):
        config = manager.config
        assert "default" in config.profiles
        assert config.active_profile == "default"

    def test_load_legacy_format_migrates(self, config_path):
        config_path.write_text(yaml.dump({
            "claude": {"command": "my-claude", "model": "opus", "max_turns": 80}
        }))
        mgr = ClaudeConfigManager(config_path)
        assert mgr.config.command == "my-claude"
        p = mgr.active_profile()
        assert p.model == "opus"
        assert p.max_turns == 80

    def test_load_new_format(self, config_path):
        config_path.write_text(yaml.dump({
            "claude": {
                "command": "claude",
                "active_profile": "prod",
                "profiles": {
                    "prod": {
                        "provider": "bedrock",
                        "model": "opus",
                        "max_turns": 80,
                        "env": {"AWS_REGION": "us-east-1"},
                        "role_models": {"head_leader": "opus"},
                    }
                }
            }
        }))
        mgr = ClaudeConfigManager(config_path)
        assert mgr.config.active_profile == "prod"
        p = mgr.active_profile()
        assert p.provider == "bedrock"
        assert p.role_models == {"head_leader": "opus"}

    def test_create_profile(self, manager):
        manager.create_profile("fast", ClaudeProfile(
            name="fast", model="haiku", max_turns=10,
        ))
        assert "fast" in manager.config.profiles

    def test_create_duplicate_raises(self, manager):
        with pytest.raises(ValueError, match="already exists"):
            manager.create_profile("default", ClaudeProfile(name="default"))

    def test_update_profile(self, manager):
        manager.update_profile("default", {"model": "opus"})
        assert manager.config.profiles["default"].model == "opus"

    def test_update_nonexistent_raises(self, manager):
        with pytest.raises(KeyError):
            manager.update_profile("nope", {"model": "opus"})

    def test_delete_profile(self, manager):
        manager.create_profile("temp", ClaudeProfile(name="temp"))
        manager.delete_profile("temp")
        assert "temp" not in manager.config.profiles

    def test_delete_active_raises(self, manager):
        with pytest.raises(ValueError, match="Cannot delete active"):
            manager.delete_profile("default")

    def test_switch_profile(self, manager):
        manager.create_profile("alt", ClaudeProfile(name="alt", model="opus"))
        manager.switch_profile("alt")
        assert manager.config.active_profile == "alt"
        assert manager.active_profile().model == "opus"

    def test_switch_nonexistent_raises(self, manager):
        with pytest.raises(KeyError):
            manager.switch_profile("nope")

    def test_save_roundtrip(self, config_path):
        mgr = ClaudeConfigManager(config_path)
        mgr.create_profile("new", ClaudeProfile(
            name="new", provider="bedrock", model="opus",
        ))
        mgr.save()
        mgr2 = ClaudeConfigManager(config_path)
        assert "new" in mgr2.config.profiles
        assert mgr2.config.profiles["new"].provider == "bedrock"

    def test_save_preserves_non_claude_sections(self, config_path):
        config_path.write_text(yaml.dump({
            "concurrency": {"feature_realizer": 4},
            "claude": {"model": "sonnet"},
        }))
        mgr = ClaudeConfigManager(config_path)
        mgr.save()
        raw = yaml.safe_load(config_path.read_text())
        assert raw["concurrency"]["feature_realizer"] == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jerry/code/orchestra && python -m pytest tests/test_claude_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.core.claude_config'`

- [ ] **Step 3: Implement ClaudeConfigManager**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/jerry/code/orchestra && python -m pytest tests/test_claude_config.py -v`
Expected: All 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchestra/core/claude_config.py tests/test_claude_config.py
git commit -m "feat: add ClaudeConfig profiles data model and manager"
```

---

### Task 3: Integrate into Orchestrator and AgentSpawner

**Files:**
- Modify: `src/orchestra/core/orchestrator.py` (lines 117-147)
- Modify: `src/orchestra/core/agent_spawner.py` (lines 125-199)
- Modify: `src/orchestra/main.py` (lines 19-40)

- [ ] **Step 1: Modify OrchestraConfig to hold ClaudeConfigManager and Vault**

In `orchestrator.py`, add imports at top and modify `OrchestraConfig`:

```python
# Add after existing imports (line 18):
from .claude_config import ClaudeConfigManager
from .vault import Vault
```

Add two new optional fields to the `OrchestraConfig` dataclass (after line 130):

```python
@dataclass
class OrchestraConfig:
    project_dir: Path
    orchestra_dir: Path
    max_fr: int = 2
    max_fi: int = 1
    max_hl: int = 1
    claude_cmd: str = "claude"
    max_turns: int = 50
    model: str = "sonnet"
    auto_accept: bool = False
    tracked_branch: Optional[str] = None
    auto_create_issues: bool = False
    claude_config_mgr: Optional[ClaudeConfigManager] = None
    vault: Optional[Vault] = None
```

- [ ] **Step 2: Modify Orchestrator.__init__ to pass config manager and vault to spawner**

In `Orchestrator.__init__` (line 142-147), modify the spawner construction:

Replace:
```python
self.spawner = AgentSpawner(
    claude_cmd=config.claude_cmd,
    max_turns=config.max_turns,
    model=config.model,
    on_output=self._on_agent_output,
)
```

With:
```python
self.spawner = AgentSpawner(
    claude_cmd=config.claude_cmd,
    max_turns=config.max_turns,
    model=config.model,
    on_output=self._on_agent_output,
    claude_config_mgr=config.claude_config_mgr,
    vault=config.vault,
)
```

- [ ] **Step 3: Modify AgentSpawner to use active profile**

In `agent_spawner.py`, update the constructor (lines 128-141) to accept and store the new params:

```python
class AgentSpawner:
    """Manages Claude Code CLI subprocesses with real-time output streaming."""

    def __init__(
        self,
        claude_cmd: str = "claude",
        max_turns: int = 50,
        model: str = "sonnet",
        on_output: Optional[LineCallback] = None,
        on_session_id: Optional[SessionIdCallback] = None,
        claude_config_mgr=None,
        vault=None,
    ):
        self.claude_cmd = claude_cmd
        self.max_turns = max_turns
        self.model = model
        self.on_output = on_output
        self.on_session_id = on_session_id
        self._agents: dict[str, AgentHandle] = {}
        self._counter = 0
        self._config_mgr = claude_config_mgr
        self._vault = vault
```

Then modify `spawn()` method (lines 148-217). Replace the command construction and subprocess call:

Replace lines 163-199:
```python
        cmd = [self.claude_cmd, "-p", "--verbose",
               "--output-format", "stream-json",
               "--permission-mode", "bypassPermissions"]

        # All long text goes to temp files to avoid ARG_MAX / shell escaping issues
        temp_files = []

        if system_prompt:
            sp_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", prefix="orch-sys-", delete=False)
            sp_file.write(system_prompt)
            sp_file.close()
            temp_files.append(sp_file.name)
            cmd.extend(["--system-prompt-file", sp_file.name])

        # Model priority: explicit param > role default > spawner default
        effective_model = model or ROLE_MODEL.get(role, self.model)
        if effective_model:
            cmd.extend(["--model", effective_model])

        if add_dirs:
            for d in add_dirs:
                cmd.extend(["--add-dir", str(d)])

        if extra_args:
            cmd.extend(extra_args)

        # Task prompt via stdin (not positional arg) — avoids length limits and escaping issues
        log.info("[%s] Spawning in %s (model=%s): %s", agent_id, cwd, effective_model, task_prompt[:80])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
```

With:
```python
        # Resolve active profile (if config manager present)
        profile = self._config_mgr.active_profile() if self._config_mgr else None

        permission_mode = profile.permission_mode if profile else "bypassPermissions"
        cmd = [self.claude_cmd, "-p", "--verbose",
               "--output-format", "stream-json",
               "--permission-mode", permission_mode]

        temp_files = []

        if system_prompt:
            sp_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", prefix="orch-sys-", delete=False)
            sp_file.write(system_prompt)
            sp_file.close()
            temp_files.append(sp_file.name)
            cmd.extend(["--system-prompt-file", sp_file.name])

        # Model priority: explicit param > profile role_models > profile.model > ROLE_MODEL > spawner default
        if model:
            effective_model = model
        elif profile and profile.role_models.get(role.value):
            effective_model = profile.role_models[role.value]
        elif profile:
            effective_model = profile.model
        else:
            effective_model = ROLE_MODEL.get(role, self.model)
        if effective_model:
            cmd.extend(["--model", effective_model])

        # MCP servers from profile
        if profile and profile.mcp_servers:
            mcp_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", prefix="orch-mcp-", delete=False)
            import json as _mcp_json
            _mcp_json.dump({"mcpServers": profile.mcp_servers}, mcp_file)
            mcp_file.close()
            temp_files.append(mcp_file.name)
            cmd.extend(["--mcp-config", mcp_file.name])

        if add_dirs:
            for d in add_dirs:
                cmd.extend(["--add-dir", str(d)])

        if extra_args:
            cmd.extend(extra_args)

        # Build subprocess env: inherit os.environ, overlay profile env (resolve vault refs)
        import os as _os
        proc_env = dict(_os.environ)
        if profile and profile.env:
            for k, v in profile.env.items():
                proc_env[k] = self._vault.resolve(v) if self._vault else v

        log.info("[%s] Spawning in %s (model=%s, profile=%s): %s",
                 agent_id, cwd, effective_model,
                 profile.name if profile else "none",
                 task_prompt[:80])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )
```

- [ ] **Step 4: Modify main.py load_config to init ClaudeConfigManager and Vault**

Replace the `load_config` function (lines 19-40 in `main.py`):

```python
def load_config(config_path: Path, project_dir: Path) -> OrchestraConfig:
    """Load config.yaml and build OrchestraConfig."""
    from .core.claude_config import ClaudeConfigManager
    from .core.vault import Vault

    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    conc = raw.get("concurrency", {})
    orchestra_dir = project_dir / ".orchestra"

    config_mgr = ClaudeConfigManager(config_path)
    vault = Vault(
        vault_path=orchestra_dir / "vault.enc",
        key_path=Path.home() / ".orchestra-vault-key",
    )

    profile = config_mgr.active_profile()

    return OrchestraConfig(
        project_dir=project_dir,
        orchestra_dir=orchestra_dir,
        max_fr=conc.get("feature_realizer", 2),
        max_fi=conc.get("feature_interpreter", 1),
        max_hl=conc.get("head_leader", 1),
        claude_cmd=config_mgr.config.command,
        max_turns=profile.max_turns,
        model=profile.model,
        claude_config_mgr=config_mgr,
        vault=vault,
    )
```

- [ ] **Step 5: Run existing tests to verify nothing is broken**

Run: `cd /Users/jerry/code/orchestra && python -m pytest tests/ -v --timeout=30`
Expected: All existing tests still pass (spawner tests use `claude_cmd="echo"` and won't have config_mgr set, so the `if self._config_mgr` guard keeps them working)

- [ ] **Step 6: Commit**

```bash
git add src/orchestra/core/orchestrator.py src/orchestra/core/agent_spawner.py src/orchestra/main.py
git commit -m "feat: integrate ClaudeConfigManager and Vault into spawner pipeline"
```

---

### Task 4: Backend API Endpoints

**Files:**
- Modify: `src/orchestra/web/api.py`
- Test: `tests/test_claude_config_api.py`

- [ ] **Step 1: Write failing tests for API endpoints**

```python
# tests/test_claude_config_api.py
"""Tests for /api/claude-config/* endpoints."""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from orchestra.web.api import app, set_orchestrator
from orchestra.core.orchestrator import Orchestrator, OrchestraConfig
from orchestra.core.claude_config import ClaudeConfigManager, ClaudeProfile
from orchestra.core.vault import Vault


@pytest.fixture
def setup_env(tmp_path):
    """Set up a minimal orchestrator with ClaudeConfigManager and Vault."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    orchestra_dir = project_dir / ".orchestra"
    orchestra_dir.mkdir()

    config_path = project_dir / "orchestra.yaml"
    config_path.write_text(yaml.dump({
        "claude": {
            "command": "echo",
            "active_profile": "dev",
            "profiles": {
                "dev": {"model": "sonnet", "max_turns": 30},
                "prod": {"provider": "bedrock", "model": "opus", "max_turns": 80},
            },
        }
    }))

    config_mgr = ClaudeConfigManager(config_path)
    vault = Vault(orchestra_dir / "vault.enc", tmp_path / "vault.key")

    config = OrchestraConfig(
        project_dir=project_dir,
        orchestra_dir=orchestra_dir,
        claude_cmd="echo",
        claude_config_mgr=config_mgr,
        vault=vault,
    )

    # Mock Orchestrator to avoid git repo requirement
    with patch.object(Orchestrator, '__init__', lambda self, cfg: None):
        orch = Orchestrator.__new__(Orchestrator)
        orch.config = config
        set_orchestrator(orch)

    yield {"config_mgr": config_mgr, "vault": vault, "orch": orch}

    set_orchestrator(None)


@pytest.fixture
def client(setup_env):
    return TestClient(app)


class TestClaudeConfigAPI:
    def test_get_config(self, client):
        res = client.get("/api/claude-config")
        assert res.status_code == 200
        data = res.json()
        assert data["active_profile"] == "dev"
        assert "dev" in data["profiles"]

    def test_list_profiles(self, client):
        res = client.get("/api/claude-config/profiles")
        assert res.status_code == 200
        names = [p["name"] for p in res.json()]
        assert "dev" in names
        assert "prod" in names

    def test_create_profile(self, client):
        res = client.post("/api/claude-config/profiles", json={
            "name": "fast",
            "model": "haiku",
            "max_turns": 10,
        })
        assert res.status_code == 200
        assert res.json()["name"] == "fast"

    def test_create_duplicate_returns_409(self, client):
        res = client.post("/api/claude-config/profiles", json={
            "name": "dev", "model": "sonnet",
        })
        assert res.status_code == 409

    def test_update_profile(self, client):
        res = client.put("/api/claude-config/profiles/dev", json={
            "model": "opus",
        })
        assert res.status_code == 200
        assert res.json()["model"] == "opus"

    def test_delete_profile(self, client):
        res = client.delete("/api/claude-config/profiles/prod")
        assert res.status_code == 204

    def test_delete_active_returns_400(self, client):
        res = client.delete("/api/claude-config/profiles/dev")
        assert res.status_code == 400

    def test_switch_profile(self, client):
        res = client.put("/api/claude-config/active-profile", json={"name": "prod"})
        assert res.status_code == 200
        assert res.json()["name"] == "prod"

    def test_vault_store_and_list(self, client):
        client.post("/api/claude-config/vault", json={
            "name": "my-key", "value": "secret123",
        })
        res = client.get("/api/claude-config/vault")
        assert "my-key" in res.json()

    def test_vault_delete(self, client):
        client.post("/api/claude-config/vault", json={
            "name": "tmp", "value": "x",
        })
        res = client.delete("/api/claude-config/vault/tmp")
        assert res.status_code == 204
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jerry/code/orchestra && python -m pytest tests/test_claude_config_api.py -v`
Expected: FAIL — endpoints not defined yet

- [ ] **Step 3: Add API endpoints to api.py**

Add these imports at the top of `api.py` (after existing imports):

```python
from ..core.claude_config import ClaudeConfigManager, ClaudeProfile
from ..core.vault import Vault
```

Add Pydantic models after the existing models section (~line 100):

```python
class ProfileCreate(BaseModel):
    name: str
    provider: str = "anthropic"
    model: str = "sonnet"
    max_turns: int = 50
    permission_mode: str = "bypassPermissions"
    api_base_url: Optional[str] = None
    env: dict[str, str] = {}
    mcp_servers: dict[str, dict] = {}
    role_models: dict[str, str] = {}


class ProfileUpdate(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    max_turns: Optional[int] = None
    permission_mode: Optional[str] = None
    api_base_url: Optional[str] = None
    env: Optional[dict[str, str]] = None
    mcp_servers: Optional[dict[str, dict]] = None
    role_models: Optional[dict[str, str]] = None


class ActiveProfileSwitch(BaseModel):
    name: str


class VaultStore(BaseModel):
    name: str
    value: str
```

Add helper functions:

```python
def _config_mgr() -> ClaudeConfigManager:
    orch = _orch()
    if not orch.config.claude_config_mgr:
        raise HTTPException(503, "ClaudeConfigManager not initialized")
    return orch.config.claude_config_mgr


def _vault() -> Vault:
    orch = _orch()
    if not orch.config.vault:
        raise HTTPException(503, "Vault not initialized")
    return orch.config.vault


def _mask_env(env: dict[str, str]) -> dict[str, str]:
    """Mask secret-looking values for API responses."""
    SECRET_SUFFIXES = ("_KEY", "_SECRET", "_TOKEN", "_PASSWORD")
    masked = {}
    for k, v in env.items():
        if v.startswith("vault:"):
            masked[k] = v
        elif any(k.upper().endswith(s) for s in SECRET_SUFFIXES):
            masked[k] = "••••••••"
        else:
            masked[k] = v
    return masked


def _profile_summary(p: ClaudeProfile) -> dict:
    return {
        "name": p.name,
        "provider": p.provider,
        "model": p.model,
        "max_turns": p.max_turns,
        "permission_mode": p.permission_mode,
        "api_base_url": p.api_base_url,
        "env": _mask_env(p.env),
        "mcp_servers": p.mcp_servers,
        "role_models": p.role_models,
    }
```

Add the endpoint implementations (add before the SSE event_stream route):

```python
# ── Claude Config API ──────────────────────────────────────────

@app.get("/api/claude-config")
async def get_claude_config():
    mgr = _config_mgr()
    return {
        "command": mgr.config.command,
        "active_profile": mgr.config.active_profile,
        "profiles": {
            name: _profile_summary(p)
            for name, p in mgr.config.profiles.items()
        },
    }


@app.get("/api/claude-config/profiles")
async def list_profiles():
    mgr = _config_mgr()
    return [
        {"name": p.name, "provider": p.provider, "model": p.model}
        for p in mgr.config.profiles.values()
    ]


@app.post("/api/claude-config/profiles")
async def create_profile(req: ProfileCreate):
    mgr = _config_mgr()
    profile = ClaudeProfile(
        name=req.name, provider=req.provider, model=req.model,
        max_turns=req.max_turns, permission_mode=req.permission_mode,
        api_base_url=req.api_base_url, env=req.env,
        mcp_servers=req.mcp_servers, role_models=req.role_models,
    )
    try:
        mgr.create_profile(req.name, profile)
    except ValueError:
        raise HTTPException(409, f"Profile '{req.name}' already exists")
    return _profile_summary(profile)


@app.get("/api/claude-config/profiles/{name}")
async def get_profile(name: str):
    mgr = _config_mgr()
    if name not in mgr.config.profiles:
        raise HTTPException(404, f"Profile '{name}' not found")
    return _profile_summary(mgr.config.profiles[name])


@app.put("/api/claude-config/profiles/{name}")
async def update_profile(name: str, req: ProfileUpdate):
    mgr = _config_mgr()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        mgr.update_profile(name, updates)
    except KeyError:
        raise HTTPException(404, f"Profile '{name}' not found")
    return _profile_summary(mgr.config.profiles[name])


@app.delete("/api/claude-config/profiles/{name}", status_code=204)
async def delete_profile(name: str):
    mgr = _config_mgr()
    try:
        mgr.delete_profile(name)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/claude-config/active-profile")
async def switch_active_profile(req: ActiveProfileSwitch):
    mgr = _config_mgr()
    try:
        mgr.switch_profile(req.name)
    except KeyError:
        raise HTTPException(404, f"Profile '{req.name}' not found")
    return {"name": mgr.config.active_profile}


@app.get("/api/claude-config/vault")
async def list_vault_keys():
    v = _vault()
    return v.list_keys()


@app.post("/api/claude-config/vault")
async def store_vault_secret(req: VaultStore):
    v = _vault()
    v.store(req.name, req.value)
    return {"name": req.name}


@app.delete("/api/claude-config/vault/{name}", status_code=204)
async def delete_vault_secret(name: str):
    v = _vault()
    v.delete(name)
```

- [ ] **Step 4: Run API tests**

Run: `cd /Users/jerry/code/orchestra && python -m pytest tests/test_claude_config_api.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/jerry/code/orchestra && python -m pytest tests/ -v --timeout=30`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add src/orchestra/web/api.py tests/test_claude_config_api.py
git commit -m "feat: add /api/claude-config/* REST endpoints"
```

---

### Task 5: Web Dashboard — Configuration Tab UI

**Files:**
- Modify: `src/orchestra/web/static/index.html`
- Modify: `src/orchestra/web/static/app.js`
- Modify: `src/orchestra/web/static/style.css`

- [ ] **Step 1: Add Configuration tab button to index.html**

In `index.html`, add a new side-tab button after the "Agents" button (after line 243):

```html
      <button class="side-tab" data-side-tab="config" title="Claude Code configuration">
        <span class="side-tab-icon">⚙</span>
        <span class="side-tab-label">Config</span>
      </button>
```

Add the config tab pane inside `.side-panel-content` (after the PRs pane, around line 300):

```html
        <!-- Config -->
        <div class="side-tab-pane" data-pane="config">
          <div id="config-panel"></div>
        </div>
```

- [ ] **Step 2: Add Configuration tab JavaScript to app.js**

Append the following to the end of `app.js`:

```javascript
// ── Claude Config Tab ─────────────────────────────────────────

let _configData = null;

async function fetchClaudeConfig() {
  const res = await fetch('/api/claude-config');
  if (!res.ok) return;
  _configData = await res.json();
  renderConfigPanel();
}

function renderConfigPanel() {
  const el = document.getElementById('config-panel');
  if (!el || !_configData) return;

  const { active_profile, profiles } = _configData;
  const profileNames = Object.keys(profiles);

  let html = '<div class="config-profiles">';

  // Profile cards
  html += '<div class="config-profile-cards">';
  for (const name of profileNames) {
    const p = profiles[name];
    const isActive = name === active_profile;
    html += `
      <div class="config-card ${isActive ? 'config-card-active' : ''}"
           onclick="selectConfigProfile('${name}')">
        <div class="config-card-name">${name}</div>
        <div class="config-card-meta">
          <span class="config-badge">${p.provider}</span>
          <span class="config-badge">${p.model}</span>
        </div>
        ${isActive ? '<div class="config-card-active-dot">●</div>' : ''}
      </div>`;
  }
  html += `
    <div class="config-card config-card-add" onclick="showCreateProfileDialog()">
      <span class="config-add-icon">+</span>
      <span>New Profile</span>
    </div>`;
  html += '</div>';

  // Active profile editor
  const p = profiles[active_profile];
  if (p) {
    html += renderProfileEditor(active_profile, p);
  }

  // Vault section
  html += renderVaultSection();

  html += '</div>';
  el.innerHTML = html;
}

function renderProfileEditor(name, p) {
  const roleNames = ['head_leader', 'feature_realizer', 'feature_interpreter', 'discussion_analyst'];
  const envRows = Object.entries(p.env || {}).map(([k, v]) =>
    `<tr>
      <td><input class="config-input config-input-sm" value="${k}" data-env-key="${k}" onchange="updateProfileEnvKey(this, '${name}')"></td>
      <td><input class="config-input config-input-sm" value="${v.startsWith('vault:') ? v : v}" ${v.startsWith('vault:') ? 'readonly' : ''} data-env-val="${k}" onchange="updateProfileEnvVal(this, '${name}')">
      </td>
      <td><button class="btn-icon-sm" onclick="removeProfileEnv('${name}', '${k}')">&times;</button></td>
    </tr>`
  ).join('');

  const mcpRows = Object.entries(p.mcp_servers || {}).map(([sname, srv]) =>
    `<tr>
      <td><code>${sname}</code></td>
      <td><code>${srv.command || ''} ${(srv.args || []).join(' ')}</code></td>
      <td><button class="btn-icon-sm" onclick="removeMcpServer('${name}', '${sname}')">&times;</button></td>
    </tr>`
  ).join('');

  return `
  <div class="config-editor">
    <div class="config-section">
      <div class="config-section-header" onclick="toggleConfigSection(this)">
        <span>Provider & Model</span><span class="config-chevron">▸</span>
      </div>
      <div class="config-section-body">
        <div class="config-row">
          <label>Provider</label>
          <select class="config-input" onchange="updateProfile('${name}', 'provider', this.value)">
            ${['anthropic','bedrock','vertex','custom'].map(o =>
              `<option value="${o}" ${p.provider === o ? 'selected' : ''}>${o}</option>`
            ).join('')}
          </select>
        </div>
        <div class="config-row">
          <label>Model</label>
          <input class="config-input" value="${p.model}" onchange="updateProfile('${name}', 'model', this.value)">
        </div>
        <div class="config-row" ${p.provider !== 'custom' ? 'style="display:none"' : ''}>
          <label>API Base URL</label>
          <input class="config-input" value="${p.api_base_url || ''}" onchange="updateProfile('${name}', 'api_base_url', this.value)">
        </div>
      </div>
    </div>

    <div class="config-section">
      <div class="config-section-header" onclick="toggleConfigSection(this)">
        <span>Agent Settings</span><span class="config-chevron">▸</span>
      </div>
      <div class="config-section-body">
        <div class="config-row">
          <label>Max Turns</label>
          <input class="config-input" type="number" value="${p.max_turns}" onchange="updateProfile('${name}', 'max_turns', parseInt(this.value))">
        </div>
        <div class="config-row">
          <label>Permission Mode</label>
          <select class="config-input" onchange="updateProfile('${name}', 'permission_mode', this.value)">
            ${['bypassPermissions','default'].map(o =>
              `<option value="${o}" ${p.permission_mode === o ? 'selected' : ''}>${o}</option>`
            ).join('')}
          </select>
        </div>
      </div>
    </div>

    <div class="config-section">
      <div class="config-section-header" onclick="toggleConfigSection(this)">
        <span>Role Models</span><span class="config-chevron">▸</span>
      </div>
      <div class="config-section-body">
        ${roleNames.map(r => `
          <div class="config-row">
            <label>${r}</label>
            <input class="config-input" value="${(p.role_models || {})[r] || ''}"
                   placeholder="(default: ${p.model})"
                   onchange="updateProfileRoleModel('${name}', '${r}', this.value)">
          </div>
        `).join('')}
      </div>
    </div>

    <div class="config-section">
      <div class="config-section-header" onclick="toggleConfigSection(this)">
        <span>Environment Variables</span><span class="config-chevron">▸</span>
      </div>
      <div class="config-section-body">
        <table class="config-table">
          <thead><tr><th>Key</th><th>Value</th><th></th></tr></thead>
          <tbody>${envRows}</tbody>
        </table>
        <button class="btn btn-compact" onclick="addProfileEnv('${name}')">+ Add Variable</button>
      </div>
    </div>

    <div class="config-section">
      <div class="config-section-header" onclick="toggleConfigSection(this)">
        <span>MCP Servers</span><span class="config-chevron">▸</span>
      </div>
      <div class="config-section-body">
        <table class="config-table">
          <thead><tr><th>Name</th><th>Command</th><th></th></tr></thead>
          <tbody>${mcpRows}</tbody>
        </table>
        <button class="btn btn-compact" onclick="addMcpServer('${name}')">+ Add Server</button>
      </div>
    </div>

    <div class="config-actions">
      ${name !== _configData.active_profile
        ? `<button class="btn btn-primary" onclick="switchProfile('${name}')">Activate this profile</button>`
        : `<span class="config-active-label">● Active</span>`}
      <button class="btn btn-danger" onclick="deleteProfile('${name}')">Delete</button>
    </div>
  </div>`;
}

function renderVaultSection() {
  return `
  <div class="config-section config-vault">
    <div class="config-section-header" onclick="toggleConfigSection(this)">
      <span>🔒 Vault (Encrypted Secrets)</span><span class="config-chevron">▸</span>
    </div>
    <div class="config-section-body">
      <div id="vault-keys-list">Loading…</div>
      <div class="config-row">
        <input class="config-input" id="vault-new-name" placeholder="Secret name">
        <input class="config-input" id="vault-new-value" type="password" placeholder="Secret value">
        <button class="btn btn-compact" onclick="storeVaultSecret()">Store</button>
      </div>
    </div>
  </div>`;
}

let _selectedConfigProfile = null;

function selectConfigProfile(name) {
  _selectedConfigProfile = name;
  renderConfigPanel();
}

function toggleConfigSection(header) {
  const body = header.nextElementSibling;
  const chevron = header.querySelector('.config-chevron');
  if (body.style.display === 'none') {
    body.style.display = '';
    chevron.textContent = '▾';
  } else {
    body.style.display = 'none';
    chevron.textContent = '▸';
  }
}

let _updateDebounce = null;
async function updateProfile(name, field, value) {
  clearTimeout(_updateDebounce);
  _updateDebounce = setTimeout(async () => {
    const body = {};
    body[field] = value;
    const res = await fetch(`/api/claude-config/profiles/${name}`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (res.ok) {
      _configData.profiles[name] = await res.json();
    }
  }, 500);
}

async function updateProfileRoleModel(profileName, role, value) {
  const p = _configData.profiles[profileName];
  const rm = {...(p.role_models || {})};
  if (value) rm[role] = value;
  else delete rm[role];
  await updateProfile(profileName, 'role_models', rm);
}

async function updateProfileEnvKey(input, profileName) {
  // handled via full env update
  await _syncProfileEnv(profileName);
}

async function updateProfileEnvVal(input, profileName) {
  await _syncProfileEnv(profileName);
}

async function _syncProfileEnv(profileName) {
  const rows = document.querySelectorAll(`[data-env-key]`);
  const env = {};
  rows.forEach(row => {
    const key = row.value;
    const valInput = document.querySelector(`[data-env-val="${row.dataset.envKey}"]`);
    if (key && valInput) env[key] = valInput.value;
  });
  await updateProfile(profileName, 'env', env);
}

async function addProfileEnv(profileName) {
  const key = prompt('Variable name:');
  if (!key) return;
  const val = prompt('Value (use vault:name for secrets):');
  if (val === null) return;
  const p = _configData.profiles[profileName];
  const env = {...(p.env || {}), [key]: val};
  const res = await fetch(`/api/claude-config/profiles/${profileName}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({env}),
  });
  if (res.ok) {
    _configData.profiles[profileName] = await res.json();
    renderConfigPanel();
  }
}

async function removeProfileEnv(profileName, key) {
  const p = _configData.profiles[profileName];
  const env = {...(p.env || {})};
  delete env[key];
  const res = await fetch(`/api/claude-config/profiles/${profileName}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({env}),
  });
  if (res.ok) {
    _configData.profiles[profileName] = await res.json();
    renderConfigPanel();
  }
}

async function addMcpServer(profileName) {
  const name = prompt('Server name:');
  if (!name) return;
  const command = prompt('Command (e.g. uvx):');
  if (!command) return;
  const argsStr = prompt('Args (comma-separated):') || '';
  const args = argsStr.split(',').map(a => a.trim()).filter(Boolean);

  const p = _configData.profiles[profileName];
  const servers = {...(p.mcp_servers || {}), [name]: {command, args}};
  const res = await fetch(`/api/claude-config/profiles/${profileName}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mcp_servers: servers}),
  });
  if (res.ok) {
    _configData.profiles[profileName] = await res.json();
    renderConfigPanel();
  }
}

async function removeMcpServer(profileName, serverName) {
  const p = _configData.profiles[profileName];
  const servers = {...(p.mcp_servers || {})};
  delete servers[serverName];
  const res = await fetch(`/api/claude-config/profiles/${profileName}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mcp_servers: servers}),
  });
  if (res.ok) {
    _configData.profiles[profileName] = await res.json();
    renderConfigPanel();
  }
}

async function switchProfile(name) {
  const res = await fetch('/api/claude-config/active-profile', {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name}),
  });
  if (res.ok) {
    await fetchClaudeConfig();
  }
}

async function deleteProfile(name) {
  if (!confirm(`Delete profile "${name}"?`)) return;
  const res = await fetch(`/api/claude-config/profiles/${name}`, {method: 'DELETE'});
  if (res.ok) {
    await fetchClaudeConfig();
  } else {
    const err = await res.json();
    alert(err.detail || 'Cannot delete');
  }
}

async function showCreateProfileDialog() {
  const name = prompt('Profile name:');
  if (!name) return;
  const res = await fetch('/api/claude-config/profiles', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name}),
  });
  if (res.ok) {
    await fetchClaudeConfig();
  } else {
    const err = await res.json();
    alert(err.detail || 'Error');
  }
}

async function fetchVaultKeys() {
  const res = await fetch('/api/claude-config/vault');
  if (!res.ok) return;
  const keys = await res.json();
  const el = document.getElementById('vault-keys-list');
  if (!el) return;
  if (keys.length === 0) {
    el.innerHTML = '<div class="config-empty">No secrets stored</div>';
    return;
  }
  el.innerHTML = keys.map(k => `
    <div class="vault-key-row">
      <span class="vault-key-name">${k}</span>
      <span class="vault-key-value">••••••••</span>
      <button class="btn-icon-sm" onclick="deleteVaultKey('${k}')">&times;</button>
    </div>
  `).join('');
}

async function storeVaultSecret() {
  const nameEl = document.getElementById('vault-new-name');
  const valueEl = document.getElementById('vault-new-value');
  if (!nameEl.value || !valueEl.value) return;
  await fetch('/api/claude-config/vault', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: nameEl.value, value: valueEl.value}),
  });
  nameEl.value = '';
  valueEl.value = '';
  fetchVaultKeys();
}

async function deleteVaultKey(name) {
  if (!confirm(`Delete secret "${name}"?`)) return;
  await fetch(`/api/claude-config/vault/${name}`, {method: 'DELETE'});
  fetchVaultKeys();
}
```

Also, hook the config tab into the existing side-tab switching logic. Find the existing `document.addEventListener('DOMContentLoaded', ...)` or equivalent initialization in `app.js` and add a call to `fetchClaudeConfig()` when the config tab is activated. Look for the side-tab click handler and add:

```javascript
// Inside the side-tab click handler, add a case for config:
if (tabName === 'config') {
  fetchClaudeConfig();
  fetchVaultKeys();
}
```

- [ ] **Step 3: Add CSS styles for the configuration tab**

Append to `style.css`:

```css
/* ── Config Tab ────────────────────────────────────────────── */

.config-profiles { padding: 12px; }

.config-profile-cards {
  display: flex; gap: 8px; flex-wrap: wrap;
  margin-bottom: 16px; padding-bottom: 12px;
  border-bottom: 1px solid var(--border-soft);
}

.config-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 10px 14px;
  cursor: pointer;
  min-width: 120px;
  position: relative;
  transition: border-color 0.15s, background 0.15s;
}
.config-card:hover { background: var(--bg-hover); border-color: var(--border-strong); }
.config-card-active { border-color: var(--accent); background: var(--bg-hover); }
.config-card-name { font-weight: 600; font-size: 13px; margin-bottom: 4px; }
.config-card-meta { display: flex; gap: 4px; }
.config-card-active-dot {
  position: absolute; top: 6px; right: 8px;
  color: var(--accent); font-size: 10px;
}
.config-card-add {
  border-style: dashed;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  color: var(--text-mute);
}
.config-card-add:hover { color: var(--accent); border-color: var(--accent-soft); }
.config-add-icon { font-size: 20px; line-height: 1; }

.config-badge {
  background: var(--bg-panel);
  border: 1px solid var(--border-soft);
  border-radius: 3px;
  padding: 1px 6px;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-mute);
}

.config-editor { margin-top: 8px; }

.config-section {
  border: 1px solid var(--border-soft);
  border-radius: var(--radius);
  margin-bottom: 8px;
  overflow: hidden;
}
.config-section-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px;
  background: var(--bg-elevated);
  cursor: pointer;
  font-weight: 500; font-size: 12px;
  color: var(--text-mute);
  user-select: none;
}
.config-section-header:hover { color: var(--text); }
.config-chevron { font-size: 10px; }
.config-section-body { padding: 10px 12px; }

.config-row {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 8px;
}
.config-row label {
  min-width: 140px; font-size: 12px;
  color: var(--text-mute);
}

.config-input {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 12px;
  padding: 5px 8px;
  flex: 1;
}
.config-input:focus { border-color: var(--accent-soft); outline: none; }
.config-input-sm { font-size: 11px; padding: 3px 6px; }

.config-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.config-table th {
  text-align: left; padding: 4px 6px;
  color: var(--text-dim); font-weight: 500;
  border-bottom: 1px solid var(--border-soft);
}
.config-table td { padding: 4px 6px; }

.config-actions {
  display: flex; align-items: center; gap: 8px;
  margin-top: 12px; padding-top: 12px;
  border-top: 1px solid var(--border-soft);
}
.config-active-label { color: var(--accent); font-size: 12px; font-weight: 500; }

.btn-danger {
  background: transparent; border: 1px solid var(--c-rejected);
  color: var(--c-rejected); margin-left: auto;
}
.btn-danger:hover { background: rgba(232,107,104,0.1); }

.btn-icon-sm {
  background: none; border: none; color: var(--text-dim);
  cursor: pointer; padding: 2px 4px; font-size: 14px;
}
.btn-icon-sm:hover { color: var(--c-rejected); }

.config-vault { margin-top: 16px; }

.vault-key-row {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 0;
  border-bottom: 1px solid var(--border-soft);
}
.vault-key-name { font-family: var(--font-mono); font-size: 12px; min-width: 120px; }
.vault-key-value { color: var(--text-dim); font-size: 12px; flex: 1; }

.config-empty { color: var(--text-dim); font-size: 12px; padding: 8px 0; }
```

- [ ] **Step 4: Test manually in browser**

Run: `cd /Users/jerry/code/orchestra && python -m orchestra web --port 8420`

Verify:
1. Config tab appears in right sidebar
2. Profile cards render correctly
3. Clicking a profile card shows its editor
4. Editing a field triggers auto-save (check YAML file)
5. Profile switching works
6. Vault store/delete works
7. Creating and deleting profiles works

- [ ] **Step 5: Commit**

```bash
git add src/orchestra/web/static/index.html src/orchestra/web/static/app.js src/orchestra/web/static/style.css
git commit -m "feat: add Configuration tab to web dashboard"
```

---

### Task 6: Ensure .orchestra/vault.enc is gitignored

**Files:**
- Modify: `src/orchestra/core/worktree_manager.py` (the `ensure_orchestra_gitignored` method)

- [ ] **Step 1: Check current gitignore logic**

Read the `ensure_orchestra_gitignored` method in `worktree_manager.py`. It should already be adding `.orchestra/` to `.gitignore`. Since `vault.enc` lives under `.orchestra/`, it's already covered.

Verify by reading the method. If `.orchestra/` is already gitignored as a directory, no changes needed. Just confirm and skip.

If NOT already covered, add `vault.enc` to the gitignore pattern.

- [ ] **Step 2: Add vault key path to user-level gitignore**

The vault key lives at `~/.orchestra-vault-key` which is outside the repo, so it's automatically safe from git. No action needed.

- [ ] **Step 3: Commit (only if changes were needed)**

```bash
git add -A && git commit -m "chore: ensure vault files are gitignored"
```

---

### Task 7: Final Integration Test

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/jerry/code/orchestra && python -m pytest tests/ -v --timeout=30`
Expected: All tests pass

- [ ] **Step 2: Verify backward compatibility**

Create a minimal `orchestra.yaml` with old format and verify it works:

```bash
cd /tmp && mkdir test-orch && cd test-orch
cat > orchestra.yaml << 'EOF'
claude:
  command: claude
  model: sonnet
  max_turns: 50
concurrency:
  feature_realizer: 2
EOF
cd /Users/jerry/code/orchestra
python -c "
from orchestra.core.claude_config import ClaudeConfigManager
from pathlib import Path
mgr = ClaudeConfigManager(Path('/tmp/test-orch/orchestra.yaml'))
p = mgr.active_profile()
assert p.model == 'sonnet'
assert p.max_turns == 50
assert mgr.config.active_profile == 'default'
print('Backward compatibility OK')
"
```

- [ ] **Step 3: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: Claude Code Configuration Manager — complete implementation"
```
