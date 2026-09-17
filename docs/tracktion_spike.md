# Tracktion Engine Spike

Date: 2026-07-06

## Workspace

- Project folder: `C:\Users\UserName\Desktop\cover-recorder`
- Spike checkout: `C:\Users\UserName\Desktop\cover-recorder\.spikes\tracktion_engine`
- `.spikes/` is ignored by git.

## Local Toolchain

- Git is available.
- CMake is not on PATH, but Visual Studio Build Tools provides CMake here:
  `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe`
- MSVC compiler is available through Visual Studio 2022 Build Tools.

## Clone

Tracktion Engine's JUCE submodule may use an SSH-style GitHub URL. On this machine, cloning with an HTTPS rewrite avoids SSH host-key setup:

```powershell
git -c url.https://github.com/.insteadOf=git@github.com: clone --depth 1 --branch develop --recurse-submodules --shallow-submodules https://github.com/Tracktion/tracktion_engine.git .spikes\tracktion_engine
```

## Configure

```powershell
& 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe' --preset windows
```

Generated build tree:

```text
C:\Users\UserName\Desktop\cover-recorder\.spikes\tracktion_engine\cmake-build\Windows\cmake-windows
```

## Build Notes

On Chinese Windows code page 936, MSVC reports `C4819` for several Tracktion/JUCE source files. The Tracktion example project treats warnings as errors, so the first build failed with `C2220`.

Use `CL=/utf-8` when building:

```powershell
$env:CL='/utf-8'
& 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe' --build --preset windows-Release --target TestRunner
```

```powershell
$env:CL='/utf-8'
& 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe' --build --preset windows-Release --target DemoRunner
```

## Results

Built successfully:

```text
C:\Users\UserName\Desktop\cover-recorder\.spikes\tracktion_engine\cmake-build\Windows\cmake-windows\examples\TestRunner\TestRunner_artefacts\Release\TestRunner.exe
C:\Users\UserName\Desktop\cover-recorder\.spikes\tracktion_engine\cmake-build\Windows\cmake-windows\examples\DemoRunner\DemoRunner_artefacts\Release\DemoRunner.exe
```

`TestRunner.exe` ran, but the full official suite ended with one failing doctest:

```text
tracktion_EditClip.test.cpp:103
CHECK( getRMSLevel(...) == Approx(0.0f).epsilon(0.03) )
actual RMS: 0.711176

doctest: 53 test cases, 52 passed, 1 failed
assertions: 1024, 1023 passed, 1 failed
```

This does not block using the engine for a product spike, but it means we should not treat the full upstream suite as clean on this machine without investigating that edit-clip rendering test.

## Useful Entry Points

Tracktion recording demo:

```text
C:\Users\UserName\Desktop\cover-recorder\.spikes\tracktion_engine\examples\DemoRunner\demos\RecordingDemo.h
```

Key behavior in that demo:

- Creates or loads a `.tracktionedit`.
- Enables wave input devices.
- Creates audio tracks and assigns inputs.
- Arms recording with `setRecordingEnabled`.
- Starts and stops recording through `EngineHelpers::toggleRecord`.
- Saves the edit after recording stops.

Audio-file import helper:

```text
C:\Users\UserName\Desktop\cover-recorder\.spikes\tracktion_engine\examples\common\Utilities.h
```

Relevant helper:

```cpp
EngineHelpers::loadAudioFileAsClip(edit, file)
```

Render example:

```text
C:\Users\UserName\Desktop\cover-recorder\.spikes\tracktion_engine\modules\tracktion_engine\model\export\tracktion_Renderer.test.cpp
```

Relevant API shape:

```cpp
Renderer::Parameters params(edit);
params.destFile = destFile;
params.time = params.time.withLength(fileLength);
params.audioFormat = engine.getAudioFileFormatManager().getWavFormat();
EditRenderer::render(std::move(params), callback, thumbnail);
```

## Product Direction From This Spike

Tracktion Engine is a plausible base for the one-stop cover recorder because it already covers the hard audio parts: audio devices, transport, recording, tracks, clips, plugins, waveform UI, and render-to-audio. We would still build our own simplified UI and keep FFmpeg for the final video mux/export path.

Next technical spike:

1. Fork a minimal app target from `DemoRunner`.
2. Keep only: backing-track import, one armed input track, play/record/stop, rendered mixdown WAV.
3. Add FFmpeg step to combine rendered WAV with a chosen video and trim to backing-track length.
