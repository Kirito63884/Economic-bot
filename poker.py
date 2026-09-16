# ============================================================
#              POKER — ПОЛНАЯ РЕАЛИЗАЦИЯ
#              Texas Hold'em No-Limit
# ============================================================
import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import random
from typing import List, Optional, Dict
from itertools import combinations


# ============================================================
#                  НАСТРОЙКИ
# ============================================================
POKER_MIN_PLAYERS = 2
POKER_MAX_PLAYERS = 8
POKER_FEE_PERCENT = 5
POKER_TURN_TIMEOUT = 90          # секунд на действие
POKER_STARTING_STACK_MULT = 50   # стек = bet × это значение


# ============================================================
#                  КОЛОДА
# ============================================================
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_VALUES = {r: i for i, r in enumerate(RANKS, start=2)}

HAND_NAMES = {
    9: "Роял-флеш", 8: "Стрит-флеш", 7: "Каре", 6: "Фулл-хаус",
    5: "Флеш", 4: "Стрит", 3: "Сет", 2: "Две пары", 1: "Пара", 0: "Старшая карта",
}


def create_deck():
    return [(rank, suit) for suit in SUITS for rank in RANKS]


def card_str(card):
    return f"{card[0]}{card[1]}"


def hand_str(cards):
    return " ".join(card_str(c) for c in cards)


def evaluate_5cards(cards):
    """Оценка 5 карт → кортеж (category, tiebreakers...)"""
    values = sorted([RANK_VALUES[c[0]] for c in cards], reverse=True)
    suits = [c[1] for c in cards]

    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    count_sorted = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)

    is_flush = len(set(suits)) == 1

    unique_vals = sorted(set(values), reverse=True)
    is_straight = False
    straight_high = None
    if len(unique_vals) == 5:
        if unique_vals[0] - unique_vals[4] == 4:
            is_straight = True
            straight_high = unique_vals[0]
        elif unique_vals == [14, 5, 4, 3, 2]:
            is_straight = True
            straight_high = 5

    if is_flush and is_straight and straight_high == 14:
        return (9,)
    if is_flush and is_straight:
        return (8, straight_high)
    if count_sorted[0][1] == 4:
        four = count_sorted[0][0]
        kicker = max(v for v in values if v != four)
        return (7, four, kicker)
    if count_sorted[0][1] == 3 and count_sorted[1][1] == 2:
        return (6, count_sorted[0][0], count_sorted[1][0])
    if is_flush:
        return (5, *values)
    if is_straight:
        return (4, straight_high)
    if count_sorted[0][1] == 3:
        three = count_sorted[0][0]
        kickers = sorted((v for v in values if v != three), reverse=True)
        return (3, three, *kickers)
    if count_sorted[0][1] == 2 and count_sorted[1][1] == 2:
        hp = max(count_sorted[0][0], count_sorted[1][0])
        lp = min(count_sorted[0][0], count_sorted[1][0])
        kicker = max(v for v in values if v != hp and v != lp)
        return (2, hp, lp, kicker)
    if count_sorted[0][1] == 2:
        pair = count_sorted[0][0]
        kickers = sorted((v for v in values if v != pair), reverse=True)
        return (1, pair, *kickers)
    return (0, *values)


def evaluate_best_hand(seven_cards):
    best_score = None
    best_hand = None
    for combo in combinations(seven_cards, 5):
        score = evaluate_5cards(list(combo))
        if best_score is None or score > best_score:
            best_score = score
            best_hand = combo
    return best_score, list(best_hand)


def score_to_name(score):
    return HAND_NAMES.get(score[0], "?")


# ============================================================
#                  ИГРОК
# ============================================================
class PokerPlayer:
    def __init__(self, user_id, nickname, steam_id, stack):
        self.user_id = user_id
        self.nickname = nickname
        self.steam_id = steam_id
        self.stack = stack
        self.starting_stack = stack
        self.hand = []
        self.current_bet = 0
        self.total_bet = 0
        self.folded = False
        self.all_in = False

    @property
    def in_hand(self):
        return not self.folded

    @property
    def can_act(self):
        return not self.folded and not self.all_in and self.stack > 0


# ============================================================
#                  ИГРА
# ============================================================
class PokerGame:
    def __init__(self, host_id, host_nickname, bet, bot, guild_id,
                 channel_id, on_finish):
        self.host_id = host_id
        self.host_nickname = host_nickname
        self.bet = bet
        self.bot = bot
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.on_finish = on_finish

        self.players: Dict[int, PokerPlayer] = {}
        self.deck = []
        self.community = []
        self.pot = 0
        self.side_pots: List[dict] = []

        self.started = False
        self.finished = False
        self.hand_over = False
        self.stage = "lobby"
        self.current_bet = 0
        self.min_raise = 0
        self.dealer_idx = 0
        self.turn_idx = 0
        self.last_raiser_idx = None
        self.acted_since_raise: set = set()

        self.message: Optional[discord.Message] = None
        self.turn_task: Optional[asyncio.Task] = None
        self.hand_number = 0

    # ---------- УТИЛИТЫ ----------
    def player_list(self):
        return list(self.players.values())

    def active_players(self):
        return [p for p in self.players.values() if p.can_act]

    def in_hand_players(self):
        return [p for p in self.players.values() if p.in_hand]

    def pot_total(self):
        return sum(p.total_bet for p in self.players.values())

    def index_of(self, user_id):
        ids = list(self.players.keys())
        return ids.index(user_id)

    def idx_player(self, idx):
        ids = list(self.players.keys())
        return self.players[ids[idx % len(ids)]]

    # ---------- РАЗДАЧА ----------
    def start_new_hand(self):
        for uid in list(self.players.keys()):
            if self.players[uid].stack <= 0:
                del self.players[uid]

        if len(self.players) < 2:
            self.finished = True
            return False

        self.hand_number += 1
        self.deck = create_deck()
        random.shuffle(self.deck)
        self.community = []
        self.pot = 0
        self.side_pots = []
        self.current_bet = 0
        self.min_raise = self.bet * 2
        self.last_raiser_idx = None
        self.acted_since_raise = set()
        self.hand_over = False

        for p in self.players.values():
            p.hand = []
            p.current_bet = 0
            p.total_bet = 0
            p.folded = False
            p.all_in = False

        n = len(self.players)

        if n == 2:
            sb_idx = self.dealer_idx % 2
            bb_idx = (self.dealer_idx + 1) % 2
            first_to_act_preflop = sb_idx
        else:
            sb_idx = (self.dealer_idx + 1) % n
            bb_idx = (self.dealer_idx + 2) % n
            first_to_act_preflop = (bb_idx + 1) % n

        sb_player = self.idx_player(sb_idx)
        bb_player = self.idx_player(bb_idx)

        self._post_bet(sb_player, self.bet, force=True)
        self._post_bet(bb_player, self.bet * 2, force=True)

        self.current_bet = self.bet * 2
        self.min_raise = self.bet * 2

        for _ in range(2):
            for offset in range(n):
                idx = (sb_idx + offset) % n
                p = self.idx_player(idx)
                p.hand.append(self.deck.pop())

        self.stage = "preflop"
        self.turn_idx = first_to_act_preflop
        self.started = True

        if len(self.active_players()) <= 1:
            asyncio.create_task(self._run_to_showdown())
            return True

        return True

    def _post_bet(self, player, amount, force=False):
        actual = min(amount, player.stack)
        player.stack -= actual
        player.current_bet += actual
        player.total_bet += actual
        self.pot += actual
        if player.stack == 0:
            player.all_in = True

    # ---------- ДЕЙСТВИЯ ----------
    def do_fold(self, player):
        player.folded = True
        self.acted_since_raise.add(player.user_id)

    def do_check(self, player):
        if player.current_bet != self.current_bet:
            return False
        self.acted_since_raise.add(player.user_id)
        return True

    def do_call(self, player):
        to_call = self.current_bet - player.current_bet
        actual = min(to_call, player.stack)
        player.stack -= actual
        player.current_bet += actual
        player.total_bet += actual
        self.pot += actual
        if player.stack == 0:
            player.all_in = True
        self.acted_since_raise.add(player.user_id)
        return True

    def do_raise(self, player, raise_to):
        to_call = self.current_bet - player.current_bet
        additional = raise_to - player.current_bet

        if additional > player.stack:
            return False, "Недостаточно средств"

        min_raise_to = self.current_bet + self.min_raise
        if raise_to < min_raise_to and additional < player.stack:
            return False, f"Минимальный рейз: до {min_raise_to}"

        player.stack -= additional
        player.current_bet += additional
        player.total_bet += additional
        self.pot += additional
        if player.stack == 0:
            player.all_in = True

        if player.current_bet > self.current_bet:
            self.min_raise = player.current_bet - self.current_bet
            self.current_bet = player.current_bet
            self.last_raiser_idx = self.index_of(player.user_id)
            self.acted_since_raise = {player.user_id}

        return True, None

    # ---------- ХОД ----------
    def next_active_turn(self):
        ids = list(self.players.keys())
        n = len(ids)
        for i in range(1, n + 1):
            idx = (self.turn_idx + i) % n
            p = self.idx_player(idx)
            if p.can_act:
                self.turn_idx = idx
                return True
        return False

    def betting_round_complete(self):
        if len(self.in_hand_players()) <= 1:
            return True

        can_act = self.active_players()
        if not can_act:
            return True

        for p in can_act:
            if p.current_bet != self.current_bet:
                return False
            if p.user_id not in self.acted_since_raise:
                return False
        return True

    def advance_stage(self):
        for p in self.players.values():
            p.current_bet = 0
        self.current_bet = 0
        self.min_raise = self.bet
        self.last_raiser_idx = None
        self.acted_since_raise = set()

        if self.stage == "preflop":
            self.stage = "flop"
            self.deck.pop()
            self.community.extend([self.deck.pop() for _ in range(3)])
        elif self.stage == "flop":
            self.stage = "turn"
            self.deck.pop()
            self.community.append(self.deck.pop())
        elif self.stage == "turn":
            self.stage = "river"
            self.deck.pop()
            self.community.append(self.deck.pop())
        elif self.stage == "river":
            self.stage = "showdown"
            return

        n = len(self.players)
        if n == 2:
            self.turn_idx = (self.dealer_idx + 1) % 2
        else:
            self.turn_idx = (self.dealer_idx + 1) % n
        self._skip_to_actor()

    def _skip_to_actor(self):
        ids = list(self.players.keys())
        n = len(ids)
        for _ in range(n):
            p = self.idx_player(self.turn_idx)
            if p.can_act:
                return
            self.turn_idx = (self.turn_idx + 1) % n

    # ---------- SIDE POTS ----------
    def build_side_pots(self):
        contributions = {p.user_id: p.total_bet for p in self.players.values()
                         if p.total_bet > 0}

        levels = sorted(set(contributions.values()))
        side_pots = []
        prev_level = 0

        for level in levels:
            pot_amount = 0
            eligible = []

            for uid, contrib in contributions.items():
                taken = max(0, min(contrib, level) - prev_level)
                pot_amount += taken
                p = self.players[uid]
                if contrib >= level and p.in_hand:
                    eligible.append(uid)

            if pot_amount > 0:
                side_pots.append({"amount": pot_amount, "eligible": eligible})

            prev_level = level

        return side_pots

    # ---------- SHOWDOWN ----------
    async def showdown(self):
        self.hand_over = True
        self.side_pots = self.build_side_pots()

        scores = {}
        for p in self.players.values():
            if p.in_hand and p.hand and self.community:
                seven = p.hand + self.community
                score, _ = evaluate_best_hand(seven)
                scores[p.user_id] = score

        payouts: Dict[int, int] = {}
        pot_breakdown = []

        for i, sp in enumerate(self.side_pots):
            amount = sp["amount"]
            eligible = [uid for uid in sp["eligible"] if uid in scores]

            if not eligible:
                eligible = list(scores.keys())

            if not eligible:
                continue

            best_score = max(scores[uid] for uid in eligible)
            winners = [uid for uid in eligible if scores[uid] == best_score]

            share = amount // len(winners)
            remainder = amount - share * len(winners)

            for uid in winners:
                payouts[uid] = payouts.get(uid, 0) + share

            if remainder > 0:
                payouts[winners[0]] += remainder

            pot_breakdown.append({
                "index": i + 1,
                "amount": amount,
                "winners": winners,
            })

        total_pot = sum(payouts.values())
        total_fee = int(total_pot * POKER_FEE_PERCENT / 100)

        if total_fee > 0 and payouts:
            total_after_fee = total_pot - total_fee
            for uid in payouts:
                payouts[uid] = int(payouts[uid] * total_after_fee / total_pot)

        for uid, amount in payouts.items():
            self.players[uid].stack += amount

        await self._show_showdown_embed(payouts, pot_breakdown, scores, total_fee)
        await self.finish_hand()

    async def _show_showdown_embed(self, payouts, pot_breakdown, scores, fee):
        embed = discord.Embed(
            title=f"🃏 Вскрытие — раздача #{self.hand_number}",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="🎴 Общие карты",
            value=hand_str(self.community),
            inline=False
        )

        lines = []
        for p in self.players.values():
            if p.folded:
                lines.append(f"❌ **{p.nickname}** — *сбросил*")
            elif p.user_id in scores:
                lines.append(
                    f"**{p.nickname}** — {hand_str(p.hand)} → *{score_to_name(scores[p.user_id])}*"
                )
        embed.add_field(name="👤 Руки игроков", value="\n".join(lines), inline=False)

        pot_lines = []
        for sp in pot_breakdown:
            winners = ", ".join(self.players[uid].nickname for uid in sp["winners"])
            pot_lines.append(f"**Банк {sp['index']}** — `{sp['amount']}` → {winners}")
        embed.add_field(
            name=f"💰 Всего банков: {len(pot_breakdown)}",
            value="\n".join(pot_lines) or "—",
            inline=False
        )

        pay_lines = []
        for uid, amount in payouts.items():
            pay_lines.append(f"🏆 **{self.players[uid].nickname}** — `+{amount}`")
        embed.add_field(name="💵 Выплаты", value="\n".join(pay_lines) or "—", inline=False)

        if fee > 0:
            embed.add_field(
                name=f"✂️ Комиссия ({POKER_FEE_PERCENT}%)",
                value=f"`{fee}`",
                inline=True
            )

        embed.set_footer(text="Следующая раздача: /poker_next (хост)")

        if self.message:
            try:
                await self.message.edit(embed=embed, view=None)
            except Exception:
                pass

    async def _run_to_showdown(self):
        while self.stage != "showdown":
            self.advance_stage()
            await asyncio.sleep(0.3)
            if self.message:
                try:
                    await self.message.edit(embed=self.build_embed(), view=None)
                except Exception:
                    pass
        await self.showdown()

    # ---------- ЗАВЕРШЕНИЕ ----------
    async def finish_hand(self):
        for uid in list(self.players.keys()):
            if self.players[uid].stack <= 0:
                del self.players[uid]

        if len(self.players) > 0:
            self.dealer_idx = (self.dealer_idx + 1) % len(self.players)

        if len(self.players) < 2:
            self.finished = True
            await self._finish_game()
            return

        self.started = False
        self.stage = "lobby"

    async def _finish_game(self):
        embed = discord.Embed(
            title="🏁 Игра завершена",
            color=discord.Color.dark_red()
        )
        if self.players:
            winner = max(self.players.values(), key=lambda p: p.stack)
            embed.description = f"🏆 Победитель: **{winner.nickname}** (стек `{winner.stack}`)"

        if self.message:
            try:
                await self.message.edit(embed=embed, view=None)
            except Exception:
                pass

        await self.on_finish(self)

    # ---------- EMBED ----------
    def build_embed(self):
        stage_names = {
            "lobby": "Лобби",
            "preflop": "Пре-флоп",
            "flop": "Флоп",
            "turn": "Тёрн",
            "river": "Ривер",
            "showdown": "Вскрытие",
        }

        embed = discord.Embed(
            title=f"🃏 Покер — {stage_names.get(self.stage, self.stage)} "
                  f"(раздача #{self.hand_number})",
            color=discord.Color.dark_green()
        )

        lines = []
        for i, p in enumerate(self.player_list()):
            markers = []
            if len(self.players) > 0 and i == self.dealer_idx % len(self.players):
                markers.append("🎲")
            if self.started and not self.hand_over and i == self.turn_idx:
                markers.append("🎯")
            if p.folded:
                markers.append("❌")
            if p.all_in:
                markers.append("🔥")
            marker_str = " ".join(markers)
            lines.append(
                f"{marker_str} **{p.nickname}** — стек: `{p.stack}` | в банке: `{p.total_bet}`"
            )
        embed.add_field(name="👥 Игроки", value="\n".join(lines) or "—", inline=False)

        embed.add_field(name="💰 Банк", value=f"`{self.pot_total()}`", inline=True)
        embed.add_field(name="📊 Ставка", value=f"`{self.current_bet}`", inline=True)
        embed.add_field(name="📈 Мин. рейз", value=f"`{self.min_raise}`", inline=True)

        if self.community:
            embed.add_field(name="🎴 На столе", value=hand_str(self.community), inline=False)

        return embed

    def add_player_safe(self, user_id, nickname, steam_id, stack):
        self.players[user_id] = PokerPlayer(user_id, nickname, steam_id, stack)


# ============================================================
#                  UI: RAISE MODAL
# ============================================================
class RaiseModal(discord.ui.Modal, title="Повысить ставку"):
    amount = discord.ui.TextInput(
        label="До какой суммы поднять (общая ставка в раунде)",
        placeholder="Например: 200",
        required=True
    )

    def __init__(self, game: PokerGame, player: PokerPlayer, view: "ActionsView"):
        super().__init__()
        self.game = game
        self.player = player
        self.view_ref = view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            raise_to = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("❌ Введите целое число.", ephemeral=True)
            return

        if raise_to <= self.game.current_bet:
            await interaction.response.send_message(
                f"❌ Рейз должен быть больше текущей ставки `{self.game.current_bet}`.",
                ephemeral=True
            )
            return

        ok, err = self.game.do_raise(self.player, raise_to)
        if not ok:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"📈 **{self.player.nickname}** повышает до `{self.game.current_bet}`"
        )
        await self.view_ref.after_action()


# ============================================================
#                  UI: ACTIONS VIEW
# ============================================================
class ActionsView(discord.ui.View):
    def __init__(self, game: PokerGame, actor: PokerPlayer):
        super().__init__(timeout=POKER_TURN_TIMEOUT)
        self.game = game
        self.actor = actor
        self.resolved = False

        to_call = game.current_bet - actor.current_bet
        if to_call > 0:
            self.check_call.label = f"📞 Колл {min(to_call, actor.stack)}"
        else:
            self.check_call.label = "✅ Чек"

        if actor.stack <= to_call:
            self.raise_bet.disabled = True

    @discord.ui.button(label="✅ Чек", style=discord.ButtonStyle.green)
    async def check_call(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.actor.user_id:
            await interaction.response.send_message("❌ Это не твой ход.", ephemeral=True)
            return

        game = self.game
        player = self.actor
        to_call = game.current_bet - player.current_bet

        if to_call == 0:
            game.do_check(player)
            msg = f"✅ **{player.nickname}** — чек"
        else:
            actual = min(to_call, player.stack)
            game.do_call(player)
            msg = f"📞 **{player.nickname}** — колл `{actual}`"

        await interaction.response.send_message(msg)
        await self.after_action()

    @discord.ui.button(label="📈 Рейз", style=discord.ButtonStyle.blurple)
    async def raise_bet(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.actor.user_id:
            await interaction.response.send_message("❌ Это не твой ход.", ephemeral=True)
            return

        modal = RaiseModal(self.game, self.actor, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="❌ Фолд", style=discord.ButtonStyle.red)
    async def fold(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.actor.user_id:
            await interaction.response.send_message("❌ Это не твой ход.", ephemeral=True)
            return

        self.game.do_fold(self.actor)
        await interaction.response.send_message(f"❌ **{self.actor.nickname}** — фолд")
        await self.after_action()

    async def after_action(self):
        if self.resolved:
            return
        self.resolved = True

        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass

        game = self.game

        if len(game.in_hand_players()) <= 1:
            await game.showdown()
            return

        if game.betting_round_complete():
            game.advance_stage()
            if game.stage == "showdown":
                await game.showdown()
                return
            if len(game.active_players()) <= 1:
                await game._run_to_showdown()
                return
        else:
            if not game.next_active_turn():
                await game._run_to_showdown()
                return

        await send_turn_to_current(game)


# ============================================================
#                  UI: TABLE VIEW
# ============================================================
class TableView(discord.ui.View):
    def __init__(self, game: PokerGame):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="🎯 Мой ход", style=discord.ButtonStyle.primary)
    async def act(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self.game

        if game.finished or game.hand_over:
            await interaction.response.send_message("⏳ Раздача завершена.", ephemeral=True)
            return

        if not game.started:
            await interaction.response.send_message("⏳ Игра ещё не началась.", ephemeral=True)
            return

        current = game.idx_player(game.turn_idx)

        if interaction.user.id != current.user_id:
            await interaction.response.send_message(
                f"⏳ Сейчас ход **{current.nickname}**.", ephemeral=True
            )
            return

        view = ActionsView(game, current)
        to_call = game.current_bet - current.current_bet

        msg = (
            f"🎯 **Твой ход, {current.nickname}!**\n"
            f"🎴 Твоя рука: {hand_str(current.hand)}\n"
            f"🃏 На столе: {hand_str(game.community) if game.community else '—'}\n"
            f"💰 Банк: `{game.pot_total()}` | Колл: `{to_call}` | "
            f"Мин. рейз до: `{game.current_bet + game.min_raise}`\n"
            f"💼 Твой стек: `{current.stack}`"
        )
        await interaction.response.send_message(msg, view=view, ephemeral=True)


# ============================================================
#                  ОТПРАВКА ХОДА
# ============================================================
async def send_turn_to_current(game: PokerGame):
    if not game.message:
        return

    current = game.idx_player(game.turn_idx)

    embed = game.build_embed()
    embed.description = (
        f"🎯 **Ход: {current.nickname}** (<@{current.user_id}>)\n\n"
        f"Нажми кнопку **«Мой ход»** ниже.\n"
        f"⏱️ Таймаут: {POKER_TURN_TIMEOUT} сек."
    )

    view = TableView(game)
    try:
        await game.message.edit(
            content=f"<@{current.user_id}>",
            embed=embed,
            view=view
        )
    except Exception:
        pass

    if game.turn_task and not game.turn_task.done():
        game.turn_task.cancel()
    game.turn_task = asyncio.create_task(turn_timeout(game, current.user_id))


async def turn_timeout(game: PokerGame, user_id: int):
    await asyncio.sleep(POKER_TURN_TIMEOUT)

    if game.finished or game.hand_over:
        return

    current = game.idx_player(game.turn_idx)
    if current.user_id != user_id:
        return

    game.do_fold(current)

    if game.message:
        try:
            await game.message.channel.send(
                f"⏱️ **{current.nickname}** не ответил вовремя — автофолд."
            )
        except Exception:
            pass

    if len(game.in_hand_players()) <= 1:
        await game.showdown()
        return

    if game.betting_round_complete():
        game.advance_stage()
        if game.stage == "showdown":
            await game.showdown()
            return

    game.next_active_turn()
    await send_turn_to_current(game)


# ============================================================
#                  МЕНЕДЖЕР ИГР
# ============================================================
class PokerManager:
    def __init__(self):
        self.games: Dict[int, Dict[int, PokerGame]] = {}

    def get_guild(self, guild_id):
        if guild_id not in self.games:
            self.games[guild_id] = {}
        return self.games[guild_id]

    def find_by_player(self, guild_id, user_id):
        for host_id, game in self.get_guild(guild_id).items():
            if user_id in game.players:
                return host_id, game
        return None, None

    def remove(self, guild_id, host_id):
        g = self.get_guild(guild_id)
        if host_id in g:
            del g[host_id]


poker_manager = PokerManager()


# ============================================================
#                  РЕГИСТРАЦИЯ КОМАНД
# ============================================================
def register_poker_commands(bot, tree, load_data, save_data,
                            find_player, player_autocomplete, log_balance):
    """Регистрирует команды покера в главном дереве бота"""

    @tree.command(
        name="poker_create",
        description=f"Создать покерный стол ({POKER_MIN_PLAYERS}-{POKER_MAX_PLAYERS})"
    )
    @app_commands.describe(
        nickname="Твой игровой ник (из базы)",
        bet="Малый блайнд. Большой = bet×2. Стек = bet×50"
    )
    @app_commands.autocomplete(nickname=player_autocomplete)
    async def poker_create(interaction: discord.Interaction, nickname: str, bet: int):
        await interaction.response.defer()

        if bet <= 0:
            await interaction.followup.send("❌ Ставка должна быть больше 0.")
            return

        data = load_data()
        host = find_player(data, nickname)
        if not host:
            await interaction.followup.send(f"❌ Игрок `{nickname}` не найден.")
            return

        stack = bet * POKER_STARTING_STACK_MULT
        if host.get("balance", 0) < stack:
            await interaction.followup.send(
                f"❌ Нужен стек `{stack}` (bet×{POKER_STARTING_STACK_MULT}). "
                f"Баланс: `{host['balance']}`"
            )
            return

        guild_id = interaction.guild_id
        manager = poker_manager.get_guild(guild_id)

        if interaction.user.id in manager:
            await interaction.followup.send("❌ У тебя уже есть активный стол.")
            return

        other_host, _ = poker_manager.find_by_player(guild_id, interaction.user.id)
        if other_host is not None:
            await interaction.followup.send("❌ Ты уже участвуешь в другом столе.")
            return

        before = host["balance"]
        host["balance"] -= stack
        save_data(data)

        log_balance(
            nickname=host["nickname"], steam_id=host["steam_id"],
            action="poker_buyin", amount=-stack, before=before,
            after=host["balance"], source=str(interaction.user),
            reason=f"Бай-ин в покер (стек {stack})"
        )

        async def on_finish(g: PokerGame):
            fresh = load_data()
            for p in g.players.values():
                if p.stack <= 0:
                    continue
                rec = find_player(fresh, p.nickname)
                if not rec:
                    continue
                b = rec["balance"]
                rec["balance"] += p.stack
                save_data(fresh)
                log_balance(
                    nickname=rec["nickname"], steam_id=rec["steam_id"],
                    action="poker_cashout", amount=p.stack, before=b,
                    after=rec["balance"], source="poker",
                    reason="Возврат стека после покера"
                )
            poker_manager.remove(g.guild_id, g.host_id)

        game = PokerGame(
            host_id=interaction.user.id,
            host_nickname=host["nickname"],
            bet=bet, bot=bot, guild_id=guild_id,
            channel_id=interaction.channel_id,
            on_finish=on_finish
        )
        game.add_player_safe(interaction.user.id, host["nickname"],
                             host["steam_id"], stack)
        manager[interaction.user.id] = game

        embed = discord.Embed(
            title="🃏 Покерный стол создан",
            description=(
                f"**Хост:** {host['nickname']}\n"
                f"**Блайнды:** `{bet}` / `{bet*2}`\n"
                f"**Стек:** `{stack}`\n"
                f"**Игроков:** 1/{POKER_MAX_PLAYERS}\n\n"
                f"Присоединиться: `/poker_join host:@{interaction.user.display_name}`\n"
                f"Начать: `/poker_start`"
            ),
            color=discord.Color.dark_green()
        )
        await interaction.followup.send(embed=embed)

    @tree.command(name="poker_join", description="Сесть за покерный стол")
    @app_commands.describe(host="Discord-пользователь — хост",
                           nickname="Твой игровой ник (из базы)")
    @app_commands.autocomplete(nickname=player_autocomplete)
    async def poker_join(interaction: discord.Interaction,
                         host: discord.User, nickname: str):
        await interaction.response.defer()

        guild_id = interaction.guild_id
        manager = poker_manager.get_guild(guild_id)

        if host.id not in manager:
            await interaction.followup.send(f"❌ У {host.mention} нет активного стола.")
            return

        game = manager[host.id]

        if game.started:
            await interaction.followup.send("❌ Игра уже началась.")
            return

        if interaction.user.id in game.players:
            await interaction.followup.send("❌ Ты уже за столом.")
            return

        if len(game.players) >= POKER_MAX_PLAYERS:
            await interaction.followup.send(f"❌ Стол полон (макс {POKER_MAX_PLAYERS}).")
            return

        other_host, _ = poker_manager.find_by_player(guild_id, interaction.user.id)
        if other_host is not None:
            await interaction.followup.send("❌ Ты уже в другом столе.")
            return

        data = load_data()
        player = find_player(data, nickname)
        if not player:
            await interaction.followup.send(f"❌ Игрок `{nickname}` не найден.")
            return

        stack = game.bet * POKER_STARTING_STACK_MULT
        if player.get("balance", 0) < stack:
            await interaction.followup.send(
                f"❌ Нужен стек `{stack}`. Баланс: `{player['balance']}`"
            )
            return

        before = player["balance"]
        player["balance"] -= stack
        save_data(data)

        log_balance(
            nickname=player["nickname"], steam_id=player["steam_id"],
            action="poker_buyin", amount=-stack, before=before,
            after=player["balance"], source=str(interaction.user),
            reason=f"Бай-ин в покер (хост: {host})"
        )

        game.add_player_safe(interaction.user.id, player["nickname"],
                             player["steam_id"], stack)

        players_list = "\n".join(
            f"• **{p.nickname}** (стек `{p.stack}`)" for p in game.players.values()
        )
        embed = discord.Embed(
            title="✅ Игрок сел за стол",
            description=f"**{player['nickname']}** присоединился\n\n"
                        f"**Участники ({len(game.players)}/{POKER_MAX_PLAYERS}):**\n{players_list}",
            color=discord.Color.dark_green()
        )
        await interaction.followup.send(embed=embed)

    @tree.command(name="poker_start", description="Начать раздачу (хост)")
    async def poker_start(interaction: discord.Interaction):
        await interaction.response.defer()

        guild_id = interaction.guild_id
        manager = poker_manager.get_guild(guild_id)

        if interaction.user.id not in manager:
            await interaction.followup.send("❌ У тебя нет активного стола.")
            return

        game = manager[interaction.user.id]

        if game.finished:
            await interaction.followup.send("❌ Игра уже завершена.")
            return

        if game.started and not game.hand_over:
            await interaction.followup.send("❌ Раздача уже идёт.")
            return

        if len(game.players) < POKER_MIN_PLAYERS:
            await interaction.followup.send(
                f"❌ Нужно минимум {POKER_MIN_PLAYERS} игрока."
            )
            return

        if len(game.players) > POKER_MAX_PLAYERS:
            await interaction.followup.send(
                f"❌ Максимум {POKER_MAX_PLAYERS} игроков."
            )
            return

        ok = game.start_new_hand()
        if not ok:
            await interaction.followup.send("❌ Не удалось начать раздачу.")
            return

        embed = game.build_embed()
        current = game.idx_player(game.turn_idx)
        embed.description = (
            f"🎯 **Ход: {current.nickname}** (<@{current.user_id}>)\n\n"
            f"Нажми **«Мой ход»**."
        )
        view = TableView(game)

        msg = await interaction.followup.send(
            content=f"<@{current.user_id}>",
            embed=embed,
            view=view
        )
        game.message = msg

        game.turn_task = asyncio.create_task(
            turn_timeout(game, current.user_id)
        )

    @tree.command(name="poker_next", description="Начать следующую раздачу (хост)")
    async def poker_next(interaction: discord.Interaction):
        await interaction.response.defer()

        guild_id = interaction.guild_id
        manager = poker_manager.get_guild(guild_id)

        if interaction.user.id not in manager:
            await interaction.followup.send("❌ У тебя нет активного стола.")
            return

        game = manager[interaction.user.id]

        if game.finished:
            await interaction.followup.send("❌ Игра завершена.")
            return

        if game.started and not game.hand_over:
            await interaction.followup.send("❌ Раздача ещё идёт.")
            return

        if len(game.players) < POKER_MIN_PLAYERS:
            await interaction.followup.send("❌ Недостаточно игроков для продолжения.")
            await game._finish_game()
            return

        ok = game.start_new_hand()
        if not ok:
            await interaction.followup.send("❌ Не удалось начать раздачу.")
            return

        embed = game.build_embed()
        current = game.idx_player(game.turn_idx)
        embed.description = f"🎯 **Ход: {current.nickname}** (<@{current.user_id}>)"
        view = TableView(game)

        msg = await interaction.followup.send(
            content=f"<@{current.user_id}>",
            embed=embed,
            view=view
        )
        game.message = msg
        game.turn_task = asyncio.create_task(
            turn_timeout(game, current.user_id)
        )

    @tree.command(name="poker_cancel", description="Отменить стол (возврат бай-инов)")
    async def poker_cancel(interaction: discord.Interaction):
        await interaction.response.defer()

        guild_id = interaction.guild_id
        manager = poker_manager.get_guild(guild_id)

        if interaction.user.id not in manager:
            await interaction.followup.send("❌ У тебя нет активного стола.")
            return

        game = manager[interaction.user.id]

        if game.started and not game.hand_over:
            await interaction.followup.send("❌ Раздача идёт — отменить нельзя.")
            return

        data = load_data()
        refund_lines = []
        for p in game.players.values():
            rec = find_player(data, p.nickname)
            if not rec:
                continue
            b = rec["balance"]
            rec["balance"] += p.stack
            save_data(data)
            log_balance(
                nickname=rec["nickname"], steam_id=rec["steam_id"],
                action="poker_refund", amount=p.stack, before=b,
                after=rec["balance"], source=str(interaction.user),
                reason="Отмена стола — возврат стека"
            )
            refund_lines.append(f"↩️ **{p.nickname}** — `{p.stack}`")

        poker_manager.remove(guild_id, interaction.user.id)

        embed = discord.Embed(
            title="🚫 Стол отменён",
            description="\n".join(refund_lines) or "Пусто.",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)