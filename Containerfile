FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libxcb1 \
    libxext6 \
    libsm6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*


WORKDIR /app

COPY app/requirements.txt .

RUN pip install --no-cache-dir torch==2.3.1 torchvision==0.18.1 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --no-deps open_clip_torch
RUN pip install --no-cache-dir -r requirements.txt

COPY app/main.py .
COPY app/face_search.py .

RUN mkdir -p /data /pictures /references_faces

# Refuse images over ~179 MP (Pillow's decompression-bomb limit, which main.py
# already gets) instead of OpenCV's 1-gigapixel default: a PNG of a few MB can
# otherwise decode to gigabytes of RAM during face indexing.
ENV OPENCV_IO_MAX_IMAGE_PIXELS=178956970

ENTRYPOINT ["python", "-u", "main.py"]

