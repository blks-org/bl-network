import asyncio, json
from ..core.block import Block
from ..core.transaction import Transaction

MAX = 4 * 1024 * 1024

def _enc(m): return (json.dumps(m, separators=(",",":")) + "\n").encode()


class P2P:
    def __init__(self, bc, host, port, peers, on_block=None, on_tx=None, log=print):
        self.bc = bc; self.host = host; self.port = port
        self.peers = set(peers or [])
        self.on_block = on_block or (lambda b: None)
        self.on_tx = on_tx or (lambda t: None)
        self.log = log; self.running = False
        self._server = None; self._conns = set(); self._connected = set()

    async def start(self):
        self.running = True
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        self.log(f"[P2P] :{self.port}")
        asyncio.create_task(self._loop())

    async def stop(self):
        self.running = False
        if self._server:
            self._server.close()
            try: await self._server.wait_closed()
            except: pass
        for w in list(self._conns):
            try: w.close()
            except: pass

    async def _loop(self):
        while self.running:
            for h, p in list(self.peers):
                try: await self._connect(h, p)
                except Exception as e: self.log(f"[P2P] {h}:{p} {e}")
            await asyncio.sleep(15)

    async def _connect(self, h, p):
        if (h,p) in self._connected: return
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(h,p), 5)
        except: return
        self._connected.add((h,p)); self._conns.add(w)
        await self._send(w, {"type":"HELLO","height":self.bc.height(),
                             "head":self.bc.head().hash,"port":self.port})
        asyncio.create_task(self._serve(r, w, (h,p)))
        self.log(f"[P2P] → {h}:{p}")

    async def _client(self, r, w):
        self._conns.add(w)
        await self._serve(r, w, None)

    async def _serve(self, r, w, key):
        try:
            while self.running:
                line = await asyncio.wait_for(r.readline(), 180)
                if not line or len(line) > MAX: break
                try: m = json.loads(line)
                except: continue
                await self._handle(m, w)
        except: pass
        finally:
            self._conns.discard(w)
            if key: self._connected.discard(key)
            try: w.close()
            except: pass

    async def _handle(self, m, w):
        t = m.get("type")
        if t == "HELLO":
            ph = m.get("height",0)
            if ph > self.bc.height():
                await self._send(w, {"type":"GET","from":self.bc.height()})
            elif ph < self.bc.height():
                await self._send(w, {"type":"CHAIN","blocks":self._blocks(ph)})
        elif t == "GET":
            await self._send(w, {"type":"CHAIN","blocks":self._blocks(m["from"])})
        elif t == "CHAIN":
            for bd in m.get("blocks",[]):
                try: b = Block.from_dict(bd)
                except: continue
                ok,_ = self.bc.validate_and_apply(b)
                if ok: self.on_block(b)
        elif t == "TX":
            try: tx = Transaction.from_dict(m["tx"])
            except: return
            ok,_ = self.bc.submit(tx)
            if ok: self.on_tx(tx)
        elif t == "BLOCK":
            try: b = Block.from_dict(m["block"])
            except: return
            ok,err = self.bc.validate_and_apply(b)
            if ok: self.on_block(b)
            elif "height" in err or "prev" in err:
                await self._send(w, {"type":"GET","from":self.bc.height()})

    def _blocks(self, frm):
        with self.bc.lock:
            return [b.to_dict() for b in self.bc.blocks if b.height >= frm][:500]

    async def _send(self, w, m):
        try: w.write(_enc(m)); await w.drain()
        except: pass

    async def broadcast(self, m):
        d = _enc(m)
        for w in list(self._conns):
            try: w.write(d); await w.drain()
            except: self._conns.discard(w)

    async def send_tx(self, tx): await self.broadcast({"type":"TX","tx":tx.to_dict()})
    async def send_block(self, b): await self.broadcast({"type":"BLOCK","block":b.to_dict()})