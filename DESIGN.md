# Easel appearance

## Day theme: A · 纸感编辑室

Default appearance mode: follow system. Day uses `paper` (A), night uses `forest` (C). Approved visual references: `design/UI-v2-20261015/A/01.png` through `22.png`. Implemented shared palette and component treatment live in `web/frontend/src/styles/paper.css`.

- Warm paper background `#f8f5ef`, raised surface `#fffdf9`, sidebar `#f4f0e7`.
- Ink text `#282a25`, muted text `#727369`, fine border `#e6e0d5`.
- Terracotta primary action `#b74f30`, soft accent `#f3e3d7`, moss support `#657451`.
- Local sans body stack with PingFang SC / Microsoft YaHei fallbacks. Chinese editorial headings use Songti SC / Noto Serif SC / SimSun; English masthead and statistics use Georgia. No external font request.
- Restrained 5–12px radii, fine rules, quiet surfaces, minimal shadow. Functional colors retain meaning; primary actions use terracotta.
- Full grouped sidebar: 创作 / 内容运营 / 管理. Settings are an in-page workspace; keep underlying page mounted to preserve local drafts.
- Dashboard landscape is a local generated asset bundled through Vite. Typography and controls remain real DOM.

## Available themes

All five approved themes are implemented across the real application. B–E use scoped tokens and component layouts in `web/frontend/src/styles/variants.css`, loaded after the shared paper stylesheet.

| Theme | ID | Visual system |
| --- | --- | --- |
| A · 纸感编辑室 | paper | Warm paper, terracotta, serif editorial headings and landscape |
| B · 极简效率台 | minimal | Cool white, graphite, cobalt blue, compact surfaces and icon discs |
| C · 深色创作舱 | forest | Evergreen layers, lime actions, dark forest landscape and light text |
| D · 柔和彩色工作室 | pastel | Cream, sage, lilac accents, soft illustration and rounded panels; split creation/statistics on desktop |
| E · 瑞士网格系统 | swiss | Black rules, orange actions, square corners, bold typography and a greeting/statistics grid |

Primary action colors for D/E are slightly deeper than the illustration palette to preserve normal-size white-label contrast. Text, borders, status states, inputs, dropdowns, drawers and native controls follow semantic tokens. Forest alone uses the native dark color scheme.

## Theme behavior

`web/frontend/src/lib/theme.ts` owns theme availability and appearance preferences. The authoritative `easel_appearance` value atomically stores `{mode, theme}`. Missing or invalid preferences default to `system`, which observes `prefers-color-scheme: dark` and chooses A/day or C/night in real time. Explicit `light`/`dark` modes keep A/C fixed. Selecting any theme card enters `custom` mode and preserves that exact theme until a display mode is chosen again. Old `easel_theme` values no longer override the new system default; the legacy key is only mirrored for older tabs. Storage failures remain nonfatal and preference changes synchronize across current tabs.

`index.html` resolves the same preference before the stylesheet paints. Only C uses the native dark color scheme. The lower-left runtime area includes a compact current-mode icon/button, which switches to the opposite fixed day/night mode. Settings show three display-mode choices above the five theme cards and explain the current automatic/fixed/custom state. Shared controls and responsive footer styling live in `styles/appearance.css`.

Theme changes update presentation without remounting the underlying business page. Unsent chat drafts survive opening settings and switching themes. Hero artwork and theme previews are local bundled assets; interface text and controls remain real DOM.

Responsive behavior: mobile navigation becomes an icon rail, account identities become stacked rows/cards, environment dependencies use one column, hotspot columns shrink to the available width, and the calendar/settings tables retain intentional local scrolling. E's account table becomes stacked before its action columns can be clipped.

Acceptance screenshots and browser measurements: `outputs/_run/all-themes/`. The numbered main-page screenshots map to 工作台, 对话, 热点雷达, 选题库, 爆款拆解, 技能库, 内容库, 内容日历, 发布中心, 账号, 画像. Other filenames identify settings channels and detail/modal/mobile states.

UI examples in mockups are visual references. Existing real account, content, schedule, profile and model data remain authoritative. Do not seed demo content just to match a screenshot. Preserve existing business/security callbacks when changing presentation.
