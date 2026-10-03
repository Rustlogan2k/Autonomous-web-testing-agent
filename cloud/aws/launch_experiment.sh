#!/usr/bin/env bash
# Run the transfer experiment unattended on ONE EC2 spot instance, with a live LLM.
#
#   cloud/aws/launch_experiment.sh --dry-run <run_name> [run_transfer.py args...]   # validates, launches nothing
#   cloud/aws/launch_experiment.sh <run_name> [run_transfer.py args...]             # launches
#
# Reads BUCKET and REGION from cloud/aws/config.env and uses the restricted AWS CLI profile
# `wta` (AWS_PROFILE overrides). Never run with root credentials: the script refuses them.
#
# Design (see cloud/aws/SETUP.md and the experiment report for the reasoning):
# * One g4dn.4xlarge spot instance (16 vCPU, 64 GB, one T4 GPU). The browsers use the CPUs;
#   the GPU serves the dynamic LLM reward (Ollama, qwen2.5:7b-instruct, digest-pinned), so
#   the reward stays live in the cloud: cached judgments are reused, unseen states are
#   judged on demand, exactly as on the development machine.
# * The committed tree ships as a `git archive` tarball together with the warm LLM judgment
#   cache and label store. Refuses to launch from a dirty tree.
# * Three independent stops: the job's own `shutdown -h now` when it finishes; an idle
#   watchdog that shuts down if no job has written a log line for IDLE_MINUTES; and a hard
#   `shutdown -h +MAX_HOURS*60` armed at boot. Shutdown terminates the instance
#   (InstanceInitiatedShutdownBehavior=terminate); the root volume is deleted with it.
# * Results, logs, checkpoints, reward logs and discovered traces sync to S3 every 5
#   minutes and at the end. The runner is resumable: relaunching with the same run name
#   pulls finished jobs back first and continues.
# * Spot only. If spot capacity is unavailable the launch fails; it never falls back to
#   on-demand on its own.

set -euo pipefail
# Git Bash on Windows rewrites arguments that look like POSIX paths (/aws/service/...)
# into Windows paths; SSM parameter names and S3 keys must reach the CLI untouched.
export MSYS_NO_PATHCONV=1

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then DRY_RUN=1; shift; fi
RUN_NAME="${1:?usage: launch_experiment.sh [--dry-run] <run_name> [run_transfer args...]}"
shift
EXTRA_ARGS="$*"

cd "$(git rev-parse --show-toplevel)"
# shellcheck disable=SC1091
source cloud/aws/config.env
AWS="${AWS_CLI:-aws}"
PROFILE="${AWS_PROFILE:-wta}"
INSTANCE_TYPE="${INSTANCE_TYPE:-g4dn.4xlarge}"
WORKERS="${WORKERS:-12}"
MAX_HOURS="${MAX_HOURS:-14}"
# NEEDS_LLM=0 for an instance that runs only arms without the LLM reward (random, search,
# bandit, DQN without LLM): it then skips the model entirely and can be a CPU instance.
NEEDS_LLM="${NEEDS_LLM:-1}"
IDLE_MINUTES="${IDLE_MINUTES:-45}"
VOLUME_GB="${VOLUME_GB:-100}"
MODEL="qwen2.5:7b-instruct"
MODEL_DIGEST="845dbda0ea48"
A=(--profile "$PROFILE" --region "$REGION")

CALLER="$("$AWS" sts get-caller-identity --query Arn --output text "${A[@]}")"
case "$CALLER" in
  *":root") echo "refusing to run as the root user ($CALLER); use the wta-operator profile" >&2; exit 2 ;;
esac
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "refusing to launch from a dirty tree: commit first, so the results name their code" >&2
  exit 2
fi
COMMIT="$(git rev-parse HEAD)"; SHORT="${COMMIT:0:10}"
echo "caller=$CALLER run=$RUN_NAME commit=$SHORT type=$INSTANCE_TYPE workers=$WORKERS max_hours=$MAX_HOURS needs_llm=$NEEDS_LLM"

AMI="$("$AWS" ssm get-parameters "${A[@]}" \
  --names /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id \
  --query 'Parameters[0].Value' --output text)"
[ "$AMI" != "None" ] && [ -n "$AMI" ] || { echo "Deep Learning AMI lookup failed" >&2; exit 2; }
echo "ami=$AMI"

USER_DATA="$(cat <<EOF
#!/bin/bash
set -x
exec > /var/log/wta-bootstrap.log 2>&1
shutdown -h +$((MAX_HOURS * 60))
RUN=/srv/runs/$RUN_NAME
mkdir -p /srv/wta \$RUN && cd /srv/wta
sync_out() {
  aws s3 sync \$RUN s3://$BUCKET/runs/$RUN_NAME --exclude '*.tmp' --only-show-errors
  aws s3 sync /srv/wta/data/llm_cache s3://$BUCKET/runs/$RUN_NAME/llm_cache --only-show-errors
  cp /var/log/wta-bootstrap.log \$RUN/bootstrap.log 2>/dev/null || true
}
aws s3 cp s3://$BUCKET/code/$SHORT/code.tar.gz - | tar -xz
aws s3 cp s3://$BUCKET/code/$SHORT/caches.tar.gz - | tar -xz
aws s3 sync s3://$BUCKET/runs/$RUN_NAME \$RUN --exclude 'llm_cache/*' --only-show-errors || true
if [ "$NEEDS_LLM" = 1 ]; then
  # The dynamic LLM reward: Ollama on the T4, the same pinned model as development.
  curl -fsSL https://ollama.com/install.sh | sh
  systemctl enable --now ollama || (nohup ollama serve >/var/log/ollama.log 2>&1 &)
  for i in \$(seq 1 60); do curl -s http://127.0.0.1:11434/api/tags && break; sleep 2; done
  ollama pull $MODEL
  curl -s http://127.0.0.1:11434/api/tags | grep -q '"digest":"$MODEL_DIGEST' || { echo "MODEL DIGEST MISMATCH"; sync_out; shutdown -h now; }
fi
docker build -t wta . || { echo "DOCKER BUILD FAILED"; sync_out; shutdown -h now; }
( while true; do sleep 300; sync_out; done ) &
# Idle watchdog: every job writes its log continuously; silence means a hang.
( sleep 1800; while true; do sleep 300;
    if [ -z "\$(find \$RUN/logs -newermt '-$IDLE_MINUTES minutes' -type f 2>/dev/null | head -1)" ]; then
      echo "IDLE FOR $IDLE_MINUTES MINUTES"; sync_out; shutdown -h now; fi; done ) &
docker run --rm --network host --shm-size=2g \
  -e OLLAMA_HOST=http://127.0.0.1:11434 \
  -v /srv/runs:/app/runs -v /srv/wta/data/llm_cache:/app/data/llm_cache \
  -v /srv/wta/models/semantic:/app/models/semantic wta \
  python scripts/run_transfer.py --out-dir runs/$RUN_NAME --workers $WORKERS $EXTRA_ARGS \
  > \$RUN/runner.log 2>&1
echo "RUNNER EXITED WITH \$?"
sync_out
shutdown -h now
EOF
)"
bash -n <(printf '%s\n' "$USER_DATA") && echo "user-data: shell syntax ok ($(printf '%s' "$USER_DATA" | wc -c) bytes)"

LAUNCH=(ec2 run-instances "${A[@]}" --image-id "$AMI" --instance-type "$INSTANCE_TYPE"
  --instance-market-options '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"one-time","InstanceInterruptionBehavior":"terminate"}}'
  --instance-initiated-shutdown-behavior terminate
  --iam-instance-profile Name=wta-worker-profile
  --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":$VOLUME_GB,\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]"
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=wta-$RUN_NAME},{Key=project,Value=wta},{Key=commit,Value=$SHORT}]"
  --user-data "$USER_DATA" --query 'Instances[0].InstanceId' --output text)

if [ "$DRY_RUN" = 1 ]; then
  echo "-- dry run: checks only, nothing is uploaded or provisioned"
  "$AWS" s3 ls "s3://$BUCKET/" "${A[@]}" >/dev/null && echo "bucket s3://$BUCKET: accessible"
  "$AWS" budgets describe-budget --account-id "$(echo "$CALLER" | cut -d: -f5)" --budget-name wta-experiments \
    --profile "$PROFILE" --region us-east-1 --query 'Budget.BudgetLimit' --output text && echo "budget: present"
  for Q in L-34B43A08 L-3819A6DF; do
    printf 'quota %s: ' "$Q"; "$AWS" service-quotas get-service-quota --service-code ec2 --quota-code "$Q" "${A[@]}" --query Quota.Value --output text
  done
  "$AWS" ec2 describe-spot-price-history "${A[@]}" --instance-types "$INSTANCE_TYPE" --product-descriptions Linux/UNIX \
    --max-items 3 --query 'SpotPriceHistory[].[AvailabilityZone,SpotPrice]' --output text
  if OUT="$("$AWS" "${LAUNCH[@]}" --dry-run 2>&1)"; then echo "$OUT"; else
    case "$OUT" in *DryRunOperation*) echo "run-instances: permitted (EC2 DryRun, nothing launched)" ;;
                   *) echo "run-instances would FAIL: $OUT" >&2; exit 3 ;; esac; fi
  exit 0
fi

STAGE="$(mktemp -d)"
git archive --format=tar.gz -o "$STAGE/code.tar.gz" "$COMMIT"
tar -czf "$STAGE/caches.tar.gz" data/llm_cache models/semantic
"$AWS" s3 cp "$STAGE/code.tar.gz" "s3://$BUCKET/code/$SHORT/code.tar.gz" "${A[@]}" --only-show-errors
"$AWS" s3 cp "$STAGE/caches.tar.gz" "s3://$BUCKET/code/$SHORT/caches.tar.gz" "${A[@]}" --only-show-errors
rm -rf "$STAGE"

INSTANCE_ID="$("$AWS" "${LAUNCH[@]}")"
echo "launched $INSTANCE_ID; it terminates itself when done, after $IDLE_MINUTES idle minutes, or after $MAX_HOURS h."
echo "progress: $AWS s3 cp s3://$BUCKET/runs/$RUN_NAME/runner.log - --profile $PROFILE | tail"
echo "results:  $AWS s3 sync s3://$BUCKET/runs/$RUN_NAME runs/$RUN_NAME --profile $PROFILE"
echo "stop now: $AWS ec2 terminate-instances --instance-ids $INSTANCE_ID --profile $PROFILE --region $REGION"
