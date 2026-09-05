#!/bin/sh
export PYTHONPATH=/home/iconbaypark2900/openworker-tasks/a7090ed4-579
python verify_concord.py
echo "---- metered ----"
python verify_metered.py
