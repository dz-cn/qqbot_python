"""向量腿：Chroma 的唯一出口。
本模块独占 embed_model 与持久化目录 —— 别的文件不 import chromadb、不碰集合名、不自己算向量。

与关系层（store_service）的三条契约，缺一条两库就会漂：
  1) id 同源：chunk_id 由 save_section 生成后原样传进来，本模块不生成、也不解析 id；
  2) 只 upsert：同一 chunk_id 覆盖，所以任何一批向量都能重放 —— 重放是唯一的修复手段；
  3) 不裁决权限：metadata 里的 openid/type 只为加速过滤，回来的结果必须由 retrieve 回 rag_parent 复核。
"""
import config
from botpy import logger

META_SPACE = "hnsw:space"                    # chroma 的相似度函数键（旧写法，0.4~1.x 通用）
META_MODEL = "embedding_model"               # 自建 metadata：记录这套向量是哪个模型产的
VECTOR_SPACE = "cosine"                      # 余弦距离：distance = 1 - cos，取负后与字面腿同向
META_OPENID = "openid"
META_TYPE = "type"
META_PARENT = "parent_id"

_CLIENT = None                               # 模块级单例：PersistentClient 持有文件句柄，重复建会互踩
_COLLECTION = None                           # 同上；drop_collection() 时必须一起清掉，否则指向已删对象


def _client():
    """延迟导入 chromadb：没装依赖时只关掉了向量腿，不该把字面腿一起拖死。"""
    global _CLIENT
    if _CLIENT is None:
        try:
            import chromadb
        except ImportError as exc:
            raise RuntimeError("向量腿需要 chromadb：pip install chromadb 并写进 requirements.txt") from exc
        _CLIENT = chromadb.PersistentClient(path=str(_vector_dir()))
    return _CLIENT


def _vector_dir():
    from utils.path_tool import get_project_root_path
    path = get_project_root_path() / config.RAG_VECTOR_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _embeddings():
    """延迟取模型：factory 在导入时就构造了 Ollama 客户端，只查字面腿的脚本不必为它买单。"""
    from model.factory import embedding_model
    return embedding_model


def _collection():
    """取（或首次创建）集合，并校验它是当前这个嵌入模型建的。
    换模型必须重建：维度不同会直接写失败，而维度恰好相同（同为 1024 的两个模型）更坏 ——
    写得进去、查得出来，只是分数全是噪声，且没有任何报错。所以这里宁可硬拦。"""
    global _COLLECTION
    if _COLLECTION is None:
        collection = _client().get_or_create_collection(
            name=config.RAG_VECTOR_COLLECTION,
            metadata={META_SPACE: VECTOR_SPACE, META_MODEL: config.OLLAMA_EMBEDDING_MODEL_NAME},
        )
        stored = (collection.metadata or {}).get(META_MODEL)
        if stored != config.OLLAMA_EMBEDDING_MODEL_NAME:
            raise RuntimeError(
                f"集合 {config.RAG_VECTOR_COLLECTION} 是用 {stored!r} 建的，当前配置为 "
                f"{config.OLLAMA_EMBEDDING_MODEL_NAME!r}。换嵌入模型必须重建："
                f"drop_collection() → 从 rag_chunk 重放全部子块")
        _COLLECTION = collection
    return _COLLECTION


# ---------------------------------------------------------------- 写入与删除
def save_vectors(chunks: list[tuple[str, str]], parent_id: str, openid: str, scope: str) -> int:
    """灌入一个父段的子块向量，返回写入条数。
    chunks 直接用 store.save_section 的返回值 [(chunk_id, chunk_text)]，id 因此天然同源。
    一次 embed_documents 喂一批：Ollama 逐条调用会把网络往返数放大到子块数量级。"""
    if not chunks:
        return 0
    ids = [chunk_id for chunk_id, _ in chunks]
    texts = [text for _, text in chunks]
    _collection().upsert(ids=ids,
                         embeddings=_embeddings().embed_documents(texts),
                         metadatas=[{META_OPENID: openid, META_TYPE: scope, META_PARENT: parent_id}
                                    for _ in chunks])
    logger.info(f"[rag.vector] 灌向量 parent_id={parent_id} 子块={len(ids)}")
    return len(ids)


def delete_vectors(chunk_ids: list[str]) -> int:
    """按 chunk_id 删向量（幂等：id 不存在不报错）。
    入参来自 store.drop_source 的返回值 —— 向量层不认识 source，也不该认识。"""
    if not chunk_ids:
        return 0
    _collection().delete(ids=chunk_ids)
    logger.info(f"[rag.vector] 删向量 条数={len(chunk_ids)}")
    return len(chunk_ids)


def drop_collection() -> None:
    """整个集合作废（换嵌入模型时用）。必须同时清掉缓存对象，否则后续调用还指着已删集合。"""
    global _COLLECTION
    _client().delete_collection(name=config.RAG_VECTOR_COLLECTION)
    _COLLECTION = None
    logger.info(f"[rag.vector] 集合已删除 name={config.RAG_VECTOR_COLLECTION}")


# ---------------------------------------------------------------- 语义腿
def vector_search(question: str, open_ids: list[str],
                  limit: int = config.RAG_VECTOR_LIMIT) -> list[tuple[str, float]]:
    """向量检索子块，返回 [(chunk_id, score)]，score 越大越相关（与 keyword_search 同形状）。
    cosine 的 distance = 1 - 相似度，取负变正向 —— 两腿同向，融合时不必各记一套方向约定。

    where 只是「少算一点」，不是权限裁决：metadata 是 rag_parent 的副本，副本可以旧、可以缺，
    所以调用方拿到 chunk_id 后仍要回 rag_parent 复核（见 sql_visible_parents 的注释）。
    """
    if not (question or "").strip() or not open_ids:
        return []
    collection = _collection()
    total = collection.count()
    if total == 0:
        logger.info("[rag.vector] 集合为空，语义腿跳过")
        return []
    got = collection.query(
        query_embeddings=[_embeddings().embed_query(question)],
        n_results=min(limit, total),          # n_results 超过条目数在部分版本会直接报错
        where={"$or": [{META_OPENID: openid} for openid in open_ids] + [{META_TYPE: config.SCOPE_GLOBAL}]},
        include=["distances"],
    )
    ids = (got.get("ids") or [[]])[0]
    distances = (got.get("distances") or [[]])[0]
    hits = [(chunk_id, round(-distance, 4)) for chunk_id, distance in zip(ids, distances)]
    logger.info(f"[rag.vector] 命中={len(hits)} 集合总数={total} 问句={question!r}")
    return hits


def count_vectors() -> int:
    """集合内条数，仅供自检与漂移判断（与 rag_chunk 行数不等即说明两库不同步）。"""
    return _collection().count()