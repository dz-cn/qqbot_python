"""RAG 写编排：一篇文本 → 切块 → 关系层（rag_parent/rag_chunk/rag_fts）→ 向量层（Chroma）。
本模块只负责顺序与失败策略，不写 SQL、不 import chromadb、也不解析 id —— 那些各有唯一出口。

顺序为什么是「关系层先、向量层后」且向量失败不回滚：
  rag_parent / rag_chunk 是唯一真相，Chroma 只是它的派生索引。派生索引坏了可以重放
  （rebuild_vectors），而为了一个可重放的东西回滚已提交的真相，是把小错换成大错。
  两库之间不存在分布式事务，所以这里追求的不是「原子」，而是「任何一步失败后都能重放修好」。
"""
from pathlib import Path

from utils.log_util import logger
from service.rag import store_service as store
from service.rag import vector_service as vec
from service.rag.segment_service import segment


def ingest(text: str, source: str, openid: str, scope: str) -> int:
    """写入或覆盖一篇文档，返回子块总数。
    幂等：同一 source 再调一次就是重灌，所以不需要单独的 reingest。
    切块数量可能变少（改了阈值或改了正文），因此必须先按 source 整篇清理，不能只覆盖。"""
    drop(source)
    blocks = segment(text)
    if not blocks:
        logger.warning(f"[rag.ingest] 切块为空，未写入 source={source} 文本长度={len(text or '')}")
        return 0

    total = 0
    for index, blk in enumerate(blocks):
        parent_id = store.get_parent_id(source, index)
        chunks = store.save_section(parent_id, openid, scope, blk["parent_text"], blk["children"])
        total += len(chunks)
        vec.save_vectors(chunks, parent_id, openid, scope)   # 抛错则两库漂移：跑 rebuild_vectors() 补齐
    logger.info(f"[rag.ingest] source={source} scope={scope} 父段={len(blocks)} 子块={total}")
    return total


def ingest_file(path: str | Path, openid: str, scope: str, source: str = "") -> int:
    """从本地文件灌一篇。source 缺省用文件名 —— 这就是 source 的全部来历：
    一个稳定、可复现、不含 '::' 的分组键。用文件名而非内容哈希，是为了「改了内容仍是同一篇」。
    参数顺序：默认值参数必须排在必填参数之后（Python 语法要求），所以 source 放最后。"""
    file = Path(path)
    return ingest(file.read_text(encoding="utf-8"), source or file.name, openid, scope)


def drop(source: str) -> int:
    """整篇删除：向量与关系层都清，返回被删子块数。
    先删向量再删关系层：chunk_id 只有关系层还活着时才拿得到，反过来的话这些向量就成了孤儿。"""
    removed = store.drop_source(source)
    vec.delete_vectors(removed)
    return len(removed)

def rebuild_vectors() -> int:
    """从 rag_chunk 全量重放向量 —— 两库漂移的唯一修复手段。
    内部先 vec.drop_collection() 再逐段重放：既补齐缺失向量，也清掉关系层已删的孤儿向量，
    因此换嵌入模型直接调本函数即可（旧集合被作废，新集合以当前模型名重建，校验必过）。
    代价：drop 到重放完成之间向量腿不可用，检索会静默降级为只有字面腿，尽量在低峰执行。
    按父段逐段调用 save_vectors：每次 embed 只处理一节的几块，不会把全库正文压进一个请求。"""
    vec.drop_collection()
    sections = store.all_sections()
    total = 0
    for parent_id, item in sections.items():
        total += vec.save_vectors(item["chunks"], parent_id, item["openid"], item["scope"])
    logger.info(f"[rag.rebuild_vectors] 重放父段={len(sections)} 子块={total}")
    return total