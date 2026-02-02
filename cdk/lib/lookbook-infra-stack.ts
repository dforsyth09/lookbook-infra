import * as cdk from 'aws-cdk-lib/core';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import * as path from 'path';

export class LookbookInfraStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // --- S3 Bucket for cached product images ---
    const imageBucket = new s3.Bucket(this, 'LookbookImages', {
      bucketName: `lookbook-images-${this.account}`,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      lifecycleRules: [
        {
          expiration: cdk.Duration.days(120),
        },
      ],
    });

    // --- CloudFront distribution ---
    const originAccessIdentity = new cloudfront.OriginAccessIdentity(
      this,
      'LookbookOAI'
    );
    imageBucket.grantRead(originAccessIdentity);

    const distribution = new cloudfront.Distribution(
      this,
      'LookbookDistribution',
      {
        defaultBehavior: {
          origin: new origins.S3Origin(imageBucket, {
            originAccessIdentity,
          }),
          viewerProtocolPolicy:
            cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        },
      }
    );

    // --- Secrets Manager secret (you populate this manually) ---
    const apiSecrets = new secretsmanager.Secret(this, 'LookbookApiSecrets', {
      secretName: 'lookbook/api-credentials',
      description: 'API keys for LookBook product sync',
      secretObjectValue: {
        RAPIDAPI_KEY: cdk.SecretValue.unsafePlainText('PLACEHOLDER'),
        AMAZON_ACCESS_KEY: cdk.SecretValue.unsafePlainText('PLACEHOLDER'),
        AMAZON_SECRET_KEY: cdk.SecretValue.unsafePlainText('PLACEHOLDER'),
        AMAZON_PARTNER_TAG: cdk.SecretValue.unsafePlainText('PLACEHOLDER'),
        SUPABASE_URL: cdk.SecretValue.unsafePlainText('PLACEHOLDER'),
        SUPABASE_SERVICE_KEY: cdk.SecretValue.unsafePlainText('PLACEHOLDER'),
      },
    });

    // --- Lambda Layer for Python dependencies ---
    const depsLayer = new lambda.LayerVersion(this, 'LookbookDepsLayer', {
      code: lambda.Code.fromAsset(
        path.join(__dirname, '../../lambda/layer')
      ),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      description: 'Dependencies for LookBook Lambdas',
    });

    // --- Product Sync Lambda ---
    const productSyncFn = new lambda.Function(this, 'ProductSyncFn', {
      functionName: 'lookbook-product-sync',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'product_sync.handler',
      code: lambda.Code.fromAsset(
        path.join(__dirname, '../../lambda/src')
      ),
      layers: [depsLayer],
      memorySize: 512,
      timeout: cdk.Duration.minutes(5),
      environment: {
        S3_BUCKET: imageBucket.bucketName,
        CLOUDFRONT_DOMAIN: distribution.distributionDomainName,
        SECRET_NAME: apiSecrets.secretName,
      },
    });

    imageBucket.grantReadWrite(productSyncFn);
    apiSecrets.grantRead(productSyncFn);

    // --- Cleanup Lambda ---
    const cleanupFn = new lambda.Function(this, 'CleanupFn', {
      functionName: 'lookbook-cleanup',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'cleanup.handler',
      code: lambda.Code.fromAsset(
        path.join(__dirname, '../../lambda/src')
      ),
      layers: [depsLayer],
      memorySize: 256,
      timeout: cdk.Duration.minutes(3),
      environment: {
        S3_BUCKET: imageBucket.bucketName,
        SECRET_NAME: apiSecrets.secretName,
      },
    });

    imageBucket.grantReadWrite(cleanupFn);
    apiSecrets.grantRead(cleanupFn);

    // --- EventBridge Schedules ---
    new events.Rule(this, 'DailySyncRule', {
      ruleName: 'lookbook-daily-sync',
      schedule: events.Schedule.cron({ hour: '6', minute: '0' }),
      targets: [new targets.LambdaFunction(productSyncFn)],
    });

    new events.Rule(this, 'WeeklyCleanupRule', {
      ruleName: 'lookbook-weekly-cleanup',
      schedule: events.Schedule.cron({
        hour: '7',
        minute: '0',
        weekDay: 'SUN',
      }),
      targets: [new targets.LambdaFunction(cleanupFn)],
    });

    // --- Outputs ---
    new cdk.CfnOutput(this, 'CloudFrontDomain', {
      value: distribution.distributionDomainName,
      description: 'CloudFront domain for product images',
    });

    new cdk.CfnOutput(this, 'ImageBucketName', {
      value: imageBucket.bucketName,
      description: 'S3 bucket for product images',
    });

    new cdk.CfnOutput(this, 'SecretArn', {
      value: apiSecrets.secretArn,
      description: 'ARN of the API keys secret — update values in AWS Console',
    });
  }
}
