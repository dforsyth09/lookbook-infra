"""
LookBook Cleanup Lambda
Deactivates old products (>90 days) and deletes their S3 images.
"""

import json
import os
from datetime import datetime, timezone, timedelta

import boto3
from supabase import create_client

s3 = boto3.client("s3")
secrets_client = boto3.client("secretsmanager")

S3_BUCKET = os.environ["S3_BUCKET"]
SECRET_NAME = os.environ["SECRET_NAME"]


def get_secrets():
    resp = secrets_client.get_secret_value(SecretId=SECRET_NAME)
    return json.loads(resp["SecretString"])


def handler(event, context):
    secrets = get_secrets()
    supabase = create_client(secrets["SUPABASE_URL"], secrets["SUPABASE_SERVICE_KEY"])

    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()

    # Find old active items
    result = (
        supabase.table("clothing_items")
        .select("id, source, source_id, image_url")
        .eq("is_active", True)
        .lt("created_at", cutoff)
        .execute()
    )

    deactivated = 0
    s3_deleted = 0

    for item in result.data:
        # Deactivate in Supabase
        try:
            supabase.table("clothing_items").update({"is_active": False}).eq(
                "id", item["id"]
            ).execute()
            deactivated += 1
        except Exception as e:
            print(f"Failed to deactivate {item['id']}: {e}")
            continue

        # Delete from S3
        try:
            url = item.get("image_url", "")
            # Extract S3 key from CloudFront URL: https://domain/source/id.ext
            parts = url.split("/")
            if len(parts) >= 2:
                s3_key = "/".join(parts[-2:])
                s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
                s3_deleted += 1
        except Exception as e:
            print(f"Failed to delete S3 object for {item['id']}: {e}")

    result_summary = {
        "items_deactivated": deactivated,
        "s3_objects_deleted": s3_deleted,
    }
    print(f"Cleanup complete: {json.dumps(result_summary)}")
    return result_summary
