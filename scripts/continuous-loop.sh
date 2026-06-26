#!/bin/bash
# Continuous review loop - monitors and improves breakability system

LOG=~/code/breakability/loop.log
REVIEWERS=(code-review rubber-duck)

echo "[$(date '+%H:%M:%S')] Loop started" | tee -a $LOG

while true; do
    echo "[$(date '+%H:%M:%S')] === Iteration Start ===" | tee -a $LOG
    
    # Check latest NDM test run
    cd ~/code/ndm-fresh-breakability
    LATEST=$(gh run list --workflow=breakability-agent.yml --limit 1 --json databaseId,status,conclusion --jq '.[0]')
    RUN_ID=$(echo $LATEST | jq -r '.databaseId')
    STATUS=$(echo $LATEST | jq -r '.status')
    CONCLUSION=$(echo $LATEST | jq -r '.conclusion')
    
    echo "[$(date '+%H:%M:%S')] Run $RUN_ID: $STATUS $CONCLUSION" | tee -a $LOG
    
    if [[ "$STATUS" == "completed" ]]; then
        # Validate against gold standards
        echo "[$(date '+%H:%M:%S')] Validating PR #67, #66..." | tee -a $LOG
        
        PR67=$(gh api repos/CSC-Security-sandbox/ndm-fresh-breakability/issues/67/comments --jq '.[-1].body' 2>/dev/null)
        PR66=$(gh api repos/CSC-Security-sandbox/ndm-fresh-breakability/issues/66/comments --jq '.[-1].body' 2>/dev/null)
        
        # Check PR #67 (uuid - NOT REACHED expected)
        if echo "$PR67" | grep -q "verify affected callsites"; then
            echo "[$(date '+%H:%M:%S')] ❌ PR #67 FAIL: Shows callsites for NOT REACHED" | tee -a $LOG
            ISSUE="PR #67 uuid (NOT REACHED) shows 'verify callsites' instead of 'Review changelog'"
        elif echo "$PR67" | grep -q "Review the changelog"; then
            echo "[$(date '+%H:%M:%S')] ✅ PR #67 PASS" | tee -a $LOG
        else
            echo "[$(date '+%H:%M:%S')] ⚠️  PR #67 UNKNOWN format" | tee -a $LOG
            ISSUE="PR #67 has unexpected format"
        fi
        
        # Check PR #66 (jwks-rsa - NOT REACHED expected)
        if echo "$PR66" | grep -q "verify affected callsites"; then
            echo "[$(date '+%H:%M:%S')] ❌ PR #66 FAIL: Shows callsites for NOT REACHED" | tee -a $LOG
            ISSUE="PR #66 jwks-rsa (NOT REACHED) shows 'verify callsites'"
        elif echo "$PR66" | grep -q "Review the changelog"; then
            echo "[$(date '+%H:%M:%S')] ✅ PR #66 PASS" | tee -a $LOG
        fi
        
        # If issue found, trigger review agents
        if [[ -n "$ISSUE" ]]; then
            echo "[$(date '+%H:%M:%S')] Triggering review for: $ISSUE" | tee -a $LOG
            
            # Log to user's visible location
            echo "ISSUE DETECTED: $ISSUE" >> ~/.copilot/session-state/3bf7c685-5c9f-4bf6-9c80-06f53938dd10/loop-issues.txt
            
            # Sleep before next check
            sleep 600  # 10 min
        else
            echo "[$(date '+%H:%M:%S')] ✅ All validations passed" | tee -a $LOG
            sleep 1800  # 30 min
        fi
    else
        # Run in progress or queued
        echo "[$(date '+%H:%M:%S')] Waiting for completion..." | tee -a $LOG
        sleep 180  # 3 min
    fi
done
