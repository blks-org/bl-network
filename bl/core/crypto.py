import base64, hashlib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

_R = serialization.Encoding.Raw
_PR = serialization.PrivateFormat.Raw
_PU = serialization.PublicFormat.Raw


def new_keypair():
    sk = Ed25519PrivateKey.generate()
    return sk, sk.public_key()

def priv_to_hex(sk): return sk.private_bytes(_R, _PR, serialization.NoEncryption()).hex()
def priv_from_hex(h): return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(h))
def pub_to_hex(vk): return vk.public_bytes(_R, _PU).hex()
def pub_from_hex(h): return Ed25519PublicKey.from_public_bytes(bytes.fromhex(h))
def pub_bytes(vk): return vk.public_bytes(_R, _PU)
def sha256(b): return hashlib.sha256(b).digest()

def _b64e(b): return base64.urlsafe_b64encode(b).decode().rstrip("=")
def _b64d(s): return base64.urlsafe_b64decode(s + "=" * ((4 - len(s) % 4) % 4))

def pubkey_to_address(vk, wc=0):
    acc = sha256(pub_bytes(vk))
    raw = bytes([wc & 0xFF]) + acc
    return "BLC" + _b64e(raw + sha256(raw)[:4])

def parse_address(addr):
    if not addr or not addr.startswith("BLC"): raise ValueError("not BLC")
    p = _b64d(addr[3:])
    if len(p) != 37: raise ValueError("bad len")
    if sha256(p[:33])[:4] != p[33:]: raise ValueError("bad crc")
    wc = p[0] if p[0] < 128 else p[0] - 256
    return wc, p[1:33]

def is_valid_address(a):
    try: parse_address(a); return True
    except: return False

def verify(vk, msg, sig):
    try: vk.verify(sig, msg); return True
    except: return False