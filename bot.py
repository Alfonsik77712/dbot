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

events = {}
MAIN_MESSAGE_ID = None
MAIN_CHANNEL = None  # теперь канал определяется автоматически


# ---------- EMBED ----------
def make_event_embed(event_id: int, event: dict) -> discord.Embed:
    users_text = "\n".join(
        [f"{i+1}. <@{uid}> — {t}" for i, (uid, t) in enumerate(event["users"].items())]
    ) or "Пока пусто"

    status = "Открыто" if not event["closed"] else "Закрыто"

    embed = discord.Embed(
        title=f"#{event_id} — {event['name']}",
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


# ---------- VIEW ----------
class EventsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.build_buttons()

    def build_buttons(self):
        self.clear_items()
        for event_id, event in events.items():
            if event["closed"]:
                continue

            button = discord.ui.Button(
                label=f"Записаться #{event_id}",
                style=discord.ButtonStyle.primary,
                custom_id=f"join_{event_id}",
            )

            async def callback(interaction, eid=event_id):
                await handle_join(interaction, eid)

            button.callback = callback
            self.add_item(button)


# ---------- ОБНОВЛЕНИЕ ГЛАВНОГО СООБЩЕНИЯ ----------
async def update_main_message(interaction=None):
    global MAIN_MESSAGE_ID, MAIN_CHANNEL

    # если команда вызвана — обновляем канал
    if interaction is not None:
        MAIN_CHANNEL = interaction.channel

    # если бот перезапустился — MAIN_CHANNEL может быть None
    if MAIN_CHANNEL is None:
        return  # ждём первой команды

    embeds = [make_event_embed(eid, ev) for eid, ev in sorted(events.items())]
    view = EventsView() if any(not e["closed"] for e in events.values()) else None

    if MAIN_MESSAGE_ID is None:
        msg = await MAIN_CHANNEL.send("**Список мероприятий:**", embeds=embeds, view=view)
        MAIN_MESSAGE_ID = msg.id
    else:
        try:
            msg = await MAIN_CHANNEL.fetch_message(MAIN_MESSAGE_ID)
            await msg.edit(content="**Список мероприятий:**", embeds=embeds, view=view)
        except discord.NotFound:
            msg = await MAIN_CHANNEL.send("**Список мероприятий:**", embeds=embeds, view=view)
            MAIN_MESSAGE_ID = msg.id


# ---------- ЗАПИСЬ ----------
async def handle_join(interaction: discord.Interaction, event_id: int):
    event = events.get(event_id)
    if not event or event["closed"]:
        return await interaction.response.send_message("Это мероприятие закрыто.", ephemeral=True)

    if interaction.user.id in event["users"]:
        return await interaction.response.send_message("Ты уже записан!", ephemeral=True)

    if len(event["users"]) >= event["max"]:
        return await interaction.response.send_message("Мест больше нет!", ephemeral=True)

    now_msk = datetime.now(MSK)
    event["users"][interaction.user.id] = now_msk.strftime("%H:%M:%S")

    await update_main_message(interaction)
    await interaction.response.send_message("Ты записан!", ephemeral=True)


# ---------- АВТО‑ЗАКРЫТИЕ ----------
@tasks.loop(seconds=10)
async def auto_close_events():
    now = datetime.now(MSK)
    changed = False

    for event in events.values():
        if not event["closed"] and now >= event["close_time"]:
            event["closed"] = True
            changed = True

    if changed:
        await update_main_message()


# ---------- КОМАНДЫ ----------
@bot.tree.command(name="event_create", description="Создать мероприятие")
@app_commands.describe(
    name="Название",
    max_people="Максимум участников",
    close_at="Время закрытия по МСК (HH:MM)",
    image="Картинка"
)
async def event_create(interaction: discord.Interaction, name: str, max_people: int, close_at: str, image: discord.Attachment | None = None):
    if interaction.user.id not in ADMIN_IDS:
        return await interaction.response.send_message("Нет прав.", ephemeral=True)

    try:
        hh, mm = map(int, close_at.split(":"))
        today = datetime.now(MSK).date()
        close_dt = datetime.combine(today, dtime(hour=hh, minute=mm, tzinfo=MSK))
    except:
        return await interaction.response.send_message("Формат HH:MM", ephemeral=True)

    event_id = max(events.keys(), default=0) + 1

    events[event_id] = {
        "name": name,
        "max": max_people,
        "users": {},
        "close_time": close_dt,
        "image_url": image.url if image else None,
        "closed": False,
    }

    await update_main_message(interaction)
    await interaction.response.send_message(f"Мероприятие #{event_id} создано.", ephemeral=True)


@bot.tree.command(name="event_edit", description="Редактировать мероприятие")
@app_commands.describe(
    event_id="ID мероприятия",
    name="Новое название",
    max_people="Новый лимит",
    close_at="Новое время закрытия (HH:MM)",
    image="Новая картинка"
)
async def event_edit(interaction: discord.Interaction, event_id: int, name: str | None = None, max_people: int | None = None, close_at: str | None = None, image: discord.Attachment | None = None):
    if interaction.user.id not in ADMIN_IDS:
        return await interaction.response.send_message("Нет прав.", ephemeral=True)

    event = events.get(event_id)
    if not event:
        return await interaction.response.send_message("Не найдено.", ephemeral=True)

    if name:
        event["name"] = name
    if max_people:
        event["max"] = max_people
    if close_at:
        try:
            hh, mm = map(int, close_at.split(":"))
            today = datetime.now(MSK).date()
            event["close_time"] = datetime.combine(today, dtime(hour=hh, minute=mm, tzinfo=MSK))
        except:
            return await interaction.response.send_message("Формат HH:MM", ephemeral=True)
    if image:
        event["image_url"] = image.url

    await update_main_message(interaction)
    await interaction.response.send_message(f"Мероприятие #{event_id} обновлено.", ephemeral=True)


@bot.tree.command(name="event_clear", description="Очистить участников")
async def event_clear(interaction: discord.Interaction, event_id: int):
    if interaction.user.id not in ADMIN_IDS:
        return await interaction.response.send_message("Нет прав.", ephemeral=True)

    event = events.get(event_id)
    if not event:
        return await interaction.response.send_message("Не найдено.", ephemeral=True)

    event["users"] = {}
    await update_main_message(interaction)
    await interaction.response.send_message("Очищено.", ephemeral=True)


@bot.tree.command(name="event_delete", description="Удалить мероприятие")
async def event_delete(interaction: discord.Interaction, event_id: int):
    if interaction.user.id not in ADMIN_IDS:
        return await interaction.response.send_message("Нет прав.", ephemeral=True)

    if event_id not in events:
        return await interaction.response.send_message("Не найдено.", ephemeral=True)

    del events[event_id]
    await update_main_message(interaction)
    await interaction.response.send_message("Удалено.", ephemeral=True)


# ---------- ON_READY ----------
@bot.event
async def on_ready():
    await bot.tree.sync()
    auto_close_events.start()
    print(f"Бот запущен как {bot.user}")


bot.run(TOKEN)
