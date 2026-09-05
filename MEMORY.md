# 项目记忆

> 开发过程中的关键决策、踩坑记录、经验沉淀。按主题分类，不按日期记录。

---

## 架构决策

### v1 范围拍板（2026-09-05 与作者确认）
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
  本插件 6 个工具（0.4.0 起）全部 async

### 治理集成
- 6 个工具全部 `tool_type="shell"`
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

## 实机验证清单（装进 QwenPaw 后——已全部核销，0.3.2 装机验收收官）

- [x] 宿主事件循环是 Proactor ✅（0.1.x 冒烟实锤；`plugin.register`
      已埋检测日志，非 Proactor 会在工具返回中报错）
- [x] 工具调用里 `get_current_session_id()` 返回真实会话 ✅（冒烟实锤）
- [x] 通知气泡在 WebUI 弹出 ✅（冒烟 + 0.3.2 装机验收双实锤）
- [x] `/console/chat/task` 唤醒闭环 ✅（0.1.2 端到端测试 ghost=0；
      0.3.2 实机：气泡→唤醒→1 刷接逐字直播）
- [x] 会话隔离实际生效 ✅（71 测试双向 + 冒烟 list 隔离；多 agent
      **真机并发**未测，归入远期观察）
- [x] 治理审批链路 `tool_type="shell"` detector 不误杀 ✅（冒烟实锤）

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

---

## 实机联调轮（2026-09-05，另一会话冒烟 + 本次修复）

### 冒烟结果：8/8 能力通过，0 残留
前台 exec / 后台 #N / list 隔离与计数 / check 尾部 / read_stdout 增量续读 /
write_stdin 中文往返 / notice 立即补发气泡 / sigkill 杀树 + 前台超时自杀——全 ✅。
核销「待实机验证清单」：Proactor 循环 ✅、contextvar 真会话 ✅、气泡 ✅、
shell detector 无误杀 ✅。仍待专测：/chat/task 唤醒闭环、多 agent 并发隔离。

### 核心发现（v0.1.1 修复）：Windows sigint ≠ 优雅中断
- 实锤：带自定义 SIGINT handler 的进程与裸进程**都**以 `0xC000013A`
  (STATUS_CONTROL_C_EXIT) 被 OS 直接终止——CTRL_BREAK 走控制台默认处置程序，
  轮不到 CPython 信号机制，连 KeyboardInterrupt traceback 都没有
- 修复：`status_for_exit()` 纯函数把该码（有符号 -1073741510 / 无符号双形态）
  映射为 `killed`（此前误报 failed 会让 agent 误判任务出错）；
  docstring/回显/README 全部诚实化：Windows sigint ≈ 略轻于 sigkill 的第二档硬杀，
  **优雅退出走 write_stdin 约定指令**
- **连带发现（测试环境专测钉死）**：`GenerateConsoleCtrlEvent` 要求目标进程组
  attach 在当前控制台——**无控制台宿主**（服务化/管道化运行 QwenPaw）里
  CTRL_BREAK 无处送达，sigint 静默无效（工具返回"发送信号失败"文案）。
  实链路测试对此双环境自适应 skip，不伪装通过

### 其他小修
- 截断风格统一 `<<truncated>>`（list 摘要此前用 `…`）
- 版本 0.1.1；测试 54 passed + 1 skip（skip 项为无控制台环境的 Windows 专测）

---

## 唤醒闭环专测轮（2026-09-05 续场，实测定雷 → v0.1.2）

### 前置核销（全绿）
- 装机 0.1.1 = 仓库零漂移（29 文件 hash 全同）；测试须用
  `D:\Software\miniconda3\envs\qwenpaw\python.exe`（normal 环境无 agentscope，跑不了）
- **sigint 复查** ✅：exit=3221225786(0xC000013A) → 状态 `killed`；本机宿主有控制台
- **多 agent 隔离** ✅：spawn_subagent（session=sub-e47ed726）双向不可见
  对方进程、编号各自从 #1 排、日志文件名带各自会话 token

### 大雷：唤醒投递到幽灵会话（用户实锤发现）
- 现象：notice 注册的完成通知没回本会话，WebUI 冒出全新 chat
  「【后台进程通知】以下」(a09715b1)
- chats.json 取证：两 chat 的 session_id **相同**（`1788551250567-g9t2380`），
  差异只在 **user_id：本会话 `default` vs 幽灵 `main`**
- 根因链：`/console/chat/task` → `resolve_session_id` 原样采纳显式
  session_id → `get_or_create_chat(session_id, user_id, channel)` 三元组
  **全等**匹配（repo/base.py get_chat_by_id），查不到**静默新建**；
  而 notifier `_submit_wake_task` 把 user_id 写死 `"main"`（照抄示例），
  真实值明明就在 mp_key 第二位却被 `_user_id` 丢弃
- 教训：**投递寻址的每个维度都必须用 contextvar 真实值，写死=造鬼**；
  内核 get_or_create 类接口是「查不到就新建」语义，不会报错救你

### v0.1.2 修复
- payload `user_id` 透传 mp_key 真实值（空回落 "default"）+ 显式 `channel`
- **频道守卫**：Notice 新增 `wake_channel`（注册时 `utils.current_channel()`
  快照 contextvar），非 console 跳过任务唤醒只发气泡（回显「唤醒⏭️」），
  堵住跨频道投递的第二条造鬼路径
- 测试 54 → 60 passed + 1 skip（新增 test_notifier_wake.py 6 项钉死）

### 复测方法（装机后）
后台 exec → notice(wake_agent=True) → 结束回合等进程完成 →
**应在原会话**收到唤醒（agent 自动复活汇报）；chats.json 不再长新 chat。

### HTTP 层预验证（2026-09-05 本会话，curl/urllib 手工投修正 payload）
- 忙时投 → **409** "already running for this chat"（证明三元组命中当前 chat）
- 空闲投（sleep 25s 后）→ **200** + task_id，agent 在**原会话**被唤醒，
  判别标记 WAKE-OK-2026 读到并复述 ✅
- 结论：修正 payload（真实 user_id="default"）寻址正确；插件通路复测
  仍待 commit → --force 重装 0.1.2

---

## 前端伴生·自动刷新（0.2.0，2026-09-05 作者拍板）

**问题**：唤醒闭环后端通了，但 QwenPaw 前端主对话气泡区**同一会话内永不
自动重拉**（SessionLoader 仅切会话触发；2.2.0 bundle 实测无 chat-reload
事件、reconnect 字样为 0），agent 被唤醒回复完，用户页面零动静。

**方案拍板**（用户决策）：整页 reload 而非小插件——
- 输入框草稿**不用守卫**：宿主本来就把草稿存 localStorage，刷新自动恢复
- 生成期零感知、打断阅读位置：接受（fp 只在生成完成落盘时变，天然无半截态）
- **不建独立通用插件**，并进 process-tools（HTTP 端点 + 零构建前端脚本）

**实现要点**：
- `web_api.py`：`GET /api/process-tools/session-fp?chat_id=` → 遍历
  workspace 的 chats.json（mtime+size 缓存）定位 `(session_id,user_id,
  channel)` → **只 stat** session 文件返回 `mtime_ns:size`。绝不读会话
  正文（本会话文件已 420KB+，3s 全量拉取是灾难）
- session 文件名**复用内核** `app.chats.session.session_filename()`：
  `{safe_uid}_{safe_sid}.json`（非法字符→`--`；uid==sid 省段），
  路径 `sessions/<safe_channel>/…`，兜底旧布局无 channel 子目录
- `frontend/index.js` 零构建纯 JS（**无 JSX/组件就不需要 vite**；
  参考 session-tools：type=command 带 entry.frontend 可行）；
  **基线机制**防误刷：进页/切 chat 只记 fp 不 reload；同会话 fp 非空
  变化才 reload；`__qptLiveRefreshInstalled` 防 SPA 重复装载
- ⚠️ **前端调 API 三件套**（session-tools 血泪直拷）：原生 fetch +
  `host.getApiUrl()` + Bearer `getApiToken()` + `X-Agent-Id`；
  **不能用 host.fetch**——它内部再拼 /api 变成 /api/api/ 404
- 测试 60 → 69（test_web_api.py 9 项；兜底分支用
  `sys.modules["qwenpaw.config"]=None` 逼 ImportError，比 patch
  builtins.__import__ 干净）

---

## 端到端大考与编号复用 bug（0.2.1，2026-09-05）

- **0.2.0 装机后端到端全绿**：33 文件零漂移；session-fp 端点宿主直调
  200（真实 chat UUID）、404/跨 workspace 遍历 OK、前端 bundle 经
  `/api/plugins/{id}/files/frontend/index.js` 下发、plugins list
  `loaded:true`；真 notice 唤醒**回本会话**（ghost 恒 0，
  0.1.2 插件通路收官）
- **顺带抓到实锤**：唤醒通知的"输出末尾"带着上一轮 sigint 实验的
  `^C^C`——宿主重启后 `_counters`（纯内存）归零，新 #1 撞重启前 #1 的
  日志文件名，`open(path, "a")` 混排两个进程输出。工具描述承诺
  「编号单调分配、永不复用」，跨重启不成立
- **0.2.1 修复**：`_alloc_id` 落盘续号（`counters/{token}.cnt`，
  读盘取 max+1、原子写 tmp→replace；读写失败静默降级内存计数，
  附属不阻塞 exec）；cleanup_old_logs 同清 logs+counters；
  token 抽 `_session_token()` 供日志名/计数器名共用防漂移
- 测试 69 → 73 passed + 1 skip

---

## 及时刷新三轮迭代（0.2.0→0.3.2 定稿，2026-09-05 用户实测驱动）

需求（作者原话）：「后端会话在活跃、前端在不动，就刷新」。
三轮判据演化，每轮都被实测定律——**记录为过程资产，防未来走回头路**：

| 轮 | 判据 | 死因（用户实测） |
|---|---|---|
| 0.2.0 | session 文件指纹变化（`session-fp`） | 落盘=生成**完整结束**，刷新太晚；正常轮次落盘还误刷活跃页 |
| 0.3.0~0.3.1 | 后端 running × MutationObserver DOM 静止 | **等待吐字期**界面明明显示"正在生成"但 DOM 无字符 → 活跃页照刷、连环闪 |
| **0.3.2** | 后端 running × **发送按钮 loading 态** × **run 身份键** | （现行，✅ 装机验收通过：仅 1 刷接直播） |

**0.3.2 终稿三要素**：
1. **后端活动**：`GET /api/process-tools/chat-status?chat_id=` → 转发
   `task_tracker.get_status(chat.id)`（**run_key 就是 chat.id**，console.py
   attach_or_start 决定；纯内存 O(1)）+ `run_at`=
   `get_global_status().last_run_at`（workspace 级，同一 run 稳定）。
   异常兜底 `unknown`（前端按非活动处理，宁可不刷）。
2. **前端知情判据（关键突破，CloakBrowser 实测取证）**：chat 库发送按钮
   class `qwenpaw-sender-actions-btn-loading-button`——实测生命周期
   `disabled(空闲)→clean(待发)→loading(覆盖整个 run 含思考空窗)→disabled`；
   全局稳定前缀非 hash。**running ∧ 本页非 loading = 页面不知情** → 该刷。
3. **run 身份键去重（最后一道闸）**：sessionStorage 三标记
   `qptReloadRun`(接管一刷)/`qptBusyRun`(本页见过 loading=直播中)/
   `qptFallbackRun`(落沿补看一刷，仅当刷过但从没见过 loading 即
   reconnect 疑似失败)——**任一 run 对同一页至多两刷**；run_at 缺失退回
   30s 时间桶；最小 reload 间隔 15s；切 chat 清空三标记；只认
   `/chat/<UUID>` URL；visibilitychange 回前台立即 tick。

刷新后的直播原理（upstream 原生，源码实证）：SPA 冷启动进入 running
会话 → `getSession`→`getChat`→`isGenerating(status==='running')`→SDK
`reconnect()`（POST /console/chat {reconnect:true}，tracker buffer 回放
+ 续流；`patchLastUserMessage` 专治 user 消息未落盘空窗）。
输入框草稿宿主本来存 localStorage，reload 无损。

**教训（两轮各半对，合成完整版）**：
① 判「该不该刷新」别用远端代理猜（文件落盘=结果、打点=来源），直接量
需求里的原始变量；② 但**量的必须是被控对象的语义状态而非行为代理**——
"前端动不动"用 DOM 变化频率数仍是猜，组件自己渲染的 loading 态才是
第一手信号；③ **刷新次数上限（身份键去重）是独立于判据的最后一道闸**，
判据可以错，风暴不能放。

相关退役面：fp/chats.json 索引/session_filename 复用/wake 打点表
（notifier 干净）、MutationObserver。0.2.1 编号落盘续号保留 +
0.3.2 并首装盲区修复（`_max_logged_num()` 扫 logs 兜底）。
测试 71 passed + 1 skip。

---

## exec 参数扩展轮（0.4.0，2026-09-05）

### 已落地 4 参数（作者拍板：🟢 中先做 detach/env/name/hide_window）
- **`detach`（核心拍板）**：「允许启动一个不受我们控制的进程，agent 自己用
  PID 管理」——一次性 `subprocess.Popen`，stdio 全 DEVNULL、
  无 reader/monitor、不进注册表、**不消耗 #N 编号**、不占并发名额、
  宿主退出后继续运行。Win
  `CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW`（**不能用 DETACHED_PROCESS**，
  见「评审落地轮」PS 罢工坑）、POSIX `start_new_session`（免疫 SIGHUP）。
  返回 OS 级 PID（是 shell 进程的 pid，杀整树 Windows 要 `/F /T`、
  POSIX 可 `kill -- -PID`）。background/timeout/max_output_chars/encoding
  失效。zombie 语义：宿主活着时 Popen 弃置对象进 subprocess._active，
  后续任何 Popen 创建会顺带 `_cleanup()` 回收已退出的——QwenPaw 常态
  起进程，泄漏有界，接受。
- **`env`**：增量语义 `{**os.environ, **clean}`（subprocess env 是全量
  替换，不并入宿主环境 PATH/SystemRoot 直接丢光）。`utils.parse_env`
  兼容 dict 与 JSON 字符串（通道字符串化防御，file-tools 教训 #14 同款）、
  键值 str 化（LLM 传 {"PORT": 8080} 很常见）。
- **`name`**：≤80 字符存、展示截 40，四处渲染 `#N「标签」`
  （exec 返回 / check 头 / list summary_line / notifier 完成+周期消息头）。
- **`hide_window`**：仅 Windows 托管路径——`CREATE_NO_WINDOW` +
  STARTUPINFO(SW_HIDE)。detach 无需（其 spawn 恒带 NO_WINDOW）。
  实测要点：宿主有控制台时子进程共享 console 本来就不弹窗，此参数主要
  服务「宿主无控制台（pythonw/服务化）时跑 console 程序」的场景。

### 探针沉淀
- asyncio `create_subprocess_shell` 实测拒绝 `text`/`encoding`/
  `universal_newlines`（强制 bytes 流）——编码只能在自己管线里做，
  别指望 spawn 层；接受 `env/executable/limit/startupinfo/creationflags/
  process_group/close_fds`；`pass_fds` Windows 不支持。
- QwenPaw schema 生成器对 `dict`→object、`list`→array、`bool`→boolean、
  `Union[str, list]`→anyOf 全支持，新类型参数无障碍（探针脚本模式：
  qwenpaw 环境跑 `agentscope.tool._utils._extract_input_schema`）。

### 框架惯例对齐轮（子 Agent 调查 execute_shell_command，2026-09-05）
零上下文子 Agent 对 `<site-packages>/qwenpaw/agents/tools/shell.py` 等的
调查报告，**全文归档 `docs/框架execute_shell_command调查报告.md`**（签名/
env 三层合成公式/smart_decode 三板斧/超时 Job Object/采纳与不跟对照表），
采纳项：
- **env 合并惯例**：框架不把 env 暴露为 LLM 参数，入口固定
  `os.environ.copy()` + **PATH 前置 `sys.executable` 目录**（子进程
  python/pip 命中框架环境）；envs.json 变量走 os.environ 注入被我们
  免费继承。我们实现 `manager.build_subprocess_env()`：宿主 ⊕ 用户 env
  ⊕ PATH 前置（Windows 键名大小写变体归并，学它的 Path/PATH 检测）。
  **PATH 前置是无条件常开**（框架同款），用户即使覆写 PATH 也被前置
  宿主 python 目录压过首位。
- **smart_decode 三板斧（UTF-8 严格→locale 回退）**：与作者拍板的
  auto=本机编码**不同**——我们 auto 用**控制台码页**而非 locale（见下），
  差异有意为之：auto 报告要诚实标注单一 codec，混码场景交给 agent 显式
  切换。框架沙箱路径硬编码 utf-8 不一致是它的坑，别跟。
- **不跟清单**：`timeout == 60.0` 相等判断回填默认值（应 None 哨兵）；
  env 黑名单置空非删除；声明未实现字段；sandbox_config 裸露 schema。
- 框架 stdout/stderr **分临时文件捕获**（Windows 管道句柄继承会挂死
  communicate——它注释里明说的实战坑）。我们是常驻 reader 流式读取，
  场景不同不受制，但「Windows 管道句柄挂死」在 v1 无 PTY 路线上留档。

### encoding 参数（第二波落地，含 PYTHONUTF8 大坑）
- 现象：第一版 auto 用 `locale.getpreferredencoding(False)`，本机实测
  返回 **utf-8**（宿主设了 `PYTHONUTF8=1`，UTF-8 模式下 preferred 被
  翻转），cmd 原生 GBK 输出依旧乱码——**修了个寂寞**。且首版冒烟脚本
  断言被「命令回显行含中文」污染而假通过（做事后要拿无污染证据）。
- 正解：Windows 的"本机编码"=控制台输出码页
  `ctypes.windll.kernel32.GetConsoleOutputCP()`（936→codecs 规范名 gbk；
  65001→utf-8 天然兼容），POSIX 才用 locale。`utils._auto_codec()`。
- 接线面（三处硬编码 UTF-8 全参数化）：Sanitizer(encoding=) 增量解码、
  sanitize_full(encoding=)（read_stdout 回放用 mp.encoding）、
  write_stdin 用 mp.encoding 编码。**日志文件恒 UTF-8**（解码前落盘后，
  编码切换不动存量）。detach 无管道，encoding 无效（docstring 标明）。
- 返回信息：`stdin/stdout/stderr encoding=gbk（auto=本机编码；若输出一堆
  ??? 乱码请显式调整 encoding，如 utf-8）`（作者点名设计）；显式值时只报
  `encoding=gbk`。前台/后台两个返回分支都带。
- 混码现实：本机 ACP=936 但子 **python** 继承 PYTHONUTF8=1 会输出
  UTF-8——单 codec 流无完美解，auto 报告 + agent 显式切换就是设计意图。

### no_shell 参数（第二波落地）
- 托管 `create_subprocess_exec(*argv)` / detach `Popen(argv, shell=False)`；
  command 双形态 `Union[str, list]`（schema anyOf ✅），JSON 字符串列表
  有 `utils.parse_argv` 通道防御，元素统一 str。误配双向报错
  （list 没开 no_shell / 开了 no_shell 传普通字符串都引导）。
- ManagedProcess 存储约定：`_spawn_target`（str|list 用于 spawn）+
  `self.command`（join 后的展示串，list/check/日志字段全走展示串）——
  下游对 argv 形态零感知。
- 卖点即陷阱 #4 的正解：argv 里 `< > & |` 字面传参、无 `%TIME%` 解析期
  展开、无单双引号地狱；代价无 shell 组合语义。

### stdin/stdout/stderr encoding 现状调查（2026-09-05 第一波，作者点名）
- **spawn 层**：纯 bytes（见上探针），无编码概念。
- **stdout/stderr 方向**：子进程原始 bytes → 环形缓冲（**字节级，编码无关
  ✅**）→ Sanitizer `codecs.getincrementaldecoder("utf-8")(errors="replace")`
  ——**UTF-8 硬编码**，非法字节替换成 U+FFFD → 净化日志以 UTF-8 落盘。
- **stderr**：`stderr=STDOUT` 合流进 stdout，**无独立编码通道**（v1 拍板）。
- **stdin 方向**：`write_stdin` 里 `data.encode("utf-8", errors="replace")`
  ——**UTF-8 硬编码**。
- **实测复现（本机中文 Windows，代码页 936）**：`cmd /c echo 中文测试`
  输出乱码 `???Ĳ??`——cmd 原生命令输出 GBK bytes，被按 UTF-8 解码替换。
- **根因定性**：字节管线正确，问题只在 **decode/encode 策略层两端硬编码
  UTF-8**。中文 Windows 上 cmd 内建命令/GBK 工具输出必乱码；反向
  write_stdin 给 GBK 程序中文也会乱码。Python 子进程默认也按 ANSI
  (cp936) 写管道（除非 PYTHONIOENCODING/chcp 65001）。
- **当前 workaround（0.4.0 env 参数落地后）**：`env={"PYTHONIOENCODING":
  "utf-8"}` 治 Python 子进程；命令前缀 `chcp 65001>nul && ` 治 cmd 原生
  输出。彻底解=暴露 `encoding` 参数（要动 Sanitizer 增量解码器+
  write_stdin+日志读取三处，另轮拍板）。

### 评审落地轮（第三波：wait 工具 + shell 枚举，2026-09-05 作者逐项拍板）
- **裁决记录**：W1 超时返回 error「进程 #N 仍运行中」文案；W2 **process_id
  支持列表**（all 语义，JSON 串通道防御）；W3 无 kill_on_timeout 参数、
  超时文案**不带任何杀进程引导**；W4 detach 进程不可 wait（不在注册表，
  docstring 已明说）。**stdin_data 作者否决**（别再提，除非作者翻案）。
  executable 演化 → **shell 枚举 `default / pwsh / bash`**。
- **`process_tools_wait`（第 6 工具）**：`wait(process_id, timeout=60,
  tail_lines=20)`。纪律：超时/取消只毁本次等待绝不杀（wrapper task 取消
  安全，内层 shield future 无伤）；已结束幂等即返；批量共享一个 timeout
  预算，error 里点名仍运行的+列出已结束的；tail_lines=0 只回状态行（批量
  收口省 token）。asyncio.wait 在 3.12+ 拒收裸 Future——**包成 task 再等**。
- **shell 枚举**：`resolve_shell_argv`（utils）→ 工具层把 str 命令包装成
  `[exe, *flags, command]` 走 create_subprocess_exec（**create_subprocess_shell
  自此只剩 manager 直连测试路径在用**）。default：Win pwsh→powershell→cmd
  回落、POSIX bash→sh 回落；显式选择找不到→报错引导不静默换壳。ManagedProcess
  新增 `display` 参数：shell 前缀不进回显（用户命令原文展示）。
  **行为变更警示**：Windows 默认壳 cmd→pwsh，agent 写命令遇怪现象先想这条
  （PS 带引号 exe 路径要 `& `、`$var` 是插值、cmd 内建是 alias 语义）。
  测试 helper `py_cmd` 改裸路径形态（不带引号）= cmd/pwsh/bash 三壳公约数，
  含空格路径的机器需要改（helpers docstring 已录）。
- **DETACHED_PROCESS 大坑（变体矩阵实测）**：完全无 console 时 PowerShell
  （7 和 5.1 一样）**静默罢工**——0.3s 内 exit 0、零输出、-Command 根本不
  执行；stdin DEVNULL/PIPE/inherit 三变体全灭，EncodedCommand 也救不了。
  正解 `CREATE_NO_WINDOW`（有 console 无窗口）：detach 语义不变（宿主退出
  不连坐）、PS 正常干活、矩阵 3/3 存活。**教训：给 PS 类宿主程序加
  creationflags 变体必须实测，不能按 Win32 文档想当然**。

### 搁置新想法（作者点名记录，未排期）
- 「让 process_tools 操作 OS 里任意 PID 的进程」（attach/taskkill 系统进程、
  按 PID 查询等）——作者 2026-09-05 评审 W4 时提出，**先搁置**。

### 0.4.2 notice 语义收口轮（2026-09-05 续场，0.4.1 埋点定谳 → 作者拍板新设计）

- **定雷定谳（埋点实证）**：`notice`(已结束) → `唤醒❌(会话忙，重试 20 次
  (约 10 分钟)后仍未成功)`。**推翻「异常/超时秒败」假设**——真实形态是
  conflict 重试耗尽；此前「秒回」观感是墙钟错觉（轮次边界不显示时间，
  实际每次卡了约 10 分钟，本会话时间线 16:3x→17:35 佐证）。**根因=自锁
  结构**：notice 工具同步 await 重试循环 → 本次前台 run 自己占着会话 →
  20 连 409 必输（外部探针同三元组也得 409，同一枚硬币）。
- **作者拍板新设计（语义收口）**：notice 只面向**未来事件**——
  ① 对已结束进程注册 → **返回 error**（提示已结束+引导
  `process_tools_check` 看状态/输出尾；作者纠偏：终态已知不该引导
  wait），不再立即投递任何东西；
  ② 运行中注册后到期结束（agent 回合未结束）→ 异步路径**不变**：
  exit-listener 后台投递不卡 run，唤醒遇忙自动排队，agent 空闲即送达。
  旧 S3 幂等担忧随立即路径退役，钉死测试迁移落点至 `_send_completion`
  双调仅一投。
- **落地**：`tools/notice.py` 立即投递块删除+docstring 更新、3 测试原地
  改写（116 passed + 4 skip 不变）、schema 重导出、CHANGELOG 0.4.2、
  plugin.json 0.4.1→0.4.2。**已由作者 commit（`8fff884`+`67ac269`）**。
- **装机核销顺带**：宿主重启后编号 **#9 续起**——「跨重启续号」待办 ✅
  核销；重装 0.4.1 后全文件 hash 与仓库零漂移。
- **装机核销（2026-09-05 同日完成，作者重装 0.4.2 + 重启，零漂移）**：
  ① `notice`(已结束#10) → **秒回 error 引导 check**，10 分钟自锁消灭 ✅
  （check 对终态进程返回终态+用时+输出尾+日志路径，引导语指对 ✅）；
  ② `notice`(运行中#11) 注册后进程于前台回合繁忙期结束 → 气泡即时 +
  唤醒 conflict 排队 → **回合结束 ≤30s 迟到送达并成功唤醒**（唤醒消息
  即「【后台进程通知】」独立回合实证）——「忙排队→空闲送达」全链走通 ✅。
  跨重启续号第二证：#10/#11 从盘上计数续起。

### 0.4.0 实链路核收轮（2026-09-05 续场，装机 0.4.0 零漂移后逐条打钩）

- **T1 detach ✅ 完整闭环**：bash 显式选择报错引导不静默换壳 ✅；返回 OS PID
  不进注册表不占号 ✅；pwsh 包装 + CREATE_NO_WINDOW 下真实干活（文件落地、
  到期自灭），DETACHED_PROCESS 罢工坑未复发 ✅。坏 cwd 报 WinError 267
  统一 OSError 捕获 ✅。
- **T2 no_shell ✅ + 新坑一条**：python 直启 `> & | <` 字面传参 ✅、
  JSON 字符串 argv 通道防御 ✅。**但 no_shell 目标选 cmd.exe 时元字符仍被
  cmd 自身重解析**（"此时不应有 &"，exit 1）——no_shell 免疫的是壳包装层，
  治不了 cmd 的命令行再解析天性；文档卖点表述要限定「避开 shell 劫持」。
- **T3 encoding 全链路 ✅**：auto 检测报 `gbk`（GetConsoleOutputCP）；
  cmd 原生 GBK 中文错误文案正确解码；python 子进程继承 PYTHONUTF8=1 输出
  UTF-8 → auto 下乱码属**设计内混码现实**，回显自带自纠提示，实测
  `encoding="utf-8"` 显式切换立即恢复 ✅。
- **T4 wait ✅**：`"[1]"` JSON 串解析 ✅；混合批量（已结束+运行中）超时
  error 点名运行中、汇报已结束、**不杀进程**（幸存靶自然跑完 exit 0）✅；
  已结束幂等即返 ✅。
- **T5 编号/惯例**：counters/ 落盘就位（新会话从 #1 起为正常语义）；
  env 增量语义（PT_TEST 注入 + SystemRoot 保留）✅；name 标签四处渲染 ✅；
  PATH 前置宿主 python 目录在 no_shell 直启下验证成功。
  **pwsh 包装时 PATH 头会变 `$PSHOME`**（PowerShell 7 启动子进程时自己插的，
  无害——PS 目录无 python.exe，解析顺序不受影响）。
- **🐛 定雷：轮次内 notice 已结束进程 = 唤醒秒失败零信息**。foreground 轮次
  中调 notice(已结束#N) → 「气泡✅ 唤醒❌」立即返回。外部探针复现同三元组
  POST 得 409 "already running"（本会话 run 正持有着），按代码应走 conflict
  30s 重试而非秒败——说明内部真实响应 ≠ 409，死因被 `_try_wake` 吞在
  debug 日志零呈现。**头号嫌疑：宿主自己 POST 自己端点被同 chat 的 run 锁
  卡到 httpx 15s 超时 → 异常路径秒 False**。已落 0.4.1 埋点（reason 进
  报告 + logger.warning + 3 测试钉，116 passed + 4 skip），待重装重启后
  复跑 notice(#N) 一次定谳。
  **（0.4.2 轮定谳：15s 超时假设被推翻，真凶=conflict 重试 20 次耗尽
  自锁，「秒败」是墙钟错觉；见下文「0.4.2 notice 语义收口轮」。）**
- **版本与装机**：0.4.0 已由作者 commit（`f410c74`）并 `--force` 装机，
  核收时全文件 hash 零漂移；0.4.1 埋点改动在工作区未 commit（规则：commit
  由作者决定），装机核收需重启宿主（会杀本会话宿主进程，由作者择机）。

### 0.4.3 死会话数据清理轮（2026-09-05 收尾前作者追加需求）

- **需求**：真实会话文件已没时（被删除），磁盘计数器一并清掉。调查发现
  内核 `DELETE /chats/{id}` **只删 chats.json 条目，不删会话文件**
  （api.py 明文注释），而手动删文件则索引残留——任一单判据都漏一种死法，
  故采**双判据**：条目 ∧ 真实会话文件（`sessions/<channel>/<uid>_<sid>.json`
  现行布局 + legacy 根目录兜底，命名规则对齐内核 `session_filename()`）
  双存在才算活。
- **实现** `manager.cleanup_stale_sessions(workspaces=None, grace=600)`
  （startup hook 挂接）：token 是 crc32 单向映射不可从文件名反推，
  正向枚举各 workspace chats.json 活条目算 live token 集合，对
  `counters/*.cnt` 与 `logs/proc_*_{N}.log` 差集清除。防误杀三闸：
  mtime 宽限期 600s、chats.json 缺失/损坏整 ws 跳过、解析不出 token
  的文件名（含 `.cnt.tmp` 残留）永不碰。agent 维度取 agent.json id +
  目录名**双别名**（多留无害多删有害）。多工作区扫描：`workspaces_root()`
  从当前 ws 上推、回落 `~/.qwenpaw/workspaces`。
- **实环境干跑验证**（只读）：34 chats 全判活；仅 `default|default|default`
  （无会话归属垃圾）、bu2tuw8（UI 删除会话的 7 日志——正是本功能靶场景）、
  `default|main|g9t2380`（0.1.1 幽灵 user 化石）三类被判死，共 1 cnt +
  10 log；4 个真实会话全数保全。
- **勘误**：前轮「xxu4jne 缺 #8 日志实证烧号」说法**不成立**——#8 日志
  实际在场（当时目录列举看漏），烧号语义本身不变（启动失败占号文案
  仍在），但那条"实证"作废。
- 测试 116 → **128 passed + 4 skip**（test_cleanup_stale.py 12 项）；
  工具面与 schema 不变；文档四件套（README/CHANGELOG/AGENTS/plugin.json
  0.4.3）已同步。待作者 commit + 重装重启（清理在重启时生效）。

---

### 现状快照与会话交接（2026-09-05 notice 收口轮之后）

- **版本**：v0.4.3——6 工具；exec 七参数；0.4.1=唤醒失败 reason 埋点、
  0.4.2=notice 语义收口（已结束→error 引导 check；异步到期投递不变、
  忙排队空闲送达）、0.4.3=死会话数据清理（双判据即删 cnt+日志）。
  **notice=未来事件 / check=当下与过去 / wait=未完成**
  的时态三分定稿（作者拍板+纠偏）。
- **git**：0.4.1=`8fff884`、0.4.2=`67ac269`、0.4.2 文档=`5f99e29` 均已由
  作者 commit；**0.4.3 改动在工作区待 commit**（utils/manager/plugin +
  新测试 + 文档四件套）。
- **测试**：**128 passed + 4 skip**（skip 全为平台守卫）；跑测试用
  `envs\qwenpaw\python.exe`；notifier_wake 的 Proactor `__del__`
  ResourceWarning 为存量（基线同样存在）。
- **装机状态**：装机仍为 0.4.2（0.4.2 曾 `--force` 重装+重启核销：实链路
  两验通过——已结束 notice 秒 error 引导 check、繁忙期结束唤醒排队迟到
  送达；跨重启续号双实证 #9、#10/#11）；**0.4.3 待重装+重启**后死会话
  清理生效（实环境干跑已预验判决：仅清 1 cnt + 10 log，全为死会话/垃圾）。
- **遗留待办**：① Linux/macOS 实机 POSIX 分支（bash 默认壳路径，云端
  实测优先级↑）；② 唤醒重试 10 分钟上限（20×30s）是否放宽——连续繁忙
  超 10 分钟通知会过期，等真实场景反馈；③ 远期：PTY 双后端、
  `qwenpaw:chat-reload` 上游需求、周期通知 token 实测、气泡 60s 过期
  补偿、run_at workspace 级键漂移、「按 OS PID 操作任意进程」搁置组；
  ④ 小改进候选（新会话做）：notice docstring「忙碌自动排队」可注明
  10 分钟上限、wait 批量全结束时 error/success 边界文案再打磨。
