# 项目记忆

> 关键决策、踩坑与裁决。按主题分类；历史轮次的**过程细节**已压缩为结论行，完整叙事见 git 历史中的旧版（file_tools 历史 #69 可回溯）。

---

## 当前状态速览（2026-09-12）

- **版本**：v0.6.1（notice 批量注册）。0.6.0 已提交（`cd19195`）。历史：`4fb3c76`+`66bb3ac`（0.4.4）→ 0.5.0 信使装机成功（prefix 踩坑后合并 router）→ 0.5.1 会话门闩修并发丢失（装机验证通过）→ 0.6.0 会话聚合器（双链路实机闭环：wecom 作者实测 + console agent 亲验）→ 0.6.1 notice 批量。
- **测试**：**164 passed + 4 skipped**（skip 全为平台守卫）；必须用 `D:\Software\miniconda3\envs\qwenpaw\python.exe` 跑（系统 python 缺 agentscope）。
- **装机**：git 工作树方式（`~/.qwenpaw/plugins/qwenpaw-plugin-process-tools`，`run` 分支）。0.6.1 待同步 py 进工作树 → 作者重启验证。
- **两轮零上下文子 Agent 复查均通过**（第二轮 A–H 八项 + 双解析器 18+15 用例实测零缺陷）。

---

## 装机与更新流程（重要）

- **git 工作树方式安装**：装机目录就是工作树，**严禁 `qwenpaw plugin install`**（破坏工作树）。
- 更新流程 = 同步代码文件进工作树 → **作者重启 QwenPaw**（小音不能重启宿主）。单纯覆盖文件**不触发重载**（sys.modules 缓存）——实测踩过：复制 utils.py 后复测仍旧行为。
- plugin.json/文档允许暂时与代码不同步（只同步 py 即可先验证行为）。
- `qwenpaw plugin install <URL>` 仅支持 zip 归档，Git 仓库需 clone 后本地路径安装（QwenPaw 安装器行为）。

---

## 架构决策

- **subprocess + 管道，无 PTY**（跨平台优先）；无前端 xterm 控制台；**零外部 pip 依赖**（纯标准库 + QwenPaw 自带 agentscope/httpx）。
- **注册表 key = (agent_id, user_id, session_id)** 三元组；contextvar 由内核 `hooks/request_setup/contextvars_hook.py` 每请求注入，工具内直读 `qwenpaw.app.agent_context`。后台 asyncio.Task 创建时快照 context。
- **通知双投递**：气泡 `qwenpaw.app.console_push_store.append(session_id, text, sticky=True)`；唤醒 `agents.tools.agent_management.submit_agent_chat_task`（POST /console/chat/task，httpx 放 to_thread）。409 会话忙 → 30s 重试 ×20 上限。**0.4.4 起无开关，气泡+唤醒固定双投递**（wake_agent 参数已删：False 时 agent 行动链断裂，自由度弊大于利）。
- 导入与 file-tools 同款：相对导入 + try/except ImportError 双模式；docstring `Args:` 即参数 schema；`make_success/make_error` 统一 ToolChunk；6 工具全 async。
- 治理：全工具 `tool_type="shell"`；仅 exec `target_param="command"`。
- **工具时态三分（作者定稿）**：notice=未来事件（仅运行中可注册，已结束→error 引导 check）／check=当下与过去／wait=未完成。**勿回退**成 wait/check 并列引导。
- **notice 结果三段式（作者定稿 0.6.1）**：固定三段「已生效→不存在→已结束」按序拼接、空段跳过，单/批/混合共用一条生成规则；**逐个独立校验，有生效即 success、全失败才 error**，勿回退成整批连坐或逐行逐条引导（引导语每类最多一次）。

---

## 已知陷阱与限制

1. asyncio 子进程 Windows 参数名是 `creationflags`（写 `creation_flags` 直接 TypeError）。
2. **Sanitizer 必须先归一 CRLF**（Windows 全灭级）：`\r\n` 是行尾不是覆写；不归一直接按 `\r` 切会吃掉整行，表现为「exit=0 但日志为空」。
3. `_monitor` 吞 CancelledError 后继续 await = 死锁：捕获 → 同步 `_emergency_stop()` → `raise`。
4. cmd.exe 不认单引号，`> < & |` 是元字符；跨平台命令按目标 shell 自行转义（no_shell 可绕开）。
5. 内核 `write_stdin` 不补换行，工具层 `append_newline=True` 才补。
6. 测试 mock 必须逐模块 setattr 覆盖（`from .utils import x` 值绑定，file-tools 同款教训）。
7. 前台超时后 kill + `wait(timeout=3)` 收尾，否则名额释放有延迟窗口。
8. **Windows sigint ≠ 优雅中断**：一律 `0xC000013A` 被 OS 硬杀（映射为 `killed`），≈第二档硬杀；优雅退出走 `write_stdin` 约定指令。无控制台宿主里 sigint 静默无效。POSIX 相反：SIGINT 可捕获（Debian 实测 37/37）。
9. **SIGINT 后状态滞后属有意设计**：孙进程握 stdout 管道时 `_monitor` 先拉干管道（EOF）再取退出码——输出完整性优先；sigkill killpg 连孙杀后回收真实码。已写入 README 已知行为。
10. **PYTHONUTF8 坑**：`locale.getpreferredencoding` 会谎报 utf-8；auto 编码必须用 `GetConsoleOutputCP()`（936→gbk）。
11. **通道双层 JSON 编码**（实链路实测）：工具参数会被通道字符串化成「外围引号+内部转义引号」双层（如 `"[\"35\", \"36\"]"`）——parse_process_ids/parse_env 均以**逐层 json.loads（最多 2 次）+ 病态变体受限兜底**（剥一次仅当剥完能解出目标类型才接受）处理；`""5""` 双层引号仍拒绝。
12. 已接受不修：半条 OSC/CSI 跨行残留裸 ESC；shutdown 最后一批 exit-listener 投递可能被取消；后台进程永不超时（timeout=0 前台真等，靠 description 提醒）；exec 超时错误分支不带 shell/encoding 行（聚焦超时本身）。

---

## 版本沿革与关键裁决

| 版本 | 内容 | 核心教训/裁决 |
|---|---|---|
| 0.1.0 | 6 工具骨架；对抗审查修 S1-S4 + R1-R10 | **幂等标记与检查必须同一同步段（await 之前）**；测试全绿≠世界正确，孤儿路径要「失败注入+存活性外证」钉 |
| 0.1.1 | Windows sigint 语义诚实化 | 见陷阱 8 |
| 0.1.2 | 幽灵会话修复 | **投递寻址每维必须用 contextvar 真实值，写死=造鬼**；内核 get_or_create 是「查不到就新建」，不会报错救你 |
| 0.2.0 | 前端伴生（session-fp 端点 + 轮询脚本） | 前端调 API 三件套：原生 fetch + `host.getApiUrl()` + Bearer token + X-Agent-Id；**不能用 host.fetch**（内部再拼 /api → 404） |
| 0.2.1 | 编号落盘续号（counters/{token}.cnt） | 修「宿主重启编号复用→日志混排」；启动失败也占号（烧号语义） |
| 0.3.0-0.3.2 | 及时刷新判据三轮迭代定稿 | 见下节「刷新判据演化」 |
| 0.4.0 | exec 参数扩展：detach/env/name/hide_window/encoding/no_shell/shell | **PATH 前置 sys.executable 目录无条件常开**（框架同款）；detach不能用 DETACHED_PROCESS；schema 探针：dict/list/Union 全支持 |
| 0.4.1 | 唤醒失败 reason 埋点 | 定谳「已结束进程 notice 卡 10 分钟」真凶=前台 run 自锁 409×20 重试 |
| 0.4.2 | notice 语义收口 | 已结束→error 引导 **check**（当时作者纠偏：误写 wait）；异步到期投递不变 |
| 0.4.3 | 死会话数据清理 | **双判据**（chats.json 条目 ∧ 真实会话文件）防两种死法漏杀；agent 维度双别名（多留无害多删有害）；Linux Debian 实机 37/37 核销 |
| 0.4.4 | 见下节详解 | 见下节 |
| 0.5.0 | IM 信使（非 console 频道通知投递） | 见「观察项与待办」 |
| 0.5.1 | 会话门闩修并发完成通知丢失 | 见「观察项与待办」 |
| 0.6.0 | 会话聚合器（3s 窗口聚合投递，忙等无上限） | 见「观察项与待办」 |
| 0.6.1 | notice 批量注册：逐个独立校验（部分成功语义，有生效即 success）、三段式结果消息（已生效/不存在/已结束，空段跳过零特例） | 消息表经三轮迭代定稿：逐行→分组同类项→三段式；引导语每类最多一次；「新注册 vs 更新」合并为「已生效」；单进程旧文案同步退役 |

### 刷新判据演化（0.3.2 终稿，防走回头路）

| 轮 | 判据 | 死因（用户实测） |
|---|---|---|
| 0.2.0 | session 文件指纹 | 落盘=完整结束，刷新太晚还误刷 |
| 0.3.0-1 | running × MutationObserver DOM 静止 | 等待吐字期误判活跃页，连环闪 |
| **0.3.2** | running × **发送按钮 loading态** × **run身份键** | 现行，装机验收通过 |

终稿三要素：chat-status 端点转发 `task_tracker`（run_key=chat.id，纯内存）+ `run_at`；按钮 class `qwenpaw-sender-actions-btn-loading-button` 覆盖整个 run（含思考空窗）；sessionStorage 三标记保证**每 run 至多两刷**（接管一刷+落沿补看），15s 最小间隔。教训：①判该不该刷新直接量原始变量；②要量**语义状态**（组件渲染的 loading）而非行为代理；③**刷新上限（身份键去重）是独立于判据的最后一道闸**。

### 框架对齐（0.4.0 子 Agent 调查 execute_shell_command，全文在 docs/）

采纳：PATH 前置（无条件）；不跟：timeout 相等判断回填、env 黑名单置空、smart_decode 用 locale（我们用控制台码页，差异有意——auto 诚实标注单一 codec，混码交 agent 显式切换）。框架 stdout/stderr 分临时文件捕获的「Windows 管道句柄挂死」坑已留档。

---

## 0.4.4 详解（当前版本）

### 行为五连改（作者点名）
- **exec 并发上限移除**：`MAX_RUNNING_PER_SESSION=5` 删除（常量+start 检查+list 显示）；`test_many_concurrent_processes` 8 进程并存钉死；`_running_count` 方法保留（测试仍用，语义自洽检查）。
- **shell 默认值 default→auto**：Windows pwsh>powershell>cmd、Linux bash>sh、**macOS zsh>bash>sh**（darwin 分支，未实机验证）；旧值 `"default"` 静默映射 auto（升级兼容别名）。**exec 返回新增 `shell="…"` 行**（后台/前台/detach 三分支，no_shell 不报）。
- **list 新增 limit 参数（默认 10）**：新→旧 = **num 降序**（编号单调永不复用，等价创建顺序且无同秒精度问题；首版误用 registered_at——那是 Notice 的字段——被测试当场抓）；limit≤0 全列（None 同，parse_int 自然结果，agent 不可达不进描述）。
- **描述语义校准**：「主动收口」→「等待进程结束」（exec/wait/plugin.json/README）；env 描述显著写「传 JSON 字典」。
- **描述减负原则（多轮拍板汇总）**：agent 可见描述只留「怎么传参」，潜在收益/机制细节运行时自解释或静默容错，**不增加决策难度**。

### 描述与格式
- notice docstring 重写（240→130 字）：首句「若要动态接收后台进程状态的周期通知或结束通知，必须调用本工具注册进程状态通知」；「注册后无需再用 wait 等待进程结束——系统会自动把通知发到用户侧并唤醒 agent」；成功消息去掉 `（wake_agent=…）` 与复读句。
- encoding 报告：`encoding="gbk" (auto=本机编码；若输出很多 "??" 或 乱码请显式调整 encoding，如 utf-8)`——引号+空格+半角括号；显式值同格式；拼接点仅 exec.py `enc_note` 一处。
- **README/AGENTS.md/plugin.py/notifier/sanitizer/manager 全部去蓝本化**（AI_MED_UI/v1 字样清零，技术教训保留）；CHANGELOG 0.1.0 历史条目按作者拍板保留不改。

### 双层编码修复（实链路冒烟抓到）
- 现象与修法见陷阱 11；**教训：单测用 json.dumps 构造的「无转义变体」没模拟真实通道——冒烟才暴露**；补 `test_parse_process_ids_channel_double_encoded`。
- 修复中两个 self-bug（replace 写入手滑）：env 漏 `obj = decoded` 分支（标准形式都挂）；process_ids 兜底 `v = cand` 被循环后收口覆盖（改 `decoded = cand`）。**教训：改解析器必须清 `__pycache__` 直测**；工具调用 JSON 层会把测试字面量的 `\"` 失真成 `\\"`，构造用 `json.dumps` 嵌套绕开。

### 两轮零上下文复查
- 第一轮：抓到 manager 等 4 模块 docstring 蓝本残留、plugin.py「5 个工具」、test 注释 registered_at 遗物——全修。
- 第二轮（终审）：A–H 八项过，132 绿核对一致，双解析器 18+15 用例零缺陷；抓到 CHANGELOG Tests 段数字失真（131→132）+漏双层测试名、smoke_fail.txt 误入 commit（已删）、test 注释旧措辞、README encoding 样式——全修。
- **裁决不修留档**：`_running_count` 无生产调用者（测试用）；limit=null 全列；exec 超时分支不带 shell 行；notifier.py:149 变更注释保留；双层坏 JSON 报错显示内层串。

### 附：chat-status「请求消失」未复现存档
- 现象：QwenPaw 重启 + 页面不刷新后 chat-status 轮询停发（其他请求正常）。事后无法复现，作者拍板搁置。
- 已排除（实测）：端点 200 正常；路由即时 include 且插 SPA 兜底前；jwt_secret 持久化 auth.json（token 跨重启有效）；前端 tick 全 try/catch 不会自死。另：qwenpaw.log 只记 governance 不记 HTTP 访问，服务端无记录≠请求未到达。
- 头号嫌疑（未证实）：SPA 内部导航致 `chatIdFromUrl()` null → 静默 return（唯一不发请求路径）。复现先跑 Console：`location.pathname`、`window.__qptLiveRefreshInstalled`、`sessionStorage.getItem("qptRe...")`。
- 可选增强（未做）：`if (!chat) return` 加一次性 warn；fetch 加 `AbortSignal.timeout(5000)`。

---

## 数据布局与钩子

- 日志：`{workspace}/process_tools_data/logs/proc_{crc32token}_{N}.log`（恒 UTF-8 落盘，解码在前）；token=`zlib.crc32` 三元组（hash() 受 PYTHONHASHSEED 随机化，勿用）。
- startup hook：30 天龄日志清理 + 死会话数据清理（双判据，宽限 600s，chats.json 坏则整工作区跳过）；shutdown hook：`shutdown_all()` 杀全部托管进程。
- detach 进程不进注册表、不占编号、宿主退出不清理（agent 按 OS PID 自理）。

---

## 观察项与待办

- **0.5.0 已实施：IM 信使（非 console 频道通知投递）**（2026-09-12 调查→实弹→落地，当日闭环）：
  **演进**：原 0.4.5 候选方案 messages/send（单向推送，作者否决——要
  agent 回复）→ 三方法调查 → 方法 1 实测（`/console/chat/task` +
  payload `channel` 透传 wecom 三元组：agent 真跑 15s、WebUI 会话可见、
  **不造幽灵**——但 stream_one 无频道路由，IM 收不到）→ 方法 3 落地
  「信使路由」。
  **实现**：messenger.py `run_wake(workspace, channel, user_id,
  session_id, text)` = cron agent job 同款链路——get_channel 预检
  （未配置分类报错）→ get_or_create_chat 幂等取 chat（三元组全等）→
  task_tracker.get_status(chat.id) 忙检 → workspace.stream_query(req)
  （dict 形态，request_context.suppress_console_push=True）→
  channel_manager.send_event 逐事件转发（基类只放行 message+Completed，
  base.py:2302；wecom 底层 aibot WS SEND_MSG 主动推送，无 webhook
  过期问题）。handler = POST /api/process-tools/wake-channel（**并入
  web_api 既有 router**，见下方踩坑；get_agent_for_request 按
  X-Agent-Id 路由 workspace；忙→409）；notifier 侧 _wake_via_messenger
  （409→30s×20 重试，同 console 唤醒）+ _submit_messenger_task
  （httpx，MESSENGER_HTTP_TIMEOUT=330s > 端点
  WAKE_AGENT_TIMEOUT_SECONDS=300s）。deliver 分流：console=气泡+
  chat/task 双投递不动；非 console=无气泡（死信废弃）+信使，报告
  「IM唤醒✅/❌(原因)」。
  **关键内核锚点**：chat/task 的 payload channel 字段透传（console.py
  `_extract_session_and_payload`）；get_agent_for_request 优先级含
  X-Agent-Id header（agent_context.py:54）；stream_query 无并发保护
  （忙检必须插件自己做）；cron executor.py 是 stream_query+send_event
  的官方范本。全量 146 passed + 4 skipped。
  **待实机冒烟**：wecom 真实通知触发（装机重启后验证手机收到）。
- **踩坑：插件 HTTP prefix 插件级唯一**（0.5.0 首次装机实机踩坑，
  2026-09-12）：内核 `plugins/registry.py:262` 对同 prefix 二次注册
  raise ValueError——**同一插件也不行**，且失败导致整个插件加载回滚
  （loader 清理 startup/shutdown/tool 全部登记项，日志锚「Cleaning up
  failed plugin load」+「Unregistered all entries for plugin」）。
  修复=多端点全部挂同一 router（web_api.create_router 里
  messenger.add_wake_route(router)）。教训：**插件路由注册逻辑单测
  测不到**（单测只验证 APIRouter 本身）——涉及 register_http_router
  的改动必须实机重启验证。相关：pytest 项目根直导的相对 import
  （web_api import messenger）要 try/except 兜底（notifier 同款）。
- **0.5.1 踩坑+修复：并发完成通知丢失**（2026-09-12 作者实机 bug 报告）：
  现象=多进程「仅完成通知」触发时刻接近时只达一条（#8/#9 同刻 2/2
  复现；#6 先触发反被丢——agent 正处理前一条通知的唤醒回合）。根因=
  **stream_query 不向 task_tracker 登记**（running 只有 console 路径
  attach_or_start 写）→ run_wake 忙检对信使自身并发失明 → 同 session
  并发多个 agent 回合（未定义行为）。修复=`_SESSION_GATES` 会话门闩
  （键四元组 agent/channel/user/session）：locked 检查→acquire 同步
  原子（asyncio 单线程无 await 间隙），撞锁回 busy → notifier 复用
  30s×20 重试；finally 释放（完成/失败/超时全覆盖）。附：周期快照
  「0m50s 而非 0m30s」不是 bug——sleep 从注册时刻起算，注册前进程已
  跑 ~20s（报告判断「行为可解释」正确）。教训：**忙检依赖的信号源
  要验证「谁在写」**——tracker 是 console 专写的，信使不能白嫖。
- **0.6.0 已实施：会话聚合器**（2026-09-12 作者设计拍板，当日闭环）：
  语义=同会话通知进累积器（内存态，宿主关/崩即丢无恢复——作者明确）
  → 首条通知启动 flusher 睡 3s（FLUSH_WINDOW_SECONDS）聚合 → 忙则
  3s 粒度忙等**无上限永不放弃**（期间新通知继续累积，重试批次含新
  到者）→ 空闲取走全部聚合一条投出（标头「【进程通知聚合 ×N】」+
  分隔线；console=一气泡+一唤醒；IM=一信使回合）。**结构简化**：
  `_try_wake/_wake_via_messenger` 单次化（`_wake_once/_messenger_once`
  三态 ok/busy/fail），删 30s×20 重试循环——重试节奏单点归 flusher。
  气泡时序=唤醒 ok 才发（busy 重试不刷屏）；fail 消费本批不重试。
  flusher 生命周期=items 空 return + flusher_task=None，新通知懒启动。
  **实现坑**：deliver 的 key 是四元组（+channel），_deliver_once 拆包
  必须 key[:3]（首跑 too many values to unpack 炸了 flusher）。聚合器
  天然串行化同会话投递（0.5.1 gate 的上层加固）。**子 agent 盲审**（零
  上下文 10 项清单全 PASS，可发布）：4 低危已闭环——messenger 三处
  「30s×20」过时注释修正、notifier 重复注释行删除、补 flusher 异常
  路径测试（items 保留+task 复位+懒启动再投）；dict 永不回收记录为
  已知权衡（受会话数约束，符合内存态拍板）。156+4 测试。
- **macOS zsh 分支未实机验证**（Linux 已 Debian 37/37 核销，同 POSIX 路径风险低）。
- 远期组：PTY 双后端、`qwenpaw:chat-reload` 上游需求、周期通知 token 实测、气泡 60s 过期补偿、run_at workspace 级键漂移、「按 OS PID 操作任意进程」搁置。
- 多 agent 真机并发隔离未测（单测+子代理双向不可见已过）。
- 通知投递最终形态（0.6.0 实机闭环）：全部会话统一「聚合器」——3s 窗口聚合一口气投递，忙等无上限（内存态，宿主关/崩即丢）。console=聚合气泡+单唤醒回合（agent 亲验）；非 console=信使回合回复送回 IM（wecom 作者实测）。0.5.1 gate 保留为 API 直调的兜底防并发层。
