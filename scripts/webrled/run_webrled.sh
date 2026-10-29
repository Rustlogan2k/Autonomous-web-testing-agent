#!/usr/bin/env bash
# Run WebRLED's released artifact (Gu et al., 2025) once on one benchmark application, as its
# README describes, and keep what is needed to score it with our own coverage counter.
#
#   APP=splittypie WEBRLED_DIR=/opt/webrled ISTANBUL_DIR=/opt/express-istanbul \
#   OUT_DIR=out bash run_webrled.sh
#
# Environment:
#   APP            one of dimeshift pagekit splittypie phoenix retroboard petclinic
#   WEBRLED_DIR    the unzipped webrled/ folder (written to: WebRLED logs under src/run/)
#   ISTANBUL_DIR   the unzipped express-istanbul/ folder (node_modules included)
#   OUT_DIR        where the outputs go
#   TIME_LIMIT     seconds of testing; 3600 as released (anything else patches settings.py
#                  and is recorded in meta.json)
#   URL, DOMAIN    override WebRLED's built-in address for APP (its defaults are localhost:400x)
#   SKIP_INSTALL   1 to skip the Python dependency install
#   SERVER_COVERAGE  for the second (held-out) benchmark: the application image's own coverage
#                  server (http://localhost:6969/coverage). express-istanbul is then not
#                  started (the image owns port 6969) and coverage.json is that server's JSON,
#                  read when the run ends; ISTANBUL_DIR is not needed.
#
# Changes from the released artifact, all recorded in meta.json:
#   - torch/torchvision are the CPU builds of the pinned versions and the CUDA-only packages
#     (nvidia-*, triton) are not installed: the released code places every model on the CPU
#     (drl/learner.py and webenv/action_discriminator.py hard-code torch.device("cpu")).
#   - the browser it launches headed runs under a virtual display (xvfb-run).
#   - the folder /home/gzy/Desktop exists (see the express-istanbul step below).
# Outputs: coverage.json (express-istanbul's merged /coverage/object), coverage.html (its
# report, the figure WebRLED itself logs), webrled.log, meta.json.
set -euo pipefail

: "${APP:?}" "${WEBRLED_DIR:?}" "${OUT_DIR:?}"
[ -n "${SERVER_COVERAGE:-}" ] || : "${ISTANBUL_DIR:?}"
TIME_LIMIT="${TIME_LIMIT:-3600}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

if [ "${SKIP_INSTALL:-0}" != "1" ]; then
  grep -v -E '^(nvidia-|triton==|torch==|torchvision==)' "$WEBRLED_DIR/requirements.txt" \
    > "$OUT_DIR/requirements-cpu.txt"
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet torch==2.6.0+cpu torchvision==0.21.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu
  python -m pip install --quiet -r "$OUT_DIR/requirements-cpu.txt"
  python -m playwright install --with-deps chromium
fi

if [ "$TIME_LIMIT" != "3600" ]; then
  sed -i -E "s/^time_limit = [0-9]+/time_limit = $TIME_LIMIT/" "$WEBRLED_DIR/src/settings.py"
fi
grep -E "^time_limit = " "$WEBRLED_DIR/src/settings.py"

# express-istanbul: WebRLED posts window.__coverage__ here after every step and reads the
# merged report back. The released copy of istanbul-middleware (lib/handlers.js, POST
# /client) first writes each post to /home/gzy/Desktop/nyc_output.txt, a path on the authors'
# machine; without that folder every post fails and WebRLED resets the page forever. The
# folder is created rather than the code edited.
if [ -z "${SERVER_COVERAGE:-}" ]; then
  GZY=/home/gzy/Desktop
  mkdir -p "$GZY" 2>/dev/null || { sudo mkdir -p "$GZY" && sudo chmod -R a+rwx /home/gzy; }
  (cd "$ISTANBUL_DIR" && node index.js > "$OUT_DIR/istanbul.log" 2>&1 &)
  for _ in $(seq 1 60); do
    curl -sf -o /dev/null http://localhost:6969/coverage && break
    sleep 1
  done
fi
curl -sf -o /dev/null http://localhost:6969/coverage

args=(--appname "$APP" --coverage)
if [ -n "${URL:-}" ]; then args+=(--url "$URL" --domain "${DOMAIN:-$URL}"); fi

started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
status=0
# The released loop stops at time_limit or after n_learner_cycle learner updates; the outer
# timeout only guards against a hang (model loading and the last episode come on top).
(cd "$WEBRLED_DIR" && PYTHONPATH="$WEBRLED_DIR" timeout --kill-after=60 $((TIME_LIMIT + 1200)) \
  xvfb-run -a python src/main.py "${args[@]}") || status=$?
finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)

if [ -n "${SERVER_COVERAGE:-}" ]; then
  curl -sf "$SERVER_COVERAGE" -o "$OUT_DIR/coverage.json" || echo "{}" > "$OUT_DIR/coverage.json"
else
  curl -sf http://localhost:6969/coverage/object -o "$OUT_DIR/coverage.json" || echo "{}" > "$OUT_DIR/coverage.json"
  curl -sf http://localhost:6969/coverage -o "$OUT_DIR/coverage.html" || true
fi
find "$WEBRLED_DIR/src/run" -name "$APP.log" -exec cp {} "$OUT_DIR/webrled.log" \; || true

cat > "$OUT_DIR/meta.json" <<EOF
{"tool": "webrled", "app": "$APP", "time_limit_s": $TIME_LIMIT, "exit_status": $status,
 "server_coverage": "${SERVER_COVERAGE:-}",
 "started": "$started", "finished": "$finished",
 "changes": ["torch 2.6.0+cpu, torchvision 0.21.0+cpu; nvidia-* and triton not installed (code is CPU-only)",
             "xvfb-run for the headed browser",
             "created /home/gzy/Desktop, the folder express-istanbul's handler writes each coverage post to"]}
EOF
echo "WebRLED $APP finished with status $status"
