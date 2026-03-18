import os
import re
import json
import time
import asyncio
import requests
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
from io import BytesIO
from datetime import datetime, timezone
from collections import defaultdict, deque

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==== ANTI-SPAM CONFIG ====
USER_MAX_CALLS = 0          # comandos
USER_WINDOW = 0          # segundos

CHAT_MAX_CALLS = 0          # comandos
CHAT_WINDOW = 0            # segundos

SLOWDOWN_MSG = "⏳ Too unstable!! Wait a bit."

# ==== CONFIG ====
BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ.get("ALLOWED_CHAT_ID", "0"))  # <<--- PROTECCIÓN
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

SUPPLY_DATA_URL = "https://turbousd.com/supply_data.php"
TOTAL_SUPPLY = 100_000_000_000

COLOR_STAKING = "#15c785"
COLOR_TREASURY = "#f6a85b"
COLOR_BURNED = "#ff4c4c"
COLOR_REMAINING_WEDGE = "#e6e6e6"
COLOR_REMAINING_LEGEND = "#bcbcbc"
COLOR_TRADEABLE_WEDGE = "#F3D8A2"
COLOR_CONTRACTS_WEDGE = "#C9B6FF"
COLOR_CONTRACTS_LEGEND = "#8bcfb4"

user_calls = defaultdict(deque)
chat_calls = defaultdict(deque)
debt_cycles = {
    "private": {"last_ts": 0, "cycle_id": 0},
    "public": {"last_ts": 0, "cycle_id": 0},
}






# ==== DEBT AND INFLATION CONFIG ====
TREASURY_DEBT_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
    "v2/accounting/od/debt_to_penny?page[size]=1&sort=-record_date"
)

ANNUAL_INFLATION = 0.03
BASE_LOSS_SINCE_2020 = 0.255

DATA_PATH = "/app/data"
HISTORY_PATH = f"{DATA_PATH}/history.json"

WATCH_STATE_PATH = f"{DATA_PATH}/watch_state.json"

# ==== ONCHAIN WATCHERS (staking + burn) ====
SCAN_APIKEY = (os.environ.get("ETHERSCAN_APIKEY", "") or os.environ.get("BASESCAN_API_KEY", "")).strip()
TUSD_CONTRACT_ADDRESS = (os.environ.get("TUSD_CONTRACT_ADDRESS", "").strip() or "0x3d5e487B21E0569048c4D1A60E98C36e1B09DB07")

STAKING_CONTRACT_ADDRESS = "0x2a70a42BC0524aBCA9Bff59a51E7aAdB575DC89A"
BURN_ADDRESS = "0x000000000000000000000000000000000000dEaD"

DEPOSIT_METHOD_ID = "0xe2bbb158"  # deposit(uint256,uint256)
HANDLEOPS_METHOD_ID = "0x1fad948c"  # handleOps(tuple[] ops,address beneficiary)

# Common Base token addresses (used for /scan buy estimation)
WETH_ADDRESS = "0x4200000000000000000000000000000000000006"
USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDT_ADDRESS = "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2"
TUSD_DECIMALS = 18

WATCH_POLL_SEC = int(os.environ.get("WATCH_POLL_SEC", "180"))
MAX_EVENT_EMOJIS = 150
EMOJI_PER_TUSD = 10_000_000  # fallback default
STAKE_EMOJI_PER_TUSD = int(os.environ.get("STAKE_EMOJI_PER_TUSD", str(EMOJI_PER_TUSD)))
BURN_EMOJI_PER_TUSD = int(os.environ.get("BURN_EMOJI_PER_TUSD", str(EMOJI_PER_TUSD)))
BUYBACK_EMOJI_PER_TUSD = int(os.environ.get("BUYBACK_EMOJI_PER_TUSD", str(EMOJI_PER_TUSD)))
BUYBACK_CONTRACT_ADDRESS = (os.environ.get("BUYBACK_CONTRACT_ADDRESS", "").strip() or "0x3dbF93D110C677A1c063A600cb42940262f3BBd6")
BUYBACK_METHOD_ID = (os.environ.get("BUYBACK_METHOD_ID", "").strip().lower() or "0x79a9fa1c")
BUYBACK_MEDIA_PATH = (os.environ.get("BUYBACK_MEDIA_PATH", "").strip() or "assets/buyback.png")
BASE_RPC_URL = os.environ.get("BASE_RPC_URL", "").strip()
BASESCAN_API_URL = (os.environ.get("BASESCAN_API_URL", "").strip() or "https://api.basescan.org/api")
RPC_LOG_CHUNK = int(os.environ.get("RPC_LOG_CHUNK", "2000"))  # blocks per eth_getLogs query

if not BASE_RPC_URL:
    raise RuntimeError("BASE_RPC_URL environment variable is not set")

# ERC20 Transfer event topic
TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


# Event images (place these files on disk)
EVENT_IMAGES = {
    "staked": {"path": "assets/staked.png"},
    "staked_pool5": {"path": "assets/staked2.png"},
    "burned": {"path": "assets/burned.png"},
}

HISTORY_MAX_ITEMS = 2000
DEBT_UPDATE_DELAY_SEC = 300

DEBT_UPDATE_LOCK_SECONDS = 6 * 3600  # 6h cooldown global

# ==== FALLBACK DEBT GROWTH (when Treasury hasn't updated yet)
# ~ $1T per year ≈ $31,700 per second
FALLBACK_DEBT_GROWTH_PER_SECOND = 31_700






CPI_TABLE = {
    # --- Historical (coarse, pre-BLS) ---
    1800: 12.6,
    1810: 13.5,
    1820: 13.1,
    1830: 13.0,
    1840: 13.9,
    1850: 13.1,
    1860: 13.1,
    1870: 14.0,
    1880: 12.5,
    1890: 12.9,
    1900: 13.8,
    1910: 15.1,

    # --- Official CPI era (annual) ---
    1913: 9.9,
    1914: 10.0,
    1915: 10.1,
    1916: 10.9,
    1917: 12.8,
    1918: 15.0,
    1919: 17.3,
    1920: 20.0,
    1921: 17.9,
    1922: 16.8,
    1923: 17.1,
    1924: 17.1,
    1925: 17.5,
    1926: 17.7,
    1927: 17.4,
    1928: 17.1,
    1929: 17.1,
    1930: 17.1,
    1931: 15.2,
    1932: 13.7,
    1933: 13.0,
    1934: 13.4,
    1935: 13.7,
    1936: 13.9,
    1937: 14.4,
    1938: 14.1,
    1939: 13.9,
    1940: 14.0,
    1941: 14.7,
    1942: 16.3,
    1943: 17.3,
    1944: 17.6,
    1945: 18.0,
    1946: 19.5,
    1947: 22.3,
    1948: 24.1,
    1949: 23.8,
    1950: 24.1,
    1951: 26.0,
    1952: 26.5,
    1953: 26.7,
    1954: 26.9,
    1955: 26.8,
    1956: 27.2,
    1957: 28.1,
    1958: 28.9,
    1959: 29.1,
    1960: 29.6,
    1961: 29.9,
    1962: 30.2,
    1963: 30.6,
    1964: 31.0,
    1965: 31.5,
    1966: 32.4,
    1967: 33.4,
    1968: 34.8,
    1969: 36.7,
    1970: 38.8,
    1971: 40.5,
    1972: 41.8,
    1973: 44.4,
    1974: 49.3,
    1975: 53.8,
    1976: 56.9,
    1977: 60.6,
    1978: 65.2,
    1979: 72.6,
    1980: 82.4,
    1981: 90.9,
    1982: 96.5,
    1983: 99.6,
    1984: 103.9,
    1985: 107.6,
    1986: 109.6,
    1987: 113.6,
    1988: 118.3,
    1989: 124.0,
    1990: 130.7,
    1991: 136.2,
    1992: 140.3,
    1993: 144.5,
    1994: 148.2,
    1995: 152.4,
    1996: 156.9,
    1997: 160.5,
    1998: 163.0,
    1999: 166.6,
    2000: 172.2,
    2001: 177.1,
    2002: 179.9,
    2003: 184.0,
    2004: 188.9,
    2005: 195.3,
    2006: 201.6,
    2007: 207.3,
    2008: 215.3,
    2009: 214.5,
    2010: 218.1,
    2011: 224.9,
    2012: 229.6,
    2013: 233.0,
    2014: 236.7,
    2015: 237.0,
    2016: 240.0,
    2017: 245.1,
    2018: 251.1,
    2019: 255.7,
    2020: 258.8,
    2021: 270.9,
    2022: 292.7,
    2023: 305.3,
    2024: 318.0,
    2025: 324.8,  # proxy
}


# ==== MEMES FOR DEBT FLOW ====
MEMES = {
    "debt_main": {
        "number": 433,
        "path": "assets/433.jpeg",
    },
    "debt_update": {
        "number": 6,
        "path": "assets/6.jpeg",
    },
}


MEME_BYTES = {}

ANTI_SPAM_ENABLED = False

WATCHER_STARTED = False
WATCH_LOCK = asyncio.Lock()










async def global_command_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return



    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return

    user_id = user.id
    chat_id = chat.id

    # 👑 ADMIN BYPASS
    if user_id == ADMIN_ID:
        return

    # 🚫 ANTI-SPAM OFF
    if not ANTI_SPAM_ENABLED:
        return



    now = time.time()

    # =====================================================
    # 👤 USER RATE LIMIT (PRIVATE + PUBLIC)
    # =====================================================
    q_user = user_calls[user_id]
    while q_user and now - q_user[0] > USER_WINDOW:
        q_user.popleft()

    if len(q_user) >= USER_MAX_CALLS:
        await update.message.reply_text(SLOWDOWN_MSG)
        return

    q_user.append(now)

    # =====================================================
    # 🔒 PRIVATE CHAT → STOP HERE
    # =====================================================
    if chat.type == "private":
        return

    # =====================================================
    # 🔒 PUBLIC CHAT PROTECTION
    # =====================================================
    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        return

    # =====================================================
    # 💬 CHAT RATE LIMIT (PUBLIC ONLY)
    # =====================================================
    q_chat = chat_calls[chat_id]
    while q_chat and now - q_chat[0] > CHAT_WINDOW:
        q_chat.popleft()

    if len(q_chat) >= CHAT_MAX_CALLS:
        await update.message.reply_text(SLOWDOWN_MSG)
        return

    q_chat.append(now)








def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_data_dir():
    os.makedirs(DATA_PATH, exist_ok=True)
    if not os.path.exists(HISTORY_PATH):
        _atomic_write_json(HISTORY_PATH, {"debt_snapshots": []})


def _atomic_write_json(path: str, obj: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"debt_snapshots": []}


def load_history() -> dict:
    ensure_data_dir()
    data = _read_json(HISTORY_PATH)
    if "debt_snapshots" not in data or not isinstance(data["debt_snapshots"], list):
        data = {"debt_snapshots": []}
    return data


def append_debt_snapshot(snapshot: dict):
    data = load_history()
    snaps = data["debt_snapshots"]
    snaps.append(snapshot)
    if len(snaps) > HISTORY_MAX_ITEMS:
        data["debt_snapshots"] = snaps[-HISTORY_MAX_ITEMS:]
    _atomic_write_json(HISTORY_PATH, data)


def fmt_usd_int(n: int) -> str:
    return f"${n:,}"


def fetch_latest_us_debt() -> dict:
    r = requests.get(TREASURY_DEBT_URL, timeout=15)
    r.raise_for_status()
    j = r.json()
    row = j["data"][0]
    record_date = row.get("record_date") or ""
    amt_str = row["tot_pub_debt_out_amt"]
    debt_int = int(float(amt_str))
    return {
        "record_date": record_date,
        "debt": debt_int,
        "fetched_at": utc_now_iso(),
        "source": "treasury_fiscaldata_debt_to_penny",
    }


def estimate_rate_per_second_from_history() -> float:
    data = load_history()
    snaps = data.get("debt_snapshots", [])
    if len(snaps) < 2:
        return 0.0

    # Find last two snapshots with different record_date and valid debt
    last = None
    prev = None
    for s in reversed(snaps):
        if not isinstance(s, dict):
            continue
        if "debt" not in s or "record_date" not in s or "fetched_at" not in s:
            continue
        if last is None:
            last = s
            continue
        if s.get("record_date") != last.get("record_date"):
            prev = s
            break

    if not last or not prev:
        return 0.0

    try:
        last_date = datetime.fromisoformat(last["fetched_at"])
        prev_date = datetime.fromisoformat(prev["fetched_at"])
    except Exception:
        return 0.0

    dt = (last_date - prev_date).total_seconds()
    if dt <= 0:
        return 0.0

    delta = int(last["debt"]) - int(prev["debt"])
    return float(delta) / float(dt)



def inflation_loss(amount: int, annual_rate: float) -> tuple[int, int, int]:
    daily = amount * (annual_rate / 365.0)
    monthly = amount * (annual_rate / 12.0)
    return int(daily), int(monthly)


def is_allowed_chat(update: Update) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    return chat.type == "private" or ALLOWED_CHAT_ID == 0 or chat.id == ALLOWED_CHAT_ID







async def debt_or_inflation_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed_chat(update):
        return

    msg = update.message
    if not msg:
        return

    chat = update.effective_chat
    scope = "private" if chat.type == "private" else "public"
    cycle = debt_cycles[scope]

    now = time.time()

    # -------------------------------------------------
    # Fetch current debt
    # -------------------------------------------------
    try:
        latest = fetch_latest_us_debt()
    except Exception:
        await msg.reply_text("Error fetching US debt data.")
        return

    append_debt_snapshot(latest)

    debt_now = latest["debt"]
    debt_fmt = fmt_usd_int(debt_now)

    daily_loss, monthly_loss = inflation_loss(100_000, ANNUAL_INFLATION)
    yearly_loss = int(100_000 * ANNUAL_INFLATION)
    loss_since_2020 = int(100_000 * BASE_LOSS_SINCE_2020)

    caption = (
        f"🇺🇸 US NATIONAL DEBT\n"
        f"{debt_fmt}\n\n"
        f"📉 Annual inflation: {ANNUAL_INFLATION*100:.1f}%\n\n"
        f"The disastrous effects:\n\n"
        f"$100,000 right now will lose...\n\n"
        f"<pre>"
        f"{fmt_usd_int(daily_loss):<10} today\n"
        f"{fmt_usd_int(monthly_loss):<10} in one month\n"
        f"{fmt_usd_int(yearly_loss):<10} in one year\n"
        f"{fmt_usd_int(loss_since_2020):<10} since 2020 alone\n"
        f"</pre>\n\n"
        f"Your wealth is melting as we speak!\n\n"
        f"Embrace the Unstable ⚡️\n\n"
        f"PS. Reply to this msg with a specific year and I'll show you something else..."
    )


    sent = await msg.reply_photo(
        photo=MEME_BYTES["debt_main"],
        caption=caption,
        parse_mode="HTML"
    )



    # -------------------------------------------------
    # Decide whether to schedule updates
    # -------------------------------------------------
    if scope == "public":
        if now - cycle["last_ts"] < DEBT_UPDATE_LOCK_SECONDS:
            return

    cycle["last_ts"] = now
    cycle["cycle_id"] += 1
    cycle_id = cycle["cycle_id"]

    for delay, label in (
        (300, "5 MIN UPDATE"),
        (3600, "1 HOUR UPDATE"),
        (86400, "24 HOUR UPDATE"),
    ):
        context.application.create_task(
            debt_update_after_delay(
                context=context,
                chat_id=sent.chat_id,
                reply_to_message_id=sent.message_id,
                initial_debt=debt_now,
                initial_record_date=latest.get("record_date", ""),
                delay=delay,
                label=label,
                cycle_id=cycle_id,
                scope=scope,
            )
        )







async def debt_update_after_delay(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    reply_to_message_id: int,
    initial_debt: int,
    initial_record_date: str,
    delay: int,
    label: str,
    cycle_id: int,
    scope: str,
):
    await asyncio.sleep(delay)

    # ❌ Cancel silently if cycle was reset or replaced
    if cycle_id != debt_cycles[scope]["cycle_id"]:
        return

    try:
        latest = fetch_latest_us_debt()
        append_debt_snapshot(latest)
        new_debt = latest["debt"]
        new_record_date = latest.get("record_date", "")
    except Exception:
        return

    growth = new_debt - initial_debt
    used_estimate = False

    # Treasury didn't move → estimate
    if new_record_date == initial_record_date and growth == 0:
        rate = estimate_rate_per_second_from_history()
        if rate <= 0:
            rate = FALLBACK_DEBT_GROWTH_PER_SECOND

        growth = int(rate * delay)
        new_debt = initial_debt + growth
        used_estimate = True

    # -----------------------------
    # Human-readable time line
    # -----------------------------
    if label.startswith("5"):
        time_line = "in just 5 minutes."
    elif label.startswith("1 HOUR"):
        time_line = "in just 1 hour."
    elif label.startswith("24"):
        time_line = "in just 24 hours."
    else:
        time_line = ""

    caption = (
        f"⏱ {label}\n\n"
        f"US National Debt now:\n"
        f"{fmt_usd_int(new_debt)}\n\n"
        f"Grown by:\n"
        f"{fmt_usd_int(growth)}\n"
        f"{time_line}\n\n"
        f"Money printer go brrr"
    )


    await context.bot.send_photo(
        chat_id=chat_id,
        photo=MEME_BYTES["debt_update"],
        caption=caption,
        reply_to_message_id=reply_to_message_id,
    )


    # 🔓 Release lock only for public cycles
    if label == "24 HOUR UPDATE" and scope == "public":
        debt_cycles["public"]["last_ts"] = time.time()


def generate_inflation_chart(
    start_year: int,
    start_amount: int,
    end_year: int,
    end_amount: int,
):
    years = [start_year]
    values = [start_amount]

    base_cpi = CPI_TABLE[start_year]
    span = end_year - start_year

    for frac in (0.25, 0.5, 0.75):
        y = int(start_year + span * frac)

        nearest = max(k for k in CPI_TABLE if k <= y)
        cpi_y = CPI_TABLE[nearest]

        val = int(start_amount * (base_cpi / cpi_y))
        years.append(y)
        values.append(val)

    years.append(end_year)
    values.append(end_amount)

    fig, ax = plt.subplots(figsize=(7, 4))

    ax.plot(years, values, linewidth=2)
    ax.scatter(years, values, zorder=3)

    ax.set_title("Purchasing Power of $100,000", fontsize=14, fontweight="bold")
    ax.set_xlabel("Year")
    ax.set_ylabel("USD equivalent")

    # 👉 margen superior dinámico (20%)
    max_val = max(values)
    ax.set_ylim(0, max_val * 1.25)

    ax.ticklabel_format(style="plain", axis="y")

    for x, v in zip(years, values):
        ax.annotate(
            f"${v:,}",
            (x, v),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9,
            bbox=dict(
                boxstyle="round,pad=0.3",
                fc="white",
                ec="#cfcfcf",
                lw=0.8,
                alpha=0.95,
            ),
            zorder=4,
        )

    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", dpi=160)
    buf.seek(0)
    plt.close(fig)

    return buf







async def year_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed_chat(update):
        return

    msg = update.message
    if not msg or not msg.reply_to_message:
        return

    raw = (msg.text or "").strip()
    if not raw.isdigit():
        return

    year = int(raw)
    current_year = datetime.now().year

    # -------------------------------------------------
    # Range validation
    # -------------------------------------------------
    if year < 1800 or year > current_year:
        await msg.reply_text(
            "Year out of range. Available data starts at 1800."
        )
        return

    # -------------------------------------------------
    # CPI availability check (nearest year <= requested)
    # -------------------------------------------------
    available_years = [y for y in CPI_TABLE if y <= year]
    if not available_years:
        await msg.reply_text(
            "I do not have CPI data for that year. Available from 1800 onwards."
        )
        return

    nearest_year = max(available_years)

    start_amount = 100_000
    today_year = 2025

    past_cpi = CPI_TABLE[nearest_year]
    today_cpi = CPI_TABLE[today_year]

    equivalent = int(start_amount * (past_cpi / today_cpi))

    # -------------------------------------------------
    # Chart generation (CRASH-SAFE)
    # -------------------------------------------------
    try:
        chart = generate_inflation_chart(
            start_year=year,
            start_amount=start_amount,
            end_year=today_year,
            end_amount=equivalent,
        )
    except Exception:
        await msg.reply_text(
            "Something went wrong generating the chart. Please try another year."
        )
        return

    note = ""
    if nearest_year != year:
        note = f"\n(CPI adjusted using {nearest_year} data)"

    caption = (
        f"$100,000 in {year} ≈ {fmt_usd_int(equivalent)} today{note}\n\n"
        f"Get out of the fiat scam asap!!\n\n"
        f"Embrace the Unstable ⚡️"
    )

    await context.bot.send_photo(
        chat_id=msg.chat_id,
        photo=chart,
        caption=caption,
        reply_to_message_id=msg.message_id,
    )







async def reset_debt_cycle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user

    # 🔒 SOLO ADMIN
    if not user or user.id != ADMIN_ID:
        return

    # 🔄 RESET PRIVATE
    debt_cycles["private"]["last_ts"] = 0
    debt_cycles["private"]["cycle_id"] += 1

    # 🔄 RESET PUBLIC
    debt_cycles["public"]["last_ts"] = 0
    debt_cycles["public"]["cycle_id"] += 1

    await update.message.reply_text(
        "✅ Debt cycles reset.\nPrivate and public updates cleared."
    )















# ==== Helpers ====

def pretty(n: float) -> str:
    if n >= 1e9:
        s = f"{n/1e9:.2f}".rstrip("0").rstrip(".")
        return f"{s}B"
    if n >= 1e6:
        s = f"{n/1e6:.2f}".rstrip("0").rstrip(".")
        return f"{s}M"
    return f"{n:,.0f}"

def draw_center_text_fit(ax, text: str, inner_radius=0.65, max_frac=0.85, weight="bold"):
    fig = ax.figure
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    lo, hi = 6, 64
    best = lo

    (x1, _y) = ax.transData.transform((-inner_radius, 0))
    (x2, _y) = ax.transData.transform((inner_radius, 0))
    avail_px = (x2 - x1) * max_frac

    for _ in range(12):
        mid = (lo + hi) // 2
        t = ax.text(0, 0, text, ha="center", va="center", fontsize=mid, fontweight=weight)
        bb = t.get_window_extent(renderer=renderer)
        t.remove()

        if bb.width <= avail_px:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    ax.text(0, 0, text, ha="center", va="center", fontsize=best, fontweight=weight)

def draw_legend_under(ax, labels, values, colors, y0=-0.14, line_h=0.08, font_size=12, total=None, text_color="#333"):
    if total is None:
        total = sum(values) if sum(values) > 0 else 1.0

    for i, (lbl, v, col) in enumerate(zip(labels, values, colors)):
        y = y0 - i * line_h

        ax.add_patch(Rectangle(
            (0.03, y - 0.018), 0.025, 0.025,
            transform=ax.transAxes,
            clip_on=False,
            facecolor=col,
            edgecolor="none"
        ))

        ax.text(0.08, y, lbl, transform=ax.transAxes, ha="left", va="center",
                fontsize=font_size, color=text_color)
        ax.text(0.95, y, f"{pretty(v)} ({(v/total*100):.1f}%)", transform=ax.transAxes,
                ha="right", va="center", fontsize=font_size, color=text_color)

def fetch_supply_data():
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TurboBot/1.0; +https://turbousd.com)"
    }
    r = requests.get(SUPPLY_DATA_URL, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()

# ==== Figure generators ====

def generate_stats_combo_image():
    j = fetch_supply_data()
    staking = j["staking_adjusted"]
    treasury = j["treasury"]
    burned = j["burned"]
    remaining = max(TOTAL_SUPPLY - (staking + treasury + burned), 0)

    circulating = j["circulating"]
    in_contracts = j["inContracts"]

    fig, (axL, axR) = plt.subplots(nrows=1, ncols=2, figsize=(12, 6))
    plt.subplots_adjust(top=0.84, bottom=0.38, wspace=0.25)

    # Left chart
    left_values = [staking, treasury, burned, remaining]
    left_labels = ["Staking", "Treasury", "Burned", "Remaining"]
    left_colors_wedges = [COLOR_STAKING, COLOR_TREASURY, COLOR_BURNED, COLOR_REMAINING_WEDGE]
    left_colors_legend = [COLOR_STAKING, COLOR_TREASURY, COLOR_BURNED, COLOR_REMAINING_LEGEND]

    axL.pie(left_values, colors=left_colors_wedges, startangle=90, wedgeprops=dict(width=0.35))
    axL.set(aspect="equal")
    axL.set_title("Supply Distribution", fontsize=18, fontweight="bold", pad=16)
    draw_legend_under(axL, left_labels, left_values, left_colors_legend)

    # Right chart
    right_values = [circulating, in_contracts, burned]
    right_labels = ["Circululating", "In Contracts", "Burned"]
    right_colors_wedges = [COLOR_TRADEABLE_WEDGE, COLOR_CONTRACTS_WEDGE, COLOR_BURNED]
    right_colors_legend = [COLOR_TRADEABLE_WEDGE, COLOR_CONTRACTS_LEGEND, COLOR_BURNED]

    axR.pie(right_values, colors=right_colors_wedges, startangle=90, wedgeprops=dict(width=0.35))
    axR.set(aspect="equal")
    axR.set_title("Supply Status", fontsize=18, fontweight="bold", pad=16)

    center_text = f"{round(circulating/1e9)}B/100B"
    draw_center_text_fit(axR, center_text)

    draw_legend_under(axR, right_labels, right_values, right_colors_legend)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=170)
    buf.seek(0)
    plt.close(fig)
    return buf

def generate_circulating_donut():
    j = fetch_supply_data()
    circulating = j["circulating"]
    in_contracts = j["inContracts"]

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.pie(
        [circulating, in_contracts, j["burned"]],
        colors=[COLOR_TRADEABLE_WEDGE, COLOR_CONTRACTS_WEDGE, COLOR_BURNED],
        startangle=90,
        wedgeprops=dict(width=0.35)
    )
    ax.set(aspect="equal")
    draw_center_text_fit(ax, f"{round(circulating/1e9)}B/100B")
    ax.set_title("Supply Status", fontsize=18, fontweight="bold", pad=16)

    handles = [
        Line2D([0], [0], marker="s", linestyle="none", markersize=12, markerfacecolor=COLOR_TRADEABLE_WEDGE),
        Line2D([0], [0], marker="s", linestyle="none", markersize=12, markerfacecolor=COLOR_CONTRACTS_WEDGE),
        Line2D([0], [0], marker="s", linestyle="none", markersize=12, markerfacecolor=COLOR_BURNED)
    ]
    ax.legend(handles, ["Circulating", "In Contracts", "Burned"],
              loc="lower center", bbox_to_anchor=(0.5, -0.05), ncol=3, frameon=False)

    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    return buf

# ==== Telegram commands ====

_PRICE_CACHE = {"ts": 0, "price": 0, "fdv": 0}
_HOLDERS_CACHE = {}


def _dex_price_fdv(token: str):
    try:
        import time
        import requests

        if time.time() - _PRICE_CACHE["ts"] < 60:
            return _PRICE_CACHE["price"], _PRICE_CACHE["fdv"]

        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}", timeout=20)
        r.raise_for_status()
        j = r.json()
        pairs = j.get("pairs") or []
        if not pairs:
            return 0.0, 0.0

        pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
        p = pairs[0]

        price = float(p.get("priceUsd") or 0)
        fdv = float(p.get("fdv") or 0)

        _PRICE_CACHE.update({"ts": time.time(), "price": price, "fdv": fdv})
        return price, fdv
    except Exception:
        return 0.0, 0.0


def _basescan_token_holder_count(token_addr: str):
    try:
        token = (token_addr or "").strip().lower()
        if not token or not token.startswith("0x"):
            return None

        now = time.time()
        c = _HOLDERS_CACHE.get(token)

        if c and (now - float(c.get("ts") or 0.0)) <= 3600:
            v = int(c.get("count") or 0)
            return v if v > 0 else None

        try:
            params = {
                "chainid": 8453,
                "module": "token",
                "action": "tokenholdercount",
                "contractaddress": token,
            }
            if SCAN_APIKEY:
                params["apikey"] = SCAN_APIKEY

            r = requests.get("https://api.etherscan.io/v2/api", params=params, timeout=20)
            r.raise_for_status()
            j = r.json() if r.content else {}

            if str(j.get("status") or "") == "1":
                res = j.get("result")
                n = int(str(res)) if res is not None else 0
                if n > 0:
                    _HOLDERS_CACHE[token] = {"ts": now, "count": n}
                    return n
        except Exception:
            pass

        try:
            url = f"https://basescan.org/token/{token}"
            r = requests.get(
                url,
                timeout=20,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            r.raise_for_status()
            html = r.text or ""

            html = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
            html = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", html)

            text = re.sub(r"(?s)<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()

            start_idx = text.lower().find("overview")
            search_space = text[start_idx:] if start_idx != -1 else text

            m = re.search(r"\bHolders\b\s*([0-9][0-9,]*)\b", search_space, re.IGNORECASE)
            if not m:
                m = re.search(r"\bHolders\b\s*([0-9][0-9,]*)\b", text, re.IGNORECASE)

            if m:
                n = int(m.group(1).replace(",", ""))
                if n > 0:
                    _HOLDERS_CACHE[token] = {"ts": now, "count": n}
                    return n
        except Exception:
            pass

    except Exception:
        return None

    return None


def _erc20_balance_of(token: str, holder: str) -> int:
    selector = "0x70a08231"
    holder_padded = holder.lower().replace("0x", "").rjust(64, "0")
    data = selector + holder_padded
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": token, "data": data}, "latest"],
    }
    r = requests.post(BASE_RPC_URL, json=payload, timeout=15)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(str(j["error"]))
    return int(j["result"], 16)


def _staking_contract_balance() -> float:
    try:
        bal_int = _erc20_balance_of(
            TUSD_CONTRACT_ADDRESS,
            "0x2a70a42BC0524aBCA9Bff59a51E7aAdB575DC89A"
        )
        return bal_int / (10 ** TUSD_DECIMALS)
    except Exception:
        return 0.0


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    j = fetch_supply_data()

    treasury = float(j.get("treasury", 0) or 0)
    staked = float(j.get("staking_adjusted", 0) or 0)
    burned = float(j.get("burned", 0) or 0)

    treasury_pct = (treasury / TOTAL_SUPPLY * 100) if TOTAL_SUPPLY else 0
    staked_pct = (staked / TOTAL_SUPPLY * 100) if TOTAL_SUPPLY else 0
    burned_pct = (burned / TOTAL_SUPPLY * 100) if TOTAL_SUPPLY else 0

    price, fdv = _dex_price_fdv(TUSD_CONTRACT_ADDRESS)

    holders = _basescan_token_holder_count(TUSD_CONTRACT_ADDRESS)
    holders = int(holders or 0)

    staking_contract_balance = _staking_contract_balance()
    extra_from_treasury = max(0.0, staking_contract_balance - staked)
    extra_pct = (extra_from_treasury / TOTAL_SUPPLY * 100) if TOTAL_SUPPLY else 0

    caption = (
        f"📊 <b>₸USD Stats</b>\n"
        f"Current price: ${price:,.8f}\n"
        f"FDV: ${fdv:,.0f}\n"
        f"Holders: {holders:,}\n\n"
        f"⚡️ <b>Treasury</b>\n"
        f"{pretty(treasury)} ₸USD (${treasury * price:,.0f} · {treasury_pct:.2f}%)\n\n"
        f"🔒 <b>Staked</b>\n"
        f"{pretty(staked)} ₸USD (${staked * price:,.0f} · {staked_pct:.2f}%)\n"
        f"+{pretty(extra_from_treasury)} ₸USD from treasury (${extra_from_treasury * price:,.0f} · {extra_pct:.2f}%)\n\n"
        f"🔥 <b>Burned</b>\n"
        f"{pretty(burned)} ₸USD (${burned * price:,.0f} · {burned_pct:.2f}%)"
    )

    img = generate_stats_combo_image()

    await update.message.reply_photo(
        photo=img,
        caption=caption,
        parse_mode="HTML"
    )

async def circsupply(update: Update, context: ContextTypes.DEFAULT_TYPE):

    image = generate_circulating_donut()
    await update.message.reply_photo(photo=image, caption="📊 Circulating Supply Overview")



def preload_memes():
    # Base memes
    for key, m in MEMES.items():
        try:
            with open(m["path"], "rb") as f:
                MEME_BYTES[key] = f.read()
        except Exception as e:
            raise RuntimeError(f"Failed to load meme {key}: {e}")

    # Event images (staking and burn notifications)
    for key, m in EVENT_IMAGES.items():
        try:
            with open(m["path"], "rb") as f:
                MEME_BYTES[f"event_{key}"] = f.read()
        except Exception as e:
            raise RuntimeError(f"Failed to load event image {key}: {e}")


def generate_printer_card():
    per_sec = FALLBACK_DEBT_GROWTH_PER_SECOND
    per_min = per_sec * 60
    per_day = per_sec * 86400

    fig, ax = plt.subplots(figsize=(6, 4))
    fig.patch.set_facecolor("#0f0f0f")
    ax.set_facecolor("#0f0f0f")
    ax.axis("off")

    # -----------------------------
    # Title
    # -----------------------------
    ax.text(
        0.5, 0.86,
        "MONEY PRINTER SPEED",
        ha="center",
        va="center",
        fontsize=18,
        fontweight="bold",
        color="white"
    )

    block_gap = 0.25
    label_offset = 0.085

    # -----------------------------
    # PER SECOND
    # -----------------------------
    y_sec = 0.67

    ax.text(
        0.5, y_sec,
        f"${per_sec:,.0f}",
        ha="center",
        va="center",
        fontsize=22,
        fontweight="bold",
        color="#FFD23F"
    )
    ax.text(
        0.5, y_sec - label_offset,
        "PER SECOND",
        ha="center",
        va="center",
        fontsize=10,
        color="#bbbbbb"
    )

    # -----------------------------
    # PER MINUTE
    # -----------------------------
    y_min = y_sec - block_gap

    ax.text(
        0.5, y_min,
        f"${per_min:,.0f}",
        ha="center",
        va="center",
        fontsize=26,
        fontweight="bold",
        color="#FF9F1C"
    )
    ax.text(
        0.5, y_min - label_offset,
        "PER MINUTE",
        ha="center",
        va="center",
        fontsize=10,
        color="#bbbbbb"
    )

    # -----------------------------
    # PER DAY
    # -----------------------------
    y_day = y_min - block_gap

    ax.text(
        0.5, y_day,
        f"${per_day:,.0f}",
        ha="center",
        va="center",
        fontsize=32,
        fontweight="bold",
        color="#FF4C4C"
    )
    ax.text(
        0.5, y_day - label_offset,
        "PER DAY",
        ha="center",
        va="center",
        fontsize=10,
        color="#bbbbbb"
    )

    # -----------------------------
    # Footer (more space from PER DAY)
    # -----------------------------
    ax.text(
        0.5, y_day - 0.26,
        "Money printer go brrr",
        ha="center",
        va="center",
        fontsize=16,
        color="white"
    )

    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", dpi=170)
    buf.seek(0)
    plt.close(fig)

    return buf






def generate_printer_counter(elapsed_sec: int):
    printed = int(FALLBACK_DEBT_GROWTH_PER_SECOND * elapsed_sec)

    fig, ax = plt.subplots(figsize=(6, 3.2))
    fig.patch.set_facecolor("#0f0f0f")
    ax.set_facecolor("#0f0f0f")
    ax.axis("off")

    ax.text(
        0.5, 0.80,
        f"IN THE LAST {elapsed_sec} SECONDS",
        ha="center",
        va="center",
        fontsize=14,
        color="#bbbbbb",
        fontweight="bold"
    )

    ax.text(
        0.5, 0.48,
        f"${printed:,.0f}",
        ha="center",
        va="center",
        fontsize=42,
        fontweight="bold",
        color="#FF4C4C"
    )

    ax.text(
        0.5, 0.20,
        "added to US debt",
        ha="center",
        va="center",
        fontsize=16,
        color="white"
    )

    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", dpi=170)
    buf.seek(0)
    plt.close(fig)

    return buf




async def printer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed_chat(update):
        return

    msg = update.message
    if not msg:
        return

    # 1️⃣ First message (speed card)
    card = generate_printer_card()
    sent = await msg.reply_photo(
        photo=card,
        caption="🖨️ Money printer is ON.\nWatch it closely."
    )

    # 2️⃣ Second message after 30 seconds (counter)
    async def delayed_counter():
        await asyncio.sleep(30)

        counter_img = generate_printer_counter(elapsed_sec=30)

        await context.bot.send_photo(
            chat_id=sent.chat_id,
            photo=counter_img,
            caption="⏱️ While you were reading the message above…",
            reply_to_message_id=sent.message_id
        )

    context.application.create_task(delayed_counter())






async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if not chat or not user:
        return

    # 🔒 PRIVATE + OWNER ONLY
    if chat.type != "private" or user.id != ADMIN_ID:
        return

    await chat.send_message("♻️ Restarting bot...")
    await asyncio.sleep(1)

    os._exit(1)  # 👈 fuerza restart del proceso










    

# ==== Onchain event watchers (staking + burn) ====

def _read_watch_state() -> dict:
    ensure_data_dir()
    try:
        with open(WATCH_STATE_PATH, "r", encoding="utf-8") as f:
            j = json.load(f)
            if not isinstance(j, dict):
                return {}
            return j
    except Exception:
        return {}

def _write_watch_state(state: dict):
    ensure_data_dir()
    _atomic_write_json(WATCH_STATE_PATH, state)

def _rpc_call(method: str, params: list) -> dict | None:
    """
    Basic JSON-RPC call with minimal retries.
    Returns parsed JSON dict on success, or None on failure.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(BASE_RPC_URL, json=payload, timeout=25)
            r.raise_for_status()
            j = r.json()
            # JSON-RPC can return {"error": ...}
            if isinstance(j, dict) and j.get("error"):
                last_err = j.get("error")
                raise RuntimeError(f"rpc_error: {last_err}")
            return j
        except Exception as e:
            last_err = str(e)
            # small backoff
            time.sleep(0.35 * (attempt + 1))

    # Keep it silent, but print helps when you look at logs
    try:
        print(f"[rpc] failed method={method} err={last_err}")
    except Exception:
        pass
    return None

def _rpc_latest_block() -> int | None:
    j = _rpc_call("eth_blockNumber", [])
    if not j or "result" not in j:
        return None
    try:
        return int(j["result"], 16)
    except Exception:
        return None


def _rpc_get_receipt(tx_hash: str) -> dict | None:
    j = _rpc_call("eth_getTransactionReceipt", [tx_hash])
    if not j or "result" not in j or not isinstance(j["result"], dict):
        return None
    return j["result"]

def _rpc_get_tx(tx_hash: str) -> dict | None:
    j = _rpc_call("eth_getTransactionByHash", [tx_hash])
    if not j or "result" not in j or not isinstance(j["result"], dict):
        return None
    return j["result"]

def _hex_word_to_int(word_hex: str) -> int:
    s = (word_hex or "").lower()
    if s.startswith("0x"):
        s = s[2:]
    s = s.strip()
    if not s:
        return 0
    try:
        return int(s, 16)
    except Exception:
        return 0

def _extract_pool_id_from_input(input_hex: str) -> int | None:
    """Extract staking poolId from transaction input data.

    Supported:
    - deposit(uint256 amount, uint256 poolId) selector 0xe2bbb158 (direct call)
    - handleOps(...) relayed calls where deposit() calldata appears inside input
    """
    data = (input_hex or "").lower()
    if not data or data == "0x":
        return None
    if not data.startswith("0x"):
        data = "0x" + data
    # Direct deposit: 4-byte selector + 2 words
    if data.startswith(DEPOSIT_METHOD_ID):
        payload = data[2 + 8:]  # strip 0x + selector
        # needs 64 chars for amount + 64 for pool
        if len(payload) < 128:
            return None
        pool_word = payload[64:128]
        pool_id = _hex_word_to_int(pool_word)
        return pool_id

    # Relayed: try to find embedded deposit selector inside calldata
    dep_sel = DEPOSIT_METHOD_ID[2:]  # without 0x
    idx = data.find(dep_sel)
    if idx != -1:
        # idx is position in the full string (includes 0x), so start after selector
        start = idx + len(dep_sel)
        payload = data[start:]
        # After selector: amount word + pool word
        if len(payload) < 128:
            return None
        pool_word = payload[64:128]
        pool_id = _hex_word_to_int(pool_word)
        return pool_id

    # Fallback: sometimes poolId shows up as a standalone word.
    # Scan first ~20 words after selector and pick the first that matches known pools.
    payload = data[2 + 8:] if len(data) >= 10 else data[2:]
    for i in range(0, min(len(payload), 64 * 20), 64):
        word = payload[i:i+64]
        if len(word) != 64:
            break
        n = _hex_word_to_int(word)
        if n in (3, 4, 5):
            return n

    return None

def _lock_until_label_from_pool_id(pool_id: int | None) -> str | None:
    if pool_id == 3:
        return "January 1, 2027"
    if pool_id == 4:
        return "January 1, 2028"
    if pool_id == 5:
        return "January 1, 2030"
    return None

def _stake_lock_until_from_tx_hash(tx_hash: str) -> str | None:
    tx = _rpc_get_tx(tx_hash)
    if not tx:
        return None
    pool_id = _extract_pool_id_from_input(tx.get("input") or "")
    return _lock_until_label_from_pool_id(pool_id)

    try:
        return int(j["result"], 16)
    except Exception:
        return None

def _topic_address(addr: str) -> str:
    a = (addr or "").lower()
    if not a.startswith("0x"):
        a = "0x" + a
    a = a[2:].rjust(64, "0")
    return "0x" + a

def _normalize_basescan_log(log: dict) -> dict:
    """
    Basescan/Etherscan getLogs can return decimal strings for blockNumber/logIndex.
    Normalize them into hex strings like JSON-RPC.
    """
    out = dict(log)

    def _to_hex_str(v):
        if v is None:
            return "0x0"
        if isinstance(v, int):
            return hex(v)
        s = str(v).strip()
        if s.startswith("0x"):
            return s.lower()
        # decimal string
        try:
            return hex(int(s))
        except Exception:
            return "0x0"

    out["blockNumber"] = _to_hex_str(out.get("blockNumber"))
    out["logIndex"] = _to_hex_str(out.get("logIndex"))
    out["transactionIndex"] = _to_hex_str(out.get("transactionIndex"))
    return out


def _basescan_get_logs(from_block: int, to_block: int) -> list[dict]:
    """
    Fallback to BaseScan getLogs API when RPC eth_getLogs fails.

    Important: Some explorers can be picky about topic operators.
    We query ALL Transfer logs (topic0) for the token in the block range,
    then filter topic2 locally.
    Requires BASESCAN_API_KEY or ETHERSCAN_APIKEY in env.
    """
    if not SCAN_APIKEY:
        return []

    params = {
        "module": "logs",
        "action": "getLogs",
        "fromBlock": str(from_block),
        "toBlock": str(to_block),
        "address": TUSD_CONTRACT_ADDRESS,
        "topic0": TRANSFER_TOPIC0,
        "apikey": SCAN_APIKEY,
    }

    try:
        r = requests.get(BASESCAN_API_URL, params=params, timeout=25)
        r.raise_for_status()
        j = r.json()
        if not isinstance(j, dict):
            return []
        res = j.get("result")
        if not isinstance(res, list):
            return []
        return [_normalize_basescan_log(x) for x in res if isinstance(x, dict)]
    except Exception as e:
        try:
            print(f"[basescan] getLogs failed err={e}")
        except Exception:
            pass
        return []


def _rpc_get_logs(from_block: int, to_block: int, topic2_to: str) -> list[dict]:
    """
    JSON-RPC eth_getLogs with limited retries.

    Important: an empty result list is a valid response and should NOT trigger retries or fallback.
    Fallback to BaseScan getLogs is used ONLY if the RPC call fails repeatedly.
    """
    params = [{
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
        "address": TUSD_CONTRACT_ADDRESS,
        "topics": [TRANSFER_TOPIC0, None, topic2_to],
    }]

    last_ok = None
    # Retry only on RPC failure/invalid payload, not on empty results
    for attempt in range(2):
        j = _rpc_call("eth_getLogs", params)
        if j and "result" in j and isinstance(j["result"], list):
            last_ok = j["result"]
            return last_ok  # may be empty, that's fine
        time.sleep(0.25 * (attempt + 1))

    # Fallback: BaseScan getLogs ONLY if RPC failed
    logs = _basescan_get_logs(from_block, to_block)
    out: list[dict] = []
    t2 = (topic2_to or "").lower()
    for lg in logs:
        topics = lg.get("topics") or []
        if not isinstance(topics, list) or len(topics) < 3:
            continue
        if str(topics[2]).lower() == t2:
            out.append(lg)
    return out

def _dexscreener_mcap_usd(token_address: str) -> int | None:
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}", timeout=20)
        r.raise_for_status()
        j = r.json()
        pairs = j.get("pairs") or []
        if not pairs:
            return None

        base_pairs = [p for p in pairs if (p.get("chainId") or "").lower() == "base"]
        best = base_pairs[0] if base_pairs else pairs[0]

        mc = best.get("marketCap")
        if mc is None:
            mc = best.get("fdv")
        if mc is None:
            return None
        return int(float(mc))
    except Exception:
        return None

def _fmt_int(n: int) -> str:
    return f"{n:,}"

def _fmt_tusd(amount_float: float) -> str:
    try:
        n = int(round(float(amount_float)))
    except Exception:
        n = 0
    return f"{n:,}"


def _event_token_price_usd() -> float:
    try:
        price, _fdv = _dex_price_fdv(TUSD_CONTRACT_ADDRESS)
        return float(price or 0.0)
    except Exception:
        return 0.0


def _fmt_tusd_with_usd(amount_float: float) -> str:
    usd = int(round(float(amount_float) * _event_token_price_usd()))
    return f"{_fmt_tusd(amount_float)} (${usd:,})"


def _emoji_block_flat(emoji: str, amount_float: float, amount_per_emoji: int | float | None = None) -> str:
    per_emoji = float(amount_per_emoji or EMOJI_PER_TUSD or 1)
    count = int(amount_float // per_emoji)

    if amount_float > 0 and count == 0:
        count = 1

    count = min(count, MAX_EVENT_EMOJIS)

    return emoji * count


def _emoji_block(emoji: str, amount_float: float, amount_per_emoji: int | float | None = None) -> str:
    return _emoji_block_flat(emoji, amount_float, amount_per_emoji)


def _short_addr(a: str) -> str:
    if not a or len(a) < 10:
        return a
    return f"{a[:6]}...{a[-4:]}"



def _norm_addr(a: str) -> str:
    s = (a or "").strip().lower()
    if not s:
        return ""
    if not s.startswith("0x"):
        s = "0x" + s
    return s

def _aggregate_net_deltas_from_receipt(receipt: dict, token_addresses: dict[str, int]) -> dict[str, dict[str, int]]:
    """Return dict[token_addr][holder_addr] = net_delta_int."""
    deltas: dict[str, dict[str, int]] = {}
    for t in token_addresses.keys():
        deltas[_norm_addr(t)] = {}

    logs = receipt.get("logs") or []
    if not isinstance(logs, list):
        return deltas

    for lg in logs:
        if not isinstance(lg, dict):
            continue
        addr = _norm_addr(lg.get("address") or "")
        if addr not in deltas:
            continue
        topics = lg.get("topics") or []
        if not isinstance(topics, list) or len(topics) < 3:
            continue
        if str(topics[0]).lower() != TRANSFER_TOPIC0:
            continue
        from_addr = _topic_to_addr(str(topics[1]))
        to_addr = _topic_to_addr(str(topics[2]))
        try:
            value_int = int(lg.get("data", "0x0"), 16)
        except Exception:
            continue

        deltas[addr][from_addr] = int(deltas[addr].get(from_addr, 0)) - value_int
        deltas[addr][to_addr] = int(deltas[addr].get(to_addr, 0)) + value_int

    return deltas

def _pick_biggest_positive_holder(token_deltas: dict[str, int], exclude: set[str]) -> tuple[str | None, int]:
    best_addr = None
    best_delta = 0
    for addr, delta in (token_deltas or {}).items():
        a = _norm_addr(addr)
        if not a or a in exclude:
            continue
        if int(delta) > best_delta:
            best_delta = int(delta)
            best_addr = a
    return best_addr, best_delta

def _classify_transfer_log_kind(log: dict) -> tuple[str, str, float] | None:
    """Classify a single ERC20 Transfer log as stake/burn and return (kind, from_addr, amount_float)."""
    if _norm_addr(log.get("address") or "") != _norm_addr(TUSD_CONTRACT_ADDRESS):
        return None
    topics = log.get("topics") or []
    if not isinstance(topics, list) or len(topics) < 3:
        return None
    if str(topics[0]).lower() != TRANSFER_TOPIC0:
        return None
    from_addr = _topic_to_addr(str(topics[1]))
    to_addr = _topic_to_addr(str(topics[2]))
    try:
        value_wei = int(log.get("data", "0x0"), 16)
    except Exception:
        return None
    amount = value_wei / (10 ** TUSD_DECIMALS)

    if _norm_addr(to_addr) == _norm_addr(STAKING_CONTRACT_ADDRESS):
        return ("stake", from_addr, float(amount))
    if _norm_addr(to_addr) == _norm_addr(BURN_ADDRESS):
        return ("burn", from_addr, float(amount))
    return None

def _build_stake_caption(tx_hash: str, amount_float: float, from_addr: str) -> str:
    emoji_lines = _emoji_block_flat("🔒", amount_float).strip()
    tx_url = f"https://basescan.org/tx/{tx_hash}"
    from_url = f"https://basescan.org/address/{from_addr}"

    lock_until = _stake_lock_until_from_tx_hash(tx_hash)
    lock_line = f"\nLocked until: {lock_until}" if lock_until else "\nLocked until: Unknown"

    caption = (
        "<b>₸USD STAKED!</b>\n\n"
        f"{emoji_lines}\n\n"
        f"₸USD: {_fmt_tusd(amount_float)} (<a href=\"{tx_url}\">Tx</a>)\n"
        f"Wallet: <a href=\"{from_url}\">{_short_addr(from_addr)}</a>"
        f"{lock_line}"
    )
    return caption

def _build_burn_caption(tx_hash: str, amount_float: float, from_addr: str) -> str:
    emoji_lines = _emoji_block_flat("🔥", amount_float).strip()
    tx_url = f"https://basescan.org/tx/{tx_hash}"
    from_url = f"https://basescan.org/address/{from_addr}"
    caption = (
        "<b>₸USD BURNED!</b>\n\n"
        f"{emoji_lines}\n\n"
        f"₸USD: {_fmt_tusd(amount_float)} (<a href=\"{tx_url}\">Tx</a>)\n"
        f"Wallet: <a href=\"{from_url}\">{_short_addr(from_addr)}</a>"
    )
    return caption

def _build_buy_caption(tx_hash: str, amount_float: float, buyer_addr: str) -> str:
    emoji_lines = _emoji_block_flat("🟢", amount_float).strip()
    tx_url = f"https://basescan.org/tx/{tx_hash}"
    buyer_url = f"https://basescan.org/address/{buyer_addr}"
    caption = (
        "<b>₸USD BOUGHT!</b>\n\n"
        f"{emoji_lines}\n\n"
        f"₸USD: {_fmt_tusd(amount_float)} (<a href=\"{tx_url}\">Tx</a>)\n"
        f"Wallet: <a href=\"{buyer_url}\">{_short_addr(buyer_addr)}</a>"
    )
    return caption

def _buy_from_receipt_simple(tx_hash: str, receipt: dict) -> dict | None:
    """Heuristic buy detector for /scan <tx_hash>.

    It looks for the biggest positive ₸USD delta to a non-excluded address.
    """
    token_addresses = {
        TUSD_CONTRACT_ADDRESS: TUSD_DECIMALS,
        USDC_ADDRESS: 6,
        USDT_ADDRESS: 6,
        WETH_ADDRESS: 18,
    }
    deltas = _aggregate_net_deltas_from_receipt(receipt, token_addresses)
    tusd_deltas = deltas.get(_norm_addr(TUSD_CONTRACT_ADDRESS)) or {}
    if not tusd_deltas:
        return None

    exclude = {
        _norm_addr(TUSD_CONTRACT_ADDRESS),
        _norm_addr(STAKING_CONTRACT_ADDRESS),
        _norm_addr(BURN_ADDRESS),
        _norm_addr(WETH_ADDRESS),
        _norm_addr(USDC_ADDRESS),
        _norm_addr(USDT_ADDRESS),
    }

    buyer, buyer_delta_int = _pick_biggest_positive_holder(tusd_deltas, exclude)
    if not buyer or buyer_delta_int <= 0:
        return None

    amount = buyer_delta_int / (10 ** TUSD_DECIMALS)

    # If this was actually a stake, ignore it here (stake/burn handled separately)
    # Stake transfers would have to == staking contract, not buyer.
    return {"buyer": buyer, "amount": float(amount)}


def _is_buyback_tx(tx_hash: str, receipt: dict | None = None, tx: dict | None = None) -> bool:
    tx = tx or _rpc_get_tx(tx_hash)
    if not tx:
        return False
    tx_to = _norm_addr(tx.get("to") or "")
    tx_input = (tx.get("input") or "").lower()
    if tx_to != _norm_addr(BUYBACK_CONTRACT_ADDRESS):
        return False
    if not tx_input.startswith(BUYBACK_METHOD_ID):
        return False
    if receipt is not None:
        status = str(receipt.get("status") or "").lower()
        if status not in ("0x1", "1"):
            return False
    return True


def _buyback_from_receipt(tx_hash: str, receipt: dict) -> dict | None:
    tx = _rpc_get_tx(tx_hash)
    if not _is_buyback_tx(tx_hash, receipt=receipt, tx=tx):
        return None

    total_int = 0
    for lg in (receipt.get("logs") or []):
        if not isinstance(lg, dict):
            continue
        if _norm_addr(lg.get("address") or "") != _norm_addr(TUSD_CONTRACT_ADDRESS):
            continue
        topics = lg.get("topics") or []
        if not isinstance(topics, list) or len(topics) < 3:
            continue
        if str(topics[0]).lower() != TRANSFER_TOPIC0:
            continue
        to_addr = _topic_to_addr(str(topics[2]))
        if _norm_addr(to_addr) != _norm_addr(BUYBACK_CONTRACT_ADDRESS):
            continue
        try:
            total_int += int(lg.get("data", "0x0"), 16)
        except Exception:
            continue

    if total_int <= 0:
        return None

    return {"wallet": _norm_addr(BUYBACK_CONTRACT_ADDRESS), "amount": total_int / (10 ** TUSD_DECIMALS)}


async def _send_event_media(app, chat_id: int, caption: str, *, photo_bytes: bytes | None = None, media_path: str | None = None):
    if photo_bytes:
        await app.bot.send_photo(chat_id=chat_id, photo=photo_bytes, caption=caption, parse_mode="HTML")
        return

    media_path = (media_path or "").strip()
    if media_path and os.path.exists(media_path):
        lower = media_path.lower()
        with open(media_path, "rb") as f:
            if lower.endswith((".mp4", ".gif.mp4", ".gif", ".webm")):
                await app.bot.send_animation(chat_id=chat_id, animation=f, caption=caption, parse_mode="HTML")
            else:
                await app.bot.send_photo(chat_id=chat_id, photo=f, caption=caption, parse_mode="HTML")
        return

    await app.bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML", disable_web_page_preview=True)


async def _post_stake_event(app, tx_hash: str, amount_float: float, from_addr: str):
    if not ALLOWED_CHAT_ID:
        return

    caption = _build_stake_caption(tx_hash, amount_float, from_addr)
    photo = MEME_BYTES.get("event_staked") or MEME_BYTES.get("debt_update")

    lock_until = _stake_lock_until_from_tx_hash(tx_hash)
    if lock_until == "January 1, 2030":
        photo = MEME_BYTES.get("event_staked_pool5", photo)

    await _send_event_media(app, ALLOWED_CHAT_ID, caption, photo_bytes=photo)


async def _post_burn_event(app, tx_hash: str, amount_float: float, from_addr: str):
    if not ALLOWED_CHAT_ID:
        return

    caption = _build_burn_caption(tx_hash, amount_float, from_addr)
    photo = MEME_BYTES.get("event_burned") or MEME_BYTES.get("debt_update")
    await _send_event_media(app, ALLOWED_CHAT_ID, caption, photo_bytes=photo)


async def _post_buyback_event(app, tx_hash: str, amount_float: float):
    if not ALLOWED_CHAT_ID:
        return

    caption = _build_buyback_caption(tx_hash, amount_float)
    await _send_event_media(app, ALLOWED_CHAT_ID, caption, media_path=BUYBACK_MEDIA_PATH)

def _topic_to_addr(topic_hex: str) -> str:
    t = topic_hex.lower()
    if t.startswith("0x"):
        t = t[2:]
    return "0x" + t[-40:]


async def _process_transfer_logs(app, logs: list[dict], last_block: int, last_log_index: int, kind: str) -> tuple[int, int]:
    max_block = last_block
    max_index = last_log_index

    def _key(l):
        try:
            b = int(l.get("blockNumber", "0x0"), 16)
        except Exception:
            b = 0
        try:
            i = int(l.get("logIndex", "0x0"), 16)
        except Exception:
            i = 0
        return (b, i)

    for log in sorted(logs, key=_key):
        try:
            b = int(log.get("blockNumber", "0x0"), 16)
            i = int(log.get("logIndex", "0x0"), 16)
        except Exception:
            continue

        if b < last_block or (b == last_block and i <= last_log_index):
            continue

        tx_hash = log.get("transactionHash") or ""
        if not tx_hash:
            continue

        # 🔹 EXTRAER FROM ADDRESS
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue

        from_addr = _topic_to_addr(topics[1])

        # 🔹 EXTRAER MONTO
        try:
            value_wei = int(log.get("data", "0x0"), 16)
        except Exception:
            continue

        amount = value_wei / (10 ** TUSD_DECIMALS)

        # 🔹 POST EVENTO
        if kind == "stake":
            await _post_stake_event(app, tx_hash, amount, from_addr)
        else:
            await _post_burn_event(app, tx_hash, amount, from_addr)

        if b > max_block or (b == max_block and i > max_index):
            max_block = b
            max_index = i

    return max_block, max_index


def _append_seen_id(state: dict, eid: str, max_seen: int) -> None:
    """
    Keep seen_event_ids as an ORDERED rolling list (stable trimming).
    Also maintain a set in memory (created in monitor) for fast lookup.
    """
    seen = state.get("seen_event_ids")
    if not isinstance(seen, list):
        seen = []
    seen.append(eid)
    if len(seen) > max_seen:
        seen[:] = seen[-max_seen:]
    state["seen_event_ids"] = seen


async def _process_transfer_logs_dedup_cursor(
    *,
    app,
    kind: str,
    logs: list[dict],
    last_block: int,
    last_log_index: int,
    seen_set: set,
    state: dict,
    max_seen: int,
) -> tuple[int, int]:
    """
    Process logs ordered by (blockNumber, logIndex).
    Dedup by (kind, tx_hash, logIndex).
    Advance cursor EXACTLY to the max processed (block, logIndex).
    """

    def _sort_key(log: dict) -> tuple[int, int]:
        try:
            b = int(log.get("blockNumber", "0x0"), 16)
        except Exception:
            b = 0
        try:
            i = int(log.get("logIndex", "0x0"), 16)
        except Exception:
            i = 0
        return (b, i)

    max_block = last_block
    max_index = last_log_index

    posted = 0

    for log in sorted(logs, key=_sort_key):
        try:
            b = int(log.get("blockNumber", "0x0"), 16)
            i = int(log.get("logIndex", "0x0"), 16)
        except Exception:
            continue

        # Skip already-processed cursor region
        if b < last_block or (b == last_block and i <= last_log_index):
            continue

        tx_hash = (log.get("transactionHash") or "").strip()
        if not tx_hash:
            continue

        log_index_hex = (log.get("logIndex") or "").lower()
        eid = f"{kind}:{tx_hash.lower()}:{log_index_hex}"

        # Dedup
        if eid in seen_set:
            # Even if dup, still advance cursor safely
            if b > max_block or (b == max_block and i > max_index):
                max_block = b
                max_index = i
            continue

        topics = log.get("topics") or []
        if not isinstance(topics, list) or len(topics) < 3:
            continue

        from_addr = _topic_to_addr(topics[1])

        try:
            value_wei = int(log.get("data", "0x0"), 16)
        except Exception:
            continue

        amount = value_wei / (10 ** TUSD_DECIMALS)

        if kind == "stake":
            await _post_stake_event(app, tx_hash, amount, from_addr)
        else:
            await _post_burn_event(app, tx_hash, amount, from_addr)

        posted += 1

        seen_set.add(eid)
        _append_seen_id(state, eid, max_seen)

        if b > max_block or (b == max_block and i > max_index):
            max_block = b
            max_index = i

    return max_block, max_index, posted
async def monitor_stake_and_burn(app):
    if not ALLOWED_CHAT_ID:
        return
    if not TUSD_CONTRACT_ADDRESS:
        return

    # How many blocks to wait before considering logs "stable"
    confirmations = int(os.environ.get("WATCH_CONFIRMATIONS", "2"))

    # Always rescan a small overlap window to never miss events due to ordering,
    # node inconsistency, or same block log ordering edge cases.
    overlap_blocks = int(os.environ.get("WATCH_OVERLAP_BLOCKS", "5"))

    # Keep a rolling cache of processed event IDs to prevent duplicates
    max_seen = int(os.environ.get("WATCH_MAX_SEEN_EVENTS", "4000"))

    stake_to_topic = _topic_address(STAKING_CONTRACT_ADDRESS)
    burn_to_topic = _topic_address(BURN_ADDRESS)
    buyback_to_topic = _topic_address(BUYBACK_CONTRACT_ADDRESS)

    state = _read_watch_state()

    # Cursor blocks (we will rescan overlap anyway)
    stake_last_block = int(state.get("stake_last_block", 0) or 0)
    burn_last_block = int(state.get("burn_last_block", 0) or 0)
    buyback_last_block = int(state.get("buyback_last_block", 0) or 0)

    # Rolling dedupe cache
    seen = state.get("seen_event_ids") or []
    if not isinstance(seen, list):
        seen = []
    seen_set = set(x for x in seen if isinstance(x, str))

    # Initialize cursors to "safe latest" to avoid historical spam on first boot
    if stake_last_block == 0 or burn_last_block == 0 or buyback_last_block == 0:
        latest = _rpc_latest_block()
        if latest is None:
            return
        safe_latest = max(latest - confirmations, 0)
        if stake_last_block == 0:
            stake_last_block = safe_latest
        if burn_last_block == 0:
            burn_last_block = safe_latest
        if buyback_last_block == 0:
            buyback_last_block = safe_latest

        state["stake_last_block"] = stake_last_block
        state["burn_last_block"] = burn_last_block
        state["buyback_last_block"] = buyback_last_block
        state["seen_event_ids"] = list(seen_set)[-max_seen:]
        _write_watch_state(state)

    def _event_id(kind: str, tx_hash: str, log_index_hex: str) -> str:
        return f"{kind}:{tx_hash.lower()}:{(log_index_hex or '').lower()}"

    def _sort_key(log: dict) -> tuple[int, int]:
        try:
            b = int(log.get("blockNumber", "0x0"), 16)
        except Exception:
            b = 0
        try:
            i = int(log.get("logIndex", "0x0"), 16)
        except Exception:
            i = 0
        return (b, i)

    async def _handle_logs(kind: str, logs: list[dict]):
        nonlocal seen_set

        for log in sorted(logs, key=_sort_key):
            tx_hash = (log.get("transactionHash") or "").strip()
            if not tx_hash:
                continue

            log_index_hex = log.get("logIndex", "0x0")
            eid = _event_id(kind, tx_hash, log_index_hex)
            if eid in seen_set:
                continue

            topics = log.get("topics") or []
            if not isinstance(topics, list) or len(topics) < 3:
                continue

            # topics[1] is "from" for ERC20 Transfer
            from_addr = _topic_to_addr(topics[1])

            try:
                value_wei = int(log.get("data", "0x0"), 16)
            except Exception:
                continue

            amount = value_wei / (10 ** TUSD_DECIMALS)

            try:
                if kind == "stake":
                    await _post_stake_event(app, tx_hash, amount, from_addr)
                elif kind == "burn":
                    await _post_burn_event(app, tx_hash, amount, from_addr)
                else:
                    buyback = _buyback_from_receipt(tx_hash, _rpc_get_receipt(tx_hash) or {})
                    if not buyback:
                        continue
                    await _post_buyback_event(app, tx_hash, float(buyback["amount"]))
            except Exception as e:
                print(f"[watcher] post failed kind={kind} tx={tx_hash} logIndex={log_index_hex} err={e}")
                continue

            seen_set.add(eid)

    while True:
        try:
            # NOTE: do not hold WATCH_LOCK during RPC calls (can block /scan)
            latest = _rpc_latest_block()
            if latest is None:
                await asyncio.sleep(WATCH_POLL_SEC)
                continue

            safe_latest = max(latest - confirmations, 0)

            # If chain is not ahead enough, just wait
            if safe_latest <= 0:
                await asyncio.sleep(WATCH_POLL_SEC)
                continue

            # Rescan a small overlap window to ensure no misses
            stake_from = max(stake_last_block - overlap_blocks, 0)
            burn_from = max(burn_last_block - overlap_blocks, 0)
            buyback_from = max(buyback_last_block - overlap_blocks, 0)

            # -------------------------
            # Stake transfers (to staking contract)
            # -------------------------
            b = stake_from
            while b <= safe_latest:
                end_b = min(b + RPC_LOG_CHUNK - 1, safe_latest)
                logs = _rpc_get_logs(b, end_b, stake_to_topic)
                if logs:
                    await _handle_logs("stake", logs)
                b = end_b + 1

            # Advance cursor to safe_latest (we rely on overlap + dedupe for safety)
            stake_last_block = safe_latest

            # -------------------------
            # Burn transfers (to dead)
            # -------------------------
            b = burn_from
            while b <= safe_latest:
                end_b = min(b + RPC_LOG_CHUNK - 1, safe_latest)
                logs = _rpc_get_logs(b, end_b, burn_to_topic)
                if logs:
                    await _handle_logs("burn", logs)
                b = end_b + 1

            burn_last_block = safe_latest

            # -------------------------
            # Buyback transfers (₸USD transferred into buyback contract through buyback())
            # -------------------------
            b = buyback_from
            while b <= safe_latest:
                end_b = min(b + RPC_LOG_CHUNK - 1, safe_latest)
                logs = _rpc_get_logs(b, end_b, buyback_to_topic)
                if logs:
                    await _handle_logs("buyback", logs)
                b = end_b + 1

            buyback_last_block = safe_latest

            # Persist state
            # Keep only the newest max_seen entries
            seen_list = list(seen_set)
            if len(seen_list) > max_seen:
                seen_list = seen_list[-max_seen:]
                seen_set = set(seen_list)

            state["stake_last_block"] = stake_last_block
            state["burn_last_block"] = burn_last_block
            state["buyback_last_block"] = buyback_last_block
            state["seen_event_ids"] = seen_list
            _write_watch_state(state)

        except Exception as e:
            print("Watcher tick failed:", e)

        await asyncio.sleep(WATCH_POLL_SEC)



# ==== Admin command: manual rescan ====

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat or not user:
        return

    # Private + admin only
    if chat.type != "private" or user.id != ADMIN_ID:
        return

    confirmations = int(os.environ.get("WATCH_CONFIRMATIONS", "2"))
    max_seen = int(os.environ.get("WATCH_MAX_SEEN_EVENTS", "4000"))

    stake_to_topic = _topic_address(STAKING_CONTRACT_ADDRESS)
    burn_to_topic = _topic_address(BURN_ADDRESS)

    def _parse_blocks_csv(s: str) -> list[int]:
        parts = [p.strip() for p in (s or "").split(",") if p.strip()]
        out: list[int] = []
        for p in parts:
            try:
                out.append(int(p))
            except Exception:
                continue
        # Unique, keep order
        seen_local = set()
        uniq: list[int] = []
        for b in out:
            if b in seen_local:
                continue
            seen_local.add(b)
            uniq.append(b)
        return uniq

    # Mode: /scan <tx_hash>  -> detect buy/stake/burn for a single transaction and DM you the alert
    if len(context.args) == 1:
        tx_hash = (context.args[0] or "").strip()
        if re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash):
            await msg.reply_text("Scanning tx hash...")

            receipt = _rpc_get_receipt(tx_hash)
            if not receipt:
                await msg.reply_text("Transaction not found (no receipt).")
                return

            # 1) Try BUYBACK first
            buyback = None
            try:
                buyback = _buyback_from_receipt(tx_hash, receipt)
            except Exception:
                buyback = None

            if buyback:
                caption = _build_buyback_caption(tx_hash, float(buyback["amount"]))
                await _send_event_media(context.application, ADMIN_ID, caption, media_path=BUYBACK_MEDIA_PATH)
                await msg.reply_text("Buyback alert sent in DM.")
                return

            # 2) Try generic BUY
            buy = None
            try:
                buy = _buy_from_receipt_simple(tx_hash, receipt)
            except Exception:
                buy = None

            if buy:
                caption = _build_buy_caption(tx_hash, float(buy["amount"]), str(buy["buyer"]))
                await context.application.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=caption,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                await msg.reply_text("Buy alert sent in DM.")
                return

            # 3) Try stake/burn (based on Transfer logs)
            detected: list[str] = []
            try:
                for lg in (receipt.get("logs") or []):
                    if not isinstance(lg, dict):
                        continue
                    classified = _classify_transfer_log_kind(lg)
                    if not classified:
                        continue
                    kind, from_addr, amount_float = classified
                    if kind == "stake":
                        caption = _build_stake_caption(tx_hash, float(amount_float), str(from_addr))
                        photo = MEME_BYTES.get("event_staked")

                        lock_until = _stake_lock_until_from_tx_hash(tx_hash)
                        if lock_until == "January 1, 2030":
                            photo = MEME_BYTES.get("event_staked_pool5", photo)
                    else:
                        caption = _build_burn_caption(tx_hash, float(amount_float), str(from_addr))
                        photo = MEME_BYTES.get("event_burned")

                    # For /scan we DM the same alert format, including the event image when available.
                    await _send_event_media(context.application, ADMIN_ID, caption, photo_bytes=photo)
                    detected.append(kind)
            except Exception:
                detected = []

            if detected:
                kinds = ", ".join(sorted(set(detected)))
                await msg.reply_text(f"{kinds.capitalize()} alert sent in DM.")
            else:
                await msg.reply_text("That tx is not detected as a buy, stake, or burn (no alert sent).")
            return

    # Modes:
    #   /scan            -> last 2000 blocks
    #   /scan 4000       -> last N blocks
    #   /scan b 42523377 -> scan specific block
    #   /scan b 1,2,3    -> scan specific blocks (csv)
    mode = (context.args[0].lower() if context.args else "")
    is_block_mode = (mode == "b")

    # Read state under lock, but do not hold lock during RPC calls
    async with WATCH_LOCK:
        state = _read_watch_state()
        seen_list = state.get("seen_event_ids") or []
        if not isinstance(seen_list, list):
            seen_list = []
        seen_set = set(x for x in seen_list if isinstance(x, str))

    latest = _rpc_latest_block()
    if latest is None:
        await msg.reply_text("RPC error: could not fetch latest block.")
        return

    safe_latest = max(latest - confirmations, 0)

    posted_stake = 0
    posted_burn = 0

    # -------------------------
    # Block mode: scan exact blocks
    # -------------------------
    if is_block_mode:
        if len(context.args) < 2:
            await msg.reply_text("Usage: /scan b <block> or /scan b <block1,block2,...>")
            return

        blocks_str = " ".join(context.args[1:]).replace(" ", "")
        blocks = _parse_blocks_csv(blocks_str)   
        if not blocks:
            await msg.reply_text("No valid blocks provided.")
            return

        blocks_safe = [b for b in blocks if 0 <= b <= safe_latest]
        if not blocks_safe:
            await msg.reply_text("Blocks are not confirmed yet.")
            return

        for blk in blocks_safe:
            logs = _rpc_get_logs(blk, blk, stake_to_topic)
            if logs:
                _mb, _mi, posted = await _process_transfer_logs_dedup_cursor(
                    app=context.application,
                    kind="stake",
                    logs=logs,
                    last_block=0,
                    last_log_index=-1,
                    seen_set=seen_set,
                    state=state,
                    max_seen=max_seen,
                )
                posted_stake += posted

            logs = _rpc_get_logs(blk, blk, burn_to_topic)
            if logs:
                _mb, _mi, posted = await _process_transfer_logs_dedup_cursor(
                    app=context.application,
                    kind="burn",
                    logs=logs,
                    last_block=0,
                    last_log_index=-1,
                    seen_set=seen_set,
                    state=state,
                    max_seen=max_seen,
                )
                posted_burn += posted

        # Persist state
        seen_after = state.get("seen_event_ids") or []
        if not isinstance(seen_after, list):
            seen_after = []
        if len(seen_after) > max_seen:
            state["seen_event_ids"] = seen_after[-max_seen:]

        # Do not move cursors backwards
        state["stake_last_block"] = max(int(state.get("stake_last_block", 0) or 0), max(blocks_safe))
        state["burn_last_block"] = max(int(state.get("burn_last_block", 0) or 0), max(blocks_safe))

        async with WATCH_LOCK:
            _write_watch_state(state)

        await msg.reply_text(
            "Scan complete.\n"
            f"Blocks: {', '.join(str(b) for b in blocks_safe)}\n"
            f"Posted stake events: {posted_stake}\n"
            f"Posted burn events: {posted_burn}"
        )
        return

    # -------------------------
    # Range mode: scan last N blocks
    # -------------------------
    n = 2000
    if context.args:
        try:
            n = int(context.args[0])
        except Exception:
            n = 2000

    n = max(1, min(n, 50_000))

    from_block = max(safe_latest - n + 1, 0)
    to_block = safe_latest

    b = from_block
    while b <= to_block:
        end_b = min(b + RPC_LOG_CHUNK - 1, to_block)
        logs = _rpc_get_logs(b, end_b, stake_to_topic)
        if logs:
            _mb, _mi, posted = await _process_transfer_logs_dedup_cursor(
                app=context.application,
                kind="stake",
                logs=logs,
                last_block=0,
                last_log_index=-1,
                seen_set=seen_set,
                state=state,
                max_seen=max_seen,
            )
            posted_stake += posted
        b = end_b + 1

    b = from_block
    while b <= to_block:
        end_b = min(b + RPC_LOG_CHUNK - 1, to_block)
        logs = _rpc_get_logs(b, end_b, burn_to_topic)
        if logs:
            _mb, _mi, posted = await _process_transfer_logs_dedup_cursor(
                app=context.application,
                kind="burn",
                logs=logs,
                last_block=0,
                last_log_index=-1,
                seen_set=seen_set,
                state=state,
                max_seen=max_seen,
            )
            posted_burn += posted
        b = end_b + 1

    state["stake_last_block"] = max(int(state.get("stake_last_block", 0) or 0), to_block)
    state["burn_last_block"] = max(int(state.get("burn_last_block", 0) or 0), to_block)

    seen_after = state.get("seen_event_ids") or []
    if not isinstance(seen_after, list):
        seen_after = []
    if len(seen_after) > max_seen:
        state["seen_event_ids"] = seen_after[-max_seen:]

    async with WATCH_LOCK:
        _write_watch_state(state)

    await msg.reply_text(
        "Scan complete.\n"
        f"Range: {from_block} to {to_block}\n"
        f"Posted stake events: {posted_stake}\n"
        f"Posted burn events: {posted_burn}"
    )


async def parse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat or not user:
        return

    # Private + admin only
    if chat.type != "private" or user.id != ADMIN_ID:
        return

    # Accept text either as arguments, or as a reply to another message
    text = ""
    if context.args:
        text = " ".join(context.args).strip()
    elif msg.reply_to_message and (msg.reply_to_message.text or msg.reply_to_message.caption):
        text = (msg.reply_to_message.text or msg.reply_to_message.caption or "").strip()
    else:
        await msg.reply_text("Reply to a message that contains the BaseScan text, or pass the text after /parse.")
        return

    # Normalize whitespace
    t = "\n".join([ln.strip() for ln in text.splitlines() if ln.strip()])

    # Tx hash
    tx_hash = ""
    m = re.search(r"(0x[a-fA-F0-9]{64})", t)
    if m:
        tx_hash = m.group(1).lower()

    # Block
    block = None
    m = re.search(r"\bBlock:\s*([0-9]{1,12})\b", t, flags=re.IGNORECASE)
    if m:
        try:
            block = int(m.group(1))
        except Exception:
            block = None

    # Kind
    kind = ""
    if re.search(r"\bStaking\b", t, flags=re.IGNORECASE) or re.search(re.escape(STAKING_CONTRACT_ADDRESS), t, flags=re.IGNORECASE):
        kind = "stake"
    if re.search(r"\bdEaD\b", t, flags=re.IGNORECASE) or re.search(re.escape(BURN_ADDRESS), t, flags=re.IGNORECASE):
        # If both appear, prefer burn
        kind = "burn" if kind != "stake" else "burn"

    if not kind:
        await msg.reply_text("Could not detect if this is stake or burn.")
        return

    # Amount: prefer "For 1,000,000" style
    amount = None
    m = re.search(r"\bFor\s*([0-9][0-9,\.]*)\b", t, flags=re.IGNORECASE)
    if m:
        raw = m.group(1).replace(",", "")
        try:
            amount = float(raw)
        except Exception:
            amount = None

    # Fallback: "Transfer 1.00 M"
    if amount is None:
        m = re.search(r"\bTransfer\s*([0-9][0-9,\.]*)\s*([kKmMbB])?\b", t, flags=re.IGNORECASE)
        if m:
            num = m.group(1).replace(",", "")
            suf = (m.group(2) or "").lower()
            mult = 1.0
            if suf == "k":
                mult = 1e3
            elif suf == "m":
                mult = 1e6
            elif suf == "b":
                mult = 1e9
            try:
                amount = float(num) * mult
            except Exception:
                amount = None

    if amount is None:
        await msg.reply_text("Could not parse amount.")
        return

    # From address: try to find a 0x address after "From:" line
    from_addr = ""
    m = re.search(r"\bFrom:\s*\n?([^\n]+)", t, flags=re.IGNORECASE)
    if m:
        line = m.group(1)
        m2 = re.search(r"(0x[a-fA-F0-9]{40})", line)
        if m2:
            from_addr = m2.group(1)
    if not from_addr:
        # fallback: first address in text that is 40 chars
        m = re.search(r"(0x[a-fA-F0-9]{40})", t)
        if m:
            from_addr = m.group(1)

    if not tx_hash:
        await msg.reply_text("Could not find tx hash.")
        return

    # Dedup in watch state
    eid = f"manual:{kind}:{tx_hash}"
    async with WATCH_LOCK:
        state = _read_watch_state()
        seen = state.get("seen_event_ids") or []
        if not isinstance(seen, list):
            seen = []
        if eid in set(x for x in seen if isinstance(x, str)):
            await msg.reply_text("Already recorded.")
            return

        # Append to seen list
        seen.append(eid)
        max_seen = int(os.environ.get("WATCH_MAX_SEEN_EVENTS", "4000"))
        if len(seen) > max_seen:
            seen = seen[-max_seen:]
        state["seen_event_ids"] = seen

        # Store manual events for audit
        manual = state.get("manual_events")
        if not isinstance(manual, list):
            manual = []
        manual.append({
            "kind": kind,
            "tx_hash": tx_hash,
            "block": block,
            "amount": amount,
            "from": from_addr,
            "ts": utc_now_iso(),
        })
        if len(manual) > 2000:
            manual = manual[-2000:]
        state["manual_events"] = manual

        _write_watch_state(state)

    # Post using existing formatters
    try:
        if kind == "stake":
            await _post_stake_event(context.application, tx_hash, amount, from_addr)
        else:
            await _post_burn_event(context.application, tx_hash, amount, from_addr)
    except Exception as e:
        await msg.reply_text(f"Parsed but failed to post: {e}")
        return

    await msg.reply_text(
        "Parsed and posted.\n"
        f"Kind: {kind}\n"
        f"Amount: {_fmt_tusd(amount)}\n"
        f"Tx: {tx_hash}\n"
        f"Block: {block if block is not None else 'unknown'}"
    )
# ==== Admin helpers to download/reset watch_state.json ====


async def watchstate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.message

    if not chat or not user or not msg:
        return

    if chat.type != "private" or user.id != ADMIN_ID:
        return

    path = WATCH_STATE_PATH
    if not os.path.exists(path):
        await msg.reply_text("watch_state.json not found.")
        return

    try:
        with open(path, "rb") as f:
            await msg.reply_document(
                document=f,
                filename="watch_state.json",
                caption="watch_state.json"
            )
    except Exception as e:
        await msg.reply_text(f"Failed to send file: {e}")


async def resetwatchstate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.message

    if not chat or not user or not msg:
        return

    if chat.type != "private" or user.id != ADMIN_ID:
        return

    try:
        _write_watch_state({})
        await msg.reply_text("✅ watch_state.json reset. Next events will be detected fresh.")
    except Exception as e:
        await msg.reply_text(f"Failed to reset watch_state.json: {e}")

# ==== MAIN ====

async def on_startup(application):
    global WATCHER_STARTED

    # Startup ping
    try:
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text="⚡ Bot arrancado correctamente"
        )
    except Exception as e:
        print("Startup message failed:", e)

    # Start onchain watchers only once
    try:
        if not WATCHER_STARTED:
            WATCHER_STARTED = True
            loop = asyncio.get_running_loop()
            loop.create_task(monitor_stake_and_burn(application))
            print("✅ Watcher started")
        else:
            print("⚠️ Watcher already running, skipping")
    except Exception as e:
        print("Watcher task failed:", e)



def main():
    ensure_data_dir()
    preload_memes()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )

    # 🚫 GLOBAL COMMAND GUARD
    app.add_handler(
        MessageHandler(filters.COMMAND, global_command_guard, block=False),
        group=0
    )

    # === SUPPLY COMMANDS ===
    app.add_handler(CommandHandler("stats", stats), group=1)
    app.add_handler(CommandHandler("circsupply", circsupply), group=1)

    # === DEBT / INFLATION COMMANDS ===
    app.add_handler(
        CommandHandler(["debt", "inflation"], debt_or_inflation_command),
        group=1
    )

    # === MONEY PRINTER ===
    app.add_handler(CommandHandler("printer", printer_command), group=1)

    # === RESET / RESTART ===
    app.add_handler(
        CommandHandler("reset", reset_debt_cycle, filters=filters.ChatType.PRIVATE),
        group=1
    )

    app.add_handler(
        CommandHandler("restart", restart, filters=filters.ChatType.PRIVATE),
        group=1
    )

    app.add_handler(
        CommandHandler("scan", scan, filters=filters.ChatType.PRIVATE),
        group=1
    )

    app.add_handler(CommandHandler("parse", parse, filters=filters.ChatType.PRIVATE), group=1)
    # === WATCH STATE ADMIN COMMANDS ===
    app.add_handler(
        CommandHandler("watchstate", watchstate, filters=filters.ChatType.PRIVATE),
        group=1
    )

    app.add_handler(
        CommandHandler("resetwatchstate", resetwatchstate, filters=filters.ChatType.PRIVATE),
        group=1
    )

    # === YEAR REPLY HANDLER ===
    app.add_handler(
        MessageHandler(filters.TEXT & filters.REPLY, year_reply_handler),
        group=2
    )

    app.run_polling()


if __name__ == "__main__":
    main()
