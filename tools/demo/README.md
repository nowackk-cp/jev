# Public office demo

The demo reuses `jev/office/static` and replaces its HTTP/SSE transport **only in the generated copy**. It runs deterministic example scenarios entirely in the browser. It never starts a model, invokes a CLI, writes project files or needs an API key.

```powershell
python tools/demo/build.py
python -m http.server 8088 --directory docs
```

Open `http://localhost:8088`. GitHub Pages serves `docs/` from the `main` branch. The committed generated files let Pages publish without dependencies or a build workflow. Run the builder again after office UI changes.

- `parallel`: independent tasks and their dependent CLI task.
- `retry`: failed verification, a Jev decision and another attempt.
- `mini`: a single worker without a planning phase.

Playback can be paused, restarted or accelerated. Task cards, agent details, plan, decision feed and report use the real office renderers. A sample correction request demonstrates the second report round.

Optional query parameters: `scenario=parallel|retry|mini`, `speed=1|2|4`, `step=<frame>`, `paused=1`. Initial frames support repeatable screenshots and smoke checks.

The builder asserts the office transport entry points before rewriting them, so a future incompatible client change fails visibly instead of shipping a broken demo. Labels and agent metadata come from the repository's Python constants and default configuration.
