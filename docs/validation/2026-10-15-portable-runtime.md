# macOS ARM64 portable core trial — local acceptance, 2026-10-15

Local unsigned artifact: `outputs/_run/portable-delivery/Easel Core Trial/Easel Core Trial.app`.
Delivery archive: `outputs/_run/portable-delivery/Easel-Core-Trial-macOS15-arm64.zip`.
Archive size: **382,883,457 bytes**. SHA256: `e6be0db9243624560c33e5d0ef63ef5a38b65ed409ce61d938cc48add4a09742`.
The ZIP contains the app, README and relative stop helper; ZIP CRC check returned no bad member. File permissions and internal Python symlinks are retained by macOS ditto. Resources contain 3,379 inventoried files.

## Inputs and provenance

Private Python 3.12.13 / python-build-standalone build 20260510 was prepared with uv 0.11.16. The original archive download SHA was **not** independently verified locally. The tracked Python inventory has 1,678 records; 1,650 non-bytecode runtime file/symlink records were checked exactly, with 28 known bytecode entries excluded from verification and copying. Unpinned runtime/native extras are rejected before staging. All six prepared metadata files must byte-match their tracked authoritative versions. The build installs the tracked 25-wheel hash lock and copies tracked provenance; offline uv installation into staged private CPython succeeded. Target machines need no Node, Python or uv. Native Codex 0.146.0 and all five platform vendor files were hash verified against tracked provenance. The bundled zsh resource requires macOS 15; the supported trial platform is macOS 15+ Apple Silicon.

`packaging/macos` retains pinned requirements, wheel provenance, runtime inventory, preparation notes, Python origin/archive metadata, native hashes and collected licenses. The malformed temporary Python provenance JSON is excluded. License notices include Easel, Python/components, Codex, ripgrep/PCRE2, zsh, ratatui and frontend dependencies. This is not a comprehensive legal audit of compiled transitive crates.

The builder uses git-tracked program/seed allowlists plus prebuilt frontend. It excludes profiles, outputs, credentials, caches, developer `.venv`, node_modules and validation scripts. Runtime manifests hash native payloads; the complete resource inventory is verified before startup. Build provenance records commit 29b7fcd, dirty status and appearance input hashes; the twelve preexisting appearance source files remain outside these implementation commits. The frontend contains those working-tree appearance inputs.

## Executed checks

Commands run through RTK:

```sh
rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests -q --tb=short
rtk proxy python scripts/packaging/build_macos.py --codex-root /Users/jiandan/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin
rtk proxy python scripts/validation/verify_portable.py 'outputs/_run/portable-delivery/Easel Core Trial/Easel Core Trial.app'
```

Final source regression: **1,401 passed, 6 skipped, 1 existing multipart deprecation warning in 53.34s**. The durable runner uses fresh HOME and writable workspace, strips key/token/secret/password/Codex/Easel environment variables, and seeds only tracked allowlisted skills/scripts/agents; source resources remain immutable. Optional pytest is appended after venv dependencies. Focused launcher/runtime/provider checks: **87 passed**. Initial review RED reproduced 16 launcher/builder failures before fixes, a relative child-root mismatch, and raw dummy provider key exposure through generic output GET. Final checks cover atomic interrupted seed copies, metadata-save recovery, preserved user edits/directories, strict bounded hash metadata, individual/dangling control symlinks, exact builder authority/inventory, complete SSE success, relative root normalization and private credential output boundaries including case-insensitive spellings/symlink aliases.

Frontend: `rtk proxy node --test tests/frontend/*.test.mjs` **120 passed**. `rtk proxy npm --prefix web/frontend run lint` exits 0 with the existing linkifyOutputs.ts:27 escape warning. `rtk proxy npm --prefix web/frontend run build` exits 0 with the existing >500kB chunk warning; rebuilt assets are index-CaibeDE4.js and index-BJZhgziv.css. Bundled setup/help/footer copy now directs users to custom provider URL/model/key settings. All twelve preexisting appearance source hashes match their protected baseline.

The actual app was moved (rename, no duplicate stage) to a temporary directory with Chinese characters and spaces. The probe used a fresh HOME and sibling workspace, PATH `/usr/bin:/bin`, fake bearer, denied non-loopback proxy and loopback dummy Responses. Private runtime imported native dependency extensions and served real HTTP/static/settings and Web SSE. Native Codex called only the dummy `/v1/responses` twice. Its actual shell tool ran `.venv/bin/python -c`, which wrote and read the owned workspace marker; the dummy observed that tool result. Saved-provider response and SSE contained no raw fake bearer. Authenticated own stop, restart and profile preservation passed. No real upstream/login/developer credential was used.

Final strict proof evidence: `/var/folders/yk/yvtck8_x19d256zbmlrh20xc0000gn/T/easel-portable-proof-nofwl51a/report.json` and `stream.txt`. Assertions require HTTP HTML/static success, expected marker token, exactly one final done event, no error, actual marker file contents, tool result/output marker and correct fake bearer. Observed SSE: activity 2, token 1, done 1, no error; both dummy requests used loopback `/v1/responses`; marker_verified/tool_result_seen/tool_output_marker/bearer_correct all true. Authenticated stop/restart/profile preservation passed; the relocated app was returned to its delivery path.

A bounded ZIP privacy scan found no `.git`, node_modules, profiles, outputs, `.venv`, `.env`, auth.json or private WeChat YAML path. Runtime/native input hashes were independently checked after build. No user data or global installation was cleaned.

## Support and remaining acceptance

Core trial supports Web UI, custom Responses configuration, native agent streaming, workspace file/shell operations and ordinary Python. Media models, browser automation, publishing integrations, ffmpeg/pandoc and optional dependencies are outside the guarantee. `/api/env/install` rejects portable requests before job creation to keep private Python immutable. Small skills/scripts/agents are copied to the writable managed workspace; source defaults remain supported. Detailed startup/stop/update/data instructions are in `packaging/macos/README.md` and the ZIP.

Independent clean-device/Gatekeeper verification, signing/notarization, real upstream provider behavior and optional capability acceptance remain **unverified**. The exact source/frontend regressions above were run independently of the packaged proof. Final broad source/browser review is coordinated by the parent agent; no clean-device or real-provider outcome is inferred from these local checks.
