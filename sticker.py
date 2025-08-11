from __future__ import annotations

import asyncio
import logging
import re
from typing import List, Optional

from telegram import Bot, InputSticker, StickerSet

logger = logging.getLogger(__name__)


def normalize_pack_name(base: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", base).strip("_")
    return slug.lower()


async def create_pack(
    bot: Bot,
    owner_user_id: int,
    name_slug: str,
    title: str,
    first_sticker: InputSticker,
    sticker_type: str,
) -> str:
    await bot.create_new_sticker_set(
        user_id=owner_user_id,
        name=name_slug,
        title=title,
        stickers=[first_sticker],
        sticker_type=sticker_type,
    )
    return name_slug


async def add_item_to_pack(bot: Bot, name_slug: str, sticker: InputSticker) -> None:
    await bot.add_sticker_to_set(name=name_slug, sticker=sticker)


async def remove_item_from_pack(bot: Bot, sticker_file_id: str) -> None:
    await bot.delete_sticker_from_set(sticker=sticker_file_id)


async def get_pack(bot: Bot, name_slug: str) -> StickerSet:
    return await bot.get_sticker_set(name=name_slug)


def parse_pack_link(link: str) -> Optional[str]:
    m = re.search(r"t\.me\/(?:addstickers|addemoji)\/([A-Za-z0-9_]+)", link or "")
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_]+", link or ""):
        return link
    return None


async def duplicate_pack(
    bot: Bot,
    target_pack_name: str,
    new_owner_user_id: int,
    new_name_slug: str,
    new_title: str,
) -> str:
    original = await bot.get_sticker_set(name=target_pack_name)
    sticker_type = original.sticker_type

    input_stickers: List[InputSticker] = []
    for s in original.stickers:
        try:
            input_stickers.append(InputSticker(sticker=s.file_id, format=s.format, emoji_list=s.emoji))
        except Exception:
            input_stickers.append(InputSticker(sticker=s.file_id, format="static", emoji_list=s.emoji))

    if not input_stickers:
        raise ValueError("Source pack has no stickers")

    first = input_stickers[0]
    await bot.create_new_sticker_set(
        user_id=new_owner_user_id,
        name=new_name_slug,
        title=new_title,
        stickers=[first],
        sticker_type=sticker_type,
    )

    for idx in range(1, len(input_stickers)):
        await bot.add_sticker_to_set(name=new_name_slug, sticker=input_stickers[idx])
        if idx % 10 == 0:
            await asyncio.sleep(0)

    return new_name_slug