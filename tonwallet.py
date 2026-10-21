"""کیف پول TON برای امضا و ارسال تراکنش‌هایی که API مارکت‌اپ (Marketapp) برای خرید استارز/پریمیوم برمی‌گرداند.

- نسخه‌های پشتیبانی‌شده: V4R2 و V5R1 (Tonkeeper نسخه‌ی جدید V5R1 است).
- فقط به pytoniq-core (سلول/BOC و امضای ed25519) نیاز دارد؛ ارتباط با شبکه از طریق HTTP API توِن‌سنتر (toncenter v2).
- نتیجه‌ی ارسال سه‌حالته است (مثل provider.py): ok | fail | unknown. اگر فقط تأیید ارسال در شبکه‌ی TON را
  نتوانیم ببینیم، نتیجه «unknown» است و هیچ‌وقت خودکار دوباره ارسال نمی‌شود (جلوگیری از پرداخت دوباره).
- امنیت: عبارت بازیابی (mnemonic) فقط از تنظیمات (ستون secret) خوانده و هرگز لاگ یا نمایش داده نمی‌شود.
  برای این کار یک کیف پول داغ جدا با موجودی کم بسازید، نه کیف پول اصلی.
"""
import asyncio
import base64
import logging
import time

log = logging.getLogger("tonwallet")

try:  # وابستگی اختیاری؛ اگر نصب نباشد فقط همین قابلیت غیرفعال می‌شود
    from pytoniq_core import Address, Builder, Cell, CurrencyCollection, ExternalMsgInfo, InternalMsgInfo, MessageAny, StateInit
    from pytoniq_core.crypto.keys import mnemonic_is_valid, mnemonic_to_private_key, private_key_to_public_key
    from pytoniq_core.crypto.signature import sign_message
    from pytoniq_core.tlb.custom.wallet import WalletMessage, WalletV4Data, WalletV5R1Data
    AVAILABLE = True
except Exception:  # noqa: BLE001
    AVAILABLE = False

from ton_codes import WALLET_V4R2_CODE_B64, WALLET_V5R1_CODE_B64

SEND_MODE = 3                  # فی را جدا از مبلغ کسر کن + خطاها نادیده گرفته شوند
V5R1_MAINNET_WALLET_ID = 2147483409   # = (client ctx 0x80000000) XOR (-239 & 0xFFFFFFFF)
V4R2_WALLET_ID = 698983191
NANO = 10 ** 9
MIN_RESERVE_NANO = 50_000_000  # 0.05 TON کف موجودی برای کارمزد شبکه


class WalletError(Exception):
    pass


def _code(version: str):
    b = WALLET_V5R1_CODE_B64 if version == "v5r1" else WALLET_V4R2_CODE_B64
    return Cell.one_from_boc(base64.b64decode(b))


class TonWallet:
    def __init__(self, mnemonic: str, version: str = "v5r1", wc: int = 0):
        if not AVAILABLE:
            raise WalletError("pytoniq-core is not installed (pip install pytoniq-core)")
        version = (version or "v5r1").lower()
        if version not in ("v5r1", "v4r2"):
            raise WalletError("unsupported wallet version")
        words = (mnemonic or "").lower().split()
        if len(words) != 24 or not mnemonic_is_valid(words):
            raise WalletError("mnemonic must be 24 valid words")
        _pub, self._priv = mnemonic_to_private_key(words)
        self.public_key = private_key_to_public_key(self._priv)
        self.version, self.wc = version, wc
        if version == "v5r1":
            self.wallet_id = V5R1_MAINNET_WALLET_ID
            data = WalletV5R1Data(seqno=0, wallet_id=self.wallet_id, public_key=self.public_key).serialize()
        else:
            self.wallet_id = V4R2_WALLET_ID + wc
            data = WalletV4Data(seqno=0, wallet_id=self.wallet_id, public_key=self.public_key, plugins=None).serialize()
        self.state_init = StateInit(code=_code(version), data=data)
        self.address = Address((wc, self.state_init.serialize().hash))

    # ---------- نمایش ----------
    def address_str(self, bounceable=False) -> str:
        return self.address.to_str(is_user_friendly=True, is_bounceable=bounceable)

    # ---------- ساخت پیام‌ها ----------
    @staticmethod
    def internal_message(dest: str, amount_nano: int, payload_b64: str = "", state_init_b64: str = ""):
        """یک آیتم از transaction.messages مارکت‌اپ را به WalletMessage داخلی تبدیل می‌کند."""
        d = Address(dest)
        body = Cell.one_from_boc(base64.b64decode(payload_b64)) if payload_b64 else Cell.empty()
        init = None
        if state_init_b64:
            init = StateInit.deserialize(Cell.one_from_boc(base64.b64decode(state_init_b64)).begin_parse())
        info = InternalMsgInfo(True, d.is_bounceable, False, None, d, CurrencyCollection(grams=int(amount_nano), other=None), 0, 0, 0, 0)
        return WalletMessage(send_mode=SEND_MODE, message=MessageAny(info=info, init=init, body=body))

    def transfer_body(self, seqno: int, messages: list, valid_until: int) -> Cell:
        if not 1 <= len(messages) <= 4:
            raise WalletError("1..4 messages per transfer")
        if seqno == 0:
            valid_until = 0xFFFFFFFF
        if self.version == "v4r2":
            sm = Builder().store_uint(self.wallet_id, 32).store_uint(valid_until, 32).store_uint(seqno, 32).store_uint(0, 8)
            for m in messages:
                sm.store_cell(m.serialize())
            sm = sm.end_cell()
            return Builder().store_bytes(sign_message(sm.hash, self._priv)).store_cell(sm).end_cell()
        # v5r1: پیام امضا‌شده + امضا در انتها
        actions = Cell.empty()
        for m in messages:
            act = Builder().store_uint(0x0ec3c86d, 32).store_uint(m.send_mode, 8).store_ref(m.message.serialize()).end_cell()
            actions = Builder().store_ref(actions).store_cell(act).end_cell()
        packed = Builder().store_uint(1, 1).store_ref(actions).store_uint(0, 1).end_cell()
        sm = (Builder().store_uint(0x7369676e, 32).store_uint(self.wallet_id, 32).store_uint(valid_until, 32)
              .store_uint(seqno, 32).store_cell(packed).end_cell())
        return Builder().store_cell(sm).store_bytes(sign_message(sm.hash, self._priv)).end_cell()

    def external_message(self, seqno: int, messages: list, valid_until: int) -> Cell:
        body = self.transfer_body(seqno, messages, valid_until)
        return MessageAny(info=ExternalMsgInfo(None, self.address, 0), init=None, body=body).serialize()


# ---------- اعتبارسنجی تراکنش دریافتی از API ----------
def parse_transaction(tx: dict):
    """tx = {"validUntil": int, "messages": [{"address","amount","payload","stateInit"}]} → (valid_until, [dict])"""
    if not isinstance(tx, dict) or not isinstance(tx.get("messages"), list) or not tx["messages"]:
        raise WalletError("transaction has no messages")
    msgs = []
    for m in tx["messages"]:
        try:
            amount = int(str(m["amount"]))
            addr = str(m["address"])
            Address(addr)
        except (KeyError, ValueError, TypeError) as e:
            raise WalletError(f"bad message in transaction: {type(e).__name__}") from e
        if amount < 0:
            raise WalletError("negative amount")
        msgs.append({"address": addr, "amount": amount, "payload": m.get("payload") or "", "stateInit": m.get("stateInit") or ""})
    return int(tx.get("validUntil") or 0), msgs


def total_nano(msgs) -> int:
    return sum(m["amount"] for m in msgs)


# ---------- شبکه (toncenter v2) ----------
class Toncenter:
    def __init__(self, http, base="https://toncenter.com", api_key=""):
        self.http, self.base, self.key = http, (base or "https://toncenter.com").rstrip("/"), api_key

    def _h(self):
        return {"X-API-Key": self.key} if self.key else {}

    async def wallet_info(self, address: str) -> dict:
        async with self.http.get(f"{self.base}/api/v2/getWalletInformation", params={"address": address}, headers=self._h(), timeout=20) as r:
            j = await r.json(content_type=None)
            if r.status >= 400 or not j.get("ok"):
                raise WalletError(f"toncenter {r.status}: {str(j.get('error', ''))[:120]}")
            res = j.get("result") or {}
            return {"balance": int(res.get("balance") or 0), "seqno": int(res.get("seqno") or 0),
                    "state": res.get("account_state", ""), "is_wallet": bool(res.get("wallet"))}

    async def send_boc(self, boc_b64: str):
        async with self.http.post(f"{self.base}/api/v2/sendBoc", json={"boc": boc_b64}, headers=self._h(), timeout=30) as r:
            j = await r.json(content_type=None)
            return r.status, j


async def get_info(shop, s: dict) -> dict:
    """اطلاعات کیف پول برای دکمه‌ی تست: آدرس مشتق‌شده، موجودی و seqno."""
    w = TonWallet(s.get("mapp_wallet_mnemonic", ""), s.get("mapp_wallet_version", "v5r1"))
    tc = Toncenter(shop.http, s.get("mapp_toncenter_url", ""), s.get("toncenter_key", ""))
    info = await tc.wallet_info(w.address_str())
    info.update({"address": w.address_str(), "address_raw": w.address.to_str(is_user_friendly=False), "version": w.version})
    want = (s.get("mapp_wallet_address") or "").strip()
    info["matches"] = None
    if want:
        try:
            info["matches"] = Address(want).hash_part == w.address.hash_part
        except Exception:  # noqa: BLE001
            info["matches"] = False
    return info


async def send_transaction(shop, s: dict, tx: dict, wait_sec: int = 90):
    """امضا و ارسال یک تراکنش مارکت‌اپ. خروجی: (state ok|fail|unknown, توضیح, hash پیام یا '')."""
    try:
        w = TonWallet(s.get("mapp_wallet_mnemonic", ""), s.get("mapp_wallet_version", "v5r1"))
        valid_until, msgs = parse_transaction(tx)
        want = (s.get("mapp_wallet_address") or "").strip()
        if want and Address(want).hash_part != w.address.hash_part:
            return "fail", f"wallet address mismatch: derived {w.address_str()} != configured (check wallet version v4r2/v5r1)", ""
        now = int(time.time())
        if valid_until and valid_until <= now + 20:
            return "fail", "transaction expired (validUntil)", ""
        cap = float(s.get("mapp_max_ton") or 0)
        if cap and total_nano(msgs) > cap * NANO:
            return "fail", f"transaction amount {total_nano(msgs) / NANO:.3f} TON exceeds safety cap {cap} TON", ""
        tc = Toncenter(shop.http, s.get("mapp_toncenter_url", ""), s.get("toncenter_key", ""))
        info = await tc.wallet_info(w.address_str())
        if not info["is_wallet"] or info["state"] != "active":
            return "fail", f"wallet {w.address_str()} is not active/deployed (state={info['state']})", ""
        need = total_nano(msgs) + MIN_RESERVE_NANO
        if info["balance"] < need:
            return "fail", f"wallet balance {info['balance'] / NANO:.3f} TON < required {need / NANO:.3f} TON", ""
        wmsgs = [w.internal_message(m["address"], m["amount"], m["payload"], m["stateInit"]) for m in msgs]
        vu = min(valid_until, now + 180) if valid_until else now + 120
        ext = w.external_message(info["seqno"], wmsgs, vu)
        boc = base64.b64encode(ext.to_boc()).decode()
        ref = ext.hash.hex()
    except WalletError as e:
        return "fail", str(e)[:200], ""
    except Exception as e:  # noqa: BLE001  خطای ساخت/اعتبارسنجی؛ هنوز چیزی ارسال نشده
        log.warning("tx build failed: %s", type(e).__name__)
        return "fail", f"{type(e).__name__}: {str(e)[:160]}", ""

    sent_err = ""
    try:
        status, j = await tc.send_boc(boc)
        if status >= 400 or not j.get("ok"):
            if 400 <= status < 500 and status != 429:
                return "fail", f"toncenter rejected: {str(j.get('error', ''))[:160]}", ""
            sent_err = f"toncenter {status}"
    except (asyncio.TimeoutError, OSError) as e:
        sent_err = f"{type(e).__name__}"
    except Exception as e:  # noqa: BLE001
        sent_err = f"{type(e).__name__}: {str(e)[:100]}"
    # بررسی ورود تراکنش به بلاک‌چین: seqno کیف پول باید زیاد شود
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        await asyncio.sleep(4)
        try:
            cur = await tc.wallet_info(w.address_str())
            if cur["seqno"] > info["seqno"]:
                return "ok", "confirmed on-chain (wallet seqno advanced)", ref
        except Exception:  # noqa: BLE001
            continue
    return "unknown", ("send error: " + sent_err + "; " if sent_err else "") + "broadcast but not confirmed within timeout — verify in wallet before retrying", ref
