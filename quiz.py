import os
import json
import html
import time
import requests
import anthropic
from pydantic import BaseModel

MODEL = "claude-opus-5-5"
TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT_ID = str(os.environ["TG_CHAT_ID"])
SESSION_MINUTES = int(os.environ.get("SESSION_MINUTES", "180"))  # сколько бот максимум «живёт» за запуск
IDLE_MINUTES = int(os.environ.get("IDLE_MINUTES", "30"))         # выключается, если долго нет действий
HISTORY_FILE = "quiz_history.json"
API = f"https://api.telegram.org/bot{TG_TOKEN}"
client = anthropic.Anthropic()  # ключ берётся из ANTHROPIC_API_KEY


class Question(BaseModel):
    question: str
    hint1: str
    hint2: str
    answer: str
    explanation: str


class Quiz(BaseModel):
    questions: list[Question]


class Verdict(BaseModel):
    correct: bool
    comment: str


# ---------- Claude ----------

def read_prompt(name):
    with open(f"prompts/{name}.md", encoding="utf-8") as f:
        return f.read()


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-150:], f, ensure_ascii=False, indent=1)


def ask(output_format, system, task, effort):
    resp = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": task}],
        output_format=output_format,
        output_config={"effort": effort},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    return resp.parsed_output


def generate_quiz(history):
    task = "Придумай 3 новых вопроса."
    if history:
        task += "\nЭти ответы уже были, не повторяй их и близкие темы:\n" + "\n".join(history[-100:])
    return ask(Quiz, read_prompt("quiz_system"), task, "high").questions


def check_answer(q, guess):
    task = (f"Вопрос: {q.question}\nПравильный ответ: {q.answer}\n"
            f"Ответ игрока: {guess}")
    return ask(Verdict, read_prompt("quiz_judge"), task, "low")


# ---------- Telegram ----------

def tg(method, **data):
    r = requests.post(f"{API}/{method}", json=data, timeout=60)
    return r.json().get("result") if r.ok else None


def send(text, buttons=None):
    data = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}
    if buttons:
        data["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d}] for t, d in buttons]}
    return tg("sendMessage", **data)


def drop_buttons(msg):
    if msg:
        tg("editMessageReplyMarkup", chat_id=TG_CHAT_ID, message_id=msg["message_id"])


def skip_old_updates():
    old = tg("getUpdates", offset=-1, timeout=0) or []
    return old[-1]["update_id"] + 1 if old else 0


# ---------- Игра ----------

class Game:
    def __init__(self):
        self.history = load_history()
        self.questions = []
        self.i = 0
        self.stage = 0       # 0 — вопрос, 1 — подсказка 1, 2 — подсказка 2, 3 — ответ открыт
        self.last_msg = None
        self.finished = True

    def new_round(self):
        send("🎲 Готовлю 3 новых вопроса…")
        self.questions = generate_quiz(self.history)
        self.history += [q.answer for q in self.questions]
        save_history(self.history)
        self.i, self.finished = 0, False
        self.show_question()

    @property
    def q(self):
        return self.questions[self.i]

    def show(self, text, buttons):
        drop_buttons(self.last_msg)
        self.last_msg = send(text, buttons)

    def show_question(self):
        self.stage = 0
        self.show(f"<b>❓ Вопрос {self.i + 1} из {len(self.questions)}</b>\n\n{html.escape(self.q.question)}\n\n"
                  "<i>Напиши свой ответ сообщением или возьми подсказку.</i>",
                  [("💡 Подсказка 1", "next")])

    def advance(self):
        if self.finished:
            return
        if self.stage == 0:
            self.stage = 1
            self.show(f"💡 <b>Подсказка 1:</b> {html.escape(self.q.hint1)}", [("💡 Подсказка 2", "next")])
        elif self.stage == 1:
            self.stage = 2
            self.show(f"💡 <b>Подсказка 2:</b> {html.escape(self.q.hint2)}", [("✅ Показать ответ", "next")])
        elif self.stage == 2:
            self.reveal()
        elif self.i + 1 < len(self.questions):
            self.i += 1
            self.show_question()
        else:
            self.debrief()

    def reveal(self, prefix=""):
        self.stage = 3
        last = self.i + 1 == len(self.questions)
        self.show(f"{prefix}✅ <b>Ответ:</b> {html.escape(self.q.answer)}",
                  [("📚 Развёрнутый разбор" if last else "➡️ Следующий вопрос", "next")])

    def guess(self, text):
        if self.finished or self.stage == 3:
            return
        v = check_answer(self.q, text)
        if v.correct:
            self.reveal(f"🎉 Верно! {html.escape(v.comment)}\n\n")
        else:
            nxt = {0: "💡 Подсказка 1", 1: "💡 Подсказка 2", 2: "✅ Показать ответ"}[self.stage]
            self.show(f"🤔 Пока нет. {html.escape(v.comment)}\n<i>Попробуй ещё раз или возьми подсказку.</i>",
                      [(nxt, "next")])

    def debrief(self):
        drop_buttons(self.last_msg)
        self.last_msg = None
        send("<b>📚 Разбор вопросов</b>")
        for n, q in enumerate(self.questions, 1):
            send(f"<b>{n}. {html.escape(q.answer)}</b>\n\n<i>{html.escape(q.question)}</i>\n\n"
                 f"{html.escape(q.explanation)}")
        self.finished = True
        send("Раунд окончен 👏 Ещё 3 вопроса — /new, закончить — /stop", None)


def main():
    game = Game()
    offset = skip_old_updates()
    start = last_action = time.time()
    game.new_round()

    while time.time() - start < SESSION_MINUTES * 60 and time.time() - last_action < IDLE_MINUTES * 60:
        updates = tg("getUpdates", offset=offset, timeout=30,
                     allowed_updates=["message", "callback_query"]) or []
        for u in updates:
            offset = u["update_id"] + 1
            if "callback_query" in u:
                cb = u["callback_query"]
                tg("answerCallbackQuery", callback_query_id=cb["id"])
                if str(cb["message"]["chat"]["id"]) != TG_CHAT_ID:
                    continue
                last_action = time.time()
                if cb["message"]["message_id"] == (game.last_msg or {}).get("message_id"):
                    game.advance()
            elif "message" in u:
                msg = u["message"]
                text = (msg.get("text") or "").strip()
                if str(msg["chat"]["id"]) != TG_CHAT_ID or not text:
                    continue
                last_action = time.time()
                if text.startswith("/new"):
                    game.new_round()
                elif text.startswith("/stop"):
                    send("До встречи! 👋")
                    return
                elif not text.startswith("/"):
                    game.guess(text)

    drop_buttons(game.last_msg)
    send("⏰ Бот ушёл спать. Следующие вопросы придут по расписанию "
         "(или запусти вручную в GitHub → Actions → Quiz → Run workflow).")


if __name__ == "__main__":
    main()
