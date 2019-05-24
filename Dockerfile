FROM python:3
WORKDIR /usr/src/app
COPY . .
RUN pip install -r requirements.txt
RUN \
	apt-get update && \
	DEBIAN_FRONTEND=noninteractive \
		apt-get install -y \
			libav-tools gpac \
	&& \
	apt-get clean && \
	rm -rf /var/lib/apt/lists/
  
EXPOSE 8000
CMD ["python", "websocket-demo.py"]
