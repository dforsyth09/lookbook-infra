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
│   ├── build_layer.sh                   # Builds the Lambda layer locally
│   └── test_apis.py                     # Local API testing script
├── supabase_migration_v2.sql            # Migration for user accounts + new fields
├── ios_updates_plan.md                  # Plan for iOS app changes
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

### Initial Setup (New Project)

If starting fresh, run `supabase_migration_v2.sql` in its entirety.

### Migration (Existing Tables)

If you already have `clothing_items` and `sync_log` tables, run **only** `supabase_migration_v2.sql` which will:
- Add new columns to `clothing_items` (colour, is_on_sale, additional_image_urls, source_url)
- Create `users` table (for device-based auth)
- Create `cart_items` table (synced cart for admin visibility)
- Create `wishlist_items` table
- Create `admin_cart_view` (helper view for admin dashboard)
- Set up Row Level Security policies
- Create `link_admin_to_user()` helper function

See `supabase_migration_v2.sql` for full schema.

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

## Admin/User Account Linking

The app supports linked accounts so you (admin) can see your mother-in-law's cart and purchase items for her.

### How It Works

1. Both devices run the app, which auto-generates a unique device ID
2. You link your device as "admin" to her device via SQL
3. Your app shows an Admin tab where you can see her cart with real ASOS URLs

### Setup Steps

1. On **her phone**: 10-tap the app logo to reveal her device ID, copy it
2. On **your phone**: Same gesture, copy your device ID
3. Run in Supabase SQL Editor:
   ```sql
   SELECT link_admin_to_user('YOUR-DEVICE-ID', 'HER-DEVICE-ID');
   ```
4. Restart your app — Admin tab appears with her cart

See `ios_updates_plan.md` for full iOS implementation details.
