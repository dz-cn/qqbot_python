"""RAG 关系层：父段、子块、FTS 倒排的写入 / 回捞 / 按篇清理。
读走 base_service.query，多表写走单事务 _tx；SQL 全部来自 db 门面，本文件不出现 SQL 字面量。

id 约定（只能由本文件生成）：
    parent_id = f"{source}::{节序号:03d}"   零填充使「按 parent_id 排序」== 「按原文节序」
    chunk_id  = f"{parent_id}::c{序号}"     从不参与排序（顺序一律走 rag_chunk.seq），故不填充
同一批 id 一次生成后同批喂给 SQLite 与 Chroma：靠 parent_id 归并，靠 chunk_id 去重。
"""
import re
from typing import NamedTuple

import config
import db as db_sql
from botpy import logger
from service.base_service import tx
from service.base_service import query

ID_SEPARATOR = "::"                        # id 分段符；source 内不得出现，否则前缀有歧义
SECTION_INDEX_WIDTH = 3                    # 节序号零填充宽度
INDEX_UPPER_CHAR = chr(ord(":") + 1)       # ':' 的下一个码点 ';'，用作前缀区间的开上界
WINDOW_SIZE = 3                            # 滑窗字数：trigram 下能命中的最短单位就是 3 字
WINDOW_STEP = 2                            # 步长 2（窗长 3）→ 相邻窗口重叠 1 字，措辞错位时不漏
WINDOW_MAX_COUNT = 8                       # 窗口越多召回越宽也越噪，封顶 8 个（约覆盖 17 字）


# ---------------------------------------------------------------- id
def get_parent_id(source: str, index: int) -> str:
    """父段 id：同一篇同一节永远得到同一 id，重灌即覆盖（幂等的前提）。"""
    return f"{_source_prefix(source)}{index:0{SECTION_INDEX_WIDTH}d}"


def _chunk_id(parent_id: str, seq: int) -> str:
    return f"{parent_id}{ID_SEPARATOR}c{seq}"


def _source_prefix(source: str) -> str:
    """source → "source::"。id 生成与前缀区间都走这里，所以校验不可能只写在一侧。
    失守的后果不是报错而是无声多删/多捞：
      含 :: —— 一篇会切成两截，切分点不唯一；以 : 结尾 —— "note:" 拼成 "note:::000"，
      恰好落进 "note" 那一篇的区间里。"""
    if ID_SEPARATOR in source:
        raise ValueError(f"source 不得包含分隔符 {ID_SEPARATOR!r}：{source!r}")
    if source.endswith(":"):
        raise ValueError(f"source 不得以冒号结尾（会与分隔符粘连）：{source!r}")
    return f"{source}{ID_SEPARATOR}"


# ---------------------------------------------------------------- 写入
def save_section(parent_id: str, openid: str, scope: str,
                 full_text: str, children: list[str]) -> list[tuple[str, str]]:
    """写入一个父段及其全部子块，返回 [(chunk_id, chunk_text)] —— 正是向量层要灌的东西。

    先删后插而非 INSERT OR REPLACE：重灌后子块可能变少，多余的旧行不会自己消失（孤儿会被命中）；
    FTS5 无主键概念，重复插入即重复文档；外键开启下 REPLACE 父行还会级联删掉子行。
    顺序：清 FTS → 清 chunk → 写 parent → 逐块写 chunk + FTS（同一事务）。
    """
    full_text = (full_text or "").strip()
    blocks = [(seq, text.strip()) for seq, text in enumerate(children) if text and text.strip()]
    if not full_text or not blocks:
        logger.warning(f"[rag.save_section] 内容被拒 parent_id={parent_id} "
                       f"整段空={not full_text} 有效子块={len(blocks)}")
        return []

    statements = [(db_sql.sql_delete_fts_by_parent(), (parent_id,)),
                  (db_sql.sql_delete_chunks(), (parent_id,)),
                  (db_sql.sql_insert_parent(), (parent_id, openid, scope, full_text))]
    for seq, text in blocks:
        chunk_id = _chunk_id(parent_id, seq)
        statements.append((db_sql.sql_insert_chunk(), (chunk_id, parent_id, seq, text)))
        statements.append((db_sql.sql_insert_fts(), (text, chunk_id, parent_id)))
    tx(statements)

    logger.info(f"[rag.save_section] 入库 parent_id={parent_id} scope={scope} 子块={len(blocks)}")
    return [(_chunk_id(parent_id, seq), text) for seq, text in blocks]


# ---------------------------------------------------------------- 按篇（source）
def parents_of_source(source: str) -> list[str]:
    """同一篇文档的父段 id，按原文节序。
    过滤写成 parent_id 的闭开区间 [source::, source::;) 而不是 LIKE：
    实测只有区间式能在任何 PRAGMA 下走成 SEARCH（有界索引扫描），且它不要求
    给 source 加「不得含 _ / %」的命名限制。区间之所以精确：'::' 是字面量，
    其后只可能是序号数字（0x30~0x39 < ':' < ';'），兄弟前缀（note-1 / note-12）被天然排除。"""
    low = _source_prefix(source)
    rows = query(db_sql.sql_parent_ids_by_source(), (low, low + INDEX_UPPER_CHAR))
    return [row["parent_id"] for row in rows]


def drop_source(source: str) -> list[str]:
    """删掉一篇文档的全部父段/子块/FTS 行，返回被删 chunk_id 供向量层同步删除。
    每段一个事务，中途失败会留半套 —— 可接受，因为重灌整体幂等，重跑即恢复。"""
    parent_ids = parents_of_source(source)
    removed: list[str] = []
    for parent_id in parent_ids:
        removed += [row["chunk_id"] for row in query(db_sql.sql_chunk_ids_by_parent(), (parent_id,))]
        tx([(db_sql.sql_delete_fts_by_parent(), (parent_id,)),
             (db_sql.sql_delete_chunks(), (parent_id,)),
             (db_sql.sql_delete_parent(), (parent_id,))])
    logger.info(f"[rag.drop_source] source={source} 父段={len(parent_ids)} 子块={len(removed)}")
    return removed


# ---------------------------------------------------------------- 检索用的读
class ChunkLoc(NamedTuple):
    """子块的位置：属于哪个父段、在父段内排第几。"""
    parent_id: str
    seq: int


def locate_chunks(chunk_ids: list[str]) -> dict[str, ChunkLoc]:
    """chunk_id -> ChunkLoc。查不到的 id 不出现在结果里 ——
    那是「向量层有、关系层已删」的漂移痕迹，静默丢弃，由 rebuild_vectors 修复。"""
    if not chunk_ids:
        return {}
    marks = ",".join("?" * len(chunk_ids))
    return {row["chunk_id"]: ChunkLoc(row["parent_id"], row["seq"])
            for row in query(db_sql.sql_locate_chunks(marks), tuple(chunk_ids))}


def fetch_full_texts(parent_ids: list[str], open_ids: list[str]) -> dict[str, str]:
    """批量捞整段全文（小切大返的「大返」），并在同一条 SQL 里做权限裁决：
    不可见的 parent_id 直接不出现在返回结果里，调用方按「有没有这个 key」判断即可。
    为什么裁决写在 SQL 而不是取回全文后在 Python 里筛：不该让别人的正文先进本进程内存，
    再决定给不给看；而且那样要多查一次 openid/type，等于把权限真相摊到两处。
    为什么 open_ids 是必填而不是可选：可选参数当开关会造出「默认不安全」的 API ——
    漏传一个参数就退化成无过滤版本，而且不报错。"""
    if not parent_ids or not open_ids:
        return {}
    id_marks = ",".join("?" * len(parent_ids))
    scope_marks = ",".join("?" * len(open_ids))
    return {row["parent_id"]: row["full_text"]
            for row in query(db_sql.sql_fetch_visible_parents(id_marks, scope_marks),
                             (*parent_ids, *open_ids))}


def chunks_of_section(parent_id: str, around_seq: int | None = None,
                      span: int = 2) -> list[dict]:
    """取子块：不给 around_seq 返回全部；给了只返回命中块 ±span 的连续窗口（整段太长时的降级）。"""
    if around_seq is None:
        return query(db_sql.sql_chunks_by_parent(), (parent_id,))
    return query(db_sql.sql_window_chunks(),
                 (parent_id, max(0, around_seq - span), around_seq + span))


def all_sections() -> dict[str, dict]:
    """{parent_id: {"openid", "scope", "chunks": [(chunk_id, chunk_text)]}} —— 全库导出。
    目前唯一调用者是 rag_service.rebuild_vectors（向量层漂移后的重放）。
    一次 JOIN 平铺回来再在 Python 里归组：SQL 侧分组会把权限字段挤成 GROUP BY 的负担。"""
    sections: dict[str, dict] = {}
    for row in query(db_sql.sql_all_chunks_with_parent()):
        item = sections.setdefault(row["parent_id"],
                                   {"openid": row["openid"], "scope": row["type"], "chunks": []})
        item["chunks"].append((row["chunk_id"], row["chunk_text"]))
    return sections


# ---------------------------------------------------------------- 关键词腿（FTS5）
def _match_expr(keyword: str) -> str:
    """把检索词压成连续字后做 3 字滑窗，拼成一条 OR 表达式。
    为什么必须有滑窗：用户问法与原文字序几乎不会一致
    （「未休完的年假怎么结转」 vs 原文「未休完的年假最多结转到下一年」），
    整句当一个短语必然零命中，而只要有 3 个连续字重合就能捞回候选。
    不做 AND→OR 多轮降级：字面腿的任务是别零召回，精度由后面的 RRF 融合与精排负责。"""
    core = re.sub(r"[\s\W]+", "", keyword or "")        # \w 含汉字：只去掉标点与空白
    windows = [core[start:start + WINDOW_SIZE]
               for start in range(0, len(core) - WINDOW_SIZE + 1, WINDOW_STEP)][:WINDOW_MAX_COUNT]
    return " OR ".join(f'"{window}"' for window in windows)


def keyword_search(keyword: str, open_ids: list[str],
                   limit: int = config.RAG_KEYWORD_LIMIT) -> list[tuple[str, float]]:
    """FTS5 BM25 检索子块，返回 [(chunk_id, score)]，score 越大越相关（rank 越小，故取负）。
    参数名用 keyword 而非 query：本模块从 base_service 导入了同名函数 query，
    若参数叫 query 会把它遮蔽掉，函数体内 query(...) 直接变成「字符串不可调用」。"""
    expression = _match_expr(keyword)
    if not expression or not open_ids:
        logger.info(f"[rag.keyword] 跳过（检索词不足 {WINDOW_SIZE} 字或无身份）keyword={keyword!r}")
        return []
    marks = ",".join("?" * len(open_ids))
    rows = query(db_sql.sql_keyword_search(marks), (expression, *open_ids, limit))
    logger.info(f"[rag.keyword] 命中={len(rows)} 表达式={expression}")
    return [(row["chunk_id"], round(-float(row["rank"]), 4)) for row in rows]