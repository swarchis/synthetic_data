"""Spec check for ef_psram: a cycle model written from designs/ef_psram/spec.md alone, compared with the
golden RTL every cycle on HREADYOUT, HRDATA (known bits), sck, ce_n, dout and douten, driven by a legal
AHB-Lite master (scripts/drivers.py AhbLiteDriver) with random memory data on din and random resets.

    docker run --rm --network none -v "$(pwd)":/work -w /work/scripts/spec_checks rtl-env \\
        make -f Makefile.ef_psram

Result on 2026-11-06: 359,907 values compared (HRDATA on 59,907 cycles), 0 mismatches, all four MODE
values used. Broken variants (QSPI data nibble order; upper-half read lane) mismatch 1,750 and 6,816 times.
"""
import json
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from drivers import AhbLiteDriver

M32 = 0xFFFFFFFF


class Model:
    def __init__(s):
        s.data = [(0, 0)] * 4          # (value, known-bit mask) per byte; no reset
        s.data_i = None                # no reset
        s.reset()

    def reset(s):
        s.lsel = s.laddr = s.lwrite = s.ltrans = 0; s.lsize = 0
        s.last_ap = 0; s.data_cfg = 0; s.rd_wr = 0; s.wstate = 0; s.hreadyout = 1
        s.RD, s.WR, s.EQ, s.XQ, s.WS, s.MODE, s.ENT, s.EXT = 0x03, 0x02, 0x35, 0xF5, 0, 0, 0, 0
        s.estate = 0; s.sck = 0; s.ce_n = 1; s.counter = 0

    def comb(s):
        qpi, qspi = (s.MODE >> 1) & 1, s.MODE & 1; quad = qpi | qspi
        size = {0: 1, 1: 2, 2: 4}.get(s.lsize, 4)
        short = s.ENT | s.EXT
        cmd = s.EQ if s.ENT else s.XQ if s.EXT else (s.RD if s.rd_wr else s.WR)
        ws = (2 if qpi else 8) + (6 if quad else 24)
        ds = (ws + (s.WS if s.rd_wr else 0)) & 0xFF
        fc = 8 if short else (ds + (2 if quad else 8) * size) & 0xFF
        return dict(qpi=qpi, qspi=qspi, quad=quad, size=size, short=short, cmd=cmd, ds=ds, fc=fc, done=int(s.counter == fc))

    def sized(s):
        if s.data_i is None: return None
        d, a = s.data_i, s.laddr & 3
        if s.lsize == 2: return d
        if s.lsize == 1: return (d >> 16) & 0xFFFF if (a >> 1) & 1 else d & 0xFFFF
        if s.lsize == 0: return (d >> (8 * a)) & 0xFF
        return (d >> 24) & 0xFF

    def hrdata(s):
        v = sum(s.data[i][0] << (8 * i) for i in range(4)); k = sum(s.data[i][1] << (8 * i) for i in range(4)); a = s.laddr & 3
        if s.lsize == 2: return v, k
        if s.lsize == 1: return ((v & 0xFFFF) << 16, (k & 0xFFFF) << 16) if (a >> 1) & 1 else (v & 0xFFFF, (k & 0xFFFF) | 0xFFFF0000)
        sh = 8 * a if s.lsize == 0 else 24
        return (v & 0xFF) << sh, ((k & 0xFF) << sh) | (M32 & ~(0xFF << sh))

    def pins(s):
        c = s.comb(); n = s.counter; cmd = c["cmd"]; addr = s.laddr & 0x7FFFFF; d = s.sized()
        nib = lambda k: None if d is None else (d >> k) & 0xF
        dnibs = [4, 0, 12, 8, 20, 16, 28, 24]
        anibs = [20, 16, 12, 8, 4, 0]
        if c["qpi"]:
            dout = (cmd >> 4) & 0xF if n == 0 else cmd & 0xF if n == 1 else (addr >> anibs[n - 2]) & 0xF if n < 8 else nib(dnibs[n - 8]) if n < 16 else 0
        elif c["qspi"]:
            dout = (cmd >> (7 - n)) & 1 if n < 8 else (addr >> anibs[n - 8]) & 0xF if n < 14 else nib(dnibs[n - 14]) if n < 22 else 0
        else:
            if n < 8: dout = (cmd >> (7 - n)) & 1
            elif n < 32: dout = (addr >> (31 - n)) & 1
            else:
                k = 39 - n if n < 40 else 55 - n if n < 48 else 71 - n if n < 56 else 87 - n if n < 64 else 0
                dout = None if d is None else (d >> k) & 1
        has_wait = int(s.WS != 0 and s.rd_wr)
        if c["qpi"]:
            en = 0xF if n < 8 else 0 if (n < c["ds"] and has_wait) else 0 if s.rd_wr else 0xF
        elif c["qspi"]:
            en = 1 if n < 8 else 0xF if n < 14 else 0 if (n < c["ds"] and has_wait) else 0 if s.rd_wr else 0xF
        else:
            en = 1
        return dout, en

    def edge(s, i, din):
        c = s.comb(); done = c["done"]
        ap = int((i["HTRANS"] >> 1) & 1 and i["HSEL"] and i["HREADY"])
        start = int(s.last_ap and s.wstate == 0 and s.data_cfg)
        n = {}
        if i["HREADY"]: n.update(lsel=i["HSEL"], laddr=i["HADDR"], lwrite=i["HWRITE"], ltrans=i["HTRANS"])
        if (i["HTRANS"] >> 1) & 1 and s.hreadyout: n["lsize"] = i["HSIZE"]
        n["last_ap"] = ap
        if ap: n["data_cfg"] = int(not (i["HADDR"] >> 23) & 1)
        if s.last_ap: n["data_i"] = i["HWDATA"] & M32
        if (s.ltrans >> 1) & 1: n["rd_wr"] = int(not s.lwrite)
        cfg_side = (s.laddr >> 23) & 1
        if s.wstate == 0:
            n["wstate"] = 1 if start else 0
            n["hreadyout"] = 0 if (s.last_ap or ap) else 1
        else:
            n["wstate"] = 0 if (done and not cfg_side) else 1
            n["hreadyout"] = 1 if (done and not cfg_side) else 0
        if cfg_side and s.lwrite and s.lsel and (s.ltrans >> 1) & 1:
            w = i["HWDATA"]
            for bit, name, mask in ((8, "RD", 0xFF), (9, "WR", 0xFF), (10, "EQ", 0xFF), (11, "XQ", 0xFF), (12, "WS", 0xF), (13, "MODE", 3), (14, "ENT", 1), (15, "EXT", 1)):
                if (s.laddr >> bit) & 1: n[name] = w & mask
        n["estate"] = (1 if start else 0) if s.estate == 0 else (0 if done else 1)
        n["sck"] = 0 if done else (1 - s.sck if s.ce_n == 0 else s.sck)
        n["ce_n"] = 1 if done else (0 if s.estate == 1 else 1)
        n["counter"] = (s.counter + 1) & 0xFF if (s.sck and not done) else (0 if s.estate == 0 else s.counter)
        if c["ds"] <= s.counter <= c["fc"] and s.sck:
            k = (s.counter - c["ds"]) >> (1 if c["quad"] else 3)
            if k <= 3:
                v, m = s.data[k]
                if c["quad"]: v, m = ((v << 4) | (din & 0xF)) & 0xFF, ((m << 4) | 0xF) & 0xFF
                else: v, m = ((v << 1) | ((din >> 1) & 1)) & 0xFF, ((m << 1) | 1) & 0xFF
                s.data = s.data[:k] + [(v, m)] + s.data[k + 1:]
        for key, val in n.items(): setattr(s, key, val)


@cocotb.test()
async def spec_vs_rtl(dut):
    rng = random.Random(51)
    cfg = json.loads((Path(__file__).resolve().parent.parent.parent / "designs/ef_psram/diff.json").read_text())
    drv = AhbLiteDriver(cfg["ahb"], rng)
    cocotb.start_soon(Clock(dut.HCLK, 10, "ns").start())
    m = Model()
    dut.HRESETn.value = 0; dut.din.value = 0
    for k, v in drv.step({"HREADYOUT": 1}).items(): getattr(dut, k).value = v
    await FallingEdge(dut.HCLK); await FallingEdge(dut.HCLK)
    dut.HRESETn.value = 1; drv.reset()
    stats = {"compared": 0, "mism": 0, "data_transfers": 0, "modes": set()}
    for cyc in range(60000):
        if rng.random() < 0.0003:
            dut.HRESETn.value = 0; await Timer(1, "ns"); m.reset(); drv.reset()
            await RisingEdge(dut.HCLK); m.reset(); await FallingEdge(dut.HCLK); dut.HRESETn.value = 1
        i = drv.step({"HREADYOUT": m.hreadyout}); din = rng.getrandbits(4)
        for k, v in i.items(): getattr(dut, k).value = v
        dut.din.value = din
        await Timer(1, "ns")
        dout, en = m.pins(); hv, hk = m.hrdata()
        def chk(name, want, mask=None):
            if want is None: return
            got = getattr(dut, name).value
            if not got.is_resolvable and mask is None: got = None
            else:
                got = int(got) if got.is_resolvable else None
                if mask is not None and got is not None: got, want = got & mask, want & mask
            stats["compared"] += 1
            if got != want:
                stats["mism"] += 1
                if stats["mism"] <= 10:
                    dut._log.error(f"cycle {cyc}: {name} rtl={got} spec={want} cnt={m.counter} mode={m.MODE} rd={m.rd_wr} size={m.lsize} ws={m.WS} ent={m.ENT}/{m.EXT}")
        chk("HREADYOUT", m.hreadyout); chk("sck", m.sck); chk("ce_n", m.ce_n); chk("dout", dout); chk("douten", en)
        if hk == M32 or True:
            got = dut.HRDATA.value
            if got.is_resolvable:
                chk("HRDATA", hv, hk); stats["hrdata_compared"] = stats.get("hrdata_compared", 0) + 1
        stats["modes"].add(m.MODE); stats["data_transfers"] += int(m.ce_n == 1 and False)
        await RisingEdge(dut.HCLK)
        m.edge(i, din)
        await FallingEdge(dut.HCLK)
    stats["modes"] = sorted(stats["modes"])
    dut._log.info(f"RESULT {stats}")
    assert stats["mism"] == 0
