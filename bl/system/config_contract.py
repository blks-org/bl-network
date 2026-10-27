"""Config Contract — параметры сети (аналог Param 0 в TON).

Управляется голосованием валидаторов (2/3). Менять параметры в коде
не требуется — только транзакция `config_propose` в сеть.
"""
import time


CONFIG_KEYS = [
    "min_stake", "max_validators", "election_interval", "block_reward",
    "dns_price", "auction_duration", "bridge_fee", "unstake_delay",
]

DEFAULT_PARAMS = {
    "min_stake":         100.0,
    "max_validators":    100,
    "election_interval": 3600,
    "block_reward":      10.0,
    "dns_price":         0.1,
    "auction_duration":  180,
    "bridge_fee":        0.01,
    "unstake_delay":     3600,
}


class ConfigContract:
    def __init__(self, bc):
        self.bc = bc
        self.params = dict(DEFAULT_PARAMS)
        self.pending = {}  # (key, str(value)) -> {addr: ts}
        self.last_update = time.time()

    def get(self, key, default=None):
        return self.params.get(key, default)

    def get_all(self):
        return dict(self.params)

    def propose_change(self, key, new_value, proposer):
        if key not in CONFIG_KEYS:
            return {"ok": False, "err": f"unknown key: {key}"}
        if proposer not in self.bc.elector.validators:
            return {"ok": False, "err": "not a validator"}

        vote_key = (key, str(new_value))
        votes = self.pending.setdefault(vote_key, {})
        votes[proposer] = time.time()

        need = max(1, (len(self.bc.elector.validators) * 2 + 2) // 3)
        if len(votes) >= need:
            old = self.params.get(key)
            self.params[key] = new_value
            self.last_update = time.time()
            del self.pending[vote_key]
            return {"ok": True, "applied": True, "key": key,
                    "old": old, "new": new_value,
                    "votes": len(votes), "needed": need}
        return {"ok": True, "applied": False, "votes": len(votes), "needed": need}

    def get_pending(self):
        return {f"{k[0]}={k[1]}": {"votes": len(v), "voters": list(v.keys())}
                for k, v in self.pending.items()}

    def to_dict(self):
        return {"params": self.params, "last_update": self.last_update}

    @staticmethod
    def from_dict(bc, d):
        c = ConfigContract(bc)
        if d:
            c.params = d.get("params", c.params)
            c.last_update = d.get("last_update", c.last_update)
        return c