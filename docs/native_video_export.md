# 原生视频导出

2026-09-16：原生录音器新增 `More > Export video...`，在后台调用现有 Python/FFmpeg 导出模块。没有使用 Computer Use 验证本轮改动。

此文保留文件视频导出阶段的验证记录。后续摄像头自动对齐及实机成品验收已完成，最新结论见 [MVP 验收总结](mvp_status.md) 和 [摄像头采集](camera_capture.md)。

## 使用

1. 录音完成后调整轨道音量。需要旧工程时先 `Ctrl+O` 打开。
2. `More > Export video...` 选择同一次连续演奏的手机视频。
3. 填写音频偏移毫秒数：正值延后混音，负值裁去混音开头。
4. 点 Export。应用先渲染当前混音快照，再后台编码；编码期间暂时锁定编辑和正常关闭。
5. 完成后状态栏显示工程目录中的 `cover.mp4` 路径。已有文件保留，新导出自动使用后缀。用播放器回看，不合适可重新填写偏移导出。

保持原视频时长；混音不足补静音，超出则截断。未实现按伴奏裁视频。视频路径和偏移写入工程；重新选择视频时会显示上次保存值，应按所选素材核对。

## 格式

两个界面的文件选择器均支持 MP4、MOV、M4V、MKV、AVI、WMV、WebM、MPG/MPEG、MTS/M2TS/TS、FLV、3GP。

容器扩展名不代表内部编码。兼容的 8-bit H.264 画面直接复制；HEVC、MPEG-4、WMV、AV1 等其他可解码的 SDR 输入转为 H.264/yuv420p，输出音频统一 AAC。奇数宽高转码时在右边或底边补一个像素；原始文件不修改。

格式集成测试实际生成并导出 15 组容器/编码：MP4/H.264、MOV/H.264、MOV/HEVC、M4V/H.264、MKV/FFV1、AVI/MPEG-4、WMV/WMV2、WebM/AV1、MPG/MPEG-2、MPEG/MPEG-2、MTS/H.264、M2TS/H.264、TS/H.264、FLV/FLV1、3GP/MPEG-4。检查输出 H.264/AAC、帧数、时长、完整解码和原文件哈希。WebM 的 VP8/VP9 解码器在本机存在，但未把 AV1 测试描述为所有 WebM 编码实测。

MPEG-PS 实测发现末帧时间戳缺失、文件头时长少一帧，已改为读取解码帧时间和帧持续时间，补算缺失时间戳，避免截掉末帧。

HDR（PQ/HLG）会明确提示先转 SDR，避免错误的亮度与颜色转换。文件导入入口未实现 HDR 色调映射、导入视频预览或自动对齐；摄像头入口另有预览和自动对齐流程。损坏文件、缺少解码器等情况报告导出失败。

## 环境与验证

默认使用用户目录下 `miniconda3/envs/cover-syncer/python.exe`；若环境位于其他位置，设置 `COVER_RECORDER_PYTHON` 指向其 `python.exe`。Python 项目需按 README 安装；原生启动的 Python 会查找自身环境里的 FFmpeg，无需先打开 Conda 终端。

Native `VideoExportProcess.h` 用参数数组启动 `python -m cover_syncer.native_export`，通过 UTF-8 JSON 文件传递视频、WAV、输出和偏移。错误返回 JSON；覆盖权限始终为 false。原生定时器读取结果、恢复编辑，失败时显示完整提示。正常关闭会等待导出完成；尚未提供取消导出。

`tests/test_native_export.py` 实际调用编译出的 C++ 可执行文件，再由 C++ 启动 Python/FFmpeg，验证中文及空格路径、-250/0/+250 ms 三个偏移、可听标记位置、原画面帧数、源文件保护、已有输出拒绝覆盖。`tests/test_video_formats.py` 验证格式兼容与 HDR 明确拒绝。

运行全部 Python 与原生进程集成测试：

```powershell
$env:COVER_RECORDER_TEST_EXE = (Resolve-Path 'native/cover_recorder/build/windows-vs2026/CoverRecorderNative_artefacts/Release/Cover Recorder.exe').Path
conda run --no-capture-output -n cover-syncer python -m pytest -q -ra
conda run --no-capture-output -n cover-syncer python tests/verify_native_session.py --exe $env:COVER_RECORDER_TEST_EXE
```

不设置测试 EXE 时，三个原生进程集成用例会跳过，不能把它们算作通过。本轮设置测试 EXE 后 **56 passed in 24.94s，无跳过**。完整结果保存在 `tmp/native-recording-check/all-results.xml`；构建日志在同目录 `release-build.log`。合成和实录工程恢复另由原生检查程序验证通过。

回归时曾出现独立 Python 界面的线程清理阶段崩溃。已将 UI 完成/错误槽显式排队到 GUI 线程，在线程结束后等待原生清理完成，再销毁对象，并新增 20 轮分析和导出失败的清理检查。此前还出现一次 FFprobe JSON 末尾缺失，后续专项及完整重跑未复现；未把重跑成功当作该环境偶发问题的根因已定位。

尚待人工验收：新版原生菜单和偏移弹窗、导出期间的交互状态，以及用户正式 cover 成品。既有实录试听和既有视频对齐的用户确认仍分别有效，不能代替这几个新验收项。
