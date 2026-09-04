import json, urllib.request

cfg = json.load(open("C:/Users/pc/Desktop/NIFTY PAPER TRADING/Gold Silver live trading/config/settings.json"))["telegram"]
token = cfg["bot_token"]
url = f"https://api.telegram.org/bot{token}/getUpdates"
resp = urllib.request.urlopen(url, timeout=10)
data = json.loads(resp.read())

print("=== All Telegram Chats ===")
seen = set()
for u in data.get("result", []):
    msg = u.get("message", {})
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    name = chat.get("first_name", "") or chat.get("username", "")
    text = msg.get("text", "")
    if chat_id and chat_id not in seen:
        seen.add(chat_id)
        print(f"  chat_id={chat_id}  name={name}  last_msg={text}")
print(f"\nCurrent config chat_id: {cfg['chat_id']}")
