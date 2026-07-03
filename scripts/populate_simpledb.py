import csv
import boto3

# Update with your ASU ID
ASU_ID = "1237472342"
DOMAIN_NAME = f"{ASU_ID}-simpleDB"

# Initialize SimpleDB client
client = boto3.client("sdb", region_name="us-east-1")

# Path to your CSV
csv_file = "dataset/classification_face_images_1000.csv"

with open(csv_file, "r") as f:
    reader = csv.reader(f)
    next(reader)  # skip header
    count = 0
    for row in reader:
        image_id, label = row
        item_name = image_id  # match autograder’s filenames
        client.put_attributes(
            DomainName=DOMAIN_NAME,
            ItemName=item_name,
            Attributes=[
                {"Name": "result", "Value": label, "Replace": True}
            ]
        )
        count += 1
        if count % 100 == 0:
            print(f"Inserted {count} items...")

print("Done! Inserted 1000 items into SimpleDB.")
