// ATEM controller header
// Encapsulates Blackmagic SDK functionality

#pragma once

#include <string>
#include <map>
#include <memory>

// Forward declarations (SDK will be included in .cpp)
// #include "BMDSwitcherAPI.h"

struct AtemInput {
    int id;
    std::string name;
    std::string short_name;
    std::string type;
    bool connected;
};

class AtemController {
public:
    AtemController();
    ~AtemController();
    
    // Connection management
    bool Connect(const std::string& ip);
    bool Disconnect();
    bool IsConnected() const;
    
    // Input management
    bool EnumerateInputs();
    const std::map<int, AtemInput>& GetInputs() const;
    
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
    
private:
    // SDK members (will be defined in .cpp)
    // IBMDSwitcher* m_switcher;
    // IBMDSwitcherMixEffectBlock* m_mix_effect;
    // etc.
    
    bool m_connected;
    int m_program_input;
    int m_preview_input;
    bool m_streaming;
    bool m_recording;
    bool m_transition_in_progress;
    
    std::map<int, AtemInput> m_inputs;
};
