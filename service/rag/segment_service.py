"""RAG 切块：把一篇原文切成「父级整段 + 细子块」两层，供后续落库与检索。

一、两层粒度各自的用途（小切大返）
    父级整段 rag_parent.full_text —— 被「返回」的单位：整而全，喂给 LLM
    细子块   rag_chunk.chunk_text —— 被「检索」的单位：小而准，进 Chroma 与 FTS

二、一个子块是五步流水线的产物，每步只用一种判据：
    第1步 结构切   按 Markdown 标题（无标题则按空行）切出父段
    第2步 切句     按终止标点把父段拆成句子 —— 标点只负责「划定可下刀的候选位置」
    第3步 比语义   算相邻句的相似度（embedding 用句向量，lexical 用词面重叠）
    第4步 选刀口   相似度低于本节阈值的缝隙才真的切开 —— 语义在候选里做选择
    第5步 收尾     太碎的块并回前一块；超过字数预算的再打包或硬切 —— 长度有最终否决权

三、语义判断只用在「子块」这一层；父段只看结构，不看向量，保证整段边界稳定可溯源。
"""
from collections import Counter
from botpy import logger
import math
import re
import config


# ==================================================================== 公开 API
def segment(text: str, embedder=None) -> list[dict]:
    """把原文切成待入库的两层结构（本模块唯一总入口）。

    输入：text 一篇文档的纯文本（Markdown 亦可）。
    输出：[{"parent_text": 整段原文, "children": [子块, ...]}, ...]
          children 的列表下标就是落库时的 rag_chunk.seq，顺序即原文顺序。
    """
    sections = split_sections(text)
    blocks = []
    for section in sections:
        blocks.append({
            "parent_text": section,
            "children": split_children(section, embedder),
        })
    return blocks


def split_sections(text: str) -> list[str]:
    """第1步（结构切）：切出父级整段。

    规则优先级：Markdown 标题 > 空行分段 > 整篇视为一段。
    父段超过 RAG_PARENT_MAX_CHARS 时，再按句子打包拆小，避免一节过长稀释检索。
    """
    normalized = _normalize_newlines(text)
    if not normalized:
        return []
    raw_blocks = (_split_by_headings(normalized)
                  or _split_by_paragraphs(normalized)
                  or [normalized])
    sections: list[str] = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        if len(block) <= config.RAG_PARENT_MAX_CHARS:
            sections.append(block)
        else:
            sections.extend(_refill_oversized_parent(block))
    return sections


def split_children(section: str, embedder=None) -> list[str]:
    """第2~5步（语义切）：在一个父段内切出细子块。

    读起来就是流程本身，细节都在被调用的私有函数里：
        切句 → 比语义 → 选刀口 → 合并碎块 → 拼成文本 → 按预算收尾
    """
    section = (section or "").strip()
    if not section:
        return []

    sentences = _split_into_sentences(section)
    if len(sentences) <= 1:
        return [section]

    scores, _engine = _get_sentences_similarity(sentences, embedder)
    groups = _group_by_weak_bonds(scores)
    groups = _absorb_undersized_groups(sentences, groups)
    blocks = _join_groups(groups, sentences)
    return _fit_to_size_limit(blocks, config.RAG_CHUNK_SIZE)


def preview_boundaries(section: str, embedder=None) -> dict:
    """调参望远镜：列出每道缝隙的相似度、当前阈值、以及刀是否落在这里。

    调 RAG_SEMANTIC_BREAK_PERCENTILE 时靠它确认「切在该切的地方」，而不是靠猜。
    不参与正式切分流程，仅供观测。
    """
    sentences = _split_into_sentences(section)
    if len(sentences) < 2:
        return {"engine": "n/a", "threshold": 0.0, "gaps": []}

    scores, engine = _get_sentences_similarity(sentences, embedder)
    threshold = _get_scores_threshold(scores)
    gaps = []
    for index, score in enumerate(scores):
        gaps.append({
            "after": index,
            "score": round(score, 4),
            "is_cut": score < threshold,
            "left": sentences[index][:PREVIEW_SNIPPET_CHARS],
            "right": sentences[index + 1][:PREVIEW_SNIPPET_CHARS],
        })
    return {"engine": engine, "threshold": round(threshold, 4), "gaps": gaps}


# ==================================================== 第2步：切句（候选刀口）
def _split_into_sentences(text: str) -> list[str]:
    """
    按照标点符号切（在标点后切），返回句子列表。
    eg： return -> ["你好世界。", "你是谁？"]
    """
    pieces = _SENT_SPLIT.split(text)
    return [piece.strip() for piece in pieces if piece and piece.strip()]


# ==================================================== 第3步：比语义（相似度）
def _get_sentences_similarity(sentences: list[str], embedder=None) -> tuple[list[float], str]:
    """作用：算出每道「相邻句缝隙」的相似度。

    输出长度是 len(sentences) - 1：相似度属于缝隙，不属于句子。
    第二个返回值是实际使用的引擎名，用来发现「以为在用模型、其实降级了」。
    """
    engine = embedder or _get_embedder()
    if engine is None:
        return _get_sentences_lexical_scores(sentences), ENGINE_LEXICAL
    try:
        return _get_sentences_embedding_scores(sentences, engine), ENGINE_EMBEDDING
    except Exception as error:
        logger.warning(f"[rag.segment] 句向量计算失败，本节降级为词面相似度：{error}")
        return _get_sentences_lexical_scores(sentences), ENGINE_DOWNGRADED


def _get_embedder():
    """作用：按 config.RAG_SEGMENTER 决定「向量从哪来」
    return 批量取向量的函数 | None -> 表示不依赖向量模型，而是使用自定义的向量计算方法进行计算。
    """
    if not config.RAG_USE_EMBEDDING:
        return None
    try:
        from model.factory import embedding_model
        return embedding_model.embed_documents
    except Exception as error:
        logger.warning(f"[rag.segment] 嵌入模型不可用，本批次降级为词面相似度：{error}")
        return None


def _get_sentences_embedding_scores(sentences: list[str], embedder) -> list[float]:
    """
    利用向量模型，批量获取 sentences 的向量坐标。然后手动计算余弦相似度。
    return 相邻句子之间的余弦相似度列表
    """
    # 获取所有的向量坐标集合
    vectors = embedder(list(sentences))
    # 求模长
    lengths = [math.sqrt(sum(value * value for value in vec)) or 1.0 for vec in vectors]
    # 每个句子的余弦相似度
    scores = []
    for index in range(len(vectors) - 1):
        left, right = vectors[index], vectors[index + 1]
        # 点积
        dot = sum(a * b for a, b in zip(left, right))
        # 余弦相似度 == 点积 / 模长之积
        scores.append(dot / (lengths[index] * lengths[index + 1]))
    return scores


def _get_sentences_lexical_scores(sentences: list[str]) -> list[float]:
    """
    这里是由于向量模型不可用，于是利用词频作为向量进行的。
    例如：["年","假","年假","年","申请"] -词频-> {"年":2, "假":1, "年假":1, "申请":1}
    则这句话的向量为：[2,1,1,1]
    """
    # Counter 作用就是统计词频，例如：
    # tokens = ["年","假","年假","年","申请"]
    # cnt = Counter(tokens)
    # cnt = {"年":2, "假":1, "年假":1, "申请":1}

    # 这里其实就是
    profiles = [Counter(_tokenize(sentence)) for sentence in sentences]
    scores = []
    for index in range(len(profiles) - 1):
        scores.append(_cosine(profiles[index], profiles[index + 1]))
    return scores


def _tokenize(text: str) -> list[str]:
    """作用：把句子切成词面的 token
    例如：「年假申请」 return -> ["年","假","申","请","年假","假申","申请"]（BIGRAM_WINDOW=2,表示两个字拼一起）
    """
    # 将文本中的英文转为小写
    lowered = text.lower()
    # 获取所有英文单词
    latin_words = _LATIN.findall(lowered)
    # 获取所有中文字符（单个字）
    cjk_chars = _CJK.findall(lowered)
    # 将之前的中文单字进行拼接，BIGRAM_WINDOW: 表示几个字为一个窗口，也就是接几个字拼一起
    cjk_bigrams = []
    for index in range(len(cjk_chars) - config.BIGRAM_WINDOW + 1):
        cjk_bigrams.append("".join(cjk_chars[index:index + config.BIGRAM_WINDOW]))
    # 返回的是单词列表 + 单字 + BIGRAM_WINDOW 字列表
    return latin_words + cjk_chars + cjk_bigrams


def _cosine(left: Counter, right: Counter) -> float:
    """
    作用：两个词频向量的余弦相似度：点积 / 模长之积。
    归一化后与文本长度无关，只看「用词方向」是否一致：0=不相干，1=同方向。
    """
    if not left or not right:
        return 0.0
    # 取出两边都出现的交集，因为没有交集的没有意义，因为：[1,2,3,0] [0,2,3,1],对应为 0 的位置是因为没有对应的词，所以乘积为 0，
    # +0 对后续没有意义
    shared = set(left) & set(right)
    # 点积
    dot = sum(left[token] * right[token] for token in shared)
    # 模长计算
    left_length = math.sqrt(sum(value * value for value in left.values()))
    right_length = math.sqrt(sum(value * value for value in right.values()))
    if not left_length or not right_length:
        return 0.0
    # 余弦相似度 == 点积 / 模长之积
    return dot / (left_length * right_length)


# ==================================================== 第4步：选刀口（分组）
def _get_scores_threshold(scores: list[float]) -> float:
    """
    作用：在本节内部挑一个「什么算话题转换」的分界值，也就是当相似度低于这个值时，我们认为发生了话题转换，进行切分。
    """
    if not scores:
        return 0.0
    ordered = sorted(scores)
    position = int(len(ordered) * config.RAG_SEMANTIC_BREAK_RATIO)
    return ordered[min(len(ordered) - 1, position)]


def _group_by_weak_bonds(scores: list[float]) -> list[list[int]]:
    """
    作用：缝隙相似度低于阈值处下刀，把句子下标分成若干连续组。
    输入：N 个句子对应的 N-1 个缝隙相似度。
    输出：如 [[0,1,2],[3,4,5]] —— 话题从「年假」转到「调休」时正好断开。
    注意：阈值是在本节内部算的，所以切分结果不随全文规模变化。
    """
    threshold = _get_scores_threshold(scores)
    groups: list[list[int]] = []
    current = [0]
    for index, score in enumerate(scores):
        if score < threshold:
            groups.append(current)
            current = [index + 1]
        else:
            current.append(index + 1)
    groups.append(current)
    return groups


# ==================================================== 第5步：收尾（碎块与预算）
def _absorb_undersized_groups(sentences: list[str], groups: list[list[int]]) -> list[list[int]]:
    """作用：把不足 RAG_CHUNK_MIN_CHARS 的碎块并回前一块。

    为什么必要：第4步只看「像不像」，不看「够不够长」。一个 12 字的块即便被检索到，
    也没有足够上下文喂给 LLM，纯属噪音。语义断点必须配一道长度回收。
    首块没有前一块可并，所以并到次块。
    """
    merged: list[list[int]] = []
    for group in groups:
        if merged and _group_length(sentences, group) < config.RAG_CHUNK_MIN_CHARS:
            merged[-1].extend(group)
        else:
            merged.append(list(group))
    if len(merged) > 1 and _group_length(sentences, merged[0]) < config.RAG_CHUNK_MIN_CHARS:
        merged[1] = merged[0] + merged[1]
        merged.pop(0)
    return merged


def _group_length(sentences: list[str], group: list[int]) -> int:
    """作用：一组句子连起来的字符总数（判碎块用）。"""
    return sum(len(sentences[index]) for index in group)


def _join_groups(groups: list[list[int]], sentences: list[str]) -> list[str]:
    """作用：把句子下标分组还原成文本块。

    用空串连接：句子自带终止标点，且换行等空白已被 _split_into_sentences 去掉，
    拼回结果与原文只差空白字符，因此守恒自检能通过。
    """
    return ["".join(sentences[index] for index in group) for group in groups]


def _fit_to_size_limit(blocks: list[str], limit: int) -> list[str]:
    """作用：按字符预算重新打包文本块，保证不产出超长块。

    三种情形：
      1) 接上下一块会超预算  → 先交付当前块，下一块另起
      2) 单块本身就超预算    → 硬切成多段（全模块唯一会破坏句边界的地方，
                              只在「一句话长过 limit」时触发，属保底妥协）
      3) 连着几块都没超      → 合并成一块（这也是「长度对语义一刀的终审否决」）
    """
    fitted: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > limit:
            if current:
                fitted.append(current)
                current = ""
            fitted.extend(_hard_cut(block, limit))
        elif current and len(current) + len(block) > limit:
            fitted.append(current)
            current = block
        else:
            current += block
    if current:
        fitted.append(current)
    return fitted


def _hard_cut(block: str, limit: int) -> list[str]:
    """作用：把超长文本按预算等长硬切（保底，不看语义也不看标点）。"""
    return [block[start:start + limit] for start in range(0, len(block), limit)]


def _refill_oversized_parent(section: str) -> list[str]:
    """作用：父段超预算时按句子重新打包拆小（与子块共用同一套预算逻辑）。"""
    return _fit_to_size_limit(_split_into_sentences(section), config.RAG_PARENT_MAX_CHARS)


# ==================================================== 第1步：结构切分的零件
def _normalize_newlines(text: str) -> str:
    """作用：把 Windows/老 Mac 换行统一成 \\n，让后面的分行逻辑只处理一种情况。"""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _split_by_headings(text: str) -> list[str]:
    """作用：按 Markdown 标题切节，标题连同正文一起留作父段。

    为什么把标题留在块内：标题就是这份内容的出处标签，
    LLM 拿到「## 请假制度」这句话，才知道自己在读哪一节。
    全文没有标题时返回空列表，交给空行切分接手。
    """
    lines = text.split("\n")
    if not any(_HEADING.match(line) for line in lines):
        return []
    blocks: list[str] = []
    current: list[str] = []
    for line in lines:
        if _HEADING.match(line) and current:
            blocks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _split_by_paragraphs(text: str) -> list[str]:
    """作用：无标题时按空行分段。"""
    return [block.strip() for block in _PARAGRAPH_SPLIT.split(text) if block.strip()]


# ==================================================================== 常量区
# 以下是「运行时观测标签」：只用于日志与 preview 输出，告诉你这一批相似度到底是谁算的
ENGINE_EMBEDDING = "embedding"          # RAG_SEGMENTER 取值：句向量语义判断（需模型可用）
ENGINE_LEXICAL = "lexical"              # 引擎名：词面降级
ENGINE_DOWNGRADED = "lexical(downgrade)"  # 引擎名：本想用模型，但调用失败降级（看到它就该去查模型/网络）
PREVIEW_SNIPPET_CHARS = 18              # 调参明细里每侧句子截断长度，纯粹为了看得清

_HEADING = re.compile(r"^#{1,6}\s+")                    # 一到六级标题行首
_SENT_SPLIT = re.compile(r"(?<=[。！？；!?;\n])")        # 零宽：在标点之后切，标点不丢
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")               # 连续空行
_CJK = re.compile(r"[\u4e00-\u9fff]")                   # 单个汉字
_LATIN = re.compile(r"[a-z0-9]+")                       # 连续英数词