#!/usr/bin/env python3
import boto3, os, json, time, subprocess, re, torch

REGION = "us-east-1"
ASU_ID = "1237472342"
REQ_QUEUE = f"{ASU_ID}-req-queue"
RESP_QUEUE = f"{ASU_ID}-resp-queue"
IN_BUCKET = f"{ASU_ID}-in-bucket"
OUT_BUCKET = f"{ASU_ID}-out-bucket"

sqs = boto3.client("sqs", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
req_url = sqs.get_queue_url(QueueName=REQ_QUEUE)["QueueUrl"]
resp_url = sqs.get_queue_url(QueueName=RESP_QUEUE)["QueueUrl"]

print("✅ App-tier worker started — waiting for messages...")

NAME_LINE = re.compile(r"^[A-Za-z][A-Za-z \-']*$")

def extract_prediction(stdout_text: str) -> str:
    if not stdout_text:
        return "UNKNOWN"
    try:
        js = json.loads(stdout_text.strip())
        if isinstance(js, dict):
            return js.get("prediction") or js.get("name") or "UNKNOWN"
    except Exception:
        pass

    for l in [l.strip() for l in stdout_text.splitlines() if l.strip()]:
        if NAME_LINE.match(l):
            return l
    return "UNKNOWN"

while True:
    try:
        resp = sqs.receive_message(
            QueueUrl=req_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=10,
            VisibilityTimeout=180
        )
        msgs = resp.get("Messages", [])
        if not msgs:
            time.sleep(1)
            continue

        msg = msgs[0]
        receipt = msg["ReceiptHandle"]
        filename = msg["Body"].strip()
        base = os.path.splitext(filename)[0]
        local = f"/tmp/{filename}"

        try:
            s3.download_file(IN_BUCKET, filename, local)
            print(f"📥 Downloaded {filename}")
        except Exception as e:
            print(f"❌ Download failed for {filename}: {e}")
            continue

        prediction = "UNKNOWN"
        try:
            result = subprocess.run(
                ["python3", "face_recognition.py", local],
                cwd="/home/ubuntu/model",
                capture_output=True,
                text=True,
                timeout=120
            )
            stdout = (result.stdout or "").strip()
            prediction = extract_prediction(stdout)
            print(f"✅ Prediction for {filename}: {prediction}")
        except Exception as e:
            print(f"❌ Model error for {filename}: {e}")

        try:
            s3.put_object(Bucket=OUT_BUCKET, Key=base, Body=prediction.encode("utf-8"))
            print(f"📤 Uploaded result for {base} → {prediction}")
        except Exception as e:
            print(f"⚠️ Could not upload result for {base}: {e}")

        try:
            body = json.dumps({"file_name": filename, "prediction": prediction})
            sqs.send_message(QueueUrl=resp_url, MessageBody=body)
            print(f"📩 Sent response for {filename} → {prediction}")
        except Exception as e:
            print(f"❌ Failed to send response for {filename}: {e}")

        try:
            sqs.delete_message(QueueUrl=req_url, ReceiptHandle=receipt)
            print(f"✅ Processed {filename}")
        except Exception as e:
            print(f"⚠️ Failed to delete request message for {filename}: {e}")

        try:
            os.remove(local)
        except Exception:
            pass

    except Exception as e:
        print("❌ Worker loop error:", e)
        time.sleep(2)
