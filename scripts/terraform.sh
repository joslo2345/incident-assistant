#!/usr/bin/env bash
# Terraform from the official image, pinned by digest (no local install needed):
#   scripts/terraform.sh <dir> <terraform args...>     e.g. scripts/terraform.sh infra/azure validate
# Azure credentials, when needed (plan/apply), come from your `az login` (~/.azure is mounted).
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=$1; shift
exec docker run --rm -i \
  -v "$PWD/$DIR:/w" -w /w \
  -v "${HOME}/.azure:/root/.azure" \
  -e ARM_USE_CLI=true \
  hashicorp/terraform:1.16.4@sha256:985cdc6c1d9b0a65b83377f666efd2f740b47f02ac55be1ced3d18f7d3b0e829 "$@"
