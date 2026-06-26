# 言 (Yan)

让 AI 主动找你说话的推送系统。基于记忆和时段感知生成内容，每天在随机时间通过 ntfy 或 Telegram 发送。

## 架构

```
配置 → 调度(APScheduler) → 内容生成(时段+记忆+Claude API) → 推送(ntfy/Telegram)
```

系统分为云端和本地两部分：主程序（调度+内容生成+推送）部署在云服务器上 7x24 运行；MCP 服务和同步工具运行在本地，与 Claude Desktop 集成。

- **调度**：每天 0 点生成当日随机时间表，APScheduler 精确到秒触发，SQLite 持久化
- **内容**：时段标签 + 短期记忆(context.json) + 长时记忆(Ombre-Brain) 三路融合，调用 Claude API 生成
- **推送**：ntfy.sh 或 Telegram Bot 双通道，config.json 中切换
- **MCP 服务**：可通过 Claude Desktop 查看/修改推送配置

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置
cp config.example.json config.json
cp context.example.json context.json
cp schedule.example.json schedule.json
# 编辑 config.json，填入你的 ntfy topic 或 Telegram 凭证

# 3. 设置环境变量
export CLAUDE_API_KEY="sk-ant-xxx"

# 4. 启动
python main.py
```

## 环境变量

| 变量 | 说明 | 必须 |
|------|------|------|
| `CLAUDE_API_KEY` | Anthropic API Key | 是 |
| `NTFY_TOPIC` | ntfy 订阅主题（覆盖 config.json） | 否 |
| `NTFY_SERVER` | ntfy 服务器地址（覆盖 config.json） | 否 |
| `YAN_PUSH_LOG_PATH` | 推送记录日志路径（留空不写入） | 否 |
| `OMBRE_TRANSPORT` | Ombre-Brain 传输模式（http/mock） | 否 |
| `OMBRE_URL` | Ombre-Brain 服务地址 | 否 |

## 文件结构

```
├── main.py              # 主程序（调度+内容融合+推送控制）
├── send.py              # 发送模块（Telegram + ntfy）
├── utils.py             # 工具函数（时段判断、去重校验等）
├── ombre_client.py      # Ombre-Brain MCP 客户端
├── push_mcp_server.py   # 推送配置 MCP 服务（stdio/HTTP 双模式）
├── ntfy_sync.py         # ntfy 推送同步监听器
├── sync_push_log.py     # ntfy → yan.log 同步工具
├── sync_log.py          # ntfy → 本地推送记录同步
├── config.example.json  # 配置模板
├── context.example.json # 短期记忆模板
├── schedule.example.json# 时间表模板
└── requirements.txt     # Python 依赖
```

## 可选组件

### Ombre-Brain（长时记忆）

独立部署的长时记忆服务（[P0luz/Ombre-Brain](https://github.com/P0luz/Ombre-Brain)），让推送内容能引用过去的对话和事件。不部署时系统自动降级，跳过长时记忆，推送照常执行。

### MCP 服务

```bash
# stdio 模式（Claude Desktop 自动拉起）
python push_mcp_server.py --stdio

# HTTP 模式
python push_mcp_server.py
```

提供的工具：`get_push_config` / `set_push_config` / `get_push_schedule` / `set_push_style` / `get_push_log`
