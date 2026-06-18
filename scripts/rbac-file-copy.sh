#!/bin/bash
#set -x

source_bucket=$1
rbac_file_path=$2
rbac_file_name=$3
release_version=$4
sha256_file_name=$5  # Checksum file name as input parameter
verify_checksum=${6:-false}  # Checksum verify flag as 6th parameter, default to false

#check files exist in the source bucket
gsutil -q stat "gs://${source_bucket}/${rbac_file_path}/${rbac_file_name}"
RBAC_FILE_EXIST=$?
if [ ${RBAC_FILE_EXIST} -ne 0 ]; then
    echo "❌ Error: RBAC file not found in the source bucket"
    exit 1
fi

gsutil -q stat "gs://${source_bucket}/${rbac_file_path}/${sha256_file_name}"
SHA256_FILE_EXIST=$?
if [ ${SHA256_FILE_EXIST} -ne 0 ]; then
    echo "❌ Error: RBAC SHA256 file not found in the source bucket"
    exit 1
fi
echo "✅ Files found in the source bucket"




#validate checksum of the files by validating the sha256 file of rbac file (only if verify_checksum is true)
if [ "$verify_checksum" = "true" ]; then
    gsutil cp "gs://${source_bucket}/${rbac_file_path}/${rbac_file_name}" .
    gsutil cp "gs://${source_bucket}/${rbac_file_path}/${sha256_file_name}" .
    checksum=$(sha256sum "${rbac_file_name}" | awk '{print $1}')
    checksum_sha256=$(cat "${sha256_file_name}" | awk '{print $1}')

    if [ "$checksum" != "$checksum_sha256" ]; then
        echo "❌ Error: Checksum mismatch for the file"
        exit 1
    else 
        echo "✅ Checksum match for the file"
    fi
else
    echo "⚠️  Checksum verification skipped (verify_checksum=false)"
fi


# Copy RBAC file from source bucket to destination bucket
gsutil cp "gs://${source_bucket}/${rbac_file_path}/${rbac_file_name}" "gs://vsa-compute-images/GCNV/${release_version}/RBAC/${rbac_file_name}"
#validate file copied successfully to gcp storage bucket vsa-compute-images
gsutil -q stat "gs://vsa-compute-images/GCNV/${release_version}/RBAC/${rbac_file_name}"
RBAC_FILE_EXIST=$?
if [ ${RBAC_FILE_EXIST} -ne 0 ]; then
    echo "❌ Error: RBAC file not found in the gcp storage bucket vsa-compute-images"
    exit 1
fi
echo "✅ Files found in the source bucket"


# Copy RBAC SHA256 file from source bucket to destination bucket
gsutil cp "gs://${source_bucket}/${rbac_file_path}/${sha256_file_name}" "gs://vsa-compute-images/GCNV/${release_version}/RBAC/${sha256_file_name}"
gsutil -q stat "gs://vsa-compute-images/GCNV/${release_version}/RBAC/${sha256_file_name}"
SHA256_FILE_EXIST=$?
if [ ${SHA256_FILE_EXIST} -ne 0 ]; then
    echo "❌ Error: RBAC SHA256 file not found in the gcp storage bucket vsa-compute-images"
    exit 1
fi


echo "✅ RBAC file and SHA256 file copied to destination bucket vsa-compute-images"
