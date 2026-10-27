import json, time
from .crypto import pub_from_hex, pub_to_hex, sha256, verify
from .transaction import Transaction


class Block:
    __slots__ = ("height","prev_hash","timestamp","txs","producer",
                 "producer_pubkey","signature","_hash")

    def __init__(self, height, prev_hash, txs, producer="",
                 producer_pubkey="", signature="", timestamp=None):
        self.height = int(height)
        self.prev_hash = prev_hash
        self.txs = list(txs)
        self.producer = producer
        self.producer_pubkey = producer_pubkey
        self.signature = signature
        self.timestamp = float(timestamp) if timestamp is not None else time.time()
        self._hash = None

    def _header(self):
        return {"height": self.height, "prev_hash": self.prev_hash,
                "timestamp": int(self.timestamp), "producer": self.producer,
                "txs_hashes": [tx.tx_hash() for tx in self.txs]}

    def _payload(self):
        return json.dumps(self._header(), sort_keys=True,
                          separators=(",",":"), ensure_ascii=False).encode()

    def compute_hash(self): return sha256(self._payload()).hex()

    @property
    def hash(self):
        if self._hash is None: self._hash = self.compute_hash()
        return self._hash

    def sign(self, sk):
        self.producer_pubkey = pub_to_hex(sk.public_key())
        self.signature = sk.sign(self._payload()).hex()
        self._hash = None

    def verify_signature(self):
        if not self.producer_pubkey or not self.signature: return False
        try:
            return verify(pub_from_hex(self.producer_pubkey),
                          self._payload(), bytes.fromhex(self.signature))
        except: return False

    def to_dict(self):
        return {**self._header(),
                "producer_pubkey": self.producer_pubkey,
                "signature": self.signature,
                "hash": self.hash,
                "txs": [tx.to_dict() for tx in self.txs]}

    @staticmethod
    def from_dict(d):
        b = Block(d["height"], d["prev_hash"],
                  [Transaction.from_dict(t) for t in d.get("txs",[])],
                  d.get("producer",""), d.get("producer_pubkey",""),
                  d.get("signature",""), d.get("timestamp"))
        if "hash" in d: b._hash = d["hash"]
        return b