from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, List

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    LabeledPrice,
    InputSticker,
    Message,
)
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler,
    PreCheckoutQueryHandler,
)

from config import (
    BOT_TOKEN,
    OWNER_ID,
    DB_PATH,
    BACKUP_DIR,
    FREE_MAX_STICKERS,
    FREE_MAX_EMOJIS,
    PAID_MAX_ITEMS,
    FREE_PACK_NAME_MIN_LEN,
    FREE_PACK_NAME_MAX_LEN,
    PAID_PACK_NAME_MIN_LEN,
    PAID_PACK_NAME_MAX_LEN,
    PRICE_BPACK_EMOJI_XTR,
    PRICE_BPACK_STICKER_XTR,
    PRICE_APACK_XTR,
    PRICE_DUPLICATE_XTR,
    ONLY_PRIVATE_CHATS,
    SETTING_OWNER_ITEMS_FOR_SALE,
    APP_NAME,
)
from emoji import list_available_fonts, render_text_emoji, pil_image_bytes_to_input_sticker
from sticker import normalize_pack_name, create_pack, add_item_to_pack, remove_item_from_pack, parse_pack_link, duplicate_pack

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== Persistence ==================

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        is_paid INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        free_pack_uses INTEGER DEFAULT 0,
        paid_pack_uses INTEGER DEFAULT 0,
        adaptive_pack_name TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS packs (
        pack_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        title TEXT NOT NULL,
        type TEXT NOT NULL,
        is_paid_pack INTEGER DEFAULT 0,
        pack_link TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS pack_items (
        item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        pack_id INTEGER NOT NULL,
        file_id TEXT NOT NULL,
        emoji TEXT,
        type TEXT NOT NULL,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """,
]


def ensure_dirs():
    Path(os.path.dirname(DB_PATH) or ".").mkdir(parents=True, exist_ok=True)
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)


def init_db():
    ensure_dirs()
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        for stmt in SCHEMA:
            cur.execute(stmt)
        # default settings
        cur.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
            (SETTING_OWNER_ITEMS_FOR_SALE, json.dumps(True)),
        )
        con.commit()


@contextmanager
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    try:
        yield con
    finally:
        con.close()


# ================== Helpers ==================

@dataclass
class User:
    user_id: int
    is_paid: bool
    is_admin: bool
    free_pack_uses: int
    paid_pack_uses: int
    adaptive_pack_name: Optional[str]


def get_or_create_user(user_id: int) -> User:
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT user_id, is_paid, is_admin, free_pack_uses, paid_pack_uses, adaptive_pack_name FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            cur.execute("INSERT INTO users(user_id) VALUES(?)", (user_id,))
            con.commit()
            return User(user_id, False, False, 0, 0, None)
        return User(
            user_id=row[0],
            is_paid=bool(row[1]),
            is_admin=bool(row[2]),
            free_pack_uses=row[3],
            paid_pack_uses=row[4],
            adaptive_pack_name=row[5],
        )


def set_user_field(user_id: int, field: str, value):
    with db() as con:
        cur = con.cursor()
        cur.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, user_id))
        con.commit()


def inc_user_field(user_id: int, field: str, delta: int = 1):
    with db() as con:
        cur = con.cursor()
        cur.execute(f"UPDATE users SET {field} = COALESCE({field},0) + ? WHERE user_id=?", (delta, user_id))
        con.commit()


def get_setting(key: str):
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cur.fetchone()
        return json.loads(row[0]) if row and row[0] else None


def set_setting(key: str, value):
    with db() as con:
        cur = con.cursor()
        cur.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        con.commit()


def insert_pack(user_id: int, name: str, title: str, type_: str, is_paid_pack: bool, link: str) -> int:
    with db() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO packs(user_id, name, title, type, is_paid_pack, pack_link) VALUES(?,?,?,?,?,?)",
            (user_id, name, title, type_, int(is_paid_pack), link),
        )
        con.commit()
        return cur.lastrowid


def find_user_packs(user_id: int, type_: Optional[str] = None) -> List[Tuple[int, str, str, str, int, str]]:
    with db() as con:
        cur = con.cursor()
        if type_:
            cur.execute("SELECT pack_id, name, title, type, is_paid_pack, pack_link FROM packs WHERE user_id=? AND type=? ORDER BY pack_id DESC", (user_id, type_))
        else:
            cur.execute("SELECT pack_id, name, title, type, is_paid_pack, pack_link FROM packs WHERE user_id=? ORDER BY pack_id DESC", (user_id,))
        return list(cur.fetchall())


def get_pack_by_id(pack_id: int) -> Optional[Tuple[int, int, str, str, str, int, str]]:
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT pack_id, user_id, name, title, type, is_paid_pack, pack_link FROM packs WHERE pack_id=?", (pack_id,))
        return cur.fetchone()


def insert_pack_item(pack_id: int, file_id: str, emoji: Optional[str], type_: str):
    with db() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO pack_items(pack_id, file_id, emoji, type) VALUES(?,?,?,?)",
            (pack_id, file_id, emoji, type_),
        )
        con.commit()


def count_pack_items(pack_id: int) -> int:
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM pack_items WHERE pack_id=?", (pack_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0


def remove_pack_item_if_exists(pack_id: int, file_id: str) -> bool:
    with db() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM pack_items WHERE pack_id=? AND file_id=?", (pack_id, file_id))
        deleted = cur.rowcount
        con.commit()
        return deleted > 0


def user_item_counts(user_id: int, type_: str) -> int:
    with db() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM pack_items WHERE pack_id IN (SELECT pack_id FROM packs WHERE user_id=? AND type=?)",
            (user_id, type_),
        )
        row = cur.fetchone()
        return row[0] if row else 0


# ================== Conversations State ==================

(CREATE_WAIT_NAME, CREATE_WAIT_FIRST_ITEM,
 REM_WAIT_PACK_SELECT, REM_WAIT_ITEM,
 DELETE_WAIT_TYPE_SELECT, DELETE_WAIT_PACK_SELECT,
 ACR_WAIT_INPUT, ACR_WAIT_FONT, ACR_WAIT_BG,
 DUP_WAIT_LINK,
) = range(10)

# Ephemeral state store in memory for flows
pending_create: Dict[int, Dict] = {}
pending_remove: Dict[int, Dict] = {}
pending_delete: Dict[int, Dict] = {}
pending_acr: Dict[int, Dict] = {}
pending_duplicate: Dict[int, Dict] = {}


# ================== Validators ==================

def is_private(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.type == ChatType.PRIVATE


async def ensure_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if ONLY_PRIVATE_CHATS and not is_private(update):
        await update.effective_message.reply_text("Please use this bot in a private chat.")
        return False
    return True


# ================== Command Handlers ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_private(update, context):
        return
    get_or_create_user(update.effective_user.id)
    await update.message.reply_text(
        "Welcome! Create powerful, playful emoji and sticker packs — remix, adapt, and share. ✨\nType /help to see all features."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_private(update, context):
        return
    msg = (
        "Commands:\n"
        "/create <emoji|sticker> – Create a pack.\n"
        "/bpack <emoji|sticker> – Buy paid pack creation.\n"
        "/apack – Buy adaptive emoji pack (owner-only now).\n"
        "/acr – Create adaptive emoji from text/photo/emoji.\n"
        "/duplicate <pack_link> – Duplicate a pack (paid).\n"
        "/rem – Remove an item from a pack.\n"
        "/delete <emoji|sticker> – Delete by sending item.\n"
        "/import – Import backup JSON.\n"
        "/export – Export your data JSON.\n"
        "/admin <user_id> – Owner: give 20 free creates + free paid features.\n"
        "/broadcast <message> – Owner: broadcast to all users.\n"
        "/set <on|off> – Owner: toggle sale of owner items.\n"
        "Limits: Free: 1 emoji pack (40), 1 sticker pack (30). Paid: up to 120 items, long names."
    )
    await update.message.reply_text(msg)


# 3. Create packs
async def create_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_private(update, context):
        return ConversationHandler.END

    user = get_or_create_user(update.effective_user.id)
    args = context.args
    if not args or args[0].lower() not in ("emoji", "sticker"):
        await update.message.reply_text("Usage: /create <emoji|sticker>")
        return ConversationHandler.END

    pack_type = args[0].lower()
    # Check quotas for free users unless paid
    user_packs = find_user_packs(user.user_id, pack_type)
    if not user.is_paid:
        if pack_type == "emoji" and any(user_packs):
            await update.message.reply_text("Free users can have only 1 emoji pack. Buy /bpack emoji for more.")
            return ConversationHandler.END
        if pack_type == "sticker" and any(user_packs):
            await update.message.reply_text("Free users can have only 1 sticker pack. Buy /bpack sticker for more.")
            return ConversationHandler.END

    pending_create[user.user_id] = {
        "type": pack_type,
        "is_paid": user.is_paid,
    }
    await update.message.reply_text(
        "Send a name for your pack.\n"
        f"Free: {FREE_PACK_NAME_MIN_LEN}-{FREE_PACK_NAME_MAX_LEN} chars; Paid: {PAID_PACK_NAME_MIN_LEN}-{PAID_PACK_NAME_MAX_LEN}."
    )
    return CREATE_WAIT_NAME


async def create_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in pending_create:
        return ConversationHandler.END

    name = (update.message.text or "").strip()
    meta = pending_create[user_id]
    is_paid = meta["is_paid"]
    if is_paid:
        if not (PAID_PACK_NAME_MIN_LEN <= len(name) <= PAID_PACK_NAME_MAX_LEN):
            await update.message.reply_text("Name length invalid for paid pack; please resend.")
            return CREATE_WAIT_NAME
        title = name
        base_slug = normalize_pack_name(name)
    else:
        if not (FREE_PACK_NAME_MIN_LEN <= len(name) <= FREE_PACK_NAME_MAX_LEN):
            await update.message.reply_text("Name length invalid for free pack; please resend.")
            return CREATE_WAIT_NAME
        # Free must include bot username suffix
        bot_username = (await context.bot.get_me()).username
        title = name
        base_slug = normalize_pack_name(f"{name}_by_{bot_username}")

    meta["title"] = title
    meta["slug"] = base_slug
    await update.message.reply_text("Now send a single emoji or sticker as the first item.")
    return CREATE_WAIT_FIRST_ITEM


async def create_receive_first_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in pending_create:
        return ConversationHandler.END
    meta = pending_create[user_id]

    pack_type = meta["type"]

    sticker: Optional[InputSticker] = None
    if update.message.sticker:
        s = update.message.sticker
        if pack_type == "emoji" and not s.is_custom_emoji:
            await update.message.reply_text("Please send a custom emoji (not a sticker) for an emoji pack.")
            return CREATE_WAIT_FIRST_ITEM
        if pack_type == "sticker" and s.is_custom_emoji:
            await update.message.reply_text("Please send a sticker (not a custom emoji) for a sticker pack.")
            return CREATE_WAIT_FIRST_ITEM
        # Build InputSticker
        emojis = s.emoji or "😀"
        sticker = InputSticker(sticker=s.file_id, format=s.format, emoji_list=[emojis] if isinstance(emojis, str) else emojis)
    elif update.message.text and pack_type == "emoji":
        # Render text to image for emoji pack first item
        png = render_text_emoji(update.message.text[:4], font_path=None)
        sticker = pil_image_bytes_to_input_sticker(png, emojis=["😀"]) 
    elif update.message.photo and pack_type == "sticker":
        file = await update.message.photo[-1].get_file()
        sticker = InputSticker(sticker=file.file_id, format="static", emoji_list=["😀"])  # best-effort
    else:
        await update.message.reply_text("Please send a single emoji or sticker.")
        return CREATE_WAIT_FIRST_ITEM

    # Create pack via API
    sticker_type = "custom_emoji" if pack_type == "emoji" else "regular"
    slug = meta["slug"]
    title = meta["title"]

    try:
        await create_pack(context.bot, user_id, slug, title, sticker, sticker_type)
    except Exception as e:
        logger.exception("create_pack failed")
        await update.message.reply_text(f"Failed to create pack: {e}")
        return ConversationHandler.END

    # Store DB
    link = f"https://t.me/{'addemoji' if pack_type == 'emoji' else 'addstickers'}/{slug}"
    pack_id = insert_pack(user_id, slug, title, pack_type, meta.get("is_paid", False), link)

    await update.message.reply_text(f"Pack created! {link}")

    # Save first item to DB with best-effort file_id (may be bytes for uploads)
    try:
        file_id = None
        if update.message.sticker:
            file_id = update.message.sticker.file_id
        elif update.message.photo:
            file_id = update.message.photo[-1].file_id
        else:
            file_id = "GENERATED"
        insert_pack_item(pack_id, file_id, None, pack_type)
    except Exception:
        pass

    pending_create.pop(user_id, None)
    return ConversationHandler.END


# 4. Adaptive Emojis
async def apack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Adaptive packs are currently owner-only.")
        return
    user = get_or_create_user(update.effective_user.id)
    if user.adaptive_pack_name:
        await update.message.reply_text("Adaptive pack already exists.")
        return
    # charge stars via Stars invoice (provider token implicit for XTR)
    payload = f"apack:{user.user_id}:{int(time.time())}"
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title="Adaptive Pack",
        description="Create an adaptive emoji pack",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice("Adaptive Pack", PRICE_APACK_XTR)],
        need_name=False,
        need_email=False,
        is_flexible=False,
    )


async def acr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Adaptive creation is currently owner-only.")
        return ConversationHandler.END
    pending_acr[update.effective_user.id] = {}
    await update.message.reply_text("Send text for the emoji (multi-line supported), or send a photo/emoji.")
    return ACR_WAIT_INPUT


async def acr_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = pending_acr.get(uid, {})

    if update.message.text and not update.message.text.startswith("/"):
        state["mode"] = "text"
        state["text"] = update.message.text[:64]
        fonts = list_available_fonts()
        kb = [[InlineKeyboardButton(text=os.path.basename(fp), callback_data=f"acr_font|{i}")] for i, fp in enumerate(fonts[:10])]
        await update.message.reply_text("Choose a font:", reply_markup=InlineKeyboardMarkup(kb))
        return ACR_WAIT_FONT

    if update.message.photo:
        state["mode"] = "photo"
        file = await update.message.photo[-1].get_file()
        state["photo_file_id"] = file.file_id
        # background selection may still apply for consistency
        kb = [[InlineKeyboardButton("No background", callback_data="acr_bg|none")],
              [InlineKeyboardButton("50% transparent", callback_data="acr_bg|translucent")],
              [InlineKeyboardButton("Background only", callback_data="acr_bg|background_only")]]
        await update.message.reply_text("Choose background:", reply_markup=InlineKeyboardMarkup(kb))
        return ACR_WAIT_BG

    if update.message.sticker and update.message.sticker.is_custom_emoji:
        state["mode"] = "emoji"
        state["emoji_file_id"] = update.message.sticker.file_id
        kb = [[InlineKeyboardButton("No background", callback_data="acr_bg|none")],
              [InlineKeyboardButton("50% transparent", callback_data="acr_bg|translucent")],
              [InlineKeyboardButton("Background only", callback_data="acr_bg|background_only")]]
        await update.message.reply_text("Choose background:", reply_markup=InlineKeyboardMarkup(kb))
        return ACR_WAIT_BG

    await update.message.reply_text("Please send text, photo, or emoji.")
    return ACR_WAIT_INPUT


async def acr_font_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    state = pending_acr.get(uid, {})
    if not state or state.get("mode") != "text":
        await q.edit_message_text("State expired. Send /acr again.")
        return ConversationHandler.END
    _, idx_s = q.data.split("|", 1)
    state["font_idx"] = int(idx_s)
    kb = [[InlineKeyboardButton("No background", callback_data="acr_bg|none")],
          [InlineKeyboardButton("50% transparent", callback_data="acr_bg|translucent")],
          [InlineKeyboardButton("Background only", callback_data="acr_bg|background_only")]]
    await q.edit_message_text("Choose background:", reply_markup=InlineKeyboardMarkup(kb))
    return ACR_WAIT_BG


async def acr_bg_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    state = pending_acr.get(uid, {})
    if not state:
        await q.edit_message_text("State expired. Send /acr again.")
        return ConversationHandler.END
    _, mode = q.data.split("|", 1)
    state["bg"] = mode

    # Create result as static PNG
    if state.get("mode") == "text":
        fonts = list_available_fonts()
        font_path = fonts[state.get("font_idx", 0)] if fonts else None
        png = render_text_emoji(state["text"], font_path=font_path, background_mode=state.get("bg", "none"))
        input_sticker = pil_image_bytes_to_input_sticker(png, ["😀"]) 
    elif state.get("mode") == "photo":
        input_sticker = InputSticker(sticker=state["photo_file_id"], format="static", emoji_list=["😀"]) 
    elif state.get("mode") == "emoji":
        input_sticker = InputSticker(sticker=state["emoji_file_id"], format="static", emoji_list=["😀"]) 
    else:
        await q.edit_message_text("Invalid mode; try /acr again.")
        return ConversationHandler.END

    # Ensure adaptive pack exists for user
    user = get_or_create_user(uid)
    slug = user.adaptive_pack_name
    if not slug:
        bot_username = (await context.bot.get_me()).username
        slug = normalize_pack_name(f"adaptive_{uid}_by_{bot_username}")
        try:
            await create_pack(context.bot, uid, slug, f"Adaptive {uid}", input_sticker, "custom_emoji")
        except Exception as e:
            await q.edit_message_text(f"Failed to create adaptive pack: {e}")
            return ConversationHandler.END
        set_user_field(uid, "adaptive_pack_name", slug)
        link = f"https://t.me/addemoji/{slug}"
        await context.bot.send_message(chat_id=uid, text=f"Adaptive pack created: {link}")
    else:
        # Add to existing pack
        try:
            await add_item_to_pack(context.bot, slug, input_sticker)
        except Exception as e:
            await q.edit_message_text(f"Failed to add to adaptive pack: {e}")
            return ConversationHandler.END

    await q.edit_message_text("Added to your adaptive emoji pack.")
    pending_acr.pop(uid, None)
    return ConversationHandler.END


# 5. Adding to existing packs via incoming item
async def incoming_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_private(update, context):
        return
    msg = update.message
    if not msg:
        return

    s = msg.sticker
    if not s and not msg.sticker and not msg.text and not msg.photo:
        return

    user_id = update.effective_user.id

    # Determine type based on content
    if s and s.is_custom_emoji:
        ptype = "emoji"
    elif s and not s.is_custom_emoji:
        ptype = "sticker"
    elif msg.text:
        ptype = "emoji"
    elif msg.photo:
        ptype = "sticker"
    else:
        return

    packs = find_user_packs(user_id, ptype)
    if not packs:
        await msg.reply_text(f"You have no {ptype} packs. Use /create {ptype} first.")
        return

    buttons = []
    for pack_id, name, title, type_, is_paid, link in packs[:10]:
        buttons.append([InlineKeyboardButton(text=title, callback_data=f"addto|{pack_id}")])
    buttons.append([InlineKeyboardButton(text="Cancel", callback_data="addto|cancel")])

    # Stash the item for later
    context.user_data["pending_add_item_msg_id"] = msg.id
    await msg.reply_text("Choose a pack to add this to:", reply_markup=InlineKeyboardMarkup(buttons))


async def addto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split("|", 1)[1]
    if data == "cancel":
        await q.edit_message_text("Canceled.")
        return

    pack_id = int(data)
    pack = get_pack_by_id(pack_id)
    if not pack:
        await q.edit_message_text("Pack not found.")
        return

    # Enforce limits
    limit = PAID_MAX_ITEMS if bool(pack[5]) else (FREE_MAX_EMOJIS if pack[4] == "emoji" else FREE_MAX_STICKERS)
    current = count_pack_items(pack_id)
    if current >= limit:
        await q.edit_message_text(f"Pack is at its limit ({limit}).")
        return

    # Retrieve original message
    chat_id = q.message.chat.id
    msg_id = context.user_data.pop("pending_add_item_msg_id", None)
    if not msg_id:
        await q.edit_message_text("Original item missing; send again.")
        return
    try:
        orig_msg: Message = await context.bot.forward_message(chat_id=chat_id, from_chat_id=chat_id, message_id=msg_id)
    except Exception:
        # Fallback: use callback message's reply_to if available
        orig_msg = q.message.reply_to_message or q.message

    # Build InputSticker
    input_sticker: Optional[InputSticker] = None
    if orig_msg.sticker:
        s = orig_msg.sticker
        input_sticker = InputSticker(sticker=s.file_id, format=s.format, emoji_list=[s.emoji] if s.emoji else ["😀"]) 
    elif orig_msg.text and pack[4] == "emoji":
        png = render_text_emoji(orig_msg.text[:4], None)
        input_sticker = pil_image_bytes_to_input_sticker(png, ["😀"]) 
    elif orig_msg.photo and pack[4] == "sticker":
        f = await orig_msg.photo[-1].get_file()
        input_sticker = InputSticker(sticker=f.file_id, format="static", emoji_list=["😀"]) 

    if not input_sticker:
        await q.edit_message_text("Unsupported item; send an emoji/sticker/photo.")
        return

    try:
        await add_item_to_pack(context.bot, pack[2], input_sticker)
        insert_pack_item(pack_id, getattr(input_sticker.sticker, 'file_id', None) or "FILE", None, pack[4])
    except Exception as e:
        await q.edit_message_text(f"Failed to add: {e}")
        return

    await q.edit_message_text("Added!")


# 6. Duplicate
async def duplicate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_private(update, context):
        return ConversationHandler.END
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /duplicate <pack_link>")
        return ConversationHandler.END
    # charge stars via invoice
    payload = f"duplicate:{update.effective_user.id}:{int(time.time())}:{args[0]}"
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title="Duplicate Pack",
        description="Duplicate target pack into your account",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice("Duplicate", PRICE_DUPLICATE_XTR)],
    )
    return ConversationHandler.END


# 7. Remove item flow
async def rem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_private(update, context):
        return ConversationHandler.END
    packs = find_user_packs(update.effective_user.id)
    if not packs:
        await update.message.reply_text("You have no packs.")
        return
    buttons = [[InlineKeyboardButton(text=title, callback_data=f"rempick|{pid}")] for pid, name, title, t, p, link in packs]
    await update.message.reply_text("Pick a pack:", reply_markup=InlineKeyboardMarkup(buttons))
    return REM_WAIT_PACK_SELECT


async def rem_pack_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, pid = q.data.split("|", 1)
    pending_remove[q.from_user.id] = {"pack_id": int(pid)}
    await q.edit_message_text("Send the emoji/sticker to remove.")
    return REM_WAIT_ITEM


async def rem_receive_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = pending_remove.get(uid)
    if not state:
        return ConversationHandler.END
    pack = get_pack_by_id(state["pack_id"])
    if not pack:
        await update.message.reply_text("Pack missing.")
        return ConversationHandler.END

    file_id = None
    if update.message.sticker:
        file_id = update.message.sticker.file_id
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
    else:
        await update.message.reply_text("Send a sticker or emoji.")
        return REM_WAIT_ITEM

    # Ask confirm
    buttons = [[InlineKeyboardButton("Confirm", callback_data=f"remconf|{pack[0]}|{file_id}")],
               [InlineKeyboardButton("Cancel", callback_data="remconf|cancel")]]
    await update.message.reply_text("Confirm removal?", reply_markup=InlineKeyboardMarkup(buttons))
    return ConversationHandler.END


async def rem_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split("|")
    if data[1] == "cancel":
        await q.edit_message_text("Canceled.")
        return
    _, _, pack_id_s, file_id = data[0], data[1], data[2], data[3]
    pack_id = int(pack_id_s)
    try:
        await remove_item_from_pack(context.bot, file_id)
        remove_pack_item_if_exists(pack_id, file_id)
        await q.edit_message_text("Removed.")
    except Exception as e:
        await q.edit_message_text(f"Failed: {e}")


# 8. /delete <emoji|sticker>
async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_private(update, context):
        return ConversationHandler.END
    args = context.args
    if not args or args[0] not in ("emoji", "sticker"):
        await update.message.reply_text("Usage: /delete <emoji|sticker>")
        return ConversationHandler.END
    ptype = args[0]
    packs = find_user_packs(update.effective_user.id, ptype)
    if not packs:
        await update.message.reply_text("No packs of that type.")
        return ConversationHandler.END
    buttons = [[InlineKeyboardButton(text=title, callback_data=f"delpick|{pid}")] for pid, name, title, t, p, l in packs]
    await update.message.reply_text("Pick a pack then send item to delete.", reply_markup=InlineKeyboardMarkup(buttons))
    pending_delete[update.effective_user.id] = {"type": ptype}
    return DELETE_WAIT_PACK_SELECT


async def delete_receive_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = pending_delete.get(uid)
    if not state or "pack_id" not in state:
        return ConversationHandler.END
    pack_id = state["pack_id"]
    file_id = None
    if update.message.sticker:
        file_id = update.message.sticker.file_id
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
    else:
        await update.message.reply_text("Send an emoji/sticker.")
        return DELETE_WAIT_PACK_SELECT
    buttons = [[InlineKeyboardButton("Confirm", callback_data=f"remconf|{pack_id}|{file_id}")],
               [InlineKeyboardButton("Cancel", callback_data="remconf|cancel")]]
    await update.message.reply_text("Confirm deletion?", reply_markup=InlineKeyboardMarkup(buttons))
    return ConversationHandler.END


async def del_pack_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, pid = q.data.split("|", 1)
    pending_delete[q.from_user.id] = {"pack_id": int(pid)}
    await q.edit_message_text("Send the emoji/sticker to delete.")
    return DELETE_WAIT_PACK_SELECT


# 9. Buying paid packs
async def bpack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_private(update, context):
        return
    args = context.args
    if not args or args[0] not in ("emoji", "sticker"):
        await update.message.reply_text("Usage: /bpack <emoji|sticker>")
        return
    kind = args[0]
    price = PRICE_BPACK_EMOJI_XTR if kind == "emoji" else PRICE_BPACK_STICKER_XTR
    payload = f"bpack:{update.effective_user.id}:{int(time.time())}:{kind}"
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=f"Buy {kind} pack tier",
        description=f"Unlock paid {kind} packs",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice("Paid pack", price)],
    )


# 10. Owner/Admin
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /admin <user_id>")
        return
    try:
        target_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("Invalid user id")
        return
    get_or_create_user(target_id)
    set_user_field(target_id, "is_paid", 1)
    set_user_field(target_id, "is_admin", 1)
    set_user_field(target_id, "free_pack_uses", 20)
    await update.message.reply_text(f"User {target_id} promoted with free/paid privileges.")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Unauthorized.")
        return
    # Prefer forwarding the replied message if present
    if update.message.reply_to_message:
        with db() as con:
            cur = con.cursor()
            cur.execute("SELECT user_id FROM users")
            uids = [r[0] for r in cur.fetchall()]
        sent = 0
        for uid in uids:
            try:
                await update.message.reply_to_message.copy(chat_id=uid)
                sent += 1
            except Exception:
                continue
        await update.message.reply_text(f"Broadcast forwarded to {sent} users.")
        return

    text = " ".join(context.args) if context.args else None
    if not text:
        await update.message.reply_text("Provide text or reply to a message.")
        return
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT user_id FROM users")
        uids = [r[0] for r in cur.fetchall()]
    sent = 0
    for uid in uids:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception:
            continue
    await update.message.reply_text(f"Broadcast sent to {sent} users.")


async def set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Unauthorized.")
        return
    if not context.args or context.args[0] not in ("on", "off"):
        await update.message.reply_text("Usage: /set <on|off>")
        return
    val = context.args[0] == "on"
    set_setting(SETTING_OWNER_ITEMS_FOR_SALE, val)
    await update.message.reply_text(f"Owner items for sale set to {val}.")


# 11. Payments handling (Stars invoices)
async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.pre_checkout_query
    # Accept only our payload patterns
    ok = bool(re.match(r"^(bpack|apack|duplicate):", q.invoice_payload or ""))
    await q.answer(ok=ok, error_message=None if ok else "Invalid invoice.")


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sp = update.message.successful_payment
    payload = sp.invoice_payload or ""
    parts = payload.split(":")
    if not parts:
        return
    kind = parts[0]
    uid = int(parts[1]) if len(parts) > 1 else update.effective_user.id

    if kind == "bpack":
        set_user_field(uid, "is_paid", 1)
        await update.message.reply_text("Paid pack unlocked. Use /create to make a paid pack.")
    elif kind == "apack":
        await update.message.reply_text("Payment received. Use /acr to add adaptive emoji.")
    elif kind == "duplicate":
        link = parts[3] if len(parts) > 3 else None
        target_name = parse_pack_link(link) if link else None
        if not target_name:
            await update.message.reply_text("Invalid pack link.")
            return
        bot_username = (await context.bot.get_me()).username
        new_slug = normalize_pack_name(f"dup_{uid}_{int(time.time())}_by_{bot_username}")
        try:
            new_name, new_type = await duplicate_pack(context.bot, target_name, uid, new_slug, f"Duplicate of {target_name}")
        except Exception as e:
            await update.message.reply_text(f"Duplication failed: {e}")
            return
        link = f"https://t.me/{'addemoji' if new_type == 'custom_emoji' else 'addstickers'}/{new_name}"
        insert_pack(uid, new_name, f"Duplicate of {target_name}", 'emoji' if new_type == 'custom_emoji' else 'sticker', True, link)
        await update.message.reply_text(f"Duplicated: {link}")


# 12. Backup & Migration
async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_private(update, context):
        return
    uid = update.effective_user.id
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM packs WHERE user_id=?", (uid,))
        packs = cur.fetchall()
        cur.execute(
            "SELECT * FROM pack_items WHERE pack_id IN (SELECT pack_id FROM packs WHERE user_id=?)",
            (uid,),
        )
        items = cur.fetchall()
    data = {"packs": packs, "items": items}
    content = json.dumps(data).encode()
    path = os.path.join(BACKUP_DIR, f"export_{uid}_{int(time.time())}.json")
    with open(path, "wb") as f:
        f.write(content)
    await update.message.reply_text("Export complete.")


async def import_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_private(update, context):
        return
    if not update.message.document:
        await update.message.reply_text("Please attach a JSON backup file to the /import command as a document.")
        return
    file = await update.message.document.get_file()
    content = await file.download_as_bytearray()
    try:
        data = json.loads(content)
    except Exception:
        await update.message.reply_text("Invalid JSON.")
        return
    packs = data.get("packs", [])
    items = data.get("items", [])
    with db() as con:
        cur = con.cursor()
        for p in packs:
            try:
                _, user_id, name, title, type_, is_paid_pack, link, _ = p
                if user_id != update.effective_user.id:
                    continue
                cur.execute(
                    "INSERT OR IGNORE INTO packs(user_id,name,title,type,is_paid_pack,pack_link) VALUES(?,?,?,?,?,?)",
                    (user_id, name, title, type_, is_paid_pack, link),
                )
            except Exception:
                continue
        for it in items:
            try:
                _, pack_id, file_id, emoji, type_, _ = it
                cur.execute(
                    "INSERT OR IGNORE INTO pack_items(pack_id,file_id,emoji,type) VALUES(?,?,?,?)",
                    (pack_id, file_id, emoji, type_),
                )
            except Exception:
                continue
        con.commit()
    await update.message.reply_text("Import complete.")


# Fallbacks
async def cancel_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending_create.pop(update.effective_user.id, None)
    pending_remove.pop(update.effective_user.id, None)
    pending_delete.pop(update.effective_user.id, None)
    pending_acr.pop(update.effective_user.id, None)
    await update.message.reply_text("Canceled.")
    return ConversationHandler.END


async def mypack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_private(update, context):
        return
    uid = update.effective_user.id
    packs = find_user_packs(uid)
    if not packs:
        await update.message.reply_text("You have no packs yet. Use /create to get started.")
        return
    buttons = [[InlineKeyboardButton(text=f"{title} ({'emoji' if t=='emoji' else 'sticker'})", callback_data=f"mypack|{pid}")]
               for pid, name, title, t, p, link in packs[:20]]
    await update.message.reply_text("Your packs:", reply_markup=InlineKeyboardMarkup(buttons))


async def mypack_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        _, pid_s = q.data.split("|", 1)
        pid = int(pid_s)
    except Exception:
        await q.edit_message_text("Invalid selection.")
        return
    p = get_pack_by_id(pid)
    if not p or p[1] != q.from_user.id:
        await q.edit_message_text("Pack not found.")
        return
    pack_id, owner_id, name, title, type_, is_paid_pack, link = p

    # Try fetching live sticker set info
    items_count = None
    try:
        ss = await context.bot.get_sticker_set(name=name)
        items_count = getattr(ss, "sticker_count", None)
    except Exception:
        pass
    if items_count is None:
        items_count = count_pack_items(pack_id)

    info_lines = [
        f"Title: {title}",
        f"Type: {'emoji' if type_=='emoji' else 'sticker'}",
        f"Paid pack: {'yes' if is_paid_pack else 'no'}",
        f"Items: {items_count}",
        f"Link: {link}",
        "Users using: not tracked",
    ]
    buttons = [
        [InlineKeyboardButton(text="Open", url=link)],
    ]
    await q.edit_message_text("\n".join(info_lines), reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)


def build_app() -> Application:
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Core commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("mypack", mypack_cmd))

    # Create conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("create", create_cmd)],
        states={
            CREATE_WAIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_receive_name)],
            CREATE_WAIT_FIRST_ITEM: [MessageHandler((filters.Sticker.ALL | filters.PHOTO | filters.TEXT) & ~filters.COMMAND, create_receive_first_item)],
        },
        fallbacks=[CommandHandler("cancel", cancel_all)],
        name="create_conv",
        persistent=False,
    ))

    # Adaptive
    app.add_handler(CommandHandler("apack", apack))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("acr", acr)],
        states={
            ACR_WAIT_INPUT: [MessageHandler((filters.TEXT | filters.PHOTO | filters.Sticker.ALL) & ~filters.COMMAND, acr_receive)],
            ACR_WAIT_FONT: [CallbackQueryHandler(acr_font_choice, pattern=r"^acr_font\|")],
            ACR_WAIT_BG: [CallbackQueryHandler(acr_bg_choice, pattern=r"^acr_bg\|")],
        },
        fallbacks=[CommandHandler("cancel", cancel_all)],
        name="acr_conv",
        persistent=False,
    ))

    # Inline add to packs
    app.add_handler(MessageHandler((filters.Sticker.ALL | filters.PHOTO | filters.TEXT) & ~filters.COMMAND, incoming_item))
    app.add_handler(CallbackQueryHandler(addto_callback, pattern=r"^addto\|"))
    app.add_handler(CallbackQueryHandler(mypack_select, pattern=r"^mypack\|"))

    # Duplicate
    app.add_handler(CommandHandler("duplicate", duplicate_cmd))

    # Remove flow
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("rem", rem)],
        states={
            REM_WAIT_PACK_SELECT: [CallbackQueryHandler(rem_pack_pick, pattern=r"^rempick\|")],
            REM_WAIT_ITEM: [MessageHandler((filters.Sticker.ALL | filters.PHOTO) & ~filters.COMMAND, rem_receive_item)],
        },
        fallbacks=[CommandHandler("cancel", cancel_all), CallbackQueryHandler(rem_confirm, pattern=r"^remconf\|")],
        name="rem_conv",
        persistent=False,
    ))
    app.add_handler(CallbackQueryHandler(rem_confirm, pattern=r"^remconf\|"))

    # Delete
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("delete", delete_cmd)],
        states={
            DELETE_WAIT_PACK_SELECT: [CallbackQueryHandler(del_pack_pick, pattern=r"^delpick\|"),
                                      MessageHandler((filters.Sticker.ALL | filters.PHOTO) & ~filters.COMMAND, delete_receive_item)],
        },
        fallbacks=[CommandHandler("cancel", cancel_all), CallbackQueryHandler(rem_confirm, pattern=r"^remconf\|")],
        name="delete_conv",
        persistent=False,
    ))

    # Paid
    app.add_handler(CommandHandler("bpack", bpack))

    # Admin
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("set", set_cmd))

    # Payments
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Backup
    app.add_handler(CommandHandler("export", export_cmd))
    app.add_handler(CommandHandler("import", import_cmd))

    return app


async def main_async():
    app = build_app()
    logger.info("Starting bot %s", APP_NAME)
    await app.run_polling(close_loop=False)


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass