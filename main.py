import base64
import json
import logging
import os
import base64

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from google.cloud import datastore
from google.cloud import error_reporting
from google.cloud import logging
from google.cloud import storage
from google.cloud import vision

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
    # for index, image_entity in enumerate(image_entities):
    #     datastore_client.delete(datastore_client.key('Landmarks',image_entity["description"]))
    for index, image_entity in enumerate(image_entities):
        info = dict()
        info["description"] = image_entity.get("description", "Unknown")
        info["latitude"] = image_entity.get("latitude", "Unknown")
        info["longitude"] = image_entity.get("longitude", "Unknown")
        info["url"] = image_entity.get("image_public_url", "Unknown")
        info['formatted_address'] = image_entity.get("formatted_address", "Unknown")
        info["formatted_phone_number"] = image_entity.get("formatted_phone_number", "Unknown")
        info["international_phone_number"] = image_entity.get("international_phone_number", "Unknown")
        info["types"] = image_entity.get("types", [])
        info["website"] = image_entity.get("website", "Unknown")
        info["wikipedia_extract"] = image_entity.get("wikipedia_extract", "Unknown")
        info["audio"] = image_entity.get("audio", "Unknown")
        data.append(info)

    return jsonify(json.dumps(data)), 200, {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
    }


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


def upload_mp3_to_storage(audio, audioname):
    # Create a Cloud Storage client.
    storage_client = storage.Client()
    # Get the bucket that the file will be uploaded to.
    bucket = storage_client.get_bucket(CLOUD_STORAGE_BUCKET)

    # Create a new blob and upload the file's content.
    blob = bucket.blob(audioname)
    blob.upload_from_string(
        base64.b64decode(audio), content_type="audio/mpeg")

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


def get_text(name, logger):
    # Create a Cloud Vision client.
    vision_client = vision.ImageAnnotatorClient()

    # Use the Cloud Vision client to detect a face for our image.
    source_uri = 'gs://{}/{}'.format(CLOUD_STORAGE_BUCKET, name)
    image = vision.types.Image(
        source=vision.types.ImageSource(gcs_image_uri=source_uri))
    text_annotations = vision_client.text_detection(image).text_annotations
    if len(text_annotations) > 0:
        text_object = text_annotations[0]
        text = text_object.description
    else:
        text = "Unknown"
    return text


def get_place_id(description):
    payload = dict()
    payload["key"] = 'AIzaSyCl9VWpSabxI5kyc1aLEXQ6oDSY7sJ2JYI'
    payload["input"] = description
    payload["inputtype"] = "textquery"

    response = requests.get("https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
                            payload)
    response = response.json()
    place_id = response.get("candidates", None)
    if place_id is None or place_id == []:
        return None
    place_id = place_id[0]["place_id"]
    return place_id


def get_details(place_id):
    if place_id is None:
        return "Unkown", "Unknown", "Unknown", [], "Unknown"
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


def remove_htlm_tags(html_input):
    start = html_input.find('<')
    while start >= 0:
        stop = html_input.find('>') + 1
        html_input = html_input[0:start] + html_input[stop:]
        start = html_input.find('<')
    return html_input


def get_wikipedia_extract(description):
    if description is "Unknown":
        return "Unknown"
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
    data = dict()
    data["q"] = text
    data["target"] = language
    data["key"] = 'AIzaSyCl9VWpSabxI5kyc1aLEXQ6oDSY7sJ2JYI'
    response = requests.post("https://translation.googleapis.com/language/translate/v2", data).json()
    response = response["data"]["translations"][0]["translatedText"]
    return response


def get_audio(text, language):
    data = dict()
    data["input"] = {"text": text}
    data["voice"] = {"languageCode": language}
    data["audioConfig"] = {"audioEncoding": "MP3"}
    response = requests.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize?key=AIzaSyDvgTvCu9L8FYXckNZG2cn76-pKqsbj36w",
        None, data).json()
    with open("a.mp3", "wb") as f:
        f.write(base64.b64decode(response["audioContent"]))
    return response["audioContent"]


@app.route('/upload_photo', methods=['GET', 'POST'])
def upload_photo():
    photo = request.json['file']
    filename = request.json['filename']
    selected_language = request.json['language']

    audio_language = selected_language
    selected_language = selected_language.split('-')[0]
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
    wikipedia_extract = translate_text(wikipedia_extract, selected_language)

    audio = get_audio(wikipedia_extract, audio_language)
    audio_name = audio_language + description + ".mp3"
    audio_name, audio_url = upload_mp3_to_storage(audio, audio_name)
    audio = audio_url

    entity = datastore.Entity(key, exclude_from_indexes=['wikipedia_extract', "audio"])
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
    entity["audio"] = audio

    # Save the new entity to Datastore.
    datastore_client.put(entity)

    info = dict()
    info["description"] = entity.get("description", "Unknown")
    info["latitude"] = entity.get("latitude", "Unknown")
    info["longitude"] = entity.get("longitude", "Unknown")
    info["url"] = entity.get("image_public_url", "Unknown")
    info['formatted_address'] = entity.get("formatted_address", "Unknown")
    info["formatted_phone_number"] = entity.get("formatted_phone_number", "Unknown")
    info["international_phone_number"] = entity.get("international_phone_number", "Unknown")
    info["types"] = entity.get("types", [])
    info["website"] = entity.get("website", "Unknown")
    info["wikipedia_extract"] = entity.get("wikipedia_extract", "Unknown")
    info["audio"] = audio

    return jsonify(json.dumps(info)), 200, {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
    }


@app.route('/upload_text_photo', methods=['GET', 'POST'])
def upload_text_photo():
    photo = request.json['file']
    filename = request.json['filename']
    selected_language = request.json['language']

    audio_language = selected_language
    selected_language = selected_language.split('-')[0]
    name, blob_public_url = upload_photo_to_storage(photo, filename)

    client = logging.Client()
    logger = client.logger('log_name')

    text = get_text(name, logger)

    translated_text = translate_text(text, selected_language)

    audio = get_audio(translated_text, audio_language)
    audio_name = audio_language + name + ".mp3"
    audio_name, audio_url = upload_mp3_to_storage(audio, audio_name)
    audio = audio_url

    info = dict()
    info["original_text"] = text
    info["translated_text"] = translated_text
    info["audio"] = audio

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
