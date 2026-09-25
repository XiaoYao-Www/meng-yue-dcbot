from typing import Optional, List

import discord
from discord import app_commands, Interaction
from discord.ext import commands

from config import MAX_USER_ITEMS_PER_TAG
from database.user_base_db import userBaseDB


class MenuManageCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    ##### 自動補全輔助 #####

    async def _get_user_menu_items(self, user_id: int) -> List[str]:
        """### 取得使用者的菜單項目清單（供自動補全使用）

        Args:
            user_id: 使用者 Discord ID

        Returns:
            菜單項目字串列表
        """
        return await userBaseDB.get_user_items(user_id, tag="menu")

    async def _menu_item_autocomplete(
        self, interaction: Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """### menu_remove 的自動補全回呼

        Args:
            interaction: Discord Interaction
            current: 使用者已輸入的文字

        Returns:
            符合條件的選項列表
        """
        items = await self._get_user_menu_items(interaction.user.id)
        filtered = [item for item in items if current.lower() in item.lower()]
        return [app_commands.Choice(name=item, value=item) for item in filtered[:25]]

    ##### Menu Add #####

    @app_commands.command(name="menu_add", description="將項目加入你的個人菜單")
    @app_commands.describe(item="要加入的項目名稱")
    async def menu_add(self, interaction: Interaction, item: str):
        """### 新增菜單項目

        Args:
            interaction: Discord Interaction
            item: 項目名稱
        """
        try:
            item = item.strip()
            if not item:
                await interaction.response.send_message("❌ 項目名稱不可為空。", ephemeral=True)
                return

            count = await userBaseDB.get_user_item_count(interaction.user.id, tag="menu")
            if count >= MAX_USER_ITEMS_PER_TAG:
                await interaction.response.send_message(
                    f"❌ 你的菜單已達上限 **{MAX_USER_ITEMS_PER_TAG}** 個項目，請先移除一些再新增。",
                    ephemeral=True,
                )
                return

            success = await userBaseDB.add_user_item(interaction.user.id, tag="menu", item=item)
            if success:
                await interaction.response.send_message(
                    f"✅ 已將 **{item}** 加入你的菜單！（目前共 {count + 1} 項）",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"⚠️ **{item}** 已在你的菜單中，請勿重複新增。",
                    ephemeral=True,
                )
        except Exception as e:
            print(f"❌ menu_add 指令錯誤: {e}")
            try:
                await interaction.response.send_message("❌ 新增失敗，請稍後再試。", ephemeral=True)
            except Exception:
                pass

    ##### Menu Remove #####

    @app_commands.command(name="menu_remove", description="從你的個人菜單移除項目")
    @app_commands.describe(item="要移除的項目名稱")
    @app_commands.autocomplete(item=_menu_item_autocomplete)
    async def menu_remove(self, interaction: Interaction, item: str):
        """### 移除菜單項目

        Args:
            interaction: Discord Interaction
            item: 項目名稱
        """
        try:
            success = await userBaseDB.remove_user_item(interaction.user.id, tag="menu", item=item)
            if success:
                await interaction.response.send_message(
                    f"🗑️ 已從你的菜單移除 **{item}**。",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"❌ 你的菜單中沒有 **{item}**。",
                    ephemeral=True,
                )
        except Exception as e:
            print(f"❌ menu_remove 指令錯誤: {e}")
            try:
                await interaction.response.send_message("❌ 移除失敗，請稍後再試。", ephemeral=True)
            except Exception:
                pass

    ##### Menu List #####

    @app_commands.command(name="menu_list", description="列出你個人菜單中的所有項目")
    async def menu_list(self, interaction: Interaction):
        """### 列出個人菜單

        Args:
            interaction: Discord Interaction
        """
        try:
            items = await userBaseDB.get_user_items(interaction.user.id, tag="menu")
            if not items:
                await interaction.response.send_message(
                    "📭 你的菜單目前是空的，使用 `/menu_add` 加入項目吧！",
                    ephemeral=True,
                )
                return

            # 分頁顯示：每頁 20 項
            lines = []
            for i, item in enumerate(items, start=1):
                lines.append(f"{i}. {item}")

            embed = discord.Embed(
                title=f"📋 {interaction.user.display_name} 的菜單",
                description="\n".join(lines),
                color=discord.Color.blue(),
            )
            embed.set_footer(text=f"共 {len(items)} 項")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            print(f"❌ menu_list 指令錯誤: {e}")
            try:
                await interaction.response.send_message("❌ 查詢失敗，請稍後再試。", ephemeral=True)
            except Exception:
                pass

    ##### Menu Pick #####

    @app_commands.command(name="menu_pick", description="從你的個人菜單中隨機抽出一個項目")
    @app_commands.describe(private="設為 True 則只有你自己看得到結果（預設 False）")
    async def menu_pick(self, interaction: Interaction, private: Optional[bool] = False):
        """### 隨機抽取菜單項目

        Args:
            interaction: Discord Interaction
            private: 是否僅自己可見
        """
        try:
            item = await userBaseDB.pick_random_user_item(interaction.user.id, tag="menu")
            if item is None:
                await interaction.response.send_message(
                    "📭 你的菜單是空的！先用 `/menu_add` 加入一些項目吧。",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="🎲 抽籤結果",
                description=f"**{interaction.user.display_name}** 抽到了...\n\n# 🎉 {item} 🎉",
                color=discord.Color.gold(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=private)

        except Exception as e:
            print(f"❌ menu_pick 指令錯誤: {e}")
            try:
                await interaction.response.send_message("❌ 抽取失敗，請稍後再試。", ephemeral=True)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(MenuManageCommand(bot))
