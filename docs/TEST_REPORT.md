# v0.4 第一阶段工程测试报告

记录时间：2026-11-02 12:13 UTC。环境：云端 Linux，Python 3.12.14。**不是 Windows 验收报告。**

## 来源

真实恢复并审查 Library 中 `PortableAI_SmartHybrid_v0.3_Source.zip`（63,449 bytes）。原包 SHA-256：
`8ee695ef912ab560db73460f6a7627b4122e6dd2c42c3ad56c67b2034857ee74`。
恢复后的原始基线 36 个测试全部通过。本轮进一步新增安全回归测试，发现并修复旧版普通写入缺少事务、FTS/版本缺失、同步写入/回滚竞态窗口、扫描错误可能混作删除等问题。

## 实际执行

| 检查 | 结果 |
|---|---|
| `python3 -m unittest discover -s tests -v` | 87 个测试，全部通过；原始输出见 TEST_RESULTS.txt |
| `python3 -m compileall -q app.py portable_ai scripts tests` | 通过 |
| `python3 scripts/offline_smoke.py` | 通过；6 个跨模块步骤，0 网络调用；见 SMOKE_RESULTS.json |
| Python 固定配方编译 | 临时测试目录实际编译成功，输出进入本地构建缓存而非源码目录 |
| GUI 逻辑测试 | 14 项 headless/mock 测试，含拒绝授权、重复操作、版本更新、文件回滚、云传输同意等；不等于可见 GUI 像素验收 |
| 原生 Tk GUI 启动 | 已尝试，失败：无 DISPLAY；没有伪报界面成功运行 |
| Windows 构建/独立 EXE | 未运行；当前 Linux 无 Windows 执行环境，没有 Wine/MinGW；未安装新工具 |
| Windows 10/11、无 Python 目标机 | 未运行 |
| 实际 DeepSeek/OpenAI 请求 | 未运行；只做协议 fake HTTP 测试，不使用真实 Key、不产生 API 费用 |
| USB 物理拔插/断电 | 未运行；中断仅为可控故障注入 |
| AWS/VPN/管理员操作 | 未运行、未部署、未购买 |

## 覆盖重点

- 路径穿越、Windows ADS/保留名、敏感文件、符号链接/重解析点拒绝。
- 审批拒绝、错误类型参数、未知工具、插件缺少 manifest/哈希变化。
- 普通写入快照与回滚、用户后改拒绝覆盖、损坏快照、中断收据恢复、未解决事务阻止新写入。
- 双向 SHA-256 基线、冲突保留、不静默删除、多电脑共同基线、U盘路径变化、空间不足。
- 同步期间目标新建/修改、回滚期间修改、扫描权限错误、损坏基线、锁并发、恢复完整性。
- SQLite FTS5、中文子串、项目隔离、去重、历史版本、旧版本更新拒绝。
- DeepSeek/OpenAI 工具调用协议、HTTP 网络失败/错误结构、重定向拒绝、工具步数上限。

## 未完成门槛

第一阶段仍不能标为全部完成：用户要求的无 Python 独立 Windows EXE 尚无构建及运行证据。
源码版可在具备 Python/Tk/FTS5 的环境运行；交付包提供 Windows 构建脚本与验收清单。
加密存储、恶意宿主 OS 沙盒、货币级预算硬限制、完整任务恢复队列尚未实现。
