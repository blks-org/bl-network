"""Minter — эмиссия, supply, treasury."""


class Minter:
    def __init__(self, bc):
        self.bc = bc
        self.initial_supply = 0.0
        self.total_minted = 0.0
        self.treasury_address = bc.system_addresses["treasury"]

    def init_genesis_supply(self, amount):
        self.bc._bal[self.treasury_address] = self.bc._bal.get(self.treasury_address, 0.0) + amount
        self.initial_supply = amount
        self.total_minted = amount
        return amount

    def mint_block_reward(self, producer, amount=None):
        amount = amount if amount is not None else self.bc.config.get("block_reward", 10.0)
        self.bc._bal[producer] = self.bc._bal.get(producer, 0.0) + amount
        self.total_minted += amount
        return amount

    def get_total_supply(self):
        return (sum(self.bc._bal.values())
                + self.bc.elector.total_stake()
                + sum(u["amount"] for u in self.bc.elector.pending_unstake))

    def to_dict(self):
        return {"initial_supply": self.initial_supply,
                "total_minted": self.total_minted,
                "treasury": self.treasury_address}

    @staticmethod
    def from_dict(bc, d):
        m = Minter(bc)
        if d:
            m.initial_supply = d.get("initial_supply", 0.0)
            m.total_minted = d.get("total_minted", 0.0)
        return m