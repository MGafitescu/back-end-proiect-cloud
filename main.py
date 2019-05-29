import logging
import os
import base64
import json

from flask import Flask, request, jsonify

from google.cloud import datastore
from google.cloud import storage
from google.cloud import vision
from google.cloud import error_reporting
from google.cloud import logging
from flask_cors import CORS

CLOUD_STORAGE_BUCKET = os.environ.get('CLOUD_STORAGE_BUCKET')

app = Flask(__name__)
CORS(app)


@app.route('/')
def homepage():
    # Create a Cloud Datastore client.
    datastore_client = datastore.Client()

    # Use the Cloud Datastore client to fetch information from Datastore about
    # each photo.
    query = datastore_client.query(kind='Landmarks')
    image_entities = list(query.fetch())

    data = []
    for index,image_entity in enumerate(image_entities):
        info = dict()
        info["description"] = image_entity["description"]
        info["latitude"] = image_entity["latitude"]
        info["longitude"] = image_entity["longitude"]
        info["url"] = image_entity["image_public_url"]
        data.append(info)
    return jsonify(json.dumps(data)), 200, {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
    }


@app.route('/upload_photo', methods=['GET', 'POST'])
def upload_photo():
    photo = request.json['file']
    filename = request.json['filename']

    # Create a Cloud Storage client.
    storage_client = storage.Client()

    # Get the bucket that the file will be uploaded to.
    bucket = storage_client.get_bucket(CLOUD_STORAGE_BUCKET)

    # Create a new blob and upload the file's content.
    blob = bucket.blob(filename)
    blob.upload_from_string(
        base64.b64decode(photo), content_type="image/jpeg")

    # Make the blob publicly viewable.
    blob.make_public()

    # Create a Cloud Vision client.
    vision_client = vision.ImageAnnotatorClient()

    # Use the Cloud Vision client to detect a face for our image.
    source_uri = 'gs://{}/{}'.format(CLOUD_STORAGE_BUCKET, blob.name)
    image = vision.types.Image(
        source=vision.types.ImageSource(gcs_image_uri=source_uri))
    landmarks = vision_client.landmark_detection(image).landmark_annotations

    client = logging.Client()
    logger = client.logger('log_name')


    if len(landmarks) > 0:
        landmark = landmarks[0]
        description = landmark.description
        location = landmark.locations[0]
        latitude = location.lat_lng.latitude
        longitude = location.lat_lng.longitude
    else:
        description = "Unknown"
        latitude = "Unknown"
        longitude = "Unknown"
        logger.log_text('No landmarks detected for '+blob.name)


    # Create a Cloud Datastore client.
    datastore_client = datastore.Client()

    # The kind for the new entity.
    kind = 'Landmarks'

    # The name/ID for the new entity.
    name = blob.name

    # Create the Cloud Datastore key for the new entity.
    key = datastore_client.key(kind, name)

    # Construct the new entity using the key. Set dictionary values for entity
    # keys blob_name, storage_public_url, timestamp, and joy.
    entity = datastore.Entity(key)
    entity['blob_name'] = blob.name
    entity['image_public_url'] = blob.public_url
    entity['description'] = description
    entity["latitude"] = latitude
    entity['longitude'] = longitude

    # Save the new entity to Datastore.
    datastore_client.put(entity)

    # Redirect to the home page.
    info = dict()
    info["description"] = description
    info["latitude"] = latitude
    info["longitude"] = longitude
    info["url"] = blob.public_url

    return jsonify(json.dumps(info)), 200, {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
    }


@app.errorhandler(500)
def server_error(e):
    client = error_reporting.Client()
    client.report_exception()
    client.report("An error occurred during a request")
    return """
    An internal error occurred: <pre>{}</pre>
    See logs for full stacktrace.
    """.format(e), 500


if __name__ == '__main__':
    # This is used when running locally. Gunicorn is used to run the
    # application on Google App Engine. See entrypoint in app.yaml.
    app.run(host='127.0.0.1', port=8080, debug=True)
