#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { LookbookInfraStack } from '../lib/lookbook-infra-stack';

const app = new cdk.App();
new LookbookInfraStack(app, 'LookbookInfraStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
});
