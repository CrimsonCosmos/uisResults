#!/bin/bash
#
# Deploy UIS Athletics Notifier to Google Cloud
#
# Prerequisites:
# 1. Google Cloud account (free tier is sufficient)
# 2. gcloud CLI installed: https://cloud.google.com/sdk/docs/install
# 3. Gmail App Password created
#
# Usage:
#   ./deploy.sh <your-gmail-app-password>
#

set -e

# Configuration
PROJECT_ID="uis-athletics-notifier"
REGION="us-central1"
FUNCTION_NAME="check-results"
INIT_FUNCTION_NAME="initialize-state"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}UIS Athletics Notifier - Cloud Deploy${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check for Gmail App Password
if [ -z "$1" ]; then
    echo -e "${RED}Error: Please provide your Gmail App Password${NC}"
    echo ""
    echo "Usage: ./deploy.sh <gmail-app-password>"
    echo ""
    echo "To create an App Password:"
    echo "1. Go to https://myaccount.google.com/apppasswords"
    echo "2. Select 'Mail' and your device"
    echo "3. Copy the 16-character password"
    exit 1
fi

GMAIL_APP_PASSWORD="$1"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo -e "${RED}Error: gcloud CLI is not installed${NC}"
    echo ""
    echo "Install it from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

echo -e "${YELLOW}Step 1: Checking Google Cloud authentication...${NC}"
if ! gcloud auth list 2>&1 | grep -q "ACTIVE"; then
    echo "Please log in to Google Cloud:"
    gcloud auth login
fi

echo -e "${YELLOW}Step 2: Creating/selecting project...${NC}"
# Check if project exists
if ! gcloud projects describe $PROJECT_ID &> /dev/null 2>&1; then
    echo "Creating new project: $PROJECT_ID"
    gcloud projects create $PROJECT_ID --name="UIS Athletics Notifier"
fi
gcloud config set project $PROJECT_ID

echo -e "${YELLOW}Step 3: Enabling required APIs...${NC}"
gcloud services enable cloudfunctions.googleapis.com
gcloud services enable cloudscheduler.googleapis.com
gcloud services enable firestore.googleapis.com
gcloud services enable cloudbuild.googleapis.com

echo -e "${YELLOW}Step 4: Setting up Firestore...${NC}"
# Create Firestore database if it doesn't exist
if ! gcloud firestore databases describe --database="(default)" &> /dev/null 2>&1; then
    gcloud firestore databases create --location=$REGION --type=firestore-native
fi

echo -e "${YELLOW}Step 5: Deploying Cloud Function...${NC}"
gcloud functions deploy $FUNCTION_NAME \
    --gen2 \
    --runtime=python311 \
    --region=$REGION \
    --source=. \
    --entry-point=check_results \
    --trigger-http \
    --allow-unauthenticated \
    --set-env-vars="GMAIL_APP_PASSWORD=$GMAIL_APP_PASSWORD,RECIPIENT_EMAIL=dylangehl31@gmail.com,SENDER_EMAIL=dylangehl31@gmail.com" \
    --memory=256MB \
    --timeout=60s

# Get the function URL
FUNCTION_URL=$(gcloud functions describe $FUNCTION_NAME --region=$REGION --format='value(serviceConfig.uri)')

echo -e "${YELLOW}Step 6: Deploying initialization function...${NC}"
gcloud functions deploy $INIT_FUNCTION_NAME \
    --gen2 \
    --runtime=python311 \
    --region=$REGION \
    --source=. \
    --entry-point=initialize_state \
    --trigger-http \
    --allow-unauthenticated \
    --set-env-vars="GMAIL_APP_PASSWORD=$GMAIL_APP_PASSWORD" \
    --memory=256MB \
    --timeout=120s

INIT_URL=$(gcloud functions describe $INIT_FUNCTION_NAME --region=$REGION --format='value(serviceConfig.uri)')

echo -e "${YELLOW}Step 7: Initializing state (loading existing results)...${NC}"
curl -s "$INIT_URL"
echo ""

echo -e "${YELLOW}Step 8: Creating Cloud Scheduler job (runs every minute)...${NC}"
# Delete existing job if it exists
gcloud scheduler jobs delete uis-notifier-job --location=$REGION --quiet 2>/dev/null || true

gcloud scheduler jobs create http uis-notifier-job \
    --location=$REGION \
    --schedule="* * * * *" \
    --uri="$FUNCTION_URL" \
    --http-method=GET \
    --attempt-deadline=60s

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Your notifier is now running in the cloud!"
echo ""
echo "Function URL: $FUNCTION_URL"
echo "Schedule: Every minute"
echo ""
echo "To test manually:"
echo "  curl $FUNCTION_URL"
echo ""
echo "To view logs:"
echo "  gcloud functions logs read $FUNCTION_NAME --region=$REGION"
echo ""
echo "To pause notifications:"
echo "  gcloud scheduler jobs pause uis-notifier-job --location=$REGION"
echo ""
echo "To resume notifications:"
echo "  gcloud scheduler jobs resume uis-notifier-job --location=$REGION"
echo ""
echo -e "${GREEN}Estimated monthly cost: \$0.00 (free tier)${NC}"
