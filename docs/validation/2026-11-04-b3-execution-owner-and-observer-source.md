# B3 execution-owner foundation and fixed observer — source acceptance

Scope: current `codex/ui-redesign` worktree, no commit/push/merge. No frontend changes or explanatory product text. Existing subscription/custom API and workbench state are outside this change.

## Current observer-handoff repair accepted (2026-11-05)

A later Root cross-component check reproduced a response-format mismatch: actual `execution_observer._observe`/`_write_frame` emits `easel.b3.execution-baseline-response.v1`, while the host parser and its synthetic fixture expected `easel.b3.execution-baseline-observation.v1`. Toolb5a067 used real producer/framing and host parsing with only fixed kernel acquisition/IO seams replaced;45836 bytes were rejected. Earlier302 green tests and source review remain historical source evidence, not proof of this previously uncovered actual handoff. The once003 wheel/input archive are uninstalled historical candidates and must not enter the next native gate. Builder implementation was stopped before any native launch. Minimal producer-to-host regression/fix is in progress, followed by new review, tests and packaging. Evidence: `outputs/_run/b3-observer-handoff-fix-20261105/ROOT-REPRO.safe.json` and `ROOT-ACCEPTANCE-STATUS.safe.json`.

The blocker above is now resolved at source level. Minimal shared response-format import/compare was independently reviewed C0/I0/M0 (review SHA c511d4309bff40e5d7e9eb78ee96826d5d5270503b880bf9236c9afc03a3067d). Root reran the current-source owner48, caller/supervisor91 and seven regression modules165:304 PASS total. `outputs/_run/b3-observer-handoff-fix-20261105/ROOT-SOURCE-ACCEPTANCE.safe.json` binds final11-file inventory, both source reviews and exact original terminal test handles. The original302-test checkpoint and once003 artifacts remain historical and are not used for the next native gate.

The corrected current wheel is `current-wheel-once004/easel_central_platform-0.1.0-py3-none-any.whl`,608209 bytes,SHA256 da6800e85d9836b80d326d2e4064c95feebcea9b2450c22e4d7e8ff3c5aa9815. Its161-input frozen manifest SHA is83d5a38628e87c56238b55daaee45baffc2c0a6ff7c98ba18c1f8ce0c3f74cca;159 payload files/162 zip entries reverified. New public input archive once004 is321781760 bytes/SHA b9691101b4de682494c930313cd95981d4ef2b600d7e7810b1c8077bb1be8340; independent36-member verification confirms only wheel/intent/driver changed and33 old public members remain identical. No new image or installed worker is qualified yet. The new bounded builder controller is under source implementation/review before actual use.

## Previously accepted checkpoint

Task3 and source-acceptance Task4 are complete for the frozen source: 302 fresh Root tests and independent C0/I0/M0 review. The four public tools have also passed their separate actual compatibility gate and the temporary helper has been removed. A new current-source wheel has been built and byte-verified, but is not installed/cold-qualified or target-bound. Full restoration and delivery remain open. Detailed evidence follows, with earlier snapshots explicitly historical.

## Accepted foundation history

| Component | Accepted foundation SHA256 | Fresh Root verification | Independent review |
| --- | --- | --- | --- |
| Original producer custody and separate execution journal (`execution.py`) | `0c06e697af7809bc3683bbc2f0157213caae3738b6ddd41df8c65cb839755164` | 29 focused tests,39.034s,OK; tool5494c5 | Task1 `REVIEW.fix.safe.md`, C0/I0/M0 |
| Fixed baseline observer (`execution_observer.py`) | `ca8bf2976e31d44fbc9b2dedfe071732e60ff1419824f1e0a75a10da91d39674` | 22 focused tests,0.141s,OK; tool0a4de9 | Task2a `REVIEW.M1.safe.md`, C0/I0/M0 |

Task1's initial review found an observation/close binding gap. The fix shares original binding checks between active guards and observation, rejects replacement events/content/closed peers before release, and preserves passive audit after a known successful close. The canonical dotted-PATH restriction was corrected. Tests retain meaningful pre-fix failures; they do not reinterpret the earlier26-test pass as covering those findings.

The observer preserves complete raw network/proc identity data, anchors its deadline before reading, uses exact bounded frames and nonblocking transport, and returns failure for late stdout/stderr close errors even after a complete response frame. It has no import-time discovery, arbitrary command/path selector, expected-network-hash authority or database access. Source-only tests use synthetic private seams.

Foundation test SHA256 (Task1 owner snapshot; later stages below supersede it):

- `test_media_backup_b3_execution.py`: `1f80c7c4fafbd20a801d55b46aa55cdb81126d4302ad9db307fc102ce5324eac`.
- `test_media_backup_b3_execution_observer.py`: `e41c2ab275f0886882f4a84ccaa3a3f64cd15224053061777bd22edc7be36095`.

## Actual public-tool qualification remains incomplete

The fixed public PG image's four selected tool binaries were inspected offline. Three separate public compatibility attempts failed, each with its own original clock/claims and preserved evidence:

- once001: optional inspect field omitted; failed before start.
- once002: inspect observed created before original asynchronous start published running; failed before probes. A failed pre-stop precheck was followed by an unconditional STOP due to Root orchestration error. This remains explicitly recorded. Further cleanup used a separate successful gate.
- once003: same-start wait passed, sha256sum version and loader completed, but the manually declared library set omitted transitive libz/libzstd dependencies. No compatibility success was accepted.

All three dedicated public containers were normally removed after known-state gates. Their retention owners were released only after remote cleanup and terminal checks. Original source process PID62835/start/restart0 remained equal, and all227 volume identifiers matched each attempt's own before inventory. No image or data volume was deleted by these public compatibility attempts. Ordinary STOP exit137 is cleanup evidence, not the planned natural-EOF/exit0 qualification.

User approved the complete-closure revision, frozen plan SHA256 `37da940cdb09d04b795181b21f6efdbf254d62c25baf7cb6cd92abcadb4ec0fa`. Offline derivation and its independent review have completed; the resulting frozen manifest is described below. It must reconstruct the complete transitive ELF/library/cache/image-alias graph from the frozen public OCI image, be independently reviewed and only then supply a new actual compatibility manifest. Merely adding the two observed names is not closure. No fourth runtime attempt has been entered.

## Open delivery gates

Task3 caller/supervisor graph and disposition source acceptance and regressions; actual target-bound Task2b ATTEST; new artifact build including the observer; installed/cold and fresh original target-pair qualification; approved bootstrap handover, trusted roles/control/restore/catalog/application checks and the full B3 matrix remain open. The prior approved worker image contains the previous closure and does not qualify these new files.

These source/fake tests, partial native loader observations and cleanup proofs do not establish a completed restore, production deployment, real-model/payment verification or final macOS delivery.

## Task2b source acceptance and current integration

Task2b bounded host transport, exact request journal, baseline reparse and ordered original-target ATTEST source passed Root’s fresh 48 owner +10 IO +3 baseline tests (61 total; tools b3a2e3/706456/406ed2). Independent review approved C0/I0/M0 in `outputs/_run/b3-execution-owner-task2b-20261104/REVIEW.safe.md`. Its frozen execution.py SHA is `8233792924a9da8cf1ebe274d86644e9b34e6613ab94f0c9c96dde440cfed3d1`; source-freeze and delta are archived beside that review. This is source acceptance, not actual native ATTEST. Task3 is now integrating caller/supervisor and narrowly approved private retention bookkeeping; its working files are not yet independently accepted.

## Complete public dependency closure

The offline helper’s final synthetic suite passed 89 tests. Root’s once003 offline derivation completed from the frozen archive with 4 tools,11 unique ELF nodes and29 edges. Canonical manifest: `outputs/_run/b3-target-tools-public-20261104/closure-actual-offline-once003/MANIFEST.public.json`,55328 bytes,SHA256 `0b8607dcfadbc133527904ec6da1996be27a47e1e5a125d084f1a6d47b1f5dde`. Independent actual-artifact review approved C0/I0/M0 in `closure-ACTUAL-OFFLINE-REVIEW.safe.md`. All runtime/target/SQL authority flags remain false.

The new once004 controller consumes this complete manifest, resolves actual loader paths through frozen aliases, hashes all closure files and pins the original public image environment without loader overrides. Six pure controller tests passed; independent controller review is in progress before its single actual run. Earlier failures remain failures.

## Directed Docker cleanup

A new user-requested cleanup removed20 stopped/never-started obsolete Easel tests and one superseded worker image, leaving9 containers and all227 volumes. Original source PID62835 and every retained container state matched the pre-cleanup inventory. Separate cleanup ledger: `docs/validation/2026-11-04-docker-obsolete-tests-cleanup.md`. This supersedes earlier statements that these old failure containers still exist; their safe historical evidence remains archived.

## Actual public-tool compatibility accepted

The reviewed once004 controller (SHA256 `959f05703fb0a2d6a7e94532b478ee4c15f9a62c277deab7ccbc604b165754d7`) completed its single actual run. Original launch tool01c21a/session6325 returned exit0 through the same-session terminal tool555c8c. Root checked that terminal observation within the same original120-second clock with98.144396125 seconds remaining. The provisional candidate stayed accepted=false; Root accepted only after binding that terminal/deadline evidence and reading the independent C0/I0/M0 actual-evidence review.

All33 CLI intent/terminal pairs are complete and zero-exit. Four GNU coreutils9.7 versions, all four loader closures, actual alias targets and11 file hashes match the frozen manifest. UID999/cap0/NNP/proc/namespace checks and natural attach EOF/container exit0 passed. Exact source process state and all227 volume IDs matched. Subsequent fresh known-terminal pre-removal gate passed; normal rm toolab5fe3 removed only this temporary container. Postcheck fd1404 confirmed its absence,9 retained containers, original source PID62835 and unchanged227 volumes.

Evidence: `outputs/_run/b3-target-tools-public-20261104/runtime-once004/ROOT-ACCEPTANCE-AND-CLEANUP.safe.json` and `RUNTIME-ONCE004-ACTUAL-REVIEW.safe.md`. This closes the four public-tool compatibility gate only; target-bound ATTEST, new installed observer artifact and full restore are still open. Earlier failures remain failures.

## Task3 and source-acceptance Task4 completed

Independent review identified the legacy-versus-full context digest mismatch. A concrete regression reproduced it through the actual pre-capture digest producer, original owner/two commits and real supervisor validation. The minimal ATTEST-only fix preserves the legacy projection and full original owner binding. Follow-up review approved C0/I0/M0 in `outputs/_run/b3-execution-owner-task3-20261104/REVIEW.I1.safe.md`.

Root final-source verification: caller/supervisor91 PASS36.304s (47a67e); unchanged owner48 PASS154.940s (a81b2d); explicit observer/baseline/IO/contract/worker/factory/lifecycle modules163 PASS183.357s (69529a). Aggregate302 tests. Unchanged source hashes were rechecked rather than repeating the long owner/regression runs after the three-line caller-only repair. `ROOT-SOURCE-ACCEPTANCE.safe.json` binds the final ten-file inventory, review and exact test terminals. This is source acceptance only.

Current-source offline wheel built by601ef2: `easel_central_platform-0.1.0-py3-none-any.whl`,608209 bytes,SHA256 `ae9370ea8ea228893a74ce7951b9a97450d0ebe7cb6079743407573f27875ed7`. All157 previous source input files match their old pins; four accepted execution/observer files are the only additions. All159 package payload files were compared byte-for-byte against161 frozen source inputs;162 zip entries including metadata. Build tools and all source inputs were reverified after build. No worker import, installation, Docker container or network operation occurred. Frozen manifest `wheel-build-inputs-once003.safe.json` SHA256 `b3ba54455f2bbebc1d67fdbbf594af344755f83c2d61dc68f65036767b43bf05`; wheel build result `current-wheel-once003.safe.json` under the worker artifact preparation evidence directory.

Next actual gate is to build and qualify the new installed worker/observer closure from this wheel, then use a new original job/input/target pair for sequential ATTEST. The old worker image is not promoted into qualification of the new files. Full SQL/bootstrap/application/media/financial/matrix and final macOS delivery remain open.

## Current-source image built and temporary builder removed (2026-11-05)

Once004 failed before create because CLI inventory contained valid UTF-8 Chinese labels while its text logger required ASCII. That original attempt remains FAILED_PRECREATE; independent no-mutation/no-child/source-and-volume gates preceded release of only its dedicated idle owner, same-session exit143. The operator logger was minimally fixed to strict UTF-8, with original bytes/hash preserved and invalid UTF-8 still rejected. Root30 fresh tests and independent C0/I0/M0 review passed.

New once005 executed the reviewed fixed plan, original session95129/exit0 within its original900-second window. Independent actual-artifact review approved C0/I0/M0. Accepted image is `sha256:2bfa53d96607a65b444c1eb820b22ab5395676332181b57a394220a18c9ea0f4`,tag `easel-b3-worker:local-20261105-once005`. Build manifest SHA1cf991e3f79876662f201acf271da53f4045e23f7a6cceabf9f4aaf4fd2bd070 covers5596 files/472808260 bytes; all159 installed application payloads match161 frozen source inputs. Observerca8b and corrected baselineb293 are installed. Full image defaults and original base-layer prefix plus one layer are checked.

After an independent fresh exited0/full-state/source/volume gate, the exact temporary builder was removed normally. Postcheck confirms9 original containers,227 unchanged volumes, original source PID62835 and unchanged new image. Root acceptance and cleanup: `outputs/_run/b3-worker-artifact-preparation-20261104/public-worker-once005/ROOT-BUILD-ACCEPTANCE-AND-CLEANUP.safe.json`. This is **built-not-attested**: no worker/cold/target/SQL/full restore proof yet. Next step is fixed installed observer and worker cold qualification using binary-safe framed transport.
# 2026-11-05 R3 source correction and rebuilt wheel

The lifecycle correction is now accepted as source only. Root independently ran125 focused checks: baseline12, cold-controller64, original execution-owner48 and real factory/caller-context composition1; all original sessions ended0. Independent source review0eaab904... reports C0/I0/M0. Root acceptance and the18-file frozen inventory are under `outputs/_run/b3-worker-lifecycle-metadata-fix-20261105/`. The prior304-test result is retained as historical parent evidence, not relabelled as a fresh run of the revised code.

The shared private parser validates raw created/running/exited paths and trusted original-target inheritance before comparing stable projections. Resource, descriptor, security, original process/deadline and no-replay controls remain intact. Current production baseline SHA is `eb06a247345ca9ccf02d2228f59ee1b39f3a5b0799fab1cf090a876c0374265a`.

Root rebuilt wheel005:608789 bytes/SHA `f75295c0e6199912b28f99bfdcd4eefae427a0c88ae392933a75728306aeb3e6`; all159 payloads byte-match the161 pinned source inputs, with162 ZIP entries including metadata. New public inputs005 archive SHA is `98617dadf50b2980f89e127544ddb8631dea6f43b63cbf9af943d506c6f51968`; independent archive verification confirms36 members, exactly3 intended replacements and33 byte/metadata-identical public members, with no host extraction. These files are not installed or cold-qualified.

The actual public once006 build has now completed with original session67986 exit0, independently reviewed C0/I0/M0. New image `sha256:4536d85782bd8386142beecf311eccd68faa55d92711daeccfcc6015c26a75d4` is accepted **built-not-attested only**. Its actual902459-byte manifest SHA is `98f1e7bb6884d1e89df3ce8aa2915414103391d13b3d6e06b6835aa2c5e0d14c`, covering5596 files/472810826 bytes; full image canonical SHA is `781931d7072d05943e2343d44cdbdc1b6ad1cb62586d92d18c1f5edcbce9527c`. All159 installed project payloads match the frozen source pins.

After fresh identity/state checks, the successful temporary builder was removed normally;9 retained containers, their stable states/health statuses, source PID62835 and all227 volumes remained unchanged. The superseded, unreferenced once005 image2bfa was also removed with `image rm --no-prune`, preserving the new image and every other image/container/volume. Docker image logical usage returned to10.08GB. Actual receipts and review51961fa3... are under public-worker-once006 and current-source-builder-author.

Cold once002 is now being mechanically rebound to these actual new pins; fresh source review/tests still precede its actual run. No cold/target/SQL/fullrestore acceptance is claimed here. The following failed-run history remains unchanged.

# 2026-11-05 actual cold qualification status

The new public image2bfa / wheel004da680 reached actual created and running metadata in cold001, but **did not pass cold qualification**. Original session11014 failed before observer exec or main request: empty created Docker file paths became the original container's populated runtime paths, and raw OomKillDisable changed false→null. The cold controller's immutable projection rejected these lifecycle changes. Original raw CLI records and frozen source are preserved in `outputs/_run/b3-worker-artifact-preparation-20261104/cold-worker-20261105-once001`.

The source-only cold controller had passed50 focused tests and independent I1/I2 re-review before the run. These tests lacked a realistic populated-path transition; their success did not establish installed cold behavior. Production execution_baseline._worker has the same created-path assumption and an additional shared-network hostname expectation requiring correction. The specific correction is tracked in `docs/superpowers/plans/2026-11-05-b3-worker-lifecycle-metadata-fix.md`. No new actual candidate may use the obsolete source/artifact pins.

Root independently observed the original worker exited1/PID0/no active exec and no live child before releasing only the exact retained idle owner; the same original tool session ended143. The worker's exit reason was not independently captured. The earlier timeout label is an inference, corrected by the v2 terminal receipt. Independent actual-failure review78eda170... accepts only the failed pre-dispatch archive. No observer/attestation response or main-local wait exit code is claimed.

After review, Root fresh pre-RM586835 confirmed exact known-exited CID/name/image, original source PID62835 and all227 volumes unchanged. Normal rm51f4fb removed only the failed temporary container. Initial postcheck e48aed incorrectly compared naturally advancing Health logs; read-only diagnosisbc0ae7 confirmed only two portfolio Health logs advanced while both stayed healthy. Corrected postcheck3d1a06 confirmed container set9, retained identities/stable process states/health statuses, all227 volumes and full image hash unchanged; no rm replay. Current image remains built-only, no actual cold/target/fullrestore acceptance.

Nine missing public files in the separate old host-test runtime were restored only after each original SHA matched an existing public copy; 3,959,548 bytes, no overwrite/network/package install/execution. All3777 runtime hashes then matched. Prepared target packet009 was strict-shape checked but never launched; it is superseded before launch because the lifecycle source/artifact hashes now require regeneration.


## 当前镜像实际冷启动组件验收

image006对应的cold002完成原始会话82825的正常exit0。当前控制器b2cbb617...、测试31be1023...在新制品绑定后通过64项Root回归和独立源码复核；实际运行全部15条原始command/terminal记录、原job120s/main60s窗口、独立安装observer的完整N4/proc、请求原policy重建、response解析及10个host ZIP导入来源均通过Root与独立复核。实际报告ACTUAL.cold002.REVIEW.safe.md为C0/I0/M0，SHA ccea5e488362d0c1b9831332c0f08d45175df2a1bbc040452f23afc0fbb6b973。

Root已完成新鲜检查和普通容器删除，cold002/ROOT-COLD-COMPONENT-ACCEPTANCE-AND-CLEANUP.safe.json仅接受“安装后的observer＋worker冷启动和自身命名空间关联”。该组件验收不证明PG target/kernel pair、完整host execution owner、SQL恢复或完整B3；这些标记保持false。worker package origins为固定worker的自报校验，与独立观察的10个host ZIP来源分别记录。原失败cold001仍保留为失败。

## 当前完整目标任务 once010 的实际失败边界

新参数包ff3c9285...独立DATA／源码复核b07191d5...通过，Root新原始监督会话84895（0e652e）与真实session绑定07bcd6后，唯一900秒时钟883544.014638125→884444.014638125开始。首次9cdde0在bootstrap前因Root准备目录继承gid0而失败；修正为501:20且严格private_read通过后才启动原任务，字节及源代码未改，未重放已消费任务。

两目标分别完成8步READY记录；执行器central产生38个intent／37个result（原held-main intent未完成），在execution.py701的目标复核后guard于原main60秒期限失败。没有DISPATCH_CLAIMED／COMMITTED，未发送执行请求。worker实际exited1；未捕获其内部退出原因，不把该exit自行解释为具体错误。Root归档、复核及RETAINED处置均保留FAILED／UNKNOWN／remote unknown。原任务的完整host-owner/PG kernel pair资格仍未通过。

当前需要进一步诊断和优化复核成本，保持原60秒和900秒限制、全部原始目标/文件/资金隔离检查。Root合成公共CLI读数分析：41,043,648字节读取＋SHA约0.01545秒；1254次单独fstatvfs约0.00055秒，容量系统调用本身不足以解释延迟。仅合成fixture的_PrivateJobFiles.verify一次cProfile为0.603秒／3,212,537函数调用，1274次_guard引起2548次RootFactoryPolicy.__post_init__，有明确的重复验证成本；该离线profile不等同于原任务完整耗时归因，也尚未证明某项优化能解决60秒问题。原生产代码尚未改动。

失败容器已普通清理、229卷保留，空闲监督器精确释放后原会话137。实际归档与清理证据见b3-execution-owner-qualification-20261105-once010。完整Tasks4–9、SQL恢复／应用／矩阵／桌面交付继续未完成。

## 重复校验成本修复及新版制品（2026-11-05 后续）

上述“原生产代码尚未改动”描述的是 once010 失败归档时点。后续已接受两项保持检查语义的源码修复：policy 每次 guard 通过现有 `_projection()` 完整验证，去掉紧邻它的重复显式验证；Owner.attest 三个检查边界各调用一次原始 handle 的完整双目标检查，去掉每个边界对同一对目标重复检查。没有缓存、跳过目标或字段校验、延长 main60/job900 或改变失败状态。

源码验收分别在 `b3-policy-validation-dedup-20261105/ROOT-SOURCE-ACCEPTANCE.safe.json` 与 `b3-paired-verification-dedup-20261105/ROOT-SOURCE-ACCEPTANCE.v2.safe.json`。后一份是复审全文读取后的最终记录；早期 pending 记录保留但不作为最终验收。Root 各自完成相关 fresh suites 和独立复审；这些源码／合成成本证据尚不能证明真实任务满足原60秒期限。

新版 wheel007 与 image007 已离线构建并验证文件对应关系，构建辅助容器已普通清理。实际构建原会话49942/同会话407af5正常 exit0；独立保存证据复核C0/I0/M0。`public-worker-once007/ROOT-BUILD-ACCEPTANCE-AND-CLEANUP.safe.json` 仅接受 built-not-attested。image1ed361...、wheel85ce6f...与新源码绑定，尚待独立冷启动和全新目标任务验证；不继承旧 cold002 或 once010 的资格。

## 最新 image007 实际冷启动组件验收完成

cold003 使用当前wheel007/image007，在原会话51926正常exit0（同会话1bd806）。Root在原钟内观测到job剩余101.868059083秒、main剩余42.127745500秒；没有续期。Root独立重解析15条原始terminal、完整N4基线、原policy/request/response/private摘要、10个实际ZIP导入来源和前后源／卷／镜像不变量。独立保存DATA复审 `ACTUAL.cold003.REVIEW.safe.md`（90be298b...）为C0/I0/M0。

新鲜检查、普通容器删除及后检已完成，Root组件验收记录SHA `71df53badb45e1dcd2e902f62867cc97c9ca91b61f619e598a10208953216d50`。正向范围仅为installed observer/worker cold SELF namespace correlation；PG目标、kernel pair、完整Owner、SQL和完整恢复仍未通过。原candidate保持accepted=false，原失败和历史验收均未改写。旧image006已确认零引用后删除，当前image007保留。

新目标任务once011参数包已由Root生成：453源码闭包与3777公共运行时文件重新核对，私有文件0600／目录0700及501:20组已验证，绑定新的owner源码及image007。当前只完成准备（原会话89975/ae57c1 exit0），等待独立公共DATA复审；尚未启动bootstrap／新900秒任务或新目标容器，不是目标资格证据。

## once011 双目标 ATTEST 实际验收闭环

全新参数包经独立公开DATA／源码复核（cb08b4b1...），Root新鲜复核453源码和3777公共运行时后启动原监督会话85984（c28bde）。postlaunch绑定5b6407后开始唯一900秒原钟889686.007332541→890586.007332541；原会话同一terminal45e68c正常exit0。Root原钟内观测完成时仍余550.430136秒。没有延长main60/job900或使用旧once010时钟。

两个新目标均完成原8步READY链；两worker的原始ATTEST路径均COMMITTED。Root真实终态validator按原始私有manifest、目录dev/inode、operation IDs和ceil/floor原钟验证通过；source-before/after原私有快照相等。公开归档含167条完整execution链、16条目标READY记录和两份精确请求帧，独立实际保存DATA／源码复核C0/I0/M0（bcdb61de...，Root全文读取f299f0）。

证据限度：native stdout/stderr缓冲区及main60起点仅在原owner内存验证，未持久化，不能从hash重建原响应或独立测量该60秒的完整耗时。已保存精确请求、durable commit、原正常会话终态及已审源码的原始检查，支持本次限定的两目标两worker ATTEST路径。历史target receipt的kernel_network_isolation_proven=false未改写；SQL_restore/isolated_restore_verified继续false。

独立复核后，两PG和parser正常停止，两worker原exit0保留；五个精确临时容器经已退出/PID0/无exec检查后普通删除。parser在显式正常stop后exit137，属于验收完成后的资源停止，不冒充自然任务exit0。首次只读后检f8a370断言失败且未捕获比较项；后续bdd7b7及最终52302f两次只读检查均与原保留状态一致，没有重放stop或rm，也不猜测首次失败原因。

最终 `ROOT-ACTUAL-ACCEPTANCE-AND-CLEANUP.v2.safe.json`（SHA256 c935d5e835213ce243c418030c1255deb94125730defea84ee4446dcde2a14e2）记录限定接受及清理：保留9容器／3运行、全部231卷集合不变、原源PG PID62835和当前image007不变。实际恢复的下一步仍为原Task4角色／凭据／数据库重建，随后Task5–9。只读Task4集成图指出必须新增独立source-owner reconstruction context与版本化角色转换证明，旧factory元数据不会证明A=OID10／Bnew身份；没有实施SQL或手动bootstrap旁路。


## Task4 源数据库上下文数据契约验收

下一步角色／数据库重建的私有数据契约已完成：`reconstruction_context.py` 为无IO的不可变PG16数据模型，完整保留角色、独立OID10身份、数据库locale／ICU／tablespace、数据库／角色设置、xmin、快照及原时间渲染文本；包含边界、关联一致性和脱敏摘要。它只表达已读取的数据，不授予重放权限或证明原生执行。

Root保存验收 `outputs/_run/b3-source-context-data-20261105/ROOT-SOURCE-ACCEPTANCE.safe.json`（d05f8dc4...）记录29项合成测试通过（8fbd3f，0.231秒，exit0）及独立复审C0/I0/M0。生产SHA d370ce8e...／测试SHA1357388b...。导入检查的原M1通过限定的进程内重执行与九个内存负控变体补齐；这不是冷启动沙箱或系统调用审计。

Root曾把SCRAM的04096迭代字符串误判为必须规范化，检查官方PG16解析源码后撤回，保留原误判及修正记录。契约保留精确正数十进制拼写，不重写凭据。此规则不自动扩大原B3归档解析器的兼容范围。

后续已开始源捕获生命周期接入的源码开发：固定BEFORE／DURING／AFTER观察、原源owner与排他引用、publication描述符保留、真实pipeline终止状态。当前只有实现任务已派发；尚未完成或验收接入，不是实际数据库采集、SQL重建、完整恢复、安装包或生产上线通过。

## 本机基础镜像清理后的可用性边界

按照本次清理要求，旧AMD64Python623674...及构建专用Trixie基础镜像adbc7c...已普通删除。当前完整image007（1ed361...）及运行源PG保持不变。历史构建／镜像来源证据仍保留；需要重新构建时必须重新取得和核对独立基础镜像，不能使用旧readiness记录声称它当前存在。详见清理once004证据。


## 源捕获与发布生命周期源码接入已验收

已完成固定BEFORE／DURING／AFTER采样、独立启动的排他引用、原始source owner／输入／上下文绑定，以及真实发布目录句柄、验证回执、COMMITTED、OPEN和finally终态之间的关联。默认无companion路径、CLI／ATTEST入口和原七文件归档格式保留；新入口仍为内部开发helper。

首次独立复审发现三项Important：捕获阶段期限与较长Root期限混淆、释放复用句柄时缺少身份检查、AFTER新观察连接未明确保留。均经保留RED证据、定向修复和永久回归解决。现在到期不会产生成功标记；释放前核对分配时设备／inode，拒绝关闭外来句柄；AFTER连接由原捕获owner实际关闭并观察结果，未知读取／关闭仍保留引用，已知数据拒绝维持失败终结。

Root新鲜完整离线套件177项通过（30.93秒、exit0），原始两项问题复现脚本重新运行也通过（0.89秒、exit0；两项与永久用例重叠，不能相加为179项独立覆盖）。独立复审C0／I0／M0，Root全文读取并保存验收记录：`outputs/_run/b3-owned-capture-integration-20261105/ROOT-SOURCE-ACCEPTANCE.safe.json`。最终生产／测试9文件、完整diff、修复diff和原失败证据均保留。

这是源码和合成结构流程验收：实际临时文件、密码学、归档、自验、持久化、发布、控制收尾均在合成数据上执行，原生数据库传输为假实现。实际PG16命令／超时／后端终止、真实完整源捕获、角色／数据库重建、执行准入、POSTCHECK、完整B3恢复、部署及安装包资格仍未通过。本次没有新增Docker资源、操作生产服务或修改UI／Git状态。


## 角色／数据库计划与原执行owner准入源码验收

2026-11-05已接通原成功捕获、独立预先授权、完整三相样本、可选成员权限闭包、真实历史reader／commit／目录来源，以及原RootExecutionOwner和两个原READY目标之间的内部准入。计划是封闭DATA，不含SQL、回调或新执行能力；writer／reader分别签发但同原始policy issuer，未复活已关闭句柄或要求不同工作目录inode相等。默认CLI／ATTEST和七文件归档格式保留。

复审发现并修复了授权总大小检查过晚、显式管理员／设置引用遗漏、默认样本缺少捕获时原件pin、历史引用漂移误作可释放拒绝、计划摘要整树复制。现在原样本／引用／source UNKNOWN保留消费者和现场；纯计划或已知期限拒绝允许显式关闭，已过期并取消的已知资源仍可释放。摘要流式计算完成后再执行最后时限／custody检查，双计划原子安装。

Root先验证既有完整选择396项通过（299.46秒）；修复后对最终源独立跑完全部受影响三套件161项（211.87秒、exit0、无warnings）。二者是不同版本／范围，不相加。独立DATA和MAIN复审均C0／I0／M0；最终18源／测试文件pin已复核。验收记录`outputs/_run/b3-role-plan-admission-20261105/ROOT-SOURCE-ACCEPTANCE.safe.json`，SHA256 2c3f713a652ce6c50051f20971a780637c02564c5b990fac5e57ae31cb07ed81。正确完整差异为MAIN-SOURCE-v3.diff；错误基线路径的v2差异保留为失败证据，不作为完整增量。

此处仅关闭内部DATA计划／host准入与清理的源码门禁。合成测试的native叶仍为假实现，未证明实际PostgreSQL查询、角色／数据库写入、H-B-A三次提交、最终密码认证、内容／控制恢复、完整B3矩阵或安装包。原Task4及整体交付继续保持未完成；未部署生产或修改前端UI／Git。


## 原始 owner 到角色工作进程的连通源码增量

已接通独立恢复策略、原始 owner／进程／pipe／selector、固定 INITIAL_OBSERVE 和无交接 ROLES_APPLY 程序、严格 READY／request／RESULT／EOF／exit 验收、持久阶段记录和不可伪造的中间 RoleCreationProof。每个有限阶段使用自己的原始主进程启动前60秒窗口，同时受原任务总期限约束。已知终态的取消／过期清理与 UNKNOWN 保留区分；没有重发 COMMIT 或补偿 SQL。

独立审查发现三项 Important，现已按反例先失败再修复闭合：角色写入前消费初始库存 pin 并复查；完整请求／认证材料超限在 claim／native 分配前拒绝，READY 后才计算实际剩余时间并重核固定投影；原恢复策略对象和摘要在 owner 构造时保留，包含 None，拒绝后续替换。覆盖用例使用跨会话共享合成角色／成员库存，检查额外角色、成员与目录变化在 BEGIN／CREATE／COMMIT 前被拒绝。

作者覆盖5套件150项通过（306.00秒，原会话72662，terminal259a89，exit0）。Root在最终冻结字节上运行更广10套件291项通过（331.60秒，原会话39614，terminal4b0b9b，exit0）；两个数量不相加。独立修复复审 PASS C0／I0／M0，Root复核最终合并14文件 pin；验收记录为 `outputs/_run/b3-task4-native-reconstruction-20261105/review-fix06/ROOT-SOURCE-ACCEPTANCE.safe.json`。初次13文件277项日志属于修复前版本，其原工具终端丢失的边界仍保留，未替代本次291项完整终态。

本次仍是源码和合成 transport／PG 叶验收，测试进程调用实际固定 worker 函数，尚未通过安装态 main。完整 entry／program／binary-client／依赖／资源／netns 测量与 installer 接入进入下一源码任务。H_CREATE／HANDOVER／H_DROP、完整私有 initdb 基线、数据库 prepared／final、原始有序 ACL、tablespace／GUC 顺序、凭据 FD、caller、真实 Task5／6 最终 LOGIN 门禁及完整 B3 仍待完成。未运行真实数据库重建、修改前端、操作生产或写入 Git。
