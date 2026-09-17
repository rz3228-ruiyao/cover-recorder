#pragma once

// The Python backend owns FFmpeg and atomic publication of the output.
// Polling this process from the app timer keeps encoding off the UI thread.
class VideoExportProcess
{
public:
    static juce::File pythonExecutable()
    {
        const auto configured = juce::SystemStats::getEnvironmentVariable ("COVER_RECORDER_PYTHON", {});
        if (configured.isNotEmpty())
            return juce::File (configured);
        return juce::File::getSpecialLocation (juce::File::userHomeDirectory)
            .getChildFile ("miniconda3/envs/cover-syncer/python.exe");
    }

    juce::Result start (const juce::File& job)
    {
        const auto python = pythonExecutable();
        if (! python.existsAsFile())
            return juce::Result::fail ("Video export needs the cover-syncer Python environment. "
                "Set COVER_RECORDER_PYTHON to its python.exe if installed elsewhere.");
        if (! child.start (juce::StringArray { python.getFullPathName(), "-m", "cover_syncer.native_export", job.getFullPathName() }))
            return juce::Result::fail ("Could not start video export.");
        return juce::Result::ok();
    }

    bool isRunning() const { return child.isRunning(); }

    juce::Result finish()
    {
        const auto response = child.readAllProcessOutput();
        result = juce::JSON::parse (response);
        if (child.getExitCode() == 0 && (static_cast<bool> (result["success"]) || static_cast<bool> (result["needs_manual"])))
            return juce::Result::ok();
        auto message = result["error"].toString();
        if (message.isEmpty())
            message = response.substring (0, 2000);
        return juce::Result::fail (message.isEmpty() ? "Video export exited without a result." : message);
    }

    const juce::var& getResponse() const { return result; }

private:
    mutable juce::ChildProcess child;
    juce::var result;
};
