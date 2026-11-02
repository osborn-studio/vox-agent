# Vox Agent

**语音优先的 AI Agent 驾驶舱：说出任务，看着 Agent 在限定权限内执行，再审阅有据可查的结果。**

[English](README.md) · [简体中文](README.zh-CN.md)

Vox Agent 由原生 macOS 驾驶舱、可运行于 Windows 的桌面外壳、Python Agent 引擎、
带共享线协议契约的 TypeScript 网关、Web 控制台和移动端伴侣应用组成。语音是主要
输入方式：听写内容实时流入输入框，由你配置的 LLM 润色后派发给 Agent；Agent 的
权限请求始终内嵌显示、范围可见，并留有审计记录。

> 本仓库发布的是**源代码**和未签名的 CI 测试版（见[安装桌面版](#安装桌面版)），
> 不是经过验收的正式版本。构建或部署前请先阅读[源码来源](#源码来源)和
> [限制说明](#限制说明)。

## 组成部分

| 路径 | 说明 |
|---|---|
| `engine/`、`runtime/` | Python 3.11+ 引擎：调度、Agent 执行、权限、记忆、组织策略、数据保留、流式语音、工具、插件，以及 FastAPI 路由（`engine/api/`）。 |
| `macos/` | 主力原生 macOS 驾驶舱（SwiftUI、Swift 6.2、Swift Package Manager）。为保持兼容，模块与持久化标识沿用旧名 `VoxLog`。 |
| `apps/desktop-tauri/` | 唯一可运行于 Windows 的桌面外壳（Tauri + Rust）。 |
| `packages/contracts/` | 事件及桌面/移动端接口共用的 Zod 线协议契约。 |
| `services/api-ts/` | 面向桌面端和移动端的 TypeScript 网关。 |
| `apps/web-console/` | 管理与运维 Web 控制台。 |
| `mobile/voxagent-companion/` | iOS/Android 伴侣应用（Expo），依赖供应链经哈希锁定。 |
| `crates/` | 输入输出与 whisper.cpp 语音识别的原生辅助库。 |
| `tests/`、`scripts/`、`security/` | 测试套件、构建/校验工具与依赖防护。 |
| `docs/` | API 参考、设计决策、安全说明与契约文档。 |

所有界面的设计规则见 [DESIGN.md](DESIGN.md)。贡献者与 Agent 的工作说明见
[CLAUDE.md](CLAUDE.md)（其他 Agent 经由 [AGENTS.md](AGENTS.md) 引导到该文件）。

## 安装桌面版

两个桌面应用的未签名测试版由两个手动触发的工作流在 GitHub 托管的 runner 上构建。
它们是 CI 构建，不是经过验收的正式版本：CI 构建不等于真机验证通过。

1. 打开仓库的 **Actions** 页面，选择 **Release DMG**（macOS）或
   **Release Windows**，找一次在 `main` 上成功的运行；维护者可以用
   **Run workflow** 重新构建。
2. 在该次运行的摘要页面，从 **Artifacts** 下载构件。需要登录 GitHub，构件 30 天后
   过期。
   - `vox-agent-v<版本>-dmg`：`VoxAgent-v<版本>-cloud.dmg` 和 `SHA256SUMS.txt`
   - `vox-agent-v<版本>-windows`：`VoxAgent-v<版本>-windows-x64-setup.exe`
     （NSIS，按用户安装）、`VoxAgent-v<版本>-windows-x64.msi` 和
     `SHA256SUMS.txt`
3. 用 `SHA256SUMS.txt` 校验下载的文件：macOS 上运行
   `shasum -a 256 -c SHA256SUMS.txt`；Windows 上在 PowerShell 中运行
   `Get-FileHash .\VoxAgent-v<版本>-windows-x64-setup.exe -Algorithm SHA256`，
   与对应行比对。

**macOS。** 打开 DMG，把 **Vox Agent** 拖进“应用程序”。应用只做了临时（ad-hoc）
签名，未经公证，首次启动时请右键点击应用并选择**打开**，再确认。

**Windows 10/11（x64）。** 运行 `setup.exe`（或 MSI）。安装包没有 Authenticode
签名，SmartScreen 会提示“Windows 已保护你的电脑”：点击**更多信息**，再点
**仍要运行**。如缺少 Microsoft Edge WebView2 运行时，安装程序会自动下载并安装。应用会自行
启动内置的引擎和语音后端；听写前请先在**设置**中填写语音识别（ASR）密钥和 LLM
密钥。本地 ASR 模型暂未内置。

## 快速开始

克隆仓库后，请在一次性虚拟环境中工作。仅克隆仓库不会启动任何服务、模型、麦克风或
付费服务商。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,mcp]'
npm ci
```

### 检查命令

迭代时先跑最相关的检查，提交前再跑完整的受影响范围。

```sh
# Python（外部 Claude CLI 端到端测试需单独选择运行）
.venv/bin/python -m pytest -q
.venv/bin/ruff check .

# TypeScript 契约、网关、Web 与移动端
npm run check --workspace packages/contracts
npm run check --workspace services/api-ts
npm test --workspace services/api-ts
npm run typecheck --workspace apps/web-console
npm test --workspace apps/web-console
(cd mobile/voxagent-companion && npm ci && npm run typecheck && npm test)

# Windows 桌面外壳（Rust）
cargo fmt --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --all -- --check
cargo test --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --locked

# 原生 macOS（需要 Xcode 26 / Swift 6.2）
cd macos && swift build && swift test
```

## 持续集成

GitHub Actions 只使用 GitHub 托管的 runner；任何 PR 都无法触达自托管 runner。

- [`verify.yml`](.github/workflows/verify.yml) 在每个 PR 和推送到 `main` 时运行：
  `swift-build`、`python-test`（3.11 与 3.12）、`python-lint`、`typescript-test`、
  `mobile-test`、`windows-tauri-test`，以及锁定版本的 RustSec 安全公告审计。
- [`release-dmg.yml`](.github/workflows/release-dmg.yml) 手动触发，构建 ad-hoc 签名的
  macOS DMG。DMG 仅作为工作流产物上传，不会发布为正式版本。
- [`qwen-local-spike.yml`](.github/workflows/qwen-local-spike.yml) 手动触发，在 Windows
  CPU 上评估本地 Qwen ASR 的可行性，只报告哈希、大小与耗时。

RustSec 任务使用锁定的安全公告数据库，且锁定版本最长有效 14 天。更新方法见
[docs/security/rust-advisory-audit.md](docs/security/rust-advisory-audit.md)。移动端依赖
锁定机制见 [docs/security/mobile-dependency-defenses.md](docs/security/mobile-dependency-defenses.md)。
原私有仓库的工作流定义以非执行形式保存在
[`.github/source-workflows/`](.github/source-workflows/README.md)，因为部分测试会读取它们。

## 安全边界

- 管理与组织操作的身份来自经过认证的主体，绝不取自请求体。
- 密钥存放在 Keychain、Credential Manager 或基于 keyring 的存储中，绝不写入提交的文件、
  URL、日志或事件载荷。
- 权限请求显示确切范围，批准或拒绝后仍保留带操作者的审计记录；不存在盲目批准操作。
- 原始音频和转写内容绝不进入安全审计日志。
- 数据保留与删除需要试运行证据、有界目标、幂等性和审计记录。

HTTP 与 WebSocket 接口见 [docs/API.md](docs/API.md)；架构决策见
[docs/decisions/](docs/decisions/)。

## 源码来源

本公开仓库始于 2026-10-16 对维护中源码树的一次审核快照，采用全新的根提交，不含私有
开发历史、PR、发行包、凭据、模型、审计报告或备份，详见
[SOURCE_SNAPSHOT.md](SOURCE_SNAPSHOT.md)。此后的开发在本仓库通过经过审查的 PR 持续进行。
开发版本号记录在 [docs/RELEASE.md](docs/RELEASE.md)。

## 限制说明

- 不提供 ASR 模型、原生依赖、服务商凭据、签名/公证、平台 SDK 或托管服务。
- 源码中的示例服务器地址和旧更新地址不代表可访问任何托管服务。请勿对不属于你的基础
  设施运行部署脚本。
- 检查通过、模拟器运行或 macOS 构建成功都不等于设备验收或发布资格。iOS、Android 与
  Windows 真机结论需要指明具体设备的证据。
- 录制的测试音频和可选的内置提示音未包含在内；macOS 应用会回退为合成提示音。

## 许可证

本仓库自有代码采用 [MIT 许可证](LICENSE)。第三方源码包、补丁和声明保留各自的许可证，
MIT 不覆盖这些条款。
