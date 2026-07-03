from flask import Flask
from flask import request
from flask import Response
from flask import redirect
from flask import flash
from flask import secure_filename


import boto3
import os

app = Flask(__name__)

s3 = boto3.client("s3", region_name="us-east-1")
client = boto3.client("sdb", region_name="us-east-1")

@app.route("/", methods=["POST"])
def upload_file():
    if "inputFile" not in request.files:
        #flash('No file part') these gets used when its for the browsers
        #return redirect(request.url)

        return Response("no file provided", status=400, mimetype="text/plain")

    file = request.files["inputFile"]
    if file.filename == "":
        #flash('No selected file')      
        #return redirect(request.url)
        return Response("Filename is empty", status=400, mimetype="text/plain")

    filename = file.filename
    name, _ = os.path.splitext(filename)
    #file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename)) coz we dont need to store it locally here
    #return redirect(url_for('download_file', name=filename))  here too no need to go to another url, not browser


    s3.upload_fileobj(file, '1237472342-in-bucket', filename)

    response = client.get_attributes(
    DomainName='1237472342-simpleDB',
    ItemName=name,
    ConsistentRead=True
    )
    response_inside = response.get("Attributes", [])
    label = None
    for i in response_inside:
        if i["Name"] == "result":
            label = i["Value"]
            break

    if label:
        return Response(f"{name}:{label}", mimetype="text/plain")

    return Response("Label Not found", status=404, mimetype="text/plain")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
