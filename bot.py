import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import asyncio
import random
from typing import List

import poker


# ============================================================
#                       НАСТРОЙКИ
# ============================================================
TOKEN = "ТВОЙ_ТОКЕН_ЗДЕСЬ"              # Токен бота
GUILD_ID = 123456789012345678            # ID сервера (или None)
DATA_FILE = "players.json"
LOG_FILE = "balance_logs.json"

TAX_PERCENT = 5                          # Ежедневное списание
TAX_INTERVAL_HOURS = 24
TAX_LOG_CHANNEL_ID = None

TRANSFER_FEE_PERCENT = 20                # Комиссия за перевод
GAME_FEE_PERCENT = 5                     # Комиссия за игры
GAME_MIN_PLAYERS = 2


# ============================================================
#                    ИНИЦИАЛИЗАЦИЯ
# ============================================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ============================================================
#                  РАБОТА С ФАЙЛАМИ
# ============================================================
def load_data():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=4)
        return []

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    changed = False
    for player in data:
        if "balance" not in player:
            player["balance"] = 0
            changed = True

    if changed:
        save_data(data)

    return data


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


# ============================================================
#                  РАБОТА С ЛОГАМИ
# ============================================================
def load_logs():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=4)
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_logs(logs):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=4)


def log_balance(nickname: str, steam_id: str, action: str,
                amount: int, before: int, after: int,
                source: str = "system", reason: str = ""):
    logs = load_logs()
    logs.append({
        "timestamp": discord.utils.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "nickname": nickname,
        "steam_id": steam_id,
        "action": action,
        "amount": amount,
        "balance_before": before,
        "balance_after": after,
        "source": source,
        "reason": reason
    })
    save_logs(logs)


# ============================================================
#                       ХЕЛПЕРЫ
# ============================================================
def find_player(data, query: str):
    for p in data:
        if p["nickname"].lower() == query.lower() or p["steam_id"] == query:
            return p
    return None


async def player_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> List[app_commands.Choice[str]]:
    data = load_data()
    current_lower = current.lower()
    matches = [
        p["nickname"] for p in data
        if current_lower in p["nickname"].lower()
    ][:25]
    return [app_commands.Choice(name=n, value=n) for n in matches]


# ============================================================
#                  ХРАНИЛИЩЕ ИГР (КУБИК)
# ============================================================
active_games = {}


def get_guild_games(guild_id: int):
    if guild_id not in active_games:
        active_games[guild_id] = {}
    return active_games[guild_id]


def find_game_by_player(guild_id: int, user_id: int):
    games = get_guild_games(guild_id)
    for host_id, game in games.items():
        if user_id in game["players"]:
            return host_id, game
    return None, None


# ============================================================
#                       СОБЫТИЯ
# ============================================================
@bot.event
async def on_ready():
    load_data()
    load_logs()
    print(f"✅ Бот запущен как {bot.user}")

    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            tree.copy_global_to(guild=guild)
            synced = await tree.sync(guild=guild)
            print(f"⚡ Синхронизировано команд: {len(synced)}")
        else:
            synced = await tree.sync()
            print(f"🌐 Синхронизировано глобальных команд: {len(synced)}")
    except Exception as e:
        print(f"❌ Ошибка синхронизации: {e}")

    if not daily_tax.is_running():
        daily_tax.start()
        print(f"💰 [TAX] Автосписание {TAX_PERCENT}% запущено")

    await bot.change_presence(activity=discord.Game(name="/econom_help"))


# ============================================================
#           ЕЖЕДНЕВНОЕ СПИСАНИЕ
# ============================================================
@tasks.loop(hours=TAX_INTERVAL_HOURS)
async def daily_tax():
    data = load_data()

    if not data:
        print("💰 [TAX] База пуста — списание пропущено.")
        return

    total_taken = 0
    affected = 0

    for player in data:
        balance = player.get("balance", 0)
        if balance <= 0:
            continue

        tax = int(balance * TAX_PERCENT / 100)
        if tax < 1:
            continue

        new_balance = balance - tax
        player["balance"] = new_balance
        total_taken += tax
        affected += 1

        log_balance(
            nickname=player["nickname"],
            steam_id=player["steam_id"],
            action="tax",
            amount=-tax,
            before=balance,
            after=new_balance,
            source="daily_tax",
            reason=f"Ежедневное списание {TAX_PERCENT}%"
        )

    save_data(data)

    print(f"💰 [TAX] Списано {TAX_PERCENT}% у {affected} игроков. "
          f"Всего собрано: {total_taken}")

    if TAX_LOG_CHANNEL_ID:
        channel = bot.get_channel(TAX_LOG_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title=f"💰 Ежедневное списание {TAX_PERCENT}%",
                color=discord.Color.dark_red()
            )
            embed.add_field(name="Затронуто игроков", value=str(affected), inline=True)
            embed.add_field(name="Всего списано", value=f"`{total_taken}`", inline=True)
            embed.set_footer(text=discord.utils.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
            try:
                await channel.send(embed=embed)
            except Exception as e:
                print(f"⚠️ [TAX] Не удалось отправить лог в канал: {e}")


@daily_tax.before_loop
async def before_daily_tax():
    await bot.wait_until_ready()


# ============================================================
#                      СЛЭШ-КОМАНДЫ
# ============================================================

# ------------------------- HELP -----------------------------
@tree.command(name="econom_help", description="Показать список всех команд бота")
async def econom_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Список команд бота",
        description="Все доступные команды для управления базой игроков",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="👥 Управление игроками",
        value=(
            "`/econom_add` — добавить игрока *(админ)*\n"
            "`/econom_remove` — удалить игрока *(админ)*\n"
            "`/econom_list` — список игроков\n"
            "`/econom_find` — найти игрока\n"
            "`/econom_clear` — очистить базу *(админ)*\n"
            "`/econom_export` — скачать файл базы *(админ)*"
        ),
        inline=False
    )

    embed.add_field(
        name="💰 Баланс",
        value=(
            "`/econom_give` — начислить средства *(админ)*\n"
            "`/econom_take` — списать средства *(админ)*\n"
            f"`/econom_transfer` — перевод игроку (комиссия {TRANSFER_FEE_PERCENT}%)\n"
            "`/econom_balances` — список всех балансов *(админ)*"
        ),
        inline=False
    )

    embed.add_field(
        name="🎲 Кубик",
        value=(
            f"`/game_dice` — создать игру (комиссия {GAME_FEE_PERCENT}%)\n"
            "`/game_join` — присоединиться\n"
            "`/game_start` — запустить\n"
            "`/game_cancel` — отменить с возвратом ставок"
        ),
        inline=False
    )

    embed.add_field(
        name=f"🃏 Покер ({poker.POKER_MIN_PLAYERS}-{poker.POKER_MAX_PLAYERS} игроков)",
        value=(
            f"`/poker_create` — создать стол (стек = bet×{poker.POKER_STARTING_STACK_MULT})\n"
            "`/poker_join` — сесть за стол\n"
            "`/poker_start` — начать раздачу\n"
            "`/poker_next` — следующая раздача\n"
            "`/poker_cancel` — отменить стол\n"
            f"*Правила: Техасский Холдем, комиссия {poker.POKER_FEE_PERCENT}%*"
        ),
        inline=False
    )

    embed.add_field(
        name="ℹ️ Прочее",
        value="`/econom_help` — это сообщение",
        inline=False
    )

    embed.set_footer(
        text=f"Игроков в базе: {len(load_data())} | "
             f"Ежедневное списание: {TAX_PERCENT}%"
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ------------------------- ADD ------------------------------
@tree.command(name="econom_add", description="Добавить игрока в базу")
@app_commands.describe(
    nickname="Ник игрока",
    steam_id="Steam ID игрока"
)
@app_commands.default_permissions(administrator=True)
async def econom_add(interaction: discord.Interaction, nickname: str, steam_id: str):
    await interaction.response.defer()

    data = load_data()

    for player in data:
        if player["steam_id"] == steam_id:
            await interaction.followup.send(
                f"⚠️ Игрок со Steam ID `{steam_id}` уже есть в базе "
                f"(ник: **{player['nickname']}**)."
            )
            return

    for player in data:
        if player["nickname"].lower() == nickname.lower():
            await interaction.followup.send(f"⚠️ Ник **{nickname}** уже занят.")
            return

    data.append({
        "nickname": nickname,
        "steam_id": steam_id,
        "balance": 0,
        "added_by": str(interaction.user),
        "added_at": discord.utils.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    })
    save_data(data)

    log_balance(
        nickname=nickname,
        steam_id=steam_id,
        action="add",
        amount=0,
        before=0,
        after=0,
        source=str(interaction.user),
        reason="Игрок добавлен в базу"
    )

    embed = discord.Embed(
        title="✅ Игрок добавлен",
        color=discord.Color.green()
    )
    embed.add_field(name="Ник", value=nickname, inline=True)
    embed.add_field(name="Steam ID", value=f"`{steam_id}`", inline=True)
    embed.add_field(name="Баланс", value="💰 `0`", inline=True)
    embed.set_footer(text=f"Всего в базе: {len(data)}")

    await interaction.followup.send(embed=embed)


# ----------------------- REMOVE -----------------------------
@tree.command(name="econom_remove", description="Удалить игрока из базы")
@app_commands.describe(query="Ник или Steam ID игрока")
@app_commands.autocomplete(query=player_autocomplete)
@app_commands.default_permissions(administrator=True)
async def econom_remove(interaction: discord.Interaction, query: str):
    await interaction.response.defer()

    data = load_data()
    new_data = [p for p in data
                if p["nickname"].lower() != query.lower()
                and p["steam_id"] != query]

    if len(new_data) == len(data):
        await interaction.followup.send(f"❌ Игрок `{query}` не найден.")
        return

    save_data(new_data)
    await interaction.followup.send(
        f"🗑️ Игрок `{query}` удалён. Осталось в базе: **{len(new_data)}**"
    )


# ------------------------ LIST ------------------------------
@tree.command(name="econom_list", description="Показать всех игроков в базе")
async def econom_list(interaction: discord.Interaction):
    data = load_data()

    if not data:
        await interaction.response.send_message("📭 База игроков пуста.", ephemeral=True)
        return

    lines = []
    for i, p in enumerate(data, 1):
        lines.append(f"`{i}.` **{p['nickname']}** — `{p['steam_id']}`")

    chunks = [lines[i:i+20] for i in range(0, len(lines), 20)]

    embeds = []
    for idx, chunk in enumerate(chunks, 1):
        embed = discord.Embed(
            title=f"📋 База игроков (стр. {idx}/{len(chunks)})",
            description="\n".join(chunk),
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Всего игроков: {len(data)}")
        embeds.append(embed)

    await interaction.response.send_message(embeds=embeds)


# ------------------------ FIND ------------------------------
@tree.command(name="econom_find", description="Найти игрока по нику или Steam ID")
@app_commands.describe(query="Ник или Steam ID (можно частично)")
async def econom_find(interaction: discord.Interaction, query: str):
    data = load_data()
    results = [p for p in data
               if query.lower() in p["nickname"].lower()
               or query in p["steam_id"]]

    if not results:
        await interaction.response.send_message(
            f"❌ Ничего не найдено по запросу `{query}`.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"🔍 Найдено: {len(results)}",
        color=discord.Color.gold()
    )
    for p in results[:20]:
        embed.add_field(
            name=p["nickname"],
            value=f"`{p['steam_id']}`",
            inline=False
        )

    await interaction.response.send_message(embed=embed)


# ------------------------ CLEAR -----------------------------
@tree.command(name="econom_clear", description="Очистить всю базу игроков")
@app_commands.default_permissions(administrator=True)
async def econom_clear(interaction: discord.Interaction):
    data = load_data()
    count = len(data)

    for player in data:
        log_balance(
            nickname=player["nickname"],
            steam_id=player["steam_id"],
            action="clear",
            amount=-player.get("balance", 0),
            before=player.get("balance", 0),
            after=0,
            source=str(interaction.user),
            reason="Полная очистка базы"
        )

    save_data([])
    await interaction.response.send_message(
        f"🧹 База игроков очищена. Удалено записей: **{count}**"
    )


# ------------------------ EXPORT ----------------------------
@tree.command(name="econom_export", description="Скачать файл базы игроков")
@app_commands.default_permissions(administrator=True)
async def econom_export(interaction: discord.Interaction):
    if not os.path.exists(DATA_FILE):
        await interaction.response.send_message("❌ Файл ещё не создан.", ephemeral=True)
        return

    await interaction.response.send_message(
        "📤 Файл с базой игроков:",
        file=discord.File(DATA_FILE),
        ephemeral=True
    )


# ------------------------ GIVE ------------------------------
@tree.command(name="econom_give", description="Начислить средства игроку")
@app_commands.describe(
    player="Ник игрока",
    amount="Сколько начислить"
)
@app_commands.autocomplete(player=player_autocomplete)
@app_commands.default_permissions(administrator=True)
async def econom_give(interaction: discord.Interaction, player: str, amount: int):
    await interaction.response.defer()

    if amount <= 0:
        await interaction.followup.send("❌ Сумма должна быть больше 0.")
        return

    data = load_data()
    target = find_player(data, player)

    if not target:
        await interaction.followup.send(f"❌ Игрок `{player}` не найден.")
        return

    before = target.get("balance", 0)
    after = before + amount
    target["balance"] = after
    save_data(data)

    log_balance(
        nickname=target["nickname"],
        steam_id=target["steam_id"],
        action="give",
        amount=amount,
        before=before,
        after=after,
        source=str(interaction.user),
        reason="Ручное начисление администратором"
    )

    embed = discord.Embed(title="💵 Средства начислены", color=discord.Color.green())
    embed.add_field(name="Игрок", value=target["nickname"], inline=True)
    embed.add_field(name="Начислено", value=f"`+{amount}`", inline=True)
    embed.add_field(name="Новый баланс", value=f"`{after}`", inline=True)
    embed.set_footer(text=f"Администратор: {interaction.user}")

    await interaction.followup.send(embed=embed)


# ------------------------ TAKE ------------------------------
@tree.command(name="econom_take", description="Списать средства у игрока")
@app_commands.describe(
    player="Ник игрока",
    amount="Сколько списать"
)
@app_commands.autocomplete(player=player_autocomplete)
@app_commands.default_permissions(administrator=True)
async def econom_take(interaction: discord.Interaction, player: str, amount: int):
    await interaction.response.defer()

    if amount <= 0:
        await interaction.followup.send("❌ Сумма должна быть больше 0.")
        return

    data = load_data()
    target = find_player(data, player)

    if not target:
        await interaction.followup.send(f"❌ Игрок `{player}` не найден.")
        return

    before = target.get("balance", 0)

    if before < amount:
        await interaction.followup.send(
            f"❌ У игрока **{target['nickname']}** недостаточно средств.\n"
            f"Текущий баланс: `{before}`, запрошено: `{amount}`"
        )
        return

    after = before - amount
    target["balance"] = after
    save_data(data)

    log_balance(
        nickname=target["nickname"],
        steam_id=target["steam_id"],
        action="take",
        amount=-amount,
        before=before,
        after=after,
        source=str(interaction.user),
        reason="Ручное списание администратором"
    )

    embed = discord.Embed(title="💸 Средства списаны", color=discord.Color.orange())
    embed.add_field(name="Игрок", value=target["nickname"], inline=True)
    embed.add_field(name="Списано", value=f"`-{amount}`", inline=True)
    embed.add_field(name="Новый баланс", value=f"`{after}`", inline=True)
    embed.set_footer(text=f"Администратор: {interaction.user}")

    await interaction.followup.send(embed=embed)


# --------------------- TRANSFER -----------------------------
@tree.command(
    name="econom_transfer",
    description=f"Перевести средства другому игроку (комиссия {TRANSFER_FEE_PERCENT}%)"
)
@app_commands.describe(
    sender="Ник отправителя",
    receiver="Ник получателя",
    amount="Сумма перевода"
)
@app_commands.autocomplete(sender=player_autocomplete, receiver=player_autocomplete)
async def econom_transfer(interaction: discord.Interaction,
                          sender: str, receiver: str, amount: int):
    await interaction.response.defer()

    if amount <= 0:
        await interaction.followup.send("❌ Сумма должна быть больше 0.")
        return

    if sender.lower() == receiver.lower():
        await interaction.followup.send("❌ Нельзя перевести средства самому себе.")
        return

    data = load_data()
    src = find_player(data, sender)
    dst = find_player(data, receiver)

    if not src:
        await interaction.followup.send(f"❌ Отправитель `{sender}` не найден.")
        return
    if not dst:
        await interaction.followup.send(f"❌ Получатель `{receiver}` не найден.")
        return

    src_before = src.get("balance", 0)

    if src_before < amount:
        await interaction.followup.send(
            f"❌ У игрока **{src['nickname']}** недостаточно средств.\n"
            f"Баланс: `{src_before}`, требуется: `{amount}`"
        )
        return

    fee = int(amount * TRANSFER_FEE_PERCENT / 100)
    received = amount - fee

    if received < 1:
        await interaction.followup.send(
            f"❌ Слишком маленькая сумма — после комиссии {TRANSFER_FEE_PERCENT}% "
            f"получатель не получит ничего."
        )
        return

    src_after = src_before - amount
    dst_before = dst.get("balance", 0)
    dst_after = dst_before + received

    src["balance"] = src_after
    dst["balance"] = dst_after
    save_data(data)

    log_balance(
        nickname=src["nickname"],
        steam_id=src["steam_id"],
        action="transfer_out",
        amount=-amount,
        before=src_before,
        after=src_after,
        source=str(interaction.user),
        reason=f"Перевод игроку {dst['nickname']} (комиссия {fee})"
    )
    log_balance(
        nickname=dst["nickname"],
        steam_id=dst["steam_id"],
        action="transfer_in",
        amount=received,
        before=dst_before,
        after=dst_after,
        source=str(interaction.user),
        reason=f"Перевод от игрока {src['nickname']} (комиссия {fee})"
    )

    embed = discord.Embed(
        title="🔁 Перевод выполнен",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="Отправитель",
        value=f"**{src['nickname']}**\n`{src_before}` → `{src_after}`",
        inline=True
    )
    embed.add_field(
        name="Получатель",
        value=f"**{dst['nickname']}**\n`{dst_before}` → `{dst_after}`",
        inline=True
    )
    embed.add_field(name="Сумма", value=f"`{amount}`", inline=True)
    embed.add_field(name=f"Комиссия ({TRANSFER_FEE_PERCENT}%)", value=f"`{fee}` 🔥", inline=True)
    embed.add_field(name="Зачислено", value=f"`{received}`", inline=True)

    await interaction.followup.send(embed=embed)


# --------------------- BALANCES -----------------------------
@tree.command(
    name="econom_balances",
    description="Список всех игроков и их балансов (только админ)"
)
@app_commands.default_permissions(administrator=True)
async def econom_balances(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    data = load_data()

    if not data:
        await interaction.followup.send("📭 База игроков пуста.", ephemeral=True)
        return

    sorted_data = sorted(data, key=lambda p: p.get("balance", 0), reverse=True)
    total_balance = sum(p.get("balance", 0) for p in sorted_data)

    per_page = 20
    chunks = [sorted_data[i:i+per_page] for i in range(0, len(sorted_data), per_page)]

    embeds = []
    for idx, chunk in enumerate(chunks, 1):
        lines = []
        for i, p in enumerate(chunk, start=(idx - 1) * per_page + 1):
            lines.append(f"`{i}.` **{p['nickname']}** — 💰 `{p.get('balance', 0)}`")

        embed = discord.Embed(
            title=f"💰 Балансы игроков (стр. {idx}/{len(chunks)})",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        embed.set_footer(
            text=f"Всего игроков: {len(sorted_data)} | Общая сумма: {total_balance}"
        )
        embeds.append(embed)

    await interaction.followup.send(embeds=embeds, ephemeral=True)


# ============================================================
#                      КУБИК
# ============================================================
@tree.command(name="game_dice", description="Создать игру в кубик")
@app_commands.describe(
    nickname="Твой игровой ник (из базы)",
    bet="Размер ставки"
)
@app_commands.autocomplete(nickname=player_autocomplete)
async def game_dice(interaction: discord.Interaction, nickname: str, bet: int):
    await interaction.response.defer()

    if bet <= 0:
        await interaction.followup.send("❌ Ставка должна быть больше 0.")
        return

    data = load_data()
    host = find_player(data, nickname)

    if not host:
        await interaction.followup.send(f"❌ Игрок `{nickname}` не найден в базе.")
        return

    if host.get("balance", 0) < bet:
        await interaction.followup.send(
            f"❌ Недостаточно средств. Баланс: `{host['balance']}`, нужно: `{bet}`"
        )
        return

    guild_id = interaction.guild_id
    games = get_guild_games(guild_id)

    if interaction.user.id in games:
        await interaction.followup.send("❌ У тебя уже есть активная игра.")
        return

    host_id, _ = find_game_by_player(guild_id, interaction.user.id)
    if host_id is not None:
        await interaction.followup.send("❌ Ты уже участвуешь в другой игре.")
        return

    games[interaction.user.id] = {
        "host_id": interaction.user.id,
        "host_nickname": host["nickname"],
        "host_steam_id": host["steam_id"],
        "bet": bet,
        "players": {interaction.user.id: host["nickname"]},
        "started": False
    }

    before = host["balance"]
    host["balance"] = before - bet
    save_data(data)

    log_balance(
        nickname=host["nickname"],
        steam_id=host["steam_id"],
        action="game_bet",
        amount=-bet,
        before=before,
        after=host["balance"],
        source=str(interaction.user),
        reason="Ставка в кубике (создатель)"
    )

    embed = discord.Embed(
        title="🎲 Игра создана!",
        description=(
            f"**Создатель:** {host['nickname']}\n"
            f"**Ставка:** `{bet}` 💰\n"
            f"**Игроков:** 1/{GAME_MIN_PLAYERS} минимум\n\n"
            f"Присоединиться: `/game_join host:@{interaction.user.display_name}`\n"
            f"Запустить: `/game_start`\n"
            f"Отменить: `/game_cancel`"
        ),
        color=discord.Color.green()
    )
    embed.set_footer(text=f"Комиссия с выигрыша: {GAME_FEE_PERCENT}%")

    await interaction.followup.send(embed=embed)


@tree.command(name="game_join", description="Присоединиться к игре в кубик")
@app_commands.describe(
    host="Discord-пользователь — создатель игры",
    nickname="Твой игровой ник (из базы)"
)
@app_commands.autocomplete(nickname=player_autocomplete)
async def game_join(interaction: discord.Interaction,
                    host: discord.User, nickname: str):
    await interaction.response.defer()

    guild_id = interaction.guild_id
    games = get_guild_games(guild_id)

    if host.id not in games:
        await interaction.followup.send(
            f"❌ У пользователя {host.mention} нет активной игры."
        )
        return

    game = games[host.id]

    if game["started"]:
        await interaction.followup.send("❌ Игра уже началась.")
        return

    if interaction.user.id in game["players"]:
        await interaction.followup.send("❌ Ты уже в этой игре.")
        return

    other_host_id, _ = find_game_by_player(guild_id, interaction.user.id)
    if other_host_id is not None:
        await interaction.followup.send(
            "❌ Ты уже участвуешь в другой игре."
        )
        return

    data = load_data()
    player = find_player(data, nickname)

    if not player:
        await interaction.followup.send(f"❌ Игрок `{nickname}` не найден в базе.")
        return

    bet = game["bet"]

    if player.get("balance", 0) < bet:
        await interaction.followup.send(
            f"❌ Недостаточно средств. Баланс: `{player['balance']}`, нужно: `{bet}`"
        )
        return

    before = player["balance"]
    player["balance"] = before - bet
    save_data(data)

    log_balance(
        nickname=player["nickname"],
        steam_id=player["steam_id"],
        action="game_bet",
        amount=-bet,
        before=before,
        after=player["balance"],
        source=str(interaction.user),
        reason=f"Ставка в кубике (участник, хост: {host})"
    )

    game["players"][interaction.user.id] = player["nickname"]

    players_list = "\n".join(f"• **{n}**" for n in game["players"].values())

    embed = discord.Embed(
        title="✅ Игрок присоединился",
        description=(
            f"**{player['nickname']}** вступил в игру (ставка `{bet}`)\n\n"
            f"**Участники ({len(game['players'])}):**\n{players_list}\n\n"
            f"Создатель может запустить: `/game_start`"
        ),
        color=discord.Color.blurple()
    )

    await interaction.followup.send(embed=embed)


@tree.command(name="game_start", description="Запустить свою игру в кубик")
async def game_start(interaction: discord.Interaction):
    await interaction.response.defer()

    guild_id = interaction.guild_id
    games = get_guild_games(guild_id)

    if interaction.user.id not in games:
        await interaction.followup.send("❌ У тебя нет активной игры.")
        return

    game = games[interaction.user.id]

    if game["started"]:
        await interaction.followup.send("❌ Игра уже запущена.")
        return

    players = game["players"]

    if len(players) < GAME_MIN_PLAYERS:
        await interaction.followup.send(
            f"❌ Нужно минимум {GAME_MIN_PLAYERS} игрока. "
            f"Сейчас: **{len(players)}**"
        )
        return

    game["started"] = True
    bet = game["bet"]
    bank = bet * len(players)

    rolls = {uid: random.randint(1, 6) for uid in players}

    max_roll = max(rolls.values())
    winners = [uid for uid, r in rolls.items() if r == max_roll]

    fee = int(bank * GAME_FEE_PERCENT / 100)
    prize_pool = bank - fee
    prize_each = prize_pool // len(winners)
    remainder = prize_pool - prize_each * len(winners)

    data = load_data()
    payout_lines = []

    for uid in winners:
        nick = players[uid]
        record = find_player(data, nick)
        if not record:
            continue

        before = record.get("balance", 0)
        record["balance"] = before + prize_each
        save_data(data)

        log_balance(
            nickname=record["nickname"],
            steam_id=record["steam_id"],
            action="game_win",
            amount=prize_each,
            before=before,
            after=record["balance"],
            source="game_dice",
            reason=f"Победа в кубике (кубик {max_roll}, банк {bank}, комиссия {fee})"
        )

        payout_lines.append(
            f"🏆 **{nick}** — `{prize_each}` 💰 (кубик: {rolls[uid]})"
        )

    roll_lines = []
    for uid, r in rolls.items():
        mark = "🏆" if uid in winners else "▫️"
        roll_lines.append(f"{mark} **{players[uid]}** — кубик: `{r}`")

    embed = discord.Embed(
        title="🎲 Результаты игры",
        description="\n".join(roll_lines),
        color=discord.Color.gold()
    )
    embed.add_field(name="💰 Банк", value=f"`{bank}`", inline=True)
    embed.add_field(
        name=f"✂️ Комиссия ({GAME_FEE_PERCENT}%)",
        value=f"`{fee}`",
        inline=True
    )
    embed.add_field(name="🎁 Призовой фонд", value=f"`{prize_pool}`", inline=True)
    embed.add_field(
        name="🏆 Победители",
        value="\n".join(payout_lines) if payout_lines else "—",
        inline=False
    )

    if remainder > 0:
        embed.add_field(
            name="🔥 Сгорело (округление)",
            value=f"`{remainder}`",
            inline=False
        )

    embed.set_footer(text=f"Участников: {len(players)} | Ставка: {bet}")

    await interaction.followup.send(embed=embed)

    del games[interaction.user.id]


@tree.command(name="game_cancel", description="Отменить свою игру в кубик")
async def game_cancel(interaction: discord.Interaction):
    await interaction.response.defer()

    guild_id = interaction.guild_id
    games = get_guild_games(guild_id)

    if interaction.user.id not in games:
        await interaction.followup.send("❌ У тебя нет активной игры.")
        return

    game = games[interaction.user.id]

    if game["started"]:
        await interaction.followup.send("❌ Игра уже запущена — отменить нельзя.")
        return

    bet = game["bet"]
    players = game["players"]

    data = load_data()
    refund_lines = []

    for uid, nick in players.items():
        record = find_player(data, nick)
        if not record:
            continue

        before = record.get("balance", 0)
        record["balance"] = before + bet
        save_data(data)

        log_balance(
            nickname=record["nickname"],
            steam_id=record["steam_id"],
            action="game_refund",
            amount=bet,
            before=before,
            after=record["balance"],
            source=str(interaction.user),
            reason="Отмена кубика — возврат ставки"
        )

        refund_lines.append(f"↩️ **{nick}** — возвращено `{bet}`")

    del games[interaction.user.id]

    embed = discord.Embed(
        title="🚫 Игра отменена",
        description="\n".join(refund_lines) if refund_lines else "Участников не было.",
        color=discord.Color.red()
    )
    embed.set_footer(text=f"Возвращено ставок: {len(refund_lines)}")

    await interaction.followup.send(embed=embed)


# ============================================================
#                  РЕГИСТРАЦИЯ ПОКЕРА
# ============================================================
poker.register_poker_commands(
    bot=bot,
    tree=tree,
    load_data=load_data,
    save_data=save_data,
    find_player=find_player,
    player_autocomplete=player_autocomplete,
    log_balance=log_balance
)


# ============================================================
#                   ОБРАБОТКА ОШИБОК
# ============================================================
@tree.error
async def on_app_command_error(interaction: discord.Interaction,
                               error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "🚫 У тебя нет прав для этой команды."
    else:
        msg = f"⚠️ Ошибка: `{error}`"

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


# ============================================================
#                         ЗАПУСК
# ============================================================
if __name__ == "__main__":
    bot.run(TOKEN)