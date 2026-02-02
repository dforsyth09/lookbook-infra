#!/bin/bash
# Builds the Lambda layer with Python dependencies.
# Run this before `cdk deploy`.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAYER_DIR="$SCRIPT_DIR/layer"
OUTPUT_DIR="$LAYER_DIR/python"

echo "Installing dependencies into $OUTPUT_DIR ..."
rm -rf "$OUTPUT_DIR"
pip install -r "$LAYER_DIR/requirements.txt" -t "$OUTPUT_DIR" --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.12 --quiet

echo "Layer built. Size: $(du -sh "$OUTPUT_DIR" | cut -f1)"
echo "Ready for cdk deploy."
