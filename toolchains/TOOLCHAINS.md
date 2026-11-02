# Portable DevKit — Windows x64

本压缩包只包含源代码与工具链目录结构，**没有**包含 Windows 编译器/JDK/解释器二进制文件。选择官方发行版解压到下列位置后，本项目才能成为真正自包含的U盘开发环境。

| 组件 | 放置位置 | 官方来源 | 注意事项 |
|---|---|---|---|
| Python | `toolchains/python/python.exe` 或 `toolchains/python/<WinPython目录>/.../python.exe` | https://winpython.github.io/ | WinPython 解压版；选择与操作系统匹配的 64 位版 |
| Java JDK 25 LTS | `toolchains/java/<jdk-folder>/bin/java.exe` 和 `javac.exe` | https://adoptium.net/temurin/releases/?version=25 | 选择 Windows x64 **JDK ZIP** 而不是 MSI |
| .NET SDK 10 | `toolchains/dotnet/dotnet.exe` | https://learn.microsoft.com/en-us/dotnet/core/install/windows | 用 `scripts/Install-DotNet10.ps1`，或手动下载 SDK binaries ZIP |
| C/C++ | `toolchains/llvm-mingw/<folder>/bin/clang.exe` | https://github.com/mstorsjo/llvm-mingw/releases | 需要完整 LLVM + MinGW-w64 sysroot，不是单独 clang.exe |
| Node.js LTS | `toolchains/node/<folder>/node.exe` | https://nodejs.org/en/download | 下载 Windows x64 ZIP，保留附带的 npm 文件 |
| Git | `toolchains/git/cmd/git.exe` | https://git-scm.com/install/windows | 下载 PortableGit x64 发行版，解压后应具有 cmd 目录 |
| CMake | `toolchains/utilities/cmake/bin/cmake.exe` | https://cmake.org/download/ | 使用 Windows ZIP |
| Ninja | `toolchains/utilities/ninja/ninja.exe` | https://github.com/ninja-build/ninja/releases | 使用 Windows ZIP |
| FFmpeg | `toolchains/utilities/ffmpeg/bin/ffmpeg.exe` | https://ffmpeg.org/download.html | 选择有可靠来源的 Windows build |
| 7-Zip | `toolchains/utilities/7zip/7z.exe` | https://www.7-zip.org/ | ZIP 包或手动从合法发行版复制 |

将解压后的 JDK、Node、llvm-mingw 等保留在各自的单独子目录中，以免升级时破坏旧工具链。`Launch-DevShell.cmd` 在当前 CMD 会话内临时设置环境变量，不持久写注册表。

**安全与完整性**：从官方站点获取软件；保存版本号、SHA256、来源 URL。首次下载请检查官方校验值。避免脚本自动执行来自第三方的未经验证安装包。许可证因组件不同而不同，后续重发布需审核授权。

### 不保证完全便携的部分
- MSVC 和 Windows SDK、Android SDK 和大型 IDE 可能依赖系统安装或额外工具。
- 代码编译不等于运行沙盒。即使编译器从U盘运行，MSBuild、Java 注解处理器、构建脚本或可执行程序仍可访问宿主机权限范围内的文件。
- 某些学校或单位电脑禁用可移动设备执行、外部程序联网或管理员网络配置；U盘程序不能绕过这些政策。

### 智能混合工作区的构建缓存

从 GUI 在已绑定本机工作区执行编译时，`DOTNET_CLI_HOME`、`NUGET_PACKAGES`、`npm_config_cache`、`PIP_CACHE_DIR` 指向本机 `%LOCALAPPDATA%\PortableAI\caches\<项目ID>`，默认不参与 U 盘文件同步。运行 `Launch-DevShell.cmd` 时优先指向本机 `%LOCALAPPDATA%\PortableAI\caches\devshell`；如系统不提供该环境变量才回退到 U 盘 `data/`。
