# Orchestra

Multi-agent coworking system powered by Claude Code. Watches GitHub issues, participates in discussions, decomposes requirements into features, implements them in parallel, and delivers code — all through a web dashboard.

## How it works

```
GitHub Issues / PRs
    |
    | watch & discuss
    v
[Discussion Analyst] ── reads issue threads, participates in discussions
    |                     generates draft comments for user review
    |                     tracks linked sub-issues automatically
    v
Create Idea (from issue or discussion)
    |
    v
[Head Leader] ── analyzes codebase, decomposes into features
    |              respects sub-issue boundaries (#55, #56)
    |              links tasks back to source issues
    v
Proposal Review ── user selects features to implement
    |
    v
[Feature Realizer] x N (parallel)
    |               isolated git worktree per feature
    |               branch: feat/54_user_edit_page
    v
[Feature Interpreter] ── code review, tests, verification report
    |
    v
Accept ── merge locally → push → done
    |      user controls: merge / push / create PR
    |      rename branch, push independently at any time
    v
Done ── downstream features unblocked, cycle continues
```

## Agents

| Role | Model | Responsibility |
|------|-------|----------------|
| **Head Leader** | Opus | Decomposes requirements into features. Respects user's own issue decomposition — won't over-split into implementation steps. |
| **Discussion Analyst** | Opus | Reads GitHub issue/PR threads, drafts analysis comments for user review. Matches the discussion language. |
| **Feature Realizer** | Sonnet | Implements one feature in an isolated worktree. Addresses rejection feedback on retry. |
| **Feature Interpreter** | Sonnet | Reviews implementation against spec, runs tests, writes verification report. |

## Features

### Discussion Tracking

- **Watch labels** — track issues/PRs with specific GitHub labels (e.g. `discuss`, `rfc`)
- **Focus issues** — pin specific issue numbers for the agent to follow
- **Sub-issue discovery** — automatically finds linked issues from `#N` references, cross-references, and agent analysis
- **PR tracking** — monitors pull requests alongside issues, including reviews and comments
- **Draft comments** — agent analysis goes to a review queue; user edits, approves, or discards before posting to GitHub
- **Draft chat** — discuss a draft with the agent, ask for rewrites, then apply the result
- **Bot loop prevention** — Orchestra's own posted comments don't re-trigger analysis

### Implementation Pipeline

- **Create Idea from issue** — click `+ Idea` on any issue in the Issues tab; fetches content + comments and sends to Head Leader
- **Smart decomposition** — HL respects `#N` sub-issue references as feature boundaries instead of over-splitting
- **Source issue linking** — branches named `feat/54_user_edit_page`, PRs reference `Implements #54`
- **Branch management** — rename, push, or merge any feature branch at any time, regardless of task status
- **Accept options** — merge locally / push to remote / create PR (checkboxes)
- **Dependency graph** — features auto-promote when their dependencies complete
- **Parallel execution** — multiple Feature Realizers work simultaneously on independent features
- **Auto-accept mode** — skip manual review; FI completion triggers automatic acceptance

### Dashboard

- **Refined dark UI** — Inter Tight / JetBrains Mono / Instrument Serif typography, warm-tinted dark palette
- **Git graph** — multi-lane branches, remote refs as dashed pills, click any commit to checkout
- **Side-panel tabs** — Details / Drafts / Discussions / Issues / PRs / Agents
- **Compact header** — status pills (Watch / Branch / Auto-Accept), overflow menu for settings
- **Draggable resizers** — adjust panel proportions by dragging
- **Real-time SSE** — agent output, events, and status updates streamed live
- **Tracked branch** — auto-fetches and checks out the latest on startup
- **Checkout with safety** — shows dirty files and unpushed commits before force-switching

### Git Integration

- **`.orchestra/` auto-gitignored** — survives branch switches, auto-recovers if clobbered
- **Conventional branches** — `feat/slug`, `bugfix/slug` with underscore separators
- **Fetch / push controls** — header button + API for manual git operations
- **Conflict handling** — merge conflicts trigger automatic rebase; pipeline continues

## Quick Start

```bash
pip install -e .
python -m orchestra.main web
# open http://127.0.0.1:8420
# select a project directory → set tracked branch → Initialize
```

Or from the command line:

```bash
python -m orchestra.main init --project /path/to/repo
python -m orchestra.main watch --project /path/to/repo       # watch issues
python -m orchestra.main submit "Build a user settings page" --project /path/to/repo
python -m orchestra.main run --project /path/to/repo          # start the orchestration loop
```

## Configuration

`orchestra.yaml` in the project root:

```yaml
concurrency:
  head_leader: 1
  feature_realizer: 2
  feature_interpreter: 1

claude:
  command: claude
  max_turns: 50

watch:
  labels: ["discuss", "rfc"]
  poll_interval: 120
  auto_submit: false
  max_depth: 3
  max_issues_per_tree: 15
  ready_label: "orchestra-ready"
```

## Requirements

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- [GitHub CLI](https://cli.github.com/) (for issue/PR features)
- Python 3.11+
