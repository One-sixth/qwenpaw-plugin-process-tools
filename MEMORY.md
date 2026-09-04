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

## 现状快照与会话交接（截至 2026-09-05 本开发会话）

- **版本**：v0.3.2（及时刷新定稿：chat-status×发送按钮 loading×run
  身份键 + 编号续号兜底），插件名「QwenPaw 增强多进程管理插件」
- **git**：✅ 全部已 commit（作者 2026-09-05 提交 5 笔：
  `6d96a44`=0.1.2 唤醒寻址、`322f10c`=0.2.0 指纹+前端首版、
  `cbd7feb`=0.2.1/0.3.0草案 wake 打点+计数器、`b9ffadb`=0.3.0 chat-status、
  `32902bf`=0.3.2 run 键定稿；MEMORY 收尾修订留工作区）
- **测试**：71 passed + 1 skip；跑测试用 `envs\qwenpaw\python.exe`
- **安装状态**：✅ 宿主已跑 0.3.2（探针响应带 `run_at` 实锤；装机目录
  与仓库逐文件哈希一致，仅 MEMORY.md 文档差异）。45s notice 唤醒场景
  实测通过；日志同 token 续号 #1→#3 正常。cmd 彩蛋：`cmd /c` 整行在
  **解析期一次性展开** `%TIME%`，`&&` 串多段 echo 时间戳同刻——非插件
  bug，写多段命令/排障时留意（要逐段实时值得用 `!TIME!` 延迟展开或拆调用）。
  ④跨重启续号留待下次宿主重启顺带核：同 token 应发 max(logs)+1（本
  token 现至 #3）。探针备查：处理中 `curl .../api/process-tools/chat-status?chat_id=<本chatUUID>`
  回 `{"status":"running","run_at":<epoch>}`，响应带 run_at=0.3.2 已加载。
- **遗留物**：无（幽灵会话与 main_ session 文件已被用户清理）

### 下一会话待办（按优先级）
1. **Linux/macOS 实机**：POSIX 分支（start_new_session/killpg/SIGINT 优雅
   语义）未经真实宿主验证，值得上云跑一轮 pytest
2. 远期可选：PTY 双后端、上游提 `qwenpaw:chat-reload` 软刷新需求
   （届时把 reload 升级无痕）、周期通知 token 实测、气泡 60s 过期错过的
   补偿、run_at 是 workspace 级——同 agent 多 chat 并发时键会漂（现状
   影响：可能提前放行下一次刷新许可，有两刷上限+15s 间隔垫底，暂不处理）
