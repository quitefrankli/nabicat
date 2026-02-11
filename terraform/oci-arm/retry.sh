#!/bin/bash
# Periodically attempts to deploy ARM instance until capacity is available.
# Usage: bash retry_arm.sh [interval_seconds]
# Default interval: 300 (5 minutes)

INTERVAL=${1:-300}
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

echo "Retrying ARM instance every ${INTERVAL}s in $SCRIPT_DIR ..."

while true; do
	output=$(terraform -chdir="$SCRIPT_DIR" apply -auto-approve 2>&1)
	if [ $? -eq 0 ]; then
		echo "SUCCESS: ARM instance created!"
		echo "$output" | grep -E "public_ip|instance_ocid"
		exit 0
	fi

	if echo "$output" | grep -q "Out of host capacity"; then
		echo "$(date): No capacity yet, retrying in ${INTERVAL}s..."
	else
		echo "$(date): Unexpected error:"
		echo "$output" | tail -5
		exit 1
	fi

	sleep $INTERVAL
done
