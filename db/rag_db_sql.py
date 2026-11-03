"""RAG 三表的 SQL 构造：rag_parent / rag_chunk / rag_fts。
只返回语句字符串，不执行；所有「值」走 ? 参数（FTS5 的 MATCH 表达式由服务层拼好后作为 ? 传入）。
「为什么这样查」的理由统一写在 service/rag/store_service.py，本文件不复述，避免两处漂移。
"""
import config


# ---------------------------------------------------------------- 写入
def sql_insert_parent() -> str:
    """幂等锚点：同一 parent_id 重灌即覆盖。
    ⚠️ 外键开启时 REPLACE 的实现是「先 DELETE 再 INSERT」，会级联删掉 rag_chunk 的子行，
       所以调用方必须先清子块与 FTS，再执行本句。"""
    return ("INSERT OR REPLACE INTO rag_parent(parent_id, openid, type, full_text) "
            "VALUES (?,?,?,?)")


def sql_insert_chunk() -> str:
    """普通 INSERT 而非 REPLACE：调用方已按 parent_id 清过子块，这里再撞 UNIQUE(parent_id, seq)
    就是真 bug，要它报错，不要 REPLACE 把错误无声吞掉。"""
    return "INSERT INTO rag_chunk(chunk_id, parent_id, seq, chunk_text) VALUES (?,?,?,?)"


def sql_insert_fts() -> str:
    """FTS5 只需普通 INSERT，倒排索引由 fts5 模块自动维护；它没有主键概念，重复插入即重复文档，
    所以重灌前必须先 sql_delete_fts_by_parent。"""
    return "INSERT INTO rag_fts(chunk_text, chunk_id, parent_id) VALUES (?,?,?)"


# ---------------------------------------------------------------- 清理
def sql_delete_fts_by_parent() -> str:
    return "DELETE FROM rag_fts WHERE parent_id = ?"


def sql_delete_chunks() -> str:
    return "DELETE FROM rag_chunk WHERE parent_id = ?"


def sql_delete_parent() -> str:
    return "DELETE FROM rag_parent WHERE parent_id = ?"


def sql_chunk_ids_by_parent() -> str:
    """删段前取回 chunk_id —— 同步删 Chroma 向量要用（Chroma 在事务之外，只能我们掌控顺序）。"""
    return "SELECT chunk_id FROM rag_chunk WHERE parent_id = ?"


# ---------------------------------------------------------------- 按篇取节
def sql_parent_ids_by_source() -> str:
    """一篇文档的全部父段 id，按原文节序返回；区间上下界由服务层算好传入，故本句不含字面量。"""
    return ("SELECT parent_id FROM rag_parent "
            "WHERE parent_id >= ? AND parent_id < ? ORDER BY parent_id")


# ---------------------------------------------------------------- 归并与回捞
def sql_fetch_visible_parents(id_marks: str, scope_marks: str) -> str:
    """按 id 批量捞整段全文，并在同一条 SQL 里完成权限裁决 —— 不可见的 parent 直接不返回。
    权限唯一真相在 rag_parent：语义分支的 Chroma metadata 只是副本，可能旧、可能缺，
    所以回捞这一步是整条读链路最后的、也是唯一权威的设防点。"""
    return (f"SELECT parent_id, full_text FROM rag_parent "
            f"WHERE parent_id IN ({id_marks}) "
            f"AND (openid IN ({scope_marks}) OR type = '{config.SCOPE_GLOBAL}')")

def sql_locate_chunks(marks: str) -> str:
    """chunk_id -> (parent_id, seq)：两条检索腿回来的 id 都靠它找到父段。
    带上 seq 是为了窗口降级 —— 不知道命中块排在第几，就不知道从哪里居中展开。"""
    return f"SELECT chunk_id, parent_id, seq FROM rag_chunk WHERE chunk_id IN ({marks})"

def sql_chunks_by_parent() -> str:
    """按原文顺序取一父段全部子块；用 seq 数值排序，避开 chunk_id 的字典序坑（c10 < c2）。"""
    return "SELECT seq, chunk_id, chunk_text FROM rag_chunk WHERE parent_id = ? ORDER BY seq"


def sql_window_chunks() -> str:
    """邻接窗口：整段太长超上下文预算时，只取命中块 seq ± span 的连续内容。"""
    return ("SELECT seq, chunk_id, chunk_text FROM rag_chunk "
            "WHERE parent_id = ? AND seq BETWEEN ? AND ? ORDER BY seq")


# ---------------------------------------------------------------- 关键词腿
def sql_keyword_search(scope_marks: str) -> str:
    """FTS5 BM25 检索子块；rank 越小越相关（服务层取负换算成正向分数）。
    权限过滤走子查询回 rag_parent —— FTS 里不存 openid/type，避免第二个权限真相源。"""
    return (
        "SELECT chunk_id, parent_id, rank FROM rag_fts "
        "WHERE rag_fts MATCH ? "
        f"AND parent_id IN (SELECT parent_id FROM rag_parent "
        f"                    WHERE openid IN ({scope_marks}) OR type = '{config.SCOPE_GLOBAL}') "
        "ORDER BY rank LIMIT ?"
    )


def sql_all_chunks_with_parent() -> str:
    """全部子块 + 其父段的权限字段（JOIN 一次取全，避免「先列父段再逐段查子块」的 N+1）。
    唯一用途是向量索引重放，见 rag_service.rebuild_vectors。"""
    return ("SELECT rc.chunk_id, rc.parent_id, rc.chunk_text, rp.openid, rp.type "
            "FROM rag_chunk rc JOIN rag_parent rp ON rp.parent_id = rc.parent_id "
            "ORDER BY rc.parent_id, rc.seq")
