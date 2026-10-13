FROM ubuntu:24.04

RUN apt update

RUN apt install -y language-pack-zh-hans
RUN apt install -y curl
RUN locale-gen zh_CN.UTF-8
RUN update-locale LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8
ENV LANG=zh_CN.UTF-8
ENV LC_ALL=zh_CN.UTF-8

RUN apt install -y python3 ffmpeg

ENV AUDIO_CPP_VERSION=v0.9.1
RUN mkdir -p /vc/apps
WORKDIR /vc/apps
RUN curl -L -o audiocpp.tar.gz https://github.com/0xShug0/audio.cpp/releases/download/$AUDIO_CPP_VERSION/audio-$AUDIO_CPP_VERSION-bin-ubuntu-x64-cpu-portable.tar.gz
RUN mkdir -p ./audiocpp
RUN tar xzvf audiocpp.tar.gz -C ./audiocpp

RUN mkdir -p /vc/models
WORKDIR /vc/models
RUN curl -L -o vcmodel.gguf https://www.modelscope.cn/models/audio-cpp/audio.cpp-gguf/resolve/master/Tone-Color-VC-GGUF/tone-color-vc-f32.gguf

WORKDIR /work
COPY audioref /vc/audioref
ENV PATH=$PATH:/vc/apps/audiocpp
COPY main.py /vc
RUN chmod +x /vc/main.py
ENTRYPOINT ["/vc/main.py"]