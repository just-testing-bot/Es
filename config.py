import os

# Bot configuration
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "8218454531:AAFq9oMvoVtpk1hqycvQQxcYhPguvY1-0Rg")
OWNER_ID: int = int(os.getenv("OWNER_ID", "2020690884"))

# Persistence
DB_PATH: str = os.getenv("DB_PATH", "/workspace/bot.db")
BACKUP_DIR: str = os.getenv("BACKUP_DIR", "/workspace/backups")

# Feature flags and limits
FREE_MAX_STICKERS: int = int(os.getenv("FREE_MAX_STICKERS", "30"))
FREE_MAX_EMOJIS: int = int(os.getenv("FREE_MAX_EMOJIS", "40"))
PAID_MAX_ITEMS: int = int(os.getenv("PAID_MAX_ITEMS", "120"))

FREE_PACK_NAME_MIN_LEN: int = 4
FREE_PACK_NAME_MAX_LEN: int = 12
PAID_PACK_NAME_MIN_LEN: int = 1
PAID_PACK_NAME_MAX_LEN: int = 32

# Prices in Telegram Stars (XTR)
PRICE_BPACK_EMOJI_XTR: int = int(os.getenv("PRICE_BPACK_EMOJI_XTR", "35"))
PRICE_BPACK_STICKER_XTR: int = int(os.getenv("PRICE_BPACK_STICKER_XTR", "25"))
PRICE_APACK_XTR: int = int(os.getenv("PRICE_APACK_XTR", "100"))
PRICE_DUPLICATE_XTR: int = int(os.getenv("PRICE_DUPLICATE_XTR", "30"))

# Operational
ONLY_PRIVATE_CHATS: bool = os.getenv("ONLY_PRIVATE_CHATS", "true").lower() == "true"

# Settings keys
SETTING_OWNER_ITEMS_FOR_SALE: str = "owner_items_for_sale"

# Derived
APP_NAME: str = os.getenv("APP_NAME", "TeleStickersBot")