import XCTest
@testable import PrepPalCore

final class ServerControllerTests: XCTestCase {
    private var logDirectory: URL!
    private var controllers: [ServerController] = []

    override func setUpWithError() throws {
        logDirectory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        // A test that fails mid-flight would otherwise strand its fixture for five minutes.
        controllers.forEach { _ = $0.stop() }
        controllers.removeAll()
        try? FileManager.default.removeItem(at: logDirectory)
    }

    private func controller(_ mode: String, readyTimeout: TimeInterval = 10, grace: TimeInterval = 10) throws -> ServerController {
        let script = try XCTUnwrap(Bundle.module.url(forResource: "fake_server", withExtension: "sh", subdirectory: "Fixtures"))
        let server = ServerController(configuration: ServerConfiguration(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: [script.path, mode],
            environment: ["PATH": "/usr/bin:/bin"],
            logFile: logDirectory.appendingPathComponent("server-\(mode).log"),
            readyTimeout: readyTimeout,
            stopGracePeriod: grace
        ))
        controllers.append(server)
        return server
    }

    private func firstEvent(of server: ServerController, timeout: TimeInterval = 15) -> ServerEvent? {
        let received = expectation(description: "start completion")
        var event: ServerEvent?
        server.start { event = $0; received.fulfill() }
        wait(for: [received], timeout: timeout)
        return event
    }

    private func log(_ mode: String) throws -> String {
        try String(contentsOf: logDirectory.appendingPathComponent("server-\(mode).log"), encoding: .utf8)
    }

    private func isAlive(_ pid: pid_t) -> Bool { kill(pid, 0) == 0 }

    func testReadyReportsThePortAndLogsTheOutput() throws {
        let server = try controller("ready")
        XCTAssertEqual(firstEvent(of: server), .ready(port: 45678))
        XCTAssertTrue(try log("ready").contains("INFO: starting"))
        XCTAssertEqual(server.stop(), .stoppedGracefully)
    }

    func testALockedFolderIsReported() throws {
        XCTAssertEqual(firstEvent(of: try controller("locked")), .failed(.locked(pid: "4242")))
    }

    func testExitingThreeWithoutALockedLineIsNotReportedAsALock() throws {
        // uvicorn's own startup failure exits 3 as well, so status 3 alone means nothing:
        // only the LOCKED line the server prints may be reported as a held lock.
        let event = firstEvent(of: try controller("locked-silent"))
        XCTAssertEqual(event, .failed(.exitedBeforeReady(status: 3)))
        if case .failed(.locked)? = event { XCTFail("status 3 without a LOCKED line was read as a lock") }
    }

    func testAMalformedProtocolLineIsReportedAndTheServerKilled() throws {
        let server = try controller("malformed")
        XCTAssertEqual(firstEvent(of: server), .failed(.malformedLine("READY soon")))
        let pid = try XCTUnwrap(server.pid)
        let gone = expectation(description: "server killed")
        DispatchQueue.global().asyncAfter(deadline: .now() + 1) { if kill(pid, 0) != 0 { gone.fulfill() } }
        wait(for: [gone], timeout: 5)
    }

    func testNoReadyWithinTheTimeoutIsReportedAndTheServerKilled() throws {
        let server = try controller("silent", readyTimeout: 1)
        let unexpectedExit = expectation(description: "onExit must not run after startup timeout")
        unexpectedExit.isInverted = true
        server.onExit = { _ in unexpectedExit.fulfill() }
        XCTAssertEqual(firstEvent(of: server), .failed(.readyTimeout(seconds: 1)))
        let pid = try XCTUnwrap(server.pid)
        let gone = expectation(description: "server killed")
        DispatchQueue.global().asyncAfter(deadline: .now() + 1) { if kill(pid, 0) != 0 { gone.fulfill() } }
        wait(for: [gone], timeout: 5)
        wait(for: [unexpectedExit], timeout: 0.2)
    }

    func testAReadyLineThatLostTheDeadlineRaceIsRefusedWithoutRecordingReadiness() throws {
        // The two orderings of this race cannot be forced through a real server: the deadline
        // kills the process group microseconds after it fires, so a fixture's READY either
        // arrives well before the deadline or never reaches the pipe at all. The losing
        // ordering is therefore driven directly, exactly as the reader thread would drive it.
        let server = try controller("silent", readyTimeout: 1)
        var events: [ServerEvent] = []

        let deadlineWon = expectation(description: "the deadline resolves the startup")
        server.resolve(.failed(.readyTimeout(seconds: 1))) { events.append($0); deadlineWon.fulfill() }
        wait(for: [deadlineWon], timeout: 5)

        let lateReady = expectation(description: "a refused READY delivers no event")
        lateReady.isInverted = true
        server.resolveReady(port: 45682) { events.append($0); lateReady.fulfill() }
        wait(for: [lateReady], timeout: 0.5)

        XCTAssertEqual(events, [.failed(.readyTimeout(seconds: 1))])
        // Recording readiness outside the refusal's critical section would make waitForExit
        // deliver an onExit for a server the caller was already told never started.
        XCTAssertFalse(server.becameReady, "a refused READY must not record readiness")
    }

    func testAReadyLineArrivingAtItsDeadlineResolvesTheStartupExactlyOnce() throws {
        let server = try controller("late", readyTimeout: 1)
        var events: [ServerEvent] = []
        let firstEvent = expectation(description: "one startup event")
        let secondEvent = expectation(description: "no second startup event")
        secondEvent.isInverted = true
        let spuriousExit = expectation(description: "no onExit for an unresolved startup")
        spuriousExit.isInverted = true

        server.onExit = { _ in spuriousExit.fulfill() }
        server.start { event in
            events.append(event)
            if events.count == 1 { firstEvent.fulfill() } else { secondEvent.fulfill() }
        }
        wait(for: [firstEvent], timeout: 10)
        wait(for: [secondEvent, spuriousExit], timeout: 3)
        XCTAssertEqual(events.count, 1, "the startup resolved more than once: \(events)")
    }

    func testExitingBeforeReadyReportsTheStatusAndKeepsTheOutput() throws {
        XCTAssertEqual(firstEvent(of: try controller("die-early")), .failed(.exitedBeforeReady(status: 1)))
        XCTAssertTrue(try log("die-early").contains("Traceback: boom"))
    }

    func testAnExitAfterReadyIsReportedThroughOnExit() throws {
        let server = try controller("crash")
        let exited = expectation(description: "onExit")
        server.onExit = { status in XCTAssertEqual(status, 7); exited.fulfill() }
        XCTAssertEqual(firstEvent(of: server), .ready(port: 45679))
        wait(for: [exited], timeout: 5)
    }

    func testStopEscalatesWhenTermIsIgnored() throws {
        let server = try controller("stubborn", grace: 1)
        XCTAssertEqual(firstEvent(of: server), .ready(port: 45680))
        let pid = try XCTUnwrap(server.pid)
        XCTAssertEqual(server.stop(), .killed)
        XCTAssertFalse(isAlive(pid))
    }

    func testStopKillsChildrenLeftBehindInTheGroup() throws {
        let server = try controller("with-child")
        XCTAssertEqual(firstEvent(of: server), .ready(port: 45681))
        let childLine = try XCTUnwrap(try log("with-child").split(separator: "\n").first { $0.hasPrefix("CHILD ") })
        let child = try XCTUnwrap(pid_t(childLine.dropFirst("CHILD ".count)))
        XCTAssertTrue(isAlive(child))
        XCTAssertEqual(server.stop(), .killed)
        let gone = expectation(description: "child killed")
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.5) { if kill(child, 0) != 0 { gone.fulfill() } }
        wait(for: [gone], timeout: 5)
    }

    func testAMissingExecutableIsALaunchFailure() throws {
        let server = ServerController(configuration: ServerConfiguration(
            executable: URL(fileURLWithPath: "/nonexistent/python3"), arguments: [], environment: [:],
            logFile: logDirectory.appendingPathComponent("server-missing.log")))
        controllers.append(server)
        guard case .failed(.launchFailed(let reason))? = firstEvent(of: server) else {
            return XCTFail("expected a launch failure")
        }
        XCTAssertFalse(reason.isEmpty)
    }

    func testStoppingANeverStartedServerSaysSo() throws {
        XCTAssertEqual(try controller("ready").stop(), .notRunning)
    }

    func testStartingTheSameControllerTwiceRefusesTheSecondLaunch() throws {
        let server = try controller("ready")
        XCTAssertEqual(firstEvent(of: server), .ready(port: 45678))
        XCTAssertEqual(firstEvent(of: server), .failed(.launchFailed("server already started")))
        XCTAssertEqual(server.stop(), .stoppedGracefully)
    }

    /// `applicationShouldTerminate` blocks the quit for as long as this takes and the app tells
    /// the user how long that is, so the bound has to be derived from the escalation `stop()`
    /// really runs rather than restated in the app.
    func testStopNeverBlocksLongerThanTheWorstCaseBound() throws {
        let server = try controller("stubborn", grace: 1)
        XCTAssertEqual(firstEvent(of: server), .ready(port: 45680))
        XCTAssertEqual(server.worstCaseStopSeconds, 8, "1s grace, then 5s for the exit, then 2s for the group")
        let started = Date()
        XCTAssertEqual(server.stop(), .killed)
        let elapsed = Date().timeIntervalSince(started)
        XCTAssertGreaterThanOrEqual(elapsed, 1, "SIGTERM was ignored, so the whole grace period ran out")
        XCTAssertLessThanOrEqual(elapsed, server.worstCaseStopSeconds)
    }

    func testTheWorstCaseStopBoundFollowsTheConfiguredGracePeriod() throws {
        XCTAssertEqual(try controller("ready", grace: 10).worstCaseStopSeconds, 17,
                       "the default grace period is what the app's quit message quotes")
    }

    /// The environment carries the OpenRouter key, and "never print this" was a comment at one
    /// call site. Interpolating a configuration anywhere -- an error message, a log line, a print
    /// left behind while debugging -- must not be able to put the key on disk.
    func testAConfigurationNeverRendersItsEnvironmentValues() throws {
        let configuration = ServerConfiguration(
            executable: URL(fileURLWithPath: "/usr/bin/true"),
            arguments: ["-m", "circuit_mcp.app_server"],
            environment: ["OPENROUTER_API_KEY": "sk-or-v1-NEVERPRINTME", "PATH": "/usr/bin:/bin"],
            logFile: logDirectory.appendingPathComponent("server-redaction.log"))

        for rendered in ["\(configuration)", String(reflecting: configuration), "\([configuration])"] {
            XCTAssertFalse(rendered.contains("NEVERPRINTME"), "a secret reached a rendered configuration: \(rendered)")
            XCTAssertFalse(rendered.contains("/usr/bin:/bin"), "environment values are redacted as a whole, not by name")
            XCTAssertTrue(rendered.contains("OPENROUTER_API_KEY"), "the names are what makes a redacted configuration useful")
            XCTAssertTrue(rendered.contains("circuit_mcp.app_server"), "everything that is not the environment still prints")
        }
    }

    // MARK: - A log the reader cannot write to

    /// A handle whose `write(contentsOf:)` fails the way a write to a full disk does. A real
    /// ENOSPC cannot be produced from a unit test without process-global state (an RLIMIT_FSIZE
    /// the whole test binary would then run under) or a scratch filesystem to fill; a descriptor
    /// that is not open for writing fails at the same call with the same thrown error, which is
    /// what the reader has to survive.
    private func unwritableLog(_ name: String) throws -> FileHandle {
        let path = logDirectory.appendingPathComponent(name)
        XCTAssertTrue(FileManager.default.createFile(atPath: path.path, contents: nil))
        let handle = try FileHandle(forReadingFrom: path)
        XCTAssertThrowsError(try handle.write(contentsOf: Data("probe".utf8)),
                             "the fixture must fail the very write the reader is being tested on")
        return handle
    }

    /// A pipe already holding `text`, so the reader's first `read` returns without blocking.
    private func primedPipe(_ text: String) throws -> (read: Int32, write: Int32) {
        var fds: [Int32] = [0, 0]
        XCTAssertEqual(pipe(&fds), 0)
        let bytes = Array(text.utf8)
        XCTAssertEqual(write(fds[1], bytes, bytes.count), bytes.count)
        return (fds[0], fds[1])
    }

    /// `log.write(chunk)` was Objective-C `-writeData:`, which raises `NSFileHandleOperationException`
    /// on a write failure. Swift cannot catch that, so a full disk terminated the app -- and the
    /// server, spawned into its own process group so that it outlives its parent, kept running and
    /// kept its flock on the data folder. The next launch's server then printed LOCKED and exited 3,
    /// and the Restart button on the error screen hit the same lock every time.
    ///
    /// The reader is driven directly here, as `resolve` and `resolveReady` are above: the failure
    /// is in the handle, and the handle is the one thing `start()` does not take from the caller.
    func testALogWriteFailureStopsTheReaderInsteadOfKillingTheApp() throws {
        let server = try controller("ready")
        let pipe = try primedPipe("READY 45678\n")
        defer { close(pipe.write) }
        let noEvent = expectation(description: "a chunk that was never logged delivers no event")
        noEvent.isInverted = true

        // Returning from this call at all is the fix: -writeData: would have taken the test
        // process down with it, exactly as it took the app down.
        server.readOutput(from: pipe.read, into: try unwritableLog("unwritable.log")) { _ in noEvent.fulfill() }

        // `close(fd)` and `outputDrained.signal()` are the two statements after the loop, so a
        // reader that returned ran both -- and the stop path waits on that semaphore.
        XCTAssertEqual(fcntl(pipe.read, F_GETFD), -1, "the reader must close its end of the pipe on the way out")
        XCTAssertEqual(errno, EBADF)
        // The chunk is logged before it is parsed, so giving up on the log also gives up on the
        // protocol line inside it. The startup then fails on its ready timeout rather than
        // running on with a log nothing can be diagnosed from.
        wait(for: [noEvent], timeout: 0.2)
    }

    /// The whole point of catching the write instead of dying on it: the app stays up, so the
    /// server it spawned is still its to stop. Driven on a live controller, with a real server in
    /// a real process group, because an orphaned server is what the old crash actually left behind.
    func testAServerWhoseLogCouldNotBeWrittenCanStillBeStopped() throws {
        let server = try controller("ready")
        XCTAssertEqual(firstEvent(of: server), .ready(port: 45678))
        let pid = try XCTUnwrap(server.pid)

        let pipe = try primedPipe("INFO: more output\n")
        defer { close(pipe.write) }
        server.readOutput(from: pipe.read, into: try unwritableLog("unwritable-live.log")) { _ in }

        XCTAssertEqual(server.stop(), .stoppedGracefully)
        XCTAssertFalse(isAlive(pid), "the server must not outlive the app that could not write its log")
    }
}
