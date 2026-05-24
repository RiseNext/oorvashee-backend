# Oorvashee — Project Rules for Claude Code

## Stack
Next.js 15 App Router · TypeScript (strict) · Tailwind CSS v4 · shadcn/ui · Radix UI · Zustand · TanStack Query · react-hook-form + Zod · Framer Motion · Razorpay

---

## Project Structure

```
src/
├── app/                  # Next.js App Router pages & layouts
├── components/
│   ├── ui/               # shadcn/ui + generic primitives (Button, Card, Badge, Modal, Skeleton)
│   └── shared/           # Reusable cross-feature components (ProductCard, SectionTitle, ImageReveal)
├── features/
│   ├── home/
│   ├── products/
│   ├── collections/
│   ├── cart/
│   ├── checkout/
│   └── navbar/
├── hooks/                # Shared custom hooks
├── lib/
│   ├── api/              # All API functions (no raw fetch elsewhere)
│   └── validators/       # Zod schemas
├── store/                # Zustand stores only
├── services/             # External service wrappers
├── types/                # Shared TypeScript types
├── animations/           # Shared Framer Motion variants
└── styles/
```

Each feature folder may contain: `components/`, `hooks/`, `sections/`, `loaders/`, `animations/`, `services/`

---

## Core Rules

### 1. Before Creating Anything
1. Search for an existing component, hook, store, or API function
2. Extend it with props/variants if 80% fits
3. Only create new if genuinely different

### 2. Component Placement
| Type | Location |
|---|---|
| Generic/reusable UI | `src/components/ui` or `src/components/shared` |
| Feature-specific | Inside `src/features/<feature>/components` |

Never put feature-specific code in global shared folders.

### 3. State Management
- Zustand for: cart, wishlist, UI state (drawer, navbar, modals)
- No duplicate stores. Check `src/store/` before creating one.
- Server state (products, collections, orders) → TanStack Query only

Stores:
```
src/store/cart-store.ts
src/store/wishlist-store.ts
src/store/ui-store.ts
```

### 4. Data Fetching
Flow: `src/lib/api/` → TanStack Query hook → UI component

- No raw `fetch()` inside components
- All queries need explicit `queryKey` and `staleTime`
- Paginated or infinite lists use `useInfiniteQuery`

### 5. Forms
- react-hook-form + Zod for every form, no exceptions
- Schemas live in `src/lib/validators/` or feature-specific validator file

### 6. Styling
- Tailwind CSS only. No external CSS frameworks.
- Design tokens via CSS variables — do not hardcode colors or spacing
- Component variants via `cva` (class-variance-authority)

### 7. Animations
- Framer Motion only. No other animation libraries.
- Shared variants live in `src/animations/`
- Animate: `opacity`, `transform`, `scale` — these are GPU-composited
- Never animate: `width`, `height`, `top`, `left` continuously
- Every animation must feel intentional — not decorative filler

### 8. Images & Media
- `next/image` always. Never `<img>`.
- Format: WebP or AVIF
- Always: `alt`, `sizes`, `placeholder="blur"` where applicable
- Videos: compressed, muted, lazy-loaded, used sparingly

### 9. Performance
- Dynamic import heavy sections with `next/dynamic`
- Skeleton loaders for every async section (use `loading.tsx`)
- Streaming: progressively render navbar → hero → content → recommendations
- No unnecessary re-renders — memoize where measured, not preemptively

### 10. TypeScript
- Strict mode. No `any`. No type assertions unless unavoidable.
- All shared types in `src/types/`
- API response types must be defined before use

### 11. Accessibility
- Radix UI handles most of it — do not override focus/keyboard behavior
- Semantic HTML always: `nav`, `main`, `section`, `article`, `button` (not div)
- Every interactive element must be keyboard-navigable

### 12. File Naming
- All files: `kebab-case.tsx` / `kebab-case.ts`
- Hooks: `use-<name>.ts`
- Stores: `<name>-store.ts`
- Validators: `<name>-schema.ts`

---

## Design Direction

**Aesthetic:** Luxury Indian ethnic wear. Refined. Cinematic. Unhurried.

**Typography:**
- Display: editorial serif (e.g. Cormorant Garamond, Playfair Display)
- Body: clean humanist sans (e.g. DM Sans, Jost)
- Never: Inter, Roboto, Arial as primary fonts

**Color:**
- Deep jewel tones: burgundy, forest green, midnight navy, antique gold
- Never purple-gradient-on-white
- High contrast between background and primary text

**Spacing:** Generous. Let product imagery breathe.

**Motion:** Slow reveals, fade-ins, subtle scale. Nothing that distracts from the product.

---

## Payments

- Razorpay frontend integration only for now
- Keep payment UI decoupled from backend logic
- Backend verification comes later — do not tightly couple

---
## responsive! 
- everythink in the website should be responive for all monitor screens and mobile , tablet and desktop 
- create very very responsivens

## What This Project Is Not

- Not a quick prototype — every file should be production-grade
- Not a component library experiment — always ship toward real pages
- Not a place for temporary one-off logic — if it's needed twice, abstract it

---

## Current State (keep this section updated manually)

```
Built:        []
In Progress:  []
Planned:      []
```