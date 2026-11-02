> 历史 v0.2/v0.3 设计资料，非当前验收结论。当前实现、限制和测试以 README.md 与 docs/TEST_REPORT.md 为准。

# 智能混合工作区：Portable AI Agent v0.3

> 2026-11-02 工程样机。**已实现并完成离线测试**：首次授权复制、双向 SHA-256 增量同步、单边删除的单独审批、跨计算机冲突检测、本地写前快照、事务日志、中断后的安全核对、目标端回滚、GUI 同步按钮与自动同步策略。**尚未完成**：真实 Windows 10/11 实机及 EXE 集成测试、云端 AWS 同步、文件内容合并器、可靠的物理断电/故障注入测试、恶意本地进程对抗与完整数据加密。

## 1. 主体数据分布

```text
USB (盘符可变)
└─ PortableAI/
   ├─ PortableAI.exe                   [Windows 打包后才存在]
   ├─ app.py, portable_ai/            客户端核心
   ├─ config/hybrid_sync.json          {version, mode, project_id}
   ├─ memory/memory.sqlite3            记忆的主副本（当前未加密）
   ├─ plugins/, toolchains/            插件和便携工具链
   └─ workspace/                       源码 / 文档 / 小型必要资产

目标 Windows 电脑（每个用户）
└─ %LOCALAPPDATA%/PortableAI/
   ├─ workspaces/<project_id>/         默认工作目录（用户可选其他目录）
   ├─ caches/<project_id>/             pip、npm、NuGet、DOTNET 等缓存
   └─ sync/<project_id>/
      ├─ binding.json                  此电脑授权目录和自动同步许可
      ├─ state.json                    此电脑的上次共同文件指纹
      ├─ sync.lock                     文件级进程互斥锁
      ├─ transactions/*.json           已执行的文件写入/删除收据及恢复信息
      ├─ transactions/*.jsonl          逐项刷盘的审计事件
      └─ snapshots/<transaction_id>/  修改前的字节快照
```

U 盘 `project_id` 稳定，因此从 E: 换到 G:，同一台电脑能定位已有目录。不同电脑各自保存独立共同版本，不把上一台电脑的用户名/本地路径写进 U 盘配置。**本机工作区和恢复快照会在目标电脑留下数据**，不是“用完不留痕”。

**首次授权步骤**（不能绕过）：双击程序启动 → 点击“授权本机工作区” → 选择默认或自定义目录 → 确认写入权限 → 单独确认是否开启自动同步 → 阅读首次同步预览并确认。**首次同步未成功批准之前，自动同步不会启动，也不允许 AI 直接修改本机绑定工作区。**以后仅对已绑定且首次同步已审核的工作区运行自动同步。

## 2. 自动触发与同步范围

| 触发 | 允许的动作 | 限制 |
|---|---|---|
| 首次目录授权后的同步 | 预览并经本人批准后复制 | 任何双方已有同名且不同内容的文件一律冲突 |
| 已授权电脑下一次启动 | 自动扫描，自动复制单边变化 | 自动模式已启用，且全批无冲突、删除、阻止项 |
| AI 任务成功结束 | 自动扫描与安全复制 | 与上同；任务期间不会同时启动同步 |
| 程序运行每 5 分钟 | 自动扫描与安全复制 | 与上同；程序必须运行，非系统服务 |
| 点击“同步预览/执行” | 本人审查后执行 | 删除需第二次单独批准 |
| 点击“回滚最近同步” | 仅恢复收据中的目标端 | 当前文件必须仍等于事务写入的 SHA-256；自动同步暂停 |

默认**不进入同步**：`.git`、`.ssh`、`.aws`、`node_modules`、`.venv`、`bin`、`obj`、`target`、`build`、`dist`、`__pycache__`、IDE 元数据、`.env`、常见私钥/凭据扩展名和文件名。单文件默认上限 128 MiB；单批复制默认上限 1 GiB；目录默认最多扫描 30,000 个候选文件；向 U 盘写入时默认预留至少 8 GiB 空间。超过限制时停止批次，不会静默跳过整个项目并宣称“同步完毕”。

**关于构建产物**：`build/`、`dist/`、`bin/` 等默认只保留在电脑上，不会自动带回 U 盘。需长期保存的成品应手动导出到 `workspace/releases/` 等未排除的目录，先核对体积再同步。

## 3. 三方比对算法

每台电脑的 `state.json` 保存上次同步成功的共同版本 `base[path]=SHA256`。每轮扫描 USB (`u`) 与本机 (`h`) 文件的完整 SHA-256，**不依据设备本地时间**判定谁比较新。

| 条件 | 动作 |
|---|---|
| `u==h` | 两端内容相同，只更新共同指纹；包括两端都删除 |
| `base` 无该路径，只有一端存在 | 复制新文件到另一端 |
| `base` 无该路径，两端不同 | 冲突，不覆盖 |
| `u==base`，`h!=base` | 本机修改，回写 USB；若 h 缺失则提交删除候选 |
| `h==base`，`u!=base` | USB 修改，更新本机；若 u 缺失则提交删除候选 |
| 两端都偏离 `base` 且不同 | 冲突，不覆盖 |

发现跨两端文件名大小写相撞、超过单文件大小、Windows 不支持的名字、符号链接/目录连接点、读取异常、超过本轮限额时，停止该批次；不能跟随链接遍历到授权目录之外。修改前再算一次源文件与目标文件的 SHA-256。如果预览后任何文件发生变化，取消执行并要求重新预览。

**注意**：这是保守的文件级同步，不是 Git 自动合并或 CRDT。文件冲突需手工比对/备份/合并，在两端得到一致内容后再执行下一次同步；未来才考虑交给 AI 提供合并建议并由用户逐次确认。

## 4. 断开、断电、恢复

写入一个文件时：

```text
SCAN + PLAN
   ↓
VERIFY current hashes & available space
   ↓
SNAPSHOT old destination → host snapshots/<tx>/... (verified)
   ↓
PERSIST prepared transaction receipt + fsync
   ↓
COPY source → temporary file in same destination directory + fsync
   ↓
VERIFY SHA-256 of temp content and source
   ↓
os.replace(temp, destination)       [per-file best-effort replacement]
   ↓
VERIFY destination SHA-256
   ↓
PERSIST receipt and last-common hash on host
```

- U 盘突然拔出 / 应用异常退出：不把整个批次当作成功；已完成的文件保留，未完成的文件可以下一轮重新复制。未应用的暂存文件不会成为正式文件。
- 如果程序中断在替换之后、提交收据之前，下一次运行先读取 `prepared` 收据，比较目标端新旧 SHA-256。能确认已生效才补记回滚收据；无法判断时停止，要求人工检查。
- 无法在 USB 离线时继续读取 USB 上的程序及主记忆库。本机源码可以在普通文件管理器中继续访问，但程序本身是否保持运行取决于 Windows 与启动方式。
- 单个文件写入和多个文件同步**不是整批 ACID 事务**，断电、USB 控制器缓存、文件系统错误或介质损坏仍可能造成数据损失。`os.replace` 不能代替异地备份。
- 断线恢复当前按**文件粒度**进行，不提供大文件中间字节位置的断点续传。

## 5. 安全回滚范围

最近事务中每个文件都有 `before_sha`、`after_sha`、修改前快照路径和操作方向。用户点击“回滚最近同步”后：

1. 再读目标文件并核对仍是那次事务所写入的 `after_sha`；若后来有人编辑过，拒绝覆盖。
2. 把**当前目标版本**额外保存为回滚保护快照（便于人工抢救）。
3. 如本次同步是新建文件，删除目标端那个新文件；如是更新/删除，恢复旧版快照（校验 SHA-256）。
4. 仅作用于**同步目标端**，不直接改另一端；完成后自动同步关闭，必须人工检查下一轮双向传播方向。

若中途又发生异常，已恢复的文件会被记录，下一次不会盲目重复执行。回滚不能恢复用户在无本工具参与情况下删除且从未备份的文件；也无法保证修复磁盘硬件故障。

## 6. 独立命令行诊断

在 USB 项目根目录（已具备 Python 3.11+）：

```powershell
python scripts/hybrid_sync_cli.py status
python scripts/hybrid_sync_cli.py bind       # 首次授权，交互式确认
python scripts/hybrid_sync_cli.py plan       # 只读预览
python scripts/hybrid_sync_cli.py sync       # 同步前确认
python scripts/hybrid_sync_cli.py sync --deletions   # 删除需额外输入 DELETE
python scripts/hybrid_sync_cli.py recover    # 核对已记录的中断事务
python scripts/hybrid_sync_cli.py undo       # 回滚最近事务，交互式确认
```

## 7. 发布前还需要验证

- 在 Windows 11 NTFS + exFAT U 盘上检验 Python 3.11/3.12+ 的路径及文件锁语义；不同 USB 盘拔插、盘符变化和共享模式。
- EXE 打包后包含 Tkinter、模块路径和 Windows 原生库，并在没有 Python 的 Windows 电脑上测试。
- 多 GB 工程、开源软件仓库、病毒扫描程序占用文件、OneDrive 同步目录、长路径、Unicode 名称、Windows 文件锁。
- 本机快照目录的 ACL、占用空间上限、定期清理策略与用户数据清除向导。
- 独立离线加密备份（例如受控的加密外置盘或经客户端加密上传 AWS）；**本版本没有加密记忆和快照**。

相关平台依据：Python `pathlib.Path.is_junction()` 仅从 3.12 提供，因此本模块同时检查 Windows `lstat().st_file_attributes` 重解析点标记；参考 https://docs.python.org/3.12/library/pathlib.html 。
