# Process Tools — QwenPaw 增强多进程管理插件

**增强 QwenPaw 的多进程管理和交流能力：后台进程启动、状态查看、stdin/stdout 交互、信号控制、完成/周期通知。**

蓝本：《AI_MED_UI 进程工具设计》（v1 = subprocess + 管道，无 PTY、无前端控制台），
核心思想是 **"进程是共享对象"**：agent 工具里的 `#N` 与日志文件指向同一个进程，
会话内人机同一视图。

## 功能概览（5 个工具）

| 工具 | 功能 |
|------|------|
| `process_tools_exec` | 启动托管 shell 进程。前台等待返回完整净化输出（超时自动杀进程），或后台立即返回编号 `#N` |
| `process_tools_list` | 列出本会话全部进程（编号/状态/退出码/时长/命令） |
| `process_tools_check` | 查进程状态、退出码、运行时长、输出日志尾部与路径 |
| `process_tools_communicate` | 与进程交互：`write_stdin` / `read_stdout`（环形缓冲增量读）/ `send_sigint` / `send_sigkill` |
| `process_tools_notice` | 注册完成/周期通知（opt-in，通知以用户消息级别送达：气泡 + 唤醒 agent） |

## 前端伴生能力（0.2.0 引入，0.3.2 三轮迭代定稿）

- **及时自动刷新**：每 3s 轮询状态端点
  `GET /api/process-tools/chat-status?chat_id=…`（后端 `running`/`idle`
  + `last_run_at` run 身份键），并检测本页发送按钮的
  `…actions-btn-loading-button` 态（发出消息起覆盖整个 run，含等待
  吐字期——「页面正在展示生成」的精确 UI 信号）。
  **后端 running ∧ 本页非 loading → 刷新**——SPA 冷启动进入运行中
  会话原生 reconnect 接上进行中的 SSE 流，用户全程看到 agent"打字"；
  每个 run 对本页至多一次接管刷新 + 一次落沿补看（sessionStorage run
  级标记），**物理杜绝连环闪**；正在对话的活跃页永不被打扰。
  输入框草稿由宿主 localStorage 自动存取，刷新不丢；只管理
  `/chat/<UUID>` 页面。该缺口非 process-tools 独有，cron 通知、
  跨 agent 提交同样受益。
- 迭代史（详见 CHANGELOG）：文件指纹（太晚+误刷）→ DOM 动静
  MutationObserver（等待吐字期误判活跃页）→ **按钮 loading × run 键**
  终稿。

## 核心机制

- **会话隔离**：进程注册表 key = `(agent_id, user_id, session_id)`，
  由 QwenPaw 内核 contextvar 注入，跨会话/跨用户不可见不可操作
- **编号 `#N`**：按会话单调分配、永不复用（0.2.1 起计数器落盘，宿主重启后继续续号，日志文件绝不混排）；每会话并发运行上限 **5**（已结束不占名额）
- **三路数据流（v1 两路）**：512KB 环形缓冲（`read_stdout` 回放/增量续读）+
  净化日志落盘（剥 ANSI、折叠 `\r` 覆写、增量 UTF-8），
  路径 `{workspace}/process_tools_data/logs/proc_{session}_{N}.log`
- **通知系统**：完成通知幂等；周期通知间隔 ≥30s（推荐 ≥900s，省 token）；
  投递 = `console_push_store` 通知气泡 +（可选）`/chat/task` 后台任务唤醒 agent，
  会话忙碌自动延后重试
- **信号语义**：`send_sigint` 默认只中断主进程（`group=True` 整组）；
  `send_sigkill` 连子孙进程杀干净；前台等待被取消时尽力 kill，**绝不留孤儿**；
  应用退出钩子统一终止全部托管进程
- **超时自解释**：前台超时返回"输出末尾 20 行 + 加大 timeout / background=True 建议 + 完整日志路径"

## 安装

```bash
# 安装插件（QwenPaw 离线时执行）
qwenpaw plugin install /path/to/qwenpaw-plugin-process-tools

# 启动 QwenPaw
qwenpaw app
```

> 无任何外部 pip 依赖（纯标准库 + QwenPaw 自带 agentscope）。
> 安装后工具默认启用，无需额外配置。

## 跨平台说明

| 平台 | spawn | sigint | sigkill |
|------|-------|--------|---------|
| Linux / macOS | `/bin/sh -c` + `start_new_session` | SIGINT 主进程 / killpg 整组，**程序可捕获做优雅退出** | SIGKILL 整组 |
| Windows | `cmd.exe /c` + `CREATE_NEW_PROCESS_GROUP` | CTRL_BREAK：⚠️ 实测**不经 CPython 信号机制**，自定义 handler 不会执行，进程以 `0xC000013A` 被 OS 终止（映射为 `killed` 状态），≈ 略轻于 sigkill 的第二档硬杀 | `taskkill /F /T` 杀树 |

> **Windows 想优雅退出**：用 `write_stdin` 发送约定指令（如 REPL 的 `exit()`、
> 或程序自定义的 quit 命令），不要指望 sigint。

⚠️ **无 TTY**：进度条/TUI 程序按普通管道输出（一般会自行降级）；
Windows 下 `cmd.exe` 不认单引号，命令里的 `>` `<` `&` `|` 等元字符需自行按目标 shell 规则转义。

## 依赖的 QwenPaw 内核能力（2.0 – 2.3）

- `qwenpaw.app.agent_context`：会话 contextvar（请求期由 ContextVarsSetupHook 注入）
- `qwenpaw.app.console_push_store`：通知气泡
- `qwenpaw.agents.tools.agent_management`：本地 API 客户端（唤醒 agent 走 `/console/chat/task`）
- 治理集成：`tool_type="shell"`，`process_tools_exec` 的 `target_param="command"`

## 已知限制

- v1 无前端 xterm 控制台（设计文档中的实时渲染层未移植）
- 唤醒通知按 console 会话投递：`/chat/task` 以 `(session_id, user_id, channel)`
  三元组全等匹配会话，非 console 频道的通知会自动跳过任务唤醒（只发气泡），
  避免误建会话
- 环形缓冲仅 512KB，更早输出请读日志文件
- 插件热重载/应用崩溃后的历史孤儿进程不做接管（正常退出有 shutdown 钩子兜底）
- 不处理并发写入同一进程的 stdin（多 agent 同时写不保证顺序）
