#include "nixboy_client.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using fallout::nixboy::Client;
using fallout::nixboy::Context;
using fallout::nixboy::Event;
using fallout::nixboy::EventType;

int main(int argc, char** argv)
{
    try {
        if (argc != 3) throw std::runtime_error("expected port and scenario");
        int port = std::stoi(argv[1]);
        if (port < 1 || port > 65535) throw std::runtime_error("invalid port");
        std::string scenario = argv[2];
        Client client;
        std::string error;
        Context context { { "player.hp", INT32_MAX }, { "global.minimum", INT32_MIN } };
        std::vector<fallout::nixboy::TrainingClaim> claims;
        if (scenario == "training") {
            context = { { "training.version", 3 }, { "training.profile.0", 1 }, { "training.profile.1", 0 },
                { "training.profile.2", 0 }, { "training.profile.3", 0 } };
            for (const char* name : { "small_guns", "big_guns", "energy_weapons", "unarmed", "melee_weapons", "throwing",
                     "first_aid", "doctor", "sneak", "lockpick", "steal", "traps", "science", "repair",
                     "speech", "barter", "gambling", "outdoorsman" })
                context.emplace_back(std::string("skill.") + name, 15);
            fallout::nixboy::TrainingReceipt first {}, second {};
            first.back() = 1;
            second.back() = 2;
            claims = { { 3, 2, first }, { 13, 300, second } };
        }
        if (!client.connect(static_cast<uint16_t>(port), std::string(64, scenario == "bad-auth" ? 'b' : 'a'), error)
            || !client.open(24, 80, context, error, scenario == "training", claims)) {
            throw std::runtime_error(error);
        }
        bool ready = false;
        bool minimum = false;
        bool maximum = false;
        bool rewarded = false;
        std::string output;
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(8);
        while (std::chrono::steady_clock::now() < deadline) {
            std::vector<Event> events;
            client.poll(events);
            for (const Event& event : events) {
                switch (event.type) {
                case EventType::Ready:
                    if (ready || scenario == "bad-auth") throw std::runtime_error("unexpected READY");
                    ready = true;
                    if (scenario == "training") {
                        if (!client.sendTrainingAction(fallout::nixboy::TrainingAction::Check))
                            throw std::runtime_error("training check rejected");
                        break;
                    }
                    if (!client.sendInput(std::string("command\0\r\x03", 10))
                        || !client.resize(30, 100)
                        || !client.sendContext({ { "quest.updated", -17 } })) {
                        throw std::runtime_error("ready session rejected outgoing messages");
                    }
                    if (scenario == "close") {
                        client.close();
                        std::cout << "C++ client explicit close passed\n";
                        return EXIT_SUCCESS;
                    }
                    break;
                case EventType::Output:
                    if (!ready) throw std::runtime_error("output before ready");
                    output += event.text;
                    break;
                case EventType::Result:
                    if (event.name == "minimum" && event.value == INT32_MIN)
                        minimum = true;
                    else if (event.name == "maximum" && event.value == INT32_MAX)
                        maximum = true;
                    else
                        throw std::runtime_error("unexpected signed RESULT");
                    break;
                case EventType::TrainingReward: {
                    fallout::nixboy::TrainingReceipt expected {};
                    expected.back() = 37;
                    if (scenario != "training" || rewarded || event.skill != 13 || event.level != UINT16_MAX || event.receipt != expected)
                        throw std::runtime_error("unexpected v3 training reward");
                    rewarded = true;
                    for (auto& value : context) {
                        if (value.first == "skill.repair") value.second = 20;
                    }
                    if (!client.acknowledgeTraining(event.skill, event.level, event.receipt, true) || !client.sendContext(context))
                        throw std::runtime_error("training acknowledgment or refreshed context rejected");
                    break;
                }
                case EventType::Exit:
                    if (scenario == "training") {
                        if (!rewarded || event.value != 42 || client.isConnected())
                            throw std::runtime_error("training reward/exit mismatch");
                        std::cout << "C++ client / Python v3 training ledger and uint16 reward round trip passed\n";
                        return EXIT_SUCCESS;
                    }
                    if (!minimum || !maximum || event.value != 42
                        || output != std::string("Linux \0\x1b[32mready\r\n", 19) || client.isConnected()) {
                        throw std::runtime_error("wire data or event-before-EXIT ordering mismatch");
                    }
                    std::cout << "C++ client / Python bridge wire round trip passed\n";
                    return EXIT_SUCCESS;
                case EventType::Error:
                    if (scenario == "bad-auth" && !ready && !client.isConnected()) {
                        std::cout << "C++ client rejected authentication passed\n";
                        return EXIT_SUCCESS;
                    }
                    throw std::runtime_error(event.text);
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        throw std::runtime_error("bridge round trip timed out");
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
