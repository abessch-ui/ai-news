import os
import datetime
import requests
import anthropic

MODEL = "claude-sonnet-5"
TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]
client = anthropic.Anthropic()  # ключ берётся из ANTHROPIC_API_KEY

MSK = datetime.timezone(datetime.timedelta(hours=3))
TODAY = datetime.datetime.now(MSK).strftime("%d.%m.%Y")


def read_prompt(name):
    with open(f"prompts/{name}.md", encoding="utf-8") as f:
        return f.read().replace("{DATE}", TODAY)


def ask_claude(task):
    system = read_prompt("system")
    messages = [{"role": "user", "content": task}]
    for _ in range(5):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=system,
            messages=messages,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
        )
        if resp.stop_reason == "pause_turn":  # длинный поиск — продолжаем
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    return "".join(b.text for b in resp.content if b.type == "text").strip()


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
        if not r.ok:  # если HTML сломан — отправляем простым текстом
            data.pop("parse_mode")
            requests.post(url, json=data).raise_for_status()


if __name__ == "__main__":
    ru = ask_claude(read_prompt("russia"))
    world = ask_claude(read_prompt("world"))
    digest = (f"<b>🤖 AI-дайджест за {TODAY}</b>\n\n"
              f"<b>🇷🇺 Россия</b>\n{ru}\n\n"
              f"<b>🌍 Мир</b>\n{world}")
    send_telegram(digest)
    print("Отправлено")
