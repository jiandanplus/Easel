# Console overview-to-list navigation repair

Root observed a real authenticated-browser failure: overview rendered successfully, but selecting 用户 produced a blank React page and `Cannot read properties of undefined (reading 'length')` in DataPage. The previous navigation changed the page while retaining overview's ready payload until a passive effect reset data. DataPage then treated the overview object as a list.

The production delta is two narrow changes in the independent Console frontend:

- App navigation immediately cancels the previous data request and sets loading before changing page/query/offset. It also increments the existing refresh revision so selecting the already-current page does not get stuck after clearing its data.
- DataPage validates items/has_more before using list data. An incompatible payload produces an explicit retryable format error, never a fabricated empty list or a render exception.

Two regressions were added first. The real React SSR view test reproduced the exact undefined-length exception. A small hook driver runs the actual App handler, observes the transition before passive effects, and checks old-request cancellation, final users data and repeated-current-page recovery. It also covers users→overview: the first transition render is loading, the users request is aborted, and the eventual overview data renders. It does not inspect implementation text and is not represented as a browser/React scheduler acceptance test. The test loader gained optional dependency overrides solely for this harness; no runtime dependency was added.

Observed RED: **20 passed / 2 failed**, with both the stale-ready transition and undefined-length exception. Observed GREEN: **22 passed / 0 failed / 0 skipped**. TypeScript/Vite production build passed. Unified `build_console.py --verify-existing` passed (10 source / 3 output files).

New asset: `assets/index-BdRwO4kX.js`; CSS remains `index-DoPTl_OF.css`. Asset receipt SHA256: `c3fddd3e9849e2f71057f02f9ef4c3abf9086f5391f15142e5270e839651d62f`.

Only services/platform/frontend source/tests/build outputs were changed. No backend, deployment, real browser, account state, database or model action was performed in this repair task. Root owns the safe static update and real-browser retest; this report does not claim that browser acceptance has already passed.
