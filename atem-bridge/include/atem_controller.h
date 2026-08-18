// ATEM controller header
// Encapsulates Blackmagic SDK functionality.
//
// All SDK usage is Windows-only (COM). On other platforms the controller
// compiles as a stub so the rest of the bridge (HTTP server) can still be
// built and inspected, but connection attempts fail gracefully.

#pragma once

#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#include "atem_state.h"

#ifdef _WIN32
#include <windows.h>
#include "BMDSwitcherAPI.h"
#endif

struct AtemInput {
    int id;
    std::string name;
    std::string short_name;
    std::string type;
    bool connected;
};

#ifdef _WIN32
class MixEffectCallback;
class SwitcherCallback;
class InputCallback;
class StreamCallback;
class RecordCallback;
#endif

class AtemController {
public:
    // Invoked (from an SDK worker thread) whenever cached state changes.
    using StateChangeCallback = std::function<void()>;

    AtemController();
    ~AtemController();

    AtemController(const AtemController&) = delete;
    AtemController& operator=(const AtemController&) = delete;

    // Connection management
    bool Connect(const std::string& ip);
    bool Disconnect();
    bool IsConnected() const;

    // Input management
    bool EnumerateInputs();
    std::map<int, AtemInput> GetInputs() const;

    // Program/Preview control
    int GetProgramInput() const;
    bool SetProgramInput(int input_id);

    int GetPreviewInput() const;
    bool SetPreviewInput(int input_id);

    // Transitions
    bool PerformCut();
    bool PerformAuto();
    bool GetTransitionInProgress() const;

    // Streaming/Recording
    bool GetStreamingState() const;
    bool StartStreaming();
    bool StopStreaming();

    bool GetRecordingState() const;
    bool StartRecording();
    bool StopRecording();

    // Thread-safe snapshot of the full state.
    AtemState GetState() const;

    // Last error message (for HTTP error responses).
    std::string GetLastError() const;

    // Register a hook fired when cached state changes.
    void SetStateChangeCallback(StateChangeCallback cb);

    // Internal hooks called by the SDK callback objects.
    void OnMixEffectChanged();
    void OnInputChanged();
    void OnStreamingChanged();
    void OnRecordingChanged();
    void OnSwitcherDisconnected();

private:
    void SetError(const std::string& msg);
    void NotifyStateChanged();

#ifdef _WIN32
    bool ConnectInternal(const std::string& ip);
    void ReleaseAll();
    void RegisterCallbacks();
    void UnregisterCallbacks();
    void RefreshProgramPreview();
    void RefreshTransition();
    void RefreshStreamingRecording();
    void RefreshInputsInternal();
#endif

    mutable std::recursive_mutex m_mutex;
    bool m_connected;
    std::string m_last_error;
    StateChangeCallback m_state_cb;

    // Cached state (protected by m_mutex).
    int m_program_input;
    int m_preview_input;
    bool m_streaming;
    bool m_recording;
    bool m_transition_in_progress;
    std::map<int, AtemInput> m_inputs;

#ifdef _WIN32
    bool m_com_initialized;
    IBMDSwitcherDiscovery* m_discovery;
    IBMDSwitcher* m_switcher;
    IBMDSwitcherMixEffectBlock* m_mix_effect;
    IBMDSwitcherStreamRTMP* m_stream;
    IBMDSwitcherRecordAV* m_record;

    MixEffectCallback* m_me_callback;
    SwitcherCallback* m_switcher_callback;
    StreamCallback* m_stream_callback;
    RecordCallback* m_record_callback;
    std::vector<std::pair<IBMDSwitcherInput*, InputCallback*>> m_input_callbacks;
#endif
};
