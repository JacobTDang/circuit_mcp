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
/// **Main thread only**, and `@MainActor` states that to the compiler. None of this is
/// synchronised, and a caller on another thread would race exactly the bookkeeping that exists to
/// stop two servers running at once: `beginStop` reads `workInFlight` and writes it, so two of
/// them interleaved both return `.stop` for the same controller. Work that runs off the main
/// thread -- stopping a server, copying a notebook -- hops back before it touches any of this.
@MainActor
public final class ServerLifecycle {
    /// Work that has to finish before a server may start or the app may quit.
    public enum Work {
        case stoppingServer(ServerController)
        /// The copy counts into this progress, and stops at the end of the file it is on once the
        /// progress is cancelled.
        case importingData(Progress)

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
    /// Set by the first of a quit's answers, so that no other one answers it again.
    private var quitAnswered = false

    /// How long a quit waits for a cancelled import to stop before the app quits anyway.
    ///
    /// A cancelled copy stops at the end of the file it is on, which is quick -- unless that file is
    /// on a volume that has stopped answering, and a read the filesystem never returns cannot be
    /// interrupted from inside the process. The quit used to wait for it forever. Quitting without
    /// it is safe because the data folder is not touched until a copy is complete, and the partial
    /// copy left behind is removed by the next import.
    public let importQuitDeadline: TimeInterval

    public init(importQuitDeadline: TimeInterval = 10) {
        self.importQuitDeadline = importQuitDeadline
    }

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
    public func beginImportCopy(_ progress: Progress) -> String? {
        if let workInFlight { return workInFlight.inProgress }
        workInFlight = .importingData(progress)
        return nil
    }

    public func finishImportCopy() -> WorkCompletion {
        workInFlight = nil
        return isQuitting ? .quitPending : .resume
    }

    /// Once this is called nothing starts a server again. Work already in flight answers the quit
    /// when it finishes, so the app never exits over a live server. An import is cancelled first:
    /// the student asked to quit, not to wait for a copy, and a cancelled import leaves their data
    /// as it was. The caller then sets the deadline with `answerQuitAtImportDeadline`.
    public func beginQuit() -> QuitDecision {
        isQuitting = true
        if let workInFlight {
            if case .importingData(let copy) = workInFlight { copy.cancel() }
            return .waitForWorkInFlight(workInFlight)
        }
        guard let live = liveServer else { return .terminateNow }
        return .stopTheServer(live)
    }

    /// True for the first of a quit's answers only: `applicationShouldTerminate` is answered once,
    /// and with a deadline running the import that finishes and the deadline that passes can both
    /// try. Whichever is second does nothing.
    public func claimQuitReply() -> Bool {
        guard isQuitting, !quitAnswered else { return false }
        quitAnswered = true
        return true
    }

    /// Answers the quit with `answer` once `importQuitDeadline` has passed, unless the import has
    /// answered it first.
    public func answerQuitAtImportDeadline(_ answer: @escaping @MainActor () -> Void) {
        DispatchQueue.main.asyncAfter(deadline: .now() + importQuitDeadline) {
            MainActor.assumeIsolated {
                guard self.claimQuitReply() else { return }
                answer()
            }
        }
    }
}
