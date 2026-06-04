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

1. **Wire the form** — near the bottom of `index.html` there's a `LEAD FORM CONFIG`
   block with two variables. Pick one:
   - **Easiest / zero signup:** set `YOUR_EMAIL` to your real inbox. Submitting the
     form opens the visitor's email app with all the details prefilled.
   - **Recommended:** make a free form at [formspree.io](https://formspree.io),
     paste its ID into `FORMSPREE_ID`. Submissions are emailed to you automatically
     (no page reload, shows a thank-you message).
   Until you set one, the form still works via the email fallback — just change
   `YOUR_EMAIL` from the placeholder.
2. **Double-check the legal disclaimer** wording with counsel — it's drafted to
   keep the "software, not legal advice / we don't file" boundary clear, but your
   dad should bless it (see `docs/legal-review-packet.docx`).

## Deploy (pick one — all free, all take ~5 min)

The site is static, so hosting is trivial and free:

- **Cloudflare Pages / Netlify / Vercel** — drag-drop the `site/` folder, or
  connect this repo and set the build output to `site/`. Then point
  `dnddefense.com`'s DNS at the host (each provides exact instructions).
- **GitHub Pages** — enable Pages on this repo, serve from `/site`.

> The marketing site is separate from the app (`dd_defense.webapp`). Deploy the
> app (behind a password) only once a pilot is lined up; the landing page can go
> up now to make outreach look legit.
