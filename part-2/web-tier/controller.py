#!/usr/bin/env python3
import boto3, time

# ---------------- Configuration ----------------
REGION = "us-east-1"
ASU_ID = "1237472342"
AMI_ID = "ami-0e3e2a5d0130d1372"
KEY_NAME = "web-instance-key"
SECURITY_GROUP_ID = "sg-081227ea4e9d8bf2a"
INSTANCE_TYPE = "t3.micro"
IAM_ROLE_NAME = "AppTierRole"
MAX_INSTANCES = 15

# ---------------- AWS Clients ------------------
ec2 = boto3.resource("ec2", region_name=REGION)
sqs = boto3.client("sqs", region_name=REGION)
req_url = sqs.get_queue_url(QueueName=f"{ASU_ID}-req-queue")["QueueUrl"]

# ---------------- Helper Functions -------------
def get_instances(state):
    """Return all instances in a given state that belong to this project."""
    return list(ec2.instances.filter(
        Filters=[
            {"Name": "instance-state-name", "Values": [state]},
            {"Name": "tag:Project", "Values": ["CSE546"]},
            {"Name": "tag:Name", "Values": ["app-tier-instance-*"]}
        ]
    ))

def get_running():
    """Return instances that are running or starting up (pending)."""
    return get_instances("running") + get_instances("pending")

def get_stopped():
    """Return stopped instances ready to start."""
    return get_instances("stopped")

def start_instances(instances):
    """Start the given instances immediately."""
    ids = [i.id for i in instances]
    if ids:
        print(f"▶️ Starting {len(ids)} instance(s): {ids}")
        ec2.meta.client.start_instances(InstanceIds=ids)

def stop_instances(instances):
    """Send stop command without waiting (fast scale-in)."""
    ids = [i.id for i in instances]
    if ids:
        print(f"🛑 Stopping {len(ids)} instance(s): {ids}")
        ec2.meta.client.stop_instances(InstanceIds=ids)
        print("✅ Stop signal sent — continuing loop (non-blocking).")

# ---------------- Main Loop --------------------
print("✅ Controller started — monitoring SQS for autoscaling...")

idle_start = None
while True:
    try:
        # ---- check queue depth ----
        attrs = sqs.get_queue_attributes(
            QueueUrl=req_url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
            ],
        )["Attributes"]

        visible = int(attrs["ApproximateNumberOfMessages"])
        inflight = int(attrs["ApproximateNumberOfMessagesNotVisible"])
        running = get_running()
        stopped = get_stopped()
        count = len(running)
        print(f"[Monitor] Queue={visible}+{inflight} inflight, Running={count}")

        desired = min(MAX_INSTANCES, max(visible, inflight))

        # ---------- Scale Out ----------
        if desired > count:
            to_start = min(desired - count, len(stopped))
            if to_start > 0:
                start_instances(stopped[:to_start])
            idle_start = None

        # ---------- Scale In ----------
        elif visible == 0 and inflight == 0:
            if idle_start is None:
                idle_start = time.time()
            elif time.time() - idle_start > 0.5 and count > 0:
                # idle for just >0.5s → scale in fast
                print("🕒 Idle >0.5s — scaling in now...")
                stop_instances(running)
                idle_start = None
        else:
            idle_start = None  # reset if new messages appear

        time.sleep(0.5)  # faster reaction time
    except Exception as e:
        print("❌ Controller error:", e)
        time.sleep(2)
