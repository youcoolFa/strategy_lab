"""假的實盤主程式(只給 tests/integration/test_ui_daemon_lifecycle.py 用):不連交易所、不下單。
模擬 live/main.py 對訊號的反應:SIGTERM = 收尾後結束(印「收尾完成」);SIGUSR1 = 脫離,直接結束不收尾。"""

import signal
import sys
import time

print("假程式啟動", " ".join(sys.argv[1:]), flush=True)


def on_term(signum, frame):
    print("開始收尾:取消掛單、市價平倉(假的)", flush=True)
    time.sleep(0.3)
    print("收尾完成", flush=True)
    sys.exit(0)


def on_detach(signum, frame):
    print("脫離:不收尾", flush=True)
    sys.exit(0)


signal.signal(signal.SIGTERM, on_term)
signal.signal(signal.SIGUSR1, on_detach)
while True:
    time.sleep(0.1)
