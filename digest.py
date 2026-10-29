import os
import datetime
import requests
import anthropic
from bs4 import BeautifulSoup

MODEL = "claude-sonnet-5"
TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]
client = anthropic.Anthropic()  # ключ берётся из ANTHROPIC_API_KEY

MSK = datetime.timezone(datetime.timedelta(hours=3))
TODAY = datetime.datetime.now(MSK).strftime("%d.%m.%Y")

# Публичные Telegram-каналы, посты из которых попадают в дайджест
TG_CHANNELS = ["rvnikita_blog", "ai_newz", "llm_under_hood", "rybakalexey"]


def read_prompt(name):
    with open(f"prompts/{name}.md", encoding="utf-8") as f:
        return f.read().replace("{DATE}", TODAY)


def ask_claude(task, search=True):
    system = read_prompt("system")
    messages = [{"role": "user", "content": task}]
    extra = {"tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}]} if search else {}
    for _ in range(5):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=system,
            messages=messages,
            **extra,
        )
        if resp.stop_reason == "pause_turn":  # длинный поиск — продолжаем
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    print(f"Claude: stop_reason={resp.stop_reason}, {len(text)} символов")
    return text


def fetch_channel_posts(channel, hours=24):
    """Посты публичного канала за последние `hours` часов (через веб-превью t.me/s/)."""
    r = requests.get(f"https://t.me/s/{channel}", timeout=30,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    posts = []
    for msg in BeautifulSoup(r.text, "html.parser").select("div.tgme_widget_message[data-post]"):
        time_tag = msg.select_one("a.tgme_widget_message_date time[datetime]")
        text_tag = msg.select_one("div.tgme_widget_message_text")
        if not time_tag or not text_tag:
            continue
        date = datetime.datetime.fromisoformat(time_tag["datetime"])
        if date < since:
            continue
        posts.append({"url": f"https://t.me/{msg['data-post']}",
                      "text": text_tag.get_text("\n", strip=True)[:2000]})
    return posts


def telegram_section():
    blocks = []
    for channel in TG_CHANNELS:
        try:
            posts = fetch_channel_posts(channel)
        except Exception as e:  # один недоступный канал не должен ронять дайджест
            print(f"Не удалось получить @{channel}: {e}")
            continue
        print(f"@{channel}: постов за сутки — {len(posts)}")
        for p in posts:
            blocks.append(f"Канал @{channel}, пост {p['url']}:\n{p['text']}")
    if not blocks:
        print("Свежих постов в каналах нет — раздел пропущен")
        return None
    return ask_claude(read_prompt("telegram") + "\n\nПосты:\n\n" + "\n\n---\n\n".join(blocks),
                      search=False)


def split(text, limit=4000):  # лимит Telegram — 4096 символов
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for chunk in split(text):
        data = {"chat_id": TG_CHAT_ID, "text": chunk,
                "parse_mode": "HTML", "disable_web_page_preview": True}
        r = requests.post(url, json=data)
        print(f"Сообщение {len(chunk)} символов: {r.status_code}")
        if not r.ok:  # если HTML сломан — отправляем простым текстом
            data.pop("parse_mode")
            requests.post(url, json=data).raise_for_status()


if __name__ == "__main__":
    ru = ask_claude(read_prompt("russia"))
    world = ask_claude(read_prompt("world"))
    tg = telegram_section()
    digest = (f"<b>🤖 AI-дайджест за {TODAY}</b>\n\n"
              f"<b>🇷🇺 Россия</b>\n{ru}\n\n"
              f"<b>🌍 Мир</b>\n{world}")
    if tg:
        digest += f"\n\n<b>📢 Telegram-каналы</b>\n{tg}"
    send_telegram(digest)
    print("Отправлено")
