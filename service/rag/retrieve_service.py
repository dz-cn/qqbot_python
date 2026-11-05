"""RAG 读编排：问句 → 两腿召回 → RRF 融合 → 归并父段 → 权限裁决 → 按预算装填（整段 / 窗口降级）。
本模块不写 SQL、不碰 chromadb、不算向量：腿在 store_service / vector_service，这里只管顺序与合并。

四个口径（改动前先读）：
  1) 融合取并集不取交集 —— 任一条腿单独命中就该有分；
  2) 只吃名次不吃分数 —— BM25 的 rank 与 cosine 的 distance 量纲不可比，名次可比；
  3) 权限由 rag_parent 裁决 —— Chroma 的 metadata 只是副本，可能旧、可能缺；
  4) 给多少由预算决定，不由条数决定 —— top_k 是上限，不是目标。
"""
from typing import NamedTuple
from utils.log_util import logger
from service.rag import store_service as store
from service.rag import vector_service as vec
from service.rag.rerank_service import rerank
import config

OVERFETCH = 2                              # 截断前多取几倍候选，留给权限落空与窗口省下的余量
ELLIPSIS = "…"                             # 节选标记：不标出来，模型会把窗口当成完整规则复述


class Passage(NamedTuple):
    """一个候选父段：正文（整段或节选）+ 融合分数 + 命中了哪几块 + 是否被降级过。"""
    parent_id: str
    text: str
    score: float
    evidence: list[str]
    truncated: bool


class _ParentHit(NamedTuple):
    """归并后的父段：分数取命中块里最高的那块，best_seq 就是窗口展开的中心。"""
    score: float
    best_seq: int
    evidence: list[str]


def retrieve(question: str, open_ids: list[str],
             top_k: int = config.RAG_TOP_K,
             budget: int = config.RAG_CONTEXT_BUDGET_CHARS) -> list[Passage]:
    """混合检索入口。查不到就是空列表（不是异常）—— 空列表是有价值的信息，
    调用方据此回答「我不知道」，而不是拿一堆不相关的段落硬凑。"""
    if not (question or "").strip() or not open_ids:
        return []
    if isinstance(open_ids, str):
        # 与 keyword_search 里同一道检查：两处都是对外入口，各守各的边界，不是重复逻辑。
        raise TypeError(f"open_ids 必须是列表，收到字符串 {open_ids!r}："
                        "字符串会被当作字符序列展开，权限条件恒假且不报错（静默零召回）")

    literal = store.keyword_search(question, open_ids)
    semantic = vec.vector_search(question, open_ids)
    fused = _fuse(literal, semantic)
    if not fused:
        logger.info(f"[rag.retrieve] 两腿皆零召回 question={question!r}")
        return []

    merged = _merge_to_parents(fused)
    ranked = sorted(merged.items(), key=lambda item: item[1].score, reverse=True)
    candidates = ranked[:top_k * OVERFETCH]
    texts = store.fetch_full_texts([parent_id for parent_id, _ in candidates], open_ids)
    passages = _fill_to_budget(candidates, texts, top_k, budget)
    logger.info(f"[rag.retrieve] 字面={len(literal)} 语义={len(semantic)} 融合={len(fused)} "
                f"父段={len(merged)} 可见={len(texts)} 返回={len(passages)} "
                f"占用={sum(len(p.text) for p in passages)}/{budget} question={question!r}")

    # 重排后返回
    return rerank(question, passages)


def _fuse(*legs: list[tuple[str, float]]) -> dict[str, float]:
    """倒数排名融合（RRF）：score = Σ 1/(k + 名次)，名次从 1 起。
    k 越大越压平头部，两腿越容易互相补位；60 在两腿各返 20 条的量级下约等于「单腿冠军≈双腿中游」。"""
    scores: dict[str, float] = {}
    for hits in legs:
        for rank, (chunk_id, _) in enumerate(hits, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (config.RAG_RRF_K + rank)
    return scores


def _merge_to_parents(fused: dict[str, float]) -> dict[str, _ParentHit]:
    """子块归并到父段：同一父段被多块命中只算一条，分数取其中最高（不是求和）。
    求和会让「同一节命中三块」压过「两个不同的节各命中一块」，而后者覆盖面更广。
    归并的目的是去重，不是加分。"""
    ordered = sorted(fused, key=fused.get, reverse=True)
    locations = store.locate_chunks(ordered)
    hits: dict[str, _ParentHit] = {}
    for chunk_id in ordered:                 # 按名次遍历：父段首次出现时拿到的就是最高分与最佳块
        loc = locations.get(chunk_id)
        if loc is None:
            continue
        hit = hits.get(loc.parent_id)
        if hit is None:
            hits[loc.parent_id] = _ParentHit(fused[chunk_id], loc.seq, [chunk_id])
        else:
            hits[loc.parent_id] = hit._replace(evidence=hit.evidence + [chunk_id])
    return hits


def _fill_to_budget(candidates: list[tuple[str, _ParentHit]], texts: dict[str, str],
                    top_k: int, budget: int) -> list[Passage]:
    """按名次贪心装填：每个父段先试整段，装不下退到 ±span 窗口，还装不下就继续缩到命中块本身。
    为什么逐个决定而不是「统一降级」：第 1 名值得给整段，第 3 名可能只配一个窗口 ——
    统一策略在语料变大后一定浪费预算。
    预算用完就停，所以返回条数是浮动的：可能 3 条整段，也可能 1 条整段加 1 条窗口。"""
    passages: list[Passage] = []
    used = 0
    for parent_id, hit in candidates:
        full = texts.get(parent_id)
        if full is None:
            continue                                    # 权限裁决没放行
        text, truncated = full, False
        span = config.RAG_WINDOW_SPAN
        while used + len(text) > budget and span >= 0:
            text = _window_text(parent_id, hit.best_seq, span)
            truncated = True
            span -= 1                                   # 下一轮更窄：窗口装不下就继续缩
        if used + len(text) > budget:
            if passages:
                break                                   # 预算用尽，名次靠后的不再进
            text, truncated = text[:budget], True        # 一条都没有时至少给一条：硬截断好过空手
        used += len(text)
        passages.append(Passage(parent_id, text, hit.score, hit.evidence, truncated))
        if len(passages) >= top_k:
            break
    return passages


def _window_text(parent_id: str, around_seq: int, span: int) -> str:
    """命中块 ±span 的连续窗口，按原文顺序拼回（SQL 已 ORDER BY seq）。
    前后加省略号是必须的：模型看到一段没有标记的文字，就会把它当成完整规则复述 ——
    被切掉的「第七条」不会让它主动声明资料不完整。
    代价：每个降级父段多一次本地 SQL 查询。SQLite 是进程内读，比省下的上下文便宜得多。"""
    rows = store.chunks_of_section(parent_id, around_seq=around_seq, span=span)
    return f"{ELLIPSIS}{''.join(row['chunk_text'] for row in rows)}{ELLIPSIS}"