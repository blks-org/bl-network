import asyncio, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from .config import (min_bid_for, AUCTION_DURATION, DNS_PRICE,
                     MIN_BID_SHORT, MIN_BID_MEDIUM, MIN_BID_LONG)
from .core.crypto import is_valid_address
from .core.transaction import Transaction


class H(BaseHTTPRequestHandler):
    bc = None; node = None
    def log_message(self,*a): pass

    def _json(self, o, s=200):
        b = json.dumps(o, default=str, ensure_ascii=False).encode()
        self.send_response(s)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(b)))
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers(); self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.end_headers()

    def do_GET(self):
        try: self._get()
        except Exception as e: self._json({"ok":False,"error":str(e)},500)

    def _get(self):
        p = urlparse(self.path).path
        if p in ("/","/api","/status"):
            return self._json({"name":"BLC","version":2,
                               "height":self.bc.height(),
                               "head":self.bc.head().hash,
                               "mempool":len(self.bc.mempool),
                               "validators":self.bc.active_validators()})
        if p == "/chain": return self._json(self.bc.to_dict())
        if p.startswith("/balance/"):
            a = p[9:]
            if not is_valid_address(a):
                return self._json({"ok":False,"error":"bad addr"},400)
            return self._json({"address":a,
                               "balance":self.bc.get_balance(a),
                               "nonce":self.bc.get_nonce(a),
                               "stake":self.bc.get_stake(a)})
        if p == "/validators": return self._json(self.bc.active_validators())
        if p == "/contracts":
            with self.bc.lock:
                return self._json([{"address":k,"owner":v["owner"],
                                    "created_at":v["created_at"],
                                    "last_call":v.get("last_call")}
                                   for k,v in self.bc._contracts.items()])
        if p.startswith("/contract/"):
            a = p[10:]
            with self.bc.lock:
                c = self.bc._contracts.get(a)
                if not c: return self._json({"ok":False,"error":"not found"},404)
                return self._json({"ok":True,"address":a,**c})
        if p.startswith("/txs/"):
            a = p[5:]
            txs = []
            with self.bc.lock:
                for b in self.bc.blocks:
                    for t in b.txs:
                        if t.from_addr==a or t.to_addr==a:
                            txs.append({**t.to_dict(),
                                        "height":b.height,"hash":t.tx_hash()})
            return self._json(txs[:100])
        # DNS
        if p == "/dns/domains":
            return self._json({"ok":True,"domains":self.bc.dns_domains()})
        if p.startswith("/dns/resolve/"):
            n = p[13:].lower(); d = self.bc.dns_resolve(n)
            if not d: return self._json({"ok":False,"error":"not found"},404)
            return self._json({"ok":True,"name":n+".bl",**d})
        if p.startswith("/dns/auction/"):
            n = p[13:].lower(); a = self.bc.auction_get(n)
            if not a: return self._json({"ok":False,"error":"not found",
                                        "min_bid":min_bid_for(n)},404)
            return self._json({"ok":True,"auction":a,"min_bid":min_bid_for(n)})
        if p == "/dns/list":
            a, s = self.bc.auctions_list()
            return self._json({"ok":True,"auctions":a,"settled":s,
                               "duration":AUCTION_DURATION})
        return self._json({"ok":False,"error":"not found"},404)

    def do_POST(self):
        try: self._post()
        except Exception as e: self._json({"ok":False,"error":str(e)},400)

    def _post(self):
        p = urlparse(self.path).path
        b = self._body()
        if p == "/send":
            tx = Transaction(b["from"], b.get("to","SYSTEM"), b["amount"],
                            b["nonce"], b.get("timestamp"), b["pubkey"],
                            b["signature"], b.get("fee",0.0),
                            b.get("op","transfer"), b.get("params") or {})
            ok, err = self.bc.submit(tx)
            if not ok: return self._json({"ok":False,"error":err},400)
            if self.node and self.node._loop:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self.node.p2p.send_tx(tx), self.node._loop)
                except: pass
            return self._json({"ok":True,"tx_hash":tx.tx_hash()})
        if p == "/call":
            a = b["address"]; fn = b["method"]
            from .vm.compiler import compile_tolk
            from .vm.tvm import TVM
            with self.bc.lock:
                c = self.bc._contracts.get(a)
                if not c: return self._json({"ok":False,"error":"not found"},404)
                try:
                    src = bytes.fromhex(c["code"]).decode()
                    bc = compile_tolk(src)
                    if fn not in bc:
                        return self._json({"ok":False,"error":"no method"},400)
                    vm = TVM(gas=100_000)
                    ret = vm.execute(bc[fn], b.get("args",[]), c["data"])
                    return self._json({"ok":True,"returned":ret,
                                       "gas_used":vm.gas_used})
                except Exception as e:
                    return self._json({"ok":False,"error":str(e)},400)
        return self._json({"ok":False,"error":"not found"},404)


def start(bc, node, host, port):
    handler = type("_H",(H,),{"bc":bc,"node":node})
    srv = ThreadingHTTPServer((host,port), handler)
    srv.serve_forever()