---

Telegram Stickers & Adaptive Emojis Bot — Full Feature Specification

Overview

A Telegram private-chat bot that lets users create, manage, and monetize emoji and sticker packs with adaptive emoji generation and Telegram Stars payment integration. Supports pack creation, addition, deletion, duplication, and offers free & paid tiers with limits. Bot owner has admin privileges.


---

Commands & Features

1. /start

Sends a warm welcome message with a short inspiring tagline about the bot’s power and fun.


2. /help

Detailed usage instructions for all commands, limits, and paid features.



---

3. Creating Packs

/create <emoji|sticker>

Flow:

1. Bot asks user for pack name (4–12 chars for free packs).


2. Bot asks user to send a single emoji or sticker as first item.


3. Bot creates pack via Telegram API with name:

Free packs: <user_given_name>_<bot_username>.

Paid packs: custom name without suffix (1–32 chars).



4. Bot sends the pack link to the user.


5. Stores pack metadata and items in the database.



Limits:

Free users: 1 emoji pack (max 40 emojis), 1 sticker pack (max 30 stickers).

Paid users: up to Telegram max limits (e.g., 120 items), longer names allowed.




---

4. Adaptive Emojis (Paid, owner-only for now)

/apack

Creates an adaptive emoji pack for the user (cost: 100 Telegram Stars).


/acr

Adaptive emoji creation from user input. Supports:

Emoji input: auto-converted/scaled, no manual edits.

Text input:

/acr → prompts user to send multi-line text (enter creates new lines).

Bot shows font selection (10–20 fonts: handwriting, Times New Roman, italic, bold, etc.).

Bot shows background options:

1. No background


2. 50% transparent background (if feasible, else cancel)


3. Only background filled (text transparent, background visible)




Photo input: auto scaled and formatted.


Supports animated emojis if Telegram allows.

Adds created emoji to user’s adaptive emoji pack, confirms success.



---

5. Adding to Existing Packs

When user sends or forwards a single emoji/sticker, bot shows inline keyboard of user’s packs of that type.

User selects pack → item is added to that pack and DB.

Includes Cancel button.



---

6. Duplicating Packs (Paid Feature)

/duplicate <pack_link>

Costs 30 Telegram Stars.

Downloads all emojis/stickers (any format) from target pack.

Creates new pack for user with same content.

Stores new pack in DB.



---

7. Removing Emoji/Sticker from Packs

/rem

Flow:

1. User sends /rem.


2. Bot shows inline keyboard of user’s packs.


3. User selects a pack.


4. Bot prompts user to send emoji/sticker to remove.


5. If exists in pack, bot asks confirmation (Confirm / Cancel).


6. On confirm, removes emoji/sticker from Telegram pack and DB, then confirms success.


7. If not found, informs user and cancels.





---

8. Deleting Emoji/Sticker (Alternative Command)

/delete <emoji|sticker>

Shows user’s packs of the type.

User selects pack → confirms deletion of given emoji/sticker.



---

9. Buying Paid Packs

/bpack <emoji|sticker>

Sends Telegram Stars invoice (35 stars emoji, 25 stars sticker).

On successful payment, user can create paid packs with:

Extended max limits (Telegram max, e.g., 120 items).

Longer pack names (1–32 chars, no bot username suffix).


Follows /create flow with upgraded options.



---

10. Owner/Admin Features

Owner ID: 2020690884.

/admin <user_id> → marks user non-payable, allowing 20 free /create uses and free paid features.

/broadcast:

Reply mode: forwards replied message to all users.

Direct mode: /broadcast <message> sends message/media to all users.




---

11. Data & Process Control

Bot works only in private chats.

Proper error handling, callback, and button handling.

Concurrency control to prevent conflicting processes.

Telegram Bot API payment with Telegram Stars (sendInvoice).

Adaptive emoji creation and duplicate features restricted to owner for now.

Accepts all common Telegram sticker formats (.webp, .tgs, .webm).

Adaptive emojis auto-converted without manual user editing.

Text emoji rendering supports specified fonts and backgrounds.



---

12. Database Schema (Suggested)

Users

user_id (int)

is_paid (bool)

is_admin (bool)

free_pack_uses (int)

paid_pack_uses (int)

adaptive_pack_id (nullable)

Other metadata


Packs

pack_id (unique id)

user_id (owner)

name (string)

type (emoji or sticker)

is_paid_pack (bool)

pack_link (string)

items (list of file_ids or custom_emoji_ids)

created_at


Pack Items

item_id

pack_id

file_id or custom_emoji_id

type (emoji or sticker)

added_at



---

13. Backup & Migration

Support /import and /export commands for database backup and restore.

Auto-migration for schema updates.



---
Make the code in three files: main.py, emoji.py, sticker.py and config.py for constants and changeable variables.
Bot token: 8218454531:AAFq9oMvoVtpk1hqycvQQxcYhPguvY1-0Rg
Bot owner: 2020690884
