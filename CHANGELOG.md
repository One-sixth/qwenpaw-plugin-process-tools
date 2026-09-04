# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.3.2] - 2026-09-05

刷新判据第三轮迭代（用户实测否决 DOM 静止判据）：**发送按钮
loading 态 × run 身份键**，每个 run 至多一刷，物理杜绝连环闪。

### Added / Changed
- **UI 信号源实证**（CloakBrowser 实测抓包）：chat 库发送按钮
  `qwenpaw-sender-actions-btn-loading-button` 从**发出消息起**覆盖
  整个 run——含等待吐字的思考空窗（该阶段 DOM 无变化，MutationObserver
  判据会误伤活跃页，0.3.1 实测被否）。按钮回到非 loading 态 = 本页
  不处于「正在生成」展示。
- **run 身份键**：`chat-status` 端点新增 `run_at`（workspace 级
  `last_run_at`，同一 run 稳定、新 run 必变）。前端三个 sessionStorage
  run 级标记（`qptReloadRun`/`qptBusyRun`/`qptFallbackRun`）保证
  **任一 run 对本页至多触发一次接管刷新 + 一次落沿补看**；tracker
  信号缺失时退回 30s 时间桶做键。最小 reload 间隔 15s。
- 规则：running ∧ 本页非 loading ∧ 本 run 未刷过 → reload 接直播；
  run 结束 ∧ 刷过但从未见 loading（reconnect 疑似失败）→ 补一刷看
  结果；活跃页永不刷。切换 chat 清空 run 标记。

### Fixed
- 编号续号首装盲区（0.3.1 内容并入本版）：计数文件缺失时兜底扫描
  `logs/` 中本会话 token 的最大既有编号，历史号段不再被复用。

### Tests
- web_api 测试重写 6 项（run_at 透传、global 炸不拖主判定等）。
  **71 passed + 1 skip**。

## [0.3.0] - 2026-09-05

及时刷新（用户实测两轮反馈定稿）：**后端活动 × 前端静止** 双信号，
废弃 session 文件指纹。

### Added
- **状态端点** `GET /api/process-tools/chat-status?chat_id=<UUID>`：
  转发 `task_tracker.get_status(chat.id)` 的实时
  `running`/`idle`（纯内存 O(1)；拿不到宿主状态 → `unknown`）。
- **前端双信号状态机**（`frontend/index.js`）：
  MutationObserver 度量本页 DOM 最后变化时刻——SSE 直播吐字、用户打字
  都算「在动」。规则：① `running` 且本页静止 >5s → reload（SPA 冷启动
  进入 running 会话原生 reconnect 接上进行中的 SSE，用户从「看不到」
  变「全程直播」）；② running→idle 落沿本页仍静止 → reload（整场错过
  的页补看结果）；③ 活跃页（DOM 在动）**永不刷新**。
  sessionStorage 30s reload 冷却防风暴；回到前台立刻查一次。

### Removed
- 0.2.0 的 `session-fp` 文件指纹方案（含 chats.json 索引、
  session_filename 解析、wake 打点表）：**持久化时机 = 生成完整结束**，
  刷新太晚；且正常轮次落盘会误刷正在对话的页面（用户实锤：每次 agent
  正常回复完都白闪一下）。前端伴生的判定信号从「结果落盘」换成
  「后端活动+前端静止」，语义正对需求本身。

### Tests
- `test_web_api.py` 重写为状态转发 6 项（running/idle 透传、run_key
  即 chat UUID、空 id/无 workspace/tracker 炸三兜底、端点形状）。

## [0.2.1] - 2026-09-05

端到端大考通过后的顺带收网：编号跨重启复用 bug（通知日志里的
`^C` 残影实锤）。

### Fixed
- **宿主重启后 `#N` 撞旧日志文件**：`_counters` 是纯内存计数，重启清零
  → 新会话进程 #1 复用重启前 #1 的日志路径（append 模式），两个不同
  进程的输出混进同一文件（且工具描述承诺"编号单调分配"）。现编号
  落盘续号：`{data_dir}/counters/{token}.cnt` 与本会话历史最大号取
  max 续排；磁盘读写失败静默降级纯内存计数（附属功能不阻塞 exec）。
- startup 清理（30 天）现在同时覆盖 `logs/` 与 `counters/` 两目录。

### Added
- 测试 4 项（`tests/test_manager.py`）：模拟重启续号、跨会话 key 计数
  独立、磁盘不可写降级、过期计数器清理。69 → **73 passed + 1 skip**。

## [0.2.0] - 2026-09-05

前端伴生组件：唤醒/后台任务落盘后 WebUI 自动刷新（实机体验缺口修复）。

### Added
- **HTTP 轻量端点** `GET /api/process-tools/session-fp?chat_id=<UUID>`
  （新文件 `web_api.py`）：chat UUID → 遍历 agent workspace 的
  chats.json（mtime+size 缓存）→ stat 会话文件返回 `mtime_ns:size`
  指纹。O(1)，不读 MB 级会话正文。
- **前端轮询脚本** `frontend/index.js`（插件首个前端 bundle，零构建
  纯 JS）：每 3s 对 URL 当前 chat 拉指纹；**基线机制**——进页/切会话
  只记基线不刷新，同会话 fp 变化（= 后台任务生成完成整体落盘）才
  `location.reload()`。输入框草稿由宿主 localStorage 存取不受刷新影响；
  fp 只在生成完整落盘时变化，不会打断进行中的 SSE 流。
- 背景：QwenPaw 前端主对话气泡区同一会话内永不自动重拉
  （SessionLoader 仅切会话触发），agent 被 notice 唤醒并回复后
  用户页面零动静——本组件补上这个感知缺口。
- 测试 `tests/test_web_api.py` 9 项：文件名 sanitize 规则与内核
  `session_filename` 对齐、chat 索引与缓存击穿、坏 chats.json 容错、
  端点 200/404、workspace 枚举兜底。60 → **69 passed + 1 skip**。

## [0.1.2] - 2026-09-05

唤醒闭环实测定雷：完成通知的 agent 唤醒曾投递到**凭空创建的幽灵会话**。

### Fixed
- **唤醒投递幽灵会话 bug（实机唤醒闭环专测发现）**：`/console/chat/task`
  用 `(session_id, user_id, channel)` 三元组**全等**匹配 chat，找不到即
  `get_or_create_chat` 新建。v0.1.1 的 payload 把 `user_id` 写死 `"main"`，
  与 console 会话真实 user_id（contextvar，如 `"default"`）错配 →
  通知落到陌生新 chat。现改为透传注册表 key 里的真实 user_id
  （`mp_key` 本就带着它，此前被 `_user_id` 丢弃）。
- **非 console 频道守卫**：notice 注册时快照 contextvar 频道
  （`utils.current_channel()`），非 console 会话跳过任务唤醒、只发气泡
  ——否则同样三元组错配造幽灵 chat。旧行为是 best-effort 盲投。

### Added
- 回归测试 `tests/test_notifier_wake.py` 6 项：payload 保真（真实 user_id、
  非 "main"、channel=console）、空 user 回落、deliver 双投递路由、
  频道守卫跳过、notice 工具频道快照接线、current_channel 兜底。
  测试 54 → **60 passed + 1 skip**。

## [0.1.1] - 2026-09-05

实机冒烟（另一会话）8/8 通过，修复其发现的 Windows sigint 语义坑。

### Fixed
- **Windows `send_sigint` 语义实锤修正**：CTRL_BREAK 不经 CPython 信号机制，
  自定义 handler 不会执行，进程以 `0xC000013A`（STATUS_CONTROL_C_EXIT）被 OS 终止。
  该退出码（有符号/无符号双形态）现映射为 `killed` 状态而非 `failed`，
  避免 agent 误判任务出错；新增纯函数 `status_for_exit` + 单测 + Windows 实链路测试
- 截断风格统一：`list` 的命令摘要从 `…` 改为 `<<truncated>>`（与其余工具一致）

### Changed
- 插件更名「QwenPaw 增强多进程管理插件」，description 强调
  "增强 QwenPaw 的多进程管理和交流能力"（plugin.json / README / AGENTS 同步）
- communicate docstring / 信号回显 / README 跨平台表：明示
  "Windows sigint ≈ 第二档硬杀，优雅退出走 stdin 指令约定"；POSIX 优雅语义保留

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
