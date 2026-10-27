# PD01 打印机 MCP：人类使用教程

这套程序现在有两部分：家里电脑负责连接打印机；云端中转站负责暂存电报。电脑会主动去云端取件，所以家里不需要固定公网 IP，也不需要保持入站隧道一直在线。

## 一、先把打印机接到电脑

### 1. 安装

```powershell
git clone 你的仓库地址
cd pd01-printer-mcp
setup_windows.cmd
```

脚本会创建 Python 环境、安装依赖，并生成不会提交到 Git 的 `.env`。

### 2. 配对打印机

1. 给 PD01 装好热敏纸并开机。
2. 关闭手机上的打印机 App。
3. 打开“Windows 设置 → 蓝牙和设备 → 添加设备”。
4. 选择 `PD01`，等待显示“已配对”。

“已配对，未连接”通常是正常状态。程序需要检查或打印时才会临时连接。

### 3. 启动本地 MCP

双击 `start_printer.cmd`。窗口出现 `Uvicorn running on http://127.0.0.1:4176` 就表示启动成功。保持窗口开着。

在同一台电脑上的 MCP 客户端填写：

```text
http://127.0.0.1:4176/mcp
```

第一次先让 AI“扫描打印机，不要打印”，再让它“检查打印机状态，不要打印”。

## 二、可选：启用不依赖 PC 入站隧道的云端电报

### 1. 部署中转站

选择一个能长期运行 Docker 容器、提供 HTTPS、身份验证和持久磁盘的云服务。用仓库里的 `Dockerfile.relay` 构建，并把持久磁盘挂载到 `/data`。

在云服务的环境变量中填写 `relay.env.example` 里的项目。用密码管理器生成一个长随机值作为 `RELAY_WORKER_SECRET`；也可以在自己的电脑运行：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

中转站必须整体放在带身份验证的 HTTPS 入口后面。共享密钥只保护电脑取件相关工具，并不能代替公网入口的登录验证。

### 2. 让家里电脑主动取件

用记事本打开仓库根目录的 `.env`，填写：

```dotenv
TELEGRAM_RELAY_URL=https://你的中转站域名/mcp
TELEGRAM_RELAY_ACCESS_TOKEN=你的入口令牌（没有就留空）
TELEGRAM_RELAY_WORKER_SECRET=与云端完全相同的长随机值
TELEGRAM_AUTOPRINT=1
```

保存后重新双击 `start_printer.cmd`。让 AI 调用 `telegram_worker_status`；看到 `enabled: true` 和 `configured: true` 说明电脑已经准备好取件。

电脑或网络临时掉线时，电报会留在云端。电脑恢复后会继续取件。不要删除 `telegram_delivery.sqlite3`，它用来防止“已经打印、确认回执却刚好断网”时重复出纸。

### 3. 两种发电报方式

- **每次确认：** AI 先准备电报并展示全文；你确认后，它才放进云端队列。
- **预先授权：** 云端设置 `ALLOW_AUTONOMOUS_TELEGRAMS=1` 后，AI 可以在限定时段、每日次数和字数内主动发电报。每条消息仍必须带唯一幂等键，防止重复入队。

默认采用每次确认。安静时段、每日上限和字数上限都在 `relay.env.example` 中设置。

## 三、日常使用

1. 让电脑保持开机并运行 `start_printer.cmd`。
2. 打开打印机，确认有纸。
3. 关闭会占用打印机蓝牙连接的手机 App。
4. 本地打印按“准备 → 展示全文 → 明确确认”的流程进行。
5. 云端电报会由电脑主动领取。

## 四、常见问题

### 电报不见了？

先让 AI 查 `telegram_status`：

- `queued`：还在等电脑取件。
- `leased`：电脑正在处理。
- `delivered`：电脑已记录打印成功。
- `review_required`：可能已经开始出纸，但连接中断；程序已停止自动重试，请人工看一眼打印机。

### AI 找不到打印机

- 按一下电源键唤醒打印机。
- 把打印机放到电脑旁边。
- 关闭手机打印 App。
- 检查 Windows 蓝牙和配对状态。

### `WinError 10048`

通常表示 MCP 已经运行。回到原来的黑色窗口，不要重复启动。

### 浏览器显示 400、404 或 406

MCP 不是普通网页。请用 MCP 客户端连接，不能用浏览器页面判断服务是否正常。

## 五、关闭

在本地 MCP 窗口按 `Ctrl+C`。电脑停止取件后，云端队列会保留尚未送达的电报。
