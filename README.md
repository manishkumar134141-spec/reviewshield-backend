# 🛡️ ReviewShield — Complete Deployment Guide

Fake review detector for Amazon, Flipkart, Meesho, Myntra, Snapdeal.

## Architecture

```
Browser (index.html)
    │
    ▼ HTTP POST /api/analyze
FastAPI Backend (Python)
    ├── SerpAPI ──────────────► Amazon Reviews (real data)
    ├── httpx scraper ────────► Flipkart / Meesho / Myntra / Snapdeal
    ├── Rule-based engine ────► Fast signals (language, timing, rating)
    └── Google Gemini Flash ──► Deep AI fake detection + report
```

---

## Step 1 — Get Your API Keys (All Free)

### A) Google Gemini API Key (Required)
1. Go to → https://aistudio.google.com/app/apikey
2. Click **Create API Key**
3. Copy it. Free tier: **15 RPM, 1M tokens/day** — plenty for this project.

### B) SerpAPI Key (Required for Amazon)
1. Go to → https://serpapi.com
2. Sign up (free) → Dashboard → **API Key**
3. Free plan: **100 searches/month**

### C) HuggingFace Token (Optional — improves per-review scoring)
1. Go to → https://huggingface.co/settings/tokens
2. Click **New Token** → Read scope
3. Copy it.

---

## Step 2 — Deploy Backend to Railway (Free)

### Option A: Railway (Recommended — easiest)

1. **Push backend to GitHub:**
   ```bash
   cd reviewshield/backend
   git init
   git add .
   git commit -m "ReviewShield backend"
   # Create a new repo on github.com, then:
   git remote add origin https://github.com/YOUR_USERNAME/reviewshield-backend.git
   git push -u origin main
   ```

2. **Deploy on Railway:**
   - Go to https://railway.app → **New Project**
   - Click **Deploy from GitHub repo** → select your repo
   - Railway auto-detects the Dockerfile ✅

3. **Set Environment Variables** (Railway Dashboard → Variables tab):
   ```
   GEMINI_API_KEY   = your_gemini_key
   SERPAPI_KEY      = your_serpapi_key
   HF_TOKEN         = your_hf_token (optional)
   ```

4. **Get your URL:**
   - Railway gives you a URL like: `https://reviewshield-backend-production.up.railway.app`
   - Test it: `https://your-url.up.railway.app/api/health`

### Option B: Render (Also Free)

1. Push backend to GitHub (same as above)
2. Go to https://render.com → **New Web Service**
3. Connect GitHub repo
4. Set:
   - **Environment:** Docker
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add environment variables in Render dashboard
6. Free tier spins down after inactivity (first request is slow)

### Option C: Run Locally (for testing)

```bash
cd backend
pip install -r requirements.txt

# Create .env file
cp .env.example .env
# Edit .env with your keys

# Run server
uvicorn main:app --reload --port 8000
```

Backend runs at: http://localhost:8000
API docs at: http://localhost:8000/docs

---

## Step 3 — Configure the Frontend

1. Open `index.html` in your browser
2. Click **⚙ Settings** (top right of nav bar)
3. Paste your Railway/Render URL: `https://your-app.up.railway.app`
4. Click **Save & Reconnect**
5. The status bar will turn **green** when connected ✅

---

## Step 4 — Host the Frontend (Optional)

The `index.html` is a single file — host it anywhere:

### GitHub Pages (Free)
```bash
# Create a repo named: reviewshield-frontend
# Upload index.html
# Settings → Pages → Deploy from main branch
# URL: https://your-username.github.io/reviewshield-frontend
```

### Netlify (Free — Drag & Drop)
1. Go to https://netlify.com
2. Drag `index.html` onto the deploy area
3. Done! Get a URL like `https://amazing-einstein-123.netlify.app`

### Vercel (Free)
```bash
npm i -g vercel
vercel index.html
```
## API Reference

### POST /api/analyze
```json
Request:
{
  "url": "https://www.amazon.in/dp/B08L5TNJHG",
  "max_reviews": 30
}

Response:
{
  "fakeScore": 72,
  "confidence": "High",
  "totalReviews": 156,
  "fakeCount": 48,
  "suspiciousCount": 64,
  "genuineCount": 44,
  "summary": "...",
  "signals": [...],
  "ratingDist": {...},
  "sampleReviews": [...],
  "platform": "amazon",
  "productName": "...",
  "reviewsScraped": 30,
  "analyzedAt": "2025-04-14T10:23:00Z"
}
```

### GET /api/health
```json
{
  "status": "ok",
  "gemini": true,
  "serpapi": true,
  "hf": false
}
```

### GET /api/history
Returns last 20 analyses.

---

## Troubleshooting

**Backend shows "offline":**
- Make sure environment variables are set correctly
- Check Railway/Render logs for startup errors
- Visit `/api/health` directly in browser

**Amazon scraping fails:**
- Verify `SERPAPI_KEY` is set and has remaining quota
- Check dashboard at serpapi.com → Usage

**Gemini errors:**
- Verify `GEMINI_API_KEY` is valid at aistudio.google.com
- Check for rate limits (wait 1 min and retry)

**Flipkart/Meesho scraping returns empty:**
- These platforms occasionally change their HTML structure
- The AI will still analyze based on URL + product name
