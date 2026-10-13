# vc

[English](README.md) | **简体中文**

一条命令即可使用的**音色转换(Voice Conversion)**工具,以 Docker 镜像形式交付。

`vc` 可以把任意音频文件转换成参考音频的音色,纯 CPU 运行。镜像内封装了 [audio.cpp](https://github.com/0xShug0/audio.cpp)、[Tone-Color-VC](https://www.modelscope.cn/models/audio-cpp/audio.cpp-gguf) GGUF 模型和 `ffmpeg`,无需安装 Python、PyTorch 等任何环境。

## 工作原理

```
输入音频(m4a / mp3 / wav / ...)
        │
        ▼
[1/2] ffmpeg          → 重采样为 24 kHz、单声道、16bit PCM wav
        │
        ▼
[2/2] audiocpp_cli    → 使用 Tone-Color-VC 模型 + 内置参考音色
                        执行音色转换(--task vc)
        │
        ▼
vc_out.wav
```

## 快速开始(预构建镜像)

在源音频文件所在目录执行:

**Linux / macOS / Git Bash:**

```shell
docker run --rm -ti -v `pwd`:/work xtiqin/vc Untitled.m4a
```

**Windows PowerShell:**

```powershell
docker run --rm -ti -v ${PWD}:/work xtiqin/vc Untitled.m4a
```

**Windows CMD:**

```bat
docker run --rm -ti -v %cd%:/work xtiqin/vc Untitled.m4a
```

> **注意(Apple Silicon / ARM 机器):** 镜像内的 audio.cpp 二进制和模型为 `linux/amd64` 构建,需要在命令中加上 `--platform linux/amd64`,以模拟方式运行,速度会较慢。

转换完成后,结果输出到同一目录(即挂载到 `/work` 的目录)下的 `vc_out.wav`。

### 参数说明

容器只接受**一个参数**:源音频文件路径,相对于挂载的 `/work` 目录。输入格式支持 `ffmpeg` 能识别的任意音频(m4a、mp3、wav、flac、ogg 等)。

## 自行构建镜像

```shell
git clone https://github.com/plter/vc.git
cd vc
docker build -t vc .
```

构建过程会:

1. 在 `ubuntu:24.04` 上安装 `ffmpeg` 和中文语言环境
2. 从 GitHub Releases 下载 audio.cpp CLI(v0.9.1,CPU 便携版,x64)
3. 从 ModelScope 下载 `tone-color-vc-f32.gguf` 模型

构建完成后,运行方式与预构建镜像相同:

```shell
docker run --rm -ti -v `pwd`:/work vc Untitled.m4a
```

## 更换目标音色

目标音色(即音频要被转换**成**的声音)是 `audioref/audio.wav`,在构建镜像时被打包进镜像。想使用自己的目标音色:

1. 用你自己的录音替换 `audioref/audio.wav`(建议为干净的单人语音片段,时长约 10 秒,优先使用 WAV 格式)
2. 重新构建镜像(见[自行构建镜像](#自行构建镜像))

## 项目结构

| 路径 | 说明 |
| --- | --- |
| `Dockerfile` | 镜像构建:ubuntu 24.04 + ffmpeg + audio.cpp + Tone-Color-VC 模型 |
| `main.sh` | 容器入口脚本:ffmpeg 预处理 → audiocpp_cli 推理 |
| `audioref/audio.wav` | 内置参考音色(转换目标音色) |
| `LICENSE` | MIT 许可证 |

## 致谢

- [audio.cpp](https://github.com/0xShug0/audio.cpp) —— 高性能音频推理引擎
- [Tone-Color-VC GGUF 模型](https://www.modelscope.cn/models/audio-cpp/audio.cpp-gguf) —— 由 ModelScope 托管
- [ffmpeg](https://ffmpeg.org/) —— 音频预处理

## 免责声明

本工具仅供学习与研究使用。音色转换涉及人格权(声音权益),在许多司法辖区受法律约束。**请只转换你已获得明确授权的声音**,不得将输出用于诈骗、冒充他人、传播虚假信息等任何损害他人权益的用途。使用本工具产生的一切后果由使用者自行承担。

## 许可证

[MIT](LICENSE) © 2026 梦宇
