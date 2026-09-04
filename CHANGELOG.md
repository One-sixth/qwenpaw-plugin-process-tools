# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.1.0] - 2026-09-05

首个版本。蓝本《AI_MED_UI 进程工具设计》v1 移植：subprocess + 管道，跨平台，无前端。

### Added
- `process_tools_exec` — 启动托管 shell 进程（前台等待返回净化全文 / 后台立即返回 `#N`），
  前台超时自动杀进程并返回输出末尾 20 行 + 自解释建议
- `process_tools_list` — 本会话全部托管进程一览
- `process_tools_check` — 进程状态 / 退出码 / 运行时长 / 净化日志尾部 / 日志路径
- `process_tools_communicate` — write_stdin / read_stdout（512KB 环形缓冲按偏移增量读）/
  send_sigint（单击语义：主进程或整组）/ send_sigkill（杀进程树）
- `process_tools_notice` — opt-in 完成/周期通知（间隔 ≥30s 生效），双投递：
  通知气泡（console_push_store）+ 唤醒 agent（/console/chat/task，忙则 30s 重试）
- ProcessManager：会话隔离注册表（agent_id, user_id, session_id），`#N` 单调永不复用，
  每会话并发上限 5（已结束不占名额）
- Sanitizer：ANSI/OSC 剥离 + CRLF 归一 + `\r` 覆写折叠 + `\b` + 增量 UTF-8 解码
- 治理集成：`tool_type="shell"`（exec 带 `target_param="command"`）
- 生命周期钩子：启动清理 30 天旧日志；应用退出终止全部托管进程（防孤儿）
- 41 个 pytest 测试（Windows 实测全绿，Linux/macOS 依赖 CI/实机确认）
- `show_tool_schema.py` 工具 schema 离线导出

### Fixed（零上下文对抗审查后，同版本内修复）
- 审查 S1：`_monitor` 遇 reader 异常谎报结束（活进程不占名额、shutdown 漏杀、管道满挂死）
- 审查 S2：`start()` 先 spawn 后建日志目录，目录失败留下无句柄不可追踪孤儿
- 审查 S3：已结束进程立即通知的幂等标记在 await 后置位，并发双调用重复投递
- 审查 S4：`strip_ansi` 提前剥 `\x08` 致退格模拟死代码（净化链自废武功）
- 审查 R1/R2/R6/R7/R8/R9/R10：read_log_tail 错杀完整首行、坏 cwd 异常类型漏捕、
  日志文件名 hash() 跨进程不稳定改 crc32、字节/字符混用、编号消耗提示、
  重复注册叠加 exit 监听、max_output_chars=0 边界
- 审查 R3：read_stdout 字节切片边界语义在 docstring 明示「完整内容以日志为准」

### Known limitations
- 无 TTY：进度条/TUI 按管道降级；未移植前端 xterm 控制台
- 非 console channel 的唤醒为 best-effort
- 不接管应用崩溃遗留的历史孤儿进程
