"""Spec check for ef_i2s: a cycle model written from designs/ef_i2s/spec.md alone, compared with the golden
RTL on every output (PRDATA at the current address, IRQ, sck, ws, PREADY) every cycle, under random APB
traffic, random sdi and random resets.

    docker run --rm --network none -v "$(pwd)":/work -w /work/scripts/spec_checks rtl-env \\
        make -f Makefile.ef_i2s

Result on 2026-11-06: spec_vs_rtl (random traffic) 299,772 values and stable_configs (long runs per
configuration, constant-data phases, a filling FIFO) 1,599,762 values compared, 0 mismatches. With `abs`
changed from bitwise inversion to negation, stable_configs mismatches 1,120 times (it took the forced
constant-data scenario to see it: random data almost never crosses a threshold by that margin).
"""
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

M32 = 0xFFFFFFFF
ADDRS = [0x0, 0x4, 0x8, 0xC, 0x10, 0x14, 0xFE00, 0xFE04, 0xFE08, 0xFF00, 0xFF04, 0xFF08, 0xFF0C, 0xFF10, 0x18, 0xFE0C]


class Model:
    def __init__(s):
        s.reset()
        s.mem = [None] * 16

    def reset(s):
        s.PR = s.AVGT = s.ZCRT = s.CTRL = 0; s.CFG = 0x201; s.THR = 0; s.FLUSH = 0
        s.IM = s.IC = s.RIS = 0; s.GCLK = 0
        s.sync0 = s.sync1 = 0
        s.prescaler = 0; s.sck = 0; s.bit_ctr = 0; s.ws = 1
        s.last_ws = 1; s.last_sck = 0; s.sr = 0; s.ws_dly0 = s.ws_dly = 0; s.last_ws_dly = 0
        s.ws_ppulse = s.ws_npulse = s.ws_dly_ppulse = s.ws_dly_npulse = 0
        s.first = 0; s.rdy = 0; s.sample = 0
        s.wp = s.rp = 0; s.full = 0; s.empty = 1; s.level = 0
        s.sum_ctr = 0; s.sum = 0; s.zc_ctr = 0; s.prev_sign = 0; s.zcr = 0

    # combinational helpers (from current state)
    def cfg(s):
        c = s.CFG
        return dict(ch=c & 3, sx=(c >> 2) & 1, lj=(c >> 3) & 1, size=(c >> 4) & 63, avg_sel=(c >> 10) & 1, zcr_sel=(c >> 11) & 1)

    def value(s):
        c = s.cfg(); sz = c["size"]
        part = (s.sample >> (32 - sz)) & M32 if 1 <= sz <= 32 else 0     # shift by 32 or more gives 0
        sign = (((M32 if (s.sample >> 31) & 1 else 0) << sz) & M32) if (c["sx"] and sz < 32) else 0
        return part | sign

    def selected(s):
        c = s.cfg()
        cur = 2 if c["lj"] == (1 - s.ws) else 1
        return (cur & c["ch"]) != 0

    def flags(s):
        c = s.cfg(); en_avg = (s.CTRL >> 2) & 1; en_zcr = (s.CTRL >> 3) & 1
        avg = en_avg and ((s.sum >> (6 if c["avg_sel"] else 5)) > s.AVGT)
        zcr = en_zcr and (s.zcr > s.ZCRT)
        above = (s.level > s.THR) or s.full
        return [s.empty, int(bool(above)), s.full, int(bool(avg)), int(bool(zcr)), int(bool(avg and zcr))]

    def prdata(s, a):
        a &= 0xFFFF
        if a == 0x0: return s.mem[s.rp]
        return {0x4: s.PR, 0x8: s.AVGT, 0xC: s.ZCRT, 0x10: s.CTRL, 0x14: s.CFG, 0xFE00: s.level, 0xFE04: s.THR,
                0xFE08: s.FLUSH, 0xFF00: s.IM, 0xFF04: s.RIS & s.IM, 0xFF08: s.RIS, 0xFF10: s.GCLK}.get(a, 0xDEADBEEF)

    def edge(s, psel, pen, pwrite, paddr, pwdata, sdi):
        a = paddr & 0xFFFF
        we = psel and pen and pwrite; re = psel and pen and not pwrite
        c = s.cfg(); en = s.CTRL & 1; fifo_en = (s.CTRL >> 1) & 1; avg_en = (s.CTRL >> 2) & 1; zcr_en = (s.CTRL >> 3) & 1
        n = {}
        # interrupts (conditions before the edge)
        f = s.flags(); ris = s.RIS
        for i in range(6):
            if (s.IC >> i) & 1: ris &= ~(1 << i)
            elif f[i]: ris |= 1 << i
        n["RIS"] = ris
        # clock generator
        if en:
            n["prescaler"] = s.PR if s.prescaler == 0 else s.prescaler - 1
            n["sck"] = (1 - s.sck) if s.prescaler == 0 else s.sck
            n["bit_ctr"] = (s.bit_ctr + 1) & 31 if (s.prescaler == 0 and s.sck == 1) else s.bit_ctr
            n["ws"] = (1 - s.ws) if (s.prescaler == 0 and s.sck == 1 and s.bit_ctr == 0) else s.ws
        # receiver
        sck_rise = s.sck and not s.last_sck; sck_fall = (not s.sck) and s.last_sck
        ws_pulse = s.ws_ppulse or s.ws_npulse; ws_dly_pulse = s.ws_dly_ppulse or s.ws_dly_npulse
        n["last_sck"] = s.sck; n["last_ws"] = s.ws
        n["ws_ppulse"] = int(s.ws and not s.last_ws); n["ws_npulse"] = int((not s.ws) and s.last_ws)
        if sck_fall: n["ws_dly0"] = s.ws; n["ws_dly"] = s.ws_dly0
        n["last_ws_dly"] = s.ws_dly
        n["ws_dly_ppulse"] = int(s.ws_dly and not s.last_ws_dly); n["ws_dly_npulse"] = int((not s.ws_dly) and s.last_ws_dly)
        if sck_rise: n["sr"] = ((s.sr << 1) | s.sync1) & M32
        if c["lj"] and ws_pulse: n["sample"] = s.sr
        elif (not c["lj"]) and ws_dly_pulse: n["sample"] = s.sr
        if ws_pulse or ws_dly_pulse: n["first"] = 1
        n["rdy"] = int(bool((ws_pulse if c["lj"] else ws_dly_pulse) and s.first))
        # processing (this cycle's rdy)
        sel = s.rdy and s.selected(); v = s.value()
        wr = fifo_en and sel; rd = re and a == 0x0
        if sel:
            n["sum_ctr"] = (s.sum_ctr + 1) & 63
            zero = (s.sum_ctr == 0) if c["avg_sel"] else ((s.sum_ctr & 31) == 0)
            ab = (~v & M32) if (v >> 31) & 1 else v
            if zero: n["sum"] = ab
            elif avg_en: n["sum"] = (s.sum + ab) & M32
            n["zc_ctr"] = (s.zc_ctr + 1) & 63; n["prev_sign"] = (v >> 31) & 1
            zz = (s.zc_ctr == 0) if c["zcr_sel"] else ((s.zc_ctr & 31) == 0)
            if zz: n["zcr"] = 0
            elif zcr_en: n["zcr"] = (s.zcr + (s.prev_sign ^ ((v >> 31) & 1))) & M32
        # FIFO
        w_en = wr and not s.full
        if w_en: s.mem[s.wp] = v
        if s.FLUSH:
            n.update(wp=0, rp=0, full=0, empty=1, level=0)
        else:
            if w_en and not rd:
                n.update(wp=(s.wp + 1) & 15, empty=0, level=(s.level + 1) & 15)
                if ((s.wp + 1) & 15) == s.rp: n["full"] = 1
            elif rd and not w_en:
                if not s.empty:
                    n.update(rp=(s.rp + 1) & 15, full=0, level=(s.level - 1) & 15)
                    if ((s.rp + 1) & 15) == s.wp: n["empty"] = 1
            elif rd and w_en:
                n.update(wp=(s.wp + 1) & 15, rp=(s.rp + 1) & 15)
        # registers
        if we:
            if a == 0x4: n["PR"] = pwdata & 0xFF
            if a == 0x8: n["AVGT"] = pwdata & M32
            if a == 0xC: n["ZCRT"] = pwdata & M32
            if a == 0x10: n["CTRL"] = pwdata & 0xF
            if a == 0x14: n["CFG"] = pwdata & 0xFFF
            if a == 0xFE04: n["THR"] = pwdata & 0xF
            if a == 0xFF00: n["IM"] = pwdata & 0x3F
            if a == 0xFF10: n["GCLK"] = pwdata & 1
        n["FLUSH"] = (pwdata & 1) if (we and a == 0xFE08) else 0
        n["IC"] = (pwdata & 0x3F) if (we and a == 0xFF0C) else 0
        n["sync0"] = sdi; n["sync1"] = s.sync0
        for k, v2 in n.items(): setattr(s, k, v2)


@cocotb.test()
async def spec_vs_rtl(dut):
    rng = random.Random(41)
    cocotb.start_soon(Clock(dut.PCLK, 10, "ns").start())
    m = Model()
    for x in ("PSEL", "PENABLE", "PWRITE", "PADDR", "PWDATA", "sdi"): getattr(dut, x).value = 0
    dut.PRESETn.value = 0
    await FallingEdge(dut.PCLK); await FallingEdge(dut.PCLK)
    dut.PRESETn.value = 1
    stats = {"compared": 0, "mism": 0, "fifo_writes": 0, "rdy": 0, "irq_cycles": 0, "flushes": 0}
    phase, cur = 0, None
    def pick():
        a = rng.choices(ADDRS, [6, 2, 1, 1, 2, 3, 2, 1, 1, 1, 1, 2, 1, 1, 1, 1])[0]
        w = int(rng.random() < 0.5) if a not in (0x0, 0xFE00, 0xFF04, 0xFF08) else int(rng.random() < 0.1)
        d = rng.getrandbits(32)
        if a == 0x4: d = rng.choice([0, 0, 1, 1, 2, 3, 5])
        if a == 0x10: d = rng.choice([0xF, 0xF, 0x3, 0x7, 0xB, 0x1, 0xE, 0x0])
        if a == 0x14: d = rng.choice([0x201, 0x203, 0x20B, 0x187, 0x10E, 0x082, 0xE03, 0x40B, 0x80F, 0x000, 0x3F3, 0x207]) ^ (rng.getrandbits(1) << 10)
        if a in (0x8, 0xC): d = rng.choice([0, 1, 3, 7, 0x10, 0x100, 0x7FFF, rng.getrandbits(32)])
        if a == 0xFF0C: d = rng.getrandbits(6)
        return a, w, d
    for cyc in range(60000):
        # inputs for this cycle
        if rng.random() < 0.0005:
            dut.PRESETn.value = 0; await Timer(1, "ns"); m.reset()
            await RisingEdge(dut.PCLK); m.reset(); await FallingEdge(dut.PCLK); dut.PRESETn.value = 1; phase = 0; cur = None
        if phase == 0 and rng.random() < 0.7: cur = pick(); phase = 1
        if phase == 1: psel, pen = 1, 0
        elif phase == 2: psel, pen = 1, 1
        else: psel, pen = rng.choice([(0, 0), (0, 1)]), 0
        if isinstance(psel, tuple): psel, pen = psel
        a, w, d = cur if cur else (rng.choice(ADDRS), 0, 0)
        sdi = rng.getrandbits(1)
        for k, v in (("PSEL", psel), ("PENABLE", pen), ("PWRITE", w), ("PADDR", a), ("PWDATA", d), ("sdi", sdi)):
            getattr(dut, k).value = v
        await Timer(1, "ns")
        # compare combinational/registered outputs now
        def chk(name, want):
            if want is None: return
            got = getattr(dut, name).value
            got = int(got) if got.is_resolvable else None
            stats["compared"] += 1
            if got != want:
                stats["mism"] += 1
                if stats["mism"] <= 10:
                    dut._log.error(f"cycle {cyc}: {name} rtl={got} spec={want} addr={a:#x} CTRL={m.CTRL:#x} CFG={m.CFG:#x} lvl={m.level}")
        chk("PRDATA", m.prdata(a)); chk("IRQ", int((m.RIS & m.IM) != 0)); chk("sck", m.sck); chk("ws", m.ws); chk("PREADY", 1)
        stats["irq_cycles"] += (m.RIS & m.IM) != 0
        await RisingEdge(dut.PCLK)
        stats["rdy"] += m.rdy; stats["fifo_writes"] += bool(m.rdy and m.selected() and (m.CTRL >> 1) & 1); stats["flushes"] += m.FLUSH
        m.edge(psel, pen, w, a, d, sdi)
        if phase == 1: phase = 2
        elif phase == 2: phase = 0; cur = None
        await FallingEdge(dut.PCLK)
    dut._log.info(f"RESULT {stats}")
    assert stats["mism"] == 0


@cocotb.test()
async def stable_configs(dut):
    """Long stretches with one configuration (fast clock, all detectors on, small thresholds), mostly
    RXDATA reads and status reads, so many samples flow through the FIFO and both detector windows."""
    rng = random.Random(43)
    cocotb.start_soon(Clock(dut.PCLK, 10, "ns").start())
    m = Model()
    for x in ("PSEL", "PENABLE", "PWRITE", "PADDR", "PWDATA", "sdi"): getattr(dut, x).value = 0
    dut.PRESETn.value = 0
    await FallingEdge(dut.PCLK); await FallingEdge(dut.PCLK)
    dut.PRESETn.value = 1
    stats = {"compared": 0, "mism": 0, "samples_selected": 0, "avg_flag_cycles": 0, "zcr_flag_cycles": 0, "vad_cycles": 0, "full_cycles": 0}
    script = []
    def config():
        cfg = rng.choice([0x203, 0x20B, 0x207, 0x60F, 0xA0B, 0xE07, 0x183, 0x103, 0x0C7, 0x3F7]) ^ (rng.getrandbits(2) << 10)
        return [(0x10, 0), (0x4, rng.choice([0, 0, 1])), (0x14, cfg), (0x8, rng.choice([0, 1, 4, 64, 0x10000, 0x1FFFFFF])),
                (0xC, rng.choice([0, 1, 3, 8, 20])), (0xFE04, rng.randrange(16)), (0xFF00, 0x3F), (0x10, rng.choice([0xF, 0xF, 0xD, 0x7, 0xB]))]
    sdi_mode = 0
    for cyc in range(400000):
        if cyc % 25000 == 0:
            if (cyc // 25000) % 3 == 2:     # exact case: constant data, 32-bit samples, both channels, thresholds 0 and 1
                script += [(a, 1, d) for a, d in [(0x10, 0), (0x4, 0), (0x14, rng.choice([0x203, 0x603, 0x207, 0x607])), (0x8, rng.choice([0, 1])),
                           (0xC, 0), (0xFF00, 0x3F), (0x10, 0xF)]]
                sdi_mode = rng.choice([3, 3, 4])
            else:
                script += [(a, 1, d) for a, d in config()]; sdi_mode = rng.randrange(5)   # 3, 4: constant 1 / 0
        if not script and rng.random() < 0.08:
            r = rng.random()
            script.append(((0x0 if (cyc // 25000) % 2 == 0 else 0xFE00), 0, 0) if r < 0.6 else   # odd stretches never pop: the FIFO fills
             (0xFF0C, 1, rng.getrandbits(6)) if r < 0.7 else (0xFE08, 1, 1) if r < 0.72 else (rng.choice([0xFF08, 0xFF04, 0xFE00, 0x0]), 0, 0))
        a, w, d = script[0] if script else (rng.choice(ADDRS), 0, 0)
        ph = getattr(stable_configs, "_ph", 0)
        psel, pen = (1, 0) if (script and ph == 0) else (1, 1) if (script and ph == 1) else (0, 0)
        sdi = (rng.getrandbits(1) if sdi_mode == 0 else int(rng.random() < 0.9) if sdi_mode == 1 else int(rng.random() < 0.1) if sdi_mode == 2 else int(sdi_mode == 3))
        for k, v in (("PSEL", psel), ("PENABLE", pen), ("PWRITE", w), ("PADDR", a), ("PWDATA", d), ("sdi", sdi)):
            getattr(dut, k).value = v
        await Timer(1, "ns")
        for name, want in (("PRDATA", m.prdata(a)), ("IRQ", int((m.RIS & m.IM) != 0)), ("sck", m.sck), ("ws", m.ws)):
            if want is None: continue
            got = getattr(dut, name).value; got = int(got) if got.is_resolvable else None
            stats["compared"] += 1
            if got != want:
                stats["mism"] += 1
                if stats["mism"] <= 10: dut._log.error(f"cycle {cyc}: {name} rtl={got} spec={want} addr={a:#x} CFG={m.CFG:#x} sum={m.sum:#x} zcr={m.zcr}")
        fl = m.flags(); stats["avg_flag_cycles"] += fl[3]; stats["zcr_flag_cycles"] += fl[4]; stats["vad_cycles"] += fl[5]; stats["full_cycles"] += m.full
        await RisingEdge(dut.PCLK)
        stats["samples_selected"] += bool(m.rdy and m.selected())
        m.edge(psel, pen, w, a, d, sdi)
        if script:
            if ph == 0: stable_configs._ph = 1
            else: stable_configs._ph = 0; script.pop(0)
        await FallingEdge(dut.PCLK)
    dut._log.info(f"RESULT2 {stats}")
    assert stats["mism"] == 0
