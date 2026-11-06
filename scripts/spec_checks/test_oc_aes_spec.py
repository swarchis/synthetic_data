"""Spec check for oc_aes: AES-128 (FIPS-197) and the timing rules of designs/oc_aes/spec.md, written from
the spec alone, compared with the golden RTL every cycle: enc_done and dec_done always, enc_text_out and
dec_text_out in the done clock whenever the spec defines them. Random traffic: encryptions with restarts,
decryptor key loads and decryptions that follow the spec's protocol, random synchronous resets.

    docker run --rm --network none -v "$(pwd)":/work -w /work/scripts/spec_checks rtl-env \\
        make -f Makefile.oc_aes

Result on 2026-11-05: 1,268 encryptions and 1,905 protocol-following decryptions checked, 0 mismatches
(264 protocol-breaking decryptions skipped as unspecified). With the done timing changed by one clock
the same model mismatches 4,066 times. This check found one spec error before release: the first draft
said "13 clocks after the previous dec_ld"; after a protocol-breaking dec_ld that is not enough, and the
spec now states the real condition (the decryptor must be idle).
"""
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

# ---- AES-128 from FIPS-197 ----
def _sbox():
    s, p, q = [0] * 256, 1, 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)          # p *= 3
        q ^= q << 1; q ^= q << 2; q ^= q << 4; q &= 0xFF                 # q /= 3
        if q & 0x80: q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        s[p] = (x ^ 0x63) & 0xFF
        if p == 1: break
    s[0] = 0x63
    return s
SBOX = _sbox(); INV = [0] * 256
for i, v in enumerate(SBOX): INV[v] = i
def xt(b): return ((b << 1) ^ (0x1B if b & 0x80 else 0)) & 0xFF
def mul(a, b):
    r = 0
    while b:
        if b & 1: r ^= a
        a, b = xt(a), b >> 1
    return r
def expand(key):
    w = [list(key[4 * i:4 * i + 4]) for i in range(4)]; rc = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [SBOX[b] for b in t[1:] + t[:1]]; t[0] ^= rc; rc = xt(rc)
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(11)]
def ark(s, k): return [a ^ b for a, b in zip(s, k)]
def shift(s, inv=False):
    o = [0] * 16
    for c in range(4):
        for r in range(4):
            src = (c + r) % 4 if not inv else (c - r) % 4
            o[4 * c + r] = s[4 * src + r]
    return o
def mix(s, m):
    o = []
    for c in range(4):
        col = s[4 * c:4 * c + 4]
        o += [mul(col[0], m[r][0]) ^ mul(col[1], m[r][1]) ^ mul(col[2], m[r][2]) ^ mul(col[3], m[r][3]) for r in range(4)]
    return o
M = [[2, 3, 1, 1], [1, 2, 3, 1], [1, 1, 2, 3], [3, 1, 1, 2]]
MI = [[14, 11, 13, 9], [9, 14, 11, 13], [13, 9, 14, 11], [11, 13, 9, 14]]
def to_b(x): return list(x.to_bytes(16, "big"))
def to_i(b): return int.from_bytes(bytes(b), "big")
def encrypt(pt, key):
    k = expand(to_b(key)); s = ark(to_b(pt), k[0])
    for r in range(1, 11):
        s = shift([SBOX[b] for b in s])
        if r < 10: s = mix(s, M)
        s = ark(s, k[r])
    return to_i(s)
def decrypt(ct, key):
    k = expand(to_b(key)); s = ark(to_b(ct), k[10])
    for r in range(9, -1, -1):
        s = [INV[b] for b in shift(s, inv=True)]
        s = ark(s, k[r])
        if r > 0: s = mix(s, MI)
    return to_i(s)
assert encrypt(0x00112233445566778899aabbccddeeff, 0x000102030405060708090a0b0c0d0e0f) == 0x69c4e0d86a7b0430d8cdb78070b4c55a
assert decrypt(0x69c4e0d86a7b0430d8cdb78070b4c55a, 0x000102030405060708090a0b0c0d0e0f) == 0x00112233445566778899aabbccddeeff


@cocotb.test()
async def spec_vs_rtl(dut):
    rng = random.Random(11)
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    # spec state
    ecnt, edone, eblk = 0, 0, None              # encryptor counter, done, (pt, key) of the latest ld
    dcnt, go, ddone, dblk = 0, 0, 0, None       # decryptor counter, go, done, (ct, key, valid) of the latest ld
    last_kld, last_dld, kload_ok_from, key = -100, -100, None, None
    stats = {"enc_checked": 0, "dec_checked": 0, "dec_unspecified": 0, "mism": 0}
    for s in ("enc_ld", "dec_kld", "dec_ld"): getattr(dut, s).value = 0
    dut.rst_n.value = 0
    hist = []
    for e in range(40000):
        # inputs for edge e
        rst = 0 if (e < 2 or rng.random() < 0.002) else 1
        eld = int(rng.random() < 0.08)
        ok_dld = e - last_dld >= 13 and key is not None and e - last_kld >= 12
        ok_kld = e - last_dld >= 13
        kld = int(ok_kld and rng.random() < 0.03)
        dld = int(not kld and ok_dld and rng.random() < 0.25)
        if rng.random() < 0.01 and e - last_dld >= 1: dld = int(not kld)        # occasionally break the protocol
        ek, ept, dk, dct = (rng.getrandbits(128) for _ in range(4))
        for n, v in (("rst_n", rst), ("enc_ld", eld), ("dec_kld", kld), ("dec_ld", dld), ("enc_key", ek), ("enc_text_in", ept), ("dec_key", dk), ("dec_text_in", dct)):
            getattr(dut, n).value = v
        hist.append((e, rst, eld, kld, dld)); hist = hist[-40:]
        await RisingEdge(dut.clk)
        # spec: next state (from values before the edge)
        n_edone = int(ecnt == 1 and not eld)
        n_ecnt = 0 if not rst else 11 if eld else (ecnt - 1 if ecnt else 0)
        n_ddone = int(dcnt == 11 and not dld)
        n_dcnt = 0 if not rst else 0 if ddone else 1 if dld else (dcnt + 1) & 15 if go else dcnt
        n_go = 0 if not rst else 1 if dld else 0 if ddone else go
        if eld: eblk = (ept, ek)
        if kld:
            key, last_kld = dk, e
        if not rst and key is not None and e - last_kld < 12: key = None   # reset stops a key load in progress
        if dld:
            valid = key is not None and e - last_kld >= 12 and dcnt == 0 and ddone == 0   # spec (a) and (b): idle before the edge
            dblk = (dct, key, valid); last_dld = e
        ecnt, edone, dcnt, go, ddone = n_ecnt, n_edone, n_dcnt, n_go, n_ddone
        await FallingEdge(dut.clk)
        # compare
        def chk(name, want):
            got = getattr(dut, name).value
            got = int(got) if got.is_resolvable else None
            if got != want:
                stats["mism"] += 1
                if stats["mism"] <= 8:
                    dut._log.error(f"edge {e}: {name} rtl={got if got is None else hex(got)} spec={hex(want)}")
                    if name == "dec_text_out": dut._log.error("HIST " + " ".join(f"{h[0]}:{'R' if not h[1] else ''}{'K' if h[3] else ''}{'D' if h[4] else ''}" for h in hist if (not h[1]) or h[3] or h[4]))
        if e < 2: continue
        chk("enc_done", edone); chk("dec_done", ddone)
        if edone:
            chk("enc_text_out", encrypt(*eblk)); stats["enc_checked"] += 1
        if ddone:
            if dblk[2]:
                chk("dec_text_out", decrypt(dblk[0], dblk[1])); stats["dec_checked"] += 1
            else:
                stats["dec_unspecified"] += 1
    dut._log.info(f"RESULT {stats}")
    assert stats["mism"] == 0
