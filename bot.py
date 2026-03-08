import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, time as dtime, timedelta, timezone

# ---------- НАСТРОЙКИ ----------
ADMIN_IDS = {
    1072968512076787744,
    770549354783571978,
    392978988877873162,
}

TOKEN = os.getenv("TOKEN")
MSK = timezone(timedelta(hours=3))

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Храним мероприятия по каналам
events_by_channel = {}      # {channel_id: {event_id: event_data}}
event_messages = {}         # {event_id: message_id}
main_messages = {}          # {channel_id: message_id}


# ---------- EMBED МЕРОПРИЯТИЯ ----------
def make_event_embed(event: dict) -> discord.Embed:
    users_text = "\n".join(
        [f"{i+1}. <@{uid}> — {t}" for i, (uid, t) in enumerate(event["users"].items())]
    ) or "Пока пусто"

    status = "Открыто" if not event["closed"] else "Закрыто"

    embed = discord.Embed(
        title=f"{event['name']}",
        description=(
            f"Статус: **{status}**\n"
            f"Мест: {len(event['users'])}/{event['max']}\n"
            f"Закрытие по МСК: {event['close_time'].strftime('%H:%M')}\n\n"
            f"**Участники:**\n{users_text}"
        ),
        color=0x2f3136 if not event["closed"] else 0x555555,
    )

    if event.get("image_url"):
        embed.set_image(url=event["image_url"])

    return embed


# ---------- EMBED СПИСКА ----------
def make_list_embed(events: dict) -> discord.Embed:
    if not events:
        desc = "Пока нет мероприятий"
    else:
        desc = "\n".join(
            [f"**#{eid}** — {ev['name']} ({len(ev['users'])}/{ev['max']})"
             for eid, ev in events.items()]
        )

    embed = discord.Embed(
        title="Список мероприятий",
        description=desc,
        color=0x2f3136
    )
    return embed


# ---------- VIEW ----------
class EventView(discord.ui.View):
    def __init__(self, channel_id, event_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.event_id = event_id

    @discord.ui.button(label="Записаться", style=discord.ButtonStyle.primary)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_join(interaction, self.channel_id, self.event_id)


# ---------- ОБНОВЛЕНИЕ СПИСКА ----------
async def update_list(channel):
    channel_id = channel.id
    events = events_by_channel.get(channel_id, {})

    embed = make_list_embed(events)

    if channel_id not in main_messages:
        msg = await channel.send(embed=embed)
        main_messages[channel_id] = msg.id
    else:
        try:
            msg = await channel.fetch_message(main_messages[channel_id])
            await msg.edit(embed=embed)
        except discord.NotFound:
            msg = await channel.send(embed=embed)
            main_messages[channel_id] = msg.id


# ---------- ЗАПИСЬ ----------
async def handle_join(interaction: discord.Interaction, channel_id: int, event_id: int):
    events = events_by_channel.get(channel_id, {})
    event = events.get(event_id)

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

    # обновляем embed мероприятия
    msg = await interaction.channel.fetch_message(event_messages[event_id])
    await msg.edit(embed=make_event_embed(event), view=EventView(channel_id, event_id))

    # обновляем список
    await update_list(interaction.channel)

    await interaction.response.send_message("Ты записан!", ephemeral=True)


# ---------- АВТО‑ЗАКРЫТИЕ ----------
@tasks.loop(seconds=10)
async def auto_close_events():
    now = datetime.now(MSK)

    for channel_id, events in events_by_channel.items():
        channel = bot.get_channel(channel_id)
        if not channel:
            continue

        changed = False

        for event_id, event in events.items():
            if not event["closed"] and now >= event["close_time"]:
                event["closed"] = True
                changed = True

                # обновляем embed мероприятия
                try:
                    msg = await channel.fetch_message(event_messages[event_id])
                    await msg.edit(embed=make_event_embed(event), view=None)
                except:
                    pass

        if changed:
            await update_list(channel)


# ---------- КОМАНДЫ ----------
@bot.tree.command(name="event_create", description="Создать мероприятие")
async def event_create(interaction: discord.Interaction, name: str, max_people: int, close_at: str, image: discord.Attachment | None = None):
    if interaction.user.id not in ADMIN_IDS:
        return await interaction.response.send_message("Нет прав.", ephemeral=True)

    try:
        hh, mm = map(int, close_at.split(":"))
        today = datetime.now(MSK).date()
        close_dt = datetime.combine(today, dtime(hour=hh, minute=mm, tzinfo=MSK))
    except:
        return await interaction.response.send_message("Формат HH:MM", ephemeral=True)

    channel = interaction.channel
    channel_id = channel.id

    events_by_channel.setdefault(channel_id, {})
    event_id = max(events_by_channel[channel_id].keys(), default=0) + 1

    events_by_channel[channel_id][event_id] = {
        "name": name,
        "max": max_people,
        "users": {},
        "close_time": close_dt,
        "image_url": image.url if image else None,
        "closed": False,
    }

    # отправляем embed мероприятия
    msg = await channel.send(
        embed=make_event_embed(events_by_channel[channel_id][event_id]),
        view=EventView(channel_id, event_id)
    )
    event_messages[event_id] = msg.id

    # обновляем список
    await update_list(channel)

    await interaction.response.send_message(f"Мероприятие #{event_id} создано.", ephemeral=True)


@bot.tree.command(name="event_delete", description="Удалить мероприятие")
async def event_delete(interaction: discord.Interaction, event_id: int):
    if interaction.user.id not in ADMIN_IDS:
        return await interaction.response.send_message("Нет прав.", ephemeral=True)

    channel = interaction.channel
    channel_id = channel.id

    events = events_by_channel.get(channel_id, {})
    if event_id not in events:
        return await interaction.response.send_message("Не найдено.", ephemeral=True)

    # удаляем сообщение мероприятия
    try:
        msg = await channel.fetch_message(event_messages[event_id])
        await msg.delete()
    except:
        pass

    del events[event_id]

    await update_list(channel)
    await interaction.response.send_message("Удалено.", ephemeral=True)


# ---------- ON_READY ----------
@bot.event
async def on_ready():
    await bot.tree.sync()
    auto_close_events.start()
    print(f"Бот запущен как {bot.user}")


bot.run(TOKEN)
