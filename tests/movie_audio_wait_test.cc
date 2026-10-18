// Focus-loss regression: an audio cursor cannot advance until the event pump
// resumes it. Compile with section GC to test the actual movie wait function
// without linking the game or opening a window.
#define SDL_MAIN_HANDLED
#include <cstdio>
#include <cstdlib>
#include "../src/movie_lib.cc"

namespace fallout {
static unsigned int testCursor;
static int testPolls;
static int testPumps;
static int testDelays;
unsigned int compat_timeGetTime() { return 0; }
void _GNW95_process_message() { ++testPumps; testCursor = 300; }
bool audioEngineSoundBufferGetStatus(int, unsigned int* status) {
    *status = AUDIO_ENGINE_SOUND_BUFFER_STATUS_PLAYING;
    return true;
}
bool audioEngineSoundBufferGetCurrentPosition(int, unsigned int* read, unsigned int* write) {
    if (++testPolls > 10) {
        std::fputs("FAIL: movie waits on paused audio without processing focus events\n", stderr);
        std::exit(1);
    }
    *read = testCursor;
    *write = testCursor;
    return true;
}
bool audioEngineSoundBufferSetCurrentPosition(int, unsigned int) { return true; }
bool audioEngineSoundBufferPlay(int, unsigned int) { return true; }
bool audioEngineSoundBufferStop(int) { return true; }
static void resetTest(unsigned int cursor) {
    testCursor = cursor;
    testPolls = testPumps = testDelays = 0;
    _sync_active = 0;
    _sync_wait_quanta = -1000;
    _sync_FrameDropped = 0;
    gMveSoundBuffer = 0;
    gMveBufferBytes = 1000;
    dword_6B36A4 = 400;
    dword_6B39E0[0] = 200;
    _gSoundTimeBase = 0;
    dword_6B3660 = 0;
    dword_6B3AE4 = 1;
}
}
void delay_ms(int ms) {
    if (ms != 1) std::abort();
    ++fallout::testDelays;
}
int main() {
    using namespace fallout;
    resetTest(100);
    _MVE_sndSync();
    if (testPumps != 1 || testDelays != 1 || testPolls != 2) return 2;
    resetTest(300);
    _MVE_sndSync();
    if (testPumps != 0 || testDelays != 0 || testPolls != 1) return 3;
    resetTest(100);
    gMveSoundBuffer = -1;
    _MVE_sndSync();
    if (testPumps != 0 || testPolls != 0) return 4;
    std::puts("PASS: paused cursor resumes through event pump; ready/no-audio paths unchanged");
}
