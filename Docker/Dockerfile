FROM python:3.7-slim
WORKDIR /usr/src/app

ADD app.py /usr/src/app
COPY /models /usr/src/app/models
COPY 7c54cd2c-5984-44b3-af1d-3f8a9bc5129e.key /usr/src/app/7c54cd2c-5984-44b3-af1d-3f8a9bc5129e.key
COPY /sample_files /usr/src/app/sample_files

RUN apt update
#Check for SUDO, since fastai_audio install script uses SUDO
RUN if type sudo 2>/dev/null; then \
     echo "The sudo command already exists... Skipping."; \
    else \
     echo -e "#!/bin/sh\n\${@}" > /usr/sbin/sudo; \
     chmod +x /usr/sbin/sudo; \
    fi

RUN apt install -y git
RUN apt install -y sox libsox-dev libsox-fmt-all
RUN apt install -y python3-dev gcc
RUN apt install -y build-essential

ADD requirements.txt requirements.txt
RUN pip install -r requirements.txt

#fastai_audio install
RUN git clone https://github.com/mogwai/fastai_audio
RUN fastai_audio/install.sh
RUN mv fastai_audio/audio .
RUN rm -rf fastai_audio/

EXPOSE 8000
CMD [ "python", "./app.py" ]
