"""Цепь + состояние + системные контракты + детерминированное применение."""
import json
import os
import re
import tempfile
import threading

from ..config import (
    BLOCK_REWARD, MIN_STAKE, UNSTAKE_DELAY, FAUCET_AMOUNT, FAUCET_ONCE,
    DNS_PRICE, DOMAIN_TTL, AUCTION_DURATION, EXTEND_WINDOW, BID_STEP,
    min_bid_for, NAME_RE, GENESIS_SUPPLY,
)
from .block import Block
from .transaction import Transaction
from ..vm.compiler import compile_tolk
from ..vm.tvm import TVM

from ..system import (
    all_system_addresses,
    ConfigContract, ElectorContract, Minter, DNSRoot, BridgeContract,
)


GENESIS_TS = 1_700_000_000


class ChainError(Exception):
    pass


class Blockchain:
    def __init__(self, genesis_validators):
        if not genesis_validators:
            raise ChainError("genesis must have at least 1 validator")
        self.lock = threading.RLock()
        self.validators = list(genesis_validators)
        self.blocks = []
        self.mempool = {}

        # ── пользовательское состояние ──
        self._bal = {}
        self._nonce = {}
        self._contracts = {}
        self._dns = {}
        self._auctions = {}
        self._refunds = {}
        self._faucet = set()

        # ── genesis-блок ──
        v0 = genesis_validators[0]
        g = Block(0, "0" * 64, [], v0["address"], v0["pubkey"], "", GENESIS_TS)
        g._hash = g.compute_hash()
        self.blocks.append(g)

        # ═══ СИСТЕМНЫЕ КОНТРАКТЫ — ЗАЛОЖЕНЫ В GENESIS ═══
        # Адреса детерминированы от имени роли + workchain(-1) → одинаковы на всех нодах
        self.system_addresses = all_system_addresses()

        # Инициализация контрактов (порядок важен — config до elector)
        self.config   = ConfigContract(self)
        self.elector  = ElectorContract(self)
        self.minter   = Minter(self)
        self.dns_root = DNSRoot(self)
        self.bridge   = BridgeContract(self)

        # 1) Начальная эмиссия в treasury
        self.minter.init_genesis_supply(GENESIS_SUPPLY)

        # 2) Genesis-валидаторы → в Elector (stake=0, но они в списке)
        for v in genesis_validators:
            self.elector.validators[v["address"]] = {
                "stake": 0.0,
                "pubkey": v.get("pubkey", ""),
                "since": g.timestamp,
            }
        self.elector.last_election = g.timestamp

        # 3) Оракулы моста = genesis-валидаторы
        self.bridge.set_oracles([v["address"] for v in genesis_validators])

    # ═══════════ ГЕТТЕРЫ ═══════════

    def head(self):
        with self.lock:
            return self.blocks[-1]

    def height(self):
        with self.lock:
            return self.blocks[-1].height

    def get_balance(self, a):
        with self.lock:
            return self._bal.get(a, 0.0)

    def get_nonce(self, a):
        with self.lock:
            return self._nonce.get(a, 0)

    def get_stake(self, a):
        with self.lock:
            v = self.elector.validators.get(a)
            return v["stake"] if v else 0.0

    def active_validators(self):
        """Источник истины — Elector."""
        with self.lock:
            vs = self.elector.active_validators()
            if vs:
                return vs
            return [v["address"] for v in self.validators]

    def expected_producer(self, height):
        with self.lock:
            vs = self.active_validators()
            return {"address": vs[height % len(vs)]}

    # ═══════════ DNS ═══════════

    def dns_resolve(self, n):
        with self.lock:
            return self._dns.get(n.lower())

    def dns_domains(self):
        with self.lock:
            out = [{"name": k, **v} for k, v in self._dns.items()]
            out.sort(key=lambda x: x.get("registered_at", 0), reverse=True)
            return out

    def auction_get(self, n):
        with self.lock:
            return self._auctions.get(n.lower())

    def auctions_list(self):
        with self.lock:
            a, s = [], []
            for x in self._auctions.values():
                (s if x.get("settled") else a).append(x)
            return a, s

    def refund_of(self, n, a):
        with self.lock:
            return self._refunds.get(n.lower(), {}).get(a, 0.0)

    # ═══════════ PRECHECK ═══════════

    def _precheck(self, tx, now):
        op, p = tx.op, tx.params

        if op == "transfer":
            return True, ""

        if op == "faucet":
            if FAUCET_ONCE and tx.from_addr in self._faucet:
                return False, "faucet already claimed"
            if abs(tx.amount) > 1e-9:
                return False, "faucet amount=0"
            return True, ""

        if op == "stake":
            if tx.amount < 1e-9:
                return False, "stake amount must be > 0"
            return True, ""

        if op == "unstake":
            v = self.elector.validators.get(tx.from_addr)
            if not v:
                return False, "not a validator"
            if v["stake"] < tx.amount:
                return False, "exceeds stake"
            return True, ""

        if op == "deploy_contract":
            h = p.get("code", "")
            if not h:
                return False, "no code"
            try:
                bytes.fromhex(h)
            except Exception:
                return False, "bad hex"
            return True, ""

        if op == "call_contract":
            if p.get("address") not in self._contracts:
                return False, "no contract"
            return True, ""

        if op == "dns_register":
            n = str(p.get("name", "")).lower()
            if not re.match(NAME_RE, n):
                return False, "bad name"
            if n in self._dns and self._dns[n]["expires"] > now:
                return False, "taken"
            if n in self._auctions and not self._auctions[n].get("settled"):
                return False, "auction active"
            if abs(tx.amount - DNS_PRICE) > 1e-9:
                return False, f"price must be {DNS_PRICE}"
            return True, ""

        if op == "dns_renew":
            n = str(p.get("name", "")).lower()
            d = self._dns.get(n)
            if not d or d["owner"] != tx.from_addr:
                return False, "not owner"
            if abs(tx.amount - DNS_PRICE) > 1e-9:
                return False, "price mismatch"
            return True, ""

        if op == "dns_set_wallet":
            n = str(p.get("name", "")).lower()
            d = self._dns.get(n)
            if not d or d["owner"] != tx.from_addr:
                return False, "not owner"
            if not p.get("wallet"):
                return False, "wallet required"
            return True, ""

        if op == "auction_start":
            n = str(p.get("name", "")).lower()
            if not re.match(NAME_RE, n):
                return False, "bad name"
            if n in self._dns and self._dns[n]["expires"] > now:
                return False, "taken"
            if n in self._auctions and not self._auctions[n].get("settled"):
                return False, "running"
            if tx.amount < min_bid_for(n):
                return False, f"min bid {min_bid_for(n)}"
            return True, ""

        if op == "auction_bid":
            n = str(p.get("name", "")).lower()
            a = self._auctions.get(n)
            if not a or a["settled"]:
                return False, "no auction"
            if now > a["end_time"]:
                return False, "ended"
            if tx.amount < a["highest_bid"] * BID_STEP:
                return False, "too low"
            return True, ""

        if op == "auction_settle":
            n = str(p.get("name", "")).lower()
            a = self._auctions.get(n)
            if not a or a["settled"]:
                return False, "no auction"
            if now < a["end_time"]:
                return False, "not ended"
            return True, ""

        if op == "auction_refund":
            n = str(p.get("name", "")).lower()
            if self._refunds.get(n, {}).get(tx.from_addr, 0) <= 0:
                return False, "no refund"
            return True, ""

        # ── системные op ──
        if op == "config_propose":
            key = str(p.get("key", ""))
            from ..system.config_contract import CONFIG_KEYS
            if key not in CONFIG_KEYS:
                return False, f"unknown key: {key}"
            if tx.from_addr not in self.elector.validators:
                return False, "not a validator"
            return True, ""

        if op == "bridge_deposit":
            if p.get("chain") not in ("ethereum", "ton", "bitcoin"):
                return False, "unsupported chain"
            if tx.amount <= 0:
                return False, "amount > 0"
            return True, ""

        if op == "bridge_withdraw":
            if tx.from_addr not in self.bridge.oracles:
                return False, "not an oracle"
            return True, ""

        if op == "bridge_finalize":
            wid = p.get("withdraw_id", "")
            w = self.bridge.withdrawals.get(wid)
            if not w:
                return False, "withdraw not found"
            if w["status"] == "processed":
                return False, "already processed"
            return True, ""

        return False, f"unknown op: {op}"

    # ═══════════ ПРИМЕНЕНИЕ ═══════════

    def _apply_tx(self, tx, now):
        # У unstake НЕ списываем с баланса (деньги приходят ИЗ стейка)
        # У faucet amount=0, пропуск по условию amount > 0
        if tx.amount > 0 and tx.op != "unstake":
            self._bal[tx.from_addr] = self._bal.get(tx.from_addr, 0.0) - tx.amount
        if tx.op == "transfer" and tx.amount > 0:
            self._bal[tx.to_addr] = self._bal.get(tx.to_addr, 0.0) + tx.amount
        self._exec(tx, now)
        self._nonce[tx.from_addr] = tx.nonce + 1

    def _exec(self, tx, now):
        op, p = tx.op, tx.params

        if op == "transfer":
            return

        if op == "faucet":
            self._faucet.add(tx.from_addr)
            self._bal[tx.from_addr] = self._bal.get(tx.from_addr, 0.0) + FAUCET_AMOUNT
            return

        if op == "stake":
            # stake → в Elector
            self.elector.register(tx.from_addr, tx.amount, tx.pubkey)
            return

        if op == "unstake":
            # вычет из стейка, в очередь на разблокировку
            self.elector.unstake(tx.from_addr, tx.amount)
            return

        if op == "deploy_contract":
            import hashlib
            addr = "BLCX" + hashlib.sha256(
                (tx.from_addr + p["code"] + str(tx.nonce)).encode()
            ).hexdigest()[:32]
            self._contracts[addr] = {
                "code": p["code"], "data": {},
                "owner": tx.from_addr, "created_at": now,
            }
            return

        if op == "call_contract":
            c = self._contracts[p["address"]]
            try:
                src = bytes.fromhex(c["code"]).decode()
                bc = compile_tolk(src)
                fn = p.get("method") or list(bc.keys())[0]
                if fn not in bc:
                    return
                vm = TVM(gas=100_000)
                ret = vm.execute(bc[fn], p.get("args", []), c["data"])
                c["data"] = vm.storage
                c["last_call"] = {
                    "method": fn, "returned": ret,
                    "gas_used": vm.gas_used, "at": now,
                }
            except Exception as e:
                c["last_call"] = {"error": str(e), "at": now}
            return

        if op == "dns_register":
            n = p["name"].lower()
            self._dns[n] = {
                "owner": tx.from_addr,
                "wallet": p.get("wallet") or tx.from_addr,
                "expires": now + DOMAIN_TTL,
                "source": "direct", "registered_at": now,
            }
            return

        if op == "dns_renew":
            d = self._dns[p["name"].lower()]
            d["expires"] = max(d["expires"], now) + DOMAIN_TTL
            return

        if op == "dns_set_wallet":
            self._dns[p["name"].lower()]["wallet"] = p["wallet"]
            return

        if op == "auction_start":
            n = p["name"].lower()
            self._auctions[n] = {
                "name": n, "started_at": now,
                "end_time": now + AUCTION_DURATION,
                "highest_bid": tx.amount,
                "highest_bidder": tx.from_addr,
                "bids": [{"address": tx.from_addr,
                          "amount": tx.amount, "ts": now}],
                "settled": False, "winner": None,
            }
            return

        if op == "auction_bid":
            n = p["name"].lower()
            a = self._auctions[n]
            r = self._refunds.setdefault(n, {})
            r[a["highest_bidder"]] = r.get(a["highest_bidder"], 0) + a["highest_bid"]
            a["highest_bid"] = tx.amount
            a["highest_bidder"] = tx.from_addr
            a["bids"].append({"address": tx.from_addr, "amount": tx.amount, "ts": now})
            if a["end_time"] - now < EXTEND_WINDOW:
                a["end_time"] = now + EXTEND_WINDOW
            return

        if op == "auction_settle":
            n = p["name"].lower()
            a = self._auctions[n]
            a["settled"] = True
            a["settled_at"] = now
            a["winner"] = a["highest_bidder"]
            if a["winner"]:
                self._dns[n] = {
                    "owner": a["winner"], "wallet": a["winner"],
                    "expires": now + DOMAIN_TTL, "source": "auction",
                    "registered_at": now,
                }
            return

        if op == "auction_refund":
            n = p["name"].lower()
            r = self._refunds.setdefault(n, {})
            amt = r.pop(tx.from_addr, 0.0)
            self._bal[tx.from_addr] = self._bal.get(tx.from_addr, 0.0) + amt
            return

        # ── системные op ──
        if op == "config_propose":
            self.config.propose_change(p["key"], p["value"], tx.from_addr)
            return

        if op == "bridge_deposit":
            r = self.bridge.deposit(
                tx.from_addr, tx.amount,
                p["chain"], p.get("target", ""), tx.nonce,
            )
            if not r["ok"]:
                raise ValueError(r["err"])
            return

        if op == "bridge_withdraw":
            r = self.bridge.submit_withdraw(
                p["chain"], p["source_tx"],
                p["recipient"], float(p["amount"]), tx.from_addr,
            )
            if not r["ok"]:
                raise ValueError(r["err"])
            return

        if op == "bridge_finalize":
            r = self.bridge.finalize_withdraw(p["withdraw_id"])
            if not r["ok"]:
                raise ValueError(r["err"])
            return

    # ═══════════ ВАЛИДАЦИЯ БЛОКА ═══════════

    def _force_apply(self, block):
        """Только для load() — replay без перепроверки."""
        now = block.timestamp
        self.elector.process_pending_unstake(now)
        self.elector.elect(now)
        self.minter.mint_block_reward(block.producer)
        for tx in block.txs:
            self._apply_tx(tx, now)
        self.blocks.append(block)

    def validate_and_apply(self, block):
        with self.lock:
            head = self.blocks[-1]
            if block.height != head.height + 1:
                return False, "bad height"
            if block.prev_hash != head.hash:
                return False, "bad prev"
            expected = self.expected_producer(block.height)
            if block.producer != expected["address"]:
                return False, "wrong producer"
            if not block.verify_signature():
                return False, "bad sig"

            # снапшот для откатов
            saved = {
                "bal":           dict(self._bal),
                "nonce":         dict(self._nonce),
                "contracts":     {k: dict(v) for k, v in self._contracts.items()},
                "dns":           dict(self._dns),
                "auctions":      {k: dict(v) for k, v in self._auctions.items()},
                "refunds":       {k: dict(v) for k, v in self._refunds.items()},
                "faucet":        set(self._faucet),
                "elector":       self.elector.to_dict(),
                "config":        self.config.to_dict(),
                "minter":        self.minter.to_dict(),
                "bridge":        self.bridge.to_dict(),
            }

            try:
                now = block.timestamp

                # 1) Системные действия перед транзакциями
                self.elector.process_pending_unstake(now)
                self.elector.elect(now)
                self.minter.mint_block_reward(block.producer)

                # 2) Транзакции блока
                for tx in block.txs:
                    ok, err = tx.validate()
                    if not ok:
                        raise ValueError(f"tx invalid: {err}")
                    # для unstake не проверяем баланс — деньги в стейке
                    if tx.op != "unstake" and tx.op != "faucet":
                        if self._bal.get(tx.from_addr, 0.0) < tx.amount + tx.fee:
                            raise ValueError(f"insufficient: {tx.from_addr[:10]}")
                    if self._nonce.get(tx.from_addr, 0) > tx.nonce:
                        raise ValueError("nonce too low")
                    ok, err = self._precheck(tx, now)
                    if not ok:
                        raise ValueError(f"op: {err}")
                    self._apply_tx(tx, now)
            except Exception as e:
                # откат
                self._bal = saved["bal"]
                self._nonce = saved["nonce"]
                self._contracts = saved["contracts"]
                self._dns = saved["dns"]
                self._auctions = saved["auctions"]
                self._refunds = saved["refunds"]
                self._faucet = saved["faucet"]
                self.elector = ElectorContract.from_dict(self, saved["elector"])
                self.config = ConfigContract.from_dict(self, saved["config"])
                self.minter = Minter.from_dict(self, saved["minter"])
                self.bridge = BridgeContract.from_dict(self, saved["bridge"])
                return False, str(e)

            self.blocks.append(block)
            for t in block.txs:
                self.mempool.pop(t.tx_hash(), None)
            return True, ""

    # ═══════════ МЕМПУЛ ═══════════

    def submit(self, tx):
        ok, err = tx.validate()
        if not ok:
            return False, err
        with self.lock:
            if self._nonces_safe(tx):
                return False, "nonce too low"
            if tx.tx_hash() in self.mempool:
                return False, "dup"
            spent = sum(t.amount + t.fee for t in self.mempool.values()
                        if t.from_addr == tx.from_addr)
            bal = self._bal.get(tx.from_addr, 0.0)
            if tx.op not in ("unstake", "faucet"):
                if bal - spent < tx.amount + tx.fee:
                    return False, "insufficient"
            ok, err = self._precheck(tx, self.blocks[-1].timestamp)
            if not ok:
                return False, err
            self.mempool[tx.tx_hash()] = tx
            return True, ""

    def _nonces_safe(self, tx):
        return self._nonce.get(tx.from_addr, 0) > tx.nonce

    def take(self, n=200):
        with self.lock:
            ts = self.blocks[-1].timestamp
            good = [t for t in self.mempool.values()
                    if self._precheck(t, ts)[0]]
            good.sort(key=lambda t: (t.timestamp, t.nonce))
            return good[:n]

    # ═══════════ СЕРИАЛИЗАЦИЯ ═══════════

    def to_dict(self):
        with self.lock:
            return {
                "validators": self.validators,
                "blocks": [b.to_dict() for b in self.blocks],
                "height": self.blocks[-1].height,
                "system": {
                    "config":   self.config.to_dict(),
                    "elector":  self.elector.to_dict(),
                    "minter":   self.minter.to_dict(),
                    "dns_root": self.dns_root.to_dict(),
                    "bridge":   self.bridge.to_dict(),
                },
            }

    def save(self, path):
        d = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".b_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise

    @staticmethod
    def load(path, genesis):
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return Blockchain(genesis)
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        bc = Blockchain(d.get("validators") or genesis)

        # Восстановить блоки с системными действиями
        for bd in d["blocks"][1:]:
            bc._force_apply(Block.from_dict(bd))

        # Восстановить system state (может быть впереди блоков из-за pending elections)
        sys = d.get("system", {})
        if sys:
            bc.config = ConfigContract.from_dict(bc, sys.get("config"))
            bc.elector = ElectorContract.from_dict(bc, sys.get("elector"))
            bc.minter = Minter.from_dict(bc, sys.get("minter"))
            bc.dns_root = DNSRoot.from_dict(bc, sys.get("dns_root"))
            bc.bridge = BridgeContract.from_dict(bc, sys.get("bridge"))
        return bc