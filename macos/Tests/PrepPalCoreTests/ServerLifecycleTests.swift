import XCTest
@testable import PrepPalCore

/// `AppDelegate` supervises exactly one server, and the bookkeeping for that -- which controller
/// is live, what work is in flight, and whether a quit is waiting on it -- is what these drive.
/// The delegate itself is in the executable target and cannot be imported here, so the rules live
/// in `ServerLifecycle` and the delegate does nothing else with them; the model below is the
/// delegate's three server paths reduced to the calls it makes.
/// These drive the app's main-thread paths, which is where `ServerLifecycle` lives, so the whole
/// case is main-actor isolated rather than each test hopping on its own.
@MainActor
final class ServerLifecycleTests: XCTestCase {
    private var logDirectory: URL!

    override func setUpWithError() throws {
        logDirectory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: logDirectory)
    }

    /// A controller that is never started: only its identity takes part in the bookkeeping.
    private func controller() -> ServerController {
        ServerController(configuration: ServerConfiguration(
            executable: URL(fileURLWithPath: "/usr/bin/true"), arguments: [], environment: [:],
            logFile: logDirectory.appendingPathComponent("\(UUID().uuidString).log")))
    }

    // MARK: - The app's server paths, minus AppKit and the dispatch queues

    @MainActor
    private final class AppModel {
        let lifecycle = ServerLifecycle()
        /// Every server the app would have spawned, in order.
        private(set) var started: [ServerController] = []
        /// Every server it asked to stop.
        private(set) var stopRequests: [ServerController] = []
        private(set) var refusals: [String] = []
        private(set) var quitReplies = 0
        /// Work a quit cancelled, which the app writes to the log rather than doing.
        private(set) var abandoned: [String] = []

        private let makeController: () -> ServerController
        private var inFlight: [(controller: ServerController, pendingWork: String?, then: () -> Void)] = []

        init(_ makeController: @escaping () -> ServerController) { self.makeController = makeController }

        var hasStopInFlight: Bool { !inFlight.isEmpty }

        /// A server that was started, is not the one the app holds, and was never stopped: the
        /// process that survives the quit because nothing refers to it any more.
        var orphans: [ServerController] {
            started.filter { candidate in
                candidate !== lifecycle.liveServer && !stopRequests.contains { $0 === candidate }
            }
        }

        /// `AppDelegate.startServer()`.
        func startServer() {
            if let refusal = lifecycle.refusalToStart {
                refusals.append(refusal)
                return
            }
            let controller = makeController()
            started.append(controller)
            if let refusal = lifecycle.adopt(controller) { refusals.append(refusal) }
        }

        /// `AppDelegate.stopServer(_:pendingWork:then:)`. The stop itself runs off the main
        /// thread, so it is held here and finished by the test: that window is the whole bug.
        func stopServer(pendingWork: String?, then: @escaping () -> Void) {
            switch lifecycle.beginStop() {
            case .refused(let reason):
                refusals.append(reason)
            case .nothingToStop:
                then()
            case .stop(let running):
                stopRequests.append(running)
                inFlight.append((running, pendingWork, then))
            }
        }

        /// The stop that was asked for first finishes and its main-queue completion runs.
        func completeOldestStop() {
            let stop = inFlight.removeFirst()
            switch lifecycle.finishStop(stop.controller) {
            case .resume:
                stop.then()
            case .quitPending:
                if let work = stop.pendingWork { abandoned.append(work) }
                quitReplies += 1
            }
        }

        /// `AppDelegate.restartServer()`.
        func restart() {
            stopServer(pendingWork: "the restart") { [unowned self] in self.startServer() }
        }

        /// `AppDelegate.importExistingData()`, whose copy runs off the main thread after the stop.
        func importData() {
            stopServer(pendingWork: "the import") { [unowned self] in
                if let refusal = self.lifecycle.beginImportCopy() { self.refusals.append(refusal) }
            }
        }

        func finishImportCopy() {
            switch lifecycle.finishImportCopy() {
            case .resume: startServer()
            case .quitPending: quitReplies += 1
            }
        }

        /// `AppDelegate.applicationShouldTerminate(_:)`.
        @discardableResult
        func quit() -> ServerLifecycle.QuitDecision {
            let decision = lifecycle.beginQuit()
            if case .stopTheServer = decision {
                stopServer(pendingWork: nil) { [unowned self] in self.quitReplies += 1 }
            }
            return decision
        }
    }

    private func app() -> AppModel { AppModel(controller) }

    // MARK: - One server at a time

    func testTheAppHoldsTheServerItStarted() {
        let app = self.app()
        app.startServer()
        XCTAssertEqual(app.started.count, 1)
        XCTAssertTrue(app.lifecycle.liveServer === app.started[0])
        XCTAssertEqual(app.orphans.count, 0)
    }

    func testAServerIsNeverStartedOnTopOfOneThatIsRunning() {
        let app = self.app()
        app.startServer()
        app.startServer()
        XCTAssertEqual(app.started.count, 1, "a second server was started while the first was running")
        XCTAssertEqual(app.refusals.count, 1)
    }

    /// The trace from the review: a restart, a second restart inside its stop window, and both
    /// completions starting a server. Unguarded this starts three servers, the app holds the
    /// third, and the second runs with nothing referring to it -- the orphan that survives a quit.
    func testASecondRestartInsideTheStopWindowStartsNoServerNothingHolds() {
        let app = self.app()
        app.startServer()                                    // launch
        app.restart()                                        // the stop begins
        app.restart()                                        // Settings -> Save, inside that window
        while app.hasStopInFlight { app.completeOldestStop() }

        XCTAssertEqual(app.started.count, 2, "\(app.started.count) servers were started for one restart")
        XCTAssertTrue(app.lifecycle.liveServer === app.started.last, "the app does not hold the server it started last")
        XCTAssertEqual(app.orphans.count, 0, "a server was started that nothing stopped and nothing holds")
        XCTAssertEqual(app.refusals.count, 1, "the refused restart must say why rather than pass silently")
    }

    /// The same window reached from the other menu command: Import Existing Data would have
    /// copied a folder into the data directory while a restart was starting a server on top of it.
    func testAnImportInsideTheStopWindowIsRefusedRatherThanRunOnTheOtherStop() {
        let app = self.app()
        app.startServer()
        app.restart()
        app.importData()
        XCTAssertEqual(app.refusals.count, 1)
        while app.hasStopInFlight { app.completeOldestStop() }
        XCTAssertEqual(app.started.count, 2)
        XCTAssertEqual(app.orphans.count, 0)
    }

    func testAStopWithNoServerRunningJustRunsTheWork() {
        let app = self.app()
        var ran = false
        app.stopServer(pendingWork: "the restart") { ran = true }
        XCTAssertTrue(ran)
        XCTAssertFalse(app.hasStopInFlight)
    }

    // MARK: - Quitting

    func testQuittingWithNothingRunningTerminatesAtOnce() {
        guard case .terminateNow = app().quit() else { return XCTFail("the quit waited for a server that was not running") }
    }

    func testQuittingWithAServerRunningStopsItFirst() {
        let app = self.app()
        app.startServer()
        guard case .stopTheServer(let running) = app.quit() else { return XCTFail("the quit did not stop the server") }
        XCTAssertTrue(running === app.started[0])
        app.completeOldestStop()
        XCTAssertNil(app.lifecycle.liveServer)
        XCTAssertEqual(app.quitReplies, 1)
        XCTAssertEqual(app.orphans.count, 0)
    }

    /// Command-Q during a restart. The restart's completion must not start a replacement the
    /// quit knows nothing about, which is the variant that exits with a live server behind it.
    func testQuittingDuringARestartStartsNoReplacementServer() {
        let app = self.app()
        app.startServer()
        app.restart()
        guard case .waitForWorkInFlight(.stoppingServer(let stopping)) = app.quit() else {
            return XCTFail("the quit did not wait for the stop that was already running")
        }
        XCTAssertTrue(stopping === app.started[0], "the quit must quote the controller that is being stopped")

        app.completeOldestStop()
        XCTAssertEqual(app.started.count, 1, "the restart started a replacement while the app was quitting")
        XCTAssertNil(app.lifecycle.liveServer)
        XCTAssertEqual(app.quitReplies, 1, "the quit was never answered")
        XCTAssertEqual(app.abandoned, ["the restart"], "work a quit cancelled has to reach the log")
        XCTAssertEqual(app.orphans.count, 0)
    }

    /// Command-Q while the import's copy is running. The copy is off the main thread now, so the
    /// quit can reach the delegate in the middle of it; it must wait rather than exit mid-copy.
    func testQuittingDuringTheImportCopyWaitsForItAndStartsNoServer() {
        let app = self.app()
        app.startServer()
        app.importData()
        app.completeOldestStop()
        guard case .waitForWorkInFlight(.importingData) = app.quit() else {
            return XCTFail("the quit did not wait for the copy that was running")
        }
        app.finishImportCopy()
        XCTAssertEqual(app.started.count, 1, "the import restarted the server while the app was quitting")
        XCTAssertNil(app.lifecycle.liveServer)
        XCTAssertEqual(app.quitReplies, 1)
    }

    func testNoServerMayBeStartedOnceTheQuitHasBegun() {
        let app = self.app()
        app.startServer()
        app.quit()
        app.completeOldestStop()
        app.startServer()
        XCTAssertEqual(app.started.count, 1)
        XCTAssertNil(app.lifecycle.liveServer)
    }

    // MARK: - What the menu asks

    func testTheMenuCommandsThatStopTheServerAreClosedWhileAStopIsRunning() {
        let app = self.app()
        app.startServer()
        XCTAssertFalse(app.lifecycle.isBusy, "Settings and Import are open while the server just runs")
        app.restart()
        XCTAssertTrue(app.lifecycle.isBusy, "Settings and Import stay open during the stop they would collide with")
        app.completeOldestStop()
        XCTAssertFalse(app.lifecycle.isBusy)
    }

    func testABusyLifecycleSaysWhatItIsBusyWith() throws {
        let app = self.app()
        app.startServer()
        app.restart()
        let reason = try XCTUnwrap(app.lifecycle.refusalToStart, "a refusal with no reason is a silent one")
        XCTAssertTrue(reason.lowercased().contains("stopping"), "the reason has to name the work: \(reason)")
    }
}
