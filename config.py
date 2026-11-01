"""全局配置：集中管理模型、提示词、会话窗口、记忆卡片等可调参数，避免魔术值散落各处。

约定：
- 本模块不 import 任何项目内模块，防止循环依赖；
- 每个配置项都必须带一行说明其含义与影响；
- 修改与数据库 CHECK 约束相关的值（如打分上下限）时，需同步 session/db.py 的建表语句。
"""
import os
from dotenv import load_dotenv

# 从项目根目录的 .env 加载本地密钥/凭据；.env 不提交，本文件只负责读取
load_dotenv()

# ---------------------------------------------------------------- 模型
SUMMARY_TEMPERATURE = 0             # 摘要模型温度：设 0 保证忠实原文、不发散
CHAT_TEMPERATURE = 0.7                                  # 对话温度：越高越发散，陪聊可 0.7 左右

# ---------------------------------------------------------------- Ollama本地模型
CHAT_MODEL_NAME = "qwen3:8b"        # 陪聊/推理用的对话模型名称
SUMMARY_MODEL_NAME = "qwen3:8b"     # 摘要/压缩用的模型名称（可与对话模型不同）

# ---------------------------------------------------------------- DeepSeek
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")    # DeepSeek 密钥：从环境变量读取，切勿写死或提交
DEEPSEEK_BASE_URL = "https://api.deepseek.com"              # DeepSeek 的 OpenAI 兼容接口地址
DEEPSEEK_CHAT_MODEL = "deepseek-flash"                      # 对话模型名
DEEPSEEK_SUMMARY_MODEL = "deepseek-flash"                   # 摘要/压缩用的模型名称

# ---------------------------------------------------------------- QQ 机器人凭据
QQ_BOT_APPID = os.getenv("QQ_BOT_APPID", "")      # QQ 机器人 appid：从环境变量读取，切勿写死或提交
QQ_BOT_SECRET = os.getenv("QQ_BOT_SECRET", "")    # QQ 机器人 secret：从环境变量读取，切勿写死或提交

# ---------------------------------------------------------------- 提示词
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
SUMMARY_MAX_LINES = 8                 # 滚动摘要的行数上限（供摘要提示词引用）

# ---------------------------------------------------------------- 记忆卡片
CARD_LOAD_LIMIT = 20           # 单次读取的最大卡片数
CARD_SCORE_MIN = 1             # 打分下限（需与 memory_card 表 CHECK 一致）
CARD_SCORE_MAX = 5             # 打分上限（需与 memory_card 表 CHECK 一致）
CARD_SCORE_DEFAULT = 3         # 非法打分时的兜底值
CARD_IMPORTANCE_DEFAULT = 3    # 卡片重要性默认值（模型未给时使用）
CARD_CLARITY_DEFAULT = 3       # 卡片清晰度默认值（模型未给时使用）
CARD_CLARITY_BUMP = 1          # 同名卡片重复出现时，可信度(clarity)的增量
