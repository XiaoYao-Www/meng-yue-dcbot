import re
import emoji
import unicodedata
from discord.ext import commands
from discord import Message
from database.user_base_db import userBaseDB
from config import MAX_MESSAGE_REPUTATION


def count_readable_chars(message: Message) -> int:
    """計算可讀字元數

    Args:
        message (Message): 訊息

    Returns:
        int: 自數
    """
    text = message.content

    # 1. 去除零寬字符
    zero_width_chars = ['\u200b', '\u200c', '\u200d', '\uFEFF', '\n', '\t', '\r']
    for zw in zero_width_chars:
        text = text.replace(zw, ' ')

    # 2. 替換連結
    text = re.sub(r'https?://\S+', 'U', text)

    # 3. 替換自訂表情
    text = re.sub(r'<a?:\w+:\d+>', 'E', text)

    # 4. 替換 mentions
    text = re.sub(r'<@!?\d+>', 'M', text)   # 使用者
    text = re.sub(r'<@&\d+>', 'R', text)    # 角色
    text = re.sub(r'<#\d+>', 'C', text)     # 頻道

    # 5. 替換 Discord stickers (如果有)
    if hasattr(message, "stickers"):
        for _ in message.stickers:
            text += 'S'

    # 6. 替換 emoji
    text = emoji.replace_emoji(text, replace='E')

    # 7. 去掉 Markdown 符號 (*, _, ~, `)
    text = re.sub(r'[*_~`]', '', text)

    # 8. 附件算 1 個
    if hasattr(message, "attachments"):
        for _ in message.attachments:
            text += 'A'

    # 9. 正規化文字（避免全形、組合字膨脹）
    text = unicodedata.normalize('NFC', text)

    text = text.strip()
    
    text = text.replace(' ', '')

    # 最終可讀字數
    return len(text)


class OnMessageEvent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        """偵測訊息

        Args:
            message (Message): 訊息
        """
        try:
            # 排除機器人訊息
            if message.author.bot:
                return
            
            user_id = message.author.id
            word_count = min(count_readable_chars(message), MAX_MESSAGE_REPUTATION)

            await userBaseDB.update_user_stats(user_id, xp=word_count, reputation=word_count)
        except Exception as e:
            print(f"❌ on_message 處理錯誤: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(OnMessageEvent(bot))