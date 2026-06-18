#!/bin/bash
#set -x

# Script to copy signature files from source bucket to target bucket
# Usage: signature-file-copy.sh <version> <image_type> <sig_file_path> <target_bucket>
#
# Parameters:
#   version: Version string (e.g., 9.18.1X32)
#   image_type: Image type in uppercase (e.g., VSA or MEDIATOR)
#   sig_file_path: Full GCS path to signature file (e.g., gs://cot-releases-public/.../IMAGE_SIG_TGZ)
#   target_bucket: Target bucket name (e.g., vsa-compute-images or gcnv-autopush-images-bucket)

VERSION=$1
IMAGE_TYPE=$2
SIG_FILE_PATH=$3
TARGET_BUCKET=$4

# Validate required parameters
if [ -z "$VERSION" ] || [ -z "$IMAGE_TYPE" ] || [ -z "$SIG_FILE_PATH" ] || [ -z "$TARGET_BUCKET" ]; then
    echo "❌ Error: Missing required parameters"
    echo "Usage: signature-file-copy.sh <version> <image_type> <sig_file_path> <target_bucket>"
    exit 1
fi

echo "📋 Signature File Copy Script"
echo "Version: $VERSION"
echo "Image Type: $IMAGE_TYPE"
echo "Source Signature File: $SIG_FILE_PATH"
echo "Target Bucket: $TARGET_BUCKET"
echo ""

# Extract filename from signature file path
SIG_FILE_NAME=$(basename "$SIG_FILE_PATH")

# Construct destination path: GCNV/{VERSION}/{IMAGE_TYPE}/{filename}
DEST_PATH="GCNV/${VERSION}/${IMAGE_TYPE}/${SIG_FILE_NAME}"
DEST_URI="gs://${TARGET_BUCKET}/${DEST_PATH}"

echo "Destination Path: $DEST_PATH"
echo "Destination URI: $DEST_URI"
echo ""

# Check if source file exists
echo "🔍 Checking if source signature file exists..."
gsutil -q stat "$SIG_FILE_PATH"
SOURCE_EXISTS=$?

if [ ${SOURCE_EXISTS} -ne 0 ]; then
    echo "❌ Error: Signature file not found in source: $SIG_FILE_PATH"
    exit 1
fi
echo "✅ Source signature file found"

# Check if destination file already exists
echo "🔍 Checking if destination signature file already exists..."
gsutil -q stat "$DEST_URI"
DEST_EXISTS=$?

if [ ${DEST_EXISTS} -eq 0 ]; then
    echo "⚠️  Signature file already exists in destination: $DEST_URI"
    echo "✅ Skipping copy - file already present"
    exit 0
fi

# Create temporary working directory
WORK_DIR=$(mktemp -d)
cd "$WORK_DIR"

echo "📥 Step 1: Downloading IMAGE_SIG_TGZ from source..."
gsutil cp "$SIG_FILE_PATH" ./IMAGE_SIG_TGZ

DOWNLOAD_EXIT_CODE=$?
if [ ${DOWNLOAD_EXIT_CODE} -ne 0 ]; then
    echo "❌ Error: Failed to download signature file from source"
    rm -rf "$WORK_DIR"
    exit 1
fi
echo "✅ IMAGE_SIG_TGZ downloaded"

echo "📦 Step 2: Extracting IMAGE_SIG_TGZ..."
tar -xzf IMAGE_SIG_TGZ

EXTRACT_EXIT_CODE=$?
if [ ${EXTRACT_EXIT_CODE} -ne 0 ]; then
    echo "❌ Error: Failed to extract IMAGE_SIG_TGZ"
    rm -rf "$WORK_DIR"
    exit 1
fi
echo "✅ IMAGE_SIG_TGZ extracted"

echo "🔍 Step 3: Finding digest signature file..."
DIGEST_SIG=$(ls -1 *_digest.sig 2>/dev/null | head -n 1)

if [ -z "$DIGEST_SIG" ]; then
    echo "❌ Error: _digest.sig file not found after extraction"
    echo "Available files:"
    ls -la
    rm -rf "$WORK_DIR"
    exit 1
fi
echo "✅ Found digest signature file: $DIGEST_SIG"

echo "🧹 Step 4: Removing all files except digest signature..."
# Remove all files except the digest signature file
find . -type f ! -name "$DIGEST_SIG" ! -name "IMAGE_SIG_TGZ" -delete
find . -type d -empty -delete

echo "📦 Step 5: Creating new tar.gz with only digest signature..."
tar -czf IMAGE_SIG_TGZ "$DIGEST_SIG"

TAR_EXIT_CODE=$?
if [ ${TAR_EXIT_CODE} -ne 0 ]; then
    echo "❌ Error: Failed to create new tar.gz file"
    rm -rf "$WORK_DIR"
    exit 1
fi
echo "✅ New IMAGE_SIG_TGZ created with only digest signature"

echo "📤 Step 6: Uploading processed signature file to destination..."
gsutil cp ./IMAGE_SIG_TGZ "$DEST_URI"

COPY_EXIT_CODE=$?
if [ ${COPY_EXIT_CODE} -ne 0 ]; then
    echo "❌ Error: Failed to upload signature file to destination"
    rm -rf "$WORK_DIR"
    exit 1
fi
echo "✅ Signature file uploaded to destination"

# Verify file was uploaded successfully
echo "🔍 Step 7: Verifying file was uploaded successfully..."
gsutil -q stat "$DEST_URI"
VERIFY_EXISTS=$?

if [ ${VERIFY_EXISTS} -ne 0 ]; then
    echo "❌ Error: Signature file not found in destination after upload: $DEST_URI"
    rm -rf "$WORK_DIR"
    exit 1
fi

# Cleanup
rm -rf "$WORK_DIR"

echo "✅ Signature file processed and uploaded successfully to: $DEST_URI"
echo "✅ Signature file copy completed"

