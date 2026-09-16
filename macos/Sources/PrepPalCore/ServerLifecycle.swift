import Foundation

/// The app supervises exactly one server, and this is the bookkeeping for that: which controller
/// it holds, what work is in flight, and whether a quit is waiting on it.
///
/// It exists as its own type because getting it wrong orphans a server. A stop runs off the main
/// thread and takes the whole grace period in practice, and inside that window a second stop used
/// to be accepted against the same controller: both completions then started a server, the app
/// kept a reference to the last one, and the one in the middle ran on with nothing referring to
/// it -- not stopped on quit, and holding the data folder against its replacement. The same window
/// reached from `applicationShouldTerminate` exited the app with a live server behind it.
///
/// Every rule here is one `AppDelegate` cannot state twice: the delegate keeps no server state of
/// its own, so what these tests drive is what the app runs.
///
/// **Main thread only.** None of this is synchronised, and a caller on another thread would race
/// exactly the bookkeeping that exists to stop two servers running at once: `beginStop` reads
/// `workInFlight` and writes it, so two of them interleaved both return `.stop` for the same
/// controller. The app is safe today because every path into it is on the main thread -- the
/// delegate's menu actions and view callbacks, and `ServerController`, which hops every event,
/// exit and stop completion to the main queue before calling back -- but that is stated over in
/// `ServerController`, not here, and nothing stops a new caller from reaching this from a
/// `DispatchQueue.global()` block. If you add one, hop to the main queue first.
///
/// `@MainActor` would say this to the compiler rather than to the reader, and it is the right
/// answer eventually. It cannot be applied on its own: under this package's Swift 5 language mode
/// it makes `AppDelegate` fail to compile until it is `@MainActor` too, and that in turn makes
/// `main.swift` fail on `AppDelegate()` and turns the non-`Sendable` captures in the delegate's
/// background blocks into warnings. That is a whole-target change, not this type's.
public final class ServerLifecycle {
    /// Work that has to finish before a server may start or the app may quit.
    public enum Work {
        case stoppingServer(ServerController)
        case importingData

        /// Said to the user when it is what stands in the way.
        public var inProgress: String {
            switch self {
            case .stoppingServer: return "The server is still stopping."
            case .importingData: return "The import is still running."
            }
        }
    }

    public enum StopDecision {
        /// No server is running, so the caller's work can go ahead at once.
        case nothingToStop
        /// Stop this controller off the main thread, then report back with `finishStop`.
        case stop(ServerController)
        /// Work is already under way that this stop would collide with, and why.
        case refused(reason: String)
    }

    public enum WorkCompletion: Equatable {
        /// Run the work the stop or the copy was asked for.
        case resume
        /// A quit arrived while it ran: answer the quit, and start nothing.
        case quitPending
    }

    public enum QuitDecision {
        case terminateNow
        /// Work is already running; the quit waits for it rather than starting a second stop.
        case waitForWorkInFlight(Work)
        case stopTheServer(ServerController)
    }

    /// The one server the app holds, if any.
    public private(set) var liveServer: ServerController?
    public private(set) var workInFlight: Work?
    public private(set) var isQuitting = false

    public init() {}

    /// True while the menu commands that would stop or replace the server have to be closed.
    /// Refusing them after the fact still leaves the user having chosen a folder for nothing.
    public var isBusy: Bool { workInFlight != nil || isQuitting }

    /// Why a server may not be started now, or nil when one may be. Every caller is a path that
    /// would otherwise spawn a process the app holds no reference to.
    public var refusalToStart: String? {
        if isQuitting { return "The app is quitting." }
        if let workInFlight { return workInFlight.inProgress }
        if liveServer != nil { return "A server is already running." }
        return nil
    }

    /// Records a server that has just been started. Returns nil, or the reason it was refused --
    /// overwriting the reference to a running server is exactly how one is lost.
    @discardableResult
    public func adopt(_ controller: ServerController) -> String? {
        if let refusal = refusalToStart { return refusal }
        liveServer = controller
        return nil
    }

    /// A stop already running refuses this one rather than queueing it. Queueing would have to
    /// hold every waiting completion until the last stop finished, because the first one to run
    /// starts the replacement server and the rest would then be acting on top of it; refusing
    /// says so at once, and the commands that could ask are closed for the length of the stop.
    public func beginStop() -> StopDecision {
        if let workInFlight { return .refused(reason: workInFlight.inProgress) }
        guard let live = liveServer else { return .nothingToStop }
        workInFlight = .stoppingServer(live)
        return .stop(live)
    }

    public func finishStop(_ controller: ServerController) -> WorkCompletion {
        // A controller that is no longer the live one has already been replaced, and clearing
        // the reference would drop the server that replaced it.
        if liveServer === controller { liveServer = nil }
        workInFlight = nil
        return isQuitting ? .quitPending : .resume
    }

    /// The import's copy runs off the main thread once the server is out of the way, so a quit
    /// can arrive in the middle of it. It counts as work in flight for the same reason a stop
    /// does: nothing may start a server on the folder it is writing, and a quit has to wait.
    @discardableResult
    public func beginImportCopy() -> String? {
        if let workInFlight { return workInFlight.inProgress }
        workInFlight = .importingData
        return nil
    }

    public func finishImportCopy() -> WorkCompletion {
        workInFlight = nil
        return isQuitting ? .quitPending : .resume
    }

    /// Once this is called nothing starts a server again. Work already in flight keeps running
    /// and answers the quit when it finishes, so the app never exits over a live server.
    public func beginQuit() -> QuitDecision {
        isQuitting = true
        if let workInFlight { return .waitForWorkInFlight(workInFlight) }
        guard let live = liveServer else { return .terminateNow }
        return .stopTheServer(live)
    }
}
