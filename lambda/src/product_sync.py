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
    "pants": 4208,
    "shoes": 6461,
}


def get_secrets():
    resp = secrets_client.get_secret_value(SecretId=SECRET_NAME)
    return json.loads(resp["SecretString"])


def get_supabase(secrets):
    return create_client(secrets["SUPABASE_URL"], secrets["SUPABASE_SERVICE_KEY"])


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
    """Fetch products from ASOS via RapidAPI."""
    headers = {
        "X-RapidAPI-Key": secrets["RAPIDAPI_KEY"],
        "X-RapidAPI-Host": "asos2.p.rapidapi.com",
    }

    products = []
    for category, cat_id in ASOS_CATEGORY_MAP.items():
        try:
            resp = requests.get(
                "https://asos2.p.rapidapi.com/products/v2/list",
                headers=headers,
                params={
                    "store": "US",
                    "offset": "0",
                    "categoryId": str(cat_id),
                    "limit": "20",
                    "country": "US",
                    "currency": "USD",
                    "sizeSchema": "US",
                    "lang": "en-US",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("products", []):
                product_id = str(item.get("id", ""))
                name = item.get("name", "")
                price_current = item.get("price", {}).get("current", {}).get("value")
                image_url = item.get("imageUrl", "")

                if not name or not image_url:
                    continue

                # ASOS image URLs need https: prefix
                if image_url.startswith("//"):
                    image_url = "https:" + image_url

                products.append(
                    {
                        "source": "asos",
                        "source_id": product_id,
                        "title": name,
                        "original_image_url": image_url,
                        "price": float(price_current) if price_current else generate_price(),
                        "category": category,
                        "brand": item.get("brandName"),
                    }
                )
        except Exception as e:
            print(f"ASOS fetch error for category {category}: {e}")

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

    # Fetch from both sources
    all_products = []
    try:
        all_products.extend(fetch_amazon_products(secrets))
    except Exception as e:
        errors.append({"source": "amazon", "error": str(e)})
        print(f"Amazon fetch failed entirely: {e}")

    try:
        all_products.extend(fetch_asos_products(secrets))
    except Exception as e:
        errors.append({"source": "asos", "error": str(e)})
        print(f"ASOS fetch failed entirely: {e}")

    items_fetched = len(all_products)

    # Process each product
    for product in all_products:
        try:
            if item_exists(supabase, product["source"], product["source_id"]):
                continue

            cdn_url = cache_image_to_s3(
                product["original_image_url"],
                product["source"],
                product["source_id"],
            )
            if not cdn_url:
                continue

            insert_item(
                supabase,
                {
                    "id": str(uuid.uuid4()),
                    "source": product["source"],
                    "source_id": product["source_id"],
                    "title": product["title"],
                    "image_url": cdn_url,
                    "original_image_url": product["original_image_url"],
                    "price": product["price"],
                    "category": product["category"],
                    "brand": product.get("brand"),
                    "is_active": True,
                },
            )
            items_added += 1
        except Exception as e:
            errors.append(
                {
                    "source": product["source"],
                    "source_id": product["source_id"],
                    "error": str(e),
                }
            )
            print(f"Error processing {product['source']}/{product['source_id']}: {e}")

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
