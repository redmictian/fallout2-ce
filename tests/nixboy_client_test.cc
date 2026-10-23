#include "nixboy_client.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

using fallout::nixboy::Client;
using fallout::nixboy::Context;
using fallout::nixboy::Event;
using fallout::nixboy::EventType;
using Bytes = std::vector<uint8_t>;

namespace {

const std::string kToken(64, 'a');

void require(bool condition, const char* message)
{
    if (!condition) {
        throw std::runtime_error(message);
    }
}

#ifdef _WIN32
using Socket = SOCKET;
constexpr Socket kInvalid = INVALID_SOCKET;
void closeSocket(Socket socket) { closesocket(socket); }
struct Networking {
    Networking()
    {
        WSADATA data;
        require(WSAStartup(MAKEWORD(2, 2), &data) == 0, "WSAStartup");
    }
    ~Networking() { WSACleanup(); }
};
#else
using Socket = int;
constexpr Socket kInvalid = -1;
void closeSocket(Socket socket) { ::close(socket); }
struct Networking { };
#endif

void put16(Bytes& bytes, uint16_t value)
{
    bytes.push_back(static_cast<uint8_t>(value >> 8));
    bytes.push_back(static_cast<uint8_t>(value));
}

void put32(Bytes& bytes, uint32_t value)
{
    bytes.push_back(static_cast<uint8_t>(value >> 24));
    bytes.push_back(static_cast<uint8_t>(value >> 16));
    bytes.push_back(static_cast<uint8_t>(value >> 8));
    bytes.push_back(static_cast<uint8_t>(value));
}

uint32_t get32(const uint8_t* bytes)
{
    return (uint32_t(bytes[0]) << 24) | (uint32_t(bytes[1]) << 16) | (uint32_t(bytes[2]) << 8) | bytes[3];
}

Bytes frame(uint8_t type, const Bytes& payload = {})
{
    Bytes bytes { type };
    put32(bytes, static_cast<uint32_t>(payload.size()));
    bytes.insert(bytes.end(), payload.begin(), payload.end());
    return bytes;
}

Bytes bytes(const std::string& value)
{
    return Bytes(value.begin(), value.end());
}

Bytes contextBytes(const Context& context)
{
    Bytes payload;
    put16(payload, static_cast<uint16_t>(context.size()));
    for (const auto& item : context) {
        payload.push_back(static_cast<uint8_t>(item.first.size()));
        payload.insert(payload.end(), item.first.begin(), item.first.end());
        put32(payload, static_cast<uint32_t>(item.second));
    }
    return payload;
}

struct Service {
    Socket listener = kInvalid;
    Socket peer = kInvalid;
    uint16_t port = 0;

    Service()
    {
        listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        require(listener != kInvalid, "listener socket");
        sockaddr_in address {};
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        require(bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0, "bind");
        require(listen(listener, 1) == 0, "listen");
#ifdef _WIN32
        int length = sizeof(address);
#else
        socklen_t length = sizeof(address);
#endif
        require(getsockname(listener, reinterpret_cast<sockaddr*>(&address), &length) == 0, "getsockname");
        port = ntohs(address.sin_port);
    }

    ~Service()
    {
        closePeer();
        if (listener != kInvalid) {
            closeSocket(listener);
        }
    }

    void closePeer()
    {
        if (peer != kInvalid) {
            closeSocket(peer);
            peer = kInvalid;
        }
    }

    void acceptClient()
    {
        peer = accept(listener, nullptr, nullptr);
        require(peer != kInvalid, "accept");
#ifdef _WIN32
        DWORD timeout = 2000;
#else
        timeval timeout { 2, 0 };
#endif
        require(setsockopt(peer, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout)) == 0, "receive timeout");
        require(setsockopt(peer, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout)) == 0, "send timeout");
    }

    void send(const Bytes& value)
    {
        size_t offset = 0;
        while (offset < value.size()) {
#ifdef MSG_NOSIGNAL
            constexpr int flags = MSG_NOSIGNAL;
#else
            constexpr int flags = 0;
#endif
            int sent = static_cast<int>(::send(peer, reinterpret_cast<const char*>(value.data() + offset), static_cast<int>(value.size() - offset), flags));
            require(sent > 0, "service send timed out");
            offset += static_cast<size_t>(sent);
        }
    }

    Bytes read(size_t length)
    {
        Bytes value(length);
        size_t offset = 0;
        while (offset < length) {
            int count = static_cast<int>(recv(peer, reinterpret_cast<char*>(value.data() + offset), static_cast<int>(length - offset), 0));
            require(count > 0, "service receive timed out");
            offset += static_cast<size_t>(count);
        }
        return value;
    }

    Bytes readFrame(uint8_t type)
    {
        Bytes header = read(5);
        require(header[0] == type, "unexpected client frame type");
        uint32_t length = get32(header.data() + 1);
        require(length <= 65536, "client frame length limit");
        return read(length);
    }
};

void start(Service& service, Client& client, const Context& context = {}, bool training = false)
{
    std::string error;
    require(client.connect(service.port, kToken, error), error.c_str());
    service.acceptClient();
    require(client.open(24, 80, context, error, training), error.c_str());
    std::vector<Event> events;
    client.poll(events);
    require(events.empty(), "unexpected event before server sends");
    require(service.readFrame(1) == bytes(kToken), "AUTH exact token");
    Bytes expected;
    put16(expected, 24);
    put16(expected, 80);
    Bytes encoded = contextBytes(context);
    expected.insert(expected.end(), encoded.begin(), encoded.end());
    require(service.readFrame(training ? 7 : 2) == expected, "OPEN dimensions/context signed encoding");
}

void pollUntil(Client& client, std::vector<Event>& events, size_t count)
{
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (events.size() < count && std::chrono::steady_clock::now() < deadline) {
        client.poll(events);
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    require(events.size() >= count, "timed out polling client events");
}

void makeReady(Service& service, Client& client)
{
    service.send(frame(16));
    std::vector<Event> events;
    pollUntil(client, events, 1);
    require(events.size() == 1 && events[0].type == EventType::Ready, "READY event");
}

void expectError(const Bytes& value, bool ready = false)
{
    Service service;
    Client client;
    start(service, client);
    if (ready) {
        makeReady(service, client);
    }
    service.send(value);
    std::vector<Event> events;
    pollUntil(client, events, 1);
    require(events.size() == 1 && events[0].type == EventType::Error, "malformed peer must produce exactly one error");
    require(!client.isConnected(), "malformed peer must disconnect");
    client.poll(events);
    require(events.size() == 1, "disconnection must not repeat error");
}

void testValidation()
{
    Client client;
    std::string error;
    require(!client.connect(0, kToken, error) && !error.empty(), "zero port");
    require(!client.connect(1234, "short", error), "short token");
    require(!client.connect(1234, std::string(64, 'z'), error), "nonhex token");
    require(!client.open(24, 80, {}, error), "open before connect");
    require(!client.sendInput("x") && !client.resize(24, 80) && !client.sendContext({}), "writes before connect");
    uint16_t closedPort;
    {
        Service service;
        closedPort = service.port;
    }
    auto before = std::chrono::steady_clock::now();
    require(!client.connect(closedPort, kToken, error), "refused loopback port");
    require(std::chrono::steady_clock::now() - before < std::chrono::milliseconds(500), "connect bounded");
    Service service;
    require(client.connect(service.port, kToken, error), "connect for context validation");
    service.acceptClient();
    require(!client.open(0, 80, {}, error), "zero rows");
    require(!client.open(201, 80, {}, error), "too many rows");
    require(!client.open(24, 301, {}, error), "too many columns");
    require(!client.open(24, 80, { { "duplicate", 1 }, { "duplicate", 2 } }, error), "duplicate context name");
    require(!client.open(24, 80, { { "9invalid", 1 } }, error), "invalid first name char");
    require(!client.open(24, 80, { { "bad name", 1 } }, error), "invalid name char");
    require(!client.open(24, 80, { { std::string(65, 'a'), 1 } }, error), "context name limit");
    require(!client.open(24, 80, {}, error, true), "training requires version context");
    require(!client.open(24, 80, { { "training.version", 2 } }, error, true), "unsupported training context version");
    Context large;
    for (int index = 0; index < 129; index++) {
        large.emplace_back("value" + std::to_string(index), index);
    }
    require(!client.open(24, 80, large, error), "context count limit");
    require(client.open(200, 300, { { std::string(64, 'a'), INT32_MIN } }, error), "maximum valid dimensions and name");
    require(!client.open(24, 80, {}, error), "OPEN once");
    require(!client.sendInput("x") && !client.resize(24, 80) && !client.sendContext({}), "writes wait for READY");
}

void testRoundTrip()
{
    Service service;
    Client client;
    start(service, client, { { "player.level", INT32_MAX }, { "_negative-test", INT32_MIN } });
    Bytes ready = frame(16);
    std::vector<Event> events;
    for (size_t index = 0; index < ready.size() - 1; index++) {
        service.send({ ready[index] });
        client.poll(events);
        require(events.empty(), "fragmented header must wait without event");
    }
    service.send({ ready.back() });
    pollUntil(client, events, 1);
    require(events[0].type == EventType::Ready, "fragmented READY");
    events.clear();
    std::string input("hello\0\r", 7);
    require(client.sendInput(""), "empty input is a no-op");
    require(client.sendInput(input), "queue binary INPUT");
    require(client.resize(30, 100), "queue RESIZE");
    Context context { { "quest.result", -17 } };
    require(client.sendContext(context), "queue CONTEXT");
    client.poll(events);
    require(service.readFrame(3) == bytes(input), "binary input preserved");
    Bytes dimensions;
    put16(dimensions, 30);
    put16(dimensions, 100);
    require(service.readFrame(4) == dimensions, "resize wire encoding");
    require(service.readFrame(6) == contextBytes(context), "context wire encoding");
    Bytes combined = frame(17, bytes(std::string("x\0\x1b[31m", 7)));
    Bytes result { 5, 's', 'c', 'o', 'r', 'e' };
    put32(result, static_cast<uint32_t>(INT32_MIN));
    Bytes resultFrame = frame(18, result);
    combined.insert(combined.end(), resultFrame.begin(), resultFrame.end());
    Bytes exit;
    put32(exit, static_cast<uint32_t>(-7));
    Bytes exitFrame = frame(19, exit);
    combined.insert(combined.end(), exitFrame.begin(), exitFrame.end());
    service.send(combined);
    pollUntil(client, events, 3);
    require(events.size() == 3, "coalesced frame count");
    require(events[0].type == EventType::Output && events[0].text == std::string("x\0\x1b[31m", 7), "binary output preserved");
    require(events[1].type == EventType::Result && events[1].name == "score" && events[1].value == INT32_MIN, "signed result decoded");
    require(events[2].type == EventType::Exit && events[2].value == -7 && !client.isConnected(), "signed exit closes session");
    client.poll(events);
    require(events.size() == 3, "no EOF error following EXIT");
}

void testMalformedFrames()
{
    expectError(frame(21, Bytes(18, 1)));
    expectError(frame(21, Bytes(18, 1)), true); // Legacy sessions cannot award training.
    expectError(frame(17, bytes("before-ready")));
    expectError(frame(18, { 1, 'x', 0, 0, 0, 1 }));
    expectError(frame(19, { 0, 0, 0, 0 }));
    expectError(frame(16, { 0 }));
    expectError(frame(3));
    expectError(frame(255));
    expectError({ 17, 0, 1, 0, 1 }, true); // Reject oversized header without waiting for payload.
    expectError(frame(16), true); // Duplicate READY.
    expectError(frame(19, { 0, 0, 0 }), true);
    expectError(frame(18, { 1, '9', 0, 0, 0, 1 }), true);
    expectError(frame(18, { 2, 'x', 0, 0, 0, 1 }), true);
    expectError(frame(18, { 0, 0, 0, 0, 1 }), true);
    expectError(frame(20, Bytes(4097, 'x')));
    expectError(frame(20, { 0xC0, 0xAF })); // Overlong UTF-8.
    expectError(frame(20, { 0xED, 0xA0, 0x80 })); // Surrogate.
    expectError(frame(20, { 0xF4, 0x90, 0x80, 0x80 })); // Beyond Unicode.
    Service service;
    Client client;
    start(service, client);
    std::string message = "bridge unavailable \xE2\x98\x83";
    service.send(frame(20, bytes(message)));
    std::vector<Event> events;
    pollUntil(client, events, 1);
    require(events[0].type == EventType::Error && events[0].text == message && !client.isConnected(), "valid UTF-8 error preserved and fatal");
}

void testTrainingProtocol()
{
    using fallout::nixboy::TrainingAction;
    using fallout::nixboy::TrainingReceipt;
    Service service;
    Client client;
    Context context { { "training.version", 1 }, { "training.profile.0", INT32_MIN } };
    start(service, client, context, true);
    TrainingReceipt receipt {};
    receipt.back() = 37;
    require(!client.sendTrainingAction(TrainingAction::Check), "actions wait for READY");
    require(!client.acknowledgeTraining(3, 1, receipt, true), "ack waits for READY");
    makeReady(service, client);
    for (int code = 1; code <= 4; code++)
        require(client.sendTrainingAction(static_cast<TrainingAction>(code)), "training action accepted");
    require(!client.sendTrainingAction(static_cast<TrainingAction>(5)), "invalid training action rejected");
    std::vector<Event> events;
    client.poll(events);
    for (int code = 1; code <= 4; code++)
        require(service.readFrame(8) == Bytes { static_cast<uint8_t>(code) }, "action wire encoding");
    Bytes reward { 3, 1 };
    reward.insert(reward.end(), receipt.begin(), receipt.end());
    service.send(frame(21, reward));
    pollUntil(client, events, 1);
    require(events.size() == 1 && events[0].type == EventType::TrainingReward
            && events[0].skill == 3 && events[0].level == 1 && events[0].receipt == receipt,
        "typed reward receipt decoded");
    require(client.acknowledgeTraining(3, 1, receipt, true), "queue accepted acknowledgment");
    require(client.acknowledgeTraining(3, 1, receipt, false), "queue rejected acknowledgment");
    events.clear();
    client.poll(events);
    Bytes ack = reward;
    ack.push_back(1);
    require(service.readFrame(9) == ack, "accepted acknowledgment preserves receipt");
    ack.back() = 0;
    require(service.readFrame(9) == ack, "rejected acknowledgment explicit");
    service.send(frame(21, Bytes(17, 1)));
    pollUntil(client, events, 1);
    require(events[0].type == EventType::Error && !client.isConnected(), "malformed reward length disconnects");

    Service helperService;
    Client helperClient;
    start(helperService, helperClient, context, true);
    makeReady(helperService, helperClient);
    helperService.send(frame(18, { 1, 'x', 0, 0, 0, 1 }));
    events.clear();
    pollUntil(helperClient, events, 1);
    require(events[0].type == EventType::Error && !helperClient.isConnected(), "legacy helper result cannot enter training session");

    Service earlyService;
    Client earlyClient;
    start(earlyService, earlyClient, context, true);
    earlyService.send(frame(21, reward));
    events.clear();
    pollUntil(earlyClient, events, 1);
    require(events[0].type == EventType::Error && !earlyClient.isConnected(), "training reward before READY rejected");

    Service legacyService;
    Client legacy;
    start(legacyService, legacy);
    makeReady(legacyService, legacy);
    require(!legacy.sendTrainingAction(TrainingAction::Check) && !legacy.acknowledgeTraining(3, 1, receipt, true), "legacy client cannot send training controls");
}

void testFragmentedPayloadAndLimits()
{
    Service service;
    Client client;
    start(service, client);
    makeReady(service, client);
    Bytes value = frame(17, Bytes(65536, 'x'));
    service.send(Bytes(value.begin(), value.begin() + 13));
    std::vector<Event> events;
    client.poll(events);
    require(events.empty(), "partial payload is not emitted");
    auto before = std::chrono::steady_clock::now();
    for (int index = 0; index < 1000; index++) {
        client.poll(events);
    }
    require(std::chrono::steady_clock::now() - before < std::chrono::seconds(1), "partial payload never stalls poll");
    require(events.empty(), "idle partial payload emits no events");
    service.send(Bytes(value.begin() + 13, value.end()));
    pollUntil(client, events, 1);
    require(events.size() == 1 && events[0].type == EventType::Output && events[0].text.size() == 65536, "maximum frame assembled across polls");
    Bytes many;
    for (int index = 0; index < 1000; index++) {
        Bytes emptyOutput = frame(17);
        many.insert(many.end(), emptyOutput.begin(), emptyOutput.end());
    }
    service.send(many);
    events.clear();
    client.poll(events);
    require(events.size() <= 128, "per-poll frame budget");
    pollUntil(client, events, 1000);
    require(events.size() == 1000, "coalesced frame backlog drained without loss");
    require(!client.sendInput(std::string(65537, 'x')), "oversized input rejected");
    for (int index = 0; index < 3; index++) {
        require(client.sendInput(std::string(65536, 'x')), "bounded queue accepts three maximum frames");
    }
    require(!client.sendInput(std::string(65536, 'x')), "send queue applies backpressure");
    before = std::chrono::steady_clock::now();
    for (int index = 0; index < 1000; index++) {
        client.sendInput(std::string(8192, 'x'));
        client.poll(events);
    }
    require(std::chrono::steady_clock::now() - before < std::chrono::seconds(2), "slow peer never stalls poll or input");
}

void testDisconnectAndClose()
{
    for (int partial = 0; partial < 2; partial++) {
        Service service;
        Client client;
        start(service, client);
        makeReady(service, client);
        if (partial != 0) {
            service.send({ 17, 0, 0, 0, 5, 'x' });
        }
        service.closePeer();
        std::vector<Event> events;
        pollUntil(client, events, 1);
        require(events.size() == 1 && events[0].type == EventType::Error && !client.isConnected(), "EOF disconnect");
        require(partial == 0 || events[0].text.find("partial") != std::string::npos, "partial EOF diagnostic");
    }
    Service service;
    Client client;
    start(service, client);
    makeReady(service, client);
    client.close();
    require(!client.isConnected(), "explicit close disconnects");
    require(service.readFrame(5).empty(), "CLOSE sent before disconnect");
    client.close();
    // The same object may be reused after normal close or any failed session.
    service.closePeer();
    std::string error;
    require(client.connect(service.port, kToken, error), "reconnect after close");
    service.acceptClient();
    require(client.open(24, 80, {}, error), "reopen after close");
}

} // namespace

int main()
{
    try {
        [[maybe_unused]] Networking networking;
        testValidation();
        testRoundTrip();
        testTrainingProtocol();
        testMalformedFrames();
        testFragmentedPayloadAndLimits();
        testDisconnectAndClose();
        std::cout << "Nix-Boy client: all tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "Nix-Boy client test failure: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
