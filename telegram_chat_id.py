
import requests, os, json
TOKEN = os.environ.get('TOKEN')  # lub wklej token jako string
r = requests.get(f'https://api.telegram.org/bot{TOKEN}/getUpdates').json()
# drukuj wszystkie chat.id
for u in r.get('result', []):
    m = u.get('message') or u.get('channel_post') or {}
    chat = m.get('chat') or {}
    if chat:
        print('chat id:', chat.get('id'), ' type:', chat.get('type'), ' text:', m.get('text'))