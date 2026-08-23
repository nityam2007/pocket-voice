# Pocket Voice

A private, local voice-cloning WebUI powered by Kyutai Pocket TTS and FastAPI.

Accepted voice samples: MP3, WAV, M4A, FLAC, and OGG (up to 100 MB).

## Run on Windows

Double-click `setup.bat` once, then double-click `run.bat`. The WebUI opens at
`http://127.0.0.1:8000`.

Before cloning your own recording for the first time, double-click
`enable-voice-cloning.bat`. Accept Kyutai's model conditions on Hugging Face, create a
free read token, and paste it into the secure Hugging Face login prompt. This is an
upstream requirement for the full voice encoder; the base synthesis model works without
an account.

All Pocket TTS model weights are stored visibly inside this project's `models/` folder.
Later runs reuse those local files and do not use a user-profile model cache. Uploaded
source recordings are converted with the bundled FFmpeg binary,
then deleted after the reusable `.safetensors` voice state is saved under `voices/`.

## Best voice samples

- Use 15–30 seconds of connected, conversational speech from one speaker.
- Do not use the alphabet, isolated words, or deliberately slow spelling; those prompts
  teach robotic cadence instead of normal speech prosody.
- Avoid music, echo, clipping, room noise, and other voices.
- Use the same language as the selected model.
- Use **Natural** for ordinary recordings, **Already cleaned** for externally denoised
  files, and **Noisy room** only when the original truly contains steady background noise.
- Only clone a voice you own or have explicit permission to use.

## Hardware

Pocket TTS officially targets CPU execution and usually uses about two CPU cores. The
launcher sets conservative thread limits to avoid wasting the hybrid CPU's efficiency
cores. Intel Arc/XPU is not enabled because Pocket TTS does not officially expose an XPU
path, and the upstream project notes that GPU execution often does not help this small,
batch-size-one model. Try INT8 in Generation settings if it improves speed on your CPU;
leave it off if it does not.
