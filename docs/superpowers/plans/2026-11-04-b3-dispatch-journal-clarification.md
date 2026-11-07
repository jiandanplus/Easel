# Task2b exact-request durability clarification

The approved HostExecutionOwner plan requires exclusive bounded JSON journal rows and durable commitment of the original exact request bytes, but its initial common field list does not state where those bytes are stored. This clarification defines one closed intent variant; the original plan is preserved unchanged.

Only the `DISPATCH_CLAIMED` intent row additionally contains `request_frame_hex`: lowercase hexadecimal encoding of the **same original immutable complete frame**, including its four-byte header. Its existing `request_sha256` is the SHA256 of those exact frame bytes. All other intent/result/claim variants forbid this extra field. The source sequence/ordinal chooses this variant; public callers cannot select it or supply JSON/bytes/argv.

Before allocating the hex encoding, calculate a conservative prospective canonical row size, then require the actual canonical row to fit the existing8192-byte cap. No separate request file/prefix, higher cap, lower envelope limit or mutable payload reference is introduced. Write the original exclusive0600/nofollow file with complete partial-write handling, fsync file and directory, validate original row/FD/hash/bindings/deadline, then recheck immediately before delivering these exact bytes once. A row/custody/fsync/close/expiry ambiguity burns the attempt UNKNOWN; no request replay or positive summary.

This is fixed ATTEST policy/correlation data; it contains no model prompt, account credential or SQL. Capturing a hash alone must not be described as fsync of payload bytes.

The host-generated v3 schema is bounded independently of its general transport cap. Root's pure maximum-shape calculation uses96-character IDs, uint64 original stat device/inode, int63 host nanoseconds and maximum existing policy limits. The complete frame is2005 bytes and the dispatch row including hex5311 bytes, leaving2881 of8192. Evidence: `outputs/_run/b3-execution-owner-task2b-20261104/REQUEST-JOURNAL-BOUND.safe.json`, tool3495c1. This is a data-size calculation, not a live owner/native proof. Implementation must include its own representative maximum-bound regression and exact raw-frame/hash consistency checks; it may not narrow valid original inputs to make the test fit.

All original clocks, exclusive names, limits, target/factory custody, native command sequence and no-replay/disposition boundaries remain unchanged. Task2b independent source review must explicitly cover this variant before actual qualification.
