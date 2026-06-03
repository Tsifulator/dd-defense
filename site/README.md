# D&D Defense — landing page

A single, self-contained `index.html` (no build step, no dependencies). Marketing
page for **dnddefense.com**: explains the product, the dispute grounds, pricing,
and collects free-pilot leads.

## Preview locally

```bash
cd site
python3 -m http.server 8900   # then open http://127.0.0.1:8900/
```

## Before going live (two small edits)

1. **Wire the form.** The pilot form posts to a placeholder. Create a free
   [Formspree](https://formspree.io) form (or similar) and replace
   `https://formspree.io/f/REPLACE_ME` in `index.html` with your endpoint, so
   submissions land in your inbox. (Until then, the form won't deliver.)
2. **Double-check the legal disclaimer** wording with counsel — it's drafted to
   keep the "software, not legal advice / we don't file" boundary clear, but your
   dad should bless it.

## Deploy (pick one — all free, all take ~5 min)

The site is static, so hosting is trivial and free:

- **Cloudflare Pages / Netlify / Vercel** — drag-drop the `site/` folder, or
  connect this repo and set the build output to `site/`. Then point
  `dnddefense.com`'s DNS at the host (each provides exact instructions).
- **GitHub Pages** — enable Pages on this repo, serve from `/site`.

> The marketing site is separate from the app (`dd_defense.webapp`). Deploy the
> app (behind a password) only once a pilot is lined up; the landing page can go
> up now to make outreach look legit.
