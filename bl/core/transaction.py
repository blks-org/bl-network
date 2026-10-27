import json, time
from .crypto import (is_valid_address, pub_from_hex, pubkey_to_address,
                     sha256, verify, pub_to_hex)
from ..config import NANO


class Transaction:
    __slots__ = ("from_addr","to_addr","amount","nonce","timestamp",
                 "pubkey","signature","fee","op","params")

    def __init__(self, from_addr, to_addr, amount, nonce, timestamp=None,
                 pubkey="", signature="", fee=0.0, op="transfer", params=None):
        self.from_addr = from_addr
        self.to_addr = to_addr
        self.amount = float(amount)
        self.nonce = int(nonce)
        self.timestamp = float(timestamp) if timestamp is not None else time.time()
        self.pubkey = pubkey
        self.signature = signature
        self.fee = float(fee)
        self.op = op
        self.params = dict(params or {})

    def _payload_dict(self):
        return {"from": self.from_addr, "to": self.to_addr,
                "amount_nano": int(round(self.amount*NANO)),
                "nonce": self.nonce,
                "fee_nano": int(round(self.fee*NANO)),
                "ts": int(self.timestamp),
                "op": self.op, "params": self.params}

    def _payload(self):
        return json.dumps(self._payload_dict(), sort_keys=True,
                          separators=(",",":"), ensure_ascii=False).encode()

    def tx_hash(self):
        sig = bytes.fromhex(self.signature or "00")
        return sha256(self._payload() + sig).hex()

    def sign(self, sk):
        self.pubkey = pub_to_hex(sk.public_key())
        self.signature = sk.sign(self._payload()).hex()

    def verify_signature(self):
        if not self.pubkey or not self.signature: return False
        try:
            return verify(pub_from_hex(self.pubkey), self._payload(),
                          bytes.fromhex(self.signature))
        except: return False

    def validate(self):
        if not is_valid_address(self.from_addr): return False, "bad from"
        if self.op == "transfer":
            if not is_valid_address(self.to_addr): return False, "bad to"
            if self.amount <= 0: return False, "amount<=0"
        elif self.amount < 0: return False, "amount<0"
        if self.fee < 0: return False, "fee<0"
        if self.nonce < 0: return False, "nonce<0"
        if not self.verify_signature(): return False, "bad sig"
        try: actual = pubkey_to_address(pub_from_hex(self.pubkey))
        except: return False, "bad pubkey"
        if actual != self.from_addr: return False, "addr mismatch"
        return True, ""

    def to_dict(self):
        return {"from": self.from_addr, "to": self.to_addr,
                "amount": self.amount, "nonce": self.nonce,
                "timestamp": self.timestamp, "pubkey": self.pubkey,
                "signature": self.signature, "fee": self.fee,
                "op": self.op, "params": self.params}

    @staticmethod
    def from_dict(d):
        return Transaction(d["from"], d["to"], d["amount"], d["nonce"],
                          d.get("timestamp"), d.get("pubkey",""),
                          d.get("signature",""), d.get("fee",0.0),
                          d.get("op","transfer"), d.get("params") or {})