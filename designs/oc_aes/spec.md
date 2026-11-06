# aes_unit: AES-128 encryption and decryption unit

An AES-128 unit with two independent channels: an encryptor and a decryptor. Each computes one round per clock and finishes a 128-bit block in 11 clocks. The cipher is AES as defined in FIPS-197 with a 128-bit key (10 rounds): the encryptor computes Cipher(text_in, key), the decryptor computes InvCipher(text_in, key), so decrypting a ciphertext with the key that produced it returns the plaintext.

The file contains: `aes_unit` (top, two channels side by side), `aes_cipher_top` (encryptor), `aes_inv_cipher_top` (decryptor), `aes_key_expand_128` (round-key generator), `aes_rcon` (round constants), `aes_sbox` and `aes_inv_sbox` (byte substitution tables). Tests use only the top-level ports. The cipher modules are the OpenCores AES core by Rudolf Usselmann.

## Ports

| Port | Dir | Width | Meaning |
|---|---|---|---|
| clk | in | 1 | Clock. Everything happens on its rising edge. |
| rst_n | in | 1 | Reset, active low, **synchronous** (see "Reset"). |
| enc_ld | in | 1 | Start an encryption (sampled at a rising edge). |
| enc_key | in | 128 | Encryption key, sampled at the enc_ld edge. |
| enc_text_in | in | 128 | Plaintext block, sampled at the enc_ld edge. |
| enc_done | out | 1 | One-clock pulse: the ciphertext is on enc_text_out. |
| enc_text_out | out | 128 | Ciphertext (valid only while enc_done = 1). |
| dec_kld | in | 1 | Load a decryption key (sampled at a rising edge). |
| dec_ld | in | 1 | Start a decryption (sampled at a rising edge). |
| dec_key | in | 128 | Decryption key, sampled at the dec_kld edge. |
| dec_text_in | in | 128 | Ciphertext block, sampled at the dec_ld edge. |
| dec_done | out | 1 | One-clock pulse: the plaintext is on dec_text_out. |
| dec_text_out | out | 128 | Plaintext (valid only while dec_done = 1). |

"Sampled at edge E" means the input's value just before rising edge E. All outputs are registers.

## Data format

Blocks and keys are 128-bit values written most significant byte first: bits [127:120] are byte 0 of the FIPS-197 input, key or output, bits [119:112] byte 1, and so on to bits [7:0], byte 15. So the hex strings in FIPS-197 read directly as 128-bit numbers. Example (FIPS-197 Appendix C.1):

- key = 0x000102030405060708090a0b0c0d0e0f
- plaintext = 0x00112233445566778899aabbccddeeff
- ciphertext = 0x69c4e0d86a7b0430d8cdb78070b4c55a

## Encryption channel

- An encryption starts at every rising edge where enc_ld = 1. That edge samples enc_key and enc_text_in.
- If enc_ld is sampled at edge E and not again at edges E+1 to E+11, enc_done is 1 for exactly one clock, from edge E+11 to edge E+12, and during that clock enc_text_out holds the ciphertext of the block and key sampled at E.
- An enc_ld at any of the edges E+1 to E+11 abandons the earlier encryption (it never produces enc_done) and starts a new one, timed from the new edge. enc_ld may be asserted at any time; there is no busy state to wait for.
- Exact rule: a 4-bit counter is 0 after reset; at each edge it becomes 0 if rst_n = 0, else 11 if enc_ld = 1, else it decrements if it is not 0. enc_done (a register with no reset) becomes 1 at an edge exactly when the counter was 1 before the edge and enc_ld = 0 at the edge.

## Decryption channel

The decryptor precomputes the 11 round keys from the key into an internal buffer, then uses them in reverse order. The buffer is not cleared by reset.

- **Key load.** A key load starts at every rising edge where dec_kld = 1, sampling dec_key. The round keys take 12 clocks to compute: if dec_kld is sampled at edge K, a decryption started at edge K+12 or later uses the new key. There is no signal that the key is ready.
- **Decryption.** A decryption starts at a rising edge E where dec_ld = 1, sampling dec_text_in. If dec_ld is not sampled again at edges E+1 to E+11, dec_done is 1 for exactly one clock, from edge E+11 to edge E+12, and during that clock dec_text_out holds the plaintext.
- **Protocol.** The result is only specified (correct InvCipher output) when (a) the last dec_kld before E was at edge E-12 or earlier, (b) the decryptor is idle just before edge E: its counter is 0 and dec_done is 0 (see the exact rule below), and (c) no dec_kld is sampled at edges E+1 to E+12. After a decryption started at E', the decryptor is idle again from edge E'+13 on, so in normal use: wait 12 clocks after a key load, and leave one clock after each dec_done before the next dec_ld or dec_kld. A dec_ld sampled while the decryptor is not idle (for example at E'+12, the edge that ends the dec_done clock) still runs and still produces a dec_done pulse by the exact rule, but its dec_text_out is unspecified, and the decryptor is idle again only after that pulse ends. Breaking these rules gives an unspecified dec_text_out (dec_done still follows the exact rule below).
- Exact rule for dec_done: a 4-bit counter (reset 0) and a `go` flag (reset 0). At each edge, the counter becomes 0 if rst_n = 0; else 0 if dec_done = 1 (before the edge); else 1 if dec_ld = 1; else counter + 1 if `go` = 1. `go` becomes 0 if rst_n = 0; else 1 if dec_ld = 1; else 0 if dec_done = 1. dec_done (a register with no reset) becomes 1 at an edge exactly when the counter was 11 before the edge and dec_ld = 0 at the edge.

## Reset

A rising edge with rst_n = 0 sets both channels' counters to 0 (and the decryptor's `go` to 0), which abandons operations in progress: they produce no done pulse, except that a done pulse whose condition holds at the reset edge itself still appears (the done registers follow the exact rules above and have no reset). Reset does not clear the decryptor's round-key buffer, the text outputs, or any data register. Reset does stop a decryption key load in progress (the buffer keeps whatever round keys were already written), so after a reset during a key load, load the key again before decrypting.

## Outputs outside the done clock

enc_text_out and dec_text_out change on every clock while the round logic runs; their values outside the clock where the matching done is 1 are not specified, and tests must not check them there.
