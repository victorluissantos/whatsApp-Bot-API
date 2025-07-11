FROM python:3.6-alpine
RUN apk add --no-cache python3-dev libstdc++ && \
    apk add --no-cache g++ && \
    ln -s /usr/include/locale.h /usr/include/xlocale.h && \
    pip3 install numpy && \
    pip3 install pandas

### configura ambiente do selenium
RUN apk update
RUN apk add dbus-x11 ttf-freefont xvfb xdg-utils xdpyinfo fontconfig chromium chromium-chromedriver firefox-esr x11vnc 
RUN apk update
RUN apk update && apk add postgresql-dev gcc python3-dev musl-dev

### Install Pillow
RUN apk update \
    && apk add --virtual build-deps gcc python3-dev musl-dev \
    && apk add postgresql \
    && apk add postgresql-dev \
    && pip install psycopg2 \
    && apk add jpeg-dev zlib-dev libjpeg \
    && pip install Pillow \
    && apk del build-deps

ENV DISPLAY=:99
ENV DBUS_SESSION_BUS_ADDRESS=/dev/null

ADD . /code
WORKDIR /code
RUN pip install --upgrade pip
# RUN pip install mysql-connector-python

ADD ./ /code
WORKDIR /code

RUN pip install --upgrade  pip
RUN pip install -r requirements.txt

COPY entry_point.sh /entry_point.sh
RUN chmod +x /entry_point.sh
CMD ["/entry_point.sh"]