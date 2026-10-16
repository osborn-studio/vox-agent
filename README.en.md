# Vox Agent source snapshot

See [README.md](README.md) for the source layout, local commands and limitations.

## 中文说明

Vox Agent 是语音优先的桌面工作台与 Agent 执行引擎。本仓库发布经过核验的
**当前源码快照**，采用全新的根提交；没有继承私有开发历史、PR、发行包、凭据、
模型、私有审计记录或备份。它不是已经验收的安装包或正式版发布。

源码包括 Python 引擎、macOS SwiftUI 客户端、Windows Tauri 客户端、共享契约、
网关、Web 与移动端。保留 `VoxLog` 模块名和开发版本字段用于兼容，没有借公开
源码的工作重新改造产品命名。

本仓库 GitHub Actions 关闭，原工作流仅以 `.yml.txt` 非执行参考保存；没有自动
PR 构建、自托管 runner 调度、签名发布或凭据迁移。原私有仓的开发、PR、发行版
和门禁保持独立。以后启用 CI 须先审查 runner、权限、不可信 PR 和费用。

本机运行方式见 README 和 CLAUDE.md。SDK、模型、原生依赖、云服务、签名与账号
需要自行配置；源码中的历史服务/更新地址不表示提供相应服务的访问权限。外部
Claude E2E 单独选择运行，不能把本机检查当作实际设备或发布资格。

旧测试音频/数组和可选打包声音文件未复制；保留数学样本生成器，缺少打包提示音时
既有 macOS 代码会回退到合成提示音。没有复制模型权重或应用二进制。项目自有代码
保持 MIT，第三方源码包、补丁及许可证分别保留原归属，MIT 不覆盖第三方条款。

详细来源、导出范围与维护方式见 [SOURCE_SNAPSHOT.md](SOURCE_SNAPSHOT.md)。
