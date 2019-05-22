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

from pathlib import Path
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.pyplot import specgram

from fastai import *
from fastai.vision import *

import librosa
import librosa.display
import requests

from dotenv import load_dotenv
load_dotenv()

# Only used for record function

logging.captureWarnings(True)

# Constants:
MS_PER_FRAME = 20  # Duration of a frame in ms
RATE = 16000
SILENCE = 12  # How many continuous frames of silence determine the end of a phrase
CLIP_MIN_MS = 350  # ms - the minimum audio clip that will be used
MAX_LENGTH = 4000  # Max length of a sound clip for processing in ms
VAD_SENSITIVITY = 3
CLIP_MIN_FRAMES = CLIP_MIN_MS // MS_PER_FRAME

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

model_file_url = 'https://www.dropbox.com/s/lds8bwm46yrtqig/amd-stage1?dl=0'
model_file_name = 'amd-stage1'
classes = ['beep', 'speech']

path = Path(__file__).parent

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

class FastAI(object):
    def __init__(self):
        self.learn = self.setup_learner()

    def download_file(self, url, dest):

        if dest.exists():
            print("already downloaded");
            return
        r = requests.get(url)
        print(r)
        open(dest, 'wb').write(r.content)

    def setup_learner(self):
        learn = load_learner(path/'models',f'{model_file_name}.pth')
        return learn

    def predict_from_file(self,file):
      print("loading file",file)
      samples, sample_rate = librosa.load(file)
      fig = plt.figure(figsize=[0.72,0.72])
      ax = fig.add_subplot(111)
      ax.axes.get_xaxis().set_visible(False)
      ax.axes.get_yaxis().set_visible(False)
      ax.set_frame_on(False)
      filename  = file.split("/")[-1].replace("wav","png")
      print(filename)
      S = librosa.feature.melspectrogram(y=samples, sr=sample_rate)
      librosa.display.specshow(librosa.power_to_db(S, ref=np.max))
      plt.savefig(filename, dpi=400, bbox_inches='tight',pad_inches=0)
      plt.close('all')
      img = open_image(filename)
      predict = self.learn.predict(img)
      self.removeFile(file)
      return predict

    def removeFile(self,wav_file):
        os.remove(wav_file)
        png_file  = wav_file.split("/")[-1].replace("wav","png")
        os.remove(png_file)

class AudioProcessor(object):
    def __init__(self, path, fastai):
        self._path = path
        self.fastai = fastai

    def process(self, count, payload, id):
        if count > CLIP_MIN_FRAMES :  # If the buffer is less than CLIP_MIN_MS, ignore it
            print("record clip")
            fn = "rec-{}-{}.wav".format(id,datetime.datetime.now().strftime("%Y%m%dT%H%M%S"))
            output = wave.open(fn, 'wb')
            output.setparams(
                (1, 2, RATE, 0, 'NONE', 'not compressed'))
            output.writeframes(payload)
            output.close()
            pred_class, pred_idx, outputs = self.fastai.predict_from_file(fn)
            print(pred_idx.item())
            print(pred_class)
        else:
            info('Discarding {} frames'.format(str(count)))

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
            info(message)
            fastai = FastAI()
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
    def post(self):
        data = json.loads(self.request.body)
        print(data)
        ncco = [
              {
                "action": "talk",
                "text": "Thanks. Connecting you now"
              },
             {
             "action": "connect",
              # "eventUrl": [self.request.protocol +"://" + self.request.host  + "/event"],
               "from": MY_LVN,
               "endpoint": [
                 {
                   "type": "phone",
                   "number": data["dtmf"]
                 }
               ]
             },
              {
                 "action": "connect",
                 # "eventUrl": [self.request.protocol +"://" + self.request.host  +"/event"],
                 "from": MY_LVN,
                 "endpoint": [
                     {
                        "type": "websocket",
                        "uri" : "ws://"+self.request.host +"/socket",
                        "content-type": "audio/l16;rate=16000",
                        "headers": {
                            "uuid":data["uuid"]
                        }
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
            (r"/ncco", EnterPhoneNumberHandler),
            (r"/recording", RecordHandler),
            (r"/ivr", AcceptNumberHandler),
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
