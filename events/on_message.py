import re
import emoji
import unicodedata
from discord.ext import commands
from discord import Message
from database.user_base_db import userBaseDB
from config import MAX_MESSAGE_REPUTATION


# 預編譯正規表達式，避免高頻訊息重覆配置編譯物件與字串
URL_PATTERN = re.compile(r'https?://\S+')
CUSTOM_EMOJI_PATTERN = re.compile(r'<a?:\w+:\d+>')
MENTION_USER_PATTERN = re.compile(r'<@!?\d+>')
MENTION_ROLE_PATTERN = re.compile(r'<@&\d+>')
MENTION_CHANNEL_PATTERN = re.compile(r'<#\d+>')
MARKDOWN_PATTERN = re.compile(r'[*_~`]')
ZERO_WIDTH_CHARS = ('\u200b', '\u200c', '\u200d', '\uFEFF', '\n', '\t', '\r')

def count_readable_chars(message: Message) -> int:
    """計算可讀字元數（預編譯高效版）"""
    text = message.content

    # 1. 去除零寬字符
    for zw in ZERO_WIDTH_CHARS:
        text = text.replace(zw, ' ')

    # 2. 替換連結、表情、mentions（使用預編譯 regex）
    text = URL_PATTERN.sub('U', text)
    text = CUSTOM_EMOJI_PATTERN.sub('E', text)
    text = MENTION_USER_PATTERN.sub('M', text)
    text = MENTION_ROLE_PATTERN.sub('R', text)
    text = MENTION_CHANNEL_PATTERN.sub('C', text)

    # 3. 替換 Discord stickers
    if getattr(message, "stickers", None):
        text += 'S' * len(message.stickers)

    # 4. 替換 emoji
    text = emoji.replace_emoji(text, replace='E')

    # 5. 去掉 Markdown 符號
    text = MARKDOWN_PATTERN.sub('', text)

    # 6. 附件算 1 個
    if getattr(message, "attachments", None):
        text += 'A' * len(message.attachments)

    # 7. 正規化文字
    text = unicodedata.normalize('NFC', text).strip().replace(' ', '')

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

            # 即時身分組檢查（單人）
            if message.guild:
                cog = self.bot.get_cog("RoleCheckEvent")
                if cog and hasattr(cog, "check_single_user"):
                    await cog.check_single_user(message.guild, user_id)
        except Exception as e:
            print(f"❌ on_message 處理錯誤: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(OnMessageEvent(bot))