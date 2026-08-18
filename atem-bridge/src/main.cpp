// Main entry point for the ATEM bridge.
//
// Starts the HTTP control server. The ATEM connection itself is established
// on demand through the POST /connect endpoint (driven by the backend), or
// eagerly at startup when ATEM_AUTO_CONNECT=1.

#include <csignal>
#include <cstdlib>
#include <string>

#include "atem_controller.h"
#include "http_server.h"
#include "logger.h"

namespace {

HttpServer* g_server = nullptr;

void HandleSignal(int signal) {
    LOG_INFO("Received signal " + std::to_string(signal) + ", shutting down");
    if (g_server != nullptr) {
        g_server->Stop();
    }
}

int GetPortFromEnv() {
    const char* env = std::getenv("ATEM_BRIDGE_PORT");
    if (env != nullptr) {
        int port = std::atoi(env);
        if (port > 0 && port < 65536) {
            return port;
        }
    }
    return 8090;
}

bool EnvFlag(const char* name) {
    const char* value = std::getenv(name);
    return value != nullptr && (std::string(value) == "1" || std::string(value) == "true");
}

}  // namespace

int main() {
    LOG_INFO("ATEM Bridge starting...");

    AtemController controller;
    controller.SetStateChangeCallback([]() { LOG_DEBUG("ATEM state changed"); });

    const int port = GetPortFromEnv();
    HttpServer server(port);
    g_server = &server;

    std::signal(SIGINT, HandleSignal);
    std::signal(SIGTERM, HandleSignal);

    if (EnvFlag("ATEM_AUTO_CONNECT")) {
        const char* ip = std::getenv("ATEM_IP");
        std::string atem_ip = (ip != nullptr) ? ip : "192.168.30.20";
        LOG_INFO("Auto-connecting to ATEM at " + atem_ip);
        if (!controller.Connect(atem_ip)) {
            LOG_WARN("Initial ATEM connection failed: " + controller.GetLastError());
        }
    }

    LOG_INFO("ATEM Bridge running on http://127.0.0.1:" + std::to_string(port));

    // Blocks until a shutdown signal stops the server.
    bool ok = server.Start(&controller);

    controller.Disconnect();
    g_server = nullptr;
    LOG_INFO("ATEM Bridge stopped");
    return ok ? 0 : 1;
}
