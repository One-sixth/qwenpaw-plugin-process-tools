# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.6.0] - 2026-09-12

会话聚合器：同会话通知聚合为一口气投递（作者设计，全类型会话统一逻辑）。

### Added
- **通知累积器 + flusher**（`Notifier.deliver` 重构为入队语义）：
  通知进入所属会话的累积器（key = agent/user/session/channel 四元组，
  仅内存态——宿主关闭/崩溃即丢，无恢复，作者拍板）；首条通知启动
  flusher 睡 ``FLUSH_WINDOW_SECONDS``（3s）聚合后续通知；窗口到点
  会话忙 → 以窗口粒度忙等（**无上限、永不放弃**，期间新通知继续
  累积并聚合进重试批次）；空闲时取走累积全部，聚合为一条文本
  （「【进程通知聚合 ×N】」标头 + 分隔线）一口气投出。
  console = 一条聚合气泡 + 一个唤醒回合；IM = 一个信使回合（agent
  一条回复覆盖全部通知）。投递期间新到的进下一轮；队列空 flusher
  退出，新通知懒启动。
- 聚合器天然串行化同会话投递（0.5.1 gate 的上层加固）+ 消除 IM 刷屏。

### Changed
- **投递函数全部单次化**：`_try_wake`→`_wake_once`、
  `_wake_via_messenger`→`_messenger_once`（删 30s×20 内部重试循环），
  返回统一三态 `ok/busy/fail`；重试节奏单点归 flusher。
- console 气泡时序：唤醒成功才发气泡（busy 重试期间不重复刷气泡）；
  fail 时仍尽力发气泡（通知文本至少可见）。
- 真失败（fail）消费本批不重试（作者拍板）；busy 是唯一重试态。

### Tests
- 新增 `tests/test_notifier_aggregate.py` 5 例：忙等续聚（重试批次
  含新通知）、fail 消费不重试 + 懒启动、气泡只在最终投递时发、聚合
  文本形状、单条直通；`test_notifier_wake.py` 适配 0.6.0 语义
  （flusher 级断言 + 单次语义映射）。全量 155 passed + 4 skipped。

## [0.5.1] - 2026-09-12

会话门闩：修复并发完成通知丢失（作者实机 bug 报告驱动）。

### Fixed
- **多进程完成通知接近同时触发时仅一条送达，其余静默丢失**（实机
  2/2 复现：#8/#9 同刻触发只达一条；#6 先触发反而被丢）。根因：
  `workspace.stream_query` 是裸跑——**不向 task_tracker 登记**（其
  running 状态只有 console 路径 `attach_or_start` 会写），`run_wake`
  的忙检对信使自身的并发完全失明 → 同窗口多通知并发启动多个 agent
  回合跑在同一 session 上（未定义行为）→ 后到者丢失。
- **修复：`messenger` 会话级门闩**（`_SESSION_GATES`，键 =
  (agent_id, channel, user_id, session_id)）：回合开始前 locked 检查
  （同步原子，单线程事件循环无竞态）→ 撞锁返回 busy → 调用方复用
  现有 30s×20 重试排队；回合结束/失败/超时均 `finally` 释放。
  副产物：同会话通知从「轮询重试」变「gate 拒绝+重试」，409 语义
  保持不变。

### Tests
- 新增 3 例：gate 忙检短路（第一回合跑着时第二通知 busy 且不启动
  stream_query）、完成/失败后 gate 释放（finally 回归）、不同会话键
  互不阻塞。全量 149 passed + 4 skipped。

## [0.5.0] - 2026-09-12

IM 信使：非 console 频道通知从「静默丢失」到「agent 回合 + 回复送达 IM」。

### Added
- **信使路由 `messenger.py` + `POST /api/process-tools/wake-channel`**：
  非 console 会话（wecom/dingtalk/feishu/qq…）注册的通知不再静默丢失。
  端点内完成「忙检 → agent 回合（`workspace.stream_query`，cron agent
  job 同款 dict 形态）→ 回复事件经 `channel_manager.send_event` 送回
  注册频道」。回复落 session 文件（WebUI 可见）+ IM 收到（wecom 底层
  走 aibot WS `SEND_MSG` 主动推送，无需回调帧）。忙碌返回 409。
  **端点并入 web_api 既有 router**（`messenger.add_wake_route`）——
  内核约束插件 HTTP prefix 插件级唯一（registry.py:262），同插件
  第二次 register_http_router 直接 ValueError 使**整个插件加载回滚**
  （0.5.0 首次装机实机踩坑，日志锚 `Cleaning up failed plugin load`）。

### Fixed
- 插件注册失败（上述 prefix 冲突）：拆独立 router → 合并进 web_api
  的单 router；web_api 对 messenger 的 import 用 try/except 兼容
  pytest 项目根直导（notifier 同款手法）。
- **`Notifier._wake_via_messenger` / `_submit_messenger_task`**：
  非 console 投递走信使端点；409 忙碌与 console 唤醒同款 30s×20 重试；
  `X-Agent-Id` 携带注册时 agent 维度（多 agent 不串台）；
  `MESSENGER_HTTP_TIMEOUT=330s`（> 端点内 agent 回合上限 300s）。

### Changed
- **`deliver` 投递按频道分流**：console 会话维持「气泡+chat/task 唤醒」
  双投递不变；非 console 会话**不再写 console_push_store**（死信——该
  session 无网页消费），报告项由「唤醒⏭️」变为「IM唤醒✅/❌(原因)」。
- 调查实证（方法 1 预研）：`/console/chat/task` 的 payload `channel`
  字段可透传真实频道名、三元组全等命中现有会话（不造幽灵），但回复无
  频道路由——因此信使改走 stream_query + send_event 组合。

### Tests
- 新增 `tests/test_messenger.py`（8 例：req 形态保真/忙检短路/频道缺失
  短路/agent_done 语义/超时/参数校验）+ `test_notifier_wake.py` 增补
  5 例（信使 payload/header、409 映射、重试语义、失败原因透传）；全量
  145 passed + 4 skipped。

## [0.4.4] - 2026-09-12

notice 描述减负 + 固定双投递 + wait 列表引号容错。

### Changed
- **`process_tools_notice` 删除 `wake_agent` 参数（作者拍板方案 B）**：
  气泡+唤醒固定双投递（`deliver` 不再收开关，`Notice` 删 `wake_agent`
  字段）。理由：`wake_agent=False` 时 agent 自己收不到通知、行动链断
  裂，这种自由度弊大于利；注册即想被叫醒，不想被周期叫醒就别注册周期
  通知（`interval_seconds=0`）。非 console 频道跳过唤醒的守卫不变。
- **docstring 重写**（240 字 → 130 字）：首句改为作者拍板语义「若要动
  态接收后台进程状态的周期通知或结束通知，必须调用本工具注册」；新增
  「注册后无需再用 wait 等待进程结束」防 agent 注册后挂等；砍投递机制
  细节（气泡/唤醒/忙碌排队/通知内容清单）。已结束进程的报错引导维持
  只指 `check`（0.4.2 裁决）。描述减负追加（同轮作者点名）：notice 删
  「（轮询烧 token）」、wait 的 tail_lines 删「（批量收口时最省
  token）」、list 删「运行中进程超过 5 个时…不占名额」整句（并发上限
  list 运行时输出 `（运行中 N/5）` 自解释）——理由同理：潜在收益信息
  不进描述，不增加决策难度。
- **成功返回消息去掉「（wake_agent=…）」与复读句「不注册则进程结束不
  会有任何推送」**：全局语义归 docstring，单次成功消息只报注册结果。
- **exec 的 encoding 报告格式**（作者点名）：`encoding=gbk（auto=…）`
  → `encoding="gbk" (auto=…)`——codec 名带双引号、加空格、全角括号
  改半角；显式值同格式 `encoding="utf-8"`。前台/后台两分支共用
  `enc_note` 一处拼接，测试三处断言同步。auto 提示语同步细化：
  「若输出一堆 ??? 乱码」→「若输出很多 "??" 或 乱码」——避免 agent
  把乱码窄化成问号堆（作者点名）。
- **README 去蓝本化**（作者点名）：删「蓝本：《AI_MED_UI 进程工具设计》
  （v1 = …）」整段；「三路数据流（v1 两路）」→「三路数据流」；已知限制
  「v1 无前端 xterm 控制台（设计文档…未移植）」→「无前端 xterm 控制台」
  ——README 不再出现内部设计文档名与 v1/v2 阶段叙述。顺带修正 wake_agent
  删除后的失真描述：通知系统「+（可选）唤醒」→「+ 唤醒 agent（固定双投递）」。
  复查轮扩大战果：manager/plugin/notifier/sanitizer 四模块 docstring 的
  蓝本与 v1 引用同批清理（见「零上下文子 Agent 复查轮」节）。
- **exec 并发上限移除（作者点名）**：删 `MAX_RUNNING_PER_SESSION = 5`
  常量与 start 检查、list 不再显示 `N/5`；`_running_count` 方法保留
  （测试仍用作名额语义自洽检查）。
- **shell 默认值 default → auto（作者点名）**：Windows pwsh > powershell >
  cmd、Linux bash > sh、**macOS zsh > bash > sh**（新增 darwin 分支，
  本机实测从未覆盖——装机后 macOS 待验）；旧值 `"default"` 静默映射
  为 auto（升级兼容别名，防装机升级期旧调用报错）。exec 返回信息新增
  `shell="…"` 行报告实际使用的解释器（后台/前台/detach 三分支；
  no_shell 直启无 shell 不报）。
- **process_tools_list 新增 `limit` 参数（默认 10，作者点名）**：新进程
  在前——**num 降序**实现（编号单调分配永不复用，等价创建顺序且无
  同秒时间戳精度问题；首版误用不存在的 registered_at 字段被测试当场
  抓住）；limit≤0 列全部，截断时提示「limit=0 看全部」。
- **描述语义校准（作者点名）**：「主动收口」→「等待进程结束」
  （exec/wait docstring、plugin.json、README）；env 描述显著写明
  「传 JSON 字典」，JSON 对象字符串容错保持静默 + 外围引号剥一次
  （同 parse_process_ids 手法）。
- **`parse_process_ids` 外围引号容错**：LLM 偶发把 JSON 数组字符串整体
  再包一层双引号（`"[\"1\", \"2\"]"`）→ **逐层 json.loads（最多 2 次）**
  解析（实链路实测通道字符串化是「外围引号+内部转义」的双层编码，
  单次剥引号不够——冒烟轮发现后升级为逐层 loads + 病态变体受限
  兜底）；不递归、不扩展单引号（作者拍板：仅兼容这一种）。单个编号
  被引号包裹（`"3"` / `"#4"`）同路受益。`parse_env` 同款双层防御。
  **wait 的 process_id 描述同步
  删去「或 JSON 数组字符串 "[1,2]"」**——容错是潜在包容，不写进
  agent 可见描述增加决策难度（作者拍板）；exec no_shell 的
  command「或其 JSON 数组字符串」是声明的双形态接口，保留不动。

### Tests
- 129 → **132 passed + 4 skipped**：新增
  `test_parse_process_ids_quoted_forms`（标准形式回归 + 剥引号目标场景
  + 单编号引号 + 两层引号只剥一次 + 单引号数组拒绝 + 坏 JSON）、
  `test_parse_process_ids_channel_double_encoded`（实链路双层编码
  回归，json.dumps 嵌套构造与通道产物逐字符一致）、
  `test_parse_env_quoted_json_object`（同款边界，目标场景用 json.dumps
  构造避手写转义）、`test_list_limit_and_newest_first`（新→旧排序 +
  limit=2 截断提示 + limit=0 全列）、`test_many_concurrent_processes`
  （8 进程并存钉死无上限，替代删除的 test_concurrency_cap）；
  `test_deliver_wake_routing_and_channel_guard` 删 wake_agent=False
  段、`fake_deliver`/`_Recorder` 签名同步；`tools_schema.json` 重导出。


## [0.4.3] - 2026-09-05

死会话数据清理：会话没了，它的计数器与日志跟着走。

### Added
- **`ProcessManager.cleanup_stale_sessions()`**（startup hook 自动调用）：
  遍历 `workspaces/` 下全部 agent 工作区，对「会话判活双判据」——
  chats.json 索引条目 **且** 真实会话文件（`sessions/<channel>/<uid>_<sid>.json`，
  兼容旧版根目录布局）——任一已没的会话，**立即删除**其
  `counters/*.cnt` 与 `logs/proc_{token}_*.log`（旧行为：等 30 天龄清理）。
- **为什么必须双判据**：UI 删除 chat 内核只删索引不删会话文件；
  手动删/挪文件则索引可能残留——任一单判据都漏掉一种死法。
- **token 单向不可逆**：`{token}.cnt` 是三元组 crc32，无法从文件名反推
  会话，故正向枚举活会话构建 live 集合、对差集清除；agent 维度取
  agent.json `id` 与目录名双别名（多留无害，多删有害）。
- **防误杀三重闸**：文件 mtime 宽限期 600s（防新建会话索引/文件迟落盘）；
  chats.json 缺失/损坏 → 整个工作区跳过；解析不出 token 的文件名永不碰。
- 零 pip 依赖；工具面与 schema 不变（仍 6 工具）。测试 116 → **128 passed
  + 4 skip**（`tests/test_cleanup_stale.py` 新增 12 项）。

## [0.4.2] - 2026-09-05

notice 语义收口（作者拍板）：**notice 只面向未来事件，获取当下结果归
wait/check**。

### Changed
- **对已结束进程注册通知 → 返回错误**（旧行为：立即投递一次完成通知）。
  实链路定雷：立即投递路径的唤醒 POST 必然撞上「当前前台 run 正占着
  会话」的 409，同步重试 20×30s≈10 分钟后仍失败——工具调用整体卡死
  10 分钟且零产出（0.4.1 埋点实证 `唤醒❌(会话忙，重试 20 次…)`）。
  错误文案引导用 `process_tools_check` 看状态/输出尾——终态已知，
  wait 的等待语义多此一举（作者纠偏：初版误写 wait）。
- **运行中进程注册后到期结束**的异步路径不变：exit-listener 在后台
  投递，唤醒遇会话忙自动排队重试，agent 空闲后即送达（气泡即时可见）。
- 测试维持 **116 passed + 4 skipped**：`test_notice_on_finished_*` 原地
  改为断言 error 且零投递；S3 并发幂等钉死迁移落点至
  `_send_completion`（双调 deliver 仅一次）；频道守卫测试同步退役改造。

## [0.4.1] - 2026-09-05

0.4.0 实链路核收发现：foreground 轮次内对已结束进程 `notice`，完成通知
的唤醒投递**秒回「唤醒❌」**且无任何原因细节（错误文本被 `_try_wake`
吞在 debug 日志里），无法区分「会话忙/端点异常/自锁超时」。本版把死因
surface 到工具报告。

### Changed
- **唤醒失败原因进报告**：`Notifier._try_wake` 返回值 `bool` →
  `(ok, reason)`；`deliver` 报告由 `唤醒❌` 变为
  `唤醒❌(RuntimeError: timed out)` / `唤醒❌(404: ...)` /
  `唤醒❌(会话忙，重试 20 次(约 10 分钟)后仍未成功)` 等具体死因，
  并同步打 `logger.warning`。非冲突错误不重试不睡眠的行为不变。
- 测试 113 → **116 passed + 4 skipped**（`test_notifier_wake.py` +3：
  报告含 reason、异常路径元组、错误透传且不重试）。

## [0.4.0] - 2026-09-05

exec 参数扩展：从「只有 command/background/timeout/cwd/max_output_chars」
升级到暴露 subprocess 的可用能力（含 QwenPaw 框架惯例对齐）。

### Added
- **`env`（增量环境变量）**：dict 或 JSON 字符串（通道字符串化防御），
  键值统一转 str。**对齐框架 execute_shell_command 惯例**（子 Agent
  源码调查报告）：spawn 环境=宿主 `os.environ` ⊕ 用户 env ⊕ PATH 前置
  宿主 `sys.executable` 目录（子进程 `python`/`pip` 命中框架所在环境；
  Windows PATH 键大小写变体不敏感归并，envs.json 注入的变量自动继承）。
- **`encoding`（管道编解码 codec）**：输出解码（Sanitizer 增量解码器 +
  read_stdout 回放）与 stdin 编码共用一个 codec。默认 **`"auto"`=本机
  原生编码**：Windows 用 `ctypes GetConsoleOutputCP()`（中文系统
  936→gbk——不能用 `locale.getpreferredencoding`，宿主 `PYTHONUTF8=1`
  会谎报 utf-8 而 cmd 原生输出仍是 GBK，实测踩过）；POSIX 用 locale。
  启动返回信息写明 `stdin/stdout/stderr encoding=XXX`，auto 时附
  「看到 ??? 乱码请显式调 encoding」提示。日志文件恒为 UTF-8（解码后
  落盘），编码切换不影响存量日志。
- **`no_shell`（原生直启）**：`create_subprocess_exec`（托管）/
  `Popen(argv, shell=False)`（detach），command 改传 **argv 列表**
  （或其 JSON 数组字符串，元素统一 str）。零引号地狱、`< > & |`
  按字面传参（陷阱 #4 的正解）；代价是无重定向/管道/通配符语义。
  list 未配 no_shell、字符串配了 no_shell 均报错引导。
- **`name`（人肉标签）**：≤80 字符，`exec` 返回、`check`、`list` 摘要行、
  通知消息头四处统一渲染 `#N「标签」`；纯展示，不影响执行与寻址。
- **`hide_window`（Windows 压控制台窗）**：`CREATE_NO_WINDOW` +
  STARTUPINFO `SW_HIDE`；POSIX 忽略；detach 模式无需此参数
  （其 spawn 恒带 CREATE_NO_WINDOW，本就没有窗口）。
- **`detach`（脱离托管）**：一次性 `subprocess.Popen`，stdio 全 DEVNULL、
  无 reader/monitor、不进注册表、不占并发名额、宿主退出后继续运行，
  **立即返回 OS 级 PID**（非 #N）。Windows 走
  `CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW`（⚠️ 弃用 `DETACHED_PROCESS`：
  完全无 console 时 PowerShell 0.3s exit 0 静默罢工、-Command 根本不执行，
  变体矩阵实测钉死）、POSIX 走 `start_new_session`。此后
  list/check/communicate/notice 对它全部无效，管理需按 PID 用系统命令
  自理（`taskkill /F /T /PID` / `kill`）。
  `background`/`timeout`/`max_output_chars`/`encoding` 在 detach 下失效。

- **`shell` 枚举（default / pwsh / bash）**：命令解释器选择。
  **行为变更**：default 不再是系统裸壳——Windows 优先 pwsh
  （`-NoProfile -NonInteractive -Command`，powershell 5.1 兜底、再退
  `cmd.exe /c`），POSIX 优先 bash（退 `/bin/sh`）。显式选 pwsh/bash 而机器
  没有 → 报错引导（不静默换壳）。实现=工具层把命令包装成
  `[shell_exe, *flags, command]` argv 走 `create_subprocess_exec`，回显
  显示用户原文（display 不被前缀污染）；`no_shell=True` 时忽略。
  PowerShell 语义提醒：带引号 exe 路径需 `& ` 调用运算符；`$var` 是 PS 插值。
- **detach Windows 标志修正**：`DETACHED_PROCESS` → `CREATE_NO_WINDOW`。
  实测钉死：完全无 console 时 PowerShell（7 与 5.1 同病）无法初始化宿主、
  0.3s exit 0 静默罢工——detach+pwsh 组合全军覆没；CREATE_NO_WINDOW 给
  子进程「有 console 但无窗口」，既保 detach 语义（宿主退出关控制台不连坐）
  又让 PS 正常工作（变体矩阵 3/3 存活）。

### Added（新工具）
- **`process_tools_wait`（第 6 个工具，⏳）**：前台主动等待一个/一批后台
  进程结束（作者点名场景：background 先干别的、快结束时收口）。
  `wait(process_id, timeout=60, tail_lines=20)`：process_id 支持单个
  （1/"1"/"#1"）与列表（[1,2]/JSON 串 "[1,2]"，通道防御），列表为
  **all 语义**（全部结束才返回）。核心纪律（与前台 exec 的本质区别）：
  **超时/被取消都只毁本次等待、绝不杀进程**（shield 共享 future 多等待者
  安全）；已结束进程幂等即返；超时返回 error「进程 #N 仍运行中」——
  **不带任何杀进程引导文案**（作者 W3 拍板）。detach 进程不可等待
  （不在注册表，W4 拍板；「按 OS PID 操作系统内进程」为作者新想法暂搁置）。

### Tests
- 新增 `tests/test_exec_params.py` 36 项：env 注入/JSON串/数值转str/宿主
  合并/非法拒绝/框架 PATH 前置钉；name 三处展示与截断；hide_window 不破坏
  托管链；detach 返 PID 不进注册表不消耗编号/timeout 失效/工具不可见/env
  副作用外证；encoding auto 标注与码页钉（ctypes）/显式 utf-8、gbk 双向往返
  （断言防命令回显污染，chr 码组装）/cmd 原生 GBK 实战钉（chcp 活动代码页，
  非 936 码页机器自动 skip）/stdin gbk 中文回显闭环/未知 codec 拒绝；
  no_shell argv 列表、JSON 串、元字符字面量、双向误配报错引导、detach 组合；
  shell 枚举 default=pwsh(Write-Output)/bash($(( )))/未知值拒绝/缺失不静默
  换壳/no_shell 忽略 shell/回显不被 shell 前缀污染。
- 新增 `tests/test_wait.py` 10 项：单进程收口、已结束幂等即返、超时 error
  「仍运行中」+进程存活+无杀引导、批量 all、JSON 串 process_id、批量超时
  点名分段、tail_lines=0、不存在报错、空列表拒绝、双等待者并发同收。
- **113 passed + 4 skip**（skip 均为平台守卫：POSIX bash 专属/本机无
  bash/本机装有 PowerShell）。

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
