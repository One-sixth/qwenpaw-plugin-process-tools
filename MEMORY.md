# 项目记忆

> 开发过程中的关键决策、踩坑记录、经验沉淀。按主题分类，不按日期记录。

---

## 架构决策

### v1 范围拍板（2026-09-05 与泰斗先生确认）
- **subprocess + 管道，不做 PTY**：看重跨平台（Win/macOS/Linux 三大系统正常用），`pywinpty` 不引入
- **不做前端 xterm 控制台**：只做 agent 侧 5 工具 + 日志文件
- **工具命名全部 `process_tools_XXX`**，5 个：exec / list / check / communicate / notice
- **零外部 pip 依赖**：纯标准库 + QwenPaw 自带 agentscope/httpx

### 内核 contextvar 定位（替代设计文档的 current_username/current_session_id）
- `qwenpaw.app.agent_context` 的 `get_current_agent_id/user_id/session_id()`，
  由 `hooks/request_setup/contextvars_hook.py` 每次请求注入，工具内直读即可
- 注册表 key = `(agent_id, user_id, session_id)` 三元组（比原设计多一维 agent_id，
  因 QwenPaw 是多 agent 架构）
- 后台 asyncio.Task 创建时快照 context，monitor/通知任务里读取有效

### 通知唤醒路径（对齐设计文档 msg_queue.enqueue_notification）
- 气泡：`qwenpaw.app.console_push_store.append(session_id, text, sticky=True)`（进程内直用）
- 唤醒：`qwenpaw.agents.tools.agent_management.submit_agent_chat_task`
  （POST /console/chat/task，与 submit_to_agent 同路），同步 httpx → 放 `asyncio.to_thread`
- 409（会话忙）= error 字段含 "already running" → 30s 重试，上限 20 次
- 先例参照：mail monitor 用 `workspace.stream_query(req)` 唤醒（插件拿不到 workspace 对象，
  所以走 HTTP 客户端 kit；wait_agent_task 插件证明该 kit 可被插件 import）

### 导入方式 / 工具 Description 策略 / ToolChunk 返回
- 与 file-tools 一致：全部相对导入 + `try/except ImportError` 双模式；
  docstring `Args:` 段落即参数 schema；`register_tool(description=...)` 写一句话中文；
  `make_success/make_error` 统一 ToolChunk
- **async 工具**：agentscope Toolkit 支持 coroutine 工具函数（`_toolkit.py` 检查
  `iscoroutinefunction`），治理包装器 `_policy_tool_call` 本身就是 async——
  本插件 5 个工具全部 async

### 治理集成
- 5 个工具全部 `tool_type="shell"`
- `process_tools_exec` 用 `target_param="command"`（shell 逃逸检测生效）；
  其余工具 `target_param=""`（无 shell 命令参数，跳过逃逸检测但同受 shell 类策略管辖）

---

## 已知陷阱（全是测试期真踩出来的）

### 1. asyncio 子进程 Windows 参数名是 `creationflags`
`create_subprocess_shell` 透传给 `Popen`，写 `creation_flags` 直接
`TypeError: unexpected keyword argument`。

### 2. Sanitizer 必须先归一 CRLF（Windows 全灭级）
管道输出 `hello world\r\n` 中 `\r` 是行尾而非 PTY 覆写；不先
`replace("\r\n", "\n")` 就按 `\r` rsplit 会把整行内容吃掉只剩空行，
且表现为"进程 exit=0 但日志为空"的极难排查形态。

### 3. `_monitor` 吞 CancelledError 后继续 await = 死锁
`asyncio.run` 收尾 `_cancel_all_tasks` **只取消一次且等待任务真正结束**；
except CancelledError 后 `return`/继续 await 子进程都会永久卡死（取消不重发）。
纪律：捕获 → 同步兜底杀子进程（`_emergency_stop`，纯同步不 await）→ `raise` 上抛。

### 4. cmd.exe 不认单引号，`>` `<` `&` `|` 是元字符
`cmd /c "python -c "print('>'+x)""` 里的 `>` 被 cmd 解析成重定向，输出凭空消失。
测试与文档都要警告：跨平台命令字符串按目标 shell 规则自行转义。

### 5. 内核 `write_stdin` 不补换行，工具层才补
`ManagedProcess.write_stdin` 原样写入；`communicate` 工具的
`append_newline=True` 才负责补 `\n`。直测内核时忘了补会"stdin 写成功但进程无响应"。

### 6. 测试并发上限不能用"快命令连开 N 个"
python.exe 启动约 0.3~0.5s，连续 start 7 个 print 会真的同时运行撞上限 5；
正确姿势是 start→wait 交替验证"已结束不占名额"。

### 7. 测试 mock 必须覆盖值绑定（file-tools 同款教训）
`manager.py` 用 `from .utils import get_data_dir, session_key` 绑定的是引用，
conftest 要逐个模块 setattr 覆盖。

### 8. 前台等待超时后必须 kill + 短等收尾
`wait_for(shield(future))` 超时只毁自己的等待；杀完用
`await self.wait(timeout=3)` 等 monitor 置状态，否则并发名额释放有延迟窗口。

---

## 待实机验证清单（装进 QwenPaw 后）

- [ ] 宿主事件循环是否 Proactor（Windows 下 `create_subprocess` 的前提；
      plugin.register 已埋检测日志，非 Proactor 会在工具返回中报错）
- [ ] 工具调用里 `get_current_session_id()` 返回真实会话（非 "default"）
- [ ] 通知气泡在 WebUI 弹出（console channel 会话）
- [ ] `/console/chat/task` 唤醒闭环：会话空闲立即回复、忙碌排队/重试
- [ ] 多 agent 并发时会话隔离实际生效
- [ ] 治理审批链路：`tool_type="shell"` 下 exec 的命令送 detector 不误杀

---

## 备份与数据布局

- 数据根：`{workspace}/process_tools_data/logs/proc_{sanitize_session_token}_{N}.log`
- startup hook 清理 30 天前日志（`cleanup_old_logs(days=30)`）
- shutdown hook `shutdown_all()` 终止全部运行中托管进程（防孤儿）

---

## 对抗审查轮（零上下文子 Agent 审查后修复）

骨架被打磨前，无历史上下文子 Agent 用一次性验证脚本（`D:\Temp\pt_audit\`）
实锤了 4 严重 + 10 风险，全部处置如下：

### 严重（已修 + 钉死测试）
- **S1** `_monitor` 遇 reader 异常直接拿 `returncode`（活体=None）谎报 failed
  → 活进程不占名额、shutdown 漏杀、管道满挂死。
  修：异常路径 `_killed_by_us=True` + `_emergency_stop()` + 真实 wait 取码；
  reader 侧日志写失败降级丢段（`_write_log` 捕 OSError/ValueError）不再杀 reader
- **S2** `start()` 先 spawn 后建日志目录，目录炸掉留下**无句柄不可追踪的孤儿**
  → 修：makedirs+open 挪到 spawn 前，spawn 失败关句柄再抛
- **S3** 已结束进程的立即通知在 `await deliver` **之后**才置幂等标记
  → 并发双调用重复投递（deliver 内含 409 重试可挂 10 分钟）。
  修：检查与置位放同一同步段（await 之前），与 `_send_completion` 对齐
- **S4** `_CTRL_RE` 把 `\x08` 提前剥掉 → `apply_backspace` 沦为死代码、
  退格语义整体失效，且孤立函数单测**假通过**掩盖。
  修：正则放行 `\b`；补全管线 feed 链测试

### 风险（已修）
- R1 `read_log_tail` 窗口起点恰在行首时错杀完整首行 → 多探一字节判 `\n`（+7 utils 测试含跨进程）
- R2 Windows 坏 cwd 抛 `NotADirectoryError`（非 FileNotFoundError 子类）→ exec 统一捕 `OSError`
- R6 `sanitize_session_token` 用 `hash()` 受 PYTHONHASHSEED 随机化 → 改 `zlib.crc32`（子进程稳定性测试钉死）
- R7 check 提示混用字节/字符 → `len(body.encode())`
- R8 启动失败错误文案补「编号 #N 已消耗」
- R9 带间隔重复注册叠加 exit 监听 → `Notice.exit_listener_added` 标志去重
- R10 `max_output_chars` 下限 1（0 恒走截断分支）
- R3 read_stdout 字节切片可能断半行/半个多字节 → docstring 明示「完整内容以日志为准」

### 接受为已知限制（不修）
- R4 半条 OSC/CSI 恰好跨 `\n` 时残留裸 ESC（低概率，日志轻微污染）
- R5 应用 shutdown 最后一批 exit-listener 投递任务可能被取消（宿主都在关，语义可接受）
- 后台进程永不超时（设计拍板）；`timeout=0` 前台真等（LLM 危险默认，靠 description 提醒）

### 教训沉淀（已进知识库《编程技巧速查》§10）
**幂等标记必须与检查处在同一同步段（await 之前），否则 check-then-act 并发双投递**；
测试全绿 ≠ 世界正确：孤儿路径要用「失败注入 + 进程存活性外证」（taskkill 基线计数）钉。
