import XCTest
@testable import PrepPalCore

final class ServerControllerTests: XCTestCase {
    private var logDirectory: URL!

    override func setUpWithError() throws {
        logDirectory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: logDirectory)
    }

    private func controller(_ mode: String, readyTimeout: TimeInterval = 10, grace: TimeInterval = 10) throws -> ServerController {
        let script = try XCTUnwrap(Bundle.module.url(forResource: "fake_server", withExtension: "sh", subdirectory: "Fixtures"))
        return ServerController(configuration: ServerConfiguration(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: [script.path, mode],
            environment: ["PATH": "/usr/bin:/bin"],
            logFile: logDirectory.appendingPathComponent("server-\(mode).log"),
            readyTimeout: readyTimeout,
            stopGracePeriod: grace
        ))
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
