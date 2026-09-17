# 本机环境与运行基线

历史记录：本文描述首次环境配置，不代表项目收尾状态。最新结论见 [MVP 验收总结](mvp_status.md)。私人用户名已替换为占位符。

检查日期：2026-09-16（America/New_York）。本次仅配置环境、构建和确认运行基线，没有修改应用源码或实现手动对齐功能。

本文保留环境配置阶段的历史基线；后续手动对齐功能和 34 项测试结果见 [手动对齐开发记录](manual_alignment.md)。
后续 Focusrite 实录、用户回放确认和 WAV 混音测量见 [原生录音验收记录](native_recording_validation.md)，下方“未验证”描述仅代表环境配置阶段。

## 环境

项目：`C:\Users\<username>\Desktop\cover-recorder`。

未发现项目目录及其父目录中适用的 `AGENTS.md`。Git 中主要项目文件仍是未跟踪状态；本次保留已有文件和旧构建产物，没有执行清理、重置或提交。

通过现有 Miniconda 的 `environment.yml` 创建独立环境 `cover-syncer`，未升级 base 或其他环境。

| 组件 | 本机实测版本或位置 |
| --- | --- |
| 环境目录 | `C:\Users\<username>\miniconda3\envs\cover-syncer` |
| Python | 3.10.21 |
| PySide6 / Qt | 6.11.2 |
| NumPy | 2.2.6 |
| SciPy | 1.15.2 |
| SoundFile | 0.14.0 |
| pytest | 9.1.1 |
| FFmpeg / ffprobe | conda 环境的 `Library\bin`，FFmpeg 9.0.1 |
| Visual Studio | Community 2026，18.9.2 |
| CMake | 4.3.1-msvc1，使用 Visual Studio 自带版本 |
| MSVC | 编译器 19.51.36256.0，工具集目录 14.51.36231 |
| Tracktion Engine | 3.2.0，commit `2877b621f2fbee564d0696a616b86bf8ba8c8ab0` |
| JUCE | 8.0.12，submodule commit `7c89e11f6b7316c369f3d3f22227c60e816e738b` |

Tracktion checkout 工作区干净，JUCE 子模块已存在。没有更新第三方引擎。

默认 shell 中 `python` 指向 WindowsApps 占位程序，FFmpeg 和 CMake 也不在全局 PATH。请使用 README 中的 `conda run` 和 CMake 完整路径，不需要修改全局 PATH。

## 已验证

1. 环境创建完成，项目以 editable 模式安装；`python -m pip check` 报告 `No broken requirements found`。
2. 执行 `conda run --no-capture-output -n cover-syncer python -m pytest -v -ra`：**8 passed in 1.40s，0 failed，0 skipped**。包含 FFmpeg 实际导出测试。当前导出测试检查音视频流数量，不证明音轨内容及同步质量。
3. Python GUI 实际启动，窗口标题为“Cover 自动对轨器”，界面和初始控件正常显示，进程响应正常，启动 stderr 为空。没有通过 GUI 执行自动分析或导出。
4. 使用独立目录 `native\cover_recorder\build\windows-vs2026` 完成 CMake 配置和 Release 编译，退出码均为 0。没有复用或删除旧 `build\windows` 缓存。
5. 新构建的 `Cover Recorder.exe` 实际启动，窗口及进程响应正常，创建了 `C:\Users\<username>\Documents\CoverRecorderSessions\20260916_204516\session.tracktionedit`（1817 字节）。首次窗口发现超时后，通过重新枚举找到了已启动的窗口。

## 验收边界与待办

- 原生窗口随后处于最小化状态。自动化恢复时连续返回 `user input was detected in this window; call get_window_state before continuing`，重新读取又返回 `window is minimized`；停止继续操作该窗口。因此本次没有完成原生音频导入、播放、WAV 导出的 UI 验证。
- 没有开始麦克风/声卡录音，没有验证输入设备、延迟、监听、停止保留/丢弃 take 或真实 cover 工作流。
- 没有验证 Python 工作线程生命周期、GUI 分析/导出流程、无参考音流程和真实素材音画同步。纯手动导出仍是后续功能任务。
- 没有运行上游 Tracktion TestRunner；历史文档中的结果不代表本次结果。

当前结论：Python 开发和测试环境可用，原生源码可重新构建且应用可启动；完整录音与音视频工作流仍待验收。

## 复现与记录

从项目根目录执行 README 的环境、构建、运行、测试命令。不要同时启动多个 `conda run` 检查：本次并行调用曾触发 Conda 临时文件占用错误，顺序重试版本检查后成功。

- 原生构建日志：`native\cover_recorder\build\windows-vs2026\build.log`。
- Python 启动日志：`tmp\environment-check\python.stdout.log`、`python.stderr.log`。
- 本次 Conda 显式包清单：`tmp\environment-check\conda-win64-explicit.txt`；pip 清单：`tmp\environment-check\pip-freeze.txt`。这些是本机快照，未把未锁定的 environment.yml 描述为精确版本锁。
- 已生成 3 秒、48 kHz 的低音量测试音：`tmp\environment-check\test-tone.wav`，可用于下一步原生导入与导出检查；本次尚未导入原生应用。

AI 参与：检查环境和源码入口、创建隔离依赖环境、执行原有测试、编译并启动应用、记录证据及更新过时命令。用户本次决策：先配置环境并确认运行状态。未替用户记录功能验收或真实试听结论。
