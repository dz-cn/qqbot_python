# qqbot_python

基于 **QQ 官方机器人 (botpy) + LangChain 1.0** 的智能陪聊机器人，核心特性是**分层记忆架构**与**上下文滚动压缩**。

## 功能特性

- 🤖 接入 QQ 官方开放平台，收发私聊 (C2C) 与群聊消息
- 🧠 三层记忆：原始消息层 / 滚动摘要层 / 事实卡片层
- 🗜️ 上下文压缩：未压缩消息超阈值时，把最老的一批滚动并入摘要并推进水位线
- 🃏 记忆卡片：模型自主判断是否调用工具沉淀长期事实，支持 **全局 / 局部** 两种作用域
- 🎭 场景化提示词：群聊、私聊使用不同的角色提示词
- 🧵 异步压缩：摘要生成放入后台线程池，不阻塞回复主链路
- 🔐 密钥隔离：API Key / Bot 凭据通过 `.env` + 环境变量管理，不进入代码与 git

## 技术栈

- Python 3.13
- [qq-botpy](https://pypi.org/project/qq-botpy/) — QQ 官方机器人 SDK（导入名是 `botpy`，安装名是 `qq-botpy`）
- LangChain 1.0 + LangGraph — 智能体与工具调用（`create_agent`）
- 模型：DeepSeek 等 OpenAI 兼容服务（`ChatOpenAI`）/ 本地 Ollama（`ChatOllama`）
- SQLite — 记忆持久化（WAL 模式，一次性连接）
- python-dotenv — 加载 `.env`

## 目录结构

```
qqbot_python/
├── MyClient.py          # botpy 客户端入口，注册消息事件（appid/secret 从 config 读取）
├── Main.py              # 入口
├── config.py            # 全局配置：模型、窗口、卡片、阈值等，每项带注释；敏感值走 os.getenv
├── requirements.txt     # 依赖清单
├── .env.example         # 环境变量模板（提交，重命名为 .env 后填入真实值）
├── agent/
│   ├── recat_agent.py   # 主流程：装配上下文 → 调模型 → 存消息 → 后台压缩
│   └── tools/
│       ├── agent_tools.py    # memory_card 工具（模型侧写卡片，支持 is_global）
│       ├── message_build.py  # 把卡片/摘要/原文拼成模型输入
│       └── middleware.py     # agent 中间件钩子
├── model/
│   └── factory.py       # 模型工厂 + 注册表（Ollama / DeepSeek 的 chat 与 summary）
├── session/             # 记忆层（对外只暴露门面 __init__.py）
│   ├── db.py            # 连接与建表（三层表结构）
│   ├── db_ops.py        # 所有 SQL
│   ├── memory_ops.py    # 卡片写入 + 上下文压缩业务编排
│   ├── current.py       # 用 contextvar 把会话身份隐式传给工具
│   └── __init__.py      # 包门面，统一导出
├── prompts/             # 提示词：system.text / group.text / user.text
├── utils/               # 路径解析、提示词加载
└── data/app.db          # SQLite 数据（运行时自动创建）
```

## 快速开始

### 1. 创建虚拟环境并安装依赖

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置密钥（.env）

把 `.env.example` 重命名为 `.env`（去掉 `.example` 后缀），再填入真实值：

```
DEEPSEEK_API_KEY=sk-你的真实密钥
QQ_BOT_APPID=你的机器人appid
QQ_BOT_SECRET=你的机器人secret
```

### 3. 运行

```powershell
python Main.py
```

首次运行会自动创建 `data/` 目录；数据库建表通过 `session.init_db()`（DDL 全为 `IF NOT EXISTS`，可重复执行）。

### 4. 使用
1. 配置好密钥并运行后，在QQ聊天框中找到对应的机器人即可开始对话。
2. 群聊中，需要让群主开启机器人接收所有聊天消息的权限，才会接收所有消息，否则只会接收 @机器人的消息。


## 记忆架构

| 层 | 表 | 作用 |
|---|---|---|
| 原始层 | `chat_message` | 全量对话原文；`msg_id` 遵循 `"{role}: {消息标识}"` 格式 |
| 摘要层 | `chat_summary` | 滚动摘要 + 水位线 `final_msg_id`（版本化，并发有冲突保护） |
| 事实层 | `memory_card` | 结构化记忆卡片，含 `importance` / `clarity` 打分（1~5） |

**每轮对话的上下文装配**：最近一条摘要 + 水位线之后 `RECENT_WINDOW` 条原文 + 相关记忆卡片（全局桶 + 当前会话桶）。

### 上下文压缩

- **触发**：未压缩消息数 > `COMPRESS_KEEP_RECENT` 才启动；否则跳过。
- **限量**：每次只把最老的 `COMPRESS_BATCH` 条并入摘要，空摘要绝不推进水位线。
- **安全比例**：`COMPRESS_KEEP_RECENT ≤ RECENT_WINDOW`（避免消息既没压又出窗而不可见）；`COMPRESS_BATCH ≤ COMPRESS_KEEP_RECENT / 2`（防上下文塌缩）。
- **异步**：压缩通过 `ThreadPoolExecutor` 在后台线程执行，不阻塞回复。

### 全局 / 局部记忆卡片

- `memory_card` 工具的 `is_global=true` 时，卡片写入 `openid="global"` 的**全局桶**，跨会话可见；
- 默认 `is_global=false`，写入当前会话真实 openid 的**局部桶**；
- 读侧 `load_cards` 按 `openid IN ("global", 当前openid)` 过滤，因此两类卡片都能被加载。
- `openid` / `scope` 等敏感标识由代码注入，模型只能表达"是否全局"，不能填写。

## 配置项

所有可调参数集中在 [`config.py`](config.py)，每项均带注释说明含义与影响。约定：

- `config.py` 不 import 任何项目内模块，防止循环依赖；
- 与数据库 `CHECK` 约束相关的值（如卡片打分上下限）修改时，需同步 `session/db.py` 建表语句；
- 敏感值一律 `os.getenv`，本文件不得出现明文秘密。

## 安全须知

- 密钥 / Bot secret 只放 `.env` 或环境变量，**严禁写入代码或提交**。
- 若凭据曾以明文进入过 git 历史，应立即在对应平台**作废旧值并重新生成**。
- `.gitignore` 已忽略 `.env`、`data/`、`*.db`、`*.log`、`.venv/`、`__pycache__/`、`.idea/`，并显式放行 `.env.example`。
