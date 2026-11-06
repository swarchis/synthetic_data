"""Spec check for oc_spi: a cycle model written from designs/oc_spi/spec.md alone, compared with the
golden RTL on every output, every cycle, under random Wishbone traffic, random MISO and random resets.
A mismatch means the spec is wrong or incomplete. Run (no network needed):

    docker run --rm --network none -v "$(pwd)":/work -w /work/scripts/spec_checks rtl-env \
        make -f Makefile.oc_spi

Result on 2026-11-05: 299,975 output values compared, 0 mismatches (641 transfers, 325 SPIF events,
882 write-FIFO overruns, 866 empty read-FIFO reads, 405 CPHA = 1 starts). The same model with one rule
changed (CPHA start edge, or refusing writes to a full FIFO) mismatches within 75 cycles."""
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

NTAB = [0, 1, 3, 15, 31, 7, 63, 127, 255, 511, 1023, 2047]


class Fifo:
    def __init__(s):
        s.mem = [None] * 4; s.wp = s.rp = 0; s.gb = 0

    def empty(s): return s.wp == s.rp and not s.gb
    def full(s): return s.wp == s.rp and s.gb == 1
    def head(s): return s.mem[s.rp]

    def edge(s, we, din, re, clr, srst):
        if we: s.mem[s.wp] = din
        gb = s.gb
        if srst or clr: gb = 0
        elif we and ((s.wp + 1) & 3) == s.rp: gb = 1
        elif re: gb = 0
        if clr: wp = rp = 0
        else:
            wp = (s.wp + 1) & 3 if we else s.wp
            rp = (s.rp + 1) & 3 if re else s.rp
        s.wp, s.rp, s.gb = wp, rp, gb


class Model:
    def __init__(s):
        s.spcr, s.sper, s.ack = 0x10, 0, 0
        s.wf, s.rf = Fifo(), Fifo()
        s.dat_o = None; s.spif = s.wcol = None; s.inta = None
        s.state = None; s.bcnt = None; s.treg = None; s.sck = None; s.wfre = None; s.rfwe = None
        s.clkcnt = None; s.tcnt = None

    def areset(s):
        s.spcr, s.sper, s.ack = 0x10, 0, 0
        for f in (s.wf, s.rf): f.wp = f.rp = 0

    def spsr(s):
        return ((s.spif or 0) << 7) | ((s.wcol or 0) << 6) | (s.wf.full() << 3) | (s.wf.empty() << 2) | (s.rf.full() << 1) | s.rf.empty()

    def mosi(s): return None if s.treg is None else (s.treg >> 7) & 1

    def edge(s, rst, cyc, stb, we, adr, dat, miso):
        spe = (s.spcr >> 6) & 1; spie = s.spcr >> 7; cpol = (s.spcr >> 3) & 1; cpha = (s.spcr >> 2) & 1
        icnt = s.sper >> 6; espr = ((s.sper & 3) << 2) | (s.spcr & 3)
        acc = cyc and stb; wr = acc and we
        wfwe = acc and adr == 2 and s.ack and we; rfre = acc and adr == 2 and s.ack and not we
        wfov = wfwe and s.wf.full()
        clear7 = wr and adr == 1 and (dat >> 7) & 1; clear6 = wr and adr == 1 and (dat >> 6) & 1
        tirq = s.tcnt == 0 and s.rfwe == 1
        s.transfers = getattr(s, 'transfers', 0) + int(s.rfwe == 1 and spe == 1)
        s.spif_sets = getattr(s, 'spif_sets', 0) + int(bool(tirq) and spe == 1)
        s.ovr = getattr(s, 'ovr', 0) + int(bool(wfov) and spe == 1)
        s.erd = getattr(s, 'erd', 0) + int(bool(rfre) and s.rf.empty() and spe == 1)
        s.c1 = getattr(s, 'c1', 0) + int(spe == 1 and s.state == 0 and not s.wf.empty() and cpha == 1)
        ena = s.clkcnt == 0
        # next values (all from pre-edge state)
        n = {}
        n["dat_o"] = {0: s.spcr, 1: s.spsr(), 2: s.rf.head(), 3: s.sper}[adr]
        n["inta"] = None if s.spif is None else (s.spif & spie)
        if not spe:
            n.update(spif=0, wcol=0, state=0, bcnt=0, treg=0, wfre=0, rfwe=0, sck=0, tcnt=icnt)
        else:
            n["spif"] = None if s.spif is None else int((tirq or s.spif) and not clear7)
            n["wcol"] = None if s.wcol is None else int((wfov or s.wcol) and not clear6)
            n["tcnt"] = s.tcnt
            if s.rfwe: n["tcnt"] = (s.tcnt - 1) if s.tcnt else icnt
            n.update(state=s.state, bcnt=s.bcnt, treg=s.treg, sck=s.sck, wfre=0, rfwe=0)
            if s.state == 0:
                n.update(bcnt=7, treg=s.wf.head(), sck=cpol)
                if not s.wf.empty():
                    n.update(wfre=1, state=1)
                    if cpha: n["sck"] = 1 - s.sck
            elif s.state == 1:
                if ena: n.update(sck=1 - s.sck, state=3)
            elif s.state == 3:
                if ena:
                    n["treg"] = ((s.treg << 1) & 0xFF) | miso
                    n["bcnt"] = (s.bcnt - 1) & 7
                    if s.bcnt == 0: n.update(state=0, sck=cpol, rfwe=1)
                    else: n.update(state=1, sck=1 - s.sck)
        if s.clkcnt is not None and spe and s.clkcnt != 0 and s.state != 0: n["clkcnt"] = s.clkcnt - 1
        else: n["clkcnt"] = NTAB[espr] if espr < 12 else s.clkcnt
        # FIFOs
        s.wf.edge(wfwe, dat, s.wfre == 1, not spe, not rst)
        s.rf.edge(s.rfwe == 1, s.treg, rfre, not spe, not rst)
        # registers
        if wr and adr == 0: s.spcr = dat | 0x10
        if wr and adr == 3: s.sper = dat
        s.ack = int(acc and not s.ack)
        for k, v in n.items(): setattr(s, k, v)


def val(sig):
    v = sig.value
    return int(v) if v.is_resolvable else None


@cocotb.test()
async def spec_vs_rtl(dut):
    rng = random.Random(int(dut._name.__hash__() & 0) + 7)
    cocotb.start_soon(Clock(dut.clk_i, 10, "ns").start())
    m = Model()
    for x in ("cyc_i", "stb_i", "we_i", "adr_i", "dat_i", "miso_i"): getattr(dut, x).value = 0
    dut.rst_i.value = 0
    await FallingEdge(dut.clk_i)
    m.areset()
    await RisingEdge(dut.clk_i); m.edge(0, 0, 0, 0, 0, 0, 0)
    mismatches, checked = 0, 0
    cur = None; left = 0
    for cyc in range(60000):
        await FallingEdge(dut.clk_i)
        # compare outputs (model value None = undefined, skip)
        for name, mv in (("dat_o", m.dat_o), ("ack_o", m.ack), ("inta_o", m.inta), ("sck_o", m.sck), ("mosi_o", m.mosi())):
            rv = val(getattr(dut, name))
            if mv is not None and rv is not None:
                checked += 1
                if rv != mv:
                    mismatches += 1
                    if mismatches <= 10:
                        dut._log.error(f"cycle {cyc}: {name} rtl={rv} spec={mv} state={m.state} spcr={m.spcr:#x} sper={m.sper:#x}")
        # drive next inputs
        rst = 0 if rng.random() < 0.0005 else 1
        if left:
            left -= 1
        elif rng.random() < 0.5:
            cur = None
        else:
            adr = rng.choices([0, 1, 2, 3], [2, 2, 6, 1])[0]
            we = int(rng.random() < 0.6)
            dat = rng.getrandbits(8)
            if adr == 0 and rng.random() < 0.8: dat = rng.choice([0x50, 0x54, 0x58, 0x5C, 0xD0, 0xD1, 0x52, 0x4F, 0x10, 0xC5])
            if adr == 3 and rng.random() < 0.7: dat = rng.choice([0x00, 0x40, 0x80, 0xC0, 0x01, 0x41, 0x03, 0x02])
            cur = (adr, we, dat); left = 1
        cyc_, stb, we, adr, dat = (1, 1, cur[1], cur[0], cur[2]) if cur else (rng.randrange(2), 0, rng.randrange(2), rng.randrange(4), rng.getrandbits(8))
        miso = rng.randrange(2)
        dut.cyc_i.value = cyc_; dut.stb_i.value = stb; dut.we_i.value = we; dut.adr_i.value = adr; dut.dat_i.value = dat
        dut.miso_i.value = miso; dut.rst_i.value = rst
        await Timer(1, "ns")
        if not rst: m.areset()
        await RisingEdge(dut.clk_i)
        m.edge(rst, cyc_, stb, we, adr, dat, miso)
        if not rst: m.areset()
    dut._log.info(f"checked {checked} output values, {mismatches} mismatches; transfers {m.transfers} spif_sets {m.spif_sets} overruns {m.ovr} empty_reads {m.erd} cpha1_starts {m.c1}")
    assert mismatches == 0
