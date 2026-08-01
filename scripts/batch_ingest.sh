#!/bin/bash

# Batch Harvester for openworker
# Recursively scans a directory for PDFs, Python scripts, and Jupyter Notebooks
# and pipes them into the Harvester to generate Expert Packs.

INPUT_DIR=${1:-"/Users/jose/Onedrive/AI_Master-2023"}
OUTPUT_DIR="knowledge_packs"
VISION_FLAG=""

# Check if --vision is passed as the second argument
if [[ "$2" == "--vision" ]]; then
    VISION_FLAG="--vision"
    echo "[!] Multimodal Vision enabled for PDFs."
fi

echo "=========================================="
echo "Starting Big Bang Knowledge Ingestion"
echo "Target Directory: $INPUT_DIR"
echo "Output Directory: $OUTPUT_DIR"
echo "=========================================="

mkdir -p "$OUTPUT_DIR"

# Find all relevant files and loop through them
find "$INPUT_DIR" -type f \( -iname "*.pdf" -o -iname "*.py" -o -iname "*.ipynb" -o -iname "*.md" -o -iname "*.txt" \) | while read -r FILE; do
    
    # Skip temporary files or hidden files
    if [[ "$FILE" == */.* ]]; then
        continue
    fi

    # Extract the base filename without extension
    BASENAME=$(basename "$FILE")
    NAME="${BASENAME%.*}"
    
    # Clean the filename for the output (replace spaces with underscores, lowercase)
    CLEAN_NAME=$(echo "$NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '_')
    OUTPUT_FILE="$OUTPUT_DIR/${CLEAN_NAME}.md"

    # Skip if already harvested (prevents double ingestion if script stops)
    if [[ -f "$OUTPUT_FILE" ]]; then
        echo "[-] Skipping $BASENAME (Already harvested)"
        continue
    fi

    echo "[+] Harvesting: $BASENAME"
    
    # Run the harvester
    if [[ "$FILE" == *.pdf ]] && [[ -n "$VISION_FLAG" ]]; then
        uv run scripts/harvester.py "$FILE" --output "$OUTPUT_FILE" $VISION_FLAG
    else
        uv run scripts/harvester.py "$FILE" --output "$OUTPUT_FILE"
    fi
    
    # Brief pause to respect API rate limits
    sleep 2
done

echo "=========================================="
echo "Ingestion Complete!"
echo "Check $OUTPUT_DIR for your new Expert Packs."
echo "=========================================="
