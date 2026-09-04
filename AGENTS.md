# qwenpaw-plugin-process-tools

QwenPaw 增强多进程管理插件。注册 5 个 Agent 工具，提供托管后台进程、
会话隔离、stdin/stdout 交互、信号控制与通知系统。
蓝本《AI_MED_UI 进程工具设计》v1（subprocess + 管道，无 PTY/无前端）。

## 快速导航

| 文件 | 用途 |
|------|------|
| `plugin.py` | 插件入口，注册 5 个工具 + 启动清理/退出杀进程钩子 |
| `utils.py` | 公共函数：ToolChunk 封装、contextvar 会话定位、parse_int、日志尾读 |
| `sanitizer.py` | 输出净化：ANSI 剥离、CRLF 归一、`\r` 覆写折叠、增量 UTF-8 |
| `manager.py` | ProcessManager 注册表 + ManagedProcess 内核（环形缓冲/守护等待/杀树） |
| `notifier.py` | 通知系统：注册制、完成幂等、周期任务、气泡+唤醒双投递 |
| `tools/exec.py` | process_tools_exec（前台/后台） |
| `tools/check.py` | process_tools_check（状态+输出尾部+日志路径） |
| `tools/list.py` | process_tools_list（会话进程清单） |
| `tools/communicate.py` | process_tools_communicate（write_stdin / read_stdout / send_sigint / send_sigkill） |
| `tools/notice.py` | process_tools_notice（完成/周期通知注册） |
| `show_tool_schema.py` | 工具 schema 展示/导出脚本（生成 tools_schema.json） |
| `tests/` | 53 个 pytest 测试（qwenpaw 环境运行） |

## 5 个工具清单

1. `process_tools_exec` — 启动托管进程（前台等待 / 后台即返）
2. `process_tools_list` — 本会话全部进程一览
3. `process_tools_check` — 单进程状态 + 输出日志尾部
4. `process_tools_communicate` — stdin 写入 / stdout 环形缓冲增量读 / sigint / sigkill
5. `process_tools_notice` — 注册完成/周期通知（气泡 + 唤醒 agent）

## 测试

```bash
D:\Software\miniconda3\envs\qwenpaw\python.exe -m pytest tests -q
```
