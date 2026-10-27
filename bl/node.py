import argparse, asyncio, json, os, time, traceback
from .api import start as start_api
from .core.block import Block
from .core.blockchain import Blockchain
from .core.crypto import (new_keypair, priv_from_hex, priv_to_hex,
                          pub_to_hex, pubkey_to_address)
from .config import BLOCK_INTERVAL, MAX_TXS_PER_BLOCK
from .network.p2p import P2P


def _key(path):
    if os.path.exists(path):
        with open(path) as f: c = f.read().strip()
        return priv_from_hex(json.loads(c)["priv"] if c.startswith("{") else c)
    sk,_ = new_keypair()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path,"w") as f:
        json.dump({"priv":priv_to_hex(sk),
                   "pub":pub_to_hex(sk.public_key()),
                   "address":pubkey_to_address(sk.public_key())}, f, indent=2)
    os.chmod(path, 0o600)
    return sk


class Node:
    def __init__(self, args):
        self.args = args
        os.makedirs(args.data_dir, exist_ok=True)
        kp = args.key or os.path.join(args.data_dir, "validator.json")
        gp = args.genesis or os.path.join(args.data_dir, "genesis.json")
        self.chain = os.path.join(args.data_dir, "chain.json")

        self.sk = _key(kp)
        self.pub = pub_to_hex(self.sk.public_key())
        self.addr = pubkey_to_address(self.sk.public_key())

        genesis = self._genesis(gp)
        self.bc = Blockchain.load(self.chain, genesis)
        self._log(f"chain h={self.bc.height()} val={len(self.bc.validators)}")
        self._log(f"addr: {self.addr}")

        peers = []
        for p in args.peers or []:
            if ":" in p:
                h, prt = p.rsplit(":",1); peers.append((h,int(prt)))

        self.p2p = P2P(self.bc, args.host, args.port, peers,
                       on_block=self._on_block, on_tx=self._on_tx, log=self._log)
        self._loop = None

    def _genesis(self, path):
        if os.path.exists(path):
            with open(path) as f: return json.load(f)["validators"]
        g = [{"address":self.addr,"pubkey":self.pub,"name":"v0"}]
        with open(path,"w") as f: json.dump({"validators":g},f,indent=2)
        return g

    def _log(self, m):
        print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

    def _on_block(self, b):
        try: self.bc.save(self.chain)
        except: pass
        self._log(f"← block #{b.height} ({len(b.txs)} tx)")

    def _on_tx(self, t):
        self._log(f"← tx {t.op} {t.from_addr[:10]}")

    def _mine(self):
        exp = self.bc.expected_producer(self.bc.height()+1)
        return exp["address"] == self.addr

    async def _producer(self):
        while True:
            await asyncio.sleep(1.0)
            try:
                if not self._mine(): continue
                txs = self.bc.take(MAX_TXS_PER_BLOCK)
                h = self.bc.head()
                b = Block(h.height+1, h.hash, txs, self.addr, self.pub)
                b.sign(self.sk)
                ok,err = self.bc.validate_and_apply(b)
                if not ok:
                    self._log(f"own block rejected: {err}"); continue
                self.bc.save(self.chain)
                await self.p2p.send_block(b)
                self._log(f"→ block #{b.height} ({len(txs)} tx)")
            except Exception:
                traceback.print_exc()
            await asyncio.sleep(BLOCK_INTERVAL)

    async def run(self):
        self._loop = asyncio.get_event_loop()
        await self.p2p.start()
        import threading
        threading.Thread(target=start_api,
                        args=(self.bc, self, self.args.api_host, self.args.api_port),
                        daemon=True).start()
        self._log(f"HTTP :{self.args.api_port}")
        try: await self._producer()
        finally:
            await self.p2p.stop(); self.bc.save(self.chain)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7001)
    ap.add_argument("--api-host", default="0.0.0.0")
    ap.add_argument("--api-port", type=int, default=8001)
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--key")
    ap.add_argument("--genesis")
    ap.add_argument("--peers", action="append", default=[])
    a = ap.parse_args()
    try: asyncio.run(Node(a).run())
    except KeyboardInterrupt: print("\nstopped")


if __name__ == "__main__":
    main()