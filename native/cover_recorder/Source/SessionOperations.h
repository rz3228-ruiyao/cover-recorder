#pragma once

#include <filesystem>

// Shared by the application and the non-interactive integration checks.
namespace sessionIO
{
inline std::unique_ptr<tracktion::Edit> load (tracktion::Engine& engine, const juce::File& file,
                                              juce::String& error)
{
    error.clear();
    if (! file.existsAsFile() || file.getSize() == 0 || ! file.hasFileExtension ("tracktionedit"))
        error = "Choose a non-empty .tracktionedit file.";
    else if (! tracktion::loadEditFromFile (engine, file, tracktion::ProjectItemID()).hasType (tracktion::IDs::EDIT))
        error = "Invalid session file.";
    if (error.isNotEmpty())
        return {};
    auto edit = tracktion::loadEditFromFile (engine, file);
    if (edit == nullptr)
        error = "Cannot read that session.";
    return edit;
}

inline juce::StringArray missingMedia (tracktion::Edit& edit)
{
    juce::StringArray missing;
    for (auto* track : tracktion::getAudioTracks (edit))
        for (auto* clip : track->getClips())
            if (auto* wave = dynamic_cast<tracktion::WaveAudioClip*> (clip))
            {
                const auto source = wave->getOriginalFile();
                if (! source.existsAsFile())
                    missing.addIfNotAlreadyThere (source == juce::File()
                        ? wave->getName() + " (no source file)" : source.getFullPathName());
            }
    return missing;
}

inline tracktion::TimeDuration backingLength (tracktion::Edit& edit)
{
    double recovered = 0.0;
    const auto tracks = tracktion::getAudioTracks (edit);
    if (! tracks.isEmpty())
        for (auto* clip : tracks[0]->getClips())
            if (dynamic_cast<tracktion::WaveAudioClip*> (clip) != nullptr)
                recovered = std::max (recovered, clip->getEditTimeRange().getEnd().inSeconds());
    const auto stored = static_cast<double> (edit.state.getProperty ("coverBackingLengthSeconds", recovered));
    return tracktion::TimeDuration::fromSeconds (std::isfinite (stored) && stored >= 0 ? stored : recovered);
}

inline bool save (tracktion::Edit& edit, tracktion::TimeDuration length)
{
    if (edit.getTransport().isRecording())
        return false;
    edit.state.setProperty ("coverBackingLengthSeconds", length.inSeconds(), nullptr);
    return tracktion::EditFileOperations (edit).save (false, true, false);
}

inline juce::Result render (tracktion::Edit& edit, const juce::File& directory,
                            tracktion::TimeDuration length, juce::File& output)
{
    if (edit.getTransport().isRecording())
        return juce::Result::fail ("Stop recording before exporting.");
    const auto missing = missingMedia (edit);
    if (! missing.isEmpty())
        return juce::Result::fail ("Missing media:\n" + missing.joinIntoString ("\n"));
    if (length.inSeconds() <= 0.0)
        length = edit.getLength();
    if (! std::isfinite (length.inSeconds()) || length.inSeconds() <= 0.0)
        return juce::Result::fail ("Nothing to render yet.");
    if (directory.createDirectory().failed())
        return juce::Result::fail ("Cannot create the export folder.");
    output = directory.getChildFile ("mixdown.wav").getNonexistentSibling();
    const juce::TemporaryFile temporary (output);
    if (! tracktion::Renderer::renderToFile ("Cover Recorder Mixdown", temporary.getFile(), edit,
            tracktion::TimeRange (tracktion::TimePosition(), length),
            tracktion::toBitSet (tracktion::getAllTracks (edit)), true, true, {}, false)
        || temporary.getFile().getSize() == 0)
        return juce::Result::fail ("WAV rendering failed. Previous exports were preserved.");

    // Create-if-absent: even a destination created during rendering must survive.
    // Both files are on the same filesystem. Unsupported filesystems fail safely.
    std::error_code error;
    std::filesystem::create_hard_link (
        std::filesystem::path (temporary.getFile().getFullPathName().toWideCharPointer()),
        std::filesystem::path (output.getFullPathName().toWideCharPointer()), error);
    if (error)
        return juce::Result::fail ("Cannot publish WAV; previous exports were preserved: "
                                  + juce::String (error.message()));
    return juce::Result::ok();
}
}
