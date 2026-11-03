import botpy
import config
from MyClient import MyClient
from db.db import init_db
from service.rag.retrieve_service import retrieve
from service.rag.rag_service import ingest, ingest_file


def insertRag(text: str):
    """
    将文本存储到向量数据库和索引数据库
    :param text: 要存储的文本
    """
    # QQ 机器人聊天使用下：source：消息 id | openid：用户id/群聊id | scope：区分是群聊/私聊
    return ingest(text, "source", "openid", "scope")

def insertRagFile(file: str):
    """
    将文件存储到向量数据库和索引数据库
    :param file: 要存储的文件
    """
    return ingest_file(file, "openid", "scope", "source")

def queryRag(question: str):
    """
    从混合检索
    :param question: 要查询的问题
    """
    return retrieve(question, ["openid"])


if __name__ == "__main__":
    init_db()
    intents = botpy.Intents(public_messages=True, direct_message=True)
    client = MyClient(intents=intents)
    client.run(appid=config.QQ_BOT_APPID, secret=config.QQ_BOT_SECRET)