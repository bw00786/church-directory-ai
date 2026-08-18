// HTTP server header
// Provides REST API for ATEM control

#pragma once

#include "atem_controller.h"

class HttpServer {
public:
    explicit HttpServer(int port = 8090);
    ~HttpServer();
    
    bool Start(AtemController* atem);
    bool Stop();
    bool IsRunning() const;
    
private:
    int m_port;
    bool m_running;
    AtemController* m_atem;
    
    // TODO: Implement HTTP server
    // Consider using libraries like:
    // - boost::asio
    // - cpp-httplib
    // - pistache
};
