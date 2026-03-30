# Reference: Frontend Review

---

## Section 1 — Design Handoff Checklist

Source pin: frontend-packet/Front-End-Design-Checklist-master/README.md (local copy, 2024-12-10)

### Design requirements checklist
- **Grid system:** Provide explicit grid specs (width, gutters, columns). Use the same grid across templates.
- **Colors:** Name colors by purpose or palette. Define component color states. Ensure key colors are easy to access.
- **Fonts and text:** Provide webfont formats (WOFF/WOFF2/TTF). Include fallback stacks. Keep total font weight reasonable. Use real copy and plan for longer strings.
- **Links and navigation:** Define default, hover, focus, active, visited states. Include current-page states.
- **Images and icons:** Provide a 512×512 PNG favicon. Deliver icons as SVGs with consistent dimensions and naming (`icon-` prefix).
- **Forms and buttons:** Provide form titles/legends, input states (focus/disabled), error text/placement/color, required markers, primary/secondary buttons, button states, and loading indicators.
- **Responsive design:** Provide mobile design before or alongside desktop. Provide tablet design when needed. Expect non-pixel-perfect adaptation.
- **Style guide and components:** Use component-based approach. Provide a style guide listing elements, components, styles, and dimensions.
- **Delivery files:** Provide PSD/Sketch with correct dimensions. Clean layers. Include 404/500, popups, alerts. Use layer comps for multiple views.

### Pre-work
- Confirm design tool versions and artboard widths.
- Watch for heavy shadows/gradients that impact performance.
- Confirm sitemap/breadcrumbs and retina image requirements.
- Paper analysis: structure sections, heading hierarchy, component grouping, CSS-first visuals, consistency checks.

---

## Section 2 — Token and Component Rubric

Source pins:
- frontend-packet/Awesome-Design-Tokens-main/README.md (local copy, 2024-10-17)
- frontend-packet/Front-End-Design-Checklist-master/README.md (local copy, 2024-12-10)

### Design token expectations
- Tokens are the shared contract between design and code. Prefer tokens over raw values.
- Use a clear hierarchy: reference tokens → semantic tokens → component tokens.
- Keep naming consistent and purpose-driven (role-based, not color-based).
- Avoid one-off values that bypass the token system.

### How to find tokens in a real repo

**Tailwind:**
- `tailwind.config.js`, `tailwind.config.ts`, `tailwind.config.cjs`, `tailwind.config.mjs`
- Look for `theme.extend.colors`, `spacing`, `fontFamily`, `borderRadius`.
- Look for semantic patterns: `primary`, `foreground`, `muted`, `accent` — not `blue-500`.

**CSS variables:**
- Global stylesheets (`src/styles/*`, `globals.css`) containing `:root { --token-name: ... }` or theme scopes (`.dark { ... }`).

**Token JSON / design-system folders:**
- `tokens.json`, `tokens/*.json`, `design-tokens/*`, or Style Dictionary outputs. Prefer the source-of-truth folder.

### Search patterns (find violations quickly)
- Hex colors: `#[0-9a-fA-F]{3,8}`
- Color functions: `rgb(`, `rgba(`, `hsl(`, `hsla(`
- Inline styles: `style=`
- Tailwind arbitrary values: `\[#[0-9a-fA-F]{3,8}\]`, `\[[0-9.]+(px|rem|em)\]`
- Hardcoded spacing in CSS: `: [0-9.]+(px|rem|em);`

### Replacement patterns (raw → token)

**Tailwind classes:**
```diff
- <div class="bg-[#0f172a] text-[#f8fafc] px-[18px]">
+ <div class="bg-primary text-primary-foreground px-4">
```

**CSS variables:**
```diff
- color: #111827;
+ color: var(--text);
```

Rules:
- Prefer existing semantic tokens over adding new ones.
- If a needed token does not exist, propose it explicitly and document in `conformance_notes.md`.
- If tokens exist in two competing systems, do not pick one silently — escalate.

### Component and style guide expectations
- Use component-based approach for reuse and consistency.
- Reuse existing components before creating new ones.
- Interactive states must be covered consistently across components.

### Asset naming conventions
When new assets are added, enforce consistent structure:
- Folder structure: `images/background/`, `images/icons/`, `images/layout/`
- Prefixes: background images = `bg-*`, icons = `icon-*`, hero banners = `hero-*` or `banner-*`.
- If a PR adds assets that do not follow conventions, request renames before merge.
