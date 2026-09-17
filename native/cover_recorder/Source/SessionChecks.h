#pragma once

#include <stdexcept>

namespace sessionChecks
{
struct OfflineEngine final : tracktion::EngineBehaviour
{
    bool autoInitialiseDeviceManager() override { return false; }
};

struct Settings final : tracktion::PropertyStorage
{
    explicit Settings (juce::File folder) : PropertyStorage ("Cover Recorder Checks"), root (folder) {}
    juce::File getAppCacheFolder() override { return root.getChildFile ("cache"); }
    juce::File getAppPrefsFolder() override { return root.getChildFile ("settings"); }
    juce::File root;
};

inline void require (bool condition, const juce::String& message)
{
    if (! condition)
        throw std::runtime_error (message.toStdString());
}

inline juce::String snapshot (tracktion::Edit& edit)
{
    juce::ValueTree result ("Snapshot");
    result.setProperty ("backingLength", sessionIO::backingLength (edit).inSeconds(), nullptr);
    result.setProperty ("videoPath", edit.state.getProperty ("coverVideoPath"), nullptr);
    result.setProperty ("videoOffsetMs", edit.state.getProperty ("coverVideoOffsetMs"), nullptr);
    result.addChild (edit.state.getChildWithName (tracktion::IDs::INPUTDEVICES).createCopy(), -1, nullptr);
    for (auto* track : tracktion::getAudioTracks (edit))
    {
        juce::ValueTree t ("Track");
        t.setProperty ("name", track->getName(), nullptr);
        t.setProperty ("muted", track->isMuted (false), nullptr);
        t.setProperty ("solo", track->isSolo (false), nullptr);
        if (auto* volume = track->getVolumePlugin())
            t.setProperty ("volumeDb", volume->getVolumeDb(), nullptr);
        for (auto* clip : track->getClips())
            if (auto* wave = dynamic_cast<tracktion::WaveAudioClip*> (clip))
            {
                // Includes placement, trim offset, fades, gain and mute state.
                auto state = wave->state.createCopy();
                state.setProperty (tracktion::IDs::source, wave->getOriginalFile().getFullPathName(), nullptr);
                t.addChild (state, -1, nullptr);
            }
        result.addChild (t, -1, nullptr);
    }
    return result.toXmlString();
}

inline juce::MemoryBlock contents (const juce::File& file)
{
    juce::MemoryBlock data;
    require (file.loadFileAsData (data), "Could not read file for comparison.");
    return data;
}
}

// Usage: --check-session SOURCE.tracktionedit NEW_OUTPUT_DIRECTORY
// Works only in a new directory. SOURCE and its media are never saved or overwritten.
inline int runSessionChecks (const juce::StringArray& args)
{
    using namespace sessionChecks;
    if (args.size() != 3)
        return 2;
    const juce::File source (args[1].unquoted());
    const juce::File root (args[2].unquoted());
    if (root.exists() || root.createDirectory().failed())
        return 2;
    juce::StringArray passed;
    auto report = [&] (const juce::String& error)
    {
        juce::DynamicObject::Ptr object = new juce::DynamicObject();
        object->setProperty ("passed", juce::var (passed));
        object->setProperty ("error", error);
        object->setProperty ("success", error.isEmpty());
        root.getChildFile ("report.json").replaceWithText (juce::JSON::toString (juce::var (object.get())));
    };
    try
    {
        tracktion::Engine engine (std::make_unique<Settings> (root), nullptr, std::make_unique<OfflineEngine>());
        juce::String error;
        auto original = sessionIO::load (engine, source, error);
        require (original != nullptr, error);
        require (sessionIO::missingMedia (*original).isEmpty(), "Source fixture must have all media.");
        const auto before = snapshot (*original);
        root.getChildFile ("before.xml").replaceWithText (before);
        // Relocate only a copy, keeping media references absolute.
        auto state = original->state.createCopy();
        for (auto* track : tracktion::getAudioTracks (*original))
            for (auto* clip : track->getClips())
                if (auto* wave = dynamic_cast<tracktion::WaveAudioClip*> (clip))
                    for (auto t : state)
                        for (auto c : t)
                            if (c.hasType (tracktion::IDs::AUDIOCLIP)
                                && c.getProperty (tracktion::IDs::id) == wave->state.getProperty (tracktion::IDs::id))
                                c.setProperty (tracktion::IDs::source, wave->getOriginalFile().getFullPathName(), nullptr);
        const auto copy = root.getChildFile ("session.tracktionedit");
        require (state.createXml()->writeTo (copy), "Could not write isolated fixture.");
        original.reset();
        auto edit = sessionIO::load (engine, copy, error);
        require (edit != nullptr, error);
        require (snapshot (*edit) == before, "Initial load changed session state.");
        passed.add ("load_preserves_clips_gains_inputs");
        juce::File first;
        require (sessionIO::render (*edit, root, sessionIO::backingLength (*edit), first).wasOk(), "Initial render failed.");
        const auto firstHash = contents (first);
        require (sessionIO::save (*edit, sessionIO::backingLength (*edit)), "Save failed.");
        edit.reset();
        edit = sessionIO::load (engine, copy, error);
        require (edit != nullptr, error);
        const auto after = snapshot (*edit);
        root.getChildFile ("after.xml").replaceWithText (after);
        require (after == before, "Save/reload changed session state (see before/after.xml).");
        passed.add ("save_destroy_reload_preserves_state");
        juce::File second;
        require (sessionIO::render (*edit, root, sessionIO::backingLength (*edit), second).wasOk(), "Second render failed.");
        require (first != second && firstHash == contents (first), "Previous WAV was overwritten.");
        passed.add ("repeated_render_preserves_previous_export");
        auto beforeFailure = snapshot (*edit);
        for (const auto& name : { "invalid", "empty", "malformed" })
        {
            const auto bad = root.getChildFile (juce::String (name) + ".tracktionedit");
            bad.replaceWithText (juce::String (name) == "invalid" ? "<WRONG/>"
                : juce::String (name) == "malformed" ? "<EDIT broken" : "");
            const auto hash = contents (bad);
            require (sessionIO::load (engine, bad, error) == nullptr && error.isNotEmpty(), "Bad file was accepted.");
            require (hash == contents (bad), "Rejected source was changed.");
        }
        require (snapshot (*edit) == beforeFailure, "Failed load changed existing edit.");
        passed.add ("invalid_empty_malformed_rejected_without_changes");

        auto tracks = tracktion::getAudioTracks (*edit);
        require (! tracks.isEmpty() && ! tracks[0]->getClips().isEmpty(), "Fixture needs a first-track audio clip.");
        auto* wave = dynamic_cast<tracktion::WaveAudioClip*> (tracks[0]->getClips()[0]);
        require (wave != nullptr, "Fixture needs an audio clip.");
        wave->state.setProperty (tracktion::IDs::source, root.getChildFile ("missing.wav").getFullPathName(), nullptr);
        require (! sessionIO::missingMedia (*edit).isEmpty(), "Missing media was not detected.");
        juce::File blocked;
        const auto badRender = sessionIO::render (*edit, root.getChildFile ("blocked"), sessionIO::backingLength (*edit), blocked);
        require (badRender.failed() && ! root.getChildFile ("blocked").exists(), "Missing media was exported.");
        require (firstHash == contents (first), "Failed export damaged old WAV.");
        passed.add ("missing_media_blocks_render_and_preserves_exports");
        edit.reset();
        report ({});
        return 0;
    }
    catch (const std::exception& error)
    {
        report (error.what());
        return 1;
    }
}
