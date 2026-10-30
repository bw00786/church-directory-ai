// ATEM state structure

#pragma once

#include <vector>
#include <string>
#include <ctime>

struct AtemInputState {
    int id;
    std::string name;
    std::string short_name;
    std::string type;
    bool connected;
    bool program_tally;
    bool preview_tally;
};

struct AtemState {
    bool connected;
    int program_input;
    int preview_input;
    bool streaming;
    bool recording;
    bool transition_in_progress;
    std::vector<AtemInputState> inputs;
    time_t timestamp;
    // Wall-clock time of the last SDK callback that changed cached state
    // (0 until the first change). Lets the backend detect a stale bridge.
    time_t last_change;
};
