"""
Tracks GitHub discussion trees and dispatches DiscussionAnalyst agents.

A "discuss"-labeled issue is the root of a discussion tree. As people discuss,
they may spawn child issues for specific topics. This tracker:

1. Discovers root issues by label
2. Crawls cross-references to build a discussion tree
3. Incrementally detects new comments
4. Builds a full tree context for the DiscussionAnalyst agent
5. Posts agent analysis as comments on the appropriate issues
6. Detects when discussions mature into implementable requirements
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, Awaitable

from .github_manager import GitHubManager
from .agent_spawner import AgentSpawner, AgentRole
from .task_queue import TaskQueue, Discussion, DiscussionIssue, DraftComment

log = logging.getLogger(__name__)

RESULT_PATTERN = re.compile(r"ORCHESTRA_RESULT:({.*})")

# Patterns that indicate an issue was spawned/split from another
SPAWN_PATTERNS = re.compile(
    r'(?:split|拆分|moved to|转移到|see also|详见|created|新建|follow.up|后续)\s+#(\d+)',
    re.IGNORECASE,
)


@dataclass
class WatchConfig:
    watch_labels: list[str] = field(default_factory=lambda: ["discuss"])
    focus_issues: list[int] = field(default_factory=list)  # specific issue numbers to track
    poll_interval: int = 120
    auto_submit: bool = False
    max_depth: int = 3
    max_issues_per_tree: int = 15
    ready_label: str = "orchestra-ready"


@dataclass
class TrackedNode:
    """In-memory representation of a discussion tree node."""
    issue_number: int
    title: str
    body: str = ""
    parent_issue: Optional[int] = None
    comments: list[dict] = field(default_factory=list)
    last_comment_id: int = 0
    snapshot: str = ""


@dataclass
class DiscussionTree:
    """In-memory representation of a full discussion tree."""
    root_issue: int
    title: str
    status: str = "watching"
    nodes: dict[int, TrackedNode] = field(default_factory=dict)
    last_analysis: str = ""

    @property
    def all_issue_numbers(self) -> list[int]:
        return sorted(self.nodes.keys())


class IssueTracker:
    """Tracks GitHub discussion trees, dispatches DiscussionAnalyst agents."""

    def __init__(
        self,
        github: GitHubManager,
        spawner: AgentSpawner,
        task_queue: TaskQueue,
        project_dir: Path,
        orchestra_dir: Path,
        config: WatchConfig,
        on_event: Optional[Callable[[str, dict], Awaitable[None]]] = None,
        on_ready: Optional[Callable[[DiscussionTree, str], Awaitable[None]]] = None,
    ):
        self.github = github
        self.spawner = spawner
        self.task_queue = task_queue
        self.project_dir = project_dir
        self.orchestra_dir = orchestra_dir
        self.config = config
        self._on_event = on_event
        self._on_ready = on_ready
        self._trees: dict[int, DiscussionTree] = {}
        self._stop = False
        self._analyzing: set[int] = set()  # root issues currently being analyzed
        self._lock = asyncio.Lock()  # protects _trees mutations

    async def _emit(self, event: str, data: dict):
        log.info("Event: %s %s", event, json.dumps(data, default=str))
        await self.task_queue.add_event(event, data)
        if self._on_event:
            await self._on_event(event, data)

    # ── Main Loop ──────────────────────────────────────────

    async def run(self):
        log.info("IssueTracker started, watching labels: %s", self.config.watch_labels)
        self._stop = False

        # Restore persisted discussion trees from DB
        await self._restore_from_db()

        while not self._stop:
            try:
                await self._poll_cycle()
            except Exception:
                log.exception("IssueTracker poll error")
            await asyncio.sleep(self.config.poll_interval)

    def stop(self):
        self._stop = True

    async def _restore_from_db(self):
        """Load previously tracked discussions from DB on startup."""
        discussions = await self.task_queue.get_discussions()
        for disc in discussions:
            if disc.status == "submitted":
                continue
            tree = DiscussionTree(
                root_issue=disc.root_issue,
                title=disc.title,
                status=disc.status,
                last_analysis=disc.last_analysis,
            )
            db_issues = await self.task_queue.get_discussion_issues(disc.root_issue)
            for di in db_issues:
                tree.nodes[di.issue_number] = TrackedNode(
                    issue_number=di.issue_number,
                    title=di.title,
                    body=di.body,
                    parent_issue=di.parent_issue,
                    # Reset to 0 so first poll re-fetches all comments
                    # (comments aren't persisted, only count was)
                    last_comment_id=0,
                    snapshot=di.snapshot,
                )
            self._trees[disc.root_issue] = tree
            log.info("Restored discussion tree #%d (%d nodes)", disc.root_issue, len(tree.nodes))

    # ── Poll Cycle ─────────────────────────────────────────

    async def _poll_cycle(self):
        to_analyze: list[DiscussionTree] = []

        async with self._lock:
            # 1. Discover new root issues
            new_roots = await self._discover_roots()

            # 2. For each active tree: fetch new comments, decide if analysis needed
            for root_num, tree in list(self._trees.items()):
                if tree.status == "submitted":
                    continue
                if root_num in self._analyzing:
                    continue

                has_new = await self._fetch_new_comments(tree)

                if has_new:
                    await self._crawl_children(tree)

                if has_new or root_num in new_roots or not tree.last_analysis:
                    to_analyze.append(tree)

        # Run analysis outside the lock (long-running)
        for tree in to_analyze:
            await self._safe_analyze(tree)

    # ── Phase 1: Discover root issues ──────────────────────

    async def _register_root(self, num: int, title: str, body: str = "") -> bool:
        """Register a single issue as a discussion tree root. Returns True if new."""
        if num in self._trees:
            return False
        tree = DiscussionTree(root_issue=num, title=title)
        tree.nodes[num] = TrackedNode(
            issue_number=num, title=title, body=body,
        )
        self._trees[num] = tree

        await self.task_queue.upsert_discussion(num, title)
        await self.task_queue.upsert_discussion_issue(num, num, title, body=body)

        log.info("Discovered discussion root: #%d %s", num, title)
        await self._emit("discussion_discovered", {
            "issue_number": num, "title": title,
        })
        return True

    async def _discover_roots(self) -> set[int]:
        """Discover root issues. Returns set of newly discovered root issue numbers."""
        new_roots: set[int] = set()

        # By label
        for label in self.config.watch_labels:
            issues = await self.github.list_issues_by_label(label)
            for issue in issues:
                if await self._register_root(
                    issue["number"], issue["title"], issue.get("body", ""),
                ):
                    new_roots.add(issue["number"])

        # By explicit focus issue numbers
        for num in self.config.focus_issues:
            if num in self._trees:
                continue
            issue = await self.github.get_issue(num)
            if issue:
                if await self._register_root(
                    num, issue["title"], issue.get("body", ""),
                ):
                    new_roots.add(num)

        return new_roots

    # ── Phase 2: Crawl child issues ────────────────────────

    async def _crawl_children(self, tree: DiscussionTree, depth: int = 0):
        """BFS to discover child issues via cross-references and #N mentions."""
        if depth >= self.config.max_depth:
            return
        if len(tree.nodes) >= self.config.max_issues_per_tree:
            return

        new_refs: list[tuple[int, int]] = []  # (child_num, parent_num)

        for num, node in list(tree.nodes.items()):
            # From timeline cross-references
            timeline = await self.github.get_issue_timeline(num)
            for event in timeline:
                if event.get("event") == "cross-referenced":
                    source = event.get("source", {}).get("issue", {})
                    ref_num = source.get("number")
                    if ref_num and ref_num not in tree.nodes:
                        new_refs.append((ref_num, num))

            # From spawn patterns in body + comments
            all_text = node.body + " " + " ".join(
                c.get("body", "") for c in node.comments
            )
            for match in SPAWN_PATTERNS.finditer(all_text):
                ref_num = int(match.group(1))
                if ref_num not in tree.nodes:
                    new_refs.append((ref_num, num))

        # Fetch and register new children
        for ref_num, parent_num in new_refs:
            if len(tree.nodes) >= self.config.max_issues_per_tree:
                break
            if ref_num in tree.nodes:
                continue

            issue = await self.github.get_issue(ref_num)
            if not issue or issue.get("state") == "CLOSED":
                continue

            tree.nodes[ref_num] = TrackedNode(
                issue_number=ref_num,
                title=issue["title"],
                body=issue.get("body", ""),
                parent_issue=parent_num,
            )

            # Persist
            await self.task_queue.upsert_discussion_issue(
                tree.root_issue, ref_num, issue["title"],
                parent_issue=parent_num, body=issue.get("body", ""),
            )

            log.info("Found child #%d (parent #%d) in tree #%d",
                     ref_num, parent_num, tree.root_issue)
            await self._emit("discussion_child_found", {
                "root": tree.root_issue,
                "child": ref_num,
                "parent": parent_num,
                "title": issue["title"],
            })

        # Recurse for newly discovered nodes
        if new_refs:
            await self._crawl_children(tree, depth + 1)

    # ── Phase 3: Incremental comment fetching ──────────────

    async def _fetch_new_comments(self, tree: DiscussionTree) -> bool:
        """Fetch new comments for all issues in tree. Returns True if any new."""
        has_new = False

        for num, node in tree.nodes.items():
            comments = await self.github.get_issue_comments(num)
            if not comments:
                continue

            # Use count-based tracking — last_comment_id stores the count we last saw
            old_count = node.last_comment_id  # repurposed as last seen count
            if len(comments) > old_count:
                new_comments = comments[old_count:]
                node.comments.extend(new_comments)
                node.last_comment_id = len(comments)
                has_new = True

                await self.task_queue.update_discussion_issue(
                    num, last_comment_id=node.last_comment_id,
                )
                log.info("Fetched %d new comments on #%d", len(new_comments), num)

        return has_new

    # ── Phase 4: Build tree context for agent ──────────────

    def _build_tree_context(self, tree: DiscussionTree) -> str:
        """Serialize the entire discussion tree as structured markdown."""
        lines = [
            f"# Discussion Tree: #{tree.root_issue} — {tree.title}",
            f"Total issues tracked: {len(tree.nodes)}",
            f"Current status: {tree.status}",
            "",
        ]

        if tree.last_analysis:
            lines.append("## Previous Analysis Summary")
            lines.append(tree.last_analysis)
            lines.append("")

        # Tree structure overview
        lines.append("## Issue Graph")
        for num in sorted(tree.nodes):
            node = tree.nodes[num]
            parent = f" (from #{node.parent_issue})" if node.parent_issue else " (ROOT)"
            comment_count = len(node.comments)
            lines.append(f"- #{num}: {node.title}{parent} [{comment_count} comments]")
        lines.append("")

        # Full content of each issue (with cycle protection)
        visited = set()

        def render_node(num: int, indent: int = 0):
            if num in visited:
                return  # break cycles
            visited.add(num)
            node = tree.nodes[num]
            pfx = "  " * indent
            is_root = num == tree.root_issue
            lines.append(f"\n{pfx}## {'Root: ' if is_root else ''}Issue #{num}: {node.title}")
            if node.parent_issue:
                lines.append(f"{pfx}*Spawned from #{node.parent_issue}*")

            lines.append(f"{pfx}### Body")
            lines.append(f"{pfx}{node.body[:3000]}")

            if node.comments:
                # Filter out bot's own comments to avoid self-referential loop
                human_comments = [
                    c for c in node.comments
                    if "Orchestra Discussion Analyst" not in c.get("body", "")
                ]
                if human_comments:
                    lines.append(f"{pfx}### Comments ({len(human_comments)})")
                    for c in human_comments:
                        author = c.get("author", {}).get("login", "unknown")
                        body = c.get("body", "")[:1000]
                        lines.append(f"{pfx}- **@{author}**: {body}")

            # Render children
            children = [n for n, nd in tree.nodes.items() if nd.parent_issue == num]
            for child in sorted(children):
                render_node(child, indent + 1)

        render_node(tree.root_issue)

        # Also render orphan nodes not reached from root
        for num in sorted(tree.nodes):
            if num not in visited:
                render_node(num, indent=0)

        return "\n".join(lines)

    # ── Phase 5: Dispatch DiscussionAnalyst ────────────────

    async def _safe_analyze(self, tree: DiscussionTree):
        """Guard against concurrent analysis of the same tree."""
        if tree.root_issue in self._analyzing:
            log.info("Skipping analysis for #%d — already in progress", tree.root_issue)
            return
        self._analyzing.add(tree.root_issue)
        try:
            await self._analyze_tree(tree)
        finally:
            self._analyzing.discard(tree.root_issue)

    async def _analyze_tree(self, tree: DiscussionTree):
        """Spawn a DiscussionAnalyst to analyze the tree and post comments."""
        context = self._build_tree_context(tree)

        await self._emit("discussion_analyzing", {
            "root": tree.root_issue,
            "node_count": len(tree.nodes),
        })

        # Load prompt
        prompt_file = Path(__file__).parent.parent / "prompts" / "discussion_analyst.md"
        system_prompt = prompt_file.read_text()

        # Inject project context
        from .context_manager import ContextManager
        ctx = ContextManager(self.orchestra_dir)

        arch = "(not yet created)"
        if ctx.context_dir.joinpath("architecture.md").exists():
            arch = ctx.context_dir.joinpath("architecture.md").read_text()
        conv = "(not yet created)"
        if ctx.context_dir.joinpath("conventions.md").exists():
            conv = ctx.context_dir.joinpath("conventions.md").read_text()

        contracts = []
        if ctx.contracts_dir.is_dir():
            for f in sorted(ctx.contracts_dir.iterdir()):
                if f.is_file():
                    contracts.append(f"### {f.name}\n{f.read_text()}")

        system_prompt = system_prompt.replace("{architecture_content}", arch)
        system_prompt = system_prompt.replace("{conventions_content}", conv)
        system_prompt = system_prompt.replace(
            "{contracts_content}",
            "\n\n".join(contracts) if contracts else "(none)",
        )

        handle = await self.spawner.spawn(
            role=AgentRole.DISCUSSION_ANALYST,
            system_prompt=system_prompt,
            task_prompt=context,
            cwd=self.project_dir,
            log_path=self.orchestra_dir / "logs" / f"da-tree-{tree.root_issue}.log",
        )

        result = await self.spawner.wait(handle)
        parsed = self._parse_result(result.stdout)

        if not parsed:
            # Agent didn't output structured JSON — treat entire output as a comment draft
            text = result.stdout.strip()
            if text:
                log.info("DA produced unstructured output for #%d, saving as draft", tree.root_issue)
                await self.task_queue.add_draft_comment(
                    root_issue=tree.root_issue,
                    target_issue=tree.root_issue,
                    body=text,
                    source="analyst",
                )
                tree.last_analysis = text[:500]
                await self.task_queue.update_discussion(
                    tree.root_issue, last_analysis=tree.last_analysis,
                )
                await self._emit("draft_comment_created", {
                    "draft_id": 0,
                    "root": tree.root_issue,
                    "target_issue": tree.root_issue,
                    "source": "analyst",
                    "body_preview": text[:200],
                })
            else:
                log.warning("DiscussionAnalyst no output for tree #%d", tree.root_issue)
            return

        await self._handle_result(tree, parsed)

    # ── Phase 6: Handle agent result ───────────────────────

    async def _handle_result(self, tree: DiscussionTree, parsed: dict):
        """Process DiscussionAnalyst output: save drafts for review, update snapshots."""

        # Save comments as drafts for user review (not posted directly)
        for action in parsed.get("comments", []):
            target = action.get("issue_number", tree.root_issue)
            body = action.get("body", "")
            if not body:
                continue
            draft = await self.task_queue.add_draft_comment(
                root_issue=tree.root_issue,
                target_issue=target,
                body=body,
                source="analyst",
            )
            await self._emit("draft_comment_created", {
                "draft_id": draft.id,
                "root": tree.root_issue,
                "target_issue": target,
                "source": "analyst",
                "body_preview": body[:200],
            })

        # Update snapshots for each issue
        for snap in parsed.get("snapshots", []):
            issue_num = snap.get("issue_number")
            summary = snap.get("summary", "")
            if issue_num and issue_num in tree.nodes:
                tree.nodes[issue_num].snapshot = summary
                await self.task_queue.update_discussion_issue(
                    issue_num, snapshot=summary,
                )

        # Save overall analysis summary
        overall_summary = parsed.get("summary", "")
        if overall_summary:
            tree.last_analysis = overall_summary
            await self.task_queue.update_discussion(
                tree.root_issue, last_analysis=overall_summary,
            )

        # Update maturity status
        maturity = parsed.get("maturity", tree.status)
        if maturity != tree.status:
            old_status = tree.status
            tree.status = maturity
            await self.task_queue.update_discussion(tree.root_issue, status=maturity)
            await self._emit("discussion_status_changed", {
                "root": tree.root_issue,
                "old_status": old_status,
                "new_status": maturity,
            })

        # If ready, extract requirement and trigger callback
        if maturity == "ready":
            requirement = parsed.get("requirement", "")
            if requirement:
                await self.github.add_label(tree.root_issue, self.config.ready_label)
                if self._on_ready:
                    await self._on_ready(tree, requirement)
                await self._emit("discussion_ready", {
                    "root": tree.root_issue,
                    "title": tree.title,
                    "issue_count": len(tree.nodes),
                })

    # ── Public API ─────────────────────────────────────────

    def get_trees(self) -> dict[int, DiscussionTree]:
        return dict(self._trees)

    def get_tree(self, root_issue: int) -> Optional[DiscussionTree]:
        return self._trees.get(root_issue)

    async def post_approved_draft(self, draft_id: int) -> bool:
        """Post an approved draft comment to GitHub."""
        draft = await self.task_queue.get_draft_comment(draft_id)
        if not draft or draft.status != "pending":
            return False
        body = draft.body + "\n\n---\n*Orchestra Discussion Analyst*"
        ok = await self.github.post_issue_comment(draft.target_issue, body)
        if ok:
            await self.task_queue.update_draft_status(draft_id, "posted")
            await self._emit("discussion_commented", {
                "root": draft.root_issue,
                "target_issue": draft.target_issue,
                "draft_id": draft_id,
            })
        return ok

    async def analyze_now(self, issue_number: int) -> None:
        """Immediately analyze a specific issue (called when user adds a focus issue)."""
        async with self._lock:
            # Register as root if not already tracked
            if issue_number not in self._trees:
                issue = await self.github.get_issue(issue_number)
                if issue:
                    await self._register_root(
                        issue_number, issue["title"], issue.get("body", ""),
                    )

            tree = self._trees.get(issue_number)
            if not tree:
                return

            await self._crawl_children(tree)
            await self._fetch_new_comments(tree)

        # Run analysis outside the lock (it's long-running)
        await self._safe_analyze(tree)

    @staticmethod
    def _parse_result(output: str) -> Optional[dict]:
        for line in reversed(output.splitlines()):
            m = RESULT_PATTERN.search(line)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    pass
        return None
