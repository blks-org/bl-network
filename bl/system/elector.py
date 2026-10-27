"""Elector Contract — регистрация валидаторов, выборы, slashing (Param 1 в TON).

Отвечает за:
  - регистрацию кандидатов (stake → candidates)
  - периодические выборы активного набора валидаторов (каждый election_interval)
  - slashing — штраф за нарушения (перевод в treasury)
  - очередь unstake — вывод стейка с задержкой (unstake_delay)
  - распределение epoch'ов
"""
import time


class ElectorContract:
    def __init__(self, bc):
        self.bc = bc
        self.epoch = 0
        self.last_election = time.time()

        # addr -> {stake: float, pubkey: str, since: float}
        self.validators = {}
        # addr -> {stake: float, pubkey: str, since: float}
        self.candidates = {}
        # [{addr, amount, unlock_at}]
        self.pending_unstake = []
        # addr -> total_slashed
        self.slashed = {}

    # ═══════════ РЕГИСТРАЦИЯ / ВЫХОД ═══════════

    def register(self, addr, stake, pubkey=""):
        """Зарегистрировать кандидата. Валидаторы проверяются при elect()."""
        if stake < self.bc.config.get("min_stake", 100.0):
            return {"ok": False, "err": f"stake too low (need {self.bc.config.get('min_stake', 100.0)})"}

        # если уже валидатор — увеличиваем стейк
        if addr in self.validators:
            self.validators[addr]["stake"] += float(stake)
            return {"ok": True, "role": "validator",
                    "stake": self.validators[addr]["stake"]}

        # иначе — в кандидаты
        existing = self.candidates.get(addr, {})
        self.candidates[addr] = {
            "stake": existing.get("stake", 0.0) + float(stake),
            "pubkey": pubkey or existing.get("pubkey", ""),
            "since": existing.get("since", time.time()),
        }
        return {"ok": True, "role": "candidate",
                "stake": self.candidates[addr]["stake"]}

    def unstake(self, addr, amount):
        """Вычесть из стейка и поставить в очередь на разблокировку.

        Вызывается из Blockchain._exec (op="unstake").
        Баланс НЕ увеличивается сразу — деньги придут через process_pending_unstake.
        """
        v = self.validators.get(addr)
        if not v:
            return False
        actual = min(float(amount), v["stake"])
        if actual <= 0:
            return False

        v["stake"] -= actual

        # если стейк упал ниже min_stake — выпадает из активного набора
        min_stake = self.bc.config.get("min_stake", 100.0)
        if v["stake"] < min_stake:
            # полностью выводим из validators (или удаляем если 0)
            if v["stake"] <= 1e-9:
                del self.validators[addr]
            # иначе — валидатор остаётся, но не попадает в выборы (см. elect)

        # в очередь на разблокировку
        delay = self.bc.config.get("unstake_delay", 3600)
        self.pending_unstake.append({
            "addr": addr,
            "amount": actual,
            "unlock_at": time.time() + delay,
        })
        return True

    def unregister(self, addr):
        """Полный выход: забрать весь стейк в очередь."""
        v = self.validators.get(addr)
        if not v:
            return {"ok": False, "err": "not a validator"}

        amount = v["stake"]
        del self.validators[addr]

        delay = self.bc.config.get("unstake_delay", 3600)
        self.pending_unstake.append({
            "addr": addr,
            "amount": amount,
            "unlock_at": time.time() + delay,
        })
        return {"ok": True, "unlock_in": delay, "amount": amount}

    # ═══════════ ВЫБОРЫ ═══════════

    def elect(self, now=None):
        """Провести выборы (раз в election_interval).

        Возвращает список избранных адресов или None, если выборы ещё не пора.
        """
        now = now or time.time()
        interval = self.bc.config.get("election_interval", 3600)
        if now - self.last_election < interval:
            return None

        # пул = текущие валидаторы + кандидаты (у кого stake >= min_stake)
        min_stake = self.bc.config.get("min_stake", 100.0)
        pool = {}

        for addr, v in self.validators.items():
            if v["stake"] >= min_stake:
                pool[addr] = dict(v)

        for addr, c in self.candidates.items():
            if c["stake"] >= min_stake:
                if addr in pool:
                    pool[addr]["stake"] += c["stake"]
                else:
                    pool[addr] = dict(c)

        # сортировка по стейку (больше — выше)
        max_v = self.bc.config.get("max_validators", 100)
        chosen = sorted(
            pool.items(),
            key=lambda x: x[1]["stake"],
            reverse=True,
        )[:max_v]

        # обновляем validators
        self.validators = {
            addr: {
                "stake": info["stake"],
                "pubkey": info.get("pubkey", ""),
                "since": info.get("since", now),
            }
            for addr, info in chosen
        }

        # кандидаты, ставшие валидаторами, убираем из candidates
        for addr in self.validators:
            self.candidates.pop(addr, None)

        self.epoch += 1
        self.last_election = now
        return list(self.validators.keys())

    # ═══════════ SLASHING ═══════════

    def slash(self, addr, amount, reason=""):
        """Оштрафовать валидатора. Штраф уходит в treasury.

        Возвращает фактически списанную сумму.
        """
        v = self.validators.get(addr)
        if not v:
            return 0.0

        actual = min(float(amount), v["stake"])
        if actual <= 0:
            return 0.0

        v["stake"] -= actual
        self.slashed[addr] = self.slashed.get(addr, 0.0) + actual

        # перевод в treasury
        treasury = self.bc.system_addresses.get("treasury")
        if treasury:
            self.bc._bal[treasury] = self.bc._bal.get(treasury, 0.0) + actual

        # если стейк упал ниже min_stake — удаляем
        if v["stake"] < self.bc.config.get("min_stake", 100.0):
            del self.validators[addr]

        return actual

    # ═══════════ ОЧЕРЕДЬ UNSTAKE ═══════════

    def process_pending_unstake(self, now):
        """Разблокировать стейки, у которых прошёл unlock_at.

        Вызывается в validate_and_apply() и _force_apply() при каждом блоке.
        """
        for u in list(self.pending_unstake):
            if now >= u["unlock_at"]:
                addr = u["addr"]
                amount = u["amount"]
                # возвращаем на баланс
                self.bc._bal[addr] = self.bc._bal.get(addr, 0.0) + amount
                self.pending_unstake.remove(u)

    # ═══════════ ГЕТТЕРЫ ═══════════

    def active_validators(self):
        """Список адресов текущих валидаторов (отсортированный для round-robin)."""
        return sorted(self.validators.keys())

    def total_stake(self):
        return sum(v["stake"] for v in self.validators.values())

    def get_validators(self):
        return [
            {
                "address": addr,
                "stake": v["stake"],
                "pubkey": v.get("pubkey", ""),
                "since": v.get("since", 0),
            }
            for addr, v in self.validators.items()
        ]

    def get_candidates(self):
        return [
            {
                "address": addr,
                "stake": c["stake"],
                "pubkey": c.get("pubkey", ""),
                "since": c.get("since", 0),
            }
            for addr, c in self.candidates.items()
        ]

    # ═══════════ СЕРИАЛИЗАЦИЯ ═══════════

    def to_dict(self):
        return {
            "epoch": self.epoch,
            "last_election": self.last_election,
            "validators": self.validators,
            "candidates": self.candidates,
            "pending_unstake": self.pending_unstake,
            "slashed": self.slashed,
        }

    @staticmethod
    def from_dict(bc, d):
        e = ElectorContract(bc)
        if d:
            e.epoch = d.get("epoch", 0)
            e.last_election = d.get("last_election", time.time())
            e.validators = d.get("validators", {})
            e.candidates = d.get("candidates", {})
            e.pending_unstake = d.get("pending_unstake", [])
            e.slashed = d.get("slashed", {})
        return e