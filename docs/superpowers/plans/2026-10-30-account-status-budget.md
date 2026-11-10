# Account status: shared server budget repair

Authority: the approved creator-task/readiness/recovery plan and the continuing full-delivery goal. This repairs an existing status-read correctness gap; no new identity architecture or business feature. B2 local development has separately been approved; AV and workspace migration retain their separate gates.

Evidence: v34 fixes the browser's unbounded latest read, but `SessionStore.locked` can block before Central's GET deadline; `local_status` then reacquires the lock for a separate unbudgeted capabilities request. A real read exceeded the old operator's 10-second limit, and the original UI has exercised protected unknown recovery. These observations do not prove a permanent backend hang or identify a particular production lock holder.

Contract:

- A single monotonic 15-second budget belongs to each `local_status` call, including executor admission, both lock acquisitions, identity verification, permitted refresh/one GET repair, and public capabilities. The route returns a safe unavailable/unknown outcome when that budget expires; default non-status CentralClient callers retain their current transport behavior.
- Route-level bounded waiting is paired with deadline-aware internal ownership. Cancellation of `to_thread` never proves a worker stopped. A worker admitted after expiry must not read/change auth, create a refresh marker or begin HTTP. A worker already doing HTTP owns cancellable transport and closes it before releasing the lock. OS filesystem delays/cleanup are not claimed to obey an exact millisecond bound.
- Optional timed file locking uses `LOCK_NB`, checks before each attempt and immediately after acquisition, and closes/unlocks on failure. Default locking remains unchanged. Timeout is safe 503, never proof for credential removal.
- Only ephemeral endpoint-owned clients use the cancellable HTTP path, called inside `invoke` threads. Status and capabilities share that same client/deadline. Keep Central's decoded 2 MiB/error whitelist, scope checks, no-proxy/no-redirect behavior, refresh serialization/durable uncertain fence and single GET repair. Public capabilities has no bearer or refresh. No paid submits or automatic auth actions.

Execution:

- [x] Freeze baseline source/test pins; add meaningful behavior RED for real file-lock contention, exhausted/admitted-late workers, aggregate me/caps budget, stalled headers/body, permitted refresh/repair and durable unknown rotation. No real accounts/network/providers in these tests.
- [x] Minimal implementation in `session.py`, `central.py`, `central_web.py`; new focused test module. Default-sync clients, including callers in an event loop, preserved; no frontend change.
- [x] Independent R1/R2 review and final nine-file regression: 143 passed. R1 DNS cleanup P2 reproduced and repaired with an owned host/port-only resolver child; independent R2 proved termination/reap before lock availability, and closed the finding.
- [x] Current HTTPX documentation checked; no dependency changes.
- [x] v35 built, static artifact checks passed, fresh private backup verified, and original17868/origin/profile activated. The unchanged web build ID is checked alongside actual v35 executable/listener identity; the old operator's changed-ID assumption and subsequent finite status timeout are preserved.
- [x] [Delivery and evidence limits](../../validation/2026-10-30-workbench-v35-delivery.md) recorded; 31-file safe closure rehashed. Actual card retry returned to authenticated menu. No model replay, commit/push/merge.

Independent email continuity validation may proceed against an explicitly owned ephemeral test database. It does not prove SMTP delivery or authorize a real password reset or email send.
