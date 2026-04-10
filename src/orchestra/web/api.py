"""FastAPI web server for Orchestra dashboard."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core.orchestrator import Orchestrator, OrchestraConfig
from ..core.task_queue import TaskStatus
from ..core.issue_tracker import WatchConfig

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Orchestra Dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

app.add_middleware(NoCacheMiddleware)

# Will be set by the launcher (or lazily via /api/init)
_orchestrator: Optional[Orchestrator] = None
_orchestrator_task: Optional[asyncio.Task] = None  # the run_loop task


def set_orchestrator(orch: Orchestrator) -> None:
    global _orchestrator
    _orchestrator = orch


def _orch() -> Orchestrator:
    if _orchestrator is None:
        raise HTTPException(503, "Orchestrator not initialized")
    return _orchestrator


def _is_initialized() -> bool:
    return _orchestrator is not None


# ── Models ──────────────────────────────────────────────────────

class SubmitRequest(BaseModel):
    requirement: str


class ReviewAction(BaseModel):
    action: str  # "accept" or "reject"
    reason: str = ""


class ProposalAction(BaseModel):
    action: str  # "approve" or "reject"
    feature_ids: list[str] | None = None  # approve subset, or None = all


class InitRequest(BaseModel):
    project_path: str


# ── Routes ──────────────────────────────────────────────────────

@app.get("/api/status")
async def get_init_status():
    """Check if orchestrator is initialized and with which project."""
    if _is_initialized():
        return {
            "initialized": True,
            "project_path": str(_orchestrator.config.project_dir),
            "auto_accept": _orchestrator.config.auto_accept,
        }
    return {"initialized": False, "project_path": None}


@app.get("/api/browse")
async def browse_directory(path: str = "~"):
    """List directories at a given path for the project selector."""
    try:
        target = Path(path).expanduser().resolve()
    except Exception:
        raise HTTPException(400, "Invalid path")

    if not target.is_dir():
        raise HTTPException(400, f"Not a directory: {target}")

    entries = []
    try:
        for entry in sorted(target.iterdir()):
            if entry.name.startswith('.'):
                continue
            if entry.is_dir():
                is_git = (entry / ".git").exists()
                has_orchestra = (entry / ".orchestra").exists()
                entries.append({
                    "name": entry.name,
                    "path": str(entry),
                    "is_git": is_git,
                    "has_orchestra": has_orchestra,
                })
    except PermissionError:
        raise HTTPException(403, "Permission denied")

    return {
        "current": str(target),
        "parent": str(target.parent),
        "entries": entries,
    }


@app.post("/api/auto-accept")
async def toggle_auto_accept():
    """Toggle pass_whatever mode."""
    orch = _orch()
    orch.config.auto_accept = not orch.config.auto_accept
    state = orch.config.auto_accept
    await orch._emit("auto_accept_toggled", {"enabled": state})
    return {"auto_accept": state}


@app.post("/api/disconnect")
async def disconnect_project():
    """Disconnect from the current project and return to setup screen."""
    global _orchestrator, _orchestrator_task

    if _orchestrator:
        _orchestrator.stop_tracking()
        _orchestrator.stop()
        await _orchestrator.close()

    if _orchestrator_task and not _orchestrator_task.done():
        _orchestrator_task.cancel()

    _orchestrator = None
    _orchestrator_task = None

    return {"status": "disconnected"}


@app.post("/api/init")
async def init_project(req: InitRequest):
    """Initialize orchestra on a project directory (called from the web setup screen)."""
    global _orchestrator, _orchestrator_task

    project_dir = Path(req.project_path).expanduser().resolve()
    if not project_dir.is_dir():
        raise HTTPException(400, f"Directory does not exist: {project_dir}")

    orchestra_dir = project_dir / ".orchestra"
    config = OrchestraConfig(
        project_dir=project_dir,
        orchestra_dir=orchestra_dir,
    )

    orch = Orchestrator(config)
    await orch.init()
    set_orchestrator(orch)

    # Start the orchestrator loop
    _orchestrator_task = asyncio.create_task(orch.run_loop())

    return {
        "status": "initialized",
        "project_path": str(project_dir),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text()


@app.get("/api/graph")
async def get_graph():
    """Return the full DAG: requirements → tasks with deps and status."""
    orch = _orch()
    tasks = await orch.task_queue.get_tasks()
    reqs = await orch.task_queue.get_all_requirements()

    # Build nodes
    nodes = []
    for req in reqs:
        nodes.append({
            "id": req.id,
            "type": "requirement",
            "label": req.content[:80] + ("..." if len(req.content) > 80 else ""),
            "content": req.content,
            "created_at": req.created_at,
        })

    for task in tasks:
        nodes.append({
            "id": task.id,
            "type": "task",
            "label": task.title,
            "status": task.status.value,
            "priority": task.priority,
            "requirement_id": task.requirement_id,
            "depends_on": task.depends_on,
            "assigned_to": task.assigned_to,
            "branch": task.branch,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        })

    # Build edges: requirement → task, and task → task (dependencies)
    edges = []
    for task in tasks:
        if task.requirement_id:
            edges.append({"from": task.requirement_id, "to": task.id, "type": "origin"})
        for dep in task.depends_on:
            edges.append({"from": dep, "to": task.id, "type": "dependency"})

    # Include pending proposals as dashed/preview nodes
    proposals = await orch.task_queue.get_proposals(status="pending")
    for prop in proposals:
        for feat in prop.features:
            nodes.append({
                "id": feat["id"],
                "type": "proposal_feature",
                "label": feat.get("title", feat["id"]),
                "proposal_id": prop.id,
                "requirement_id": prop.requirement_id,
                "depends_on": feat.get("depends_on", []),
            })
            # Edge from requirement to proposed feature
            edges.append({"from": prop.requirement_id, "to": feat["id"], "type": "proposal"})
            for dep in feat.get("depends_on", []):
                edges.append({"from": dep, "to": feat["id"], "type": "proposal_dep"})

    # Include repo branches (non-feat/ branches)
    branches = await orch.worktree.list_branches()

    # Git commit log for the graph
    commits = await orch.worktree.get_log_graph(max_count=40)

    return {"nodes": nodes, "edges": edges, "branches": branches, "commits": commits,
            "proposals": [
                {"id": p.id, "requirement_id": p.requirement_id, "summary": p.summary,
                 "status": p.status, "features": p.features, "created_at": p.created_at}
                for p in proposals
            ]}


@app.get("/api/issues")
async def get_issues(state: str = "open", label: Optional[str] = None):
    """Fetch GitHub issues for the connected project."""
    orch = _orch()
    issues = await orch.github.list_issues(state=state, labels=label or "")
    # Normalize for frontend
    return [
        {
            "number": i.get("number"),
            "title": i.get("title", ""),
            "url": i.get("url", ""),
            "state": i.get("state", "open"),
            "labels": [lb.get("name", "") for lb in i.get("labels", [])],
            "author": i.get("author", {}).get("login", "unknown"),
            "comment_count": len(i.get("comments", [])),
            "created_at": i.get("createdAt", ""),
            "updated_at": i.get("updatedAt", ""),
            "body_preview": (i.get("body") or "")[:200],
        }
        for i in issues
    ]


@app.get("/api/branches")
async def get_branches():
    orch = _orch()
    return await orch.worktree.list_branches()


@app.get("/api/proposals")
async def get_proposals(status: Optional[str] = None):
    orch = _orch()
    proposals = await orch.task_queue.get_proposals(status)
    result = []
    for p in proposals:
        req = await orch.task_queue.get_requirement(p.requirement_id)
        result.append({
            "id": p.id, "requirement_id": p.requirement_id,
            "requirement_content": req.content if req else "",
            "features": p.features, "status": p.status,
            "created_at": p.created_at,
        })
    return result


@app.get("/api/proposals/{proposal_id}")
async def get_proposal(proposal_id: str):
    orch = _orch()
    p = await orch.task_queue.get_proposal(proposal_id)
    if not p:
        raise HTTPException(404, f"Proposal {proposal_id} not found")
    req = await orch.task_queue.get_requirement(p.requirement_id)

    # Read spec files if HL already wrote them
    features_with_specs = []
    for feat in p.features:
        spec = orch.context.read_spec(feat["id"])
        features_with_specs.append({**feat, "spec": spec})

    return {
        "id": p.id, "requirement_id": p.requirement_id,
        "requirement_content": req.content if req else "",
        "features": features_with_specs, "status": p.status,
        "created_at": p.created_at,
    }


@app.post("/api/proposals/{proposal_id}/review")
async def review_proposal(proposal_id: str, action: ProposalAction):
    orch = _orch()
    p = await orch.task_queue.get_proposal(proposal_id)
    if not p:
        raise HTTPException(404, f"Proposal {proposal_id} not found")
    if p.status != "pending":
        raise HTTPException(400, f"Proposal is already {p.status}")

    if action.action == "approve":
        tasks = await orch.approve_proposal(proposal_id, action.feature_ids)
        return {"status": "approved", "tasks_created": len(tasks),
                "task_ids": [t.id for t in tasks]}
    elif action.action == "reject":
        await orch.reject_proposal(proposal_id)
        return {"status": "rejected"}
    else:
        raise HTTPException(400, f"Unknown action: {action.action}")


@app.get("/api/tasks")
async def get_tasks(status: Optional[str] = None):
    orch = _orch()
    if status:
        tasks = await orch.task_queue.get_tasks(TaskStatus(status))
    else:
        tasks = await orch.task_queue.get_tasks()
    return [
        {
            "id": t.id, "title": t.title, "status": t.status.value,
            "priority": t.priority, "depends_on": t.depends_on,
            "requirement_id": t.requirement_id,
            "assigned_to": t.assigned_to, "branch": t.branch,
            "reject_reason": t.reject_reason,
            "created_at": t.created_at, "updated_at": t.updated_at,
        }
        for t in tasks
    ]


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    orch = _orch()
    t = await orch.task_queue.get_task(task_id)
    if not t:
        raise HTTPException(404, f"Task {task_id} not found")

    spec = orch.context.read_spec(task_id)
    report = orch.context.read_report(task_id)

    # Read the requirement
    req = None
    if t.requirement_id:
        r = await orch.task_queue.get_requirement(t.requirement_id)
        if r:
            req = {"id": r.id, "content": r.content}

    return {
        "id": t.id, "title": t.title, "status": t.status.value,
        "priority": t.priority, "depends_on": t.depends_on,
        "requirement_id": t.requirement_id,
        "requirement": req,
        "assigned_to": t.assigned_to, "branch": t.branch,
        "reject_reason": t.reject_reason,
        "spec": spec,
        "report": report,
        "created_at": t.created_at, "updated_at": t.updated_at,
    }


@app.get("/api/agents")
async def get_agents():
    orch = _orch()
    # Return all agents (not just running) so UI can show finished/failed too
    all_agents = orch.spawner.get_all()
    return [
        {
            "agent_id": h.agent_id,
            "role": h.role.value,
            "task_id": h.task_id,
            "state": h.state.value,
            "elapsed": time.time() - h.started_at if h.started_at else 0,
            "log_line_count": len(h.log_lines),
        }
        for h in all_agents
    ]


@app.get("/api/agents/{agent_id}/logs")
async def get_agent_logs(agent_id: str, offset: int = 0):
    """Get buffered log lines for a specific agent."""
    orch = _orch()
    handle = orch.spawner.get_agent(agent_id)
    if not handle:
        raise HTTPException(404, f"Agent {agent_id} not found")
    lines = list(handle.log_lines)
    return {
        "agent_id": agent_id,
        "state": handle.state.value,
        "total": len(lines),
        "lines": lines[offset:],
    }


@app.get("/api/summary")
async def get_summary():
    orch = _orch()
    summary = await orch.task_queue.all_tasks_summary()
    total = sum(summary.values())
    return {"by_status": summary, "total": total}


@app.post("/api/submit")
async def submit_requirement(req: SubmitRequest):
    orch = _orch()
    # Run in background so the HTTP response returns immediately
    asyncio.create_task(orch.submit_requirement(req.requirement))
    return {"status": "submitted", "message": "Head Leader is processing..."}


@app.post("/api/tasks/{task_id}/review")
async def review_task(task_id: str, action: ReviewAction):
    orch = _orch()
    t = await orch.task_queue.get_task(task_id)
    if not t:
        raise HTTPException(404, f"Task {task_id} not found")
    if t.status != TaskStatus.REVIEW:
        raise HTTPException(400, f"Task {task_id} is not in REVIEW status")

    if action.action == "accept":
        await orch.accept_task(task_id)
        return {"status": "accepted"}
    elif action.action == "reject":
        await orch.reject_task(task_id, action.reason)
        return {"status": "rejected"}
    else:
        raise HTTPException(400, f"Unknown action: {action.action}")


# ── Discussion Tracking ─────────────────────────────────────

class WatchRequest(BaseModel):
    labels: list[str] = ["discuss"]
    focus_issues: list[int] = []
    poll_interval: int = 120
    auto_submit: bool = False


@app.post("/api/tracking/start")
async def start_tracking(req: WatchRequest):
    """Start watching GitHub issues for discussions."""
    orch = _orch()
    config = WatchConfig(
        watch_labels=req.labels,
        focus_issues=req.focus_issues,
        poll_interval=req.poll_interval,
        auto_submit=req.auto_submit,
    )
    await orch.start_tracking(config)
    return {"status": "tracking", "labels": req.labels, "focus_issues": req.focus_issues}


@app.post("/api/tracking/stop")
async def stop_tracking():
    """Stop the issue tracker."""
    orch = _orch()
    orch.stop_tracking()
    return {"status": "stopped"}


@app.get("/api/tracking/status")
async def tracking_status():
    """Get current tracking status."""
    orch = _orch()
    if not orch.tracker:
        return {"active": False}
    return {
        "active": True,
        "labels": orch.tracker.config.watch_labels,
        "focus_issues": orch.tracker.config.focus_issues,
        "tree_count": len(orch.tracker.get_trees()),
    }


class LabelsUpdate(BaseModel):
    labels: list[str]


class FocusIssuesUpdate(BaseModel):
    issues: list[int]


@app.put("/api/tracking/labels")
async def update_tracking_labels(req: LabelsUpdate):
    """Update the labels the tracker watches."""
    orch = _orch()
    labels = [l.strip() for l in req.labels if l.strip()]
    if not labels:
        raise HTTPException(400, "At least one label is required")

    if orch.tracker:
        orch.tracker.config.watch_labels = labels
        return {"status": "updated", "labels": labels, "active": True}
    else:
        return {"status": "saved", "labels": labels, "active": False}


@app.put("/api/tracking/focus")
async def update_focus_issues(req: FocusIssuesUpdate):
    """Set specific issue numbers for the tracker to focus on."""
    orch = _orch()
    issues = sorted(set(req.issues))
    if orch.tracker:
        orch.tracker.config.focus_issues = issues
        return {"status": "updated", "issues": issues, "active": True}
    return {"status": "saved", "issues": issues, "active": False}


@app.get("/api/tracking/focus")
async def get_focus_issues():
    """Get current focus issue numbers."""
    if not _is_initialized():
        return {"issues": []}
    orch = _orch()
    if orch.tracker:
        return {"issues": orch.tracker.config.focus_issues}
    return {"issues": []}


@app.get("/api/tracking/labels")
async def get_tracking_labels():
    """Get the current watch labels."""
    if not _is_initialized():
        return {"labels": ["discuss"]}
    orch = _orch()
    if orch.tracker:
        return {"labels": orch.tracker.config.watch_labels}
    return {"labels": ["discuss"]}


@app.get("/api/discussions")
async def get_discussions():
    """Get all tracked discussion trees."""
    orch = _orch()
    if not orch.tracker:
        return []
    trees = orch.tracker.get_trees()
    return [
        {
            "root_issue": tree.root_issue,
            "title": tree.title,
            "status": tree.status,
            "issue_count": len(tree.nodes),
            "issues": [
                {
                    "number": node.issue_number,
                    "title": node.title,
                    "parent": node.parent_issue,
                    "comment_count": len(node.comments),
                    "snapshot": node.snapshot,
                }
                for node in tree.nodes.values()
            ],
            "last_analysis": tree.last_analysis[:500] if tree.last_analysis else "",
        }
        for tree in trees.values()
    ]


@app.get("/api/discussions/{root_issue}")
async def get_discussion(root_issue: int):
    """Get a specific discussion tree with full details."""
    orch = _orch()
    if not orch.tracker:
        raise HTTPException(404, "Tracker not running")
    tree = orch.tracker.get_tree(root_issue)
    if not tree:
        raise HTTPException(404, f"Discussion #{root_issue} not found")
    return {
        "root_issue": tree.root_issue,
        "title": tree.title,
        "status": tree.status,
        "last_analysis": tree.last_analysis,
        "issues": [
            {
                "number": node.issue_number,
                "title": node.title,
                "parent": node.parent_issue,
                "body": node.body[:2000],
                "comment_count": len(node.comments),
                "snapshot": node.snapshot,
                "recent_comments": [
                    {
                        "author": c.get("author", {}).get("login", "unknown"),
                        "body": c.get("body", "")[:500],
                    }
                    for c in node.comments[-5:]  # last 5 comments
                ],
            }
            for node in tree.nodes.values()
        ],
    }


@app.post("/api/discussions/{root_issue}/submit")
async def submit_discussion(root_issue: int):
    """Manually submit a ready discussion for implementation."""
    orch = _orch()
    try:
        proposal_id = await orch.submit_discussion(root_issue)
        return {"status": "submitted", "proposal_id": proposal_id}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/discussions/{issue_number}/analyze")
async def analyze_issue_now(issue_number: int):
    """Immediately analyze an issue. Auto-starts tracker if not running."""
    orch = _orch()

    # Auto-start tracker with current focus issue if not running
    if not orch.tracker:
        from ..core.issue_tracker import WatchConfig
        config = WatchConfig(
            watch_labels=["discuss"],
            focus_issues=[issue_number],
        )
        await orch.start_tracking(config)

    # Ensure this issue is in focus list
    if issue_number not in orch.tracker.config.focus_issues:
        orch.tracker.config.focus_issues.append(issue_number)

    async def _run_analyze():
        try:
            await orch.tracker.analyze_now(issue_number)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("analyze_now failed for #%d", issue_number)

    asyncio.create_task(_run_analyze())
    return {"status": "analyzing", "issue_number": issue_number}


# ── Draft Comments ─────────────────────────────────────────

@app.get("/api/drafts")
async def get_drafts(status: str = "pending"):
    """Get draft comments awaiting review."""
    orch = _orch()
    drafts = await orch.task_queue.get_draft_comments(status)
    return [
        {
            "id": d.id,
            "root_issue": d.root_issue,
            "target_issue": d.target_issue,
            "body": d.body,
            "source": d.source,
            "status": d.status,
            "created_at": d.created_at,
        }
        for d in drafts
    ]


class DraftAction(BaseModel):
    action: str  # approve | reject | edit
    body: str = ""  # new body if editing


@app.post("/api/drafts/{draft_id}/review")
async def review_draft(draft_id: int, action: DraftAction):
    """Review a draft comment: approve (post to GitHub), reject, or edit."""
    orch = _orch()
    draft = await orch.task_queue.get_draft_comment(draft_id)
    if not draft:
        raise HTTPException(404, f"Draft {draft_id} not found")
    if draft.status != "pending":
        raise HTTPException(400, f"Draft is already {draft.status}")

    if action.action == "approve":
        if not orch.tracker:
            raise HTTPException(400, "Tracker not running")
        ok = await orch.tracker.post_approved_draft(draft_id)
        return {"status": "posted" if ok else "failed"}
    elif action.action == "reject":
        await orch.task_queue.update_draft_status(draft_id, "rejected")
        return {"status": "rejected"}
    elif action.action == "edit":
        if not action.body.strip():
            raise HTTPException(400, "Body is required for edit")
        await orch.task_queue.update_draft_body(draft_id, action.body)
        return {"status": "edited", "body": action.body}
    else:
        raise HTTPException(400, f"Unknown action: {action.action}")


class ChatMessage(BaseModel):
    message: str


@app.get("/api/drafts/{draft_id}/chat")
async def get_draft_chat(draft_id: int):
    """Get chat history for a draft."""
    orch = _orch()
    messages = await orch.task_queue.get_draft_messages(draft_id)
    return [
        {"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at}
        for m in messages
    ]


@app.post("/api/drafts/{draft_id}/chat")
async def chat_with_draft(draft_id: int, msg: ChatMessage):
    """Send a message to discuss/refine a draft with the agent."""
    orch = _orch()
    if not orch.tracker:
        raise HTTPException(400, "Tracker not running")
    draft = await orch.task_queue.get_draft_comment(draft_id)
    if not draft:
        raise HTTPException(404, f"Draft {draft_id} not found")

    reply = await orch.tracker.chat_draft(draft_id, msg.message)
    return {"reply": reply}


@app.post("/api/drafts/{draft_id}/rewrite")
async def rewrite_draft(draft_id: int, msg: ChatMessage):
    """Rewrite a draft based on user instruction. Returns the new draft body."""
    orch = _orch()
    if not orch.tracker:
        raise HTTPException(400, "Tracker not running")
    new_body = await orch.tracker.rewrite_draft(draft_id, msg.message)
    if not new_body:
        raise HTTPException(500, "Agent produced no output")
    return {"body": new_body}


@app.get("/api/events/stream")
async def event_stream():
    """SSE endpoint — streams new events to the browser."""
    orch = _orch()
    last_id = 0

    # Get the latest event id as starting point
    events = await orch.task_queue.get_events(since_id=0, limit=1)
    if events:
        # Start from the latest
        all_events = await orch.task_queue.get_events(since_id=0, limit=10000)
        if all_events:
            last_id = all_events[-1]["id"]

    async def generate():
        nonlocal last_id
        while True:
            events = await orch.task_queue.get_events(since_id=last_id, limit=50)
            for ev in events:
                last_id = ev["id"]
                data = json.dumps({"event": ev["event"], "data": ev["data"],
                                   "created_at": ev["created_at"]})
                yield f"id: {ev['id']}\nevent: orchestra\ndata: {data}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
