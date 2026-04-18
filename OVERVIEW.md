# Orchestra 简介

Orchestra 是一个 AI 多智能体协作系统，帮助开发团队自动化软件开发流程。

## 它能做什么？

想象你有一个 GitHub 仓库，上面有各种 issue（功能需求、bug 报告、讨论帖）。Orchestra 能：

1. **自动关注 issue 讨论** — 你指定一些标签（比如 `discuss`），系统会持续监控这些 issue 下的讨论，帮你总结进展、发现关联的子 issue
2. **参与讨论** — AI 会生成技术分析评论，但不会直接发出去，而是先给你审核，你可以编辑或和 AI 讨论后再发布
3. **从 issue 直接启动开发** — 看到一个你觉得可以动手的 issue？点一下，AI 就开始分析怎么实现
4. **自动拆分任务** — AI 分析代码库后，把需求拆成几个可以并行开发的小任务
5. **并行实现** — 多个 AI agent 同时在各自的 git 分支上写代码
6. **代码审查** — 另一个 AI 审查代码，跑测试，写验证报告
7. **合并上线** — 你选择是直接合入主分支还是创建 PR

## 核心流程

```
GitHub Issue  →  AI 分析讨论  →  创建 Idea
                                    ↓
                              AI 拆分成任务
                                    ↓
                     多个 AI 同时写代码（各自独立分支）
                                    ↓
                              AI 审查每个任务
                                    ↓
                        你审核 → 合并 → 推送到远程
```

## 四种 AI 角色

| 角色 | 干什么 |
|------|--------|
| **讨论分析师** | 阅读 GitHub issue 讨论，帮你总结、提问、分析技术方案 |
| **总指挥 (Head Leader)** | 分析代码库，把需求拆成可实现的任务 |
| **开发者 (Feature Realizer)** | 在独立的代码分支上实现一个任务 |
| **审查员 (Feature Interpreter)** | 审查代码、跑测试、写验证报告 |

## 你需要做什么？

Orchestra 不是全自动的，你在关键环节有决策权：

- **选择要关注的 issue** — 通过标签或直接指定编号
- **审核草稿评论** — AI 写的评论先给你看，你编辑满意后才发到 GitHub
- **审批任务拆分** — AI 拆完任务后你可以接受、调整或拒绝
- **验收实现** — 每个任务完成后你决定是否合并、是否推送

## 怎么用？

1. 安装：`pip install -e .`
2. 启动：`python -m orchestra.main web`
3. 浏览器打开 `http://127.0.0.1:8420`
4. 选择你的项目目录 → 初始化
5. 在 Issues 标签页浏览 GitHub issue，点 `+ Idea` 开始

## 需要什么？

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — AI 执行引擎
- [GitHub CLI](https://cli.github.com/) — 和 GitHub 交互
- Python 3.11+
