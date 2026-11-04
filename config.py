"""全局配置：集中管理模型、提示词、会话窗口、记忆卡片等可调参数，避免魔术值散落各处。

约定：
- 本模块不 import 任何项目内模块，防止循环依赖；
- 每个配置项都必须带一行说明其含义与影响；
- 修改与数据库 CHECK 约束相关的值（如打分上下限）时，需同步 session/db.py 的建表语句。
"""
from dotenv import load_dotenv
import os

# 从项目根目录的 .env 加载本地密钥/凭据；.env 不提交，本文件只负责读取
load_dotenv()

# ---------------------------------------------------------------- 模型
SUMMARY_TEMPERATURE = 0             # 摘要模型温度：设 0 保证忠实原文、不发散
CHAT_TEMPERATURE = 0.7                                  # 对话温度：越高越发散，陪聊可 0.7 左右

# ---------------------------------------------------------------- Ollama本地模型
OLLAMA_CHAT_MODEL_NAME = "qwen3:8b"        # 陪聊/推理用的对话模型名称
OLLAMA_SUMMARY_MODEL_NAME = "qwen3:8b"     # 摘要/压缩用的模型名称（可与对话模型不同）
OLLAMA_EMBEDDING_MODEL_NAME = "qwen3-embedding:latest" # 嵌入模型名称

# ---------------------------------------------------------------- OpenAI 的远程模型
OPENAI_CHAT_BASE_URL = "https://api.deepseek.com"         # OpenAI 的大语言模型兼容接口地址
OPENAI_CHAT_API_KEY = os.getenv("OPEN_CHAT_API_KEY", "")  # 密钥：从环境变量读取，切勿写死或提交
OPENAI_CHAT_MODEL = "deepseek-flash"                      # 对话模型名
OPENAI_SUMMARY_MODEL = "deepseek-flash"                   # 摘要/压缩用的模型名称
OPENAI_EMBEDDING_BASE_URL = "https://api.deepseek.com"    # OpenAI 的向量模型兼容接口地址
OPENAI_EMBEDDING_API_KEY = os.getenv("OPEN_EMBEDDING_API_KEY", "")   # 密钥：从环境变量读取，切勿写死或提交
OPENAI_EMBEDDING_MODEL = "deepseek-flash-embedding"       # 嵌入模型名称

# ---------------------------------------------------------------- QQ 机器人凭据
QQ_BOT_APPID = os.getenv("QQ_BOT_APPID", "")      # QQ 机器人 appid：从环境变量读取，切勿写死或提交
QQ_BOT_SECRET = os.getenv("QQ_BOT_SECRET", "")    # QQ 机器人 secret：从环境变量读取，切勿写死或提交

# ---------------------------------------------------------------- 提示词目录
GROUP_PROMPT_FILE = "prompts/group.text"     # 群聊场景提示词文件路径
SYSTEM_PROMPT_FILE = "prompts/system.text"   # 全局系统提示词文件路径
USER_PROMPT_FILE = "prompts/user.text"       # 私聊场景提示词文件路径
PROMPT_ENCODING = "utf-8"                    # 提示词文件读取编码

# ---------------------------------------------------------------- 会话标识
GLOBAL_OPENID = "global"     # 全局共享记忆所用的占位 openid
SCOPE_GROUP = "group"        # scope 取值：群聊
SCOPE_PRIVATE = "private"    # scope 取值：私聊
SCOPE_GLOBAL = "global"      # scope 取值：全局（跨会话可读）
MSG_ROLE_USER = "user"       # 消息角色：用户
MSG_ROLE_ASSISTANT = "assistant"  # 消息角色：助手（bot 回复）

# ---------------------------------------------------------------- 上下文窗口 / 压缩
RECENT_WINDOW = 40                    # 上下文里保留的最近原文条数
COMPRESS_KEEP_RECENT = RECENT_WINDOW  # 未压缩消息 > 该值才触发压缩（与窗口对齐，保证未压消息都可见）
COMPRESS_BATCH = 20                   # 每次压缩最老的消息条数（必须 < COMPRESS_KEEP_RECENT，防上下文塌缩）
SUMMARY_MAX_LINES = 10                # 滚动摘要的行数上限（供摘要提示词引用）

# ---------------------------------------------------------------- 记忆卡片
CARD_LOAD_LIMIT = 20           # 单次读取的最大卡片数
CARD_SCORE_MIN = 1             # 打分下限（需与 memory_card 表 CHECK 一致）
CARD_SCORE_MAX = 5             # 打分上限（需与 memory_card 表 CHECK 一致）
CARD_SCORE_DEFAULT = 3         # 非法打分时的兜底值
CARD_IMPORTANCE_DEFAULT = 3    # 卡片重要性默认值（模型未给时使用）
CARD_CLARITY_DEFAULT = 3       # 卡片清晰度默认值（模型未给时使用）
CARD_CLARITY_BUMP = 1          # 同名卡片重复出现时，可信度(clarity)的增量

# ---------------------------------------------- 记忆衰减（半衰期）
CARD_HALFLIFE_DEFAULT = 3      # 半衰期档位默认值(1~5,复用 CARD_SCORE 区间):模型未给/兜底时使用
# clarity=置信度(该不该信)、half_life=衰减速率(还活多久),两轴正交;检索按 0.5^(Δt/天数) 衰减
CARD_HALFLIFE_DAYS = {1: 3, 2: 7, 3: 30, 4: 180, 5: 0}   # 档位→半衰期天数;0 表示不衰减(核心事实永久)
CARD_MAX_HALF_LIVES = 3.0    # 距上次强化经过多少个半衰期即视为遗忘、不再加载(3≈保留率12.5%)

# ---------------------------------------------------------------- RAG 分段检索
RAG_FTS_TOKENIZER = "trigram"      # FTS5 分词器：三字符滑窗，中文免 jieba（需 SQLite>=3.34；查询短语须 >=3 字）
RAG_USE_EMBEDDING = True           # 语义断点引擎：True=模型向量判话题转换；False=词面重叠降级（免模型）
RAG_PARENT_MAX_CHARS = 1200        # 父级整段字符预算；超出按句号打包拆小，防一节过长稀释检索
RAG_CHUNK_SIZE = 320               # 子块字符预算；语义块仍超长时按此值打包/硬切
RAG_CHUNK_MIN_CHARS = 40           # 子块字符下限；不足则并入前一块，避免「半句话」独立成块成为检索噪音
RAG_SEMANTIC_BREAK_RATIO = 0.35    # 语义断点分位（0~1）：在本节内「最不像」的这个比例的缝隙处下刀。调小→块更大更连贯，调大→块更细更碎
BIGRAM_WINDOW = 2                  # 中文 token 的滑窗字数：2 = 双字组合
RAG_KEYWORD_LIMIT = 20             # 关键词腿（FTS5 BM25）返回的子块候选数，供后续 RRF 融合；太小会漏、太大只是多算名次
RAG_VECTOR_DIR = "data/chroma"     # Chroma 持久化目录（相对项目根）；整目录可删，删了就等于向量腿清空
RAG_VECTOR_COLLECTION = "rag_chunk"  # 集合名：换嵌入模型或改向量语义时必须换名/重建
RAG_VECTOR_LIMIT = 20              # 语义腿返回的子块候选数，与 RAG_KEYWORD_LIMIT 对齐（RRF 两腿名次同量纲）
RAG_RRF_K = 60                     # RRF 平滑常数：越大越压平头部，单腿冠军越难独走；60 为经验值
RAG_TOP_K = 5                      # 最终交给上下文的父段条数；越大越全，但越稀释模型注意力
RAG_WINDOW_SPAN = 2                # 窗口降级初值：命中块 ±2 个子块；装不下会逐轮缩到 0（只给命中块）
RAG_CONTEXT_BUDGET_CHARS = 2600    # 检索内容总字符预算 ≈ 2 个整段 + 余量；条数由它决定，top_k 只是上限
RAG_MESSAGE_PROXY = "msg-"         # source 来源前缀-消息作为存储内容
RAG_KNOWLEDGE_PROXY = "knowledge-" # source 来源前缀-知识作为存储内容
# ---------------------------------------------------------------- RAG 精排（cross-encoder）
RERANKER_MODEL_DIR = os.getenv("RERANKER_MODEL_DIR", "")   # 模型权重目录：机器相关且体积大，走 .env 且不进 git；空=精排自动跳过
RAG_USE_RERANK = False              # 精排总开关：False=retrieve 不调模型；CPU 上这是秒级开销，先实测耗时再打开
RERANK_MAX_CHARS = 512              # 打分文本截断长度；父段可达 1200 字，注意力开销随长度平方增长（不影响交给 LLM 的正文）
RERANK_MIN_SCORE = None             # 绝对相关阈值（sigmoid 后 0~1）：低于它的当不相关丢弃；None=不启用，必须先测真文档分布再定
RERANK_USE_FP16 = False             # fp16 只在 CUDA 生效；CPU 开它无收益（config.json 声明 torch_dtype=float32）
