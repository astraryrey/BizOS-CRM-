# Call assist by voice: what is done, what to assemble

## The pipeline, in four parts

```
[1] AUDIO FROM THE CALL   Asterisk AudioSocket -> raw audio
        ↓
[2] SPEECH RECOGNITION    Vosk / Whisper -> text
        ↓
[3] PROMPT ENGINE         DONE (app/intents.py) -> a templated line
        ↓
[4] VOICE IN THE EARPIECE Piper -> audio -> ChanSpy whisper
```

**Part 3 is written and working.** Parts 1, 2 and 4 are off-the-shelf
components to be wired together; nothing needs inventing.

## What already exists

`POST /api/assist/utterance` takes the **text** of an utterance and
returns:

```json
{"speak": "1200 hryvnia", "screen": "Seasonal tyre change - 1200 UAH, 60 min"}
```

The module has no idea where the text came from, which is why the whole
engine is testable **without a byte of audio** - you type the lines.

### Nothing is invented

A prompt is an intent matched on keywords, plus a real fact from the
database, poured into a fixed template. No language model is involved:
what reaches the ear is known in advance.

Spoken-language synonyms live in the YAML:

```yaml
service_synonyms:
  "Seasonal tyre change": [tyre, tire, wheels, winter tyre, swap my t, rims]
```

### Dialogue memory and negation

"Anything later?" produces the **next** slot, not the same one again.
"That doesn't work" is not agreement - negation is handled separately,
which was a real bug caught on the first run-through.

## The key insight: the "one moment" signal

The most underestimated problem is **two voices in one ear**. If the
synthesiser speaks while the customer is speaking, the operator follows
neither.

The answer is already in the conversation: the operator says
**"one moment"** and the customer goes quiet and waits. That is the only
safe window for the voice.

So the system:
* while the customer talks, **prepares** the answer and shows it on the
  screen, silently;
* when the operator says "one moment", it **speaks** into the earpiece.

That pause also covers the 1.5-3 second technical delay.

## What has to be assembled

### [1] Audio from Asterisk
**AudioSocket**, built in since version 16: TCP, raw 8 kHz PCM. You need
**two separate streams** - caller and operator - or the system cannot
tell who said "one moment".

### [2] Speech recognition

| Option | Upside | Downside |
|---|---|---|
| **Vosk** | true streaming, offline, free, many languages | lower accuracy |
| Whisper | best accuracy | not streaming by nature, higher latency |
| Cloud services | accuracy plus streaming | around $0.02 per minute |

Always feed the recogniser a **vocabulary**: service names, vehicle
makes, staff names. Without it "Audi A3" comes back as noise.

### [4] Synthesis and delivery to one ear
**Piper** or **Silero** for synthesis. **ChanSpy with the `w` (whisper)
flag** is the Asterisk feature that mixes audio so only one side hears
it - originally built for supervisor coaching, which is exactly this.

## Latency budget

| Stage | Time |
|---|---|
| audio buffer + end-of-phrase detection | 200-500 ms |
| recognition | 300-1500 ms |
| prompt engine plus database | 10-50 ms |
| speech synthesis | 100-300 ms |
| audio delivery | 100-200 ms |
| **total** | **~1.5-3 s** |

## Things to know up front

**There is no ready-made "call assist for Asterisk" repository.** There
are building blocks, assembled per project. The examples online build a
fully automated robot operator, not a prompter for a human - different
logic entirely.

**Telephone audio is 8 kHz**, half the quality the models were trained
on. Accuracy drops, particularly on names, makes and numbers.

**Mixed-language speech** breaks models trained on a single language.

**Consent to processing.** Speech recognition means processing personal
data. It needs an announcement at the start of the call and a clause in
the contract.

## Order of work

1. **Done:** the prompt engine, testable by typing text.
2. Build a vocabulary from real call recordings, extend
   `service_synonyms`.
3. Wire in Vosk and feed the text to `/api/assist/utterance`. Prompts
   **on screen only** at this stage - already useful.
4. Run it against live calls and measure accuracy.
5. Only then Piper and the voice in the ear.

Step 3 delivers most of the value. Step 5 carries the risk: if
recognition is wrong, the operator hears the wrong price.
