import time, unittest
from bl.core.block import Block
from bl.core.blockchain import Blockchain
from bl.core.crypto import new_keypair, pub_to_hex, pubkey_to_address
from bl.core.transaction import Transaction


class T(unittest.TestCase):
    def setUp(self):
        self.sk, vk = new_keypair()
        self.addr = pubkey_to_address(vk)
        self.bc = Blockchain([{"address":self.addr,
                               "pubkey":pub_to_hex(vk), "name":"v0"}])
        self.u_sk, uvk = new_keypair()
        self.u_addr = pubkey_to_address(uvk)

    def _produce(self, txs):
        h = self.bc.head()
        b = Block(h.height+1, h.hash, txs, self.addr,
                  pub_to_hex(self.sk.public_key()), timestamp=time.time())
        b.sign(self.sk)
        ok, err = self.bc.validate_and_apply(b)
        self.assertTrue(ok, err)

    def _tx(self, op, params=None, amount=0, nonce=0):
        tx = Transaction(self.u_addr, "SYSTEM", amount, nonce,
                        op=op, params=params or {})
        tx.sign(self.u_sk)
        return tx

    def test_01_faucet(self):
        self._produce([self._tx("faucet")])
        self.assertAlmostEqual(self.bc.get_balance(self.u_addr), 100.0)

    def test_02_stake(self):
        self._produce([self._tx("faucet")])
        self._produce([self._tx("stake", amount=100, nonce=1)])
        self.assertAlmostEqual(self.bc.get_stake(self.u_addr), 100.0)
        vs = self.bc.active_validators()
        self.assertTrue(any(v["address"]==self.u_addr for v in vs))

    def test_03_contract(self):
        self._produce([self._tx("faucet")])
        src = "fun add(a: int, b: int): int { return a + b; }"
        self._produce([self._tx("deploy_contract",
                               {"code": src.encode().hex()}, nonce=1)])
        with self.bc.lock:
            addr = list(self.bc._contracts.keys())[0]
        self._produce([self._tx("call_contract",
                               {"address":addr,"method":"add","args":[2,3]},
                               nonce=2)])
        with self.bc.lock:
            call = self.bc._contracts[addr]["last_call"]
        self.assertEqual(call["returned"], 5)


if __name__ == "__main__": unittest.main()