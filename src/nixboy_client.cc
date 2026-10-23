#include "nixboy_client.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <deque>
#include <limits>
#include <set>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

namespace fallout {
namespace nixboy {
    namespace {

        constexpr size_t kMaxPayload = 65536;
        constexpr size_t kMaxQueuedBytes = 256 * 1024;
        constexpr size_t kPollBytes = 64 * 1024;
        constexpr size_t kPollFrames = 128;
        constexpr size_t kMaxContext = 128;
        constexpr uint8_t kAuth = 1;
        constexpr uint8_t kOpen = 2;
        constexpr uint8_t kInput = 3;
        constexpr uint8_t kResize = 4;
        constexpr uint8_t kClose = 5;
        constexpr uint8_t kContext = 6;
        constexpr uint8_t kTrainingOpen = 7;
        constexpr uint8_t kTrainingAction = 8;
        constexpr uint8_t kTrainingAck = 9;
        constexpr uint8_t kReady = 16;
        constexpr uint8_t kOutput = 17;
        constexpr uint8_t kResult = 18;
        constexpr uint8_t kExit = 19;
        constexpr uint8_t kError = 20;
        constexpr uint8_t kTrainingReward = 21;

#ifdef _WIN32
        using Socket = SOCKET;
        constexpr Socket kInvalidSocket = INVALID_SOCKET;
        int socketError() { return WSAGetLastError(); }
        bool wouldBlock(int error) { return error == WSAEWOULDBLOCK; }
        bool interrupted(int error) { return error == WSAEINTR; }
        void closeSocket(Socket socket) { closesocket(socket); }
        bool nonblocking(Socket socket)
        {
            u_long value = 1;
            return ioctlsocket(socket, FIONBIO, &value) == 0;
        }
#else
        using Socket = int;
        constexpr Socket kInvalidSocket = -1;
        int socketError() { return errno; }
        bool wouldBlock(int error) { return error == EAGAIN || error == EWOULDBLOCK; }
        bool interrupted(int error) { return error == EINTR; }
        void closeSocket(Socket socket) { ::close(socket); }
        bool nonblocking(Socket socket)
        {
            int flags = fcntl(socket, F_GETFL, 0);
            return flags >= 0 && fcntl(socket, F_SETFL, flags | O_NONBLOCK) == 0;
        }
#endif

        int sendBytes(Socket socket, const uint8_t* bytes, size_t length)
        {
#ifdef MSG_NOSIGNAL
            constexpr int flags = MSG_NOSIGNAL;
#else
            constexpr int flags = 0;
#endif
            return static_cast<int>(::send(socket, reinterpret_cast<const char*>(bytes), static_cast<int>(length), flags));
        }

        void put16(std::vector<uint8_t>& bytes, uint16_t value)
        {
            bytes.push_back(static_cast<uint8_t>(value >> 8));
            bytes.push_back(static_cast<uint8_t>(value));
        }

        void put32(std::vector<uint8_t>& bytes, uint32_t value)
        {
            bytes.push_back(static_cast<uint8_t>(value >> 24));
            bytes.push_back(static_cast<uint8_t>(value >> 16));
            bytes.push_back(static_cast<uint8_t>(value >> 8));
            bytes.push_back(static_cast<uint8_t>(value));
        }

        uint32_t get32(const uint8_t* bytes)
        {
            return (uint32_t(bytes[0]) << 24) | (uint32_t(bytes[1]) << 16)
                | (uint32_t(bytes[2]) << 8) | uint32_t(bytes[3]);
        }

        int32_t getSigned32(const uint8_t* bytes)
        {
            uint32_t bits = get32(bytes);
            return bits <= uint32_t(INT32_MAX) ? static_cast<int32_t>(bits)
                                               : -1 - static_cast<int32_t>(~bits);
        }

        bool letter(uint8_t ch)
        {
            return (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || ch == '_';
        }

        bool validName(const uint8_t* bytes, size_t length)
        {
            if (length == 0 || length > 64 || !letter(bytes[0])) {
                return false;
            }
            for (size_t index = 1; index < length; index++) {
                uint8_t ch = bytes[index];
                if (!letter(ch) && !(ch >= '0' && ch <= '9') && ch != '.' && ch != '-') {
                    return false;
                }
            }
            return true;
        }

        bool validDimensions(uint16_t rows, uint16_t cols)
        {
            return rows >= 1 && rows <= 200 && cols >= 1 && cols <= 300;
        }

        bool encodeContext(const Context& context, std::vector<uint8_t>& payload)
        {
            if (context.size() > kMaxContext) {
                return false;
            }
            std::set<std::string> names;
            for (const auto& item : context) {
                if (!validName(reinterpret_cast<const uint8_t*>(item.first.data()), item.first.size())
                    || !names.insert(item.first).second) {
                    return false;
                }
            }
            put16(payload, static_cast<uint16_t>(context.size()));
            for (const auto& item : context) {
                payload.push_back(static_cast<uint8_t>(item.first.size()));
                payload.insert(payload.end(), item.first.begin(), item.first.end());
                put32(payload, static_cast<uint32_t>(item.second));
            }
            return true;
        }

        bool validUtf8(const uint8_t* bytes, size_t length)
        {
            size_t index = 0;
            while (index < length) {
                uint8_t ch = bytes[index++];
                if (ch < 0x80) {
                    continue;
                }
                unsigned int count;
                uint32_t value;
                uint32_t minimum;
                if (ch >= 0xC2 && ch <= 0xDF) {
                    count = 1;
                    value = ch & 0x1F;
                    minimum = 0x80;
                } else if (ch >= 0xE0 && ch <= 0xEF) {
                    count = 2;
                    value = ch & 0x0F;
                    minimum = 0x800;
                } else if (ch >= 0xF0 && ch <= 0xF4) {
                    count = 3;
                    value = ch & 0x07;
                    minimum = 0x10000;
                } else {
                    return false;
                }
                if (count > length - index) {
                    return false;
                }
                while (count-- != 0) {
                    uint8_t next = bytes[index++];
                    if ((next & 0xC0) != 0x80) {
                        return false;
                    }
                    value = (value << 6) | (next & 0x3F);
                }
                if (value < minimum || value > 0x10FFFF || (value >= 0xD800 && value <= 0xDFFF)) {
                    return false;
                }
            }
            return true;
        }

    } // namespace

    struct Client::Impl {
        enum class State { Closed,
            Connected,
            AwaitingReady,
            Ready };
        Socket socket = kInvalidSocket;
        State state = State::Closed;
        bool training = false;
        std::deque<std::vector<uint8_t>> outgoing;
        size_t outgoingOffset = 0;
        size_t queuedBytes = 0;
        std::vector<uint8_t> incoming;
        size_t incomingOffset = 0;
#ifdef _WIN32
        bool winsockStarted = false;
#endif

        void disconnect()
        {
            if (socket != kInvalidSocket) {
                closeSocket(socket);
                socket = kInvalidSocket;
            }
#ifdef _WIN32
            if (winsockStarted) {
                WSACleanup();
                winsockStarted = false;
            }
#endif
            state = State::Closed;
            training = false;
            outgoing.clear();
            outgoingOffset = 0;
            queuedBytes = 0;
            incoming.clear();
            incomingOffset = 0;
        }

        bool queue(uint8_t type, const std::vector<uint8_t>& payload)
        {
            if (socket == kInvalidSocket || payload.size() > kMaxPayload
                || payload.size() + 5 > kMaxQueuedBytes - queuedBytes) {
                return false;
            }
            std::vector<uint8_t> frame;
            frame.reserve(payload.size() + 5);
            frame.push_back(type);
            put32(frame, static_cast<uint32_t>(payload.size()));
            frame.insert(frame.end(), payload.begin(), payload.end());
            queuedBytes += frame.size();
            outgoing.push_back(std::move(frame));
            return true;
        }

        // Byte and syscall budgets keep both slow readers and signals from taking
        // over the engine frame. Nonblocking mode is set before the first send.
        bool flush()
        {
            size_t budget = kPollBytes;
            size_t calls = 0;
            while (!outgoing.empty() && budget != 0 && calls++ < kPollFrames) {
                const auto& frame = outgoing.front();
                size_t count = std::min(frame.size() - outgoingOffset, budget);
                int sent = sendBytes(socket, frame.data() + outgoingOffset, count);
                if (sent < 0) {
                    int error = socketError();
                    if (wouldBlock(error)) {
                        return true;
                    }
                    if (interrupted(error)) {
                        continue;
                    }
                    return false;
                }
                if (sent == 0) {
                    return false;
                }
                outgoingOffset += static_cast<size_t>(sent);
                queuedBytes -= static_cast<size_t>(sent);
                budget -= static_cast<size_t>(sent);
                if (outgoingOffset == frame.size()) {
                    outgoing.pop_front();
                    outgoingOffset = 0;
                }
            }
            return true;
        }

        void fail(std::vector<Event>& events, const std::string& message)
        {
            events.push_back({ EventType::Error, message, {}, 0 });
            disconnect();
        }

        // Returns true if a complete frame was consumed (including a fatal frame).
        bool parse(std::vector<Event>& events)
        {
            size_t available = incoming.size() - incomingOffset;
            if (available < 5) {
                return false;
            }
            const uint8_t* header = incoming.data() + incomingOffset;
            uint8_t type = header[0];
            size_t length = get32(header + 1);
            bool ready = state == State::Ready;
            if (length > kMaxPayload
                || (type == kReady && (state != State::AwaitingReady || length != 0))
                || (type == kOutput && !ready)
                || (type == kResult && (!ready || training || length < 6 || length > 69))
                || (type == kTrainingReward && (!ready || !training || length != 18))
                || (type == kExit && (!ready || length != 4))
                || (type == kError && length > 4096)
                || (type != kReady && type != kOutput && type != kResult && type != kExit && type != kError && type != kTrainingReward)) {
                fail(events, "Invalid Nix-Boy protocol frame or session order.");
                return true;
            }
            if (available - 5 < length) {
                return false;
            }
            const uint8_t* payload = header + 5;
            incomingOffset += length + 5;
            switch (type) {
            case kReady:
                state = State::Ready;
                events.push_back({ EventType::Ready, {}, {}, 0 });
                break;
            case kOutput:
                events.push_back({ EventType::Output, std::string(reinterpret_cast<const char*>(payload), length), {}, 0 });
                break;
            case kResult: {
                size_t nameLength = payload[0];
                if (length != nameLength + 5 || !validName(payload + 1, nameLength)) {
                    fail(events, "Invalid Nix-Boy result name or length.");
                    return true;
                }
                events.push_back({ EventType::Result, {}, std::string(reinterpret_cast<const char*>(payload + 1), nameLength), getSigned32(payload + 1 + nameLength) });
                break;
            }
            case kExit:
                events.push_back({ EventType::Exit, {}, {}, getSigned32(payload) });
                disconnect();
                break;
            case kTrainingReward: {
                Event event { EventType::TrainingReward, {}, {}, 0 };
                event.skill = payload[0];
                event.level = payload[1];
                std::copy(payload + 2, payload + 18, event.receipt.begin());
                events.push_back(std::move(event));
                break;
            }
            case kError:
                if (!validUtf8(payload, length)) {
                    fail(events, "Nix-Boy bridge returned invalid UTF-8 error text.");
                    return true;
                }
                // Copy before disconnect invalidates the receive buffer.
                events.push_back({ EventType::Error, std::string(reinterpret_cast<const char*>(payload), length), {}, 0 });
                disconnect();
                break;
            }
            return true;
        }
    };

    Client::Client()
        : impl_(new Impl())
    {
    }

    Client::~Client()
    {
        close();
    }

    bool Client::connect(uint16_t port, const std::string& token, std::string& error)
    {
        close();
        error.clear();
        if (port == 0 || token.size() != 64
            || !std::all_of(token.begin(), token.end(), [](unsigned char ch) {
                   return (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f') || (ch >= 'A' && ch <= 'F');
               })) {
            error = "Nix-Boy requires a loopback port and a 64-character hexadecimal token.";
            return false;
        }
#ifdef _WIN32
        WSADATA data;
        if (WSAStartup(MAKEWORD(2, 2), &data) != 0) {
            error = "Unable to initialize Nix-Boy sockets.";
            return false;
        }
        impl_->winsockStarted = true;
#endif
        auto failed = [this, &error](const char* message) {
            error = message;
            impl_->disconnect();
            return false;
        };
        impl_->socket = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (impl_->socket == kInvalidSocket || !nonblocking(impl_->socket)) {
            return failed("Unable to create a nonblocking Nix-Boy socket.");
        }
#ifndef _WIN32
        // select cannot represent descriptors outside its fixed-size bitset.
        if (impl_->socket >= FD_SETSIZE) {
            return failed("Nix-Boy socket exceeds the platform descriptor limit.");
        }
#endif
#ifdef SO_NOSIGPIPE
        int noSigpipe = 1;
        if (setsockopt(impl_->socket, SOL_SOCKET, SO_NOSIGPIPE, &noSigpipe, sizeof(noSigpipe)) != 0) {
            return failed("Unable to configure Nix-Boy socket.");
        }
#endif
        sockaddr_in address {};
        address.sin_family = AF_INET;
        address.sin_port = htons(port);
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        int result = ::connect(impl_->socket, reinterpret_cast<sockaddr*>(&address), sizeof(address));
        if (result != 0) {
            int connectError = socketError();
#ifdef _WIN32
            bool pending = connectError == WSAEWOULDBLOCK || connectError == WSAEINPROGRESS;
#else
            bool pending = connectError == EINPROGRESS;
#endif
            if (!pending) {
                return failed("Cannot connect to the local Nix-Boy bridge.");
            }
            const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(250);
            for (;;) {
                auto remaining = deadline - std::chrono::steady_clock::now();
                if (remaining <= decltype(remaining)::zero()) {
                    return failed("Timed out connecting to the local Nix-Boy bridge.");
                }
                auto micros = std::chrono::duration_cast<std::chrono::microseconds>(remaining).count();
                timeval timeout { static_cast<long>(micros / 1000000), static_cast<long>(micros % 1000000) };
                fd_set writeSet;
                fd_set errorSet;
                FD_ZERO(&writeSet);
                FD_ZERO(&errorSet);
                FD_SET(impl_->socket, &writeSet);
                FD_SET(impl_->socket, &errorSet);
#ifdef _WIN32
                result = select(0, nullptr, &writeSet, &errorSet, &timeout);
#else
                result = select(impl_->socket + 1, nullptr, &writeSet, &errorSet, &timeout);
#endif
                if (result < 0 && interrupted(socketError())) {
                    continue;
                }
                if (result <= 0) {
                    return failed("Cannot connect to the local Nix-Boy bridge within 250 ms.");
                }
                int status = 0;
#ifdef _WIN32
                int statusLength = sizeof(status);
#else
                socklen_t statusLength = sizeof(status);
#endif
                if (getsockopt(impl_->socket, SOL_SOCKET, SO_ERROR, reinterpret_cast<char*>(&status), &statusLength) != 0 || status != 0) {
                    return failed("Cannot connect to the local Nix-Boy bridge.");
                }
                break;
            }
        }
        impl_->state = Impl::State::Connected;
        return impl_->queue(kAuth, std::vector<uint8_t>(token.begin(), token.end()));
    }

    bool Client::open(uint16_t rows, uint16_t cols, const Context& context, std::string& error, bool training)
    {
        error.clear();
        if (impl_->state != Impl::State::Connected) {
            error = "Nix-Boy session is disconnected or already open.";
            return false;
        }
        if (training && std::find(context.begin(), context.end(), std::make_pair(std::string("training.version"), int32_t(1))) == context.end()) {
            error = "Nix-Boy training requires context version 1.";
            return false;
        }
        std::vector<uint8_t> payload;
        put16(payload, rows);
        put16(payload, cols);
        if (!validDimensions(rows, cols) || !encodeContext(context, payload)) {
            error = "Invalid Nix-Boy terminal dimensions or context (maximum 128 unique names).";
            return false;
        }
        if (!impl_->queue(training ? kTrainingOpen : kOpen, payload)) {
            error = "Nix-Boy send queue is full.";
            return false;
        }
        impl_->state = Impl::State::AwaitingReady;
        impl_->training = training;
        return true;
    }

    bool Client::sendInput(const std::string& bytes)
    {
        if (impl_->state != Impl::State::Ready || bytes.size() > kMaxPayload) {
            return false;
        }
        if (bytes.empty()) {
            return true;
        }
        return impl_->queue(kInput, std::vector<uint8_t>(bytes.begin(), bytes.end()));
    }

    bool Client::resize(uint16_t rows, uint16_t cols)
    {
        if (impl_->state != Impl::State::Ready || !validDimensions(rows, cols)) {
            return false;
        }
        std::vector<uint8_t> payload;
        put16(payload, rows);
        put16(payload, cols);
        return impl_->queue(kResize, payload);
    }

    bool Client::sendContext(const Context& context)
    {
        if (impl_->state != Impl::State::Ready) {
            return false;
        }
        std::vector<uint8_t> payload;
        return encodeContext(context, payload) && impl_->queue(kContext, payload);
    }

    bool Client::sendTrainingAction(TrainingAction action)
    {
        uint8_t code = static_cast<uint8_t>(action);
        return impl_->state == Impl::State::Ready && impl_->training && code >= 1 && code <= 4
            && impl_->queue(kTrainingAction, { code });
    }

    bool Client::acknowledgeTraining(uint8_t skill, uint8_t level, const TrainingReceipt& receipt, bool accepted)
    {
        if (impl_->state != Impl::State::Ready || !impl_->training) return false;
        std::vector<uint8_t> payload { skill, level };
        payload.insert(payload.end(), receipt.begin(), receipt.end());
        payload.push_back(accepted ? 1 : 0);
        return impl_->queue(kTrainingAck, payload);
    }

    void Client::poll(std::vector<Event>& events)
    {
        if (!isConnected()) {
            return;
        }
        if (!impl_->flush()) {
            impl_->fail(events, "Nix-Boy bridge disconnected while sending.");
            return;
        }
        size_t budget = kPollBytes;
        size_t frames = 0;
        size_t calls = 0;
        std::array<uint8_t, 8192> bytes;
        while (isConnected()) {
            while (frames < kPollFrames && impl_->parse(events)) {
                frames++;
                if (!isConnected()) {
                    return;
                }
            }
            if (frames == kPollFrames || budget == 0 || calls++ == kPollFrames) {
                return;
            }
            if (impl_->incomingOffset != 0) {
                impl_->incoming.erase(impl_->incoming.begin(), impl_->incoming.begin() + impl_->incomingOffset);
                impl_->incomingOffset = 0;
            }
            size_t capacity = kMaxPayload + 5 - impl_->incoming.size();
            size_t count = std::min({ bytes.size(), budget, capacity });
            if (count == 0) {
                impl_->fail(events, "Nix-Boy receive buffer limit exceeded.");
                return;
            }
            int received = static_cast<int>(::recv(impl_->socket, reinterpret_cast<char*>(bytes.data()), static_cast<int>(count), 0));
            if (received < 0) {
                int error = socketError();
                if (wouldBlock(error)) {
                    return;
                }
                if (interrupted(error)) {
                    continue;
                }
                impl_->fail(events, "Nix-Boy bridge disconnected while receiving.");
                return;
            }
            if (received == 0) {
                impl_->fail(events, impl_->incoming.empty() ? "Nix-Boy bridge closed the connection." : "Nix-Boy bridge closed during a partial frame.");
                return;
            }
            impl_->incoming.insert(impl_->incoming.end(), bytes.begin(), bytes.begin() + received);
            budget -= static_cast<size_t>(received);
        }
    }

    void Client::close()
    {
        if (isConnected()) {
            impl_->queue(kClose, {});
            impl_->flush();
        }
        impl_->disconnect();
    }

    bool Client::isConnected() const
    {
        return impl_->socket != kInvalidSocket;
    }

} // namespace nixboy
} // namespace fallout
