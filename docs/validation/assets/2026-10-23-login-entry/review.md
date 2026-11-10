# Sidebar account entry incremental review

**Specification: PASS. Code quality: PASS. No blocking finding in the reviewed increment.** Compared the supplied before snapshots with current App, Sidebar, SettingsPanel, appearance CSS and navigation test; read the new SidebarAccount component and its regression tests. No source modification, test/build execution, server/auth/model call or private-state read was performed.

- `App.tsx:61,804,848` extends only the existing settings section type and adds `onOpenAccount → openSettings('account')`. It neither calls auth/start nor selects/creates a chat. The existing page remains mounted behind `hidden={settingsOpen}`; stream controllers, chat identity and handlers are unchanged.
- `SettingsPanel.tsx:20` accepts the existing account section. Existing sectionRequest navigation and PlatformAccountSettings/CentralAccountSettings rendering are reused. Backend task-active/account-change guards and the established login/logout/cancel flow are untouched.
- `SidebarAccount.tsx:15-51` performs only centralStatus reads on mount, focus and settingsOpen changes. There is no timer/polling or auth mutation. Each read clears displayed identity, aborts its predecessor and accepts results only while mounted, un-aborted and still current. Cleanup removes the focus listener and blocks late writes; even a transport ignoring abort cannot restore obsolete identity.
- `SidebarAccount.tsx:27-35,53-56` treats the real `configured=false` / `not_configured` response as the expected unavailable-platform state. Other reported status errors and transport errors clear identity and show “状态待确认”. Email/id is displayed only from configured, explicitly authenticated, successfully read status. Retained account/scope fields alone cannot imply login; raw errors are not rendered. The entry remains usable for recovery and explanation.
- `Sidebar.tsx:170-183` places the account entry in a footer above existing runtime/appearance controls. Semantic theme tokens govern surface/text/border/focus colors. At <=620px the native button remains 40px with icon, aria-label and title; only copy/chevron are hidden. Keyboard focus has an explicit visible outline. The footer does not shrink; existing navigation/history scroll rules remain. This is source-level accessibility/layout review, not a browser measurement of every theme or screen size.
- Regression source covers configured/unconfigured and authenticated/unknown states, stale account fields, read errors, late aborted responses, cleanup, single refresh triggers, and navigation-only click behavior. Navigation tests cover repeated account/model/theme section requests. Root reports **226 frontend tests passed plus TypeScript/Vite build passed**; not rerun by this reviewer.

Proceed with the v4 package and actual browser placement check. No core runtime/provider/auth implementation changed in this increment; it does not require new paid model calls merely to validate the navigation/status UI. Do not describe the status endpoint read as a commercial login validation.

Reviewed SHA256:

```
c2faa6cbd420e4be097a8c223628d3ef32cae06db9daec2e93133f436ea9d601  SidebarAccount.tsx
9de1ff99ac955962c33abd0ab1faa439eaa106c9991c207eaafdb13778643c3b  App.tsx
985ee276a95c618267bb5c036ac257f7617a9d1f07e4c887701cf6d03bf0295c  Sidebar.tsx
87a4c670e5ddc1135c20b68331d2585e3e74ed133109056c48e483b5af8d0766  SettingsPanel.tsx
6a3850dc3e5cf4c242ae3edae03588c2e3fb837aab0002cdf0e983129f3dc89c  appearance.css
0763202d91cd000658499f5f95deea73b4ce6f9129c540fffd0c19c1e260bf73  sidebarAccount.test.mjs
a9f868a72ff6cb25143a64d6a9e3050ce29b0fc695cc4a03b4aa5f4e0bc79d1e  settingsNavigation.test.mjs
```
