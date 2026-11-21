# Process Tools — QwenPaw增强多进程工具

让 QwenPaw Agent 具备完整的**后台进程管理能力**：启动、查看、等待、交互、通知——长任务不再阻塞对话。

GitHub 仓库：https://github.com/One-sixth/qwenpaw-plugin-process-tools

## 解决什么问题

QwenPaw 内置的 `execute_shell_command` 是为「快速命令」设计的，跑长任务时会遇到：

- **前台阻塞**：命令没跑完，agent 干不了别的，你只能干等；
- **超时即杀**：到点进程被终止，前功尽弃，且 agent 与进程彻底失联；
- **输出一次性返回**：大输出挤占上下文，中途进度不可见；
- **无进程概念**：命令一结束（或一被杀）就什么都查不到了，无法跟进。

如果你只想跑 `pip install`、`git status`，内置命令足够。但当你需要——

- 跑一个 10 分钟的模型训练，期间继续和 agent 聊别的；
- 启动一个开发服务器 / REPL / watch 进程，随时写指令、读输出；
- 长任务结束时 agent **自动收到通知并汇报结果**，不用反复问「跑完没」；
- 同时挂着多个任务，哪个完成了一眼看清；

——装这个插件。

## 六个工具

| 工具 | 干什么 |
|------|--------|
| `process_tools_exec` | 启动进程：前台等待结果 / 后台立即返回编号 / `detach` 完全脱离托管 |
| `process_tools_list` | 本会话全部进程一览（编号、状态、退出码、时长、命令） |
| `process_tools_check` | 查单个进程状态 + 输出日志尾部（按日志行呈现：程序尚未输出换行时可能显示「暂无输出」，此时以日志文件为准） |
| `process_tools_wait` | 等待一个/一批进程结束（超时只报「仍运行中」，绝不杀进程） |
| `process_tools_communicate` | 与进程交互：写入 stdin、增量读 stdout、发中断/强杀 |
| `process_tools_notice` | 注册完成/周期通知（单个/批量）：完成后按会话聚合（3s 窗口），气泡提醒 + 自动唤醒 agent 处理；`cancel=True` 取消注册并撤回尚未投出的通知（通知仅在会话空闲时投递） |

## 相比内置命令

| 能力 | 内置 `execute_shell_command` | process-tools |
|------|------------------------------|---------------|
| 执行方式 | 仅前台，阻塞对话 | 前台 / 后台托管 / 脱离托管 |
| 长任务 | 超时即杀，无法跟进 | 后台持续运行，随时查状态、读输出 |
| 等待收口 | 无 | `wait` 单个/批量等待，超时不杀 |
| 完成通知 | 无 | `notice` 气泡 + 自动唤醒 agent（单个/批量注册），长任务零轮询；`cancel` 可取消/撤回 |
| 进程交互 | 无 | stdin 写入 / stdout 环形缓冲增量读 |
| 多进程 | 无概念 | `#N` 编号管理（永不复用），批量等待与操作 |
| 大输出 | 全量挤进上下文 | 净化日志落盘，按需读尾部（省 token） |
| 进程终止 | 仅超时自动杀 | `sigint` / `sigkill` 精确控制（杀整棵进程树） |

## 典型用法

**长任务 + 完成通知**——对 agent 说「后台跑 `python train.py`，完成后通知我」：

```
agent: process_tools_exec("python train.py", background=True)   → 进程 #1
       process_tools_notice(1)
（agent 继续陪你干别的……训练结束时你收到通知，agent 被唤醒汇报结果）
```

**多任务并行收口**——挂三个任务，全部结束后一次性汇报：

```
agent: process_tools_wait("[1, 2, 3]")    ← 全部结束才返回（超时不杀）
   或: process_tools_notice("[1, 2, 3]")  ← 批量注册完成通知，零轮询
```

**取消通知**——计划有变，不等了：

```
agent: process_tools_notice(1, cancel=True)  ← 解除注册并撤回尚未投出的通知
```

**交互式进程**——启动 REPL / 数据库客户端，随时发指令、增量读输出：

```
agent: process_tools_exec("python", no_shell=True, background=True)
       process_tools_communicate(2, action="write_stdin", text="print(6*7)")
       process_tools_communicate(2, action="read_stdout")
```

**前端伴生（自动刷新）**：agent 被通知唤醒在后台干活时，已打开的 QwenPaw 页面会自动刷新并接上实时输出流——不用手动刷新页面，就能实时看到 agent 的处理回复。零配置，装插件即生效。

## 安装

```bash
qwenpaw plugin install /path/to/qwenpaw-plugin-process-tools
qwenpaw app
```

> - 无任何外部 pip 依赖（纯标准库 + QwenPaw 自带模块），装完即用。
> - ⚠️ `qwenpaw plugin install <URL>` 仅支持 **zip 归档**；Git 仓库请先 clone 到本地再按路径安装。
> - 依赖 QwenPaw 2.2.0 – 2.9.0。

## 跨平台行为

| 平台 | 默认 shell | sigint | sigkill |
|------|-----------|--------|---------|
| Linux | `bash -c`（回落 `/bin/sh`） | 可捕获，程序能优雅退出 | SIGKILL 整组 |
| macOS | `zsh -c`（回落 bash > `/bin/sh`） | 同 Linux | 同 Linux |
| Windows | `pwsh`（回落 powershell > `cmd.exe /c`） | ⚠️ 送 CTRL_BREAK；**shell 包装的命令常收不到**（实测 `pwsh → cmd → ping` 连发两次仍 running），返回里会附「实测」结果——要确定终止请用 `send_sigkill`，优雅退出走 `write_stdin` 约定指令 | `taskkill /F /T` 杀树 |

> Windows 下 `cmd.exe` 不认单引号，`> < & |` 等元字符需自行转义（或让 agent 用 `no_shell=True` 原生直启绕开）。进度条/TUI 程序按普通管道输出，一般会自行降级。

> **显式选壳**：`shell="pwsh" / "bash" / "cmd"` 可强制指定解释器（`auto` 为默认回落链）。`cmd` 仅 Windows 可用（POSIX 传会报错引导）：cmd 内建命令与 `%VAR%` 展开原样可用，引号开头的 exe 路径直接写、不需要 `&` 调用符。
>
> ⚠️ **Windows 上的 `bash`**：本机若无 Git Bash，`bash` 会解析到 `System32\bash.exe`（WSL 启动器）——实测它会把 `$变量` 与引号提前吞掉（`X=42; echo "VAR-$X"` 输出 `VAR-`，exit=0）。此时返回头会带警告；需要可靠脚本请用 `no_shell` 原生直启，或改用 `pwsh` / `cmd`。装了 Git Bash 的机器不受此影响。

> PowerShell 壳下，以引号开头、**看起来像程序路径**（含 `\` 或 `/`，或以 `.exe/.bat/.cmd/.com/.ps1/.py/.sh` 结尾）的命令会自动补 `&` 调用运算符——原样交给 PowerShell 会被当成「字符串表达式 + 意外标记」而报 `ParserError`。字符串表达式（`'A'+'B'`、`"abc" -eq "x"`、`"abc"[0]`、`.Length`）、纯字面量、管道/重定向/注释一律不动。
>
> 注意：`&` 只补在**命令开头那一条语句**上——用 `;` 或换行拼起来的多条语句，后面的引号路径不会被改写（`"…\a.exe" x; "…\b.exe" y` 仍会 ParserError），请拆成多次调用，或改用 `no_shell=True` 传 argv。

> **Windows 编码（v0.7.1 起）**：`encoding` 默认 `auto`=本机编码（取控制台输出码页）。**跑 Python 程序时建议加 `patch_win_py_utf8=true`**：会向子进程注入 `PYTHONUTF8=1`、把 PowerShell 控制台 IO 编码钉成 UTF-8，并强制按 utf-8 解码（此时 `encoding` 参数被忽略）。不开开关时行为与旧版一致，不碰控制台码页。输出里出现替换符（`�`）时会在**元信息区**（`check`/`read_stdout` 各自的分隔线之前；`exec` 前台为编码行之后、分隔线之前；超时分支在「输出末尾 20 行」之前）追加一行提示建议改用该参数（仅 Windows 触发）。
>
> ⚠️ **`auto` 的已知局限**：控制台码页是整机共享属性，可能被设成 65001（或被 `patch_win_py_utf8` 顶成 65001），而按 ANSI 写管道的原生程序（如 3.13 Python）仍输出 cp936 → 默认路径会错配、中文乱码（有乱码提示兜底）。同样地 **stdin 方向**也按同一个 codec 编码，错配时写中文会静默乱码——用 `patch_win_py_utf8=true` 或显式 `encoding="gbk"` 可解决。

## 已知限制

- 无前端终端模拟（xterm）——进程输出走日志与增量读取
- 非 console 频道（dingtalk/feishu/qq/wecom…）的通知为「信使模式」：完成/周期通知会唤醒 agent 跑一轮并把回复送回频道（单向通知文本不直接进 IM）
- 同会话通知聚合投递：3s 窗口内多条通知合并为一条消息/一个唤醒回合；会话忙则以 3s 粒度忙等（无上限、永不放弃，通知仅内存态——宿主关闭即丢）
- 环形缓冲仅 512KB，更早的输出读日志文件
- 极短进程（exec 返回时已结束）注册通知不会生效——返回「已结束：#N …（用 process_tools_check 查看结果）」引导，直接 `wait` 收结果即可
- `command` 传 argv 列表会被通道 JSON 字符串化：**必须同时设 `no_shell=True`**；漏了这个参数会被直接拒绝，并提示正确用法（不会降级成 shell 命令去执行）
- `check` 的「日志共 N 字节」是**日志文件大小**（已解码、按 UTF-8 与 LF 归一落盘），而 `read_stdout` 的偏移是**环形缓冲的原始字节空间**——两者不可混用，续读请一律用 `read_stdout` 回显里的 `[start,end)`，别拿日志字节数当偏移
- 日志按**解码后的文本**落盘：codec 配错时原始字节不留存（日志里只剩替换符 `�`），事后无法从日志还原——重要输出请配 `patch_win_py_utf8=true` 或正确的 `encoding`
