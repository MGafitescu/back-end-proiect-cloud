import logging
import os
import base64
import json
import requests

from flask import Flask, request, jsonify

from google.cloud import datastore
from google.cloud import storage
from google.cloud import vision
from google.cloud import error_reporting
from google.cloud import logging
from google.cloud import translate
from flask_cors import CORS

CLOUD_STORAGE_BUCKET = os.environ.get('CLOUD_STORAGE_BUCKET')

app = Flask(__name__)
CORS(app)


def remove_htlm_tags(html_input):
    start = html_input.find('<')
    while start >= 0:
        stop = html_input.find('>') + 1
        html_input = html_input[0:start] + html_input[stop:]
        start = html_input.find('<')
    return html_input


@app.route('/')
def homepage():
    # Create a Cloud Datastore client.
    datastore_client = datastore.Client()

    # Use the Cloud Datastore client to fetch information from Datastore about
    # each photo.
    query = datastore_client.query(kind='Landmarks')
    image_entities = list(query.fetch())
    data = []
    # for index, image_entity in enumerate(image_entities):
    #     datastore_client.delete(datastore_client.key('Landmarks',image_entity["description"]))
    for index, image_entity in enumerate(image_entities):
        info = dict()
        info["description"] = image_entity["description"]
        info["latitude"] = image_entity["latitude"]
        info["longitude"] = image_entity["longitude"]
        info["url"] = image_entity["image_public_url"]
        info['formatted_address'] = image_entity['formatted_address']
        info["formatted_phone_number"] = image_entity["formatted_phone_number"]
        info["international_phone_number"] = image_entity["international_phone_number"]
        info["types"] = image_entity["types"]
        info["website"] = image_entity["website"]
        info["wikipedia_extract"] = image_entity["wikipedia_extract"]
        data.append(info)

    return jsonify(json.dumps(data)), 200, {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
    }


@app.route('/muie', methods=['GET'])
def muie():
    return "aaa"


def upload_photo_to_storage(photo, filename):
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
    return blob.name, blob.public_url


def get_landmark(name, logger):
    # Create a Cloud Vision client.
    vision_client = vision.ImageAnnotatorClient()

    # Use the Cloud Vision client to detect a face for our image.
    source_uri = 'gs://{}/{}'.format(CLOUD_STORAGE_BUCKET, name)
    image = vision.types.Image(
        source=vision.types.ImageSource(gcs_image_uri=source_uri))
    landmarks = vision_client.landmark_detection(image).landmark_annotations
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
        logger.log_text('No landmarks detected for ' + name)
    return description, latitude, longitude


def get_place_id(description):
    payload = dict()
    payload["key"] = 'AIzaSyCl9VWpSabxI5kyc1aLEXQ6oDSY7sJ2JYI'
    payload["input"] = description
    payload["inputtype"] = "textquery"

    response = requests.get("https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
                            payload)
    place_id = response.json()["candidates"][0]["place_id"]
    return place_id


def get_details(place_id):
    payload = dict()
    payload["key"] = 'AIzaSyCl9VWpSabxI5kyc1aLEXQ6oDSY7sJ2JYI'
    payload["placeid"] = place_id

    response = requests.get("https://maps.googleapis.com/maps/api/place/details/json",
                            payload)
    response = response.json()["result"]
    formatted_address = response["formatted_address"]
    formatted_phone_number = response["formatted_phone_number"]
    international_phone_number = response["international_phone_number"]
    types = response["types"]
    website = response["website"]
    return formatted_address, formatted_phone_number, international_phone_number, types, website


def get_wikipedia_extract(description):
    payload = dict()
    payload["action"] = "opensearch"
    payload["search"] = description
    payload["limit"] = 1
    payload["namespace"] = 0
    payload["format"] = "json"

    response = requests.get("https://en.wikipedia.org/w/api.php", payload)
    wiki_name = response.json()[0]
    request_url = "https://en.wikipedia.org/w/api.php?action=query&prop=extracts&exintro&titles=" + wiki_name + "&format=json"
    response = requests.get(request_url)
    response = response.json()
    pages = response["query"]["pages"]
    extract = response["query"]["pages"][list(pages.keys())[0]]["extract"]
    extract = remove_htlm_tags(extract)

    return extract


def translate_text(text, language):
    client = translate.Client()
    response = client.translate(text, target_language=language)
    return response["translatedText"]


@app.route('/upload_photo', methods=['GET', 'POST'])
def upload_photo():
    photo = request.json['file']
    filename = request.json['filename']

    name, blob_public_url = upload_photo_to_storage(photo, filename)

    client = logging.Client()
    logger = client.logger('log_name')

    description, latitude, longitude = get_landmark(name, logger)

    # Create a Cloud Datastore client.
    datastore_client = datastore.Client()
    # The kind for the new entity.
    kind = 'Landmarks'

    # Create the Cloud Datastore key for the new entity.
    key = datastore_client.key(kind, name)

    place_id = get_place_id(description)

    formatted_address, formatted_phone_number, international_phone_number, types, website = get_details(place_id)

    wikipedia_extract = get_wikipedia_extract(description)
    wikipedia_extract = translate_text(wikipedia_extract, "ro")

    print(wikipedia_extract)

    entity = datastore.Entity(key, exclude_from_indexes=['wikipedia_extract'])
    entity['blob_name'] = name
    entity['image_public_url'] = blob_public_url
    entity['description'] = description
    entity["latitude"] = latitude
    entity['longitude'] = longitude
    entity['formatted_address'] = formatted_address
    entity["formatted_phone_number"] = formatted_phone_number
    entity["international_phone_number"] = international_phone_number
    entity["types"] = types
    entity["website"] = website
    entity["wikipedia_extract"] = wikipedia_extract

    # Save the new entity to Datastore.
    datastore_client.put(entity)

    info = dict()
    info["public_url"] = blob_public_url

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
    app.run(host='127.0.0.1', port=8090, debug=True)
