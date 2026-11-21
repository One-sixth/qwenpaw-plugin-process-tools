# 项目记忆

> 关键决策、踩坑与裁决。按主题分类；历史轮次的**过程细节**已压缩为结论行，完整叙事见 git 历史中的旧版（file_tools 历史 #69 可回溯）。

---

## 当前状态速览

- **版本**：v0.7.1（UTF-8 收敛为显式开关）：`encoding` 默认恢复 `auto`（本机编码，0.6.x 老行为），`local` 移除（与 auto 同义，显式传报错并提示省略）；新增 `patch_win_py_utf8=False`——**仅 Windows 生效**、跑 Python 推荐开启：① 子进程注 `PYTHONUTF8=1`（只补不盖、不改宿主 os.environ、detach 同注）；② PowerShell 控制台编码三钉（`[Console]::OutputEncoding/InputEncoding` + `$OutputEncoding`，**无 chcp**）；③ 工具侧强制 utf-8 解码并忽略 encoding（返回信息注明「encoding 参数被忽略」）。新增 U+FFFD 乱码提示（**仅 Windows**——提示指向的参数是 Windows 专属；exec 前台含截断/超时 + check + read_stdout）。0.7.0 的「默认 utf-8 + 无条件补丁」经实机矩阵判定副作用过大已回退（见陷阱 14）。全量 **262 passed + 5 skipped**。pwsh 引号路径命令 `ParserError` 修复（`ps_quote_call_fix`，只处理开头一条语句）、`shell="cmd"` 支持（仅 Windows，走原始命令行）、以及三轮子 agent 黑盒测试后的六处修复（`&` 判定收紧 / JSON argv 引导 / sigint 实测回报 / write_stdin 有损告知 / no_shell 缺程序点名 / bash 落到 WSL 启动器时警告）均属同一 0.7.1 批次，见下方对应条目。0.6.9（list 通知注册标记：running 进程状态方括号内 🔔 标记——`[running 🔔周期900s+完成通知]`/`[running 🔔完成通知]`，作者定稿周期注册两个都显示、header 不加计数；已结束进程注册记录残留于 _notices 需 list 侧按状态过滤，cancel 后天然不显示；`summary_line(state_tag="")` 加可选参数、manager 不 import notifier 分层不破坏；`set_notice` docstring 补隐式不变量标注；check/wait 不加标记（作者拍板：🔔 只属 list 视图）；**三路无上下文对抗复审 A/B/C 后修复**：test mock 补 self 参（类属性挂载休眠地雷）、tools_schema.json 按最终 docstring 重生成（生成产物历史惯例每版同步）、断言位置化、补混合状态/恒等 2 用例；新增 tests/test_list_notice_tag.py 7 用例，全量 210 passed + 3 skipped——旧集合 1 条 importorskip 环境守卫转 passed 总数守恒；**2026-11-16 docstring 精简**：6 工具全砍 50-66%（exec/check/wait/communicate/list），子 agent 模糊请求盲测零试错通过）。0.6.8（exec cwd 语义对齐内核 `get_tool_base_dir`：空串默认值走 project dir→workspace→WORKING_DIR 三级回落；相对路径修 bug——旧 `os.path.abspath` 实际挂宿主进程启动目录、与文档不符，新挂第一个项目目录；resolve 丢 to_thread 防断连网络盘卡事件循环；get_data_dir 等内部状态保持 get_workspace_dir 不动；plugin.json qwenpaw_version min 2.2.0/max 2.9.0 由作者手改；2026-10-17 已双树提交，重启生效，实机冒烟 3/3 PASS：默认 cwd→workspace、相对 cwd="tools"→{workspace}\tools（宿主 cwd bug 修复实锤）、~→<用户主目录>）。0.6.7：输出分隔线家族统一（check/wait 标签、read_stdout 净化输出标签、notifier 完成通知/周期通知文本融合式分隔线，已装机同步）。0.6.6：exec 前台返回新增输出分隔线 `————以下为stdout/stderr输出————`。0.6.5：notice 新增 `cancel` 参数（取消注册 + 撤回聚合缓存待发通知；swap-and-clear 无锁聚合器 + 三道闸；`read_log_tail` 修 TOCTOU）。0.6.4：meta.tools 逐条补 `category: "runtime"` + 插件更名「QwenPaw增强多进程工具」。0.6.3：命令/name 控制字符转义显示 `escape_ctrl` + 截断标记统一 `<<<TRUNCATED>>>`。历史：0.4.4 → 0.5.0 信使装机成功（prefix 踩坑后合并 router）→ 0.5.1 会话门闩修并发丢失 → 0.6.0 会话聚合器（双链路实机闭环）→ 0.6.1 notice 批量 → 0.6.2 meta.tools → 0.6.3 转义+标记 → 0.6.4 category+更名 → 0.6.5 notice cancel → 0.6.6 exec 分隔线 → 0.6.7 分隔线家族统一 → 0.6.9 list 通知标记。
- **测试**：**262 passed + 5 skipped**（skip 全为平台守卫/importorskip；0.7.1 encoding 段重写 + test_windows_utf8.py 重写 + test_ps_quote_fix.py 22 用例 + 乱码提示仅 Windows 2 用例 + shell="cmd" 7 用例 + 黑盒修复回归 6 用例）；必须用 `D:\Software\miniconda3\envs\qwenpaw\python.exe` 跑（系统 python 缺 agentscope）。
- **装机**：zip 热装方式（`git ls-files -z` 导出**无顶层目录** zip → `qwenpaw plugin install <zip> --force`，2026-11-16 切换，替代旧 git 工作树方式）。**0.7.1 已热装（2026-11-21）**：装机目录 `%USERPROFILE%\.qwenpaw\plugins\qwenpaw-plugin-process-tools` 实测版本 0.7.1 + 新语义在盘上（`resolve_encoding(None)→utf-8 auto` / `local` 报错 / PS 前缀不含 chcp / `patch_win_py_utf8` 默认 False）；打包脚本 `%TEMP%\build_pt_zip.py`（版本号取 plugin.json），回滚包 `dist/process-tools-0.6.9-rollback.zip`。⚠️ **工具绑定按回合装配**：热装发生在回合中途时，本回合后续调用仍是旧版——改动验证必须下一回合跑。**0.7.1 实机验收（下一回合，全过）**：默认路径 `auto→utf-8`（本机 OutCP 65001）子进程 3.13 出 gbk → 乱码且**提示如期出现**（老行为随机性的兜底生效）；`patch_win_py_utf8=True` → 中文+emoji 正常、头部注明「已强制 UTF-8，encoding 参数被忽略」、子进程 `utf8_mode=1`；显式 `encoding="gbk"` → 中文正常无提示；`encoding="local"` → 干净报错；py315 默认 → 正常；`write_stdin` emoji 往返正常；`check` 与 `no_shell` 两路提示一致生效。**零上下文子 agent 盲测（2026-11-21）**：① 直接说「用 Python 算…带中文」→ 子 agent 首次调用即自带 `patch_win_py_utf8=true`（schema 里的参数说明可发现性生效，零乱码）；② 只给一个内含 python 的 `.bat`（看不出是 Python）→ 默认路径出乱码 + 提示 → 自行改 `encoding="gbk"` 与 `patch_win_py_utf8=true` 两条路都试并都验证通过，还复述了「子进程 GBK / 工具按 UTF-8 解」的机理——**默认关 + 提示兜底对零上下文 agent 可自愈**。附带发现（既有坑，与本次改造无关）：pwsh 包装对带引号的 exe 路径仍抛 `ParserError`（`"path" -c` 需 `&` 调用符），子 agent 改 `no_shell=True` 绕过——**已在同批次修复**（见上方「pwsh 调用运算符补丁」条）。0.6.9 已装机 e2e 验证通过（🔔 标记 + 周期通知 + 完成通知全链路正常）。**0.7.1 重装（2026-11-21，并入 pwsh `&` 补丁 + 提示仅 Windows）**：zip **47 条目**——打包脚本改为**自动收集未跟踪文件**（清单 = `git ls-files -z` ∪ `git ls-files --others --exclude-standard -z`），此前手写 EXTRA 漏了 `tests/test_windows_utf8.py` 导致装机目录缺该测试；重装后 `utils.py` / `tools/exec.py` / 两个测试 / CHANGELOG / README 哈希与工作树全 SAME。⚠️ **装包建议走 `detach=True` + 装完另起命令看日志**：2026-11-21 两次观察到「装包期间/紧随其后启动的托管进程被同刻杀掉（exit=1、无输出）」；同期用 `Start-Sleep` 探针在装包前启动的 2 个进程则全部存活——**属于竞态，不是必杀**，机理未定；detached 装包自身不受影响，装完再起新命令读 `%LOCALAPPDATA%\Temp\pt_install*.log` 即可。打包脚本 `%LOCALAPPDATA%\Temp\build_pt_zip.py` 属临时文件（旧的在 `%TEMP%` 被系统清掉过），丢失后按上述清单重写。**`shell="cmd"` 实机验收（2026-11-21 下一回合，全过）**：`echo` 内建 / `%ComSpec%` 展开 / cmd 专属 `if exist … (…) else (…)` / 引号路径+参数 / 带空格目录里的 `.bat` + 参数 / 后台 + check + read_stdout 双行输出 / detach 写文件 / `exit /b 3`→exit=3 / 不存在命令→exit=1 且 stderr 合并进输出 / sigkill 杀 cmd 树（cmd 与子 `ping` 均 0 存活）/ `write_stdin` 中文+emoji 由子进程 `-u` python 读回（`GOT:中文输入😀`）；cmd + `patch_win_py_utf8` 中文正常；`auto` 下 cmd 内建中文随控制台码页（本机 CP 已漂到 65001，故 utf-8 正确、显式 gbk 反成乱码并触发提示）。回归：`auto`→pwsh 引号路径正常、`bash` 正常。**936 码页整轮复测（2026-11-21，`chcp 936` 后，全过）**：默认 3.13 中文 → auto→gbk **正常无提示**；强制 `encoding="utf-8"` → 乱码 + 提示；cmd 内建 `echo 中文` → gbk 正确；pwsh 引号路径（`&` 补丁）→ 正常；后台 + `check` + `read_stdout` 中文 → 正确；py315（`utf8_mode=1`）默认 → 乱码 + 提示（正是提示要引的那种场景）；开补丁后 3.13 / cmd / 引号路径三路 `中文😀` 全正常。**副作用实测**：开补丁后 `GetConsoleOutputCP()` 由 936 变 **65001**（PS 前缀 `[Console]::OutputEncoding` 连带改整机码页），与本机作者设定一致，故测完无需回滚。
- **四路子 agent 黑盒测试（2026-11-21）**：A 生命周期/并发、B 编码与壳、C 通知/视图、D 异常与文档。**C 全 PASS**（通知投递须会话空闲，测试内观测不到属预期）；A/B 的 FAIL 主要指向 `auto` 码页漂移（作者已定不改，README 补「已知局限」）；**D 抓出两个真 bug + 一个文案谎话，均已修**：① `ps_quote_call_fix` 把字符串表达式（`'A'+'B'`、`"abc".Length`）误当命令补 `&` → 判定收紧为「引号内像程序路径（含 `\`/`/` 或程序扩展名）」；② `command` 传 list 未设 `no_shell` 时通道 JSON 字符串化直接喂 shell，报 `“[”后缺少类型名称` → 新增识别并提示 `no_shell=True`；③ `send_sigint` 对 `pwsh→cmd→ping` 连发两次仍 running，旧文案却称「0xC000013A 已终止」→ 改为发送后等 1 秒实测并回报结果。另有若干 WARN 未改（`max_output_chars=0/-1` 归一为 1、命令回显与输出截断共用 `<<<TRUNCATED>>>`、no_shell 回显丢 argv 边界、list 不含 pid/日志路径、`wait` 为 all 语义），记入 README 已知限制/本文件。**第二轮盲测（2026-11-21 收尾）**：文档核对员 10 项全 PASS 并给出「能发布」结论（另提两处措辞已补：check 按日志行呈现、乱码提示在 check/read_stdout/超时分支的具体位置）；新用户视角复测核心路径通过，剩余问题全是已记录的限制（默认 auto 码页错配、多语句只修首条、argv 的 JSON 转义叠加、no_shell 不认内建命令）。连带补的两处修复：`write_stdin` 目标 codec 无法表示的字符不再静默（回执注明「注意：N 个字符无法用 <codec> 编码，已替换为 ?」，不附补充建议——作者 2026-11-21 定稿）；`no_shell` 找不到程序时点名 `argv[0]`。另实测记录：**热装进行中调用 `process_tools_exec` 可能被 governance 拒一次（`'ProcessToolsExec' is denied by policy`），重试即恢复**——属热装窗口内的瞬时态，不要当故障。

- **历史清洗（2026-11-21，作者授权）**：重写前的 `origin/main`（v0.6.9）历史里有 23 个提交的 MEMORY.md 含真实称呼/用户名。处置：`git bundle` 备份 → `E:\Data\backups\qwenpaw-plugin-process-tools-pre-filter-20261121.bundle`（298KB，`git bundle verify` 通过）→ 临时克隆里 `filter-branch --tree-filter`（脚本对**全部文件**做替换：真实称呼 → 「作者」/「助手」，邮箱与系统用户名 → 占位串）`--tag-name-filter cat -- --branches --tags` → 重写后 refs 命中 0 → 本地 `git fetch + reset --mixed`（**不动工作树**，未提交改动保持原样）→ `git push --force origin main / run / --tags` 全部成功（main 已重写并强推，标签全量重指；重写前的完整快照见 bundle 备份）。复扫：工作树 0 / 提交内容 0 / 提交消息 0 / 历史路径 0 / 身份仅 OneSixth。⚠️ GitHub 侧旧提交在 GC 前仍可能按 SHA 访问，彻底抹除需联系 GitHub 支持。**注意**：filter-branch 会在 clone 里留 `refs/original/*`，校验历史必须用 `rev-list main run --tags`（用 `--all` 会把 refs/original 与旧的 origin/* 算进来，误报命中）。

- **两轮零上下文子 Agent 复查均通过**（第二轮 A–H 八项 + 双解析器 18+15 用例实测零缺陷）。



---

## 装机与更新流程（重要）

- **zip 热装方式（2026-11-16 切换，替代旧 git 工作树方式）**：
  - 打包：`git ls-files -z` 导出**无顶层目录** zip（plugin.json 在 zip 根，arcname = git 相对路径）
  - 安装：`qwenpaw plugin install <zip> --force`（QwenPaw 运行时经 API 立即生效，无需重启）
  - 未 commit 变更需手动追加到打包列表（git ls-files 只含已跟踪文件）
  - 装机目录变干净拷贝（.git 消失），不再做 git 操作
- **⚠️ DEPENDENCY_ORIGIN_INVALID 根因（2026-11-16 实测）**：手动同步文件到装机目录（如 plugin.json version 变化）导致磁盘与宿主内存注册快照不一致 → QwenPaw 2.3.0b2 热装 reload 路径 `_read_source_identity` manifest 比对拒绝。**规则：不再手动同步文件到装机目录，一律走 zip 热装**。重启 QwenPaw 可临时解决（内存快照从磁盘重建）。
- **⚠️ 热加载不刷新已运行会话**：热装替换宿主模块，但当前会话工具绑定是启动时快照——改完工具行为后当前会话调工具仍返回旧结果，需新开会话（或重启）才用上新版。
- 历史：0.4.4-0.6.8 为 git 工作树方式（装机目录是工作树，严禁 qwenpaw plugin install）；0.6.9 起切换 zip 热装。

---

## 架构决策

- **subprocess + 管道，无 PTY**（跨平台优先）；无前端 xterm 控制台；**零外部 pip 依赖**（纯标准库 + QwenPaw 自带 agentscope/httpx）。
- **注册表 key = (agent_id, user_id, session_id)** 三元组；contextvar 由内核 `hooks/request_setup/contextvars_hook.py` 每请求注入，工具内直读 `qwenpaw.app.agent_context`。后台 asyncio.Task 创建时快照 context。
- **通知双投递**：气泡 `qwenpaw.app.console_push_store.append(session_id, text, sticky=True)`；唤醒 `agents.tools.agent_management.submit_agent_chat_task`（POST /console/chat/task，httpx 放 to_thread）。409 会话忙 → 30s 重试 ×20 上限。**0.4.4 起无开关，气泡+唤醒固定双投递**（wake_agent 参数已删：False 时 agent 行动链断裂，自由度弊大于利）。
- **通知投递时机 = 会话空闲才 flush（行为契约，0.6.5 起写进工具描述）**：通知入队后，agent 连续调工具期间（会话活跃）唤醒端点 409 → flusher 忙等挂起、气泡也不发——**活跃时通知压在队列里不出现，停手/回合结束那一刻才补发**。docstring ⚠️ 行只保留投递时机一句（作者定稿：够用即可）；两条推论留记忆不进描述：①验证「是否投出」勿连续 sleep/轮询等气泡（活跃永远等不到，两次实测误判 E1 的根源），等自己回合结束自然补发；②活跃时 cancel 几乎必中撤回（批次未消费或渲染前被 cancelled 标志过滤），停手超聚合窗口（约 3s）即投出失效。教训：**工具行为中 agent 不可见的时序特性必须前置写进描述**，只记项目记忆对零上下文 agent 无效。

- 导入与 file-tools 同款：相对导入 + try/except ImportError 双模式；docstring `Args:` 即参数 schema；`make_success/make_error` 统一 ToolChunk；6 工具全 async。
- 治理：全工具 `tool_type="shell"`；仅 exec `target_param="command"`。
- **工具时态三分（作者定稿）**：notice=未来事件（仅运行中可注册，已结束→error 引导 check）／check=当下与过去／wait=未完成。**勿回退**成 wait/check 并列引导。
- **notice 结果三段式（作者定稿 0.6.1）**：固定三段「已生效→不存在→已结束」按序拼接、空段跳过，单/批/混合共用一条生成规则；**逐个独立校验，有生效即 success、全失败才 error**，勿回退成整批连坐或逐行逐条引导（引导语每类最多一次）。
- **命令控制字符展示转义（0.6.3）**：`escape_ctrl`（`\n\r\t\b\f` 命名转义、C0/DEL/C1 用 `\xNN`、非 ASCII 原样）只包在展示层，存储 `mp.command`/`mp.name` 原串不动（spawn 用原文）；**先截断后转义**（控制字符单字符，截断点不劈开转义序列）；截断标记统一 `<<<TRUNCATED>>>`（对齐框架风格，历史条目不改写）。用户可控字段 = command + name 两个，展示点全仓 grep 枚举（命令 8 处 + name 10 处）；`log_path` 等插件自构路径无控制字符风险不处理。
- **pwsh 调用运算符补丁（0.7.1 同批次）**：PowerShell 把「引号开头的字符串」当**表达式**，后面跟参数即 `ParserError`（`表达式或语句中存在意外的标记`）——`"C:\Program Files\x.exe" -a` 必炸，补 `&` 才能执行。`utils.ps_quote_call_fix(command, shell_used)` 在 exec 组装 spawn 时**单点**调用（detach 走同一 spawn_target，一并生效）：只补「引号开头 + 引号后还跟参数」的形态（双/单引号都认；反引号与 `""`/`''` 双写按 PS 转义规则找闭合，未闭合不补）；纯字面量、`| ; , ) } > #`、`-eq/-like/-match/-replace…` 等字符串运算符一律原样；cmd/bash/sh/None 壳不处理。**显示层 `display_command` 保持原文**（回显不带 `&`），只有 spawn 串被改。测试 `tests/test_ps_quote_fix.py`（22 纯函数 + 2 接线：真跑引号路径命令成功、字符串管道不被改写）。**只处理开头那一条语句**（作者 2026-11-21 拍板「只补一句」）：多语句用 `;`/换行拼接时，后续引号路径不会被改写，属已知限制、已写入 README；扩展需 quote-aware 语句切分，暂不做。

- **`shell="cmd"`（0.7.1，仅 Windows）**：`SHELL_CHOICES` 加 `cmd`；Windows 返 `[%ComSpec% 或 cmd.exe, "/c"]`，POSIX 显式传报错「cmd 仅 Windows 可用」。**cmd 不能走 argv 形式**：① cmd 的 `/c` 有「命令以引号开头且引号多于两个 → 剥首尾各一个」的旧规则；② argv 还会叠 `list2cmdline` 的 `\"` 转义，cmd 不认（实测报 `'"...python.exe\"' is not recognized`）。故 exec 对 cmd **直接交原始命令行给 `create_subprocess_shell`**（Windows 上即 `COMSPEC /c "<命令>"`，外层引号剥离后正好还原原文）。cmd 分支不挂 PS UTF-8 前缀（`windows_utf8_prefix` 对 cmd 返回空），开关打开时靠 env 的 `PYTHONUTF8=1` 生效，实测中文+emoji 正常。

- **编码策略（0.7.1 作者定稿）**：默认 `encoding="auto"`＝本机编码（0.6.x 老行为，`GetConsoleOutputCP`），**子进程一个字节都不动**；跑 Python 时由 `patch_win_py_utf8=True` 显式开新世界：子进程注 `PYTHONUTF8=1` + PowerShell 控制台编码三钉 + 工具侧强制 utf-8 解码（忽略 `encoding`）。开关仅 Windows 生效，其他系统整体忽略。裁决链：0.7.0 的「默认 utf-8 + 无条件补丁」被实机矩阵否掉（GBK 资产被毁、`open()` 默认编码被改、码页被动漂移、`param()` 失效），收敛为 opt-in；`chcp` 移除（PS 的 `[Console]::OutputEncoding` 本身会调 `SetConsoleOutputCP`，chcp 冗余）；`local` 移除（与 auto 同义）。**禁止**把补丁改回无条件——副作用面已实测。乱码兜底＝输出含 U+FFFD 时追加一行提示（exec 前台/check/read_stdout 三处，**仅 Windows 触发**——提示指向的 `patch_win_py_utf8` 是 Windows 专属参数，其他系统不弹以免噪声）；**提示位置定死在元信息区**——编码行之后、`————以下为…————` 分隔线之前（超时分支则在「输出末尾 20 行」之前），程序输出段里绝不混入插件文本。**`auto` 语义作者拍板（2026-11-21）保持 `GetConsoleOutputCP` 不改绑 `GetACP`**：码页漂移属已知代价，靠提示兜底；改绑会让 `cmd /c` 内建在 65001 控制台解错。**漂移机制已实测闭环**：控制台先 `chcp 936`（OutCP=936、auto→gbk），再跑一次 `patch_win_py_utf8=True`，随后 `GetConsoleOutputCP()` 变回 **65001**——补丁的 `[Console]::OutputEncoding=[Text.Encoding]::UTF8` 会连带 `SetConsoleOutputCP`，且控制台码页是整机共享属性（子进程改完全局生效）。本机 65001 是作者故意设置（2026-11-21 说明），不属 bug。


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
10. **编码三坑（0.7.1 定稿）**：① `locale.getpreferredencoding` 会谎报 utf-8（宿主常设 PYTHONUTF8 时）；② `GetConsoleOutputCP` 会被子进程 `chcp` 等操作改写而漂移（本机实测 936→65001），`auto` 的结果因此不稳定——这正是补丁存在的理由，也是「默认 auto」的固有随机性，靠 U+FFFD 提示兜底；③ Python 3.15 管道默认 UTF-8 而 3.13 在中文 Windows 管道默认 GBK，错配时 `errors="replace"` 写进 U+FFFD 不可还原。
11. **通道双层 JSON 编码**（实链路实测）：工具参数会被通道字符串化成「外围引号+内部转义引号」双层（如 `"[\"35\", \"36\"]"`）——parse_process_ids/parse_env 均以**逐层 json.loads（最多 2 次）+ 病态变体受限兜底**（剥一次仅当剥完能解出目标类型才接受）处理；`""5""` 双层引号仍拒绝。
12. 已接受不修：半条 OSC/CSI 跨行残留裸 ESC；shutdown 最后一批 exit-listener 投递可能被取消；后台进程永不超时（timeout=0 前台真等，靠 description 提醒）；exec 超时错误分支不带 shell/encoding 行（聚焦超时本身）。
13. **`start` 拉常驻程序前台假超时（2026-11-02 auto_run 实测）**：`cmd /c 'start "" charmap.exe'` 前台必超时——charmap 继承 stdout 管道句柄不放手，`_read_loop` 等 EOF 等不到，`_monitor` 永不完成 → 前台 timeout 杀进程树（纯显示假象，charmap 本身正常）。对照实验：`ping -t` 继承管道同样超时；子进程三管道全 DEVNULL 则父进程退出即返回。**调用方加固**：拉起常驻程序的 Popen 加 `stdout/stderr/stdin=DEVNULL`（不继承句柄）；pythonw 无管道宿主天然免疫。

14. **0.7.0 实机矩阵观测（设计已被 0.7.1 回退，观测仍有效）**：0.7.0 曾「默认 utf-8 + 无条件补丁」，矩阵结论直接促成回退——以下条目只有**开关打开时**才会重现，或属于跨改造的固有事实：
    - **`PYTHONUTF8=1` 连带效应（开关打开时）**：子进程 `open()` 默认改 UTF-8——写文件变 UTF-8 字节，读 GBK 文件 `UnicodeDecodeError`；逃生阀 `env={"PYTHONUTF8":"0"}`。
    - **pwsh 管线内的原生命令依旧有损**（与本改造无关的固有事实）：PS 先按 `[Console]::OutputEncoding` 解码再编码，GBK 原生程序（ipconfig 等）中文会变 U+FFFD → 要原始字节只能 `no_shell` 或 `cmd /c` 直出。
    - **`chcp` 前缀已删除**（0.7.1），其全局码页副作用随之消失；`param()` 块失效同因消失。
    - **`local` 已删除**（0.7.1）：0.7.0 实测四个反例（py315 管道恒 UTF-8、`cmd /c` 内建跟码页、pwsh cmdlet 输出 UTF-8、经 PS 管线被转码）说明「按 GetACP 解」并不可靠——这是删掉它的实证依据。
    - **PS 管道喂中文给原生工具仍不可靠**：`"中文" | findstr "中文"` 无匹配（`$OutputEncoding` 变了但对方按 ANSI 比）。

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
| 0.6.3 | 命令/name 控制字符转义显示（`escape_ctrl`）+ 截断标记统一 `<<<TRUNCATED>>>` | 只动展示层，存储 `mp.command`/`mp.name` 原串不动；**先截断后转义**（控制字符单字符，截断点不劈开转义序列，无悬空反斜杠）；list/wait 摘要原"换行→空格"均换同款转义；name 10 处展示点同款包转义；标记对齐框架风格。**双盲复审教训：用户可控字段（command、name）的展示点枚举必须全仓 grep**——首版漏 wait.py 展示点 + summary_line 转义截断反序（悬空反斜杠），name 点位子 agent 也只列了 7/10 |
| 0.6.4 | meta.tools 逐条补 `"category": "runtime"`（UI 分组「运行与自动化」）+ 更名「QwenPaw增强多进程工具」+ description 焕新 | category 走 `GET /tools` 端点实时读 PluginRegistry 缓存的 manifest，**不进 agent.json 快照**——存量 agent 无需直改 agent.json，重启即生效（与 meta.tools 补快照条目机制分属两层，见知识库《QwenPaw插件工具可见性机制》）；与 file-tools v1.8.2 同款做法；更名同步 plugin.json/README/AGENTS，CHANGELOG 0.1.0 历史条目原文保留；复验脚本 workspace `tools/review_process_tools_v064.py` |
| 0.6.5 | notice 新增 `cancel` 参数（取消注册 + 撤回聚合缓存待发通知）+ 聚合器 swap-and-clear 化 + `read_log_tail` 修 TOCTOU | 设计与作者五轮推演定稿：PendingNotice 对象缓存 + **投递前一刻渲染**（完成通知冻结一致、周期通知时间戳更新鲜）+ 无锁三道闸（cancelled 短路 / cancel 清扫 / 渲染前过滤），cancel 全程无 await 点与 flusher 天然互斥；拿走即消费（ok/fail/异常均无删除操作），**异常批次从"保留"改"丢弃"**（杜绝毒丸批次，测试语义同步变更）；busy 放回仅 survivors（渲染失败不回炉）；互斥量方案因"flusher 消费含 HTTP 最长 330s、同步段本就原子"被无锁方案替代 |
| 0.6.6 | exec 前台返回新增输出分隔线 `————以下为stdout/stderr输出————`（元信息段与程序输出划界） | 范围四分：前台正常 + 超长截断均加（截断提示留分隔线上方）；**空输出不加**（`（无输出）`占位——"以下为输出"后面空荡荡反而误导）；后台启动不加（无输出段）；前台超时不加（已有「输出末尾 20 行：」引导）。改返回格式前先 grep 测试断言 |
| 0.6.7 | 分隔线家族统一：check/wait「输出末尾：」→ `————以下为输出末尾————`；read_stdout → `————以下为净化输出 [start,next) 共 N 字节————`；notifier 完成通知/周期通知文本同款补齐（零上下文复审抓漏） | **融合式**：标签语义合并进分隔线行，一行两用不增行数；偏移量/字节数/滑出提示是续读必需信息，不能因美化丢失；空输出占位与 exec 对齐（周期通知原"占位也套标签"一并修正）。**教训：改返回格式先全仓 grep 拼装点，别只盯 tools/ 主链——notifier 通知文本是独立演化的隐藏成员**；零上下文复审的价值=枚举完备性 |
| 0.6.8 | exec cwd 语义对齐内核 `get_tool_base_dir`：空串默认值三级回落（project dir→workspace→WORKING_DIR）、相对路径挂第一个项目目录（修旧 abspath 挂宿主 cwd 的 bug）、resolve 丢 to_thread 防网络盘卡事件循环、绝对路径 expanduser+resolve；get_data_dir/清理扫描等内部状态保持 get_workspace_dir 不动 | **对齐≠全盘照抄**：内核 docstring 明确 internal subsystems 禁用 get_tool_base_dir——插件数据跟着 project dir 走会落进用户项目目录且随会话漂移，工具 cwd 与内部状态要分家；wrapper 统一 try-import+回落保住无 qwenpaw 环境（测试/独立运行）；`resolve()` 真实走文件系统，断连映射盘符一碰就是 SMB 超时级阻塞，事件循环上必须丢线程（内核 run_sync_io 同款学费） |



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
- **0.6.1 已实施+实机闭环：notice 批量注册**（2026-09-12 当日设计→落地→复审→冒烟）：
  语义三轮迭代定稿：逐行报告→同类项分组→**三段式**（作者拍板）：固定三段
  「已生效→不存在→已结束」按序拼接、空段跳过，单/批/混合共用 _build_text
  （一个函数三个 if 零特例）；**逐个独立校验（部分成功语义）**：有效编号照常
  注册，无效编号单独报告不连坐；**有生效即 success、全失败才 error**（作者
  拍板；区别于 wait 的 all 语义，同插件两种批量语义并存合理）。批量全成功
  一行枚举；「新注册 vs 幂等更新」合并为「已生效」；单进程旧文案退役统一。
  实现：归档（oks/missing/ended）与注册均无 await 无并发缝隙；模式文本复用
  MIN_INTERVAL_SECONDS。**教训×2**：①凭记忆重写旧文案差点丢「进程 」前缀——
  行为等价性核对必须 `git show HEAD:file` 逐字比对；②测试断言把一行枚举误写
  成每进程一行（实现没错断言错）——debug dump 实值核对再改。**零上下文终审
  （第三轮）**：A-E 全 PASS（契约 7 条逐核对、schema 与 docstring 逐字一致、
  170+4 复现）；F 组扫出存量规约违规（真实名称字样一行，历史提交遗留）已修——
  **真实名称全仓 grep 应为发布终审固定项**。装机实机冒烟 5 项命中 + killed
  聚合 ×2 E2E（投递等 agent 回合结束，忙等时序正确）。170+4 测试。
- **macOS zsh 分支未实机验证**（Linux 已 Debian 37/37 核销，同 POSIX 路径风险低）。
- 0.6.1 复审遗留测试缺口（轻微不阻塞，后续版本补）：编号 0/负数路径、interval=30 边界、已注册 900→改 0 的反向更新、exit=None 理论路径（实际不可达）。
- 远期组：PTY 双后端、`qwenpaw:chat-reload` 上游需求、周期通知 token 实测、气泡 60s 过期补偿、run_at workspace 级键漂移、「按 OS PID 操作任意进程」搁置。
- 多 agent 真机并发隔离未测（单测+子代理双向不可见已过）。
- 通知投递最终形态（0.6.0 实机闭环）：全部会话统一「聚合器」——3s 窗口聚合一口气投递，忙等无上限（内存态，宿主关/崩即丢）。console=聚合气泡+单唤醒回合（agent 亲验）；非 console=信使回合回复送回 IM（wecom 作者实测）。0.5.1 gate 保留为 API 直调的兜底防并发层。
