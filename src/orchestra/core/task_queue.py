"""SQLite-backed task queue with state machine for feature lifecycle."""

from __future__ import annotations

import enum
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS requirements (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'idea',
    priority INTEGER NOT NULL DEFAULT 0,
    depends_on TEXT NOT NULL DEFAULT '[]',
    requirement_id TEXT,
    assigned_to TEXT,
    branch TEXT,
    worktree_path TEXT,
    spec_path TEXT,
    reject_reason TEXT,
    source_issue INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (requirement_id) REFERENCES requirements(id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_requirement ON tasks(requirement_id);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    requirement_id TEXT NOT NULL,
    features TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    FOREIGN KEY (requirement_id) REFERENCES requirements(id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS discussions (
    root_issue INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'watching',
    last_analysis TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS discussion_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_issue INTEGER NOT NULL,
    issue_number INTEGER NOT NULL UNIQUE,
    parent_issue INTEGER,
    title TEXT NOT NULL,
    body TEXT DEFAULT '',
    last_comment_id INTEGER DEFAULT 0,
    snapshot TEXT DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY (root_issue) REFERENCES discussions(root_issue)
);

CREATE INDEX IF NOT EXISTS idx_disc_issues_root ON discussion_issues(root_issue);

CREATE TABLE IF NOT EXISTS draft_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_issue INTEGER NOT NULL,
    target_issue INTEGER NOT NULL,
    body TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'analyst',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    FOREIGN KEY (root_issue) REFERENCES discussions(root_issue)
);

CREATE INDEX IF NOT EXISTS idx_drafts_status ON draft_comments(status);

CREATE TABLE IF NOT EXISTS draft_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES draft_comments(id)
);

CREATE INDEX IF NOT EXISTS idx_draft_messages_draft ON draft_messages(draft_id);
"""


class TaskStatus(str, enum.Enum):
    IDEA = "idea"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    TESTING = "testing"
    REVIEW = "review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DONE = "done"
    FAILED = "failed"


# Valid state transitions
TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.IDEA: {TaskStatus.ASSIGNED, TaskStatus.FAILED},
    TaskStatus.ASSIGNED: {TaskStatus.IN_PROGRESS, TaskStatus.FAILED},
    TaskStatus.IN_PROGRESS: {TaskStatus.IMPLEMENTED, TaskStatus.FAILED},
    TaskStatus.IMPLEMENTED: {TaskStatus.TESTING},
    TaskStatus.TESTING: {TaskStatus.REVIEW, TaskStatus.FAILED},  # FAILED for dev server crashes during FI
    TaskStatus.REVIEW: {TaskStatus.ACCEPTED, TaskStatus.REJECTED},
    TaskStatus.REJECTED: {TaskStatus.ASSIGNED},
    TaskStatus.ACCEPTED: {TaskStatus.DONE},
    TaskStatus.FAILED: {TaskStatus.ASSIGNED},
}


@dataclass
class Requirement:
    id: str
    content: str
    created_at: float = 0.0


@dataclass
class Proposal:
    """HL output awaiting human review before features become tasks."""
    id: str
    requirement_id: str
    features: list[dict]  # [{"id": "feat-001", "title": ..., "depends_on": [...], "priority": N}, ...]
    summary: str = ""
    status: str = "pending"  # pending | approved | rejected
    created_at: float = 0.0


@dataclass
class Discussion:
    """A tracked discussion tree rooted at a GitHub issue."""
    root_issue: int
    title: str
    status: str = "watching"  # watching | converging | ready | submitted
    last_analysis: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class DiscussionIssue:
    """A single issue node in a discussion tree."""
    id: int = 0
    root_issue: int = 0
    issue_number: int = 0
    title: str = ""
    parent_issue: Optional[int] = None
    body: str = ""
    last_comment_id: int = 0
    snapshot: str = ""
    created_at: float = 0.0


@dataclass
class DraftComment:
    """A pending comment draft awaiting user review before posting to GitHub."""
    id: int
    root_issue: int
    target_issue: int
    body: str
    source: str = "analyst"  # analyst | head_leader
    status: str = "pending"  # pending | approved | rejected | posted
    created_at: float = 0.0


@dataclass
class DraftMessage:
    """A single message in a draft chat conversation."""
    id: int = 0
    draft_id: int = 0
    role: str = ""        # user | assistant
    content: str = ""
    created_at: float = 0.0


@dataclass
class Task:
    id: str
    title: str
    status: TaskStatus = TaskStatus.IDEA
    priority: int = 0
    depends_on: list[str] = field(default_factory=list)
    requirement_id: Optional[str] = None
    assigned_to: Optional[str] = None
    branch: Optional[str] = None
    worktree_path: Optional[str] = None
    spec_path: Optional[str] = None
    reject_reason: Optional[str] = None
    source_issue: Optional[int] = None  # GitHub issue that originated this work
    created_at: float = 0.0
    updated_at: float = 0.0
    fr_session_id: Optional[str] = None
    fail_reason: Optional[str] = None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Task:
        d = dict(row)
        d["status"] = TaskStatus(d["status"])
        d["depends_on"] = json.loads(d["depends_on"])
        return cls(**d)


class TaskQueue:
    """Async SQLite-backed task queue with state machine enforcement."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        """Apply column additions for existing DBs."""
        async with self._db.execute("PRAGMA table_info(tasks)") as cur:
            cols = {row["name"] async for row in cur}
        if "source_issue" not in cols:
            await self._db.execute("ALTER TABLE tasks ADD COLUMN source_issue INTEGER")
        if "fr_session_id" not in cols:
            await self._db.execute("ALTER TABLE tasks ADD COLUMN fr_session_id TEXT")
        if "fail_reason" not in cols:
            await self._db.execute("ALTER TABLE tasks ADD COLUMN fail_reason TEXT")
        # review_findings table
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS review_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                round INTEGER NOT NULL,
                recommendation TEXT NOT NULL,
                critical TEXT,
                important TEXT,
                report_path TEXT,
                created_at REAL,
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_findings_task ON review_findings (task_id, round)"
        )

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def add_requirement(self, req_id: str, content: str) -> Requirement:
        now = time.time()
        await self._db.execute(
            "INSERT INTO requirements (id, content, created_at) VALUES (?, ?, ?)",
            (req_id, content, now),
        )
        await self._db.commit()
        return Requirement(id=req_id, content=content, created_at=now)

    async def get_requirement(self, req_id: str) -> Optional[Requirement]:
        async with self._db.execute("SELECT * FROM requirements WHERE id = ?", (req_id,)) as cur:
            row = await cur.fetchone()
            return Requirement(**dict(row)) if row else None

    async def get_all_requirements(self) -> list[Requirement]:
        async with self._db.execute("SELECT * FROM requirements ORDER BY created_at ASC") as cur:
            return [Requirement(**dict(row)) async for row in cur]

    # ── Proposals ────────────────────────────────────────────────

    async def add_proposal(self, proposal_id: str, requirement_id: str,
                           features: list[dict], summary: str = "") -> Proposal:
        now = time.time()
        await self._db.execute(
            "INSERT INTO proposals (id, requirement_id, features, summary, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (proposal_id, requirement_id, json.dumps(features), summary, "pending", now),
        )
        await self._db.commit()
        return Proposal(id=proposal_id, requirement_id=requirement_id,
                        features=features, summary=summary, status="pending", created_at=now)

    async def get_proposal(self, proposal_id: str) -> Optional[Proposal]:
        async with self._db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            d["features"] = json.loads(d["features"])
            return Proposal(**d)

    async def get_proposals(self, status: str | None = None) -> list[Proposal]:
        if status:
            sql = "SELECT * FROM proposals WHERE status = ? ORDER BY created_at DESC"
            params = (status,)
        else:
            sql = "SELECT * FROM proposals ORDER BY created_at DESC"
            params = ()
        async with self._db.execute(sql, params) as cur:
            results = []
            async for row in cur:
                d = dict(row)
                d["features"] = json.loads(d["features"])
                results.append(Proposal(**d))
            return results

    async def update_proposal_status(self, proposal_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE proposals SET status = ? WHERE id = ?", (status, proposal_id)
        )
        await self._db.commit()

    # ── Events ──────────────────────────────────────────────────

    async def add_event(self, event: str, data: dict) -> None:
        await self._db.execute(
            "INSERT INTO events (event, data, created_at) VALUES (?, ?, ?)",
            (event, json.dumps(data, default=str), time.time()),
        )
        await self._db.commit()

    async def get_events(self, since_id: int = 0, limit: int = 100) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
            (since_id, limit),
        ) as cur:
            return [{"id": row["id"], "event": row["event"],
                     "data": json.loads(row["data"]), "created_at": row["created_at"]}
                    async for row in cur]

    async def add_task(self, task_id: str, title: str, priority: int = 0,
                       depends_on: list[str] | None = None,
                       spec_path: str | None = None,
                       requirement_id: str | None = None,
                       source_issue: int | None = None) -> Task:
        now = time.time()
        deps = depends_on or []
        await self._db.execute(
            """INSERT INTO tasks (id, title, status, priority, depends_on,
               requirement_id, spec_path, source_issue, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, title, TaskStatus.IDEA.value, priority,
             json.dumps(deps), requirement_id, spec_path, source_issue, now, now),
        )
        await self._db.commit()
        return Task(
            id=task_id, title=title, status=TaskStatus.IDEA,
            priority=priority, depends_on=deps,
            requirement_id=requirement_id, spec_path=spec_path,
            source_issue=source_issue,
            created_at=now, updated_at=now,
        )

    async def get_task(self, task_id: str) -> Optional[Task]:
        async with self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
            row = await cur.fetchone()
            return Task.from_row(row) if row else None

    async def get_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        if status:
            sql = "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC, created_at ASC"
            params = (status.value,)
        else:
            sql = "SELECT * FROM tasks ORDER BY priority DESC, created_at ASC"
            params = ()
        async with self._db.execute(sql, params) as cur:
            return [Task.from_row(row) async for row in cur]

    async def update_task_fields(self, task_id: str, **kwargs) -> Optional[Task]:
        """Update task fields without changing status. Allowed: branch, assigned_to,
        worktree_path, reject_reason."""
        sets: list[str] = ["updated_at = ?"]
        params: list = [time.time()]
        for col in ("branch", "assigned_to", "worktree_path", "reject_reason"):
            if col in kwargs:
                sets.append(f"{col} = ?")
                params.append(kwargs[col])
        if len(sets) == 1:
            return await self.get_task(task_id)
        params.append(task_id)
        await self._db.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()
        return await self.get_task(task_id)

    async def transition(self, task_id: str, new_status: TaskStatus, **kwargs) -> Task:
        """Atomically transition a task to a new status.

        Additional fields (assigned_to, branch, worktree_path, reject_reason)
        can be passed as kwargs.
        """
        task = await self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        allowed = TRANSITIONS.get(task.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid transition: {task.status.value} → {new_status.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )

        sets = ["status = ?", "updated_at = ?"]
        params: list = [new_status.value, time.time()]

        for col in ("assigned_to", "branch", "worktree_path",
                    "reject_reason", "fail_reason", "fr_session_id"):
            if col in kwargs:
                sets.append(f"{col} = ?")
                params.append(kwargs[col])

        params.append(task_id)
        await self._db.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()
        return await self.get_task(task_id)

    async def set_fr_session_id(self, task_id: str, sid: Optional[str]) -> None:
        """Persist the FR (Feature Realizer) Claude session id for a task.

        Pass `None` to clear it.
        """
        await self._db.execute(
            "UPDATE tasks SET fr_session_id = ? WHERE id = ?", (sid, task_id)
        )
        await self._db.commit()

    # ── Review Findings ─────────────────────────────────────

    async def add_review_finding(
        self,
        task_id: str,
        round: int,
        recommendation: str,
        critical: list[dict],
        important: list[dict],
        report_path: str,
    ) -> None:
        """Insert a finding row. `critical` and `important` are JSON-encoded on write."""
        await self._db.execute(
            """INSERT INTO review_findings
               (task_id, round, recommendation, critical, important, report_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, round, recommendation,
             json.dumps(critical), json.dumps(important),
             report_path, time.time()),
        )
        await self._db.commit()

    async def get_latest_review_finding(self, task_id: str) -> Optional[dict]:
        """Returns latest finding row with `critical` and `important` JSON-decoded to lists."""
        async with self._db.execute(
            "SELECT * FROM review_findings WHERE task_id = ? ORDER BY round DESC LIMIT 1",
            (task_id,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            d["critical"] = json.loads(d["critical"]) if d["critical"] else []
            d["important"] = json.loads(d["important"]) if d["important"] else []
            return d

    async def get_review_finding(self, task_id: str, round: int) -> Optional[dict]:
        """Returns specific round's finding row with `critical` and `important` JSON-decoded."""
        async with self._db.execute(
            "SELECT * FROM review_findings WHERE task_id = ? AND round = ?",
            (task_id, round),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            d["critical"] = json.loads(d["critical"]) if d["critical"] else []
            d["important"] = json.loads(d["important"]) if d["important"] else []
            return d

    async def get_tasks_for_proposal(self, proposal_id: str) -> list[Task]:
        """Return all materialized tasks for the features in a proposal.

        Returns `[]` if the proposal does not exist or has no features.
        """
        prop = await self.get_proposal(proposal_id)
        if not prop:
            return []
        feature_ids = [f["id"] for f in prop.features]
        if not feature_ids:
            return []
        placeholders = ",".join("?" * len(feature_ids))
        async with self._db.execute(
            f"SELECT * FROM tasks WHERE id IN ({placeholders})", feature_ids
        ) as cur:
            return [Task.from_row(row) async for row in cur]

    async def get_ready_tasks(self) -> list[Task]:
        """Get IDEA tasks whose dependencies are all DONE."""
        ideas = await self.get_tasks(TaskStatus.IDEA)
        if not ideas:
            return []

        # Fetch all DONE task ids
        async with self._db.execute(
            "SELECT id FROM tasks WHERE status = ?", (TaskStatus.DONE.value,)
        ) as cur:
            done_ids = {row["id"] async for row in cur}

        return [t for t in ideas if all(dep in done_ids for dep in t.depends_on)]

    async def promote_ready_tasks(self) -> list[Task]:
        """Move IDEA tasks with satisfied dependencies to ASSIGNED."""
        ready = await self.get_ready_tasks()
        promoted = []
        for task in ready:
            t = await self.transition(task.id, TaskStatus.ASSIGNED)
            promoted.append(t)
        return promoted

    async def all_tasks_summary(self) -> dict[str, int]:
        """Return count of tasks per status."""
        async with self._db.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
        ) as cur:
            return {row["status"]: row["cnt"] async for row in cur}

    # ── Discussions ─────────────────────────────────────────

    async def upsert_discussion(self, root_issue: int, title: str,
                                 status: str = "watching") -> Discussion:
        now = time.time()
        await self._db.execute(
            """INSERT INTO discussions (root_issue, title, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(root_issue) DO UPDATE SET
                 title = excluded.title, updated_at = excluded.updated_at""",
            (root_issue, title, status, now, now),
        )
        await self._db.commit()
        return Discussion(root_issue=root_issue, title=title, status=status,
                          created_at=now, updated_at=now)

    async def get_discussion(self, root_issue: int) -> Optional[Discussion]:
        async with self._db.execute(
            "SELECT * FROM discussions WHERE root_issue = ?", (root_issue,)
        ) as cur:
            row = await cur.fetchone()
            return Discussion(**dict(row)) if row else None

    async def get_discussions(self, status: Optional[str] = None) -> list[Discussion]:
        if status:
            sql = "SELECT * FROM discussions WHERE status = ? ORDER BY updated_at DESC"
            params = (status,)
        else:
            sql = "SELECT * FROM discussions ORDER BY updated_at DESC"
            params = ()
        async with self._db.execute(sql, params) as cur:
            return [Discussion(**dict(row)) async for row in cur]

    async def update_discussion(self, root_issue: int, **kwargs) -> None:
        sets = ["updated_at = ?"]
        params: list = [time.time()]
        for col in ("status", "last_analysis", "title"):
            if col in kwargs:
                sets.append(f"{col} = ?")
                params.append(kwargs[col])
        params.append(root_issue)
        await self._db.execute(
            f"UPDATE discussions SET {', '.join(sets)} WHERE root_issue = ?", params
        )
        await self._db.commit()

    async def upsert_discussion_issue(self, root_issue: int, issue_number: int,
                                       title: str, parent_issue: Optional[int] = None,
                                       body: str = "") -> DiscussionIssue:
        now = time.time()
        await self._db.execute(
            """INSERT INTO discussion_issues
               (root_issue, issue_number, parent_issue, title, body, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(issue_number) DO UPDATE SET
                 title = excluded.title, body = excluded.body""",
            (root_issue, issue_number, parent_issue, title, body, now),
        )
        await self._db.commit()
        return DiscussionIssue(root_issue=root_issue, issue_number=issue_number,
                                title=title, parent_issue=parent_issue,
                                body=body, created_at=now)

    async def get_discussion_issues(self, root_issue: int) -> list[DiscussionIssue]:
        async with self._db.execute(
            "SELECT * FROM discussion_issues WHERE root_issue = ? ORDER BY issue_number",
            (root_issue,),
        ) as cur:
            return [DiscussionIssue(**dict(row)) async for row in cur]

    async def update_discussion_issue(self, issue_number: int, **kwargs) -> None:
        sets = []
        params: list = []
        for col in ("last_comment_id", "snapshot", "body"):
            if col in kwargs:
                sets.append(f"{col} = ?")
                params.append(kwargs[col])
        if not sets:
            return
        params.append(issue_number)
        await self._db.execute(
            f"UPDATE discussion_issues SET {', '.join(sets)} WHERE issue_number = ?", params
        )
        await self._db.commit()

    # ── Draft Comments ──────────────────────────────────────

    async def add_draft_comment(self, root_issue: int, target_issue: int,
                                 body: str, source: str = "analyst") -> DraftComment:
        now = time.time()
        async with self._db.execute(
            """INSERT INTO draft_comments (root_issue, target_issue, body, source, status, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (root_issue, target_issue, body, source, now),
        ) as cur:
            draft_id = cur.lastrowid
        await self._db.commit()
        return DraftComment(id=draft_id, root_issue=root_issue,
                            target_issue=target_issue, body=body,
                            source=source, created_at=now)

    async def get_draft_comments(self, status: str = "pending") -> list[DraftComment]:
        async with self._db.execute(
            "SELECT * FROM draft_comments WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ) as cur:
            return [DraftComment(**dict(row)) async for row in cur]

    async def get_draft_comment(self, draft_id: int) -> Optional[DraftComment]:
        async with self._db.execute(
            "SELECT * FROM draft_comments WHERE id = ?", (draft_id,),
        ) as cur:
            row = await cur.fetchone()
            return DraftComment(**dict(row)) if row else None

    async def update_draft_status(self, draft_id: int, status: str) -> None:
        await self._db.execute(
            "UPDATE draft_comments SET status = ? WHERE id = ?", (status, draft_id),
        )
        await self._db.commit()

    async def update_draft_body(self, draft_id: int, body: str) -> None:
        await self._db.execute(
            "UPDATE draft_comments SET body = ? WHERE id = ?", (body, draft_id),
        )
        await self._db.commit()

    # ── Draft Messages ──────────────────────────────────────

    async def add_draft_message(self, draft_id: int, role: str,
                                 content: str) -> DraftMessage:
        now = time.time()
        async with self._db.execute(
            "INSERT INTO draft_messages (draft_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (draft_id, role, content, now),
        ) as cur:
            msg_id = cur.lastrowid
        await self._db.commit()
        return DraftMessage(id=msg_id, draft_id=draft_id, role=role,
                            content=content, created_at=now)

    async def get_draft_messages(self, draft_id: int) -> list[DraftMessage]:
        async with self._db.execute(
            "SELECT * FROM draft_messages WHERE draft_id = ? ORDER BY created_at ASC",
            (draft_id,),
        ) as cur:
            return [DraftMessage(**dict(row)) async for row in cur]
