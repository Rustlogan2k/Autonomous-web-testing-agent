#!/usr/bin/env bash
# Run part of the external comparison (hybrid.external) on ONE EC2 spot instance.
#
#   cloud/aws/launch_external.sh --dry-run <run_name> <plan>   # validates, launches nothing
#   cloud/aws/launch_external.sh <run_name> <plan>             # launches
#
# <plan> is one or more "apps:arms:seeds" groups separated by ";", each list comma-separated,
# run one after another on the instance into the same output directory, e.g.
#   "phoenix:hybrid_final,hybrid_curiosity,random:0,1;retroboard:hybrid_final:2"
#
# Same safety design as launch_experiment.sh: restricted profile `wta` (refuses root), clean
# tree only, spot only, the instance terminates itself (job end, idle watchdog, hard cap), the
# root volume is deleted with it, results sync to S3 every 5 minutes. The runner starts the
# subject applications' containers through the host's Docker (socket and CLI mounted into the
# project image), on the host network, exactly as on the development machine.

set -euo pipefail
export MSYS_NO_PATHCONV=1

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then DRY_RUN=1; shift; fi
RUN_NAME="${1:?usage: launch_external.sh [--dry-run] <run_name> <plan>}"
PLAN="${2:?usage: launch_external.sh [--dry-run] <run_name> <plan>}"

cd "$(git rev-parse --show-toplevel)"
# shellcheck disable=SC1091
source cloud/aws/config.env
AWS="${AWS_CLI:-aws}"
PROFILE="${AWS_PROFILE:-wta}"
INSTANCE_TYPE="${INSTANCE_TYPE:-m7i-flex.large}"
MAX_HOURS="${MAX_HOURS:-12}"
IDLE_MINUTES="${IDLE_MINUTES:-100}"
VOLUME_GB="${VOLUME_GB:-60}"
TIME_BUDGET="${TIME_BUDGET:-3600}"
A=(--profile "$PROFILE" --region "$REGION")

CALLER="$("$AWS" sts get-caller-identity --query Arn --output text "${A[@]}")"
case "$CALLER" in
  *":root") echo "refusing to run as the root user; use the wta-operator profile" >&2; exit 2 ;;
esac
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "refusing to launch from a dirty tree: commit first, so the results name their code" >&2
  exit 2
fi
COMMIT="$(git rev-parse HEAD)"; SHORT="${COMMIT:0:10}"

# The plan as runner invocations and the application images it needs.
COMMANDS=""
IMAGES=""
IFS=';' read -ra PLAN_GROUPS <<< "$PLAN"
for GROUP in "${PLAN_GROUPS[@]}"; do
  IFS=':' read -r G_APPS G_ARMS G_SEEDS <<< "$GROUP"
  [ -n "$G_APPS" ] && [ -n "$G_ARMS" ] && [ -n "$G_SEEDS" ] || { echo "bad plan group: $GROUP" >&2; exit 2; }
  COMMANDS+="python -m web_testing_agent.hybrid.external --out-dir runs/$RUN_NAME"
  COMMANDS+=" --apps-file configs/external_apps.json --apps ${G_APPS//,/ } --arms ${G_ARMS//,/ }"
  COMMANDS+=" --seeds ${G_SEEDS//,/ } --workers 1 --time-budget $TIME_BUDGET; "
  for APP in ${G_APPS//,/ }; do
    IMAGE="$(python -c "import json,sys; print(json.load(open('configs/external_apps.json'))[sys.argv[1]]['up'][-2])" "$APP")"
    case " $IMAGES " in *" $IMAGE "*) ;; *) IMAGES+="$IMAGE " ;; esac
  done
done
echo "caller=wta-operator run=$RUN_NAME commit=$SHORT type=$INSTANCE_TYPE max_hours=$MAX_HOURS"
echo "images: $IMAGES"
echo "commands: $COMMANDS"

AMI="$("$AWS" ssm get-parameters "${A[@]}" \
  --names /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id \
  --query 'Parameters[0].Value' --output text)"
[ "$AMI" != "None" ] && [ -n "$AMI" ] || { echo "AMI lookup failed" >&2; exit 2; }

USER_DATA="$(cat <<EOF
#!/bin/bash
set -x
exec > /var/log/wta-bootstrap.log 2>&1
shutdown -h +$((MAX_HOURS * 60))
RUN=/srv/runs/$RUN_NAME
mkdir -p /srv/wta \$RUN && cd /srv/wta
sync_out() {
  aws s3 sync \$RUN s3://$BUCKET/runs/$RUN_NAME --exclude '*.tmp' --only-show-errors
  cp /var/log/wta-bootstrap.log \$RUN/bootstrap.log 2>/dev/null || true
}
aws s3 cp s3://$BUCKET/code/$SHORT/code.tar.gz - | tar -xz
aws s3 sync s3://$BUCKET/runs/$RUN_NAME \$RUN --only-show-errors || true
for IMAGE in $IMAGES; do docker pull -q \$IMAGE || { echo "PULL FAILED \$IMAGE"; sync_out; shutdown -h now; }; done
docker build -t wta . || { echo "DOCKER BUILD FAILED"; sync_out; shutdown -h now; }
( while true; do sleep 300; sync_out; done ) &
# Idle watchdog: the runner logs every episode; silence for IDLE_MINUTES means a hang.
( sleep 1800; while true; do sleep 300;
    if [ -z "\$(find \$RUN -name runner.log -newermt '-$IDLE_MINUTES minutes' 2>/dev/null | head -1)" ]; then
      echo "IDLE FOR $IDLE_MINUTES MINUTES"; sync_out; shutdown -h now; fi; done ) &
docker run --rm --network host --shm-size=2g -e WTA_GIT_COMMIT=$COMMIT \
  -v /var/run/docker.sock:/var/run/docker.sock -v \$(command -v docker):/usr/bin/docker:ro \
  -v /srv/runs:/app/runs wta bash -c "$COMMANDS" > \$RUN/runner.log 2>&1
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
  "$AWS" s3 ls "s3://$BUCKET/" "${A[@]}" >/dev/null && echo "bucket: accessible"
  if OUT="$("$AWS" "${LAUNCH[@]}" --dry-run 2>&1)"; then echo "$OUT"; else
    case "$OUT" in *DryRunOperation*) echo "run-instances: permitted (EC2 DryRun, nothing launched)" ;;
                   *) echo "run-instances would FAIL: $OUT" >&2; exit 3 ;; esac; fi
  exit 0
fi

# The code tarball for this commit (shared by every instance launched from it).
if ! "$AWS" s3 ls "s3://$BUCKET/code/$SHORT/code.tar.gz" "${A[@]}" >/dev/null 2>&1; then
  STAGE="runs/.launch-stage-$$"
  mkdir -p "$STAGE"
  git archive --format=tar.gz -o "$STAGE/code.tar.gz" "$COMMIT"
  "$AWS" s3 cp "$STAGE/code.tar.gz" "s3://$BUCKET/code/$SHORT/code.tar.gz" "${A[@]}" --only-show-errors
  rm -rf "$STAGE"
fi

INSTANCE_ID="$("$AWS" "${LAUNCH[@]}")"
echo "launched $INSTANCE_ID; it terminates itself when done, after $IDLE_MINUTES idle minutes, or after $MAX_HOURS h."
echo "progress: $AWS s3 cp s3://$BUCKET/runs/$RUN_NAME/runner.log - --profile $PROFILE | tail"
echo "results:  $AWS s3 sync s3://$BUCKET/runs/$RUN_NAME runs/$RUN_NAME --profile $PROFILE"
echo "stop now: $AWS ec2 terminate-instances --instance-ids $INSTANCE_ID --profile $PROFILE --region $REGION"
