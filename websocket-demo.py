#!/usr/bin/env python

from __future__ import absolute_import, print_function
import wave
import datetime
import argparse
import io
import logging
import os
import sys
import time
from logging import debug, info
import uuid
import cgi
import audioop
import requests
import tornado.ioloop
import tornado.websocket
import tornado.httpserver
import tornado.template
import tornado.web
import webrtcvad
from tornado.web import url
import json

from base64 import b64decode
import nexmo
import collections


import numpy as np
from scipy.io import wavfile
import librosa
import pickle
from google.cloud import storage

from dotenv import load_dotenv
load_dotenv()

# Only used for record function

logging.captureWarnings(True)

# Constants:
MS_PER_FRAME = 20  # Duration of a frame in ms
RATE = 16000
SILENCE = 10  # How many continuous frames of silence determine the end of a phrase
CLIP_MIN_MS = 200  # ms - the minimum audio clip that will be used
MAX_LENGTH = 5000  # Max length of a sound clip for processing in ms
VAD_SENSITIVITY = 3
CLIP_MIN_FRAMES = CLIP_MIN_MS // MS_PER_FRAME
CONVERSATION_NAME = "my_conf"
CONNECT_NUMBER = "14082488718"

# Global variables
conns = {}
conversation_uuids = collections.defaultdict(list)
uuids = []

# Environment Variables, these are set in .env locally
HOSTNAME = os.getenv("HOSTNAME")
PORT = os.getenv("PORT")

MY_LVN = os.getenv("MY_LVN")
APP_ID = os.getenv("APP_ID")
PROJECT_ID = os.getenv("PROJECT_ID")
CLOUD_STORAGE_BUCKET = os.getenv("CLOUD_STORAGE_BUCKET")

def _get_private_key():
    try:
        return os.environ['PRIVATE_KEY']
    except:
        with open('private.key', 'r') as f:
            private_key = f.read()

    return private_key

PRIVATE_KEY = _get_private_key()
client = nexmo.Client(application_id=APP_ID, private_key=PRIVATE_KEY)

class BufferedPipe(object):
    def __init__(self, max_frames, sink):
        """
        Create a buffer which will call the provided `sink` when full.

        It will call `sink` with the number of frames and the accumulated bytes when it reaches
        `max_buffer_size` frames.
        """
        self.sink = sink
        self.max_frames = max_frames

        self.count = 0
        self.payload = b''

    def append(self, data, id):
        """ Add another data to the buffer. `data` should be a `bytes` object. """

        self.count += 1
        self.payload += data

        if self.count == self.max_frames:
            self.process(id)

    def process(self, id):
        """ Process and clear the buffer. """
        self.sink(self.count, self.payload, id)
        self.count = 0
        self.payload = b''

class AudioProcessor(object):
    def __init__(self, path, client):
        self._path = path
        self.client = client
    def process(self, count, payload, id):
        if count > CLIP_MIN_FRAMES:  # If the buffer is less than CLIP_MIN_MS, ignore it
            fn = "{}rec-{}-{}.wav".format('', id, datetime.datetime.now().strftime("%Y%m%dT%H%M%S"))
            output = wave.open(fn, 'wb')
            output.setparams(
                (1, 2, RATE, 0, 'NONE', 'not compressed'))
            output.writeframes(payload)
            output.close()
            debug('File written {}'.format(fn))
            self.process_file(fn)
            info('Processing {} frames for {}'.format(str(count), id))
            self.removeFile(fn)
        else:
            info('Discarding {} frames'.format(str(count)))
    def process_file(self, wav_file):
        if loaded_model != None:
            print("load file {}".format(wav_file))
            X, sample_rate = librosa.load(wav_file, res_type='kaiser_fast')
            X = librosa.resample(X, sample_rate, 16000)

            duration = librosa.get_duration(X, sample_rate)
            stft = np.abs(librosa.stft(X))
            mfccs_40 = np.mean(librosa.feature.mfcc(y=X, sr=sample_rate, n_mfcc=40).T,axis=0)
            chroma = np.mean(librosa.feature.chroma_stft(S=stft, sr=sample_rate).T,axis=0)
            mel = np.mean(librosa.feature.melspectrogram(y=X, sr=sample_rate,n_mels=128,fmax=8000).T,axis=0)
            contrast = np.mean(librosa.feature.spectral_contrast(S=stft, sr=sample_rate).T,axis=0)
            tonnetz = np.mean(librosa.feature.tonnetz(y=librosa.effects.harmonic(X),
            sr=sample_rate).T,axis=0)

            a = [mfccs_40, chroma, mel, contrast, tonnetz]

            total_len = 0
            for f in a:
                total_len += f.shape[0]

            features = np.vstack([np.empty((0,total_len)),np.hstack(a)])

            prediction = loaded_model.predict(features)
            # prediction = loaded_model.predict([mfccs_40])
            print("prediction",prediction)

            if prediction[0] == 0:
                beep_captured = True
                print("send_speech",uuids)
                for id in uuids:
                    response = self.client.send_speech(id, text='Answering Machine Detected')
                    print("send_speech response",response)
                time.sleep(4)
                for id in uuids:
                    try:
                        self.client.update_call(id, action='hangup')
                    except:
                        pass

        else:
            print("model not loaded")
    def removeFile(self, wav_file):
         os.remove(wav_file)

class WSHandler(tornado.websocket.WebSocketHandler):
    def initialize(self):
        # Create a buffer which will call `process` when it is full:
        self.frame_buffer = None
        # Setup the Voice Activity Detector
        self.tick = None
        self.id = uuid.uuid4().hex
        self.vad = webrtcvad.Vad()
        # Level of sensitivity
        self.vad.set_mode(VAD_SENSITIVITY)

        self.processor = None
        self.path = None
        conns[self.id] = self

    def open(self, path):
        info("client connected")
        debug(self.request.uri)
        self.path = self.request.uri
        self.tick = 0

    def on_message(self, message):
        # Check if message is Binary or Text
        if type(message) != str:
            if self.vad.is_speech(message, RATE):
                debug("SPEECH from {}".format(self.id))
                self.tick = SILENCE
                self.frame_buffer.append(message, self.id)
            else:
                debug("Silence from {} TICK: {}".format(self.id, self.tick))
                self.tick -= 1
                if self.tick == 0:
                    # Force processing and clearing of the buffer
                    self.frame_buffer.process(self.id)
        else:
            # Here we should be extracting the meta data that was sent and attaching it to the connection object
            data = json.loads(message)
            if data.get('content-type'):
                uuid = data.get('uuid')
                self.processor = AudioProcessor(
                    self.path, fastai).process
                self.frame_buffer = BufferedPipe(MAX_LENGTH // MS_PER_FRAME, self.processor)
                self.write_message('ok')

    def on_close(self):
        # Remove the connection from the list of connections
        del conns[self.id]
        print("client disconnected")

class EventHandler(tornado.web.RequestHandler):
    @tornado.web.asynchronous
    def post(self):
        data = json.loads(self.request.body)
        print(data)
        if data["status"] == "answered":
            conversation_uuid = data["conversation_uuid"]
            uuid = data["uuid"]
            conversation_uuids[conversation_uuid].append(uuid)
            uuids.append(uuid)

        if data["status"] == "completed":
            conversation_uuid = data["conversation_uuid"]
            for uuid in conversation_uuids[conversation_uuid]:
                print("hangup uuid",uuid)
                try:
                    response = client.update_call(uuid, action='hangup')
                    print(response)
                except Exception as e:
                    print(e)
        self.content_type = 'text/plain'
        self.write('ok')
        self.finish()

class EnterPhoneNumberHandler(tornado.web.RequestHandler):
    @tornado.web.asynchronous
    def get(self):
        ncco = [
              {
                "action": "talk",
                "text": "Please enter a phone number to dial"
              },
              {
                "action": "input",
                "eventUrl": [self.request.protocol +"://" + self.request.host +"/ivr"],
                "timeOut":10,
                "maxDigits":12,
                "submitOnHash":True
              }
            ]
        self.write(json.dumps(ncco))
        self.set_header("Content-Type", 'application/json; charset="utf-8"')
        self.finish()

class AcceptNumberHandler(tornado.web.RequestHandler):
    @tornado.web.asynchronous
    def get(self):
        # data = json`.loads(self.request.body)
        # print(data)`
        ncco = [
              {
                "action": "talk",
                "text": "Thanks. Connecting you now"
              },
             {
             "action": "connect",
              "eventUrl": [self.request.protocol +"://" + self.request.host  + "/event"],
               "from": MY_LVN,
               "endpoint": [
                 {
                   "type": "phone",
                   "number": CONNECT_NUMBER
                 }
               ]
             },
              {
                 "action": "connect",
                 "eventUrl": [self.request.protocol +"://" + self.request.host  +"/event"],
                 "from": MY_LVN,
                 "endpoint": [
                     {
                        "type": "websocket",
                        "uri" : "ws://"+self.request.host +"/socket",
                        "content-type": "audio/l16;rate=16000",
                        # "headers": {
                        #     "uuid":data["uuid"]
                        # }
                     }
                 ]
               }
            ]
        self.write(json.dumps(ncco))
        self.set_header("Content-Type", 'application/json; charset="utf-8"')
        self.finish()

class RecordHandler(tornado.web.RequestHandler):
    @tornado.web.asynchronous
    def post(self):
        data = json.loads(self.request.body)

        response = client.get_recording(data["recording_url"])
        fn = "call-{}.wav".format(data["conversation_uuid"])

        if PROJECT_ID and CLOUD_STORAGE_BUCKET:
            blob = bucket.blob(fn)
            blob.upload_from_string(response, content_type="audio/wav")
            print('File uploaded.')

        self.write('ok')
        self.set_header("Content-Type", 'text/plain')
        self.finish()

class PingHandler(tornado.web.RequestHandler):
    @tornado.web.asynchronous
    def get(self):
        self.write('ok')
        self.set_header("Content-Type", 'text/plain')
        self.finish()


def main():
    try:
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)7s %(message)s",
        )
        application = tornado.web.Application([
			url(r"/ping", PingHandler),
            (r"/event", EventHandler),
            (r"/ncco", AcceptNumberHandler),
            (r"/recording", RecordHandler),
            url(r"/(.*)", WSHandler),
        ])
        http_server = tornado.httpserver.HTTPServer(application)
        port = int(os.getenv('PORT', 8000))
        http_server.listen(port)
        tornado.ioloop.IOLoop.instance().start()
    except KeyboardInterrupt:
        pass  # Suppress the stack-trace on quit


if __name__ == "__main__":
    main()
