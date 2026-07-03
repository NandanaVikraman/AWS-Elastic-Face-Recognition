#!/usr/bin/env python3
import boto3, json, os, time, threading
from flask import Flask, request, Response

# ---------------------------- Configuration ----------------------------
REGION = "us-east-1"
ASU_ID = "1237472342"
IN_BUCKET = f"{ASU_ID}-in-bucket"
OUT_BUCKET = f"{ASU_ID}-out-bucket"
REQ_QUEUE = f"{ASU_ID}-req-queue"
RESP_QUEUE = f"{ASU_ID}-resp-queue"

# ---------------------------- AWS Clients ------------------------------
s3 = boto3.client("s3", region_name=REGION)
sqs = boto3.client("sqs", region_name=REGION)
req_url = sqs.get_queue_url(QueueName=REQ_QUEUE)["QueueUrl"]
resp_url = sqs.get_queue_url(QueueName=RESP_QUEUE)["QueueUrl"]

app = Flask(__name__)

pending = {}
results = {}
lock = threading.Lock()

# ---------------------------- Response Consumer ------------------------
def resp_consumer():
    print("📥 Response consumer thread started")
    while True:
        try:
            resp = sqs.receive_message(
                QueueUrl=resp_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=10,
                VisibilityTimeout=30
            )
            for m in resp.get("Messages", []):
                body_raw = m["Body"]
                fname = pred = None

                # Try JSON first
                try:
                    js = json.loads(body_raw)
                    if isinstance(js, dict) and "file_name" in js and "prediction" in js:
                        fname = os.path.basename(str(js["file_name"]).strip())
                        pred = str(js["prediction"]).strip()
                except json.JSONDecodeError:
                    pass

                # Fallback: plain text "filename:prediction"
                if fname is None and not body_raw.lstrip().startswith("{"):
                    if ":" in body_raw:
                        left, right = body_raw.split(":", 1)
                        fname = os.path.basename(left.strip())
                        pred = right.strip()

                if fname and pred:
                    with lock:
                        results[fname] = pred
                        if fname in pending:
                            pending[fname].set()
                    print(f"✅ Received response for {fname}: {pred}")
                else:
                    print(f"ℹ️ Ignoring malformed or unrelated message: {body_raw[:120]}")

                sqs.delete_message(QueueUrl=resp_url, ReceiptHandle=m["ReceiptHandle"])
        except Exception as e:
            print("❌ Response consumer error:", e)
            time.sleep(2)

threading.Thread(target=resp_consumer, daemon=True).start()

# ---------------------------- Request Handler --------------------------
@app.route("/", methods=["POST"])
def handle_request():
    if "inputFile" not in request.files:
        return Response("Missing inputFile", status=400)
    file = request.files["inputFile"]
    filename = os.path.basename(file.filename)
    if not filename:
        return Response("Invalid filename", status=400)

    try:
        s3.put_object(Bucket=IN_BUCKET, Key=filename, Body=file.read())
        print(f"📤 Uploaded {filename} to {IN_BUCKET}")
    except Exception as e:
        print("❌ S3 upload error:", e)
        return Response("S3 upload failed", status=500)

    try:
        sqs.send_message(QueueUrl=req_url, MessageBody=filename)
        print(f"📩 Enqueued request: {filename}")
    except Exception as e:
        print("❌ SQS send error:", e)
        return Response("Failed to enqueue", status=500)

    evt = threading.Event()
    prediction = None
    with lock:
        if filename in results:
            prediction = results.pop(filename)
        else:
            pending[filename] = evt

    deadline = time.time() + 180
    while prediction is None and time.time() < deadline:
        evt.wait(timeout=2)
        with lock:
            if filename in results:
                prediction = results.pop(filename)
                pending.pop(filename, None)
                break

    with lock:
        pending.pop(filename, None)

    if prediction is None:
        print(f"⚠️ Timeout waiting for prediction for {filename}")
        return Response("Timeout", status=504)

    result = f"{os.path.splitext(filename)[0]}:{prediction}"
    print(f"✅ Returning result: {result}")
    return Response(result, mimetype="text/plain", status=200)

# ---------------------------- Main ----------------------------
if __name__ == "__main__":
    print("✅ Web server started on port 8000 — ready to receive requests")
    app.run(host="0.0.0.0", port=8000, threaded=True)
