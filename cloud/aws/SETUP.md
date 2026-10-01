# AWS setup for the experiment workers

One-time setup, about 15 minutes. You do it in your own AWS account; the access key you
create stays on your machine in the AWS CLI's credential store and is never pasted into a
chat, a file in this repository, or a commit. The agent working in this repository runs the
`aws` CLI with `--profile wta`, and can do only what the policy below allows:

- launch the listed CPU instance types and stop/terminate only instances tagged `project=wta`;
- read and write one S3 bucket;
- create and read a cost budget.

It cannot create users, change IAM, touch other buckets, or launch GPU or other large
instance types.

All commands are PowerShell. Replace `wta-experiments-<yourname>` with a globally unique
bucket name; region is `us-east-1` throughout (cheapest spot capacity for these types).

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
#   Default region name:   us-east-1
#   Default output format: json
aws sts get-caller-identity --profile wta     # must print the wta-operator ARN
```

## 4. Create the bucket and the worker role (CLI, with your admin credentials)

These two steps need IAM rights the operator user deliberately does not have, so run them
with your own admin profile (or in CloudShell in the console).

```powershell
$BUCKET = "wta-experiments-<yourname>"
aws s3 mb "s3://$BUCKET" --region us-east-1

# replace REPLACE_WITH_BUCKET in cloud/aws/worker-role-policy.json first
aws iam create-role --role-name wta-worker-role --assume-role-policy-document file://cloud/aws/worker-role-trust.json
aws iam put-role-policy --role-name wta-worker-role --policy-name wta-worker-s3 --policy-document file://cloud/aws/worker-role-policy.json
aws iam create-instance-profile --instance-profile-name wta-worker-profile
aws iam add-role-to-instance-profile --instance-profile-name wta-worker-profile --role-name wta-worker-role
```

## 5. Check the spot vCPU quota

New accounts often have a small limit. The experiment uses one 16- or 32-vCPU instance.

```powershell
aws service-quotas get-service-quota --service-code ec2 --quota-code L-34B43A08 --region us-east-1 --profile wta --query "Quota.Value"
```

`L-34B43A08` is "All Standard (A, C, D, H, I, M, R, T, Z) Spot Instance Requests" (vCPUs).
If it is below 32, request an increase to 32 in the console (Service Quotas → Amazon EC2 →
that quota). Approval is usually quick but can take a day.

## 6. Tell the agent

Send three things: that the profile `wta` is configured, the **bucket name**, and an
**email address** for the budget alert. Nothing else is needed — not the keys.

## What the agent will do with it, and what it will cost

1. Upload the code at a fixed commit (as a `git archive` tarball) and the LLM judgment
   cache to the bucket. Nothing is pushed to GitHub.
2. Create an AWS Budget of **$40/month** with an email alert at 80%.
3. Launch **one** spot `c7i.4xlarge` (16 vCPU, 32 GB; spot price roughly $0.25-0.35/h in
   us-east-1, on-demand $0.71/h). It builds the Docker image, runs the experiment with 12
   parallel browser workers, syncs results to S3 every 5 minutes, and **terminates itself**
   when done or after a hard cap of 10 hours, whichever comes first.
4. Download the results from S3 and analyse them locally.

Estimated cost of the full leave-one-application-out experiment: **about $3-8** on spot,
under $15 even at on-demand prices and with a second pass for extra arms. No GPU instance
is used: the experiment is browser-bound, and the language-model judgments it needs are
computed beforehand on the local GPU and shipped in the cache.

The agent asks before the first launch. To stop everything at any time:

```powershell
aws ec2 describe-instances --profile wta --filters Name=tag:project,Values=wta Name=instance-state-name,Values=running --query "Reservations[].Instances[].InstanceId"
aws ec2 terminate-instances --profile wta --instance-ids <id>
```
