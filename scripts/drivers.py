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
A "wishbone" section does the same for classic Wishbone (see WishboneDriver).
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
        # Per-register write data, e.g. {"4": [5, 20, 100]}: keeps values in a useful range.
        self.data_by_addr = {int(k, 0): v for k, v in rule.get("data_by_addr", {}).items()}
        # Writes issued after every reset, before random traffic: [[addr, data], ...]
        self.init_writes = rule.get("init_writes", [])
        self.p_alias = rule.get("p_alias", 0.0)              # set random upper address bits
        self.alias_bits = rule.get("alias_bits", [16, 32])   # ...in this bit range [lo, hi)
        self.p_random_addr = rule.get("p_random_addr", 0.0)  # fully random address in the low range
        self.random_addr_bits = rule.get("random_addr_bits", 16)
        self.p_near_addr = rule.get("p_near_addr", 0.0)      # valid address with one decoded bit flipped
        # Optional traffic phases: [{"transfers": N, "weights": [...]}, ...], repeated in order.
        # Lets a device reach states that steady traffic never does (e.g. a FIFO filling up).
        self.weight_phases = rule.get("phases", [])
        self.transfers = 0
        self.pending_init = list(self.init_writes)
        self.names = {k: rule.get(k, k) for k in ("PSEL", "PENABLE", "PWRITE", "PADDR", "PWDATA")}
        self.phase = "idle"
        self.cur = None

    def _new_transfer(self):
        if self.pending_init:
            a, data = self.pending_init.pop(0)
            return {"PWRITE": 1, "PADDR": a & 0xFFFFFFFF, "PWDATA": data & 0xFFFFFFFF}
        weights = self.weights
        if self.weight_phases:
            t = self.transfers % sum(ph["transfers"] for ph in self.weight_phases)
            for ph in self.weight_phases:
                if t < ph["transfers"]:
                    weights = ph["weights"]
                    break
                t -= ph["transfers"]
        self.transfers += 1
        a = self.rng.choices(self.addrs, weights=weights)[0]
        write = int(self.rng.random() < self.p_write)
        if a in self.data_by_addr and self.rng.random() < 0.85:
            data = self.rng.choice(self.data_by_addr[a])
        elif self.data_values and self.rng.random() < 0.5:
            data = self.rng.choice(self.data_values)
        else:
            data = self.rng.getrandbits(32)
        if self.rng.random() < self.p_misaligned:
            a += self.rng.randrange(1, 4)
        if self.rng.random() < self.p_random_addr:
            a = self.rng.getrandbits(self.random_addr_bits)
        elif self.rng.random() < self.p_near_addr:            # near-miss: catches decoders using too few bits
            a ^= 1 << self.rng.randrange(2, self.random_addr_bits)
        if self.rng.random() < self.p_alias:
            lo, hi = self.alias_bits
            a |= self.rng.getrandbits(hi - lo) << lo
        return {"PWRITE": write, "PADDR": a & 0xFFFFFFFF, "PWDATA": data & 0xFFFFFFFF}

    def step(self):
        n = self.names
        if self.phase == "setup":
            self.phase = "access"
            return {n["PSEL"]: 1, n["PENABLE"]: 1, **{n[k]: v for k, v in self.cur.items()}}
        if self.rng.random() < self.p_idle and not self.pending_init:
            self.phase = "idle"
            # Bus junk while not selected, including PENABLE: a slave must ignore it without PSEL.
            junk = {"PENABLE": self.rng.randrange(2), "PWRITE": self.rng.randrange(2),
                    "PADDR": self.rng.choice(self.addrs) if self.rng.random() < 0.5 else self.rng.randrange(256),
                    "PWDATA": self.rng.getrandbits(32)}
            return {n["PSEL"]: 0, **{n[k]: v for k, v in junk.items()}}
        self.cur = self._new_transfer()
        self.phase = "setup"
        return {n["PSEL"]: 1, n["PENABLE"]: 0, **{n[k]: v for k, v in self.cur.items()}}

    def reset(self):
        self.phase = "idle"
        self.pending_init = list(self.init_writes)

    @property
    def signals(self):
        return set(self.names.values())


class WishboneDriver:
    """Classic Wishbone single transfers for a slave with a registered ACK (ACK one clock
    after CYC & STB, and the write taking effect on the edge where ACK is high): each
    transfer holds CYC, STB, WE, ADR and DAT for 2 cycles, then the bus may idle.

    Config "wishbone": {"addresses": [...], "weights": [...], "p_write": 0.5,
    "p_idle": 0.3, "data_by_addr": {"4": [...]}, "init_writes": [[adr, data], ...],
    "phases": [{"transfers": N, "weights": [...]}], "names": {"CYC": "wb_cyc_i", ...},
    "p_sequence": 0.05, "sequences": [{"weight": 1, "steps": [[3, 160], ["idle", 300], [4, "R"]]}]}.
    Idle cycles drop CYC or STB (never both high) with junk on the other signals.
    A sequence is a script played from start to end when chosen: [adr, data] is a write
    (data "R" = a random byte), ["read", adr] is a read, ["idle", n] is n idle cycles. Sequences let the stimulus
    form whole protocol transactions that random traffic would almost never produce."""

    def __init__(self, rule, rng):
        self.rng = rng
        self.addrs = rule["addresses"]
        self.weights = rule.get("weights")
        self.p_idle = rule.get("p_idle", 0.3)
        self.p_write = rule.get("p_write", 0.5)
        self.data_by_addr = {int(k, 0): v for k, v in rule.get("data_by_addr", {}).items()}
        self.init_writes = rule.get("init_writes", [])
        self.weight_phases = rule.get("phases", [])
        self.width = rule.get("data_width", 8)
        self.sequences = rule.get("sequences", [])
        self.p_sequence = rule.get("p_sequence", 0.0)
        self.script = []           # steps left in the current sequence
        self.idle_left = 0
        names = {"CYC": "wb_cyc_i", "STB": "wb_stb_i", "WE": "wb_we_i", "ADR": "wb_adr_i", "DAT": "wb_dat_i"}
        self.names = {**names, **rule.get("names", {})}
        self.transfers = 0
        self.pending_init = list(self.init_writes)
        self.left = 0
        self.cur = None

    def _new_transfer(self):
        if self.pending_init:
            a, data = self.pending_init.pop(0)
            return {"WE": 1, "ADR": a, "DAT": data}
        weights = self.weights
        if self.weight_phases:
            t = self.transfers % sum(ph["transfers"] for ph in self.weight_phases)
            for ph in self.weight_phases:
                if t < ph["transfers"]:
                    weights = ph["weights"]
                    break
                t -= ph["transfers"]
        self.transfers += 1
        a = self.rng.choices(self.addrs, weights=weights)[0]
        write = int(self.rng.random() < self.p_write)
        if a in self.data_by_addr and self.rng.random() < 0.85:
            data = self.rng.choice(self.data_by_addr[a])
        else:
            data = self.rng.getrandbits(self.width)
        return {"WE": write, "ADR": a, "DAT": data}

    def _idle(self):
        n = self.names
        cyc = self.rng.randrange(2)
        junk = {"WE": self.rng.randrange(2), "ADR": self.rng.choice(self.addrs), "DAT": self.rng.getrandbits(self.width)}
        return {n["CYC"]: cyc, n["STB"]: 0 if cyc else self.rng.randrange(2), **{n[k]: v for k, v in junk.items()}}

    def step(self):
        n = self.names
        if self.left:
            self.left -= 1
            return {n["CYC"]: 1, n["STB"]: 1, **{n[k]: v for k, v in self.cur.items()}}
        if self.idle_left:
            self.idle_left -= 1
            return self._idle()
        if not self.script and self.sequences and not self.pending_init and self.rng.random() < self.p_sequence:
            seq = self.rng.choices(self.sequences, weights=[q.get("weight", 1) for q in self.sequences])[0]
            self.script = list(seq["steps"])
        if self.script:
            a, d = self.script.pop(0)
            if a == "idle":
                self.idle_left = d - 1
                return self._idle()
            if a == "read":          # ["read", adr]: a read transfer
                self.cur = {"WE": 0, "ADR": d, "DAT": self.rng.getrandbits(self.width)}
                self.left = 1
                return {n["CYC"]: 1, n["STB"]: 1, **{n[k]: v for k, v in self.cur.items()}}
            self.cur = {"WE": 1, "ADR": a, "DAT": self.rng.getrandbits(self.width) if d == "R" else d}
            self.left = 1
            return {n["CYC"]: 1, n["STB"]: 1, **{n[k]: v for k, v in self.cur.items()}}
        if self.rng.random() < self.p_idle and not self.pending_init:
            cyc = self.rng.randrange(2)
            junk = {"WE": self.rng.randrange(2), "ADR": self.rng.choice(self.addrs), "DAT": self.rng.getrandbits(self.width)}
            return {n["CYC"]: cyc, n["STB"]: 0 if cyc else self.rng.randrange(2), **{n[k]: v for k, v in junk.items()}}
        self.cur = self._new_transfer()
        self.left = 1
        return {n["CYC"]: 1, n["STB"]: 1, **{n[k]: v for k, v in self.cur.items()}}

    def reset(self):
        self.left = 0
        self.idle_left = 0
        self.script = []
        self.pending_init = list(self.init_writes)

    @property
    def signals(self):
        return set(self.names.values())


def make_rng(seed):
    return random.Random(seed)


class AhbLiteDriver:
    """A legal single-master AHB-Lite master for one slave (HREADY = the slave's HREADYOUT).

    Config "ahb": {"transfers": [{"weight": w, "addr_range": [lo, hi] or "addr_list": [...], "write": p,
    "sizes": [0, 1, 2], "data": [...] (optional)}], "p_idle": 0.3, "p_unselected": 0.05,
    "names": {...}, "init": [[addr, data, size], ...] (writes done first)}.
    Pipelined: a transfer's address phase is followed by its data phase (HWDATA) in the next cycle;
    while HREADY is low, both phases' signals are held. The driver needs feedback: each cycle the
    harness passes the golden design's outputs, and HREADY is driven from its HREADYOUT."""

    wants_feedback = True

    def __init__(self, rule, rng):
        self.rng = rng
        self.kinds = rule["transfers"]
        self.p_idle = rule.get("p_idle", 0.3)
        self.p_unselected = rule.get("p_unselected", 0.05)
        names = {"HSEL": "HSEL", "HADDR": "HADDR", "HWDATA": "HWDATA", "HTRANS": "HTRANS", "HSIZE": "HSIZE",
                 "HWRITE": "HWRITE", "HREADY": "HREADY", "HREADYOUT": "HREADYOUT"}
        self.names = {**names, **rule.get("names", {})}
        self.init = rule.get("init", [])
        self.reset()

    def reset(self):
        self.aphase = None          # transfer in its address phase
        self.dphase = None          # transfer in its data phase
        self.prev_ready = 1
        self.pending_init = list(self.init)

    def _new(self):
        if self.pending_init:
            a, d, sz = self.pending_init.pop(0)
            return {"sel": 1, "trans": 2, "addr": a, "write": 1, "size": sz, "data": d}
        if self.rng.random() < self.p_idle:
            return {"sel": int(self.rng.random() < 0.5), "trans": 0, "addr": self.rng.getrandbits(24), "write": self.rng.randrange(2), "size": 2, "data": self.rng.getrandbits(32)}
        k = self.rng.choices(self.kinds, weights=[x.get("weight", 1) for x in self.kinds])[0]
        size = self.rng.choice(k.get("sizes", [2]))
        addr = self.rng.choice(k["addr_list"]) if "addr_list" in k else self.rng.randrange(*k["addr_range"])
        addr &= ~((1 << size) - 1)
        data = self.rng.choice(k["data"]) if k.get("data") else self.rng.getrandbits(32)
        t = {"sel": 1, "trans": 2, "addr": addr, "write": int(self.rng.random() < k.get("write", 0.5)), "size": size, "data": data}
        if self.rng.random() < self.p_unselected:
            t["sel"] = 0
        return t

    def step(self, feedback=None):
        n = self.names
        ready = (feedback or {}).get(n["HREADYOUT"])
        ready = 1 if ready is None else ready
        if self.prev_ready:              # the previous cycle's edge accepted the address phase
            self.dphase = self.aphase if (self.aphase and self.aphase["trans"] == 2 and self.aphase["sel"]) else None
            self.aphase = self._new()
        self.prev_ready = ready
        a = self.aphase
        return {n["HSEL"]: a["sel"], n["HTRANS"]: a["trans"], n["HADDR"]: a["addr"], n["HWRITE"]: a["write"],
                n["HSIZE"]: a["size"], n["HWDATA"]: self.dphase["data"] if self.dphase else 0,
                n["HREADY"]: ready}

    @property
    def signals(self):
        n = self.names
        return {n[k] for k in ("HSEL", "HADDR", "HWDATA", "HTRANS", "HSIZE", "HWRITE", "HREADY")}
