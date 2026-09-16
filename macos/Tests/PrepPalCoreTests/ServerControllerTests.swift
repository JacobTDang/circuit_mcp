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
}
