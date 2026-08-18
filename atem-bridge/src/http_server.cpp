// HTTP server implementation
// TODO: Implement REST API

#include "http_server.h"
#include <iostream>

HttpServer::HttpServer(int port)
    : m_port(port), m_running(false), m_atem(nullptr) {
}

HttpServer::~HttpServer() {
    if (m_running) {
        Stop();
    }
}

bool HttpServer::Start(AtemController* atem) {
    m_atem = atem;
    m_running = true;
    
    // TODO: Implement HTTP server
    // Set up routes:
    // GET /health
    // GET /status
    // GET /inputs
    // POST /program
    // POST /preview
    // POST /cut
    // POST /auto
    // POST /stream/start
    // POST /stream/stop
    // POST /record/start
    // POST /record/stop
    
    std::cout << "HTTP server starting on port " << m_port << std::endl;
    
    return true;
}

bool HttpServer::Stop() {
    m_running = false;
    return true;
}

bool HttpServer::IsRunning() const {
    return m_running;
}
