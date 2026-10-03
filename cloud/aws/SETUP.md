# AWS setup for the experiment workers

One-time setup, about 15 minutes. You do it in your own AWS account; the access key you
create stays on your machine in the AWS CLI's credential store and is never pasted into a
chat, a file in this repository, or a commit. The agent working in this repository runs the
`aws` CLI with `--profile wta`, and can do only what the policy below allows:

- launch the listed instance types (c7i.xlarge/2xlarge/4xlarge, g4dn.2xlarge/4xlarge),
  spot only, and stop/terminate only instances tagged `project=wta`;
- read and write one S3 bucket;
- create and read a cost budget.

It cannot create users, change IAM, touch other buckets, or launch any other instance type.
Root credentials are never used: the launcher refuses them.

All commands are PowerShell. Replace `wta-experiments-<yourname>` with a globally unique
bucket name. This project's bucket and region are recorded in `cloud/aws/config.env`
(`ap-south-1`, Mumbai); the commands below use that region.

## 1. Install the AWS CLI

```powershell
winget install -e --id Amazon.AWSCLI
# close and reopen the terminal, then:
aws --version
```

## 2. Create the IAM user and its access key (AWS console)

1. Sign in to the AWS console with your root or admin account.
2. IAM → Users → **Create user** → name `wta-operator` → no console access → Next.
3. Attach permissions: **Create policy** → JSON → paste `cloud/aws/operator-policy.json`
   after replacing both `REPLACE_WITH_BUCKET` with your bucket name → name it
   `wta-operator-policy` → create → attach it to `wta-operator`.
4. Open the user → Security credentials → **Create access key** → "Command Line Interface"
   → copy the key id and secret (shown once).

## 3. Configure the CLI profile on this machine

```powershell
aws configure --profile wta
#   AWS Access Key ID:     <paste>
#   AWS Secret Access Key: <paste>
#   Default region name:   ap-south-1
#   Default output format: json
aws sts get-caller-identity --profile wta     # must print the wta-operator ARN
```

## 4. Create the bucket and the worker role (CLI, with your admin credentials)

These two steps need IAM rights the operator user deliberately does not have, so run them
with your own admin profile (or in CloudShell in the console).

```powershell
$BUCKET = "wta-experiments-<yourname>"
aws s3 mb "s3://$BUCKET" --region ap-south-1

# replace REPLACE_WITH_BUCKET in cloud/aws/worker-role-policy.json first
aws iam create-role --role-name wta-worker-role --assume-role-policy-document file://cloud/aws/worker-role-trust.json
aws iam put-role-policy --role-name wta-worker-role --policy-name wta-worker-s3 --policy-document file://cloud/aws/worker-role-policy.json
aws iam create-instance-profile --instance-profile-name wta-worker-profile
aws iam add-role-to-instance-profile --instance-profile-name wta-worker-profile --role-name wta-worker-role
```

## 5. Check the spot vCPU quotas

New accounts often have small limits, and a new account's GPU limit is usually 0.

```powershell
aws service-quotas get-service-quota --service-code ec2 --quota-code L-34B43A08 --region ap-south-1 --profile wta --query "Quota.Value"
aws service-quotas get-service-quota --service-code ec2 --quota-code L-3819A6DF --region ap-south-1 --profile wta --query "Quota.Value"
```

- `L-34B43A08`, "All Standard (A, C, D, H, I, M, R, T, Z) Spot Instance Requests": the CPU
  instance needs 8 (c7i.2xlarge) or 16 (c7i.4xlarge).
- `L-3819A6DF`, "All G and VT Spot Instance Requests": the GPU instance needs 16
  (g4dn.4xlarge).

Request increases in the console (Service Quotas → Amazon EC2 → the quota). Approval is
often quick for CPU quotas and can take a day or two for GPU quotas.

## 6. Tell the agent

Send three things: that the profile `wta` is configured, the **bucket name**, and an
**email address** for the budget alert. Nothing else is needed — not the keys. After the
operator profile works, delete (or at least deactivate) any root access keys and enable MFA
on the root user.

## What the agent will do with it, and what it will cost

1. Create an AWS Budget of **$20** with email alerts at 50%, 80% and 100% (forecast).
2. Upload the code at a fixed, committed revision (a `git archive` tarball; the launcher
   refuses a dirty tree) and the LLM judgment cache to the bucket. Nothing is pushed to
   GitHub.
3. Launch two independent spot instances with `cloud/aws/launch_experiment.sh`:
   - **GPU** (`g4dn.4xlarge`, 16 vCPU, one T4; about $0.40/h spot in ap-south-1): the arms
     with the dynamic LLM reward and their pretraining. The pinned model runs in Ollama on
     the T4, so states the cache has not seen are judged live; the launcher stops the
     instance if the model digest differs from the pinned one.
   - **CPU** (`c7i.2xlarge`, `NEEDS_LLM=0`): the arms without an LLM term (random, both
     searches, both bandits, DQN without LLM). No model is installed.
   Each builds the Docker image, runs its jobs with parallel browser workers, applies the
   pre-registered top-up rule to its own primary pair (`--top-up`), syncs results to S3
   every 5 minutes, and **terminates itself** when done, after 45 minutes with no log
   output, or at a hard cap (14 h), whichever comes first. The root volume is deleted
   with the instance. A relaunch with the same run name resumes.
4. Download the results from S3 and analyse them locally (`scripts/analyze_transfer.py`).

Estimated cost of the full experiment: **about $5-7** in total, well under the $20 cap.
No NAT gateway, load balancer, Elastic IP or other persistent infrastructure is created;
the only standing resources are the S3 bucket and the IAM user and role.

The agent asks before the first launch. To stop everything at any time:

```powershell
aws ec2 describe-instances --profile wta --filters Name=tag:project,Values=wta Name=instance-state-name,Values=running --query "Reservations[].Instances[].InstanceId"
aws ec2 terminate-instances --profile wta --instance-ids <id>
```
