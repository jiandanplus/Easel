# AV pack host transport implementation plan

Continue the current `codex/ui-redesign` checkout. No Git mutations, UI copy, product state, provider calls or actual resources in this task. This is executor Task2B1, following the complete-manifest Task2A bridge; it does not close the whole host controller or native acceptance.

## Task 2B1 — bounded single-response HTTP receiver

Files: new `services/platform/scripts/av_sources/pack_http.py` and `services/platform/tests/test_av_pack_http.py` only. Report and RED/GREEN/pins/diff under `outputs/_run/av-pack-http-20261102/`. All shell commands prefixed `rtk`. Pure small synthetic readers and guards only: no socket, threads, timer, native process/resource operation, Docker, network, real packs, private data or imports of existing controllers. Root is the only eventual resource actor. Existing worker/runtime/store/verifier/artifact sources and tests are protected.

### Purpose and interface

Implement an import-inert stdlib parser for exactly one HTTP/1.1 response obtained through an explicitly supplied bounded reader. It is a protocol component, not an authenticated transport or acceptance receipt. The future Task2B2 owns fixed Unix socket, cancellation, original deadline, request allowlist/once state, exact CID/configuration, prepared validator and publication fences. Never reuse the old PG API client or archive endpoint.

Export `HTTPError(ValueError)`, frozen `ResponseLimits`, named fixed limits `METADATA_LIMITS`, `STDOUT_LIMITS`, `ARTIFACT_LIMITS`, and `read_response(read, *, guard, expected_status, content_types, limits)`. Return a frozen DTO with immutable `body: bytes` and sanitized numeric/framing facts only. No endpoint, headers, raw response, error text, success/native/adoption/provenance marker. Required read and guard callables; no ambient I/O. Exact public names and signature may be documented with a small sensible adjustment but no scope expansion.

`read(n)` must return exact bytes of length 0..n; short reads are normal. Read requests at most 65536 bytes, only bounded positive lengths, never unbounded `read()`. Guard before and after every read including EOF; any guard/read/parser failure produces fixed safe HTTPError without raw exception text. Catchable BaseException must not leak raw content. Callers must still authenticate dependencies; no claim that an arbitrary guard is a real deadline. The component does not itself start a timer, open sockets or prove a hard wall.

### Layer budgets, enforced before further allocation/read

- Fixed response presets: metadata body <=131072, wire <=163840; stdout body (mux) <=65536, wire <=98304; artifact body (mux) <=16809985 (=16777216+1+4096*8), wire <=16842752 (=16777216+65536).
- All presets HTTP overhead <=32767; status+headers <=16384; at most64 header fields; line <=8192 (status <=1024), chunk count <=8192, chunk-size token <=16 hex digits; read chunk <=65536.
- Only exact positive ints, no bool, no custom cap larger than the selected preset/default maxima. Named presets are immutable. A tightening mechanism may only reduce caps; do not allow a generic default object to silently widen the stdout preset. Maxima relationships must be validated. Body, HTTP overhead, header bytes, wire and chunk counts are separate counters; chunk metadata cannot consume body budget instead of overhead.
- Meter every received byte incrementally including CRLF, status, fields, chunk-size lines, chunk CRLF, terminal chunk and final empty trailer line. Never retain a raw wire copy; keep only bounded current line/header metadata and bounded body. Avoid a per-byte/per-fragment retained list that defeats the body bound.

### Strict admitted response grammar

- Exactly HTTP/1.1 and expected exact-int status in 200/201/204. Reject informational/redirect/other status, malformed status line, bare LF and control/obs-fold/non-ASCII header syntax. No status/header logging.
- Case-insensitive valid token header names, visible ASCII or HTAB values with outer whitespace removed; reject duplicate names (all names), >64 fields, too long line/header block. Never permit Content-Length plus Transfer-Encoding, any Content-Encoding, upgrade, HTTP/2 or multipart framing.
- Non-204 success requires one allowed exact media type after stripping an optional semicolon parameter section, compared case-insensitively; caller supplies a nonempty finite tuple drawn only from application/json, application/vnd.docker.raw-stream, application/vnd.docker.multiplexed-stream. Missing/wrong media type rejects. This is a framing parser, not JSON/mux admission.
- Exactly one explicit framing: canonical decimal Content-Length (0 or nonzero digit followed by digits; reject signs, leading zeroes, spaces, comma lists) or Transfer-Encoding exactly chunked (case-insensitive). Never close-delimited body for non-204. 204 permits absent framing or Content-Length:0, rejects chunked or nonzero body framing; content-type optional but must be an allowed type if present.
- Content-Length budget checked before reading body. Exact count required, then read once for EOF; early EOF or trailing byte rejects. The request side will demand Connection:close; parser requires EOF and never reconnects.
- Chunked: nonempty 1..16 hex size token, no extensions/whitespace/sign; chunk length bound checked against remaining body/wire/overhead before body read; data then exact CRLF. Include empty terminal chunk in chunk count. Zero chunk must be followed by immediately empty trailer line: no trailers. EOF required after terminal sequence. Independent mux limits remain the artifact consumer's job; HTTP chunk count is not mux frame count.
- No gzip decoding, redirects, retries, permissive fallback or archive handling.

### Tests and review

Meaningful RED then implementation. Save logs/exits, final file pins, report APIs/limits/evidence boundary, a complete review diff. Positive Content-Length/chunked metadata/stdout/artifact-type bytes with split reads and exact counters, empty 204; arbitrary reader segmentation must produce identical body/facts. Negative every framing/header/status/content-type/duplicate/encoding/trailer/EOF/fault/guard case. Test each independent cap at a small tightened exact/one-over boundary, proving Content-Length over-budget rejects before any body read, chunk budget before chunk body read, rejected inputs never call JSON/mux/parser/store callbacks. Test errors contain no secret sentinels even for BaseException; no real I/O. A small constructor counter assertion may prove invalid dependencies fail before first read. Do not allocate a near16MiB sample; Root's actual retention case is a separate mandatory gate.

Run `rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_pack_http.py -q`. Root reads full implementation and tests, performs a fresh focused run, then an independent specification and quality review. No native resource operation before the entire future Task2B2 controller/transport integration is reviewed and frozen. Original AV19, full76-token FFmpeg9.0.2, B2/B3/F2/DR, signing/cleanMac and all original delivery gates remain open.
