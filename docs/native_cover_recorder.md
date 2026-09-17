# Native Cover Recorder MVP

这是基于 Tracktion Engine/JUCE 的本机录音轨道原型，用来验证“一站式 cover 录制器”的 DAW 核心体验。

## 当前能力

- 启动后自动创建一个 session：`Documents\CoverRecorderSessions\<timestamp>`。
- `More > Save session`（`Ctrl+S`）和 `More > Open session...`（`Ctrl+O`）。打开前先保存当前工程；录音中禁止切换。保存、销毁、读回及重渲染已通过程序化检查，菜单和弹窗仍待人工验收，详见 [验证记录](native_recording_validation.md)。
- `Import Backing` 把伴奏音频导入到 `Backing` 轨。
- 内置 Tracktion 轨道视图，显示轨道、片段、录音中的片段和波形；示例轨道头里的 `I/A/M/S` 小按钮默认隐藏。
- 默认创建 `Take 1` 录音轨，并自动把第一个可用 wave input 分配到该轨，让它 ready to record。
- `Track` 下拉框选择当前录音轨；`Input` 下拉框选择这条轨道接哪一个声卡输入；`Add Track` 新增录音轨，支持多轨录音。
- 多条轨道分别选择输入并勾选 `Record this track` 后，`Track` 下拉框会标出 `Ready/Off`；切换轨道时状态区会直接说明 `Record` 是否包含这条轨，并显示 `Ready tracks` 数量；只想录当前轨时可用 `Record only this Track`。
- 主界面只常驻 cover 录制高频动作；回到开头、undo/redo、缩放和打开 session 文件夹集中放在 `More`，避免按钮墙。
- 主按钮、轨道选择、输入选择、光标和音量/增益滑块都有悬停短提示；界面不额外堆说明文字，但第一次使用时能快速确认每个控件的作用。
- `More > Import audio to current track` 可把现有音频插入当前录音轨的光标位置，方便在没有真实声卡录音时验证多轨波形、裁剪、增益和导出。
- `Record this track` 用直白勾选项替代 DAW 里的 arm 术语；未手动选录音轨时默认作用到 `Take 1`。
- `Hear input` 可切换当前/默认录音轨的输入监听，方便确认声卡输入是否真的接通。
- `Input` 电平条显示当前绑定输入的峰值，帮助判断是否有声、是否过载。
- 选中信息会显示当前轨道输入是否连接、是否 ready、是否正在 hear input，减少录音前的猜测。
- `Track Edit` / `Clip Edit` 菜单集中放置轨道和片段相关动作，避免主界面常驻一排低频编辑按钮。
- `Track Edit` / `Clip Edit` 菜单里可重命名当前轨、重置当前轨音量，也可对当前轨做 mute/solo，还可清除所有 mute/solo；`Track` 下拉框和状态区会显示自定义轨名、`Muted`、`Solo` 或 `Listen: normal`。
- `Record` 从当前光标位置开始录音，不再强制从 0 秒开始；如果没有选中录音轨，会默认使用 `Take 1`，并自动分配输入和 arm。
- `Stop` 停止并保留当前录音；`Discard Take` 停止并丢弃当前正在录的 take。若正在用替换式重录，丢弃时会恢复原内容。
- 光标滑块支持毫秒级定位。
- `More > Zoom In`、`More > Zoom Out`、`More > Fit view` 支持围绕当前光标缩放时间线视图，便于长伴奏里做裁剪和重录。
- `Track Edit > Record over track from cursor` 会把当前录音轨从光标往后的内容替换为新 take，适合“不想细选 clip，直接从这里重来”的场景。
- `Track Edit` / `Clip Edit` 菜单里的 `Record only this Track` 会让当前轨保持 ready，并关闭其它 ready 轨，避免多轨时误把所有 ready 轨一起录进去。
- `Track Edit` / `Clip Edit` 菜单里的 `Rename current Track...` 可把 `Take 1`、`Take 2` 改成更直观的乐器或段落名，方便多轨录音时辨认。
- `Track Edit` / `Clip Edit` 菜单里的 `Reset Track Level` 会把当前 `Track` 的整轨音量恢复到 `0 dB`；`Clip Edit > Reset clip gain` 会把当前目标 clip 的增益恢复到 `0 dB`。
- `Track Edit` / `Clip Edit` 菜单里的 `Mute current Track` / `Solo current Track` 用于多轨录音时临时对比某条 take；这些监听动作不放在主界面常驻，避免像完整 DAW 一样出现过多小按钮。
- `Clip Edit > Re-record here` 支持在光标处替换式重录：可先选中 clip，也可以只把光标放到当前 `Track` 的某个 clip 内；保留光标前内容，裁掉光标后内容，并在同一轨道开始录新 take。
- `Clip Edit` 里的 split、cut before/after cursor、reset clip gain、mute/unmute clip、delete clip、跳到 clip start/end 也支持直接使用当前 `Track` 光标下的 clip，不必先点选。
- `Mute clip` 是非破坏性的试听动作，只让该片段暂时不参与播放/导出；误静音或想恢复全局试听时可用 `Unmute all clips`，需要真正移除时再用 `Delete clip`。
- 光标位于当前 `Track` 的 clip 内时，状态区会显示 `Cursor clip` 和 clip 是否 muted，帮助确认 `Clip Edit` 和 `Clip Gain` 的当前目标。
- 导入音频、split、cut before/after 和录音保留 take 后会自动给相关音频片段加极短边缘淡化；多轨一起录时会覆盖所有参与录制的 ready 轨，减少硬切产生的咔哒声，不暴露额外参数。
- `Track Level` 滑块可调整当前轨道整体音量，方便平衡伴奏和录音轨；`Clip Gain` 滑块可设置选中或光标下音频 clip 的增益，范围为 `-48 dB` 到 `+24 dB`；滑动时状态区会确认影响的是整条轨还是当前 clip。
- `More > Undo` / `More > Redo` 接入 Tracktion edit 的 undo stack，用于恢复裁剪、删除、增益等编辑操作。
- 支持常用快捷键：`Space` 播放/停止，`R` 从光标录音，`S` 在光标处分割，`[` / `]` 跳到当前目标 clip 开头/结尾，`Delete` / `Backspace` 删除选中 waveform clip，`Ctrl+Z` / `Ctrl+Y` 撤销/重做。
- `Export WAV` 导出当前 edit 的 `mixdown.wav`；如果已导入伴奏，则按伴奏长度渲染。先写临时文件，已有成品保留，后续导出自动加文件名后缀；重复导出和缺失音频阻止导出已通过程序化检查。
- `More > Export video...` 选择手机视频、填写音频偏移，导出 MP4。支持主流容器格式；不兼容的 SDR 视频编码转为 H.264，详见 [视频导出说明](native_video_export.md)。
- `More > Camera + reference sound...` 打开摄像头与参考麦克风采集面板，随原生录音启动和停止，Stop 后自动保存、对齐并合成。详见 [采集说明与实机验收边界](camera_capture.md)。

## 基本工作流

1. 打开应用。
2. 点 `Audio Setup` 选择声卡输入输出。本机构建已验证 Windows Audio + Focusrite Input 1；当前未启用 ASIO，不把低延迟 ASIO 录音列为已验证能力。
3. 点 `Import Backing` 导入伴奏。
4. 需要多轨时点 `Add Track`；逐条用 `Track` 下拉框选择要录的轨道，用 `Input` 下拉框选择声卡输入，然后勾选 `Record this track`。需要听输入时勾选 `Hear input`。
5. 用光标滑块或时间线定位到要开始录的位置。
6. 点 `Record` 开始录音；多条轨 ready 时会一起录，如果只想录当前轨，先用 `Track Edit` / `Clip Edit > Record only this Track`；如果没有手动选轨，会自动录到 `Take 1`。
7. 录好点 `Stop` 保存；录坏点 `Discard Take` 丢弃。
8. 如果只是想测试编辑流程，可用 `More > Import audio to current track` 把现有音频插到当前轨道，不必先接真实声卡。
9. 要从当前轨道某处直接重来时，把光标放到起点，点 `Track Edit > Record over track from cursor`；录坏后点 `Discard Take` 会恢复旧内容。
10. 多轨变多后，用 `Track Edit` / `Clip Edit` 里的 `Rename current Track...` 给当前轨命名。
11. 多轨对比时，用 `Mute current Track` 或 `Solo current Track` 临时听某条 take；听完可用 `Clear all mute/solo` 回到正常监听。
12. 只想编辑某个 clip 时，把光标放到当前 `Track` 的 clip 内，或先选中要编辑的 clip，再用 `Clip Edit` 做重录、split、cut before/after、reset clip gain、mute/unmute 或 delete；误静音多个片段时用 `Unmute all clips` 一键恢复。
13. 长音频里先用光标定位，再用 `More > Zoom In` 放大局部；需要回到全局时点 `More > Fit view`。
14. 用 `Track Level` 平衡伴奏和录音轨；把光标放进 clip 或选中 clip 后，用 `Clip Edit` 和 `Clip Gain` 做基础整理；滑动音量/增益时看状态区确认影响范围，调乱后用 `Reset Track Level` 或 `Reset clip gain` 归零；剪错时用 `More > Undo`，需要恢复时用 `More > Redo`。
15. 点 `Export WAV` 导出混音。
16. `Ctrl+S` 保存工程；下次启动后用 `Ctrl+O` 选择原来的 `session.tracktionedit`。外部导入音频仍按路径引用，请保留原文件位置。缺失音频时提示路径并阻止导出。
17. `More > Export video...` 选择同一次演奏的视频，输入偏移毫秒数并导出。正值延后混音，负值裁去混音开头。保持完整视频时长，音频不足补静音、超出截断。

## 快捷键

- `Space`：播放/停止。
- `R`：从当前光标开始录音。
- `S`：在当前光标处分割当前目标 clip。
- `[` / `]`：跳到当前目标 clip 的开头/结尾。
- `Delete` / `Backspace`：删除选中的 waveform clip，避免光标误停时误删。
- `Ctrl+Z` / `Ctrl+Y`：撤销/重做。
- `Ctrl+S` / `Ctrl+O`：保存/打开工程。

## 构建

Tracktion Engine checkout 需要存在于：

```text
.spikes\tracktion_engine
```

配置：

```powershell
$cmake = 'C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
& $cmake -S native\cover_recorder -B native\cover_recorder\build\windows-vs2026 -G 'Visual Studio 18 2026' -A x64
```

编译：

```powershell
& $cmake --build native\cover_recorder\build\windows-vs2026 --config Release --target CoverRecorderNative --parallel 4
```

生成的应用：

```text
native\cover_recorder\build\windows-vs2026\CoverRecorderNative_artefacts\Release\Cover Recorder.exe
```

## 当前限制

- 已有视频选择与 MP4 导出，没有内置视频预览或自动对齐界面；手动偏移需通过播放器回看调整。
- 视频导出依赖本机 `cover-syncer` 环境。编码期间锁定编辑和正常关闭，等待完成后恢复；暂未提供取消按钮。
- 当前视频转码针对 SDR，HDR 会明确拒绝；WAV 的无覆盖发布需要支持硬链接的文件系统（本机 NTFS 已验证）。
- `Track Edit > Record over track from cursor` 和 `Clip Edit > Re-record here` 已覆盖从光标开始替换重录；还没有完整 punch-in/out 区间、自动 crossfade 或 take comping。
- 已有自动短边缘淡化来避免硬切咔哒声；还没有手动 fade 曲线、插件链或完整混音电平表。
- `Export WAV` 是阻塞式操作，导出时 UI 可能短暂停住。
- 还没有打包 installer。

## 下一步

- 完成新版菜单及对话框的人工操作验收，并录制一个完整 cover 成品。
- 继续减少需要常驻显示的按钮，把低频操作做成更清晰的上下文动作。
- 优先完成保存重开验收，以及同一次连续演奏的录音与手机视频端到端导出；复杂重录与 take comping 不属于当前 MVP。
