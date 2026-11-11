# What to download, and in what order

Everything here is free and open source. **You do not need all of it.**

## Telephony
* Asterisk - https://www.asterisk.org/ · docs https://docs.asterisk.org/
  **Version 16+ required** (check with `asterisk -V`)
* FreePBX (web front end, installed INSTEAD of plain Asterisk) -
  https://www.freepbx.org/
* Softphones for testing: Linphone https://www.linphone.org/ ·
  Zoiper https://www.zoiper.com/

## Audio out of a call
* AudioSocket - built into Asterisk, nothing to install:
  https://docs.asterisk.org/Configuration/Channel-Drivers/AudioSocket/
* Ready-made receivers to start from:
  https://github.com/silentindark/audiosocket_server ·
  https://github.com/nadirhamid/asterisk-audiofork

## Speech recognition
* **Vosk** - https://github.com/alphacep/vosk-api ·
  models https://alphacephei.com/vosk/models
  Install with `pip install vosk`
* Ukrainian models - https://github.com/egorsmkv/speech-recognition-uk
* Whisper, if Vosk is not accurate enough:
  https://github.com/SYSTRAN/faster-whisper ·
  https://github.com/ggml-org/whisper.cpp

## Speech synthesis
* **Piper - the repository has moved:**
  https://github.com/OHF-Voice/piper1-gpl
  (the old one was archived in October 2025)
* Voices: https://huggingface.co/rhasspy/piper-voices
* Listen to samples: https://rhasspy.github.io/piper-samples/

  **Warning:** Piper has known complaints about its Russian
  pronunciation. Listen to the samples before building on it.
* **Silero TTS** (fallback, stronger on Slavic languages) -
  https://github.com/snakers4/silero-models

## Installation order

**None of this is required for the current BizOS to run.**

1. Find out what the call centre already runs - Asterisk may be there.
2. A softphone, so you can call yourself.
3. Asterisk locally in Docker (`telephony/`) - a sandbox.
4. The caller-ID webhook - **already in the code**; no STT or TTS needed.
5. Vosk, with prompts on screen only.
6. Piper or Silero, the earpiece voice, last.

Steps 1-4 give a working product.

## Costs

Everything listed is free. You pay for a server, an SMS gateway, and
cloud speech recognition **if** Vosk is not accurate enough (around
$0.02 per minute of conversation).
