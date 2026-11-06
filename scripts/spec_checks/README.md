# Spec checks

Each check is a cycle model of one design written from its `spec.md` alone, run against the golden RTL
on random traffic, comparing every output the spec defines on every clock. A mismatch means the spec
is wrong or incomplete. Run them in the `rtl-env` container (no network needed):

    docker run --rm --network none -v "$(pwd)":/work -w /work/scripts/spec_checks rtl-env make -f Makefile.oc_spi
    docker run --rm --network none -v "$(pwd)":/work -w /work/scripts/spec_checks rtl-env make -f Makefile.oc_aes
    docker run --rm --network none -v "$(pwd)":/work -w /work/scripts/spec_checks rtl-env make -f Makefile.oc_fpu
    docker run --rm --network none -v "$(pwd)":/work -w /work/scripts/spec_checks rtl-env make -f Makefile.ef_i2s
    docker run --rm --network none -v "$(pwd)":/work -w /work/scripts/spec_checks rtl-env make -f Makefile.ef_psram

| Design | Values compared | Mismatches | Spec errors it caught before release |
|---|---|---|---|
| oc_spi | 299,975 | 0 | none |
| oc_aes | 1,268 encryptions + 1,905 decryptions | 0 | decrypt restart rule (the decryptor must be idle) |
| oc_fpu | 656,195 | 0 | a tie-rule exception; later, model failures plus a 144,988-operation boundary sweep showed tiny multiply/divide results needed to be left unspecified |
| ef_i2s | 299,772 + 1,599,762 | 0 | none (needed forced constant-data runs to see the `abs` rule) |
| ef_psram | 359,907 | 0 | none |

Each check was also run with one rule deliberately broken, and it reported mismatches, so it can see
errors of that kind.

**Limits:** random traffic rarely reaches boundary cases (exact results, values right at a threshold);
for datapaths, also run a structured boundary sweep and forced scenarios. And a check proves that *our reading* of the spec matches the design. It cannot show that the
wording is unambiguous to someone else. The oc_spi spec passed its check but was still ambiguous about
whether a FIFO write stores its byte while the core is disabled; a model's failing test suite exposed
that, and the spec was fixed (see `results/testgen_failure_audit.md`).
