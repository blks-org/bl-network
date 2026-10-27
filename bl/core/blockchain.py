import json, os, re, tempfile, threading
from ..config import (BLOCK_REWARD, MIN_STAKE, UNSTAKE_DELAY, FAUCET_AMOUNT,
                     FAUCET_ONCE, DNS_PRICE, DOMAIN_TTL, AUCTION_DURATION,
                     EXTEND_WINDOW, BID_STEP, min_bid_for, NAME_RE)
from .block import Block
from .transaction import Transaction
from ..vm.compiler import compile_tolk
from ..vm.tvm import TVM

GENESIS_TS = 1_700_000_000


class ChainError(Exception): pass


class Blockchain:
    def __init__(self, genesis_validators):
        if not genesis_validators: raise ChainError("no genesis")
        self.lock = threading.RLock()
        self.validators = list(genesis_validators)
        self.blocks = []
        self.mempool = {}

        self._bal = {}
        self._nonce = {}
        self._stakes = {}
        self._unstake = []
        self._contracts = {}
        self._dns = {}
        self._auctions = {}
        self._refunds = {}
        self._faucet = set()

        v0 = genesis_validators[0]
        g = Block(0, "0"*64, [], v0["address"], v0["pubkey"], "", GENESIS_TS)
        g._hash = g.compute_hash()
        self.blocks.append(g)

    # ─── геттеры ───
    def head(self):
        with self.lock: return self.blocks[-1]

    def height(self):
        with self.lock: return self.blocks[-1].height

    def get_balance(self, a):
        with self.lock: return self._bal.get(a, 0.0)

    def get_nonce(self, a):
        with self.lock: return self._nonce.get(a, 0)

    def get_stake(self, a):
        with self.lock: return self._stakes.get(a, {}).get("amount", 0.0)

    def active_validators(self):
        vs = [{"address": a, "stake": s["amount"]}
              for a, s in self._stakes.items() if s["amount"] >= MIN_STAKE]
        vs.sort(key=lambda v: v["address"])
        # если никто не застейкал — genesis
        return vs or [{"address": v["address"], "stake": 0.0}
                      for v in self.validators]

    def expected_producer(self, height):
        vs = self.active_validators()
        return vs[height % len(vs)]

    def dns_resolve(self, n):
        with self.lock: return self._dns.get(n.lower())

    def dns_domains(self):
        with self.lock:
            out = [{"name":k, **v} for k,v in self._dns.items()]
            out.sort(key=lambda x: x.get("registered_at",0), reverse=True)
            return out

    def auction_get(self, n):
        with self.lock: return self._auctions.get(n.lower())

    def auctions_list(self):
        with self.lock:
            a, s = [], []
            for x in self._auctions.values():
                (s if x.get("settled") else a).append(x)
            return a, s

    def refund_of(self, n, a):
        with self.lock: return self._refunds.get(n.lower(), {}).get(a, 0.0)

    # ─── precheck ───
    def _precheck(self, tx, now):
        op, p = tx.op, tx.params

        if op == "transfer": return True, ""
        if op == "faucet":
            if FAUCET_ONCE and tx.from_addr in self._faucet: return False, "faucet used"
            if abs(tx.amount) > 1e-9: return False, "faucet amount=0"
            return True, ""
        if op == "stake":
            if tx.amount < MIN_STAKE: return False, f"min stake {MIN_STAKE}"
            return True, ""
        if op == "unstake":
            s = self._stakes.get(tx.from_addr)
            if not s: return False, "not staked"
            if s["amount"] < tx.amount: return False, "exceeds stake"
            return True, ""
        if op == "deploy_contract":
            h = p.get("code","")
            if not h: return False, "no code"
            try: bytes.fromhex(h)
            except: return False, "bad hex"
            return True, ""
        if op == "call_contract":
            if p.get("address") not in self._contracts: return False, "no contract"
            return True, ""
        if op == "dns_register":
            n = str(p.get("name","")).lower()
            if not re.match(NAME_RE, n): return False, "bad name"
            if n in self._dns and self._dns[n]["expires"] > now: return False, "taken"
            if n in self._auctions and not self._auctions[n].get("settled"): return False, "auction"
            if abs(tx.amount - DNS_PRICE) > 1e-9: return False, f"price={DNS_PRICE}"
            return True, ""
        if op == "dns_renew":
            n = str(p.get("name","")).lower()
            d = self._dns.get(n)
            if not d or d["owner"] != tx.from_addr: return False, "not owner"
            if abs(tx.amount - DNS_PRICE) > 1e-9: return False, "price"
            return True, ""
        if op == "auction_start":
            n = str(p.get("name","")).lower()
            if not re.match(NAME_RE, n): return False, "bad name"
            if n in self._dns and self._dns[n]["expires"] > now: return False, "taken"
            if n in self._auctions and not self._auctions[n].get("settled"): return False, "running"
            if tx.amount < min_bid_for(n): return False, f"min={min_bid_for(n)}"
            return True, ""
        if op == "auction_bid":
            n = str(p.get("name","")).lower()
            a = self._auctions.get(n)
            if not a or a["settled"]: return False, "no auction"
            if now > a["end_time"]: return False, "ended"
            if tx.amount < a["highest_bid"] * BID_STEP: return False, "too low"
            return True, ""
        if op == "auction_settle":
            n = str(p.get("name","")).lower()
            a = self._auctions.get(n)
            if not a or a["settled"]: return False, "no auction"
            if now < a["end_time"]: return False, "not ended"
            return True, ""
        if op == "auction_refund":
            n = str(p.get("name","")).lower()
            if self._refunds.get(n, {}).get(tx.from_addr, 0) <= 0: return False, "no refund"
            return True, ""
        return False, f"unknown op:{op}"

    # ─── применение ───
    def _apply(self, tx, now):
        if tx.amount > 0:
            self._bal[tx.from_addr] = self._bal.get(tx.from_addr, 0.0) - tx.amount
        if tx.op == "transfer" and tx.amount > 0:
            self._bal[tx.to_addr] = self._bal.get(tx.to_addr, 0.0) + tx.amount
        self._exec(tx, now)
        self._nonce[tx.from_addr] = tx.nonce + 1

    def _exec(self, tx, now):
        op, p = tx.op, tx.params
        if op == "transfer": return

        if op == "faucet":
            self._faucet.add(tx.from_addr)
            self._bal[tx.from_addr] = self._bal.get(tx.from_addr, 0.0) + FAUCET_AMOUNT
            return
        if op == "stake":
            s = self._stakes.setdefault(tx.from_addr, {"amount": 0.0, "since": now})
            s["amount"] += tx.amount
            return
        if op == "unstake":
            s = self._stakes[tx.from_addr]
            s["amount"] -= tx.amount
            self._unstake.append({"addr": tx.from_addr, "amount": tx.amount,
                                  "unlock_at": now + UNSTAKE_DELAY})
            return
        if op == "deploy_contract":
            import hashlib
            addr = "BLCX" + hashlib.sha256(
                (tx.from_addr + p["code"] + str(tx.nonce)).encode()).hexdigest()[:32]
            self._contracts[addr] = {"code": p["code"], "data": {},
                                     "owner": tx.from_addr, "created_at": now}
            return
        if op == "call_contract":
            c = self._contracts[p["address"]]
            try:
                src = bytes.fromhex(c["code"]).decode()
                bc = compile_tolk(src)
                fn = p.get("method") or list(bc.keys())[0]
                if fn not in bc: return
                vm = TVM(gas=100_000)
                ret = vm.execute(bc[fn], p.get("args",[]), c["data"])
                c["data"] = vm.storage
                c["last_call"] = {"method": fn, "returned": ret,
                                 "gas_used": vm.gas_used, "at": now}
            except Exception as e:
                c["last_call"] = {"error": str(e), "at": now}
            return
        if op == "dns_register":
            n = p["name"].lower()
            self._dns[n] = {"owner": tx.from_addr,
                           "wallet": p.get("wallet") or tx.from_addr,
                           "expires": now + DOMAIN_TTL,
                           "source": "direct", "registered_at": now}
            return
        if op == "dns_renew":
            d = self._dns[p["name"].lower()]
            d["expires"] = max(d["expires"], now) + DOMAIN_TTL
            return
        if op == "auction_start":
            n = p["name"].lower()
            self._auctions[n] = {"name": n, "started_at": now,
                                 "end_time": now + AUCTION_DURATION,
                                 "highest_bid": tx.amount,
                                 "highest_bidder": tx.from_addr,
                                 "bids": [{"address": tx.from_addr,
                                          "amount": tx.amount, "ts": now}],
                                 "settled": False, "winner": None}
            return
        if op == "auction_bid":
            n = p["name"].lower(); a = self._auctions[n]
            r = self._refunds.setdefault(n, {})
            r[a["highest_bidder"]] = r.get(a["highest_bidder"], 0) + a["highest_bid"]
            a["highest_bid"] = tx.amount
            a["highest_bidder"] = tx.from_addr
            a["bids"].append({"address": tx.from_addr, "amount": tx.amount, "ts": now})
            if a["end_time"] - now < EXTEND_WINDOW:
                a["end_time"] = now + EXTEND_WINDOW
            return
        if op == "auction_settle":
            n = p["name"].lower(); a = self._auctions[n]
            a["settled"] = True; a["settled_at"] = now
            a["winner"] = a["highest_bidder"]
            if a["winner"]:
                self._dns[n] = {"owner": a["winner"], "wallet": a["winner"],
                               "expires": now + DOMAIN_TTL, "source": "auction",
                               "registered_at": now}
            return
        if op == "auction_refund":
            n = p["name"].lower()
            r = self._refunds.setdefault(n, {})
            amt = r.pop(tx.from_addr, 0.0)
            self._bal[tx.from_addr] = self._bal.get(tx.from_addr, 0.0) + amt
            return

    def _force_apply(self, block):
        """Только при загрузке своей цепи — без проверки."""
        now = block.timestamp
        self._bal[block.producer] = self._bal.get(block.producer, 0.0) + BLOCK_REWARD
        for u in list(self._unstake):
            if now >= u["unlock_at"]:
                self._bal[u["addr"]] = self._bal.get(u["addr"], 0.0) + u["amount"]
                self._unstake.remove(u)
        for tx in block.txs:
            self._apply(tx, now)
        self.blocks.append(block)

    def validate_and_apply(self, block):
        with self.lock:
            head = self.blocks[-1]
            if block.height != head.height + 1: return False, "bad height"
            if block.prev_hash != head.hash: return False, "bad prev"
            expected = self.expected_producer(block.height)
            if block.producer != expected["address"]: return False, "wrong producer"
            if not block.verify_signature(): return False, "bad sig"

            saved = (dict(self._bal), dict(self._nonce),
                     {k: dict(v) for k,v in self._stakes.items()},
                     list(self._unstake),
                     {k: dict(v) for k,v in self._contracts.items()},
                     dict(self._dns),
                     {k: dict(v) for k,v in self._auctions.items()},
                     {k: dict(v) for k,v in self._refunds.items()},
                     set(self._faucet))
            try:
                now = block.timestamp
                self._bal[block.producer] = (
                    self._bal.get(block.producer, 0.0) + BLOCK_REWARD
                )
                for u in list(self._unstake):
                    if now >= u["unlock_at"]:
                        self._bal[u["addr"]] = (
                            self._bal.get(u["addr"], 0.0) + u["amount"]
                        )
                        self._unstake.remove(u)
                for tx in block.txs:
                    ok, err = tx.validate()
                    if not ok: raise ValueError(f"tx: {err}")
                    if self._bal.get(tx.from_addr, 0.0) < tx.amount + tx.fee:
                        raise ValueError("insufficient")
                    if self._nonce.get(tx.from_addr, 0) > tx.nonce:
                        raise ValueError("nonce low")
                    ok, err = self._precheck(tx, now)
                    if not ok: raise ValueError(f"op: {err}")
                    self._apply(tx, now)
            except Exception as e:
                (self._bal, self._nonce, self._stakes, self._unstake,
                 self._contracts, self._dns, self._auctions, self._refunds,
                 self._faucet) = saved
                return False, str(e)
            self.blocks.append(block)
            for t in block.txs: self.mempool.pop(t.tx_hash(), None)
            return True, ""

    def submit(self, tx):
        ok, err = tx.validate()
        if not ok: return False, err
        with self.lock:
            if self._nonce.get(tx.from_addr, 0) > tx.nonce: return False, "nonce low"
            if tx.tx_hash() in self.mempool: return False, "dup"
            spent = sum(t.amount + t.fee for t in self.mempool.values()
                        if t.from_addr == tx.from_addr)
            if self._bal.get(tx.from_addr, 0.0) - spent < tx.amount + tx.fee:
                return False, "insufficient"
            ok, err = self._precheck(tx, self.blocks[-1].timestamp)
            if not ok: return False, err
            self.mempool[tx.tx_hash()] = tx
            return True, ""

    def take(self, n=200):
        with self.lock:
            ts = self.blocks[-1].timestamp
            good = [t for t in self.mempool.values() if self._precheck(t, ts)[0]]
            good.sort(key=lambda t: (t.timestamp, t.nonce))
            return good[:n]

    def to_dict(self):
        with self.lock:
            return {"validators": self.validators,
                    "blocks": [b.to_dict() for b in self.blocks],
                    "height": self.blocks[-1].height}

    def save(self, path):
        d = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".b_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            try: os.unlink(tmp)
            except: pass
            raise

    @staticmethod
    def load(path, genesis):
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return Blockchain(genesis)
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        bc = Blockchain(d.get("validators") or genesis)
        for bd in d["blocks"][1:]:
            bc._force_apply(Block.from_dict(bd))
        return bc