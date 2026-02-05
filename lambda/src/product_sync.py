"""
LookBook Product Sync Lambda
Fetches women's clothing from Amazon PA-API and ASOS (RapidAPI),
caches images in S3, and syncs metadata to Supabase.
"""

import json
import os
import random
import uuid
from datetime import datetime, timezone
from io import BytesIO

import boto3
import requests
from supabase import create_client

# --- Globals (initialized once per Lambda cold start) ---
s3 = boto3.client("s3")
secrets_client = boto3.client("secretsmanager")

S3_BUCKET = os.environ["S3_BUCKET"]
CLOUDFRONT_DOMAIN = os.environ["CLOUDFRONT_DOMAIN"]
SECRET_NAME = os.environ["SECRET_NAME"]

CATEGORY_KEYWORDS = {
    "dresses": ["women dress", "women maxi dress", "women casual dress"],
    "tops": ["women top", "women blouse", "women sweater"],
    "pants": ["women pants", "women jeans", "women trousers"],
    "shoes": ["women shoes", "women heels", "women flats"],
}

# ASOS category IDs (US store) mapped to our categories
ASOS_CATEGORY_MAP = {
    "dresses": 8799,
    "tops": 4169,
    "shoes": 6461,
    "accessories": 27109,
    "sweaters": 2637,
    "blouses": 15199,
    "plus-size": 9577,
}

# Words to filter out from product titles
BLOCKED_WORDS = ["bra", "jean"]


def get_secrets():
    resp = secrets_client.get_secret_value(SecretId=SECRET_NAME)
    return json.loads(resp["SecretString"])


def get_supabase(secrets):
    url = secrets["SUPABASE_URL"].strip()
    key = secrets["SUPABASE_SERVICE_KEY"].strip()
    return create_client(url, key)


def item_exists(supabase, source, source_id):
    result = (
        supabase.table("clothing_items")
        .select("id")
        .eq("source", source)
        .eq("source_id", source_id)
        .execute()
    )
    return len(result.data) > 0


def cache_image_to_s3(image_url, source, source_id):
    """Download image and upload to S3. Returns CloudFront URL or None."""
    try:
        resp = requests.get(image_url, timeout=15)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "image/jpeg")
        ext = "jpg"
        if "png" in content_type:
            ext = "png"
        elif "webp" in content_type:
            ext = "webp"

        s3_key = f"{source}/{source_id}.{ext}"
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=resp.content,
            ContentType=content_type,
        )
        return f"https://{CLOUDFRONT_DOMAIN}/{s3_key}"
    except Exception as e:
        print(f"Failed to cache image for {source}/{source_id}: {e}")
        return None


def insert_item(supabase, item):
    supabase.table("clothing_items").insert(item).execute()


def generate_price():
    return round(random.uniform(15.00, 49.99), 2)


def ensure_https(url):
    """Ensure URL has https:// scheme."""
    if not url:
        return url
    if url.startswith("//"):
        return f"https:{url}"
    if not url.startswith("http"):
        return f"https://{url}"
    return url


# ============================================================
# Amazon PA-API
# ============================================================

def fetch_amazon_products(secrets):
    """Fetch products from Amazon Creators API (python-amazon-paapi v6)."""
    from amazon_creatorsapi import AmazonCreatorsApi, Country

    amazon = AmazonCreatorsApi(
        credential_id=secrets["AMAZON_ACCESS_KEY"],
        credential_secret=secrets["AMAZON_SECRET_KEY"],
        tag=secrets["AMAZON_PARTNER_TAG"],
        country=Country.US,
        version="2.2",
    )

    products = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            try:
                results = amazon.search_items(keywords=keyword)
                if not results or not results.items:
                    continue

                for item in results.items:
                    asin = item.asin
                    title = (
                        item.item_info.title.display_value
                        if item.item_info and item.item_info.title
                        else None
                    )
                    image_url = (
                        item.images.primary.large.url
                        if item.images and item.images.primary and item.images.primary.large
                        else None
                    )
                    price = None
                    if (
                        item.offers
                        and item.offers.listings
                        and len(item.offers.listings) > 0
                    ):
                        price = item.offers.listings[0].price.amount

                    if not title or not image_url:
                        continue

                    products.append(
                        {
                            "source": "amazon",
                            "source_id": asin,
                            "title": title,
                            "original_image_url": image_url,
                            "price": float(price) if price else generate_price(),
                            "category": category,
                            "brand": None,
                        }
                    )
            except Exception as e:
                print(f"Amazon search error for '{keyword}': {e}")

    return products


# ============================================================
# ASOS API (via RapidAPI)
# ============================================================

def fetch_asos_products(secrets):
    """Fetch products from ASOS via RapidAPI with pagination."""
    headers = {
        "X-RapidAPI-Key": secrets["RAPIDAPI_KEY"],
        "X-RapidAPI-Host": "asos2.p.rapidapi.com",
    }

    # Pagination settings: 3 pages × 48 items × 4 categories = 576 items max
    # Uses 12 API calls per run (stays within 500/month free tier if run daily)
    ITEMS_PER_PAGE = 48
    PAGES_PER_CATEGORY = 3

    products = []
    for category, cat_id in ASOS_CATEGORY_MAP.items():
        for page in range(PAGES_PER_CATEGORY):
            try:
                offset = page * ITEMS_PER_PAGE
                resp = requests.get(
                    "https://asos2.p.rapidapi.com/products/v2/list",
                    headers=headers,
                    params={
                        "store": "US",
                        "offset": str(offset),
                        "categoryId": str(cat_id),
                        "limit": str(ITEMS_PER_PAGE),
                        "country": "US",
                        "currency": "USD",
                        "sizeSchema": "US",
                        "lang": "en-US",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                page_products = data.get("products", [])
                print(f"Fetched {len(page_products)} items for {category} (page {page + 1})")

                # Stop paginating if no more products
                if not page_products:
                    break

                for item in page_products:
                    product_id = str(item.get("id", ""))
                    name = item.get("name", "")
                    price_current = item.get("price", {}).get("current", {}).get("value")
                    image_url = item.get("imageUrl", "")
                    colour = item.get("colour")
                    is_on_sale = item.get("price", {}).get("isMarkedDown", False)
                    is_selling_fast = item.get("isSellingFast", False)

                    # Get additional image URLs and ensure they have https://
                    additional_urls = item.get("additionalImageUrls", [])
                    additional_urls = [ensure_https(url) for url in additional_urls]

                    if not name or not image_url:
                        continue

                    # Filter out blocked words (bra, jean, etc.)
                    name_lower = name.lower()
                    if any(word in name_lower for word in BLOCKED_WORDS):
                        continue

                    # Ensure main image URL has https://
                    image_url = ensure_https(image_url)

                    # Build the source URL for potential purchase
                    product_url = item.get("url", "")
                    if product_url and not product_url.startswith("http"):
                        product_url = f"https://www.asos.com/{product_url}"

                    products.append(
                        {
                            "source": "asos",
                            "source_id": product_id,
                            "title": name,
                            "original_image_url": image_url,
                            "additional_image_urls": additional_urls,
                            "price": float(price_current) if price_current else generate_price(),
                            "category": category,
                            "brand": item.get("brandName"),
                            "colour": colour,
                            "is_on_sale": is_on_sale,
                            "is_selling_fast": is_selling_fast,
                            "source_url": product_url,
                        }
                    )
            except Exception as e:
                print(f"ASOS fetch error for {category} page {page + 1}: {e}")

    return products


# ============================================================
# Handler
# ============================================================

def handler(event, context):
    secrets = get_secrets()
    supabase = get_supabase(secrets)

    items_fetched = 0
    items_added = 0
    errors = []

    # Fetch from ASOS only (Amazon API requires qualifying sales)
    all_products = []
    try:
        all_products.extend(fetch_asos_products(secrets))
    except Exception as e:
        errors.append({"source": "asos", "error": str(e)})
        print(f"ASOS fetch failed entirely: {e}")

    items_fetched = len(all_products)
    print(f"Total products fetched: {items_fetched}")

    # Get existing product source_ids to skip duplicates (batch check)
    existing_ids = set()
    try:
        result = supabase.table("clothing_items").select("source_id").eq("source", "asos").execute()
        existing_ids = {row["source_id"] for row in result.data}
        print(f"Found {len(existing_ids)} existing products to skip")
    except Exception as e:
        print(f"Warning: Could not fetch existing IDs: {e}")

    # Filter out duplicates and prepare batch
    new_products = []
    for product in all_products:
        if product["source_id"] in existing_ids:
            continue

        # Use ASOS CDN URLs directly (skip S3 caching for speed)
        new_products.append({
            "id": str(uuid.uuid4()),
            "source": product["source"],
            "source_id": product["source_id"],
            "title": product["title"],
            "image_url": product["original_image_url"],  # Use ASOS URL directly
            "original_image_url": product["original_image_url"],
            "additional_image_urls": product.get("additional_image_urls", []),
            "price": product["price"],
            "category": product["category"],
            "brand": product.get("brand"),
            "colour": product.get("colour"),
            "is_on_sale": product.get("is_on_sale", False),
            "source_url": product.get("source_url"),
            "is_active": True,
        })

    print(f"New products to insert: {len(new_products)}")

    # Batch insert in chunks of 50
    BATCH_SIZE = 50
    for i in range(0, len(new_products), BATCH_SIZE):
        batch = new_products[i:i + BATCH_SIZE]
        try:
            supabase.table("clothing_items").insert(batch).execute()
            items_added += len(batch)
            print(f"Inserted batch {i // BATCH_SIZE + 1}: {len(batch)} items")
        except Exception as e:
            errors.append({"batch": i // BATCH_SIZE + 1, "error": str(e)})
            print(f"Error inserting batch {i // BATCH_SIZE + 1}: {e}")

    # Log the sync run
    try:
        supabase.table("sync_log").insert(
            {
                "id": str(uuid.uuid4()),
                "source": "all",
                "items_fetched": items_fetched,
                "items_added": items_added,
                "errors": errors if errors else None,
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
    except Exception as e:
        print(f"Failed to write sync log: {e}")

    result = {
        "items_fetched": items_fetched,
        "items_added": items_added,
        "errors_count": len(errors),
    }
    print(f"Sync complete: {json.dumps(result)}")
    return result
