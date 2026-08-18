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
};
