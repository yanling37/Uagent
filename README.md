# Portable AI Agent v0.4 — 第一阶段工程候选版

**这是可运行、可测试的 Python 源码原型，不是已经验收完成的 Windows 独立 EXE。**
本轮从 Library 中真实恢复 v0.3 源码，完成安全补强并在 Linux/Python 3.12 上执行离线测试。
Windows 10/11、无 Python 电脑、真实 USB 拔插及实际付费 API 连接尚未验证。没有部署 AWS、安装 VPN 或修改本机系统。

## 开始使用

开发运行需 Python 3.11+（包含 tkinter 和 SQLite FTS5），无第三方运行依赖：

```console
python app.py
python -m unittest discover -s tests -v
python scripts/offline_smoke.py
```

Windows 可双击 `Start-PortableAI.cmd` 运行源码版。最终目标用户无需 Python 的 EXE 必须在 Windows 构建机打包，再经过独立 Windows 测试；参见 `Build-Windows.cmd` 与 `docs/WINDOWS_ACCEPTANCE.md`。本包没有伪装成 EXE 的启动器。

1. 将整个目录放在 U 盘/移动 SSD；先备份重要数据。
2. 手动启动，点击“授权本机工作区”，默认使用 `%LOCALAPPDATA%/PortableAI/workspaces/<project_id>`。
3. 检查首次同步预览。U 盘的 `workspace/` 为便携项目，本机目录为工作副本。
4. 选择 DeepSeek/OpenAI，手动输入 API Key；密钥仅在进程内存，不保存配置。
5. 输入任务。模型将收到任务、选取的记忆和工具读出的文件内容；对文件修改/编译/记忆写入逐项批准。拒绝不会执行相应操作。
6. 检查结果和同步状态；关闭程序前等待任务结束并同步，随后通过 Windows 安全弹出设备。

真实 API 请求会产生供应商费用。没有自动重试/付费模型切换；每任务最多 8 次请求、每请求输出限制 4096 tokens、每步最多执行 4 次工具。它不是人民币/美元硬预算器，输入 tokens 也计费；可通过 `Agent(..., max_steps=...)` 降低调用上限。不要以此替代供应商额度控制。

## 已实现

- Tkinter GUI，模型选择、审批差异预览、执行状态、授权工作区、同步/恢复、插件和记忆入口。
- DeepSeek Chat Completions / OpenAI Responses 适配器，假 HTTP 可注入，无 SDK 依赖；阻止重定向泄漏授权头。
- 有界多步结构化工具调用；写入、编译、记忆修改和文件回滚逐项审批。
- 普通文件 SHA-256 事务、修改前快照、同目录原子替换、恢复核对；拒绝覆盖审批期间或操作后用户的修改。
- Windows 保留名、盘符/UNC、路径穿越、符号链接/重解析点及常见凭据文件拦截。
- Smart Hybrid 双向 SHA-256/每台电脑共同基线、增量同步、冲突双方留存、不自动传播删除、磁盘余量限制、逐文件中断恢复和同步回滚。
- SQLite FTS5 与中文子串检索、项目/重要性字段、精确去重、乐观版本更新和历史版本；最多保留 30 份数据库备份。
- 插件清单、代码+manifest 哈希信任、工具权限声明校验；**可信进程内插件，不是操作系统沙盒**。
- 本机/便携工具链路径检测与固定编译配方；进程 PATH/缓存配置不永久改系统。

## 关键限制与未完成

- **本包不含已经构建并验证的 Windows EXE，第一阶段不能宣布验收完成。**
- 核心测试通过不等于真实 Windows GUI/NTFS/U盘断电测试通过。GUI 自动化仅按报告列出的范围算数。
- 文件路径检查和最终指纹校验不能抵御恶意宿主进程在系统调用之间的竞态；不要在不可信电脑或与恶意进程共享目录中使用。
- 记忆、恢复快照、项目和审计日志目前未加密；不要保存密码、私钥或敏感个人数据。凭据文件名过滤不能识别任意文件中的所有秘密。
- 本机工作副本和恢复记录会残留在该电脑，不是无痕运行。
- 插件是用户明确信任的 Python 代码，可访问当前用户权限下系统；manifest 不是安全隔离边界。卸载/撤销信任后需重启。
- SDK 未捆绑，自动工具链下载/签名校验/更新管理、任意命令终端、Git 自动操作暂未实现。
- 不实现管理员操作、系统 VPN/驱动、AWS 服务、远程控制、跨设备加密备份。
- 冲突需人工比较/处理；大文件断点续传、跨文件整体原子事务、强制系统断电保障未实现。
- 记忆自动摘要/语义合并、完整任务依赖图/崩溃后模型会话续跑、持久化任务队列未实现。

## 源码结构

```text
app.py                         GUI
portable_ai/agent.py            有界 Agent 协议循环
portable_ai/providers.py        HTTP API 适配器
portable_ai/workspace.py        文件事务/路径检查/回滚
portable_ai/hybrid_sync.py      Smart Hybrid 同步/恢复
portable_ai/storage.py          SQLite 记忆/历史/FTS/审计
portable_ai/tools.py            工具审批/插件 manifest
portable_ai/devkit.py           已安装工具检测/固定编译配方
plugins/sysinfo/                示例可信插件
scripts/offline_smoke.py        零网络端到端验证
tests/                         单元/协议/同步/安全测试
docs/                          验收说明和报告
```

## 接口参考

本轮查阅官方 DeepSeek 文档核对接口及 `deepseek-flash` 模型名：
https://api-docs.deepseek.com/api/create-chat-completion/
OpenAI Responses 接口参考：https://platform.openai.com/docs/api-reference/responses
模型供应商接口会变化；真实账户的模型访问权限、地区合规与余额需使用者确认，不绕过服务限制。

下一阶段优先：Windows 独立 EXE 构建及无 Python 电脑实测 → USB 拔插/断电/NTFS 验收 → 可移植加密设计 → 工具链签名与离线部署。
