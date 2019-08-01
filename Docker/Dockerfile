FROM python:3.7-slim
WORKDIR /usr/src/app
ADD my_script.py /usr/src/app
COPY /models /usr/src/app/models
COPY /sample_files /usr/src/app/sample_files

RUN apt update
RUN if type sudo 2>/dev/null; then \
     echo "The sudo command already exists... Skipping."; \
    else \
     echo -e "#!/bin/sh\n\${@}" > /usr/sbin/sudo; \
     chmod +x /usr/sbin/sudo; \
    fi
RUN apt install -y git
#RUN apt install -y python3-dev gcc
RUN apt install -y sox libsox-dev libsox-fmt-all
RUN apt install -y build-essential

ADD requirements.txt requirements.txt

RUN pip install -r requirements.txt

RUN git clone https://github.com/mogwai/fastai_audio
RUN fastai_audio/install.sh
RUN mv fastai_audio/audio .
RUN rm -rf fastai_audio/

CMD [ "python", "./my_script.py" ]
