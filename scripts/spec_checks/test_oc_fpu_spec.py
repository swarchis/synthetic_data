"""Spec check for oc_fpu: exact IEEE 754 binary32 arithmetic plus the deviations and flag rules of
designs/oc_fpu/spec.md, compared with the golden RTL on random traffic (specials, subnormals, extremes,
reserved operation codes mixed in) for every output the spec defines.

    docker run --rm --network none -v "$(pwd)":/work -w /work/scripts/spec_checks rtl-env \
        make -f Makefile.oc_fpu

Result on 2026-11-06: 681,494 specified output values compared, 0 mismatches (80,000 operations;
11,454 reserved operation codes and 11,495 both-infinite or other unspecified operations mixed in and
skipped). With the division tie rule removed, the same check mismatches 4 times. This check found one
spec error before release (a tie-rule exception), but its random traffic rarely makes exact or boundary
results: Terra's test suites then exposed deviations in tiny multiply/divide results, and a structured
boundary sweep (144,988 operations) mapped them; the spec now leaves that range unspecified (deviation 2).
"""

from fractions import Fraction as F
BIAS, MANT = 127, 23
MAXF = (2 - F(1, 2**23)) * F(2)**127
def decode(b):
    s, e, m = b >> 31, (b >> 23) & 0xFF, b & 0x7FFFFF
    if e == 0xFF: return ("nan", s, m) if m else ("inf", s, 0)
    if e == 0: return ("num", s, F(m, 2**23) * F(2)**(1 - BIAS))
    return ("num", s, (1 + F(m, 2**23)) * F(2)**(e - BIAS))
def round_to(v, rmode):
    """v: exact Fraction (nonzero). returns (bits, inexact, overflow, tiny)."""
    s = 1 if v < 0 else 0; a = abs(v)
    e = a.numerator.bit_length() - a.denominator.bit_length()
    while F(2)**e > a: e -= 1
    while F(2)**(e + 1) <= a: e += 1
    e = max(e, 1 - BIAS)                      # subnormal range uses the min exponent
    ulp = F(2)**(e - MANT)
    q = a / ulp; n = q.numerator // q.denominator; rem = q - n
    up = False
    if rem:
        if rmode == 0: up = rem > F(1, 2) or (rem == F(1, 2) and n % 2 == 1)
        elif rmode == 1: up = False
        elif rmode == 2: up = (s == 0)
        else: up = (s == 1)
    n += up
    r = n * ulp; inexact = rem != 0
    tiny = a < F(2)**(1 - BIAS)
    if r > MAXF:
        to_inf = rmode == 0 or (rmode == 2 and s == 0) or (rmode == 3 and s == 1)
        return ((s << 31) | (0x7F800000 if to_inf else 0x7F7FFFFF), True, True, tiny)
    if r == 0: return (s << 31, inexact, False, tiny)
    if r < F(2)**(1 - BIAS):                   # subnormal
        m = int(r / F(2)**(1 - BIAS - MANT)); return ((s << 31) | m, inexact, False, tiny)
    E = r.numerator.bit_length() - r.denominator.bit_length()
    while F(2)**E > r: E -= 1
    while F(2)**(E + 1) <= r: E += 1
    m = int((r / F(2)**E - 1) * 2**MANT)
    return ((s << 31) | ((E + BIAS) << 23) | m, inexact, False, tiny)
def op(code, a, b, rmode):
    """returns dict(out=bits or 'nan', ine, ovf, tiny, dbz, inv)."""
    A, B = decode(a), decode(b)
    if code == 1: B = (B[0], 1 - B[1], B[2])
    res = dict(ine=False, ovf=False, tiny=False, dbz=False, inv=False)
    if A[0] == "nan" or B[0] == "nan": return {**res, "out": "nan"}
    if code in (0, 1):
        if A[0] == "inf" and B[0] == "inf":
            if A[1] != B[1]: return {**res, "out": "nan", "inv": True}
            return {**res, "out": (A[1] << 31) | 0x7F800000}
        if A[0] == "inf": return {**res, "out": (A[1] << 31) | 0x7F800000}
        if B[0] == "inf": return {**res, "out": (B[1] << 31) | 0x7F800000}
        va = A[2] * (-1 if A[1] else 1); vb = B[2] * (-1 if B[1] else 1); v = va + vb
        if v == 0:
            if va == 0 and vb == 0 and A[1] == B[1]: s = A[1]
            else: s = 1 if rmode == 3 else 0
            return {**res, "out": s << 31}
    else:
        s = A[1] ^ B[1]
        if code == 2:
            if (A[0] == "inf" and B[0] == "num" and B[2] == 0) or (B[0] == "inf" and A[0] == "num" and A[2] == 0): return {**res, "out": "nan", "inv": True}
            if A[0] == "inf" or B[0] == "inf": return {**res, "out": (s << 31) | 0x7F800000}
            v = A[2] * B[2] * (-1 if s else 1)
            if v == 0: return {**res, "out": s << 31}
        else:
            if A[0] == "inf" and B[0] == "inf": return {**res, "out": "nan", "inv": True}
            if A[0] == "num" and A[2] == 0 and B[0] == "num" and B[2] == 0: return {**res, "out": "nan", "inv": True}
            if A[0] == "inf": return {**res, "out": (s << 31) | 0x7F800000}
            if B[0] == "inf": return {**res, "out": s << 31}
            if B[2] == 0: return {**res, "out": (s << 31) | 0x7F800000, "dbz": True}
            v = A[2] / B[2] * (-1 if s else 1)
            if v == 0: return {**res, "out": s << 31}
    bits, ine, ovf, tiny = round_to(v, rmode)
    return {**res, "out": bits, "ine": ine, "ovf": ovf, "tiny": tiny}


# ---- rules of designs/oc_fpu/spec.md on top of exact IEEE 754 ----
from fractions import Fraction as Fr
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

def cls(x):
    e, m = (x >> 23) & 0xFF, x & 0x7FFFFF
    return "nan" if e == 255 and m else "inf" if e == 255 else "zero" if e == 0 and m == 0 else "sub" if e == 0 else "norm"

def spec(o, a, b, r):
    """Returns dict of specified outputs (missing key = not specified)."""
    if o > 3 or (cls(a) == "inf" and cls(b) == "inf"): return {}
    m = op(o, a, b, r); s = {}
    if m["out"] == "nan":
        n = (cls(a) == "nan") + (cls(b) == "nan")
        s["out_mag"] = 0x7FC00001
        if n == 1: s["out_sign"] = (a if cls(a) == "nan" else b) >> 31
        s.update(inf=0, qnan=1, zero=0, ine=0, overflow=0, underflow=0)
    else:
        out = m["out"]
        tiny_region = (o in (2, 3) and cls(a) in ("norm", "sub") and cls(b) in ("norm", "sub")
                       and (cls(out) in ("zero", "sub") or out & 0x7FFFFFFF == 0x00800000))   # deviation 2
        if not tiny_region:
            s["out_mag"] = out & 0x7FFFFFFF; s["out_sign"] = out >> 31; s["zero"] = int(cls(out) == "zero")
            if not (o == 3 and cls(a) == "sub"): s["underflow"] = int(m["tiny"] and m["ine"])
        s.update(inf=int(cls(out) == "inf"), qnan=0, overflow=int(m["ovf"]))
        if not (o == 3 and cls(a) == "sub"): s["ine"] = int(m["ine"] or m["ovf"])
    s["snan"] = int(any(cls(x) == "nan" and not (x >> 22) & 1 for x in (a, b)))
    s["div_by_zero"] = int(o == 3 and cls(b) == "zero" and cls(a) in ("norm", "sub"))
    return s

SPECIALS = [0, 0x80000000, 0x7F800000, 0xFF800000, 0x7FC00000, 0xFFC00321, 0x7F800001, 0xFF812345, 0x3F800000,
            0x7F7FFFFF, 0x00800000, 0x00000001, 0x007FFFFF, 0x80000005, 0x40000000, 0x3FC00000]

@cocotb.test()
async def spec_vs_rtl(dut):
    rng = random.Random(21)
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    def operand():
        k = rng.random()
        if k < 0.2: return rng.choice(SPECIALS)
        if k < 0.35: return (rng.getrandbits(1) << 31) | rng.getrandbits(23)
        if k < 0.5: return (rng.getrandbits(1) << 31) | (rng.choice([rng.randrange(1, 30), rng.randrange(225, 255)]) << 23) | rng.getrandbits(23)
        if k < 0.6: return rng.getrandbits(32)
        return (rng.getrandbits(1) << 31) | (rng.randrange(90, 165) << 23) | rng.getrandbits(23)
    ops = []
    stats = {"compared": 0, "skipped_unspecified": 0, "reserved_ops": 0, "mism": 0}
    names = ("inf", "snan", "qnan", "ine", "overflow", "underflow", "zero", "div_by_zero")
    for c in range(80000):
        o = rng.choice([0, 1, 2, 3] * 6 + [4, 5, 6, 7]); stats["reserved_ops"] += o > 3
        a, b, r = operand(), operand(), rng.randrange(4)
        dut.opa.value = a; dut.opb.value = b; dut.fpu_op.value = o; dut.rmode.value = r
        ops.append((o, a, b, r))
        await FallingEdge(dut.clk)
        if c < 3: continue
        o2, a2, b2, r2 = ops[c - 3]
        s = spec(o2, a2, b2, r2)
        if not s: stats["skipped_unspecified"] += 1; continue
        got = {n: int(getattr(dut, n).value) for n in names}; out = int(dut.out.value)
        got["out_mag"] = out & 0x7FFFFFFF; got["out_sign"] = out >> 31
        for k, v in s.items():
            stats["compared"] += 1
            if got[k] != v:
                stats["mism"] += 1
                if stats["mism"] <= 10: dut._log.error(f"op{o2} r{r2} a={a2:08x} b={b2:08x}: {k} rtl={got[k]:#x} spec={v:#x}")
    dut._log.info(f"RESULT {stats}")
    assert stats["mism"] == 0
