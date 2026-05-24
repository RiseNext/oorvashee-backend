# Oorvashee UI Design System

This file is the single source of truth for all visual decisions.
Claude Code must follow this exactly — never invent colors, fonts, or component structures.

---

## SECTION 1 — COLOR SYSTEM

### Background Colors

| Token | Hex | Usage |
|---|---|---|
| `--bg-primary` | `#F0E6D3` | Main page background — hero, collections, cart, checkout, all page shells |
| `--bg-secondary` | `#FAF6F1` | Content sections, promise blocks, lighter alternating sections |
| `--bg-card` | `#FFFFFF` | Product cards, order summary cards, form containers |
| `--bg-dark` | `#2A1308` | Saree Table banner, dark feature sections |
| `--bg-dark-deep` | `#1E0C06` | Price filter ornate cards, very dark overlays |

### Text Colors

| Token | Hex | Usage |
|---|---|---|
| `--text-primary` | `#3D1A08` | All headings, product names, nav links, prices |
| `--text-secondary` | `#6B4226` | Body paragraphs, descriptions, secondary labels |
| `--text-muted` | `#9A7055` | Captions, MRP label, "Finest Quality" sub-labels |
| `--text-on-dark` | `#FFFFFF` | Text on dark sections (Saree Table, dark banners) |
| `--text-on-dark-muted` | `#D4B896` | Subdued text on dark backgrounds |

### Brand & Accent Colors

| Token | Hex | Usage |
|---|---|---|
| `--gold` | `#C4982A` | Ornamental dividers, icon strokes, star ratings, logo ring, badge borders |
| `--gold-light` | `#E8C96A` | Gold shimmer highlights, hover states on gold elements |
| `--cta-fill` | `#7A4B15` | Primary button fill, active filter pills, active nav underline, checkout step fill |
| `--cta-fill-hover` | `#5E3810` | Hover state on primary buttons |

### Border & Divider Colors

| Token | Hex | Usage |
|---|---|---|
| `--border-default` | `#E5D5BC` | Card borders, input borders, dividers |
| `--border-focus` | `#7A4B15` | Input focus ring, active tab underline |
| `--border-light` | `#F0E0CC` | Subtle section separators |

### Semantic / UI State Colors

| Token | Hex | Usage |
|---|---|---|
| `--star-fill` | `#C4982A` | Star rating filled |
| `--star-empty` | `#E0CDB0` | Star rating empty |
| `--badge-bg` | `#F5EAD8` | "Bestseller", "NEW" badge background |
| `--badge-text` | `#7A4B15` | Badge text color |

### CSS Variables Block (paste into `globals.css`)

```css
:root {
  /* Backgrounds */
  --bg-primary: #F0E6D3;
  --bg-secondary: #FAF6F1;
  --bg-card: #FFFFFF;
  --bg-dark: #2A1308;
  --bg-dark-deep: #1E0C06;

  /* Text */
  --text-primary: #3D1A08;
  --text-secondary: #6B4226;
  --text-muted: #9A7055;
  --text-on-dark: #FFFFFF;
  --text-on-dark-muted: #D4B896;

  /* Brand */
  --gold: #C4982A;
  --gold-light: #E8C96A;
  --cta-fill: #7A4B15;
  --cta-fill-hover: #5E3810;

  /* Borders */
  --border-default: #E5D5BC;
  --border-focus: #7A4B15;
  --border-light: #F0E0CC;

  /* UI States */
  --star-fill: #C4982A;
  --star-empty: #E0CDB0;
  --badge-bg: #F5EAD8;
  --badge-text: #7A4B15;
}
```

---

## SECTION 2 — TYPOGRAPHY

### Font Families

```css
/* Display / Headlines */
font-family: 'Cormorant Garamond', Georgia, serif;

/* Body / UI */
font-family: 'DM Sans', system-ui, sans-serif;
```

Import in `layout.tsx`:
```ts
import { Cormorant_Garamond, DM_Sans } from 'next/font/google'

const cormorant = Cormorant_Garamond({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-display',
})

const dmSans = DM_Sans({
  subsets: ['latin'],
  weight: ['300', '400', '500'],
  variable: '--font-body',
})
```

### Type Scale

| Role | Font | Size | Weight | Case |
|---|---|---|---|---|
| Hero headline | Cormorant Garamond | 56–72px | 500 | Sentence |
| Page title | Cormorant Garamond | 40–48px | 500 | Sentence |
| Section heading | Cormorant Garamond | 28–36px | 400 | Sentence |
| Product name (card) | Cormorant Garamond | 18–20px | 400 | Sentence |
| Product name (PDP) | Cormorant Garamond | 32–36px | 500 | Sentence |
| Price | DM Sans | 20–24px | 500 | — |
| Body paragraph | DM Sans | 14–16px | 300–400 | Sentence |
| Nav links | DM Sans | 13–14px | 400 | UPPERCASE |
| Labels / badges | DM Sans | 11–12px | 400–500 | UPPERCASE, tracked |
| CTA button text | DM Sans | 13–14px | 500 | UPPERCASE, tracked |
| Caption / muted | DM Sans | 12px | 300 | Sentence |

### Letter Spacing
- Nav links: `tracking-widest` (0.15em)
- CTA buttons: `tracking-wider` (0.1em)
- Section labels (e.g. "OUR PROMISE", "KANCHIPURAM SILK"): `tracking-widest`
- Body: default (0)

---

## SECTION 3 — COMPONENT PATTERNS

### 3.1 Ornamental Divider

Used under headings, above subtext, between major sections.

```tsx
// components/shared/OrnamentalDivider.tsx
// Thin horizontal line with a centered gold decorative motif
// Color: var(--gold) #C4982A
// Use the SVG ornament from /public/images/ui/ornament-divider.svg
```

Structure: `——— ❖ ———` style, ~120–180px wide, centered or left-aligned under headings.

---

### 3.2 Navbar

- **Layout**: Logo left → Nav links center → Search + Cart + Hamburger right
- **Background**: `var(--bg-primary)` / transparent on full-bleed hero
- **Logo**: Circular coin medallion (gold, woman's profile). File: `/public/images/ui/logo.png`
- **Nav links**: DM Sans, uppercase, `var(--text-primary)`, `tracking-widest`, text-sm
- **Active link**: underline `2px solid var(--cta-fill)`, no bold change
- **Cart icon**: with count badge (gold circle, white number)
- **Separator**: no bottom border on transparent, subtle border on solid bg

---

### 3.3 Hero Section

- **Height**: 85–100vh
- **Background**: `var(--bg-primary)`
- **Background layer**: faint pencil-style temple/palace illustration, very low opacity (~8–12%), color `var(--gold)`. File: `/public/images/ui/temple-illustration.png`
- **Layout**: left half (text) + right half (model photography, full bleed to right edge)
- **Left content stack**:
  1. Hero headline — Cormorant Garamond, 56–72px, `var(--text-primary)`
  2. Ornamental divider
  3. Subtext with flanking `✦` symbols — italic, DM Sans, `var(--text-secondary)`
  4. Outlined CTA button
- **Trust bar** (bottom of hero or just below):
  3–4 icon + label + caption columns, `var(--gold)` icons, `var(--text-primary)` labels

---

### 3.4 Buttons

#### Primary (filled)
```tsx
// bg: var(--cta-fill) #7A4B15
// hover: var(--cta-fill-hover) #5E3810
// text: white, uppercase, tracking-wider, DM Sans 500
// padding: px-6 py-3
// border-radius: rounded (4–6px)
// icon: optional arrow → right
```

#### Secondary (outlined)
```tsx
// bg: transparent
// border: 1.5px solid var(--cta-fill)
// text: var(--cta-fill), uppercase, tracking-wider
// hover: bg var(--cta-fill), text white
// same padding + radius as primary
```

#### Ghost / Link
```tsx
// No border, no bg
// text: var(--text-primary) or var(--cta-fill)
// uppercase, tracking-wider, text-xs/sm
// Arrow → appended: "EXPLORE SAREE →"
```

---

### 3.5 Product Card (Grid)

Used in: Collections page (3-col), "You May Also Love" (4-col), New Arrivals.

```
┌─────────────────────────┐
│  [Product Image]        │  ← Full bleed, 3:4 aspect ratio
│  [♡ wishlist]   top-right│  ← White circle icon, absolute
│  [Bestseller/NEW] badge │  ← Pill badge top-left (optional)
├─────────────────────────┤
│  Product Name (serif)   │  ← Cormorant Garamond, text-lg
│  ₹ XX,XXX               │  ← DM Sans 500, text-base
│  EXPLORE SAREE →   [●]  │  ← Ghost link left + color swatch right
└─────────────────────────┘
```

- **Card bg**: `var(--bg-card)` white
- **Card border-radius**: `rounded-lg` (8px)
- **Card shadow**: none or `shadow-sm`
- **Image border-radius**: top corners only, same as card
- **Info padding**: `px-4 py-3`
- **Wishlist heart**: white circle bg, `var(--text-primary)` icon, absolute top-right `m-3`
- **Color swatch**: single circle 22–26px, absolute bottom-right of info area, represents current colorway
- **"EXPLORE SAREE →"**: DM Sans, 11px, uppercase, `tracking-widest`, `var(--cta-fill)`

#### "You May Also Love" variant (cart/PDP)
Same card but with star rating below price and "ADD TO CART" button at bottom.

---

### 3.6 Collection Cards (Homepage grid)

4-column grid.

```
┌───────────────────┐
│  [Image]          │  ← Full bleed, ~1:1.2 ratio
├───────────────────┤
│  COLLECTION NAME  │  ← DM Sans, uppercase, text-sm, tracked
│  tagline text     │  ← DM Sans, text-xs, var(--text-muted)
│                [→]│  ← Arrow button circle, right-aligned
└───────────────────┘
```

- Same card treatment as product card
- Arrow button: white circle with `→` in `var(--text-primary)`

---

### 3.7 Filter Pills (Collections page)

```
[All Sarees]  Wedding  Festive  Traditional  Bridal  Sale
```

- **Active**: bg `var(--cta-fill)`, text white, `rounded-full`, px-4 py-1.5
- **Inactive**: bg transparent, border `1px solid var(--border-default)`, text `var(--text-primary)`, same size
- **Hover on inactive**: border `var(--cta-fill)`, text `var(--cta-fill)`
- Sort by dropdown: right-aligned, DM Sans, text-sm
- Filter button: icon + "Filter" label, right side

---

### 3.8 Product Detail Page (PDP)

**Left column:**
- Vertical thumbnail strip (5 thumbnails, ~70×90px each)
- Main image large (~480–520px wide)
- "Click or pinch to zoom" caption below
- "Bestseller" badge: top-left of main image, pill shape, `var(--badge-bg)` + `var(--badge-text)`
- Wishlist heart: top-right of main image

**Right column:**
- Collection label: DM Sans, uppercase, `tracking-widest`, text-xs, `var(--gold)` — e.g. "KANCHIPURAM SILK"
- Product name: Cormorant Garamond, 32–36px
- Price: DM Sans 600, 24px, `₹ XX,XXX`
- "MRP inclusive of all taxes": DM Sans 300, text-xs, `var(--text-muted)`
- Star rating: filled `var(--star-fill)`, count in parentheses
- Description: DM Sans 300–400, text-sm/base
- Feature icons row: 4 icons (Pure Silk | Handwoven | Zari Weave | 6.3m) — icon + bold label + caption
- COLOR label + color swatches (22px circles, border when active)
- QTY: `− 1 +` inline controls
- ADD TO CART: primary filled button, full width
- BUY NOW: secondary outlined button, full width
- Trust row: Free Shipping | Easy Returns | Secure Payment — icon + label + caption

**Tab section (below fold):**
- Tabs: DESCRIPTION | DETAILS | CARE INSTRUCTIONS | SHIPPING & RETURNS
- Active tab: `var(--border-focus)` underline
- Detail images: 3-column fabric close-up grid

---

### 3.9 Cart Page

**Cart items list (left):**
- Item row: image (100×130px) + info + price + remove ×
- Info: name (serif), collection label, Color chip + label, Length
- "Move to Wishlist" ghost link below
- Qty: `− 1 +` inline
- Divider between items: `var(--border-light)`

**Order Summary (right sidebar):**
- Card: bg white, `var(--border-default)` border, `rounded-xl`
- Rows: Subtotal / Shipping / Tax / Total
- Total: Cormorant Garamond, large, bold
- PROCEED TO CHECKOUT: primary filled button, full width
- CONTINUE SHOPPING: outlined button, full width
- "WE ACCEPT" row: Visa, Mastercard, UPI, Paytm, Razorpay logos
- "100% SECURE PAYMENT" trust badge block

---

### 3.10 Checkout Stepper

4-step linear stepper at top of checkout:

```
[①] SHIPPING      [2] PAYMENT      [3] REVIEW      [4] CONFIRMATION
 Delivery Address    Payment Method   Review Your Order   Order Confirmed
```

- **Active step**: filled circle `var(--cta-fill)`, white number, bold label
- **Upcoming steps**: outlined circle `var(--border-default)`, muted number, muted label
- **Completed steps**: filled circle `var(--gold)` with checkmark
- Connecting line: `var(--border-default)`, horizontal, thin

**Form fields:**
- Label above (DM Sans, text-sm, `var(--text-secondary)`)
- Input: full border `var(--border-default)`, focus `var(--border-focus)`, `rounded`, px-3 py-2.5
- Required asterisk: `var(--cta-fill)`

**Delivery options:**
- Radio card: full-width card, border `var(--border-default)`
- Selected: border `var(--cta-fill)`, radio filled `var(--cta-fill)`

---

### 3.11 Saree Table (Dark Banner Section)

Full-width dark section used on homepage and collections page.

- **Background**: `var(--bg-dark)` `#2A1308`
- **Decorative border**: thin gold line frame inset ~8px, `var(--gold)`
- **Left content**: label "UNFOLD THE ELEGANCE ✦" uppercase small, headline serif white, ornamental divider (gold), body text `var(--text-on-dark-muted)`, 4 icon+label mini features, primary CTA button
- **Right content**: hero image of folded sarees on wooden surface + brass lamp
- **Image folder**: `/public/images/saree-table/`

---

### 3.12 Price Filter Cards ("Sarees For Every You")

Ornate Indian arch/frame style cards, 4-column.

- **Card background**: deep `#1E0C06` → `#3A1A08` gradient
- **Frame**: gold decorative arch border SVG overlay. File: `/public/images/ui/price-card-frame.svg`
- **Text**: "Under" DM Sans uppercase small + large number Cormorant Garamond, `var(--text-on-dark)`
- **CTA**: "EXPLORE NOW →" text link below card, DM Sans text-xs uppercase
- Hanging ornamental tassels at top of frame (decorative only)

---

### 3.13 Trust / Feature Bar

Used at bottom of hero, between sections, in footer top strip.

4 columns, each:
```
[icon]
LABEL TEXT         ← DM Sans uppercase, text-xs, tracking-wider, var(--text-primary)
Caption text       ← DM Sans 300, text-xs, var(--text-muted)
```

Icons: line-art SVG style, `var(--gold)` stroke, ~24–28px

Standard icons used:
- Lotus / leaf → Authentic Weaves / Pure Silk
- Spool / loom → Heritage Craftsmanship
- Shield / check → Premium Quality
- Heart → Made with Love
- Grid / weave → Handwoven

Icon files: `/public/images/icons/` (SVG)

---

### 3.14 Section Labels ("OUR PROMISE", "JUST IN")

Small uppercase labels that appear above section headings.

```
✦ OUR PROMISE ✦
```

- DM Sans, uppercase, `tracking-widest`, text-xs, `var(--gold)` or `var(--cta-fill)`
- Flanking `✦` or `+` symbols in same color
- Ornamental divider follows below the main heading

---

### 3.15 Breadcrumb

```
Home  ›  Collections  ›  Kanchipuram Silk
```

- DM Sans, text-sm, `var(--text-muted)`
- Separator `›` in `var(--text-muted)`
- Current page: `var(--text-primary)`, no link

---

### 3.16 Footer

**Top trust bar** (above main footer):
4-column feature strip, same as Trust Bar pattern.

**Main footer** (dark or matching `--bg-primary`):
- 4 columns: Logo+tagline | SHOP links | HELP links | ABOUT links + NEWSLETTER
- Logo: same circular medallion
- Tagline: "Timeless Weaves", DM Sans italic, `var(--text-muted)`
- Column headers: DM Sans uppercase, text-xs, `tracking-wider`, `var(--text-primary)`
- Links: DM Sans 300, text-sm, `var(--text-secondary)`, hover `var(--cta-fill)`
- Newsletter: email input + arrow submit button (bg `var(--cta-fill)`)
- Social icons: Instagram, Facebook, Pinterest, YouTube — `var(--text-secondary)` fill

**Bottom bar:**
- "© 2024 Saree House. All Rights Reserved." left
- "Privacy Policy | Terms & Conditions" right
- Text: DM Sans text-xs, `var(--text-muted)`
- Divider above: `var(--border-light)`

---

## SECTION 4 — IMAGES & ASSETS

### Asset Folder Structure

```
public/
├── images/
│   ├── ui/
│   │   ├── logo.png                    ← Circular gold coin logo (used in navbar + footer)
│   │   ├── temple-illustration.png     ← Faint pencil-style palace bg for hero
│   │   ├── ornament-divider.svg        ← Center ornament for dividers ——❖——
│   │   └── price-card-frame.svg        ← Gold arch frame for price filter cards
│   ├── icons/
│   │   ├── lotus.svg
│   │   ├── spool.svg
│   │   ├── shield-check.svg
│   │   ├── heart.svg
│   │   ├── weave-grid.svg
│   │   └── temple-arch.svg
│   ├── hero/                           ← Hero model photography
│   ├── collections/                    ← Collection banner images
│   ├── products/                       ← Product images
│   └── saree-table/                    ← Folded sarees + brass lamp image
```

### Image Usage Rules
- Hero model photo: right half of hero, no filter, natural lighting preserved
- Temple illustration: absolute positioned behind hero text, opacity 8–12%, do NOT use as a filter or overlay on the model
- Product images: always 3:4 portrait ratio
- Collection images: flexible ratio but consistent within the grid (1:1.2 or 3:4)

---

## SECTION 5 — SPACING & LAYOUT

- **Max content width**: 1200–1280px, centered
- **Page horizontal padding**: `px-6 md:px-10 lg:px-16`
- **Section vertical spacing**: `py-16 md:py-20 lg:py-24`
- **Grid gaps**: `gap-6` for product grids, `gap-4` for collection grids
- **Card border-radius**: `rounded-lg` (8px) — consistent across all cards
- **Input border-radius**: `rounded` (4–6px)
- **Button border-radius**: `rounded` (4–6px) — NOT pill-shaped unless filter pills

---

## SECTION 6 — WHAT NOT TO DO

- Never use `bg-white` as a page background — always `var(--bg-primary)` or `var(--bg-secondary)`
- Never use purple, blue, or cool-tone accents anywhere
- Never use Inter, Roboto, or system-ui as the display font
- Never round product card images to circles or heavy radius
- Never put heavy drop shadows on cards — the design is flat with very subtle shadow
- Never use gradient backgrounds on CTA buttons — solid fill only
- Never skip the ornamental divider after a section heading — it is always present
- Never use a filled badge that isn't `var(--badge-bg)` + `var(--badge-text)` or dark-on-dark variants
- Never use the price filter card style (ornate arch) for anything other than price brackets