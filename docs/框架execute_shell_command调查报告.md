# QwenPaw 内置 execute_shell_command 实现调查报告

> 2026-09-05 由零上下文子 Agent 调查（site-packages 安装版，共 1508 行），
> 服务于 process-tools v0.4.0 的「对齐框架惯例」决策。行号基于当时安装版，
> 升级 QwenPaw 后可能漂移，引用前先复核现场。

## 1. 函数签名与治理注册

`qwenpaw/agents/tools/shell.py`：

```python
@tool_descriptor(
    requires_sandbox=("shell_exec",),
    async_execution=True,
    tool_type="shell",
    target_param="command",
    policy_name="Bash",
)
async def execute_shell_command(
    command: str,
    timeout: float = 60.0,
    cwd: Optional[Path] = None,
    sandbox_config: Optional[Any] = None,  # 框架注入，但仍暴露在 schema 里
) -> ToolChunk:
```

- 返回 ToolChunk：成功=stdout（空则一句提示），stderr 非空缀 `\n[stderr]\n`；
  失败=`Command failed with exit code N.` + `[stdout]/[stderr]` 分段；
  超时/被杀 returncode 固定 -1。
- 预处理：`_collapse_embedded_newlines` 折叠嵌入换行；`_is_dangerous_self_kill`
  正则拦截会杀 QwenPaw 自身/父进程的 kill/taskkill（PID、$$/$PPID、危险进程名）。

## 2. env 处理（重点结论）

**A. LLM 侧不支持传 env**——框架刻意不暴露。

**B. 工具入口唯一加工**（增量叠加惯例）：

```python
env = os.environ.copy()
python_bin_dir = str(Path(sys.executable).parent)
env["PATH"] = python_bin_dir + os.pathsep + env.get("PATH", "")  # 前置
```

**C. 框架级 env 层 `envs.json`**（`qwenpaw/envs/store.py`）：
- 两层持久：envs.json（canonical，透明加密，chmod 600）→ 启动时
  `load_envs_into_environ()` 注入 os.environ；
- 注入语义：**不覆盖已有进程/系统变量**（overwrite=False）、
  `_PROTECTED_BOOTSTRAP_KEYS` 跳过；运行期删除仅当 os.environ 现值==旧记录值。
- shell 工具不直接读它，全靠 `os.environ.copy()` 自然继承（我们的插件同理免费获得）。

**D. 治理层 SandboxConfig.env_vars**（黑名单掩码注入）：
- `env_vars: Dict[str,str]`、`env_mode: "inject"`（"allowlist" 声明未实现，
  有 `report_unenforced_config` 不静默丢弃机制）；
- 来源：policy.yaml `env_blacklist`（默认 OPENAI_API_KEY 等 4 个），
  **手段=置空字符串而非删除**；
- 沙箱重建环境时显式桥接入口加工过的 PATH（`key.upper()=="PATH"` 大小写不敏感检测）；
- 各后端合并一律 `dict(os.environ) + update(env_vars)`（继承+覆盖，不删）。

**E. 类型守卫**：sandbox_config 非 SandboxConfig 实例（LLM 乱传 dict）→
warning + 丢弃回退无沙箱直跑，不崩不猜。timeout 字符串宽容转 float 失败回默认。

**合成公式**：

```
子进程 env = os.environ(宿主，含 envs.json 注入)
  ⊕ PATH 前置 sys.executable 目录
  ⊕ sandbox_config.env_vars（治理黑名单掩码，最后覆盖）
```

## 3. 输出解码：smart_decode 三板斧

```python
def smart_decode(data: bytes) -> str:
    try:
        return data.decode("utf-8").strip("\r\n")
    except UnicodeDecodeError:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        return data.decode(encoding, errors="replace").strip("\r\n")
```

- stdout/stderr **分独立临时文件捕获**（不用管道！Windows 子进程继承管道句柄
  会让 communicate() 挂死到所有句柄关闭——docstring 明说的实战坑），合并呈现。
- 限额双档：单流 1MB 截断附 notice；两流合计超 10MB 直接杀进程树
  （`QWENPAW_SHELL_MAX_OUTPUT_BYTES` 可调）。
- ⚠️ 不一致坑：沙箱路径硬编码 `decode("utf-8", errors="replace")` 无 locale
  回退——GBK 输出进沙箱路径必出替换符。

## 4. spawn/cwd/超时要点

- **无 no_shell/exec 直启模式**：永远经 shell。Windows 按 `shell_executable`
  三路包装：PowerShell 系 `[exe, "-NoProfile", "-NonInteractive", "-Command", cmd]`；
  cmd.exe `f'{shell} /D /S /C "{cmd}"'`；Git Bash 等 `[exe, "-c", cmd]`。
  POSIX `[shell or "/bin/sh", "-c", cmd]`。`Popen(shell=False)` 只是手工拼
  shell 包装。**我们的 no_shell/shell 枚举是框架没有的增量**。
- shell 选择：agent 配置 `running.shell_command_executable`（contextvar 每请求
  注入）→ `os.environ["SHELL"]` → 平台默认。
- cwd：相对挂第一个项目目录、绝对照用、expanduser+resolve（run_sync_io 线程）；
  注释明确 **cwd 不是权限边界**，治理规则才管权限；None → 项目目录→workspace→WORKING_DIR。
- 超时：monotonic deadline + 0.2s 轮询（Win 同步路径在 to_thread）；到期/取消
  用 **Job Object**（CreateJobObject+AssignProcessToJobObject）+ taskkill /T
  兜底杀全树；POSIX `cancellable_wait` + start_new_session 进程组清理；
  超时 rc=-1 + stderr 追加 "exceeded the timeout"。
- ⚠️ timeout 默认回填用 `== 60.0` 相等判断识别"用了默认值"——LLM 显式传 60
  会被 agent 配置悄悄顶掉；反面教材，正确姿势 None 哨兵。

## 5. 我们采纳了什么 / 拒绝跟什么

| 采纳（v0.4.0 已实现） | 不跟（留档警示） |
|---|---|
| env 合并=继承+update 增量，永不全量替换 | timeout `==60.0` 相等回填（None 哨兵才对） |
| PATH 无条件前置宿主 python 目录（`build_subprocess_env`） | 沙箱路径硬编码 utf-8 与宿主 smart_decode 不一致 |
| PATH 键大小写不敏感归并（Path/PATH） | env 黑名单置空非删除（CLI 分支语义漂移） |
| 字符串化数字参数宽容归一（早有 parse_int） | env_mode="allowlist"/max_processes 声明未实现 |
| 编码策略有意分歧：auto=单一诚实 codec，非 smart_decode 试错回退（见项目 MEMORY） | sandbox_config 裸露 schema 靠 isinstance 兜底 |
| 我们流式 reader 常驻读管道，与它「一次性等退出+临时文件」场景不同，不受管道句柄挂死制，但该坑在 v1 无 PTY 路线留档 | |
