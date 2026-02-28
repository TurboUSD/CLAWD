import os
import re
import json
import asyncio
import time
import calendar
import requests
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes


# =========================
# Environment variables
# =========================

BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ.get("ALLOWED_CHAT_ID", "0"))
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

BASE_RPC_URL = (os.environ.get("BASE_RPC_URL", "").strip() or "https://mainnet.base.org")
ANKR_MULTICHAIN_RPC_URL = os.environ.get("ANKR_MULTICHAIN_RPC_URL", "").strip()

# If ANKR_MULTICHAIN_RPC_URL is not set, derive it from BASE_RPC_URL when using Ankr endpoints.
# This lets you configure only BASE_RPC_URL=https://rpc.ankr.com/base/<KEY>.
if not ANKR_MULTICHAIN_RPC_URL:
    try:
        if BASE_RPC_URL.startswith("https://rpc.ankr.com/") and "/base/" in BASE_RPC_URL:
            ANKR_MULTICHAIN_RPC_URL = BASE_RPC_URL.replace("/base/", "/multichain/").rstrip("/")
    except Exception:
        pass


# Convenience: if user only set BASE_RPC_URL to Ankr Base endpoint,
# derive the multichain endpoint automatically using the same key.
if not ANKR_MULTICHAIN_RPC_URL and "rpc.ankr.com/base/" in (BASE_RPC_URL or ""):
    ANKR_MULTICHAIN_RPC_URL = BASE_RPC_URL.replace("rpc.ankr.com/base/", "rpc.ankr.com/multichain/")

WATCH_POLL_SEC = int(os.environ.get("WATCH_POLL_SEC", "30"))
WATCH_OVERLAP_BLOCKS = int(os.environ.get("WATCH_OVERLAP_BLOCKS", "8"))
WATCH_MAX_SEEN_EVENTS = int(os.environ.get("WATCH_MAX_SEEN_EVENTS", "4000"))
WATCH_CONFIRMATIONS = int(os.environ.get("WATCH_CONFIRMATIONS", "0"))
RPC_LOG_CHUNK = int(os.environ.get("RPC_LOG_CHUNK", "2000"))

DATA_PATH = os.environ.get("DATA_PATH") or ("/data" if os.path.isdir("/data") else "/app/data")
STATE_PATH = os.environ.get("STATE_PATH", os.path.join(DATA_PATH, "watch_state.json"))
ETH_PRICE_CACHE_PATH = os.environ.get("ETH_PRICE_CACHE_PATH", os.path.join(DATA_PATH, "eth_price_cache.json"))
ETH_DAILY_PRICE_CACHE_PATH = os.environ.get("ETH_DAILY_PRICE_CACHE_PATH", os.path.join(DATA_PATH, "eth_price_daily.json"))
ETH_DAILY_SERIES_CACHE_PATH = os.environ.get("ETH_DAILY_SERIES_CACHE_PATH", os.path.join(DATA_PATH, "eth_price_daily_series.json"))

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

INCINERATOR_ADDRESS = os.environ.get(
    "INCINERATOR_ADDRESS",
    "0x536453350F2EeE2EB8bFeE1866bAF4fCa494A092"
).strip()

STAKING_CONTRACT_ADDRESS = os.environ.get("STAKING_CONTRACT_ADDRESS", "").strip()

USDC_ADDRESS = os.environ.get("USDC_ADDRESS", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913").strip()
USDT_ADDRESS = os.environ.get("USDT_ADDRESS", "0xd9aaEC86B65D86f6A7B5B1b0c42FFA531710b6CA").strip()
WETH_ADDRESS = os.environ.get("WETH_ADDRESS", "0x4200000000000000000000000000000000000006").strip()
# Ignore LP position NFT transfers / ERC-721 noise
IGNORE_ERC721_CONTRACTS = {
    "0xa990C6a764b73BF43cee5Bb40339c3322FB9D55F".lower(),
}


CHAINLINK_ETH_USD_FEED = os.environ.get(
    "CHAINLINK_ETH_USD_FEED",
    "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70"
).strip()

ASSET_BUY = os.environ.get("ASSET_BUY", "assets/buy.png")
ASSET_STAKE = os.environ.get("ASSET_STAKE", "assets/stake.png")
ASSET_BURN = os.environ.get("ASSET_BURN", "assets/burn.png")

LOBSTER = "🦞"
MAX_EMOJIS = 100

TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
def _receipt_has_ignored_erc721(receipt: Dict[str, Any]) -> bool:
    """
    Returns True if the tx receipt includes logs from known ERC-721 contracts
    we want to ignore completely (eg LP position NFT transfers).
    """
    for lg in receipt.get("logs", []) or []:
        addr = _norm(lg.get("address", ""))
        if addr in IGNORE_ERC721_CONTRACTS:
            return True
    return False




# =========================
# Global Task Registry (for /cancel)
# =========================

TASK_REGISTRY: Dict[str, asyncio.Task] = {}

def _track_task(name: str, task: asyncio.Task) -> asyncio.Task:
    TASK_REGISTRY[name] = task

    def _cleanup(_t: asyncio.Task) -> None:
        TASK_REGISTRY.pop(name, None)

    task.add_done_callback(_cleanup)
    return task


# =========================
# State
# =========================

DEFAULT_STATE: Dict[str, Any] = {
    "min_usd": {"buy": 100.0, "stake": 100.0, "burn": 100.0},
    "emoji_usd": {"buy": 100.0, "stake": 100.0, "burn": 100.0},
    "watch": {
        "last_scanned_block": 0,
        "seen": {"buy": [], "stake": [], "burn": []},
        "sent": {"buy": [], "stake": [], "burn": []},
        "sent_public": {"buy": [], "stake": [], "burn": []},
        "sent_dm": {"buy": [], "stake": [], "burn": []},
    },
    "cache": {
        "token_price_usd": None,
        "token_fdv": None,
    }
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
        if isinstance(s.get("watch", {}).get("sent"), dict):
            merged["watch"].setdefault("sent", {"buy": [], "stake": [], "burn": []})
            merged["watch"]["sent"].update(s["watch"]["sent"])
        if isinstance(s.get("watch", {}).get("sent_public"), dict):
            merged["watch"].setdefault("sent_public", {"buy": [], "stake": [], "burn": []})
            merged["watch"]["sent_public"].update(s["watch"]["sent_public"])
        if isinstance(s.get("watch", {}).get("sent_dm"), dict):
            merged["watch"].setdefault("sent_dm", {"buy": [], "stake": [], "burn": []})
            merged["watch"]["sent_dm"].update(s["watch"]["sent_dm"])
        if isinstance(s.get("cache"), dict):
            merged["cache"].update(s["cache"])

        for k in ("buy", "stake", "burn"):
            merged["watch"]["seen"][k] = list(merged["watch"]["seen"].get(k) or [])
            merged["watch"].setdefault("sent", {}).setdefault(k, [])
            merged["watch"]["sent"][k] = list(merged["watch"]["sent"].get(k) or [])
            merged["watch"].setdefault("sent_public", {}).setdefault(k, [])
            merged["watch"]["sent_public"][k] = list(merged["watch"]["sent_public"].get(k) or [])
            merged["watch"].setdefault("sent_dm", {}).setdefault(k, [])
            merged["watch"]["sent_dm"][k] = list(merged["watch"]["sent_dm"].get(k) or [])

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


def _short_addr(a: str) -> str:
    if not a:
        return ""
    a = a.strip()
    if len(a) <= 12:
        return a
    return f"{a[:6]}…{a[-4:]}"


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


def _fmt_token_amount(n: float) -> str:
    return f"{n:,.0f}"


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


def _emoji_bar(total_usd: float, usd_per_emoji: float, emoji: str) -> str:
    if usd_per_emoji <= 0:
        usd_per_emoji = 100.0

    n = int(total_usd / usd_per_emoji)

    if n < 1:
        n = 1
    if n > MAX_EMOJIS:
        n = MAX_EMOJIS

    return emoji * n


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


def _get_code(addr: str, block_tag: str = "latest") -> str:
    return _rpc("eth_getCode", [addr, block_tag])


def _is_contract(addr: str, block_number: Optional[int] = None) -> bool:
    try:
        tag = hex(int(block_number)) if block_number is not None else "latest"
        code = _get_code(addr, tag)
        return isinstance(code, str) and code not in ("0x", "0x0", "")
    except Exception:
        # If in doubt, do not classify as contract here
        return False


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


def _get_logs_chunked_topics(address: str, from_block: int, to_block: int, topics: List[Optional[str]]) -> List[Dict[str, Any]]:
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
            "topics": topics,
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


def _eth_call(to: str, data: str, block_tag: str = "latest") -> str:
    return _rpc("eth_call", [{"to": to, "data": data}, block_tag])


def _chainlink_decimals(feed: str) -> int:
    out = _eth_call(feed, "0x313ce567")
    return int(out, 16)


def _chainlink_latest_answer(feed: str, block_number: Optional[int] = None) -> Optional[float]:
    try:
        dec = _chainlink_decimals(feed)
        block_tag = hex(block_number) if block_number is not None else "latest"
        out = _eth_call(feed, "0xfeaf968c", block_tag=block_tag)  # latestRoundData()
        answer_int = int(out[2 + 64:2 + 128], 16)
        return answer_int / (10 ** dec)
    except Exception:
        return None


# =========================
# Pricing (DexScreener + Chainlink)
# =========================

def _dex_best_pair(token_addr: str) -> Dict[str, Any]:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{token_addr}"
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, timeout=25, headers=headers)
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




def _get_block_timestamp(block_number: int) -> Optional[int]:
    try:
        blk = _rpc("eth_getBlockByNumber", [hex(block_number), False])
        ts_hex = blk.get("timestamp")
        if isinstance(ts_hex, str) and ts_hex.startswith("0x"):
            return int(ts_hex, 16)
    except Exception:
        return None
    return None


def _find_block_by_timestamp(target_ts: int, latest_block: int) -> int:
    """Binary search the first block whose timestamp is >= target_ts."""
    lo = 0
    hi = max(0, latest_block)

    ts_cache: Dict[int, int] = {}

    def _ts(bn: int) -> int:
        if bn in ts_cache:
            return ts_cache[bn]
        t = _get_block_timestamp(bn)
        if t is None:
            t = 0
        ts_cache[bn] = int(t)
        return ts_cache[bn]

    if _ts(hi) < target_ts:
        return hi

    while lo < hi:
        mid = (lo + hi) // 2
        if _ts(mid) >= target_ts:
            hi = mid
        else:
            lo = mid + 1

    return lo


def _load_eth_price_cache() -> Dict[str, float]:
    _ensure_data_dir()
    if not os.path.exists(ETH_PRICE_CACHE_PATH):
        return {}
    try:
        with open(ETH_PRICE_CACHE_PATH, "r", encoding="utf-8") as f:
            j = json.load(f)
        if isinstance(j, dict):
            return {str(k): float(v) for k, v in j.items()}
    except Exception:
        pass
    return {}


def _save_eth_price_cache(cache: Dict[str, float]) -> None:
    _ensure_data_dir()
    tmp = ETH_PRICE_CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ETH_PRICE_CACHE_PATH)


def _load_eth_daily_cache() -> Dict[str, float]:
    _ensure_data_dir()
    if not os.path.exists(ETH_DAILY_PRICE_CACHE_PATH):
        return {}
    try:
        with open(ETH_DAILY_PRICE_CACHE_PATH, "r", encoding="utf-8") as f:
            j = json.load(f)
        if isinstance(j, dict):
            return {str(k): float(v) for k, v in j.items()}
    except Exception:
        pass
    return {}


def _save_eth_daily_cache(cache: Dict[str, float]) -> None:
    _ensure_data_dir()
    tmp = ETH_DAILY_PRICE_CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ETH_DAILY_PRICE_CACHE_PATH)


def _load_eth_daily_series_cache() -> Dict[str, Any]:
    _ensure_data_dir()
    if not os.path.exists(ETH_DAILY_SERIES_CACHE_PATH):
        return {}
    try:
        with open(ETH_DAILY_SERIES_CACHE_PATH, "r", encoding="utf-8") as f:
            j = json.load(f)
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def _save_eth_daily_series_cache(cache: Dict[str, Any]) -> None:
    _ensure_data_dir()
    tmp = ETH_DAILY_SERIES_CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp, ETH_DAILY_SERIES_CACHE_PATH)



def _eth_usd_daily(date_utc: str) -> Optional[float]:
    """
    Return an approximate ETH/USD for a given UTC date (YYYY-MM-DD).
    This is intentionally "daily" to avoid rate limits: at most 1 HTTP call per date, cached on disk.
    """
    try:
        cache = _load_eth_daily_cache()
        if date_utc in cache and cache[date_utc] > 0:
            return float(cache[date_utc])

        # CoinGecko daily history endpoint (no Basescan). Cached so it is rarely called.
        # Date format required: dd-mm-yyyy
        y, m, d = date_utc.split("-")
        cg_date = f"{d}-{m}-{y}"
        url = f"https://api.coingecko.com/api/v3/coins/ethereum/history?date={cg_date}&localization=false"
        r = requests.get(url, timeout=20, headers={"accept": "application/json"})
        if r.status_code == 429:
            return None
        r.raise_for_status()
        j = r.json()
        price = (
            j.get("market_data", {})
             .get("current_price", {})
             .get("usd", None)
        )
        if isinstance(price, (int, float)) and price > 0:
            cache[date_utc] = float(price)
            # keep cache bounded (last 400 days)
            if len(cache) > 400:
                keys_sorted = sorted(cache.keys())
                for k in keys_sorted[:-400]:
                    cache.pop(k, None)
            _save_eth_daily_cache(cache)
            return float(price)
    except Exception:
        return None
    return None

def _ankr_multichain_rpc(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Call Ankr Advanced API (multichain) JSON-RPC methods like ankr_getTokenPriceHistory.
    Requires ANKR_MULTICHAIN_RPC_URL to be set, eg:
      https://rpc.ankr.com/multichain/<YOUR_API_KEY>
    """
    if not ANKR_MULTICHAIN_RPC_URL:
        raise RuntimeError("ANKR_MULTICHAIN_RPC_URL not set")
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = requests.post(ANKR_MULTICHAIN_RPC_URL, json=payload, timeout=25, headers={"Content-Type": "application/json"})
    r.raise_for_status()
    j = r.json()
    if isinstance(j, dict) and j.get("error"):
        raise RuntimeError(str(j["error"]))
    return j.get("result") or {}


def _eth_usd_from_ankr_history(ts: int) -> Optional[float]:
    """
    Fetch ETH/USD close to ts using Ankr Token API price history.
    We query on Base native coin price history (contractAddress omitted).
    """
    try:
        # Round to hour to reduce requests
        hour = int(ts) - (int(ts) % 3600)
        frm = max(0, hour - 3600)
        to = hour + 3600
        # Use Ethereum for ETH/USD history. Base's native coin is ETH, and using
        # "eth" avoids any chain-specific indexing quirks while keeping the
        # USD price correct.
        res = _ankr_multichain_rpc("ankr_getTokenPriceHistory", {
            "blockchain": "eth",
            "fromTimestamp": frm,
            "toTimestamp": to,
            "interval": 3600,
            "limit": 5,
            "syncCheck": False,
        })
        quotes = res.get("quotes") or []
        if not quotes:
            return None
        best = min(quotes, key=lambda q: abs(int(q.get("timestamp") or 0) - int(ts)))
        p = best.get("usdPrice")
        return float(p) if p is not None else None
    except Exception:
        return None



def _chainlink_eth_usd_at_block(block_number: int) -> Optional[float]:
    """
    Read Chainlink ETH/USD price at a specific block using eth_call with block tag.
    This avoids any external HTTP price APIs and works on standard (non-archive) RPC
    as long as the block is still available (recent history).
    """
    try:
        # latestRoundData() selector
        data = "0x" + "feaf968c"
        res = _rpc("eth_call", [{
            "to": CHAINLINK_ETH_USD_FEED,
            "data": data,
        }, hex(int(block_number))])
        if not isinstance(res, str) or not res.startswith("0x"):
            return None
        raw = bytes.fromhex(res[2:])
        if len(raw) < 32 * 5:
            return None
        # return values: (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
        answer_bytes = raw[32:64]
        answer = int.from_bytes(answer_bytes, byteorder="big", signed=True)
        if answer <= 0:
            return None
        # Chainlink ETH/USD feeds are typically 8 decimals
        return float(answer) / 1e8
    except Exception:
        return None



# Chainlink ETH/USD feed on Base Mainnet (proxy)
# Source: https://data.chain.link/feeds/base/base/eth-usd
CHAINLINK_ETH_USD_FEED_BASE = "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70"
_CL_ETHUSD_DECIMALS: Optional[int] = None

def _abi_int256_from_32(word: bytes) -> int:
    # word is 32 bytes, two's complement
    as_int = int.from_bytes(word, byteorder="big", signed=False)
    if as_int >= 2**255:
        as_int -= 2**256
    return as_int

def _chainlink_eth_usd_at_block(block_number: int) -> Optional[float]:
    """Return ETH/USD using Chainlink's Base ETH/USD feed at a specific block.

    This uses eth_call with a block tag, so it requires an RPC that can serve historical state (archive).
    """
    global _CL_ETHUSD_DECIMALS
    try:
        block_tag = hex(int(block_number))
        if _CL_ETHUSD_DECIMALS is None:
            dec_hex = _eth_call(CHAINLINK_ETH_USD_FEED_BASE, "0x313ce567", block_tag)  # decimals()
            dec = int(dec_hex, 16)
            if dec <= 0 or dec > 36:
                return None
            _CL_ETHUSD_DECIMALS = dec

        data = _eth_call(CHAINLINK_ETH_USD_FEED_BASE, "0xfeaf968c", block_tag)  # latestRoundData()
        raw = bytes.fromhex(data[2:]) if isinstance(data, str) and data.startswith("0x") else b""
        if len(raw) < 32 * 5:
            return None
        # layout: roundId, answer, startedAt, updatedAt, answeredInRound
        answer_word = raw[32:64]
        answer = _abi_int256_from_32(answer_word)
        if answer <= 0:
            return None
        return float(answer) / (10 ** int(_CL_ETHUSD_DECIMALS))
    except Exception:
        return None


def _weth_price_usd(block_number: Optional[int] = None, *, allow_live_fallback: bool = True) -> Optional[float]:
    """Return ETH/USD.

    Priority order:
    1) Chainlink ETH/USD feed on Base at the tx block (eth_call with block tag).
    2) Ankr price history near the tx timestamp with local hourly cache.
    3) Live fallback (DexScreener) ONLY when allow_live_fallback=True.
    """
    # 1) Chainlink per-block, when available
    if block_number is not None:
        px = _chainlink_eth_usd_at_block(int(block_number))
        if px is not None and px > 0:
            return float(px)
        if not allow_live_fallback:
            # For historical scans, do not lie with a live price.
            return None

    # 2) Ankr hourly cache (near timestamp)
    try:
        if block_number is not None:
            ts = _get_block_timestamp(block_number)
            if ts:
                hour_key = time.strftime("%Y-%m-%dT%H:00:00Z", time.gmtime(int(ts)))
                cache = _load_eth_hourly_cache()
                if hour_key in cache and cache[hour_key] > 0:
                    return float(cache[hour_key])

                # Pull a 24h window and take the closest hour
                px = _eth_usd_hourly_series_near_ts(int(ts))
                if px is not None and px > 0:
                    cache[hour_key] = float(px)
                    _save_eth_hourly_cache(cache)
                    return float(px)
    except Exception:
        pass

    # 3) Live fallback (only for near-realtime buys if enabled)
    if allow_live_fallback:
        try:
            live = _eth_usd_live()
            if live is not None and live > 0:
                return float(live)
        except Exception:
            pass

    return None

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


def _max_outflow_addr(deltas_for_token: Dict[str, int]) -> Tuple[Optional[str], int]:
    best_addr = None
    best_out = 0
    for addr, d in (deltas_for_token or {}).items():
        if d < 0 and -d > best_out:
            best_out = -d
            best_addr = addr
    return best_addr, best_out


def _buy_from_receipt(tx_hash: str, receipt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if _receipt_has_ignored_erc721(receipt):
        return None

    # Use the receipt block number for historic Chainlink reads (eth_call at that block)
    block_number = None
    try:
        bn_hex = receipt.get("blockNumber")
        if isinstance(bn_hex, str) and bn_hex.startswith("0x"):
            block_number = int(bn_hex, 16)
    except Exception:
        block_number = None

    token_addresses = {
        TOKEN_ADDRESS: TOKEN_DECIMALS,
        USDC_ADDRESS: 6,
        USDT_ADDRESS: 6,
        WETH_ADDRESS: 18,
    }

    deltas = _aggregate_net_deltas_from_receipt(receipt, token_addresses)
    tdel = deltas.get(_norm(TOKEN_ADDRESS)) or {}
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

    # If final receiver is a contract, it is not a personal buy
    if _is_contract(buyer, block_number):
        return None

    tokens_delta_int = int(tdel.get(buyer, 0))
    if tokens_delta_int <= 0:
        return None

    tokens_bought = _dec(tokens_delta_int, TOKEN_DECIMALS)

    # Price estimate for sanity filtering
    state = _load_state()
    cache = state.get("cache") or {}
    state["cache"] = cache

    price, _fdv = _token_price_usd_and_fdv(TOKEN_ADDRESS)
    if price is not None:
        cache["token_price_usd"] = float(price)
    else:
        price = cache.get("token_price_usd")

    _save_state(state)

    usd_est = (float(price) if price is not None else 0.0) * float(tokens_bought)

    usdc_del = deltas.get(_norm(USDC_ADDRESS)) or {}
    usdt_del = deltas.get(_norm(USDT_ADDRESS)) or {}
    weth_del = deltas.get(_norm(WETH_ADDRESS)) or {}

    payer_usdc, usdc_out = _max_outflow_addr(usdc_del)
    payer_usdt, usdt_out = _max_outflow_addr(usdt_del)
    payer_weth, weth_out = _max_outflow_addr(weth_del)

    payer = None
    spent_usd = 0.0
    eth_spent_total = 0.0
    usdc_spent = 0.0
    usdt_spent = 0.0
    weth_spent = 0.0

    # Always fetch tx once. We use tx.value to decide the payment path.
    tx_from = ""
    eth_value_int = 0
    try:
        tx = _get_tx(tx_hash)
        tx_from = _norm(tx.get("from", ""))
        eth_value_int = int(tx.get("value", "0x0"), 16)
    except Exception:
        tx = None

    # If the tx paid native ETH (tx.value > 0), treat this as an ETH-paid buy.
    # In that case, ignore any USDC/USDT movements inside the tx (they can be pool
    # rebalancing, internal router actions, or proceeds), and value ONLY the ETH.
    paid_with_eth = False
    if eth_value_int > 0:
        paid_with_eth = True
        payer = tx_from or buyer
        eth_spent_total = _dec(eth_value_int, 18)
        wp = _weth_price_usd(block_number=block_number, allow_live_fallback=False)
        if wp is None or wp <= 0:
            return None
        spent_usd = eth_spent_total * float(wp)
    else:
        # Stablecoin path: only count outflows from the inferred payer address.
        if usdc_out > 0 or usdt_out > 0:
            payer = payer_usdc if usdc_out >= usdt_out else payer_usdt
            if payer:
                # If tx.from is an EOA, require payer == tx.from (prevents sells tagged as buys)
                # If tx.from is a contract (relayer/router), allow payer != tx.from but require payer to be an EOA
                if tx_from:
                    if not _is_contract(tx_from, block_number):
                        if _norm(payer) != _norm(tx_from):
                            return None
                    else:
                        # Relayed transaction: payer must not be a contract
                        if _is_contract(payer, block_number):
                            return None

                usdc_spent = _dec(max(0, -usdc_del.get(payer, 0)), 6)
                usdt_spent = _dec(max(0, -usdt_del.get(payer, 0)), 6)
                spent_usd = usdc_spent + usdt_spent

        # Fallback: WETH path
        if spent_usd <= 0 and weth_out > 0 and payer_weth:
            payer = payer_weth

            # If tx.from is an EOA, require payer == tx.from (prevents sells tagged as buys)
            # If tx.from is a contract (relayer/router), allow payer != tx.from but require payer to be an EOA
            if tx_from:
                if not _is_contract(tx_from, block_number):
                    if _norm(payer) != _norm(tx_from):
                        return None
                else:
                    # Relayed transaction: payer must not be a contract
                    if _is_contract(payer, block_number):
                        return None

            wp = _weth_price_usd(block_number=block_number, allow_live_fallback=False)
            if wp is None or wp <= 0:
                return None
            weth_spent = _dec(max(0, -weth_del.get(payer, 0)), 18)
            spent_usd = weth_spent * float(wp)
            eth_spent_total = weth_spent

        if spent_usd <= 0:
            return None

    total_usd = spent_usd

    # Coherence filter to kill false positives.
    # Skip for native-ETH buys: token price estimates can drift and should not block valid buys.
    if (not paid_with_eth) and usd_est > 0 and spent_usd > 0:
        if spent_usd < usd_est * 0.20:
            return None
        if spent_usd > usd_est * 5.0:
            return None

    return {
        "buyer": buyer,
        "usd": float(total_usd),
        "tokens": float(tokens_bought),
        "eth": float(eth_spent_total),
        "pay": {"eth": float(eth_spent_total), "usdc": float(usdc_spent), "usdt": float(usdt_spent), "weth": float(weth_spent)},
    }



def _max_inflow_addr(deltas_for_token: Dict[str, int]) -> Tuple[Optional[str], int]:
    best_addr = None
    best_in = 0
    for addr, d in (deltas_for_token or {}).items():
        if d > 0 and d > best_in:
            best_in = d
            best_addr = addr
    return best_addr, best_in


def _pick_final_seller(token_deltas: Dict[str, int], exclude_addrs: List[str]) -> Optional[str]:
    exclude = set(_norm(a) for a in exclude_addrs if a)
    best_addr = None
    best_out = 0
    for addr, delta in token_deltas.items():
        if addr in exclude:
            continue
        if delta < 0 and (-delta) > best_out:
            best_out = -delta
            best_addr = addr
    return best_addr


def _sell_from_receipt(tx_hash: str, receipt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if _receipt_has_ignored_erc721(receipt):
        return None

    # Use the receipt block number for historic Chainlink reads (eth_call at that block)
    block_number = None
    try:
        bn_hex = receipt.get("blockNumber")
        if isinstance(bn_hex, str) and bn_hex.startswith("0x"):
            block_number = int(bn_hex, 16)
    except Exception:
        block_number = None

    token_addresses = {
        TOKEN_ADDRESS: TOKEN_DECIMALS,
        USDC_ADDRESS: 6,
        USDT_ADDRESS: 6,
        WETH_ADDRESS: 18,
    }

    deltas = _aggregate_net_deltas_from_receipt(receipt, token_addresses)
    tdel = deltas.get(_norm(TOKEN_ADDRESS)) or {}
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

    seller = _pick_final_seller(tdel, exclude)
    if not seller:
        return None

    tokens_delta_int = int(tdel.get(seller, 0))
    if tokens_delta_int >= 0:
        return None

    tokens_sold = _dec(-tokens_delta_int, TOKEN_DECIMALS)

    # Price estimate for sanity filtering
    state = _load_state()
    cache = state.get("cache") or {}
    state["cache"] = cache

    price, _fdv = _token_price_usd_and_fdv(TOKEN_ADDRESS)
    if price is not None:
        cache["token_price_usd"] = float(price)
    else:
        price = cache.get("token_price_usd")

    _save_state(state)

    usd_est = (float(price) if price is not None else 0.0) * float(tokens_sold)

    usdc_del = deltas.get(_norm(USDC_ADDRESS)) or {}
    usdt_del = deltas.get(_norm(USDT_ADDRESS)) or {}
    weth_del = deltas.get(_norm(WETH_ADDRESS)) or {}

    recv_usdc, usdc_in = _max_inflow_addr(usdc_del)
    recv_usdt, usdt_in = _max_inflow_addr(usdt_del)
    recv_weth, weth_in = _max_inflow_addr(weth_del)

    receiver = None
    got_usd = 0.0

    # Prefer stablecoin inflow
    if usdc_in > 0 or usdt_in > 0:
        receiver = recv_usdc if usdc_in >= usdt_in else recv_usdt
        if receiver:
            got_usd += _dec(max(0, usdc_del.get(receiver, 0)), 6)
            got_usd += _dec(max(0, usdt_del.get(receiver, 0)), 6)

    # Fallback: WETH inflow
    if got_usd <= 0 and weth_in > 0 and recv_weth:
        receiver = recv_weth
        wp = _weth_price_usd(block_number=block_number, allow_live_fallback=False) or 0.0
        got_usd += _dec(max(0, weth_del.get(receiver, 0)), 18) * wp

    # Add ETH received only if tx.to is seller? Hard to do reliably. Skip to avoid false positives.

    if got_usd <= 0:
        return None

    total_usd = got_usd

    # Coherence filter
    if usd_est > 0 and got_usd > 0:
        if got_usd < usd_est * 0.20:
            return None
        if got_usd > usd_est * 5.0:
            return None

    return {
        "seller": seller,
        "usd": float(total_usd),
        "tokens": float(tokens_sold),
    }


# =========================
# Stake and burn detection
# =========================

def _classify_transfer_log(log: Dict[str, Any]) -> Optional[Tuple[str, str, str, int]]:
    # Only classify stake or burn when the Transfer log belongs to the CLAWD token contract
    if _norm(log.get("address", "")) != _norm(TOKEN_ADDRESS):
        return None

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
            await app.bot.send_photo(
                chat_id=chat_id,
                photo=f,
                caption=caption,
                parse_mode="HTML",
            )
    else:
        await app.bot.send_message(
            chat_id=chat_id,
            text=caption,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def _payment_line(kind: str, pay: Optional[Dict[str, float]]) -> str:
    """Format the payment section for buy alerts.

    Rules:
    - Only for kind == "buy".
    - Show ETH line if eth > 0.
    - Show USDC/USDT line if spent > 0.
    - Always end with a newline so the following Wallet line stays on its own line.
    """
    if kind != "buy" or not pay:
        return ""

    lines: List[str] = []
    eth = float(pay.get("eth") or 0.0)
    usdc = float(pay.get("usdc") or 0.0)
    usdt = float(pay.get("usdt") or 0.0)

    if eth > 0:
        lines.append(f"ETH: {eth:.2f}")
    if usdc > 0:
        lines.append(f"USDC: {int(round(usdc)):,}")
    if usdt > 0:
        lines.append(f"USDT: {int(round(usdt)):,}")

    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _event_caption(
    kind: str,
    tx_hash: str,
    amount_tokens: float,
    usd: float,
    wallet_addr: str,
    pay: Optional[Dict[str, float]] = None
) -> str:

    state = _load_state()
    usd_per_emoji = float(state["emoji_usd"][kind])

    if kind == "buy":
        title = "CLAWD BOUGHT!"
        emoji = LOBSTER
    elif kind == "stake":
        title = "CLAWD STAKED!"
        emoji = "🔒"
    else:
        title = "CLAWD BURNED!"
        emoji = "🔥"

    bar = _emoji_bar(usd, usd_per_emoji, emoji)

    tx_url = f"https://basescan.org/tx/{tx_hash}"
    wallet_url = f"https://basescan.org/address/{wallet_addr}"

    caption = (
        f"<b>{title}</b>\n\n"
        f"{bar}\n\n"
        f'CLAWD: {_fmt_token_amount(amount_tokens)} ({_fmt_int_usd(usd)}) (<a href="{tx_url}">Tx</a>)\n'
        + (_payment_line(kind, pay) if kind == "buy" else "")
        + f'Wallet: <a href="{wallet_url}">{_short_addr(wallet_addr)}</a>'
    )

    return caption


async def _dm_user(app, user_id: int, text: str) -> bool:
    try:
        await app.bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True)
        return True
    except Exception:
        return False


def _help_text() -> str:
    lines = []
    lines.append("Commands")
    lines.append("")
    lines.append("/help")
    lines.append("Show this message")
    lines.append("")
    lines.append("/stats")
    lines.append("Show price, market cap, wallet balances, and burned stats")
    lines.append("")
    lines.append("/scan <blocks_back> <min_buy_usd>")
    lines.append("Scan last N blocks and DM you the buys above the threshold")
    lines.append("Example: /scan 5000 2000")
    lines.append("")
    lines.append("/setmin <buy|stake|burn> <usd>")
    lines.append("Set minimum USD size per event type")
    lines.append("")
    lines.append("/setemoji <buy|stake|burn> <usd_per_emoji>")
    lines.append("Set USD value per lobster emoji (max 100 emojis)")
    lines.append("")
    lines.append("/cancel")
    lines.append("Cancel any running tasks")
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

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    cancelled = 0
    had_monitor = False

    for name, task in list(TASK_REGISTRY.items()):
        if not task.done():
            task.cancel()
            cancelled += 1
            if name == "monitor":
                had_monitor = True

    await update.message.reply_text(f"Cancelled {cancelled} task(s).")

    if had_monitor:
        try:
            _track_task("monitor", asyncio.create_task(monitor(context.application)))
            await update.message.reply_text("Monitor restarted.")
        except Exception:
            pass


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


async def _scan_and_dm(app, user_id: int, blocks_back: int, min_usd: float) -> None:
    # This scan runs in a thread but sends progress in realtime via run_coroutine_threadsafe
    loop = asyncio.get_running_loop()

    def _send_dm(text: str) -> None:
        fut = asyncio.run_coroutine_threadsafe(
            app.bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True),
            loop
        )
        try:
            fut.result(timeout=15)
        except Exception:
            pass

    def _run_scan_sync() -> None:
        t0 = time.time()
        _send_dm(f"Scan started. blocks_back={blocks_back} min_usd={_fmt_usd_compact(min_usd)}")

        latest = _get_latest_block()
        end = latest - max(0, WATCH_CONFIRMATIONS)
        if end < 0:
            end = 0
        start = max(0, end - blocks_back + 1)

        _send_dm(f"Range: {start} to {end}. Fetching logs...")
        logs = _get_logs_chunked(TOKEN_ADDRESS, start, end)
        _send_dm(f"Logs: {len(logs):,}. Building tx list...")

        tx_hashes: List[str] = []
        seen_tx = set()
        for lg in logs:
            h = lg.get("transactionHash")
            if not h or h in seen_tx:
                continue
            seen_tx.add(h)
            tx_hashes.append(h)

        _send_dm(f"Unique txs: {len(tx_hashes):,}. Fetching receipts...")

        matches: List[Tuple[str, Dict[str, Any]]] = []
        ok = 0
        fail = 0

        for i, h in enumerate(tx_hashes, start=1):
            try:
                receipt = _get_receipt(h)
                ok += 1
                buy = _buy_from_receipt(h, receipt)
                if buy and float(buy["usd"]) >= min_usd:
                    matches.append((h, buy))
            except Exception:
                fail += 1

            if i % 200 == 0:
                _send_dm(
                    f"Progress: {i:,}/{len(tx_hashes):,} ok={ok:,} fail={fail:,} "
                    f"matches={len(matches):,} elapsed={time.time()-t0:.1f}s"
                )

        matches.sort(key=lambda x: float(x[1]["usd"]), reverse=True)

        lines: List[str] = []
        lines.append("Scan finished")
        lines.append(f"Blocks: {blocks_back} (from {start} to {end})")
        lines.append(f"Logs: {len(logs):,}")
        lines.append(f"Unique txs: {len(tx_hashes):,}")
        lines.append(f"Receipts ok: {ok:,}")
        lines.append(f"Receipts failed: {fail:,}")
        lines.append(f"Matches (>= {_fmt_usd_compact(min_usd)}): {len(matches):,}")
        lines.append(f"Time: {time.time()-t0:.1f}s")

        if not matches:
            _send_dm("\n".join(lines))
            return

        state = _load_state()
        usd_per_emoji = float(state["emoji_usd"]["buy"])

        lines.append("")
        lines.append("Top results (max 20):")

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

        _send_dm("\n".join(lines))

    await asyncio.to_thread(_run_scan_sync)

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id

    # Mode 1: scan a single transaction hash and, if it's a BUY, send the same buy alert.
    if len(context.args) == 1:
        tx_hash = context.args[0].strip()
        if re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash):
            await update.message.reply_text("Scanning tx hash...")
            try:
                receipt = _get_receipt(tx_hash)
                if not receipt:
                    await update.message.reply_text("Transaction not found (no receipt).")
                    return
            except Exception:
                await update.message.reply_text("Failed to fetch receipt for this tx.")
                return

            buy = None
            try:
                buy = _buy_from_receipt(tx_hash, receipt)
            except Exception:
                buy = None

            if buy:
                caption = _event_caption("buy", tx_hash, float(buy["tokens"]), float(buy["usd"]), str(buy["buyer"]), pay=buy.get("pay"))
                await _send_photo_or_text(context.application, user_id, "buy", caption)
                await update.message.reply_text("Buy alert sent in DM.")
                return

            # Optional: detect if it's a sell, just to report correctly
            try:
                sell = _sell_from_receipt(tx_hash, receipt)
            except Exception:
                sell = None

            if sell:
                await update.message.reply_text("That tx looks like a SELL. This command only sends alerts for buys.")
            else:
                await update.message.reply_text("That tx is not detected as a buy (no alert sent).")
            return

        await update.message.reply_text("Usage: /scan <blocks_back> <min_buy_usd>  OR  /scan <tx_hash>")
        return

    # Mode 2: scan a block range for buys
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /scan <blocks_back> <min_buy_usd>")
        return

    try:
        blocks_back = int(context.args[0])
        min_usd = float(context.args[1])
    except Exception:
        await update.message.reply_text("Invalid args. Example: /scan 5000 2000")
        return

    if blocks_back < 1:
        blocks_back = 1
    if blocks_back > 20000:
        blocks_back = 20000

    await update.message.reply_text(
        f"Scanning last {blocks_back} blocks for buys >= {_fmt_usd_compact(min_usd)}. Check your DM."
    )

    _track_task(f"scan:{update.effective_user.id}", asyncio.create_task(_scan_and_dm(context.application, update.effective_user.id, blocks_back, min_usd)))


CHAINLINK_ETH_USD_FEED = "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70"

_SEL_DECIMALS = "0x313ce567"
_SEL_LATEST_ROUND = "0xfeaf968c"

def _rpc_eth_call(to_addr: str, data_hex: str) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to_addr, "data": data_hex}, "latest"],
    }
    r = requests.post(BASE_RPC_URL, json=payload, timeout=10)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["result"]

def _abi_int256(word_hex: str) -> int:
    # word_hex is a 32 byte hex string like "0x" + 64 hex chars
    x = int(word_hex, 16)
    if x >= 1 << 255:
        x -= 1 << 256
    return x

def _get_eth_price_now() -> float:
    """
    Reads ETH/USD from Chainlink on Base via your Ankr Base RPC.
    No Dexscreener dependency.
    """
    try:
        dec_raw = _rpc_eth_call(CHAINLINK_ETH_USD_FEED, _SEL_DECIMALS)
        decimals = int(dec_raw, 16)

        lr_raw = _rpc_eth_call(CHAINLINK_ETH_USD_FEED, _SEL_LATEST_ROUND)
        if not lr_raw or lr_raw == "0x":
            return 0.0

        # latestRoundData() returns 5 words (32 bytes each). The answer is the 2nd word.
        data = lr_raw[2:].rjust(64 * 5, "0")
        answer_word = "0x" + data[64:128]
        answer = _abi_int256(answer_word)

        if answer <= 0:
            return 0.0

        return float(answer) / (10 ** decimals)
    except Exception:
        return 0.0


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    state = _load_state()
    cache = state.get("cache") or {}
    state["cache"] = cache

    price, fdv = _token_price_usd_and_fdv(TOKEN_ADDRESS)

    # Use live ETH price for stats (WETH USD should never be historical here)
    wp = _get_eth_price_now()

    if price is not None:
        cache["token_price_usd"] = float(price)
    if fdv is not None:
        cache["token_fdv"] = float(fdv)
    if wp and wp > 0:
        cache["eth_price_usd_now"] = float(wp)

    if price is None:
        price = cache.get("token_price_usd")
    if fdv is None:
        fdv = cache.get("token_fdv")
    if (not wp) or wp <= 0:
        wp = float(cache.get("eth_price_usd_now") or 0.0)

    _save_state(state)

    try:
        clawd_bal_int = _erc20_balance_of(TOKEN_ADDRESS, CLAWD_WALLET)
        weth_bal_int = _erc20_balance_of(WETH_ADDRESS, CLAWD_WALLET)
        burned_bal_int = _erc20_balance_of(TOKEN_ADDRESS, BURN_ADDRESS)
        incinerator_bal_int = _erc20_balance_of(TOKEN_ADDRESS, INCINERATOR_ADDRESS)
    except Exception as e:
        await update.message.reply_text(f"Failed to read balances from RPC: {e}")
        return

    clawd_amt = _dec(clawd_bal_int, TOKEN_DECIMALS)
    weth_amt = _dec(weth_bal_int, 18)
    burned_amt = _dec(burned_bal_int, TOKEN_DECIMALS)
    incinerator_amt = _dec(incinerator_bal_int, TOKEN_DECIMALS)

    clawd_usd = (float(price or 0.0)) * clawd_amt
    weth_usd = (float(wp or 0.0)) * weth_amt
    burned_usd = (float(price or 0.0)) * burned_amt
    incinerator_usd = (float(price or 0.0)) * incinerator_amt

    total_value = clawd_usd + weth_usd

    total_supply = 100_000_000_000.0
    burned_pct = (burned_amt / total_supply) * 100.0 if total_supply > 0 else 0.0
    burned_bil = burned_amt / 1_000_000_000.0
    incinerator_pct = (incinerator_amt / total_supply) * 100.0 if total_supply > 0 else 0.0
    incinerator_bil = incinerator_amt / 1_000_000_000.0

    wallet_link = f"https://basescan.org/address/{CLAWD_WALLET}"
    wallet_html = f'<a href="{wallet_link}">{CLAWD_WALLET}</a>'

    lines: List[str] = []
    lines.append("<b>📊 CLAWD Stats</b>")
    lines.append(f"Current price: {_fmt_price(price) if price is not None else 'N/A'}")
    lines.append(f"Market cap: {_fmt_int_usd(fdv) if fdv is not None else 'N/A'}")
    lines.append("")
    lines.append("<b>🦞 My Wallet</b>")
    lines.append(wallet_html)
    lines.append(f"{_fmt_big(clawd_amt)} CLAWD ({_fmt_int_usd(clawd_usd)})")
    lines.append(f"{_fmt_weth_two(weth_amt)} WETH ({_fmt_int_usd(weth_usd)})")
    lines.append(f"Total value: {_fmt_int_usd(total_value)}")
    lines.append("")
    lines.append("<b>🔥 Burned</b>")
    lines.append(f"{burned_bil:.2f}B CLAWD ({_fmt_int_usd(burned_usd)}) · {burned_pct:.2f}% of supply")
    lines.append(f"({incinerator_bil:.2f}B CLAWD pending in the incinerator · {incinerator_pct:.2f}% of supply)")
    lines.append("")
    lines.append("")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


async def cmd_burned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    # Parse args like: /burned 7d
    days = 7
    if context.args:
        arg = (context.args[0] or "").strip().lower()
        m = re.match(r"^(\d+)(d)?$", arg)
        if m:
            try:
                days = int(m.group(1))
            except Exception:
                days = 7

    days = max(1, min(60, days))

    try:
        latest = _get_latest_block()
        end_block = latest - max(0, WATCH_CONFIRMATIONS)
        if end_block < 0:
            end_block = 0

        now_ts = int(time.time())
        start_ts = now_ts - days * 86400
        start_block = _find_block_by_timestamp(start_ts, end_block)

        to_topic = "0x" + _norm(BURN_ADDRESS).replace("0x", "").rjust(64, "0")
        logs = _get_logs_chunked_topics(
            TOKEN_ADDRESS,
            start_block,
            end_block,
            [TRANSFER_TOPIC0, None, to_topic],
        )

        by_day: Dict[str, int] = defaultdict(int)
        ts_cache: Dict[int, int] = {}

        for lg in logs:
            bn_hex = lg.get("blockNumber")
            if not isinstance(bn_hex, str) or not bn_hex.startswith("0x"):
                continue
            bn = int(bn_hex, 16)

            ts = ts_cache.get(bn)
            if ts is None:
                ts = int(_get_block_timestamp(bn) or 0)
                ts_cache[bn] = ts

            data_hex = lg.get("data") or "0x0"
            try:
                amt_int = int(data_hex, 16)
            except Exception:
                amt_int = 0
            if amt_int <= 0:
                continue

            day = time.strftime("%Y-%m-%d", time.gmtime(ts))
            by_day[day] += amt_int

        days_list: List[str] = []
        daily_tokens: List[float] = []

        for i in range(days - 1, -1, -1):
            day_ts = now_ts - i * 86400
            day = time.strftime("%Y-%m-%d", time.gmtime(day_ts))
            days_list.append(day)
            daily_tokens.append(_dec(by_day.get(day, 0), TOKEN_DECIMALS))

        cumulative: List[float] = []
        running = 0.0
        for v in daily_tokens:
            running += float(v)
            cumulative.append(running)

        import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

def _fmt_compact_int(n: float) -> str:
    v = float(n)
    av = abs(v)
    if av >= 1_000_000_000:
        return f"{int(round(v / 1_000_000_000.0))}B"
    if av >= 1_000_000:
        return f"{int(round(v / 1_000_000.0))}M"
    if av >= 1_000:
        return f"{int(round(v / 1_000.0))}K"
    return str(int(round(v)))

x = list(range(len(days_list)))
fig, ax = plt.subplots(figsize=(10, 5))

# Bars: daily burned
bars = ax.bar(x, daily_tokens, color="#d62728")  # red
ax.set_title(f"CLAWD Burned per day (last {days}d)")
ax.set_xlabel("Day (UTC)")
ax.set_ylabel("Burned per day (CLAWD)")
ax.set_xticks(x)
ax.set_xticklabels([d[5:] for d in days_list], rotation=45, ha="right")

# Rounded labels on each bar
for i, b in enumerate(bars):
    h = float(b.get_height())
    if h <= 0:
        continue
    ax.annotate(
        _fmt_compact_int(h),
        (b.get_x() + b.get_width() / 2.0, h),
        xytext=(0, 6),
        textcoords="offset points",
        ha="center",
        va="bottom",
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#d62728", lw=1),
    )

# Line: cumulative burned
ax2 = ax.twinx()
ax2.plot(x, cumulative, color="#111111", linewidth=2)  # near-black
ax2.set_ylabel("Cumulative (CLAWD)")

# Label only the last cumulative point
if cumulative:
    last_x = x[-1]
    last_y = float(cumulative[-1])
    ax2.annotate(
        _fmt_compact_int(last_y),
        (last_x, last_y),
        xytext=(10, 0),
        textcoords="offset points",
        ha="left",
        va="center",
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#111111", lw=1),
    )

fig.tight_layout()

        try:
            os.makedirs(DATA_PATH, exist_ok=True)
        except Exception:
            pass

        out_path = os.path.join(DATA_PATH, f"burned_{days}d.png")
        fig.savefig(out_path, dpi=150)
        plt.close(fig)

        total_burned_period = cumulative[-1] if cumulative else 0.0
        msg = f"<b>🔥 Burned last {days}d</b>\nTotal: {_fmt_num(total_burned_period)} CLAWD"
        with open(out_path, "rb") as f:
            await update.message.reply_photo(photo=f, caption=msg, parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(f"Failed to build burned chart: {e}")


# =========================
# Watcher
# =========================

def _eid(kind: str, tx_hash: str, log_index_hex: str) -> str:
    return f"{kind}:{tx_hash}:{log_index_hex}"


def _monitor_tick_sync() -> List[Tuple[str, str, str, str]]:
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
        return []

    logs = _get_logs_chunked(TOKEN_ADDRESS, start, end)

    seen_buy = set(state["watch"]["seen"].get("buy") or [])
    seen_stake = set(state["watch"]["seen"].get("stake") or [])
    seen_burn = set(state["watch"]["seen"].get("burn") or [])

    # cache token price for buy sanity check and burn/stake USD
    price, _fdv = _token_price_usd_and_fdv(TOKEN_ADDRESS)
    if price is not None:
        state["cache"]["token_price_usd"] = float(price)
    else:
        price = state.get("cache", {}).get("token_price_usd")
    _save_state(state)

    token_price = float(price) if price is not None else 0.0

    state_min = state["min_usd"]

    outgoing: List[Tuple[str, str, str]] = []  # (kind, caption, wallet_addr_for_link)

    txs_for_buy: List[str] = []
    tx_seen_local = set()

    for lg in logs:
        tx_hash = lg.get("transactionHash")
        log_index = lg.get("logIndex", "0x0")

        classified = _classify_transfer_log(lg)
        if classified and tx_hash:
            kind, from_addr, _to, amount_int = classified
            event_id = _eid(kind, tx_hash, log_index)

            if kind == "stake":
                if event_id not in seen_stake:
                    amount = _dec(amount_int, TOKEN_DECIMALS)
                    usd = amount * token_price
                    if usd >= float(state_min["stake"]):
                        caption = _event_caption("stake", tx_hash, amount, usd, from_addr)
                        outgoing.append(("stake", event_id, caption, from_addr))
                    seen_stake.add(event_id)

            elif kind == "burn":
                if event_id not in seen_burn:
                    amount = _dec(amount_int, TOKEN_DECIMALS)
                    usd = amount * token_price
                    if usd >= float(state_min["burn"]):
                        caption = _event_caption("burn", tx_hash, amount, usd, from_addr)
                        outgoing.append(("burn", event_id, caption, from_addr))
                    seen_burn.add(event_id)

        if tx_hash and tx_hash not in tx_seen_local:
            tx_seen_local.add(tx_hash)
            txs_for_buy.append(tx_hash)

    for h in txs_for_buy:
        buy_id = f"buy:{h}"
        if buy_id in seen_buy:
            continue

        # Only mark as seen after successful processing (avoid losing events on RPC hiccups)
        try:
            receipt = _get_receipt(h)
            buy = _buy_from_receipt(h, receipt)
            if buy:
                usd = float(buy["usd"])
                if usd >= float(state_min["buy"]):
                    tokens = float(buy["tokens"])
                    buyer = buy["buyer"]
                    caption = _event_caption("buy", h, tokens, usd, buyer, float(buy.get("eth", 0.0)))
                    outgoing.append(("buy", buy_id, caption, buyer))
            seen_buy.add(buy_id)
        except Exception:
            continue

    state["watch"]["last_scanned_block"] = end
    state["watch"]["seen"]["buy"] = _prune_seen(list(seen_buy))
    state["watch"]["seen"]["stake"] = _prune_seen(list(seen_stake))
    state["watch"]["seen"]["burn"] = _prune_seen(list(seen_burn))
    _save_state(state)

    return outgoing


async def monitor(app) -> None:
    while True:
        try:
            outgoing = await asyncio.to_thread(_monitor_tick_sync)

            # Dedup at send-time to prevent double alerts (restart, overlap, RPC hiccups)
            state = _load_state()
            state.setdefault("watch", {}).setdefault("sent_public", {"buy": [], "stake": [], "burn": []})

            sent_buy = set(state["watch"]["sent_public"].get("buy") or [])
            sent_stake = set(state["watch"]["sent_public"].get("stake") or [])
            sent_burn = set(state["watch"]["sent_public"].get("burn") or [])

            for kind, uid, caption, _wallet in outgoing:
                if kind == "buy":
                    if uid in sent_buy:
                        continue
                    sent_buy.add(uid)
                    state["watch"]["sent_public"]["buy"] = _prune_seen(list(sent_buy))
                elif kind == "stake":
                    if uid in sent_stake:
                        continue
                    sent_stake.add(uid)
                    state["watch"]["sent_public"]["stake"] = _prune_seen(list(sent_stake))
                elif kind == "burn":
                    if uid in sent_burn:
                        continue
                    sent_burn.add(uid)
                    state["watch"]["sent_public"]["burn"] = _prune_seen(list(sent_burn))

                # Persist before sending to avoid duplicates if the process crashes after send
                _save_state(state)

                await _send_photo_or_text(app, POST_CHAT_ID, kind, caption)
        except Exception:
            pass

        await asyncio.sleep(WATCH_POLL_SEC)


async def post_init(app) -> None:
    try:
        if ALLOWED_CHAT_ID == 0:
            await app.bot.send_message(chat_id=ADMIN_ID, text="CLAWD bot started (test mode). Use /help")
        else:
            await app.bot.send_message(chat_id=POST_CHAT_ID, text="CLAWD bot started. Use /help")
    except Exception:
        pass

    _track_task("monitor", asyncio.create_task(monitor(app)))


# =========================
# Main
# =========================

def main() -> None:
    _ensure_data_dir()

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("setmin", cmd_setmin))
    app.add_handler(CommandHandler("setemoji", cmd_setemoji))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("burned", cmd_burned))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
