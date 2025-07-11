#!/bin/sh
export DISPLAY=:99
sleep 3 & \

Xvfb :99 -screen 0 1280x680x16 & \
# Xvfb :99 -screen 0 1350x768x16 & \
sleep 3 & \
# x11vnc -passwd 'VoyeR' -display :99 -N -forever -rfbport 5913 -f & \
x11vnc -rfbport 5914 -passwd 'V0oiye3R' -display :99 -N -forever & \
sleep 3 & \
# python -m nltk.downloader punkt
uvicorn main:app --host 0.0.0.0 --port 8000
