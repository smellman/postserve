FROM python:3.7.2
RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app

VOLUME /mapping

COPY . /usr/src/app/
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "-u","/usr/src/app/server.py"]
