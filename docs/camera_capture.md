# 摄像头与参考声音采集

本轮实现的是应用内模拟手机拍摄：摄像头与参考麦克风保存一份带现场声音的视频，Tracktion/Focusrite 同时保存独立录音；Stop 后等两路文件写完，渲染当前混音，尝试对齐并生成 MP4。

2026-09-18：自动分析已升级为分频带频谱候选 + 包络交叉检查 + 独立分段复核；只有强匹配且复核通过才直接自动合成，其他结果仍弹出手动偏移窗口。此改动在 Python 后端生效，无需重编译原生 exe。见 [接入与验证记录](spectral_alignment_integration.md)。

## 使用

1. 连接摄像头和参考麦克风。参考麦克风可以是摄像头自带麦克风或另一只现场麦克风，建议与 Focusrite 乐器输入使用不同设备。
2. 原生应用选择 `More > Camera + reference sound...`，打开随应用启动的采集面板。
3. 分别选择摄像头、参考麦克风，点击“预览并准备录制”。不选择参考麦克风不能进入准备状态；预览阶段只打开摄像头。
4. 画面就绪后，回主窗口点击 Record。此时先启动带参考音的视频录制，得到画面后再启动 Focusrite 和伴奏。等主窗口显示 Recording 后演奏。摄像头录制从工程 0 秒开始，包含当前工程中参与播放的轨道。
5. 主窗口 Stop 同时请求视频停止并保存音频；视频编码器完成收尾后，应用自动渲染混音并对齐、合成。按 Space 停止录音也经过同一流程。
6. 自动对齐只有 high 可靠性才直接导出；medium/low 或分析失败会保留素材，弹出手动偏移对话框。候选偏移需人工确认；失败不会显示成品已导出。

首版没有手机应用或无线传输协议。iPhone 只有在桥接软件向 Windows 提供兼容的摄像头、参考麦克风设备时，才可能复用此入口，尚未实测。可以继续使用原来的手机录像导入方式。

## 文件与状态

- 每次打开采集面板在当前工程目录创建独立 `camera-<UUID>` 子目录。
- 录制中写 `recording/capture.partial.mkv`，停止并检查包含视频和音轨后发布为 `recording/capture.mkv`。视频是 H.264，参考声音是 AAC；画面按比例缩放到不超过 1280×720 的框内，实际采集帧率由设备默认配置决定。
- 原生 WAV、混音 WAV、带参考音视频都保留，最终 MP4 只使用混音音轨。摄像头原声用于对齐，不再次叠入成品。
- 每段采集有独立日志、预览 JPEG 和状态文件。合成失败可以重试，无需重新演奏；视频路径和候选偏移写入工程。
- Discard Take 继续走原来的音频丢弃逻辑；摄像头视频停止后不关联、不合成，但保留文件，便于恢复。
- 正在准备录制或收尾时锁定相关编辑；录像期间不可从采集面板直接关闭，需在主窗口 Stop/Discard。工程切换要求先关闭采集面板。
- 摄像头打开无画面、采集中长时间不更新、参考音轨缺失、FFmpeg 退出或停止超时均报错。已录的 Focusrite 音频会停止并保留，不因视频失败删除。
- 主窗口正常退出会先关闭预览；采集进程检测不到主窗口心跳时尝试停止并保存。异常中断可能留下 `.partial.mkv`，不把这种情况宣称为完整录制成功。

## 实现

`src/cover_syncer/capture.py` 负责设备枚举、单个 DirectShow 音视频输入、FFmpeg 生命周期及文件验证；同一输入分出文件录像和预览，避免同时用两个采集进程争用摄像头。预览切换到录制时先关闭预览进程，再打开带参考麦克风的录制进程。

`capture_window.py` 使用现有 QtWidgets 显示画面和设备选择，不依赖当前环境无法导入的 QtMultimedia。`CameraCaptureProcess.h` 用本地控制/状态文件与面板通信，主窗口通过定时器等待准备和保存状态。`native_export.py` 复用已有自动对齐和手动导出逻辑，没有新写同步算法。

## 验证与边界

已执行生成素材检查：同时录入画面和参考音、JPEG 预览、文件尚未写完时禁止合成、重复 Stop、缺少音轨拒绝成功、已存在目录保护；采集面板用离屏 Qt 检查强制选择参考麦克风、准备/启动/录制/停止/丢弃和录制时关闭阻止。

另用已知 300 ms 起点差的生成演奏，实际经过 FFmpeg 采集、原声提取、真实自动对齐和 MP4 合成；检查偏移误差不超过 40 ms，输出可听标记位置符合预期。低/中可靠性及分析异常的手动退路另有测试。未把模拟状态测试等同于真实设备兼容性测试。

2026-09-16 用户接入 USB 摄像头后，Windows 将其枚举为 `icspring camera`，自带参考麦克风为 `麦克风 (icspring camera)`。已用实际采集后端完成短录制及正常停止，保存 `tmp/camera-hardware/20260916-233342/capture.mkv`：H.264、1280×720、30 fps；AAC、48 kHz、双声道，解码参考音长约 5.01 秒、峰值 0.0367、RMS 0.00616，包含非静音声音。检查结果写在同目录 `result.json`；没有查看或发布私人画面。

实机测试发现并修复了 DirectShow 设备名多加引号导致无法打开的问题：向 subprocess 参数列表传入完整原始选择器，空格和中文由进程启动层处理。接口格式参考 [FFmpeg DirectShow 文档](https://ffmpeg.org/ffmpeg-devices.html#dshow)。名称包含冒号时明确报错，避免错误拆分选择器。

随后用户完成主窗口实机联动：摄像头与参考麦克风采集状态为 complete，工程 `20260916_234207` 于 23:43:04 生成 `cover.mp4`（33,201,449 字节）。用户查看成品后反馈“完全正确”，本次摄像头与 Focusrite 录制、Stop 保存及对齐合成的主流程验收通过。原始带声音视频保留在工程的 `camera-45c6c6292bb54ed0a58ad92b6d9738d1/recording/capture.mkv`。

设备占用/拔插、其他型号兼容性和长时间漂移尚未实测；本次验收不扩大到这些边界。本轮没有使用 Computer Use。

最新完整回归为 **68 passed in 26.86s**（没有跳过），使用 `tmp/camera-release/Cover Recorder.exe`。此前一次回归停在 FFprobe 调用，已终止该测试进程；媒体探测增加 60 秒超时，转码/导出增加 1200 秒超时，超时后报错并保留原始素材。

随后修复直接双击 exe 时采集面板启动失败：自动对齐模块曾在运行环境配置之前导入 SoundFile，缺少 Conda PATH 时找不到 `libsndfile.dll`。现在 `runtime.py` 在导入原生依赖之前设置当前 Python 环境的 Library/bin 和 Windows DLL 搜索目录；采集入口不再为启动面板而导入对齐模块。另修复准备前关闭面板未发布 closed 状态的问题。针对本次改动，采集与原生导出 **17 项测试全部通过（10.04 秒）**，包含清除 Conda 环境、仅保留 Windows 系统 PATH 后启动真实辅助面板及原生导出的子进程检查；无需重编译 exe。报告：`tmp/native-recording-check/desktop-launch-results.xml`。

回归命令与记录：

```powershell
$env:COVER_RECORDER_TEST_EXE = (Resolve-Path 'native/cover_recorder/build/windows-vs2026/CoverRecorderNative_artefacts/Release/Cover Recorder.exe').Path
$env:QT_QPA_PLATFORM = 'offscreen'
conda run --no-capture-output -n cover-syncer python -m pytest -q -ra
```

报告：`tmp/native-recording-check/camera-results.xml`。构建：`camera-release-build.log`。这些都是本机记录，不提交私人录音或视频。
