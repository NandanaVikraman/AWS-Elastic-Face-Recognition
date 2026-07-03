# AWS Elastic Face Recognition

An elastic, auto-scaling face-recognition web service built on raw AWS IaaS primitives (EC2, S3, SQS, SimpleDB) for CSE 546 (Cloud Computing) at Arizona State University. The project was submitted in two parts: Part I builds the web-facing tier, Part II completes the system with a real ML-backed application tier and a self-implemented auto-scaling controller.

> **Note on scope:** This repo contains the code I personally wrote and submitted for grading. It intentionally excludes: AWS credentials, the EC2 SSH key, and the face-recognition model/weights, which were provided privately to the class (copyrighted course material, not authored by me) and are required to run the app tier end-to-end. See [What's not included](#whats-not-included) below.

## Architecture

```
Client ──POST /──▶ Web Tier (EC2, Flask) ──▶ S3 (input bucket)
                         │
                         ▼
                  SQS request queue ──▶ App Tier (EC2, auto-scaled 0–15 instances)
                         ▲                        │
                         │                        ▼
                  SQS response queue  ◀── Face recognition model inference
                                                   │
                                                   ▼
                                           S3 (output bucket)
```

### Part I — Web Tier (IaaS basics)

A single Flask app on one EC2 instance that:
- Accepts an image via HTTP POST (`inputFile` field) on port 8000
- Stores it in an S3 input bucket (`<ASU ID>-in-bucket`)
- Looks up the "recognition" result from a SimpleDB domain (`<ASU ID>-simpleDB`) — a stand-in for a real model at this stage
- Returns the result as plain text: `<filename>:<prediction>`

`scripts/populate_simpledb.py` seeds that SimpleDB domain from the provided ground-truth CSV, since the web tier only performs lookups, not classification, in this part.

### Part II — Full Elastic Pipeline

The web tier is extended to hand off the actual recognition work to a scalable application tier instead of doing a static lookup:

- **Web tier** (`part-2/web-tier/server.py`) stores the image in S3, pushes the filename onto an SQS request queue, and blocks (with a timeout) waiting for the matching response on an SQS response queue, correlating requests to responses with an in-memory event/lock map so concurrent requests don't cross-talk.
- **App tier** (`part-2/app-tier/backend.py`) runs on a fleet of EC2 worker instances (from a custom AMI with the ML model pre-installed). Each worker: pulls one message off the request queue, downloads the image from S3, runs face-recognition inference via a subprocess call to the model code, writes the result to an S3 output bucket, and pushes the result onto the response queue.
- **Auto-scaling controller** (`part-2/web-tier/controller.py`) — a from-scratch scaling loop (AWS Auto Scaling Groups were disallowed by the assignment) that watches SQS queue depth and starts/stops EC2 app-tier instances accordingly: scales up toward `min(MAX_INSTANCES, pending requests)`, and scales all instances back down to 0 shortly after the queue drains.

## Why these AWS services

- **S3** holds the actual image bytes — cheap, durable object storage, well suited to large binary payloads that don't need to be queried.
- **SimpleDB** (Part I) gives fast key-value lookups by filename for the emulated recognition results, without the overhead of standing up a full database for what's essentially a static lookup table.
- **SQS** (Part II) decouples the web tier from the app tier: the web tier doesn't need to know how many workers exist or where they are, it just drops a message and waits. This is what makes the app tier scalable independently of the web tier.
- Splitting storage (S3) from the request/response signaling (SQS) means the 1KB SQS message-size cap is a non-issue — only the filename crosses the queue, never the image itself.

## AWS Setup (Part II)

The app itself doesn't provision infrastructure — it assumes the following already exists, set up once via the AWS Console/CLI in `us-east-1`:

1. **S3 buckets**: `<ASU ID>-in-bucket`, `<ASU ID>-out-bucket`
2. **SQS queues**: `<ASU ID>-req-queue` (max message size 1KB), `<ASU ID>-resp-queue`
3. **SimpleDB domain**: `<ASU ID>-simpleDB` (Part I only)
4. **IAM role** with S3 + SQS access, attached to both the web-tier and app-tier EC2 instances — the code never hardcodes AWS credentials; `boto3` picks up the instance's attached role automatically
5. **Web-tier EC2 instance** (`t3.micro`/`t2.micro`), named `web-instance`, with an Elastic IP attached and a security group allowing inbound TCP on 22 (SSH) and 8000 (HTTP)
6. **App-tier AMI**: a base EC2 instance with the face-recognition model code and weights installed (see [What's not included](#whats-not-included)), turned into a custom AMI via EC2 → Actions → Image → Create Image. `controller.py` launches/starts new app-tier instances from this AMI, tagged `Name=app-tier-instance-*` and `Project=CSE546` so it can find and manage them by tag.

Once the instances are up, run each component in the background so it survives disconnecting your SSH session — e.g. `gunicorn -b 0.0.0.0:8000 server:app` for the web tier under a process manager (systemd service or `pm2`), and `controller.py`/`backend.py` similarly kept alive as background/managed processes.

### Testing

```bash
curl -X POST -F "inputFile=@test_00.jpg" http://<web-tier-elastic-ip>:8000/
# → test_00:Paul
```

### Cleanup

```bash
# Empty an S3 bucket
aws s3 rm s3://<ASU ID>-in-bucket --recursive --region us-east-1

# Purge an SQS queue
aws sqs purge-queue --queue-url <queue-url> --region us-east-1

# Find and terminate app-tier instances by tag
aws ec2 describe-instances --filters "Name=tag:Project,Values=CSE546" --region us-east-1
aws ec2 terminate-instances --instance-ids <id1> <id2> --region us-east-1
```

## Design notes

- The web tier uses a background thread continuously draining the SQS response queue into a shared results dict, and per-request threading events to wake up the specific HTTP handler waiting on that filename — this avoids polling per-request and keeps response latency low under concurrent load.
- The controller treats "stopped" EC2 instances as a warm pool: workers are pre-created but only started/stopped (not launched/terminated) to reduce scale-out latency.
- Naming, bucket/queue names, and regions all follow the fixed conventions the assignment required (`<ASU ID>-in-bucket`, `<ASU ID>-req-queue`, etc.), which is why they're hardcoded rather than configurable — this was a constraint of the grading autograder, not a design choice.

## Repository Layout

```
part-1/
  web-tier/server.py       # Part I: SimpleDB-lookup based web tier
part-2/
  web-tier/server.py       # Part II: SQS-based web tier
  web-tier/controller.py   # Part II: auto-scaling controller
  app-tier/backend.py      # Part II: app-tier worker (model inference)
scripts/
  populate_simpledb.py     # Seeds the SimpleDB lookup table for Part I
requirements.txt
```

## Running locally (partial)

The web tier alone can be run locally against real AWS resources (S3/SQS/SimpleDB), provided you have your own AWS credentials configured and the buckets/queues/domain already created:

```bash
pip install -r requirements.txt
python part-1/web-tier/server.py    # Part I
# or
python part-2/web-tier/server.py    # Part II (also start controller.py and backend.py workers)
```

The app tier (`backend.py`) additionally requires the face-recognition model code at `/home/ubuntu/model/face_recognition.py` on the host — see [What's not included](#whats-not-included).

## What's not included

By design, this repo cannot be run fully end-to-end from a fresh clone, because part of the original assignment intentionally lives outside the submitted code:

- **AWS credentials and the EC2 SSH key** — obviously never committed.
- **The face-recognition model code and pretrained weights** (`face_recognition.py`, model weights) — provided to the class privately by the course staff (copyright ASU VISA Lab) for use on a custom AMI, and explicitly excluded from what students were allowed to submit. It's not mine to redistribute here. The app tier calls out to this file via subprocess at inference time.
- **AWS infrastructure provisioning** (S3 buckets, SQS queues, SimpleDB domain, security groups, IAM roles, the custom AMI) — set up manually via the AWS Console/CLI per the assignment, not captured in any script.

## Tech Stack

Python · Flask · boto3 · AWS EC2, S3, SQS, SimpleDB · Gunicorn

## Course Context

CSE 546 — Cloud Computing, Arizona State University. Project 1 (Parts I & II), built and submitted individually.
