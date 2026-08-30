# Avatar Run 013 - Real GPU Response-Loss Evidence

Run 013 is a controlled recovery experiment executed on the local RTX 4060
against a real CUDA-backed MuseTalk v1.5 service. The fault proxy forwarded the
first inference to completion and then closed the client connection before the
successful HTTP response could be delivered.

## Result

- The first request completed one real GPU inference.
- The first successful response was deliberately dropped.
- The client classified the disconnect as transient and issued one bounded retry.
- The retry reused the same run identity and returned `completed_replay`.
- Service logs contain one request start and one inference completion.
- Both proxy observations have the same generated-video SHA-256.
- The LivePortrait handoff SHA-256 remained unchanged.

The complete recovery took 164.8695 seconds. The service reported 163.829
seconds for the owner inference, while the remaining retry path completed in
less than 0.827 seconds of the total wall-clock budget. The output is a
15-second, 1024x1024, 25 FPS H.264/AAC video.

## Evidence boundary

This run proves that a completed MuseTalk GPU result is not generated twice
when its HTTP response is lost. It does not prove recovery from worker process
death, deleted output storage, CUDA/OOM, model failure, or arbitrary network
partitions. This was a reliability-only run: technical media probing passed,
but identity and lip-sync quality were not reassessed.

All real-person media, generated video, raw reports, proxy state, service logs,
and debug sidecars remain private and uncommitted. The committed JSON contains
only sanitized metrics and SHA-256 bindings.
