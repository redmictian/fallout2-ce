#include "nixboy_client.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using fallout::nixboy::Client;
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
        if (!client.connect(static_cast<uint16_t>(port), std::string(64, scenario == "bad-auth" ? 'b' : 'a'), error)
            || !client.open(24, 80, { { "player.hp", INT32_MAX }, { "global.minimum", INT32_MIN } }, error)) {
            throw std::runtime_error(error);
        }
        bool ready = false;
        bool minimum = false;
        bool maximum = false;
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
                case EventType::Exit:
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
