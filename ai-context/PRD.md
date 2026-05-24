# Oorvashee Saree House — Product Requirements Document

**Full-Stack E-Commerce Platform — Master Overview**

| Field | Value |
|---|---|
| Project Name | Oorvashee Saree House — E-Commerce Platform |
| Brand | Oorvashee Saree House |
| Brand Identity | Deep Maroon (`#7B0D0D`) + Gold (`#C9A84C`) — Premium Ethnic Luxury |
| Document Type | Master PRD — Idea Overview (Pre-Development) |
| Document Version | v1.0 |
| Target Go-Live | 29 May 2025 — Store Grand Opening |
| Team Size | 3 Developers (Frontend · Backend · Bot Integration) |
| Prepared By | Development Team |

---

## 1. Executive Summary

Oorvashee Saree House is a premium Indian saree brand opening a flagship physical store on 29 May 2025. Alongside the physical launch, the brand requires a full-stack e-commerce platform to serve their 200,000+ social media followers who currently engage through WhatsApp and Instagram.

The platform must bridge their existing social commerce operation — where buyers message directly about specific sarees — into a structured, scalable digital storefront. The core innovation is deep bot integration: WhatsApp and Instagram bots (already built) will redirect customers directly to individual product URLs, making the website the conversion endpoint of an active social selling funnel already in operation.

This is not a startup MVP. The platform must be production-ready, cloud-hosted, SEO-optimised, payment-integrated, and capable of handling festival-season traffic spikes at launch.

---

## 2. Business Context & Problem Statement

### 2.1 Current State

Oorvashee operates a thriving social commerce model today:

- 200,000+ followers across Facebook and Instagram — majority are active buyers
- Orders placed manually via WhatsApp and Instagram DMs
- No website. No structured catalog. No payment gateway. No inventory system.
- Product discovery happens via social posts; purchase intent is communicated in natural language ("I need this saree")
- Bots on WhatsApp and Instagram are already live and functional

### 2.2 The Problem

- Zero digital storefront — all sales are manual, unscalable
- No product URLs to redirect bot users to — bots have nowhere to send buyers
- No payment infrastructure — all payments are manual transfers
- No inventory visibility — owners have no system view of stock
- No order tracking — customers have zero post-purchase visibility
- No admin control — owners cannot manage catalog, orders, or customers from one place

### 2.3 The Opportunity

The customer base and selling funnel already exist. The website is the missing conversion layer. With 200K followers and an active bot-to-product redirect system, even a 1–2% conversion rate at scale represents a significant business impact. The platform's job is to not break that funnel.

---

## 3. Product Vision

### 3.1 Vision Statement

Build a luxury, high-performance saree e-commerce platform that converts Oorvashee's existing social media following into paying customers — with the WhatsApp/Instagram bot at the centre of the acquisition funnel, and the website as the seamless, beautiful conversion destination.

### 3.2 Design Direction

Based on reference sites provided (Singhanias, Gehen, Siddhi Silks) and the brand identity:

- **Tone:** Premium ethnic luxury — not mass marketplace
- **Feel:** Editorial, spacious, large imagery — similar to Singhanias.in structure
- **Colors:** Deep maroon (`#7B0D0D`) as primary, gold (`#C9A84C`) as accent throughout
- **Typography:** Elegant serif for display headings, clean sans-serif for body text
- **Mobile-first:** Majority of bot-redirected traffic will arrive on mobile devices
- **Performance:** Lazy loading with preloaded skeletons, WebP images, CDN delivery — zero perceived load time

---

## 4. Platform Overview

The Oorvashee platform is three interconnected systems:

| System | What It Is | Who Uses It |
|---|---|---|
| Customer Website | Full-stack public-facing e-commerce store | End customers — 200K+ followers, bot-redirected users, organic search traffic |
| Admin Dashboard | Internal management panel for the Oorvashee team | Owner / Admin only (single user for now) |
| Bot Integration Layer | WhatsApp & Instagram bots redirect to product URLs | Automated — runs in background connecting social to website |

---

## 5. Customer Website — Feature Overview

### 5.1 Public Pages & Navigation

| Feature | Description | Priority |
|---|---|---|
| Homepage | Hero banner (video/image), featured collections, new arrivals, bestsellers, brand story | MUST HAVE |
| Catalog / Shop Page | Full product grid with filters: fabric, occasion, color, price range, region/type | MUST HAVE |
| Product Detail Page | Multi-image gallery, zoom, variant selector (color/fabric), add to cart, WhatsApp redirect | MUST HAVE |
| Search | Full-text search across product names, tags, descriptions | MUST HAVE |
| About Page | Brand story, physical store details | SHOULD HAVE |
| Contact Page | WhatsApp link, Instagram link, store address, email | SHOULD HAVE |

### 5.2 Shopping & Checkout

| Feature | Description | Priority |
|---|---|---|
| Cart | Add/remove items, quantity update, price summary | MUST HAVE |
| Checkout Flow | Address entry, delivery method, payment selection, order confirm | MUST HAVE |
| Payment Gateway | All Indian methods: UPI, Cards, NetBanking, Wallets, COD via Razorpay | MUST HAVE |
| Guest Checkout | Purchase without creating an account | MUST HAVE |
| Registered Checkout | Login via Clerk, saved addresses, order history | MUST HAVE |
| Order Confirmation | Email + on-screen confirmation with order ID | MUST HAVE |
| Order Tracking | Customer-facing status page (Placed → Packed → Shipped → Delivered) | MUST HAVE |
| Discount Codes | Coupon/promo code field at checkout | SHOULD HAVE |

### 5.3 User Account (via Clerk)

| Feature | Description | Priority |
|---|---|---|
| Sign Up / Login | Clerk-powered auth — email, Google, phone OTP | MUST HAVE |
| Wishlist | Save products; requires login | MUST HAVE |
| Order History | List of past orders with status and reorder option | MUST HAVE |
| Saved Addresses | Multiple delivery addresses per account | SHOULD HAVE |
| Recently Viewed | Last 10 viewed products stored per session | SHOULD HAVE |

### 5.4 Performance & Image Strategy

This is a non-negotiable requirement given the visual-heavy nature of a saree catalog.

- All uploaded images auto-compressed and served as WebP via CDN pipeline
- Lazy loading with skeleton placeholders — images load exactly as user scrolls to them
- Above-the-fold images preloaded — hero and first product row load instantly
- 10–30MB raw uploads from admin auto-converted; visual quality preserved at compressed sizes
- Video banners served via CDN with streaming — no buffering on first load
- Core Web Vitals target: LCP < 2.5s, CLS < 0.1, INP < 200ms

---

## 6. Admin Dashboard — Feature Overview

### 6.1 Product Management

| Feature | Description | Priority |
|---|---|---|
| Add Single Product | Upload images (multiple), set name, description, price, tags, variants, stock count | MUST HAVE |
| Bulk Upload via CSV/Excel | Upload hundreds of products at once via structured template | MUST HAVE |
| Edit / Delete Product | Update any product field; soft-delete to unpublish without losing data | MUST HAVE |
| Image CDN Pipeline | Auto-compress, convert to WebP, push to CDN on upload | MUST HAVE |
| Variant Management | Per-product: color variants, fabric variants, each with separate stock | MUST HAVE |
| Product Preview | See exactly how product appears on customer site before publishing | SHOULD HAVE |

### 6.2 Order Management

| Feature | Description | Priority |
|---|---|---|
| Order List | All orders with status, customer name, amount, date — filterable and searchable | MUST HAVE |
| Order Detail View | Full order info: customer, items, address, payment status, delivery status | MUST HAVE |
| Status Update | Admin updates order status — triggers customer-facing tracking update | MUST HAVE |
| COD Management | Flag COD orders separately; mark as paid on delivery confirmation | MUST HAVE |
| Delivery Partner | Manual entry of tracking ID from chosen courier partner | SHOULD HAVE |

### 6.3 Inventory Management

| Feature | Description | Priority |
|---|---|---|
| Stock Count per Variant | Real-time stock per product variant; auto-decrements on order | MUST HAVE |
| Low Stock Alerts | Dashboard flag when any variant stock falls below threshold | SHOULD HAVE |
| Out-of-Stock Handling | Auto-marks product as unavailable when stock = 0 | MUST HAVE |

### 6.4 Customer Management

| Feature | Description | Priority |
|---|---|---|
| Customer List | All registered users with order count, total spend, join date | MUST HAVE |
| Customer Detail | Individual customer: contact info, full order history | SHOULD HAVE |

### 6.5 Analytics & Reporting

| Feature | Description | Priority |
|---|---|---|
| Revenue Overview | Total revenue, today, this week, this month | MUST HAVE |
| Top Products | Best-selling sarees by order count and revenue | MUST HAVE |
| Order Volume Chart | Daily/weekly order volume graph | SHOULD HAVE |
| Traffic Source (Future) | Where users are coming from — organic, WhatsApp, Instagram | NICE TO HAVE |

---

## 7. Bot Integration Layer

### 7.1 How It Works

The bots are already built and functional. The website must satisfy one critical contract: every product must have a clean, stable, predictable URL.

| Aspect | Definition |
|---|---|
| Bot Platform | WhatsApp (custom webhook) + Instagram |
| Trigger | Customer messages: "I need this saree" or similar |
| Bot Response | Sends a direct product URL to the customer in chat |
| URL Format | `https://oorvashee.com/product/[product-slug]` |
| Landing Requirement | URL must open directly to that exact product's detail page |
| Slug Rule | Product slug generated on creation; immutable after publish |

### 7.2 Critical URL Contract

Once a product URL is shared by the bot, it must never break. This drives two technical requirements:

- Product slugs are generated at creation time and are never auto-changed, even if the product name is edited
- If a product is deleted, its URL must show a graceful 'Product Unavailable' page — not a 404

---

## 8. Technology Stack

### 8.1 Confirmed Stack

| Layer | Technology | Reason |
|---|---|---|
| Frontend | Next.js 14 (App Router) | SSR + SSG for SEO; fast routing; image optimisation built-in; mobile-first |
| Backend / API | FastAPI (Python) | High performance async API; excellent for image processing pipelines and future AI features |
| Authentication | Clerk | Production-ready auth with email, Google, phone OTP; handles all session management |
| Database | PostgreSQL | Relational, ACID-compliant; handles products, orders, customers, inventory reliably |
| ORM | SQLAlchemy + Alembic | Robust ORM for FastAPI; Alembic for DB migrations as schema evolves |
| Payments | Razorpay | Best-in-class India payment gateway: UPI, cards, netbanking, wallets, COD, EMI |
| Image Storage | Cloudinary | Auto-compress, WebP conversion, CDN delivery, responsive image URLs — one integration |
| Hosting: Frontend | Vercel | Native Next.js hosting; edge CDN globally; zero-config deploy from Git |
| Hosting: Backend | Railway or Render | Managed container hosting for FastAPI; auto-scale on traffic spikes |
| Database Host | Supabase or Neon | Managed PostgreSQL with dashboard; free tier generous; scales to paid easily |
| Search | PostgreSQL Full-Text / Algolia (Future) | Start with Postgres FTS; upgrade to Algolia if search load demands it |
| Email | Resend | Developer-friendly transactional email for order confirmations |
| Domain | To be purchased | Recommended: `oorvashee.com` or `oorvasheesilks.com` (check availability) |

---

## 9. Cloud Infrastructure — Full Breakdown

This section is specifically for the developer who has not done cloud deployment before. Every service, cost, and recommendation is explained below.

### 9.1 What 'Cloud' Means for This Project

Your website will run on servers you never physically touch. Instead of buying your own server, you pay cloud providers monthly to run your code, store your database, and serve your images to users worldwide. The key services you need:

| Service | Purpose |
|---|---|
| Compute | Runs your Next.js frontend and FastAPI backend code |
| Database | Stores all product, order, customer, and inventory data (PostgreSQL) |
| Image/Media CDN | Stores and serves all saree images and videos globally at high speed |
| Domain + DNS | Your website address (`oorvashee.com`) pointing to the right servers |
| Email Service | Sends order confirmation emails to customers |

### 9.2 Recommended Architecture (Phase 1 Launch)

| Provider | Plan / Tier | Est. Monthly Cost | Verdict |
|---|---|---|---|
| Vercel (Free/Pro) | Frontend hosting for Next.js | ₹0–₹1,700/mo | ★ RECOMMENDED — Deploy from GitHub in minutes. Global CDN. Next.js native. |
| Railway | FastAPI backend hosting | ₹420–₹1,700/mo | ★ RECOMMENDED — Easiest managed container for FastAPI. Auto-deploy from Git. |
| Supabase | PostgreSQL database | ₹0–₹2,000/mo | ★ RECOMMENDED — Managed Postgres with UI dashboard. Free tier starts well. |
| Cloudinary | Image CDN + compression | ₹0–₹1,700/mo | ★ RECOMMENDED — Handles all image upload, compression, WebP, CDN in one. |
| Resend | Transactional email | ₹0/mo | Free up to 3,000 emails/month. Covers launch easily. |
| Razorpay | Payments | 2% per transaction | No monthly fee. Industry standard India gateway. |

> Estimated monthly cost at launch: **₹2,000–₹7,000/month**. Scales with traffic. Most costs are zero until real usage hits.

### 9.3 Future Scale Architecture (Phase 2–3)

When traffic exceeds 10,000+ concurrent users or catalog exceeds 10,000 products, upgrade path:

- **Frontend:** Vercel Pro → stays the same, pricing scales automatically
- **Backend:** Railway → AWS ECS or Google Cloud Run (containerised FastAPI with auto-scaling)
- **Database:** Supabase Pro → AWS RDS or PlanetScale (read replicas for high traffic)
- **Search:** PostgreSQL FTS → Algolia (handles 100K+ products with instant search)
- **Images:** Cloudinary → Cloudinary Scale (same service, higher tier)
- **Monitoring:** Add Sentry (error tracking) + Datadog or Grafana (performance)

AWS, GCP, and Azure are more powerful but significantly more complex to set up and maintain. For a 3-person team on a hard deadline, the recommended stack above (Vercel + Railway + Supabase + Cloudinary) gives you 90% of the power with 10% of the DevOps overhead. Migrate to AWS/GCP when you have dedicated DevOps time.

---

## 10. SEO Strategy Overview

SEO is built into the product architecture, not bolted on later.

- Next.js App Router with server-side rendering — every product page is fully crawlable by Google
- Dynamic meta titles and descriptions per product — generated from product name + tags + category
- Structured data (JSON-LD): Product schema with price, availability, images for Google rich results
- Sitemap auto-generated on product publish/unpublish
- URL structure: `/sarees/[category]/[product-slug]` — keyword-rich URLs
- Image alt text: Auto-populated from product name + fabric + occasion tags
- Core Web Vitals optimisation (performance = SEO ranking signal)
- Admin uploads product tags — these are mapped to SEO metadata automatically

---

## 11. Delivery & Logistics Overview

| Aspect | Definition |
|---|---|
| Delivery Scope | Pan-India only (no international at launch) |
| Courier Model | Self-managed initially; owner manually assigns courier partner per order |
| Future Integration | Shiprocket / Delhivery API integration planned for Phase 2 |
| Tracking | Admin enters courier tracking ID in dashboard; customer sees real-time status on their order page |
| COD | Supported. COD orders flagged separately. Marked as paid manually after delivery confirmation. |
| Returns Policy | To be defined by client. Return flow not in scope for Phase 1. |

---

## 12. Development Cycles — High-Level Roadmap

The full build is divided into development cycles. Each cycle has phases and sub-phases. Detailed cycle documents will be created separately. Below is the macro view.

| Cycle | Name | What Gets Built | Target |
|---|---|---|---|
| Cycle 0 | Foundation | Repo setup, DB schema, cloud infra, CI/CD pipeline, design system, Clerk auth, domain | Day 1–2 |
| Cycle 1 | Core Catalog | Product model, product detail page, catalog/filter page, image CDN pipeline, bot URL structure | Day 2–4 |
| Cycle 2 | Commerce | Cart, checkout, Razorpay integration (all methods + COD), order placement, confirmation email | Day 4–6 |
| Cycle 3 | Admin Dashboard | Product CRUD, bulk CSV upload, order management, inventory, customer list, analytics overview | Day 5–7 |
| Cycle 4 | User Accounts | Clerk login/signup, wishlist, order history, saved addresses, recently viewed | Day 6–7 |
| Cycle 5 | Launch Readiness | Order tracking page, SEO metadata, performance audit, mobile QA, cross-browser test, go-live | Day 7–8 |
| Cycle 6+ | Post-Launch | Delivery partner API, blog/lookbook, advanced analytics, mobile app planning | Post 29 May |

> **DEADLINE REALITY:** 8 days, 3 developers, full e-commerce. This is achievable only with strict parallel work across the 3 tracks (Frontend / Backend / Bot). No feature creep. Scope above is the max.

---

## 13. Team Responsibilities

| Role | Primary | Owns |
|---|---|---|
| Dev 1 | Frontend | Next.js app, all pages, design system, Clerk integration, Razorpay checkout UI, wishlist, order tracking UI, mobile responsiveness, SEO meta, performance |
| Dev 2 | Backend | FastAPI, PostgreSQL schema, all API endpoints, Razorpay backend, Cloudinary pipeline, order management, admin APIs, email service, Alembic migrations |
| Dev 3 | Bot Integration | WhatsApp bot → product URL contract, Instagram bot redirect, bot webhook endpoints in FastAPI, URL stability testing, end-to-end bot → website → checkout flow QA |

---

## 14. Out of Scope — Phase 1

The following are explicitly excluded from the 29 May launch to protect the deadline:

- Mobile app (iOS or Android)
- Multi-language support (Telugu, Hindi) — English only
- Blog / Lookbook section
- Automated delivery partner API (Shiprocket/Delhivery) — manual tracking ID only
- Multi-admin / staff role management — single admin only
- Advanced customer segmentation or marketing automation
- Returns and refunds flow
- Loyalty points or referral programme

---

## 15. Open Items — Client Must Confirm

| # | Item | Impact If Not Decided |
|---|---|---|
| 1 | Domain name to purchase (e.g. `oorvashee.com`) | Cannot go live without domain. Must be bought Day 1. |
| 2 | Exact saree categories / filters required (fabric, occasion, region, etc.) | Cannot build filter UI or database schema without this. |
| 3 | Product data (CSV or first batch of products for testing) | Cannot test catalog, search, or bot redirect without real products. |
| 4 | Delivery partners and shipping charges per zone | Cannot set up checkout shipping calculation without this. |
| 5 | Returns & refund policy | Required for checkout legal compliance; can be placeholder text. |
| 6 | Terms & Conditions / Privacy Policy text | Required before going live with a payment gateway. |
| 7 | Blog/Lookbook decision | If yes, add to scope now. If no, confirm so we don't build it. |

---

**OORVASHEE SAREE HOUSE — Master PRD — Document End**

**Next Step:** Cycle 0 Document → Foundation Setup
Repo · DB Schema · Cloud Infra · CI/CD · Design System · Auth
