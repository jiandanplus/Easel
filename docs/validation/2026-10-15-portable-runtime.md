# macOS ARM64 portable core trial — local acceptance, 2026-10-15

Local unsigned artifact: `outputs/_run/portable-delivery/Easel Core Trial/Easel Core Trial.app`.
Delivery archive: `outputs/_run/portable-delivery/Easel-Core-Trial-macOS15-arm64.zip`.
Archive size: **382,882,610 bytes**. SHA256: `61acfe3c4679340464641f4b59b4beb2e6c2749218744c6953e8ef034cf58997`.
The ZIP contains the app, README and relative stop helper; ZIP CRC check returned no bad member. File permissions and internal Python symlinks are retained by macOS ditto. Resources contain 3,379 inventoried files.

## Inputs and provenance

Private Python 3.12.13 / python-build-standalone build 20260510 was prepared with uv 0.11.16. The original archive download SHA was **not** independently verified locally. The actual 1,678 supplied runtime file/symlink records were verified against the tracked inventory. The 25 wheel lock hashes match the prepared PyPI provenance, and offline uv installation into staged private CPython succeeded. Target machines need no Node, Python or uv. Native Codex 0.146.0 and all five platform vendor files were hash verified against tracked provenance. The bundled zsh resource requires macOS 15; the supported trial platform is macOS 15+ Apple Silicon.

`packaging/macos` retains pinned requirements, wheel provenance, runtime inventory, preparation notes, Python origin/archive metadata, native hashes and collected licenses. The malformed temporary Python provenance JSON is excluded. License notices include Easel, Python/components, Codex, ripgrep/PCRE2, zsh, ratatui and frontend dependencies. This is not a comprehensive legal audit of compiled transitive crates.

The builder uses git-tracked program/seed allowlists plus prebuilt frontend. It excludes profiles, outputs, credentials, caches, developer `.venv`, node_modules and validation scripts. Runtime manifests hash native payloads; the complete resource inventory is verified before startup. Build provenance records commit 780e210, dirty status and appearance input hashes; the twelve preexisting appearance source files remain outside these implementation commits. The frontend contains those working-tree appearance inputs.

## Executed checks

Commands run through RTK:

```sh
rtk proxy python .git/sdd/run-provider-tests.py tests/test_portable_launcher.py -q
rtk proxy python scripts/packaging/build_macos.py --codex-root /Users/jiandan/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin
rtk proxy python scripts/validation/verify_portable.py 'outputs/_run/portable-delivery/Easel Core Trial/Easel Core Trial.app'
```

Seven offline launcher/builder tests pass: seed updates preserve edits, unchanged seeds update, resource/workspace overlap fails, foreign `.venv` remains untouched, malformed seed state remains intact, integrity tamper/escape fails, builder exclusions hold, seed destination symlinks cannot overwrite profile data, and symlinked control directories fail before writing. RED was observed first as `ModuleNotFoundError: easel.portable`; tests passed after implementation. The sanitized test runner supplies temporary HOME/workspace and borrows pytest only.

The actual app was moved (rename, no duplicate stage) to a temporary directory with Chinese characters and spaces. The probe used a fresh HOME and sibling workspace, PATH `/usr/bin:/bin`, fake bearer, denied non-loopback proxy and loopback dummy Responses. Private runtime imported native dependency extensions and served real HTTP/static/settings and Web SSE. Native Codex called only the dummy `/v1/responses` twice. Its actual shell tool ran `.venv/bin/python -c`, which wrote and read the owned workspace marker; the dummy observed that tool result. Saved-provider response and SSE contained no raw fake bearer. Authenticated own stop, restart and profile preservation passed. No real upstream/login/developer credential was used.

Final proof evidence: `/var/folders/yk/yvtck8_x19d256zbmlrh20xc0000gn/T/easel-portable-proof-w7v9b3nf/report.json` and `stream.txt`; first proof before final hardening is in sibling `easel-portable-proof-gwip4dmw`. `dummy_stats.marker_verified=false` is the reused standalone handler's unused field; actual success is asserted by reading the marker file and `tool_result_seen=true`, `tool_output_marker=true`.

A bounded ZIP privacy scan found no `.git`, node_modules, profiles, outputs, `.venv`, `.env`, auth.json or private WeChat YAML path. Runtime/native input hashes were independently checked after build. No user data or global installation was cleaned.

## Support and remaining acceptance

Core trial supports Web UI, custom Responses configuration, native agent streaming, workspace file/shell operations and ordinary Python. Media models, browser automation, publishing integrations, ffmpeg/pandoc and optional dependencies are outside the guarantee. `/api/env/install` rejects portable requests before job creation to keep private Python immutable. Small skills/scripts/agents are copied to the writable managed workspace; source defaults remain supported. Detailed startup/stop/update/data instructions are in `packaging/macos/README.md` and the ZIP.

Independent clean-device/Gatekeeper verification, signing/notarization, real upstream provider behavior and optional capability acceptance remain **unverified**. Full repository backend/frontend regressions and broad source/browser review are coordinated by the parent agent and must be reported separately; this document does not infer those outcomes from the packaged probe.
