#include <JuceHeader.h>

#include "Utilities.h"
#include "PluginWindow.h"
#include "Components.h"
#include "SessionOperations.h"
#include "SessionChecks.h"
#include "VideoExportProcess.h"
#include "CameraCaptureProcess.h"

namespace te = tracktion;

namespace
{
juce::String formatDuration (te::TimeDuration duration)
{
    const auto totalSeconds = std::max (0.0, duration.inSeconds());
    const auto minutes = static_cast<int> (totalSeconds / 60.0);
    const auto seconds = static_cast<int> (std::fmod (totalSeconds, 60.0));
    return juce::String::formatted ("%02d:%02d", minutes, seconds);
}

te::AudioTrack* getAudioTrackAt (te::Edit& edit, int index)
{
    edit.ensureNumberOfAudioTracks (index + 1);
    return te::getAudioTracks (edit)[index];
}

void removeAllClips (te::AudioTrack& track)
{
    auto clips = track.getClips();

    for (int i = clips.size(); --i >= 0;)
        clips.getUnchecked (i)->removeFromParent();
}

te::TimePosition secondsToPosition (double seconds)
{
    return te::TimePosition::fromSeconds (std::max (0.0, seconds));
}

class InputLevelMeter final : public juce::Component
{
public:
    void setState (float newLevelDb, bool newOverload, const juce::String& newText)
    {
        levelDb = newLevelDb;
        overloaded = newOverload;
        text = newText;
        repaint();
    }

    void paint (juce::Graphics& g) override
    {
        auto r = getLocalBounds().toFloat().reduced (1.0f);
        g.setColour (juce::Colour (0xff151719));
        g.fillRoundedRectangle (r, 4.0f);

        const auto normalised = juce::jlimit (0.0f, 1.0f, (levelDb + 60.0f) / 60.0f);
        auto bar = r.reduced (2.0f);
        bar.setWidth (bar.getWidth() * normalised);

        const auto fill = overloaded ? juce::Colour (0xffd84a4a)
                                     : (levelDb > -9.0f ? juce::Colour (0xffd7b84a)
                                                        : juce::Colour (0xff58b971));
        g.setColour (fill);
        g.fillRoundedRectangle (bar, 3.0f);

        g.setColour (juce::Colour (0xff4a5158));
        g.drawRoundedRectangle (r, 4.0f, 1.0f);

        g.setColour (juce::Colours::whitesmoke);
        g.setFont (juce::FontOptions (12.0f));
        g.drawText (text, getLocalBounds().reduced (6, 0), juce::Justification::centredLeft, true);
    }

private:
    float levelDb = -100.0f;
    bool overloaded = false;
    juce::String text = "no input";
};
}

class CoverRecorderComponent final : public juce::Component,
                                     private juce::ChangeListener,
                                     private juce::Timer
{
public:
    CoverRecorderComponent()
        : engine ("Cover Recorder", std::make_unique<ExtendedUIBehaviour>(), nullptr),
          selectionManager (engine)
    {
        addAndMakeVisible (title);
        addAndMakeVisible (backingButton);
        addAndMakeVisible (audioSettingsButton);
        addAndMakeVisible (addTrackButton);
        addAndMakeVisible (playButton);
        addAndMakeVisible (stopButton);
        addAndMakeVisible (recordButton);
        addAndMakeVisible (discardButton);
        addAndMakeVisible (renderButton);
        addAndMakeVisible (moreButton);
        addAndMakeVisible (clipMenuButton);
        addAndMakeVisible (trackLevelLabel);
        addAndMakeVisible (trackLevelSlider);
        addAndMakeVisible (recordTrackLabel);
        addAndMakeVisible (recordTrackBox);
        addAndMakeVisible (recordInputBox);
        addAndMakeVisible (recordTrackToggle);
        addAndMakeVisible (inputMonitorToggle);
        addAndMakeVisible (clipGainLabel);
        addAndMakeVisible (clipGainSlider);
        addAndMakeVisible (inputLevelLabel);
        addAndMakeVisible (inputLevelMeter);
        addAndMakeVisible (cursorLabel);
        addAndMakeVisible (cursorSlider);
        addAndMakeVisible (sessionLabel);
        addAndMakeVisible (selectionLabel);
        addAndMakeVisible (statusLabel);

        title.setText ("Cover Recorder", juce::dontSendNotification);
        title.setFont (juce::FontOptions (22.0f, juce::Font::bold));
        title.setJustificationType (juce::Justification::centredLeft);

        setupLabel (cursorLabel);
        setupLabel (trackLevelLabel);
        setupLabel (recordTrackLabel);
        setupLabel (clipGainLabel);
        setupLabel (inputLevelLabel);
        setupLabel (sessionLabel);
        setupLabel (selectionLabel);
        setupLabel (statusLabel);

        backingButton.setButtonText ("Import Backing");
        audioSettingsButton.setButtonText ("Audio Setup");
        addTrackButton.setButtonText ("Add Track");
        playButton.setButtonText ("Play");
        stopButton.setButtonText ("Stop");
        recordButton.setButtonText ("Record");
        discardButton.setButtonText ("Discard Take");
        renderButton.setButtonText ("Export WAV");
        moreButton.setButtonText ("More");
        clipMenuButton.setButtonText ("Clip Edit");
        recordTrackToggle.setButtonText ("Record this track");
        inputMonitorToggle.setButtonText ("Hear input");

        recordTrackLabel.setText ("Track", juce::dontSendNotification);
        trackLevelLabel.setText ("Track Level", juce::dontSendNotification);
        clipGainLabel.setText ("Clip Gain", juce::dontSendNotification);
        inputLevelLabel.setText ("Input", juce::dontSendNotification);
        setupTooltips();

        recordTrackBox.onChange = [this]
        {
            if (updatingTrackBox || edit == nullptr)
                return;

            if (auto* track = getRecordTrackFromBox())
            {
                selectionManager.selectOnly (track);
                setStatus (describeRecordTrackRecordAction (*track));
                updateStateText();
            }
        };

        recordInputBox.onChange = [this]
        {
            if (updatingInputBox || edit == nullptr)
                return;

            setActionTrackInput (recordInputBox.getSelectedId() - 1);
        };

        trackLevelSlider.setSliderStyle (juce::Slider::LinearHorizontal);
        trackLevelSlider.setTextBoxStyle (juce::Slider::TextBoxRight, false, 72, 22);
        trackLevelSlider.setRange (-48.0, 12.0, 0.1);
        trackLevelSlider.setDoubleClickReturnValue (true, 0.0);
        trackLevelSlider.onValueChange = [this]
        {
            if (updatingTrackLevelSlider || edit == nullptr)
                return;

            setCurrentTrackLevelDb (static_cast<float> (trackLevelSlider.getValue()));
        };

        clipGainSlider.setSliderStyle (juce::Slider::LinearHorizontal);
        clipGainSlider.setTextBoxStyle (juce::Slider::TextBoxRight, false, 72, 22);
        clipGainSlider.setRange (-48.0, 24.0, 0.1);
        clipGainSlider.setDoubleClickReturnValue (true, 0.0);
        clipGainSlider.onValueChange = [this]
        {
            if (updatingGainSlider || edit == nullptr)
                return;

            setSelectedClipGainDb (static_cast<float> (clipGainSlider.getValue()));
        };

        cursorSlider.setSliderStyle (juce::Slider::LinearHorizontal);
        cursorSlider.setTextBoxStyle (juce::Slider::TextBoxRight, false, 80, 22);
        cursorSlider.setRange (0.0, 60.0, 0.001);
        cursorSlider.onValueChange = [this]
        {
            if (updatingCursorSlider || edit == nullptr)
                return;

            edit->getTransport().setPosition (secondsToPosition (cursorSlider.getValue()));
        };

        backingButton.onClick = [this] { chooseBackingFile(); };
        audioSettingsButton.onClick = [this] { showAudioSettings(); };
        addTrackButton.onClick = [this] { addRecordingTrack(); };
        playButton.onClick = [this] { togglePlayback(); };
        stopButton.onClick = [this] { stopTransport (false); };
        recordButton.onClick = [this] { startRecordingAtCursor(); };
        discardButton.onClick = [this] { stopTransport (true); };
        renderButton.onClick = [this] { renderMixdown(); };
        moreButton.onClick = [this] { showMoreMenu(); };
        clipMenuButton.onClick = [this] { showClipMenu(); };
        recordTrackToggle.onClick = [this] { setActionTrackArmed (recordTrackToggle.getToggleState()); };
        inputMonitorToggle.onClick = [this] { setActionTrackInputMonitoring (inputMonitorToggle.getToggleState()); };

        selectionManager.addChangeListener (this);

        createNewSession();
        updateStateText();

        setWantsKeyboardFocus (true);
        setMouseClickGrabsKeyboardFocus (true);

        const juce::Component::SafePointer<CoverRecorderComponent> safeThis (this);
        juce::MessageManager::callAsync ([safeThis]
        {
            if (safeThis != nullptr)
                safeThis->grabKeyboardFocus();
        });

        startTimerHz (12);
        setSize (1120, 720);
    }

    ~CoverRecorderComponent() override
    {
        selectionManager.removeChangeListener (this);
        detachInputLevelClient();

        if (edit != nullptr)
        {
            auto& transport = edit->getTransport();
            transport.removeChangeListener (this);

            if (transport.isPlaying())
                transport.stop (false, true);

            te::EditFileOperations (*edit).save (true, true, false);
        }
    }

    bool canClose()
    {
        if (cameraCapture != nullptr)
        {
            if (edit->getTransport().isRecording() || cameraAwaitingAudio || cameraStopping)
                setStatus ("Stop recording and wait for both files to finish before closing.");
            else
            {
                quitAfterCamera = cameraCapture->send ("close");
                setStatus ("Closing camera...");
            }
            return false;
        }
        if (videoExport == nullptr)
            return true;
        setStatus ("Video export is still running. Wait for it to finish before closing.");
        return false;
    }

    void paint (juce::Graphics& g) override
    {
        g.fillAll (juce::Colour (0xff202326));

        auto content = getLocalBounds().reduced (14);
        g.setColour (juce::Colour (0xff30363d));
        g.drawRoundedRectangle (content.toFloat(), 6.0f, 1.0f);
    }

    void resized() override
    {
        auto r = getLocalBounds().reduced (24);

        title.setBounds (r.removeFromTop (30));
        r.removeFromTop (8);

        auto topRow = r.removeFromTop (34);
        backingButton.setBounds (topRow.removeFromLeft (150).reduced (2));
        audioSettingsButton.setBounds (topRow.removeFromLeft (130).reduced (2));
        addTrackButton.setBounds (topRow.removeFromLeft (112).reduced (2));
        playButton.setBounds (topRow.removeFromLeft (72).reduced (2));
        stopButton.setBounds (topRow.removeFromLeft (72).reduced (2));
        recordButton.setBounds (topRow.removeFromLeft (122).reduced (2));
        discardButton.setBounds (topRow.removeFromLeft (112).reduced (2));
        renderButton.setBounds (topRow.removeFromLeft (128).reduced (2));
        moreButton.setBounds (topRow.removeFromLeft (88).reduced (2));

        auto editRow = r.removeFromTop (34);
        clipMenuButton.setBounds (editRow.removeFromLeft (110).reduced (2));
        editRow.removeFromLeft (8);
        trackLevelLabel.setBounds (editRow.removeFromLeft (86));
        trackLevelSlider.setBounds (editRow.removeFromLeft (210).reduced (2));
        editRow.removeFromLeft (14);
        clipGainLabel.setBounds (editRow.removeFromLeft (74));
        clipGainSlider.setBounds (editRow.removeFromLeft (190).reduced (2));

        auto recRow = r.removeFromTop (34);
        recordTrackLabel.setBounds (recRow.removeFromLeft (44));
        recordTrackBox.setBounds (recRow.removeFromLeft (150).reduced (2));
        inputLevelLabel.setBounds (recRow.removeFromLeft (44));
        recordInputBox.setBounds (recRow.removeFromLeft (220).reduced (2));
        recordTrackToggle.setBounds (recRow.removeFromLeft (142).reduced (2));
        inputMonitorToggle.setBounds (recRow.removeFromLeft (104).reduced (2));
        inputLevelMeter.setBounds (recRow.removeFromLeft (190).reduced (2));

        r.removeFromTop (6);
        auto cursorRow = r.removeFromTop (30);
        cursorLabel.setBounds (cursorRow.removeFromLeft (155));
        cursorSlider.setBounds (cursorRow);

        r.removeFromTop (8);
        sessionLabel.setBounds (r.removeFromTop (24));
        selectionLabel.setBounds (r.removeFromTop (24));
        statusLabel.setBounds (r.removeFromTop (28));
        r.removeFromTop (8);

        if (editComponent != nullptr)
            editComponent->setBounds (r);
    }

    bool keyPressed (const juce::KeyPress& key) override
    {
        if (videoExport != nullptr)
            return true;
        if (cameraAwaitingAudio || cameraStopping)
        {
            if (key == juce::KeyPress::spaceKey && ! cameraStopping)
                stopTransport (false);
            return true;
        }
        if (isTextInputFocused())
            return false;

        const auto mods = key.getModifiers();
        const bool commandDown = mods.isCommandDown() || mods.isCtrlDown();
        const auto textChar = juce::CharacterFunctions::toLowerCase (key.getTextCharacter());

        if (commandDown && textChar == 's')
        {
            saveSession();
            return true;
        }

        if (commandDown && textChar == 'o')
        {
            chooseSessionFile();
            return true;
        }

        if (commandDown && textChar == 'z')
        {
            undoEdit();
            return true;
        }

        if (commandDown && textChar == 'y')
        {
            redoEdit();
            return true;
        }

        if (key.isKeyCode (juce::KeyPress::spaceKey))
        {
            togglePlayback();
            return true;
        }

        if (key.isKeyCode (juce::KeyPress::deleteKey) || key.isKeyCode (juce::KeyPress::backspaceKey))
        {
            deleteSelected();
            return true;
        }

        if (! commandDown && textChar == 's')
        {
            splitSelectedClipAtCursor();
            return true;
        }

        if (! commandDown && textChar == 'r')
        {
            startRecordingAtCursor();
            return true;
        }

        if (! commandDown && textChar == '[')
        {
            moveCursorToSelectedClipBoundary (true);
            return true;
        }

        if (! commandDown && textChar == ']')
        {
            moveCursorToSelectedClipBoundary (false);
            return true;
        }

        return false;
    }

private:
    te::Engine engine;
    te::SelectionManager selectionManager;
    std::unique_ptr<te::Edit> edit;
    std::unique_ptr<EditComponent> editComponent;

    juce::File sessionDir;
    juce::File editFile;
    juce::File backingFile;
    juce::File renderedMixFile;
    std::unique_ptr<VideoExportProcess> videoExport;
    juce::File videoOutputFile;
    std::unique_ptr<CameraCaptureProcess> cameraCapture;
    bool cameraAwaitingAudio = false;
    bool cameraStopping = false;
    bool cameraHasAudio = false;
    bool cameraShouldExport = false;
    bool quitAfterCamera = false;
    juce::String lastCameraPhase;
    juce::String cameraFailure;
    int cameraHeartbeatTicks = 0;
    std::unique_ptr<juce::FileChooser> fileChooser;
    te::TimeDuration backingLength;

    struct PendingRerecordState
    {
        bool active = false;
        bool restoreTrackState = false;
        te::EditItemID clipID;
        te::EditItemID trackID;
        te::TimePosition originalEnd;
        juce::ValueTree trackStateBeforeRerecord;
    };

    PendingRerecordState pendingRerecord;

    int availableInputCount = 0;
    int assignedInputCount = 0;
    int armedInputCount = 0;
    bool updatingCursorSlider = false;
    bool updatingTrackBox = false;
    bool updatingInputBox = false;
    bool updatingTrackLevelSlider = false;
    bool updatingGainSlider = false;
    bool timelineFitMode = true;
    juce::String recordTrackListSignature;
    juce::String recordInputListSignature;

    juce::Label title;
    juce::TextButton backingButton;
    juce::TextButton audioSettingsButton;
    juce::TextButton addTrackButton;
    juce::TextButton playButton;
    juce::TextButton stopButton;
    juce::TextButton recordButton;
    juce::TextButton discardButton;
    juce::TextButton renderButton;
    juce::TextButton moreButton;
    juce::TextButton clipMenuButton;
    juce::Label trackLevelLabel;
    juce::Slider trackLevelSlider;
    juce::Label recordTrackLabel;
    juce::ComboBox recordTrackBox;
    juce::ComboBox recordInputBox;
    juce::ToggleButton recordTrackToggle;
    juce::ToggleButton inputMonitorToggle;
    juce::Label clipGainLabel;
    juce::Slider clipGainSlider;
    juce::Label inputLevelLabel;
    InputLevelMeter inputLevelMeter;
    juce::Label cursorLabel;
    juce::Slider cursorSlider;
    juce::Label sessionLabel;
    juce::Label selectionLabel;
    juce::Label statusLabel;
    juce::TooltipWindow tooltipWindow { this, 650 };

    te::InputDevice* meteredInputDevice = nullptr;
    te::LevelMeasurer::Client inputLevelClient;

    bool isTextInputFocused() const
    {
        auto* focused = juce::Component::getCurrentlyFocusedComponent();
        return focused != nullptr && dynamic_cast<juce::TextEditor*> (focused) != nullptr;
    }

    void setupLabel (juce::Label& label)
    {
        label.setColour (juce::Label::textColourId, juce::Colours::whitesmoke);
        label.setColour (juce::Label::backgroundColourId, juce::Colour (0x00000000));
        label.setJustificationType (juce::Justification::centredLeft);
    }

    void setupTooltips()
    {
        backingButton.setTooltip ("Import the backing track for the cover.");
        audioSettingsButton.setTooltip ("Choose the audio device and enable inputs.");
        addTrackButton.setTooltip ("Add another take track for overdubs or separate parts.");
        playButton.setTooltip ("Play or pause from the cursor.");
        stopButton.setTooltip ("Stop playback or keep the current recording.");
        recordButton.setTooltip ("Record every Ready track, starting at the cursor.");
        discardButton.setTooltip ("Stop and throw away the current take. Re-record restores the old audio.");
        renderButton.setTooltip ("Export a new mixdown WAV, preserving previous exports.");
        moreButton.setTooltip ("Less-used actions: import audio, undo/redo, zoom, and session folder.");
        clipMenuButton.setTooltip ("Edit the current Track or the clip under the cursor.");

        recordTrackLabel.setTooltip ("Current take track.");
        recordTrackBox.setTooltip ("Choose the current Track. Ready tracks are included when you press Record.");
        recordInputBox.setTooltip ("Choose the soundcard input feeding the current Track.");
        recordTrackToggle.setTooltip ("When on, this Track is included by Record.");
        inputMonitorToggle.setTooltip ("Hear the live input. Use headphones to avoid feedback.");

        trackLevelLabel.setTooltip ("Whole-track volume.");
        trackLevelSlider.setTooltip ("Adjust the whole Track volume. Double-click resets to 0 dB.");
        clipGainLabel.setTooltip ("Gain for one clip.");
        clipGainSlider.setTooltip ("Adjust only the selected or cursor clip. Double-click resets to 0 dB.");

        inputLevelLabel.setTooltip ("Live input level for the current Track.");
        cursorLabel.setTooltip ("Current playhead position.");
        cursorSlider.setTooltip ("Move the cursor. Recording and edits start here.");
        sessionLabel.setTooltip ("Session summary.");
        selectionLabel.setTooltip ("Current Track, clip, and transport state.");
        statusLabel.setTooltip ("Last action or next step.");
    }

    void createNewSession()
    {
        const auto timestamp = juce::Time::getCurrentTime().formatted ("%Y%m%d_%H%M%S");
        sessionDir = juce::File::getSpecialLocation (juce::File::userDocumentsDirectory)
                         .getChildFile ("CoverRecorderSessions")
                         .getChildFile (timestamp);
        sessionDir.createDirectory();

        editFile = sessionDir.getChildFile ("session.tracktionedit");
        renderedMixFile = sessionDir.getChildFile ("mixdown.wav");
        edit = te::createEmptyEdit (engine, editFile);
        edit->playInStopEnabled = true;
        edit->getTransport().addChangeListener (this);

        if (auto* backingTrack = getAudioTrackAt (*edit, 0))
            backingTrack->setName ("Backing");

        if (auto* takeTrack = getAudioTrackAt (*edit, 1))
            takeTrack->setName ("Take 1");

        setupRecordingDevices (true);
        rebuildEditComponent();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Session ready. Import a backing track, choose an input, then record from the cursor.");
    }

    void rebuildEditComponent()
    {
        editComponent = nullptr;

        if (edit == nullptr)
            return;

        editComponent = std::make_unique<EditComponent> (*edit, selectionManager);
        editComponent->getEditViewState().showHeaders = false;
        editComponent->getEditViewState().showFooters = false;
        editComponent->getEditViewState().showWaveDevices = false;
        editComponent->getEditViewState().drawWaveforms = true;
        addAndMakeVisible (*editComponent);
        refreshTimelineRange();
        resized();
    }

    void persistSessionMetadata()
    {
        edit->state.setProperty ("coverBackingLengthSeconds", backingLength.inSeconds(), nullptr);
    }

    bool saveSession()
    {
        if (edit == nullptr || edit->getTransport().isRecording())
        {
            setStatus ("Stop recording before saving or opening a session.");
            return false;
        }

        persistSessionMetadata();
        const bool ok = sessionIO::save (*edit, backingLength);
        setStatus (ok ? "Session saved: " + editFile.getFullPathName()
                      : "Session save failed. The current session is still open.");
        return ok;
    }

    juce::StringArray findMissingMedia (te::Edit& session) const
    {
        return sessionIO::missingMedia (session);
    }

    void showMissingMedia (const juce::StringArray& missing)
    {
        setStatus ("Missing media: " + juce::String (missing.size())
                   + " file(s). Restore their original paths before exporting.");
        juce::AlertWindow::showMessageBoxAsync (juce::MessageBoxIconType::WarningIcon,
            "Missing session media",
            "The session references these missing files:\n\n" + missing.joinIntoString ("\n")
                + "\n\nRestore them to the listed paths and reopen the session. Export is blocked until all sources exist.");
    }

    void chooseSessionFile()
    {
        if (cameraCapture != nullptr)
        {
            setStatus ("Close the camera panel before opening another session.");
            return;
        }
        if (edit == nullptr || edit->getTransport().isRecording() || fileChooser != nullptr)
            return;

        fileChooser = std::make_unique<juce::FileChooser> ("Open Cover Recorder session",
            sessionDir.getParentDirectory(), "*.tracktionedit");
        const juce::Component::SafePointer<CoverRecorderComponent> safeThis (this);
        fileChooser->launchAsync (juce::FileBrowserComponent::openMode | juce::FileBrowserComponent::canSelectFiles,
            [safeThis] (const juce::FileChooser& chooser)
            {
                if (safeThis == nullptr)
                    return;
                const auto file = chooser.getResult();
                safeThis->fileChooser.reset();
                if (file != juce::File())
                    safeThis->openSessionFile (file);
            });
    }

    void openSessionFile (const juce::File& file)
    {
        if (cameraCapture != nullptr)
            return;
        if (edit == nullptr || edit->getTransport().isRecording())
            return;
        if (! file.existsAsFile() || file.getSize() == 0 || ! file.hasFileExtension ("tracktionedit"))
        {
            setStatus ("Cannot open session: choose a non-empty .tracktionedit file.");
            return;
        }
        if (! te::loadEditFromFile (engine, file, te::ProjectItemID()).hasType (te::IDs::EDIT))
        {
            setStatus ("Invalid session file. The current session is still open.");
            return;
        }
        if (! saveSession())
            return;

        juce::String loadError;
        auto loaded = sessionIO::load (engine, file, loadError);
        if (loaded == nullptr)
        {
            setStatus (loadError + " The current session is still open.");
            return;
        }

        // The view, selection and meter must release the old edit before its owner does.
        auto& oldTransport = edit->getTransport();
        oldTransport.stop (false, false);
        oldTransport.removeChangeListener (this);
        detachInputLevelClient();
        selectionManager.deselectAll();
        editComponent.reset();
        clearPendingRerecord();
        edit = std::move (loaded);
        editFile = file;
        sessionDir = file.getParentDirectory();
        renderedMixFile = sessionDir.getChildFile ("mixdown.wav");
        edit->playInStopEnabled = true;
        edit->getTransport().addChangeListener (this);

        auto* backing = getAudioTrackAt (*edit, 0);
        getAudioTrackAt (*edit, 1);
        backingFile = {};
        for (auto* clip : backing->getClips())
            if (auto* wave = dynamic_cast<te::WaveAudioClip*> (clip))
            {
                if (backingFile == juce::File())
                    backingFile = wave->getOriginalFile();
            }
        backingLength = sessionIO::backingLength (*edit);
        recordTrackListSignature.clear();
        recordInputListSignature.clear();
        recordTrackBox.setSelectedId (0, juce::dontSendNotification);
        timelineFitMode = true;
        setupRecordingDevices (false);
        rebuildEditComponent();
        updateStateText();
        const auto missing = findMissingMedia (*edit);
        if (missing.isEmpty())
            setStatus ("Session opened: " + file.getFullPathName());
        else
            showMissingMedia (missing);
    }

    void refreshTimelineRange()
    {
        if (editComponent == nullptr)
            return;

        auto& evs = editComponent->getEditViewState();
        const auto timelineLength = getTimelineLengthSeconds();

        if (timelineFitMode)
        {
            evs.viewX1 = te::TimePosition();
            evs.viewX2 = secondsToPosition (timelineLength);
            return;
        }

        auto start = evs.viewX1.get().inSeconds();
        auto end = evs.viewX2.get().inSeconds();
        auto visibleLength = end - start;

        if (visibleLength < 0.25 || end <= start)
        {
            timelineFitMode = true;
            refreshTimelineRange();
            return;
        }

        visibleLength = juce::jlimit (0.25, timelineLength, visibleLength);
        start = juce::jlimit (0.0, std::max (0.0, timelineLength - visibleLength), start);
        end = start + visibleLength;

        evs.viewX1 = secondsToPosition (start);
        evs.viewX2 = secondsToPosition (end);
    }

    void fitTimelineToContent()
    {
        timelineFitMode = true;
        refreshTimelineRange();
        setStatus ("Timeline view fit to the full session.");
        updateStateText();
    }

    void setTimelineViewSeconds (double startSeconds, double endSeconds)
    {
        if (editComponent == nullptr)
            return;

        const auto timelineLength = getTimelineLengthSeconds();
        auto visibleLength = juce::jlimit (0.25, timelineLength, endSeconds - startSeconds);
        auto start = juce::jlimit (0.0, std::max (0.0, timelineLength - visibleLength), startSeconds);

        timelineFitMode = false;

        auto& evs = editComponent->getEditViewState();
        evs.viewX1 = secondsToPosition (start);
        evs.viewX2 = secondsToPosition (start + visibleLength);
    }

    void ensureCursorVisibleInTimeline()
    {
        if (edit == nullptr || editComponent == nullptr || timelineFitMode)
            return;

        auto& evs = editComponent->getEditViewState();
        const auto start = evs.viewX1.get().inSeconds();
        const auto end = evs.viewX2.get().inSeconds();
        const auto visibleLength = std::max (0.25, end - start);
        const auto cursorSeconds = edit->getTransport().getPosition().inSeconds();
        const auto margin = std::min (visibleLength * 0.15, 2.0);

        if (cursorSeconds >= start + margin && cursorSeconds <= end - margin)
            return;

        setTimelineViewSeconds (cursorSeconds - visibleLength * 0.5, cursorSeconds + visibleLength * 0.5);
    }

    void zoomTimeline (double factor)
    {
        if (edit == nullptr || editComponent == nullptr)
            return;

        auto& evs = editComponent->getEditViewState();
        const auto timelineLength = getTimelineLengthSeconds();
        const auto currentStart = evs.viewX1.get().inSeconds();
        const auto currentEnd = evs.viewX2.get().inSeconds();
        const auto currentLength = std::max (0.25, currentEnd - currentStart);
        const auto newLength = juce::jlimit (0.25, timelineLength, currentLength * factor);
        const auto cursorSeconds = edit->getTransport().getPosition().inSeconds();
        const auto currentCentre = (currentStart + currentEnd) * 0.5;
        const auto centre = (cursorSeconds >= currentStart && cursorSeconds <= currentEnd) ? cursorSeconds : currentCentre;

        setTimelineViewSeconds (centre - newLength * 0.5, centre + newLength * 0.5);
        setStatus ("Timeline zoom: " + juce::String (newLength, 1) + " seconds visible.");
        updateStateText();
    }

    double getTimelineLengthSeconds() const
    {
        double length = 0.0;

        if (edit != nullptr)
            length = std::max (length, edit->getLength().inSeconds());

        length = std::max (length, backingLength.inSeconds());

        if (edit != nullptr)
            length = std::max (length, edit->getTransport().getPosition().inSeconds() + 10.0);

        return std::max (30.0, length + 2.0);
    }

    void setupRecordingDevices (bool autoAssignFirstInput)
    {
        if (edit == nullptr)
            return;

        availableInputCount = 0;
        assignedInputCount = 0;
        armedInputCount = 0;

        auto& dm = engine.getDeviceManager();

        for (int i = 0; i < dm.getNumWaveInDevices(); ++i)
        {
            if (auto input = dm.getWaveInDevice (i))
            {
                ++availableInputCount;
                input->setStereoPair (false);
                input->setEnabled (true);
            }
        }

        edit->getTransport().ensureContextAllocated();

        assignedInputCount = countAssignedWaveInputs();
        armedInputCount = countArmedWaveInputs();

        if (autoAssignFirstInput && assignedInputCount == 0 && availableInputCount > 0)
        {
            if (auto* track = getAudioTrackAt (*edit, 1))
            {
                assignWaveInputToTrack (*track, 0);
                assignedInputCount = countAssignedWaveInputs();
                armedInputCount = countArmedWaveInputs();
            }
        }
    }

    int countAssignedWaveInputs() const
    {
        if (edit == nullptr)
            return 0;

        int count = 0;

        for (auto* instance : edit->getAllInputDevices())
            if (instance->getInputDevice().getDeviceType() == te::InputDevice::waveDevice
                && instance->getTargets().size() > 0)
                ++count;

        return count;
    }

    int countArmedWaveInputs() const
    {
        if (edit == nullptr)
            return 0;

        int count = 0;

        for (auto* instance : edit->getAllInputDevices())
        {
            if (instance->getInputDevice().getDeviceType() != te::InputDevice::waveDevice)
                continue;

            for (auto target : instance->getTargets())
            {
                if (instance->isRecordingEnabled (target))
                {
                    ++count;
                    break;
                }
            }
        }

        return count;
    }

    juce::Array<te::AudioTrack*> getArmedRecordingTracks() const
    {
        juce::Array<te::AudioTrack*> tracksToRecord;

        if (edit == nullptr)
            return tracksToRecord;

        auto tracks = te::getAudioTracks (*edit);

        for (int i = 1; i < tracks.size(); ++i)
            if (auto* track = tracks[i])
                if (EngineHelpers::isTrackArmed (*track))
                    tracksToRecord.add (track);

        return tracksToRecord;
    }

    juce::String describeTrackList (const juce::Array<te::AudioTrack*>& tracks, int maxNames = 3) const
    {
        juce::StringArray names;

        for (int i = 0; i < tracks.size() && i < maxNames; ++i)
            if (auto* track = tracks[i])
                names.add (track->getName());

        auto text = names.joinIntoString (", ");

        if (tracks.size() > maxNames)
            text << " +" << juce::String (tracks.size() - maxNames);

        return text;
    }

    juce::String describeReadyTracks (bool includeNames) const
    {
        auto readyTracks = getArmedRecordingTracks();

        if (readyTracks.isEmpty())
            return "Ready tracks: 0";

        juce::String text;
        text << "Ready tracks: " << readyTracks.size();

        if (includeNames)
            text << " (" << describeTrackList (readyTracks) << ")";

        return text;
    }

    bool assignWaveInputToTrack (te::AudioTrack& track, int inputIndex, bool armAfterAssign = true)
    {
        if (edit == nullptr)
            return false;

        for (auto* instance : edit->getAllInputDevices())
            if (instance->getInputDevice().getDeviceType() == te::InputDevice::waveDevice
                && instance->getTargets().contains (track.itemID))
            {
                [[ maybe_unused ]] auto result = instance->removeTarget (track.itemID, &edit->getUndoManager());
            }

        int index = 0;

        for (auto* instance : edit->getAllInputDevices())
        {
            if (instance->getInputDevice().getDeviceType() != te::InputDevice::waveDevice)
                continue;

            if (index == inputIndex)
            {
                [[ maybe_unused ]] auto result = instance->setTarget (track.itemID, true, &edit->getUndoManager(), 0);
                instance->setRecordingEnabled (track.itemID, armAfterAssign);
                return true;
            }

            ++index;
        }

        return false;
    }

    bool hasWaveInputTargetingTrack (te::AudioTrack& track) const
    {
        if (edit == nullptr)
            return false;

        for (auto* instance : edit->getAllInputDevices())
            if (instance->getInputDevice().getDeviceType() == te::InputDevice::waveDevice
                && instance->getTargets().contains (track.itemID))
                return true;

        return false;
    }

    te::InputDevice* getWaveInputForTrack (te::AudioTrack& track) const
    {
        if (edit == nullptr)
            return nullptr;

        for (auto* instance : edit->getAllInputDevices())
            if (instance->getInputDevice().getDeviceType() == te::InputDevice::waveDevice
                && te::isOnTargetTrack (*instance, track, 0))
                return &instance->getInputDevice();

        return nullptr;
    }

    int getWaveInputIndexForTrack (te::AudioTrack& track) const
    {
        if (edit == nullptr)
            return -1;

        int index = 0;

        for (auto* instance : edit->getAllInputDevices())
        {
            if (instance->getInputDevice().getDeviceType() != te::InputDevice::waveDevice)
                continue;

            if (te::isOnTargetTrack (*instance, track, 0))
                return index;

            ++index;
        }

        return -1;
    }

    void detachInputLevelClient()
    {
        if (meteredInputDevice != nullptr)
        {
            meteredInputDevice->levelMeasurer.removeClient (inputLevelClient);
            meteredInputDevice = nullptr;
        }
    }

    void updateInputLevelMeter()
    {
        auto* track = getRecordingTrackForAction();
        auto* input = track != nullptr ? getWaveInputForTrack (*track) : nullptr;

        if (input != meteredInputDevice)
        {
            detachInputLevelClient();

            if (input != nullptr)
            {
                meteredInputDevice = input;
                inputLevelClient.reset();
                meteredInputDevice->levelMeasurer.addClient (inputLevelClient);
            }
        }

        if (track == nullptr || meteredInputDevice == nullptr)
        {
            inputLevelMeter.setState (-100.0f, false, "no input");
            return;
        }

        float peakDb = -100.0f;
        const int channelCount = juce::jlimit (1, te::LevelMeasurer::Client::maxNumChannels,
                                               inputLevelClient.getNumChannelsUsed());

        for (int ch = 0; ch < channelCount; ++ch)
            peakDb = std::max (peakDb, inputLevelClient.getAndClearAudioLevel (ch).dB);

        [[ maybe_unused ]] const auto overloadState = inputLevelClient.getAndClearOverload();
        const bool overloaded = peakDb > -0.1f;
        const auto levelText = meteredInputDevice->getName() + "  "
                               + (peakDb <= -99.0f ? juce::String ("-inf dB")
                                                   : juce::String (peakDb, 1) + " dB");
        inputLevelMeter.setState (peakDb, overloaded, levelText);
    }

    te::AudioTrack* getDefaultRecordingTrack()
    {
        if (edit == nullptr)
            return nullptr;

        edit->ensureNumberOfAudioTracks (2);

        auto tracks = te::getAudioTracks (*edit);
        return tracks.size() > 1 ? tracks[1] : nullptr;
    }

    bool isBackingTrack (te::AudioTrack& track) const
    {
        if (edit == nullptr)
            return false;

        auto tracks = te::getAudioTracks (*edit);
        return tracks.size() > 0 && tracks[0] == &track;
    }

    te::AudioTrack* getRecordingTrackForAction()
    {
        if (auto* boxedTrack = getRecordTrackFromBox())
            return boxedTrack;

        if (auto* selectedTrack = getSelectedAudioTrack())
            if (! isBackingTrack (*selectedTrack))
                return selectedTrack;

        return getDefaultRecordingTrack();
    }

    te::AudioTrack* getRecordTrackFromBox() const
    {
        if (edit == nullptr)
            return nullptr;

        const int selectedId = recordTrackBox.getSelectedId();

        if (selectedId <= 0)
            return nullptr;

        const int trackIndex = selectedId - 1;
        auto tracks = te::getAudioTracks (*edit);

        if (juce::isPositiveAndBelow (trackIndex, tracks.size()))
            if (auto* track = tracks[trackIndex])
                if (! isBackingTrack (*track))
                    return track;

        return nullptr;
    }

    juce::String getRecordTrackDisplayName (te::AudioTrack& track) const
    {
        juce::String text = track.getName() + (EngineHelpers::isTrackArmed (track) ? " - Ready" : " - Off");

        if (track.isMuted (false))
            text << " - Muted";

        if (track.isSolo (false))
            text << " - Solo";

        return text;
    }

    juce::String describeRecordTrackRecordAction (te::AudioTrack& track) const
    {
        juce::String text = "Track: " + track.getName();

        if (EngineHelpers::isTrackArmed (track))
            text << " - Ready. Record will include it.";
        else
            text << " - Off. Record will skip it.";

        if (! hasWaveInputTargetingTrack (track))
            text << " Choose an input first.";

        return text;
    }

    void syncRecordTrackBox()
    {
        if (edit == nullptr)
            return;

        auto tracks = te::getAudioTracks (*edit);
        juce::String signature;

        int desiredId = 0;

        for (int i = 1; i < tracks.size(); ++i)
        {
            if (auto* track = tracks[i])
            {
                const int itemId = i + 1;
                signature << itemId << ":" << track->getName() << ":"
                          << (EngineHelpers::isTrackArmed (*track) ? "ready" : "off") << ":"
                          << (track->isMuted (false) ? "muted" : "audible") << ":"
                          << (track->isSolo (false) ? "solo" : "unsolo") << ";";

                if (auto* selectedTrack = getSelectedAudioTrack(); selectedTrack != nullptr && track == selectedTrack)
                    desiredId = itemId;
            }
        }

        if (desiredId == 0)
        {
            if (auto* boxedTrack = getRecordTrackFromBox())
                for (int i = 1; i < tracks.size(); ++i)
                    if (tracks[i] == boxedTrack)
                        desiredId = i + 1;
        }

        if (desiredId == 0)
        {
            if (auto* defaultTrack = getDefaultRecordingTrack())
                for (int i = 1; i < tracks.size(); ++i)
                    if (tracks[i] == defaultTrack)
                        desiredId = i + 1;
        }

        updatingTrackBox = true;

        if (signature != recordTrackListSignature)
        {
            recordTrackBox.clear (juce::dontSendNotification);

            for (int i = 1; i < tracks.size(); ++i)
                if (auto* track = tracks[i])
                    recordTrackBox.addItem (getRecordTrackDisplayName (*track), i + 1);

            recordTrackListSignature = signature;
        }

        if (desiredId != 0 && recordTrackBox.getSelectedId() != desiredId)
            recordTrackBox.setSelectedId (desiredId, juce::dontSendNotification);
        else if (desiredId == 0)
            recordTrackBox.setText ("No track", juce::dontSendNotification);

        updatingTrackBox = false;
    }

    void syncInputBox()
    {
        if (edit == nullptr)
            return;

        juce::String signature;
        int inputIndex = 0;

        for (auto* instance : edit->getAllInputDevices())
        {
            if (instance->getInputDevice().getDeviceType() != te::InputDevice::waveDevice)
                continue;

            signature << (inputIndex + 1) << ":" << instance->getInputDevice().getName() << ";";
            ++inputIndex;
        }

        updatingInputBox = true;

        if (signature != recordInputListSignature)
        {
            recordInputBox.clear (juce::dontSendNotification);

            int itemIndex = 0;
            for (auto* instance : edit->getAllInputDevices())
            {
                if (instance->getInputDevice().getDeviceType() != te::InputDevice::waveDevice)
                    continue;

                recordInputBox.addItem (instance->getInputDevice().getName(), itemIndex + 1);
                ++itemIndex;
            }

            recordInputListSignature = signature;
        }

        int selectedId = 0;

        if (auto* track = getRecordingTrackForAction())
        {
            const int selectedInputIndex = getWaveInputIndexForTrack (*track);

            if (selectedInputIndex >= 0)
                selectedId = selectedInputIndex + 1;
        }

        if (selectedId != 0 && recordInputBox.getSelectedId() != selectedId)
            recordInputBox.setSelectedId (selectedId, juce::dontSendNotification);
        else if (selectedId == 0)
            recordInputBox.setText (availableInputCount > 0 ? "Choose input" : "No input", juce::dontSendNotification);

        recordInputBox.setEnabled (availableInputCount > 0 && ! edit->getTransport().isRecording());
        updatingInputBox = false;
    }

    bool setTrackInputMonitoring (te::AudioTrack& track, bool enabled)
    {
        if (edit == nullptr)
            return false;

        bool changed = false;

        for (auto* instance : edit->getAllInputDevices())
        {
            if (instance->getInputDevice().getDeviceType() != te::InputDevice::waveDevice)
                continue;

            if (te::isOnTargetTrack (*instance, track, 0))
            {
                instance->getInputDevice().setMonitorMode (enabled ? te::InputDevice::MonitorMode::on
                                                                   : te::InputDevice::MonitorMode::off);
                changed = true;
            }
        }

        return changed;
    }

    bool ensureTrackReadyForRecording (te::AudioTrack& track)
    {
        setupRecordingDevices (false);

        if (availableInputCount == 0)
        {
            setStatus ("No wave input device is available. Open Audio Settings and enable an input first.");
            updateStateText();
            return false;
        }

        if (! hasWaveInputTargetingTrack (track))
        {
            if (! assignWaveInputToTrack (track, 0))
            {
                setStatus ("Could not assign the first wave input to the current track.");
                updateStateText();
                return false;
            }
        }

        selectionManager.selectOnly (&track);
        EngineHelpers::armTrack (track, true);
        assignedInputCount = countAssignedWaveInputs();
        armedInputCount = countArmedWaveInputs();

        if (! EngineHelpers::isTrackArmed (track))
        {
            setStatus ("This track is not ready to record. Check Audio Setup, then enable Record this track.");
            updateStateText();
            return false;
        }

        return true;
    }

    void chooseBackingFile()
    {
        auto wildcard = engine.getAudioFileFormatManager().readFormatManager.getWildcardForAllFormats();
        fileChooser = std::make_unique<juce::FileChooser> ("Select a backing track",
                                                           juce::File::getSpecialLocation (juce::File::userMusicDirectory),
                                                           wildcard);

        const juce::Component::SafePointer<CoverRecorderComponent> safeThis (this);
        fileChooser->launchAsync (juce::FileBrowserComponent::openMode | juce::FileBrowserComponent::canSelectFiles,
                                  [safeThis] (const juce::FileChooser& chooser)
                                  {
                                      if (safeThis == nullptr)
                                          return;

                                      auto file = chooser.getResult();
                                      safeThis->fileChooser.reset();

                                      if (file.existsAsFile())
                                          safeThis->loadBackingFile (file);
                                  });
    }

    void loadBackingFile (const juce::File& file)
    {
        if (edit == nullptr)
            return;

        te::AudioFile audioFile (engine, file);

        if (! audioFile.isValid())
        {
            setStatus ("The selected file is not a supported audio file.");
            return;
        }

        auto* backingTrack = getAudioTrackAt (*edit, 0);
        removeAllClips (*backingTrack);

        backingLength = te::TimeDuration::fromSeconds (audioFile.getLength());
        backingFile = file;
        persistSessionMetadata();

        auto clipRange = te::TimeRange (te::TimePosition(), backingLength);
        auto clip = backingTrack->insertWaveClip (file.getFileNameWithoutExtension(), file,
                                                  { clipRange, {} }, true);

        if (clip == nullptr)
        {
            setStatus ("Failed to insert backing track.");
            return;
        }

        backingTrack->setName ("Backing");
        auto& transport = edit->getTransport();
        transport.setPosition (te::TimePosition());
        transport.setLoopRange (clipRange);
        transport.looping = false;
        transport.editHasChanged();

        refreshTimelineRange();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Backing imported. Drag the playhead or use the cursor slider, then record.");
        updateStateText();
    }

    void chooseAudioFileForCurrentTrack()
    {
        if (edit == nullptr)
            return;

        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("Choose a recording track first, then import audio.");
            updateStateText();
            return;
        }

        auto wildcard = engine.getAudioFileFormatManager().readFormatManager.getWildcardForAllFormats();
        fileChooser = std::make_unique<juce::FileChooser> ("Import audio to current track",
                                                           juce::File::getSpecialLocation (juce::File::userMusicDirectory),
                                                           wildcard);

        const juce::Component::SafePointer<CoverRecorderComponent> safeThis (this);
        fileChooser->launchAsync (juce::FileBrowserComponent::openMode | juce::FileBrowserComponent::canSelectFiles,
                                  [safeThis] (const juce::FileChooser& chooser)
                                  {
                                      if (safeThis == nullptr)
                                          return;

                                      auto file = chooser.getResult();
                                      safeThis->fileChooser.reset();

                                      if (file.existsAsFile())
                                          safeThis->importAudioToCurrentTrack (file);
                                  });
    }

    void importAudioToCurrentTrack (const juce::File& file)
    {
        if (edit == nullptr)
            return;

        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("Choose a recording track first, then import audio.");
            updateStateText();
            return;
        }

        te::AudioFile audioFile (engine, file);

        if (! audioFile.isValid())
        {
            setStatus ("The selected file is not a supported audio file.");
            return;
        }

        auto& transport = edit->getTransport();

        if (transport.isPlaying() || transport.isRecording())
            transport.stop (false, false);

        const auto start = transport.getPosition();
        const auto length = te::TimeDuration::fromSeconds (audioFile.getLength());
        auto clip = track->insertWaveClip (file.getFileNameWithoutExtension(), file,
                                           { te::TimeRange (start, start + length), {} }, false);

        if (clip == nullptr)
        {
            setStatus ("Failed to import audio to the current track.");
            updateStateText();
            return;
        }

        applyClickSafeEdgeFades (clip.get());
        selectionManager.selectOnly (clip.get());
        transport.editHasChanged();
        refreshTimelineRange();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Imported audio to " + track->getName() + " at " + juce::String (start.inSeconds(), 2) + "s.");
        updateStateText();
    }

    void showAudioSettings()
    {
        juce::DialogWindow::LaunchOptions options;
        options.dialogTitle = "Audio Settings";
        options.dialogBackgroundColour = juce::Colour (0xff202326);
        options.escapeKeyTriggersCloseButton = true;
        options.useNativeTitleBar = true;
        options.resizable = true;
        options.content.setOwned (new juce::AudioDeviceSelectorComponent (engine.getDeviceManager().deviceManager,
                                                                          0, 512, 1, 512,
                                                                          false, false, true, true));
        options.content->setSize (460, 620);
        options.launchAsync();
    }

    void addRecordingTrack()
    {
        if (edit == nullptr)
            return;

        setupRecordingDevices (false);

        const auto audioTracks = te::getAudioTracks (*edit);
        const int newTrackIndex = audioTracks.size();

        auto* track = getAudioTrackAt (*edit, newTrackIndex);
        track->setName ("Take " + juce::String (std::max (1, newTrackIndex)));

        if (availableInputCount > 0)
            assignWaveInputToTrack (*track, std::max (0, (newTrackIndex - 1) % availableInputCount));

        assignedInputCount = countAssignedWaveInputs();
        armedInputCount = countArmedWaveInputs();
        selectionManager.selectOnly (track);
        edit->getTransport().editHasChanged();
        refreshTimelineRange();
        te::EditFileOperations (*edit).save (true, true, false);

        if (EngineHelpers::isTrackArmed (*track))
            setStatus ("Added ready track: " + track->getName() + ". Record will include it.");
        else
            setStatus ("Added track: " + track->getName() + ". Choose an input, then enable Record this track.");

        updateStateText();
    }

    void togglePlayback()
    {
        if (edit == nullptr)
            return;

        auto& transport = edit->getTransport();

        if (transport.isRecording() || cameraAwaitingAudio)
        {
            stopTransport (false);
            return;
        }

        if (transport.isPlaying())
            transport.stop (false, false);
        else
            transport.play (false);

        updateStateText();
    }

    void startRecordingAtCursor (bool cameraReady = false)
    {
        if (edit == nullptr)
            return;

        setupRecordingDevices (true);

        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("No recording track is available.");
            updateStateText();
            return;
        }

        auto& transport = edit->getTransport();

        if (transport.isRecording())
            return;

        if (transport.isPlaying())
            transport.stop (false, false);

        if (! ensureTrackReadyForRecording (*track))
            return;

        auto tracksToRecord = getArmedRecordingTracks();
        if (cameraCapture != nullptr && ! cameraReady)
        {
            if (cameraCapture->status()["phase"].toString() != "ready")
            {
                setStatus ("Choose a camera and reference microphone in the camera panel, then click Prepare.");
                return;
            }
            transport.setPosition (te::TimePosition());
            cameraAwaitingAudio = cameraCapture->send ("record");
            cameraShouldExport = false;
            setStatus (cameraAwaitingAudio ? "Preparing video and reference microphone. Wait before playing..."
                                          : "Could not start camera recording.");
            updateStateText();
            return;
        }
        transport.record (false);
        if (cameraCapture != nullptr)
        {
            cameraHasAudio = transport.isRecording();
            if (! cameraHasAudio)
            {
                cameraCapture->send ("discard");
                cameraStopping = true;
                setStatus ("Instrument recording did not start. Stopping camera; existing material is preserved.");
                return;
            }
        }

        if (tracksToRecord.size() > 1)
            setStatus ("Recording " + juce::String (tracksToRecord.size()) + " ready tracks: "
                       + describeTrackList (tracksToRecord) + ". Press Stop when the take is done.");
        else
            setStatus ("Recording " + track->getName() + " from cursor. Press Stop when the take is done.");

        updateStateText();
    }

    void stopTransport (bool discardRecording)
    {
        if (edit == nullptr)
            return;

        auto& transport = edit->getTransport();
        const bool wasPendingRerecord = pendingRerecord.active;
        const auto pendingTrackID = pendingRerecord.trackID;
        const bool wasRecording = transport.isRecording();
        const auto tracksRecorded = wasRecording ? getArmedRecordingTracks() : juce::Array<te::AudioTrack*>();

        if (cameraCapture != nullptr && (wasRecording || cameraAwaitingAudio) && ! cameraStopping)
        {
            cameraShouldExport = wasRecording && cameraHasAudio && ! discardRecording;
            if (! cameraCapture->send (cameraShouldExport ? "stop" : "discard"))
                cameraFailure = "Could not signal the camera to stop. Use the camera panel to close it.";
            cameraStopping = true;
            cameraAwaitingAudio = false;
        }

        if (transport.isPlaying() || transport.isRecording())
            transport.stop (discardRecording, false);

        bool restoredRerecordTail = false;

        if (discardRecording && wasPendingRerecord)
            restoredRerecordTail = restorePendingRerecordTail();
        else
            clearPendingRerecord();

        if (! discardRecording && wasRecording)
        {
            if (! pendingTrackID.isInvalid())
            {
                if (auto* track = dynamic_cast<te::AudioTrack*> (te::findTrackForID (*edit, pendingTrackID)))
                    applyClickSafeEdgeFades (*track);
            }
            else
            {
                for (auto* track : tracksRecorded)
                    if (track != nullptr)
                        applyClickSafeEdgeFades (*track);
            }

            transport.editHasChanged();
        }

        refreshTimelineRange();
        te::EditFileOperations (*edit).save (true, true, false);

        if (discardRecording && restoredRerecordTail)
            setStatus ("Stopped, discarded rerecord take, and restored the original audio.");
        else
        {
            if (discardRecording)
            {
                setStatus ("Stopped and discarded recording.");
            }
            else if (wasRecording && tracksRecorded.size() > 1 && pendingTrackID.isInvalid())
            {
                setStatus ("Stopped. Recording was saved into " + juce::String (tracksRecorded.size()) + " tracks.");
            }
            else
            {
                setStatus ("Stopped. Recording was saved into the edit.");
            }
        }

        updateStateText();
    }

    void openCameraPanel()
    {
        if (cameraCapture != nullptr || videoExport != nullptr || edit->getTransport().isRecording())
            return;
        cameraCapture = std::make_unique<CameraCaptureProcess>();
        cameraAwaitingAudio = cameraStopping = cameraHasAudio = cameraShouldExport = quitAfterCamera = false;
        lastCameraPhase.clear();
        cameraFailure.clear();
        const auto result = cameraCapture->start (sessionDir);
        if (result.failed())
        {
            cameraCapture.reset();
            setStatus (result.getErrorMessage());
        }
        else
            setStatus ("Choose a camera and a separate reference microphone in the camera panel. Camera takes start at 0.");
    }

    void updateCameraCapture()
    {
        if (cameraCapture == nullptr)
            return;
        if (++cameraHeartbeatTicks % 12 == 0)
            cameraCapture->heartbeat();
        const auto state = cameraCapture->status();
        const auto phase = state["phase"].toString();
        if (phase.isNotEmpty() && phase != lastCameraPhase)
        {
            lastCameraPhase = phase;
            if (phase == "ready")
                setStatus ("Camera preview ready. Record will capture video + reference sound and Focusrite audio from the start.");
            else if (phase == "recording" && cameraAwaitingAudio)
            {
                cameraAwaitingAudio = false;
                startRecordingAtCursor (true);
                if (! edit->getTransport().isRecording())
                {
                    cameraCapture->send ("discard");
                    cameraStopping = true;
                }
            }
            else if (phase == "error")
            {
                cameraFailure = state["error"].toString();
                if (edit->getTransport().isRecording() || cameraAwaitingAudio)
                    stopTransport (false);
                cameraShouldExport = false;
                setStatus ("Camera failed; recorded audio was kept: " + cameraFailure);
            }
        }
        if (cameraCapture->isRunning())
            return;
        if (edit->getTransport().isRecording())
        {
            stopTransport (false);
            cameraShouldExport = false;
        }
        const auto diagnostics = cameraCapture->diagnostics();
        const bool compose = cameraShouldExport && phase == "complete";
        const juce::File video (state["video"].toString());
        cameraCapture.reset();
        cameraAwaitingAudio = cameraStopping = cameraHasAudio = cameraShouldExport = false;
        if (quitAfterCamera)
        {
            quitAfterCamera = false;
            juce::JUCEApplication::getInstance()->systemRequestedQuit();
            return;
        }
        if (compose && video.existsAsFile())
        {
            edit->state.setProperty ("coverVideoPath", video.getFullPathName(), nullptr);
            edit->state.setProperty ("coverVideoOffsetMs", 0.0, nullptr);
            saveSession();
            startVideoExport (video, 0.0, true);
        }
        else if (phase == "closed" || phase == "discarded")
            setStatus ("Camera closed. Existing audio and capture files were preserved.");
        else
            setStatus ("Camera stopped. Audio was preserved. " + cameraFailure + " " + diagnostics);
    }

    void clearPendingRerecord()
    {
        pendingRerecord = {};
    }

    bool restorePendingRerecordTail()
    {
        if (edit == nullptr || ! pendingRerecord.active)
        {
            clearPendingRerecord();
            return false;
        }

        if (pendingRerecord.restoreTrackState)
        {
            if (pendingRerecord.trackStateBeforeRerecord.isValid())
                if (auto* track = te::findTrackForID (*edit, pendingRerecord.trackID))
                {
                    track->state.copyPropertiesAndChildrenFrom (pendingRerecord.trackStateBeforeRerecord, nullptr);
                    edit->getTransport().editHasChanged();
                    selectionManager.selectOnly (track);
                    clearPendingRerecord();
                    return true;
                }

            clearPendingRerecord();
            return false;
        }

        if (pendingRerecord.clipID.isInvalid())
        {
            clearPendingRerecord();
            return false;
        }

        if (auto* clip = te::findClipForID (*edit, pendingRerecord.clipID))
        {
            clip->setEnd (pendingRerecord.originalEnd, true);
            edit->getTransport().editHasChanged();
            clearPendingRerecord();
            return true;
        }

        clearPendingRerecord();
        return false;
    }

    void setCursorSeconds (double seconds)
    {
        if (edit == nullptr)
            return;

        edit->getTransport().setPosition (secondsToPosition (seconds));
        ensureCursorVisibleInTimeline();
        updateStateText();
    }

    te::Clip* getSelectedClip() const
    {
        if (auto* clip = dynamic_cast<te::Clip*> (selectionManager.getSelectedObject (0)))
            return clip;

        return selectionManager.getFirstItemOfType<te::Clip>();
    }

    te::AudioClipBase* getSelectedAudioClip() const
    {
        return dynamic_cast<te::AudioClipBase*> (getSelectedClip());
    }

    te::AudioClipBase* getAudioClipForGain()
    {
        if (auto* selectedAudioClip = getSelectedAudioClip())
            return selectedAudioClip;

        return dynamic_cast<te::AudioClipBase*> (findClipAtCursorOnRecordingTrack());
    }

    void applyClickSafeEdgeFades (te::Clip* clip)
    {
        if (auto* audioClip = dynamic_cast<te::AudioClipBase*> (clip))
            audioClip->applyEdgeFades();
    }

    void applyClickSafeEdgeFades (te::AudioTrack& track)
    {
        for (auto* clip : track.getClips())
            applyClickSafeEdgeFades (clip);
    }

    te::AudioTrack* getSelectedAudioTrack() const
    {
        if (auto* clip = getSelectedClip())
            return dynamic_cast<te::AudioTrack*> (clip->getTrack());

        return dynamic_cast<te::AudioTrack*> (selectionManager.getSelectedObject (0));
    }

    te::AudioTrack* getTrackForLevel()
    {
        if (auto* clip = getSelectedClip())
            if (auto* track = dynamic_cast<te::AudioTrack*> (clip->getTrack()))
                return track;

        if (auto* selectedTrack = dynamic_cast<te::AudioTrack*> (selectionManager.getSelectedObject (0)))
            return selectedTrack;

        return getRecordingTrackForAction();
    }

    float getTrackLevelDb (te::AudioTrack& track) const
    {
        if (auto* volume = track.getVolumePlugin())
            return volume->getVolumeDb();

        return 0.0f;
    }

    void setCurrentTrackLevelDb (float db)
    {
        auto* track = getTrackForLevel();

        if (track == nullptr)
        {
            setStatus ("Choose a track or clip first, then adjust Track Level.");
            updateStateText();
            return;
        }

        if (auto* volume = track->getVolumePlugin())
        {
            volume->setVolumeDb (juce::jlimit (-48.0f, 12.0f, db));
            edit->getTransport().editHasChanged();
            te::EditFileOperations (*edit).save (true, true, false);
            setStatus ("Track level (whole track): " + track->getName() + " "
                       + juce::String (volume->getVolumeDb(), 1) + " dB.");
            updateStateText();
        }
    }

    void resetCurrentTrackLevel()
    {
        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("Choose a Track first, then reset Track Level.");
            updateStateText();
            return;
        }

        if (auto* volume = track->getVolumePlugin())
        {
            volume->setVolumeDb (0.0f);
            edit->getTransport().editHasChanged();
            te::EditFileOperations (*edit).save (true, true, false);
            setStatus ("Track level reset: " + track->getName() + " 0.0 dB.");
            updateStateText();
        }
    }

    juce::String describeTrackRecordState (te::AudioTrack& track) const
    {
        juce::String text;
        text << "Track: " << track.getName()
             << " | Level: " << juce::String (getTrackLevelDb (track), 1) << " dB"
             << " | Listen: " << describeTrackListenState (track)
             << " | Input: " << (hasWaveInputTargetingTrack (track) ? "connected" : "not set")
             << " | Ready: " << (EngineHelpers::isTrackArmed (track) ? "yes" : "no")
             << " | Hear input: " << (EngineHelpers::isInputMonitoringEnabled (track) ? "yes" : "no");
        return text;
    }

    juce::String describeTrackListenState (te::AudioTrack& track) const
    {
        const bool muted = track.isMuted (false);
        const bool solo = track.isSolo (false);

        if (muted && solo)
            return "muted + solo";

        if (muted)
            return "muted";

        if (solo)
            return "solo";

        return "normal";
    }

    void setActionTrackInput (int inputIndex)
    {
        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("Choose a track first, then choose an input.");
            updateStateText();
            return;
        }

        if (inputIndex < 0)
        {
            setStatus ("Choose an input first.");
            updateStateText();
            return;
        }

        const bool shouldRecord = EngineHelpers::isTrackArmed (*track) || recordTrackToggle.getToggleState();
        const bool shouldHearInput = inputMonitorToggle.getToggleState();

        if (! assignWaveInputToTrack (*track, inputIndex, shouldRecord))
        {
            setStatus ("Could not connect that input to " + track->getName());
            updateStateText();
            return;
        }

        if (shouldHearInput)
            setTrackInputMonitoring (*track, true);

        assignedInputCount = countAssignedWaveInputs();
        armedInputCount = countArmedWaveInputs();
        edit->getTransport().editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Input connected. " + describeRecordTrackRecordAction (*track));
        updateStateText();
    }

    void setActionTrackArmed (bool shouldArm)
    {
        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("No recording track is available.");
            updateStateText();
            return;
        }

        if (shouldArm)
        {
            if (! ensureTrackReadyForRecording (*track))
                return;
        }
        else
        {
            EngineHelpers::armTrack (*track, false);
            assignedInputCount = countAssignedWaveInputs();
            armedInputCount = countArmedWaveInputs();
            edit->getTransport().editHasChanged();
        }

        te::EditFileOperations (*edit).save (true, true, false);
        setStatus (describeRecordTrackRecordAction (*track));
        updateStateText();
    }

    void setOnlyCurrentTrackReady()
    {
        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("Choose a Track first, then make it the only recording Track.");
            updateStateText();
            return;
        }

        if (edit != nullptr && edit->getTransport().isRecording())
        {
            setStatus ("Stop recording before changing which Tracks are ready.");
            updateStateText();
            return;
        }

        if (! ensureTrackReadyForRecording (*track))
            return;

        int disabledCount = 0;

        for (auto* otherTrack : te::getAudioTracks (*edit))
        {
            if (otherTrack == nullptr || otherTrack == track)
                continue;

            if (EngineHelpers::isTrackArmed (*otherTrack))
            {
                EngineHelpers::armTrack (*otherTrack, false);
                ++disabledCount;
            }
        }

        assignedInputCount = countAssignedWaveInputs();
        armedInputCount = countArmedWaveInputs();
        selectionManager.selectOnly (track);
        edit->getTransport().editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);

        juce::String text = "Record will use only this Track: " + track->getName();

        if (disabledCount > 0)
            text << " (" << disabledCount << " other ready " << (disabledCount == 1 ? "Track" : "Tracks") << " turned off)";

        text << ".";
        setStatus (text);
        updateStateText();
    }

    void setActionTrackInputMonitoring (bool shouldEnable)
    {
        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("No recording track is available.");
            updateStateText();
            return;
        }

        if (! hasWaveInputTargetingTrack (*track))
        {
            if (! ensureTrackReadyForRecording (*track))
                return;
        }

        if (! setTrackInputMonitoring (*track, shouldEnable))
        {
            setStatus ("Could not change Hear input for the current track.");
            updateStateText();
            return;
        }

        edit->getTransport().editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus (juce::String (shouldEnable ? "Input audible: " : "Input muted: ") + track->getName());
        updateStateText();
    }

    void armSelectedTrack()
    {
        auto* track = getSelectedAudioTrack();

        if (track == nullptr)
        {
            setStatus ("Select an audio track or clip first, then arm it.");
            updateStateText();
            return;
        }

        if (ensureTrackReadyForRecording (*track))
        {
            te::EditFileOperations (*edit).save (true, true, false);
            setStatus ("Ready to record: " + track->getName());
            updateStateText();
        }
    }

    void unarmSelectedTrack()
    {
        auto* track = getSelectedAudioTrack();

        if (track == nullptr)
        {
            setStatus ("Select an audio track or clip first, then unarm it.");
            updateStateText();
            return;
        }

        EngineHelpers::armTrack (*track, false);
        assignedInputCount = countAssignedWaveInputs();
        armedInputCount = countArmedWaveInputs();
        edit->getTransport().editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Record disabled for: " + track->getName());
        updateStateText();
    }

    void unarmAllTracks()
    {
        if (edit == nullptr)
            return;

        for (auto* track : te::getAudioTracks (*edit))
            if (track != nullptr)
                EngineHelpers::armTrack (*track, false);

        assignedInputCount = countAssignedWaveInputs();
        armedInputCount = countArmedWaveInputs();
        edit->getTransport().editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Recording disabled for all tracks.");
        updateStateText();
    }

    void toggleSelectedTrackInputMonitoring()
    {
        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("Choose a track first, then change Hear input.");
            updateStateText();
            return;
        }

        if (! hasWaveInputTargetingTrack (*track))
        {
            if (! ensureTrackReadyForRecording (*track))
                return;
        }

        const bool shouldEnable = ! EngineHelpers::isInputMonitoringEnabled (*track);

        if (! setTrackInputMonitoring (*track, shouldEnable))
        {
            setStatus ("Could not change Hear input for the current track.");
            updateStateText();
            return;
        }

        edit->getTransport().editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus (juce::String ("Hear input ")
                   + (shouldEnable ? "enabled: " : "disabled: ")
                   + track->getName());
        updateStateText();
    }

    void toggleCurrentTrackMute()
    {
        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("Choose a Track first, then mute or unmute it.");
            updateStateText();
            return;
        }

        const bool shouldMute = ! track->isMuted (false);
        track->setMute (shouldMute);
        edit->getTransport().editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);

        setStatus (juce::String (shouldMute ? "Muted current Track: " : "Unmuted current Track: ") + track->getName());
        updateStateText();
    }

    void toggleCurrentTrackSolo()
    {
        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("Choose a Track first, then solo or unsolo it.");
            updateStateText();
            return;
        }

        const bool shouldSolo = ! track->isSolo (false);
        track->setSolo (shouldSolo);
        edit->getTransport().editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);

        setStatus (juce::String (shouldSolo ? "Solo current Track: " : "Unsolo current Track: ") + track->getName());
        updateStateText();
    }

    void clearAllTrackListenStates()
    {
        if (edit == nullptr)
            return;

        int changed = 0;

        for (auto* track : te::getAudioTracks (*edit))
        {
            if (track == nullptr)
                continue;

            const bool wasMuted = track->isMuted (false);
            const bool wasSolo = track->isSolo (false);

            if (wasMuted)
                track->setMute (false);

            if (wasSolo)
                track->setSolo (false);

            if (wasMuted || wasSolo)
                ++changed;
        }

        edit->getTransport().editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);

        setStatus (changed > 0 ? "Cleared mute/solo on all Tracks." : "No muted or soloed Tracks.");
        updateStateText();
    }

    void renameCurrentTrack()
    {
        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("Choose a Track first, then rename it.");
            updateStateText();
            return;
        }

        if (edit != nullptr && edit->getTransport().isRecording())
        {
            setStatus ("Stop recording before renaming a Track.");
            updateStateText();
            return;
        }

        const auto trackID = track->itemID;
        auto* alert = new juce::AlertWindow ("Rename Track",
                                             "Give the current Track a short name.",
                                             juce::MessageBoxIconType::NoIcon,
                                             this);

        alert->addTextEditor ("trackName", track->getName(), "Track name:", false);
        if (auto* editor = alert->getTextEditor ("trackName"))
            editor->selectAll();

        alert->addButton ("Rename", 1, juce::KeyPress (juce::KeyPress::returnKey));
        alert->addButton ("Cancel", 0, juce::KeyPress (juce::KeyPress::escapeKey));

        const juce::Component::SafePointer<CoverRecorderComponent> safeThis (this);
        const juce::Component::SafePointer<juce::AlertWindow> safeAlert (alert);

        alert->enterModalState (true,
                                juce::ModalCallbackFunction::create (
                                    [safeThis, safeAlert, trackID] (int result)
                                    {
                                        if (safeThis == nullptr || safeAlert == nullptr)
                                            return;

                                        if (result != 1)
                                        {
                                            safeThis->setStatus ("Track rename cancelled.");
                                            safeThis->updateStateText();
                                            return;
                                        }

                                        auto newName = safeAlert->getTextEditorContents ("trackName").trim()
                                                                                                  .substring (0, 64);

                                        if (newName.isEmpty())
                                        {
                                            safeThis->setStatus ("Track name was left unchanged.");
                                            safeThis->updateStateText();
                                            return;
                                        }

                                        if (safeThis->edit == nullptr)
                                            return;

                                        auto* trackToRename = dynamic_cast<te::AudioTrack*> (te::findTrackForID (*safeThis->edit, trackID));

                                        if (trackToRename == nullptr)
                                        {
                                            safeThis->setStatus ("Could not find the Track to rename.");
                                            safeThis->updateStateText();
                                            return;
                                        }

                                        if (trackToRename->getName() == newName)
                                        {
                                            safeThis->setStatus ("Track name was left unchanged.");
                                            safeThis->updateStateText();
                                            return;
                                        }

                                        trackToRename->setName (newName);
                                        safeThis->edit->getTransport().editHasChanged();
                                        te::EditFileOperations (*safeThis->edit).save (true, true, false);
                                        safeThis->selectionManager.selectOnly (trackToRename);
                                        safeThis->setStatus ("Renamed current Track: " + newName);
                                        safeThis->updateStateText();
                                    }),
                                true);
    }

    bool cursorIsInsideSelectedClip (te::Clip& clip, te::TimePosition editCursor) const
    {
        const auto range = clip.getEditTimeRange();
        return editCursor > range.getStart() && editCursor < range.getEnd();
    }

    te::Clip* findClipAtCursorOnRecordingTrack()
    {
        if (edit == nullptr)
            return nullptr;

        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
            return nullptr;

        const auto editCursor = edit->getTransport().getPosition();
        auto clips = track->getClips();

        for (auto* clip : clips)
            if (clip != nullptr && cursorIsInsideSelectedClip (*clip, editCursor))
                return clip;

        return nullptr;
    }

    te::Clip* findClipForCursorEdit()
    {
        if (edit == nullptr)
            return nullptr;

        const auto editCursor = edit->getTransport().getPosition();

        if (auto* selectedClip = getSelectedClip())
            if (cursorIsInsideSelectedClip (*selectedClip, editCursor))
                return selectedClip;

        return findClipAtCursorOnRecordingTrack();
    }

    te::Clip* getClipForCursorEdit()
    {
        auto* clip = findClipForCursorEdit();

        if (clip != nullptr)
            selectionManager.selectOnly (clip);

        return clip;
    }

    te::Clip* getClipForSelectionOrCursor()
    {
        if (auto* clip = getSelectedClip())
            return clip;

        return getClipForCursorEdit();
    }

    void rerecordCurrentTrackFromCursor()
    {
        if (edit == nullptr)
            return;

        auto* track = getRecordingTrackForAction();

        if (track == nullptr)
        {
            setStatus ("Choose a recording track first, then replace from the cursor.");
            updateStateText();
            return;
        }

        auto& transport = edit->getTransport();

        if (transport.isRecording())
            return;

        if (transport.isPlaying())
            transport.stop (false, false);

        if (! ensureTrackReadyForRecording (*track))
            return;

        const auto editCursor = transport.getPosition();
        const auto rangeEndSeconds = std::max (getTimelineLengthSeconds(), editCursor.inSeconds() + 1.0);
        const auto replaceRange = te::TimeRange (editCursor, secondsToPosition (rangeEndSeconds));

        pendingRerecord.active = true;
        pendingRerecord.restoreTrackState = true;
        pendingRerecord.trackID = track->itemID;
        pendingRerecord.trackStateBeforeRerecord = track->state.createCopy();

        track->deleteRegion (replaceRange, nullptr);
        applyClickSafeEdgeFades (*track);
        selectionManager.selectOnly (track);
        transport.setPosition (editCursor);
        transport.editHasChanged();
        refreshTimelineRange();
        te::EditFileOperations (*edit).save (true, true, false);

        transport.record (false);
        setStatus ("Replacing " + track->getName() + " from cursor. Stop keeps it; Discard Take restores the old track tail.");
        updateStateText();
    }

    void moveCursorToSelectedClipBoundary (bool toStart)
    {
        auto* clip = getClipForSelectionOrCursor();

        if (clip == nullptr)
        {
            setStatus ("Select a clip, or move the cursor inside one, then jump to its boundary.");
            updateStateText();
            return;
        }

        if (edit->getTransport().isRecording())
        {
            setStatus ("Stop recording before moving the cursor to a clip boundary.");
            updateStateText();
            return;
        }

        const auto range = clip->getEditTimeRange();
        edit->getTransport().setPosition (toStart ? range.getStart() : range.getEnd());
        ensureCursorVisibleInTimeline();
        setStatus (toStart ? "Cursor moved to clip start." : "Cursor moved to clip end.");
        updateStateText();
    }

    void splitSelectedClipAtCursor()
    {
        auto* clip = getClipForCursorEdit();

        if (clip == nullptr)
        {
            setStatus ("Move the cursor inside a clip on the current Track, then split.");
            return;
        }

        const auto editCursor = edit->getTransport().getPosition();

        if (! cursorIsInsideSelectedClip (*clip, editCursor))
        {
            setStatus ("Cursor must be inside the target clip to split.");
            return;
        }

        if (auto* track = clip->getClipTrack())
        {
            if (auto* newClip = track->splitClip (*clip, editCursor))
            {
                applyClickSafeEdgeFades (clip);
                applyClickSafeEdgeFades (newClip);
                selectionManager.selectOnly (newClip);
            }

            edit->getTransport().editHasChanged();
            te::EditFileOperations (*edit).save (true, true, false);
            setStatus ("Split clip at cursor.");
        }
    }

    void trimSelectedClipLeftToCursor()
    {
        auto* clip = getClipForCursorEdit();

        if (clip == nullptr)
        {
            setStatus ("Move the cursor inside a clip on the current Track, then cut before the cursor.");
            return;
        }

        const auto editCursor = edit->getTransport().getPosition();

        if (! cursorIsInsideSelectedClip (*clip, editCursor))
        {
            setStatus ("Cursor must be inside the target clip to trim.");
            return;
        }

        clip->setStart (editCursor, true, false);
        applyClickSafeEdgeFades (clip);
        edit->getTransport().editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Cut off the clip audio before the cursor.");
    }

    void trimSelectedClipRightToCursor()
    {
        auto* clip = getClipForCursorEdit();

        if (clip == nullptr)
        {
            setStatus ("Move the cursor inside a clip on the current Track, then cut after the cursor.");
            return;
        }

        const auto editCursor = edit->getTransport().getPosition();

        if (! cursorIsInsideSelectedClip (*clip, editCursor))
        {
            setStatus ("Cursor must be inside the target clip to trim.");
            return;
        }

        clip->setEnd (editCursor, true);
        applyClickSafeEdgeFades (clip);
        edit->getTransport().editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Cut off the clip audio after the cursor.");
    }

    void rerecordSelectedClipFromCursor()
    {
        auto* clip = getClipForCursorEdit();

        if (clip == nullptr)
        {
            setStatus ("Move the cursor inside an existing clip on the current Track, or click a clip first.");
            return;
        }

        auto* track = dynamic_cast<te::AudioTrack*> (clip->getTrack());

        if (track == nullptr)
        {
            setStatus ("The selected clip is not on an audio track.");
            return;
        }

        const auto editCursor = edit->getTransport().getPosition();

        if (! cursorIsInsideSelectedClip (*clip, editCursor))
        {
            setStatus ("Cursor must be inside the selected clip to rerecord from there.");
            return;
        }

        auto& transport = edit->getTransport();

        if (transport.isPlaying() || transport.isRecording())
            transport.stop (false, false);

        if (! ensureTrackReadyForRecording (*track))
            return;

        pendingRerecord.active = true;
        pendingRerecord.restoreTrackState = true;
        pendingRerecord.clipID = clip->itemID;
        pendingRerecord.trackID = track->itemID;
        pendingRerecord.originalEnd = clip->getEditTimeRange().getEnd();
        pendingRerecord.trackStateBeforeRerecord = track->state.createCopy();

        clip->setEnd (editCursor, true);
        applyClickSafeEdgeFades (clip);
        transport.setPosition (editCursor);
        selectionManager.selectOnly (track);
        transport.editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);

        transport.record (false);
        setStatus ("Re-recording from cursor. Press Stop to keep it or Discard Take to throw it away.");
        updateStateText();
    }

    void adjustSelectedClipGain (float deltaDb)
    {
        auto* clip = getAudioClipForGain();

        if (clip == nullptr)
        {
            setStatus ("Select an audio clip, or move the cursor inside one, then adjust gain.");
            return;
        }

        setSelectedClipGainDb (clip->getGainDB() + deltaDb);
    }

    void resetSelectedClipGain()
    {
        auto* clip = getAudioClipForGain();

        if (clip == nullptr)
        {
            setStatus ("Select an audio clip, or move the cursor inside one, then reset gain.");
            return;
        }

        setSelectedClipGainDb (0.0f);
    }

    void setSelectedClipGainDb (float gainDb)
    {
        auto* clip = getAudioClipForGain();

        if (clip == nullptr)
        {
            setStatus ("Select an audio clip, or move the cursor inside one, then adjust gain.");
            updateStateText();
            return;
        }

        const auto clampedGain = juce::jlimit (-48.0f, 24.0f, gainDb);
        clip->setGainDB (clampedGain);
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Clip gain (this clip only): " + clip->getName() + " "
                   + juce::String (clip->getGainDB(), 1) + " dB.");
        updateStateText();
    }

    void deleteSelected()
    {
        if (getSelectedClip() == nullptr)
        {
            setStatus ("Click a waveform clip first, then delete.");
            return;
        }

        selectionManager.deleteSelected();
        refreshTimelineRange();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Deleted selected item.");
        updateStateText();
    }

    void deleteClipAtSelectionOrCursor()
    {
        auto* clip = getSelectedClip();

        if (clip == nullptr)
            clip = getClipForCursorEdit();

        if (clip == nullptr)
        {
            setStatus ("Click a waveform clip, or move the cursor inside one, then delete.");
            return;
        }

        selectionManager.selectOnly (clip);
        selectionManager.deleteSelected();
        refreshTimelineRange();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Deleted clip.");
        updateStateText();
    }

    void toggleClipMuteAtSelectionOrCursor()
    {
        auto* clip = getSelectedClip();

        if (clip == nullptr)
            clip = getClipForCursorEdit();

        if (clip == nullptr)
        {
            setStatus ("Click a waveform clip, or move the cursor inside one, then mute or unmute.");
            updateStateText();
            return;
        }

        const bool shouldMute = ! clip->isMuted();
        clip->setMuted (shouldMute);
        selectionManager.selectOnly (clip);
        edit->getTransport().editHasChanged();
        te::EditFileOperations (*edit).save (true, true, false);

        setStatus (juce::String (shouldMute ? "Muted clip: " : "Unmuted clip: ") + clip->getName());
        updateStateText();
    }

    int countMutedClips()
    {
        if (edit == nullptr)
            return 0;

        int mutedClips = 0;

        for (auto* track : te::getAudioTracks (*edit))
        {
            if (track == nullptr)
                continue;

            for (auto* clip : track->getClips())
                if (clip != nullptr && clip->isMuted())
                    ++mutedClips;
        }

        return mutedClips;
    }

    void unmuteAllClips()
    {
        if (edit == nullptr)
            return;

        if (edit->getTransport().isRecording())
        {
            setStatus ("Stop recording before unmuting clips.");
            updateStateText();
            return;
        }

        int changed = 0;

        for (auto* track : te::getAudioTracks (*edit))
        {
            if (track == nullptr)
                continue;

            for (auto* clip : track->getClips())
            {
                if (clip == nullptr || ! clip->isMuted())
                    continue;

                clip->setMuted (false);
                ++changed;
            }
        }

        if (changed > 0)
        {
            edit->getTransport().editHasChanged();
            te::EditFileOperations (*edit).save (true, true, false);
            setStatus ("Unmuted " + juce::String (changed) + " clips.");
        }
        else
        {
            setStatus ("No muted clips.");
        }

        updateStateText();
    }

    void undoEdit()
    {
        if (edit == nullptr)
            return;

        auto& transport = edit->getTransport();

        if (transport.isRecording())
        {
            setStatus ("Stop recording before undo.");
            return;
        }

        if (transport.isPlaying())
            transport.stop (false, false);

        auto& undoManager = edit->getUndoManager();

        if (! undoManager.canUndo())
        {
            setStatus ("Nothing to undo.");
            updateStateText();
            return;
        }

        const auto description = undoManager.getUndoDescription();
        edit->undo();
        clearPendingRerecord();
        refreshTimelineRange();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Undo" + (description.isNotEmpty() ? ": " + description : juce::String()));
        updateStateText();
    }

    void redoEdit()
    {
        if (edit == nullptr)
            return;

        auto& transport = edit->getTransport();

        if (transport.isRecording())
        {
            setStatus ("Stop recording before redo.");
            return;
        }

        if (transport.isPlaying())
            transport.stop (false, false);

        auto& undoManager = edit->getUndoManager();

        if (! undoManager.canRedo())
        {
            setStatus ("Nothing to redo.");
            updateStateText();
            return;
        }

        const auto description = undoManager.getRedoDescription();
        edit->redo();
        clearPendingRerecord();
        refreshTimelineRange();
        te::EditFileOperations (*edit).save (true, true, false);
        setStatus ("Redo" + (description.isNotEmpty() ? ": " + description : juce::String()));
        updateStateText();
    }

    void renderMixdown()
    {
        if (edit == nullptr)
            return;

        const auto missing = findMissingMedia (*edit);
        if (! missing.isEmpty())
        {
            showMissingMedia (missing);
            return;
        }

        auto& transport = edit->getTransport();

        if (transport.isPlaying())
            transport.stop (false, false);

        const auto renderLength = backingLength.inSeconds() > 0.0 ? backingLength : edit->getLength();

        if (renderLength.inSeconds() <= 0.0)
        {
            setStatus ("Nothing to render yet. Import a backing track or record audio first.");
            return;
        }

        setStatus ("Rendering mixdown WAV...");
        const auto result = sessionIO::render (*edit, sessionDir, renderLength, renderedMixFile);

        te::EditFileOperations (*edit).save (true, true, false);

        if (result.wasOk())
            setStatus ("Rendered mixdown WAV: " + renderedMixFile.getFullPathName());
        else
            setStatus (result.getErrorMessage());

        updateStateText();
    }

    void chooseVideoForExport()
    {
        if (edit == nullptr || edit->getTransport().isRecording() || fileChooser != nullptr || videoExport != nullptr)
            return;
        if (! VideoExportProcess::pythonExecutable().existsAsFile())
        {
            setStatus ("Video export needs cover-syncer. Set COVER_RECORDER_PYTHON if installed elsewhere.");
            return;
        }
        const juce::File previous (edit->state.getProperty ("coverVideoPath").toString());
        fileChooser = std::make_unique<juce::FileChooser> ("Choose the phone video of this performance",
            previous.existsAsFile() ? previous : sessionDir,
            "*.mp4;*.mov;*.m4v;*.mkv;*.avi;*.wmv;*.webm;*.mpg;*.mpeg;*.mts;*.m2ts;*.ts;*.flv;*.3gp");
        const juce::Component::SafePointer<CoverRecorderComponent> safeThis (this);
        fileChooser->launchAsync (juce::FileBrowserComponent::openMode | juce::FileBrowserComponent::canSelectFiles,
            [safeThis] (const juce::FileChooser& chooser)
            {
                if (safeThis == nullptr)
                    return;
                const auto video = chooser.getResult();
                safeThis->fileChooser.reset();
                if (video.existsAsFile())
                    safeThis->promptVideoExport (video);
            });
    }

    void promptVideoExport (const juce::File& video)
    {
        auto* dialog = new juce::AlertWindow ("Export cover video",
            video.getFileName() + "\n\nAudio offset in milliseconds: positive delays the mix; negative trims its start."
            "\nKeeps the full video length. Use video from this same continuous performance."
            "\nA new cover.mp4 will be saved in the session folder.", juce::MessageBoxIconType::NoIcon);
        dialog->addTextEditor ("offset", edit->state.getProperty ("coverVideoOffsetMs", 0).toString(), "Offset (ms)");
        dialog->addButton ("Export", 1, juce::KeyPress (juce::KeyPress::returnKey));
        dialog->addButton ("Cancel", 0, juce::KeyPress (juce::KeyPress::escapeKey));
        const juce::Component::SafePointer<CoverRecorderComponent> safeThis (this);
        dialog->enterModalState (true, juce::ModalCallbackFunction::create ([safeThis, dialog, video] (int result)
        {
            if (safeThis == nullptr || result != 1)
                return;
            const auto text = dialog->getTextEditorContents ("offset").trim().toStdString();
            try
            {
                size_t consumed = 0;
                const auto offset = std::stod (text, &consumed);
                if (consumed != text.size() || ! std::isfinite (offset) || std::abs (offset) > 3600000.0)
                    throw std::invalid_argument ("offset");
                safeThis->startVideoExport (video, offset);
            }
            catch (const std::exception&)
            {
                safeThis->setStatus ("Enter a valid offset between -3600000 and 3600000 milliseconds.");
            }
        }), true);
    }

    void startVideoExport (const juce::File& video, double offset, bool autoAlign = false)
    {
        if (edit == nullptr || edit->getTransport().isRecording() || videoExport != nullptr)
            return;
        edit->getTransport().stop (false, false);
        const auto missing = findMissingMedia (*edit);
        if (! missing.isEmpty())
        {
            showMissingMedia (missing);
            return;
        }
        setStatus ("Preparing the current mix for video export...");
        const auto rendered = sessionIO::render (*edit, sessionDir, backingLength, renderedMixFile);
        if (rendered.failed())
        {
            setStatus (rendered.getErrorMessage());
            return;
        }
        edit->state.setProperty ("coverVideoPath", video.getFullPathName(), nullptr);
        edit->state.setProperty ("coverVideoOffsetMs", offset, nullptr);
        if (! saveSession())
            return;
        videoOutputFile = sessionDir.getChildFile ("cover.mp4").getNonexistentSibling();
        juce::DynamicObject::Ptr job = new juce::DynamicObject();
        job->setProperty ("version", 1);
        job->setProperty ("video", video.getFullPathName());
        job->setProperty ("audio", renderedMixFile.getFullPathName());
        job->setProperty ("output", videoOutputFile.getFullPathName());
        job->setProperty ("offset_ms", offset);
        job->setProperty ("auto_align", autoAlign);
        const auto jobFile = sessionDir.getChildFile ("video-export.json").getNonexistentSibling();
        if (! jobFile.replaceWithText (juce::JSON::toString (juce::var (job.get()))))
        {
            setStatus ("Could not write the video export job.");
            return;
        }
        videoExport = std::make_unique<VideoExportProcess>();
        const auto started = videoExport->start (jobFile);
        if (started.failed())
        {
            videoExport.reset();
            setStatus (started.getErrorMessage());
            return;
        }
        setEnabled (false);
        setStatus ("Exporting video... " + videoOutputFile.getFileName());
    }

    void showMoreMenu()
    {
        if (edit == nullptr)
            return;

        const auto& transport = edit->getTransport();
        const bool canEdit = ! transport.isRecording();

        enum
        {
            goToStartId = 1,
            importAudioId,
            undoId,
            redoId,
            zoomOutId,
            zoomInId,
            fitViewId,
            openFolderId,
            saveSessionId,
            openSessionId,
            exportVideoId,
            cameraId
        };

        juce::PopupMenu menu;
        menu.addItem (cameraId, "Camera + reference sound...", canEdit && cameraCapture == nullptr && videoExport == nullptr);
        menu.addItem (exportVideoId, "Export video...", canEdit && fileChooser == nullptr && videoExport == nullptr && cameraCapture == nullptr);
        menu.addSeparator();
        menu.addItem (saveSessionId, "Save session (Ctrl+S)", canEdit);
        menu.addItem (openSessionId, "Open session... (Ctrl+O)", canEdit && fileChooser == nullptr && cameraCapture == nullptr);
        menu.addSeparator();
        menu.addItem (importAudioId, "Import audio to current track", canEdit && getRecordingTrackForAction() != nullptr);
        menu.addSeparator();
        menu.addItem (goToStartId, "Go to start", canEdit);
        menu.addSeparator();
        menu.addItem (undoId, "Undo", canEdit && edit->getUndoManager().canUndo());
        menu.addItem (redoId, "Redo", canEdit && edit->getUndoManager().canRedo());
        menu.addSeparator();
        menu.addItem (zoomOutId, "Zoom out", editComponent != nullptr);
        menu.addItem (zoomInId, "Zoom in", editComponent != nullptr);
        menu.addItem (fitViewId, "Fit view", editComponent != nullptr);
        menu.addSeparator();
        menu.addItem (openFolderId, "Open session folder", sessionDir.exists());

        const juce::Component::SafePointer<CoverRecorderComponent> safeThis (this);
        menu.showMenuAsync (juce::PopupMenu::Options().withTargetComponent (moreButton),
                            [safeThis] (int result)
                            {
                                if (safeThis == nullptr)
                                    return;

                                switch (result)
                                {
                                    case cameraId: safeThis->openCameraPanel(); break;
                                    case exportVideoId: safeThis->chooseVideoForExport(); break;
                                    case saveSessionId: safeThis->saveSession(); break;
                                    case openSessionId: safeThis->chooseSessionFile(); break;
                                    case importAudioId: safeThis->chooseAudioFileForCurrentTrack(); break;
                                    case goToStartId:  safeThis->setCursorSeconds (0.0); break;
                                    case undoId:       safeThis->undoEdit(); break;
                                    case redoId:       safeThis->redoEdit(); break;
                                    case zoomOutId:    safeThis->zoomTimeline (2.0); break;
                                    case zoomInId:     safeThis->zoomTimeline (0.5); break;
                                    case fitViewId:    safeThis->fitTimelineToContent(); break;
                                    case openFolderId: safeThis->sessionDir.revealToUser(); break;
                                    default: break;
                                }
                            });
    }

    void showClipMenu()
    {
        if (edit == nullptr)
            return;

        const bool canEditSelection = ! edit->getTransport().isRecording();
        const bool hasSelectedClip = getSelectedClip() != nullptr;
        const bool hasCursorEditClip = findClipForCursorEdit() != nullptr;
        auto* menuClip = getSelectedClip();
        if (menuClip == nullptr)
            menuClip = findClipForCursorEdit();
        const bool menuClipMuted = menuClip != nullptr && menuClip->isMuted();
        const int mutedClipCount = countMutedClips();
        auto* gainClip = getAudioClipForGain();
        const bool canResetClipGain = gainClip != nullptr && std::abs (gainClip->getGainDB()) > 0.01f;
        auto* actionTrack = getRecordingTrackForAction();
        const bool hasActionTrack = actionTrack != nullptr;
        const bool actionTrackMuted = actionTrack != nullptr && actionTrack->isMuted (false);
        const bool actionTrackSolo = actionTrack != nullptr && actionTrack->isSolo (false);
        const bool canResetTrackLevel = actionTrack != nullptr && std::abs (getTrackLevelDb (*actionTrack)) > 0.01f;
        const bool hasAnyListenState = edit->areAnyTracksMuted() || edit->areAnyTracksSolo();

        if (! hasSelectedClip && ! hasCursorEditClip && ! hasActionTrack)
        {
            setStatus ("Click a waveform clip first, or move the cursor inside a clip on the current Track.");
            return;
        }

        enum
        {
            replaceTrackId = 1,
            recordOnlyTrackId,
            rerecordId,
            splitId,
            trimStartId,
            trimEndId,
            resetClipGainId,
            muteClipId,
            unmuteAllClipsId,
            clipStartId,
            clipEndId,
            deleteId,
            renameTrackId,
            resetTrackLevelId,
            muteTrackId,
            soloTrackId,
            clearListenId
        };

        juce::PopupMenu menu;
        menu.addItem (replaceTrackId, "Record over track from cursor", hasActionTrack && canEditSelection);
        menu.addItem (recordOnlyTrackId, "Record only this Track", hasActionTrack && canEditSelection);
        menu.addItem (renameTrackId, "Rename current Track...", hasActionTrack && canEditSelection);
        menu.addItem (resetTrackLevelId, "Reset Track Level", canResetTrackLevel && canEditSelection);
        menu.addItem (muteTrackId, actionTrackMuted ? "Unmute current Track" : "Mute current Track", hasActionTrack && canEditSelection);
        menu.addItem (soloTrackId, actionTrackSolo ? "Unsolo current Track" : "Solo current Track", hasActionTrack && canEditSelection);
        menu.addItem (clearListenId, "Clear all mute/solo", hasAnyListenState && canEditSelection);
        menu.addSeparator();
        menu.addItem (rerecordId, "Re-record here", hasCursorEditClip && canEditSelection);
        menu.addItem (splitId, "Split at cursor", hasCursorEditClip && canEditSelection);
        menu.addItem (trimStartId, "Cut before cursor", hasCursorEditClip && canEditSelection);
        menu.addItem (trimEndId, "Cut after cursor", hasCursorEditClip && canEditSelection);
        menu.addItem (resetClipGainId, "Reset clip gain", canResetClipGain && canEditSelection);
        menu.addItem (muteClipId, menuClipMuted ? "Unmute clip" : "Mute clip", menuClip != nullptr && canEditSelection);
        menu.addItem (unmuteAllClipsId, "Unmute all clips", mutedClipCount > 0 && canEditSelection);
        menu.addSeparator();
        menu.addItem (clipStartId, "Jump to clip start", (hasSelectedClip || hasCursorEditClip) && canEditSelection);
        menu.addItem (clipEndId, "Jump to clip end", (hasSelectedClip || hasCursorEditClip) && canEditSelection);
        menu.addSeparator();
        menu.addItem (deleteId, "Delete clip", (hasSelectedClip || hasCursorEditClip) && canEditSelection);

        const juce::Component::SafePointer<CoverRecorderComponent> safeThis (this);
        menu.showMenuAsync (juce::PopupMenu::Options().withTargetComponent (clipMenuButton),
                            [safeThis] (int result)
                            {
                                if (safeThis == nullptr)
                                    return;

                                switch (result)
                                {
                                    case replaceTrackId:    safeThis->rerecordCurrentTrackFromCursor(); break;
                                    case recordOnlyTrackId: safeThis->setOnlyCurrentTrackReady(); break;
                                    case rerecordId:        safeThis->rerecordSelectedClipFromCursor(); break;
                                    case splitId:           safeThis->splitSelectedClipAtCursor(); break;
                                    case trimStartId:       safeThis->trimSelectedClipLeftToCursor(); break;
                                    case trimEndId:         safeThis->trimSelectedClipRightToCursor(); break;
                                    case resetClipGainId:   safeThis->resetSelectedClipGain(); break;
                                    case muteClipId:        safeThis->toggleClipMuteAtSelectionOrCursor(); break;
                                    case unmuteAllClipsId:  safeThis->unmuteAllClips(); break;
                                    case clipStartId:       safeThis->moveCursorToSelectedClipBoundary (true); break;
                                    case clipEndId:         safeThis->moveCursorToSelectedClipBoundary (false); break;
                                    case deleteId:          safeThis->deleteClipAtSelectionOrCursor(); break;
                                    case renameTrackId:     safeThis->renameCurrentTrack(); break;
                                    case resetTrackLevelId: safeThis->resetCurrentTrackLevel(); break;
                                    case muteTrackId:       safeThis->toggleCurrentTrackMute(); break;
                                    case soloTrackId:       safeThis->toggleCurrentTrackSolo(); break;
                                    case clearListenId:     safeThis->clearAllTrackListenStates(); break;
                                    default: break;
                                }
                            });
    }

    void setStatus (const juce::String& message)
    {
        statusLabel.setText ("Status: " + message, juce::dontSendNotification);
    }

    juce::String describeClipTarget (te::Clip& clip, const juce::String& label)
    {
        juce::String text = label + ": " + clip.getName();
        text << " | Clip: " << (clip.isMuted() ? "muted" : "audible");

        if (auto* audioClip = dynamic_cast<te::AudioClipBase*> (&clip))
            text << " | Clip gain: " << juce::String (audioClip->getGainDB(), 1) << " dB";

        if (auto* track = dynamic_cast<te::AudioTrack*> (clip.getTrack()))
            text << " | " << describeTrackRecordState (*track);

        return text;
    }

    juce::String describeSelection()
    {
        if (auto* clip = getSelectedClip())
            return describeClipTarget (*clip, "Clip");

        if (auto* cursorClip = findClipAtCursorOnRecordingTrack())
            return describeClipTarget (*cursorClip, "Cursor clip");

        if (auto* audioTrack = dynamic_cast<te::AudioTrack*> (selectionManager.getSelectedObject (0)))
            return describeTrackRecordState (*audioTrack);

        if (auto* track = dynamic_cast<te::Track*> (selectionManager.getSelectedObject (0)))
            return "Track: " + track->getName();

        if (auto* actionTrack = getRecordingTrackForAction())
            return describeTrackRecordState (*actionTrack);

        return "No clip selected";
    }

    void updateStateText()
    {
        if (edit == nullptr)
            return;

        setupRecordingDevices (false);
        refreshTimelineRange();
        syncRecordTrackBox();
        syncInputBox();

        const auto& transport = edit->getTransport();
        const auto cursorSeconds = transport.getPosition().inSeconds();
        const auto maxSeconds = getTimelineLengthSeconds();

        updatingCursorSlider = true;
        cursorSlider.setRange (0.0, maxSeconds, 0.001);
        cursorSlider.setValue (juce::jlimit (0.0, maxSeconds, cursorSeconds), juce::dontSendNotification);
        updatingCursorSlider = false;

        cursorLabel.setText ("Cursor: " + juce::String (cursorSeconds, 2) + "s", juce::dontSendNotification);

        const auto backingText = backingFile.existsAsFile()
            ? backingFile.getFileName() + " (" + formatDuration (backingLength) + ")"
            : juce::String ("no backing loaded");
        const auto readyTracks = getArmedRecordingTracks();

        sessionLabel.setText ("Backing: " + backingText
                              + " | Input devices: " + juce::String (availableInputCount)
                              + " | " + describeReadyTracks (false)
                              + " | Session folder: More > Open session folder",
                              juce::dontSendNotification);

        const auto mode = transport.isRecording() ? "Recording" : (transport.isPlaying() ? "Playing" : "Stopped");
        selectionLabel.setText (describeSelection() + " | " + mode, juce::dontSendNotification);

        playButton.setButtonText (transport.isPlaying() ? "Pause" : "Play");
        recordButton.setButtonText (transport.isRecording() ? "Recording..."
                                                            : (readyTracks.size() > 1
                                                                ? "Record " + juce::String (readyTracks.size()) + " Tracks"
                                                                : "Record"));
        const bool cameraBusy = cameraAwaitingAudio || cameraStopping;
        recordButton.setEnabled (! transport.isRecording() && ! cameraBusy);
        playButton.setEnabled (! cameraBusy);
        stopButton.setEnabled (! cameraStopping && (transport.isPlaying() || transport.isRecording() || cameraAwaitingAudio));
        discardButton.setEnabled (transport.isRecording());
        renderButton.setEnabled (! transport.isRecording() && ! cameraBusy);
        addTrackButton.setEnabled (! transport.isRecording() && ! cameraBusy);
        backingButton.setEnabled (! transport.isRecording() && ! cameraBusy);
        audioSettingsButton.setEnabled (! transport.isRecording() && ! cameraBusy);
        moreButton.setEnabled (! cameraBusy);
        recordTrackBox.setEnabled (! cameraBusy);
        recordInputBox.setEnabled (! cameraBusy && ! transport.isRecording());
        cursorSlider.setEnabled (! cameraBusy && ! transport.isRecording());

        auto* actionRecordingTrack = getRecordingTrackForAction();

        recordTrackToggle.setEnabled (actionRecordingTrack != nullptr && ! transport.isRecording() && ! cameraBusy);
        recordTrackToggle.setToggleState (actionRecordingTrack != nullptr && EngineHelpers::isTrackArmed (*actionRecordingTrack),
                                          juce::dontSendNotification);
        inputMonitorToggle.setEnabled (actionRecordingTrack != nullptr && ! transport.isRecording() && ! cameraBusy);
        inputMonitorToggle.setToggleState (actionRecordingTrack != nullptr && EngineHelpers::isInputMonitoringEnabled (*actionRecordingTrack),
                                           juce::dontSendNotification);
        updateInputLevelMeter();

        const bool hasClip = getSelectedClip() != nullptr;
        auto* gainClip = getAudioClipForGain();
        const bool hasAudioClip = gainClip != nullptr;
        const bool hasRerecordTarget = hasClip || findClipAtCursorOnRecordingTrack() != nullptr;
        const bool hasTrackEditTarget = actionRecordingTrack != nullptr;
        const bool canEditSelection = ! transport.isRecording() && ! cameraBusy;

        clipMenuButton.setButtonText (hasRerecordTarget ? "Clip Edit" : (hasTrackEditTarget ? "Track Edit" : "Select Clip"));
        clipMenuButton.setEnabled (! transport.isRecording() && ! cameraBusy && cameraCapture == nullptr);

        auto* levelTrack = getTrackForLevel();
        updatingTrackLevelSlider = true;
        trackLevelSlider.setValue (levelTrack != nullptr ? getTrackLevelDb (*levelTrack) : 0.0f,
                                   juce::dontSendNotification);
        updatingTrackLevelSlider = false;

        trackLevelLabel.setEnabled (levelTrack != nullptr && canEditSelection);
        trackLevelSlider.setEnabled (levelTrack != nullptr && canEditSelection);

        updatingGainSlider = true;
        if (gainClip != nullptr)
            clipGainSlider.setValue (gainClip->getGainDB(), juce::dontSendNotification);
        else
            clipGainSlider.setValue (0.0, juce::dontSendNotification);
        updatingGainSlider = false;

        clipGainLabel.setEnabled (hasAudioClip && canEditSelection);
        clipGainSlider.setEnabled (hasAudioClip && canEditSelection);
    }

    void changeListenerCallback (juce::ChangeBroadcaster*) override
    {
        updateStateText();
    }

    void timerCallback() override
    {
        updateCameraCapture();
        if (videoExport != nullptr && ! videoExport->isRunning())
        {
            const auto result = videoExport->finish();
            const auto response = videoExport->getResponse();
            videoExport.reset();
            setEnabled (true);
            if (result.wasOk() && response.hasProperty ("offset_ms"))
            {
                edit->state.setProperty ("coverVideoOffsetMs", response["offset_ms"], nullptr);
                saveSession();
            }
            if (static_cast<bool> (response["needs_manual"]))
            {
                setStatus ("Video and WAV saved. Check the suggested offset and export manually.");
                promptVideoExport (juce::File (edit->state.getProperty ("coverVideoPath").toString()));
                updateStateText();
                return;
            }
            setStatus (result.wasOk() ? "Exported video: " + videoOutputFile.getFullPathName()
                                     : "Video export failed: " + result.getErrorMessage());
            if (result.failed())
                juce::AlertWindow::showMessageBoxAsync (juce::MessageBoxIconType::WarningIcon,
                    "Video export failed", result.getErrorMessage());
        }
        updateStateText();
    }

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (CoverRecorderComponent)
};

class CoverRecorderApplication final : public juce::JUCEApplication
{
public:
    const juce::String getApplicationName() override { return "Cover Recorder"; }
    const juce::String getApplicationVersion() override { return "0.2.0"; }

    void initialise (const juce::String& commandLine) override
    {
        const auto args = juce::StringArray::fromTokens (commandLine, true);
        if (args.size() > 0 && args[0] == "--check-session")
        {
            // No window, input device, or user session is opened in this mode.
            setApplicationReturnValue (runSessionChecks (args));
            quit();
            return;
        }
        if (args.size() == 2 && args[0] == "--check-video-job")
        {
            VideoExportProcess process;
            auto result = process.start (juce::File (args[1].unquoted()));
            if (result.wasOk())
            {
                while (process.isRunning())
                    juce::Thread::sleep (50);
                result = process.finish();
                if (result.wasOk() && static_cast<bool> (process.getResponse()["needs_manual"]))
                    result = juce::Result::fail ("Manual alignment required: " + process.getResponse()["error"].toString());
            }
            const auto report = juce::File (args[1].unquoted()).getSiblingFile ("native-video-result.txt");
            report.replaceWithText (result.wasOk() ? "OK" : result.getErrorMessage());
            setApplicationReturnValue (result.wasOk() ? 0 : 1);
            quit();
            return;
        }
        mainWindow = std::make_unique<MainWindow> (getApplicationName());
    }

    void shutdown() override
    {
        mainWindow = nullptr;
    }

    void systemRequestedQuit() override
    {
        if (mainWindow != nullptr)
            if (auto* recorder = dynamic_cast<CoverRecorderComponent*> (mainWindow->getContentComponent()))
                if (! recorder->canClose())
                    return;
        quit();
    }

private:
    class MainWindow final : public juce::DocumentWindow
    {
    public:
        explicit MainWindow (juce::String name)
            : juce::DocumentWindow (std::move (name),
                                    juce::Colour (0xff202326),
                                    juce::DocumentWindow::allButtons)
        {
            setUsingNativeTitleBar (true);
            setContentOwned (new CoverRecorderComponent(), true);
            setResizable (true, false);
            setResizeLimits (980, 620, 1800, 1200);
            centreWithSize (1120, 720);
            setVisible (true);
        }

        void closeButtonPressed() override
        {
            juce::JUCEApplication::getInstance()->systemRequestedQuit();
        }

    private:
        JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (MainWindow)
    };

    std::unique_ptr<MainWindow> mainWindow;
};

START_JUCE_APPLICATION (CoverRecorderApplication)
