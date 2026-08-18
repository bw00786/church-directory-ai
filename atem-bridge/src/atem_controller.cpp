// ATEM controller implementation.
//
// The Windows build talks to the Blackmagic Switchers SDK over COM. Every
// public method is serialized through m_mutex so SDK access and the cached
// state remain consistent while callbacks arrive on SDK worker threads.

#include "atem_controller.h"

#include <ctime>

#include "logger.h"

#ifdef _WIN32
#include "atem_callbacks.h"

namespace {

// Convert a narrow UTF-8 string to a COM BSTR (caller frees with SysFreeString).
BSTR NarrowToBstr(const std::string& value) {
    int wide_len = MultiByteToWideChar(CP_UTF8, 0, value.c_str(), -1, nullptr, 0);
    if (wide_len <= 0) {
        return SysAllocString(L"");
    }
    std::wstring wide(static_cast<size_t>(wide_len - 1), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, value.c_str(), -1, &wide[0], wide_len);
    return SysAllocString(wide.c_str());
}

// Convert a COM BSTR to a narrow UTF-8 std::string.
std::string BstrToNarrow(BSTR value) {
    if (value == nullptr) {
        return std::string();
    }
    int len = SysStringLen(value);
    if (len <= 0) {
        return std::string();
    }
    int narrow_len = WideCharToMultiByte(CP_UTF8, 0, value, len, nullptr, 0, nullptr, nullptr);
    if (narrow_len <= 0) {
        return std::string();
    }
    std::string narrow(static_cast<size_t>(narrow_len), '\0');
    WideCharToMultiByte(CP_UTF8, 0, value, len, &narrow[0], narrow_len, nullptr, nullptr);
    return narrow;
}

const char* ExternalPortTypeName(BMDSwitcherExternalPortType type) {
    switch (type) {
        case bmdSwitcherExternalPortTypeSDI: return "SDI";
        case bmdSwitcherExternalPortTypeHDMI: return "HDMI";
        case bmdSwitcherExternalPortTypeComponent: return "Component";
        case bmdSwitcherExternalPortTypeComposite: return "Composite";
        case bmdSwitcherExternalPortTypeSVideo: return "SVideo";
        case bmdSwitcherExternalPortTypeUSB: return "USB";
        default: return "External";
    }
}

const char* ConnectFailureName(BMDSwitcherConnectToFailure reason) {
    switch (reason) {
        case bmdSwitcherConnectToFailureNoResponse:
            return "No response from ATEM";
        case bmdSwitcherConnectToFailureIncompatibleFirmware:
            return "Incompatible ATEM firmware";
        case bmdSwitcherConnectToFailureCorruptData:
            return "Corrupt data received from ATEM";
        case bmdSwitcherConnectToFailureStateSync:
            return "State sync failure";
        case bmdSwitcherConnectToFailureStateSyncTimedOut:
            return "State sync timed out";
        default:
            return "Unknown connection failure";
    }
}

}  // namespace
#endif  // _WIN32

AtemController::AtemController()
    : m_connected(false),
      m_program_input(0),
      m_preview_input(1),
      m_streaming(false),
      m_recording(false),
      m_transition_in_progress(false)
#ifdef _WIN32
      ,
      m_com_initialized(false),
      m_discovery(nullptr),
      m_switcher(nullptr),
      m_mix_effect(nullptr),
      m_stream(nullptr),
      m_record(nullptr),
      m_me_callback(nullptr),
      m_switcher_callback(nullptr),
      m_stream_callback(nullptr),
      m_record_callback(nullptr)
#endif
{
#ifdef _WIN32
    // Join a process-wide multithreaded apartment on the main thread so the
    // free-threaded SDK objects can be called from HTTP worker threads.
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (SUCCEEDED(hr) || hr == RPC_E_CHANGED_MODE) {
        m_com_initialized = true;
    } else {
        LOG_ERROR("Failed to initialize COM");
    }
#endif
}

AtemController::~AtemController() {
    Disconnect();
#ifdef _WIN32
    if (m_com_initialized) {
        CoUninitialize();
        m_com_initialized = false;
    }
#endif
}

bool AtemController::IsConnected() const {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    return m_connected;
}

std::map<int, AtemInput> AtemController::GetInputs() const {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    return m_inputs;
}

int AtemController::GetProgramInput() const {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    return m_program_input;
}

int AtemController::GetPreviewInput() const {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    return m_preview_input;
}

bool AtemController::GetTransitionInProgress() const {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    return m_transition_in_progress;
}

bool AtemController::GetStreamingState() const {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    return m_streaming;
}

bool AtemController::GetRecordingState() const {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    return m_recording;
}

std::string AtemController::GetLastError() const {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    return m_last_error;
}

void AtemController::SetError(const std::string& msg) {
    m_last_error = msg;
    LOG_ERROR(msg);
}

void AtemController::SetStateChangeCallback(StateChangeCallback cb) {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    m_state_cb = std::move(cb);
}

void AtemController::NotifyStateChanged() {
    StateChangeCallback cb;
    {
        std::lock_guard<std::recursive_mutex> lock(m_mutex);
        cb = m_state_cb;
    }
    if (cb) {
        cb();
    }
}

AtemState AtemController::GetState() const {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);

    AtemState state;
    state.connected = m_connected;
    state.program_input = m_program_input;
    state.preview_input = m_preview_input;
    state.streaming = m_streaming;
    state.recording = m_recording;
    state.transition_in_progress = m_transition_in_progress;
    state.timestamp = std::time(nullptr);

    for (const auto& entry : m_inputs) {
        AtemInputState input;
        input.id = entry.second.id;
        input.name = entry.second.name;
        input.short_name = entry.second.short_name;
        input.type = entry.second.type;
        input.connected = entry.second.connected;
        state.inputs.push_back(input);
    }
    return state;
}

// ---------------------------------------------------------------------------
// SDK callback hooks
// ---------------------------------------------------------------------------
void AtemController::OnMixEffectChanged() {
#ifdef _WIN32
    {
        std::lock_guard<std::recursive_mutex> lock(m_mutex);
        RefreshProgramPreview();
        RefreshTransition();
    }
    NotifyStateChanged();
#endif
}

void AtemController::OnInputChanged() {
#ifdef _WIN32
    {
        std::lock_guard<std::recursive_mutex> lock(m_mutex);
        RefreshInputsInternal();
    }
    NotifyStateChanged();
#endif
}

void AtemController::OnStreamingChanged() {
#ifdef _WIN32
    {
        std::lock_guard<std::recursive_mutex> lock(m_mutex);
        RefreshStreamingRecording();
    }
    NotifyStateChanged();
#endif
}

void AtemController::OnRecordingChanged() {
#ifdef _WIN32
    {
        std::lock_guard<std::recursive_mutex> lock(m_mutex);
        RefreshStreamingRecording();
    }
    NotifyStateChanged();
#endif
}

void AtemController::OnSwitcherDisconnected() {
    LOG_WARN("ATEM switcher reported disconnection");
    Disconnect();
    NotifyStateChanged();
}

#ifndef _WIN32
// ---------------------------------------------------------------------------
// Non-Windows stub: the Blackmagic SDK is only available on Windows.
// ---------------------------------------------------------------------------
bool AtemController::Connect(const std::string& ip) {
    (void)ip;
    SetError("ATEM SDK is only available on Windows");
    return false;
}

bool AtemController::Disconnect() {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    m_connected = false;
    return true;
}

bool AtemController::EnumerateInputs() { return false; }
bool AtemController::SetProgramInput(int) { return false; }
bool AtemController::SetPreviewInput(int) { return false; }
bool AtemController::PerformCut() { return false; }
bool AtemController::PerformAuto() { return false; }
bool AtemController::StartStreaming() { return false; }
bool AtemController::StopStreaming() { return false; }
bool AtemController::StartRecording() { return false; }
bool AtemController::StopRecording() { return false; }

#else
// ---------------------------------------------------------------------------
// Windows / Blackmagic SDK implementation
// ---------------------------------------------------------------------------
bool AtemController::Connect(const std::string& ip) {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);

    if (m_connected) {
        return true;
    }

    if (!m_com_initialized) {
        SetError("COM is not initialized");
        return false;
    }

    if (!ConnectInternal(ip)) {
        ReleaseAll();
        return false;
    }

    RegisterCallbacks();
    RefreshInputsInternal();
    RefreshProgramPreview();
    RefreshTransition();
    RefreshStreamingRecording();

    m_connected = true;
    m_last_error.clear();
    LOG_INFO("Connected to ATEM at " + ip);
    NotifyStateChanged();
    return true;
}

bool AtemController::ConnectInternal(const std::string& ip) {
    HRESULT hr = CoCreateInstance(__uuidof(CBMDSwitcherDiscovery), nullptr, CLSCTX_ALL,
                                  IID_PPV_ARGS(&m_discovery));
    if (FAILED(hr) || m_discovery == nullptr) {
        SetError("Failed to create switcher discovery instance");
        return false;
    }

    BSTR address = NarrowToBstr(ip);
    BMDSwitcherConnectToFailure fail_reason = bmdSwitcherConnectToFailureNoResponse;
    hr = m_discovery->ConnectTo(address, &m_switcher, &fail_reason);
    SysFreeString(address);

    if (FAILED(hr) || m_switcher == nullptr) {
        SetError(std::string("ATEM connect failed: ") + ConnectFailureName(fail_reason));
        return false;
    }

    // Grab the first mix effect block (M/E 1).
    IBMDSwitcherMixEffectBlockIterator* me_iterator = nullptr;
    hr = m_switcher->CreateIterator(__uuidof(IBMDSwitcherMixEffectBlockIterator),
                                    reinterpret_cast<void**>(&me_iterator));
    if (SUCCEEDED(hr) && me_iterator != nullptr) {
        me_iterator->Next(&m_mix_effect);
        me_iterator->Release();
    }
    if (m_mix_effect == nullptr) {
        SetError("ATEM has no mix effect block");
        return false;
    }

    // Streaming and recording are optional depending on model/firmware.
    if (FAILED(m_switcher->QueryInterface(IID_PPV_ARGS(&m_stream)))) {
        m_stream = nullptr;
        LOG_INFO("ATEM does not expose RTMP streaming interface");
    }
    if (FAILED(m_switcher->QueryInterface(IID_PPV_ARGS(&m_record)))) {
        m_record = nullptr;
        LOG_INFO("ATEM does not expose recording interface");
    }

    return true;
}

void AtemController::RegisterCallbacks() {
    if (m_switcher != nullptr) {
        m_switcher_callback = new SwitcherCallback(this);
        m_switcher->AddCallback(m_switcher_callback);
    }
    if (m_mix_effect != nullptr) {
        m_me_callback = new MixEffectCallback(this);
        m_mix_effect->AddCallback(m_me_callback);
    }
    if (m_stream != nullptr) {
        m_stream_callback = new StreamCallback(this);
        m_stream->AddCallback(m_stream_callback);
    }
    if (m_record != nullptr) {
        m_record_callback = new RecordCallback(this);
        m_record->AddCallback(m_record_callback);
    }
}

void AtemController::UnregisterCallbacks() {
    if (m_switcher != nullptr && m_switcher_callback != nullptr) {
        m_switcher->RemoveCallback(m_switcher_callback);
    }
    if (m_mix_effect != nullptr && m_me_callback != nullptr) {
        m_mix_effect->RemoveCallback(m_me_callback);
    }
    if (m_stream != nullptr && m_stream_callback != nullptr) {
        m_stream->RemoveCallback(m_stream_callback);
    }
    if (m_record != nullptr && m_record_callback != nullptr) {
        m_record->RemoveCallback(m_record_callback);
    }

    for (auto& entry : m_input_callbacks) {
        if (entry.first != nullptr && entry.second != nullptr) {
            entry.first->RemoveCallback(entry.second);
            entry.second->Release();
        }
        if (entry.first != nullptr) {
            entry.first->Release();
        }
    }
    m_input_callbacks.clear();

    if (m_switcher_callback != nullptr) {
        m_switcher_callback->Release();
        m_switcher_callback = nullptr;
    }
    if (m_me_callback != nullptr) {
        m_me_callback->Release();
        m_me_callback = nullptr;
    }
    if (m_stream_callback != nullptr) {
        m_stream_callback->Release();
        m_stream_callback = nullptr;
    }
    if (m_record_callback != nullptr) {
        m_record_callback->Release();
        m_record_callback = nullptr;
    }
}

void AtemController::ReleaseAll() {
    if (m_record != nullptr) {
        m_record->Release();
        m_record = nullptr;
    }
    if (m_stream != nullptr) {
        m_stream->Release();
        m_stream = nullptr;
    }
    if (m_mix_effect != nullptr) {
        m_mix_effect->Release();
        m_mix_effect = nullptr;
    }
    if (m_switcher != nullptr) {
        m_switcher->Release();
        m_switcher = nullptr;
    }
    if (m_discovery != nullptr) {
        m_discovery->Release();
        m_discovery = nullptr;
    }
}

bool AtemController::Disconnect() {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);

    if (m_switcher != nullptr || m_discovery != nullptr) {
        UnregisterCallbacks();
        ReleaseAll();
    }

    if (m_connected) {
        LOG_INFO("Disconnected from ATEM");
    }
    m_connected = false;
    return true;
}

bool AtemController::EnumerateInputs() {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (!m_connected && m_switcher == nullptr) {
        SetError("Not connected to ATEM");
        return false;
    }
    RefreshInputsInternal();
    return true;
}

void AtemController::RefreshInputsInternal() {
    if (m_switcher == nullptr) {
        return;
    }

    IBMDSwitcherInputIterator* iterator = nullptr;
    HRESULT hr = m_switcher->CreateIterator(__uuidof(IBMDSwitcherInputIterator),
                                            reinterpret_cast<void**>(&iterator));
    if (FAILED(hr) || iterator == nullptr) {
        return;
    }

    std::map<int, AtemInput> inputs;
    IBMDSwitcherInput* input = nullptr;
    while (iterator->Next(&input) == S_OK && input != nullptr) {
        BMDSwitcherPortType port_type = bmdSwitcherPortTypeExternal;
        input->GetPortType(&port_type);

        // Only surface physical (external) camera inputs.
        if (port_type == bmdSwitcherPortTypeExternal) {
            BMDSwitcherInputId input_id = 0;
            input->GetInputId(&input_id);

            AtemInput entry;
            entry.id = static_cast<int>(input_id);

            BSTR long_name = nullptr;
            if (SUCCEEDED(input->GetLongName(&long_name)) && long_name != nullptr) {
                entry.name = BstrToNarrow(long_name);
                SysFreeString(long_name);
            }

            BSTR short_name = nullptr;
            if (SUCCEEDED(input->GetShortName(&short_name)) && short_name != nullptr) {
                entry.short_name = BstrToNarrow(short_name);
                SysFreeString(short_name);
            }

            BMDSwitcherExternalPortType external_type = bmdSwitcherExternalPortTypeHDMI;
            input->GetCurrentExternalPortType(&external_type);
            entry.type = ExternalPortTypeName(external_type);
            entry.connected = true;

            inputs[entry.id] = entry;
        }
        input->Release();
        input = nullptr;
    }
    iterator->Release();

    m_inputs = std::move(inputs);
}

void AtemController::RefreshProgramPreview() {
    if (m_mix_effect == nullptr) {
        return;
    }
    BMDSwitcherInputId program = 0;
    BMDSwitcherInputId preview = 0;
    if (SUCCEEDED(m_mix_effect->GetProgramInput(&program))) {
        m_program_input = static_cast<int>(program);
    }
    if (SUCCEEDED(m_mix_effect->GetPreviewInput(&preview))) {
        m_preview_input = static_cast<int>(preview);
    }
}

void AtemController::RefreshTransition() {
    if (m_mix_effect == nullptr) {
        return;
    }
    BOOL in_transition = FALSE;
    if (SUCCEEDED(m_mix_effect->GetInTransition(&in_transition))) {
        m_transition_in_progress = (in_transition == TRUE);
    }
}

void AtemController::RefreshStreamingRecording() {
    if (m_stream != nullptr) {
        BOOL streaming = FALSE;
        if (SUCCEEDED(m_stream->IsStreaming(&streaming))) {
            m_streaming = (streaming == TRUE);
        }
    }
    if (m_record != nullptr) {
        BOOL recording = FALSE;
        if (SUCCEEDED(m_record->IsRecording(&recording))) {
            m_recording = (recording == TRUE);
        }
    }
}

bool AtemController::SetProgramInput(int input_id) {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (m_mix_effect == nullptr) {
        SetError("Not connected to ATEM");
        return false;
    }
    if (FAILED(m_mix_effect->SetProgramInput(static_cast<BMDSwitcherInputId>(input_id)))) {
        SetError("Failed to set program input");
        return false;
    }
    RefreshProgramPreview();
    return true;
}

bool AtemController::SetPreviewInput(int input_id) {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (m_mix_effect == nullptr) {
        SetError("Not connected to ATEM");
        return false;
    }
    if (FAILED(m_mix_effect->SetPreviewInput(static_cast<BMDSwitcherInputId>(input_id)))) {
        SetError("Failed to set preview input");
        return false;
    }
    RefreshProgramPreview();
    return true;
}

bool AtemController::PerformCut() {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (m_mix_effect == nullptr) {
        SetError("Not connected to ATEM");
        return false;
    }
    if (FAILED(m_mix_effect->PerformCut())) {
        SetError("Failed to perform cut");
        return false;
    }
    RefreshProgramPreview();
    return true;
}

bool AtemController::PerformAuto() {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (m_mix_effect == nullptr) {
        SetError("Not connected to ATEM");
        return false;
    }
    if (FAILED(m_mix_effect->PerformAutoTransition())) {
        SetError("Failed to perform auto transition");
        return false;
    }
    RefreshTransition();
    return true;
}

bool AtemController::StartStreaming() {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (m_stream == nullptr) {
        SetError("Streaming not supported by this ATEM");
        return false;
    }
    if (FAILED(m_stream->StartStreaming())) {
        SetError("Failed to start streaming");
        return false;
    }
    RefreshStreamingRecording();
    return true;
}

bool AtemController::StopStreaming() {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (m_stream == nullptr) {
        SetError("Streaming not supported by this ATEM");
        return false;
    }
    if (FAILED(m_stream->StopStreaming())) {
        SetError("Failed to stop streaming");
        return false;
    }
    RefreshStreamingRecording();
    return true;
}

bool AtemController::StartRecording() {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (m_record == nullptr) {
        SetError("Recording not supported by this ATEM");
        return false;
    }
    if (FAILED(m_record->StartRecording())) {
        SetError("Failed to start recording");
        return false;
    }
    RefreshStreamingRecording();
    return true;
}

bool AtemController::StopRecording() {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (m_record == nullptr) {
        SetError("Recording not supported by this ATEM");
        return false;
    }
    if (FAILED(m_record->StopRecording())) {
        SetError("Failed to stop recording");
        return false;
    }
    RefreshStreamingRecording();
    return true;
}

#endif  // _WIN32
