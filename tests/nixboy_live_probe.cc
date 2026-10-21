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

namespace {

// Encode every marker byte so terminal input echo cannot satisfy output checks.
std::string octal(const std::string& value)
{
    std::string result;
    for (unsigned char byte : value) {
        result += '\\';
        result += static_cast<char>('0' + ((byte >> 6) & 7));
        result += static_cast<char>('0' + ((byte >> 3) & 7));
        result += static_cast<char>('0' + (byte & 7));
    }
    return result;
}

} // namespace

int main(int argc, char** argv)
{
    try {
        if (argc != 2) throw std::runtime_error("expected loopback port; token must arrive on stdin");
        std::size_t consumed = 0;
        int port = std::stoi(argv[1], &consumed);
        if (argv[1][consumed] != '\0' || port < 1 || port > 65535) {
            throw std::runtime_error("invalid loopback port");
        }
        std::string token(64, '\0');
        std::cin.read(&token[0], static_cast<std::streamsize>(token.size()));
        if (std::cin.gcount() != 64 || std::cin.get() != '\n') {
            throw std::runtime_error("stdin must contain one 64-byte token line");
        }

        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(20);
        const std::string sizeMarker = "NIXBOY_SIZE:";
        const std::string contextMarker = "NIXBOY_CONTEXT:";
        const std::string command = "stty -echo; printf '" + octal(sizeMarker)
            + "'; stty size; printf '" + octal(contextMarker)
            + "'; nixboy context probe.value; nixboy emit probe_result -19; exit 23\n";
        Client client;
        std::string error;
        if (!client.connect(static_cast<uint16_t>(port), token, error)
            || !client.open(24, 80, { { "probe.value", 73 } }, error)) {
            throw std::runtime_error(error);
        }
        token.clear();
        bool ready = false;
        bool result = false;
        std::string output;
        while (std::chrono::steady_clock::now() < deadline) {
            std::vector<Event> events;
            client.poll(events);
            for (const Event& event : events) {
                switch (event.type) {
                case EventType::Ready:
                    if (ready || !client.sendInput(command)) {
                        throw std::runtime_error("duplicate READY or rejected probe input");
                    }
                    ready = true;
                    break;
                case EventType::Output:
                    if (!ready || event.text.size() > 256 * 1024 - output.size()) {
                        throw std::runtime_error("output before READY or probe output limit exceeded");
                    }
                    output += event.text;
                    break;
                case EventType::Result:
                    if (!ready || result || event.name != "probe_result" || event.value != -19) {
                        throw std::runtime_error("unexpected probe result");
                    }
                    result = true;
                    break;
                case EventType::Exit:
                    if (!ready || !result || event.value != 23 || client.isConnected()
                        || output.find(sizeMarker + "24 80\r\n") == std::string::npos
                        || output.find(contextMarker + "73\r\n") == std::string::npos) {
                        throw std::runtime_error("PTY output, context, result or final status mismatch");
                    }
                    std::cout << "PASS: native C++ client -> WSL bridge -> Docker PTY, context, result and exit\n";
                    return EXIT_SUCCESS;
                case EventType::Error:
                    throw std::runtime_error(event.text);
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
        throw std::runtime_error("live probe exceeded its 20-second deadline");
    } catch (const std::exception& error) {
        std::cerr << "Live probe failed: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
