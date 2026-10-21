#ifndef FALLOUT_NIXBOY_CLIENT_H_
#define FALLOUT_NIXBOY_CLIENT_H_

#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace fallout {
namespace nixboy {

    using Context = std::vector<std::pair<std::string, int32_t>>;

    enum class EventType { Ready,
        Output,
        Result,
        Exit,
        Error };

    struct Event {
        EventType type;
        std::string text;
        std::string name;
        int32_t value = 0;
    };

    // Single-threaded, frame-driven client. No game objects or callbacks cross this
    // boundary. Connect is loopback-only and bounded to 250 ms; other calls never
    // wait for socket readiness. poll appends events to the caller's vector.
    class Client {
    public:
        Client();
        ~Client();
        Client(const Client&) = delete;
        Client& operator=(const Client&) = delete;
        Client(Client&&) = delete;
        Client& operator=(Client&&) = delete;

        bool connect(uint16_t port, const std::string& token, std::string& error);
        bool open(uint16_t rows, uint16_t cols, const Context& context, std::string& error);
        // False means not ready, invalid arguments, or bounded send queue full.
        bool sendInput(const std::string& bytes);
        bool resize(uint16_t rows, uint16_t cols);
        bool sendContext(const Context& context);
        void poll(std::vector<Event>& events);
        void close();
        bool isConnected() const;

    private:
        struct Impl;
        std::unique_ptr<Impl> impl_;
    };

} // namespace nixboy
} // namespace fallout

#endif // FALLOUT_NIXBOY_CLIENT_H_
