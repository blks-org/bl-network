import argparse, json, os, sys, urllib.request
from .config import DNS_PRICE, MIN_STAKE
from .core.crypto import (new_keypair, priv_from_hex, priv_to_hex,
                          pub_to_hex, pubkey_to_address)
from .core.transaction import Transaction


def _get(u):
    with urllib.request.urlopen(u, timeout=10) as r: return json.loads(r.read())


def _post(u, d):
    req = urllib.request.Request(u, data=json.dumps(d).encode(),
                                 headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def _wallet(path):
    with open(path) as f: return json.load(f)


def _send(args, op, params=None, amount=0.0, to="SYSTEM"):
    w = _wallet(args.file)
    info = _get(args.api + "/balance/" + w["address"])
    tx = Transaction(w["address"], to, amount, info["nonce"], None,
                     "", "", 0.0, op, params or {})
    tx.sign(priv_from_hex(w["priv"]))
    r = _post(args.api + "/send", tx.to_dict())
    if r.get("ok"): print(f"✓ {op} tx={r['tx_hash'][:16]}…")
    else: print(f"✗ {r.get('error')}"); sys.exit(1)


def cmd_create(a):
    sk, vk = new_keypair()
    with open(a.out,"w") as f:
        json.dump({"priv":priv_to_hex(sk),"pub":pub_to_hex(vk),
                   "address":pubkey_to_address(vk)}, f, indent=2)
    os.chmod(a.out, 0o600)
    print(f"created {a.out}: {pubkey_to_address(vk)}")


def cmd_info(a):
    w = _wallet(a.file)
    b = _get(a.api + "/balance/" + w["address"])
    print(f"addr:  {w['address']}")
    print(f"bal:   {b['balance']} BLC")
    print(f"stake: {b['stake']} BLC")
    print(f"nonce: {b['nonce']}")


def cmd_faucet(a): _send(a, "faucet")
def cmd_stake(a): _send(a, "stake", {}, float(a.amount))
def cmd_unstake(a): _send(a, "unstake", {}, float(a.amount))
def cmd_dns(a): _send(a, "dns_register", {"name":a.name.lower()}, DNS_PRICE)
def cmd_auc_start(a): _send(a, "auction_start", {"name":a.name.lower()}, float(a.bid))
def cmd_auc_bid(a): _send(a, "auction_bid", {"name":a.name.lower()}, float(a.bid))
def cmd_auc_settle(a): _send(a, "auction_settle", {"name":a.name.lower()})
def cmd_auc_refund(a): _send(a, "auction_refund", {"name":a.name.lower()})
def cmd_send(a): _send(a, "transfer", {}, float(a.amount), a.to)


def cmd_deploy(a):
    with open(a.code) as f: src = f.read()
    _send(a, "deploy_contract", {"code": src.encode().hex()})


def cmd_call(a):
    params = {"address":a.address,"method":a.method,
              "args":[int(x) if x.lstrip('-').isdigit() else x
                      for x in (a.args or "").split(",") if x]}
    _send(a, "call_contract", params)


def main():
    ap = argparse.ArgumentParser()
    s = ap.add_subparsers(dest="c", required=True)

    def sub(n, fn, *args):
        p = s.add_parser(n)
        p.add_argument("--file", default="wallet.json")
        p.add_argument("--api", default="http://127.0.0.1:8001")
        for a, kw in args: p.add_argument(a, **kw)
        p.set_defaults(fn=fn)

    sub("create", cmd_create, ("--out", {"default":"wallet.json"}))
    sub("info", cmd_info)
    sub("faucet", cmd_faucet)
    sub("send", cmd_send, ("--to",{"required":True}), ("--amount",{"required":True}))
    sub("stake", cmd_stake, ("--amount",{"required":True}))
    sub("unstake", cmd_unstake, ("--amount",{"required":True}))
    sub("dns-register", cmd_dns, ("--name",{"required":True}))
    sub("auction-start", cmd_auc_start, ("--name",{"required":True}),("--bid",{"required":True}))
    sub("auction-bid", cmd_auc_bid, ("--name",{"required":True}),("--bid",{"required":True}))
    sub("auction-settle", cmd_auc_settle, ("--name",{"required":True}))
    sub("auction-refund", cmd_auc_refund, ("--name",{"required":True}))
    sub("deploy", cmd_deploy, ("--code",{"required":True}))
    sub("call", cmd_call, ("--address",{"required":True}),
        ("--method",{"required":True}), ("--args",{"default":""}))

    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__": main()