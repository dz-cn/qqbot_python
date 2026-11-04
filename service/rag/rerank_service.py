"""RAG 精排（cross-encoder）：给 (问句, 父段) 逐对打分、重排，并可选地按绝对阈值拒绝不相关资料。
本模块是 FlagReranker 的唯一持有者（与 vector_service 唯一持有 chromadb 对称）；不写 SQL、不做召回、不管预算。
"""
from typing import TYPE_CHECKING
from botpy import logger
import threading
import time
import config

if TYPE_CHECKING:
    # 只出现在类型标注里：retrieve_service 要 import 本模块，这里反向真 import 会成环
    from service.rag.retrieve_service import Passage

_model = None
_unavailable = ""          # 加载失败的原因；非空即不再重试，也不再刷同一行日志
_lock = threading.Lock()   # 主链路与后台线程可能同时首次调用，模型只该加载一次


def _get_model():
    """首次调用才 import FlagEmbedding、才把 2.27GB 权重读进内存。
    这不是洁癖：service/rag/__init__.py 被 recat_agent 顶层 import，
    写在模块顶层就等于「每轮对话都得先付一次 torch 启动成本」，
    没装这个重依赖的机器更是直接 ImportError 崩掉整条对话链。"""
    global _model, _unavailable
    if _model is not None:
        return _model            # 已加载：把模型交出去，不能误判成「不可用」
    if _unavailable:
        return None              # 已知加载失败：不再重试，也不再刷同一行日志
    if not config.RERANKER_MODEL_DIR:
        _unavailable = "未配置 RERANKER_MODEL_DIR"
        logger.warning(f"[rag.rerank] 精排整层跳过：{_unavailable}")
        return None
    with _lock:
        if _model is not None:   # 双检：可能已被别的线程加载好
            return _model
        if _unavailable:
            return None
        try:
            from FlagEmbedding import FlagReranker
            started = time.perf_counter()
            _model = FlagReranker(config.RERANKER_MODEL_DIR, use_fp16=config.RERANK_USE_FP16)
            logger.info(f"[rag.rerank] 模型加载完成 耗时={time.perf_counter() - started:.1f}s "
                        f"dir={config.RERANKER_MODEL_DIR}")
        except Exception as error:
            # 权重没下全、目录写错、显存/内存不足都会走到这里；只报一次，之后静默走融合名次
            _unavailable = f"{type(error).__name__}: {error}"
            logger.warning(f"[rag.rerank] 模型不可用，精排整层跳过（只报一次）：{_unavailable}")
            _model = None
    return _model


def rerank(question: str, passages: list["Passage"]) -> list["Passage"]:
    """按相关度重排候选：**全部候选都送进模型打分**，不只判前 N 条。
    开关关闭、模型缺失、候选为空时原样返回（连顺序都不动）。
    返回类型与入参一致：分数只进日志，不外泄成第二个契约 ——
    调用方（预注入与 knowledge_search）要的是资料正文，不是 0.87 这种它不会用的数字。"""
    if not config.RAG_USE_RERANK or not passages:
        logger.info(f"[rag.rerank] 候选为空，原样返回")
        return passages

    model = _get_model()
    if model is None:
        logger.info(f"[rag.rerank] 模型不可用，精排整层跳过，退回融合名次")
        return passages

    pairs = [[question, _clip(p.text)] for p in passages]
    started = time.perf_counter()
    try:
        scores = model.compute_score(pairs, normalize=True)   # normalize=True → sigmoid 压到 0~1
    except Exception as error:
        logger.warning(f"[rag.rerank] 打分失败，退回融合名次：{error}")
        return passages
    elapsed = time.perf_counter() - started
    scores = scores if isinstance(scores, (list, tuple)) else [scores]   # 只有一条候选时返回标量

    ranked = sorted(zip(passages, scores), key=lambda item: item[1], reverse=True)
    kept = [p for p, score in ranked if _accept(score)]
    threshold = config.RERANK_MIN_SCORE
    logger.info(f"[rag.rerank] 候选={len(passages)} 通过={len(kept)} 耗时={elapsed:.2f}s "
                f"阈值={'关' if threshold is None else threshold} "
                f"分数=[{','.join(f'{score:.3f}' for _, score in ranked)}] question={question!r}")

    for p, score in ranked:
        if p in kept:
            logger.info(f"[rag.rerank] 通过分数={score:.3f} 通过文段={p.text!r}")

    return kept


def _clip(text: str) -> str:
    """送进模型的文本截到 RERANK_MAX_CHARS。
    只截「打分用的文本」，交回调用方的 Passage 仍是整段原文：
    截断是为了压住注意力的平方开销，不是为了少给模型看资料。"""
    limit = config.RERANK_MAX_CHARS
    return text[:limit] if len(text) > limit else text


def _accept(score: float) -> bool:
    threshold = config.RERANK_MIN_SCORE
    # 判空必须用 is None：0.0 是合法阈值，写成 `if not threshold` 会把「阈值 0」当成「关闭」
    return threshold is None or score >= threshold