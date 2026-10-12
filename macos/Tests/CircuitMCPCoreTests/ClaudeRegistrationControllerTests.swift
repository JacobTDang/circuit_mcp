import Darwin
import XCTest
@testable import CircuitMCPCore

final class ClaudeRegistrationControllerTests: XCTestCase {
    private var directory: URL!
    private var controllers: [ClaudeRegistrationController] = []

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("circuit-registration-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        for controller in controllers { controller.cancelAndWait() }
        try FileManager.default.removeItem(at: directory)
    }

    private func executable(_ name: String, body: String) throws -> URL {
        let url = directory.appendingPathComponent(name)
        try Data("#!/bin/sh\n\(body)\n".utf8).write(to: url)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: url.path)
        return url
    }

    private func controller(timeout: TimeInterval = 2, grace: TimeInterval = 0.1) -> ClaudeRegistrationController {
        let controller = ClaudeRegistrationController(timeout: timeout, terminationGrace: grace)
        controllers.append(controller)
        return controller
    }

    func testSuccessfulRegistrationReturnsCapturedOutput() throws {
        let cli = try executable("success", body: "printf 'registered'")
        let finished = expectation(description: "registration finished")
        let registration = controller()

        XCTAssertEqual(registration.start(executable: cli, arguments: []) { result in
            XCTAssertEqual(result, .succeeded(output: "registered"))
            finished.fulfill()
        }, .started)

        wait(for: [finished], timeout: 3)
        XCTAssertFalse(registration.isRunning)
    }

    func testNonzeroExitReturnsStatusAndStandardError() throws {
        let cli = try executable("failure", body: "printf 'permission denied' >&2; exit 7")
        let finished = expectation(description: "registration finished")
        let registration = controller()

        _ = registration.start(executable: cli, arguments: []) { result in
            XCTAssertEqual(result, .failed(status: 7, output: "permission denied"))
            finished.fulfill()
        }

        wait(for: [finished], timeout: 3)
    }

    func testLaunchFailureIsReturnedByStartAndNeverReachesTheCompletion() throws {
        let missing = directory.appendingPathComponent("missing-claude")
        let callback = expectation(description: "completion after a launch failure")
        callback.isInverted = true
        let registration = controller()

        let started = registration.start(executable: missing, arguments: []) { _ in callback.fulfill() }

        guard case .launchFailed(let reason) = started else {
            return XCTFail("a missing executable was reported as \(started)")
        }
        XCTAssertFalse(reason.isEmpty)
        XCTAssertFalse(registration.isRunning)
        wait(for: [callback], timeout: 0.2)

        let cli = try executable("retry", body: "printf 'registered'")
        let finished = expectation(description: "retry finished")
        XCTAssertEqual(registration.start(executable: cli, arguments: []) { result in
            XCTAssertEqual(result, .succeeded(output: "registered"))
            finished.fulfill()
        }, .started, "a launch failure left the controller unable to start again")
        wait(for: [finished], timeout: 3)
    }

    func testHungRegistrationIsKilledAtTheDocumentedTimeout() throws {
        let cli = try executable("hung", body: "trap '' TERM; exec /bin/sleep 60")
        let finished = expectation(description: "registration timed out")
        let registration = controller(timeout: 0.05, grace: 0.05)

        _ = registration.start(executable: cli, arguments: []) { result in
            XCTAssertEqual(result, .timedOut(seconds: 0.05, output: ""))
            finished.fulfill()
        }

        wait(for: [finished], timeout: 3)
        XCTAssertFalse(registration.isRunning)
    }

    func testCancelTerminatesTheActiveRegistration() throws {
        let cli = try executable("cancel", body: "trap '' TERM; exec /bin/sleep 60")
        let finished = expectation(description: "registration cancelled")
        let registration = controller()
        _ = registration.start(executable: cli, arguments: []) { result in
            XCTAssertEqual(result, .cancelled(output: ""))
            finished.fulfill()
        }

        XCTAssertTrue(registration.cancel())

        wait(for: [finished], timeout: 3)
        XCTAssertFalse(registration.isRunning)
    }

    func testASecondRegistrationIsRefusedWhileOneIsActive() throws {
        let cli = try executable("single", body: "trap '' TERM; exec /bin/sleep 60")
        let finished = expectation(description: "first registration cancelled")
        let duplicate = expectation(description: "duplicate callback")
        duplicate.isInverted = true
        let registration = controller()
        XCTAssertEqual(registration.start(executable: cli, arguments: []) { _ in finished.fulfill() }, .started)

        XCTAssertEqual(registration.start(executable: cli, arguments: []) { _ in duplicate.fulfill() }, .alreadyRunning)
        registration.cancelAndWait()

        wait(for: [finished, duplicate], timeout: 0.5)
    }

    func testQuitTerminatesAndReapsTheActiveChild() throws {
        let cli = try executable("quit", body: "trap '' TERM; exec /bin/sleep 60")
        let finished = expectation(description: "registration cancelled for quit")
        let registration = controller()
        _ = registration.start(executable: cli, arguments: []) { result in
            XCTAssertEqual(result, .cancelled(output: ""))
            finished.fulfill()
        }
        let pid = try XCTUnwrap(registration.pid)

        registration.cancelAndWait()

        wait(for: [finished], timeout: 1)
        XCTAssertFalse(registration.isRunning)
        XCTAssertEqual(kill(pid, 0), -1)
        XCTAssertEqual(errno, ESRCH)
    }

    func testTimeoutMessageNamesTheRecoveryStepAndCapturedOutput() {
        XCTAssertEqual(ClaudeRegistrationResult.timedOut(seconds: 30, output: "network stalled").message,
                       RegistrationMessage(
                        title: "Claude Code registration timed out.",
                        detail: "Claude Code did not finish within 30 seconds, so it was stopped. Try again, or copy the MCP command and run it in Terminal.\n\nnetwork stalled"))
    }
}
