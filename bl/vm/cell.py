import hashlib

MAX_BITS = 127 * 8
MAX_REFS = 4


class Cell:
    __slots__ = ("_bits","_refs","_hash")
    def __init__(self, bits=b"", refs=None):
        if len(bits) > MAX_BITS: raise ValueError("bits")
        if refs and len(refs) > MAX_REFS: raise ValueError("refs")
        self._bits = bytes(bits)
        self._refs = tuple(refs or ())
        self._hash = None

    @property
    def bits(self): return self._bits
    @property
    def refs(self): return self._refs

    def hash(self):
        if self._hash is None:
            h = hashlib.sha256()
            h.update(len(self._bits).to_bytes(2, "big"))
            h.update(self._bits)
            for r in self._refs: h.update(r.hash())
            self._hash = h.digest()
        return self._hash

    def hash_hex(self): return self.hash().hex()

    def to_dict(self):
        return {"bits": self._bits.hex(),
                "refs": [r.hash_hex() for r in self._refs]}

    @staticmethod
    def from_dict(d):
        return Cell(bytes.fromhex(d.get("bits", "")), [])

    def __eq__(self, o): return isinstance(o, Cell) and self.hash() == o.hash()
    def __hash__(self): return hash(self.hash())