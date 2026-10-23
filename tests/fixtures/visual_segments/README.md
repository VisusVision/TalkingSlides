# Private segment engineering fixtures

These are **synthetic engineering attestations**, not production producer receipts.
They certify no historical lesson, user voice or legacy audio file. The pure tests
explicitly pass `allow_synthetic=True`; the default rejects these attestations.

`transcript.txt` contains the exact original educational sentence:
“Plants need light and water to grow.” The final newline is a text-file delimiter,
not part of the narrated input. `narration.wav` is genuine synthetic spoken English,
created offline with the installed CMU Flite SLT voice. It is not a tone or silence.
`narration.m4a` is its AAC-LC rendition: 44,100 Hz, mono, 15,501 bytes, measured
2.335011 seconds (the contract uses 2,335 ms). No application TTS API, provider,
network, downloaded model or GPU was used. Creating this fixture is a one-time
engineering operation; tests only read its bytes and metadata.

The sentence and generated test recordings are dedicated to the public domain
under CC0-1.0, to the extent permitted by law. No teacher recording or cloned voice
was used. Flite attribution and permissive license are in `FLITE_NOTICE.txt`.
`producer_metadata.json` records tool/library digests and PCM properties. This is
an explicit fixture author's attestation, not an automatic speech recognizer's
claim. Energy/duration checks alone do not establish narration correctness.

## Reproduce the audio

With the recorded FFmpeg 6.1.1-3ubuntu5 and Flite/SLT library identities, run in a
temporary directory (never overwrite checked-in fixtures during a test):

```sh
ffmpeg -nostdin -hide_banner -loglevel error -f lavfi \
  -i "flite=text='Plants need light and water to grow.':voice=slt" \
  -c:a pcm_s16le -fflags +bitexact -flags:a +bitexact -map_metadata -1 narration.wav
ffmpeg -nostdin -hide_banner -loglevel error -i narration.wav \
  -ar 44100 -ac 1 -c:a aac -profile:a aac_low -b:a 48k \
  -fflags +bitexact -flags:a +bitexact -map_metadata -1 narration.m4a
ffprobe -v error -show_streams -show_format -of json narration.m4a
ffmpeg -nostdin -hide_banner -loglevel error -i narration.m4a \
  -map 0:a:0 -c copy -f data packets.bin
```

`packet_payload_hash` hashes the concatenated encoded AAC packet payloads in
`packets.bin`, excluding container headers. It allows the future segment validator
to check stream-copy preservation even when the output MP4 container differs.
WAV speech activity is measured using absolute signed 16-bit sample amplitude
above 100: first/last active samples converted to integer milliseconds give
243–2180 ms. This is a bounded activity window, not word-level forced alignment.
Different native builds may generate different bytes. Tests use the committed
bytes and independent expected digests; they do not synthesize replacements.

## JSON fixtures

- `snapshot.json`: a supplementary test's **unchanged Phase 3A** snapshot. It has
  real deterministic flowchart, comparison and 12/18 cm chart plan metadata.
  Source indices `[0,2,0]`, splits `[1,0,0]`, orders `[0,1,2]`, three distinct keys.
- `audio_proof_0..2.json`: the same real speech bytes explicitly associated with
  each synthetic test page and an identified synthetic baseline producer.
- `request_0..2.json`: complete one-page execution contracts bound to that snapshot.
- `receipt_0..2.json`: **schema-only hypothetical outputs**. There are no MP4, SRT
  or VTT outputs. Output digests and measured stream claims are synthetic test
  values, prominently marked by `synthetic_receipt_no_output_files`. Passing a
  receipt schema test is not media acceptance. The frame reference identifies the
  existing Phase 3A documentation flowchart PNG; no segment was composed from it.
- `trust_anchors.json`: independent golden snapshot/proof/request/receipt hashes,
  trusted producer ID and a **test-only**, deliberately synthetic supported runtime
  identity. It is not a deployed rasterizer/runtime attestation or trust registry.
- `mapping.json`: explicit order/source/split association expectations.

Do not recalculate an expected hash from an untrusted payload and call it trusted.
Only deliberate fixture-author review may update these golden values. Later live
stages must use independently authenticated producer records, verified storage
and supported deployed runtime identities, never this fixture trust configuration.
