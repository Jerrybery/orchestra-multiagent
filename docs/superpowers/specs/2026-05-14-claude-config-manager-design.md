# Claude Code Configuration Manager — Design Spec

## Overview

为 Orchestra 新增 Claude Code 配置管理入口，支持 Profile 预设切换、Provider/Model 管理、API Key 加密存储、MCP Server 配置、环境变量注入，全部通过 Web Dashboard 操作。灵感来自 cc switch 项目的 Profile-Centric 设计理念。

## Goals

1. **Profile 预设切换**：定义多套命名配置（如 `dev-fast`、`prod-quality`），一键切换全局激活 profile
2. **Provider/Model 灵活配置**：支持 anthropic / bedrock / vertex / custom provider，per-role model 覆盖
3. **API Key 加密管理**：Vault 加密存储 secret，YAML 中通过 `vault:<name>` 引用
4. **MCP Server 配置**：per-profile MCP server 定义，启动 agent 时通过 `--mcp-config` 注入
5. **Web Dashboard 内嵌**：在现有 Dashboard 中新增 Configuration 面板
6. **向后兼容**：现有最小化 `orchestra.yaml` 自动迁移为新格式

## Non-Goals

- 不做多工具管理（仅 Claude Code，不像 cc switch 管理 Codex/Gemini 等）
- 不做 cloud sync
- 不做 system prompt 管理（已有 prompts/ 目录机制）
- 不做实时 hot-reload（切换 profile 后，已运行的 agent 不受影响，仅新启动的 agent 使用新配置）

---

## 1. Configuration Data Model

### orchestra.yaml 扩展结构

```yaml
claude:
  command: "claude"
  active_profile: "dev-fast"

  profiles:
    dev-fast:
      provider: anthropic
      model: sonnet
      max_turns: 30
      permission_mode: bypassPermissions
      api_base_url: null
      env:
        ANTHROPIC_API_KEY: "vault:anthropic-dev-key"
      mcp_servers: {}
      role_models:
        head_leader: opus
        discussion_analyst: opus

    prod-quality:
      provider: bedrock
      model: opus
      max_turns: 80
      permission_mode: bypassPermissions
      env:
        AWS_REGION: "us-east-1"
        AWS_PROFILE: "claude-prod"
      mcp_servers:
        fetch:
          command: "uvx"
          args: ["mcp-fetch"]
      role_models: {}

concurrency:
  head_leader: 1
  feature_realizer: 2
  feature_interpreter: 1
```

### Python Dataclasses

```python
@dataclass
class ClaudeProfile:
    name: str
    provider: str = "anthropic"        # anthropic | bedrock | vertex | custom
    model: str = "sonnet"
    max_turns: int = 50
    permission_mode: str = "bypassPermissions"
    api_base_url: Optional[str] = None
    env: dict[str, str] = field(default_factory=dict)
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    role_models: dict[str, str] = field(default_factory=dict)

@dataclass
class ClaudeConfig:
    command: str = "claude"
    active_profile: str = "default"
    profiles: dict[str, ClaudeProfile] = field(default_factory=dict)
```

### Backward Compatibility

加载旧格式 YAML（无 `profiles` 段）时，自动生成一个名为 `default` 的 profile：

```python
# Old format:
# claude: { command: "claude", model: "sonnet", max_turns: 50 }
# → Becomes:
# claude:
#   command: "claude"
#   active_profile: "default"
#   profiles:
#     default: { provider: anthropic, model: sonnet, max_turns: 50, ... }
```

---

## 2. Vault (Encrypted Secret Storage)

### Storage

- File: `.orchestra/vault.enc`
- Format: Fernet-encrypted JSON blob `{ "key-name": "secret-value", ... }`
- Key file: `~/.orchestra-vault-key` (Fernet key, auto-generated on first use, `chmod 0600`)

### Interface

```python
class Vault:
    def __init__(self, vault_path: Path, key_path: Path): ...
    def resolve(self, value: str) -> str:
        """'vault:name' → decrypted value; plain string → passthrough"""
    def store(self, name: str, secret: str) -> None: ...
    def delete(self, name: str) -> None: ...
    def list_keys(self) -> list[str]: ...
```

### Security

- `.orchestra/vault.enc` must be in `.gitignore`
- Key file at `~/.orchestra-vault-key` with `0600` permissions
- API never returns decrypted secret values
- Dependency: `cryptography` package (Fernet = AES-128-CBC + HMAC-SHA256)

---

## 3. Backend API

### New Endpoints

| Endpoint | Method | Request Body | Response | Description |
|----------|--------|-------------|----------|-------------|
| `/api/claude-config` | GET | — | `ClaudeConfig` (env vault refs masked) | Full config |
| `/api/claude-config/profiles` | GET | — | `[{name, provider, model}]` | Profile summaries |
| `/api/claude-config/profiles` | POST | `ClaudeProfile` | `ClaudeProfile` | Create profile |
| `/api/claude-config/profiles/{name}` | GET | — | `ClaudeProfile` (masked) | Single profile |
| `/api/claude-config/profiles/{name}` | PUT | `ClaudeProfile` (partial) | `ClaudeProfile` | Update profile |
| `/api/claude-config/profiles/{name}` | DELETE | — | `204` | Delete (not active) |
| `/api/claude-config/active-profile` | PUT | `{name: str}` | `{name: str}` | Switch active |
| `/api/claude-config/vault` | GET | — | `[str]` | List key names |
| `/api/claude-config/vault` | POST | `{name, value}` | `{name: str}` | Store secret |
| `/api/claude-config/vault/{name}` | DELETE | — | `204` | Delete secret |

### Masking Rules

- Env values matching `vault:*` → display as `vault:key-name` (reference visible, value hidden)
- Env values matching common secret patterns (`*_KEY`, `*_SECRET`, `*_TOKEN`) → display as `••••••••`
- All other env values → displayed as-is

### Persistence Flow

```
UI change → API endpoint → ClaudeConfigManager
  ├→ Profile CRUD → update in-memory + rewrite orchestra.yaml
  ├→ Vault CRUD → update in-memory + rewrite .orchestra/vault.enc
  └→ Active profile switch → update in-memory + YAML + notify AgentSpawner
```

---

## 4. AgentSpawner Integration

### Modified spawn() Flow

```python
async def spawn(self, role, task_prompt, ...):
    profile = self.config_manager.active_profile()

    # 1. Determine model: role_models[role] > profile.model
    effective_model = profile.role_models.get(role.value, profile.model)

    # 2. Resolve env: vault refs → decrypted values, merge with os.environ
    resolved_env = {**os.environ}
    for k, v in profile.env.items():
        resolved_env[k] = self.vault.resolve(v)

    # 3. Build MCP config temp file (if any servers defined)
    mcp_config_path = None
    if profile.mcp_servers:
        mcp_config_path = write_temp_mcp_config(profile.mcp_servers)

    # 4. Build command
    cmd = [self.claude_cmd, "-p", "--verbose",
           "--output-format", "stream-json",
           "--permission-mode", profile.permission_mode,
           "--model", effective_model]
    if mcp_config_path:
        cmd.extend(["--mcp-config", mcp_config_path])

    # 5. Spawn with resolved env
    proc = await asyncio.create_subprocess_exec(
        *cmd, env=resolved_env, ...)
```

### Key Changes from Current Implementation

- `ROLE_MODEL` dict replaced by `profile.role_models` (with fallback to profile.model)
- Environment explicitly constructed instead of inheriting blindly
- MCP servers injected via temp config file
- Permission mode configurable per-profile instead of hardcoded

---

## 5. Web Dashboard UI

### Layout: New "Configuration" Tab

Integrated into the existing Dashboard as a new tab alongside the task/agent views.

#### 5.1 Profile Switcher (Top Bar)

- Horizontal row of profile cards, active one highlighted
- Each card: name + provider badge + model label
- Click to switch active profile (immediate, affects new agents only)
- "+" button to create new profile (opens empty edit form)
- "Clone" action on each card to duplicate a profile

#### 5.2 Profile Editor (Main Area)

Collapsible sections for the selected profile:

**Provider & Model**
- Provider: dropdown (anthropic / bedrock / vertex / custom)
- Model: text input with suggestions (opus / sonnet / haiku)
- API Base URL: text input (visible only when provider = custom)

**Agent Settings**
- Max Turns: number input
- Permission Mode: dropdown (bypassPermissions / default / ...)

**Role Models**
- Table: role name | model override (empty = use profile default)
- Rows: head_leader, feature_realizer, feature_interpreter, discussion_analyst

**Environment Variables**
- Key-value editor with add/remove rows
- Values referencing vault shown as `vault:key-name` with lock icon
- "Link to Vault" button to create a vault reference

**MCP Servers**
- List of server entries: name / command / args (editable)
- Add/remove server buttons

#### 5.3 Vault Panel (Collapsible Bottom)

- Table: secret name | actions (delete)
- "Add Secret" button → modal dialog (name + value input)
- No reveal/view functionality — write-only for security

### Auto-Save Behavior

- Form fields debounce 500ms then auto-save via PUT
- Success: brief ✓ toast
- Error: red toast with message, field reverts

### Technology

Vanilla HTML + JS + CSS, consistent with existing `static/` approach. No new frameworks.

---

## 6. File Changes Summary

| File | Action | Responsibility |
|------|--------|----------------|
| `src/orchestra/core/claude_config.py` | New | `ClaudeProfile`, `ClaudeConfig`, `ClaudeConfigManager` |
| `src/orchestra/core/vault.py` | New | `Vault` encrypted storage |
| `src/orchestra/core/agent_spawner.py` | Modify | Use active profile for env/model/mcp |
| `src/orchestra/core/orchestrator.py` | Modify | Hold `ClaudeConfigManager` + `Vault` instances |
| `src/orchestra/web/api.py` | Modify | Add `/api/claude-config/*` endpoints |
| `src/orchestra/web/static/app.js` | Modify | Configuration tab UI |
| `src/orchestra/web/static/style.css` | Modify | Configuration tab styles |
| `src/orchestra/main.py` | Modify | Load extended claude config, init vault |
| `pyproject.toml` | Modify | Add `cryptography` dependency |

---

## 7. Testing Strategy

- **Unit tests**: `ClaudeConfigManager` load/save/migrate, `Vault` encrypt/decrypt/resolve, profile CRUD
- **Integration tests**: API endpoints with test orchestra.yaml, vault round-trip
- **Manual test**: Web UI profile switching, vault secret storage, agent spawn with custom profile
