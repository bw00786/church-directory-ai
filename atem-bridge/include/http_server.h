// HTTP server header
// Provides REST API for ATEM control

#pragma once

#include <memory>

#include "atem_controller.h"

namespace httplib {
class Server;
}

class HttpServer {
public:
    explicit HttpServer(int port = 8090);
    ~HttpServer();

    HttpServer(const HttpServer&) = delete;
    HttpServer& operator=(const HttpServer&) = delete;

    // Registers routes and blocks listening on 127.0.0.1:m_port until Stop().
    bool Start(AtemController* atem);
    bool Stop();
    bool IsRunning() const;

private:
    void RegisterRoutes();

    int m_port;
    bool m_running;
    AtemController* m_atem;
    std::unique_ptr<httplib::Server> m_server;
};
