# Easel appearance

## Current theme: A · 纸感编辑室

Default theme id: `paper`. Approved visual references: `design/UI-v2-20261015/A/01.png` through `22.png`. Implemented shared palette and component treatment live in `web/frontend/src/styles/paper.css`.

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

`web/frontend/src/lib/theme.ts` owns availability, initialization and persistence under `easel_theme`. The bootstrap in `index.html` must agree with its default. A remains the default; old light/dark or unknown preferences migrate to A. Appearance choice is local to the current browser, with guarded storage and cross-tab synchronization. Valid B–E preferences survive reload. Existing tabs running older app code should be refreshed after upgrading.

Theme changes update presentation without remounting the underlying business page. Unsent chat drafts survive opening settings and switching themes. Hero artwork and theme previews are local bundled assets; interface text and controls remain real DOM.

Responsive behavior: mobile navigation becomes an icon rail, account identities become stacked rows/cards, environment dependencies use one column, hotspot columns shrink to the available width, and the calendar/settings tables retain intentional local scrolling. E's account table becomes stacked before its action columns can be clipped.

Acceptance screenshots and browser measurements: `outputs/_run/all-themes/`. The numbered main-page screenshots map to 工作台, 对话, 热点雷达, 选题库, 爆款拆解, 技能库, 内容库, 内容日历, 发布中心, 账号, 画像. Other filenames identify settings channels and detail/modal/mobile states.

UI examples in mockups are visual references. Existing real account, content, schedule, profile and model data remain authoritative. Do not seed demo content just to match a screenshot. Preserve existing business/security callbacks when changing presentation.
