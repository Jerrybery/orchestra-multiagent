# Database Abstraction Layer & Docker Deployment — Design Spec

## Overview

将 Orchestra 从单机 SQLite 工具升级为支持 SQLite/PostgreSQL 双后端的团队协同平台。引入 SQLAlchemy Async ORM 替换现有 942 行手写 SQL，支持多开发者通过共享 PostgreSQL 数据库协作，服务器端通过 Docker Compose 部署 Dashboard 供团队查看和管理开发状态。

## Goals

1. **SQLAlchemy ORM 替换**：12 张表映射为 ORM Model，TaskQueue 的 49 个方法改用 session 查询
2. **双后端支持**：SQLite（本地默认）和 PostgreSQL（团队服务器），通过配置切换
3. **多用户标识**：自助注册 + user_id 打标记，追踪"谁做了什么"
4. **Docker 部署**：Compose 编排 PostgreSQL + Orchestra Dashboard，一键启动
5. **向后兼容**：本地 SQLite 模式无感知，不影响现有工作流

## Non-Goals

- 不做细粒度 RBAC 权限控制（所有人都能做所有操作）
- 不做 Dashboard 登录认证（URL 参数或 cookie 识别用户即可）
- 不做 SQLite → PG 数据迁移（两个是独立数据源）
- 不做 Claude Code CLI 的服务器端执行（所有 agent 在本地运行）
- 不做实时 WebSocket 同步（现有 SSE + 轮询足够）

---

## 1. ORM Models

### 文件结构

```
src/orchestra/core/db/
├── __init__.py          # 导出 get_engine, get_session_factory, Base
├── engine.py            # create_db_engine(), get_session_factory()
├── models.py            # 13 个 ORM Model (12 existing + User)
└── alembic/
    ├── alembic.ini
    ├── env.py
    └── versions/
        └── 001_initial.py
```

### Model 定义（13 个表）

```python
from sqlalchemy.orm import DeclarativeBase, relationship, Mapped, mapped_column
from sqlalchemy import String, Integer, Text, DateTime, JSON, ForeignKey, func
from typing import Optional
import datetime

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

class Requirement(Base):
    __tablename__ = "requirements"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    user_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    tasks: Mapped[list["Task"]] = relationship(back_populates="requirement")
    proposals: Mapped[list["Proposal"]] = relationship(back_populates="requirement")

class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    requirement_id: Mapped[str] = mapped_column(String, ForeignKey("requirements.id"))
    features: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    requirement: Mapped["Requirement"] = relationship(back_populates="proposals")

class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="idea")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    requirement_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("requirements.id"), nullable=True)
    assigned_to: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    branch: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    worktree_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    spec_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    spec: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fail_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_issue: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    user_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    requirement: Mapped[Optional["Requirement"]] = relationship(back_populates="tasks")

class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    user_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

class Discussion(Base):
    __tablename__ = "discussions"
    root_issue: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="tracking")
    last_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    issues: Mapped[list["DiscussionIssue"]] = relationship(back_populates="discussion")

class DiscussionIssue(Base):
    __tablename__ = "discussion_issues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    root_issue: Mapped[int] = mapped_column(Integer, ForeignKey("discussions.root_issue"))
    issue_number: Mapped[int] = mapped_column(Integer, unique=True)
    title: Mapped[str] = mapped_column(String, default="")
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parent_issue: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comments: Mapped[list] = mapped_column(JSON, default=list)
    snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    discussion: Mapped["Discussion"] = relationship(back_populates="issues")

class DraftComment(Base):
    __tablename__ = "draft_comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    root_issue: Mapped[int] = mapped_column(Integer, ForeignKey("discussions.root_issue"))
    target_issue: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String, default="analyst")
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

class DraftMessage(Base):
    __tablename__ = "draft_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(Integer, ForeignKey("draft_comments.id"))
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    target_kind: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, default="auto")
    status: Mapped[str] = mapped_column(String, default="pending")
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    log_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    result_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    previous_run_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agent_runs.id"), nullable=True)
    resumed_from_run_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agent_runs.id"), nullable=True)
    fallback_from_run_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agent_runs.id"), nullable=True)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id"), nullable=True)

class AutoPause(Base):
    __tablename__ = "auto_pauses"
    target_kind: Mapped[str] = mapped_column(String, primary_key=True)
    target_id: Mapped[str] = mapped_column(String, primary_key=True)
    paused_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    caused_by_run_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agent_runs.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")

class RunMessage(Base):
    __tablename__ = "run_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("agent_runs.id"))
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

class ReviewFinding(Base):
    __tablename__ = "review_findings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("tasks.id"))
    run_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agent_runs.id"), nullable=True)
    round: Mapped[int] = mapped_column(Integer, default=1)
    verdict: Mapped[str] = mapped_column(String, default="unknown")
    critical: Mapped[list] = mapped_column(JSON, default=list)
    important: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
```

### Indices

SQLAlchemy `Index()` 声明在 Model 的 `__table_args__` 中，复刻现有 9 个索引。

---

## 2. Database Engine & Session Management

### engine.py

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncEngine

def create_db_engine(database_config) -> tuple[AsyncEngine, async_sessionmaker]:
    """Create engine from config.

    database_config can be:
    - "sqlite" or missing → sqlite+aiosqlite:///<orchestra_dir>/tasks.db
    - {"url": "postgresql+asyncpg://..."} → use directly
    - env var ORCHESTRA_DATABASE_URL overrides everything
    """
    import os
    url = os.environ.get("ORCHESTRA_DATABASE_URL")

    if not url:
        if isinstance(database_config, dict) and "url" in database_config:
            url = database_config["url"]

    if not url:
        # Default: SQLite in .orchestra/
        url = None  # Caller must provide orchestra_dir to build path

    engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory
```

### 配置优先级

```
ORCHESTRA_DATABASE_URL env var  >  orchestra.yaml database.url  >  sqlite (default)
```

### 连接池

- **SQLite**：单连接（aiosqlite 限制）
- **PostgreSQL**：SQLAlchemy 默认 pool_size=5，max_overflow=10，适合团队规模

---

## 3. TaskQueue Refactor

### 构造函数变更

```python
# Before
class TaskQueue:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._db = None  # aiosqlite.Connection

    async def init(self):
        self._db = await aiosqlite.connect(...)
        await self._db.executescript(SCHEMA)

# After
class TaskQueue:
    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def init(self):
        pass  # Schema managed by Alembic or create_all
```

### 方法重写示例

```python
# Before (raw SQL)
async def get_task(self, task_id: str):
    async with self._db.execute(
        "SELECT * FROM tasks WHERE id = ?", (task_id,)
    ) as cur:
        row = await cur.fetchone()
        return self._row_to_task(row) if row else None

# After (ORM)
async def get_task(self, task_id: str):
    async with self._session_factory() as session:
        return await session.get(Task, task_id)
```

```python
# Before
async def add_task(self, task_id, title, priority, depends_on, requirement_id, spec_path="", source_issue=None):
    await self._db.execute(
        "INSERT INTO tasks (...) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, title, "idea", priority, json.dumps(depends_on), requirement_id, spec_path, source_issue)
    )
    await self._db.commit()

# After
async def add_task(self, task_id, title, priority, depends_on, requirement_id, spec_path="", source_issue=None, user_id=None):
    async with self._session_factory() as session:
        task = Task(
            id=task_id, title=title, status="idea", priority=priority,
            depends_on=depends_on, requirement_id=requirement_id,
            spec_path=spec_path, source_issue=source_issue, user_id=user_id,
        )
        session.add(task)
        await session.commit()
        return task
```

### 返回类型变更

现有方法返回 dataclass 实例（如 `Task`、`Requirement`）。改为直接返回 ORM Model 实例。由于属性名完全一致（`.id`、`.status`、`.title` 等），消费方代码无需修改。

现有 task_queue.py 中定义的 dataclass（`Task`、`Requirement`、`Proposal` 等）删除，由 ORM Model 取代。

### 修复 orchestrator.py DB 泄漏

`orchestrator.py:766-773` 直接访问 `self.task_queue._db`。

改为在 TaskQueue 中新增方法：
```python
async def get_proposal_for_task(self, task_id: str) -> Optional[str]:
    """Return the proposal_id whose features contain task_id."""
    async with self._session_factory() as session:
        result = await session.execute(select(Proposal))
        for p in result.scalars():
            if any(f.get("id") == task_id for f in p.features):
                return p.id
    return None
```

---

## 4. User Identity

### User Model

见第 1 部分。极简：`id` (string PK, 用户自选) + `display_name` + `created_at` + `last_seen_at`。

### 注册流程

本地 Orchestra 首次连接远程 PG 时：
1. 读取 `orchestra.yaml` 中的 `database.user_id`
2. 如果未配置，交互式提示输入昵称，写入配置
3. 向 `users` 表 INSERT（`ON CONFLICT DO NOTHING`——重复 ID 跳过）
4. 更新 `last_seen_at`

### 写操作打标记

写操作（submit requirement、approve proposal、transition task 等）将 `user_id` 存入对应记录。Events 表的 `user_id` 字段追踪操作归属。

Dashboard 展示时显示 "Jerry submitted requirement" 等信息。

### SQLite 模式下的 user_id

本地 SQLite 模式下 `user_id` 始终为 None（单人使用，不需要标识）。所有 `user_id` 字段都是 `nullable=True`。

---

## 5. Docker Deployment

### 文件结构

```
deploy/
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── nginx.conf          (optional: reverse proxy)
```

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
COPY prompts/ prompts/
RUN pip install --no-cache-dir ".[server]"
EXPOSE 8420
CMD ["orchestra", "web", "--host", "0.0.0.0", "--port", "8420"]
```

### docker-compose.yml

```yaml
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: orchestra
      POSTGRES_USER: orchestra
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U orchestra"]
      interval: 5s
      retries: 5

  orchestra:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    restart: unless-stopped
    ports:
      - "${ORCHESTRA_PORT:-8420}:8420"
    environment:
      ORCHESTRA_DATABASE_URL: postgresql+asyncpg://orchestra:${POSTGRES_PASSWORD}@postgres:5432/orchestra
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  pgdata:
```

### .env.example

```bash
POSTGRES_PASSWORD=change-me-to-a-strong-password
ORCHESTRA_PORT=8420
```

### pyproject.toml 变更

```toml
[project.optional-dependencies]
tui = ["textual>=0.50.0"]
server = ["asyncpg>=0.29.0"]

[project.dependencies]
# 新增
"sqlalchemy[asyncio]>=2.0"
# 保留
"aiosqlite>=0.20.0"
# 新增
"alembic>=1.13.0"
```

---

## 6. orchestra.yaml 扩展

```yaml
# 本地开发者（默认，什么都不配就用 SQLite）
database: sqlite

# 连接团队服务器
database:
  url: postgresql+asyncpg://orchestra:pass@server:5432/orchestra
  user_id: "jerry"

# Claude Code 配置（前一个 feature 已实现）
claude:
  active_profile: dev-fast
  profiles: ...

concurrency:
  head_leader: 1
  feature_realizer: 2
  feature_interpreter: 1
```

---

## 7. File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `src/orchestra/core/db/__init__.py` | Create | Package init, exports |
| `src/orchestra/core/db/engine.py` | Create | Engine + session factory creation |
| `src/orchestra/core/db/models.py` | Create | 13 ORM Models |
| `src/orchestra/core/db/alembic/` | Create | Alembic config + initial migration |
| `src/orchestra/core/task_queue.py` | Rewrite | 49 methods: raw SQL → ORM session |
| `src/orchestra/core/orchestrator.py` | Modify | Use session_factory, fix DB leak |
| `src/orchestra/core/agent_run_manager.py` | Modify | Adapt to ORM returns |
| `src/orchestra/core/context_manager.py` | Modify | Adapt to ORM returns |
| `src/orchestra/web/api.py` | Modify | User registration endpoint, adapt to ORM |
| `src/orchestra/main.py` | Modify | Load DB config, create engine |
| `pyproject.toml` | Modify | Add sqlalchemy, asyncpg, alembic deps |
| `deploy/Dockerfile` | Create | Container image |
| `deploy/docker-compose.yml` | Create | PG + Orchestra services |
| `deploy/.env.example` | Create | Environment template |
| `tests/conftest.py` | Modify | Fixtures use session_factory |
| `tests/test_task_queue_orm.py` | Create | ORM-based task queue tests |

---

## 8. Testing Strategy

- **Unit tests**: 每个 ORM Model 的 CRUD，TaskQueue 的 49 个方法在 SQLite 后端（现有测试迁移）
- **Integration tests**: PostgreSQL 后端（需要 Docker PG 或 testcontainers）
- **Backward compatibility**: 确保 SQLite 模式下所有现有测试通过
- **Docker smoke test**: `docker compose up` → 访问 Dashboard → 验证 PG 连接

现有 160+ 测试全部迁移到 ORM 版本（改 fixture 中的 TaskQueue 构造方式）。
