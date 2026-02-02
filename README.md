# lookbook-infra

AWS infrastructure for the LookBook iOS app — a therapeutic shopping simulator for seniors. This repo contains the CDK stack and Lambda functions that power the product data pipeline.

## Architecture

```
EventBridge (daily cron)
    │
    ├── lookbook-product-sync Lambda
    │       ├── Amazon Creators API  →  fetch women's clothing
    │       ├── ASOS API (RapidAPI)  →  fetch women's clothing
    │       ├── S3 + CloudFront      →  cache product images
    │       └── Supabase             →  store product metadata
    │
    └── lookbook-cleanup Lambda (weekly cron)
            ├── Supabase  →  deactivate items older than 90 days
            └── S3        →  delete expired images
```

## AWS Resources Created

| Resource | Name / ID | Purpose |
|----------|-----------|---------|
| S3 Bucket | `lookbook-images-{account_id}` | Cached product images |
| CloudFront Distribution | (auto-generated) | CDN for serving images to the iOS app |
| Lambda | `lookbook-product-sync` | Daily product fetch from Amazon + ASOS |
| Lambda | `lookbook-cleanup` | Weekly cleanup of old products |
| Lambda Layer | `LookbookDepsLayer` | Shared Python dependencies |
| EventBridge Rule | `lookbook-daily-sync` | Triggers product-sync daily at 6:00 AM UTC |
| EventBridge Rule | `lookbook-weekly-cleanup` | Triggers cleanup every Sunday at 7:00 AM UTC |
| Secrets Manager | `lookbook/api-credentials` | API keys and Supabase credentials |

## Repo Structure

```
lookbook-infra/
├── cdk/
│   ├── bin/cdk.ts                       # CDK app entry point
│   └── lib/lookbook-infra-stack.ts      # Full infrastructure stack
├── lambda/
│   ├── src/
│   │   ├── product_sync.py              # Product fetch + S3 cache + Supabase sync
│   │   └── cleanup.py                   # Deactivate old items, delete S3 objects
│   ├── layer/
│   │   └── requirements.txt             # Python dependencies for Lambda layer
│   └── build_layer.sh                   # Builds the Lambda layer locally
└── README.md
```

## Prerequisites

- Node.js (LTS) + npm
- AWS CLI configured (`aws configure`)
- AWS CDK CLI (`npm install -g aws-cdk`)
- Python 3.12
- CDK bootstrapped in your account (`cdk bootstrap`)
- Supabase project with tables created (see Database Setup below)

## Database Setup

Run the following SQL in your Supabase SQL Editor before deploying:

```sql
CREATE TABLE IF NOT EXISTS clothing_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    image_url TEXT NOT NULL,
    original_image_url TEXT,
    price NUMERIC(10,2) NOT NULL,
    category TEXT NOT NULL,
    brand TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(source, source_id)
);

CREATE INDEX idx_clothing_active_category ON clothing_items (is_active, category);

CREATE TABLE IF NOT EXISTS sync_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL,
    items_fetched INTEGER,
    items_added INTEGER,
    errors JSONB,
    ran_at TIMESTAMPTZ DEFAULT now()
);
```

## Deploy

```bash
# 1. Build the Lambda layer
cd lambda
./build_layer.sh

# 2. Deploy the stack
cd ../cdk
npx cdk deploy
```

## Post-Deploy: Set API Credentials

After the first deploy, the Secrets Manager secret `lookbook/api-credentials` will contain placeholder values. Update them in the AWS Console (Secrets Manager → lookbook/api-credentials → Retrieve secret value → Edit):

| Key | Value |
|-----|-------|
| `RAPIDAPI_KEY` | Your RapidAPI key (subscribe to ASOS API free tier) |
| `AMAZON_ACCESS_KEY` | Amazon Creators API credential ID |
| `AMAZON_SECRET_KEY` | Amazon Creators API credential secret |
| `AMAZON_PARTNER_TAG` | Your Amazon Associates tag (e.g. `yourtag-20`) |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Your Supabase service role key |

## Testing

After credentials are set, test the sync Lambda manually:

1. AWS Console → Lambda → `lookbook-product-sync`
2. Create a test event with `{}`
3. Run it and check:
   - Supabase `clothing_items` table for new rows
   - Supabase `sync_log` table for the run summary
   - CloudFront URLs from `image_url` column load in a browser

## Data Flow (How the iOS App Gets Products)

1. Lambda fetches products from Amazon + ASOS APIs daily
2. Product images are downloaded and stored in S3
3. Product metadata (title, price, category, CloudFront image URL) is written to Supabase
4. The iOS app reads from Supabase and loads images from CloudFront
5. The app caches everything locally for offline use

## Cost Estimate

| Resource | Monthly Cost |
|----------|-------------|
| Lambda | ~$0 (runs once daily, <5 min) |
| S3 | ~$0.02 (<1 GB of images) |
| CloudFront | ~$0.10 (single-user traffic) |
| Secrets Manager | ~$1.60 (4 secrets) |
| **Total** | **~$2/month** |
