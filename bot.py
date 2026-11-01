# -*- coding: utf-8 -*-
# Jamoa ro'yxatdan o'tkazish + test + kabinet boti (PythonAnywhere uchun)
# Kutubxona: pip3.10 install --user pyTelegramBotAPI

import csv
import html
import unicodedata
import json
import os
import random
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import telebot
from telebot import types

# ====== SOZLAMALAR ======
TOKEN = "BU_YERGA_YANGI_TOKENNI_QOYING"        # @BotFather dan olingan YANGI token
ADMIN_IDS = [8461176514]                        # Admin Telegram ID
MEMBERS_COUNT = 5                               # Jamoadagi odamlar soni
CHANNEL = "@ziyokitobxon"                       # Majburiy obuna kanali (bot kanalda ADMIN bo'lishi shart)
CHANNEL_URL = "https://t.me/ziyokitobxon"
TEST_QUESTIONS = 20                             # Har bir ishtirokchiga beriladigan savollar soni
TEST_MINUTES = 20                               # Test vaqti (daqiqa)
FACULTIES = [
    "Xalqaro munosabatlar",
    "Xalqaro iqtisodiy munosabatlar",
    "Yurisprudensiya",
    "Siyosatshunoslik",
]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "teams.db")
# ========================

TZ = timezone(timedelta(hours=5))  # O'zbekiston vaqti (UTC+5)


def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")


def clock(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%H:%M:%S")


bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
states = {}  # {user_id: {"step": ..., ...}}


# ---------- BAZA ----------
def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS teams(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            username TEXT,
            team_name TEXT,
            created_at TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS members(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER,
            num INTEGER,
            full_name TEXT,
            phone TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            tg_name TEXT,
            username TEXT,
            course INTEGER,
            faculty TEXT,
            grp TEXT,
            created_at TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS questions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT, a TEXT, b TEXT, c TEXT, d TEXT, correct TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS attempts(
            user_id INTEGER PRIMARY KEY,
            started_at REAL,
            finished_at REAL,
            score INTEGER,
            total INTEGER,
            qdata TEXT,
            answers TEXT)""")
        con.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
        try:
            con.execute("ALTER TABLE questions ADD COLUMN qtype TEXT DEFAULT 'c'")
        except sqlite3.OperationalError:
            pass


def get_setting(key, default="0"):
    with db() as con:
        r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_setting(key, value):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, value))


def get_user(uid):
    with db() as con:
        return con.execute(
            "SELECT user_id, tg_name, username, course, faculty, grp FROM users WHERE user_id=?", (uid,)
        ).fetchone()


def save_user(user, course, faculty, grp):
    name = (user.first_name or "") + (" " + user.last_name if user.last_name else "")
    with db() as con:
        con.execute(
            "INSERT OR REPLACE INTO users(user_id, tg_name, username, course, faculty, grp, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (user.id, name.strip(), user.username or "", course, faculty, grp, now_str()),
        )


def save_team(user, team_name, members):
    with db() as con:
        cur = con.execute(
            "INSERT INTO teams(user_id, username, team_name, created_at) VALUES(?,?,?,?)",
            (user.id, user.username or "", team_name, now_str()),
        )
        tid = cur.lastrowid
        for i, (name, phone) in enumerate(members, 1):
            con.execute(
                "INSERT INTO members(team_id, num, full_name, phone) VALUES(?,?,?,?)",
                (tid, i, name, phone),
            )
    return tid


def is_registered(user_id):
    with db() as con:
        return con.execute("SELECT 1 FROM teams WHERE user_id=?", (user_id,)).fetchone() is not None


def get_team_of(user_id):
    with db() as con:
        t = con.execute(
            "SELECT id, team_name, username, user_id, created_at FROM teams WHERE user_id=?", (user_id,)
        ).fetchone()
        if not t:
            return None, []
        ms = con.execute(
            "SELECT num, full_name, phone FROM members WHERE team_id=? ORDER BY num", (t[0],)
        ).fetchall()
    return t, ms


def get_teams():
    with db() as con:
        teams = con.execute(
            "SELECT id, team_name, username, user_id, created_at FROM teams ORDER BY id"
        ).fetchall()
        result = []
        for t in teams:
            ms = con.execute(
                "SELECT num, full_name, phone FROM members WHERE team_id=? ORDER BY num", (t[0],)
            ).fetchall()
            result.append((t, ms))
    return result


def team_text(t, ms):
    tid, name, username, user_id, created = t
    uname = f"@{username}" if username else f"ID: {user_id}"
    lines = [f"🏆 <b>#{tid} — {html.escape(name)}</b>", f"👤 {html.escape(uname)}  🕒 {created}"]
    for num, full_name, phone in ms:
        lines.append(f"{num}. {html.escape(full_name)} — <code>{html.escape(phone)}</code>")
    return "\n".join(lines)


def is_admin(uid):
    return uid in ADMIN_IDS


# ---------- OBUNA ----------
def subscribed(uid):
    try:
        st = bot.get_chat_member(CHANNEL, uid).status
        return st in ("member", "administrator", "creator")
    except Exception as e:
        print("Obuna tekshirishda xato (bot kanalda admin emasmi?):", e)
        return False


def need_sub(chat_id, uid):
    """True qaytarsa — foydalanuvchi obuna emas, xabar yuborildi."""
    if is_admin(uid) or subscribed(uid):
        return False
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📢 Kanalga obuna bo'lish", url=CHANNEL_URL),
        types.InlineKeyboardButton("✅ Tekshirish", callback_data="chk"),
    )
    bot.send_message(
        chat_id,
        "❗ Botdan foydalanish uchun avval kanalimizga obuna bo'ling, so'ng <b>Tekshirish</b> tugmasini bosing.",
        reply_markup=kb,
    )
    return True


# ---------- MENYU ----------
BTN_TEAM = "👥 Jamoani ro'yxatdan o'tkazish"
BTN_TEST = "📝 Test ishlash"
BTN_CAB = "🏠 Kabinet"


def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton(BTN_TEAM))
    kb.add(types.KeyboardButton(BTN_TEST), types.KeyboardButton(BTN_CAB))
    return kb


def phone_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("📱 Raqamni yuborish", request_contact=True))
    return kb


# ---------- START / PROFIL ----------
def begin(chat_id, user):
    """Obuna tekshirilgandan so'ng chaqiriladi."""
    uid = user.id
    if not get_user(uid):
        states[uid] = {"step": "course"}
        kb = types.InlineKeyboardMarkup(row_width=4)
        kb.add(*[types.InlineKeyboardButton(f"{i}-kurs", callback_data=f"c_{i}") for i in range(1, 5)])
        bot.send_message(chat_id, "Assalomu alaykum! 👋\nAvval o'zingiz haqingizda ma'lumot kiriting.\n\n"
                                  "🎓 <b>Nechanchi kursda o'qiysiz?</b>", reply_markup=kb)
        return
    bot.send_message(chat_id, "Asosiy menyu 👇", reply_markup=main_menu())


@bot.message_handler(commands=["start"])
def start(m):
    states.pop(m.from_user.id, None)
    if need_sub(m.chat.id, m.from_user.id):
        return
    begin(m.chat.id, m.from_user)


@bot.message_handler(commands=["cancel"])
def cancel(m):
    states.pop(m.from_user.id, None)
    bot.send_message(m.chat.id, "Bekor qilindi.", reply_markup=main_menu())


@bot.callback_query_handler(func=lambda c: c.data == "chk")
def chk_cb(c):
    if subscribed(c.from_user.id) or is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "✅ Obuna tasdiqlandi")
        try:
            bot.delete_message(c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        begin(c.message.chat.id, c.from_user)
    else:
        bot.answer_callback_query(c.id, "❌ Siz hali obuna bo'lmagansiz", show_alert=True)


@bot.callback_query_handler(func=lambda c: c.data.startswith("c_"))
def course_cb(c):
    st = states.get(c.from_user.id)
    if not st or st.get("step") != "course":
        bot.answer_callback_query(c.id)
        return
    st["course"] = int(c.data[2:])
    st["step"] = "faculty"
    bot.answer_callback_query(c.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(FACULTIES):
        kb.add(types.InlineKeyboardButton(f, callback_data=f"f_{i}"))
    bot.edit_message_text(f"🎓 Kurs: <b>{st['course']}-kurs</b>\n\n🏛 <b>Fakultetingizni tanlang:</b>",
                          c.message.chat.id, c.message.message_id, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("f_"))
def faculty_cb(c):
    st = states.get(c.from_user.id)
    if not st or st.get("step") != "faculty":
        bot.answer_callback_query(c.id)
        return
    st["faculty"] = FACULTIES[int(c.data[2:])]
    st["step"] = "group"
    bot.answer_callback_query(c.id)
    bot.edit_message_text(
        f"🎓 Kurs: <b>{st['course']}-kurs</b>\n🏛 Fakultet: <b>{html.escape(st['faculty'])}</b>\n\n"
        "👥 <b>Guruhingizni yozing</b>\n(masalan: 3-4a-2026)",
        c.message.chat.id, c.message.message_id)


# ---------- JAMOA RO'YXATI ----------
def ask_name(chat_id, st):
    n = len(st["members"]) + 1
    st["step"] = "name"
    bot.send_message(
        chat_id,
        f"👤 <b>{n}-a'zo</b> — ism va familiyasini yozing:\n(masalan: Ali Valiyev)",
        reply_markup=types.ReplyKeyboardRemove(),
    )


def finish(m, st):
    uid = m.from_user.id
    save_team(m.from_user, st["team"], st["members"])
    states.pop(uid, None)
    lines = [f"✅ <b>{html.escape(st['team'])}</b> jamoasi ro'yxatdan o'tdi!\n"]
    for i, (n, p) in enumerate(st["members"], 1):
        lines.append(f"{i}. {html.escape(n)} — {html.escape(p)}")
    bot.send_message(m.chat.id, "\n".join(lines), reply_markup=main_menu())
    for a in ADMIN_IDS:
        try:
            bot.send_message(a, "🆕 Yangi jamoa:\n\n" + "\n".join(lines))
        except Exception:
            pass


def valid_phone(text):
    digits = re.sub(r"\D", "", text)
    return text.strip(), 9 <= len(digits) <= 15


def add_member(m, st, phone):
    st["members"].append((st["name"], phone))
    if len(st["members"]) >= MEMBERS_COUNT:
        finish(m, st)
    else:
        ask_name(m.chat.id, st)


@bot.message_handler(content_types=["contact"])
def got_contact(m):
    st = states.get(m.from_user.id)
    if not st or st["step"] != "phone":
        return
    phone = m.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    add_member(m, st, phone)


# ---------- ADMIN: SAVOL QO'SHISH PARSERI ----------
OPT_RE = re.compile(r"^\s*([A-Da-d])\s*[\)\.\:]\s*(.+)$")
ANS_RE = re.compile(r"^\s*(?:javob|to'g'ri|togri|answer)\s*[:\-]\s*(.+)$", re.I)


ADDQ_HELP = (
    "❗ Format noto'g'ri yoki savol topilmadi.\n\n"
    "<b>Yopiq test</b> (variantli):\n<code>Toshkent qaysi davlat poytaxti?\nA) Qozog'iston\nB) O'zbekiston\n"
    "C) Qirg'iziston\nD) Tojikiston\nJavob: B</code>\n\n"
    "<b>Ochiq test</b> (ishtirokchi javobni yozadi):\n<code>Navoiy tavallud topgan yil?\nJavob: 1441</code>\n"
    "(bir necha to'g'ri javob bo'lsa <code>|</code> bilan ajrating: <code>Javob: 1441 | 1441-yil</code>)\n\n"
    "Savollar orasida bo'sh qator qoldiring."
)


def parse_questions(text):
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        q_lines, opts, ans = [], {}, None
        for line in block.strip().splitlines():
            am = ANS_RE.match(line)
            om = OPT_RE.match(line)
            if am:
                ans = am.group(1).strip()
            elif om:
                opts[om.group(1).upper()] = om.group(2).strip()
            elif not opts:
                q_lines.append(line.strip())
        q = re.sub(r"^\d+\s*[\.\)]\s*", "", " ".join(q_lines)).strip()
        if not q or not ans:
            continue
        letter = re.match(r"^([A-Da-d])\W*$", ans)
        if len(opts) == 4 and letter:  # yopiq test (variantli)
            out.append((q, opts["A"], opts["B"], opts["C"], opts["D"], letter.group(1).upper(), "c"))
        elif not opts:  # ochiq test (javob yoziladi)
            out.append((q, "", "", "", "", ans, "o"))
    return out


# ---------- FLOW (matnli bosqichlar) ----------
@bot.message_handler(func=lambda m: m.from_user.id in states, content_types=["text"])
def flow(m):
    if m.text.startswith("/"):
        return
    st = states[m.from_user.id]
    step = st["step"]

    if step == "group":
        grp = m.text.strip()
        if not (3 <= len(grp) <= 30):
            bot.send_message(m.chat.id, "❗ Guruh nomini to'g'ri yozing (masalan: 3-4a-2026).")
            return
        save_user(m.from_user, st["course"], st["faculty"], grp)
        states.pop(m.from_user.id, None)
        bot.send_message(m.chat.id, "✅ Ma'lumotlaringiz saqlandi.", reply_markup=main_menu())

    elif step == "team":
        st["team"] = m.text.strip()[:60]
        ask_name(m.chat.id, st)

    elif step == "name":
        parts = m.text.strip().split()
        if len(parts) < 2:
            bot.send_message(m.chat.id, "❗ Iltimos, ism va familiyani birga yozing (masalan: Ali Valiyev).")
            return
        st["name"] = " ".join(parts)[:80]
        st["step"] = "phone"
        bot.send_message(
            m.chat.id,
            f"📞 <b>{html.escape(st['name'])}</b> telefon raqamini yuboring.\n"
            "Tugmani bosing yoki qo'lda yozing (masalan: +998901234567):",
            reply_markup=phone_keyboard(),
        )

    elif step == "phone":
        phone, ok = valid_phone(m.text)
        if not ok:
            bot.send_message(m.chat.id, "❗ Telefon raqami noto'g'ri. Qayta yozing (masalan: +998901234567).")
            return
        add_member(m, st, phone)

    elif step == "addq" and is_admin(m.from_user.id):
        qs = parse_questions(m.text)
        if not qs:
            bot.send_message(m.chat.id, ADDQ_HELP)
            return
        with db() as con:
            con.executemany("INSERT INTO questions(text,a,b,c,d,correct,qtype) VALUES(?,?,?,?,?,?,?)", qs)
            total = con.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        nc = sum(1 for q in qs if q[6] == "c")
        bot.send_message(m.chat.id, f"✅ {len(qs)} ta savol qo'shildi (yopiq: {nc}, ochiq: {len(qs) - nc}). "
                                    f"Bazada jami: <b>{total}</b>.\n"
                                    "Yana yuborishingiz mumkin. Tugatish: /cancel")


# ---------- MENYU TUGMALARI ----------
@bot.message_handler(func=lambda m: m.text == BTN_TEAM)
def menu_team(m):
    if need_sub(m.chat.id, m.from_user.id):
        return
    if not get_user(m.from_user.id):
        begin(m.chat.id, m.from_user)
        return
    if is_registered(m.from_user.id):
        bot.send_message(m.chat.id, "✅ Siz allaqachon ro'yxatdan o'tgansiz. Ma'lumot: Kabinet.")
        return
    states[m.from_user.id] = {"step": "team", "members": []}
    bot.send_message(
        m.chat.id,
        f"Jamoada <b>{MEMBERS_COUNT} ta</b> odam bo'lishi kerak.\n\nAvval <b>jamoa nomini</b> yozing:",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@bot.message_handler(func=lambda m: m.text == BTN_CAB)
def menu_cabinet(m):
    if need_sub(m.chat.id, m.from_user.id):
        return
    u = get_user(m.from_user.id)
    if not u:
        begin(m.chat.id, m.from_user)
        return
    _, tg_name, username, course, faculty, grp = u
    lines = ["🏠 <b>Kabinet</b>\n",
             f"👤 {html.escape(tg_name or '')}" + (f" (@{html.escape(username)})" if username else ""),
             f"🎓 Kurs: <b>{course}-kurs</b>",
             f"🏛 Fakultet: <b>{html.escape(faculty)}</b>",
             f"👥 Guruh: <b>{html.escape(grp)}</b>\n"]
    t, ms = get_team_of(m.from_user.id)
    if t:
        lines.append(f"🏆 Jamoa: <b>{html.escape(t[1])}</b>  (#{t[0]})")
        for num, name, phone in ms:
            lines.append(f"{num}. {html.escape(name)} — <code>{html.escape(phone)}</code>")
    else:
        lines.append("🏆 Jamoa: ro'yxatdan o'tmagan")
    with db() as con:
        a = con.execute("SELECT finished_at, score, total FROM attempts WHERE user_id=?",
                        (m.from_user.id,)).fetchone()
    lines.append("")
    if not a:
        lines.append("📝 Test: topshirilmagan")
    elif a[0] is None:
        lines.append("📝 Test: jarayonda ⏳")
    else:
        lines.append(f"📝 Test natijasi: <b>{a[1]}/{a[2]}</b>")
    bot.send_message(m.chat.id, "\n".join(lines), reply_markup=main_menu())


# ---------- TEST ----------
def fmt_left(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def get_attempt(uid):
    with db() as con:
        r = con.execute(
            "SELECT started_at, finished_at, score, total, qdata, answers FROM attempts WHERE user_id=?", (uid,)
        ).fetchone()
    if not r:
        return None
    return {"started": r[0], "finished": r[1], "score": r[2], "total": r[3],
            "q": json.loads(r[4]), "ans": json.loads(r[5])}


def norm(x):
    """Javobni solishtirish uchun soddalashtiradi: o'/o‘/ò/ó/ô/ö..., g'/gʻ/ĝ/ğ..., sh/ş, ch/ç, katta-kichik harf
    va apostrof turlari farq qilmaydi."""
    x = unicodedata.normalize("NFC", str(x)).lower()
    x = x.replace("ş", "sh").replace("ç", "ch")
    x = "".join(ch for ch in unicodedata.normalize("NFD", x) if unicodedata.category(ch) != "Mn")  # ò->o, ĝ->g ...
    for ch in "ʻʼ`’‘´'ʹʽ\u02bb\u02bc\u2018\u2019\u0060\u00b4":
        x = x.replace(ch, "")  # o' -> o, g' -> g
    x = re.sub(r"[^\w ]+", " ", x)
    return " ".join(x.split())


def is_correct(q, a):
    if q.get("ty") == "o":
        return any(norm(a) == norm(x) for x in q["c"].split("|") if x.strip())
    return a == q["c"]


def grade(att):
    correct = sum(1 for i, q in enumerate(att["q"]) if i < len(att["ans"]) and is_correct(q, att["ans"][i]))
    answered = min(len(att["ans"]), len(att["q"]))
    return correct, answered - correct, len(att["q"]) - answered


def finish_attempt(uid):
    """Testni yakunlaydi. Allaqachon yakunlangan bo'lsa None qaytaradi."""
    att = get_attempt(uid)
    if not att or att["finished"] is not None:
        return None
    score = grade(att)[0]
    with db() as con:
        con.execute("UPDATE attempts SET finished_at=?, score=? WHERE user_id=?", (time.time(), score, uid))
    return score, len(att["q"])


def question_view(att, idx):
    q = att["q"][idx]
    left = att["started"] + TEST_MINUTES * 60 - time.time()
    deadline = att["started"] + TEST_MINUTES * 60
    head = (f"📝 <b>Savol {idx + 1}/{len(att['q'])}</b>\n"
            f"⏱ Qolgan vaqt: <b>{fmt_left(left)}</b> (tugash: <b>{clock(deadline)}</b>)\n\n"
            f"{html.escape(q['t'])}\n\n")
    if q.get("ty") == "o":
        return head + "✍️ <i>Javobingizni yozib yuboring.</i>", None
    text = (head + f"A) {html.escape(q['a'])}\nB) {html.escape(q['b'])}\n"
            f"C) {html.escape(q['c_'])}\nD) {html.escape(q['d'])}")
    kb = types.InlineKeyboardMarkup(row_width=4)
    kb.add(*[types.InlineKeyboardButton(o, callback_data=f"t_{idx}_{o}") for o in "ABCD"])
    return text, kb


@bot.message_handler(func=lambda m: m.text == BTN_TEST)
def menu_test(m):
    uid = m.from_user.id
    if need_sub(m.chat.id, uid):
        return
    if not get_user(uid):
        begin(m.chat.id, m.from_user)
        return
    if get_setting("test_open") != "1" and not get_attempt(uid):
        bot.send_message(m.chat.id, "🔒 Test hozircha yopiq. Admin ochganida xabar beriladi.")
        return
    att = get_attempt(uid)
    if att and att["finished"] is not None:
        bot.send_message(m.chat.id, f"✅ Siz testni topshirgansiz. Natija: <b>{att['score']}/{att['total']}</b>")
        return
    if att:  # davom etayotgan
        if time.time() > att["started"] + TEST_MINUTES * 60:
            r = finish_attempt(uid)
            if r:
                bot.send_message(m.chat.id, f"⏰ Vaqt tugadi. Natija: <b>{r[0]}/{r[1]}</b>")
            return
        text, kb = question_view(att, len(att["ans"]))
        bot.send_message(m.chat.id, text, reply_markup=kb)
        return
    with db() as con:
        n = con.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    if n == 0:
        bot.send_message(m.chat.id, "Hozircha savollar kiritilmagan.")
        return
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("▶️ Boshlash", callback_data="t_start"))
    bot.send_message(
        m.chat.id,
        f"📝 <b>Test</b>\n\nSavollar: <b>{min(TEST_QUESTIONS, n)} ta</b> (tasodifiy)\n"
        f"Vaqt: <b>{TEST_MINUTES} daqiqa</b>\nUrinish: <b>1 marta</b>\n\n"
        "Tayyor bo'lsangiz «Boshlash» tugmasini bosing — vaqt shu zahoti ketadi.",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("t_"))
def test_cb(c):
    uid = c.from_user.id
    chat, mid = c.message.chat.id, c.message.message_id

    if c.data == "t_start":
        if need_sub(chat, uid):
            bot.answer_callback_query(c.id)
            return
        if get_setting("test_open") != "1" or get_attempt(uid):
            bot.answer_callback_query(c.id, "Test mavjud emas yoki allaqachon boshlangan", show_alert=True)
            return
        with db() as con:
            rows = con.execute("SELECT text,a,b,c,d,correct,COALESCE(qtype,'c') FROM questions").fetchall()
        if not rows:
            bot.answer_callback_query(c.id, "Savollar yo'q", show_alert=True)
            return
        pick = random.sample(rows, min(TEST_QUESTIONS, len(rows)))
        qdata = [{"t": r[0], "a": r[1], "b": r[2], "c_": r[3], "d": r[4], "c": r[5], "ty": r[6]} for r in pick]
        with db() as con:
            con.execute("INSERT INTO attempts(user_id, started_at, total, qdata, answers) VALUES(?,?,?,?,?)",
                        (uid, time.time(), len(qdata), json.dumps(qdata, ensure_ascii=False), "[]"))
        bot.answer_callback_query(c.id)
        att = get_attempt(uid)
        text, kb = question_view(att, 0)
        bot.edit_message_text(text, chat, mid, reply_markup=kb)
        return

    # javob: t_{idx}_{opt}
    try:
        _, idx, opt = c.data.split("_")
        idx = int(idx)
    except ValueError:
        bot.answer_callback_query(c.id)
        return
    att = get_attempt(uid)
    if not att or att["finished"] is not None:
        bot.answer_callback_query(c.id, "Test yakunlangan", show_alert=True)
        return
    if idx != len(att["ans"]) or idx >= len(att["q"]) or att["q"][idx].get("ty") == "o":
        bot.answer_callback_query(c.id)
        return
    bot.answer_callback_query(c.id)
    advance(uid, chat, mid, opt, c.from_user.first_name or "")


def advance(uid, chat, mid, value, first_name=""):
    """Javobni qabul qiladi. mid bor bo'lsa xabarni tahrirlaydi, bo'lmasa yangi xabar yuboradi."""
    att = get_attempt(uid)
    if not att or att["finished"] is not None:
        return False

    def say(text, kb=None):
        if mid:
            bot.edit_message_text(text, chat, mid, reply_markup=kb)
        else:
            bot.send_message(chat, text, reply_markup=kb)

    if time.time() > att["started"] + TEST_MINUTES * 60:
        r = finish_attempt(uid)
        if r:
            say(f"⏰ Vaqt tugadi.\nNatija: <b>{r[0]}/{r[1]}</b>")
        return True
    att["ans"].append(value)
    with db() as con:
        con.execute("UPDATE attempts SET answers=? WHERE user_id=?", (json.dumps(att["ans"], ensure_ascii=False), uid))
    if len(att["ans"]) >= len(att["q"]):
        r = finish_attempt(uid)
        say(f"🏁 Test yakunlandi!\nNatija: <b>{r[0]}/{r[1]}</b>")
        for a in ADMIN_IDS:
            try:
                bot.send_message(a, f"📝 {html.escape(first_name)} testni tugatdi: {r[0]}/{r[1]}")
            except Exception:
                pass
    else:
        text, kb = question_view(att, len(att["ans"]))
        say(text, kb)
    return True


def open_pending(uid):
    att = get_attempt(uid)
    if not att or att["finished"] is not None:
        return False
    i = len(att["ans"])
    return i < len(att["q"]) and att["q"][i].get("ty") == "o"


@bot.message_handler(
    func=lambda m: bool(m.text) and not m.text.startswith("/") and m.text not in (BTN_TEAM, BTN_TEST, BTN_CAB)
    and open_pending(m.from_user.id),
    content_types=["text"])
def open_answer(m):
    advance(m.from_user.id, m.chat.id, None, m.text.strip()[:200], m.from_user.first_name or "")


def watcher():
    """Vaqti tugagan testlarni avtomatik yakunlaydi."""
    while True:
        try:
            limit = time.time() - TEST_MINUTES * 60
            with db() as con:
                rows = con.execute(
                    "SELECT user_id FROM attempts WHERE finished_at IS NULL AND started_at<?", (limit,)
                ).fetchall()
            for (uid,) in rows:
                r = finish_attempt(uid)
                if r:
                    try:
                        bot.send_message(uid, f"⏰ Vaqt tugadi. Natija: <b>{r[0]}/{r[1]}</b>")
                    except Exception:
                        pass
        except Exception as e:
            print("watcher xato:", e)
        time.sleep(20)


# ---------- ADMIN PANEL ----------
def admin_menu():
    is_open = get_setting("test_open") == "1"
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📋 Barcha jamoalar", callback_data="a_list"),
        types.InlineKeyboardButton("📊 Statistika", callback_data="a_stat"),
        types.InlineKeyboardButton("📥 CSV (Excel) yuklab olish", callback_data="a_csv"),
        types.InlineKeyboardButton("➕ Yopiq savol (A/B/C/D variantli)", callback_data="a_addq"),
        types.InlineKeyboardButton("➕ Ochiq savol (yozma javob)", callback_data="a_addq_o"),
        types.InlineKeyboardButton("🗂 Savollar soni", callback_data="a_qcount"),
        types.InlineKeyboardButton("🏅 Natijalar (xabar)", callback_data="a_res"),
        types.InlineKeyboardButton("📥 Natijalar (Excel, ball bo'yicha)", callback_data="a_xlsx"),
        types.InlineKeyboardButton("🔒 Testni yopish" if is_open else "🔓 Testni ochish", callback_data="a_toggle"),
        types.InlineKeyboardButton("🗑 Barcha savollarni o'chirish", callback_data="a_clearq"),
    )
    return kb


def admin_text():
    st = "🔓 OCHIQ" if get_setting("test_open") == "1" else "🔒 YOPIQ"
    return ("🛠 <b>Admin panel</b>\nTest holati: <b>" + st + "</b>\n\n"
            "Jamoani o'chirish: <code>/delete ID</code>\n"
            "Testni qayta berish: <code>/resettest USER_ID</code>")


@bot.message_handler(commands=["admin"])
def admin(m):
    if not is_admin(m.from_user.id):
        bot.send_message(m.chat.id, "⛔ Bu bo'lim faqat admin uchun.")
        return
    bot.send_message(m.chat.id, admin_text(), reply_markup=admin_menu())


@bot.message_handler(commands=["delete"])
def delete_team(m):
    if not is_admin(m.from_user.id):
        return
    parts = m.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.send_message(m.chat.id, "Foydalanish: <code>/delete 3</code>")
        return
    tid = int(parts[1])
    with db() as con:
        con.execute("DELETE FROM members WHERE team_id=?", (tid,))
        n = con.execute("DELETE FROM teams WHERE id=?", (tid,)).rowcount
    bot.send_message(m.chat.id, "🗑 O'chirildi." if n else "Bunday jamoa topilmadi.")


@bot.message_handler(commands=["resettest"])
def reset_test(m):
    if not is_admin(m.from_user.id):
        return
    parts = m.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.send_message(m.chat.id, "Foydalanish: <code>/resettest 123456789</code>")
        return
    with db() as con:
        n = con.execute("DELETE FROM attempts WHERE user_id=?", (int(parts[1]),)).rowcount
    bot.send_message(m.chat.id, "♻️ Urinish tiklandi." if n else "Bunday urinish topilmadi.")


def build_xlsx(path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    with db() as con:
        rows = con.execute(
            "SELECT a.user_id, a.started_at, a.finished_at, u.tg_name, u.username, u.course, u.faculty, u.grp "
            "FROM attempts a LEFT JOIN users u ON u.user_id=a.user_id WHERE a.finished_at IS NOT NULL"
        ).fetchall()
    data = []
    for r in rows:
        att = get_attempt(r[0])
        correct, wrong, unans = grade(att)
        data.append((correct, r[2] - r[1], wrong, unans, len(att["q"]), r))
    data.sort(key=lambda x: (-x[0], x[1]))  # eng ko'p ball birinchi, tenglikda tezroq tugatgan

    wb = Workbook()
    ws = wb.active
    ws.title = "Test natijalari"
    head = ["№", "Jamoa nomi", "Ishtirokchi (Telegram)", "Username", "Kurs", "Fakultet", "Guruh",
            "Ball (to'g'ri)", "Noto'g'ri", "Javobsiz", "Jami savol", "Foiz", "Sarflangan vaqt", "Tugatgan vaqt"]
    for i in range(1, MEMBERS_COUNT + 1):
        head += [f"{i}-a'zo ism-familiya", f"{i}-a'zo telefon"]
    ws.append(head)
    for n, (correct, spent, wrong, unans, total, r) in enumerate(data, 1):
        t, ms = get_team_of(r[0])
        row = [n, t[1] if t else "—", r[3] or "", f"@{r[4]}" if r[4] else "", r[5], r[6] or "", r[7] or "",
               correct, wrong, unans, total, f"{round(correct * 100 / total) if total else 0}%",
               fmt_left(spent), clock(r[2])]
        for k in range(MEMBERS_COUNT):
            row += [ms[k][1], ms[k][2]] if k < len(ms) else ["", ""]
        ws.append(row)
    fill = PatternFill("solid", start_color="DDEBF7")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = fill
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for col in range(1, len(head) + 1):
        width = max(len(str(c.value or "")) for c in ws[get_column_letter(col)]) + 2
        ws.column_dimensions[get_column_letter(col)].width = min(max(width, 6), 34)
    ws.freeze_panes = "C2"
    wb.save(path)
    return len(data)


def send_chunks(chat, blocks):
    chunk = ""
    for block in blocks:
        if len(chunk) + len(block) > 3800:
            bot.send_message(chat, chunk)
            chunk = ""
        chunk += block
    if chunk:
        bot.send_message(chat, chunk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("a_"))
def admin_cb(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Ruxsat yo'q")
        return
    bot.answer_callback_query(c.id)
    chat = c.message.chat.id
    d = c.data

    if d == "a_stat":
        teams = get_teams()
        with db() as con:
            nu = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            na = con.execute("SELECT COUNT(*) FROM attempts WHERE finished_at IS NOT NULL").fetchone()[0]
        bot.send_message(
            chat,
            f"📊 Jamoalar: <b>{len(teams)}</b>\n👥 Ishtirokchilar: <b>{sum(len(ms) for _, ms in teams)}</b>\n"
            f"🙋 Botdan foydalanuvchilar: <b>{nu}</b>\n📝 Testni tugatganlar: <b>{na}</b>",
        )

    elif d == "a_list":
        teams = get_teams()
        if not teams:
            bot.send_message(chat, "Hozircha ro'yxatdan o'tgan jamoa yo'q.")
            return
        send_chunks(chat, [team_text(t, ms) + "\n\n" for t, ms in teams])

    elif d == "a_csv":
        teams = get_teams()
        if not teams:
            bot.send_message(chat, "Ma'lumot yo'q.")
            return
        path = os.path.join(BASE_DIR, "jamoalar.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Jamoa ID", "Jamoa nomi", "Username", "Sana", "Kurs", "Fakultet", "Guruh",
                        "Test natijasi", "№", "Ism familiya", "Telefon"])
            for t, ms in teams:
                u = get_user(t[3])
                att = get_attempt(t[3])
                score = f"{att['score']}/{att['total']}" if att and att["finished"] is not None else ""
                for num, name, phone in ms:
                    w.writerow([t[0], t[1], t[2], t[4], u[3] if u else "", u[4] if u else "",
                                u[5] if u else "", score, num, name, phone])
        with open(path, "rb") as f:
            bot.send_document(chat, f, caption="📥 Jamoalar ro'yxati")

    elif d in ("a_addq", "a_addq_o"):
        states[c.from_user.id] = {"step": "addq"}
        if d == "a_addq":
            ex = ("<b>Yopiq savol</b> (variantli). Bir nechtasini bo'sh qator bilan ajrating:\n\n"
                  "<code>Toshkent qaysi davlat poytaxti?\nA) Qozog'iston\nB) O'zbekiston\nC) Qirg'iziston\n"
                  "D) Tojikiston\nJavob: B\n\n(keyingi savol...)</code>")
        else:
            ex = ("<b>Ochiq savol</b> (ishtirokchi javobni o'zi yozadi, bot o'zi tekshiradi). "
                  "Bir nechtasini bo'sh qator bilan ajrating:\n\n"
                  "<code>O'zbekistonning poytaxti qaysi shahar?\nJavob: Toshkent\n\n"
                  "Eng katta viloyat?\nJavob: Qoraqalpog'iston | Qoraqalpogiston</code>\n\n"
                  "Bir nechta to'g'ri javob bo'lsa <code>|</code> bilan ajrating. Katta-kichik harf, "
                  "o'/ó/ö, g'/ğ/gʻ, sh/ş, ch/ç farq qilmaydi.")
        bot.send_message(chat, "➕ " + ex + "\n\nTugatish: /cancel")

    elif d == "a_qcount":
        with db() as con:
            n = con.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        bot.send_message(chat, f"🗂 Bazadagi savollar: <b>{n}</b> (har kimga {TEST_QUESTIONS} tadan tasodifiy beriladi)")

    elif d == "a_toggle":
        set_setting("test_open", "0" if get_setting("test_open") == "1" else "1")
        bot.edit_message_text(admin_text(), chat, c.message.message_id, reply_markup=admin_menu())

    elif d == "a_clearq":
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Ha, o'chirish", callback_data="a_clearq_yes"),
               types.InlineKeyboardButton("Yo'q", callback_data="a_no"))
        bot.send_message(chat, "❓ Barcha savollar o'chirilsinmi?", reply_markup=kb)

    elif d == "a_clearq_yes":
        with db() as con:
            con.execute("DELETE FROM questions")
        bot.edit_message_text("🗑 Barcha savollar o'chirildi.", chat, c.message.message_id)

    elif d == "a_no":
        bot.edit_message_text("Bekor qilindi.", chat, c.message.message_id)

    elif d == "a_xlsx":
        path = os.path.join(BASE_DIR, "test_natijalari.xlsx")
        try:
            n = build_xlsx(path)
        except ImportError:
            bot.send_message(chat, "openpyxl o'rnatilmagan. Konsolda: <code>pip3.10 install --user openpyxl</code>")
            return
        if n == 0:
            bot.send_message(chat, "Hozircha testni tugatganlar yo'q.")
            return
        with open(path, "rb") as f:
            bot.send_document(chat, f, caption=f"📥 Testni tugatganlar: {n} ta (ball bo'yicha kamayish tartibida)")

    elif d == "a_res":
        with db() as con:
            rows = con.execute(
                "SELECT a.user_id, a.score, a.total, a.started_at, a.finished_at, u.tg_name, u.course, u.faculty, "
                "u.grp, t.team_name FROM attempts a LEFT JOIN users u ON u.user_id=a.user_id "
                "LEFT JOIN teams t ON t.user_id=a.user_id WHERE a.finished_at IS NOT NULL "
                "ORDER BY a.score DESC, (a.finished_at-a.started_at) ASC"
            ).fetchall()
        if not rows:
            bot.send_message(chat, "Hozircha natijalar yo'q.")
            return
        blocks = [f"🏅 <b>Testni tugatganlar: {len(rows)} ta</b>\n\n"]
        for i, r in enumerate(rows, 1):
            mins = fmt_left(r[4] - r[3])
            blocks.append(
                f"{i}. <b>{html.escape(r[5] or str(r[0]))}</b> — <b>{r[1]}/{r[2]}</b> ({mins})\n"
                f"   🏆 {html.escape(r[9] or '—')} | {r[6]}-kurs | {html.escape(r[7] or '')} | "
                f"{html.escape(r[8] or '')} | <code>{r[0]}</code>\n")
        send_chunks(chat, blocks)


if __name__ == "__main__":
    init_db()
    threading.Thread(target=watcher, daemon=True).start()
    print("Bot ishga tushdi...")
    bot.infinity_polling(timeout=20, long_polling_timeout=20)
