# Plan Pipeline — Design Spec

## Overview

在 approval 和 FR 实现之间插入一个 Plan Generation 阶段。HL 负责需求分解和 spec 编写（现有行为不变），approval 后每个 feature 自动触发 plan 生成——用 Opus 级模型读取 spec 和相关代码，产出详细的实现计划（改哪些文件、具体方案、测试策略）。FR 按 plan 实现，FI 对照 spec 独立审查。

## Goals

1. **提高 FR 成功率**：FR 拿到 plan 后按步骤实现，而非自行探索代码库
2. **分离 spec 和 plan**：spec 是"做什么"，plan 是"怎么做"——两者由不同阶段产出
3. **FI 独立性**：FI 只看 spec + diff，不看 plan，确保审查独立于实现思路
4. **向后兼容**：不改变 HL 分解流程、proposal 审批流程、FI 审查流程

## Non-Goals

- 不新增 agent 角色——plan 阶段复用现有 runner 基础设施，注册为新 role `"pl"`
- 不改变 HL 的 prompt 或输出格式
- 不改变 FI 的 prompt（只做措辞微调，强调"对照 spec 的 acceptance criteria"）
- 不做 plan 的人工审批断点（plan 自动流转到 FR，失败时可人工干预）

---

## 1. State Machine Changes

### 新增状态

```python
class TaskStatus(str, enum.Enum):
    IDEA = "idea"
    PLANNING = "planning"      # NEW: plan generation in progress
    PLANNED = "planned"        # NEW: plan ready, waiting for FR
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    TESTING = "testing"
    REVIEW = "review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DONE = "done"
    FAILED = "failed"
```

### 新增转换

```python
TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.IDEA: {TaskStatus.PLANNING, TaskStatus.FAILED},        # CHANGED: was ASSIGNED
    TaskStatus.PLANNING: {TaskStatus.PLANNED, TaskStatus.FAILED},     # NEW
    TaskStatus.PLANNED: {TaskStatus.ASSIGNED, TaskStatus.FAILED},     # NEW
    TaskStatus.ASSIGNED: {TaskStatus.IN_PROGRESS, TaskStatus.FAILED},
    TaskStatus.IN_PROGRESS: {TaskStatus.IMPLEMENTED, TaskStatus.FAILED},
    TaskStatus.IMPLEMENTED: {TaskStatus.TESTING},
    TaskStatus.TESTING: {TaskStatus.REVIEW, TaskStatus.FAILED},
    TaskStatus.REVIEW: {TaskStatus.ACCEPTED, TaskStatus.REJECTED},
    TaskStatus.REJECTED: {TaskStatus.ASSIGNED},                       # NOTE: retry skips planning
    TaskStatus.ACCEPTED: {TaskStatus.DONE},
    TaskStatus.FAILED: {TaskStatus.PLANNING},                         # CHANGED: retry goes to PLANNING
}
```

### Pipeline 流程

```
IDEA → PLANNING → PLANNED → ASSIGNED → IN_PROGRESS → IMPLEMENTED → TESTING → REVIEW → ACCEPTED → DONE
              ↑        ↓         ↑                                                        ↓
              └── FAILED ──┘     └────────────────── REJECTED ──────────────────────────────┘
```

关键设计决策：
- **IDEA → PLANNING**：`promote_ready_tasks()` 改为将 IDEA 推进到 PLANNING（而非直接 ASSIGNED）
- **REJECTED → ASSIGNED**：reject 后跳过 re-planning，直接重新分配给 FR（FR 有 reject_reason 作为反馈）
- **FAILED → PLANNING**：失败重试回到 planning（plan 可能需要调整）

---

## 2. PlanRunner

### 文件

```
src/orchestra/core/runners/pl.py
```

### 职责

1. 加载 feature 的 spec
2. 读取代码库中与该 feature 相关的文件（通过分析 spec 中的路径线索）
3. 生成实现计划：文件变更列表、具体方案、测试策略
4. 将 plan 追加到 task 的 spec 字段

### 实现

```python
class PLRunner(AgentRunner):
    """Plan generation runner — reads spec + codebase, outputs implementation plan."""

    def __init__(self, spawner, task_loader, prompt_loader):
        self._spawner = spawner
        self._load_task = task_loader
        self._load_prompt = prompt_loader

    async def run(self, ctx: RunContext, cancel: CancelToken) -> RunResult:
        task = await self._load_task(ctx.target_id)
        system_prompt = self._load_prompt(ctx.target_id)

        task_prompt = f"Write an implementation plan for feature {task.id}: {task.title}"
        if ctx.prev_snapshot and ctx.prev_snapshot.get("fail_reason"):
            task_prompt += f"\n\nPrevious attempt failed: {ctx.prev_snapshot['fail_reason']}"

        handle = await self._spawner.spawn(
            role="planner",
            system_prompt=system_prompt,
            task_prompt=task_prompt,
            cwd=str(ctx.project_dir),
        )
        output = await self._spawner.wait(handle, cancel)
        result = self._parse_result(output)

        return RunResult(
            status="succeeded" if result else "failed",
            result_snapshot=result,
            session_id=handle.session_id,
        )
```

### ORCHESTRA_RESULT 输出格式

```json
{
  "plan": "## Implementation Plan\n\n### Files to Create\n...\n### Files to Modify\n...\n### Steps\n...\n### Test Strategy\n...",
  "files_to_touch": ["src/foo.py", "tests/test_foo.py"],
  "estimated_complexity": "medium"
}
```

### Plan 写入

`AgentRunManager._apply_success()` 在 PL 成功后：
1. 将 `plan` 内容追加到 `task.spec` 字段（以 `\n\n---\n\n## Implementation Plan\n\n` 分隔）
2. 转换状态 PLANNING → PLANNED

---

## 3. Prompt Template

### `prompts/planner.md`

```markdown
You are a **Planner** in the Orchestra multi-agent system. Your job is to write a detailed
implementation plan for a single feature, so that a Feature Realizer agent can follow it
step by step without needing to analyze the codebase from scratch.

## Architecture Context

{architecture_content}

## Conventions

{conventions_content}

## API Contracts

{contracts_content}

## Feature Specification

{spec_content}

## Your Process

1. **Read the spec** — understand what needs to be built and the acceptance criteria
2. **Analyze the codebase** — find the files and modules relevant to this feature:
   - Which existing files need to be modified?
   - What patterns does the codebase already use for similar functionality?
   - What interfaces, types, or conventions must be followed?
3. **Write the plan** — a concrete, step-by-step guide for implementation

## Plan Structure

Your plan must include:

### Files to Create
List each new file with its path and what it's responsible for.

### Files to Modify
List each existing file with its path, the specific section/function to change,
and what the change is. Include line numbers where helpful.

### Implementation Steps
Ordered steps, each with:
- What to do (create file, modify function, add test)
- The specific code or approach to use
- How it connects to adjacent steps

### Test Strategy
- What tests to write
- What to assert
- Edge cases to cover

## Rules

- Be specific: exact file paths, function names, line references
- Follow existing patterns in the codebase — don't invent new conventions
- Keep the plan scoped to this feature only — don't suggest refactors beyond scope
- Each step should be independently verifiable
- If the spec is ambiguous, note the ambiguity and pick the simpler interpretation

## Output

After writing the plan, output:

ORCHESTRA_RESULT:{"plan": "<full plan text>", "files_to_touch": ["path1", "path2"], "estimated_complexity": "low|medium|high"}
```

---

## 4. FR Prompt Changes

### `prompts/feature_realizer.md` 修改

在 `## Feature Specification` 之后，spec_content 现在包含 plan（因为 plan 被追加到了 spec 字段）。调整 prompt 措辞：

**现有：**
```
## Feature Specification

{spec_content}
```

**改为：**
```
## Feature Specification & Implementation Plan

{spec_content}

## How to Use the Plan

The spec above includes both acceptance criteria and an implementation plan.
Follow the plan step by step:
1. Create/modify files as listed in the plan
2. Follow the specified approach — don't redesign
3. Write tests as described in the test strategy
4. If the plan seems wrong or outdated, follow it anyway and note concerns in your output
```

FR prompt 的 Rules 部分保持不变。核心改变是 FR 不再需要自行决定"改哪些文件、用什么方案"——plan 已经给出了。

---

## 5. FI Prompt Changes

微调措辞，强调独立审查：

**在 Step 5（Verify acceptance criteria）前添加：**

```markdown
### Step 4b: Independent assessment

Your review is based on the SPEC (acceptance criteria), NOT the implementation plan.
The plan guided the implementer, but your job is to verify the OUTCOME matches the SPEC.
If the implementation achieves all acceptance criteria through a different approach than
the plan suggested, that is fine — judge results, not process.
```

其余 FI prompt 不变。

---

## 6. AgentRunManager Changes

### _apply_success() 新增 PL 分支

```python
if ctx.role == "pl":
    plan_text = snap.get("plan", "")
    if plan_text:
        # Append plan to task spec
        current_spec = (await self.task_queue.get_task(ctx.target_id)).spec or ""
        combined = current_spec + "\n\n---\n\n" + plan_text
        await self.task_queue.update_task_spec(ctx.target_id, combined)
    if ctx.mode == "auto":
        await self.task_queue.transition(ctx.target_id, TaskStatus.PLANNED)
    return
```

### _apply_success() FR 分支调整

FR 的起始状态从 ASSIGNED 不变——`PLANNED → ASSIGNED` 由 AutoDriver 触发。

---

## 7. AutoDriver Changes

### _tick() 新增 plan 阶段

```python
async def _tick(self) -> None:
    await self._tick_hl()
    await self.task_queue.promote_ready_tasks()  # IDEA → PLANNING
    await self._tick_pl()                         # NEW
    await self._tick_fr()                         # picks up ASSIGNED
    await self._tick_fi()
```

### promote_ready_tasks() 行为变更

现有 `promote_ready_tasks()` 将 IDEA → ASSIGNED。改为 IDEA → PLANNING。

### 新增 _tick_pl()

```python
async def _tick_pl(self) -> None:
    """Auto-submit plan generation for PLANNING tasks."""
    planning = await self.task_queue.get_tasks(TaskStatus.PLANNING)
    running = await self.task_queue.list_agent_runs(role="pl", status="running")
    running_targets = {r.target_id for r in running}
    for task in planning:
        if task.id in running_targets:
            continue
        if await self.task_queue.is_auto_paused("task", task.id):
            continue
        if self.manager.running_count("pl") >= self.config.max_hl:  # share HL concurrency
            break
        await self.manager.submit(
            role="pl", target_kind="task",
            target_id=task.id, mode="auto",
        )
```

### 新增 _tick_planned()

在 `_tick_fr()` 之前，将 PLANNED 自动推进到 ASSIGNED：

```python
async def _tick_planned(self) -> None:
    """Auto-promote PLANNED → ASSIGNED."""
    planned = await self.task_queue.get_tasks(TaskStatus.PLANNED)
    for task in planned:
        if not await self.task_queue.is_auto_paused("task", task.id):
            await self.task_queue.transition(task.id, TaskStatus.ASSIGNED)
```

---

## 8. Orchestrator Changes

### Orchestrator.__init__() — 注册 PL Runner

在 runners dict 中新增 `"pl"`:

```python
pl_runner = PLRunner(
    self.spawner,
    task_loader=self.task_queue.get_task,
    prompt_loader=lambda tid: self._load_prompt(AgentRole.PLANNER, tid),
)
self.manager = AgentRunManager(
    task_queue=self.task_queue,
    runners={"hl": hl_runner, "fr": fr_runner, "fi": fi_runner, "pl": pl_runner},
    ...
)
```

### AgentRole 枚举新增

```python
class AgentRole(str, enum.Enum):
    HEAD_LEADER = "head_leader"
    FEATURE_REALIZER = "feature_realizer"
    FEATURE_INTERPRETER = "feature_interpreter"
    PLANNER = "planner"                          # NEW
```

### _load_prompt() 支持 PLANNER

在 prompt 加载逻辑中，PLANNER 使用 `prompts/planner.md`，注入 spec_content + architecture + conventions + contracts（与 FR 相同的注入模式）。

---

## 9. Concurrency Config

```yaml
concurrency:
  head_leader: 1
  planner: 1            # NEW — default 1, shares with HL slot
  feature_realizer: 2
  feature_interpreter: 1
```

如果不配置 `planner`，默认使用 `head_leader` 的并发数。

---

## 10. File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `src/orchestra/core/task_queue.py` | Modify | 新增 PLANNING, PLANNED 状态；更新 TRANSITIONS |
| `src/orchestra/core/runners/pl.py` | Create | PlanRunner 实现 |
| `prompts/planner.md` | Create | Plan 生成 prompt 模板 |
| `prompts/feature_realizer.md` | Modify | 添加"按 Plan 实现"的指导 |
| `prompts/feature_interpreter.md` | Modify | 添加独立审查措辞（Step 4b） |
| `src/orchestra/core/orchestrator.py` | Modify | 注册 PL Runner，新增 AgentRole.PLANNER |
| `src/orchestra/core/agent_run_manager.py` | Modify | _apply_success() 新增 PL 分支 |
| `src/orchestra/core/agent_run_manager.py` | Modify | AutoDriver 新增 _tick_pl() + _tick_planned() |
| `src/orchestra/main.py` | Modify | 读取 planner 并发配置 |
| `tests/test_task_status_transitions.py` | Modify | 新增 PLANNING/PLANNED 转换测试 |
| `tests/test_plan_runner.py` | Create | PlanRunner 单元测试 |
| `tests/test_auto_driver_plan.py` | Create | AutoDriver plan 阶段测试 |

---

## 11. Testing Strategy

- **状态机测试**：IDEA → PLANNING → PLANNED → ASSIGNED 链路，FAILED → PLANNING 重试
- **PlanRunner 测试**：mock spawner，验证 prompt 注入、结果解析、plan 写入 spec
- **AutoDriver 测试**：PLANNING 状态触发 PL 提交、PLANNED 自动推进 ASSIGNED
- **集成测试**：完整 pipeline smoke test（HL → approve → plan → FR → FI）
- **向后兼容**：现有 ASSIGNED 状态任务仍可直接运行 FR（跳过 planning）
