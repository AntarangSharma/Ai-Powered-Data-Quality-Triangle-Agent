#!/usr/bin/env bash
# Deploy to Google Cloud Run helper script

set -euo pipefail

# Configuration
PROJECT_ID=${GCP_PROJECT_ID:-"my-gcp-project-id"}
SERVICE_NAME="dq-triage-agent"
REGION="us-central1"
IMAGE_TAG="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

echo "=================================================="
echo "🚀 Preparing deployment package for Cloud Run..."
echo "=================================================="
echo "Project ID: ${PROJECT_ID}"
echo "Service Name: ${SERVICE_NAME}"
echo "Region: ${REGION}"
echo "Image Tag: ${IMAGE_TAG}"
echo "=================================================="

# Check for gcloud command
if ! command -v gcloud &> /dev/null; then
    echo "❌ Error: Google Cloud SDK (gcloud) is not installed."
    echo "Please install it and authenticate first: https://cloud.google.com/sdk"
    exit 1
fi

echo "📦 Building docker container using Cloud Build..."
gcloud builds submit --tag "${IMAGE_TAG}" --project "${PROJECT_ID}"

echo "🚀 Deploying to GCP Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
    --image "${IMAGE_TAG}" \
    --platform managed \
    --region "${REGION}" \
    --allow-unauthenticated \
    --project "${PROJECT_ID}" \
    --set-env-vars="ANTHROPIC_API_KEY=\${ANTHROPIC_API_KEY}"

echo "🟢 Deployment finished successfully!"
