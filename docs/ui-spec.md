# SoundVault — Self-Hosted Torrent Streamer · UI Design Spec

Stack: React + Vite + Tailwind CSS · Theme: dark only · Icons: `lucide-react` (stroke 1.75px)

---

## 1. Color Tokens

### 1.1 Backgrounds & Surfaces

| Token | Hex | Usage |
|---|---|---|
| `bg-base` | `#0A0B0E` | App root, sidebar background |
| `bg-main` | `#0E1014` | Main scroll canvas |
| `surface-1` | `#15171D` | Cards (album, torrent section) |
| `surface-2` | `#1B1E26` | Card hover, raised rows, table headers |
| `surface-3` | `#232733` | Modals, dropdowns, popovers, tooltips |
| `surface-inset` | `#0B0C10` | Inputs, seek/volume track, progress track |
| `border-subtle` | `#262A33` | Card borders, dividers |
| `border-strong` | `#3A3F4B` | Input borders, focus-adjacent, slider thumbs border |
| `overlay` | `#050608B8` | Modal scrim (72% black) |

### 1.2 Accent (primary)

| Token | Hex | Usage |
|---|---|---|
| `primary` | `#1FDB64` | Play buttons, active states, seek fill, primary CTA |
| `primary-hover` | `#4CE682` | Hover on primary |
| `primary-pressed` | `#17B552` | Active/pressed |
| `primary-tint` | `#1FDB641F` | Selected row bg, active nav item bg (12% alpha) |
| `on-primary` | `#04120A` | Text/icons on primary |

### 1.3 Text

| Token | Hex | Usage |
|---|---|---|
| `text-primary` | `#F4F6F9` | Titles, track names, headings |
| `text-secondary` | `#A7ADBA` | Artist names, meta, descriptions |
| `text-muted` | `#6E7482` | Timers, captions, placeholders, inactive icons |
| `text-disabled` | `#484D58` | Disabled controls |

### 1.4 Status

| Token | Hex | Tint bg (12%) | Usage |
|---|---|---|---|
| `success` | `#3ECF8E` | `#3ECF8E1F` | Downloaded / ready, seeds ≥ 10 |
| `warning` | `#FFB224` | `#FFB2241F` | No peers, seeds 1–9, sleep timer active |
| `danger` | `#F2485B` | `#F2485B1F` | Error, delete, seeds 0 |
| `info` | `#4C9AFF` | `#4C9AFF1F` | Queued, processing state |

### 1.5 Format badges

| Variant | bg | text | border |
|---|---|---|---|
| `16/44` (CD) | `#262A33` | `#A7ADBA` | transparent |
| `24/96` (Hi-Res) | `#3D2E0E` | `#F0B429` | `#7A5B17` 1px |

### 1.6 Playback chrome

| Token | Hex |
|---|---|
| `seek-track` | `#262A33` |
| `seek-buffered` | `#3A3F4B` |
| `seek-fill` | `#1FDB64` (hover state of bar), default `#F4F6F9` |
| `scrollbar-thumb` | `#3A3F4B` (hover `#4A5060`), track transparent |

---

## 2. Typography

**Families**
```css
--font-ui:      "Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
--font-display: "Space Grotesk", "Inter", sans-serif;   /* page headers */
--font-mono:    "JetBrains Mono", ui-monospace, monospace; /* timers, seeds, sizes, bitrates */
```
Numeric UI values also get `font-feature-settings: "tnum"` on Inter.

| Style | Font | Size / Line | Weight | Tracking | Usage |
|---|---|---|---|---|---|
| `display` | Space Grotesk | 30px / 38px | 700 | -0.02em | Page title ("Library") |
| `h1` | Inter | 24px / 32px | 700 | -0.01em | Modal title, section page |
| `h2` | Inter | 19px / 26px | 600 | 0 | Section header ("Jazz"), artist name |
| `h3` | Inter | 16px / 22px | 600 | 0 | Card album title, modal subhead |
| `body` | Inter | 14px / 20px | 400 | 0 | Track title, torrent name |
| `body-m` | Inter | 14px / 20px | 500 | 0 | Buttons, active track title |
| `caption` | Inter | 12.5px / 17px | 500 | 0 | Artist under album, meta rows |
| `micro` | Inter | 11px / 14px | 600 | +0.07em, uppercase | Badges, filter labels, section eyebrows |
| `mono-sm` | JetBrains Mono | 12px / 16px | 500 | 0 | Timers, seed counts, file size, % |
| `mono-xs` | JetBrains Mono | 10.5px / 14px | 500 | +0.02em | Mini-player progress, status labels |

---

## 3. Spacing / Radius / Shadow / Elevation

### 3.1 Spacing (4px base)

| Token | px | rem | Usage |
|---|---|---|---|
| `s-1` | 4 | 0.25 | icon gaps, badge padding-y |
| `s-2` | 8 | 0.5 | badge padding-x, dense rows |
| `s-3` | 12 | 0.75 | card padding, row gaps |
| `s-4` | 16 | 1 | default component padding |
| `s-5` | 20 | 1.25 | grid gaps (desktop) |
| `s-6` | 24 | 1.5 | page padding-x (desktop)

---

| `s-7` | 28 | 1.75 | section header → content gap |
| `s-8` | 32 | 2 | page padding-y, modal padding |
| `s-10` | 40 | 2.5 | between major sections |
| `s-12` | 48 | 3 | empty-state blocks, modal header |
| `s-16` | 64 | 4 | reserved (scroll bottom pad calc) |

### 3.2 Radius / Shadows / Elevation — конкретные значения

**Radius**

| Token | px | Usage |
|---|---|---|
| `r-xs` | 4 | format badges, status pills, progress track |
| `r-sm` | 6 | inputs, filter chips, small buttons |
| `r-md` | 8 | album cards, track/torrent rows, nav items, toasts |
| `r-lg` | 12 | modals, dropdowns, popovers, queue panel |
| `r-xl` | 16 | mobile bottom sheets (top corners only) |
| `r-full` | 9999 | play buttons, avatars, slider thumbs, seek bar |

**Shadows** (все с черным `#000`)

| Token | Value | Usage |
|---|---|---|
| `shadow-1` | `0 1px 2px rgb(0 0 0 / .40)` | card resting state |
| `shadow-2` | `0 4px 16px rgb(0 0 0 / .45)` | card hover, raised rows |
| `shadow-3` | `0 8px 24px rgb(0 0 0 / .50), 0 2px 6px rgb(0 0 0 / .40)` | dropdown, popover, toast |
| `shadow-4` | `0 16px 48px rgb(0 0 0 / .60)` | modals, bottom sheets |
| `shadow-player` | `0 -4px 16px rgb(0 0 0 / .45)` | PlayerBar / mini-player top edge |
| `focus-ring` | `0 0 0 2px #0A0B0E, 0 0 0 4px #1FDB64` | keyboard focus (`:focus-visible`) |

**Elevation scale (surface × shadow × z-index)**

| Level | Surface | Shadow | z-index | Examples |
|---|---|---|---|---|
| E0 | `bg-base` | — | 0 | app shell |
| E1 | `surface-1` | `shadow-1` | 1 | cards, player bar |
| E2 | `surface-2` | `shadow-2` | 10 | hover states, sticky TopBar |
| E3 | `surface-3` | `shadow-3` | 40 | dropdowns, popovers |
| E4 | `surface-3` | `shadow-4` | 50 | modals (scrim z-45) |
| E5 | `surface-3` | `shadow-3` | 60 | toasts (topmost) |

---

## 4. Component Inventory

Общие state-имена: `default | hover | active | focus-visible | disabled | loading | error`. Transition по умолчанию: `150ms ease-out` (color/bg), `200ms cubic-bezier(.2,.8,.2,1)` (transform/shadow).

### `Sidebar` — `components/Sidebar.tsx`
```ts
interface SidebarProps { collapsed: boolean; onToggle(): void; counts: { albums: number; queue: number } }
```
- Container: `w-60` (240px), collapsed `w-[68px]`; `bg-bg-base border-r border-subtle h-screen sticky top-0 flex flex-col`.
- Logo: `h-16 px-4`, `Vault` icon 24px primary + wordmark Space Grotesk 18/700 `text-primary` (hidden when collapsed).
- NavItem: `h-10 px-3 mx-2 r-md flex gap-3 items-center`, icon 20px + `body-m`. States: default `text-secondary`; hover `bg-surface-2 text-primary`; active `bg-primary-tint text-primary font-500` + left bar `w-[3px] h-5 r-full bg-primary`.
- Items: Library (`LibraryBig`), Queue (`ListMusic` + count badge), Trackers (`Rss`), Settings (`Settings` — прижат к низу, `mt-auto`).
- Badge count: `mono-xs px-1.5 py-0.5 r-xs bg-surface-3 text-secondary`.
- Collapse toggle: `ChevronsLeft`, 28px, `text-muted hover:text-primary`, в футере `p-3 border-t border-subtle`.

### `TopBar` — `components/TopBar.tsx`
```ts
interface TopBarProps { onSearch(q: string): void; onAddTracker(): void; canBack: boolean; canForward: boolean }
```
- `h-16 px-6 sticky top-0 z-10 bg-bg-main/80 backdrop-blur-md flex items-center gap-4`.
- History buttons: 32px circle `bg-surface-2 r-full`, icon 18px; disabled → `text-disabled`, bg `surface-1`.
- Search: `h-9 w-[400px] max-w-[40vw] bg-surface-inset r-sm border border-transparent`, focus → `border-strong` + `focus-ring`; icon `Search` 16px `text-muted` слева, placeholder `caption text-muted`.
- Right: primary button "Add tracker" (`h-9 px-4 r-sm bg-primary text-on-primary body-m`, hover `primary-hover`, active `primary-pressed`) + avatar 32px `r-full`.

### `AlbumCard` — `components/AlbumCard.tsx`
```ts
interface AlbumCardProps { album: Album; onOpen(id: string): void; onPlay(id: string): void }
```
- `p-3 r-md bg-surface-1 border border-subtle shadow-1 w-full`; hover → `bg-surface-2 shadow-2 -translate-y-0.5`.
- Cover: `aspect-square w-full r-sm object-cover` + format badge top-left (`micro px-2 py-0.5 r-xs`, variants из §1.5).
- Play FAB: 40px `r-full bg-primary shadow-2`, icon `Play` 18px fill `on-primary`, absolute `right-4 bottom-[60px]`; hidden → visible на hover (`opacity-0 translate-y-1` → `opacity-100 translate-y-0`); hover на самой кнопке: `bg-primary-hover scale-105`.
- Title `h3 text-primary truncate`, artist `caption text-secondary truncate`.
- States: `loading` → skeleton (`bg-surface-2 animate-pulse` на cover + 2 строки); `playing` → ring `1px primary` на cover, FAB → `Pause`; `error` (no cover) → placeholder `surface-2` + `Disc3` 32px `text-muted`.

### `AlbumGrid` — `components/AlbumGrid.tsx`
```ts
interface AlbumGridProps { albums: Album[]; loading?: boolean }
```
- `grid gap-5 [grid-template-columns:repeat(auto-fill,minmax(180px,1fr))]`; <1280px → `minmax(160px,1fr)`; <768px → `minmax(150px,1fr) gap-3`.
- Section header: `h2` + divider `border-subtle` + "Show all" `caption text-muted hover:text-primary`.
- Loading: 10 skeleton-карточек. Empty: `s-12` блок, `Music2` 40px `text-muted`, `body text-secondary`.

### `AlbumModal` — `components/AlbumModal.tsx`
```ts
interface AlbumModalProps { albumId: string; open: boolean; onClose(): void }
```
- Scrim: `fixed inset-0 bg-overlay z-45`, закрытие по клику/Esc. Panel: `w-[720px] max-w-[calc(100vw-32px)] max-h-[85dvh] bg-surface-3 r-lg shadow-4 z-50 overflow-hidden flex flex-col`.
- Header `p-8 pb-6 flex gap-6`: cover 160px `r-md shadow-2`; right — eyebrow `micro text-muted` ("ALBUM · 2021"), title `h1`, artist `h2 text-secondary`, meta row `mono-sm text-muted` (tracks · duration · size), actions `mt-4 flex gap-3`.
- Buttons: primary Play (`h-10 px-6 r-sm bg-primary`); secondary Download (`h-10 px-4 r-sm border border-strong text-primary hover:bg-surface-2`); icon Delete (`Trash2`, 40px, hover `bg-danger/12 text-danger`).
- Track list: `flex-1 overflow-y-auto border-t border-subtle` — `TrackRow[]`.
- States: `downloading` → header progress (`h-1 r-full bg-surface-inset` + fill `primary`, % `mono-sm`), Download-кнопка → `loading` spinner; `error` → inline alert `bg-danger/12 text-danger r-sm p-3`.

### `TrackRow` — `components/TrackRow.tsx`
```ts
interface TrackRowProps { track: Track; index: number; state: 'idle'|'playing'|'