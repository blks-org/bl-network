"""Детерминированные адреса системных контрактов.

Все ноды вычисляют их одинаково — из имени + workchain(-1).
Не зависят от genesis.json, заложены в протокол на этапе genesis-блока.
"""
import base64
import hashlib

SYSTEM_WORKCHAIN = -1

# Роли в протоколе — аналог Param 0/1/4 в TON
SYSTEM_CONTRACTS = {
    "treasury": "BL_SYSTEM::treasury",   # ← аналог System (-1:000…0)
    "minter":   "BL_SYSTEM::minter",
    "config":   "BL_SYSTEM::config",     # ← аналог Param 0
    "elector":  "BL_SYSTEM::elector",    # ← аналог Param 1
    "dns_root": "BL_SYSTEM::dns_root",   # ← аналог Param 4
    "bridge":   "BL_SYSTEM::bridge",
}


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def system_address(name: str) -> str:
    seed = SYSTEM_CONTRACTS.get(name, name)
    account_id = hashlib.sha256(seed.encode()).digest()
    raw = bytes([SYSTEM_WORKCHAIN & 0xFF]) + account_id
    return "BLC" + _b64e(raw + hashlib.sha256(raw)[:4])


def all_system_addresses() -> dict:
    return {role: system_address(role) for role in SYSTEM_CONTRACTS}