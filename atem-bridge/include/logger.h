// Minimal thread-safe logger for the ATEM bridge.
// Writes to stdout and to atem-bridge.log. Verbosity via LOG_LEVEL env var.

#pragma once

#include <cstdlib>
#include <ctime>
#include <cstring>
#include <fstream>
#include <iostream>
#include <mutex>
#include <string>

namespace atembridge {

enum class LogLevel { Debug = 0, Info = 1, Warn = 2, Error = 3 };

class Logger {
public:
    static Logger& Instance() {
        static Logger instance;
        return instance;
    }

    void Log(LogLevel level, const std::string& message) {
        if (level < m_min_level) {
            return;
        }

        std::lock_guard<std::mutex> lock(m_mutex);

        char timestamp[32];
        std::time_t now = std::time(nullptr);
        std::tm tm_buf{};
#ifdef _WIN32
        localtime_s(&tm_buf, &now);
#else
        localtime_r(&now, &tm_buf);
#endif
        std::strftime(timestamp, sizeof(timestamp), "%Y-%m-%d %H:%M:%S", &tm_buf);

        const std::string line =
            std::string(timestamp) + " [" + LevelName(level) + "] " + message;

        std::cout << line << std::endl;
        if (m_file.is_open()) {
            m_file << line << std::endl;
            m_file.flush();
        }
    }

private:
    Logger() {
        m_min_level = LevelFromEnv();
        m_file.open("atem-bridge.log", std::ios::app);
    }

    ~Logger() {
        if (m_file.is_open()) {
            m_file.close();
        }
    }

    static LogLevel LevelFromEnv() {
        const char* env = std::getenv("LOG_LEVEL");
        if (env == nullptr) {
            return LogLevel::Info;
        }
        if (_stricmp_compat(env, "DEBUG")) return LogLevel::Debug;
        if (_stricmp_compat(env, "INFO")) return LogLevel::Info;
        if (_stricmp_compat(env, "WARN") || _stricmp_compat(env, "WARNING")) return LogLevel::Warn;
        if (_stricmp_compat(env, "ERROR")) return LogLevel::Error;
        return LogLevel::Info;
    }

    static bool _stricmp_compat(const char* a, const char* b) {
#ifdef _WIN32
        return _stricmp(a, b) == 0;
#else
        return strcasecmp(a, b) == 0;
#endif
    }

    static const char* LevelName(LogLevel level) {
        switch (level) {
            case LogLevel::Debug: return "DEBUG";
            case LogLevel::Info: return "INFO";
            case LogLevel::Warn: return "WARN";
            case LogLevel::Error: return "ERROR";
        }
        return "INFO";
    }

    std::mutex m_mutex;
    std::ofstream m_file;
    LogLevel m_min_level;
};

}  // namespace atembridge

#define LOG_DEBUG(msg) ::atembridge::Logger::Instance().Log(::atembridge::LogLevel::Debug, (msg))
#define LOG_INFO(msg) ::atembridge::Logger::Instance().Log(::atembridge::LogLevel::Info, (msg))
#define LOG_WARN(msg) ::atembridge::Logger::Instance().Log(::atembridge::LogLevel::Warn, (msg))
#define LOG_ERROR(msg) ::atembridge::Logger::Instance().Log(::atembridge::LogLevel::Error, (msg))
