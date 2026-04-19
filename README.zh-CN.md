# Orchestra

基于 Claude Code 的多智能体协作系统。监控 GitHub issue 与 PR 讨论、参与技术分析、将需求分解为特性、在隔离的 git worktree 中并行实现、代码审查后交付 — 全部通过 Web 仪表盘操作。

## 工作流程

```
GitHub Issues / PRs
    │
    │ 按标签或编号监控
    ▼
[讨论分析师] ── 阅读 issue 讨论线程，生成草稿评论供用户审核
    │              自动发现关联的子 issue
    │              追踪 PR 评论和 review
    ▼
从 issue 创建 Idea（或从讨论树直接提交）
    │
    ▼
[总指挥 Head Leader] ── 分析代码库，将需求拆分为特性
    │                      尊重用户的子 issue 划分（#55, #56）
    │                      不会过度拆分为实现步骤
    ▼
方案审核 ── 用户选择要实现的特性子集
    │
    ▼
[开发者 Feature Realizer] × N（并行）
    │                        每个特性一个独立 git worktree
    │                        分支命名: feat/54_user_edit_page
    ▼
[审查员 Feature Interpreter] ── 代码审查、运行测试、生成验证报告
    │
    ▼
验收 ── 本地合并 → 推送 → 完成
    │     用户控制: 是否合并 / 推送 / 创建 PR
    │     随时可以重命名分支、单独推送
    ▼
完成 ── 下游依赖任务自动解锁，流程继续
```

## 智能体角色

| 角色 | 模型 | 职责 |
|------|------|------|
| **总指挥 (Head Leader)** | Opus | 分析需求和代码库，拆分为有序的、有依赖关系的特性。尊重用户在 issue 中定义的任务边界，不会把一个功能拆成多个实现步骤。 |
| **讨论分析师 (Discussion Analyst)** | Opus | 阅读 GitHub issue/PR 讨论，生成技术分析草稿供用户审核后发布。自动匹配讨论语言。 |
| **开发者 (Feature Realizer)** | Sonnet | 在隔离的 worktree 中实现单个特性。被打回时根据反馈重新实现。 |
| **审查员 (Feature Interpreter)** | Sonnet | 对照 spec 审查实现，运行测试，输出验证报告和接受/拒绝建议。 |

## 功能特性

### 讨论追踪

- **标签监控** — 追踪带有指定标签的 issue 和 PR（如 `discuss`、`rfc`），在齿轮菜单中配置
- **Focus Issues** — 直接指定 issue 编号让 agent 关注，添加后立即触发分析
- **子 issue 自动发现** — 从 `#N` 引用、timeline cross-reference、agent 分析中自动发现关联 issue 并纳入讨论树
- **PR 追踪** — 与 issue 使用相同的标签机制，review 评论和普通评论合并展示，支持 open/closed/merged 状态
- **草稿评论** — agent 的分析评论先进入待审核队列（Drafts 标签），用户可编辑、审批或丢弃后才发到 GitHub
- **草稿讨论** — 在草稿上和 agent 聊天讨论修改意见，点"重写草稿"让 agent 按你的指令重新输出
- **防自激活** — Orchestra 自己发布的评论不会触发新一轮分析（但会作为上下文保留，标记为"你之前的发言"）

### 实现管线

- **从 issue 创建 Idea** — Issues 标签页中每个 issue 右侧有 `+ Idea` 按钮，一键拉取内容+评论提交给总指挥
- **从讨论树创建 Idea** — Discussions 标签页中点"创建 Idea"，汇总整棵树的所有 issue、评论和分析摘要
- **智能拆分** — 总指挥识别 issue body 中的 `#N` 子 issue 引用，按用户定义的边界拆分（例如 `→ #55` 和 `→ #56` 各成为一个特性），不会拆成"建路由""加组件"之类的实现步骤
- **源 issue 关联** — 分支名包含源 issue 编号（`feat/54_user_edit_page`），PR body 自动写入 `Implements #54`
- **分支管理** — 任何状态下都可以重命名、推送或合并特性分支（不限于 review 状态）
- **验收选项** — 三个 checkbox 控制：本地合并到主分支 / 推送到远程 / 创建 PR
- **依赖图** — 特性之间可以声明依赖关系，前置特性完成后自动解锁下游
- **并行执行** — 多个 Feature Realizer 同时在独立 worktree 中工作
- **自动验收模式** — 跳过人工审核，审查员完成后自动合并

### 仪表盘

- **精致暗色 UI** — Inter Tight / JetBrains Mono / Instrument Serif 字体组合，暖色调深色面板
- **Git 图谱** — 多车道分支布局，远程 ref 用虚线药丸样式区分，点击任意 commit 可 checkout
- **侧面板标签** — Details（详情）/ Drafts（草稿）/ Discussions（讨论）/ Issues / PRs / Agents
- **紧凑顶栏** — 状态药丸（Watch / Branch / Auto-Accept）+ 溢出菜单收纳次要操作（Fetch、设置追踪分支、标签编辑、切换项目）
- **可拖拽分隔条** — 水平拖拽调整左右面板比例，垂直拖拽调整底部日志区高度
- **实时事件流** — agent 输出、系统事件通过 SSE 实时推送到浏览器
- **追踪分支** — 启动时自动 fetch 远程并 checkout 到指定分支的最新 commit
- **安全 checkout** — 切换 commit 前展示脏文件列表和未推送的 commit，确认后再强制切换

### Git 集成

- **`.orchestra/` 自动加入 .gitignore** — 初始化时写入，切换分支后自动检测并恢复（tasks.db 和 context 目录被清空时重建）
- **规范分支命名** — `feat/user_edit_page`、`bugfix/login_error_fix`，下划线分隔，title 含 fix/bug/修复等关键词自动用 `bugfix/` 前缀
- **Fetch / Push 控制** — 顶栏一键 Fetch 远程、API 控制推送主分支
- **冲突处理** — 合并冲突时自动在 worktree 中 rebase 后重试

### API 端点一览

| 端点 | 说明 |
|------|------|
| `GET /api/status` | 项目连接状态 |
| `GET /api/graph` | 完整 DAG（需求、任务、提案、git commit） |
| `GET /api/issues` | GitHub issue 列表（支持 state/label 过滤） |
| `GET /api/prs` | Pull Request 列表（open/closed/merged/all） |
| `POST /api/issues/{n}/idea` | 从 issue 直接创建 Idea |
| `POST /api/submit` | 提交文本需求给总指挥 |
| `GET /api/proposals` | 查看待审核的方案 |
| `POST /api/proposals/{id}/review` | 审批/拒绝方案 |
| `GET /api/tasks` | 任务列表 |
| `POST /api/tasks/{id}/review` | 验收/打回/重命名分支 |
| `POST /api/tasks/{id}/push` | 推送特性分支 |
| `POST /api/tasks/{id}/merge` | 合并到主分支并推送 |
| `POST /api/tracking/start` | 启动讨论追踪（配置标签和 focus issues） |
| `PUT /api/tracking/labels` | 更新监控标签 |
| `PUT /api/tracking/focus` | 更新关注的 issue 编号 |
| `GET /api/discussions` | 讨论树列表 |
| `POST /api/discussions/{n}/idea` | 从讨论树创建 Idea |
| `POST /api/discussions/{n}/analyze` | 立即分析指定 issue |
| `GET /api/drafts` | 待审核草稿列表 |
| `POST /api/drafts/{id}/review` | 审批/编辑/丢弃草稿 |
| `POST /api/drafts/{id}/chat` | 和 agent 讨论草稿 |
| `POST /api/drafts/{id}/rewrite` | 让 agent 重写草稿 |
| `POST /api/checkout` | 切换到指定 commit（脏工作区时返回文件列表） |
| `POST /api/git/fetch` | Fetch 远程仓库 |
| `POST /api/git/push-main` | 推送主分支 |
| `PUT /api/tracked-branch` | 设置追踪分支 |

## 快速开始

```bash
pip install -e .
python -m orchestra.main web
# 浏览器打开 http://127.0.0.1:8420
# 选择项目目录 → 设置追踪分支 → 初始化
```

命令行模式：

```bash
python -m orchestra.main init --project /path/to/repo
python -m orchestra.main watch --project /path/to/repo       # 监控 issue 讨论
python -m orchestra.main submit "实现用户设置页面" --project /path/to/repo
python -m orchestra.main run --project /path/to/repo          # 启动编排循环
```

## 配置文件

项目根目录下的 `orchestra.yaml`：

```yaml
concurrency:
  head_leader: 1        # 总指挥并发数
  feature_realizer: 2   # 开发者并发数
  feature_interpreter: 1 # 审查员并发数

claude:
  command: claude        # Claude Code CLI 路径
  max_turns: 50          # 每个 agent 最大交互轮数

watch:
  labels: ["discuss", "rfc"]  # 监控的 issue/PR 标签
  poll_interval: 120          # 轮询间隔（秒）
  auto_submit: false          # 讨论成熟后是否自动提交实现
  max_depth: 3                # 子 issue 追踪深度
  max_issues_per_tree: 15     # 单棵讨论树最多追踪的 issue 数
  ready_label: "orchestra-ready"  # 标记为可实现的标签
```

## 系统要求

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- [GitHub CLI](https://cli.github.com/)（issue/PR 功能需要）
- Python 3.11+

## 项目结构

```
.orchestra/              # 项目工作目录（自动 gitignore）
├── context/             # 共享上下文
│   ├── architecture.md  # 架构决策（HL 维护）
│   ├── conventions.md   # 技术约定（HL 维护）
│   ├── feature_specs/   # 每个特性的 spec 文件
│   └── api_contracts/   # 接口契约
├── worktrees/           # 每个特性的独立 git worktree
├── reports/             # 审查员的验证报告
├── logs/                # agent 执行日志
└── tasks.db             # SQLite 任务队列和状态
```
