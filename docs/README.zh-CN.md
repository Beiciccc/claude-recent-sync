# Claude Recent Sync

[English](../README.md)

Claude Recent Sync 是一款在同一台 Mac 上比较、备份并镜像不同账号 Claude Desktop Code Recents/会话入口的本地工具。

![Claude Recent Sync 控制台](dashboard.png)

> [!IMPORTANT]
> 本项目是非官方本地工具，与 Anthropic 无关联。它只镜像 Claude Desktop 的本地 Code Recents 索引，不能更新 Claude.ai 网页端、手机端的云端对话索引、上下文记忆或其他服务端账号数据。

## 适用场景

Claude Desktop 会按账号分别保存 Code 侧栏会话入口。切换账号后，Claude Code 的完整转录文件可能仍在本机，但新账号对应的 Recents 入口会缺失、过旧或指向旧分支。

本工具将一个本地账号作为权威**来源**，先比较差异，再备份**目标**，最后让目标账号的索引与来源完全一致。它会处理任意数量会话的新增、更新、分支变化和删除，并不限定为固定的 8 条或其他数量。

## 主要功能

- 自动发现本机账号及 profile/organization 目录。
- 按会话比较标题、分支 ID、轮数、更新时间和工作目录。
- 一次操作完成来源检查、冲突处理、备份、镜像、转录引用检查和延迟稳定性验证。
- 只结束可能回写旧分支的目标账号后台进程。
- 每次写入前自动创建带时间戳的完整目标索引备份，并支持从运行记录恢复。
- 可选账号切换后自动同步和登录时启动。
- 服务仅监听 `127.0.0.1`，不会复制凭据、Cookie 或转录正文。

## 环境要求

- macOS 12 或更高版本
- Python 3.10 或更高版本
- 同一台 Mac 上已有 Claude Desktop 和本地 Claude Code 会话数据

Node.js 只在修改并重新构建前端时需要，安装后的应用不依赖 Node.js。

## 安装

```bash
git clone https://github.com/Beiciccc/claude-recent-sync.git
cd claude-recent-sync
./scripts/install-macos-app.sh
open "$HOME/Applications/Claude Recent Sync.app"
```

应用会安装到：

```text
~/Applications/Claude Recent Sync.app
```

安装脚本会将 Python 模块和编译后的界面一起打包，并进行本地 ad-hoc 签名，不需要管理员权限。

## 使用流程

1. 在 Claude Desktop 登录需要接收会话入口的新账号。
2. 确认“上个账号 · 来源”是记录正确、内容最新的权威账号。
3. 确认“当前账号 · 目标”是刚登录的新账号。
4. 检查新增、更新、删除和一致的会话列表。
5. 点击“开始安全同步”，等待延迟验证完成后再关闭应用。

> [!CAUTION]
> 镜像是单向操作。为了让目标与来源完全一致，目标独有的 `local_*.json` 会被删除。工具会在写入前完整备份目标索引，但开始前仍应仔细确认同步方向。

更新软件时执行：

```bash
git pull --ff-only
./scripts/install-macos-app.sh
```

## 同步范围

Claude Desktop 的 Code Recents 索引通常位于：

```text
~/Library/Application Support/Claude/claude-code-sessions/<account-id>/<profile-id>/local_*.json
```

每个索引通过 `cliSessionId` 指向本机 Claude Code 转录文件，通常位于：

```text
~/.claude/projects/.../<cliSessionId>.jsonl
```

工具只镜像小型索引文件，并检查对应转录文件是否存在。它不会复制 JSONL 转录正文、OAuth Token、Cookie、账号凭据、Claude.ai 云端对话或云端记忆。

## 安全同步机制

每次可视化同步都会：

1. 重新识别当前账号和选定的来源账号。
2. 解析两侧实际使用的 profile/organization 目录。
3. 验证所有来源索引及其转录引用。
4. 比较来源和目标的每个会话。
5. 只停止正在引用待替换或待删除旧分支的目标后台进程。
6. 完整备份目标索引目录。
7. 镜像新增、更新和删除。
8. 验证写入后的目标并与来源反向比较。
9. 等待 Claude Desktop 状态稳定，再次验证；必要时自动校正一次。

默认拒绝用空来源清空目标，防止因来源识别异常造成误删。

## 自动账号切换

设置页提供两个默认关闭的选项：

- **账号切换同步**：记录实际处于活动状态的账号；检测到换号后，将换号前账号作为来源、换号后账号作为目标。
- **登录时运行**：安装用户级 LaunchAgent，在 macOS 登录后启动本地服务。

建议先手动观察几次同步方向和结果，再启用无人值守同步。

## 备份与恢复

备份和应用状态保存在：

```text
~/.claude/backups/claude-recent-sync/
~/.claude/claude-recent-sync/
```

在“运行记录”页可以查看历史任务、在 Finder 中打开备份或执行恢复。恢复旧备份前，工具也会先备份目标当前状态。

## 命令行

```bash
./bin/claude-recent-sync list
./bin/claude-recent-sync diff --from previous --to current
./bin/claude-recent-sync mirror --from previous --to current --dry-run
./bin/claude-recent-sync mirror --from previous --to current
./bin/claude-recent-sync doctor current
./bin/claude-recent-sync ui
```

需要保留目标独有索引时，可以在镜像命令中增加 `--no-delete`。账号别名有歧义时，可直接使用账号 ID 或唯一前缀：

```bash
claude-recent-sync mirror \
  --from <来源账号前缀> \
  --to <目标账号前缀> \
  --dry-run
```

手动命令中的 `current` 从以下文件识别：

```text
~/Library/Application Support/Claude/cowork-enabled-cli-ops.json
```

`previous` 表示除当前账号外、Code Recents 索引更新时间最新的账号。自动换号监听器则会单独记录换号前实际处于活动状态的账号。

## 常见问题

- **未识别当前账号**：打开 Claude Desktop，完成登录后刷新控制台。
- **来源验证失败**：确认对应转录仍存在于 `~/.claude/projects`；来源存在损坏索引或缺失转录时不会写入目标。
- **没有自动打开浏览器**：运行 `./bin/claude-recent-sync-ui`，再打开 `http://127.0.0.1:47631`。
- **端口被占用**：运行 `./bin/claude-recent-sync-ui --port 47632`。
- **安装版无法启动**：查看 `~/.claude/claude-recent-sync/launcher.log`。

## 隐私与限制

- 所有同步都只发生在本机，不会将会话数据上传到 GitHub、Anthropic 或其他服务。
- 索引和备份中包含会话标题、本地路径、账号/profile ID、时间戳等元数据，应像其他 Claude 本地数据一样妥善保护。
- 工具依赖 Claude Desktop 当前未公开承诺稳定的本地目录结构，未来客户端更新可能需要适配。
- 安装器和后台进程处理目前仅支持 macOS。
- 本工具无法让本地 Recents 出现在 Claude.ai 网页端或手机端，这些界面使用 Anthropic 管理的服务端数据。

## 开发与测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

重新构建 React 界面：

```bash
cd frontend
npm ci
npm run build
```

生产构建会写入 `src/claude_recent_sync/web_dist`，由 Python 标准库 HTTP 服务提供本地访问。

## 许可证

[MIT](../LICENSE)
