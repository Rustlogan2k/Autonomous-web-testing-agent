#!/usr/bin/env bash
# Run one experiment unattended on one EC2 spot instance, with hard cost limits.
#
#   AWS_PROFILE=wta ALERT_EMAIL=you@example.org \
#     cloud/aws/launch_experiment.sh <bucket> <run_name> [extra run_transfer.py args...]
#
# Setup is in cloud/aws/SETUP.md. What this does, and every guard it applies:
#
# * Ships the code at HEAD as a `git archive` tarball, plus the LLM judgment cache and the
#   label-vector store, to s3://<bucket>/code/<commit>/. Nothing is pushed to any git remote.
#   Refuses to run from a dirty tree: a result must name a commit that contains its code.
# * Creates (once) an AWS Budget of BUDGET_USD per month with an email alert at 80%.
# * Launches ONE spot CPU instance (default c7i.4xlarge, 16 vCPU). The workload is
#   browser-bound, so a GPU would cost more and finish no sooner.
# * The instance terminates itself when the experiment finishes, and in any case after
#   MAX_HOURS (`shutdown -h +N` with InstanceInitiatedShutdownBehavior=terminate).
# * Results sync to s3://<bucket>/runs/<run_name>/ every 5 minutes and at the end. The
#   runner is resumable: relaunching with the same run name pulls finished jobs back first.
# * The language model runs offline (`--llm-offline`) from the shipped cache: no model and
#   no GPU in the cloud. A missing judgment means no LLM term for that transition, counted.

set -euo pipefail

BUCKET="${1:?usage: launch_experiment.sh <bucket> <run_name> [run_transfer args...]}"
RUN_NAME="${2:?usage: launch_experiment.sh <bucket> <run_name> [run_transfer args...]}"
shift 2
EXTRA_ARGS="$*"
INSTANCE_TYPE="${INSTANCE_TYPE:-c7i.4xlarge}"
WORKERS="${WORKERS:-12}"
MAX_HOURS="${MAX_HOURS:-10}"
BUDGET_USD="${BUDGET_USD:-40}"
REGION="${AWS_REGION:-us-east-1}"
PROFILE_ARGS=(--region "$REGION")
[ -n "${AWS_PROFILE:-}" ] && PROFILE_ARGS+=(--profile "$AWS_PROFILE")

cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "refusing to launch from a dirty tree: commit first, so the results name their code" >&2
  exit 2
fi
COMMIT="$(git rev-parse HEAD)"
SHORT="${COMMIT:0:10}"
echo "run=$RUN_NAME commit=$SHORT type=$INSTANCE_TYPE workers=$WORKERS max_hours=$MAX_HOURS"

# -- ship the code and the caches ------------------------------------------------------------
STAGE="$(mktemp -d)"
git archive --format=tar.gz -o "$STAGE/code.tar.gz" "$COMMIT"
tar -czf "$STAGE/caches.tar.gz" data/llm_cache models/semantic
aws s3 cp "$STAGE/code.tar.gz" "s3://$BUCKET/code/$SHORT/code.tar.gz" "${PROFILE_ARGS[@]}"
aws s3 cp "$STAGE/caches.tar.gz" "s3://$BUCKET/code/$SHORT/caches.tar.gz" "${PROFILE_ARGS[@]}"
rm -rf "$STAGE"

# -- budget guard (idempotent) ---------------------------------------------------------------
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text "${PROFILE_ARGS[@]}")"
if ! aws budgets describe-budget --account-id "$ACCOUNT_ID" --budget-name wta-experiments "${PROFILE_ARGS[@]}" >/dev/null 2>&1; then
  aws budgets create-budget --account-id "$ACCOUNT_ID" "${PROFILE_ARGS[@]}" \
    --budget "{\"BudgetName\":\"wta-experiments\",\"BudgetLimit\":{\"Amount\":\"$BUDGET_USD\",\"Unit\":\"USD\"},\"TimeUnit\":\"MONTHLY\",\"BudgetType\":\"COST\"}" \
    --notifications-with-subscribers "[{\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":80},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"${ALERT_EMAIL:?set ALERT_EMAIL for the budget alert}\"}]}]"
  echo "created budget wta-experiments (\$$BUDGET_USD/month, alert at 80%)"
fi

# -- the instance's job ------------------------------------------------------------------------
USER_DATA="$(cat <<EOF
#!/bin/bash
set -x
exec > /var/log/wta-bootstrap.log 2>&1
shutdown -h +$((MAX_HOURS * 60))
apt-get update && apt-get install -y docker.io awscli
systemctl start docker
mkdir -p /srv/wta /srv/runs/$RUN_NAME && cd /srv/wta
aws s3 cp s3://$BUCKET/code/$SHORT/code.tar.gz - | tar -xz
aws s3 cp s3://$BUCKET/code/$SHORT/caches.tar.gz - | tar -xz
docker build -t wta .
aws s3 sync s3://$BUCKET/runs/$RUN_NAME /srv/runs/$RUN_NAME || true
( while true; do sleep 300; aws s3 sync /srv/runs/$RUN_NAME s3://$BUCKET/runs/$RUN_NAME --exclude '*.tmp'; aws s3 sync /srv/wta/data/llm_cache s3://$BUCKET/runs/$RUN_NAME/llm_misses --exclude '*' --include 'misses.*'; done ) &
docker run --rm --shm-size=2g -v /srv/runs:/app/runs \
  -v /srv/wta/data/llm_cache:/app/data/llm_cache -v /srv/wta/models/semantic:/app/models/semantic wta \
  python scripts/run_transfer.py --out-dir runs/$RUN_NAME --workers $WORKERS --llm-offline $EXTRA_ARGS \
  > /srv/runs/$RUN_NAME/runner.log 2>&1
cp /var/log/wta-bootstrap.log /srv/runs/$RUN_NAME/bootstrap.log
aws s3 sync /srv/runs/$RUN_NAME s3://$BUCKET/runs/$RUN_NAME --exclude '*.tmp'
aws s3 sync /srv/wta/data/llm_cache s3://$BUCKET/runs/$RUN_NAME/llm_misses --exclude '*' --include 'misses.*'
shutdown -h now
EOF
)"

AMI="$(aws ssm get-parameters "${PROFILE_ARGS[@]}" \
  --names /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query 'Parameters[0].Value' --output text)"

INSTANCE_ID="$(aws ec2 run-instances "${PROFILE_ARGS[@]}" --image-id "$AMI" \
  --instance-type "$INSTANCE_TYPE" \
  --instance-market-options '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"one-time","InstanceInterruptionBehavior":"terminate"}}' \
  --instance-initiated-shutdown-behavior terminate \
  --iam-instance-profile "Name=${INSTANCE_PROFILE:-wta-worker-profile}" \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":40,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=wta-$RUN_NAME},{Key=project,Value=wta},{Key=commit,Value=$SHORT}]" \
  --user-data "$USER_DATA" --query 'Instances[0].InstanceId' --output text)"

echo "launched $INSTANCE_ID; it terminates itself when the experiment finishes or after $MAX_HOURS h."
echo "progress: aws s3 cp s3://$BUCKET/runs/$RUN_NAME/runner.log - ${PROFILE_ARGS[*]} | tail"
echo "results:  aws s3 sync s3://$BUCKET/runs/$RUN_NAME runs/$RUN_NAME ${PROFILE_ARGS[*]}"
echo "stop now: aws ec2 terminate-instances --instance-ids $INSTANCE_ID ${PROFILE_ARGS[*]}"
