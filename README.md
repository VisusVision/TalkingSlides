# TalkingSlides
Converts PowerPoint presentations and speaker notes into narrated videos using the presenter's cloned voice and avatar (or default voice/avatar when unavailable). AI automatically detects key content in slides and applies visual effects (bold, highlight boxes, etc.). Generated videos are published on an integrated content delivery platform.

[![Python](https://img.shields.io/badge/Python-3.10-blue.svg)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.0.1-EE4C2C.svg)](https://pytorch.org/)
[![CUDA](https://img.shields.io/badge/CUDA-11.8-76B900.svg)](https://developer.nvidia.com/cuda-toolkit)
[![License](https://img.shields.io/badge/License-TBD-lightgrey.svg)](#license)

---

## Overview

**TalkingSlides** converts `.pptx` files together with their speaker notes into fully narrated videos. The system uses the presenter's cloned voice and avatar to deliver the slide content; when no personalized voice or face is provided, a default voice and avatar are used as a fallback.

In addition to narration, an AI module analyzes each slide's content and the corresponding speaker notes to automatically apply visual emphasis effects — such as **bold text**, highlight boxes, and callouts — so that the viewer's attention is guided to the key points exactly when the narrator mentions them.

The generated videos are published through an integrated content delivery platform, where they can be browsed, organized, and shared.

## Key Features

- **Slide-to-Video Pipeline** — Renders each slide as a video segment synchronized with the narration of its speaker notes.
- **Voice Cloning** — Reproduces the presenter's voice from a short reference sample (multilingual support).
- **Talking-Head Avatar** — Generates a lip-synced video of the presenter's face driven by the synthesized speech.
- **Default Mode** — Falls back to a standard voice and a generic avatar when personalized assets are not provided.
- **AI-Driven Visual Emphasis** — Automatically detects key phrases, terms, and concepts in slides and applies effects (bold, highlight box, underline, color accent) timed to the narration.
- **Multilingual Output** — Supports multiple narration languages out of the box.
- **Content Platform Integration** — Generated videos are delivered through a built-in content presentation platform with browsing, search, and sharing capabilities.

## How It Works

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  .pptx + notes  │ ──▶ │  Content Parser  │ ──▶ │  Key-Phrase AI  │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Slide Renderer │ ◀── │  Effect Planner  │ ◀── │   Slide + Tags  │
│  (with effects) │     └──────────────────┘     └─────────────────┘
└─────────────────┘
        │
        ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   TTS / Voice   │ ──▶ │   Talking-Head   │ ──▶ │  Video Composer │
│     Cloning     │     │     Avatar       │     │  (slide + face) │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                                          │
                                                          ▼
                                                 ┌─────────────────┐
                                                 │ Content Platform│
                                                 └─────────────────┘
```

1. **Parse** — Slides and speaker notes are extracted from the `.pptx` file.
2. **Analyze** — A language model identifies emphasis-worthy phrases per slide based on the notes.
3. **Render** — Slides are rendered with timed visual effects applied to the detected phrases.
4. **Synthesize Speech** — Speaker notes are converted to speech using the cloned voice (or a default voice).
5. **Generate Avatar** — A talking-head video is produced with lip movement synced to the audio.
6. **Compose** — Slide visuals, narrator avatar, and audio are combined into the final video.
7. **Publish** — The video is uploaded to the content delivery platform.

## Tech Stack

| Layer            | Technology                                                  |
|------------------|-------------------------------------------------------------|
| Runtime          | Python 3.10                                                 |
| Deep Learning    | PyTorch 2.0.1 + CUDA 11.8                                   |
| Voice Cloning    | Chatterbox Multilingual TTS                                 |
| Talking Head     | MuseTalk 1.5 (with LivePortrait / Linly-Talker evaluated)   |
| Slide Processing | python-pptx, Pillow, FFmpeg                                 |
| AI Emphasis      | LLM-based key-phrase extraction                             |
| Deployment       | Modal.com (GPU inference) / GCP                             |
| Delivery         | Integrated web-based content platform                       |

## Installation

> ⚠️ Setup instructions will be finalized after the public release of the inference and platform components.

```bash
# Clone the repository
git clone https://github.com/VisusVision/TalkingSlides.git
cd TalkingSlides

# Create a virtual environment (recommended)
python3.10 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

GPU with CUDA 11.8 support is recommended for talking-head generation.

## Usage

```bash
python talkingslides.py \
    --input  presentation.pptx \
    --voice  reference_voice.wav \
    --face   presenter.jpg \
    --lang   en \
    --output ./out
```

| Argument    | Description                                                      |
|-------------|------------------------------------------------------------------|
| `--input`   | Path to the `.pptx` file (required).                             |
| `--voice`   | Reference audio for voice cloning. Optional — default if omitted. |
| `--face`    | Reference image for the avatar. Optional — default if omitted.   |
| `--lang`    | Narration language code (e.g. `en`, `tr`, `de`).                 |
| `--output`  | Output directory for the generated video.                        |

## Roadmap

- [ ] Public alpha release
- [ ] Web UI for upload and configuration
- [ ] Batch processing of multiple presentations
- [ ] Speaker-style adaptation (formal / casual / instructional)
- [ ] Slide-level analytics on the content platform
- [ ] Multi-presenter mode (different avatars per section)

## License

License terms to be announced. Until then, all rights reserved by the project owner.

## Contact

**VisusVision** — Visus Artificial Vision and Automation Systems

For questions, partnership, or commercial inquiries, please open an issue or reach out via the organization page.
