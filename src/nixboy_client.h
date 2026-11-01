#ifndef FALLOUT_NIXBOY_CLIENT_H_
#define FALLOUT_NIXBOY_CLIENT_H_

#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "nixboy_training_state.h"

namespace fallout {
namespace nixboy {

    using Context = std::vector<std::pair<std::string, int32_t>>;

    enum class EventType { Ready,
        Output,
        Result,
        TrainingReward,
        PerkTrainingReward,
        Exit,
        Error };

    struct Event {
        EventType type;
        std::string text;
        std::string name;
        int32_t value = 0;
        uint8_t skill = 0;
        uint16_t level = 0;
        uint16_t points = 0;
        TrainingReceipt receipt {};
        TrainingReceipt profile {};
        uint16_t perk = 0;
        uint8_t rank = 0;
    };

    enum class TrainingAction : uint8_t { Check = 1,
        Hub = 2,
        Reset = 3,
        Abandon = 4 };

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
        bool open(uint16_t rows, uint16_t cols, const Context& context, std::string& error, bool training = false,
            const std::vector<TrainingClaim>& claims = {}, const std::vector<PerkTrainingClaim>& perkClaims = {});
        // False means not ready, invalid arguments, or bounded send queue full.
        bool sendInput(const std::string& bytes);
        bool resize(uint16_t rows, uint16_t cols);
        bool sendContext(const Context& context);
        bool sendTrainingAction(TrainingAction action);
        bool acknowledgeTraining(uint8_t skill, uint16_t level, uint16_t points, const TrainingReceipt& receipt, bool accepted);
        bool acknowledgePerkTraining(const TrainingReceipt& profile, uint16_t perk, uint8_t rank,
            const TrainingReceipt& receipt, bool accepted);
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
