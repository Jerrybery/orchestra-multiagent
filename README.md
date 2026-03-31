# Orchestra

Multi-agent coworking system powered by Claude Code. Decomposes high-level ideas into features, implements them in parallel across isolated git worktrees, verifies each one, and delivers pull requests — all through a web dashboard.

## Pipeline

```
Submit Idea
    |
    v
[Head Leader] ── analyzes codebase, decomposes into features
    |               creates GitHub issue (label: idea)
    v               creates child issues per feature (label: feat, linked to parent)
Proposal Review ── human reviews HL's decomposition
    |               approve / reject / select subset
    v
[Feature Realizer] x N (parallel)
    |               each works in isolated git worktree
    |               commits reference GitHub issue numbers
    |               pushes feature branch to remote on completion
    v
[Feature Interpreter]
    |               reviews code, runs tests, writes verification report
    v
Accept / Reject ── human or auto-accept mode
    |               creates PR with spec + verification report
    |               when all features for an idea complete:
    |                 creates combined branch feat/realize-{idea-slug}
    v
Done ── downstream dependent features unblocked, cycle continues
```

## Agents

| Role | Responsibility |
|------|---------------|
| **Head Leader** | Understands the requirement and existing codebase. Decomposes into ordered, dependency-aware features. Writes specs, architecture decisions, conventions. |
| **Feature Realizer** | Implements a single feature in an isolated git worktree. Follows the spec, conventions, and API contracts. Up to N running in parallel. |
| **Feature Interpreter** | Reviews the FR's implementation against the spec. Runs tests, checks conventions, writes a verification report with accept/reject recommendation. |

## Key Capabilities

- **Web dashboard** at `localhost:8420` — git-graph style visualization, proposal review, agent log streaming, accept/reject actions
- **GitHub integration** — idea issues, feature issues with cross-references, PRs with spec + report, feature branches named by content
- **Dependency management** — features specify dependencies; blocked features auto-promote when predecessors complete
- **Parallel execution** — multiple FRs work simultaneously on independent features
- **Auto-accept mode** — toggle to skip manual review; FI completion triggers automatic PR creation
- **Context injection** — architecture, conventions, API contracts, and feature specs are embedded directly into agent prompts (no wasted tool calls)
- **Real-time streaming** — agent output (tool calls, reasoning, progress) streamed to the web UI via SSE
- **Conflict handling** — merge conflicts trigger automatic rebase; pipeline continues regardless

## Quick Start

```bash
pip install -e .
python -m orchestra.main web
# open http://127.0.0.1:8420
# select a project directory → Initialize → Submit a requirement
```

Requires [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) and optionally [GitHub CLI](https://cli.github.com/) for issue/PR features.
