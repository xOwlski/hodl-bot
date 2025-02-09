import datetime

import discord
from tortoise import timezone
from discord.ext import commands
from discord_slash import cog_ext
from tortoise.query_utils import Q
from discord_slash.model import ButtonStyle
from discord_slash.context import ComponentContext
from discord_slash.utils.manage_components import create_button, create_actionrow

from app.models import User, UserEpoch, Epoch
from config import SHOULD_STAKE_AFTER_FIRST_EPOCH, PROJECT_NAME
from app.utils import ensure_registered, get_user_balance, display_staking_info
from app.constants import GENESIS_EPOCH_ID, PENALTIES_FREE_DAYS_FOR_GENESIS


class OnboardingCog(commands.Cog):
    """Cog which is resposible for onboarding new users into staking world"""

    def __init__(self, bot: commands.Bot):
        self.bot: commands.Bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # ignore bot's own messages
        if message.author == self.bot.user:
            return None
        # allow only messages from DM
        if message.guild:
            return None
        print('building')
        # check if users is already staking
        is_already_staking = False
        try:
            is_already_staking = await User.exists(Q(id=message.author.id) & Q(is_staking=True))
        except Exception as e:
            print(e)
            print("An exception occurred")
        print('success')
        if is_already_staking:
            buttons = [
                create_button(style=ButtonStyle.red, label="Yes", custom_id="continue_staking_no"),
                create_button(style=ButtonStyle.blue, label="No", custom_id="continue_staking_yes"),
            ]
            action_row = create_actionrow(*buttons)
            user = await User.get(id=message.author.id)
            current_epoch = await Epoch.all().order_by("-id").first()
            is_epoch_genesis = current_epoch.id == GENESIS_EPOCH_ID
            if not is_epoch_genesis and not SHOULD_STAKE_AFTER_FIRST_EPOCH:
                return await message.channel.send("Earned points will be distributed soon")
            user_epoch = await UserEpoch.get(user_id=user.id, epoch_id=current_epoch.id)
            return await message.channel.send(
                display_staking_info(
                    points=user.balance,
                    epoch_lowest_balance=user_epoch.epoch_lowest_balance,
                    current_epoch=current_epoch,
                )
                # + "\n\nDo you want to stop staking?",
                # components=[action_row],
            )
        else:
            buttons = [
                create_button(style=ButtonStyle.red, label="Yes", custom_id="start_staking_yes"),
                create_button(style=ButtonStyle.blue, label="No", custom_id="start_staking_no"),
            ]
            action_row = create_actionrow(*buttons)
            return await message.channel.send(f"Do you want to stake {PROJECT_NAME} points?", components=[action_row])

    @cog_ext.cog_component(components=["start_staking_yes"])
    async def choose_staking_yes(self, ctx: ComponentContext) -> None:
        await ensure_registered(ctx.author.id)
        points = await get_user_balance(ctx.author.id)
        await User.filter(id__in=[ctx.author.id]).update(
            balance=points, is_staking=True, staking_started_date=timezone.now()
        )
        current_epoch = await Epoch.all().order_by("-id").first()
        penalties_free = (
            current_epoch.id == GENESIS_EPOCH_ID
            and current_epoch.start_datetime + datetime.timedelta(days=PENALTIES_FREE_DAYS_FOR_GENESIS)
            > timezone.now()
        )
        if penalties_free:
            # allow to stake for genesis epoch without penalties during the first day
            epoch_lowest_balance = points
        elif not SHOULD_STAKE_AFTER_FIRST_EPOCH:
            await ctx.edit_origin(
                content="I'm sorry but you can't begin staking right now.",
                components=[],
            )
            return None
        else:
            # in current epoch user's staking balance will be zero
            # if you started staking in between epochs your stake will be counted from the next epoch
            epoch_lowest_balance = 0
        await ctx.edit_origin(
            content=f"Good. Your points will be staked. Please note that {current_epoch.portfolio_percentage * 100}% of your balance will be staked. To be eligible for rewards you need to HODL points. After 2 weeks you are expected to earn {current_epoch.apy * 100}%. If you started staking in between epochs your stake will be counted from the next epoch.\n\n{display_staking_info(points=points, epoch_lowest_balance=epoch_lowest_balance, current_epoch=current_epoch)}",  # noqa: E501
            components=[],
        )
        await UserEpoch.get_or_create(
            user_id=ctx.author.id, epoch_id=current_epoch.id, defaults={"epoch_lowest_balance": epoch_lowest_balance}
        )
        return None

    @cog_ext.cog_component(components=["start_staking_no", "continue_staking_no"])
    async def choose_staking_no(self, ctx: ComponentContext):
        await ctx.edit_origin(content="You choose to not receive APY, have a nice day.", components=[])
        await ensure_registered(ctx.author.id)
        await User.filter(id__in=[ctx.author.id]).update(is_staking=False, staking_started_date=None)
        current_epoch = await Epoch.all().order_by("-id").first()
        await UserEpoch.filter(user_id=ctx.author.id, epoch_id=current_epoch.id).update(epoch_lowest_balance=0)

    @cog_ext.cog_component()
    async def continue_staking_yes(self, ctx: ComponentContext):
        await ctx.edit_origin(content="You choose to continue staking, have a nice day.", components=[])


def setup(bot):
    bot.add_cog(OnboardingCog(bot))
