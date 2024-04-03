FROM python:3.12.2-alpine

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

ENV IN_DOCKER=1
COPY ./src .

CMD [ "gunicorn", "-w 4 -b 0.0.0.0", "'main:app'"]