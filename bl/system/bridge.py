"""Bridge Contract — мосты в ETH / TON / BTC.

Deposit: пользователь блокирует BLC → оракулы (2/3 валидаторов) подписывают
         событие для разблокировки в целевой сети.
Withdraw: оракулы фиксируют событие в чужой сети → контракт зачисляет BLC.
"""
import hashlib
import time


SUPPORTED_CHAINS = ["ethereum", "ton", "bitcoin"]


class BridgeContract:
    def __init__(self, bc):
        self.bc = bc
        self.deposits = {}
        self.withdrawals = {}
        self.oracles = set()
        self.locked_balance = 0.0

    def set_oracles(self, validators):
        self.oracles = set(validators)

    def deposit(self, sender, amount, target_chain, target_addr, nonce):
        if target_chain not in SUPPORTED_CHAINS:
            return {"ok": False, "err": f"unsupported chain: {target_chain}"}
        bal = self.bc._bal.get(sender, 0.0)
        if bal < amount:
            return {"ok": False, "err": "insufficient balance"}
        self.bc._bal[sender] = bal - amount
        self.locked_balance += amount
        dep_id = hashlib.sha256(
            f"{sender}:{amount}:{target_chain}:{target_addr}:{nonce}:{time.time()}".encode()
        ).hexdigest()[:16]
        self.deposits[dep_id] = {
            "from": sender, "amount": amount,
            "chain": target_chain, "target": target_addr,
            "ts": time.time(), "status": "pending",
        }
        return {"ok": True, "deposit_id": dep_id}

    def submit_withdraw(self, source_chain, source_tx, recipient, amount, signer):
        if signer not in self.oracles:
            return {"ok": False, "err": "not an oracle"}
        w_id = hashlib.sha256(
            f"{source_chain}:{source_tx}:{recipient}:{amount}".encode()
        ).hexdigest()[:16]
        w = self.withdrawals.setdefault(w_id, {
            "chain": source_chain, "source_tx": source_tx,
            "recipient": recipient, "amount": amount,
            "signatures": {}, "ts": time.time(),
            "status": "pending", "processed_at": None,
        })
        w["signatures"][signer] = time.time()
        return {"ok": True, "withdraw_id": w_id,
                "signatures": len(w["signatures"])}

    def finalize_withdraw(self, w_id):
        w = self.withdrawals.get(w_id)
        if not w:
            return {"ok": False, "err": "not found"}
        if w["status"] == "processed":
            return {"ok": False, "err": "already processed"}
        need = max(1, (len(self.oracles) * 2 + 2) // 3)
        if len(w["signatures"]) < need:
            return {"ok": False, "err": f"need {need} sigs, have {len(w['signatures'])}"}
        if w["amount"] > self.locked_balance:
            return {"ok": False, "err": "insufficient locked balance"}
        self.locked_balance -= w["amount"]
        self.bc._bal[w["recipient"]] = self.bc._bal.get(w["recipient"], 0.0) + w["amount"]
        w["status"] = "processed"
        w["processed_at"] = time.time()
        return {"ok": True, "amount": w["amount"], "recipient": w["recipient"]}

    def get_stats(self):
        return {
            "oracles": len(self.oracles),
            "locked_balance": self.locked_balance,
            "pending_deposits": sum(1 for d in self.deposits.values() if d["status"] == "pending"),
            "pending_withdrawals": sum(1 for w in self.withdrawals.values() if w["status"] == "pending"),
            "supported_chains": SUPPORTED_CHAINS,
        }

    def to_dict(self):
        return {"deposits": self.deposits, "withdrawals": self.withdrawals,
                "oracles": list(self.oracles), "locked_balance": self.locked_balance}

    @staticmethod
    def from_dict(bc, d):
        b = BridgeContract(bc)
        if d:
            b.deposits = d.get("deposits", {})
            b.withdrawals = d.get("withdrawals", {})
            b.oracles = set(d.get("oracles", []))
            b.locked_balance = d.get("locked_balance", 0.0)
        return b