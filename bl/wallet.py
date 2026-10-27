"""CLI-кошелёк с поддержкой системных контрактов."""
import argparse
import json
import os
import sys
import urllib.request

from .config import DNS_PRICE, MIN_STAKE
from .core.crypto import (
    new_keypair, priv_from_hex, priv_to_hex,
    pub_to_hex, pubkey_to_address,
)
from .core.transaction import Transaction


# ═══════════ HTTP ═══════════

def _get(u):
    with urllib.request.urlopen(u, timeout=10) as r:
        return json.loads(r.read())


def _post(u, d):
    req = urllib.request.Request(
        u, data=json.dumps(d).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def _wallet(path):
    with open(path) as f:
        return json.load(f)


def _send(args, op, params=None, amount=0.0, to="SYSTEM"):
    w = _wallet(args.file)
    info = _get(args.api + "/balance/" + w["address"])
    tx = Transaction(
        w["address"], to, amount, info["nonce"], None,
        "", "", 0.0, op, params or {},
    )
    tx.sign(priv_from_hex(w["priv"]))
    r = _post(args.api + "/send", tx.to_dict())
    if r.get("ok"):
        print(f"✓ {op} tx={r['tx_hash'][:16]}…")
        return r
    print(f"✗ {r.get('error')}")
    sys.exit(1)


def _parse_value(v):
    try:
        return float(v)
    except ValueError:
        return v


# ═══════════ БАЗОВЫЕ ═══════════

def cmd_create(a):
    sk, vk = new_keypair()
    with open(a.out, "w") as f:
        json.dump({
            "priv": priv_to_hex(sk),
            "pub": pub_to_hex(vk),
            "address": pubkey_to_address(vk),
        }, f, indent=2)
    os.chmod(a.out, 0o600)
    print(f"created {a.out}: {pubkey_to_address(vk)}")


def cmd_info(a):
    w = _wallet(a.file)
    b = _get(a.api + "/balance/" + w["address"])
    print(f"addr:  {w['address']}")
    print(f"bal:   {b['balance']} BLC")
    print(f"stake: {b['stake']} BLC")
    print(f"nonce: {b['nonce']}")


def cmd_faucet(a):
    _send(a, "faucet")


def cmd_send(a):
    _send(a, "transfer", {}, float(a.amount), a.to)


# ═══════════ СТЕЙК ═══════════

def cmd_stake(a):
    _send(a, "stake", {}, float(a.amount))


def cmd_unstake(a):
    _send(a, "unstake", {}, float(a.amount))


# ═══════════ DNS ═══════════

def cmd_dns_register(a):
    _send(a, "dns_register",
          {"name": a.name.lower(), "wallet": a.wallet or ""},
          amount=DNS_PRICE)


def cmd_dns_renew(a):
    _send(a, "dns_renew", {"name": a.name.lower()}, amount=DNS_PRICE)


def cmd_dns_set_wallet(a):
    _send(a, "dns_set_wallet",
          {"name": a.name.lower(), "wallet": a.wallet})


def cmd_auction_start(a):
    _send(a, "auction_start", {"name": a.name.lower()}, amount=float(a.bid))


def cmd_auction_bid(a):
    _send(a, "auction_bid", {"name": a.name.lower()}, amount=float(a.bid))


def cmd_auction_settle(a):
    _send(a, "auction_settle", {"name": a.name.lower()})


def cmd_auction_refund(a):
    _send(a, "auction_refund", {"name": a.name.lower()})


# ═══════════ КОНТРАКТЫ ═══════════

def cmd_deploy(a):
    with open(a.code) as f:
        src = f.read()
    _send(a, "deploy_contract", {"code": src.encode().hex()})


def cmd_call(a):
    params = {
        "address": a.address,
        "method": a.method,
        "args": [int(x) if x.lstrip('-').isdigit() else x
                 for x in (a.args or "").split(",") if x],
    }
    _send(a, "call_contract", params)


# ═══════════ СИСТЕМНЫЕ ═══════════

def cmd_system(a):
    s = _get(a.api + "/system")
    print(json.dumps(s, indent=2, ensure_ascii=False))


def cmd_config_get(a):
    c = _get(a.api + "/config")
    if a.key:
        print(f"{a.key} = {c['params'].get(a.key)}")
    else:
        print(json.dumps(c["params"], indent=2, ensure_ascii=False))


def cmd_config_propose(a):
    _send(a, "config_propose",
          {"key": a.key, "value": _parse_value(a.value)})


def cmd_elector(a):
    e = _get(a.api + "/elector")
    print(f"epoch: {e['epoch']}")
    print(f"validators: {len(e['validators'])}")
    for v in e["validators"]:
        print(f"  {v['address'][:20]}…  stake={v['stake']}")
    print(f"candidates: {len(e['candidates'])}")
    for c in e["candidates"]:
        print(f"  {c[:20]}…")
    if e.get("pending_unstake"):
        print(f"pending unstake: {len(e['pending_unstake'])}")
        for u in e["pending_unstake"]:
            print(f"  {u['addr'][:20]}…  {u['amount']} BLC")


def cmd_treasury(a):
    t = _get(a.api + "/treasury")
    print(f"treasury: {t['address']}")
    print(f"balance:  {t['balance']} BLC")


# ═══════════ МОСТ ═══════════

def cmd_bridge(a):
    b = _get(a.api + "/bridge")
    print(json.dumps(b, indent=2, ensure_ascii=False))


def cmd_bridge_deposit(a):
    _send(a, "bridge_deposit",
          {"chain": a.chain, "target": a.target},
          amount=float(a.amount))


def cmd_bridge_withdraw(a):
    _send(a, "bridge_withdraw", {
        "chain": a.chain,
        "source_tx": a.source_tx,
        "recipient": a.recipient,
        "amount": float(a.amount),
    })


def cmd_bridge_finalize(a):
    _send(a, "bridge_finalize", {"withdraw_id": a.withdraw_id})


# ═══════════ MAIN ═══════════

def main():
    ap = argparse.ArgumentParser(description="BLC wallet CLI")
    s = ap.add_subparsers(dest="c", required=True)

    def sub(n, fn, *args):
        p = s.add_parser(n)
        p.add_argument("--file", default="wallet.json")
        p.add_argument("--api", default="http://127.0.0.1:8001")
        for a, kw in args:
            p.add_argument(a, **kw)
        p.set_defaults(fn=fn)

    # базовые
    sub("create", cmd_create, ("--out", {"default": "wallet.json"}))
    sub("info", cmd_info)
    sub("faucet", cmd_faucet)
    sub("send", cmd_send,
        ("--to", {"required": True}),
        ("--amount", {"required": True}))

    # стейк
    sub("stake", cmd_stake, ("--amount", {"required": True}))
    sub("unstake", cmd_unstake, ("--amount", {"required": True}))

    # DNS
    sub("dns-register", cmd_dns_register,
        ("--name", {"required": True}),
        ("--wallet", {"default": ""}))
    sub("dns-renew", cmd_dns_renew, ("--name", {"required": True}))
    sub("dns-set-wallet", cmd_dns_set_wallet,
        ("--name", {"required": True}),
        ("--wallet", {"required": True}))
    sub("auction-start", cmd_auction_start,
        ("--name", {"required": True}),
        ("--bid", {"required": True}))
    sub("auction-bid", cmd_auction_bid,
        ("--name", {"required": True}),
        ("--bid", {"required": True}))
    sub("auction-settle", cmd_auction_settle,
        ("--name", {"required": True}))
    sub("auction-refund", cmd_auction_refund,
        ("--name", {"required": True}))

    # контракты
    sub("deploy", cmd_deploy, ("--code", {"required": True}))
    sub("call", cmd_call,
        ("--address", {"required": True}),
        ("--method", {"required": True}),
        ("--args", {"default": ""}))

    # системные
    sub("system", cmd_system)
    sub("config-get", cmd_config_get, ("--key", {"default": ""}))
    sub("config-propose", cmd_config_propose,
        ("--key", {"required": True}),
        ("--value", {"required": True}))
    sub("elector", cmd_elector)
    sub("treasury", cmd_treasury)

    # мост
    sub("bridge", cmd_bridge)
    sub("bridge-deposit", cmd_bridge_deposit,
        ("--chain", {"required": True}),
        ("--target", {"required": True}),
        ("--amount", {"required": True}))
    sub("bridge-withdraw", cmd_bridge_withdraw,
        ("--chain", {"required": True}),
        ("--source-tx", {"required": True}),
        ("--recipient", {"required": True}),
        ("--amount", {"required": True}))
    sub("bridge-finalize", cmd_bridge_finalize,
        ("--withdraw-id", {"required": True}))

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()