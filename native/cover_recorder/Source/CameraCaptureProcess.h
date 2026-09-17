#pragma once

class CameraCaptureProcess
{
public:
    juce::Result start (const juce::File& sessionDirectory)
    {
        directory = sessionDirectory.getChildFile ("camera-" + juce::Uuid().toString());
        if (directory.createDirectory().failed())
            return juce::Result::fail ("Cannot create the camera capture folder.");
        heartbeat();
        if (! child.start (juce::StringArray { VideoExportProcess::pythonExecutable().getFullPathName(),
                "-m", "cover_syncer.capture", directory.getFullPathName() }))
            return juce::Result::fail ("Could not start the camera panel.");
        return juce::Result::ok();
    }

    bool send (const juce::String& command)
    {
        juce::DynamicObject::Ptr value = new juce::DynamicObject();
        value->setProperty ("command", command);
        value->setProperty ("serial", ++serial);
        const auto destination = directory.getChildFile ("control.json");
        const juce::TemporaryFile temporary (destination);
        return temporary.getFile().replaceWithText (juce::JSON::toString (juce::var (value.get())))
            && temporary.overwriteTargetFileWithTemporary();
    }

    juce::var status() const
    {
        return juce::JSON::parse (directory.getChildFile ("status.json"));
    }
    void heartbeat() { directory.getChildFile ("heartbeat").replaceWithText ("alive"); }
    bool isRunning() const { return child.isRunning(); }
    juce::String diagnostics() { return child.readAllProcessOutput().substring (0, 2000); }

private:
    juce::File directory;
    mutable juce::ChildProcess child;
    int serial = 0;
};
