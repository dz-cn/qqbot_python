import botpy
import config
from MyClient import MyClient

if __name__ == "__main__":
    intents = botpy.Intents(public_messages=True, direct_message=True)
    client = MyClient(intents=intents)
    client.run(appid=config.QQ_BOT_APPID, secret=config.QQ_BOT_SECRET)