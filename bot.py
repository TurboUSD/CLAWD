```python
import os
import json
import asyncio
import requests
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes


# =========================
# Environment variables (same naming style as your existing bot)
# =========================

BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ.get("ALLOWED_CHAT_ID", "0"))
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

BASE_RPC_URL = (os.environ.get("BASE_RPC_URL", "").strip() or "https://mainnet.base.org")

WATCH_POLL_SEC = int(os.environ.get("WATCH_POLL_SEC", "30"))
WATCH_OVERLAP_BLOCKS = int(os.environ.get("WATCH_OVERLAP_BLOCKS", "8"))
WATCH_MAX_SEEN_EVENTS = int(os.environ.get("WATCH_MAX_SEEN_EVENTS", "4000"))
WATCH_CONFIRMATIONS = int(os.environ.get("WATCH_CONFIRMATIONS", "0"))
RPC_LOG_CHUNK = int(os.environ.get("RPC_LOG_CHUNK", "2000"))

DATA_PATH = os.environ.get("DATA_PATH", "/app/data")
STATE_PATH = os.environ.get("STATE_PATH", os.path.join(DATA_PATH, "watch_state.json"))

# Destination chat for watcher posts
# If ALLOWED_CHAT_ID=0, send to ADMIN_ID in private for testing
if ALLOWED_CHAT_ID == 0:
    if not ADMIN_ID:
        raise RuntimeError("ALLOWED_CHAT_ID=0 but ADMIN_ID not set")
    POST_CHAT_ID = ADMIN_ID
else:
    POST_CHAT_ID = ALLOWED_CHAT_ID


# =========================
# Project specific config (CLAWD)
# =========================

TOKEN_ADDRESS = os.environ.get("TOKEN_CONTRACT_ADDRESS", "0x9f86dB9fc6f7c9408e8Fda3Ff8ce4e78ac7a6b07").strip()
TOKEN_DECIMALS = int(os.environ.get("TOKEN_DECIMALS", "18"))

CLAWD_WALLET = os.environ.get("CLAWD_WALLET_ADDRESS", "0x90eF2A9211A3E7CE788561E5af54C76B0Fa3aEd0").strip()
BURN_ADDRESS = os.environ.get("BURN_ADDRESS", "0x000000000000000000000000000000000000dEaD").strip()

# Needed for stake detection via Transfer(to=staking_contract)
STAKING_CONTRACT_ADDRESS = os.environ.get("STAKING_CONTRACT_ADDRESS", "").strip()

# Base defaults (override in Railway if you want)
USDC_ADDRESS = os.environ.get("USDC_ADDRESS", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913").strip()
USDT_ADDRESS = os.environ.get("USDT_ADDRESS", "0xd9aaEC86B65D86f6A7B5B1b0c42FFA531710b6CA").strip()
WETH_ADDRESS = os.environ.get("WETH_ADDRESS", "0x4200000000000000000000000000000000000006").strip()

# Images (place these files in your repo)
ASSET_BUY = os.environ.get("ASSET_BUY", "assets/buy.png")
ASSET_STAKE = os.environ.get("ASSET_STAKE", "assets/stake.png")
ASSET_BURN = os.environ.get("ASSET_BURN", "assets/burn.png")

LOBSTER = "🦞"
MAX_EMOJIS = 100

TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


# =========================
# State
# =========================

DEFAULT_STATE: Dict[str, Any] = {
    "min_usd": {"buy": 10000.0, "stake": 10000.0, "burn": 10000.0},
    "emoji_usd": {"buy": 100.0, "stake": 100.0, "burn": 100.0},
    "watch": {
        "last_scanned_block": 0,
        "seen": {"buy": [], "stake": [], "burn": []},
    },
}


def _ensure_data_dir() -> None:
    os.makedirs(DATA_PATH, exist_ok=True)


def _load_state() -> Dict[str, Any]:
    _ensure_data_dir()
    if not os.path.exists(STATE_PATH):
        return json.loads(json.dumps(DEFAULT_STATE))
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            s = json.load(f)
        merged = json.loads(json.dumps(DEFAULT_STATE))

        if isinstance(s, dict):
            merged.update(s)
        if isinstance(s.get("min_usd"), dict):
            merged["min_usd"].update(s["min_usd"])
        if isinstance(s.get("emoji_usd"), dict):
            merged["emoji_usd"].update(s["emoji_usd"])
        if isinstance(s.get("watch"), dict):
            merged["watch"].update(s["watch"])
        if isinstance(s.get("watch", {}).get("seen"), dict):
            merged["watch"]["seen"].update(s["watch"]["seen"])

        for k in ("buy", "stake", "burn"):
            merged["watch"]["seen"][k] = list(merged["watch"]["seen"].get(k) or [])

        return merged
    except Exception:
        return json.loads(json.dumps(DEFAULT_STATE))


def _save_state(state: Dict[str, Any]) -> None:
    _ensure_data_dir()
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def _prune_seen(arr: List[str]) -> List[str]:
    if len(arr) <= WATCH_MAX_SEEN_EVENTS:
        return arr
    return arr[-WATCH_MAX_SEEN_EVENTS:]


# =========================
# Formatting
# =========================

def _norm(a: str) -> str:
    return (a or "").lower()


def _hex_to_int(x: str) -> int:
    return int(x, 16)


def _dec(v_int: int, decimals: int) -> float:
    return v_int / (10 ** decimals)


def _fmt_price(price: float) -> str:
    s = f"{price:.10f}".rstrip("0").rstrip(".")
    return f"${s}"


def _fmt_int_usd(x: float) -> str:
    return f"${int(round(x)):,}"


def _fmt_big(n: float) -> str:
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.2f}K"
    return f"{n:.0f}"


def _fmt_weth_two(n: float) -> str:
    return f"{n:.2f}"


def _fmt_usd_compact(x: float) -> str:
    if x >= 1_000_000_000:
        return f"${x/1_000_000_000:.2f}B"
    if x >= 1_000_000:
        return f"${x/1_000_000:.2f}M"
    if x >= 1_000:
        return f"${x/1_000:.2f}K"
    return f"${x:.2f}"


def _emoji_bar(total_usd: float, usd_per_emoji: float) -> str:
    if usd_per_emoji <= 0:
        usd_per_emoji = 100.0
    n = int(total_usd / usd_per_emoji)
    if n < 1:
        n = 1
    if n > MAX_EMOJIS:
        n = MAX_EMOJIS
    return LOBSTER * n


# =========================
# RPC
# =========================

def _rpc(method: str, params: List[Any]) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = requests.post(BASE_RPC_URL, json=payload, timeout=25)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(str(j["error"]))
    return j["result"]


def _get_latest_block() -> int:
    return _hex_to_int(_rpc("eth_blockNumber", []))


def _get_receipt(tx_hash: str) -> Dict[str, Any]:
    return _rpc("eth_getTransactionReceipt", [tx_hash])


def _get_tx(tx_hash: str) -> Dict[str, Any]:
    return _rpc("eth_getTransactionByHash", [tx_hash])


def _topic_addr(topic_32: str) -> str:
    return "0x" + topic_32[-40:]


def _get_logs_chunked(address: str, from_block: int, to_block: int) -> List[Dict[str, Any]]:
    all_logs: List[Dict[str, Any]] = []
    if from_block > to_block:
        return all_logs

    cur = from_block
    while cur <= to_block:
        end = min(to_block, cur + max(1, RPC_LOG_CHUNK) - 1)
        chunk = _rpc("eth_getLogs", [{
            "fromBlock": hex(cur),
            "toBlock": hex(end),
            "address": address,
            "topics": [TRANSFER_TOPIC0],
        }])
        all_logs.extend(chunk or [])
        cur = end + 1

    return all_logs


def _erc20_balance_of(token: str, holder: str) -> int:
    selector = "0x70a08231"
    holder_padded = holder.lower().replace("0x", "").rjust(64, "0")
    data = selector + holder_padded
    out = _rpc("eth_call", [{"to": token, "data": data}, "latest"])
    return int(out, 16)


# =========================
# Pricing (DexScreener)
# =========================

def _dex_best_pair(token_addr: str) -> Dict[str, Any]:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{token_addr}"
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    j = r.json()
    pairs = j.get("pairs") or []
    if not pairs:
        return {}
    pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
    return pairs[0]


def _token_price_usd_and_fdv(token_addr: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        p = _dex_best_pair(token_addr)
        if not p:
            return None, None
        price = p.get("priceUsd")
        fdv = p.get("fdv")
        return (float(price) if price is not None else None, float(fdv) if fdv is not None else None)
    except Exception:
        return None, None


def _weth_price_usd() -> Optional[float]:
    try:
        p = _dex_best_pair(WETH_ADDRESS)
        if not p:
            return None
        v = p.get("priceUsd")
        return float(v) if v is not None else None
    except Exception:
        return None


# =========================
# Buy aggregation by transaction
# =========================

def _aggregate_net_deltas_from_receipt(
    receipt: Dict[str, Any],
    token_addresses: Dict[str, int],
) -> Dict[str, Dict[str, int]]:
    deltas: Dict[str, Dict[str, int]] = {}
    for taddr in token_addresses.keys():
        deltas[_norm(taddr)] = defaultdict(int)

    for lg in receipt.get("logs", []) or []:
        addr = _norm(lg.get("address", ""))
        if addr not in deltas:
            continue

        topics = lg.get("topics") or []
        if len(topics) < 3:
            continue
        if _norm(topics[0]) != TRANSFER_TOPIC0:
            continue

        from_addr = _norm(_topic_addr(topics[1]))
        to_addr = _norm(_topic_addr(topics[2]))
        value_int = int(lg.get("data", "0x0"), 16)

        deltas[addr][from_addr] -= value_int
        deltas[addr][to_addr] += value_int

    return deltas


def _pick_final_buyer(token_deltas: Dict[str, int], exclude_addrs: List[str]) -> Optional[str]:
    exclude = set(_norm(a) for a in exclude_addrs if a)
    best_addr = None
    best_delta = 0
    for addr, delta in token_deltas.items():
        if addr in exclude:
            continue
        if delta > best_delta:
            best_delta = delta
            best_addr = addr
    return best_addr


def _buy_from_receipt(tx_hash: str, receipt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    token_addresses = {
        TOKEN_ADDRESS: TOKEN_DECIMALS,
        USDC_ADDRESS: 6,
        USDT_ADDRESS: 6,
        WETH_ADDRESS: 18,
    }

    deltas = _aggregate_net_deltas_from_receipt(receipt, token_addresses)
    tdel = deltas[_norm(TOKEN_ADDRESS)]
    if not tdel:
        return None

    exclude = [
        TOKEN_ADDRESS,
        USDC_ADDRESS,
        USDT_ADDRESS,
        WETH_ADDRESS,
        BURN_ADDRESS,
        STAKING_CONTRACT_ADDRESS,
    ]

    buyer = _pick_final_buyer(tdel, exclude)
    if not buyer:
        return None
    if tdel.get(buyer, 0) <= 0:
        return None

    usdc_out = max(0, -deltas[_norm(USDC_ADDRESS)].get(buyer, 0))
    usdt_out = max(0, -deltas[_norm(USDT_ADDRESS)].get(buyer, 0))
    weth_out = max(0, -deltas[_norm(WETH_ADDRESS)].get(buyer, 0))

    tx = _get_tx(tx_hash)
    tx_from = _norm(tx.get("from", ""))
    eth_value_int = int(tx.get("value", "0x0"), 16)
    eth_out = eth_value_int if tx_from == buyer else 0

    total_usd = 0.0
    total_usd += _dec(usdc_out, 6)
    total_usd += _dec(usdt_out, 6)

    wp = _weth_price_usd() or 0.0
    total_usd += (_dec(weth_out, 18) + _dec(eth_out, 18)) * wp

    if total_usd <= 0:
        return None

    tokens_bought = _dec(tdel[buyer], TOKEN_DECIMALS)

    return {
        "buyer": buyer,
        "usd": float(total_usd),
        "tokens": float(tokens_bought),
    }


# =========================
# Stake and burn detection
# =========================

def _classify_transfer_log(log: Dict[str, Any]) -> Optional[Tuple[str, str, str, int]]:
    topics = log.get("topics") or []
    if len(topics) < 3:
        return None
    if _norm(topics[0]) != TRANSFER_TOPIC0:
        return None

    from_addr = _norm(_topic_addr(topics[1]))
    to_addr = _norm(_topic_addr(topics[2]))
    amount_int = int(log.get("data", "0x0"), 16)

    if STAKING_CONTRACT_ADDRESS and to_addr == _norm(STAKING_CONTRACT_ADDRESS):
        return ("stake", from_addr, to_addr, amount_int)

    if to_addr == _norm(BURN_ADDRESS):
        return ("burn", from_addr, to_addr, amount_int)

    return None


# =========================
# Telegram helpers
# =========================

async def _send_photo_or_text(app, chat_id: int, kind: str, caption: str) -> None:
    path = None
    if kind == "buy":
        path = ASSET_BUY
    elif kind == "stake":
        path = ASSET_STAKE
    elif kind == "burn":
        path = ASSET_BURN

    if path and os.path.exists(path):
        with open(path, "rb") as f:
            await app.bot.send_photo(chat_id=chat_id, photo=f, caption=caption)
    else:
        await app.bot.send_message(chat_id=chat_id, text=caption)


async def _dm_user(app, user_id: int, text: str) -> bool:
    try:
        await app.bot.send_message(chat_id=user_id, text=text)
        return True
    except Exception:
        return False


def _help_text() -> str:
    # Keep it short and scannable
    lines = []
    lines.append("Commands")
    lines.append("")
    lines.append("/help")
    lines.append("Show this message")
    lines.append("")
    lines.append("/stats")
    lines.append("Show price, market cap, and CLAWD wallet balances (CLAWD and WETH)")
    lines.append("")
    lines.append("/scan <blocks_back> <min_buy_usd>")
    lines.append("Scan last N blocks and DM you the buys above the threshold")
    lines.append("Example: /scan 10 10000")
    lines.append("")
    lines.append("/setmin <buy|stake|burn> <usd>")
    lines.append("Set minimum USD size per event type")
    lines.append("Example: /setmin buy 5000")
    lines.append("")
    lines.append("/setemoji <buy|stake|burn> <usd_per_emoji>")
    lines.append("Set USD value per lobster emoji (max 100 emojis)")
    lines.append("Example: /setemoji buy 100")
    return "\n".join(lines)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(_help_text())


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text("Bot is running. Use /help")


# =========================
# Commands
# =========================

async def cmd_setmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if ADMIN_ID and update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Usage: /setmin <buy|stake|burn> <usd>")
        return

    kind = context.args[0].strip().lower()
    if kind not in ("buy", "stake", "burn"):
        await update.message.reply_text("Kind must be buy, stake, or burn.")
        return

    try:
        usd = float(context.args[1])
    except Exception:
        await update.message.reply_text("Invalid usd.")
        return

    state = _load_state()
    state["min_usd"][kind] = max(0.0, usd)
    _save_state(state)
    await update.message.reply_text(f"OK. min_usd[{kind}] = {state['min_usd'][kind]}")


async def cmd_setemoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if ADMIN_ID and update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Usage: /setemoji <buy|stake|burn> <usd_per_emoji>")
        return

    kind = context.args[0].strip().lower()
    if kind not in ("buy", "stake", "burn"):
        await update.message.reply_text("Kind must be buy, stake, or burn.")
        return

    try:
        usd_per = float(context.args[1])
    except Exception:
        await update.message.reply_text("Invalid usd_per_emoji.")
        return

    state = _load_state()
    state["emoji_usd"][kind] = max(0.01, usd_per)
    _save_state(state)
    await update.message.reply_text(f"OK. emoji_usd[{kind}] = {state['emoji_usd'][kind]}")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    if len(context.args) != 2:
        await update.message.reply_text("Usage: /scan <blocks_back> <min_buy_usd>")
        return

    try:
        blocks_back = int(context.args[0])
        min_usd = float(context.args[1])
    except Exception:
        await update.message.reply_text("Invalid args. Example: /scan 10 10000")
        return

    if blocks_back < 1:
        blocks_back = 1
    if blocks_back > 5000:
        blocks_back = 5000

    latest = _get_latest_block()
    end = latest - max(0, WATCH_CONFIRMATIONS)
    if end < 0:
        end = 0
    start = max(0, end - blocks_back + 1)

    try:
        logs = _get_logs_chunked(TOKEN_ADDRESS, start, end)
    except Exception:
        await update.message.reply_text("RPC error while scanning logs.")
        return

    tx_hashes: List[str] = []
    seen_tx = set()
    for lg in logs:
        h = lg.get("transactionHash")
        if not h or h in seen_tx:
            continue
        seen_tx.add(h)
        tx_hashes.append(h)

    matches: List[Tuple[str, Dict[str, Any]]] = []
    for h in tx_hashes:
        try:
            receipt = _get_receipt(h)
            buy = _buy_from_receipt(h, receipt)
            if not buy:
                continue
            if float(buy["usd"]) >= min_usd:
                matches.append((h, buy))
        except Exception:
            continue

    if not matches:
        msg = f"No buys found in last {blocks_back} blocks above {_fmt_usd_compact(min_usd)}"
        ok = await _dm_user(context.application, update.effective_user.id, msg)
        if not ok:
            await update.message.reply_text("I could not DM you. Start a private chat with the bot first.")
        else:
            await update.message.reply_text("Sent you a DM.")
        return

    matches.sort(key=lambda x: float(x[1]["usd"]), reverse=True)

    state = _load_state()
    usd_per_emoji = float(state["emoji_usd"]["buy"])

    lines: List[str] = []
    lines.append(f"Buys in last {blocks_back} blocks above {_fmt_usd_compact(min_usd)}")

    for h, buy in matches[:20]:
        usd = float(buy["usd"])
        tokens = float(buy["tokens"])
        buyer = buy["buyer"]
        bar = _emoji_bar(usd, usd_per_emoji)

        lines.append("")
        lines.append(f"{bar} {_fmt_usd_compact(usd)}")
        lines.append(f"Buyer: {buyer}")
        lines.append(f"Tokens: {int(round(tokens)):,} CLAWD")
        lines.append(f"Tx: https://basescan.org/tx/{h}")

    msg = "\n".join(lines)
    ok = await _dm_user(context.application, update.effective_user.id, msg)
    if not ok:
        await update.message.reply_text("I could not DM you. Start a private chat with the bot first.")
    else:
        await update.message.reply_text("Sent you a DM.")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    price, fdv = _token_price_usd_and_fdv(TOKEN_ADDRESS)
    wp = _weth_price_usd()

    try:
        clawd_bal_int = _erc20_balance_of(TOKEN_ADDRESS, CLAWD_WALLET)
        weth_bal_int = _erc20_balance_of(WETH_ADDRESS, CLAWD_WALLET)
    except Exception:
        await update.message.reply_text("Failed to read balances from RPC.")
        return

    clawd_amt = _dec(clawd_bal_int, TOKEN_DECIMALS)
    weth_amt = _dec(weth_bal_int, 18)

    clawd_usd = (price or 0.0) * clawd_amt
    weth_usd = (wp or 0.0) * weth_amt

    lines: List[str] = []
    lines.append("🔍 $CLAWD Treasury")
    lines.append("Live onchain data.")
    lines.append("")
    lines.append(f"Current price: {_fmt_price(price) if price is not None else 'N/A'}")
    lines.append(f"Market cap: {_fmt_int_usd(fdv) if fdv is not None else 'N/A'}")
    lines.append("")
    lines.append("🤖 My Wallet")
    lines.append(CLAWD_WALLET)
    lines.append(f"{_fmt_big(clawd_amt)} CLAWD")
    lines.append(f"≈ {_fmt_int_usd(clawd_usd)}")
    lines.append(f"{_fmt_weth_two(weth_amt)} WETH")
    lines.append(f"≈ {_fmt_int_usd(weth_usd)}")

    await update.message.reply_text("\n".join(lines))


# =========================
# Watcher
# =========================

def _eid(kind: str, tx_hash: str, log_index_hex: str) -> str:
    return f"{kind}:{tx_hash}:{log_index_hex}"


async def monitor(app) -> None:
    while True:
        try:
            state = _load_state()
            latest = _get_latest_block()
            confirmed_latest = latest - max(0, WATCH_CONFIRMATIONS)
            if confirmed_latest < 0:
                confirmed_latest = 0

            last_scanned = int(state["watch"].get("last_scanned_block") or 0)
            if last_scanned <= 0:
                start = max(0, confirmed_latest - 5)
            else:
                start = max(0, last_scanned - WATCH_OVERLAP_BLOCKS)

            end = confirmed_latest
            if end < start:
                await asyncio.sleep(WATCH_POLL_SEC)
                continue

            logs = _get_logs_chunked(TOKEN_ADDRESS, start, end)

            seen_buy = set(state["watch"]["seen"].get("buy") or [])
            seen_stake = set(state["watch"]["seen"].get("stake") or [])
            seen_burn = set(state["watch"]["seen"].get("burn") or [])

            price, _ = _token_price_usd_and_fdv(TOKEN_ADDRESS)
            token_price = float(price) if price is not None else 0.0

            state_min = state["min_usd"]
            state_emoji = state["emoji_usd"]

            txs_for_buy: List[str] = []
            tx_seen_local = set()

            for lg in logs:
                tx_hash = lg.get("transactionHash")
                log_index = lg.get("logIndex", "0x0")

                classified = _classify_transfer_log(lg)
                if classified and tx_hash:
                    kind, _from, _to, amount_int = classified
                    event_id = _eid(kind, tx_hash, log_index)

                    if kind == "stake":
                        if event_id not in seen_stake:
                            amount = _dec(amount_int, TOKEN_DECIMALS)
                            usd = amount * token_price
                            if usd >= float(state_min["stake"]):
                                bar = _emoji_bar(usd, float(state_emoji["stake"]))
                                caption = (
                                    f"{bar} {_fmt_usd_compact(usd)}\n"
                                    f"Amount: {int(round(amount)):,} CLAWD\n"
                                    f"Tx: https://basescan.org/tx/{tx_hash}"
                                )
                                await _send_photo_or_text(app, POST_CHAT_ID, "stake", caption)
                            seen_stake.add(event_id)

                    elif kind == "burn":
                        if event_id not in seen_burn:
                            amount = _dec(amount_int, TOKEN_DECIMALS)
                            usd = amount * token_price
                            if usd >= float(state_min["burn"]):
                                bar = _emoji_bar(usd, float(state_emoji["burn"]))
                                caption = (
                                    f"{bar} {_fmt_usd_compact(usd)}\n"
                                    f"Amount: {int(round(amount)):,} CLAWD\n"
                                    f"Tx: https://basescan.org/tx/{tx_hash}"
                                )
                                await _send_photo_or_text(app, POST_CHAT_ID, "burn", caption)
                            seen_burn.add(event_id)

                if tx_hash and tx_hash not in tx_seen_local:
                    tx_seen_local.add(tx_hash)
                    txs_for_buy.append(tx_hash)

            for h in txs_for_buy:
                buy_id = f"buy:{h}"
                if buy_id in seen_buy:
                    continue
                try:
                    receipt = _get_receipt(h)
                    buy = _buy_from_receipt(h, receipt)
                    if buy:
                        usd = float(buy["usd"])
                        if usd >= float(state_min["buy"]):
                            bar = _emoji_bar(usd, float(state_emoji["buy"]))
                            tokens = float(buy["tokens"])
                            buyer = buy["buyer"]
                            caption = (
                                f"{bar} {_fmt_usd_compact(usd)}\n"
                                f"Buyer: {buyer}\n"
                                f"Tokens: {int(round(tokens)):,} CLAWD\n"
                                f"Tx: https://basescan.org/tx/{h}"
                            )
                            await _send_photo_or_text(app, POST_CHAT_ID, "buy", caption)
                except Exception:
                    pass
                finally:
                    seen_buy.add(buy_id)

            state["watch"]["last_scanned_block"] = end
            state["watch"]["seen"]["buy"] = _prune_seen(list(seen_buy))
            state["watch"]["seen"]["stake"] = _prune_seen(list(seen_stake))
            state["watch"]["seen"]["burn"] = _prune_seen(list(seen_burn))
            _save_state(state)

        except Exception:
            pass

        await asyncio.sleep(WATCH_POLL_SEC)


async def post_init(app) -> None:
    # Startup message (same idea as your example bot)
    try:
        if POST_CHAT_ID == ADMIN_ID:
            await app.bot.send_message(chat_id=ADMIN_ID, text="CLAWD bot started (test mode). Use /help")
        else:
            await app.bot.send_message(chat_id=POST_CHAT_ID, text="CLAWD bot started. Use /help")
    except Exception:
        pass

    asyncio.create_task(monitor(app))


# =========================
# Main
# =========================

def main() -> None:
    _ensure_data_dir()

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("setmin", cmd_setmin))
    app.add_handler(CommandHandler("setemoji", cmd_setemoji))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("stats", cmd_stats))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
```
