from ibapi.client import EClient
from ibapi.wrapper import EWrapper
import time


class IBClient(EClient, EWrapper):
    def __init__(self, host, port_id, client_id):
        EClient.__init__(self, self)
        
        self.connect(host, port_id, client_id)
    
    
    def updateNewsBulletin(self, msgId: int, msgType: id, newsMessage: str, originExch: str):
        print("News Bulletins. MsgId:", msgId, "Type:", msgType, "Message:", newsMessage, "Exchange of Origin:", originExch)
    

if __name__ == "__main__":
    app = IBClient("127.0.0.1", 7497, 0)
    time.sleep(1)
    app.reqNewsBulletins(True)