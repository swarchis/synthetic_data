"""Input generators for the differential checker (diff_test.py).

Each top-level input gets a rule from diff.json; unlisted inputs are random.

    {"kind": "random"}                     uniform; for 1-bit inputs, "p1" = chance of 1
    {"kind": "random", "p1": 0.2}          each bit 1 with probability p1 (sparse/dense vectors)
    {"kind": "steady", "p_change": 0.02}   holds a value, occasionally jumps to a new random one
    {"kind": "values", "values": [0, 1, 7], "weights": [1, 1, 2]}
    {"kind": "toggle", "p_flip": 0.05}     each bit flips with probability p_flip per cycle
    {"kind": "const", "value": 0}

Buses: an "apb" section drives PSEL/PENABLE/PWRITE/PADDR/PWDATA with legal
two-cycle transfers (setup, then access) and idle cycles in between.
"""

import random


class InputGen:
    def __init__(self, rule, width, rng):
        self.rule, self.width, self.rng = dict(rule), width, rng
        self.mask = (1 << width) - 1
        self.value = 0
        if self.rule.get("kind") == "steady":
            self.value = self._uniform()

    def _uniform(self):
        return self.rng.getrandbits(self.width) if self.width else 0

    def _bits(self, p1):
        v = 0
        for b in range(self.width):
            if self.rng.random() < p1:
                v |= 1 << b
        return v

    def update(self, overrides=None):
        rule = dict(self.rule, **(overrides or {}))
        kind = rule.get("kind", "random")
        if kind == "random":
            self.value = self._bits(rule["p1"]) if "p1" in rule else self._uniform()
        elif kind == "steady":
            if self.rng.random() < rule.get("p_change", 0.02):
                self.value = self._uniform() if "values" not in rule else self.rng.choice(rule["values"])
        elif kind == "values":
            self.value = self.rng.choices(rule["values"], weights=rule.get("weights"))[0] & self.mask
        elif kind == "toggle":
            flip = self._bits(rule.get("p_flip", 0.05))
            self.value ^= flip
        elif kind == "const":
            self.value = rule["value"] & self.mask
        else:
            raise ValueError(f"unknown input kind {kind!r}")
        return self.value


class ApbDriver:
    """Legal APB3 transfers. Yields one dict of bus signal values per cycle."""

    def __init__(self, rule, rng):
        self.rng = rng
        self.addrs = rule["addresses"]
        self.weights = rule.get("weights")
        self.p_idle = rule.get("p_idle", 0.3)
        self.p_misaligned = rule.get("p_misaligned", 0.05)
        self.p_write = rule.get("p_write", 0.5)
        self.data_values = rule.get("data_values", [])     # extra interesting write data
        self.names = {k: rule.get(k, k) for k in ("PSEL", "PENABLE", "PWRITE", "PADDR", "PWDATA")}
        self.phase = "idle"
        self.cur = None

    def _new_transfer(self):
        a = self.rng.choices(self.addrs, weights=self.weights)[0]
        if self.rng.random() < self.p_misaligned:
            a += self.rng.randrange(1, 4)
        write = int(self.rng.random() < self.p_write)
        if self.data_values and self.rng.random() < 0.5:
            data = self.rng.choice(self.data_values)
        else:
            data = self.rng.getrandbits(32)
        return {"PWRITE": write, "PADDR": a & 0xFF, "PWDATA": data & 0xFFFFFFFF}

    def step(self):
        n = self.names
        if self.phase == "setup":
            self.phase = "access"
            return {n["PSEL"]: 1, n["PENABLE"]: 1, **{n[k]: v for k, v in self.cur.items()}}
        if self.rng.random() < self.p_idle:
            self.phase = "idle"
            # Bus junk while not selected, including PENABLE: a slave must ignore it without PSEL.
            junk = {"PENABLE": self.rng.randrange(2), "PWRITE": self.rng.randrange(2),
                    "PADDR": self.rng.randrange(256), "PWDATA": self.rng.getrandbits(32)}
            return {n["PSEL"]: 0, **{n[k]: v for k, v in junk.items()}}
        self.cur = self._new_transfer()
        self.phase = "setup"
        return {n["PSEL"]: 1, n["PENABLE"]: 0, **{n[k]: v for k, v in self.cur.items()}}

    def reset(self):
        self.phase = "idle"

    @property
    def signals(self):
        return set(self.names.values())


def make_rng(seed):
    return random.Random(seed)
