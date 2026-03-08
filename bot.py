import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timezone, timedelta

# ---------- НАСТРОЙКИ ----------
MAIN_ADMIN = 1072968512076787744  # главный админ
event_admins = {MAIN_ADMIN}       # список админов

TOKEN = os.getenv("TOKEN")
MSK = timezone(timedelta(hours=3))

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- ХРАНЕНИЕ ДАННЫХ ----------
events = {}          # events[guild_id][channel_id][event_id]
event_messages = {}  # event_messages[guild_id][channel_id][event_id] = message_id
main_messages = {}   # main_messages[guild_id][channel_id] = message_id


# ---------- EMBED МЕРОПРИЯТИЯ ----------
def make_event_embed(event: dict) -> discord.Embed:
    users_text = "\n".join(
        [f"{i+1}. <@{uid}> — {t}" for i, (uid, t) in enumerate(event["users"].items())]
    ) or "Пока пусто"

    status = "Открыто" if not event["closed"] else "Закрыто"

    embed = discord.Embed(
        title=event["name"],
        description=(
            f"Статус: **{status}**\n"
            f"Мест: {len(event['users'])}/{event['max']}\n"
            f"Закрытие: {event['close_time'].strftime('%Y-%m-%d %H:%M')}\n\n"
            f"**Участники:**\n{users_text}"
        ),
        color=0x2f3136 if not event["closed"] else 0x555555,
    )

    if event.get("image_url"):
        embed.set_image(url=event["image_url"])

    return embed


# ---------- EMBED СПИСКА ----------
def make_list_embed(events_dict: dict) -> discord.Embed:
    if not events_dict:
        desc = "Пока нет мероприятий"
    else:
        desc = "\n".join(
            [f"**#{eid}** — {ev['name']} ({len(ev['users'])}/{ev['max']})"
             for eid, ev in events_dict.items()]
        )

    embed = discord.Embed(
        title="Список мероприятий",
        description=desc,
        color=0x2f3136
    )
    return embed


# ---------- VIEW ----------
class EventView(discord.ui.View):
    def __init__(self, guild_id, channel_id, event_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.event_id = event_id

    @discord.ui.button(label="Записаться", style=discord.ButtonStyle.primary)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_join(interaction, self.guild_id, self.channel_id, self.event_id)


# ---------- ОБНОВЛЕНИЕ СПИСКА ----------
async def update_list(guild_id, channel):
    channel_id = channel.id
    guild_events = events.get(guild_id, {})
    channel_events = guild_events.get(channel_id, {})

    embed = make_list_embed(channel_events)

    if guild_id not in main_messages:
        main_messages[guild_id] = {}

    if channel_id not in main_messages[guild_id]:
        msg = await channel.send(embed=embed)
        main_messages[guild_id][channel_id] = msg.id
    else:
        try:
            msg = await channel.fetch_message(main_messages[guild_id][channel_id])
            await msg.edit(embed=embed)
        except discord.NotFound:
            msg = await channel.send(embed=embed)
            main_messages[guild_id][channel_id] = msg.id


# ---------- ЗАПИСЬ ----------
async def handle_join(interaction: discord.Interaction, guild_id: int, channel_id: int, event_id: int):
    guild_events = events.get(guild_id, {})
    channel_events = guild_events.get(channel_id, {})
    event = channel_events.get(event_id)

    if not event:
        return await interaction.response.send_message("Мероприятие не найдено.", ephemeral=True)

    if event["closed"]:
        return await interaction.response.send_message("Мероприятие закрыто.", ephemeral=True)

    if interaction.user.id in event["users"]:
        return await interaction.response.send_message("Ты уже записан!", ephemeral=True)

    if len(event["users"]) >= event["max"]:
        return await interaction.response.send_message("Мест больше нет!", ephemeral=True)

    now_msk = datetime.now(MSK)
    event["users"][interaction.user.id] = now_msk.strftime("%H:%M:%S")

    msg_id = event_messages[guild_id][channel_id][event_id]
    msg = await interaction.channel.fetch_message(msg_id)
    await msg.edit(embed=make_event_embed(event), view=EventView(guild_id, channel_id, event_id))

    await update_list(guild_id, interaction.channel)

    await interaction.response.send_message("Ты записан!", ephemeral=True)


# ---------- АВТО‑ЗАКРЫТИЕ ----------
@tasks.loop(seconds=10)
async def auto_close_events():
    now = datetime.now(MSK)

    for guild_id, channels in events.items():
        for channel_id, channel_events in channels.items():
            channel = bot.get_channel(channel_id)
            if not channel:
                continue

            changed = False

            for event_id, event in channel_events.items():
                if not event["closed"] and now >= event["close_time"]:
                    event["closed"] = True
                    changed = True

                    msg_id = event_messages[guild_id][channel_id][event_id]
                    try:
                        msg = await channel.fetch_message(msg_id)
                        await msg.edit(embed=make_event_embed(event), view=None)
                    except:
                        pass

            if changed:
                await update_list(guild_id, channel)


# ---------- КОМАНДЫ ----------
@bot.tree.command(name="addeventadmin", description="Добавить администратора мероприятий")
async def add_event_admin(interaction: discord.Interaction, user: discord.Member):
    if interaction.user.id != MAIN_ADMIN:
        return await interaction.response.send_message("Ты не главный админ.", ephemeral=True)

    event_admins.add(user.id)
    await interaction.response.send_message(f"{user.mention} теперь администратор мероприятий.", ephemeral=True)


@bot.tree.command(name="event_create", description="Создать мероприятие")
async def event_create(interaction: discord.Interaction, name: str, max_people: int, close_at: str, image: discord.Attachment | None = None):
    if interaction.user.id not in event_admins:
        return await interaction.response.send_message("Нет прав.", ephemeral=True)

    try:
        close_dt = datetime.strptime(close_at, "%Y-%m-%d %H:%M").replace(tzinfo=MSK)
    except:
        return await interaction.response.send_message("Формат даты: YYYY-MM-DD HH:MM", ephemeral=True)

    guild_id = interaction.guild.id
    channel = interaction.channel
    channel_id = channel.id

    events.setdefault(guild_id, {})
    events[guild_id].setdefault(channel_id, {})

    event_id = max(events[guild_id][channel_id].keys(), default=0) + 1

    events[guild_id][channel_id][event_id] = {
        "name": name,
        "max": max_people,
        "users": {},
        "close_time": close_dt,
        "image_url": image.url if image else None,
        "closed": False,
    }

    event_messages.setdefault(guild_id, {})
    event_messages[guild_id].setdefault(channel_id, {})

    msg = await channel.send(
        embed=make_event_embed(events[guild_id][channel_id][event_id]),
        view=EventView(guild_id, channel_id, event_id)
    )
    event_messages[guild_id][channel_id][event_id] = msg.id

    await update_list(guild_id, channel)

    await interaction.response.send_message(f"Мероприятие #{event_id} создано.", ephemeral=True)


@bot.tree.command(name="event_edit", description="Редактировать мероприятие")
async def event_edit(interaction: discord.Interaction, event_id: int, name: str | None = None, max_people: int | None = None, close_at: str | None = None, image: discord.Attachment | None = None):
    if interaction.user.id not in event_admins:
        return await interaction.response.send_message("Нет прав.", ephemeral=True)

    guild_id = interaction.guild.id
    channel = interaction.channel
    channel_id = channel.id

    if guild_id not in events or channel_id not in events[guild_id] or event_id not in events[guild_id][channel_id]:
        return await interaction.response.send_message("Не найдено.", ephemeral=True)

    event = events[guild_id][channel_id][event_id]

    if name:
        event["name"] = name
    if max_people:
        event["max"] = max_people
    if close_at:
        try:
            event["close_time"] = datetime.strptime(close_at, "%Y-%m-%d %H:%M").replace(tzinfo=MSK)
        except:
            return await interaction.response.send_message("Формат даты: YYYY-MM-DD HH:MM", ephemeral=True)
    if image:
        event["image_url"] = image.url

    msg_id = event_messages[guild_id][channel_id][event_id]
    msg = await channel.fetch_message(msg_id)
    await msg.edit(embed=make_event_embed(event), view=EventView(guild_id, channel_id, event_id))

    await update_list(guild_id, channel)

    await interaction.response.send_message("Обновлено.", ephemeral=True)


@bot.tree.command(name="event_clear", description="Очистить участников")
async def event_clear(interaction: discord.Interaction, event_id: int):
    if interaction.user.id not in event_admins:
        return await interaction.response.send_message("Нет прав.", ephemeral=True)

    guild_id = interaction.guild.id
    channel = interaction.channel
    channel_id = channel.id

    if guild_id not in events or channel_id not in events[guild_id] or event_id not in events[guild_id][channel_id]:
        return await interaction.response.send_message("Не найдено.", ephemeral=True)

    events[guild_id][channel_id][event_id]["users"] = {}

    msg_id = event_messages[guild_id][channel_id][event_id]
    msg = await channel.fetch_message(msg_id)
    await msg.edit(embed=make_event_embed(events[guild_id][channel_id][event_id]),
                   view=EventView(guild_id, channel_id, event_id))

    await update_list(guild_id, channel)

    await interaction.response.send_message("Участники очищены.", ephemeral=True)


@bot.tree.command(name="event_delete", description="Удалить мероприятие")
async def event_delete(interaction: discord.Interaction, event_id: int):
    if interaction.user.id not in event_admins:
        return await interaction.response.send_message("Нет прав.", ephemeral=True)

    guild_id = interaction.guild.id
    channel = interaction.channel
    channel_id = channel.id

    if guild_id not in events or channel_id not in events[guild_id] or event_id not in events[guild_id][channel_id]:
        return await interaction.response.send_message("Не найдено.", ephemeral=True)

    msg_id = event_messages[guild_id][channel_id][event_id]
    try:
        msg = await channel.fetch_message(msg_id)
        await msg.delete()
    except:
        pass

    del events[guild_id][channel_id][event_id]
    del event_messages[guild_id][channel_id][event_id]

    await update_list(guild_id, channel)
    await interaction.response.send_message("Удалено.", ephemeral=True)


# ---------- ON_READY ----------
@bot.event
async def on_ready():
    await bot.tree.sync()
    auto_close_events.start()
    print(f"Бот запущен как {bot.user}")


bot.run(TOKEN)
