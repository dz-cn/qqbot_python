# qqbot_python

基于 **QQ 官方机器人 (botpy) + LangChain 1.0** 的智能陪聊机器人。核心能力是三件事：**分层记忆**（原始 / 摘要 / 事实卡片）、**混合检索知识库**（FTS5 字面腿 + Chroma 语义腿 + RRF 融合 + cross-encoder 精排）、**模型自主工具调用**（记忆沉淀、知识检索、历史回查、时间获取）。

## 功能特性

- 🤖 接入 QQ 开放平台，支持私聊 (C2C)、群聊全量消息与 @机器人消息
- 🧠 三层记忆：原始消息层 / 滚动摘要层 / 事实卡片层
- 🗜️ 上下文滚动压缩：未压缩消息超阈值时，把最老一批并入摘要并推进水位线，后台线程执行不阻塞回复
- 🃏 记忆卡片由模型自主写入，三轴打分（重要性 / 清晰度 / 半衰期），支持**全局桶 + 会话桶**两种作用域
- ⏳ 记忆衰减：按半衰期档位对陈旧卡片降权，超过 `CARD_MAX_HALF_LIVES` 个半衰期不再加载
- 📚 知识库混合检索：五步语义切块（小切大返）→ 字面腿 + 语义腿并集 RRF → 父段归并 → 预算装填 → cross-encoder 精排重排（可选）
- 🔒 权限在 SQL 里裁决：检索结果的可见性由 `rag_parent.openid/type` 单点决定，不在 Python 里二次筛
- 🛠️ 四个模型可调用工具：`memory_card` / `knowledge_search` / `search_chat_messages` / `get_current_time`
- 🎭 场景化提示词：群聊与私聊使用不同角色提示词
- 🔐 密钥隔离：API Key 与 Bot 凭据走 `.env` + `os.getenv`，不进入代码与 git

## 技术栈

| 用途 | 选型 |
|---|---|
| 语言 | Python 3.13 |
| QQ 机器人 | [qq-botpy](https://pypi.org/project/qq-botpy/) 1.2.1（导入名 `botpy`，安装名 `qq-botpy`） |
| 智能体 | LangChain 1.x `create_agent` + LangGraph 1.x（工具调用、中间件钩子） |
| 对话 / 摘要模型 | 本地 Ollama（`ChatOllama`，默认 `qwen3:8b`），或 DeepSeek 等 OpenAI 兼容服务（`ChatOpenAI`） |
| 嵌入模型 | Ollama `OllamaEmbeddings`（默认 `qwen3-embedding:latest`） |
| 关系存储 | SQLite（WAL 模式 + 外键 + 一次性连接） |
| 关键词检索 | SQLite **FTS5**，`trigram` 分词（中文免分词库，需 SQLite ≥ 3.34） |
| 向量检索 | **ChromaDB** 持久化集合（cosine 距离） |
| 精排模型 | **FlagEmbedding** `FlagReranker`（bge-reranker-v2-m3，cross-encoder，可选） |
| 配置 | python-dotenv + `config.py` 集中管理 |


## 目录结构

```
qqbot_python/
├── Main.py                    # 入口：建表 → 注册 intents → 启动 botpy 客户端
├── MyClient.py                # 事件处理：群聊/私聊消息 → agent.chat() → 回复
├── config.py                  # 全部可调参数，每项带一行「含义 + 影响」注释
├── requirements.txt           # 依赖清单
├── .env.example               # 环境变量模板（复制为 .env 后填真实值）
│
├── agent/                     # 编排层：一次请求从头到尾怎么走
│   ├── recat_agent.py         #   装配上下文 → 预检索 → 调模型 → 存消息 → 后台压缩
│   └── tools/
│       ├── agent_tools.py     #   四个 @tool：description 即「模型侧的业务逻辑」
│       ├── message_build.py   #   数据 → 模型可读文字（卡片/资料/历史三种渲染）
│       └── middleware.py      #   wrap_tool_call 钩子：记录工具名、入参、结果
│
├── service/                   # 业务层：编排参数与 SQL，不写 SQL 字面量
│   ├── base_service.py        #   query / write / tx 三个执行原语
│   ├── message_service.py     #   chat_message：写入、最近窗口、待压批次、关键词回查
│   ├── summary_service.py     #   chat_summary：最新版本、保存摘要、压缩编排
│   ├── card_service.py        #   memory_card：upsert（同名强化）、衰减加载
│   └── rag/                   #   知识库子系统
│       ├── rag_service.py     #     写编排：ingest / ingest_file / drop / rebuild_vectors
│       ├── segment_service.py #     切块：五步流水线（结构→切句→比语义→选刀口→收尾）
│       ├── store_service.py   #     关系层：父段/子块/FTS 读写 + id 生成 + 关键词腿
│       ├── vector_service.py  #     向量层：Chroma 唯一持有者，含嵌入模型一致性校验
│       ├── retrieve_service.py#     读编排：两腿召回 → RRF → 归并父段 → 预算装填 → 交给精排
│       └── rerank_service.py  #     精排：cross-encoder 逐对打分重排（FlagReranker 唯一持有者）
│
├── db/                        # 存储层：只管连接、建表、SQL 语句文本
│   ├── db.py                  #   connect() + init_db()（5 张普通表 + 1 张 FTS5 虚表的 DDL）
│   ├── message_db_sql.py      #   chat_message 的 sql_*
│   ├── summary_db_sql.py      #   chat_summary 的 sql_*
│   ├── card_db_sql.py         #   memory_card 的 sql_*
│   ├── rag_db_sql.py          #   rag_parent / rag_chunk / rag_fts 的 sql_*
│   └── __init__.py            #   门面：service 层 `import db as db_sql` 用这个
│
├── model/
│   └── factory.py             #   模型工厂 + 注册表（chat / summary / embedding）
│
├── prompts/                   # system.text（全局人设）/ group.text / user.text
├── utils/
│   ├── current.py             #   contextvar：把会话身份隐式传给工具
│   ├── scope_util.py          #   group_openid/member_openid → (scope, openid)
│   ├── time_util.py           #   now() 入库格式 / now_text() 给模型看的格式
│   ├── prompts_loader.py      #   提示词读取
│   └── path_tool.py           #   项目根与资源路径
└── data/
    ├── app.db                 # SQLite（运行时自动创建）
    └── chroma/                # Chroma 持久化目录（删了等于向量腿清空）
```

**分层规则**（改代码前先读）：

```
MyClient → agent → service → db
                     ↑
                    utils / config / model
```

- `db/` 只返回 SQL 字符串与建连接，不含业务判断；
- `service/` 只编排参数与事务，**不出现 SQL 字面量**（一律 `db_sql.sql_*`）；
- `agent/` 只管一轮请求的流程，不碰 SQL、不碰 chromadb；
- 权限、作用域这类规则**只写一处**（`_msg_filter`、`sql_fetch_visible_parents`），出现第二份就是未来的不一致。

## 快速开始

### 1. 虚拟环境与依赖

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置密钥
填入以下密钥：

```
# 模型密钥（远程模型时，必填）
OPEN_CHAT_API_KEY=OPEN_CHAT_API_KEY
OPEN_EMBEDDING_API_KEY=OPEN_EMBEDDING_API_KEY

# QQ 官方机器人凭据
QQ_BOT_APPID=QQ_BOT_APPID
QQ_BOT_SECRET=QQ_BOT_SECRET
```

### 3. 准备模型

1️⃣ 本地路线（默认）：启动 Ollama 并拉取模型，名字要与 `config.py` 一致。

```powershell
ollama pull qwen3:8b
ollama pull qwen3-embedding:latest
```

`model/factory.py` 
```python
# 模型
chat_model = OllamaChatFactory().generator()
summary_model = OllamaSummaryChatFactory().generator()
embedding_model = OllamaEmbeddingModelFactory().generator()
```

2️⃣ 远程路线：填充 API KEY，并修改实例，补全`.env`文件\
`model/factory.py` 
```python
# 模型
chat_model = OpenAIChatFactory().generator()
summary_model = OpenAISummaryChatFactory().generator()
embedding_model = OpenAIEmbeddingModelFactory().generator()
```

### 4. 运行

```powershell
python Main.py
```

### 5. 对话

在 QQ 中找到机器人私聊即可。群聊需群主开启「接收全部消息」权限，否则只收到 @消息。

### 6. 灌入知识库文档

知识库写入目前是**编程接口**，没有命令行入口。准备好 UTF-8 的 txt/markdown，执行：

```powershell
python -c "import service.rag as r; print(r.ingest_file('docs/手册.md', 'global', 'global'))"
```

- 第二、三个参数是 `openid` 与 `scope`；填 `global`/`global` 表示**所有会话都能检索到**；
- 想只对某个私聊用户可见，传该用户的 `user_openid` 与 `config.SCOPE_PRIVATE`；
- 返回值是子块总数。同一 `source`（默认取文件名）再灌一次就是**整篇重灌**，天然幂等。

门面 `service/rag/__init__.py` 只导出 `ingest` / `ingest_file` / `retrieve`。删整篇与重放向量要直接取模块：

```python
from service.rag.rag_service import drop, rebuild_vectors
drop("手册.md")        # 按 source 整篇删除（关系层 + 向量层）
rebuild_vectors()      # 从 rag_chunk 全量重放向量，修复两库漂移
```

### 7. 启用精排（可选）

召回并装填预算之后，可再接一层 **cross-encoder 精排**：把问句与每个候选父段逐对送进模型重新打分，按相关度排序后返回。默认关闭，检索只用 RRF 融合名次。

1️⃣ 下载精排模型 **bge-reranker-v2-m3** 的权重文件到同一个本地目录（国内可用镜像站）：

https://hf-mirror.com/BAAI/bge-reranker-v2-m3/tree/main

2️⃣ 在 `.env` 里填写模型目录路径：

```
RERANKER_MODEL_DIR=/filepath
```

3️⃣ 在 `config.py` 里打开总开关：

```python
RAG_USE_RERANK = True
```

模型在首次检索时加载、之后常驻内存。`RERANKER_MODEL_DIR` 留空或 `RAG_USE_RERANK = False` 时，精排整层跳过，检索直接用融合名次返回。

## 记忆架构

| 层 | 表 | 单位 | 作用 |
|---|---|---|---|
| 原始层 | `chat_message` | 一条消息 | 全量对话原文，含 `role` / `created_time` / 自增 `id`（会话内唯一顺序定义）；`msg_id` 遵循 `"{role}: {消息标识}"` 并有唯一索引去重 |
| 摘要层 | `chat_summary` | 一个版本 | 滚动摘要 + 水位线 `final_msg_id`；`UNIQUE(type, openid, version)`，并发写冲突会被拒绝 |
| 事实层 | `memory_card` | 一张卡片 | 结构化长期事实，三轴打分 + 半衰期衰减 |

**每轮上下文装配顺序**：场景提示词 → 记忆卡片 → 预检索资料 → 历史摘要 → 水位线后的最近 `RECENT_WINDOW` 条原文 → 本条消息。

### 上下文压缩

- **触发**：水位线之后的未压缩消息 > `COMPRESS_KEEP_RECENT` 才启动，否则跳过。
- **限量**：每次只把**最老的** `COMPRESS_BATCH` 条并入摘要，空摘要绝不推进水位线。
- **安全比例**：`COMPRESS_KEEP_RECENT ≤ RECENT_WINDOW`（否则消息既没压又出窗，永久不可见）；`COMPRESS_BATCH ≤ COMPRESS_KEEP_RECENT / 2`（防上下文一次塌缩，表现得像失忆）。
- **异步**：通过 `ThreadPoolExecutor` 后台执行。注意 `Future` 里的异常不会自动打印，失败只表现为「水位线不涨」。

### 记忆卡片：三轴正交

| 轴 | 管什么 | 取值 |
|---|---|---|
| `importance` | 这条信息对回答有多关键 | 5 身份/长期设定 · 3 一般事实 · 1 可忽略 |
| `clarity` | 这条有多「真」 | 5 用户明确说过 · 3 对话推断 · 1 只是猜测 |
| `half_life` | 还能有效多久（与 clarity 正交） | 1≈3 天 · 2≈7 天 · 3≈30 天 · 4≈180 天 · 5 不衰减 |

- 读侧按 `0.5^(Δt/半衰期天数)` 衰减，距上次强化超过 `CARD_MAX_HALF_LIVES` 个半衰期即视为遗忘、不再加载。
- 同名 `title` 再写即更新那张卡，并由系统自动强化（clarity +1、importance/half_life 取更高、刷新有效期），模型不用管合并细节 —— 所以「新建还是更新」完全取决于 title。
- 作用域：`is_global=true` 写 `openid="global"` 全局桶，跨会话可见；默认写当前会话桶。读侧 `openid IN ("global", 当前openid)`，两类都能加载。

## 知识库：混合检索

### 存储：四个单位

| 表 | 角色 |
|---|---|
| `rag_parent` | 「**给**」的单位 —— 父级整段原文，也是权限的唯一真相 |
| `rag_chunk` | 「**查**」的单位 —— 细子块 + `seq`（原文节内顺序）+ chunk→parent 关系桥 |
| `rag_fts` | FTS5 虚表，倒排索引，正文独立存一份（换来插入/删除完全显式，无需触发器同步） |
| Chroma 集合 | 子块向量。每条 metadata 带 `parent_id / openid / type`；集合级 metadata 记录 `embedding_model` 与距离函数，换模型后旧集合会被校验拦下 |

小切大返：检索用细子块（准），返回给模型用父段整段（全）。

**id 约定**（只在 `store_service` 生成）：

```
parent_id = f"{source}::{节序号:03d}"     # 零填充使「按 id 排序」==「按原文节序」
chunk_id  = f"{parent_id}::c{子块序号}"
```

`source` 一个键承担三件事：幂等锚点（同 source 重灌即覆盖）、分组删除单位（`drop_source` 一次清一篇）、连续取节（前缀即身份）。因此 `source` 内**不得**含 `::`、不得以 `:` 结尾 —— 这条校验在 id 生成处，失守的后果是无声多删/多捞。

### 切块：五步流水线，每步只用一种判据

```
1 结构切   按 Markdown 标题（无标题按空行）切出父段      —— 只看结构
2 切句     按终止标点划出「可下刀的候选位置」            —— 标点只给候选
3 比语义   算相邻句相似度（句向量余弦）                  —— 模型提供判据
4 选刀口   低于本节分位阈值的缝隙才真切开                —— 语义在候选里选
5 收尾     碎块并回前块；超预算再打包或硬切              —— 长度有终审否决权
```

语义判断只用在子块层；父段只看结构，保证整段边界稳定可溯源。嵌入模型不可用时自动降级为词面重叠（bigram 词频余弦），并在日志标明 `ENGINE_DOWNGRADED`。

### 检索：一条问句的旅程

```
问句 ──┬─ 字面腿  FTS5 BM25（trigram 3字滑窗 OR，top RAG_KEYWORD_LIMIT）
       │                                                        ├─ RRF 并集融合
       └─ 语义腿  Chroma cosine（top RAG_VECTOR_LIMIT）─────────┘   score = Σ 1/(k+名次)
                                    ↓
                        子块归并到父段（同父段只算一条，分取最高）
                                    ↓
                        权限裁决（SQL 里：openid IN(...) OR type='global'）
                                    ↓
                        按字符预算贪心装填：整段优先，装不下退 ±RAG_WINDOW_SPAN 窗口
                                    ↓
                  cross-encoder 精排（RAG_USE_RERANK，可选）：逐对重新打分并按相关度排序
```

四条口径，改动前先读 `retrieve_service.py` 的模块注释：

1. **融合取并集不取交集** —— 任一条腿单独命中就该有分；
2. **只吃名次不吃分数** —— BM25 的 rank 与 cosine 的 distance 量纲不可比，名次可比；
3. **权限由 `rag_parent` 裁决** —— Chroma 的 metadata 只是副本，可能旧、可能缺；
4. **给多少由预算决定，不由条数决定** —— `RAG_TOP_K` 是上限不是目标。

### 精排：可选的 cross-encoder 重排

召回、归并、装填得到的父段是按 RRF 名次排的，名次只反映「两条腿各把它排在哪」，不含绝对相关度。开启 `RAG_USE_RERANK` 后，`rerank_service` 把问句与每个候选父段拼成 (问句, 段) 对，用 bge-reranker-v2-m3 逐对打分（sigmoid 归一到 0~1），按分数重排返回。

- **全部候选都判**，不只判前 N 条：进来的已被 `RAG_TOP_K` 与字符预算夹过一道，量级很小。
- 打分文本按 `RERANK_MAX_CHARS` 截断，只截「送进模型的文本」，交回调用方的仍是整段正文。
- 模型延迟加载（首次检索才 import FlagEmbedding、才把权重读进内存），并由锁保证只加载一次。
- 可选地按 `RERANK_MIN_SCORE` 绝对阈值丢弃不相关段（默认 `None` 不启用）；开关关闭或模型目录留空时整层跳过，原样返回融合名次结果。

## 配置项

全部旋钮集中在 [`config.py`](config.py)，每项带注释。分组与关键项：

| 分组 | 关键项 | 说明 |
|---|---|---|
| 温度 | `CHAT_TEMPERATURE` / `SUMMARY_TEMPERATURE` | 摘要设 0 保证忠实原文 |
| 模型名 | `OLLAMA_*` / `OpenAI_*` | 换嵌入模型必须同步换集合名或重建 |
| 会话标识 | `GLOBAL_OPENID` / `SCOPE_*` / `MSG_ROLE_*` | DDL 的 `CHECK` 约束直接引用它们 |
| 窗口压缩 | `RECENT_WINDOW` / `COMPRESS_KEEP_RECENT` / `COMPRESS_BATCH` | 三者有大小关系约束，见上节 |
| 卡片打分 | `CARD_SCORE_MIN/MAX` / `CARD_CLARITY_BUMP` | 改动需同步 `db/db.py` 的 `CHECK` |
| 卡片衰减 | `CARD_HALFLIFE_DAYS` / `CARD_MAX_HALF_LIVES` | 档位→天数表，`0` 表示不衰减 |
| 切块 | `RAG_PARENT_MAX_CHARS` / `RAG_CHUNK_SIZE` / `RAG_CHUNK_MIN_CHARS` / `RAG_SEMANTIC_BREAK_RATIO` | 调 `RATIO` 前先用 `segment_service.preview_boundaries()` 看缝隙分布 |
| 字面腿 | `RAG_FTS_TOKENIZER` / `RAG_KEYWORD_LIMIT` | trigram 要求 SQLite ≥ 3.34，查询短语 ≥ 3 字 |
| 语义腿 | `RAG_VECTOR_DIR` / `RAG_VECTOR_COLLECTION` / `RAG_VECTOR_LIMIT` | 两腿 limit 对齐，RRF 名次才同量纲 |
| 融合返回 | `RAG_RRF_K` / `RAG_TOP_K` / `RAG_WINDOW_SPAN` / `RAG_CONTEXT_BUDGET_CHARS` | 预算决定实际条数 |
| 精排 | `RAG_USE_RERANK` / `RERANKER_MODEL_DIR` / `RERANK_MAX_CHARS` / `RERANK_MIN_SCORE` / `RERANK_USE_FP16` | cross-encoder 重排，默认关闭；权重目录走 `.env`，留空即跳过 |