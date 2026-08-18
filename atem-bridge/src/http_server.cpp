// HTTP server implementation
// Exposes the ATEM control REST API consumed by the FastAPI backend.

#include "http_server.h"

#include <httplib.h>
#include <nlohmann/json.hpp>

#include "atem_state.h"
#include "logger.h"

using json = nlohmann::json;

namespace {

json StateToJson(const AtemState& state) {
    json inputs = json::array();
    for (const auto& input : state.inputs) {
        inputs.push_back({
            {"id", input.id},
            {"name", input.name},
            {"short_name", input.short_name},
            {"type", input.type},
            {"connected", input.connected},
        });
    }

    return json{
        {"connected", state.connected},
        {"program_input", state.program_input},
        {"preview_input", state.preview_input},
        {"streaming", state.streaming},
        {"recording", state.recording},
        {"transition_in_progress", state.transition_in_progress},
        {"inputs", inputs},
        {"timestamp", static_cast<long long>(state.timestamp)},
    };
}

void SendJson(httplib::Response& res, const json& body, int status = 200) {
    res.status = status;
    res.set_content(body.dump(), "application/json");
}

}  // namespace

HttpServer::HttpServer(int port)
    : m_port(port), m_running(false), m_atem(nullptr) {}

HttpServer::~HttpServer() {
    Stop();
}

bool HttpServer::IsRunning() const {
    return m_running;
}

bool HttpServer::Start(AtemController* atem) {
    m_atem = atem;
    m_server = std::make_unique<httplib::Server>();
    RegisterRoutes();

    LOG_INFO("HTTP server listening on http://127.0.0.1:" + std::to_string(m_port));
    m_running = true;

    // Blocks until Stop() is called from another thread.
    bool ok = m_server->listen("127.0.0.1", m_port);
    m_running = false;
    if (!ok) {
        LOG_ERROR("HTTP server failed to bind port " + std::to_string(m_port));
    }
    return ok;
}

bool HttpServer::Stop() {
    if (m_server && m_running) {
        m_server->stop();
    }
    m_running = false;
    return true;
}

void HttpServer::RegisterRoutes() {
    AtemController* atem = m_atem;
    httplib::Server& server = *m_server;

    server.set_exception_handler(
        [](const httplib::Request&, httplib::Response& res, std::exception_ptr ep) {
            std::string message = "internal error";
            try {
                if (ep) std::rethrow_exception(ep);
            } catch (const std::exception& e) {
                message = e.what();
            } catch (...) {
            }
            SendJson(res, json{{"ok", false}, {"error", message}}, 500);
        });

    server.Get("/health", [](const httplib::Request&, httplib::Response& res) {
        SendJson(res, json{{"ok", true}});
    });

    server.Get("/status", [atem](const httplib::Request&, httplib::Response& res) {
        SendJson(res, StateToJson(atem->GetState()));
    });

    server.Get("/inputs", [atem](const httplib::Request&, httplib::Response& res) {
        json inputs = json::array();
        for (const auto& entry : atem->GetInputs()) {
            const AtemInput& input = entry.second;
            inputs.push_back({
                {"id", input.id},
                {"name", input.name},
                {"short_name", input.short_name},
                {"type", input.type},
                {"connected", input.connected},
            });
        }
        SendJson(res, json{{"inputs", inputs}});
    });

    server.Post("/connect", [atem](const httplib::Request& req, httplib::Response& res) {
        std::string atem_ip;
        if (!req.body.empty()) {
            json body = json::parse(req.body, nullptr, false);
            if (body.is_object() && body.contains("atem_ip")) {
                atem_ip = body["atem_ip"].get<std::string>();
            }
        }
        if (atem_ip.empty()) {
            SendJson(res, json{{"ok", false}, {"error", "atem_ip is required"}}, 400);
            return;
        }
        bool ok = atem->Connect(atem_ip);
        if (ok) {
            SendJson(res, json{{"ok", true}});
        } else {
            SendJson(res, json{{"ok", false}, {"error", atem->GetLastError()}}, 503);
        }
    });

    server.Post("/disconnect", [atem](const httplib::Request&, httplib::Response& res) {
        atem->Disconnect();
        SendJson(res, json{{"ok", true}});
    });

    server.Post("/program", [atem](const httplib::Request& req, httplib::Response& res) {
        json body = json::parse(req.body, nullptr, false);
        if (!body.is_object() || !body.contains("input_id")) {
            SendJson(res, json{{"ok", false}, {"error", "input_id is required"}}, 400);
            return;
        }
        int input_id = body["input_id"].get<int>();
        if (atem->SetProgramInput(input_id)) {
            SendJson(res, json{{"ok", true}, {"program_input", atem->GetProgramInput()}});
        } else {
            SendJson(res, json{{"ok", false}, {"error", atem->GetLastError()}}, 500);
        }
    });

    server.Post("/preview", [atem](const httplib::Request& req, httplib::Response& res) {
        json body = json::parse(req.body, nullptr, false);
        if (!body.is_object() || !body.contains("input_id")) {
            SendJson(res, json{{"ok", false}, {"error", "input_id is required"}}, 400);
            return;
        }
        int input_id = body["input_id"].get<int>();
        if (atem->SetPreviewInput(input_id)) {
            SendJson(res, json{{"ok", true}, {"preview_input", atem->GetPreviewInput()}});
        } else {
            SendJson(res, json{{"ok", false}, {"error", atem->GetLastError()}}, 500);
        }
    });

    server.Post("/cut", [atem](const httplib::Request&, httplib::Response& res) {
        if (atem->PerformCut()) {
            SendJson(res, json{{"ok", true}});
        } else {
            SendJson(res, json{{"ok", false}, {"error", atem->GetLastError()}}, 500);
        }
    });

    server.Post("/auto", [atem](const httplib::Request&, httplib::Response& res) {
        if (atem->PerformAuto()) {
            SendJson(res, json{{"ok", true}});
        } else {
            SendJson(res, json{{"ok", false}, {"error", atem->GetLastError()}}, 500);
        }
    });

    server.Get("/stream/status", [atem](const httplib::Request&, httplib::Response& res) {
        SendJson(res, json{{"streaming", atem->GetStreamingState()}});
    });

    server.Post("/stream/start", [atem](const httplib::Request&, httplib::Response& res) {
        if (atem->StartStreaming()) {
            SendJson(res, json{{"ok", true}});
        } else {
            SendJson(res, json{{"ok", false}, {"error", atem->GetLastError()}}, 500);
        }
    });

    server.Post("/stream/stop", [atem](const httplib::Request&, httplib::Response& res) {
        if (atem->StopStreaming()) {
            SendJson(res, json{{"ok", true}});
        } else {
            SendJson(res, json{{"ok", false}, {"error", atem->GetLastError()}}, 500);
        }
    });

    server.Get("/record/status", [atem](const httplib::Request&, httplib::Response& res) {
        SendJson(res, json{{"recording", atem->GetRecordingState()}});
    });

    server.Post("/record/start", [atem](const httplib::Request&, httplib::Response& res) {
        if (atem->StartRecording()) {
            SendJson(res, json{{"ok", true}});
        } else {
            SendJson(res, json{{"ok", false}, {"error", atem->GetLastError()}}, 500);
        }
    });

    server.Post("/record/stop", [atem](const httplib::Request&, httplib::Response& res) {
        if (atem->StopRecording()) {
            SendJson(res, json{{"ok", true}});
        } else {
            SendJson(res, json{{"ok", false}, {"error", atem->GetLastError()}}, 500);
        }
    });
}
