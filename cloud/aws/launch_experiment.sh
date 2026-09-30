#!/usr/bin/env bash
# Run the transfer experiment unattended on one EC2 instance, with hard cost limits.
#
#   export AWS_PROFILE=...            # an IAM user allowed ec2:RunInstances, s3:*, budgets:*
#   cloud/aws/launch_experiment.sh s3://my-bucket/wta-runs transfer_final
#
# What it does, and every guard it applies:
#
# * Launches ONE CPU instance (default c7i.4xlarge, 16 vCPU, ~$0.71/h on demand in
#   us-east-1; spot is used when available at a lower price). The workload is browser-bound
#   -- 75% of an environment step is the page settling -- so a GPU instance would cost more
#   and finish no sooner. 16 vCPU run 12-14 browser workers.
# * The instance shuts itself down after MAX_HOURS no matter what (`shutdown -h +N` in the
#   user data, with `InstanceInitiatedShutdownBehavior=terminate`), so a hung job cannot bill
#   indefinitely. At the default 10 h and on-demand price that is a ceiling of about $7.
# * Results are synced to S3 every 5 minutes and once more at the end. The experiment is
#   resumable, so an interrupted instance loses at most the jobs in flight; re-running this
#   script with the same S3 prefix pulls the finished results down first and continues.
# * The LLM runs offline: the image carries the cached judgments (data/llm_cache), and the
#   runner is started with --llm-offline, so no model and no GPU are needed in the cloud. A
#   judgment missing from the cache means no shaping for that transition and is counted.
# * An AWS Budget with an 80% alert is created on first use (BUDGET_USD, default 40).
#
# Requires: aws CLI v2 configured, a VPC with a default subnet, and the repository pushed to
# a git remote the instance can clone (GIT_URL) at the commit to run (GIT_REF).

set -euo pipefail

S3_PREFIX="${1:?usage: launch_experiment.sh s3://bucket/prefix run_name}"
RUN_NAME="${2:?usage: launch_experiment.sh s3://bucket/prefix run_name}"
INSTANCE_TYPE="${INSTANCE_TYPE:-c7i.4xlarge}"
WORKERS="${WORKERS:-12}"
MAX_HOURS="${MAX_HOURS:-10}"
BUDGET_USD="${BUDGET_USD:-40}"
REGION="${AWS_REGION:-us-east-1}"
GIT_URL="${GIT_URL:?set GIT_URL to a clonable URL of this repository}"
GIT_REF="${GIT_REF:-$(git rev-parse HEAD)}"
EXTRA_ARGS="${EXTRA_ARGS:-}"

echo "run=$RUN_NAME type=$INSTANCE_TYPE workers=$WORKERS max_hours=$MAX_HOURS ref=$GIT_REF"

# -- budget guard (idempotent) ---------------------------------------------------------------
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
if ! aws budgets describe-budget --account-id "$ACCOUNT_ID" --budget-name wta-experiments >/dev/null 2>&1; then
  aws budgets create-budget --account-id "$ACCOUNT_ID" \
    --budget "{\"BudgetName\":\"wta-experiments\",\"BudgetLimit\":{\"Amount\":\"$BUDGET_USD\",\"Unit\":\"USD\"},\"TimeUnit\":\"MONTHLY\",\"BudgetType\":\"COST\"}" \
    --notifications-with-subscribers "[{\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":80},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"${ALERT_EMAIL:?set ALERT_EMAIL for the budget alert}\"}]}]"
  echo "created budget wta-experiments (\$$BUDGET_USD/month, alert at 80%)"
fi

# -- the instance's job ---------------------------------------------------------------------
USER_DATA="$(cat <<EOF
#!/bin/bash
set -x
shutdown -h +$((MAX_HOURS * 60))
apt-get update && apt-get install -y docker.io git awscli
systemctl start docker
git clone "$GIT_URL" /srv/wta && cd /srv/wta && git checkout "$GIT_REF"
docker build -t wta .
mkdir -p /srv/runs/$RUN_NAME
aws s3 sync "$S3_PREFIX/$RUN_NAME" /srv/runs/$RUN_NAME || true
( while true; do sleep 300; aws s3 sync /srv/runs "$S3_PREFIX" --exclude '*/checkpoints/*.tmp'; done ) &
docker run --rm -v /srv/runs:/app/runs wta \
  python scripts/run_transfer.py --out-dir runs/$RUN_NAME --workers $WORKERS --llm-offline $EXTRA_ARGS \
  > /srv/runs/$RUN_NAME/runner.log 2>&1
aws s3 sync /srv/runs "$S3_PREFIX"
shutdown -h now
EOF
)"

AMI="$(aws ssm get-parameters --region "$REGION" \
  --names /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query 'Parameters[0].Value' --output text)"

INSTANCE_ID="$(aws ec2 run-instances --region "$REGION" --image-id "$AMI" \
  --instance-type "$INSTANCE_TYPE" \
  --instance-market-options '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"one-time","InstanceInterruptionBehavior":"terminate"}}' \
  --instance-initiated-shutdown-behavior terminate \
  --iam-instance-profile "Name=${INSTANCE_PROFILE:?set INSTANCE_PROFILE to a profile with S3 write access}" \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":40,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=wta-$RUN_NAME},{Key=project,Value=wta}]" \
  --user-data "$USER_DATA" --query 'Instances[0].InstanceId' --output text)"

echo "launched $INSTANCE_ID; it terminates itself when the experiment finishes or after $MAX_HOURS h."
echo "results: aws s3 sync $S3_PREFIX/$RUN_NAME runs/$RUN_NAME"
echo "stop early: aws ec2 terminate-instances --instance-ids $INSTANCE_ID"
