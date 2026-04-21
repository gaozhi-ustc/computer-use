# Workflow Recorder — macOS 安装指南

适用版本：**v0.5.0**（及以后）
适用系统：macOS 11 Big Sur 及以上，Apple Silicon（M 系列）原生支持，Intel Mac 建议走 Rosetta

---

## 1. 下载

从 Release 页下载：

- **`WorkflowRecorder-0.5.0-macos.pkg`** （约 48 MB）

地址：<https://github.com/gaozhi-ustc/computer-use/releases/latest>

## 2. 安装

双击 `.pkg` → 按向导走：

1. **继续 / Continue**
2. **安装 / Install**（会要求你的 macOS 登录密码，因为要写到 `/Applications`）
3. 中途会弹出三个中文对话框，**依次填**：
   - **员工工号（Employee ID）**：你在公司的工号
   - **DashScope API Key**：`sk-` 开头的百炼 API key
   - **服务端地址**：公司部署的服务器 URL（例如 `https://cu.yesthing.cn`），默认 `http://localhost:8000`
4. 最后提示"Workflow Recorder is installed and running"，点 **OK**

> **如果你填错了或取消了任何一个对话框**
> 安装程序会装好 App 但不自动启动 daemon。可以手动编辑配置补齐：
> ```bash
> vim ~/Library/Application\ Support/WorkflowRecorder/model_config.json
> launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.workflow-recorder.plist
> ```

## 3. 授予屏幕录制权限（必须）

安装完后 daemon 会立刻尝试截屏，macOS 会弹出：

> **"Workflow Recorder" would like to record this computer's screen and audio.**

点 **Open System Settings** → 在列表里**勾选 Workflow Recorder** → 退出设置。

> ⚠️ **重要：授权后必须重启 daemon 才生效**
> macOS 不会把权限应用到已经在跑的进程。执行：
> ```bash
> launchctl bootout  gui/$(id -u)/com.workflow-recorder
> launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.workflow-recorder.plist
> ```

### 怎么判断权限到位了

截图的 temp 文件会短暂出现在 `$TMPDIR/workflow_recorder/captures/`，上传后会被清掉。如果你打开其中一张发现**只看得到桌面壁纸、看不到任何窗口**，说明权限没绑对——见下面的"故障排查 — 权限陷阱"。

## 4. 授予辅助功能权限（可选，但推荐）

录屏权限之外还有一个可选权限 **Accessibility**，用于捕获当前焦点控件的位置（Dashboard 回放时的**黄色焦点框**）。

**系统设置 → 隐私与安全 → 辅助功能 → +** 添加 `/Applications/WorkflowRecorder.app`。

不开这个权限 daemon 正常工作，只是 Dashboard 看不到黄色焦点框。

## 5. 验证正在工作

```bash
# daemon 在跑？
launchctl list | grep workflow
# 输出类似： 43381   0   com.workflow-recorder

# 最近在上传？
tail -5 ~/Library/Logs/WorkflowRecorder/stderr.log
# 应该看到: HTTP Request: POST <server>/frames/upload "HTTP/1.1 200 OK"
```

然后打开 Dashboard（你公司的 `https://cu.yesthing.cn` 或类似地址），登录后在「录制回放」找到**你的员工 ID**，应该能看到新 session 正在进帧。

## 文件位置速查

| 用途 | 路径 |
|------|------|
| App 本体 | `/Applications/WorkflowRecorder.app` |
| 配置文件 | `~/Library/Application Support/WorkflowRecorder/model_config.json` |
| 启动项 | `~/Library/LaunchAgents/com.workflow-recorder.plist` |
| 日志 | `~/Library/Logs/WorkflowRecorder/{stdout,stderr}.log` |
| 卸载脚本 | `/Applications/WorkflowRecorder.app/Contents/Resources/uninstall.sh` |

## 开机自启

装完后 daemon 已是 **LaunchAgent**：下次登录 macOS 会自动启动，崩溃自动重启（30 秒 throttle）。**不需要你做任何额外操作**。

## 常用操作

```bash
UID_NUM=$(id -u)
AGENT=~/Library/LaunchAgents/com.workflow-recorder.plist

# 启动
launchctl bootstrap gui/$UID_NUM $AGENT

# 停止
launchctl bootout gui/$UID_NUM/com.workflow-recorder

# 重启（授权变更后必用）
launchctl bootout gui/$UID_NUM/com.workflow-recorder
launchctl bootstrap gui/$UID_NUM $AGENT
```

## 修改配置后重启

编辑 `~/Library/Application Support/WorkflowRecorder/model_config.json`（例如切换服务器地址 / 换员工工号），**保存后要重启 agent** 才生效：

```bash
launchctl bootout gui/$(id -u)/com.workflow-recorder
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.workflow-recorder.plist
```

## 卸载

```bash
# 保留配置 + 日志（下次装回来员工工号不用再填）
bash /Applications/WorkflowRecorder.app/Contents/Resources/uninstall.sh

# 彻底清理（含员工工号 / API key / 所有日志）
bash /Applications/WorkflowRecorder.app/Contents/Resources/uninstall.sh --purge
```

如果 Resources 目录里找不到 uninstall.sh（手动删过 App），可以手动操作：

```bash
launchctl bootout gui/$(id -u)/com.workflow-recorder 2>/dev/null
rm -f ~/Library/LaunchAgents/com.workflow-recorder.plist
sudo rm -rf /Applications/WorkflowRecorder.app
sudo pkgutil --forget com.workflow-recorder.pkg
# 可选
rm -rf ~/Library/Application\ Support/WorkflowRecorder ~/Library/Logs/WorkflowRecorder
```

## 故障排查

### 一、截图里只有桌面，看不到打开的应用（**权限陷阱**）

**症状**：Dashboard 里看到的截图是纯色桌面壁纸，完全没有窗口内容。

**原因**：macOS 的屏幕录制权限（TCC）按**归属链**绑定。如果你先在 Terminal 里跑过一次 workflow-recorder（开发 / 调试场景），macOS 可能把权限绑到 Terminal 身上而不是 workflow-recorder 这个 bundle。LaunchAgent 重新拉起来时归属链丢失，权限不生效，但不会报错——只是截不到别的窗口。

**修复**：

```bash
# 清掉对这个 bundle 的所有 Screen Recording 授权记录
tccutil reset ScreenCapture com.workflow-recorder.daemon

# 重启 agent，这次从 launchd 发起，归属链干净
launchctl bootout gui/$(id -u)/com.workflow-recorder
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.workflow-recorder.plist

# 系统会再弹一次"Workflow Recorder 想录制屏幕"，点 Allow
# 授权完再重启一次，权限才会进程内生效：
launchctl bootout gui/$(id -u)/com.workflow-recorder
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.workflow-recorder.plist
```

### 二、daemon 起不来，日志里写 "exit 2"

配置文件缺 `employee_id` 或 `openai_api_key`。编辑 `~/Library/Application Support/WorkflowRecorder/model_config.json` 补齐两个字段，再重启。

### 三、`Bootstrap failed: 5: Input/output error`

launchd 瞬时状态错乱。等 2-3 秒再执行一次 bootstrap 通常就好。实在不行：

```bash
launchctl bootout gui/$(id -u)/com.workflow-recorder 2>/dev/null
sleep 3
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.workflow-recorder.plist
```

### 四、Gatekeeper 挡住了（`"Workflow Recorder" cannot be opened because Apple cannot verify it...`）

这个版本是 ad-hoc 签名（内部分发），Gatekeeper 默认不允许。两种解法：

- **右键 .pkg → 打开**（而不是双击），Gatekeeper 会给你"仍要打开"按钮
- 或在 **系统设置 → 隐私与安全** 底部点击「仍要打开」

### 五、还没生效？看日志

```bash
tail -f ~/Library/Logs/WorkflowRecorder/stderr.log
```

报错发给管理员或开 issue 时，附上这份 log。

## 关于数据与隐私

- Daemon 每秒（配置可调）截一次屏，**上传前会执行隐私过滤**：排除名单里的 App（如密码管理器）直接跳过不截；屏幕上的指定区域会被黑色蒙版盖掉
- 长时间无键鼠输入时自动进入**空闲退避**，最长 5 分钟截一次，避免浪费
- 所有截图发到你公司的私有服务器，不会经过第三方
- API Key 只存本机 `model_config.json`，不上传

----

**安装有问题？** 在 [GitHub Issues](https://github.com/gaozhi-ustc/computer-use/issues) 开 issue，附上 `~/Library/Logs/WorkflowRecorder/stderr.log` 的最近 30 行。
