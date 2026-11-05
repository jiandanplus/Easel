# Independent N3 G1 policy literal review

## Verdict

**Specification compliance: PASS for G1 literal freeze.** The candidate closes G1 from `profile-design/REVIEW.safe.md`, preserves the corrected F1 use-column semantics, and translates the frozen ten-link contract without an identified acceptance widening or unresolved numeric field.

**Design quality: PASS for G1 literal freeze.** The policy is one exact source-owned constant with closed descriptors and explicit serialization rules. The document distinguishes supplemental schema predicates from profile acceptance, and expressly prohibits a runtime policy loader/interpreter or production use of the reference generator. Root may freeze this reviewed candidate and then advance to the separately required source TDD/review stage. This review itself is not Root's freeze action and asserts no production validator or runtime PASS.

Critical findings: none. Important findings: none.

## Reviewed identity and independent byte checks

Reviewed the complete frozen contract, literal document, task brief/report, prior G1/F1 reviews, parsed policy and schemas, reference generator text, and focused same-fixture bytes. The reference generator was read, never imported or executed. Independent inline standard-library arithmetic parsed existing data without calling any product module or validator.

- Frozen contract `docs/superpowers/plans/2026-11-04-b3-network-profile-contract.md`: SHA256 `dbbfe773290d1a001525a7db7c55ec36c51a3ee65c054641c667ff2d39df25bf`.
- Literal document `docs/superpowers/plans/2026-11-04-b3-network-profile-literals.md`: SHA256 `5f94c6c327e6b503b89bce03213bba824af4e6350704aa625f4791e86a20f021`.
- `policy.canonical.json`: 81,806 bytes; plain SHA256 `a87d2bbd5bb8afb554f827fc8609ca02a5ca5b5c1817cb09045e0c35bf230d36`; domain-separated profile SHA256 `b803b4094d8254da45b7ca9c4666d2875794d18532fc721c87488bd64c245582`.
- SAME-process fixture: SHA256 `ed372111e9b71271b082dd2cc8fcd6d8a5f6e124f58f4329999e4c742dfc5bab`.
- Independently read and hashed all 23 paths listed in the drafting report: all recorded byte lengths and file SHA256 values match. All JSON artifacts reproduce their exact canonical ASCII bytes without a trailing newline. The policy schema const equals the complete policy; each serialization schema's definitions equal `policy.canonical.json:$.schemas`.
- Independently recomputed fixture security SHA256 `92bc74696861b35950e3dc4d910ba648c1965a0c7e8e589e67ffed69b0f7ccb7` and raw SHA256 `3e4a5354304f4fdc67009ceddf0419a80d98f3ef7c2e0bbcfd21f37fcfe756c5`.

All unqualified artifact paths below are relative to this review's `profile-literal/` directory.

## Contract translation and closure

Checked `policy.canonical.json:$.links[*]` against every contract table row: exact ten names/kinds, header fields, index bounds, sysfs flags/iflink/operstate, required top-level attribute sets, flags, scalar widths/values, zero current addresses, the two broadcast exceptions, qdisc/name terminators, MAP/XDP and empty flagged attributes. Checked every tunnel child ID/width/value and all exceptional bytes, including ERSPAN 2000/01, SIT defaults and ip6tnl CAP_PER_PACKET. All required nested lengths equal aligned child-TLV totals.

Checked `$.configuration_vectors` and `$.links[*].attributes[id=26]`: every 33-word IPv4 and 59-word IPv6 configuration value and per-link override, required family/child sets, count headers 38/7, fixed cache words 65535/1000 and retained unsigned bit patterns. Full static config remains security material. The expanded policy contains 630 attribute nodes: 499 fixed leaves, 73 word arrays, 55 child containers and three permanent identities.

Reviewed `$.addresses`, `$.routes`, `$.rules`, `$.sysfs_controls`, `$.slots`, `$.bounds`, `$.requirements` against contract sections 1–4: exact record inventories/headers/attributes, priority absence, route-cache zeros, control type/owner/mode, same namespace/port constraints, reparsing requirement and input bounds. Unknown or duplicate records/attributes have no fallback acceptance rule. Each of the 630 observed attribute nodes was also independently decoded from original fixture datagrams and compared with the literals and golden, including fixed bytes, flags, lengths, header facts, counters and identity constraints.

`$.links[name=ip6_vti0|ip6tnl0|ip6gre0].attributes[id=54]` preserves all 16 observed permanent bytes in security while requiring local-unicast first-byte bits and ten zero suffix bytes. No captured identity, namespace or fallback index is promoted to a universal policy constant. `$.proc_security.route6[*].use` stays zero; sentinel multiplicity is two and table_id is null; only per-row reference count leaves security equality. Raw reference counts and original row order remain in telemetry.

## Serialization and reference evidence

`$.schemas`, `$.rule_grammar`, `$.ordering`, `$.telemetry_paths`, `$.encoding`, `$.raw_framing` plus the literal document specify closed keys/tags/types, no unknown keys, lowercase hex, exact unsigned bounds, runtime int rather than bool, recursive numeric attribute order, whole-record canonical-byte ordering, duplicates before sorting, typed word placeholders and fixed count headers. Shape schemas intentionally require the additional documented literal/relationship predicates; they are not advertised as stand-alone acceptance validators. Before/after static comparison precedes selection of one canonical security view.

`$.proc_lexical` closes line endings, character set, token counts, numeric forms, exact dev headers, route header tokens and trie indentation/hierarchy. No widening via broad Unicode whitespace or ignored tails is authorized. The reference parser's incomplete malicious-input checks are explicitly documented; this is not a source parser review or approval to reuse that parser.

Independently decoded every original fixture netlink record and reconstructed all golden canonical attributes, checking the resulting link/address/route/rule arrays and recursive ordering. All 73 netlink telemetry entries match original slot/semantic-record/depth-first attribute order and every original word, including fixed count words. Raw sysfs projections preserve source order and observed fields; complete metadata matches fixture namespace, slot selectors, sequence, receiver port and packet counts. Proc use/reference field separation and the constant parsed proc static view agree.

Independently reconstructed all 24 raw components from fixture bytes, metadata and reviewed sysfs projections; their u64LE lengths, exact order and complete framed preimage equal `fixture.raw.framed.bin`. Policy/security domains contain exactly one zero byte with no length prefix; the raw domain occurs once before frames. Synthetic encoding-only bytes and all recorded synthetic digests recompute correctly; the sample is accurately marked invalid as a full profile.

For `mutability.reference.json`, the refcount exemplar changes exactly one byte in the before-route6 raw component, with the expected changed raw digest and preserved static fields/security hash. The padding exemplar changes only the first route6 SP separator to two SP and its frame length; the changed raw digest is correct. The permanent-identity exemplar modifies the stated valid identity byte and its independently recomputed security digest matches. There is no unsupported raw claim for that canonical-only mutation. Other counter/time invariance and rejection cases remain explicitly future implementation tests.

## Evidence boundary

Only this review artifact was written. No reference-generator execution, production/source/test/fixture mutation, network, native observation, socket, Docker, Git operation, product validator or suite was used. These offline consistency checks establish literal/serialization freeze readiness, not malicious-input rejection by unimplemented source, profile acceptance, installed-worker provenance, atomicity, target binding or restore authority. Future source equivalence/TDD and independent review, N4 v3 and the new installed-worker/original-job/target-pair obligations remain intact.
