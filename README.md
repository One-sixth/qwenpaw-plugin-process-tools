# Process Tools — QwenPaw 增强多进程管理插件

让 QwenPaw Agent 具备完整的**后台进程管理能力**：启动、查看、等待、交互、通知——长任务不再阻塞对话。

GitHub 仓库：https://github.com/One-sixth/qwenpaw-plugin-process-tools

## 解决什么问题

QwenPaw 内置的 `execute_shell_command` 是为「快速命令」设计的，跑长任务时会遇到：

- **前台阻塞**：命令没跑完，agent 干不了别的，你只能干等；
- **超时即杀**：到点进程被终止，前功尽弃，且 agent 与进程彻底失联；
- **输出一次性返回**：大输出挤占上下文，中途进度不可见；
- **无进程概念**：命令一结束（或一被杀）就什么都查不到了，无法跟进。

如果你只想跑 `pip install`、`git status`，内置命令足够。但当你需要——

- 跑一个 10 分钟的模型训练，期间继续和 agent 聊别的；
- 启动一个开发服务器 / REPL / watch 进程，随时写指令、读输出；
- 长任务结束时 agent **自动收到通知并汇报结果**，不用反复问「跑完没」；
- 同时挂着多个任务，哪个完成了一眼看清；

——装这个插件。

## 六个工具

| 工具 | 干什么 |
|------|--------|
| `process_tools_exec` | 启动进程：前台等待结果 / 后台立即返回编号 / `detach` 完全脱离托管 |
| `process_tools_list` | 本会话全部进程一览（编号、状态、退出码、时长、命令） |
| `process_tools_check` | 查单个进程状态 + 输出日志尾部 |
| `process_tools_wait` | 等待一个/一批进程结束（超时只报「仍运行中」，绝不杀进程） |
| `process_tools_communicate` | 与进程交互：写入 stdin、增量读 stdout、发中断/强杀 |
| `process_tools_notice` | 注册完成/周期通知：进程结束时气泡提醒 + 自动唤醒 agent 处理 |

## 相比内置命令

| 能力 | 内置 `execute_shell_command` | process-tools |
|------|------------------------------|---------------|
| 执行方式 | 仅前台，阻塞对话 | 前台 / 后台托管 / 脱离托管 |
| 长任务 | 超时即杀，无法跟进 | 后台持续运行，随时查状态、读输出 |
| 等待收口 | 无 | `wait` 单个/批量等待，超时不杀 |
| 完成通知 | 无 | `notice` 气泡 + 自动唤醒 agent，长任务零轮询 |
| 进程交互 | 无 | stdin 写入 / stdout 环形缓冲增量读 |
| 多进程 | 无概念 | `#N` 编号管理（永不复用），批量等待与操作 |
| 大输出 | 全量挤进上下文 | 净化日志落盘，按需读尾部（省 token） |
| 进程终止 | 仅超时自动杀 | `sigint` / `sigkill` 精确控制（杀整棵进程树） |

## 典型用法

**长任务 + 完成通知**——对 agent 说「后台跑 `python train.py`，完成后通知我」：

```
agent: process_tools_exec("python train.py", background=True)   → 进程 #1
       process_tools_notice(1)
（agent 继续陪你干别的……训练结束时你收到通知，agent 被唤醒汇报结果）
```

**多任务并行收口**——挂三个任务，全部结束后一次性汇报：

```
agent: process_tools_wait("[1, 2, 3]")   ← 全部结束才返回（超时不杀）
```

**交互式进程**——启动 REPL / 数据库客户端，随时发指令、增量读输出：

```
agent: process_tools_exec("python", no_shell=True, background=True)
       process_tools_communicate(2, action="write_stdin", text="print(6*7)")
       process_tools_communicate(2, action="read_stdout")
```

**前端伴生（自动刷新）**：agent 被通知唤醒在后台干活时，已打开的 QwenPaw 页面会自动刷新一次并接上实时输出流——你不用手动刷新页面就能看到 agent 的回复直播。零配置，装插件即生效。

## 安装

```bash
qwenpaw plugin install /path/to/qwenpaw-plugin-process-tools
qwenpaw app
```

> - 无任何外部 pip 依赖（纯标准库 + QwenPaw 自带模块），装完即用。
> - ⚠️ `qwenpaw plugin install <URL>` 仅支持 **zip 归档**；Git 仓库请先 clone 到本地再按路径安装。
> - 依赖 QwenPaw 2.0 – 2.3。

## 跨平台行为

| 平台 | 默认 shell | sigint | sigkill |
|------|-----------|--------|---------|
| Linux | `bash -c`（回落 `/bin/sh`） | 可捕获，程序能优雅退出 | SIGKILL 整组 |
| macOS | `zsh -c`（回落 bash > `/bin/sh`） | 同 Linux | 同 Linux |
| Windows | `pwsh`（回落 powershell > `cmd.exe /c`） | ⚠️ 不经信号机制，≈第二档硬杀，优雅退出请用 `write_stdin` 约定指令 | `taskkill /F /T` 杀树 |

> Windows 下 `cmd.exe` 不认单引号，`> < & |` 等元字符需自行转义（或让 agent 用 `no_shell=True` 原生直启绕开）。进度条/TUI 程序按普通管道输出，一般会自行降级。

## 已知限制

- 无前端终端模拟（xterm）——进程输出走日志与增量读取
- 非 console 频道（dingtalk/feishu/qq/wecom…）的通知为「信使模式」：完成/周期通知会唤醒 agent 跑一轮并把回复送回频道（单向通知文本不直接进 IM）；agent 忙碌超过重试上限（约 10 分钟）则通知过期
- 唤醒排队有上限（约 10 分钟）：agent 连续繁忙超时则通知过期，气泡仍在
- 环形缓冲仅 512KB，更早的输出读日志文件
- 极短进程（exec 返回时已结束）注册通知会报错——直接 `wait` 收结果即可
