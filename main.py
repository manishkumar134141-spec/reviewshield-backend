"""
ReviewShield Backend — FastAPI
Fake Review Detector for Amazon, Flipkart, Meesho, Myntra, Snapdeal
Uses: Google Gemini 1.5 Flash (AI analysis) + SerpAPI (Amazon scraping)
"""

import os, json, re, asyncio, hashlib
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import google.generativeai as genai

# ─────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────
app = FastAPI(
    title="ReviewShield API",
    description="AI-powered fake review detector for Indian e-commerce platforms",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # lock to your domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# CONFIG — pulled from environment variables
# ─────────────────────────────────────────────
SERPAPI_KEY   = os.getenv("SERPAPI_KEY", "")        # https://serpapi.com (100 free/month)
GEMINI_KEY    = os.getenv("GEMINI_API_KEY", "")     # https://aistudio.google.com (free)
HF_TOKEN      = os.getenv("HF_TOKEN", "")           # https://huggingface.co (optional)

genai.configure(api_key=GEMINI_KEY)
gemini_model = genai.GenerativeModel("gemini-1.5-flash")

# In-memory history store (use Redis/DB in production)
analysis_history: list[dict] = []


# ─────────────────────────────────────────────
# REQUEST / RESPONSE MODELS
# ─────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    url: str
    max_reviews: Optional[int] = 30   # how many reviews to analyze


# ─────────────────────────────────────────────
# PLATFORM DETECTION
# ─────────────────────────────────────────────
PLATFORM_MAP = {
    "amazon":   ["amazon.in", "amazon.com", "amzn.in", "amzn.to"],
    "flipkart": ["flipkart.com", "fkrt.it"],
    "meesho":   ["meesho.com"],
    "myntra":   ["myntra.com"],
    "snapdeal": ["snapdeal.com"],
    "jiomart":  ["jiomart.com"],
    "nykaa":    ["nykaa.com"],
}

def detect_platform(url: str) -> str:
    url_lower = url.lower()
    for platform, domains in PLATFORM_MAP.items():
        if any(d in url_lower for d in domains):
            return platform
    return "unknown"


# ─────────────────────────────────────────────
# AMAZON SCRAPER (via SerpAPI)
# ─────────────────────────────────────────────
def extract_asin(url: str) -> Optional[str]:
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/product/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"asin=([A-Z0-9]{10})",
        r"/([A-Z0-9]{10})(?:[/?]|$)",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

async def fetch_amazon_data(url: str, max_reviews: int = 30) -> dict:
    asin = extract_asin(url)
    if not asin:
        raise HTTPException(400, "Could not extract Amazon product ASIN from URL")
    if not SERPAPI_KEY:
        raise HTTPException(500, "SERPAPI_KEY not configured on server")

    async with httpx.AsyncClient(timeout=30) as client:
        # Fetch product info
        prod_resp = await client.get("https://serpapi.com/search.json", params={
            "engine": "amazon",
            "amazon_domain": "amazon.in",
            "asin": asin,
            "type": "product",
            "api_key": SERPAPI_KEY,
        })
        prod_data = prod_resp.json()

        # Fetch reviews (page 1 = up to 10; page 2 for more)
        reviews = []
        for page in range(1, max(2, (max_reviews // 10) + 1)):
            rev_resp = await client.get("https://serpapi.com/search.json", params={
                "engine": "amazon_reviews",
                "asin": asin,
                "amazon_domain": "amazon.in",
                "page": str(page),
                "api_key": SERPAPI_KEY,
            })
            rev_data = rev_resp.json()
            page_reviews = rev_data.get("reviews", [])
            reviews.extend(page_reviews)
            if len(page_reviews) < 10:   # no more pages
                break
            await asyncio.sleep(0.3)     # be gentle

    product = prod_data.get("product_results", {})
    return {
        "product_name": product.get("title", "Amazon Product"),
        "product_image": product.get("main_image", ""),
        "product_rating": product.get("rating", 0),
        "product_rating_count": product.get("ratings_total", 0),
        "reviews": reviews[:max_reviews],
    }

# ─────────────────────────────────────────────
# FLIPKART SCRAPER (httpx + BeautifulSoup)
# ─────────────────────────────────────────────
async def fetch_flipkart_data(url: str, max_reviews: int = 30) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            html = resp.text

        # Extract product name from <title>
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        product_name = title_match.group(1).strip() if title_match else "Flipkart Product"
        product_name = re.sub(r"\s*[-|].*Flipkart.*$", "", product_name, flags=re.IGNORECASE)

        # Extract review snippets (Flipkart embeds JSON-LD)
        reviews_raw = re.findall(r'"reviewBody"\s*:\s*"([^"]{10,})"', html)
        reviewer_names = re.findall(r'"author"\s*:\s*\{"@type"\s*:\s*"Person"\s*,\s*"name"\s*:\s*"([^"]+)"', html)
        rating_vals = re.findall(r'"ratingValue"\s*:\s*"?(\d\.?\d?)"?', html)

        reviews = []
        for i, body in enumerate(reviews_raw[:max_reviews]):
            reviews.append({
                "title": "",
                "body": body,
                "name": reviewer_names[i] if i < len(reviewer_names) else f"User{i+1}",
                "rating": float(rating_vals[i]) if i < len(rating_vals) else None,
                "verified_purchase": True,
                "date": "",
            })

        return {
            "product_name": product_name,
            "product_image": "",
            "product_rating": None,
            "product_rating_count": None,
            "reviews": reviews,
        }
    except Exception as e:
        raise HTTPException(502, f"Flipkart scraping failed: {str(e)}")


# ─────────────────────────────────────────────
# GENERIC SCRAPER (Meesho, Myntra, Snapdeal, etc.)
# Extracts whatever structured review data is on the page via JSON-LD
# ─────────────────────────────────────────────
async def fetch_generic_data(url: str, platform: str, max_reviews: int = 30) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            html = resp.text

        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        product_name = title_match.group(1).strip()[:100] if title_match else f"{platform.title()} Product"

        # JSON-LD structured data
        jsonld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                                   html, re.DOTALL | re.IGNORECASE)
        reviews = []
        for block in jsonld_blocks:
            try:
                data = json.loads(block)
                # Handle @graph arrays
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict):
                        raw_reviews = item.get("review", item.get("reviews", []))
                        if isinstance(raw_reviews, dict):
                            raw_reviews = [raw_reviews]
                        for r in raw_reviews[:max_reviews]:
                            reviews.append({
                                "title": r.get("name", ""),
                                "body": r.get("reviewBody", ""),
                                "name": (r.get("author", {}) or {}).get("name", "Anonymous"),
                                "rating": (r.get("reviewRating", {}) or {}).get("ratingValue"),
                                "verified_purchase": False,
                                "date": r.get("datePublished", ""),
                            })
            except Exception:
                continue

        return {
            "product_name": product_name,
            "product_image": "",
            "product_rating": None,
            "product_rating_count": None,
            "reviews": reviews[:max_reviews],
        }
    except Exception as e:
        raise HTTPException(502, f"Scraping {platform} failed: {str(e)}")


# ─────────────────────────────────────────────
# RULE-BASED PRE-FILTER (fast signals before Gemini)
# ─────────────────────────────────────────────
FAKE_PHRASES = [
    "best product", "must buy", "love it", "awesome product", "great product",
    "very nice", "super fast delivery", "highly recommend", "value for money",
    "5 stars", "excellent product", "good quality", "satisfied", "worth it",
    "amazing", "wonderful", "fantastic", "perfect", "outstanding",
]

def rule_based_signals(reviews: list) -> dict:
    total = len(reviews)
    if total == 0:
        return {"generic_phrase_pct": 0, "unverified_pct": 0, "five_star_pct": 0,
                "burst_score": 0, "avg_length": 0}

    generic = 0
    unverified = 0
    five_star = 0
    short_reviews = 0
    dates = []

    for r in reviews:
        body = (r.get("body") or r.get("content") or "").lower()
        # Generic language check
        if sum(1 for p in FAKE_PHRASES if p in body) >= 2:
            generic += 1
        # Verified purchase
        if not r.get("verified_purchase", True):
            unverified += 1
        # Rating
        try:
            rating = float(r.get("rating") or 0)
            if rating >= 4.8:
                five_star += 1
        except (TypeError, ValueError):
            pass
        # Short review
        if len(body.split()) < 8:
            short_reviews += 1
        # Date tracking for burst detection
        date_str = r.get("date", "")
        if date_str:
            dates.append(date_str[:10])

    # Burst detection: count dates that appear 3+ times
    from collections import Counter
    date_counts = Counter(dates)
    burst_days = sum(1 for c in date_counts.values() if c >= 3)

    return {
        "generic_phrase_pct": round(generic / total * 100),
        "unverified_pct": round(unverified / total * 100),
        "five_star_pct": round(five_star / total * 100),
        "short_review_pct": round(short_reviews / total * 100),
        "burst_score": min(burst_days * 15, 80),
        "avg_length": round(sum(
            len((r.get("body") or r.get("content") or "").split())
            for r in reviews
        ) / total),
    }


# ─────────────────────────────────────────────
# HUGGING FACE — per-review sentiment classification
# Model: distilbert fine-tuned on SST-2 (sentiment proxy for fakeness)
# ─────────────────────────────────────────────
HF_MODEL_URL = "https://api-inference.huggingface.co/models/distilbert-base-uncased-finetuned-sst-2-english"

async def hf_classify_review(text: str) -> dict:
    """Returns sentiment as a proxy fake signal. Very positive → possible fake."""
    if not HF_TOKEN or not text.strip():
        return {"label": "POSITIVE", "score": 0.5}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                HF_MODEL_URL,
                headers={"Authorization": f"Bearer {HF_TOKEN}"},
                json={"inputs": text[:512]},
            )
            result = resp.json()
            if isinstance(result, list) and result:
                best = max(result[0], key=lambda x: x["score"])
                return best
    except Exception:
        pass
    return {"label": "POSITIVE", "score": 0.5}


# ─────────────────────────────────────────────
# GEMINI ANALYSIS — core AI logic
# ─────────────────────────────────────────────
async def analyze_with_gemini(
    reviews: list,
    product_name: str,
    platform: str,
    rule_signals: dict
) -> dict:

    # Serialize top reviews for Gemini (keep prompt manageable)
    reviews_text = ""
    for i, r in enumerate(reviews[:25]):
        body = (r.get("body") or r.get("content") or r.get("reviewBody") or "").strip()
        if not body:
            continue
        reviews_text += (
            f"\n[{i+1}] Reviewer: {r.get('name','Anonymous')} | "
            f"Rating: {r.get('rating','?')}/5 | "
            f"Verified: {r.get('verified_purchase', '?')} | "
            f"Date: {r.get('date','?')} | "
            f"Text: {body[:280]}\n"
        )

    if not reviews_text.strip():
        reviews_text = "No review text available — analyze URL and product name only."

    prompt = f"""You are ReviewShield, an expert AI fake review detector for Indian e-commerce.

PLATFORM: {platform.upper()}
PRODUCT: {product_name}
RULE-BASED PRE-SIGNALS:
- Generic phrase rate: {rule_signals.get('generic_phrase_pct', 0)}%
- Unverified purchase rate: {rule_signals.get('unverified_pct', 0)}%
- 5-star ratio: {rule_signals.get('five_star_pct', 0)}%
- Short/lazy review rate: {rule_signals.get('short_review_pct', 0)}%
- Review burst score: {rule_signals.get('burst_score', 0)}/80
- Average review length: {rule_signals.get('avg_length', 0)} words

REVIEWS TO ANALYZE:
{reviews_text}

Analyze deeply for:
1. Unnatural or templated language patterns
2. Excessive superlatives without specifics
3. Suspiciously high ratings with vague praise
4. Copy-pasted or near-duplicate sentences
5. New or thin reviewer accounts
6. Incentivized/paid review signals ("got this for free", "discount code")
7. Timing bursts (many reviews same day)
8. Sentiment mismatch (rating vs text)

Return ONLY valid JSON, no markdown fences, no extra text:

{{
  "fakeScore": <integer 0-100, higher = more fake>,
  "confidence": <"Low"|"Medium"|"High">,
  "totalReviews": <integer>,
  "fakeCount": <integer>,
  "suspiciousCount": <integer>,
  "genuineCount": <integer>,
  "avgAccountAgeDays": "<string like '94 days'>",
  "suspiciousTimingBursts": <integer 0-10>,
  "verifiedPurchasePercent": <integer 0-100>,
  "summary": "<2-sentence human-readable verdict>",
  "signals": [
    {{"name": "Generic language", "percent": <int>}},
    {{"name": "New reviewer accounts", "percent": <int>}},
    {{"name": "Unverified purchases", "percent": <int>}},
    {{"name": "Rating bursts", "percent": <int>}},
    {{"name": "Duplicate phrasing", "percent": <int>}}
  ],
  "ratingDist": {{
    "5star": <pct int>, "4star": <pct int>, "3star": <pct int>,
    "2star": <pct int>, "1star": <pct int>
  }},
  "sampleReviews": [
    {{
      "reviewer": "<name>",
      "rating": <1-5>,
      "text": "<review excerpt max 120 chars>",
      "verdict": "<fake|suspicious|genuine>",
      "reasons": ["<short reason>", "<short reason>"]
    }}
  ]
}}

Classify at least 6 sample reviews. Mix verdicts realistically."""

    try:
        response = gemini_model.generate_content(prompt)
        raw = response.text.strip()
        # Strip any accidental markdown fences
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"Gemini returned invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(500, f"Gemini analysis failed: {e}")


# ─────────────────────────────────────────────
# MAIN ANALYZE ENDPOINT
# ─────────────────────────────────────────────
@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    url = req.url.strip()
    platform = detect_platform(url)

    if platform == "unknown":
        raise HTTPException(400, "URL not recognized. Supported: Amazon, Flipkart, Meesho, Myntra, Snapdeal, JioMart, Nykaa")

    # ── Step 1: Scrape reviews ──
    if platform == "amazon":
        data = await fetch_amazon_data(url, req.max_reviews)
    elif platform == "flipkart":
        data = await fetch_flipkart_data(url, req.max_reviews)
    else:
        data = await fetch_generic_data(url, platform, req.max_reviews)

    reviews = data["reviews"]

    # ── Step 2: Rule-based signals (fast) ──
    rule_signals = rule_based_signals(reviews)

    # ── Step 3: Gemini deep analysis ──
    ai_result = await analyze_with_gemini(
        reviews, data["product_name"], platform, rule_signals
    )

    # ── Step 4: Build final response ──
    result = {
        **ai_result,
        "platform": platform,
        "productName": data["product_name"],
        "productImage": data.get("product_image", ""),
        "productRating": data.get("product_rating"),
        "productRatingCount": data.get("product_rating_count"),
        "url": url,
        "reviewsScraped": len(reviews),
        "ruleSignals": rule_signals,
        "analyzedAt": datetime.utcnow().isoformat() + "Z",
        "id": hashlib.md5(url.encode()).hexdigest()[:8],
    }

    # Save to in-memory history
    analysis_history.insert(0, {k: v for k, v in result.items() if k != "sampleReviews"})
    if len(analysis_history) > 50:
        analysis_history.pop()

    return result


# ─────────────────────────────────────────────
# HISTORY ENDPOINT
# ─────────────────────────────────────────────
@app.get("/api/history")
async def get_history(limit: int = 20):
    return {"history": analysis_history[:limit]}


# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "gemini": bool(GEMINI_KEY),
        "serpapi": bool(SERPAPI_KEY),
        "hf": bool(HF_TOKEN),
        "history_count": len(analysis_history),
    }


# ─────────────────────────────────────────────
# PLATFORMS LIST
# ─────────────────────────────────────────────
@app.get("/api/platforms")
async def platforms():
    return {"platforms": list(PLATFORM_MAP.keys())}
